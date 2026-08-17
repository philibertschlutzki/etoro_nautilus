"""Issue #987/#1141 (Katalog #986, Pitfall #412 in AGENTS.md) — das Kostenmodell kannte nur Spread
und Kommission (``c_rt``), keine Finanzierung, kein Slippage-Modell, keine Wartungsmargin.

Fix (Scope-Entscheidung, siehe Code-Kommentare in ``backtest_runner.py``):
1. ``overnight_financing_bps_per_day_by_asset_class`` (``backtest.json``) — Finanzierungskosten je
   GEHALTENEM Kalendertag (``ceil(Haltedauer/24h)``, dokumentierte Vereinfachung), als
   ``rt_financing_bps`` je Round-Trip getaggt (``_finalize_round_trip``).
2. ``slippage_bps_by_asset_class`` — konstanter Aufschlag (die "mindestens"-Variante; die
   bar-spannen-proportionale "besser"-Variante ist NICHT implementiert).
3. Wartungsmargin/Margin-Call — NICHT implementiert (dritte Wiederkehr der #825/#947-Scope-Grenze,
   siehe Kommentar an ``engine.add_venue()``).
4. ``full_realism`` — sechste Kostenstress-Stufe (``expectancy_round_trip_cost_stress_full_realism``),
   zieht Finanzierung + Slippage VOLL (nicht nur den Stress-Delta-Anteil wie die 1.5x/2x-Stufen) von
   der bereits extrahierten Round-Trip-PnL ab.

Finanzierung/Slippage sind bewusst NICHT in der primaeren total_return/sortino-Berechnung enthalten
(nur additive Telemetrie + ``full_realism``) — eine primaere Simulationsaenderung war ohne
``nautilus_trader`` (Python 3.12+, in dieser Sandbox nicht installierbar) nicht end-to-end
verifizierbar.
"""
import pytest


# ── backtest_runner._full_realism_expectancy (reine Funktion) ─────────────────────────────────────
def test_full_realism_matches_capital_weighted_expectancy_at_zero_cost():
    """Bei financing_bps_per_day=0/slippage_bps=0 (backtest.json-Platzhalter-Default) ist
    full_realism numerisch identisch zur unveraenderten kapitalgewichteten Expectancy."""
    from automation.backtest_runner import _full_realism_expectancy
    records = [(100.0, 10000.0, 12 * 3600 * 10**9), (-50.0, 8000.0, 5 * 3600 * 10**9),
               (30.0, 9000.0, 30 * 3600 * 10**9)]
    plain = sum(p for p, _, _ in records) / sum(n for _, n, _ in records)
    result = _full_realism_expectancy(records, financing_bps_per_day=0.0, slippage_bps=0.0)
    assert result == pytest.approx(plain, abs=1e-9)


def test_full_realism_financing_rounds_holding_time_up_to_whole_calendar_days():
    """25h Haltedauer -> ceil(25/24) = 2 Tage Finanzierung (nicht 1, nicht 1.04)."""
    from automation.backtest_runner import _full_realism_expectancy
    records = [(0.0, 10000.0, 25 * 3600 * 10**9)]
    result = _full_realism_expectancy(records, financing_bps_per_day=10.0, slippage_bps=0.0)
    expected = (0.0 - 2 * (10.0 / 10000.0) * 10000.0) / 10000.0
    assert result == pytest.approx(expected, abs=1e-9)


def test_full_realism_financing_exactly_24h_charges_one_day_not_zero():
    from automation.backtest_runner import _full_realism_expectancy
    records = [(0.0, 10000.0, 24 * 3600 * 10**9)]
    result = _full_realism_expectancy(records, financing_bps_per_day=10.0, slippage_bps=0.0)
    expected = (0.0 - 1 * (10.0 / 10000.0) * 10000.0) / 10000.0
    assert result == pytest.approx(expected, abs=1e-9)


def test_full_realism_slippage_applied_to_full_notional():
    from automation.backtest_runner import _full_realism_expectancy
    records = [(0.0, 10000.0, 3600 * 10**9)]
    result = _full_realism_expectancy(records, financing_bps_per_day=0.0, slippage_bps=5.0)
    expected = (0.0 - (5.0 / 10000.0) * 10000.0) / 10000.0
    assert result == pytest.approx(expected, abs=1e-9)


def test_full_realism_none_without_positive_notionals():
    from automation.backtest_runner import _full_realism_expectancy
    assert _full_realism_expectancy(
        [(1.0, 0.0, 100)], financing_bps_per_day=1.0, slippage_bps=1.0) is None


def test_full_realism_respects_dust_notional_floor():
    """Denselben 5-%-Median-Notional-Boden wie _expectancy_cost_stress/expectancy_capital_weighted
    (#1031) — ein Mikro-Trade darf die Kennzahl nicht dominieren."""
    from automation.backtest_runner import _full_realism_expectancy
    records = [(100.0, 10000.0, 3600 * 10**9), (1.0, 10.0, 3600 * 10**9)]
    result = _full_realism_expectancy(records, financing_bps_per_day=0.0, slippage_bps=0.0)
    assert result == pytest.approx(100.0 / 10000.0, abs=1e-9)


# ── backtest_runner.resolve_financing_bps_per_day / resolve_slippage_bps (fail-open) ──────────────
def test_resolve_financing_bps_per_day_fails_open_without_config():
    from automation.backtest_runner import resolve_financing_bps_per_day
    assert resolve_financing_bps_per_day("TSLA.ETORO", None) == 0.0
    assert resolve_financing_bps_per_day("TSLA.ETORO", {}) == 0.0


def test_resolve_financing_bps_per_day_fails_open_on_unknown_asset_class():
    """Anders als resolve_spread_bps/resolve_atr_floor_bps (Konfigurationsfehler, wirft) ist dies
    ein neues, additiv-optionales Kostenmodell — ein unbekannter Key liefert 0.0, kein Raise."""
    from automation.backtest_runner import resolve_financing_bps_per_day
    result = resolve_financing_bps_per_day(
        "X.ETORO", {"EQUITY": 5.0}, asset_class_key="UNKNOWN_CLASS")
    assert result == 0.0


def test_resolve_financing_bps_per_day_returns_configured_value():
    from automation.backtest_runner import resolve_financing_bps_per_day
    result = resolve_financing_bps_per_day(
        "BTC.ETORO", {"CRYPTO": 3.5, "DEFAULT": 1.0}, asset_class_key="CRYPTO")
    assert result == 3.5


def test_resolve_slippage_bps_fails_open_without_config():
    from automation.backtest_runner import resolve_slippage_bps
    assert resolve_slippage_bps("TSLA.ETORO", None) == 0.0


def test_resolve_slippage_bps_returns_configured_value():
    from automation.backtest_runner import resolve_slippage_bps
    result = resolve_slippage_bps("TSLA.ETORO", {"EQUITY": 0.5}, asset_class_key="EQUITY")
    assert result == 0.5


# ── report._study_record full_realism field (end-to-end) ──────────────────────────────────────────
def _make_study(storage_url: str, study_name: str, n: int = 3):
    import optuna
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
        study.tell(trial, float(i))
    return study


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    from automation.optimizer import report
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_study_record_carries_full_realism_field(wired_storage):
    import json
    from automation.optimizer import report
    proposal = {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
            "oos_expectancy_cost_stress_full_realism": 12.5,
        }},
    }
    out_path = report.generate_sweep_report(
        [proposal], run_id="fullrealismtest1", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_expectancy_cost_stress_full_realism"] == pytest.approx(12.5)
