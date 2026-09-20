from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from factory_monitor.cli import main
from factory_monitor.config import load_config


def _call(arguments: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(arguments)
    return code, json.loads(output.getvalue())


def test_init_creates_a_safe_uncalibrated_config_without_overwriting(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    code, result = _call(["init", "--config", str(config_path)])

    assert code == 0
    assert result["ok"] is True
    assert load_config(config_path)["source"]["calibrated"] is False

    second_code, second = _call(["init", "--config", str(config_path)])
    assert second_code == 2
    assert second["ok"] is False
    assert load_config(config_path)["schema_version"] == 1


def test_evaluate_writes_machine_report_that_cannot_claim_field_pass(tmp_path: Path) -> None:
    source = tmp_path / "cases.json"
    destination = tmp_path / "evaluation.json"
    source.write_text(json.dumps([{
        "id": "case-1", "kind": "material_candidate", "truth": "positive",
        "outcome": "timeout", "stage": "candidate"
    }]), encoding="utf-8")

    code, result = _call(["evaluate", "--input", str(source), "--output", str(destination), "--environment", "field"])

    assert code == 0
    assert result["field_gate"]["status"] == "FAIL"
    assert json.loads(destination.read_text(encoding="utf-8"))["failure_outcomes"]["timeout_or_unknown"] == 1


def test_preflight_is_read_only_and_reports_local_and_field_status(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    assert main(["init", "--config", str(config_path)]) == 0
    data_dir = tmp_path / "not-created"

    code, result = _call(["preflight", "--config", str(config_path), "--data-dir", str(data_dir)])

    assert code == 0
    assert result["field_gate"]["status"] == "FAIL"
    assert result["storage"]["path_exists"] is False
    assert result["system"]["operating_system"]
