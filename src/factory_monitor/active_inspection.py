"""Pure inspection suggestions for replay experiments, never desktop actions.

The caller supplies monotonic capture times and independent identity checks.
Those inputs are assertions, not native-control verification by this module.
"""
from __future__ import annotations

import math
import re
from uuid import uuid4


def _number(value, name, *, positive=False):
    if type(value) not in (int, float) or not 0 <= value <= 1e12 or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite nonnegative number")
    if positive and not 1e-6 <= value <= 1e6:
        raise ValueError(f"{name} must be between 1e-6 and 1e6 seconds")
    return float(value)


def _inventory(ids):
    if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= 16:
        raise ValueError("camera_ids must contain 1..16 unique IDs")
    if any(type(x) is not str or not x.strip() for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("camera_ids must contain unique nonempty strings")
    return tuple(ids)


class InspectionPlanner:
    """Single-detail state machine, fair periodic coverage and bounded hints.

    Blind time means *overview unavailable*, including transition uncertainty.
    A fault stops suggestions; an explicit fresh overview receipt is required
    to rearm. Rearming never clears the rolling-hour blindness accounting.
    """

    def __init__(self, camera_ids, fingerprint, *, inspection_interval=60,
                 detail_seconds=3, transition_timeout=2, grid_hold_seconds=1,
                 blind_budget_seconds=180, scheduling_slack_seconds=.25):
        self.camera_ids = _inventory(camera_ids)
        if type(fingerprint) is not str or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
            raise ValueError("fingerprint must be 64 hexadecimal characters")
        self.fingerprint = fingerprint
        self.interval = _number(inspection_interval, "inspection_interval", positive=True)
        self.dwell = _number(detail_seconds, "detail_seconds", positive=True)
        self.timeout = _number(transition_timeout, "transition_timeout", positive=True)
        self.hold = _number(grid_hold_seconds, "grid_hold_seconds")
        self.budget = _number(blind_budget_seconds, "blind_budget_seconds", positive=True)
        self.slack = _number(scheduling_slack_seconds, "scheduling_slack_seconds", positive=True)
        self.state = "unverified"
        self.active_camera = None
        self._pending = None
        self._hints = {}
        self._due = {}
        self._intervals = []
        self._blind_start = None
        self._last_time = None
        self._fault_at = None
        self._hold_until = 0.0
        self._detail_until = 0.0
        self._expired = 0
        self._blocked = None

    def _time(self, now):
        try:
            now = _number(now, "now")
        except ValueError:
            self._fault(self._last_time or 0, "invalid_clock")
            raise
        if self._last_time is not None and now < self._last_time:
            self._fault(self._last_time, "clock_reversed")
            raise ValueError("monotonic time moved backwards")
        self._last_time = now
        return now

    def _fault(self, now, reason):
        self.state, self.active_camera = "fault", None
        self._pending = None
        self._fault_at = now
        self._blocked = reason

    def _usage(self, now):
        cutoff = now - 3600
        self._intervals = [(a, b) for a, b in self._intervals if b > cutoff]
        used = sum(b - max(a, cutoff) for a, b in self._intervals)
        if self._blind_start is not None:
            used += now - max(self._blind_start, cutoff)
        return used

    def arm_overview(self, *, now, frame_at, fingerprint, camera_ids):
        now = self._time(now)
        frame_at = _number(frame_at, "frame_at")
        if self.state not in {"unverified", "fault"}:
            raise ValueError("rearm is only allowed from unverified/fault")
        if (fingerprint != self.fingerprint or set(_inventory(camera_ids)) != set(self.camera_ids)
                or frame_at > now or now - frame_at > self.timeout
                or (self._fault_at is not None and frame_at <= self._fault_at)):
            raise ValueError("overview receipt must be complete, fresh and match the profile")
        if self._blind_start is not None:
            self._intervals.append((self._blind_start, now))
            self._blind_start = None
        self.state, self.active_camera = "grid", None
        self._pending, self._blocked = None, None
        self._hold_until = now + self.hold
        self._due = {camera: self._due.get(camera, now) for camera in self.camera_ids}

    def submit(self, camera_id, reason, *, now, ttl):
        now = self._time(now)
        ttl = _number(ttl, "ttl", positive=True)
        if type(camera_id) is not str or camera_id not in self.camera_ids:
            raise ValueError("unknown camera")
        if reason not in ("scene_change", "manual", "low_quality", "periodic"):
            raise ValueError("unknown inspection reason")
        self._expire(now)
        # A flood cannot renew one hint forever or create an unbounded queue.
        if camera_id not in self._hints:
            self._hints[camera_id] = {"reason": reason, "at": now, "expires": now + ttl}

    def _expire(self, now):
        expired = [c for c, hint in self._hints.items() if hint["expires"] <= now]
        for camera in expired:
            del self._hints[camera]
        self._expired += len(expired)

    def _request(self, action, camera, now):
        self._pending = {"action": action, "camera_id": camera,
                         "request_id": uuid4().hex, "issued_at": now,
                         "fingerprint": self.fingerprint}
        self.state = "awaiting_" + action
        self.active_camera = None
        return dict(self._pending)

    def tick(self, now):
        now = self._time(now)
        self._expire(now)
        if self._usage(now) > self.budget:
            self._fault(now, "overview_blind_budget_exceeded")
            return None
        if self._pending is not None:
            if now - self._pending["issued_at"] > self.timeout:
                self._fault(now, "transition_timeout")
            return None
        if self.state == "detail":
            if now > self._detail_until + self.slack:
                self._fault(now, "detail_scheduler_late")
                return None
            if now + 1e-9 >= self._detail_until:
                return self._request("grid", None, now)
            return None
        if self.state != "grid" or now < self._hold_until:
            return None
        overdue = [c for c in self.camera_ids if self._due[c] <= now]
        if overdue:
            camera = min(overdue, key=lambda c: self._due[c])
        elif self._hints:
            camera = min(self._hints, key=lambda c: self._hints[c]["at"])
        else:
            self._blocked = None
            return None
        reservation = self.dwell + 2 * self.timeout + self.slack
        if self._usage(now) + reservation > self.budget:
            self._blocked = "overview_blind_budget"
            return None
        self._blocked = None
        self._blind_start = now
        return self._request("detail", camera, now)

    def confirm(self, request_id, *, now, frame_at, view, camera_id,
                fingerprint, target_verified):
        try:
            now = self._time(now)
            frame_at = _number(frame_at, "frame_at")
        except ValueError:
            self._fault(self._last_time or 0, "invalid_ack_time")
            return False
        expected = self._pending
        if (expected is None or request_id != expected["request_id"]
                or view != expected["action"] or camera_id != expected["camera_id"]
                or fingerprint != self.fingerprint or target_verified is not True
                or not expected["issued_at"] < frame_at <= now
                or now - expected["issued_at"] > self.timeout):
            self._fault(now, "invalid_ack")
            return False
        self._pending = None
        self.state = view
        if view == "detail":
            self.active_camera = camera_id
            self._detail_until = now + self.dwell
            self._due[camera_id] = now + self.interval
            self._hints.pop(camera_id, None)
        else:
            self.active_camera = None
            self._intervals.append((self._blind_start, now))
            self._blind_start = None
            self._hold_until = now + self.hold
        if self._usage(now) > self.budget:
            self._fault(now, "overview_blind_budget_exceeded")
            return False
        return True

    def snapshot(self, now):
        now = self._time(now)
        self._expire(now)
        usage = self._usage(now)
        return {"state": self.state, "active_camera": self.active_camera,
                "pending_count": len(self._hints), "expired_hints": self._expired,
                "blocked_reason": self._blocked,
                "overdue_cameras": [c for c in self.camera_ids if self._due.get(c, now) <= now],
                "overview_unavailable_seconds": usage,
                "budget_gate": "FAIL" if usage > self.budget else "WITHIN_OBSERVED_BUDGET",
                "automatic_control_authorized": False}
