"""Issue #410 (P3) — Reward-Semantik-Versionierung & Study-Hygiene.

Die Fixes #404–#407 aendern die Reward-Semantik des Per-Symbol-Pfads. Reward-Werte verschiedener
Semantik-Versionen sind NICHT vergleichbar — eine Study, die noch alte Floor-Trials (Pitfall #75,
konstanter −9.75) enthaelt, wuerde diese mit neuen, gradientenreichen Trials mischen und TPE
verwirren. `_check_reward_semantics_version` stempelt frische Studies mit
`optimizer.json['reward_semantics_version']` und warnt laut bei einer Versions-Diskrepanz (Hinweis:
alte DBs loeschen).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config

CUR = {"reward_semantics_version": 4}


class _FakeStudy:
    def __init__(self, attrs=None, n_trials=0):
        self._attrs = dict(attrs or {})
        self.trials = [object()] * n_trials

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v


def _capturing_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    recs = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    lg.addHandler(_H())
    lg.setLevel(logging.WARNING)
    lg.propagate = False
    return lg, recs


def _warned(recs):
    return [r for r in recs if "Reward-Semantik" in r.getMessage()]


def test_fresh_study_is_stamped_no_warning():
    lg, recs = _capturing_logger("test410a")
    s = _FakeStudy(n_trials=0)
    ro._check_reward_semantics_version(s, CUR, logger=lg)
    assert s.user_attrs["reward_semantics_version"] == 4
    assert not _warned(recs)


def test_old_version_with_trials_raises():
    """Issue #468/#469 — der Versions-Guard ist seit #468 FAIL-LOUD (nicht mehr nur WARN): eine
    geladene Study mit ÄLTERER Semantik-Version UND bereits akkumulierten Trials muss den Lauf
    hart abbrechen (ValueError), damit inkompatible historische Trials die TPE-Posterior nicht
    kontaminieren (Blockade des Ladens inkompatibler Trials, #469-Invariante)."""
    lg, recs = _capturing_logger("test410b")
    s = _FakeStudy(attrs={"reward_semantics_version": 2}, n_trials=10)
    with pytest.raises(ValueError, match="Reward-Semantik"):
        ro._check_reward_semantics_version(s, CUR, logger=lg)


def test_unversioned_study_with_trials_raises():
    """Pre-Versioning-Study mit akkumulierten Trials unbekannter (aelterer) Semantik ⇒ fail-loud
    (Issue #468/#469): unversioniert + Trials ist nicht von „alt & inkompatibel" unterscheidbar."""
    lg, recs = _capturing_logger("test410c")
    s = _FakeStudy(n_trials=5)   # kein Versions-Attr, aber bereits Trials
    with pytest.raises(ValueError, match="Reward-Semantik"):
        ro._check_reward_semantics_version(s, CUR, logger=lg)


def test_current_version_no_warning():
    lg, recs = _capturing_logger("test410d")
    s = _FakeStudy(attrs={"reward_semantics_version": 4}, n_trials=10)
    ro._check_reward_semantics_version(s, CUR, logger=lg)
    assert not _warned(recs)


def test_no_version_configured_is_noop():
    """Fehlt der Config-Key, ist die Pruefung ein No-Op (Rueckwaerts-Kompat)."""
    lg, recs = _capturing_logger("test410e")
    s = _FakeStudy(n_trials=5)
    ro._check_reward_semantics_version(s, {}, logger=lg)
    assert not _warned(recs)
    assert "reward_semantics_version" not in s.user_attrs


def test_conflict_message_mentions_deleting_dbs():
    """Die fail-loud-Meldung bleibt aktionsleitend: Hinweis, die stale Study-DB (.db) zu löschen."""
    lg, recs = _capturing_logger("test410f")
    s = _FakeStudy(attrs={"reward_semantics_version": 1}, n_trials=3)
    with pytest.raises(ValueError, match=r"\.db"):
        ro._check_reward_semantics_version(s, CUR, logger=lg)


def test_shipped_config_has_reward_semantics_version():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    v = cfg.get("reward_semantics_version")
    # Monoton steigende Versionsnummer (Issue #410/#468): muss gesetzt, integer & schema-dokumentiert
    # sein. Bewusst >= statt == (forward-kompatibel zu künftigen Reward-Semantik-Bumps).
    assert isinstance(v, int) and v >= 4
    assert "reward_semantics_version" in cfg["_schema"]["fields"]


# ---------------------------------------------------------------------------
# Integration: optimize_symbol stempelt eine frische Study
# ---------------------------------------------------------------------------

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(trial_dir, manifest_path):
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.3}}}), "utf-8")
    return out


def test_optimize_symbol_stamps_fresh_study(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest)
    # Die frische Study muss exakt mit der ausgelieferten Version gestempelt werden (dynamisch
    # gelesen ⇒ robust gegen künftige Bumps).
    expected = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))["reward_semantics_version"]
    study = ro.optimize_symbol("SmaCrossoverStrategy", "ZZZ.ETORO", n_trials=1)
    assert study.user_attrs.get("reward_semantics_version") == expected
