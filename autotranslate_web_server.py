#!/usr/bin/env python3


# standard libraries
from flask import Flask, render_template, request, redirect, url_for, send_file
import logging.handlers
from pathlib import Path
from typing import Optional
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
import time
import sys


# non-standard imports
# intra-module imports
import autotranslate
# -----------------------------------------------------------------------
# Module-level variables (accessible to all functions in this file)
# -----------------------------------------------------------------------
app = Flask(__name__, template_folder="html")
web_logger = logging.getLogger("web")
stop_monitoring = threading.Event()
cfg_is_inited: bool = False
cfg: Optional[autotranslate.Config] = None
cfg_lock = threading.Lock()
scoreboard = {}
scoreboard_lock = threading.Lock()

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
# Web Server Functions
# -----------------------------------------------------------------------
@app.before_request
def ensure_initialized():
    EXCLUDED_ENDPOINTS = {"file_log"}
    if request.endpoint in EXCLUDED_ENDPOINTS:
        return None
    if not cfg_is_inited:
        return "Autotranslate service not initialized", 500
    
@app.route("/")
def index():
    # get the task ID if is was passed (e.g., /?job_id=2025_12_24_15_30_00_001); passed if redirected from /run, None otherwise
    job_id = request.args.get("job_id", None)

    with cfg_lock:
        global_log_filename = cfg.global_log_file_path.name if (cfg and cfg.global_log_file_path) else None

    with scoreboard_lock:
        jobs_log_filename = None
        if job_id and (job_id in scoreboard):
            entry = scoreboard[job_id]
            if entry.log_file is not None:
                jobs_log_filename = entry.log_file.name

    return render_template( "index.html",
                            cfg=cfg,
                            deepl_languages=autotranslate.get_deepl_languages(),
                            global_log_filename=global_log_filename,
                            jobs_log_filename=jobs_log_filename,
                            job_id=job_id,
                        )

@app.route("/run", methods=["POST"])
def run_translation():
    uploaded = request.files.get("pdf_file")
    if not uploaded:
        return "No PDF uploaded", 400
    # upload file to the tmp directory
    input_path = cfg.tmp_dir / uploaded.filename
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
        global scoreboard
        with scoreboard_lock:
            entry = scoreboard.get(scoreboard_key)
            if entry is not None:
                entry.log_file = path
        web_logger.info(f"Per-file log created: {path}")

    new_cfg.callback_on_local_log_file = capture_log_path

    # Callback: fires when translation is complete
    def capture_output_pdf(path: Path) -> None:
        global scoreboard
        with scoreboard_lock:
            entry = scoreboard.get(scoreboard_key)
            if entry is not None:
                entry.output_file = path
        web_logger.info(f"Output PDF created: {path}")

    new_cfg.callback_on_file_complete = capture_output_pdf

    # Run translation in background
    threading.Thread(
                    target=autotranslate.process_file,
                    args=(input_path, new_cfg),
                    daemon=True,
                ).start()

    return redirect(url_for("index", job_id=scoreboard_key))


@app.route("/check_output")
def check_output():
    # get the task ID if is was passed (e.g., /?job_id=2025_12_24_15_30_00_001); passed if redirected from /run, None otherwise
    job_id = request.args.get("job_id", None)

    output_file_path = None

    with scoreboard_lock:
        if job_id and (job_id in scoreboard):
            entry = scoreboard[job_id]
            output_file_path = entry.output_file

    if output_file_path:
        return {"ready": True, "filename": output_file_path.name}
    else:
        return {"ready": False, "filename": None}

@app.route("/output/<filename>")
def serve_output(filename: str):
    with cfg_lock:
        path = cfg.output_dir / filename
    return send_file(path)


@app.route("/log/<log_filename>")
def file_log(log_filename: str):
    with cfg_lock:
        log_dir = cfg.log_dir if cfg else None
    if not log_dir:
        log_dir = autotranslate.get_default_log_dir()
    log_path = log_dir / log_filename

    if not log_path.exists():
        return f"<p>No log file found: {log_path}</p>"

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tail = "".join(lines[-200:])

    html = f"""
    <html>
      <head>
        <meta http-equiv="refresh" content="2">
        <title>File Log</title>
      </head>
      <body>
        <h2>Log: {log_filename}</h2>
        <pre>{tail}</pre>
      </body>
    </html>
    """
    return html



# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------
def main() -> None:
    global web_logger

    setup_web_logging()

    monitor_thread = threading.Thread(target=run_directory_monitor, daemon=True)
    monitor_thread.start()

    while not cfg_is_inited:
        time.sleep(0.1)

    add_web_file_logging()

    app.run(host="0.0.0.0", port=5432)




# -----------------------------------------------------------------------
# Threaded Functions
# -----------------------------------------------------------------------

def run_directory_monitor():
    global cfg, cfg_is_inited
    try:
        web_logger.info("Monitor loop started")
        with cfg_lock:
            cfg = autotranslate.init_autotranslate()
            cfg_is_inited = True
        autotranslate.monitor_directory(cfg, stop_monitoring)
    except Exception as e:
        web_logger.error(f"Monitor loop exited: {e}")
        cfg_is_inited = False


# -----------------------------------------------------------------------
# Core Functions
# -----------------------------------------------------------------------

def unique_timestamp_key(input_file: Optional[Path]) -> str:
    """
    Generate a unique key of the form:
    YYYY_MM_DD_HH_MM_SS_###
    using a global, thread-safe dictionary.
    """
    global scoreboard
    
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

    except (OSError, PermissionError) as error:
        web_logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        web_logger.warning(f"Unable to write to global log file!")
        web_logger.warning(f"\tWeb Log file: {log_file}")
        web_logger.warning(f"\t{error}")
        g_fh = None # assign the None value if fileHandler failed
        log_file = None # assign the None value if fileHandler failed

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

    return

# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        web_logger.error(f"Fatal crash: {e}")
        # graceful_exit(1)