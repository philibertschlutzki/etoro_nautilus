"""Issue #839 (P1) — Eine gemessene GR-01-Verletzung hat keine Konsequenz.

`invariants.check_holding_time_cap` FAILt korrekt, wenn eine Study die #714/GR-01-Zeitbox
verletzt, aber bis dahin passierte danach nichts: die Study durchlief Eligibility, Confirm,
Deflation und Champion-Store, als wären ihre Metriken gültig. Der Fix führt drei Konsequenzen ein:

1. `invariants.compute_trial_timebox_violations` — je-Trial-Verletzung (Haltedauer gegen den
   gesampelten oder globalen `max_bars_in_trade`-Deckel), aggregiert je Study.
2. `confirm.confirm_per_symbol_promotion` verwirft eine Study mit `REJECT_INVALID_TIMEBOX`, wenn
   `timebox_violation_fraction > tournament.json['timebox_violation_study_tolerance']` — VOR jedem
   statistischen Gate.
3. `champions._configured_admissible_reject_details` lässt `REJECT_INVALID_TIMEBOX` NIE zu (analog
   `REJECT_HOLDOUT_GATE`) — ein Kandidat mit gebrochenem Exit-Pfad landet nie im Champion-Store.

Issue #857/#858/#861 (Katalog #856-#861, GitHub-Issue #758) — Nachtrag: die Konsequenz einer
Zeitbox-Verletzung liegt seit #857 bereits AUF TRIAL-EBENE (ein einzelner verletzender Trial
kontaminiert nicht mehr 159 saubere Geschwister, Pitfall #272); `timebox_violation_tolerance`
(Default 0.0) wurde durch `timebox_violation_study_tolerance` (Default 0.25) ersetzt — ein reiner
Bug-Detektor für einen strukturell defekten Exit-Pfad, keine Ausführungslatenz-Toleranz mehr. #858
ersetzt die feste 0.01-Bar-Toleranz durch `timebox_execution_slack_bars` (Default 3.0 =
`exit_close_max_bars + 1`). #861 vereinheitlicht `check_holding_time_cap` auf dieselbe
per-Trial-aware Berechnung wie `compute_trial_timebox_violations` (`resolve_effective_bar_cap`).
"""
import json

import optuna

from automation.optimizer import champions
from automation.optimizer import invariants as inv


# ── invariants.compute_trial_timebox_violations (reine Funktion) ────────────────────────────────
_BAR_S = 3600.0


def test_bar_seconds_is_a_mandatory_parameter():
    """Issue #902 Fix 1 — kein Default mehr: ein Aufruf ohne bar_seconds wirft TypeError statt
    (wie bis #858) still auf den 24/7-Stundenraster-Default zurückzufallen."""
    import pytest
    with pytest.raises(TypeError):
        inv.compute_trial_timebox_violations([])


def test_no_trials_with_holding_data_is_zero_fraction():
    result = inv.compute_trial_timebox_violations([], bar_seconds=_BAR_S)
    assert result == {
        "timebox_violating_trials": 0,
        "timebox_evaluated_trials": 0,
        "timebox_violation_fraction": 0.0,
        "timebox_violating_round_trips": 0,
        "timebox_evaluated_round_trips": 0,
        "timebox_round_trip_violation_fraction": 0.0,
        "timebox_violation_intensity_p95": None,
        "timebox_violated": False,
        "timebox_cap_source_counts": {},
    }


def test_trial_within_sampled_cap_is_not_a_violation():
    # Issue #858 — Default-Toleranz ist jetzt 3.0 Bars (exit_close_max_bars + 1), nicht mehr 0.01.
    attrs = [{"oos_max_holding_time_s": 23 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}}]
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_violated"] is False
    assert result["timebox_violating_trials"] == 0
    assert result["timebox_violating_round_trips"] == 0


def test_trial_beyond_sampled_cap_is_a_violation():
    attrs = [{"oos_max_holding_time_s": 100 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}}]
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_violated"] is True
    assert result["timebox_violating_trials"] == 1
    assert result["timebox_violation_fraction"] == 1.0
    assert result["timebox_violating_round_trips"] == 1
    assert result["timebox_round_trip_violation_fraction"] == 1.0


def test_missing_sampled_max_bars_falls_back_to_global_cap():
    """Strategie sampelt max_bars_in_trade nicht ⇒ Fallback auf den globalen #714/GR-01-Deckel
    (24 Bars) — dieselbe konservative Schranke wie check_holding_time_cap."""
    attrs = [{"oos_max_holding_time_s": 30 * 3600.0}]  # kein sampled_params, > 24+3 Bars
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_violated"] is True
    assert result["timebox_cap_source_counts"] == {"global": 1}

    attrs_ok = [{"oos_max_holding_time_s": 20 * 3600.0}]
    result_ok = inv.compute_trial_timebox_violations(attrs_ok, bar_seconds=_BAR_S)
    assert result_ok["timebox_violated"] is False


def test_fraction_over_mixed_cohort():
    attrs = [
        {"oos_max_holding_time_s": 100 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}},
        {"oos_max_holding_time_s": 10 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}},
        {"oos_max_holding_time_s": None},  # nicht oos_evaluated -> zaehlt nicht mit
    ]
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_evaluated_trials"] == 2
    assert result["timebox_violating_trials"] == 1
    assert result["timebox_violation_fraction"] == 0.5
    assert result["timebox_evaluated_round_trips"] == 2
    assert result["timebox_violating_round_trips"] == 1
    assert result["timebox_cap_source_counts"] == {"sampled": 2}


def test_round_trip_level_uses_raw_holding_times_when_available():
    """Issue #903 Akzeptanzkriterium — 100 Trials x 50 Round-Trips, davon 1 Round-Trip je Trial
    ueber der Box: violating_trials=100, violating_round_trips=100, evaluated_round_trips=5000,
    round_trip_violation_fraction=0.02. Die STUDY-Toleranz (angewandt in confirm.py) wirkt auf
    dieser Fraction, nicht auf der (zehnmal groesseren) Trial-Fraction."""
    cap_bars = 24
    ok_holds = [1.0 * 3600.0] * 49  # weit innerhalb der Box
    bad_hold = [(cap_bars + 3.0 + 1.0) * 3600.0]  # > (cap+tolerance)*bar_seconds
    attrs = [
        {
            "oos_max_holding_time_s": max(ok_holds + bad_hold),
            "oos_holding_times_s": ok_holds + bad_hold,
            "sampled_params": {"max_bars_in_trade": cap_bars},
        }
        for _ in range(100)
    ]
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_violating_trials"] == 100
    assert result["timebox_violating_round_trips"] == 100
    assert result["timebox_evaluated_round_trips"] == 5000
    assert result["timebox_round_trip_violation_fraction"] == 0.02


def test_round_trip_level_falls_back_to_single_point_without_raw_list():
    """Rueckwaertskompatibilitaet: Pre-#899-JSONs ohne oos_holding_times_s zaehlen hoechstens 1
    Round-Trip je Trial (der Trial-Maximum-Punkt) — konservativ, aber nie falsch-negativ."""
    attrs = [{"oos_max_holding_time_s": 100 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}}]
    result = inv.compute_trial_timebox_violations(attrs, bar_seconds=_BAR_S)
    assert result["timebox_evaluated_round_trips"] == 1
    assert result["timebox_violating_round_trips"] == 1


# ── invariants.resolve_effective_bar_cap (#861) ──────────────────────────────────────────────────
def test_resolve_effective_bar_cap_prefers_sampled_value():
    cap, source = inv.resolve_effective_bar_cap({"max_bars_in_trade": 12}, strategy="S")
    assert (cap, source) == (12.0, "sampled")


def test_resolve_effective_bar_cap_falls_back_to_strategy_defaults():
    cap, source = inv.resolve_effective_bar_cap(
        {}, strategy="S", strategy_defaults={"S": {"max_bars_in_trade": 18}})
    assert (cap, source) == (18.0, "default")


def test_resolve_effective_bar_cap_falls_back_to_global():
    cap, source = inv.resolve_effective_bar_cap(None, strategy="S", strategy_defaults={})
    assert (cap, source) == (24.0, "global")


# ── invariants.check_holding_time_cap (#861 — unified contract; #971 — trade-, not trial-level) ──
def test_check_holding_time_cap_is_blocking_severity():
    """Issue #971 — konsumiert jetzt die TRADE-(Round-Trip-)Ebene
    (timebox_violating_trades_frac/_numerator/_denominator, report._study_record), nicht mehr die
    TRIAL-Ebene (timebox_violation_fraction) und nicht mehr max_holding_time_s gegen einen
    Pauschaldeckel."""
    result = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 4, "timebox_violating_trades_frac": 1.0,
        "timebox_violating_trades_numerator": 4,
    }])
    assert result.severity == "blocking"
    assert result.passed is False
    assert result.provenance is not None
    assert result.provenance["per_study"]["S/X"] == {"numerator": 4, "denominator": 4}


def test_check_holding_time_cap_passes_within_study_tolerance():
    result = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 160, "timebox_violating_trades_frac": 0.05,
        "timebox_violating_trades_numerator": 8,
    }], study_tolerance=0.25)
    assert result.passed is True
    assert result.provenance is None


def test_check_holding_time_cap_fails_beyond_study_tolerance():
    result = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 160, "timebox_violating_trades_frac": 0.30,
        "timebox_violating_trades_numerator": 48,
    }], study_tolerance=0.25)
    assert result.passed is False
    assert "S/X" in result.detail


def test_check_holding_time_cap_ignores_trial_count_disguised_as_unevaluable_fraction():
    """Issue #971 (P0 HEADLINE) — Regressionswächter gegen den Referenzlauf-Befund: ein Trial mit
    EINEM zeitbox-verletzenden Round-Trip unter vielen sauberen darf die Study NICHT als "kaputter
    Exit-Pfad" markieren, nur weil er TRIAL-weise zu 100% als "verletzend" zählt (20/67 = 0.2985,
    identisch zu (n_trials - evaluable_trials) / n_trials im Referenzlauf 46cf5070 — eine Grösse,
    die nichts mit der Haltedauer zu tun hat, siehe #973: hit_trade_cap/time_box_penalty waren im
    gesamten Lauf konstant False/0.0)."""
    # 67 Trials, davon 20 TRIAL-weise "verletzend" (je 1 von 10 Trades pro Trial verletzt die Box).
    # TRIAL-Quote waere 20/67 = 0.2985 (> 0.25 Toleranz) — TRADE-Quote ist 20/(20*10) = 0.02.
    result = inv.check_holding_time_cap([{
        "strategy": "DynamicBreakoutStrategy", "symbol": "GSAT.ETORO",
        "timebox_violating_trades_denominator": 200,
        "timebox_violating_trades_numerator": 20,
        "timebox_violating_trades_frac": round(20 / 200, 4),
    }], study_tolerance=0.25)
    assert result.passed is True


def test_check_holding_time_cap_and_compute_trial_timebox_violations_agree():
    """Issue #861/#971-Akzeptanzkriterium — eine Study mit gesampeltem Cap 12 und 20 Bars Haltedauer
    laesst BEIDE Checks FAILen (vorher: check_holding_time_cap sauber bei 20 < 24, aber
    compute_trial_timebox_violations bereits eine Verletzung)."""
    trial_attrs = [{"oos_max_holding_time_s": 20 * 3600.0, "sampled_params": {"max_bars_in_trade": 12}}]
    timebox = inv.compute_trial_timebox_violations(trial_attrs, bar_seconds=_BAR_S)
    assert timebox["timebox_violated"] is True

    study_record = {"strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": timebox["timebox_evaluated_round_trips"],
        "timebox_violating_trades_numerator": timebox["timebox_violating_round_trips"],
        "timebox_violating_trades_frac": timebox["timebox_round_trip_violation_fraction"],
    }
    result = inv.check_holding_time_cap([study_record], study_tolerance=0.25)
    assert result.passed is False


# ── confirm.py: REJECT_INVALID_TIMEBOX vor allen statistischen Gates ─────────────────────────────
def _study_with_timebox_trials(n_violations: int, n_ok: int):
    study = optuna.create_study(direction="maximize")
    for _ in range(n_violations):
        t = study.ask()
        t.set_user_attr("oos_max_holding_time_s", 100 * 3600.0)
        t.set_user_attr("sampled_params", {"max_bars_in_trade": 24})
        t.set_user_attr("oos_eligible", True)
        study.tell(t, 1.0)
    for _ in range(n_ok):
        t = study.ask()
        t.set_user_attr("oos_max_holding_time_s", 10 * 3600.0)
        t.set_user_attr("sampled_params", {"max_bars_in_trade": 24})
        t.set_user_attr("oos_eligible", True)
        study.tell(t, 1.0)
    return study


def test_confirm_rejects_study_with_timebox_violation(tmp_path, monkeypatch):
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "timebox_violation_study_tolerance": 0.0}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps({"promotion_margin": 0.10}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload, level=None: events.append((name, payload)))

    study = _study_with_timebox_trials(n_violations=1, n_ok=0)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "REJECT_INVALID_TIMEBOX"
    assert any(name == "STUDY_REJECTED_ON_TIMEBOX_VIOLATION" for name, _ in events)
    event_payload = next(p for name, p in events if name == "STUDY_REJECTED_ON_TIMEBOX_VIOLATION")
    # Issue #903 Fix 4 — timebox_trials_invalidated entfaellt (war wertgleich mit
    # timebox_violation_trades unter zweitem Namen); die Round-Trip-Ebene ist jetzt die
    # massgebliche Entscheidungsgrundlage (Fix 2).
    assert "timebox_trials_invalidated" not in event_payload
    assert event_payload["timebox_violating_trials"] == 1
    assert event_payload["timebox_violating_round_trips"] == 1


def test_confirm_tolerates_a_single_violating_trial_within_study_tolerance(tmp_path, monkeypatch):
    """Issue #857-Akzeptanzkriterium — eine Study mit 1 verletzenden von 160 Trials wird NICHT
    verworfen (Default-Toleranz 0.25); die 159 sauberen Trials durchlaufen Eligibility/Confirm
    normal (Kohorten-Kontamination durch einen einzelnen Ausreisser ist genau das, was #857
    behebt, Pitfall #272)."""
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)

    from automation.optimizer.parsing import TournamentMetrics
    monkeypatch.setattr(
        cmod, "_holdout_metrics_for_params",
        lambda strategy, symbol, params, **kw: TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
            oos_sortino=-0.5, oos_max_drawdown=0.05, oos_total_trades=30, win_count=1,
            fully_eligible_pairs=1, is_total_trades=100, oos_total_return=-0.02))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.3)

    study = _study_with_timebox_trials(n_violations=1, n_ok=159)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res.get("is_rejection_detail_override") != "REJECT_INVALID_TIMEBOX"


def test_confirm_still_rejects_study_with_majority_violation(tmp_path, monkeypatch):
    """Issue #857-Akzeptanzkriterium — eine Study mit 60% verletzenden Trials wird weiterhin mit
    REJECT_INVALID_TIMEBOX verworfen (jenseits der Default-Study-Toleranz 0.25)."""
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps({"max_drawdown": 0.30}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps({"promotion_margin": 0.10}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)

    study = _study_with_timebox_trials(n_violations=6, n_ok=4)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "REJECT_INVALID_TIMEBOX"


def test_confirm_does_not_reject_clean_study_on_timebox(tmp_path, monkeypatch):
    """Regressionswächter: eine Study OHNE Zeitbox-Verletzung darf durch den neuen Check nicht
    beeinflusst werden (kein falsch-positiver REJECT_INVALID_TIMEBOX)."""
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False,
         "timebox_violation_study_tolerance": 0.0}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)

    from automation.optimizer.parsing import TournamentMetrics
    monkeypatch.setattr(
        cmod, "_holdout_metrics_for_params",
        lambda strategy, symbol, params, **kw: TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
            oos_sortino=-0.5, oos_max_drawdown=0.05, oos_total_trades=30, win_count=1,
            fully_eligible_pairs=1, is_total_trades=100, oos_total_return=-0.02))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.3)

    study = _study_with_timebox_trials(n_violations=0, n_ok=5)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res.get("is_rejection_detail_override") != "REJECT_INVALID_TIMEBOX"


# ── champions.py: REJECT_INVALID_TIMEBOX ist nie zulaessig ──────────────────────────────────────
def test_champion_allowlist_never_admits_timebox_rejection():
    admissible = champions._configured_admissible_reject_details(
        {"champion_admissible_reject_details": ["REJECT_INVALID_TIMEBOX", "REJECT_HOLDOUT_DSR_DROP"]})
    assert "REJECT_INVALID_TIMEBOX" not in admissible
    assert "REJECT_HOLDOUT_DSR_DROP" in admissible


def test_champion_allowlist_default_excludes_timebox_rejection():
    admissible = champions._configured_admissible_reject_details({})
    assert "REJECT_INVALID_TIMEBOX" not in admissible


# ── sweep.py: Fail-Fast-Preflight-Entscheidungsfunktion ──────────────────────────────────────────
def test_fail_fast_helper_returns_none_when_nothing_listed_fails():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_reward_term_variance", "passed": False},
              {"name": "check_holding_time_cap", "passed": True}]
    assert _first_failing_fail_fast_invariant(checks, ["check_holding_time_cap"]) is None


def test_fail_fast_helper_detects_listed_failure():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_holding_time_cap", "passed": False}]
    assert _first_failing_fail_fast_invariant(checks, ["check_holding_time_cap"]) == "check_holding_time_cap"


def test_fail_fast_helper_empty_list_is_disabled():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_holding_time_cap", "passed": False}]
    assert _first_failing_fail_fast_invariant(checks, []) is None
