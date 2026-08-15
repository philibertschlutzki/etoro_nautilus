"""Issue #945/#1111 (Katalog #960) — Kostenstress-Leiter und berichtete Expectancy standen auf
verschiedenen Basen.

Symptom: ``holdout_expectancy_cost_stress_{1_5x,2x}`` wird aus
``holdout_expectancy_capital_weighted`` abgeleitet, berichtet und sortiert wurde
``holdout_expectancy`` (Mittel von Quotienten, KEIN Notional-Boden). Bei SqueezeBreakout/PLTR
(Divergenz Faktor 7,9) erschien der "2×-Kostenstress" als Verbesserung um +145,76 bps (B-8).

Fix: ``holdout_expectancy_capital_weighted`` ist seither die kanonische, berichtete/sortierte
Grösse; die vormalige ``holdout_expectancy`` heisst jetzt ``holdout_expectancy_notional_weighted``
und ist reine Telemetrie (u. a. weiterhin die korrekte Basis fuer die #1028-Sizing-Identitaet). Eine
neue blockierende Invariante ``check_cost_stress_monotonicity`` bewacht die Konsequenz: auf
DERSELBEN Basis muss ``exp >= exp_1.5x >= exp_2x`` gelten, in gleich grossen Schritten.

Deckt ab:
  - ``check_cost_stress_monotonicity`` PASSt auf einer konsistenten (capital-weighted-basierten)
    Leiter und FAILt auf dem B-8-Altstand (Basis notional-weighted, Leiter capital-weighted-basiert
    — SqueezeBreakout/PLTR: exp=-171.34 bps roh vs. eine aus -21.57 bps abgeleitete Leiter, die
    dadurch als "Verbesserung" erscheint).
  - Akzeptanzkriterium #945: SqueezeBreakout/PLTR meldet exp=-21.57/1.5x=-23.57/2x=-25.57 bps auf
    einer konsistenten Basis — PASS, exakte 2.00-bps-Schritte (volle-c_rt-Eigenschaft #1081, EQUITY
    c_rt=4.00bps ⇒ 0.5x-Schritt = 2.00bps).
  - ``summary_de.py`` Abschnitt 2.1 zeigt/sortiert nach ``holdout_expectancy_capital_weighted``,
    nicht mehr nach der (jetzt umbenannten) notional-gewichteten Grösse.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de


def _bps(x: float) -> float:
    return x / 10000.0


# ── check_cost_stress_monotonicity ────────────────────────────────────────────────────────────

def test_passes_on_a_consistent_capital_weighted_ladder():
    """Akzeptanzkriterium #945: exp=-21.57/1.5x=-23.57/2x=-25.57 bps, exakte 2.00-bps-Schritte
    (volle-c_rt-Eigenschaft #1081) -- PASS."""
    records = [{
        "strategy": "SqueezeBreakout", "symbol": "PLTR.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-21.57),
        "holdout_expectancy_cost_stress_1_5x": _bps(-23.57),
        "holdout_expectancy_cost_stress_2x": _bps(-25.57),
    }]
    result = inv.check_cost_stress_monotonicity(records)
    assert result.passed is True


def test_fails_on_the_b8_regression_pattern_reported_base_diverges_from_stress_ladder():
    """Reproduziert B-8: die Leiter wurde aus capital_weighted abgeleitet (-21.57/-23.57/-25.57),
    aber gegen eine ANDERE, viel niedrigere notional-gewichtete Basis (-171.34 bps) verglichen --
    der 2x-Stress erscheint dann als Verbesserung von +145.77 bps. Dieser Test benutzt absichtlich
    die (falsche) notional-gewichtete Zahl als 'exp', um den Alt-Zustand nachzubilden."""
    records = [{
        "strategy": "SqueezeBreakout", "symbol": "PLTR.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-171.34),  # falsche Basis (Alt-Zustand-Analog)
        "holdout_expectancy_cost_stress_1_5x": _bps(-23.57),
        "holdout_expectancy_cost_stress_2x": _bps(-25.57),
    }]
    result = inv.check_cost_stress_monotonicity(records)
    assert result.passed is False
    assert result.severity == "blocking"
    assert "SqueezeBreakout/PLTR.ETORO" in result.actual


def test_fails_when_steps_are_uneven():
    records = [{
        "strategy": "A", "symbol": "B.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-10.0),
        "holdout_expectancy_cost_stress_1_5x": _bps(-12.0),
        "holdout_expectancy_cost_stress_2x": _bps(-20.0),  # Schritt 2 (8bps) != Schritt 1 (2bps)
    }]
    result = inv.check_cost_stress_monotonicity(records)
    assert result.passed is False
    assert result.actual["A/B.ETORO"]["violation"] == "uneven_steps"


def test_passes_on_41_of_42_studies_fails_on_the_one_altstand_study():
    """Akzeptanzkriterium #945: die Invariante feuert genau einmal auf einem gemischten Datensatz."""
    clean = [{
        "strategy": f"Strat{i}", "symbol": "X.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-10.0 - i),
        "holdout_expectancy_cost_stress_1_5x": _bps(-12.0 - i),
        "holdout_expectancy_cost_stress_2x": _bps(-14.0 - i),
    } for i in range(41)]
    broken = [{
        "strategy": "SqueezeBreakout", "symbol": "PLTR.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-171.34),
        "holdout_expectancy_cost_stress_1_5x": _bps(-23.57),
        "holdout_expectancy_cost_stress_2x": _bps(-25.57),
    }]
    result = inv.check_cost_stress_monotonicity(clean + broken)
    assert result.passed is False
    assert len(result.actual) == 1
    assert "SqueezeBreakout/PLTR.ETORO" in result.actual


def test_within_tolerance_rounding_still_passes():
    records = [{
        "strategy": "A", "symbol": "B.ETORO",
        "holdout_expectancy_capital_weighted": _bps(-10.00),
        "holdout_expectancy_cost_stress_1_5x": _bps(-12.00),
        "holdout_expectancy_cost_stress_2x": _bps(-14.03),  # 0.03bps Rundungsdifferenz < 0.05bps
    }]
    result = inv.check_cost_stress_monotonicity(records)
    assert result.passed is True


def test_not_applicable_without_all_three_fields():
    assert inv.check_cost_stress_monotonicity([{"strategy": "A", "symbol": "B"}]).passed is True


# ── summary_de.py Abschnitt 2.1: sortiert/zeigt holdout_expectancy_capital_weighted ─────────────

def _report_with_candidates(candidates):
    return {
        "run_id": "run-x", "run_status": "complete",
        "started_at_utc": "2026-08-14T19:30:00Z", "wallclock_s": 100.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": None, "symbols_planned": None,
        "studies": candidates,
        "cross_study": {
            "promotion_outcome_counts": {"READY_FOR_PR": len(candidates)},
            "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }


def test_section_2_1_shows_capital_weighted_expectancy_column():
    candidates = [{
        "strategy": "SqueezeBreakout", "symbol": "PLTR.ETORO",
        "promotion_outcome": "READY_FOR_PR", "holdout_total_return": 0.01,
        "holdout_expectancy_capital_weighted": _bps(-21.57),
        "holdout_expectancy_notional_weighted": _bps(-171.34),
        "holdout_total_trades": 8,
    }]
    text = summary_de.generate_german_summary(_report_with_candidates(candidates))
    section = text.split("### 2.1")[1].split("### 2.1b")[0]
    assert "-0.0171" not in section  # notional-gewichteter (falscher) Wert darf nicht erscheinen
    assert "-0.0022" in section  # capital-gewichteter Wert (-21.57 bps, 4 Nachkommastellen)
    assert "kapitalgew" in section.lower()
