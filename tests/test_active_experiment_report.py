import runpy
from pathlib import Path


def test_failed_replay_cannot_generate_successful_prose():
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/experiment_active_observation.py'))
    report = script['run_experiment']()
    report['experiment_gate'] = 'FAIL'
    report['checks']['person_free_change_once'] = False
    result = script['render_report'](report)
    assert '实验：FAIL' in result
    assert 'person_free_change_once：FAIL' in result
