"""Deterministic candidate rules over normalized camera observations."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from .config import validate_config


def _inside(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """Boundary-inclusive ray casting for a normalized polygon."""
    x, y = point
    inside = False
    for index in range(len(polygon)):
        x1, y1 = polygon[index - 1]
        x2, y2 = polygon[index]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-9 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y):
            intersect = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersect:
                inside = not inside
    return inside


def _line_side(point: tuple[float, float], line: list[list[float]]) -> float:
    (x1, y1), (x2, y2) = line
    return (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _segments_intersect(first_start: tuple[float, float], first_end: tuple[float, float], second_start: tuple[float, float], second_end: tuple[float, float]) -> bool:
    """Return whether two finite segments meet, including a shared endpoint."""
    first_delta = (first_end[0] - first_start[0], first_end[1] - first_start[1])
    second_delta = (second_end[0] - second_start[0], second_end[1] - second_start[1])
    offset = (second_start[0] - first_start[0], second_start[1] - first_start[1])
    denominator = _cross(first_delta, second_delta)
    if abs(denominator) < 1e-9:
        return False
    first_fraction = _cross(offset, second_delta) / denominator
    second_fraction = _cross(offset, first_delta) / denominator
    return 0 <= first_fraction <= 1 and 0 <= second_fraction <= 1


def _minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


class RuleEngine:
    """Emits review candidates only when the observation stream is continuous."""

    def __init__(self, config: dict) -> None:
        validate_config(config)
        self._config = config
        self._cameras = {camera["id"]: camera for camera in config["cameras"]}
        # A five second source gap is deliberately conservative at the V1 default FPS.
        self._max_gap = max(2.0, 5.0 / float(config["detection"]["fps"]))
        # ByteTrack IDs may be temporarily absent, but a departed ID cannot
        # retain de-duplication state indefinitely under long-running churn.
        self._track_ttl_seconds = 60.0
        self._state: dict[str, dict[str, Any]] = {identifier: self._new_state() for identifier in self._cameras}

    @staticmethod
    def _new_state() -> dict[str, Any]:
        return {"last_timestamp": None, "tracks": {}, "last_seen": {}, "emitted": set(), "absence_started": None, "absence_emitted": False}

    def _expire_departed_tracks(self, state: dict[str, Any], timestamp: float) -> None:
        expired = {track_id for track_id, seen_at in state["last_seen"].items() if timestamp - seen_at >= self._track_ttl_seconds}
        if not expired:
            return
        for track_id in expired:
            del state["last_seen"][track_id]
        state["emitted"] = {token for token in state["emitted"] if token[0] not in expired}

    def reset(self, camera_id: str | None = None) -> None:
        if camera_id is None:
            self._state = {identifier: self._new_state() for identifier in self._cameras}
            return
        if camera_id not in self._cameras:
            raise ValueError(f"unknown camera: {camera_id}")
        self._state[camera_id] = self._new_state()

    def _active(self, camera: dict, timestamp: float) -> bool:
        instant = datetime.fromtimestamp(timestamp).astimezone()
        schedule = camera["schedule"]
        if instant.weekday() not in schedule["days"]:
            return False
        minute = instant.hour * 60 + instant.minute
        active = any(_minutes(start) <= minute < _minutes(end) for start, end in schedule["active"])
        on_break = any(_minutes(start) <= minute < _minutes(end) for start, end in schedule["breaks"])
        return active and not on_break

    @staticmethod
    def _people(value: object) -> list[dict]:
        if not isinstance(value, list):
            raise ValueError("observation.people must be a list")
        result: list[dict] = []
        seen: set[int] = set()
        for person in value:
            if not isinstance(person, dict) or not isinstance(person.get("track_id"), int) or isinstance(person["track_id"], bool):
                raise ValueError("each person requires an integer track_id")
            track_id = person["track_id"]
            if track_id in seen:
                raise ValueError("observation cannot contain duplicate track_ids")
            seen.add(track_id)
            bbox = person.get("bbox")
            confidence = person.get("confidence")
            if not isinstance(bbox, list) or len(bbox) != 4 or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("each person requires bbox and confidence")
            x1, y1, x2, y2 = (float(item) for item in bbox)
            if not (0 <= x1 <= x2 <= 1 and 0 <= y1 <= y2 <= 1 and 0 <= float(confidence) <= 1):
                raise ValueError("person bbox/confidence must be normalized")
            result.append({"track_id": track_id, "foot": ((x1 + x2) / 2, y2)})
        return result

    @staticmethod
    def _event(camera_id: str, kind: str, timestamp: float, reason: str, layout_version: int) -> dict:
        return {"id": str(uuid.uuid4()), "camera_id": camera_id, "kind": kind, "triggered_at": timestamp, "status": "candidate", "reason": reason, "layout_version": layout_version}

    def observe(self, observation: dict) -> list[dict]:
        if not isinstance(observation, dict):
            raise ValueError("observation must be an object")
        camera_id = observation.get("camera_id")
        if camera_id not in self._cameras:
            raise ValueError(f"unknown camera: {camera_id}")
        timestamp = observation.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("observation.timestamp must be epoch seconds")
        timestamp = float(timestamp)
        if timestamp < 0:
            raise ValueError("observation.timestamp must be non-negative")
        camera = self._cameras[camera_id]
        state = self._state[camera_id]
        health = observation.get("health")
        layout_version = observation.get("layout_version")
        if health not in {"observable", "blind", "frozen", "mapping_invalid", "unavailable"} or not isinstance(layout_version, int):
            raise ValueError("observation health or layout_version is invalid")
        if health != "observable" or layout_version != self._config["source"]["layout_version"]:
            self.reset(camera_id)
            return []
        previous = state["last_timestamp"]
        if previous is not None and (timestamp <= previous or timestamp - previous > self._max_gap):
            self.reset(camera_id)
            state = self._state[camera_id]
        people = self._people(observation.get("people"))
        state["last_timestamp"] = timestamp
        self._expire_departed_tracks(state, timestamp)
        events: list[dict] = []
        current_tracks: dict[int, tuple[float, float]] = {}
        for person in people:
            track_id, foot = person["track_id"], person["foot"]
            old_foot = state["tracks"].get(track_id)
            current_tracks[track_id] = foot
            state["last_seen"][track_id] = timestamp
            if camera["material_roi"] and old_foot is not None and not _inside(old_foot, camera["material_roi"]) and _inside(foot, camera["material_roi"]):
                token = (track_id, "material_roi")
                if token not in state["emitted"]:
                    state["emitted"].add(token)
                    events.append(self._event(camera_id, "material_candidate", timestamp, "entered_material_roi", layout_version))
            if camera["exit_line"] and old_foot is not None:
                old_side, new_side = _line_side(old_foot, camera["exit_line"]), _line_side(foot, camera["exit_line"])
                if old_side * new_side < 0 and _segments_intersect(old_foot, foot, tuple(camera["exit_line"][0]), tuple(camera["exit_line"][1])):
                    token = (track_id, "exit_line")
                    if token not in state["emitted"]:
                        state["emitted"].add(token)
                        events.append(self._event(camera_id, "material_candidate", timestamp, "crossed_exit_line", layout_version))
        state["tracks"] = current_tracks

        occupied = bool(camera["station_roi"]) and any(_inside(person["foot"], camera["station_roi"]) for person in people)
        if not camera["station_roi"] or not self._active(camera, timestamp) or occupied:
            state["absence_started"] = None
            state["absence_emitted"] = False
            return events
        if state["absence_started"] is None:
            state["absence_started"] = timestamp
        elif not state["absence_emitted"] and timestamp - state["absence_started"] >= float(camera["absence_seconds"]):
            state["absence_emitted"] = True
            events.append(self._event(camera_id, "station_absence", timestamp, "station_unoccupied_for_threshold", layout_version))
        return events
