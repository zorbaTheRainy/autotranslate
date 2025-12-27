#!/usr/bin/env python3

"""
autotranslate.py
===========

Automated PDF translation workflow using DeepL and GoogleTranslator (for filename).

This script monitors input directories for PDF files, translates them via
DeepL's API (with optional filename translation using GoogleTranslator),
and writes results to output directories. It supports both single-file
and continuous directory-watch modes, with configurable logging,
container-aware defaults, and optional notifications via Apprise.

Features:
- Config dataclass for environment-aware defaults
- Command-line argument parsing and environment variable overrides
- Logging with console, rotating global log, and per-file logs
- Container detection (Docker, Podman, Kubernetes, etc.)
- Translation quota handling with renewal countdown
- Utility functions for safe type conversion
- Optional Apprise notifications
"""

# standard libraries
import argparse                                # https://docs.python.org/3/library/argparse.html
import atexit                                  # https://docs.python.org/3/library/atexit.html
import logging                                 # https://docs.python.org/3/library/logging.html
import logging.handlers                        # https://docs.python.org/3/library/logging.handlers.html
import os                                      # https://docs.python.org/3/library/os.html
import re                                      # https://docs.python.org/3/library/re.html
import shutil                                  # https://docs.python.org/3/library/shutil.html
import signal                                  # https://docs.python.org/3/library/signal.html
import string                                  # https://docs.python.org/3/library/string.html
import sys                                     # https://docs.python.org/3/library/sys.html
import threading                               # https://docs.python.org/3/library/threading.html
import time                                    # https://docs.python.org/3/library/time.html
from dataclasses import dataclass, field       # https://docs.python.org/3/library/dataclasses.html
from datetime import datetime                  # https://docs.python.org/3/library/datetime.html
from pathlib import Path                       # https://docs.python.org/3/library/pathlib.html
from typing import Any, Callable, Dict, List, Optional, Tuple, Union # https://docs.python.org/3/library/typing.html

# non-standard imports
import deepl                                   # pip install --upgrade deepl   # https://github.com/DeepLcom/deepl-python
import pendulum                                # pip install pendulum          # https://pendulum.eustace.io/docs/
import deep_translator.exceptions              # pip install deep-translator   # https://pypi.org/project/deep-translator/
from deep_translator import GoogleTranslator   # pip install deep-translator   # https://pypi.org/project/deep-translator/
from humanfriendly import format_timespan      # pip install humanfriendly     # https://humanfriendly.readthedocs.io/
from pypdf import PdfWriter                    # pip install pypdf             # https://pypdf.readthedocs.io/
from pypdf.errors import PdfReadError          # pip install pypdf             # https://pypdf.readthedocs.io/
from unidecode import unidecode                # pip install unidecode         # https://pypi.org/project/Unidecode/
from dotenv import load_dotenv                 # pip install dotenv            # https://pypi.org/project/python-dotenv/

# intra-module imports
from version import VERSION as __version__

# Optional Apprise import for notifications
try:
    import apprise                           # pip install apprise            # https://pypi.org/project/apprise/
    APPRISE_AVAILABLE = True
except ImportError:
    # apprise is not installed; notification functionality will be disabled
    APPRISE_AVAILABLE = False


# -----------------------------------------------------------------------
# Module-level variables (accessible to all functions in this file)
# -----------------------------------------------------------------------
logger = logging.getLogger()
# DEBUG variables
DEBUG_DUMP_VARS = True  # Set to True to enable config/args debug dump
DEBUG_NO_SEND_FILE = True  # Set to True to skip sending translated files (for testing)


# -----------------------------------------------------------------------
# CLASS DEFINITIONS
# -----------------------------------------------------------------------
@dataclass
class Config:
    """
    Central configuration object for translation workflow.

    Attributes:
        input_dir (Path): Directory to watch for input files.
        output_dir (Path): Directory to place translated files.
        log_dir (Path): Directory to write logs.
        tmp_dir (Path): Temporary working directory.
        auth_key (str): DeepL API authentication key.
        server_url (str): Custom DeepL server URL (optional).
        target_lang (str): Target language code (e.g., "EN-US").
        check_period_min (float): Polling interval in minutes.
        usage_renewal_day (int): Day of month usage renews.
        put_original_first (bool): Whether to place original before translation in merged PDFs.
        translate_filename (bool): Whether to translate filenames.
        notify_urls: List[str]:  Commas separated list of URLs in Apprise format
    """
    # Directory paths
    input_dir:  Path = Path("/inputDir")  # note: if outside a container, this is changed to ./input  in build_config()
    output_dir: Path = Path("/outputDir") # note: if outside a container, this is changed to ./output
    log_dir:    Path = Path("/logDir")    # note: if outside a container, this is changed to ./logs
    tmp_dir:    Path = Path("/tmp")       # note: if outside a container, this is changed to /tmp
    # API and server settings
    auth_key:   str = ""
    server_url: str = ""
    # Translation settings
    source_file:        Optional[Path] = None
    target_lang:        str = "EN-US"
    check_period_min:   float = 15
    usage_renewal_day:  int = 0
    put_original_first: bool = False
    translate_filename: bool = False
    notify_urls: List[str] = field(default_factory=list)
    # Run-time variables (not set via args or env)
    global_log_file_handler: Optional[logging.FileHandler] = None
    global_log_file_path: Optional[Path] = None
    callback_on_local_log_file: Optional[Callable[[Path], None]] = None
    callback_on_file_complete: Optional[Callable[[Path], None]] = None

@dataclass
class ConfigNonContainerDefaults(Config):
    # Directory paths
    input_dir:  Path = Path("./folders/input")  # note: if outside a container, this is changed to ./input  in build_config()
    output_dir: Path = Path("./folders/output") # note: if outside a container, this is changed to ./output
    log_dir:    Path = Path("./folders/logs")    # note: if outside a container, this is changed to ./logs
    tmp_dir:    Path = Path("/tmp")       # note: if outside a container, this is changed to /tmp

class EmptyArgs(argparse.Namespace):
    """
    Empty arguments namespace with all attributes returning None.
    """
    def __getattr__(self, name):
        return None

class ConfigurationError(Exception):
    """Custom exception raised when there is an issue with the configuration."""
    def __init__(self, message: str = "Configuration error"):
        super().__init__(message)

class FilenameCleanseError(Exception):
    """Custom exception raised when filename sanitization fails."""
    def __init__(self, message: str = "Filename sanitization failed"):
        super().__init__(message)

class QuotaExceededException(Exception):
    """
    Exception raised when translation quota is exceeded.

    Wraps the original exception and copies its attributes for easier
    downstream handling.
    """
    def __init__(self, original_exc: Exception):
        # Call base Exception with the same message
        super().__init__(str(original_exc))
        self.original_exc = original_exc

        # Copy over all attributes from the original exception
        for attr, value in vars(original_exc).items():
            try:
                setattr(self, attr, str(value) if value is not None else "")
            except Exception:
                setattr(self, attr, "")

class SizeBasedFilter(logging.Filter):
    """
    Logging filter that suppresses or downgrades oversized log messages.

    Args:
        max_length (int): Maximum allowed message length. Messages longer
                          than this are downgraded to INFO and replaced
                          with a placeholder.
    """
    def __init__(self, max_length=500):
        super().__init__()
        self.max_length = max_length

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if len(msg) > self.max_length:
            # Option 1: downgrade to INFO
            record.levelno = logging.INFO
            record.levelname = "INFO"

            # Option 2: replace message entirely
            record.msg = f"[suppressed oversized log message: {len(msg)} chars]"
            record.args = ()
        return True  # Always allow record through

class BufferedAppriseHandler(logging.Handler):
    """
    A logging handler that buffers ERROR-level messages and sends them as a
    single Apprise notification.

    Args:
        notify_urls (list[str]): Apprise notification URLs (e.g., Slack, Discord, email).
        flush_interval (float): Seconds to wait before bundling and sending messages.

    Attributes:
        buffer (list[str]): Collected log messages.
        last_flush (float): Timestamp of last flush.
        apobj (apprise.Apprise): Apprise client used to send notifications.

    Methods:
        emit(record): Add a log record to the buffer and flush if interval elapsed.
        flush(): Send buffered messages as one notification and clear the buffer.
    """
    def __init__(self, notify_urls, flush_interval=2.0):
        super().__init__(level=logging.ERROR)
        self.apobj = apprise.Apprise()
        for url in notify_urls:
            self.apobj.add(url)
        self.buffer = []
        self.last_flush = time.time()
        self.flush_interval = flush_interval  # seconds

    def emit(self, record):
        msg = self.format(record)
        self.buffer.append(msg)

        # If enough time has passed, flush as one notification
        if time.time() - self.last_flush >= self.flush_interval:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        try:
            body = "\n".join(self.buffer)
            # Only notify if Apprise is still usable
            self.apobj.notify(
                body=body,
                title="Logger Errors",
                body_format="text"
            )
        except RuntimeError as e:
            # Avoid raising during interpreter shutdown
            logging.debug(f"Apprise flush skipped: {e}")
        finally:
            self.buffer.clear()
            self.last_flush = time.time()

    def close(self):
        """
        Ensure buffered messages are flushed safely before shutdown.
        """
        try:
            self.flush()
        except RuntimeError:
            pass
        super().close()

# -----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the translation script.

    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(description="Auto-translate files via a translation web service.", epilog=f"Version: {__version__}")
    # Positional single-file mode
    parser.add_argument("file", nargs="?", help="Single file to translate (single-file mode).")

    # Directories and API
    parser.add_argument("-i", "--input-dir",   help="Directory to watch for input files.")
    parser.add_argument("-o", "--output-dir",  help="Directory to place translated files.")
    parser.add_argument("-l", "--log-dir",     help="Directory to write logs.")
    parser.add_argument("-k", "--auth-key", "--api-key", help="API/Auth key for translation service.")
    parser.add_argument("-s", "--server-url",  help="Translation server URL (DEEPL_SERVER_URL).")
    parser.add_argument("-t", "--target-lang", help="Target language (DEEPL_TARGET_LANG).")
    parser.add_argument("-c", "--check-every-x-minutes", type=float, help="Polling interval in minutes.")
    parser.add_argument("-r", "--renewal-day", type=int, help="Day of month usage renews (DEEPL_USAGE_RENEWAL_DAY).")
    parser.add_argument("-B", "--original-before-translation", dest="original_before_translation",
                                        action="store_const", const=True, default=None,
                                        help="If set, copy original to output before translation.")
    parser.add_argument("-N", "--translate-filename", dest="translate_filename",
                                        action="store_const", const=True, default=None,
                                        help="If set, modify filename to indicate translation.")
    parser.add_argument("-u", "--notify-urls", help="Comma-separated Apprise URLs for notifications.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args()


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------
def main() -> None:
    """
    Entry point for the translation workflow.

    - Initializes logging
    - Parses arguments and builds config
    - Validates configuration and directories
    - Connects to translation API
    - Runs in single-file or directory-watch mode
    - Handles quota exceeded events with renewal countdown
    """

    try:
        cfg = init_autotranslate()
    except (ConfigurationError, ValueError) as e:
        logger.error(e)
        # close the global log file and any Apprise handlers and exit
        graceful_exit(2)   # Fatal error

    # connect with the translation API before we do any processing
    translator = confirm_api_connection(cfg.auth_key, cfg.server_url)
    if translator is None:
        logger.error("Unable to open translator.")
        logger.error("Program closing")
        # close the global log file and any Apprise handlers and exit
        graceful_exit(2)   # Fatal error


    # now we move into doing work
    if cfg.source_file is not None:
        # single file mode
        try:
            process_file(cfg.source_file, cfg)
        except Exception as e:
            # this is really not a big deal for a 1-and-done file translation
            if isinstance(e, QuotaExceededException):
                logger.error("Translation quota exceeded during file processing.")
            elif isinstance(e, deepl.DeepLException):
                logger.error("DeepL API error occurred during file processing.")
            else:
                logger.error("Unexpected error occurred during file processing.")
            logger.error(f"{e}")
            # close the global log file and any Apprise handlers and exit
            if isinstance(e, QuotaExceededException):
                graceful_exit(3)  # Quota exceeded exit code
            else:
                graceful_exit(2)  # Fatal error
    else:
        # directory mode
        try:
            monitor_directory(cfg)
        except Exception as e:
            if isinstance(e, deepl.DeepLException):
                logger.error("DeepL API error occurred during file processing.")
            else:
                logger.error("Unexpected error occurred during file processing.")
            logger.error(f"{e}")
            graceful_exit(2)  # Fatal error


    # close the global log file and any Apprise handlers and exit
    graceful_exit(0)




# -----------------------------------------------------------------------
# Core Functions
# -----------------------------------------------------------------------

def init_autotranslate() -> Config:
    """
    Initialize the autotranslate module.

    - Sets up logging
    - Registers exit hooks
    - Loads environment variables
    - Parses command-line arguments
    - Builds and validates configuration
    - Validates directories

    Returns:
        Tuple[Config, argparse.Namespace]: Configuration object and parsed arguments.
    """
    # init logger, and start output to STDOUT
    setup_logging()

    # Handle normal exits (atexit), and SIGINT/SIGTERM signals
    setup_exit_hooks()

    # read the commandline args and ENVs, setup cfg object
    load_dotenv()  # by default, looks for a .env file in the current directory
    args = parse_args()
    # Merge ENVs with the args, args taking precedence
        # Note: args.file does not migrate to cfg, as it is only for single file mode.
    cfg = build_config(args)

    # Now setup the global log file (note: log_dir may not exist, fails gracefully)
    cfg.global_log_file_handler, cfg.global_log_file_path = add_global_file_logger(cfg.log_dir)

    # Setup Apprise notifications on Errors
    if APPRISE_AVAILABLE:
        add_apprise_notifications_logger(cfg.notify_urls)

    # debug stuff
    if DEBUG_DUMP_VARS:
        debug_dump(args, name="args")
        debug_dump(cfg, name="Config")

    # Ensure all config variables have valid values
    cfg = validate_cfg_variables(cfg) # may raise ValueError()
    # Ensure all dirs exist and are writable
    validate_directories(cfg) # may raise ConfigurationError()

    return cfg


def setup_logging() -> None:
    """
    Configure global logger with console handler and filters.

    - Sets DEBUG level
    - Adds console handler with simple formatter
    - Applies SizeBasedFilter to suppress oversized messages
    """

    # logger = logging.getLogger()  # defined at module (global) level
    # Avoid adding duplicate handlers if setup_logging is called multiple times
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG) # sets the level below which _no_ handler may go

    # Console/STDOUT handler
    ch = logging.StreamHandler(sys.stdout)  # well this is annoying.  StreamHandler is logging.* while newer handlers are logging.handlers.*
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter('%(message)s'))

    # add filtering as DeepL library is _very_ verbose at DEBUG level (i.e., it dumps a binary file into the log)
    # ch.addFilter(DowngradeEncodingDetectionFilter())
    ch.addFilter(SizeBasedFilter(max_length=500))  # suppress messages longer than 5000 characters

    logger.addHandler(ch)

    return


def setup_exit_hooks():
    """
    Register cleanup hooks for graceful shutdown.

    Ensures `graceful_exit()` runs on normal interpreter exit (atexit),
    and on SIGINT (Ctrl-C) or SIGTERM (container stop).
    """

    # Run graceful_exit on normal interpreter shutdown
    atexit.register(graceful_exit, 0)

    # Catch Ctrl-C (SIGINT) and container stop (SIGTERM)
    if threading.current_thread() is threading.main_thread():
        def handle_signal(signum, _frame):
            # newline
            logging.error("")

            sig_name = signal.Signals(signum).name  # e.g. "SIGTERM"
            if sig_name == "SIGTERM":
                logging.error("Program received SIGTERM (signal 15): container stop or `docker kill` request.")
            elif sig_name == "SIGINT":
                logging.error("Program received SIGINT (signal 2): interrupted by Ctrl-C.")
            else:
                logging.error(f"Program shutting down due to {sig_name} (signal {signum}).")
            graceful_exit(1)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)


def get_default_log_dir(failsafe: Optional[Union[Path, str]] = "/tmp") -> Path:
    env_value = os.getenv("LOG_DIR")
    if env_value:
        return Path(env_value)

    if is_in_container():
        cfg_value = Config().log_dir
    else:
        cfg_value = ConfigNonContainerDefaults().log_dir
    if cfg_value:
        return Path(cfg_value)  

    return Path(failsafe)


def build_config(args: Optional[argparse.Namespace] = None) -> Config:
    """
    Build configuration object from CLI args and environment variables.

    Args:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        Config: Fully populated configuration object.
    """
    
    # for validating and parsing notify_urls
    def parse_string_list(value: Optional[str]) -> List[str]:
        if not value:  # catches None or ""
            return []
        return [s.strip() for s in value.split(",") if s.strip()]

    # get default values
    if not args:
        args = EmptyArgs() # create an args object with all None values
    default = Config()
    new_cfg = Config()

    # make some changes if we're running outside a container
    if not is_in_container():
        default_special = ConfigNonContainerDefaults()
        default.input_dir   = default_special.input_dir
        default.output_dir  = default_special.output_dir
        default.log_dir     = default_special.log_dir
        default.tmp_dir     = default_special.tmp_dir 


    # Map CLI args and environment variables. CLI overrides env.
    new_cfg.input_dir           = Path( to_str( arg_or_env( args.input_dir,             "INPUT_DIR"),             str(default.input_dir) ))
    new_cfg.output_dir          = Path( to_str( arg_or_env( args.output_dir,            "OUTPUT_DIR"),            str(default.output_dir) ))
    new_cfg.log_dir             = Path( to_str( arg_or_env( args.log_dir,               "LOG_DIR"),               str(get_default_log_dir()) ))
    new_cfg.tmp_dir             = default.tmp_dir # is_in_container() decides this via the if statement above
    new_cfg.auth_key            = to_str(arg_or_env( args.auth_key,                     "DEEPL_AUTH_KEY"),           "")
    new_cfg.server_url          = to_str(arg_or_env( args.server_url,                   "DEEPL_SERVER_URL"),         "")
    new_cfg.target_lang         = to_str(arg_or_env( args.target_lang,                  "DEEPL_TARGET_LANG"),        str(default.target_lang) )
    new_cfg.usage_renewal_day   = to_int(arg_or_env( args.renewal_day,                  "DEEPL_USAGE_RENEWAL_DAY"),  default.usage_renewal_day)
    new_cfg.check_period_min    = to_float(arg_or_env( args.check_every_x_minutes,      "CHECK_EVERY_X_MINUTES")  ,  default.check_period_min)
    new_cfg.translate_filename  = to_bool(arg_or_env( args.translate_filename,          "TRANSLATE_FILENAME")     ,  default.translate_filename)
    new_cfg.put_original_first  = to_bool(arg_or_env( args.original_before_translation, "ORIGINAL_BEFORE_TRANSLATION"), default.put_original_first)

    if args.file is not None:
        new_cfg.source_file = Path(args.file)

    if APPRISE_AVAILABLE:
        # Convert strings to lists
        notify_urls_args_raw = parse_string_list( args.notify_urls)
        notify_urls_env_raw  = parse_string_list( os.getenv("NOTIFY_URLS", "") )
        # Combine and deduplicate while preserving order
        logger.debug(f"Validating and combining notify_urls")
        logger.debug(f"Apprise will do this via threads.  So, expect the log messages to occur out of order.")
        logger.debug(f"The pre-Apprise process is to combine the arg & ENV, then ask Apprise to validate them, if valid it will them append them in to the final notify_urls.")
        seen = set()
        combined = []
        for item in notify_urls_args_raw + notify_urls_env_raw:
            if item not in seen:
                logger.debug(f"Apprise, Combining URL: {item}")
                seen.add(item)
                combined.append(item)
        new_cfg.notify_urls = combined

        # moved the verification to this point because the Apprise URLs get used before validate_cfg_variables()
        if new_cfg.notify_urls and APPRISE_AVAILABLE:
            valid_urls = []
            for url in new_cfg.notify_urls:
                if not url or not url.strip():
                    continue
                logger.debug(f"Apprise, Validating URL: {url}")
                # Apprise returns True if the URL is valid and supported
                if apprise.Apprise().add(url.strip()):
                    logger.debug(f"Apprise, Appending URL: {url}")
                    valid_urls.append(url.strip())
            new_cfg.notify_urls = valid_urls
    else:
        new_cfg.notify_urls = []

    # all bounds checking done in validate_cfg_variables(cfg)

    # set a default value, which is hopefully the value from the last run
    # new_cfg.global_log_file_path = new_cfg.log_dir / f"_{__name__}.log"
    new_cfg.global_log_file_path = new_cfg.log_dir /f"_{Path(__file__).stem}.log"

    return new_cfg


def add_global_file_logger(log_dir: Path, log_filename: str = f"_{Path(__file__).stem}.log") -> Tuple[Optional[logging.FileHandler], Optional[Path]]:
    """
    Add a rotating global log file handler.

    Args:
        log_dir (Path): Directory for log file.
        log_filename (str): Log filename.

    Returns:
        (logging.FileHandler, Path): File handler and log file path.
    """

    # Global log file
    g_fh = None # assign the None value if fileHandler failed
    log_file = log_dir / log_filename

    try:
        # confirm log_dir exists, throw error otherwise
        log_dir.mkdir(parents=True, exist_ok=True)

        num_bytes = 10  * 1024 * 1024 #  10 MiB
        g_fh = logging.handlers.RotatingFileHandler(log_file ,'a',num_bytes,5) # filename, append, number of bytes max, number of logs max
        g_fh.setLevel(logging.DEBUG)
        g_ff = logging.Formatter('%(asctime)s - %(levelname)7s - %(message)s')
        g_fh.setFormatter(g_ff)
        logger.addHandler(g_fh)

        # write a quick new line, without any FileFormatter formatting
        g_fh.setFormatter(logging.Formatter('%(message)s')) # turn off the custom formatting too (just to write the next line)
        logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        g_fh.setFormatter(g_ff) # turn the formatting back on

        logger.info(f"Creating global log file!")
        logger.info(f"\tGlobal Log file: {log_file}")

    except (OSError, PermissionError) as error:
        logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
        logger.warning(f"Unable to write to global log file!")
        logger.warning(f"\tGlobal Log file: {log_file}")
        logger.warning(f"\t{error}")
        g_fh = None # assign the None value if fileHandler failed
        log_file = None # assign the None value if fileHandler failed

    # start the log
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f'----------------------------------------')
    logger.info(f'--- Starting new execution of script ---')
    logger.info(f'---        {timestamp}       ---')
    logger.info(f'----------------------------------------')
    logger.info(f'')

    return g_fh, log_file


def add_file_logger(log_dir: Path, log_filename: str) -> Tuple[Optional[logging.FileHandler], Optional[Path]]:
    """
    Add a per-file log handler with timestamped filename.

    Args:
        log_dir (Path): Directory for log file.
        log_filename (str): Base filename.

    Returns:
        (logging.FileHandler, Path): File handler and log file path.
    """

    # create a timestamp safe for filenames (no colons)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    # setup the individual (non-global) log file
    basename = Path(log_filename).stem
    log_file_path = log_dir /f"{basename}_{timestamp}.log"

    file_handler = None
    try:
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)7s - %(message)s'))
        logger.addHandler(file_handler)
    except (OSError, PermissionError) as error:
        logger.warning(f"Unable to write to individual log file!")
        logger.warning(f"\tIndividual Log file: {log_file_path}")
        logger.warning(f"\t{error}")

    return file_handler, log_file_path


def close_file_logger(filehandler: Optional[logging.FileHandler], log_file: Optional[Union[Path, str]] = None) -> None:
    """
    Close and remove a file handler from the logger.

    Args:
        filehandler (logging.FileHandler): File handler to close.
        log_file (Path|str, optional): Path to the log file for debug output.
    """
    try:
        if filehandler is not None:
            logger.removeHandler(filehandler) # close the log before moving on to the next file
    except (OSError, ValueError) as error:
        logger.debug(f"Unable to close log file.  (Probably never opened in the 1st place.)")
        if log_file is not None:
            logger.debug(f"\tLog file: {log_file}")
        logger.debug(f"\t{error}")
    return


def add_apprise_notifications_logger(notify_urls: Optional[List[str]] = None, flush_interval_seconds: float = 2.0) -> Optional[logging.Handler]:
    """
    Attach an Apprise notifications handler to the given logger.

    Args:
        logger (logging.Logger): The logger to attach the handler to.
        notify_urls (list[str] | None): List of Apprise notification URLs.
            If None, will read from NOTIFY_URLS environment variable (comma-separated).
        flush_interval (float): Seconds to wait before bundling and sending messages.

    Returns:
        logging.Handler: The Apprise handler attached to the logger.
    """
    if notify_urls is None:
        return None

    logger.info(f"Creating Apprise notifications handler.")
    handler = BufferedAppriseHandler(notify_urls, flush_interval=flush_interval_seconds)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return handler


def validate_cfg_variables(cfg: Config) -> Config:
    """
    Validate and normalize configuration values.

    Args:
        cfg (Config): Configuration object to validate.

    Returns:
        Config: Updated configuration with corrected values.

    Raises:
        ValueError: If mandatory fields (e.g., auth_key, target_lang) are invalid.
    """

    # get default values
    default_cfg = Config()

    # Mandatory API key
    if (not cfg.auth_key) or (cfg.auth_key.strip() == ""):
        raise ValueError("Auth key is mandatory but missing or empty.")

    # Check if target language is supported
    target_lang = get_valid_deepl_target_lang(cfg.target_lang)
    if target_lang is None:
        raise ValueError(f"Target Language is not a valid DeepL target language code: {cfg.target_lang}.")
    else:
        cfg.target_lang = target_lang

    # some basic bounds checking
    if (cfg.usage_renewal_day <= 0) or (cfg.usage_renewal_day > 31):
        cfg.usage_renewal_day = 0
        logger.error(f"Usage Renewal Day out of bounds and set to 0 (no renewal day).")

    if cfg.check_period_min <= 0:
        cfg.check_period_min = default_cfg.check_period_min
        logger.error(f"Check Directory Period (minutes) out of bounds and set to {default_cfg.check_period_min}.")

    # Add more validations as needed

    return cfg


def get_deepl_languages() -> Dict:
    """
    Retrieve supported DeepL target languages.

    Returns:
        Dict: Mapping of language codes to language names.
    """

    # https://developers.deepl.com/docs/getting-started/supported-languages#translation-target-languages
    DEEPL_LANGUAGES: Dict[str, str] = {
        "AR": "Arabic",
        "BG": "Bulgarian",
        "CS": "Czech",
        "DA": "Danish",
        "DE": "German",
        "EL": "Greek",
        "EN": "English",  # unspecified variant for backward compatibility; we recommend using EN-GB or EN-US instead)
        "EN-GB": "English (British)",
        "EN-US": "English (American)",
        "ES": "Spanish",
        "ES-419": "Spanish (Latin American)",
        "ET": "Estonian",
        "FI": "Finnish",
        "FR": "French",
        "HU": "Hungarian",
        "ID": "Indonesian",
        "IT": "Italian",
        "JA": "Japanese",
        "KO": "Korean",
        "LT": "Lithuanian",
        "LV": "Latvian",
        "NB": "Norwegian (Bokmål)",
        "NL": "Dutch",
        "PL": "Polish",
        "PT": "Portuguese", # (unspecified variant for backward compatibility; we recommend using PT-BR or PT-PT instead)
        "PT-BR": "Portuguese (Brazilian)",
        "PT-PT": "Portuguese (European)", # (all Portuguese variants excluding Brazilian Portuguese)
        "RO": "Romanian",
        "RU": "Russian",
        "SK": "Slovak",
        "SL": "Slovenian",
        "SV": "Swedish",
        "TR": "Turkish",
        "UK": "Ukrainian",
        "ZH": "Chinese", #  (unspecified variant for backward compatibility; we recommend using ZH-HANS or ZH-HANT instead)
        "ZH-HANS": "Chinese (simplified)",
        "ZH-HANT": "Chinese (traditional)"
    }

    return DEEPL_LANGUAGES


def get_valid_deepl_target_lang(lang_code: str) -> Optional[str]:
    """
    Validate and normalize a DeepL target language code.
    Can take code or language name, if the same as in the DeepL docs.
    https://developers.deepl.com/docs/getting-started/supported-languages#translation-target-languages

    Args:
        lang_code (str): Language code to validate.

    Returns:
        Optional[str]: Valid DeepL code, or None if unsupported.
    """

    # https://developers.deepl.com/docs/getting-started/supported-languages#translation-target-languages
    DEEPL_LANGUAGES = get_deepl_languages()

    # Exceptions
    if lang_code.strip().lower() in ("zh-cn", "zh", "chinese (simplified)"):
        lang_code = "zh-hans"
    elif lang_code.strip().lower() in ("no", "norwegian"):
        lang_code = "nb"
    elif lang_code.strip().lower() in ("en-ca", "en-ph"):
        lang_code = "en-us"
    elif lang_code.strip().lower() in ("en", "english", "en-au", "en-nz", "en-za", "en-in", "en-ie", "en-sg", "en-hk", "en-uk"):
        lang_code = "en-gb"
    elif lang_code.strip().lower() in ("pt", "portuguese"):
        lang_code = "pt-pt"

    # Normal processing
    code_format = lang_code.strip().upper()
    name_format = lang_code.strip().lower()

    for code, name in DEEPL_LANGUAGES.items():
        if code_format == code.upper() or name_format == name.lower():
            return code
    return None


def validate_directories(cfg: Config) -> bool:
    """
    Ensure required directories exist and are writable.

    Args:
        cfg (Config): Configuration object.

    Returns:
        bool: True if all directories are valid, False otherwise.
    """
    # Ensure all directories exist
    # Directories to check
    dirs = {
        "input_dir": cfg.input_dir,
        "output_dir": cfg.output_dir,
        "tmp_dir": cfg.tmp_dir,
        "log_dir": cfg.log_dir,
    }

    can_continue = True
    for name, path in dirs.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not os.access(str(path), os.W_OK):
                raise PermissionError("No write permission")
        except (OSError, PermissionError) as error:
            logger.error(f"Cannot write to required directory: {name}")
            logger.error(f"\t{path}")
            logger.error(f"\t{error}")
            if name == "log_dir":
                logger.warning(f"Program will still continue, but this should be attended to.")
            else:
                # sys.exit(2)  # Fatal error
                can_continue = False # exit in the above calling function
                raise ConfigurationError(error)

    return can_continue


def confirm_api_connection(auth_key: str, server_url: str = "") -> Optional[deepl.DeepLClient]:
    """
    Attempt to establish connection with DeepL API.

    Args:
        auth_key (str): DeepL API authentication key.
        server_url (str): Optional custom server URL.

    Returns:
        deepl.DeepLClient|None: Client if successful, None otherwise.

    Raises:
        AuthorizationException: Invalid API key.
        ConnectionException: Network error.
        DeepLException: API error.
        ValueError: Invalid arguments.
        OSError: System/network error.
    """

    def log_and_raise(msg: str, error: Exception):
        logger.error(msg)
        logger.error(f"\t{error}")
        raise error

    translator = None
    # deepl.http_client.max_network_retries = 3 # default is 5 retires; I see no reason to change it
    try:
        logger.info(f"Attempting to establish communication with the Web API server.")
        if server_url != "":
            logger.info(f"\tUsing custom Web API server: {server_url}") # keep as INFO as it is such a rare occurrence that the event should be noted in the log
            translator = deepl.DeepLClient(auth_key, server_url=server_url)
        else:
            logger.debug(f"\tUsing normal Web API server.")
            translator = deepl.DeepLClient(auth_key)
    except deepl.AuthorizationException as error:
        log_and_raise("Authorization failed: invalid API key.", error)
    except deepl.ConnectionException as error:
        log_and_raise("Connection error: unable to reach DeepL API server.", error)
    except deepl.DeepLException as error:
        log_and_raise("DeepL API returned an error.", error)
    except ValueError as error:
        log_and_raise("Invalid argument provided to DeepLClient.", error)
    except OSError as error:
        log_and_raise("System/network error occurred while connecting to DeepL API.", error)

    return translator


def process_file(file_path: Union[str, Path], cfg: Config) -> bool:
    """
    Process a single file for translation.

    Args:
        file_path (Union[str, Path]): Path to the input file.
        cfg (Config): Configuration object.
        translator (deepl.DeepLClient): DeepL translator client.

    Returns:
        bool: True if processing and translation succeeded, False otherwise.
    """

    # ----------------------------------------------------
    # Setup all variables and logfile

    # make sure this is a Path object
    file_path = Path(file_path)

    # establish individual log file
    file_handler, log_file_path = add_file_logger(cfg.log_dir, file_path.name)
    if cfg.callback_on_local_log_file:
        cfg.callback_on_local_log_file(log_file_path)
        logger.debug(f"Called back to extra-module after creating local log file: {log_file_path}")

    # report start of processing
    logger.info(f"{'-'*75}")
    logger.info(f'Processing file: {file_path.name}') # note the usage on the un-cleaned file name for this log entry
    logger.info(f"{'-'*75}")
    logger.info(f'Configuration values') # note the usage on the un-cleaned file name for this log entry
    logger.info(f'\t Full input file: {file_path}')
    logger.info(f'\t Output directory: {cfg.output_dir}')
    logger.info(f'\t Temporary directory: {cfg.tmp_dir}')
    logger.info(f'\t Log directory: {cfg.log_dir}')
    logger.info(f'\t Target language: {cfg.target_lang}')
    logger.info(f'\t Translate filename: {cfg.translate_filename}')
    logger.info(f'\t Put original before translation: {cfg.put_original_first}')
    logger.info(f"{'-'*75}")


    # create variables for all the files
    file_paths = generate_file_path_vars(file_path, cfg)
    if file_paths is None:
        return False # indicate failure to process the file
    # to be honest it is easiest to pass them around as a tuple.
    input_file_path, tmp_file_path, output_file_path = file_paths
    result = False

    # ----------------------------------------------------
    # Perform translation

    try:
        if input_file_path.exists():
            # translate the document
            translator = confirm_api_connection(cfg.auth_key, cfg.server_url)
            # Exception may be passed up from confirm_api_connection() and passed through to the calling subroutine to handle
            if translator is None:
                logger.error("Translator object is None, cannot proceed with translation.")
                return False
            else:
                if file_handler is not None:
                    saved_level = file_handler.level
                    file_handler.setLevel(logging.DEBUG) # turn on DEBUG logging to see the DeepL HTTP traffic
                result = send_document_to_server(input_file_path, tmp_file_path, cfg.target_lang, translator)
                if file_handler is not None:
                    file_handler.setLevel(saved_level)
        else:
            logger.error(f"Input file does not exist: {input_file_path}")
            return False

        # append the translated PDF to the original PDF and put in outputDir
        if os.path.exists(tmp_file_path):
            result = append_pdfs(input_file_path, tmp_file_path, output_file_path, cfg.put_original_first)
        else:
            logger.warning(f"Temporary file does not exist: {tmp_file_path}")
            result = False

    except PermissionError as e:
        logger.error(f"Permission denied when accessing path: {e}")
        logger.error(f"Permission error: {e}")
        return False
    except OSError as e:
        logger.error(f"OS error: {e}")
        return False
    # QuotaExceededException may be passed up from send_document_to_server() and passed through to the calling subroutine to handle
    # getUsage() # annoyingly DeepL doesn't update their usage quickly.  So, this line got  removed as worthless.

    # ----------------------------------------------------
    # Delete tmp files and close log file

    # clean up the old files, make sure that input_file_path aren't re-translated at another date
    if output_file_path.exists(): # if we successfully created the output_file_path
        logger.info(f"Cleaning up input & temporary files.")
        delete_file(tmp_file_path)
        delete_file(input_file_path)  # may be the translated file in the tmp directory
        if file_path.exists():
            delete_file(file_path)        # may be the original file in the input directory
        # report out to any extra-module callers that the file is done
        if cfg.callback_on_file_complete:
            cfg.callback_on_file_complete(output_file_path)
            logger.debug(f"Called back to extra-module after completing file: {output_file_path}")


    # send the file via Apprise
    if output_file_path.exists() and APPRISE_AVAILABLE:
        send_apprise_message( title='Translated file',
                                body=f"Translation complete: {output_file_path.name}",
                                attach=output_file_path)


    logger.info(f"{'-'*75}")
    logger.info(f'Finished processing file.')
    logger.info(f'\t Translated output file: {output_file_path}')

    # close the individual log file
    close_file_logger(file_handler, log_file_path)

    return result


def generate_file_path_vars(file_path: Union[str, Path], cfg: Config) -> Optional[Tuple[Path, Path, Path]]:
    """
    Generate input, temporary, and output file paths for translation.

    Args:
        file_path (Union[str, Path]): Original file path.
        cfg (Config): Configuration object.

    Returns:
        Optional[Tuple[Path, Path, Path]]: Tuple of (input, tmp, output) paths, or None if failure.
    """

    # make sure this is a Path object
    file_path = Path(file_path)

    # ------------------------------------------------------------
    # Setup the file paths (input, tmp, output, logging, etc.)

    # Clean the filename (if needed)
    input_file_path = get_clean_input_file(file_path, cfg.tmp_dir)
    if input_file_path is None:
        logger.error(f"Failed to process file due to input filename issues: {file_path}")
        return None  # indicate failure

    # create variables for all the files
    tmp_file_path = create_tmp_file_path(input_file_path, cfg.tmp_dir)
    if cfg.translate_filename:
        target_lang_google = deepl_to_google_code(cfg.target_lang)
        if target_lang_google is not None:
            translated_basename = translate_string(file_path.stem, target_lang_google)
            extension = file_path.suffix or ""  # note: suffix includes the dot
            output_file_path = cfg.output_dir / f"{translated_basename}{extension}"
        else:
            # well, I guess it isn't getting translated then
            output_file_path = cfg.output_dir / input_file_path.name
    else:
        output_file_path = cfg.output_dir / input_file_path.name

    logger.debug(f"{'-'*40}")
    logger.debug(f"Input file path:     {input_file_path}")
    logger.debug(f"Temporary file path: {tmp_file_path}")
    logger.debug(f"Output file path:    {output_file_path}")
    logger.debug(f"{'-'*40}")

    return input_file_path, tmp_file_path, output_file_path


def get_clean_input_file(file: Union[str, Path], tmp_dir: Path) -> Optional[Path]:
    """
    Clean the input filename and copy to tmp_dir if needed.

    Args:
        file (Union[str, Path]): Input file path.
        tmp_dir (Path): Temporary directory.

    Returns:
        Optional[Path]: Path to cleaned file, or None if failure.
    """

    try:
        clean_name = clean_filename(file)
        file_path = Path(file)
        if clean_name != file_path.name:
            # need to copy to tmp_dir with cleaned name
            tmp_dir.mkdir(parents=True, exist_ok=True)
            file_path = tmp_dir / clean_name
            shutil.copy2(file, file_path)
            logger.info(f"Renamed input file to safe filename")
            logger.info(f"\t Safe filename: {file_path}")
            return file_path
        else:
            return file_path
    except (OSError, PermissionError, FilenameCleanseError) as e:
        logger.error(f"Failed to rename input file to safe filename: {file_path.name}")
        logger.info(f"\t Used filename: None")
        logger.error(f"\t{e}")
        return None  # return None if failure


def clean_filename(file: Union[str, Path]) -> str:
    """
    Sanitize a filename to contain only safe (non-unicode or whitespace) characters.

    Args:
        file (Union[str, Path]): Input file path.

    Returns:
        str: Cleaned filename string.

    Raises:
        FilenameCleanseError: If filename cannot be cleaned.
    """

    # Set here the valid chars (note the lack of <space> as a valid char)
    safechars = string.ascii_letters + string.digits + "-_."

    # convert to Path (if not already)
    file_path = Path(file)
    # base stem and suffix
    basename = file_path.stem.strip()
    extension = file_path.suffix or ""
    name = basename + extension

    # normalize whitespace -> single space, then convert to underscores
    name = re.sub(r"\s+", " ", name)
    name = name.replace(" ", "_")

    # a few manual replacements before transliteration
    name = name.replace("Å", "Aa")
    name = name.replace("å", "aa")
    name = name.replace("¢", "ø")

    # transliterate non-ASCII to ASCII where possible
    name = unidecode(name)

    # now brutally remove any non-ASCII, un-allowed characters
    filtered_list = list(filter(lambda c: c in safechars, name))
    filtered_str = ''.join(filtered_list)

    # fallback name if everything was stripped away
    if not filtered_str or (filtered_str in (".", "..", "")):
        raise FilenameCleanseError(f"Filename '{file_path}' could not be cleaned to a safe name.")

    return filtered_str


def create_tmp_file_path(input_file: Union[str, Path], tmp_dir: Path) -> Path:
    """
    Create a temporary file path in tmp_dir with the same approximate name as input_file.

    Args:
        input_file (Union[str, Path]): Input file path.
        tmp_dir (Path): Temporary directory.

    Returns:
        Path: Path to temporary file.
    """
    input_path = Path(input_file)

    # create a timestamp safe for filenames (no colons)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    basename = Path(input_path).stem
    extension = input_path.suffix or ""  # note: suffix includes the dot
    tmp_file_path = tmp_dir / f"{basename}_{timestamp}{extension}"
    # tmp_file_path = tmp_dir / f"{basename}_ZZZ{extension}"  # Debug name with constant value

    return tmp_file_path


def deepl_to_google_code(deepl_code: str) -> Optional[str]:
    """
    Convert a DeepL language code to a GoogleTranslator code.

    Args:
        deepl_code (str): DeepL language code.

    Returns:
        Optional[str]: Corresponding GoogleTranslator code, or None if unsupported.
    """

    deepl_code_clean = get_valid_deepl_target_lang(deepl_code)

    if deepl_code_clean is None:
        return None

    # Special cases
    exceptions = {
        "ZH": "zh-cn",
        "NB": "no",
        "PT-BR": "pt",
        "PT-PT": "pt",
        "EN-US": "en",
        "EN-GB": "en",
    }
    if deepl_code_clean in exceptions:
        return exceptions[deepl_code_clean]
    # Default rule: first two letters, lowercase
    return deepl_code_clean[:2].lower()


def translate_string(orig_string: str, target_lang_short: str) -> str:
    """
    Translate a string using GoogleTranslator.

    Args:
        orig_string (str): Original string (underscores treated as spaces).
        target_lang_short (str): Target language code for GoogleTranslator.

    Returns:
        str: Translated string with underscores instead of spaces.
    """

    # target_lang_short = target_lang.split("-")[0].lower()  # e.g., "EN-US" -> "EN" (or targetLang[0:2].lower())
    # target_lang_short = target_lang[0:2].lower() # + '-' + target_lang[3:5]

    spaced_string = orig_string.replace("_", " ")
    try:
        translated = GoogleTranslator(source='auto', target=target_lang_short).translate(text=spaced_string)
        return translated.replace(" ", "_")
    except (deep_translator.exceptions.RequestError,
            deep_translator.exceptions.TooManyRequests,
            deep_translator.exceptions.TranslationNotFound) as error:
        logger.debug(f"deep-translator(Google) failed: {error}")
        return orig_string


def send_document_to_server(source_file: Union[str, Path], result_file: Union[str, Path], target_language: str, translator: deepl.DeepLClient) -> bool:
    """
    Upload a document to DeepL for translation.

    Args:
        source_file (Union[str, Path]): Path to source file.
        result_file (Union[str, Path]): Path to output file.
        target_language (str): Target language code.
        translator (deepl.DeepLClient): DeepL translator client.

    Returns:
        bool: True if translation succeeded, False otherwise.
    """

    # Translate a formal document
    # originally copied from https://github.com/DeepLcom/deepl-python#translating-documents

    source_path = Path(source_file)
    result_path = Path(result_file)

    logger.info(f'Uploading file to DeepL web API translation service.')
    logger.debug(f"\t input document    : {source_path}")
    logger.debug(f"\t returned document : {result_path}")
    logger.debug(f"\t target language   : {target_language}")

    result = False
    # QuotaExceededException  # annoyingly DeepL doesn't update their usage quickly.  So, we need to monitor and bubble up the error
    try:
        # Using translate_document_from_filepath() with file paths
        if not DEBUG_NO_SEND_FILE:
            document_status = translator.translate_document_from_filepath(
                                                            source_path,
                                                            result_path,
                                                            target_lang=target_language,
                                                            formality="prefer_more"
                                                            )
            result = document_status.ok
        else:
            # in debug mode, we skip sending the file to DeepL
            try:
                shutil.copy2(source_path, result_path)  # preserves metadata
                result = True
            except (FileNotFoundError, PermissionError, OSError) as error:
                result = False
                logger.error(f"\tOS error while copying {source_path} to {result_path}")
                logger.error(f"\tError: {error}")
        logger.info(f'Translation complete!')

    except deepl.DocumentTranslationException as error:
        # If an error occurs during document translation after the document was
        # already uploaded, a DocumentTranslationException is raised. The
        # document_handle property contains the document handle that may be used to
        # later retrieve the document from the server, or contact DeepL support.
        logger.error(f"Error after uploading to translation API.")
        # get various pieces of data from the error
        errorType = type(error)                     # here it'll only return DocumentTranslationException, but originally it was in the just Exception section
        docHNDL = str(error.document_handle)        # pull out the document ID & Key, in case the user needs to use them outside this script.
        tmpStr = ', document handle: ' + docHNDL
        errMsg = str(error).replace(tmpStr, '.')    # DocumentTranslationException doesn't have a `message` attribute, so we'll make one
        logger.error(f"\t{errMsg}")
        p = re.compile(r'Document ID:\s+(\w+), key:\s+(\w+)')  # finish pulling the ID and key
        m = p.match(docHNDL)
        if m is not None:
            docID = m.group(1)
            docKey = m.group(2)
            logger.error(f"\tDocument ID:  {docID}")
            logger.error(f"\tDocument Key: {docKey}")
        if error.document_handle is not None:
            doc_id = error.document_handle.document_id
            doc_key = error.document_handle.document_key
            logger.error(f"\tDocument ID2:  {doc_id}")
            logger.error(f"\tDocument Key2: {doc_key}")

        result = False
        # the error was that the quota was not empty, but still too low to perform the document translation (i.e., it didn't raise a QuotaExceededException, but it is still the same issue)
        if errMsg == "Quota for this billing period has been exceeded.": # note having to add the `.` because we added it in replace() above
            raise QuotaExceededException(error) from error # send this up the subroutine chain
    except deepl.exceptions.QuotaExceededException as error:
        logger.error("The quota for this billing period has been exceeded.")
        logger.error(f"{error}")
        result = False
        raise QuotaExceededException(error) from error # send this up the subroutine chain
    except Exception as error:
        # Errors during upload raise a DeepLException
        logger.error("Unexpected error occurred during the translation process.")
        errorType = type(error)
        logger.debug(f"{errorType}")
        logger.error(f"{error}")

    return result


def append_pdfs(original_pdf: Path, translated_pdf: Path, output_pdf: Path, put_original_first: bool) -> bool:
    """
    Merge two PDF files (original and translated) into a single output PDF.

    Args:
        original_pdf (Path): Path to the original PDF file.
        translated_pdf (Path): Path to the translated PDF file.
        output_pdf (Path): Destination path for the merged PDF file.
        put_original_first (bool): If True, the original PDF is placed before the translated PDF.
                                   If False, the translated PDF is placed before the original.

    Returns:
        bool: True if the merge was successful, False otherwise.
    """
    writer = PdfWriter()

    logger.info('Appending the translated PDF and the original PDF together.')
    try:
        if put_original_first:
            writer.append(original_pdf, "Original", None, True)
            writer.append(translated_pdf, "Translation", None, True)
            logger.info(f'\t Original PDF  : {original_pdf}')
            logger.info(f'\t Translated PDF: {translated_pdf}')
        else:
            writer.append(translated_pdf, "Translation", None, True)
            writer.append(original_pdf, "Original", None, True)
            logger.info(f'\t Translated PDF: {translated_pdf}')
            logger.info(f'\t Original PDF  : {original_pdf}')

        writer.write(output_pdf)
        writer.close()
        logger.info('\t PDF merger successful.')
        logger.info(f'\t Output PDF    : {output_pdf}')
        return True
    except (FileNotFoundError, PermissionError, OSError, PdfReadError, ValueError, RuntimeError) as error:
        writer.close()
        logger.error("Unknown error occurred during the PDF merging process.")
        logger.error(f"{error}")
        return False


def send_apprise_message(title: str, body: str, attach: Optional[Path] = None) -> Optional[bool]:
    """
    Send an Apprise message to the user.

    Args:
        title (str): Title of the message.
        body (str): Body of the message.
        attach (Path): Path to attach to the message.

    Returns:
        bool: True if the message was sent successfully, False otherwise
    """
    for h in logger.handlers:
        if isinstance(h, BufferedAppriseHandler):
            try:
                # Found your handler; use its Apprise object directly
                if attach and attach.exists():
                    attach_str = str(attach)
                    return h.apobj.notify( title=title, body=body, body_format="text", attach=attach_str)
                else:
                    return h.apobj.notify( title=title, body=body, body_format="text")
            except RuntimeError as e:
                logger.error(f"Apprise notify failed: {e}")
                return False
    return False


def monitor_directory(cfg: Config, stop_monitoring: threading.Event = None) -> None:
    """
    Monitor the input directory for new files and process them.

    Args:
        cfg (Config): Configuration object.

    Example for API usage:
        stop_monitoring = threading.Event()

        def run_monitor():
            try:
                cfg = autotranslate.init_autotranslate()
                autotranslate.monitor_directory(cfg, stop_monitoring)
            except Exception as e:
                print(f"Monitor loop exited: {e}")

        # start thread
        monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        monitor_thread.start()

        # later, on shutdown:
        stop_monitoring.set()

    """

    logger.info(f"Monitoring directory for new files: {cfg.input_dir}")
    logger.info(f"Check interval: {cfg.check_period_min} minutes")

    # setup vars
    check_period_sec = int(60 * cfg.check_period_min)  # convert minutes to seconds
    did_flush = False # initialize flush flag for directory mode

    while stop_monitoring is None or not stop_monitoring.is_set(): # loop forever
        for file_path in cfg.input_dir.iterdir():
            if file_path.is_dir():
                continue
            if file_path.name.startswith("."):
                continue
            if file_path.suffix.lower() == ".pdf": # only process PDF files
                did_flush = False # reset flush flag for this file processing loop

                try:
                    process_file(file_path, cfg)
                except QuotaExceededException as qe:
                    logger.error("Translation quota exceeded during file processing.")
                    logger.error(f"{qe}")

                    if not did_flush:
                        did_flush = flush_handlers() # flush any pending log messages before sleep
                    wait_seconds = num_seconds_till_renewal(cfg.usage_renewal_day)
                    if cfg.global_log_file_handler is not None:
                        if wait_seconds >= 820800:        # ≥ 9.5 days
                            num_graduations = 10
                        elif wait_seconds >= 648000:      # 7.5–8.5 days
                            num_graduations = 8
                        elif wait_seconds >= 475200:      # 5.5–6.5 days
                            num_graduations = 6
                        elif wait_seconds >= 388800:      # 4.5–5.5 days
                            num_graduations = 5
                        else:                              # ≤ 4.5 days
                            num_graduations = 4
                        sleep_with_progressbar_countdown(cfg.global_log_file_handler, secs=wait_seconds, graduations=num_graduations)
                    else:
                        logger.info(f"Sleeping for {format_timespan(wait_seconds)} until usage renewal.")
                        time.sleep(wait_seconds)

                    time.sleep(5) # Delay for X seconds to prevent pounding on the server.
                    break  # break out of the for loop to re-check the input directory after sleep


        # sleep for the configured period before checking again
        if (cfg.check_period_min >= 30) and (cfg.global_log_file_handler is not None):
            if not did_flush:
                did_flush = flush_handlers()
            sleep_with_progressbar_countdown(cfg.global_log_file_handler, secs=check_period_sec,
                                                use_time_labels=False, use_percent_labels=False)
        else:
            # logger.info(f"Sleeping for {format_timespan(cfg.check_period_sec)}.")
            if not did_flush:
                did_flush = flush_handlers()
            time.sleep(check_period_sec)


def num_seconds_till_renewal(renewal_date: int, default_days: int = 7) -> int:
    """
    Calculate seconds until next renewal of usage allowance.

    Args:
        renewal_date (int): Day of month usage renews (1-31).
        default_days (int): Default wait time in days if renewal_date is invalid.

    Returns:
        int: Number of seconds until next renewal.
    """
    # Default wait time
    wait_seconds = default_days * 24 * 60 * 60

    # Get configured renewal day (1-31)
    logger.debug(f"\tnum_seconds_till_renewal() debug output ...")
    logger.debug(f"\tusageRenewalDay: {renewal_date}")

    if 1 <= renewal_date <= 31:
        now = pendulum.now("UTC")
        # Construct this month's renewal date
        renewal_this_month = pendulum.datetime(now.year, now.month, renewal_date, tz="UTC")

        # If renewal day already passed, schedule next month
        if renewal_this_month <= now:
            renewal_this_month = renewal_this_month.add(months=1)

        # Add 1 day buffer to avoid timezone mismatch corner cases
        next_renewal = renewal_this_month.add(days=1)

        duration = next_renewal - now
        wait_seconds = int(duration.total_seconds())

        logger.debug(f"\tnow = {now}")
        logger.debug(f"\tnextRenewal = {next_renewal}")
        logger.debug(f"\tduration = {duration}")
        logger.debug(f"\twait_seconds = {wait_seconds}")

    return wait_seconds


def sleep_with_progressbar_countdown(fh: logging.FileHandler, secs: int, steps: int = 80, graduations: int = 4, use_time_labels: bool = True, use_percent_labels: bool = True) -> None:
    """
    Sleep with a countdown progress bar written directly to a logfile.

    Args:
        fh (logging.FileHandler): File handler to write progress bar.
        secs (int): Total sleep duration in seconds.
        steps (int): Number of progress bar steps.
        graduations (int): Number of graduation markers.
        use_time_labels (bool): Whether to show time remaining labels.  Implies use_percent_labels.
        use_percent_labels (bool): Whether to show percentage remaining.
    """
    logger.info(f"Sleeping for {format_timespan(secs)} (countdown mode).")

    # input error checking
    if graduations < 1:
        graduations = 1
    if steps < 10:
        steps = 10
    if use_time_labels:
        use_percent_labels = True

    # setup values to space out extra chars if steps is not perfectly divisible
    char_per_graduation = int((steps - 2) / graduations)
    #print(f"char_per_graduation: {char_per_graduation}")
    char_per_graduation_minimum = 4
    if char_per_graduation < char_per_graduation_minimum:
        graduations = (steps - 2) // char_per_graduation_minimum
        char_per_graduation = int((steps - 2) / graduations)

    # Build scale line with countdown graduations (time labels)
    bar_line = "-" * (steps - 2)
    bar_line = "|" + bar_line + "|"

    # Build scale line with countdown graduations (time labels)
    if use_time_labels:
        # write header line of the "table"
        fh.stream.write(bar_line + "\n")
        fh.stream.flush()

        # write the time table ( XX% - HH:MM:SS )
        for i in range(0, graduations): # start at 0 to get 100%
            scale = ["|"]
            label_p = f"{int((graduations - i) * 100 / graduations)}%" + " - "
            scale.append(label_p.rjust(8, " "))
            label_t = format_timespan(int(secs * (graduations - i) / graduations))
            scale.append(label_t)
            scale.append("".rjust(steps - 1 - len("".join(scale)), " "))
            scale_line = "".join(scale) + "|"
            # write to log
            fh.stream.write(scale_line + "\n")
            fh.stream.flush()

        # write step line
        interval = secs / (steps - 2)
        scale = ["|"]
        scale.append("# = ".rjust(8, " "))
        label_s = format_timespan(int(interval))
        scale.append(label_s)
        scale.append("".rjust(steps - 1 - len("".join(scale)), " "))
        scale_line = "".join(scale) + "|"
        # write to log
        fh.stream.write(scale_line + "\n")
        fh.stream.flush()


        # write separator line of the "table"
        fh.stream.write(bar_line + "\n")
        fh.stream.flush()

    # Build scale line with countdown graduations
    if use_percent_labels:
        # see if the math is less than the actual number of steps
        total_chars = 2 + (char_per_graduation * graduations)
        pad_count = steps - total_chars   # how many "-"s do we need to add to reach (pad) to equal steps
        candidates = list(range(1, graduations+1))  # add 1 to graduations because we could pad the last section

        # Choose evenly spaced indices to pad
        if pad_count > 0:
            # spacing factor
            spacing = len(candidates) / pad_count
            pad_indices = [candidates[int(round(j * spacing))] for j in range(pad_count)]
        else:
            pad_indices = []

        percent_line = "|"
        graduation_line = "|"
        for i in range(1, graduations):
            percent_line += "-" * int(char_per_graduation - 3) # 3 is 2 digits + %
            graduation_line += "-" * int(char_per_graduation - 1) # 1 is "|"
            if (total_chars < steps) and (i in pad_indices):
                percent_line += "-"
                graduation_line += "-"
                total_chars += 1
            label_p = f"{int((graduations - i) * 100 / graduations)}%"  # results in a 2 or 3 char string
            label_p = label_p.rjust(3, "-") # fill if 1 digit and not 2 digits (and % to make 2-3 char string)
            percent_line += label_p
            graduation_line += "|"
        if total_chars < steps:
            percent_line += "-" * (steps - total_chars)
            graduation_line += "-" * (steps - total_chars)
        percent_line += "-" * int(char_per_graduation ) + "|"
        graduation_line += "-" * int(char_per_graduation ) + "|"

        # write to log
        fh.stream.write(percent_line + "\n")
        fh.stream.write(graduation_line + "\n")
        fh.stream.flush()


    # Progress bar line
    fh.stream.write("[")
    fh.stream.flush()

    interval = secs / (steps - 2)
    for i in range(steps - 2):
        time.sleep(interval)
        fh.stream.write("#")
        fh.stream.flush()

    fh.stream.write("]\n")
    fh.stream.flush()
    # logger.info("Countdown finished.")

    return


def flush_handlers() -> bool:
    """
    Flush all active logging handlers to ensure buffered log messages are written.
    - Emits a final blank line to separate the last log entry cleanly.

    This function is typically invoked during program shutdown or long sleep to guarantee that
    all log output is flushed to console, files, or external services before exit.
    """
    did_flush = False

    # Write one last newline
    logger.info("")   # emits a blank line
    did_flush = True

    for h in logger.handlers[:]:
        try:
            h.flush()
        except (OSError, RuntimeError) as e:
            logger.debug(f"Handler cleanup skipped: {e}")

    return did_flush


def graceful_exit(exit_code: int = 0) -> None:
    """
    Flush and close all logging handlers before exiting.
    Ensures Apprise notifications are sent before interpreter shutdown.
    Also, removes global log file handler.
    """
    if getattr(graceful_exit, "exit_done", False):
        return
    graceful_exit.exit_done = True

    # Write one last newline
    logger.info("")   # emits a blank line

    for h in logger.handlers[:]:
        try:
            h.flush()
            h.close()
            logger.removeHandler(h)
        except (OSError, RuntimeError) as e:
            logger.debug(f"Handler cleanup skipped: {e}")

    # now exit
    # Only exit if this module is the main program
    if __name__ == "__main__":
        sys.exit(exit_code)
    else:
        # If imported, just return control to caller
        logger.debug(f"graceful_exit called with code {exit_code}, not exiting (imported mode).")

    return


# -----------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------
def to_any(to_type: Callable, value: Any, default_value: Any) -> Any:
    """
    Convert value to specified type with error handling.

    Args:
        to_type (Callable): Target type constructor.
        value (Any): Value to convert.
        default_value (Any): Fallback if conversion fails.

    Returns:
        Any: Converted value or default.
    """
    try:
        if to_type is bool:
            # Delegate to custom boolean parser
            return to_bool(value, default_value)
        elif value is None:
            return to_type(default_value)
        return to_type(value)
    except (ValueError, TypeError):
        return to_type(default_value)


def to_bool(value: Any, default_value: bool) -> bool:
    """Convert arbitrary value to boolean with fallback default."""

    if isinstance(value, bool):
        return value
    elif value is None:
        return default_value
    elif isinstance(value, int) or isinstance(value, float):
        if value != 0:
            return True
        else:
            return False
    elif isinstance(value, str):
        if value.lower() in ("yes", "y", "true",  "t", "1", "1.0"):
            return True
        elif value.lower() in ("no",  "n", "false", "f", "0", "0.0", "", "none", "[]", "{}"):
            return False
        else:
            return default_value
    else:
        return default_value


def to_int(value: Any, default_value: int) -> int:
    """Convert arbitrary value to int with fallback default."""
    return to_any(int, value, default_value)


def to_float(value: Any, default_value: float) -> float:
    """Convert arbitrary value to float with fallback default."""
    return to_any(float, value, default_value)


def to_str(value: Any, default_value: Union[Path,str]) -> str:
    """Convert arbitrary value to string with fallback default."""
    return to_any(str, value, default_value)


def is_in_container() -> bool:
    """
    Detect whether the current process is running inside a Linux container.

    The function checks:
      1. Presence of Docker's marker file (`/.dockerenv`).
      2. The `container` environment variable (used by LXC, systemd-nspawn, etc.).
      3. Contents of `/proc/1/cgroup` for known container runtime patterns.

    Returns:
        bool: True if the process appears to be running inside a container,
              False otherwise.
    """
    result = {
        "in_container": False,
        "runtime": None,
        "detail": None
    }

    # Check for Docker's marker file
    if os.path.exists("/.dockerenv"):
        result.update({"in_container": True, "runtime": "docker"})
        # return result
        return result["in_container"]

    # Check environment variable (common in LXC, systemd-nspawn, etc.)
    env_container = os.environ.get("container", "").lower()
    if env_container:
        result.update({"in_container": True, "runtime": env_container})
        return result["in_container"]

    # Parse /proc/1/cgroup for runtime-specific patterns
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return result["in_container"]  # cannot determine, assume not containerized

    # Map keywords to runtime names
    patterns = {
        "docker": "docker",
        "libpod": "podman",
        "kubepods": "kubernetes",
        "lxc": "lxc",
        "crio": "crio",
        "containerd": "containerd",
        "machine.slice": "systemd-nspawn"
    }

    for key, runtime in patterns.items():
        if key in content:
            result.update({
                "in_container": True,
                "runtime": runtime,
                "detail": content.strip()
            })
            return result["in_container"]

    # No known container keywords found
    return result["in_container"]


def arg_or_env(arg: Optional[str], env_name: str) -> Optional[str]:
    """
    Return CLI argument if provided, otherwise environment variable.

    Args:
        arg: CLI argument value.
        env_name (str): Environment variable name.

    Returns:
        str|None: Value from argument or environment.
    """
    env_val = os.getenv(env_name)
    if DEBUG_DUMP_VARS:
        logger.debug(f"arg_or_env: arg={arg!r:<25}, env_name={env_name:<30}, env_val={env_val}")  # Debug print
    if arg is not None:
        return arg
    elif env_val is not None:
        return env_val
    else:
        return None


def debug_dump(obj: Any, name="Object") -> None:
    """
    Dump object attributes or dict contents to debug log.

    Args:
        obj (Any): Object or dict to dump.
        name (str): Label for the dump.
    """
    logger.debug(f"\nDebug Dump: {name}")
    logger.debug("-" * 40)

    if isinstance(obj, dict):
        for key, value in obj.items():
            logger.debug(f"{key:<20}: {value!r}")
    elif hasattr(obj, "__dict__"):
        for key, value in vars(obj).items():
            logger.debug(f"{key:<20}: {value!r}")
    else:
        logger.debug(f"(Unsupported type: {type(obj).__name__})")
        logger.debug(repr(obj))

    logger.debug("-" * 40 + "\n")


def delete_file(filename: Union[str, Path]) -> bool:
    """
    Delete a file with error checking.

    Args:
        filename (Union[str, Path]): Path to the file.

    Returns:
        bool: True if deleted or did not exist, False if error occurred.
    """

    # make sure this is a Path object
    file_path = Path(filename)

    logger.info(f"Attempting to delete file:")
    logger.info(f"\t{file_path}")

    if file_path.exists():
        try:
            file_path.unlink(missing_ok=True)
            logger.info(f"\tFile successfully deleted.")
            return True
        except (PermissionError, OSError, ValueError) as error:
            logger.warning(f"\tError while trying to delete file: {file_path}")
            logger.warning(f"\tError: {error}")
            return False
    else:
        logger.warning(f"\tFile did not exist in the first place. Nothing to do.")
        return True


# -----------------------------------------------------------------------
# -----------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal crash: {e}")
        graceful_exit(1)

