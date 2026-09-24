import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'benchmark_local_review', Path(__file__).parents[1] / 'scripts/benchmark_local_review.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_false_positive_fails_even_if_every_response_is_valid():
    assert benchmark.negative_fixture_gate([
        {'status': 'schema_valid', 'result': {'decision': 'supported'}}], 1) == 'FAIL'


def test_missing_or_uncertain_results_cannot_pass_negative_fixture():
    assert benchmark.negative_fixture_gate([{'status': 'timeout'}], 1) == 'INCOMPLETE'
    assert benchmark.negative_fixture_gate([], 1) == 'INCOMPLETE'
    assert benchmark.negative_fixture_gate([
        {'status': 'schema_valid', 'result': {'decision': 'uncertain'}}], 1) == 'INCOMPLETE'


def test_complete_dismissals_only_pass_the_public_negative_scope():
    assert benchmark.negative_fixture_gate([
        {'status': 'schema_valid', 'result': {'decision': 'dismissed'}}], 1) == 'PASS_PUBLIC_NEGATIVE_ONLY'
