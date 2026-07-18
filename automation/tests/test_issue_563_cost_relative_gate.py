"""Issue #562 — Kostenrelatives Expectancy-Gate (k_alpha · c_rt) statt absoluter Magie-Zahl.

Das absolute Gate ``oos_min_expectancy = 0.001`` (10 bps) lag zufällig exakt auf der Round-Trip-
Kostenwand und war damit strukturell unerreichbar. Der Fix definiert das Gate relativ zur
Kostenbasis: ``oos_min_expectancy := k_alpha · c_rt``, wobei ``c_rt`` (Round-Trip-Kosten in bps)
aus dem Kostenmodell abgeleitet und als ``round_trip_cost_bps`` in die OOS-Metriken gestempelt
wird (Single Source of Truth).

Akzeptanzkriterien (Issue #562):
- Das effektive Gate wird zur Laufzeit aus ``c_rt · k_alpha`` berechnet und telemetriert
  (``effective_expectancy_gate``).
- Sensitivität: bei ``k_alpha ∈ {0.0, 0.25, 0.5}`` ist die Winner-Rate monoton fallend und > 0
  bei ``k_alpha = 0`` (breakeven-nach-Kosten erreichbar).
- Property: ändert man Spread/Kommission (⇒ c_rt), verschiebt sich das effektive Gate automatisch.
"""
from automation.backtest_runner import _evaluate_oos_eligibility


def _oos(expectancy, cost_rt_bps=None, **over):
    m = {
        "total_trades": 30, "max_drawdown": 0.05, "win_rate": 0.5,
        "total_return": 0.05, "expectancy": expectancy,
        "sortino_ratio": 2.0, "profit_factor": 1.5,
        "median_position_notional": 1000.0,
    }
    if cost_rt_bps is not None:
        m["round_trip_cost_bps"] = cost_rt_bps
    m.update(over)
    return m


def _cfg(**over):
    cfg = {
        "min_trades": 1, "oos_min_trades": 1,
        "oos_min_total_return": 0.0, "oos_min_expectancy": 0.001,
        "oos_min_win_rate": 0.0, "oos_min_sortino": 0.0, "oos_min_profit_factor": 0.0,
        "max_drawdown": 1.0,
        "eligible_requires_all": ["min_trades", "min_expectancy"],
        "eligible_requires_any": ["min_sortino"],
    }
    cfg.update(over)
    return cfg


# ── Effektives Gate wird kostenrelativ berechnet + telemetriert ──────────────────────────────
def test_effective_gate_derived_from_cost_and_k_alpha():
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    # c_rt = 3 bps ⇒ Gate = 0.25 · 3 / 1e4 = 7.5e-5.
    res = _evaluate_oos_eligibility(_oos(expectancy=1e-4, cost_rt_bps=3.0), cfg)
    assert res["effective_expectancy_gate"] == 0.25 * 3.0 / 10000.0
    # expectancy 1e-4 > 7.5e-5 ⇒ Expectancy-Gate bestanden.
    assert res["oos_gate_deltas"]["oos_min_expectancy"] > 0


def test_gate_shifts_when_cost_changes():
    """Property: c_rt hoch ⇒ effektives Gate hoch (zieht mit dem Kostenmodell mit)."""
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    low = _evaluate_oos_eligibility(_oos(1e-4, cost_rt_bps=3.0), cfg)["effective_expectancy_gate"]
    high = _evaluate_oos_eligibility(_oos(1e-4, cost_rt_bps=9.0), cfg)["effective_expectancy_gate"]
    assert high > low
    assert high == 0.25 * 9.0 / 10000.0


def test_k_alpha_zero_is_breakeven_reachable():
    """k_alpha=0 ⇒ Gate=0 ⇒ breakeven-nach-Kosten (expectancy ≥ 0) ist erreichbar (Sanity)."""
    cfg = _cfg(oos_min_expectancy_k_alpha=0.0)
    res = _evaluate_oos_eligibility(_oos(expectancy=0.0, cost_rt_bps=9.0), cfg)
    assert res["effective_expectancy_gate"] == 0.0
    # expectancy 0.0 >= Gate 0.0 ⇒ min_expectancy-Bedingung erfüllt.
    assert all("oos_min_expectancy" not in r for r in res["oos_rejection_reasons"])


def test_winner_rate_monotone_in_k_alpha():
    """Sensitivität: höhere k_alpha ⇒ strengeres Gate ⇒ monoton fallende Eligibility-Rate."""
    # Eine Population mit gestreuten (kostennahen) Expectancies bei c_rt = 9 bps (0.0009).
    expectancies = [-0.0005, 0.0, 0.0003, 0.0006, 0.001, 0.0015, 0.002]
    eligible_counts = []
    for k in (0.0, 0.25, 0.5):
        cfg = _cfg(oos_min_expectancy_k_alpha=k)
        n = sum(
            1 for e in expectancies
            if _evaluate_oos_eligibility(_oos(e, cost_rt_bps=9.0), cfg)["oos_eligible"]
        )
        eligible_counts.append(n)
    # Monoton fallend (nicht strikt): mehr Alpha-Forderung ⇒ nie mehr Winner.
    assert eligible_counts[0] >= eligible_counts[1] >= eligible_counts[2]
    # Bei k_alpha=0 (breakeven) existiert mindestens ein Winner.
    assert eligible_counts[0] > 0


# ── Rückwärtskompatibilität: fehlt k_alpha ODER die Kosten-Telemetrie ⇒ statisches Legacy-Gate ─
def test_legacy_static_gate_without_k_alpha():
    cfg = _cfg()  # kein oos_min_expectancy_k_alpha
    res = _evaluate_oos_eligibility(_oos(0.0008, cost_rt_bps=3.0), cfg)
    assert res["effective_expectancy_gate"] is None
    # Statisches Gate 0.001: expectancy 0.0008 < 0.001 ⇒ verfehlt.
    assert res["oos_gate_deltas"]["oos_min_expectancy"] < 0


def test_config_derived_fallback_gate_without_cost_telemetry():
    """Issue #684 — k_alpha gesetzt, aber round_trip_cost_bps fehlt (z. B. degenerierter Trial/
    Unit-Call): das Gate bleibt KOSTENRELATIV, nur die Kostenbasis kommt aus dem Config-Kostenmodell
    (backtest.json: commission_bps + spread_bps_by_asset_class.DEFAULT) statt aus der Live-Telemetrie
    — NICHT mehr das ~13× strengere statische 0.001-Gate (die #684-Root-Cause: zwei unabhängig
    gepflegte Schwellen)."""
    from automation.backtest_runner import _read_default_round_trip_cost_bps
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    res = _evaluate_oos_eligibility(_oos(0.0008, cost_rt_bps=None), cfg)
    default_c_rt = _read_default_round_trip_cost_bps()
    expected_gate = 0.25 * default_c_rt / 10000.0
    assert res["effective_expectancy_gate"] == expected_gate
    assert res["expectancy_gate_cost_source"] == "config_default"
    # Der Fallback ist HÖCHSTENS ein kleines Vielfaches vom kostenrelativen Referenzwert (c_rt=3bps
    # ⇒ 7.5e-5) entfernt — NICHT die 13×-strengere statische 0.001-Konstante.
    assert expected_gate < 0.001 / 2.0


def test_legacy_static_gate_without_k_alpha_or_telemetry():
    """Fehlt k_alpha GANZ (nicht konfiguriert), bleibt es beim statischen Legacy-Gate — unabhängig
    von der Kosten-Telemetrie (Zero-Hardcoding: die #684-Kostenbasis-Herleitung greift nur, wenn der
    Operator sich bereits für ein kostenrelatives Gate entschieden hat)."""
    cfg = _cfg()  # kein oos_min_expectancy_k_alpha
    res = _evaluate_oos_eligibility(_oos(0.0008, cost_rt_bps=None), cfg)
    assert res["effective_expectancy_gate"] is None
    assert res["expectancy_gate_cost_source"] == "static"
    assert res["oos_gate_deltas"]["oos_min_expectancy"] < 0  # gegen statische 0.001 gemessen
