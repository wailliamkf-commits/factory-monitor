import pytest

from factory_monitor.active_inspection import InspectionPlanner

FP = 'a' * 64


def planner(ids=('A', 'B'), **kwargs):
    p = InspectionPlanner(ids, FP, inspection_interval=30, detail_seconds=1,
                          transition_timeout=2, grid_hold_seconds=0, **kwargs)
    p.arm_overview(now=0, frame_at=0, fingerprint=FP, camera_ids=list(ids))
    return p


def confirm(p, action, now, view, camera=None, **kwargs):
    return p.confirm(action['request_id'], now=now, frame_at=now, view=view,
                     camera_id=camera, fingerprint=FP, target_verified=True, **kwargs)


def finish_cycle(p, start):
    action = p.tick(start)
    assert action['action'] == 'detail'
    assert confirm(p, action, start + .1, 'detail', action['camera_id'])
    back = p.tick(start + 1.1)
    assert back['action'] == 'grid'
    assert confirm(p, back, start + 1.2, 'grid')
    return action['camera_id']


def test_no_click_suggestions_before_verified_overview():
    p = InspectionPlanner(['A'], FP)
    assert p.tick(0) is None
    assert p.snapshot(0)['state'] == 'unverified'


def test_sixteen_quiet_cameras_are_scheduled_without_people_or_events():
    ids = [f'C{i:02}' for i in range(16)]
    p = planner(ids, blind_budget_seconds=180)
    visited = []
    for i in range(16):
        p.submit(ids[0], 'scene_change', now=i * 1.3, ttl=10)
        visited.append(finish_cycle(p, i * 1.3))
    assert visited == ids
    assert p.snapshot(21)['pending_count'] <= 16


def test_stale_result_faults_without_publishing_a_detail_camera():
    p = planner()
    action = p.tick(0)
    assert not p.confirm(action['request_id'], now=1, frame_at=0, view='detail',
                         camera_id='A', fingerprint=FP, target_verified=True)
    assert p.snapshot(1)['state'] == 'fault'
    assert p.tick(10) is None


@pytest.mark.parametrize('override', [
    {'camera_id': 'B'}, {'fingerprint': 'b' * 64}, {'target_verified': False},
    {'frame_at': float('nan')}, {'request_id': 'replayed'},
])
def test_wrong_or_untrusted_ack_never_commits_view(override):
    p = planner(); action = p.tick(0)
    values = dict(request_id=action['request_id'], now=1, frame_at=1, view='detail',
                  camera_id='A', fingerprint=FP, target_verified=True)
    values.update(override)
    assert p.confirm(**values) is False
    assert p.snapshot(1)['state'] == 'fault'


def test_timeout_retains_unknown_and_counts_continuing_blindness():
    p = planner(); p.tick(0)
    assert p.tick(2.1) is None
    assert p.snapshot(5)['overview_unavailable_seconds'] == pytest.approx(5)
    assert p.snapshot(5)['state'] == 'fault'


def test_return_failure_does_not_resume_grid_or_new_actions():
    p = planner(); a = p.tick(0); confirm(p, a, .1, 'detail', 'A')
    b = p.tick(1.1)
    assert not p.confirm(b['request_id'], now=1.2, frame_at=1.2, view='detail',
                         camera_id='A', fingerprint=FP, target_verified=True)
    assert p.tick(20) is None


def test_budget_defers_work_in_grid_and_reports_missed_deadlines():
    p = planner(blind_budget_seconds=5.25)
    finish_cycle(p, 0)
    assert p.tick(1.3) is None  # observed 1.2 + reserved 5.25 exceeds 5.25
    snapshot = p.snapshot(31)
    assert snapshot['state'] == 'grid'
    assert snapshot['blocked_reason'] == 'overview_blind_budget'
    assert snapshot['overdue_cameras']


def test_expired_event_hint_not_resurrected_but_periodic_inspection_remains():
    p = planner()
    finish_cycle(p, 0); finish_cycle(p, 1.3)
    p.submit('A', 'scene_change', now=3, ttl=1)
    assert p.tick(5) is None
    assert p.snapshot(5)['expired_hints'] == 1


def test_rearming_requires_newer_complete_readback_and_preserves_blind_usage():
    p = planner(); p.tick(0); p.tick(3)
    with pytest.raises(ValueError):
        p.arm_overview(now=4, frame_at=2, fingerprint=FP, camera_ids=['A', 'B'])
    p.arm_overview(now=4, frame_at=4, fingerprint=FP, camera_ids=['A', 'B'])
    assert p.snapshot(4)['overview_unavailable_seconds'] == 4


@pytest.mark.parametrize('ids', [[], ['A', 'A'], ['']])
def test_invalid_inventory_is_rejected(ids):
    with pytest.raises(ValueError): InspectionPlanner(ids, FP)


def test_clock_rollback_faults_and_cannot_be_ignored():
    p = planner(); p.tick(10)
    with pytest.raises(ValueError): p.tick(9)
    assert p.snapshot(10)['state'] == 'fault'
    assert p.tick(11) is None


def test_late_scheduler_reports_actual_blind_budget_breach():
    p = planner(blind_budget_seconds=5.25)
    action = p.tick(0)
    confirm(p, action, 1, 'detail', 'A')
    p.tick(10)
    snapshot = p.snapshot(10)
    assert snapshot['budget_gate'] == 'FAIL'
    assert snapshot['state'] == 'fault'


def test_unrepresentable_config_time_rejected():
    with pytest.raises(ValueError): InspectionPlanner(['A'], FP, detail_seconds=10**400)
