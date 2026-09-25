"""Synthetic, person-free images for the isolated scene-change component."""

from __future__ import annotations

import numpy as np
import pytest

from factory_monitor.scene_watch import SceneChangeWatch


def _scene(*, object_present: bool = True, brightness: int = 100) -> np.ndarray:
    image = np.full((80, 120, 3), brightness, dtype=np.uint8)
    if object_present:
        image[25:55, 40:70] = min(255, brightness + 100)
    return image


def test_person_free_object_removal_emits_once_and_never_learns_it_away():
    watch = SceneChangeWatch(warmup_seconds=1, hold_seconds=1)
    occupied, empty = _scene(), _scene(object_present=False)

    assert watch.observe(occupied, 0)["candidate"] is False
    assert watch.observe(occupied, 1)["state"] == "stable"
    assert watch.observe(empty, 2)["state"] == "pending"
    alert = watch.observe(empty, 3.1)
    assert alert["state"] == "changed"
    assert alert["candidate"] is True
    assert alert["changed_ratio"] > 0.02
    for timestamp in (4, 5, 6, 7, 8):
        persistent = watch.observe(empty, timestamp)
        assert persistent["state"] == "changed"
        assert persistent["candidate"] is False
        assert persistent["changed_ratio"] > 0.02


def test_person_free_object_addition_is_detected_after_hold():
    watch = SceneChangeWatch(warmup_seconds=1, hold_seconds=0.5)
    empty, occupied = _scene(object_present=False), _scene()

    watch.observe(empty, 0)
    watch.observe(empty, 1)
    assert watch.observe(occupied, 2)["candidate"] is False
    assert watch.observe(occupied, 2.6)["candidate"] is True


def test_warmup_motion_restarts_stability_window_before_reference_is_fixed():
    watch = SceneChangeWatch(warmup_seconds=1, hold_seconds=0)
    occupied, empty = _scene(), _scene(object_present=False)

    assert watch.observe(occupied, 0)["state"] == "warming"
    assert watch.observe(empty, 0.5)["state"] == "warming"
    assert watch.observe(empty, 1.0)["state"] == "warming"
    assert watch.observe(empty, 1.5)["state"] == "stable"
    changed = watch.observe(occupied, 2)
    assert changed["candidate"] is True
    assert changed["state"] == "changed"


def test_clock_overlay_exclusion_and_global_brightness_do_not_alarm():
    watch = SceneChangeWatch(exclude_rois=((0, 0, 0.25, 0.2),), warmup_seconds=1, hold_seconds=0)
    base = _scene()
    clock_changed = base.copy()
    clock_changed[0:16, 0:30] = 255

    watch.observe(base, 0)
    watch.observe(base, 1)
    excluded = watch.observe(clock_changed, 2)
    assert excluded["state"] == "stable"
    assert excluded["candidate"] is False
    assert excluded["changed_ratio"] == 0
    brightened = watch.observe(np.clip(clock_changed.astype(np.int16) + 35, 0, 255).astype(np.uint8), 3)
    assert brightened["state"] == "stable"
    assert brightened["candidate"] is False
    assert brightened["changed_ratio"] == 0


def test_global_scene_replacement_is_unreliable_until_explicit_reset():
    watch = SceneChangeWatch(warmup_seconds=1, hold_seconds=0, max_changed_ratio=0.6)
    blocks = np.indices((80, 120)).sum(axis=0) % 2
    baseline = np.repeat(np.where(blocks, 40, 210)[:, :, None].astype(np.uint8), 3, axis=2)
    replacement = np.repeat(np.where(blocks, 210, 40)[:, :, None].astype(np.uint8), 3, axis=2)

    watch.observe(baseline, 0)
    watch.observe(baseline, 1)
    invalid = watch.observe(replacement, 2)
    assert invalid["state"] == "unreliable"
    assert invalid["candidate"] is False
    assert invalid["changed_ratio"] > 0.6
    assert watch.observe(baseline, 3)["state"] == "unreliable"
    watch.reset()
    assert watch.observe(baseline, 4)["state"] == "warming"


@pytest.mark.parametrize(
    ("timestamp", "view_epoch", "reason"),
    [(5.0, 1, "view_epoch_changed"), (8.0, 0, "long_gap"), (2.5, 0, "clock_reversed")],
)
def test_epoch_gap_or_clock_reversal_requires_new_static_reference(timestamp, view_epoch, reason):
    watch = SceneChangeWatch(warmup_seconds=1, hold_seconds=0, max_gap_seconds=2)
    image = _scene()
    watch.observe(image, 2, view_epoch=0)
    watch.observe(image, 3, view_epoch=0)

    result = watch.observe(image, timestamp, view_epoch=view_epoch)

    assert result == {"state": "warming", "candidate": False, "changed_ratio": 0.0, "reason": reason}


@pytest.mark.parametrize("roi", [(-0.1, 0, 0.5, 0.5), (0, 0, 0, 1), (0.8, 0, 0.3, 1), (float("nan"), 0, 1, 1)])
def test_invalid_normalized_roi_is_rejected(roi):
    with pytest.raises(ValueError, match="roi"):
        SceneChangeWatch(roi=roi)


def test_invalid_exclusion_and_empty_usable_roi_are_rejected():
    with pytest.raises(ValueError, match="exclude"):
        SceneChangeWatch(exclude_rois=((float("nan"), 0, 0.2, 0.2),))
    watch = SceneChangeWatch(exclude_rois=((0, 0, 1, 1),))
    with pytest.raises(ValueError, match="unexcluded"):
        watch.observe(_scene(), 0)


@pytest.mark.parametrize("image", [np.empty((0, 10, 3), dtype=np.uint8), np.zeros((10, 10, 3), dtype=np.float32), np.array([[[np.nan]]]), "not an image"])
def test_invalid_image_is_rejected(image):
    with pytest.raises((TypeError, ValueError), match="image"):
        SceneChangeWatch().observe(image, 0)


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1, "1"])
def test_invalid_timestamp_is_rejected(timestamp):
    with pytest.raises((TypeError, ValueError), match="timestamp"):
        SceneChangeWatch().observe(_scene(), timestamp)


def test_brightness_clipping_is_unreliable_instead_of_an_object_candidate():
    watch = SceneChangeWatch(hold_seconds=0)
    baseline = np.full((100, 100), 100, dtype=np.uint8)
    baseline[20:50, 20:50] = 200
    clipped = np.clip(baseline.astype(np.int16) + 100, 0, 255).astype(np.uint8)
    watch.observe(baseline, 0)
    watch.observe(baseline, 1)
    result = watch.observe(clipped, 2)
    assert result['state'] == 'unreliable'
    assert result['candidate'] is False


def test_huge_timestamp_fails_validation_without_overflow():
    with pytest.raises(ValueError):
        SceneChangeWatch().observe(_scene(), 10**400)
