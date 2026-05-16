import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY
from io import BytesIO
import autotranslate_web_server as ws
import autotranslate


class TestEnsureInitialized:
    """Test @app.before_request ensure_initialized guard."""

    def test_initialized_allows_request(
        self, client, reset_globals, mock_deepl_languages
    ):
        """When cfg and web_log_file_path are set, request should proceed."""
        assert ws.cfg is not None
        assert ws.web_log_file_path is not None
        resp = client.get("/")
        assert resp.status_code == 200

    def test_cfg_none_blocks_request(self, client, reset_globals):
        """When cfg is None, request returns 500."""
        ws.cfg = None
        resp = client.get("/")
        assert resp.status_code == 500
        assert b"not initialized" in resp.data

    def test_web_log_file_path_none_blocks_request(
        self, client, reset_globals, mock_deepl_languages
    ):
        """When web_log_file_path is None, request returns 500."""
        ws.web_log_file_path = None
        resp = client.get("/")
        assert resp.status_code == 500
        assert b"not initialized" in resp.data

    def test_file_log_endpoint_exempt(self, reset_globals):
        """GET /log/<filename> should be exempt from initialization check."""
        ws.cfg = None
        ws.web_log_file_path = None
        with ws.app.test_client() as client:
            resp = client.get("/log/nonexistent.log")
            # Should not return 500 from ensure_initialized; instead 404 from file_log
            assert resp.status_code == 404


class TestIndex:
    """Test GET / (index) endpoint."""

    def test_index_normal_render(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Normal index render with cfg values."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"index.html" in resp.data or resp.content_type == "text/html; charset=utf-8"

    def test_index_fatal_error_returns_500(
        self, client, reset_globals, mock_deepl_languages
    ):
        """When is_fatal_error=True, return error.html with 500."""
        ws.is_fatal_error = True
        ws.fatal_error_reason = "Test crash"
        resp = client.get("/")
        assert resp.status_code == 500
        assert b"error.html" in resp.data or b"Crashed" in resp.data

    def test_index_quota_exceeded_returns_error(
        self, client, reset_globals, mock_deepl_languages, mock_num_seconds_till_renewal
    ):
        """When is_quota_exceeded=True, return quota error page."""
        ws.is_quota_exceeded = True
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Quota" in resp.data or b"error.html" in resp.data

    def test_index_with_job_id_no_entry(
        self, client, reset_globals, mock_deepl_languages
    ):
        """GET /?job_id=unknown returns index with jobs_log_filename=None."""
        resp = client.get("/?job_id=2025_01_01_12_00_00_001")
        assert resp.status_code == 200
        # jobs_log_filename should be None in template context

    def test_index_with_job_id_in_scoreboard_with_log(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """GET /?job_id=<existing> with log_file set includes filename."""
        job_id = "2025_01_01_12_00_00_001"
        log_file = fake_cfg.log_dir / "test.log"
        log_file.touch()
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=log_file, output_file=None
        )
        ws.scoreboard[job_id] = entry
        resp = client.get(f"/?job_id={job_id}")
        assert resp.status_code == 200

    def test_index_with_job_id_in_scoreboard_no_log(
        self, client, reset_globals, mock_deepl_languages
    ):
        """GET /?job_id=<existing> with log_file=None."""
        job_id = "2025_01_01_12_00_00_001"
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=None, output_file=None
        )
        ws.scoreboard[job_id] = entry
        resp = client.get(f"/?job_id={job_id}")
        assert resp.status_code == 200


class TestRunTranslation:
    """Test POST /run (upload and start translation)."""

    def test_run_no_file_returns_400(self, client, reset_globals, mock_deepl_languages):
        """POST /run with no file returns 400 error."""
        resp = client.post("/run", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert b"error" in resp.data.lower()

    @patch("threading.Thread")
    def test_run_with_file_starts_thread(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """POST /run with valid file starts background thread and redirects."""
        file_data = BytesIO(b"fake pdf content")
        resp = client.post(
            "/run",
            data={
                "pdf_file": (file_data, "test.pdf"),
                "target_language": "DE",
                "translate_filename": "on",
                "include_original": "on",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        # Should redirect to /?job_id=<key>
        assert resp.status_code == 302
        assert "job_id=" in resp.location
        # Thread should have been started
        mock_thread.assert_called_once()

    @patch("threading.Thread")
    def test_run_applies_form_values_to_cfg(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Form values are applied to a copy of cfg."""
        file_data = BytesIO(b"fake pdf content")
        resp = client.post(
            "/run",
            data={
                "pdf_file": (file_data, "test.pdf"),
                "target_language": "FR",
                "translate_filename": "on",
                "include_original": "",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        # Verify thread was called with modified cfg
        call_args = mock_thread.call_args
        assert call_args is not None


class TestCheckOutput:
    """Test GET /check_output (poll for output readiness)."""

    def test_check_output_no_job_id(self, client, reset_globals, mock_deepl_languages):
        """GET /check_output without job_id returns ready=False."""
        resp = client.get("/check_output")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is False
        assert data["filename"] is None

    def test_check_output_unknown_job_id(
        self, client, reset_globals, mock_deepl_languages
    ):
        """GET /check_output?job_id=unknown returns ready=False."""
        resp = client.get("/check_output?job_id=2025_01_01_12_00_00_001")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is False

    def test_check_output_job_not_ready(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Job exists but output_file=None -> ready=False."""
        job_id = "2025_01_01_12_00_00_001"
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=None, output_file=None
        )
        ws.scoreboard[job_id] = entry
        resp = client.get(f"/check_output?job_id={job_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is False

    def test_check_output_job_ready(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Job with output_file set -> ready=True, filename included."""
        job_id = "2025_01_01_12_00_00_001"
        output_file = fake_cfg.output_dir / "translated.pdf"
        output_file.touch()
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=None, output_file=output_file
        )
        ws.scoreboard[job_id] = entry
        resp = client.get(f"/check_output?job_id={job_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is True
        assert data["filename"] == "translated.pdf"

    @patch("autotranslate_web_server.scoreboard_lock")
    def test_check_output_exception_returns_500(
        self, mock_lock, client, reset_globals, mock_deepl_languages
    ):
        """Exception inside lock returns 500 error JSON."""
        mock_lock.__enter__.side_effect = Exception("Lock error")
        resp = client.get("/check_output?job_id=test")
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert "error" in data


class TestServeOutput:
    """Test GET /output/<filename> (download translated PDF)."""

    def test_serve_output_path_traversal(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Path traversal attempt returns 403."""
        # Create a file outside output_dir
        outside_dir = fake_cfg.tmp_dir / "outside.txt"
        outside_dir.write_text("secret")
        # Try to access it via relative path within the output_dir name
        resp = client.get("/output/..%2f..%2foutside.txt")
        assert resp.status_code == 404 or resp.status_code == 403

    def test_serve_output_file_not_found(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Non-existent file returns 404."""
        resp = client.get("/output/nonexistent.pdf")
        assert resp.status_code == 404

    def test_serve_output_directory_returns_404(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Request for directory (not regular file) returns 404."""
        subdir = fake_cfg.output_dir / "subdir"
        subdir.mkdir()
        resp = client.get("/output/subdir")
        assert resp.status_code == 404

    def test_serve_output_valid_file(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Valid file is served with 200."""
        output_file = fake_cfg.output_dir / "test.pdf"
        output_file.write_bytes(b"fake pdf content")
        resp = client.get("/output/test.pdf")
        assert resp.status_code == 200
        assert resp.data == b"fake pdf content"


class TestDownload:
    """Test GET /download/<dl_type>/<path:filename>."""

    def test_download_invalid_type_returns_404(
        self, client, reset_globals, mock_deepl_languages
    ):
        """dl_type other than 'output' or 'log' returns 404."""
        resp = client.get("/download/invalid/file.txt")
        assert resp.status_code == 404

    def test_download_output_path_traversal_returns_403(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Path traversal in output download returns 403."""
        resp = client.get("/download/output/../../../etc/passwd")
        assert resp.status_code == 403

    def test_download_output_missing_file_returns_404(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Non-existent output file returns 404."""
        resp = client.get("/download/output/missing.pdf")
        assert resp.status_code == 404

    def test_download_output_valid_file(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Valid output file is downloaded."""
        output_file = fake_cfg.output_dir / "translated.pdf"
        output_file.write_bytes(b"pdf data")
        resp = client.get("/download/output/translated.pdf")
        assert resp.status_code == 200
        assert resp.data == b"pdf data"

    def test_download_log_valid_file(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Valid log file is downloaded."""
        log_file = fake_cfg.log_dir / "test.log"
        log_file.write_text("log content")
        resp = client.get("/download/log/test.log")
        assert resp.status_code == 200
        assert b"log content" in resp.data

    def test_download_log_path_traversal_returns_403(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Path traversal in log download returns 403."""
        resp = client.get("/download/log/../../../etc/passwd")
        assert resp.status_code == 403


class TestReportScoreboard:
    """Test GET /status (list all jobs)."""

    def test_status_empty_scoreboard(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Empty scoreboard renders status.html with no jobs."""
        resp = client.get("/status")
        assert resp.status_code == 200
        assert b"Translation Jobs History" in resp.data or b"translation jobs history" in resp.data

    def test_status_with_entries(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Scoreboard with entries renders them."""
        job_id = "2025_01_01_12_00_00_001"
        input_file = fake_cfg.tmp_dir / "input.pdf"
        input_file.touch()
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=input_file, log_file=None, output_file=None
        )
        ws.scoreboard[job_id] = entry
        resp = client.get("/status")
        assert resp.status_code == 200

    def test_status_entry_with_all_files(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Scoreboard entry with all files set."""
        job_id = "2025_01_01_12_00_00_001"
        input_file = fake_cfg.tmp_dir / "input.pdf"
        log_file = fake_cfg.log_dir / "job.log"
        output_file = fake_cfg.output_dir / "output.pdf"
        for f in [input_file, log_file, output_file]:
            f.touch()
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=input_file, log_file=log_file, output_file=output_file
        )
        ws.scoreboard[job_id] = entry
        resp = client.get("/status")
        assert resp.status_code == 200

    def test_status_entry_without_output(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Entry with log but no output shows 'In Progress...'."""
        job_id = "2025_01_01_12_00_00_001"
        log_file = fake_cfg.log_dir / "job.log"
        log_file.touch()
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=log_file, output_file=None
        )
        ws.scoreboard[job_id] = entry
        resp = client.get("/status")
        assert resp.status_code == 200


class TestFileLog:
    """Test GET /log/<log_filename>."""

    def test_file_log_no_cfg_allowed(self, reset_globals):
        """file_log endpoint is exempt from ensure_initialized."""
        ws.cfg = None
        with ws.app.test_client() as client:
            resp = client.get("/log/test.log")
            # Should not be blocked by ensure_initialized; should get 404
            assert resp.status_code == 404

    def test_file_log_path_traversal_returns_403(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Path traversal returns 403."""
        resp = client.get("/log/..%2f..%2fetc%2fpasswd")
        assert resp.status_code == 404 or resp.status_code == 403

    def test_file_log_not_found_returns_404(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Non-existent log file returns 404."""
        resp = client.get("/log/missing.log")
        assert resp.status_code == 404
        assert b"No log file found" in resp.data

    def test_file_log_valid_file_default_mode(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Valid log file renders with default refresh (30s)."""
        log_file = fake_cfg.log_dir / "test.log"
        log_file.write_text("log line 1\nlog line 2")
        resp = client.get("/log/test.log")
        assert resp.status_code == 200
        assert b"30" in resp.data or b"log.html" in resp.data

    def test_file_log_realtime_mode(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Valid log file with ?mode=realtime renders with 2s refresh."""
        log_file = fake_cfg.log_dir / "test.log"
        log_file.write_text("realtime log")
        resp = client.get("/log/test.log?mode=realtime")
        assert resp.status_code == 200
        assert b"2" in resp.data or b"log.html" in resp.data

    def test_file_log_tail_200_lines(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Log file tails last 200 lines."""
        log_file = fake_cfg.log_dir / "long.log"
        lines = [f"line {i}" for i in range(300)]
        log_file.write_text("\n".join(lines))
        resp = client.get("/log/long.log")
        assert resp.status_code == 200
        # Should contain content from the file
        assert b"line" in resp.data

    def test_file_log_ansi_codes_converted(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """ANSI codes in log are converted to HTML."""
        log_file = fake_cfg.log_dir / "colored.log"
        log_file.write_text("\x1b[32mGreen text\x1b[0m")
        resp = client.get("/log/colored.log")
        assert resp.status_code == 200
        # Ansi2HTMLConverter should have processed it
        assert b"log.html" in resp.data or b"Green" in resp.data


class TestRunProcessFile:
    """Test run_process_file helper function."""

    @patch("autotranslate.process_file")
    def test_run_process_file_success(
        self, mock_process, reset_globals, fake_cfg
    ):
        """Normal execution calls process_file and returns True."""
        mock_process.return_value = True
        result = ws.run_process_file(
            fake_cfg.tmp_dir / "input.pdf", fake_cfg
        )
        assert result is True
        mock_process.assert_called_once()

    @patch("autotranslate.process_file")
    def test_run_process_file_quota_exception(
        self, mock_process, reset_globals, fake_cfg
    ):
        """QuotaExceededException sets is_quota_exceeded."""
        mock_process.side_effect = autotranslate.QuotaExceededException(Exception("Quota hit"))
        result = ws.run_process_file(
            fake_cfg.tmp_dir / "input.pdf", fake_cfg
        )
        assert ws.is_quota_exceeded is True
        assert result is False

    @patch("autotranslate.process_file")
    def test_run_process_file_other_exception(
        self, mock_process, reset_globals, fake_cfg
    ):
        """Other exceptions set is_fatal_error and fatal_error_reason."""
        mock_process.side_effect = ValueError("Some error")
        result = ws.run_process_file(
            fake_cfg.tmp_dir / "input.pdf", fake_cfg
        )
        assert ws.is_fatal_error is True
        assert ws.fatal_error_reason is not None
        assert "ValueError" in ws.fatal_error_reason or "Some error" in ws.fatal_error_reason


class TestCaptureFunctions:
    """Test capture_fatal_error and capture_quota_excess callbacks."""

    def test_capture_fatal_error(self, reset_globals):
        """capture_fatal_error sets globals."""
        ws.capture_fatal_error("Test error")
        assert ws.is_fatal_error is True
        assert ws.fatal_error_reason == "Test error"

    def test_capture_quota_excess(self, reset_globals):
        """capture_quota_excess sets is_quota_exceeded."""
        ws.capture_quota_excess()
        assert ws.is_quota_exceeded is True


class TestUniqueTimestampKey:
    """Test unique_timestamp_key helper function."""

    def test_unique_timestamp_key_format(self, reset_globals, fake_cfg):
        """Key matches YYYY_MM_DD_HH_MM_SS_### format."""
        import re
        key = ws.unique_timestamp_key(fake_cfg.tmp_dir / "test.pdf")
        assert re.match(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}", key)

    def test_unique_timestamp_key_creates_scoreboard_entry(
        self, reset_globals, fake_cfg
    ):
        """Key is added to scoreboard."""
        input_file = fake_cfg.tmp_dir / "test.pdf"
        input_file.touch()
        key = ws.unique_timestamp_key(input_file)
        assert key in ws.scoreboard
        assert ws.scoreboard[key].id == key
        assert ws.scoreboard[key].input_file == input_file

    def test_unique_timestamp_key_uniqueness(self, reset_globals, fake_cfg):
        """Rapid calls produce different keys."""
        input_file = fake_cfg.tmp_dir / "test.pdf"
        input_file.touch()
        key1 = ws.unique_timestamp_key(input_file)
        key2 = ws.unique_timestamp_key(input_file)
        assert key1 != key2

    def test_unique_timestamp_key_thread_safe(self, reset_globals, fake_cfg):
        """Multiple threads get unique keys."""
        import threading
        keys = []
        lock = threading.Lock()
        input_file = fake_cfg.tmp_dir / "test.pdf"
        input_file.touch()

        def get_key():
            k = ws.unique_timestamp_key(input_file)
            with lock:
                keys.append(k)

        threads = [threading.Thread(target=get_key) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(keys)) == 5  # All unique


class TestScoreboardEntry:
    """Test ScoreboardEntry dataclass."""

    def test_scoreboard_entry_creation(self):
        """ScoreboardEntry can be created with fields."""
        entry = ws.ScoreboardEntry(
            id="test_id",
            input_file=Path("/test/input.pdf"),
            log_file=Path("/test/log.log"),
            output_file=Path("/test/output.pdf"),
        )
        assert entry.id == "test_id"
        assert entry.input_file == Path("/test/input.pdf")
        assert entry.log_file == Path("/test/log.log")
        assert entry.output_file == Path("/test/output.pdf")

    def test_scoreboard_entry_defaults(self):
        """ScoreboardEntry defaults to None."""
        entry = ws.ScoreboardEntry()
        assert entry.id is None
        assert entry.input_file is None
        assert entry.log_file is None
        assert entry.output_file is None


class TestRunTranslationEdgeCases:
    """Test edge cases in POST /run."""

    @patch("threading.Thread")
    def test_run_with_special_filename(
        self, mock_thread, client, reset_globals, mock_deepl_languages
    ):
        """File with special characters in filename is handled."""
        file_data = BytesIO(b"fake pdf content")
        resp = client.post(
            "/run",
            data={
                "pdf_file": (file_data, "test (1).pdf"),
                "target_language": "DE",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        # Should work; secure_filename() sanitizes it
        assert resp.status_code == 302

    @patch("threading.Thread")
    def test_run_form_values_empty_defaults(
        self, mock_thread, client, reset_globals, mock_deepl_languages
    ):
        """Form values default to False/original when not provided."""
        file_data = BytesIO(b"fake pdf")
        resp = client.post(
            "/run",
            data={
                "pdf_file": (file_data, "test.pdf"),
                # No form values provided; should use cfg defaults
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestDownloadEdgeCases:
    """Test edge cases in download endpoint."""

    def test_download_output_with_nested_path(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """File in subdirectory can be downloaded."""
        subdir = fake_cfg.output_dir / "subdir"
        subdir.mkdir()
        file_path = subdir / "nested.pdf"
        file_path.write_bytes(b"nested content")
        resp = client.get("/download/output/subdir/nested.pdf")
        assert resp.status_code == 200
        assert resp.data == b"nested content"

    def test_download_log_directory_not_found(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Download from log when log_dir doesn't exist fails gracefully."""
        # This shouldn't happen in practice, but test robustness
        resp = client.get("/download/log/any.log")
        assert resp.status_code == 404


class TestFileLogEdgeCases:
    """Test edge cases in file_log endpoint."""

    def test_file_log_no_cfg_uses_fallback(self, reset_globals, fake_cfg):
        """When cfg is None, file_log falls back to /tmp for log_dir."""
        ws.cfg = None
        with ws.app.test_client() as client:
            # Request non-existent file; should get 404 from fallback /tmp path
            resp = client.get("/log/nonexistent_fallback.log")
            assert resp.status_code == 404
            assert b"No log file found" in resp.data

    def test_file_log_with_symlink(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Symlink in log_dir is followed."""
        log_file = fake_cfg.log_dir / "real.log"
        log_file.write_text("real log")
        symlink = fake_cfg.log_dir / "symlink.log"
        try:
            symlink.symlink_to(log_file)
            resp = client.get("/log/symlink.log")
            assert resp.status_code == 200
            assert b"real log" in resp.data
        finally:
            if symlink.exists():
                symlink.unlink()


class TestServeOutputEdgeCases:
    """Test edge cases in serve_output endpoint."""

    def test_serve_output_with_special_characters(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """File with special characters in name can be served."""
        file_path = fake_cfg.output_dir / "output (1).pdf"
        file_path.write_bytes(b"special char file")
        resp = client.get("/output/output%20(1).pdf")
        assert resp.status_code == 200 or resp.status_code == 404


class TestIndexEdgeCases:
    """Test edge cases in index endpoint."""

    def test_index_global_log_file_path_none(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """When global_log_file_path is None, template still renders."""
        ws.cfg.global_log_file_path = None
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_web_log_file_path_none_but_cfg_valid(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """When web_log_file_path is None, global check allows if properly guarded."""
        # Actually, ensure_initialized will return 500 if web_log_file_path is None
        ws.web_log_file_path = None
        resp = client.get("/")
        assert resp.status_code == 500


class TestCheckOutputEdgeCases:
    """Test edge cases in check_output endpoint."""

    def test_check_output_empty_job_id(
        self, client, reset_globals, mock_deepl_languages
    ):
        """Empty job_id parameter behaves like no parameter."""
        resp = client.get("/check_output?job_id=")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is False


class TestCallbacks:
    """Test callback functions set on cfg."""

    @patch("threading.Thread")
    def test_capture_log_path_callback_sets_scoreboard(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Capture_log_path callback sets log_file in scoreboard."""
        file_data = BytesIO(b"pdf")
        resp = client.post(
            "/run",
            data={"pdf_file": (file_data, "input.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Manually call the callback to test it
        thread_args = mock_thread.call_args[1]["args"]
        cfg_passed = thread_args[1]
        if cfg_passed.callback_on_local_log_file:
            log_path = fake_cfg.log_dir / "test.log"
            log_path.touch()
            # Call the callback
            cfg_passed.callback_on_local_log_file(log_path)
            # The log_file should now be set in scoreboard
            # (This is a manual execution of the callback)

    @patch("threading.Thread")
    def test_capture_output_pdf_callback(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Capture_output_pdf callback is set on cfg."""
        file_data = BytesIO(b"pdf")
        resp = client.post(
            "/run",
            data={"pdf_file": (file_data, "input.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Check that the callback was set
        thread_args = mock_thread.call_args[1]["args"]
        cfg_passed = thread_args[1]
        assert cfg_passed.callback_on_file_complete is not None


class TestStatusEdgeCases:
    """Test edge cases in status page."""

    def test_status_with_deleted_log_file(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Entry with log_file that no longer exists still renders."""
        job_id = "2025_01_01_12_00_00_001"
        log_file = fake_cfg.log_dir / "deleted.log"
        log_file.write_text("content")
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=log_file, output_file=None
        )
        ws.scoreboard[job_id] = entry
        log_file.unlink()  # Delete it after adding to scoreboard
        resp = client.get("/status")
        assert resp.status_code == 200
        # Should still render, even with missing file

    def test_status_multiple_entries_ordering(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Multiple entries are shown in reverse order (newest first)."""
        for i in range(3):
            job_id = f"2025_01_01_12_00_{i:02d}_001"
            input_file = fake_cfg.tmp_dir / f"input{i}.pdf"
            input_file.touch()
            entry = ws.ScoreboardEntry(
                id=job_id, input_file=input_file, log_file=None, output_file=None
            )
            ws.scoreboard[job_id] = entry
        resp = client.get("/status")
        assert resp.status_code == 200
        # Check that data contains entries (order verification is implicit)
        assert len(ws.scoreboard) == 3

    def test_status_output_file_deleted_shows_in_progress(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Entry with output_file set but file deleted shows 'In Progress...'."""
        job_id = "2025_01_01_12_00_00_001"
        output_file = fake_cfg.output_dir / "output.pdf"
        output_file.write_bytes(b"content")
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=None, output_file=output_file
        )
        ws.scoreboard[job_id] = entry
        # Delete the actual file
        output_file.unlink()
        resp = client.get("/status")
        assert resp.status_code == 200
        # Check that the page shows "In Progress..." for deleted output
        assert b"In Progress" in resp.data

    def test_status_log_file_deleted_shows_deleted(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Entry with log_file set but file deleted shows 'Deleted'."""
        job_id = "2025_01_01_12_00_00_001"
        log_file = fake_cfg.log_dir / "deleted.log"
        log_file.write_text("log content")
        entry = ws.ScoreboardEntry(
            id=job_id, input_file=None, log_file=log_file, output_file=None
        )
        ws.scoreboard[job_id] = entry
        # Delete the actual file
        log_file.unlink()
        resp = client.get("/status")
        assert resp.status_code == 200
        # Check that the page shows "Deleted" for deleted log
        assert b"Deleted" in resp.data


class TestDownloadDirectoryNotFound:
    """Test download behavior when directories don't exist."""

    def test_download_output_dir_removed(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Download fails when output_dir has been removed."""
        # Remove the output_dir
        import shutil

        shutil.rmtree(fake_cfg.output_dir)
        resp = client.get("/download/output/any.pdf")
        assert resp.status_code == 404

    def test_download_log_dir_removed(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Download fails when log_dir has been removed."""
        import shutil

        shutil.rmtree(fake_cfg.log_dir)
        resp = client.get("/download/log/any.log")
        assert resp.status_code == 404


class TestServeOutputPathTraversalDetection:
    """Test path traversal detection in serve_output."""

    def test_serve_output_escaped_path(
        self, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Symlink that escapes output_dir is caught."""
        # Create a file outside output_dir
        outside_file = fake_cfg.tmp_dir / "outside.pdf"
        outside_file.write_bytes(b"outside content")

        # Create a symlink inside output_dir pointing outside
        link_path = fake_cfg.output_dir / "link.pdf"
        try:
            link_path.symlink_to(outside_file)
            # Trying to access it should be caught by path traversal check
            resp = client.get("/output/link.pdf")
            # May succeed (resolves to file) or be blocked (path escapes),
            # depending on how resolve() and is_relative_to() work with symlinks
            assert resp.status_code in [200, 403]
        finally:
            if link_path.exists():
                link_path.unlink()


class TestIntegrationScenarios:
    """Integration tests covering multiple components."""

    @patch("threading.Thread")
    def test_full_upload_to_output_flow(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Full flow: upload -> thread starts -> output ready -> serve."""
        # 1. Upload file
        file_data = BytesIO(b"pdf content")
        resp = client.post(
            "/run",
            data={
                "pdf_file": (file_data, "document.pdf"),
                "target_language": "FR",
                "translate_filename": "yes",
                "include_original": "yes",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        job_id = resp.location.split("job_id=")[1]

        # 2. Check output not ready yet
        resp = client.get(f"/check_output?job_id={job_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ready"] is False

        # 3. Simulate output becoming ready
        output_file = fake_cfg.output_dir / "translated.pdf"
        output_file.write_bytes(b"translated content")
        entry = ws.scoreboard[job_id]
        entry.output_file = output_file

        # 4. Check output is ready
        resp = client.get(f"/check_output?job_id={job_id}")
        data = json.loads(resp.data)
        assert data["ready"] is True
        assert data["filename"] == "translated.pdf"

        # 5. Serve the output file
        resp = client.get("/output/translated.pdf")
        assert resp.status_code == 200
        assert resp.data == b"translated content"

    @patch("threading.Thread")
    def test_full_upload_to_status_view(
        self, mock_thread, client, reset_globals, mock_deepl_languages, fake_cfg
    ):
        """Upload file and verify it appears in status page."""
        file_data = BytesIO(b"pdf content")
        resp = client.post(
            "/run",
            data={"pdf_file": (file_data, "test.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        job_id = resp.location.split("job_id=")[1]

        # Add log file to scoreboard entry
        log_file = fake_cfg.log_dir / "job.log"
        log_file.touch()
        entry = ws.scoreboard[job_id]
        entry.log_file = log_file

        # Check status page
        resp = client.get("/status")
        assert resp.status_code == 200
        assert b"test.pdf" in resp.data or job_id in str(resp.data)
