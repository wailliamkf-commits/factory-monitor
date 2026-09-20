"""Calibrated window-relative controls with image readback verification."""

from __future__ import annotations

import time
import ctypes
import subprocess
import sys
from typing import Any, Callable

import cv2
import numpy as np


def perceptual_signature(image: np.ndarray) -> str:
    if image.size == 0:
        raise ValueError("signature image is empty")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8]
    values = low.flatten()[1:]
    median = float(np.median(values))
    bits = "".join("1" if value > median else "0" for value in values)
    return f"{int(bits, 2):016x}"


def _distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _load_template(spec: dict[str, Any]) -> np.ndarray:
    path = spec.get("template_path")
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path else None
    if template is None or template.size < 64 or float(template.std()) < 5.0:
        raise ValueError("identity template is missing, blank, or too small")
    return template


def _pixel_score(observed: np.ndarray, reference: np.ndarray) -> float:
    gray = cv2.cvtColor(observed, cv2.COLOR_BGR2GRAY) if observed.ndim == 3 else observed
    normalized = cv2.resize(gray, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    return 1.0 - float(np.mean(cv2.absdiff(normalized, reference))) / 255.0


def _verify_identity(
    image: np.ndarray,
    expected_id: str,
    spec: dict[str, Any],
    all_specs: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    height, width = image.shape[:2]
    try:
        x, y, crop_width, crop_height = spec["roi"]
        observed = image[
            round(y * height) : round((y + crop_height) * height),
            round(x * width) : round((x + crop_width) * width),
        ]
        if observed.size == 0:
            raise ValueError("empty label ROI")
        templates = {camera_id: _load_template(candidate) for camera_id, candidate in all_specs.items()}
        scores = {camera_id: _pixel_score(observed, template) for camera_id, template in templates.items()}
    except (KeyError, TypeError, ValueError, cv2.error) as exc:
        return False, f"camera {expected_id} identity readback is invalid: {exc}"
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_id, best_score = ordered[0]
    expected_score = scores.get(expected_id, -1.0)
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
    minimum = float(spec.get("min_score", 0.92))
    margin = float(spec.get("min_margin", 0.02))
    if best_id != expected_id:
        return False, f"camera {expected_id} identity readback matched {best_id}"
    if expected_score < minimum:
        return False, f"camera {expected_id} identity score below threshold"
    if best_score - runner_up < margin:
        return False, f"camera {expected_id} identity readback is ambiguous"
    return True, f"camera {expected_id} identity verified"


def verify_grid_mapping(
    image: np.ndarray,
    expected_size: tuple[int, int],
    signatures: dict[str, dict[str, Any]],
    cameras: list[dict[str, Any]],
) -> tuple[bool, str]:
    if (image.shape[1], image.shape[0]) != expected_size:
        return False, "source dimensions changed; mapping invalid"
    enabled = [camera["id"] for camera in cameras if camera.get("enabled")]
    missing = [camera_id for camera_id in enabled if camera_id not in signatures]
    if missing:
        return False, f"missing grid identity readback for {', '.join(missing)}"
    extra = sorted(set(signatures).difference(enabled))
    if extra:
        return False, f"stale grid identity readback for {', '.join(extra)}"
    height, width = image.shape[:2]
    for camera_id in enabled:
        valid, reason = _verify_identity(image, camera_id, signatures[camera_id], signatures)
        if not valid:
            return False, reason
    return True, "all grid camera identities verified"


def verify_detail_mapping(
    image: np.ndarray,
    expected_size: tuple[int, int],
    expected_id: str,
    identities: dict[str, dict[str, Any]],
    cameras: list[dict[str, Any]],
) -> tuple[bool, str]:
    if (image.shape[1], image.shape[0]) != expected_size:
        return False, "source dimensions changed; mapping invalid"
    enabled = {camera["id"] for camera in cameras if camera.get("enabled")}
    if set(identities) != enabled:
        return False, "detail identity templates do not match enabled cameras"
    if expected_id not in enabled:
        return False, f"unexpected detail camera {expected_id}"
    spec = identities[expected_id]
    ok, reason = _verify_identity(image, expected_id, spec, identities)
    if not ok:
        return ok, reason
    try:
        x, y, width, height = spec["layout_roi"]
        observed = image[
            round(y * image.shape[0]) : round((y + height) * image.shape[0]),
            round(x * image.shape[1]) : round((x + width) * image.shape[1]),
        ]
        reference = _load_template({"template_path": spec["layout_template_path"]})
        score = _pixel_score(observed, reference)
        if score < float(spec.get("min_layout_score", 0.97)):
            return False, f"camera {expected_id} detail layout indicator failed"
    except (KeyError, TypeError, ValueError, cv2.error) as exc:
        return False, f"camera {expected_id} detail layout indicator is invalid: {exc}"
    return True, f"camera {expected_id} identity and detail layout verified"


class CalibratedViewController:
    def __init__(
        self,
        *,
        source_size: tuple[int, int],
        actions: dict[str, dict[str, Any]],
        verification: dict[str, Any],
        capture: Callable[[float], np.ndarray],
        clicker: Any,
        calibrated: bool,
        settle_seconds: float = 0,
    ) -> None:
        self.source_size = source_size
        self.actions = actions
        self.verification = verification
        self.capture = capture
        self.clicker = clicker
        self.calibrated = calibrated
        self.settle_seconds = settle_seconds
        self._active_camera: str | None = None

    def request_view(self, camera_id: str) -> dict[str, Any]:
        problem = self._preflight(camera_id)
        if problem:
            return {"ok": False, "reason": problem}
        grid_identities = self.verification.get("grid_identities", {})
        if set(grid_identities) != set(self.verification.get("detail_identities", {})):
            return {"ok": False, "reason": "grid identity set is incomplete"}
        pre_click = self.capture(time.monotonic())
        for grid_camera, spec in grid_identities.items():
            verified = self._verify_on_image(pre_click, grid_camera, spec, grid_identities)
            if not verified["ok"]:
                return {"ok": False, "reason": f"pre-click grid check failed: {verified['reason']}"}
        point = self.actions[camera_id]["click"]
        self.clicker.click(round(point[0] * self.source_size[0]), round(point[1] * self.source_size[1]))
        clicked_at = time.monotonic()
        if self.settle_seconds:
            time.sleep(self.settle_seconds)
        identities = self.verification["detail_identities"]
        detail_image = self.capture(clicked_at)
        ok, reason = verify_detail_mapping(
            detail_image,
            self.source_size,
            camera_id,
            identities,
            [{"id": item, "enabled": True} for item in identities],
        )
        result = {"ok": ok, "reason": reason}
        if result["ok"]:
            self._active_camera = camera_id
        return result

    def return_grid(self) -> dict[str, Any]:
        if not self.calibrated:
            return {"ok": False, "reason": "automatic view control is not calibrated"}
        if list(self.verification.get("source_size", ())) != list(self.source_size):
            return {"ok": False, "reason": "source dimensions changed; mapping invalid"}
        action = self.verification.get("grid_action")
        identities = self.verification.get("grid_identities")
        if not action or not identities:
            return {"ok": False, "reason": "grid action and all-camera readback are required"}
        if self._active_camera is None:
            return {"ok": False, "reason": "no verified detail view is active"}
        detail_identities = self.verification.get("detail_identities", {})
        active_spec = detail_identities.get(self._active_camera)
        if active_spec is None:
            return {"ok": False, "reason": "active detail identity calibration is missing"}
        pre_click = self.capture(time.monotonic())
        active_ok, active_reason = verify_detail_mapping(
            pre_click,
            self.source_size,
            self._active_camera,
            detail_identities,
            [{"id": item, "enabled": True} for item in detail_identities],
        )
        active_result = {"ok": active_ok, "reason": active_reason}
        if not active_result["ok"]:
            return {"ok": False, "reason": f"pre-click detail check failed: {active_result['reason']}"}
        self.clicker.click(round(action[0] * self.source_size[0]), round(action[1] * self.source_size[1]))
        clicked_at = time.monotonic()
        if self.settle_seconds:
            time.sleep(self.settle_seconds)
        image = self.capture(clicked_at)
        for camera_id, spec in identities.items():
            result = self._verify_on_image(image, camera_id, spec, identities)
            if not result["ok"]:
                return result
        self._active_camera = None
        return {"ok": True, "reason": "grid identities verified"}

    def _preflight(self, camera_id: str) -> str | None:
        if not self.calibrated:
            return "automatic view control is not calibrated"
        if list(self.verification.get("source_size", ())) != list(self.source_size):
            return "source dimensions changed; mapping invalid"
        if camera_id not in self.actions or "click" not in self.actions[camera_id]:
            return f"no calibrated action for {camera_id}"
        if camera_id not in self.verification.get("detail_identities", {}):
            return f"no identity template for {camera_id}"
        detail = self.verification["detail_identities"][camera_id]
        if "layout_roi" not in detail or "layout_template_path" not in detail:
            return f"no distinct detail layout indicator for {camera_id}"
        return None

    def _verify(self, image: np.ndarray, camera_id: str, spec: dict[str, Any], all_specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return self._verify_on_image(image, camera_id, spec, all_specs)

    def _verify_on_image(self, image: np.ndarray, camera_id: str, spec: dict[str, Any], all_specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if (image.shape[1], image.shape[0]) != self.source_size:
            return {"ok": False, "reason": "source dimensions changed; mapping invalid"}
        ok, reason = _verify_identity(image, camera_id, spec, all_specs)
        return {"ok": ok, "reason": reason}


class NativeWindowClicker:
    """Issue a left click relative to a calibrated window content origin."""

    def __init__(self, origin: tuple[int, int], target_guard: dict[str, Any]) -> None:
        self.origin = (int(origin[0]), int(origin[1]))
        self.target_guard = target_guard
        if not target_guard.get("window_title") or target_guard.get("window_origin") != list(self.origin):
            raise RuntimeError("native focus guard requires exact window title and calibrated origin")
        if len(target_guard.get("window_size", [])) != 2:
            raise RuntimeError("native focus guard requires calibrated window size")
        if sys.platform not in {"darwin", "win32"}:
            raise RuntimeError("native click control is supported only on macOS and Windows")

    def click(self, x: int, y: int) -> None:
        self._verify_target()
        absolute_x, absolute_y = self.origin[0] + int(x), self.origin[1] + int(y)
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            if not user32.SetCursorPos(absolute_x, absolute_y):
                raise RuntimeError("Windows could not position the pointer")
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        services.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        services.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        services.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        point = CGPoint(absolute_x, absolute_y)
        for event_type in (5, 1, 2):  # moved, left down, left up
            event = services.CGEventCreateMouseEvent(None, event_type, point, 0)
            if not event:
                raise RuntimeError("macOS could not create pointer event; check Accessibility permission")
            services.CGEventPost(1, event)
            core_foundation.CFRelease(event)

    def _verify_target(self) -> None:
        expected_title = self.target_guard["window_title"]
        expected_origin = tuple(self.target_guard["window_origin"])
        expected_size = tuple(self.target_guard["window_size"])
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            handle = user32.GetForegroundWindow()
            if not handle:
                raise RuntimeError("no foreground window; automatic click disabled")
            user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            length = user32.GetWindowTextLengthW(handle)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            user32.GetWindowTextW(handle, title, length + 1)

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            rect = RECT()
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
            user32.GetWindowRect.restype = ctypes.c_bool
            if not user32.GetWindowRect(handle, ctypes.byref(rect)):
                raise RuntimeError("could not verify foreground window bounds")
            origin = (rect.left, rect.top)
            size = (rect.right - rect.left, rect.bottom - rect.top)
            title = title.value
        else:
            script = """
tell application "System Events"
  set p to first application process whose frontmost is true
  set w to front window of p
  set xy to position of w
  set wh to size of w
  return (name of w) & tab & (item 1 of xy) & tab & (item 2 of xy) & tab & (item 1 of wh) & tab & (item 2 of wh)
end tell
"""
            result = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, timeout=3)
            if result.returncode != 0:
                raise RuntimeError("could not verify focused macOS window; check Accessibility permission")
            parts = result.stdout.strip().split("\t")
            if len(parts) != 5:
                raise RuntimeError("focused macOS window readback was invalid")
            title = parts[0]
            origin = (int(parts[1]), int(parts[2]))
            size = (int(parts[3]), int(parts[4]))
        if title != expected_title or origin != expected_origin or size != expected_size:
            raise RuntimeError("focused window identity/origin/size changed; automatic click disabled")
