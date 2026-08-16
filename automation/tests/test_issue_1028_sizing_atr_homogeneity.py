"""Issue #1028 (Katalog #866) — Sizing-Identitaet + ATR-Skalenhomogenitaet als stehende
Datenintegritaets-Waechter (Pitfall #354). Root-Cause laut Katalog: NICHT der Sizing-/ATR-Code
selbst (der ist korrekt), sondern eine Preisreihen-Anomalie (Split-/Adjustierungsgrenze,
Snapshot-Naht) bei den betroffenen TSLA-Studies — dieser Test deckt daher nur die CODE-Seite ab
(die beiden neuen Invarianten + den Report-Helfer, der ``trade_amount_pct`` je Strategie
aufloest), nicht die Datenlage selbst."""
import json

from automation.optimizer import invariants as inv
from automation.optimizer import report
from automation.optimizer import summary_de as sd


# ── invariants.check_sizing_identity_coherence ──────────────────────────────────────────────────
def test_sizing_identity_passes_when_implied_matches_configured():
    # Referenzrechnung aus dem Katalog: Donchian/ADBE, n=32, expectancy=0.0071, TR=3.3 %
    # -> f_implied ~= 14.3 % gegen konfigurierte 15 % (Faktor < 1.35, also PASS).
    import math
    n, expectancy, total_return = 32, 0.0071, 0.033
    f_implied = (math.log(1.0 + total_return) / (n * expectancy)) * 100.0
    records = [{
        "strategy": "DonchianRegimeBreakoutStrategy", "symbol": "ADBE.ETORO",
        "holdout_total_trades": n, "holdout_expectancy_notional_weighted": expectancy,
        "holdout_total_return": total_return, "trade_amount_pct": 15.0,
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is True
    assert 14.0 < f_implied < 14.6


def test_sizing_identity_fails_on_tsla_style_divergence():
    """Beobachtete Signatur: TSLA-Kandidaten mit f_implied = 0.93 % gegen konfigurierte 15 %
    (Faktor ~16) — eine grosse Abweichung MUSS als Offender gemeldet werden."""
    import math
    n, expectancy = 40, 0.006
    # f_implied auf ~0.93 % zurückgerechnet: ln(1+TR) = f_implied/100 * n * expectancy
    target_f_implied = 0.93
    total_return = math.exp((target_f_implied / 100.0) * n * expectancy) - 1.0
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "holdout_total_trades": n, "holdout_expectancy_notional_weighted": expectancy,
        "holdout_total_return": total_return, "trade_amount_pct": 15.0,
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is False
    assert result.severity == "blocking"
    assert "SqueezeBreakoutStrategy/TSLA.ETORO" in result.actual


def test_sizing_identity_not_applicable_without_data():
    result = inv.check_sizing_identity_coherence([{"strategy": "S", "symbol": "A.ETORO"}])
    assert result.passed is True
    assert result.actual is None


def test_sizing_identity_skips_small_denominators():
    """n < min_trades bzw. |expectancy| < min_abs_expectancy sind Divisionsartefakte ohne
    Information, keine echten Kandidaten — dürfen nicht als Offender auftauchen."""
    records = [{
        "strategy": "S", "symbol": "A.ETORO",
        "holdout_total_trades": 3, "holdout_expectancy_notional_weighted": 1e-6,
        "holdout_total_return": 5.0, "trade_amount_pct": 15.0,
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is True
    assert result.actual is None


# ── invariants.check_atr_scale_homogeneity ──────────────────────────────────────────────────────
def test_atr_scale_homogeneity_passes_within_band():
    records = [
        {"strategy": "A", "symbol": "MSFT.ETORO", "atr_median_bps": 20.0},
        {"strategy": "B", "symbol": "MSFT.ETORO", "atr_median_bps": 45.0},
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.passed is True


def test_atr_scale_homogeneity_fails_on_tsla_style_jump():
    """Beobachtete Signatur: TSLA mit 18.2-366.2 bps (Faktor 20) gegen 2-59 bps auf den
    uebrigen Symbolen."""
    records = [
        {"strategy": "A", "symbol": "TSLA.ETORO", "atr_median_bps": 18.2},
        {"strategy": "B", "symbol": "TSLA.ETORO", "atr_median_bps": 366.2},
        {"strategy": "A", "symbol": "MSFT.ETORO", "atr_median_bps": 20.0},
        {"strategy": "B", "symbol": "MSFT.ETORO", "atr_median_bps": 45.0},
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.passed is False
    assert result.severity == "high"
    assert "TSLA.ETORO" in result.actual
    assert "MSFT.ETORO" not in result.actual


def test_atr_scale_homogeneity_not_applicable_without_multi_strategy_symbol():
    records = [{"strategy": "A", "symbol": "MSFT.ETORO", "atr_median_bps": 20.0}]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.passed is True
    assert result.actual is None


# ── report._trade_amount_pct_by_strategy ────────────────────────────────────────────────────────
def test_trade_amount_pct_by_strategy_reads_defaults(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(json.dumps({
        "_schema": {"description": "..."},
        "SmaCrossoverStrategy": {"trade_amount_pct": 15.0},
        "MeanReversionStrategy": {"trade_amount_pct": 10.0, "lookback": 5},
    }), encoding="utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": []}), encoding="utf-8")

    result = report._trade_amount_pct_by_strategy(tmp_path)
    assert result == {"SmaCrossoverStrategy": 15.0, "MeanReversionStrategy": 10.0}


def test_trade_amount_pct_by_strategy_strategies_json_overrides_defaults(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(json.dumps({
        "SmaCrossoverStrategy": {"trade_amount_pct": 15.0},
    }), encoding="utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps({
        "strategies": [
            {"strategy_class": "SmaCrossoverStrategy", "params": {"trade_amount_pct": 12.5}},
        ],
    }), encoding="utf-8")

    result = report._trade_amount_pct_by_strategy(tmp_path)
    assert result == {"SmaCrossoverStrategy": 12.5}


def test_trade_amount_pct_by_strategy_missing_files_returns_empty(tmp_path):
    result = report._trade_amount_pct_by_strategy(tmp_path)
    assert result == {}


# ── report._max_symbol_exposure_fraction (Issue #1042 E-2) ──────────────────────────────────────
def test_max_symbol_exposure_fraction_reads_live_risk_config(tmp_path):
    (tmp_path / "backtest.json").write_text(json.dumps({
        "live_risk": {"max_symbol_exposure_fraction": 0.10, "max_total_exposure_fraction": 0.6},
    }), encoding="utf-8")
    assert report._max_symbol_exposure_fraction(tmp_path) == 0.10


def test_max_symbol_exposure_fraction_none_without_file(tmp_path):
    assert report._max_symbol_exposure_fraction(tmp_path) is None


def test_max_symbol_exposure_fraction_none_without_live_risk_key(tmp_path):
    (tmp_path / "backtest.json").write_text(json.dumps({}), encoding="utf-8")
    assert report._max_symbol_exposure_fraction(tmp_path) is None


# ── summary_de: Sofortmassnahme 1 — Quarantäne statt Ablehnung in Abschnitt 2.1 ─────────────────────
def _promotion_record(*, strategy, symbol, snapshot_drift):
    return {
        "strategy": strategy, "symbol": symbol, "promotion_outcome": "READY_FOR_PR",
        "holdout_total_return": 0.05, "holdout_expectancy_capital_weighted": 0.01,
        "holdout_win_rate": 0.5,
        "holdout_total_trades": 40,
        "deployment_decision": {
            "admitted": False, "blocking_clause": "snapshot_drift",
            "clause_results": {"snapshot_drift": snapshot_drift},
        },
    }


def test_snapshot_drift_false_candidate_excluded_from_section_2_1_table():
    report_dict = {
        "run_id": "run1",
        "studies": [
            _promotion_record(strategy="FlashCrashReversalStrategy", symbol="TSLA.ETORO",
                               snapshot_drift=False),
            _promotion_record(strategy="DonchianRegimeBreakoutStrategy", symbol="ADBE.ETORO",
                               snapshot_drift=True),
        ],
    }
    text = sd.generate_german_summary(report_dict)
    section_2_1 = text.split("### 2.1 ")[1].split("### 2.1b")[0]
    assert "FlashCrashReversalStrategy" not in section_2_1
    assert "TSLA.ETORO" not in section_2_1
    assert "DonchianRegimeBreakoutStrategy" in section_2_1


def test_snapshot_drift_false_candidate_listed_in_quarantine_section():
    report_dict = {
        "run_id": "run1",
        "studies": [
            _promotion_record(strategy="FlashCrashReversalStrategy", symbol="TSLA.ETORO",
                               snapshot_drift=False),
        ],
    }
    text = sd.generate_german_summary(report_dict)
    quarantine_section = text.split("### 2.1b Quarantäne")[1].split("### 2.2")[0]
    assert "FlashCrashReversalStrategy" in quarantine_section
    assert "TSLA.ETORO" in quarantine_section


def test_no_quarantine_section_entries_when_no_drift():
    report_dict = {
        "run_id": "run1",
        "studies": [
            _promotion_record(strategy="DonchianRegimeBreakoutStrategy", symbol="ADBE.ETORO",
                               snapshot_drift=True),
        ],
    }
    text = sd.generate_german_summary(report_dict)
    quarantine_section = text.split("### 2.1b Quarantäne")[1].split("### 2.2")[0]
    assert "Keine Kandidaten mit nachgewiesenem Datenstand-Bruch" in quarantine_section


def test_snapshot_drift_none_stays_in_regular_table_not_quarantined():
    """``None`` heisst 'nicht geprüft' (fehlender aktueller Snapshot-Hash), nicht 'Drift
    nachgewiesen' — dieser Kandidat bleibt in der regulären Tabelle 2.1 (dort ggf. 'abgelehnt')."""
    report_dict = {
        "run_id": "run1",
        "studies": [
            _promotion_record(strategy="FlashCrashReversalStrategy", symbol="TSLA.ETORO",
                               snapshot_drift=None),
        ],
    }
    text = sd.generate_german_summary(report_dict)
    section_2_1 = text.split("### 2.1 ")[1].split("### 2.1b")[0]
    quarantine_section = text.split("### 2.1b Quarantäne")[1].split("### 2.2")[0]
    assert "FlashCrashReversalStrategy" in section_2_1
    assert "Keine Kandidaten mit nachgewiesenem Datenstand-Bruch" in quarantine_section
