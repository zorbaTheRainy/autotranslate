#!/usr/bin/env python3

"""
autotranslate_web_server.py
===========================

A Flask-based web server interface for the autotranslate library, enabling PDF translation via web uploads.

This module provides a user-friendly web interface for translating PDF documents using the DeepL API. Users can upload files through a web form, specify translation options (target language, filename translation, original placement), and monitor translation progress in real-time.

Features:
    - Web upload form for PDF files with configurable options
    - Background processing of translations using threading
    - Real-time job status tracking and scoreboard
    - Interactive log viewing with auto-refresh capability
    - Quota exceeded handling with renewal countdown
    - Directory monitoring mode for automated processing
    - Optional Apprise notifications for completed translations

The server provides a GUI for the autotranslate core module for translation logic. It supports both single-file uploads and continuous directory watching modes.

Usage:
    Run as main module to start the web server:
        python autotranslate_web_server.py
    The web interface will be available at http://localhost:8010 or APP_PORT
"""

# standard libraries
import copy                                    # https://docs.python.org/3/library/copy.html
import logging                                 # https://docs.python.org/3/library/logging.html
import logging.handlers                        # https://docs.python.org/3/library/logging.handlers.html
import sys                                     # https://docs.python.org/3/library/sys.html
import threading                               # https://docs.python.org/3/library/threading.html
import time                                    # https://docs.python.org/3/library/time.html
from dataclasses import dataclass              # https://docs.python.org/3/library/dataclasses.html
from datetime import datetime                  # https://docs.python.org/3/library/datetime.html
from pathlib import Path                       # https://docs.python.org/3/library/pathlib.html
from typing import Dict, Optional, Union       # https://docs.python.org/3/library/typing.html

# non-standard imports
import deepl                                   # pip install --upgrade deepl   # https://github.com/DeepLcom/deepl-python
from ansi2html import Ansi2HTMLConverter       # pip install ansi2html  # https://ansi2html.readthedocs.io/
from flask import Flask, abort, render_template, request, redirect, url_for, send_file  # pip install flask # https://flask.palletsprojects.com/
from werkzeug.utils import secure_filename     # pip install werkzeug  # https://werkzeug.palletsprojects.com/

# intra-module imports
import autotranslate


# -----------------------------------------------------------------------
# CLASS DEFINITIONS
# -----------------------------------------------------------------------
@dataclass
class ScoreboardEntry:
    """Represents a single job's important data."""
    id: Optional[str] = None
    input_file: Optional[Path] = None
    log_file: Optional[Path] = None
    output_file: Optional[Path] = None
    # config: Optional[autotranslate.Config] = None # decided not to store full config

# Filter to throttle /log endpoint requests from werkzeug
class LogEndpointFilter(logging.Filter):
    '''Filter to throttle repeated /log/filename requests from werkzeug.'''
    def __init__(self):
        super().__init__()
        self.last_logged = {}  # filename -> last logged timestamp
        self.quiet_period = 5 * 60  # 5 minutes in seconds
    def filter(self, record):
        if record.name == "werkzeug":
            msg = record.getMessage()
            # Check for GET /log/filename HTTP/1.1" 200
            import re
            # match = re.search(r'GET /log/([^"]+) HTTP/[^"]+" 200', msg)
            match = re.search(r'"(?:GET|HEAD)\s+/log/([^?\s"]+)(?:\?[^"\s]*)?\s+HTTP/[^"]+"\s+200\b', msg)
            if match:
                filename = match.group(1)
                now = time.time()
                if filename not in self.last_logged or (now - self.last_logged[filename]) > self.quiet_period:
                    self.last_logged[filename] = now
                    return True  # Log this one
                else:
                    return False  # Drop this one
        return True

# -----------------------------------------------------------------------
# Module-level variables (accessible to all functions in this file)
# -----------------------------------------------------------------------

# web page vars
app = Flask(__name__, template_folder="html")
APP_PORT = 8010
cfg: Optional[autotranslate.Config] = None
web_logger = logging.getLogger("web")
web_log_file_path: Optional[Path] = None
# a list of all jobs run via the webpage (and their data)
scoreboard: Dict[str, ScoreboardEntry] = {}
scoreboard_lock = threading.Lock()
# error flags
quota_lock = threading.Lock()
is_quota_exceeded: bool = False
fatal_error_lock = threading.Lock()
is_fatal_error: bool = False
fatal_error_reason: Optional[str] = None


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------
def start_web_server(cli_cfg: autotranslate.Config) -> None:
    """
    Start the web server with the given config.

    Sets up logging, starts monitor thread, adds file logging, runs Flask app.
    """

    global cfg, web_log_file_path # pylint: disable=global-statement
    cfg = cli_cfg

    # init logger, and start output to STDOUT
    setup_web_stdout_logging()

    # Wee bit of error checking
    if (cfg is None):
        web_logger.info("Config values passed from CLI are None, web server cannot start.")
        return

    try:
        cfg.callback_on_fatal_error = capture_fatal_error
        cfg.callback_on_quota_exceeded = capture_quota_excess
    except (AttributeError, TypeError) as e:
        web_logger.error(f"Failed to set fatal error callback: {e}")

    # Now setup the global log file (note: log_dir may not exist, fails gracefully)
    web_log_file_path = add_web_file_logging()
    if (web_log_file_path is None):
        web_logger.info("Web Log file not initialized.")

    # Start web server (regardless of state, let @app.before_request handle uninitialized state)
    app.run(host="0.0.0.0", port=APP_PORT)




# -----------------------------------------------------------------------
# Web Server Functions
# -----------------------------------------------------------------------
@app.before_request
def ensure_initialized():
    """
    Ensure the autotranslate service is initialized before processing requests.

    Excludes the file_log endpoint from this check.

    Returns:
        None if endpoint is excluded or initialized.
        Tuple[str, int] if not initialized.
    """
    excluded_endpoints = {"file_log"}
    if request.endpoint in excluded_endpoints:
        return None
    if (cfg is None) or (web_log_file_path is None):
        web_logger.info("Autotranslate web service blocked as not initialized")
        return "Autotranslate web service not initialized", 500

@app.route("/")
def index():
    """
    Render the main translation form page.

    Retrieves job_id from query parameters if provided.

    Returns:
        Rendered HTML template.
    """
    # get the task ID if is was passed (e.g., /?job_id=2025_12_24_15_30_00_001); passed if redirected from /run, None otherwise
    job_id = request.args.get("job_id", None)

    # get CLI log path
    global_log_filename = None
    global_log_filename = cfg.global_log_file_path.name if (cfg and cfg.global_log_file_path) else None


    # if the whole program crashed (or just autotranslate's monitor

    if is_fatal_error:
        # General crash page
        return render_template(
                                "error.html",
                                title="Service Crashed",
                                header="Translation Service Crashed",
                                error_message=fatal_error_reason,
                                global_log_filename=global_log_filename,
                                web_log_filename=web_log_file_path.name if web_log_file_path else None,
                            ), 500

    # if the quota has been exceeded, display an error page
    if is_quota_exceeded:
        renewal_secs = autotranslate.num_seconds_till_renewal(cfg.usage_renewal_day if cfg else 0)
        renewal_time = time.time() + renewal_secs
        return render_template(
                                "error.html",
                                title="Quota Exceeded",
                                header="Translation Quota Exceeded",
                                error_message="The translation quota has been exceeded. Please wait until the quota renews.",
                                is_quota=True,
                                renewal_time=renewal_time,
                                global_log_filename=global_log_filename,
                                web_log_filename=web_log_file_path.name if web_log_file_path else None,
                            )

    # now if everything is OK, render the main page
    with scoreboard_lock:
        jobs_log_filename = None
        if job_id and (job_id in scoreboard):
            entry = scoreboard[job_id]
            if entry.log_file is not None:
                jobs_log_filename = entry.log_file.name
    web_logger.info(f"\t Entered log file into scoreboard entry: {jobs_log_filename}")


    return render_template( "index.html",
                            cfg=cfg,
                            deepl_languages=autotranslate.get_deepl_languages(),
                            global_log_filename=global_log_filename,
                            web_log_filename=web_log_file_path.name if web_log_file_path else None,
                            jobs_log_filename=jobs_log_filename,
                            job_id=job_id,
                        )

@app.route("/run", methods=["POST"])
def run_translation():
    """
    Handle PDF upload and initiate translation process.

    Validates uploaded file, saves it, creates config clone,
    sets up callbacks, and starts background translation.

    Returns:
        Redirect to index with job_id.
    """
    assert cfg is not None, "Config=None should have been guarded against by ensure_initialized()"

    uploaded = request.files.get("pdf_file")
    if not uploaded:
        return render_template(
                                "error.html",
                                title="Input Error",
                                header="No PDF uploaded",
                                error_message="No PDF uploaded",
                                global_log_filename=cfg.global_log_file_path.name if (cfg and cfg.global_log_file_path) else None,
                                web_log_filename=web_log_file_path.name if web_log_file_path else None,
                            ), 400
        # return "No PDF uploaded", 400
    # clean uploaded filename
    uploaded_filename = uploaded.filename
    if uploaded_filename is None:
        uploaded_filename = ""
    safe_name = secure_filename(uploaded_filename)
    input_path = cfg.tmp_dir / safe_name
    # upload file to the tmp directory
    uploaded.save(input_path)

    # Clone cfg for this run
    default_target_lang = cfg.target_lang
    new_cfg = copy.deepcopy(cfg)

    # Extract form values
    new_cfg.source_file = input_path
    new_cfg.target_lang = request.form.get("target_language", default_target_lang)
    new_cfg.translate_filename = request.form.get("translate_filename") == "yes"
    new_cfg.put_original_first = request.form.get("include_original") == "yes"

    # create scoreboard entry (subroutine handles threading)
    scoreboard_key = unique_timestamp_key(new_cfg.source_file)

    # Callback: fires immediately when per-file log is created
    def capture_log_path(path: Path) -> None:
        with scoreboard_lock:
            entry = scoreboard.get(scoreboard_key)
            if entry is not None:
                entry.log_file = path
                web_logger.info(f"\t Captured log file in scoreboard entry: {entry.log_file}")
    new_cfg.callback_on_local_log_file = capture_log_path

    # Callback: fires when translation is complete
    def capture_output_pdf(path: Path) -> None:
        with scoreboard_lock:
            entry = scoreboard.get(scoreboard_key)
            if entry is not None:
                entry.output_file = path
        web_logger.info(f"Output PDF created: {path}")
    new_cfg.callback_on_file_complete = capture_output_pdf

    # Run translation in background
    threading.Thread(   target=run_process_file,
                        args=(input_path, new_cfg),
                        daemon=True,
                    ).start()

    return redirect(url_for("index", job_id=scoreboard_key))


@app.route("/check_output")
def check_output():
    """
    Check if the translation output is ready for a given job_id.

    Returns:
        JSON response with ready status and filename.
    """
    assert cfg is not None, "Config=None should have been guarded against by ensure_initialized()"

    try:
        # get the task ID if is was passed (e.g., /?job_id=2025_12_24_15_30_00_001); passed if redirected from /run, None otherwise
        job_id = request.args.get("job_id", None)

        output_file_path = None
        with scoreboard_lock:
            if job_id and (job_id in scoreboard):
                entry = scoreboard[job_id]
                output_file_path = entry.output_file

        web_logger.info(f"check_output: job_id={job_id}, output_file_path={output_file_path}")
        if output_file_path:
            return {"ready": True, "filename": output_file_path.name}
        else:
            return {"ready": False, "filename": None}
    except Exception as e:
        web_logger.error(f"Error in check_output: {e}")
        return {"error": "Internal server error"}, 500

@app.route("/output/<filename>")
def serve_output(filename: str):
    """
    Serve the translated output file.

    Args:
        filename (str): Name of the output file.

    Returns:
        Flask response with the file.
    """
    assert cfg is not None, "Config=None should have been guarded against by ensure_initialized()"

    path = cfg.output_dir / filename

    # final checks: file must exist and be a regular file
    if (not path.exists()) or (not path.is_file()):
        abort(404)

    web_logger.info(f"Serving output file: {path}")
    return send_file(path)


@app.route("/download/<dl_type>/<path:filename>")
def download(dl_type: str, filename: str):
    """
    Serve a file from one of X directories based on `dl_type`.
    No module-level globals except `app`. The directory map lives inside
    the route and we perform existence and path traversal checks.
    """
    assert cfg is not None, "Config=None should have been guarded against by ensure_initialized()"

    # map types to directories locally inside the function
    dir_path = None
    if dl_type == "output":
        dir_path = cfg.output_dir if cfg else None
    elif dl_type == "log":
        dir_path = cfg.log_dir if cfg else None
    else:
        dir_path = None

    if dir_path is None:
        # unknown type
        abort(404)

    # ensure the directory itself exists and is a directory
    if (not dir_path.exists()) or (not dir_path.is_dir()):
        abort(404)

    # build the candidate file path
    path = dir_path / filename

    # final checks: file must exist and be a regular file
    if (not path.exists()) or (not path.is_file()):
        abort(404)

    # send the file as an attachment (forces download)
    web_logger.info(f"Serving output file: {path}")
    return send_file(path)

@app.route("/status")
def report_scoreboard():
    """
    Display scoreboard of translation jobs in a table format.
    """
    with scoreboard_lock:
        jobs = []
        for job_id, entry in scoreboard.items():
            if entry.log_file:
                if entry.log_file.exists():
                    log_link = f'<a href="/download/log/{entry.log_file.name}" target="_blank">Log</a>'
                else:
                    log_link = 'Deleted'
            else:
                log_link = 'None'

            if entry.output_file:
                if entry.output_file.exists():
                    output_link = f'<a href="/download/output/{entry.output_file.name}" target="_blank">{entry.output_file.name}</a>'
                else:
                    output_link = f'{entry.output_file.name} (Deleted)'
            else:
                output_link = 'None'

            jobs.append({
                'id': job_id,
                'input_file': entry.input_file.name if entry.input_file else 'N/A',
                'log_link': log_link,
                'output_link': output_link,
            })

    return render_template("status.html", jobs=jobs)

@app.route("/log/<log_filename>")
def file_log(log_filename: str):
    """
    Serve the content of a log file as HTML.

    Args:
        log_filename (str): Name of the log file.

    Returns:
        HTML string with log content.
    """

    # get the mode (e.g., realtime)
    mode = request.args.get("mode", None)

    log_dir = cfg.log_dir if cfg else None
    if not log_dir:
        log_dir = Path("/tmp")
    log_path = log_dir / log_filename

    if not log_path.exists() or not log_path.is_file():
        return f"<p>No log file found: {log_path}</p>", 404

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # last 200 lines
    tail_raw = "".join(lines[-200:])

    # clean ANSI colour codes
    conv = Ansi2HTMLConverter(inline=True)
    tail = conv.convert(tail_raw, full=False)

    refresh_seconds = 30
    if mode == "realtime":
        refresh_seconds = 2
    # pass the full path+query so the HEAD request checks the same resource
    check_url = request.path
    if request.query_string:
        check_url += "?" + request.query_string.decode("utf-8")

    return render_template(
                            "log.html",
                            refresh_seconds=refresh_seconds,
                            check_url=check_url,
                            log_filename=log_filename,
                            tail=tail,
                            is_realtime = True if (mode == "realtime") else False,
                        )



# -----------------------------------------------------------------------
# Threaded Functions
# -----------------------------------------------------------------------

def run_process_file(file_path: Union[str, Path], config: autotranslate.Config) -> bool:
    """
    Run autotranslate.process_file() and update global error flags based on the outcome.

    Returns:
        True if processing succeeds, False if a quota error, DeepL error, or unexpected
        exception occurs. Also updates global flags for fatal errors and quota exhaustion.
    """
    global is_quota_exceeded, is_fatal_error, fatal_error_reason  # pylint: disable=global-statement

    try:
        autotranslate.process_file(file_path, config)
        return True
    except Exception as e:
        # this is really not a big deal for a 1-and-done file translation
        if isinstance(e, autotranslate.QuotaExceededException):
            web_logger.error("Translation quota exceeded during file processing.")
        elif isinstance(e, deepl.DeepLException):
            web_logger.error("DeepL API error occurred during file processing.")
        else:
            web_logger.error("Unexpected error occurred during file processing.")
        web_logger.error(f"{e}")
        # close the global log file and any Apprise handlers and exit
        if isinstance(e, autotranslate.QuotaExceededException):
            is_quota_exceeded = True  # Quota exceeded exit code
        else:
            # Fatal error
            is_fatal_error = True
            fatal_error_reason = str(e)
        return False

# Callback: fires immediately when per-file log is created
def capture_fatal_error(msg: str) -> None:
    global is_fatal_error, fatal_error_reason  # pylint: disable=global-statement
    with fatal_error_lock:
        is_fatal_error = True
        fatal_error_reason = msg
    web_logger.error(f"\t Fatal Error captured: {msg}")

# Callback: fires immediately when per-file log is created
def capture_quota_excess() -> None:
    global is_quota_exceeded # pylint: disable=global-statement
    with quota_lock:
        is_quota_exceeded = True
    web_logger.error(f"\t Quota exceeded.")

# -----------------------------------------------------------------------
# Core Functions
# -----------------------------------------------------------------------
def setup_web_stdout_logging():
    """
    Set up logging for the web server with console handler.
    """
    # Avoid adding duplicate handlers if setup_logging is called multiple times
    if web_logger.handlers:
        return
    web_logger.setLevel(logging.DEBUG) # sets the level below which _no_ handler may go

    # Console/STDOUT handler
    ch = logging.StreamHandler(sys.stdout)  # well this is annoying.  StreamHandler is logging.* while newer handlers are logging.handlers.*
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter('%(message)s'))

    web_logger.addHandler(ch)
    return

def add_web_file_logging() -> Optional[Path]:
    """
    Add rotating file handler for web server logs.
    """
    log_dir = cfg.log_dir if cfg else None
    if not log_dir:
        log_dir = Path("/tmp")
    log_file = log_dir / f"_{Path(__file__).stem}.log"


    try:
        # confirm log_dir exists, throw error otherwise
        log_dir.mkdir(parents=True, exist_ok=True)

        web_logger.setLevel(logging.DEBUG)
        web_logger.propagate = False

        num_bytes = 10  * 1024 * 1024 #  10 MiB
        g_fh = logging.handlers.RotatingFileHandler(log_file ,'a',num_bytes,5) # filename, append, number of bytes max, number of logs max
        g_fh.setLevel(logging.DEBUG)
        g_ff = logging.Formatter('%(asctime)s - %(levelname)7s - %(message)s')
        g_fh.setFormatter(g_ff)
        web_logger.addHandler(g_fh)

        # write a quick new line, without any FileFormatter formatting
        g_fh.setFormatter(logging.Formatter('%(message)s')) # turn off the custom formatting too (just to write the next line)
        web_logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        g_fh.setFormatter(g_ff) # turn the formatting back on

        web_logger.info(f"Creating web log file!")
        web_logger.info(f"\tWeb Log file: {log_file}")
        local_web_log_file_path = log_file

    except (OSError, PermissionError) as error:
        web_logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        web_logger.warning(f"Unable to write to global log file!")
        web_logger.warning(f"\tWeb Log file: {log_file}")
        web_logger.warning(f"\t{error}")
        local_web_log_file_path = None # assign the None value if fileHandler failed

    # start the log
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    web_logger.info(f'----------------------------------------')
    web_logger.info(f'--- Starting new execution of script ---')
    web_logger.info(f'---        {timestamp}       ---')
    web_logger.info(f'----------------------------------------')
    web_logger.info(f'')

    # Force werkzeug (Flack) to use the same handlers as web_logger
    logging.getLogger("werkzeug").handlers = web_logger.handlers
    logging.getLogger("werkzeug").propagate = False
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("werkzeug").addFilter(LogEndpointFilter()) # prevent flooding the log files
    web_logger.addFilter(LogEndpointFilter())  # prevent flooding the log files

    return local_web_log_file_path

def graceful_exit(exit_code: int = 0) -> None:
    """
    Flush and close all logging handlers before exiting.
    Ensures monitor thread is stopped gracefully.
    """
    # since we are a bit Threaded, this will assure only 1 instance of graceful_exit is called
    if getattr(graceful_exit, "exit_done", False):
        return
    graceful_exit.exit_done = True

    # Flush logs
    for h in web_logger.handlers[:]:
        try:
            h.flush()
            h.close()
            web_logger.removeHandler(h)
        except Exception:
            pass

    # now exit
    if __name__ == "__main__":
        sys.exit(exit_code)
    else:
        web_logger.debug(f"graceful_exit called with code {exit_code}, not exiting (imported mode).")


def unique_timestamp_key(input_file: Optional[Path]) -> str:
    """
    Generate a unique key of the form:
    YYYY_MM_DD_HH_MM_SS_###
    using a global, thread-safe dictionary.
    """
    base = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    counter = 0

    while True:
        key = f"{base}_{counter:03d}"
        # Atomic check + insert
        with scoreboard_lock:
            if key not in scoreboard:
                scoreboard[key] = ScoreboardEntry(id=key, input_file=input_file)
                return key
        # increment counter if the key exists
        counter += 1

# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------

# No Entrypoint here; this module is imported and started from autotranslate.py

