import pytest

from factory_monitor.view_feasibility import evaluate_view_budget


def test_sixteen_grid_and_upscaled_detail_do_not_invent_source_pixels():
    report = evaluate_view_budget(16, (1920, 1080), (4, 4),
                                 preview_source_size=(640, 360), detail_source_size=(640, 360),
                                 object_width_fraction=.00625)
    assert report['tile_pixels'] == [480, 270]
    assert report['effective_grid_pixels'] == [480, 270]
    assert report['effective_detail_pixels'] == [640, 360]
    assert report['object_grid_width_pixels'] == 3
    assert report['object_detail_width_pixels'] == 4
    assert report['quality_gate'] == 'REQUIRES_TASK_CALIBRATION'


def test_unknown_source_resolution_remains_unknown():
    report = evaluate_view_budget(9, (1920, 1080), (3, 3))
    assert report['tile_pixels'] == [640, 360]
    assert report['effective_detail_pixels'] is None
    assert report['detail_information_gain'] is None


def test_single_screen巡检_cannot_satisfy_original_blind_budget():
    report = evaluate_view_budget(16, (1920, 1080), (4, 4))
    assert report['nominal_revisit_seconds'] == 112
    assert report['overview_unavailable_seconds_per_hour'] == pytest.approx(3600 * 6 / 7)
    assert report['blind_budget_gate'] == 'FAIL'
    assert report['budget_limited_mean_sweep_seconds'] == 1920


def test_persistent_overview_is_an_assumption_not_verified_capacity():
    report = evaluate_view_budget(16, (1920, 1080), (4, 4), persistent_overview=True)
    assert report['overview_unavailable_seconds_per_hour'] == 0
    assert report['field_gate'] == 'NOT_TESTED'
    assert report['persistent_overview_requires_verification'] is True


@pytest.mark.parametrize('kwargs', [
    {'camera_count': 17}, {'display_size': (0, 1080)}, {'object_width_fraction': float('nan')},
    {'detail_seconds': -1}, {'blind_budget_seconds': 0},
])
def test_invalid_inputs_are_not_silently_rounded(kwargs):
    values = dict(camera_count=16, display_size=(1920, 1080), grid_shape=(4, 4))
    values.update(kwargs)
    with pytest.raises(ValueError): evaluate_view_budget(**values)


@pytest.mark.parametrize('value', [1e308, 10**400, 1e-310])
def test_numeric_extremes_cannot_produce_a_false_passing_budget(value):
    with pytest.raises(ValueError):
        evaluate_view_budget(16, (1920, 1080), (4, 4), detail_seconds=value,
                             grid_seconds=value, blind_budget_seconds=value)
