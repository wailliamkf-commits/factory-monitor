"""Validated, local-only configuration persistence."""

from __future__ import annotations

import copy
import ipaddress
import json
import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def _camera(index: int) -> dict:
    column, row = index % 4, index // 4
    return {
        "id": f"CAM{index + 1:02d}",
        "name": f"摄像头{index + 1:02d}",
        "enabled": True,
        "crop": [column / 4, row / 3, 1 / 4, 1 / 3],
        "material_roi": [],
        "station_roi": [],
        "exit_line": [],
        "absence_seconds": 300,
        "schedule": {"days": list(range(7)), "active": [["00:00", "24:00"]], "breaks": []},
        "map_position": [column / 3 if column else 0.0, row / 2 if row else 0.0],
        "heading": 0,
        "related": [],
        "view_action": {},
    }


_DEFAULT = {
    "schema_version": 1,
    "source": {
        "backend": "auto",
        "window_title": "",
        "display_index": 0,
        "expected_size": [0, 0],
        "layout_version": 1,
        "calibrated": False,
    },
    "detection": {"model_path": "models/yolo11n.pt", "device": "auto", "fps": 5, "confidence": 0.35},
    "review": {
        "enabled": True,
        "endpoint": "http://127.0.0.1:11435",
        "model": "qwen3-vl:2b-instruct",
        "timeout_seconds": 15,
        "cloud_enabled": False,
    },
    "evidence": {
        "pre_seconds": 30,
        "post_seconds": 60,
        "preview_seconds": 30,
        "retain_completed": 20,
        "max_inflight": 10,
        "max_disk_mb": 10240,
    },
    "switching": {
        "enabled": False,
        "calibrated": False,
        "detail_seconds": 15,
        "grid_seconds": 30,
        "blind_budget_seconds": 180,
        "verification": {},
    },
    "cameras": [],
    "floorplan": {"image_path": "", "zones": []},
}
_DEFAULT["cameras"] = [_camera(index) for index in range(10)]


def default_config() -> dict:
    """Return a fresh V1 template; it is deliberately uncalibrated."""
    return copy.deepcopy(_DEFAULT)


def _require_dict(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_keys(value: dict, keys: set[str], name: str) -> None:
    missing = keys.difference(value)
    if missing:
        raise ValueError(f"{name} missing required keys: {', '.join(sorted(missing))}")


def _number(value: object, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and numeric > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return numeric


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _normalized_point(point: object, name: str) -> None:
    if not isinstance(point, list) or len(point) != 2:
        raise ValueError(f"{name} must be [x, y]")
    _number(point[0], f"{name}[0]", minimum=0, maximum=1)
    _number(point[1], f"{name}[1]", minimum=0, maximum=1)


def _normalized_polygon(points: object, name: str) -> None:
    if points == []:
        return
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError(f"{name} must be empty or have at least three points")
    for index, point in enumerate(points):
        _normalized_point(point, f"{name}[{index}]")


def _minutes(value: object, name: str) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError(f"{name} must be HH:MM")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM") from exc
    if not 0 <= minute < 60 or not 0 <= hour <= 24 or (hour == 24 and minute != 0):
        raise ValueError(f"{name} must be a valid 24-hour time")
    return hour * 60 + minute


def _schedule(value: object, name: str) -> None:
    schedule = _require_dict(value, name)
    _require_keys(schedule, {"days", "active", "breaks"}, name)
    days = schedule["days"]
    if not isinstance(days, list) or not days or len(set(days)) != len(days):
        raise ValueError(f"{name}.days must be a non-empty unique list")
    for day in days:
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
            raise ValueError(f"{name}.days must contain weekday indexes 0..6")
    for section in ("active", "breaks"):
        ranges = schedule[section]
        if not isinstance(ranges, list):
            raise ValueError(f"{name}.{section} must be a list")
        for index, interval in enumerate(ranges):
            if not isinstance(interval, list) or len(interval) != 2:
                raise ValueError(f"{name}.{section}[{index}] must be [start, end]")
            if _minutes(interval[0], f"{name}.{section}[{index}][0]") >= _minutes(interval[1], f"{name}.{section}[{index}][1]"):
                raise ValueError(f"{name}.{section}[{index}] must have start before end")


def _loopback_endpoint(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("review.endpoint must be a URL")
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("review.endpoint must include a loopback host")
    try:
        host_is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        host_is_loopback = parsed.hostname.lower() == "localhost"
    if not host_is_loopback:
        raise ValueError("review.endpoint must use a loopback address")
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("review.endpoint must be a local http URL without credentials")


def validate_config(config: object) -> None:
    """Validate the complete V1 safety boundary, raising ``ValueError`` on bad input."""
    root = _require_dict(config, "config")
    _require_keys(root, {"schema_version", "source", "detection", "review", "evidence", "switching", "cameras", "floorplan"}, "config")
    if root["schema_version"] != 1:
        raise ValueError("schema_version must be 1")

    source = _require_dict(root["source"], "source")
    _require_keys(source, {"backend", "window_title", "display_index", "expected_size", "layout_version", "calibrated"}, "source")
    if source["backend"] not in {"auto", "wgc", "screencapturekit", "display"}:
        raise ValueError("source.backend is unsupported")
    if not isinstance(source["window_title"], str) or not isinstance(source["calibrated"], bool):
        raise ValueError("source window_title/calibrated types are invalid")
    _integer(source["display_index"], "source.display_index", minimum=0)
    _integer(source["layout_version"], "source.layout_version", minimum=1)
    size = source["expected_size"]
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError("source.expected_size must be [width, height]")
    for index, side in enumerate(size):
        _integer(side, f"source.expected_size[{index}]", minimum=0)
    if source["calibrated"]:
        if not all(side > 0 for side in size):
            raise ValueError("source.expected_size must be positive after calibration")
        if source["backend"] in {"auto", "wgc", "screencapturekit"} and not source["window_title"].strip():
            raise ValueError("source.window_title must identify the calibrated capture window")

    detection = _require_dict(root["detection"], "detection")
    _require_keys(detection, {"model_path", "device", "fps", "confidence"}, "detection")
    if not isinstance(detection["model_path"], str) or not detection["model_path"] or not isinstance(detection["device"], str):
        raise ValueError("detection model_path/device are invalid")
    _number(detection["fps"], "detection.fps", minimum=0.1, maximum=60)
    _number(detection["confidence"], "detection.confidence", minimum=0, maximum=1)

    review = _require_dict(root["review"], "review")
    _require_keys(review, {"enabled", "endpoint", "model", "timeout_seconds", "cloud_enabled"}, "review")
    if not isinstance(review["enabled"], bool) or not isinstance(review["cloud_enabled"], bool):
        raise ValueError("review enabled/cloud_enabled must be booleans")
    if review["cloud_enabled"]:
        raise ValueError("review.cloud_enabled must remain false in V1")
    _loopback_endpoint(review["endpoint"])
    if not isinstance(review["model"], str) or not review["model"]:
        raise ValueError("review.model must be a non-empty string")
    _number(review["timeout_seconds"], "review.timeout_seconds", minimum=1, maximum=120)

    evidence = _require_dict(root["evidence"], "evidence")
    _require_keys(evidence, {"pre_seconds", "post_seconds", "preview_seconds", "retain_completed", "max_inflight", "max_disk_mb"}, "evidence")
    for key in evidence:
        _integer(evidence[key], f"evidence.{key}", minimum=0 if key == "retain_completed" else 1)

    switching = _require_dict(root["switching"], "switching")
    _require_keys(switching, {"enabled", "calibrated", "detail_seconds", "grid_seconds", "blind_budget_seconds", "verification"}, "switching")
    if not isinstance(switching["enabled"], bool) or not isinstance(switching["calibrated"], bool) or not isinstance(switching["verification"], dict):
        raise ValueError("switching enabled/calibrated/verification are invalid")
    _number(switching["detail_seconds"], "switching.detail_seconds", minimum=1, maximum=15)
    _number(switching["grid_seconds"], "switching.grid_seconds", minimum=1)
    _number(switching["blind_budget_seconds"], "switching.blind_budget_seconds", minimum=1, maximum=180)

    cameras = root["cameras"]
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 10:
        raise ValueError("cameras must contain 1 to 10 entries")
    identifiers: set[str] = set()
    for index, camera_value in enumerate(cameras):
        name = f"cameras[{index}]"
        camera = _require_dict(camera_value, name)
        _require_keys(camera, {"id", "name", "enabled", "crop", "material_roi", "station_roi", "exit_line", "absence_seconds", "schedule", "map_position", "heading", "related", "view_action"}, name)
        identifier = camera["id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError(f"{name}.id must be a unique non-empty string")
        identifiers.add(identifier)
        if not isinstance(camera["name"], str) or not isinstance(camera["enabled"], bool) or not isinstance(camera["view_action"], dict):
            raise ValueError(f"{name} name/enabled/view_action are invalid")
        crop = camera["crop"]
        if not isinstance(crop, list) or len(crop) != 4:
            raise ValueError(f"{name}.crop must be [x, y, width, height]")
        x, y, width, height = (_number(point, f"{name}.crop[{offset}]", minimum=0, maximum=1) for offset, point in enumerate(crop))
        if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ValueError(f"{name}.crop must stay inside the normalized frame")
        _normalized_polygon(camera["material_roi"], f"{name}.material_roi")
        _normalized_polygon(camera["station_roi"], f"{name}.station_roi")
        line = camera["exit_line"]
        if line != []:
            if not isinstance(line, list) or len(line) != 2:
                raise ValueError(f"{name}.exit_line must be empty or two points")
            _normalized_point(line[0], f"{name}.exit_line[0]")
            _normalized_point(line[1], f"{name}.exit_line[1]")
        _number(camera["absence_seconds"], f"{name}.absence_seconds", minimum=1)
        _schedule(camera["schedule"], f"{name}.schedule")
        _normalized_point(camera["map_position"], f"{name}.map_position")
        _number(camera["heading"], f"{name}.heading", minimum=0, maximum=360)
        if not isinstance(camera["related"], list) or any(not isinstance(item, str) for item in camera["related"]):
            raise ValueError(f"{name}.related must be a list of camera ids")
    for camera in cameras:
        if any(item not in identifiers or item == camera["id"] for item in camera["related"]):
            raise ValueError("camera.related must name other configured cameras")

    floorplan = _require_dict(root["floorplan"], "floorplan")
    _require_keys(floorplan, {"image_path", "zones"}, "floorplan")
    if not isinstance(floorplan["image_path"], str) or not isinstance(floorplan["zones"], list):
        raise ValueError("floorplan image_path/zones are invalid")


def load_config(path: str | Path) -> dict:
    path = Path(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load configuration: {path}") from exc
    validate_config(loaded)
    return loaded


def save_config(config: dict, path: str | Path) -> None:
    """Validate before atomically replacing the target configuration file."""
    validate_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
