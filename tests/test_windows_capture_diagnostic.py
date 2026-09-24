import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_windows_capture.py"


def _module():
    spec = importlib.util.spec_from_file_location("diagnose_windows_capture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_rejects_missing_or_unauthorized_target_before_capture():
    module = _module()
    with pytest.raises(SystemExit):
        module.parse_args([])
    with pytest.raises(SystemExit):
        module.parse_args(["--monitor-index", "1"])
    with pytest.raises(SystemExit):
        module.parse_args(["--window-hwnd", "7", "--monitor-index", "1", "--allow-display-capture"])
    with pytest.raises(SystemExit):
        module.parse_args(["--window-hwnd", "0"])
    with pytest.raises(SystemExit):
        module.parse_args(["--window-hwnd", "7", "--seconds", "31"])
    assert module.parse_args(["--window-hwnd", "7"]).seconds == 8
    aliases = module.parse_args(["--window-hwnd", "7", "--duration", "9", "--output", "result.json"])
    assert aliases.seconds == 9 and aliases.summary == Path("result.json")


def test_summary_reports_single_frame_tail_gap_and_no_frame():
    module = _module()
    one = module.summarize_callbacks([(1.0, 2560, 1440)], end_monotonic=9.0)
    zero = module.summarize_callbacks([], end_monotonic=9.0)
    many = module.summarize_callbacks([(1.0, 100, 50), (3.0, 100, 50), (4.0, 100, 50)], 9.0)
    assert one == {"callback_count": 1, "first_offset_s": 1.0, "last_offset_s": 1.0,
                   "max_inter_callback_gap_s": None, "tail_gap_s": 8.0, "dimensions": [[2560, 1440]]}
    assert zero["callback_count"] == 0 and zero["tail_gap_s"] == 9.0
    assert many["max_inter_callback_gap_s"] == 2.0 and many["tail_gap_s"] == 5.0


def test_unsupported_hwnd_fails_closed_without_provider_start():
    module = _module()

    class Provider:
        def __init__(self, window_name=None):
            raise AssertionError("must not instantiate")

    with pytest.raises(module.DiagnosticError, match="window_hwnd"):
        module.require_target_parameter(Provider, "window_hwnd")


def test_worker_counts_metadata_without_accessing_pixels():
    module = _module()

    class Frame:
        width = 2560
        height = 1440

        @property
        def frame_buffer(self):
            raise AssertionError("pixel access forbidden")

    class Control:
        def stop(self):
            return None

        def wait(self):
            return None

        def is_finished(self):
            return True

    class Provider:
        def __init__(self, *, window_hwnd=None):
            assert window_hwnd == 7
            self.handlers = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def start_free_threaded(self):
            self.handlers["on_frame_arrived"](Frame(), None)
            return Control()

    result = module.collect_callbacks(Provider, {"window_hwnd": 7}, 0.01)
    assert result["callback_count"] == 1
    assert result["status"] == "single_frame"
    assert result["closed"] is False
    assert result["stop_succeeded"] is True
    assert result["thread_finished"] is True
    assert result["dimensions"] == [[2560, 1440]]


def test_worker_reports_zero_frames_and_start_or_stop_errors():
    module = _module()

    class Control:
        def stop(self):
            raise RuntimeError("private source detail")

        def wait(self):
            return None

        def is_finished(self):
            return True

    class Provider:
        def __init__(self, *, window_hwnd=None):
            self.handlers = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def start_free_threaded(self):
            return Control()

    empty = module.collect_callbacks(Provider, {"window_hwnd": 7}, 0.01)
    assert empty["callback_count"] == 0
    assert empty["status"] == "error"
    assert empty["worker_error"] == "RuntimeError"
    assert "private source detail" not in json.dumps(empty)

    class StartFails(Provider):
        def start_free_threaded(self):
            raise ValueError("private source detail")

    failed = module.collect_callbacks(StartFails, {"window_hwnd": 7}, 0.01)
    assert failed["status"] == "error"
    assert failed["worker_error"] == "ValueError"
    assert failed["stop_succeeded"] is False


def test_worker_marks_zero_frames_without_error_as_inconclusive():
    module = _module()

    class Control:
        def stop(self):
            return None

        def wait(self):
            return None

        def is_finished(self):
            return True

    class Provider:
        def __init__(self, *, window_hwnd=None):
            pass

        def event(self, handler):
            return handler

        def start_free_threaded(self):
            return Control()

    result = module.collect_callbacks(Provider, {"window_hwnd": 7}, 0.01)
    assert result["callback_count"] == 0
    assert result["status"] == "no_frames"


def test_parent_keeps_provider_version_when_worker_times_out(monkeypatch, tmp_path):
    module = _module()
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "1.5.0")
    monkeypatch.setattr(module, "run_isolated", lambda command, timeout, summary: {"status": "timeout", "error_type": "ChildTimeout"})
    summary = tmp_path / "result.json"
    assert module.main(["--window-hwnd", "7", "--duration", "8", "--output", str(summary)]) == 1
    result = json.loads(summary.read_text())
    assert result["provider_version"] == "1.5.0"
    assert result["status"] == "timeout"


def test_isolated_timeout_reaps_child_and_records_failure(tmp_path):
    module = _module()
    summary = tmp_path / "summary.json"
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()
    result = module.run_isolated(command, 0.1, summary)
    assert time.monotonic() - started < 3
    assert result["status"] == "timeout"
    assert json.loads(summary.read_text())["status"] == "timeout"


def test_isolated_nonzero_child_keeps_valid_no_frames_diagnosis(tmp_path):
    module = _module()
    summary = tmp_path / "result.json"
    command = [sys.executable, "-c", "import json,sys; print(json.dumps({'status':'no_frames','callback_count':0})); sys.exit(1)"]
    result = module.run_isolated(command, 2, summary)
    assert result["status"] == "no_frames"
    assert json.loads(summary.read_text())["callback_count"] == 0


def test_isolated_nonzero_child_keeps_valid_single_frame_diagnosis(tmp_path):
    module = _module()
    summary = tmp_path / "result.json"
    command = [sys.executable, "-c", "import json,sys; print(json.dumps({'status':'single_frame','callback_count':1,'tail_gap_s':7.9})); sys.exit(1)"]
    result = module.run_isolated(command, 2, summary)
    assert result["status"] == "single_frame"
    assert json.loads(summary.read_text())["tail_gap_s"] == 7.9


def test_parent_cli_returns_nonzero_for_eight_second_single_frame(monkeypatch, tmp_path):
    module = _module()
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "1.5.0")
    monkeypatch.setattr(module, "run_isolated", lambda command, timeout, summary: {
        "status": "single_frame", "callback_count": 1, "tail_gap_s": 7.9,
    })
    summary = tmp_path / "result.json"
    assert module.main(["--window-hwnd", "7", "--duration", "8", "--output", str(summary)]) == 1
    assert json.loads(summary.read_text())["status"] == "single_frame"


def test_worker_emits_safe_reason_code_for_own_windows_preflight(monkeypatch, capsys):
    module = _module()
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "1.5.0")
    monkeypatch.setattr(module.sys, "platform", "darwin")
    assert module.main(["--window-hwnd", "7", "--worker"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "windows_only"
