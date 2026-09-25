"""Optimistic pixel and scheduling budgets; never an image-quality certificate."""
from __future__ import annotations

import math


def _pair(value, name):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) is not int or not 1 <= v <= 1000000 for v in value)):
        raise ValueError(f"{name} must contain two positive integers")
    return value


def _positive(value, name):
    if type(value) not in (int, float) or not 1e-6 <= value <= 1e6 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and between 1e-6 and 1e6")
    return float(value)


def evaluate_view_budget(camera_count, display_size, grid_shape, *,
                         preview_source_size=None, detail_source_size=None,
                         object_width_fraction=None, open_seconds=1, settle_seconds=1,
                         detail_seconds=3, return_seconds=1, grid_seconds=1,
                         blind_budget_seconds=180, persistent_overview=False):
    """Return an idealized no-borders, equal-aspect-ratio planning calculation.

    ``grid_shape`` is (columns, rows). Effective pixels are optimistic per-axis
    ceilings, not verified decoded detail. Revisit assumes every visit succeeds.
    Budget-limited sweep is a long-run mean lower bound, not a worst-case SLA.
    """
    if type(camera_count) is not int or not 1 <= camera_count <= 16:
        raise ValueError("camera_count must be an integer in 1..16")
    width, height = _pair(display_size, "display_size")
    columns, rows = _pair(grid_shape, "grid_shape")
    if columns * rows < camera_count or columns > width or rows > height:
        raise ValueError("grid must fit the cameras and display")
    if type(persistent_overview) is not bool:
        raise ValueError("persistent_overview must be bool")
    times = [_positive(v, n) for v, n in [
        (open_seconds, "open_seconds"), (settle_seconds, "settle_seconds"),
        (detail_seconds, "detail_seconds"), (return_seconds, "return_seconds"),
        (grid_seconds, "grid_seconds")]]
    budget = _positive(blind_budget_seconds, "blind_budget_seconds")
    if object_width_fraction is not None:
        object_width_fraction = _positive(object_width_fraction, "object_width_fraction")
        if object_width_fraction > 1:
            raise ValueError("object_width_fraction must be <= 1")
    tile = [width // columns, height // rows]
    grid = None if preview_source_size is None else [
        min(a, b) for a, b in zip(tile, _pair(preview_source_size, "preview_source_size"))]
    detail = None if detail_source_size is None else [
        min(a, b) for a, b in zip((width, height), _pair(detail_source_size, "detail_source_size"))]
    away, slot = sum(times[:4]), sum(times)
    nominal = camera_count * slot
    unavailable = 0 if persistent_overview else 3600 * away / slot
    constrained = nominal if persistent_overview else max(nominal, camera_count * 3600 * away / budget)
    return {
        "camera_count": camera_count, "tile_pixels": tile,
        "effective_grid_pixels": grid, "effective_detail_pixels": detail,
        "object_grid_width_pixels": None if grid is None or object_width_fraction is None
        else grid[0] * object_width_fraction,
        "object_detail_width_pixels": None if detail is None or object_width_fraction is None
        else detail[0] * object_width_fraction,
        "detail_information_gain": None if grid is None or detail is None else detail[0] / grid[0],
        "detail_information_gain_unit": "horizontal_pixel_ceiling_ratio",
        "nominal_revisit_seconds": nominal,
        "overview_unavailable_seconds_per_hour": unavailable,
        "budget_limited_mean_sweep_seconds": constrained,
        "blind_budget_gate": "FAIL" if unavailable > budget else "PASS_ASSUMPTIONS_ONLY",
        "quality_gate": "REQUIRES_TASK_CALIBRATION", "field_gate": "NOT_TESTED",
        "persistent_overview_requires_verification": persistent_overview,
        "assumptions": ["no client borders or overlays", "equal aspect ratio",
                        "all transitions succeed", "provided source sizes are unverified",
                        "mean sweep is not a maximum event detection latency"],
    }
