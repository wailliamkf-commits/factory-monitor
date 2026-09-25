"""Person-independent visual change hints for one calibrated camera view.

This component compares pixels, not object types or human actions. Callers
must keep one instance per camera and feed frames from a verified view.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np


def _rectangle(value: Sequence[float], name: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError(f"{name} must be normalized x,y,width,height")
    if any(isinstance(part, bool) or not isinstance(part, (int, float)) or not math.isfinite(part) for part in value):
        raise ValueError(f"{name} must contain finite numeric coordinates")
    x, y, width, height = (float(part) for part in value)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1 and x + width <= 1 and y + height <= 1):
        raise ValueError(f"{name} must fit inside the normalized image")
    return x, y, width, height


def _nonnegative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1e12 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def _output(state: str, candidate: bool, changed_ratio: float, reason: str) -> dict[str, Any]:
    return {"state": state, "candidate": candidate, "changed_ratio": float(changed_ratio), "reason": reason}


class SceneChangeWatch:
    """Hold a fixed visual reference until explicit reset or view discontinuity."""

    def __init__(
        self,
        roi: Sequence[float] = (0, 0, 1, 1),
        exclude_rois: Sequence[Sequence[float]] = (),
        warmup_seconds: float = 1,
        hold_seconds: float = 1,
        pixel_delta: float = 25,
        min_changed_ratio: float = 0.02,
        max_changed_ratio: float = 0.6,
        max_gap_seconds: float = 2,
    ) -> None:
        self.roi = _rectangle(roi, "roi")
        if not isinstance(exclude_rois, (tuple, list)):
            raise ValueError("exclude_rois must be a list of normalized rectangles")
        self.exclude_rois = tuple(_rectangle(item, "exclude_roi") for item in exclude_rois)
        self.warmup_seconds = _nonnegative(warmup_seconds, "warmup_seconds")
        self.hold_seconds = _nonnegative(hold_seconds, "hold_seconds")
        self.pixel_delta = _nonnegative(pixel_delta, "pixel_delta")
        self.max_gap_seconds = _nonnegative(max_gap_seconds, "max_gap_seconds")
        minimum = _nonnegative(min_changed_ratio, "min_changed_ratio")
        maximum = _nonnegative(max_changed_ratio, "max_changed_ratio")
        if not (0 < minimum < maximum <= 1) or self.pixel_delta > 255 or self.max_gap_seconds == 0:
            raise ValueError("change thresholds and max_gap_seconds are invalid")
        self.min_changed_ratio = minimum
        self.max_changed_ratio = maximum
        self.reset()

    def reset(self) -> None:
        """Clear the reference; the next frame starts a new static warmup."""
        self._anchor: np.ndarray | None = None
        self._reference: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._image_shape: tuple[int, ...] | None = None
        self._warmup_started: float | None = None
        self._pending_started: float | None = None
        self._emitted = False
        self._unreliable = False
        self._last_timestamp: float | None = None
        self._view_epoch: int | None = None

    def _prepare(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
            raise ValueError("image must be a nonempty uint8 array")
        if image.ndim == 2:
            gray = image
        elif image.ndim == 3 and image.shape[2] in {1, 3, 4}:
            channels = image.shape[2]
            gray = image[:, :, 0] if channels == 1 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY if channels == 3 else cv2.COLOR_BGRA2GRAY)
        else:
            raise ValueError("image must be grayscale, BGR, or BGRA")
        height, width = gray.shape
        x, y, roi_width, roi_height = self.roi
        left, top = round(x * width), round(y * height)
        right, bottom = round((x + roi_width) * width), round((y + roi_height) * height)
        if right <= left or bottom <= top:
            raise ValueError("roi is empty at this image resolution")
        mask = np.ones((bottom - top, right - left), dtype=bool)
        for ex, ey, ew, eh in self.exclude_rois:
            ex_left = max(left, round(ex * width)) - left
            ex_top = max(top, round(ey * height)) - top
            ex_right = min(right, round((ex + ew) * width)) - left
            ex_bottom = min(bottom, round((ey + eh) * height)) - top
            if ex_right > ex_left and ex_bottom > ex_top:
                mask[ex_top:ex_bottom, ex_left:ex_right] = False
        if not mask.any():
            raise ValueError("roi has no unexcluded pixels")
        return gray[top:bottom, left:right].copy(), mask

    def _difference(self, reference: np.ndarray, current: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
        delta = current.astype(np.int16) - reference.astype(np.int16)
        # Measure a global lighting offset only over the unexcluded ROI.
        illumination = float(np.median(delta[mask]))
        ratio = float(np.count_nonzero((np.abs(delta - illumination) > self.pixel_delta) & mask) / np.count_nonzero(mask))
        return ratio, illumination

    def observe(self, image: np.ndarray, timestamp: float, view_epoch: int = 0) -> dict[str, Any]:
        """Return state, one-shot candidate, changed fraction, and reason."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not 0 <= timestamp <= 1e12 or not math.isfinite(timestamp):
            raise ValueError("timestamp must be a finite nonnegative monotonic second value")
        if isinstance(view_epoch, bool) or not isinstance(view_epoch, int) or view_epoch < 0:
            raise ValueError("view_epoch must be a nonnegative integer")
        current, mask = self._prepare(image)
        now = float(timestamp)
        reset_reason = "initial_frame"
        if self._last_timestamp is not None:
            if view_epoch != self._view_epoch:
                reset_reason = "view_epoch_changed"
            elif now < self._last_timestamp:
                reset_reason = "clock_reversed"
            elif now - self._last_timestamp > self.max_gap_seconds:
                reset_reason = "long_gap"
            elif image.shape != self._image_shape:
                reset_reason = "image_shape_changed"
            else:
                reset_reason = ""
        if reset_reason:
            self.reset()
            self._anchor = current
            self._mask = mask
            self._image_shape = image.shape
            self._warmup_started = now
            self._last_timestamp = now
            self._view_epoch = view_epoch
            return _output("warming", False, 0, reset_reason)

        self._last_timestamp = now
        if self._unreliable:
            return _output("unreliable", False, 0, "global_scene_change_requires_reset")
        assert self._anchor is not None and self._mask is not None and self._warmup_started is not None
        if self._reference is None:
            ratio, _ = self._difference(self._anchor, current, self._mask)
            if ratio >= self.min_changed_ratio:
                self._anchor = current
                self._warmup_started = now
                return _output("warming", False, ratio, "warmup_not_static")
            if now - self._warmup_started < self.warmup_seconds:
                return _output("warming", False, ratio, "warmup")
            self._reference = self._anchor.copy()
            return _output("stable", False, 0, "reference_ready")

        ratio, illumination = self._difference(self._reference, current, self._mask)
        new_clipping = ((current == 0) | (current == 255)) & (current != self._reference) & self._mask
        clipped_ratio = float(np.count_nonzero(new_clipping) / np.count_nonzero(self._mask))
        if abs(illumination) > self.pixel_delta and clipped_ratio >= self.min_changed_ratio:
            self._unreliable = True
            self._pending_started = None
            return _output("unreliable", False, ratio, "illumination_clipping_requires_reset")
        if ratio > self.max_changed_ratio:
            self._unreliable = True
            self._pending_started = None
            return _output("unreliable", False, ratio, "global_scene_change")
        if ratio < self.min_changed_ratio:
            self._pending_started = None
            self._emitted = False
            reason = "illumination_shift" if abs(illumination) > self.pixel_delta else "no_change"
            return _output("stable", False, ratio, reason)
        if self._pending_started is None:
            self._pending_started = now
        if now - self._pending_started < self.hold_seconds:
            return _output("pending", False, ratio, "change_not_yet_sustained")
        if self._emitted:
            return _output("changed", False, ratio, "fixed_reference_change_persists")
        self._emitted = True
        return _output("changed", True, ratio, "sustained_visual_change")
