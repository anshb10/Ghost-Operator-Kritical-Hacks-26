"""The evaluation suite doubles as a regression gate: every case in evals/cases.json must keep passing."""
import pytest

from evals import run as ev


@pytest.fixture(scope="module")
def missions():
    out = {k: ev.run_mission_sync(k)[0] for k in ev.MISSIONS}
    return out


@pytest.mark.parametrize("category", ["requirements", "claims", "misleading", "landed"])
def test_offline_categories(category):
    res = getattr(ev, f"eval_{category}")()
    failures = [r for r in res["rows"] if not r["pass"]]
    assert not failures, failures


def test_ranking_changes(missions):
    failures = [r for r in ev.eval_ranking(missions)["rows"] if not r["pass"]]
    assert not failures, failures
