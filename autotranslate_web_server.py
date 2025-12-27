#!/usr/bin/env python3


# standard libraries
import atexit                                  # https://docs.python.org/3/library/atexit.html
import logging                                 # https://docs.python.org/3/library/logging.html
import logging.handlers                        # https://docs.python.org/3/library/logging.handlers.html
import signal                                  # https://docs.python.org/3/library/signal.html
import sys                                     # https://docs.python.org/3/library/sys.html
import threading                               # https://docs.python.org/3/library/threading.html
import time                                    # https://docs.python.org/3/library/time.html
from dataclasses import dataclass              # https://docs.python.org/3/library/dataclasses.html
from datetime import datetime                  # https://docs.python.org/3/library/datetime.html
from pathlib import Path                       # https://docs.python.org/3/library/pathlib.html
from typing import Dict, Optional, Union       # https://docs.python.org/3/library/typing.html

# non-standard imports
import deepl                                   # pip install --upgrade deepl   # https://github.com/DeepLcom/deepl-python
from flask import Flask, abort, render_template, render_template_string, request, redirect, url_for, send_file  # pip install flask # https://flask.palletsprojects.com/
from werkzeug.utils import secure_filename     # pip install werkzeug  # https://werkzeug.palletsprojects.com/

# intra-module imports
import autotranslate


# -----------------------------------------------------------------------
# CLASS DEFINITIONS
# -----------------------------------------------------------------------
@dataclass
class ScoreboardEntry:
    id: Optional[str] = None
    input_file: Optional[Path] = None
    log_file: Optional[Path] = None
    output_file: Optional[Path] = None
    # config: Optional[autotranslate.Config] = None # decided not to store full config

# -----------------------------------------------------------------------
# Module-level variables (accessible to all functions in this file)
# -----------------------------------------------------------------------
app = Flask(__name__, template_folder="html")
web_logger = logging.getLogger("web")
monitor_thread: Optional[threading.Thread] = None
monitor_thread_lock = threading.Lock()
stop_monitoring = threading.Event()
cfg_is_inited: bool = False
cfg: Optional[autotranslate.Config] = None
cfg_lock = threading.Lock()
scoreboard: Dict[str, ScoreboardEntry] = {}
scoreboard_lock = threading.Lock()
web_log_file_path: Optional[Path] = None
is_quota_exceeded: bool = False
is_fatal_error: bool = False
fatal_error_reason: Optional[str] = None


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------
def main() -> None:
    """
    Main entry point for the web server.

    Sets up logging, starts monitor thread, waits for init, adds file logging, runs Flask app.
    """
    global monitor_thread

    # init logger, and start output to STDOUT
    setup_web_logging()

    # Handle normal exits (atexit), and SIGINT/SIGTERM signals
    setup_web_exit_hooks()

    # launch autotranslate.py in directory monitoring mode
    # also need to get cfg initialized before starting web server
    monitor_thread = threading.Thread(target=run_directory_monitor, daemon=True)
    monitor_thread.start()

    # wait for cfg to come back from monitor thread
    while not cfg_is_inited:
        time.sleep(0.1)

    # Now setup the global log file (note: log_dir may not exist, fails gracefully)
    add_web_file_logging()

    # Start web server
    app.run(host="0.0.0.0", port=5432)




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
    if (not cfg_is_inited) or (not cfg):
        return "Autotranslate service not initialized", 500

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

    # if the whole program crashed (or just autotranslate's monitor
    if is_fatal_error:
        # General crash page
        global_log_link = ""
        with cfg_lock:
            if cfg and cfg.global_log_file_path:
                global_log_link = f'<a href="/log/{cfg.global_log_file_path.name}">View Global Log</a>'

        return f"""
        <html>
        <head><title>Service Crashed</title></head>
        <body>
        <h1>Translation Service Crashed</h1>
        <p>Error: {fatal_error_reason}</p>
        <p>{global_log_link}</p>
        <p><a href="/restart">Restart Service</a></p>
        </body>
        </html>
        """, 500

    # if the quota has been exceeded, display an error page
    if is_quota_exceeded:
        renewal_secs = autotranslate.num_seconds_till_renewal(cfg.usage_renewal_day if cfg else 0)
        return render_quota_exceeded_page(renewal_secs, "The translation quota has been exceeded. Please wait until the quota renews.")

    # now if everything is OK, render the main page
    with cfg_lock:
        global_log_filename = cfg.global_log_file_path.name if (cfg and cfg.global_log_file_path) else None

    with scoreboard_lock:
        jobs_log_filename = None
        if job_id and (job_id in scoreboard):
            entry = scoreboard[job_id]
            if entry.log_file is not None:
                jobs_log_filename = entry.log_file.name
    web_logger.info(f"\t Passed log file in scoreboard entry: {jobs_log_filename}")


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
        return "No PDF uploaded", 400
    # clean uploaded filename
    uploaded_filename = uploaded.filename
    if uploaded_filename is None:
        uploaded_filename = ""
    safe_name = secure_filename(uploaded_filename)
    input_path = cfg.tmp_dir / safe_name
    # upload file to the tmp directory
    uploaded.save(input_path)

    # Clone cfg for this run
    with cfg_lock:
        default_target_lang = cfg.target_lang
        new_cfg = autotranslate.Config(
                                        input_dir=cfg.input_dir,
                                        output_dir=cfg.output_dir,
                                        log_dir=cfg.log_dir,
                                        tmp_dir=cfg.tmp_dir,
                                        auth_key=cfg.auth_key,
                                        server_url=cfg.server_url,
                                        target_lang=cfg.target_lang,
                                        translate_filename=cfg.translate_filename,
                                        put_original_first=cfg.put_original_first,
                                        notify_urls=cfg.notify_urls,
                                        source_file=input_path,
                                    )
    # Extract form values
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

    with cfg_lock:
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
    # map types to directories locally inside the function
    dir_path = None
    with cfg_lock:
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
            log_link = f'<a href="/download/log/{entry.log_file.name}" target="_blank">Log</a>' if entry.log_file and entry.log_file.exists() else 'N/A'
            output_link = f'<a href="/output/{entry.output_file.name}" target="_blank">{entry.output_file.name}</a>' if entry.output_file and entry.output_file.exists() else 'N/A'
            jobs.append({
                'id': job_id,
                'input_file': entry.input_file.name if entry.input_file else 'N/A',
                'log_link': log_link,
                'output_link': output_link,
                'status': 'Completed' if entry.output_file and entry.output_file.exists() else 'In Progress'
            })

    html = """
    <html>
    <head>
        <title>Translation Scoreboard</title>
        <style>
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Translation Jobs Scoreboard</h1>
        <table>
            <tr>
                <th>Job ID</th>
                <th>Input File</th>
                <th>Status</th>
                <th>Log</th>
                <th>Output</th>
            </tr>
    """

    for job in jobs:
        html += f"""
            <tr>
                <td>{job['id']}</td>
                <td>{job['input_file']}</td>
                <td>{job['status']}</td>
                <td>{job['log_link']}</td>
                <td>{job['output_link']}</td>
            </tr>
        """

    html += """
        </table>
        <p><a href="/">Back to Main</a></p>
    </body>
    </html>
    """
    return html

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

    with cfg_lock:
        log_dir = cfg.log_dir if cfg else None
    if not log_dir:
        log_dir = autotranslate.get_default_log_dir()
    log_path = log_dir / log_filename

    if not log_path.exists() or not log_path.is_file():
        return f"<p>No log file found: {log_path}</p>", 404

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tail = "".join(lines[-200:])

    refresh_seconds = 30
    if mode == "realtime":
        refresh_seconds = 2
    # render template with client-side JS that waits, HEADs the same URL,
    # and only reloads if the server responds OK
    template = """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>Log: {{ log_filename }}</title>
        <script>
        (function() {
          const refreshSeconds = {{ refresh_seconds }};
          // Use the same path + query so HEAD hits this route
          const checkUrl = {{ check_url | tojson }};
          // Single delayed check; replace setTimeout with setInterval for repeated polling
          setTimeout(async () => {
            try {
              const resp = await fetch(checkUrl, { method: 'HEAD', cache: 'no-store' });
              if (resp.ok) {
                // resource exists -> reload the page to get fresh content
                window.location.reload();
              } else {
                // resource missing or server error -> do nothing
                console.info('Not refreshing: server returned', resp.status);
              }
            } catch (err) {
              // network error / server down -> do nothing
              console.info('Not refreshing: fetch failed', err);
            }
          }, refreshSeconds * 1000);
        })();
        </script>
      </head>
      <body>
        <h2>Log: <a href="/download/log/{{ log_filename }}" target="_blank">{{ log_filename }}</a></h2>
        <pre>{{ tail }}</pre>
      </body>
    </html>
    """

    # pass the full path+query so the HEAD request checks the same resource
    check_url = request.path
    if request.query_string:
        check_url += "?" + request.query_string.decode("utf-8")

    return render_template_string(
        template,
        refresh_seconds=refresh_seconds,
        check_url=check_url,
        log_filename=log_filename,
        tail=tail
    )



# -----------------------------------------------------------------------
# Threaded Functions
# -----------------------------------------------------------------------

def run_directory_monitor():
    """
    Run the directory monitoring loop in a separate thread.

    Initializes config and starts monitoring.
    If fails, sets crash state.
    """
    global cfg, cfg_is_inited, is_fatal_error, fatal_error_reason
    try:
        web_logger.info("Monitor loop started")
        with cfg_lock:
            cfg = autotranslate.init_autotranslate()
            cfg_is_inited = True
        autotranslate.monitor_directory(cfg, stop_monitoring)
    except (ValueError, autotranslate.ConfigurationError) as e:
        web_logger.error(f"Fundamental error in the configuration values.  All programs have stopped")
        web_logger.error(f"Configuration error: {e}")
        cfg_is_inited = False
        # Fatal error
        is_fatal_error = True
        fatal_error_reason = str(e)
    except Exception as e:
        # BTW, we ignore Quota errors, letting monitor_dir() and process_file() handle them differently
        web_logger.error(f"Monitor loop exited: {e}")
        cfg_is_inited = False
        # Fatal error
        is_fatal_error = True
        fatal_error_reason = str(e)


def run_process_file(file_path: Union[str, Path], config: autotranslate.Config) -> bool:
    global is_quota_exceeded, is_fatal_error, fatal_error_reason

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


# -----------------------------------------------------------------------
# Core Functions
# -----------------------------------------------------------------------

def render_quota_exceeded_page(renewal_secs: int, error_msg: str) -> str:
    """
    Render the quota exceeded page with countdown including days.
    """
    renewal_time = time.time() + renewal_secs
    html = f"""
    <html>
    <head>
        <title>Quota Exceeded</title>
        <script>
            function updateCountdown() {{
                const now = Date.now() / 1000;
                const remaining = {renewal_time} - now;
                if (remaining <= 0) {{
                    location.reload();
                }} else {{
                    const days = Math.floor(remaining / 86400);
                    const hours = Math.floor((remaining % 86400) / 3600);
                    const minutes = Math.floor((remaining % 3600) / 60);
                    const seconds = Math.floor(remaining % 60);
                    let countdown = '';
                    if (days > 0) countdown += days + 'd ';
                    if (hours > 0 || days > 0) countdown += hours + 'h ';
                    countdown += minutes + 'm ' + seconds + 's';
                    document.getElementById('countdown').textContent = countdown.trim();
                }}
            }}
            setInterval(updateCountdown, 1000);
        </script>
    </head>
    <body>
        <h1>Translation Quota Exceeded</h1>
        <p>{error_msg}</p>
        <p>Quota renews in: <span id="countdown"></span></p>
        <p><a href="/log/{cfg.global_log_file_path.name if cfg and cfg.global_log_file_path else ''}">View Global Log</a></p>
    </body>
    </html>
    """
    return html


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

def setup_web_logging():
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

def add_web_file_logging():
    """
    Add rotating file handler for web server logs.
    """
    global web_log_file_path
    with cfg_lock:
        log_dir = cfg.log_dir if cfg else None
    if not log_dir:
        log_dir = autotranslate.get_default_log_dir()
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
        web_log_file_path = log_file

    except (OSError, PermissionError) as error:
        web_logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        web_logger.warning(f"Unable to write to global log file!")
        web_logger.warning(f"\tWeb Log file: {log_file}")
        web_logger.warning(f"\t{error}")
        web_log_file_path = None # assign the None value if fileHandler failed

    # start the log
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    web_logger.info(f'----------------------------------------')
    web_logger.info(f'--- Starting new execution of script ---')
    web_logger.info(f'---        {timestamp}       ---')
    web_logger.info(f'----------------------------------------')
    web_logger.info(f'')


    # Filter to throttle /log endpoint requests from werkzeug
    class LogEndpointFilter(logging.Filter):
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

    # Force werkzeug (Flack) to use the same handlers as web_logger
    logging.getLogger("werkzeug").handlers = web_logger.handlers
    logging.getLogger("werkzeug").propagate = False
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("werkzeug").addFilter(LogEndpointFilter()) # prevent flooding the log files
    web_logger.addFilter(LogEndpointFilter())  # prevent flooding the log files

    return

def setup_web_exit_hooks():
    """
    Setup web exit hooks.
    """
    # setup web server exit hooks
    # Handle normal exits (atexit), and SIGINT/SIGTERM signals
    atexit.register(graceful_exit, 0)

    # Catch Ctrl-C (SIGINT) and container stop (SIGTERM)
    if threading.current_thread() is threading.main_thread():
        def handle_signal(signum, _frame):
            # newline
            web_logger.error("")

            sig_name = signal.Signals(signum).name
            if sig_name == "SIGTERM":
                web_logger.error("Program received SIGTERM (signal 15): container stop or `docker kill` request.")
            elif sig_name == "SIGINT":
                web_logger.error("Program received SIGINT (signal 2): interrupted by Ctrl-C.")
            else:
                web_logger.error(f"Program shutting down due to {sig_name} (signal {signum}).")
            graceful_exit(1)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

def graceful_exit(exit_code: int = 0) -> None:
    """
    Flush and close all logging handlers before exiting.
    Ensures monitor thread is stopped gracefully.
    """
    # since we are a bit Threaded, this will assure only 1 instance of graceful_exit is called
    if getattr(graceful_exit, "exit_done", False):
        return
    graceful_exit.exit_done = True

    # Stop monitor thread (run_directory_monitor(), let run_process_file() finish naturally)
    stop_monitoring.set()

    # Wait for monitor thread to finish (with timeout)
    if monitor_thread:
        monitor_thread.join(timeout=5.0)

    # Flush logs
    for h in web_logger.handlers[:]:
        try:
            h.flush()
            h.close()
            web_logger.removeHandler(h)
        except Exception as e:
            pass

    # now exit
    if __name__ == "__main__":
        sys.exit(exit_code)
    else:
        web_logger.debug(f"graceful_exit called with code {exit_code}, not exiting (imported mode).")

# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        web_logger.error(f"Fatal crash: {e}")
        graceful_exit(1)
