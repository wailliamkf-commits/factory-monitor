"""Fixture and summary checks that do not load either detector model."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_detector.py"
SPEC = importlib.util.spec_from_file_location("benchmark_detector", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_ten_crops_are_distinct_and_have_fixed_shape():
    image = np.arange(1080 * 810 * 3, dtype=np.uint32).reshape(1080, 810, 3).astype(np.uint8)

    views = benchmark.make_views(image)

    assert list(views) == [f"CAM{index:02d}" for index in range(1, 11)]
    assert {view.shape for view in views.values()} == {(240, 320, 3)}
    assert len({view.tobytes() for view in views.values()}) == 10


def test_percentile_uses_same_interpolation_for_both_workers():
    values = [float(value) for value in range(1, 11)]
    assert benchmark.percentile(values, 0.5) == 5.5
    assert benchmark.percentile(values, 0.95) == pytest.approx(9.55)
    with pytest.raises(ValueError, match="samples"):
        benchmark.percentile([], 0.95)
