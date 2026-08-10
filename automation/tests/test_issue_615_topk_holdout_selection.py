"""Issue #615 — Top-k-Holdout-Selektion (#576) war toter Code.

``confirm_per_symbol_promotion`` filterte eligible Trials auf ``is_rejection_detail is None``
(Python-``None``), gestempelt wurde aber der STRING "NONE" ⇒ ``eligible_trials`` war in JEDER Study
leer ⇒ Fallback ``[study.best_trial]`` ⇒ faktisch k=1 (nie ``holdout_top_k``). Der Median-Vektor
(#594) war inert (Index immer 0), und das Proposal exportierte einen GEMISCHTEN Vektor: Params von
Trial Y (argmax), Gate/Reward/Deflation von Trial X (Median).

Fix (#615):
1. Filter auf das gestempelte ``oos_eligible`` (kohärent zu ``is_rejection_detail == "NONE"``).
2. KOHÄRENZ: der Median-Rang-Trial wird VOLLSTÄNDIG promotet — Params + Gate + R_symbol + Deflation
   aus EINEM ``trial_dir``.
3. ``holdout_top_k`` in tournament.json deklariert (Zero-Hardcoding).
4. Leere eligible-Menge ⇒ fail-loud ``HOLDOUT_NO_ELIGIBLE_TRIALS`` (KEIN Floor-Trial im Holdout).
"""
import json

import optuna
import pytest

from automation.optimizer import confirm as cmod
from automation.optimizer.parsing import TournamentMetrics


# holdout oos_total_return je Trial-Index p (bestimmt den Median-Rang der Top-k):
_RET = {9: 0.50, 8: 0.10, 7: 0.30, 6: 0.40, 5: 0.20}


def _mk(*, sortino, ret, dd, td):
    # Issue #958 — oos_n_periods > 0 macht diesen Fixture-Trial admissible (ein real evaluierter
    # Trial mit 30 Trades haette in der Praxis stets > 0 OOS-Perioden).
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=sortino, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_n_periods=50)
    m.holdout_trial_dir = td
    return m


def _study(n_eligible, *, ineligible_rewards=()):
    study = optuna.create_study(direction="maximize")
    for i in range(n_eligible):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
        study._storage.set_trial_user_attr(t._trial_id, "sampled_params", {"p": i})
        study.tell(t, float(i))
    # Ineligible Trials — auch mit (absichtlich hohem) Reward: sie DÜRFEN NIE selektiert werden.
    for k, rew in enumerate(ineligible_rewards):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", False)
        study._storage.set_trial_user_attr(t._trial_id, "sampled_params", {"p": 1000 + k})
        study.tell(t, float(rew))
    return study


def _patch(tmp_path, monkeypatch, *, top_k=5, deflated=False):
    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": top_k, "deflated_selection": deflated,
         "oos_min_total_return": 0.005}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)


def _wire_backtests(monkeypatch):
    """Mockt _holdout_metrics_for_params (params→Metriken mit trial_dir) und compute_reward
    (fängt die Metrik-Argumente ⇒ Provenienz nachweisbar). Rückgabe: (holdout_calls, reward_calls)."""
    holdout_calls, reward_calls = [], []

    def fake_holdout(strategy, symbol, params, **kw):
        holdout_calls.append(dict(params))
        i = params.get("p", -1)
        return _mk(sortino=1.0, ret=_RET.get(i, 0.05), dd=0.1, td=f"td_{i}")

    def fake_reward(m, **kw):
        reward_calls.append(m)
        return float(m.oos_total_return) * 10.0

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", fake_reward)
    return holdout_calls, reward_calls


# ── Akzeptanz: 10 eligible Trials ⇒ genau holdout_top_k Symbol-Holdouts (len(best_trials)==top_k) ──
def test_top_k_holdout_backtests_run(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch, top_k=5)
    holdout_calls, _ = _wire_backtests(monkeypatch)
    study = _study(10)
    cmod.confirm_per_symbol_promotion(study, "S", "SYM", {})
    # 1 globaler Holdout ({}) + genau top_k=5 Symbol-Holdouts (die Top-5 nach Reward: p9..p5).
    symbol_calls = [c for c in holdout_calls if "p" in c]
    assert len(symbol_calls) == 5, symbol_calls
    assert {c["p"] for c in symbol_calls} == {9, 8, 7, 6, 5}


def test_top_k_respects_configured_value(tmp_path, monkeypatch):
    """len(best_trials) == holdout_top_k — auch für ein anderes top_k (Zero-Hardcoding)."""
    _patch(tmp_path, monkeypatch, top_k=3)
    holdout_calls, _ = _wire_backtests(monkeypatch)
    cmod.confirm_per_symbol_promotion(_study(10), "S", "SYM", {})
    assert len([c for c in holdout_calls if "p" in c]) == 3


# ── Invariante: symbol_params, R_symbol, holdout_passed, trial_dir aus DEMSELBEN trial_dir ────────
def test_promotion_coherent_single_trial_dir(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch, top_k=5)
    _, reward_calls = _wire_backtests(monkeypatch)
    res = cmod.confirm_per_symbol_promotion(_study(10), "S", "SYM", {})

    # Median-Rang über Top-5-Returns [0.5,0.1,0.3,0.4,0.2] (Reihenfolge p9,p8,p7,p6,p5) ⇒ 0.3 ⇒ p7.
    assert res["symbol_params"] == {"p": 7}
    assert res["trial_dir"] == "td_7"
    # metrics_symbol stammt aus demselben (p7-)Lauf.
    assert res["metrics_symbol"]["oos_sortino"] == 1.0
    # R_symbol wurde NACHWEISLICH aus dem promoteten (td_7-)Metrikvektor berechnet:
    # reward_calls[0] == m_global ({}→td_-1), reward_calls[1] == promoted (td_7).
    assert reward_calls[1].holdout_trial_dir == "td_7"
    assert res["status"] == "READY_FOR_PR"          # R_symbol(3.0) > R_global(0.5) + margin(0.1)
    assert res["holdout_passed"] is True


def test_exported_proposal_carries_trial_dir(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch, top_k=5)
    _wire_backtests(monkeypatch)
    monkeypatch.setattr(cmod, "WORK", tmp_path)
    study = _study(10)
    res = cmod.confirm_per_symbol_promotion(study, "S", "SYM", {})
    p = cmod.export_symbol_proposal(study, "S", "SYM", res)
    written = json.loads(p.read_text("utf-8"))
    assert written["holdout_trial_dir"] == res["trial_dir"] == "td_7"
    assert written["proposed_instrument_override"] == {"p": 7}


# ── Ineligible Trials werden ausgeschlossen — auch mit (absichtlich) hohem Reward ─────────────────
def test_ineligible_high_reward_trial_excluded(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch, top_k=5)
    holdout_calls, _ = _wire_backtests(monkeypatch)
    # 3 eligible (p0..p2) + ein ineligible Trial mit Reward 9999 (darf NIE promotet werden).
    study = _study(3, ineligible_rewards=(9999.0,))
    res = cmod.confirm_per_symbol_promotion(study, "S", "SYM", {})
    symbol_calls = [c for c in holdout_calls if "p" in c]
    # Nur die 3 eligiblen (p0,p1,p2); das p1000-Ineligible ist NICHT dabei.
    assert {c["p"] for c in symbol_calls} == {0, 1, 2}
    assert res["symbol_params"].get("p") in {0, 1, 2}
    assert 1000 not in {c["p"] for c in symbol_calls}


# ── Study ohne eligible Trials ⇒ fail-loud HOLDOUT_NO_ELIGIBLE_TRIALS, KEIN Floor-Trial ──────────
def test_no_eligible_trials_fail_loud_no_floor(tmp_path, monkeypatch):
    """Issue #682 — der globale Vektor MUSS hier selbst am Symbol-Holdout scheitern (negativer
    Sortino), sonst griffe die #682-Route (PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL) — dieser Test prüft
    explizit den verbleibenden Fail-Loud-Fall (auch der globale Default ist auf diesem Symbol nicht
    tragfähig), NICHT den #682-Fallback (siehe test_issue_682_global_default_promotion.py)."""
    _patch(tmp_path, monkeypatch, top_k=5)
    holdout_calls = []

    def fake_holdout(strategy, symbol, params, **kw):
        holdout_calls.append(dict(params))
        if not params:  # globaler Aufruf — scheitert selbst am Holdout (negativer Sortino).
            return _mk(sortino=-1.0, ret=-0.05, dd=0.1, td="td_global")
        i = params.get("p", -1)
        return _mk(sortino=1.0, ret=_RET.get(i, 0.05), dd=0.1, td=f"td_{i}")

    def fake_reward(m, **kw):
        return float(m.oos_total_return) * 10.0

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", fake_reward)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload: events.append((name, payload)))
    # Nur ineligible Trials (auch ein hoher Floor-Reward): KEINER darf in den Holdout wandern.
    study = _study(0, ineligible_rewards=(5.0, 4.0, 3.0))
    res = cmod.confirm_per_symbol_promotion(study, "S", "SYM", {})

    assert res["status"] == "REJECTED_ON_HOLDOUT"
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"
    assert res["symbol_params"] == {}
    assert res["trial_dir"] is None
    # Nur der globale Holdout ({}) lief — KEIN Symbol-/Floor-Trial-Holdout.
    assert [c for c in holdout_calls if "p" in c] == []
    # Das strukturierte Event wurde emittiert.
    assert any(name == "HOLDOUT_NO_ELIGIBLE_TRIALS" for name, _ in events)


# ── Config-Deklaration (#615) ────────────────────────────────────────────────────────────────────
def test_holdout_top_k_declared_in_tournament_config():
    from pathlib import Path
    tcfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert tcfg["holdout_top_k"] == 5
    assert "holdout_top_k" in tcfg["_schema"]["fields"]
