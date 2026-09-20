"""Bounded, verification-gated global detail/grid switching policy."""

from __future__ import annotations

from collections import deque

from .config import validate_config


class SwitchPolicy:
    def __init__(self, config: dict) -> None:
        validate_config(config)
        self._config = config
        self._cameras = {camera["id"] for camera in config["cameras"] if camera["enabled"]}
        switching = config["switching"]
        self._automation_enabled = bool(switching["enabled"] and switching["calibrated"] and config["source"]["calibrated"])
        self._reason = None if self._automation_enabled else "automation requires source and switch calibration"
        self._faulted = False
        self._queue: deque[tuple[int, int, str, str, float]] = deque()
        self._sequence = 0
        self._state = "grid"
        self._pending_camera: str | None = None
        self._active_camera: str | None = None
        self._detail_started: float | None = None
        self._requested_at: float | None = None
        self._blind_started: float | None = None
        self._grid_available_at = float("-inf")
        self._blind_history: dict[str, list[tuple[float, float]]] = {camera: [] for camera in self._cameras}

    def _disable(self, reason: str) -> None:
        self._automation_enabled = False
        self._faulted = True
        self._reason = reason
        self._state = "disabled"
        self._pending_camera = None
        self._requested_at = None

    def _verification_window(self) -> float:
        return min(float(self._config["switching"]["detail_seconds"]), float(self._config["switching"]["blind_budget_seconds"]))

    def _verification_expired(self, now: float) -> bool:
        if self._requested_at is None:
            return False
        # The frozen config has no verification timeout field.  The bounded
        # detail dwell is the strictest V1 upper bound for an unverified view.
        return now - self._requested_at > self._verification_window()

    def enqueue(self, camera_id: str, kind: str, now: float) -> None:
        if camera_id not in self._cameras:
            raise ValueError(f"camera is not enabled/configured: {camera_id}")
        if kind not in {"material_candidate", "station_absence"}:
            raise ValueError("unsupported switching event kind")
        if not isinstance(now, (int, float)):
            raise ValueError("now must be seconds")
        if any(entry[2] == camera_id and entry[3] == kind for entry in self._queue):
            return
        priority = 0 if kind == "material_candidate" else 1
        self._sequence += 1
        self._queue.append((priority, self._sequence, camera_id, kind, float(now)))

    def _rolling_blind(self, camera_id: str, now: float) -> float:
        cutoff = now - 3600.0
        self._blind_history[camera_id] = [(max(start, cutoff), end) for start, end in self._blind_history[camera_id] if end > cutoff]
        return sum(end - start for start, end in self._blind_history[camera_id])

    def _budget_allows_detail(self, now: float) -> bool:
        # Detail dwell plus bounded detail/grid identity confirmation windows
        # is reserved before moving away from a verified grid.
        reserve = float(self._config["switching"]["detail_seconds"]) + 2 * self._verification_window()
        budget = float(self._config["switching"]["blind_budget_seconds"])
        return all(self._rolling_blind(camera, now) + reserve <= budget for camera in self._cameras)

    def next_action(self, now: float) -> dict | None:
        now = float(now)
        if not self._automation_enabled:
            return None
        if self._state == "awaiting_detail" or self._state == "awaiting_grid":
            if self._verification_expired(now):
                self._disable("adapter verification timed out")
            return None
        if self._state == "detail":
            if self._detail_started is None:
                self._disable("missing detail request timestamp")
                return None
            if now < self._detail_started + float(self._config["switching"]["detail_seconds"]):
                return None
            self._state = "awaiting_grid"
            self._requested_at = now
            return {"action": "grid"}
        if self._state != "grid" or now < self._grid_available_at or not self._queue:
            return None
        if not self._budget_allows_detail(now):
            self._disable("blindness budget would be exceeded")
            return None
        ordered = sorted(self._queue, key=lambda entry: (entry[0], entry[1]))
        selected = ordered[0]
        self._queue.remove(selected)
        self._pending_camera = selected[2]
        self._state = "awaiting_detail"
        self._requested_at = now
        return {"action": "detail", "camera_id": selected[2]}

    def confirm(self, camera_id_or_none: str | None, now: float, verified: bool) -> None:
        now = float(now)
        if not self._automation_enabled:
            return
        if self._verification_expired(now):
            self._disable("adapter verification timed out")
            return
        if not verified:
            self._disable("adapter could not verify view identity/layout")
            return
        if self._state == "awaiting_detail":
            if camera_id_or_none != self._pending_camera:
                self._disable("adapter verified an unexpected detail camera")
                return
            self._active_camera = self._pending_camera
            self._pending_camera = None
            if self._requested_at is None:
                self._disable("missing detail request timestamp")
                return
            self._detail_started = self._requested_at
            self._blind_started = self._requested_at
            self._state = "detail"
            return
        if self._state == "awaiting_grid":
            if camera_id_or_none is not None or self._active_camera is None or self._blind_started is None:
                self._disable("adapter could not verify return to grid")
                return
            for camera in self._cameras:
                if camera != self._active_camera:
                    self._blind_history[camera].append((self._blind_started, now))
            self._active_camera = None
            self._detail_started = None
            self._blind_started = None
            self._requested_at = None
            self._state = "grid"
            self._grid_available_at = now + float(self._config["switching"]["grid_seconds"])
            return
        self._disable("unexpected view verification state")

    def health(self, camera_id: str, now: float) -> str:
        if camera_id not in self._cameras:
            raise ValueError(f"camera is not enabled/configured: {camera_id}")
        if self._faulted:
            return "unknown"
        if self._state in {"awaiting_detail", "awaiting_grid"}:
            return "unknown"
        if self._state == "grid":
            return "observable"
        if camera_id == self._active_camera:
            return "observable"
        if self._blind_started is None:
            return "unknown"
        started = self._blind_started
        projected = self._rolling_blind(camera_id, float(now)) + max(0.0, float(now) - started)
        return "blind" if projected <= float(self._config["switching"]["blind_budget_seconds"]) else "unknown"

    def snapshot(self, now: float) -> dict:
        return {
            "automation_enabled": self._automation_enabled,
            "reason": self._reason,
            "state": self._state,
            "active_camera": self._active_camera,
            "pending_camera": self._pending_camera,
            "queued": [{"camera_id": item[2], "kind": item[3], "enqueued_at": item[4]} for item in sorted(self._queue)],
            "coverage": {camera: self.health(camera, now) for camera in sorted(self._cameras)},
            "blind_seconds_rolling_hour": {camera: self._rolling_blind(camera, float(now)) for camera in sorted(self._cameras)},
        }
