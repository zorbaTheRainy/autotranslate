import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import logging
import autotranslate_web_server as ws
import autotranslate


@pytest.fixture
def fake_cfg(tmp_path):
    """Create a fake Config object with temp directories."""
    cfg = autotranslate.Config()
    cfg.tmp_dir = tmp_path / "tmp"
    cfg.output_dir = tmp_path / "output"
    cfg.log_dir = tmp_path / "logs"
    cfg.target_lang = "EN-US"
    cfg.translate_filename = False
    cfg.put_original_first = False
    cfg.global_log_file_path = None
    cfg.usage_renewal_day = 1
    cfg.auth_key = "test_key"
    for d in [cfg.tmp_dir, cfg.output_dir, cfg.log_dir]:
        d.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(autouse=True)
def reset_globals(fake_cfg, tmp_path):
    """Reset all module-level globals before and after each test."""
    old = {
        k: getattr(ws, k)
        for k in (
            "cfg",
            "web_log_file_path",
            "is_fatal_error",
            "is_quota_exceeded",
            "fatal_error_reason",
            "scoreboard",
        )
    }

    # Set up test state
    ws.cfg = fake_cfg
    ws.web_log_file_path = tmp_path / "web.log"
    ws.web_log_file_path.touch()
    ws.is_fatal_error = False
    ws.is_quota_exceeded = False
    ws.fatal_error_reason = None
    ws.scoreboard = {}

    yield

    # Restore original state
    for k, v in old.items():
        setattr(ws, k, v)


@pytest.fixture
def client(reset_globals):
    """Create a Flask test client."""
    ws.app.config["TESTING"] = True
    with ws.app.test_client() as c:
        yield c


@pytest.fixture
def mock_deepl_languages():
    """Mock get_deepl_languages to return test languages."""
    with patch("autotranslate.get_deepl_languages") as mock:
        mock.return_value = {"EN": "English", "DE": "German", "FR": "French"}
        yield mock


@pytest.fixture
def mock_process_file():
    """Mock autotranslate.process_file to avoid real translation calls."""
    with patch("autotranslate.process_file") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_num_seconds_till_renewal():
    """Mock autotranslate.num_seconds_till_renewal."""
    with patch("autotranslate.num_seconds_till_renewal") as mock:
        mock.return_value = 3600
        yield mock


# ============================================================================
# Autotranslate.py specific fixtures
# ============================================================================

@pytest.fixture
def at_cfg(tmp_path):
    """Config with real temp dirs suitable for autotranslate tests."""
    cfg = autotranslate.Config()
    cfg.auth_key = "test:deadbeef"
    cfg.target_lang = "EN-US"
    cfg.input_dir = tmp_path / "input"
    cfg.output_dir = tmp_path / "output"
    cfg.tmp_dir = tmp_path / "tmp"
    cfg.log_dir = tmp_path / "logs"
    for d in [cfg.input_dir, cfg.output_dir, cfg.tmp_dir, cfg.log_dir]:
        d.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def minimal_pdf(tmp_path):
    """Create a minimal single-page valid PDF for PDF-merge tests."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    path = tmp_path / "page.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def mock_translator():
    """Mock deepl.DeepLClient that returns successful document_status."""
    m = MagicMock()
    status = MagicMock()
    status.ok = True
    m.translate_document_from_filepath.return_value = status
    return m


@pytest.fixture
def logger_with_apprise_handler():
    """Add a mock BufferedAppriseHandler to the logger for testing."""
    logger = logging.getLogger()
    handler = MagicMock(spec=autotranslate.BufferedAppriseHandler)
    handler.apobj = MagicMock()
    handler.apobj.notify = MagicMock(return_value=True)
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)
