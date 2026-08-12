"""Issue #1028 (Katalog #866) — Sizing-Identitaet + ATR-Skalenhomogenitaet als stehende
Datenintegritaets-Waechter (Pitfall #354). Root-Cause laut Katalog: NICHT der Sizing-/ATR-Code
selbst (der ist korrekt), sondern eine Preisreihen-Anomalie (Split-/Adjustierungsgrenze,
Snapshot-Naht) bei den betroffenen TSLA-Studies — dieser Test deckt daher nur die CODE-Seite ab
(die beiden neuen Invarianten + den Report-Helfer, der ``trade_amount_pct`` je Strategie
aufloest), nicht die Datenlage selbst."""
import json

from automation.optimizer import invariants as inv
from automation.optimizer import report


# ── invariants.check_sizing_identity_coherence ──────────────────────────────────────────────────
def test_sizing_identity_passes_when_implied_matches_configured():
    # Referenzrechnung aus dem Katalog: Donchian/ADBE, n=32, expectancy=0.0071, TR=3.3 %
    # -> f_implied ~= 14.3 % gegen konfigurierte 15 % (Faktor < 1.35, also PASS).
    import math
    n, expectancy, total_return = 32, 0.0071, 0.033
    f_implied = (math.log(1.0 + total_return) / (n * expectancy)) * 100.0
    records = [{
        "strategy": "DonchianRegimeBreakoutStrategy", "symbol": "ADBE.ETORO",
        "holdout_total_trades": n, "holdout_expectancy": expectancy,
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
        "holdout_total_trades": n, "holdout_expectancy": expectancy,
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
        "holdout_total_trades": 3, "holdout_expectancy": 1e-6,
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
