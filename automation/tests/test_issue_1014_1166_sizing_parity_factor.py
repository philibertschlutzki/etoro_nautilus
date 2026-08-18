"""Issue #1014/#1166 (Katalog #1170, P2) — Sizing-Parität Backtest 15,0 % vs. Allocator 10,0 %
über alle 15 Strategien.

Symptom: ``check_sizing_parity_backtest_vs_allocator`` failt in beiden Läufen für alle 15
Strategien mit identischem Delta: ``backtest_trade_amount_pct = 15,0`` gegen
``allocator_max_symbol_pct = 10,0``.

Root-Cause: zwei unabhängig gepflegte Grössen für dieselbe Frage ("wie viel Kapital je Symbol"),
in ``strategy_defaults.json`` und ``backtest.json:live_risk``.

Fix: EINE Quelle. ``trade_amount_pct`` wird über einen expliziten, dokumentierten Faktor
(``backtest.json['live_risk']['trade_amount_pct_parity_factor']``) auf ``max_symbol_exposure_
fraction`` bezogen. ``check_sizing_parity_backtest_vs_allocator`` prüft die Ableitung (``trade_
amount_pct == max_symbol_exposure_fraction * 100 * parity_factor``) statt zweier unabhängiger
Konstanten. Die Wahl des Zielwerts selbst bleibt eine Betreiberentscheidung — der Fix erzwingt
nur, dass es EINE benannte Zahl (den Faktor) statt einer stillen Divergenz gibt.
"""
import json
from pathlib import Path

from automation.optimizer import invariants as inv


# --- invariants.check_sizing_parity_backtest_vs_allocator: parity_factor ---------------------------

def test_default_factor_1_0_preserves_the_legacy_1042_behavior():
    """Regressionswaechter: ohne parity_factor bleibt das alte Verhalten (direkter Vergleich)
    bit-identisch -- bestehende #1042-Tests duerfen sich nicht aendern."""
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"FlashCrashReversalStrategy": 15.0}, max_symbol_exposure_fraction=0.10)
    assert result.passed is False
    assert result.actual["FlashCrashReversalStrategy"]["backtest_trade_amount_pct"] == 15.0
    assert result.actual["FlashCrashReversalStrategy"]["allocator_max_symbol_pct"] == 10.0


def test_the_documented_factor_1_5_makes_the_1166_symptom_pass():
    """Reproduziert das #1166-Symptom (15 Strategien, alle bei 15.0 vs. 10.0) UND zeigt, dass der
    dokumentierte Faktor 1.5 es zu einem PASS macht -- exakt Akzeptanzkriterium 'Der Check PASST
    oder nennt genau eine bewusst dokumentierte Abweichung'."""
    pct_by_strategy = {f"Strategy{i}": 15.0 for i in range(15)}
    result = inv.check_sizing_parity_backtest_vs_allocator(
        pct_by_strategy, max_symbol_exposure_fraction=0.10, parity_factor=1.5)
    assert result.passed is True
    assert result.actual is None


def test_a_genuine_divergence_still_fails_even_with_a_factor_configured():
    """Der Faktor deckt nur die BEHAUPTETE Beziehung -- eine Strategie, die selbst DAVON abweicht,
    bleibt ein echter Befund."""
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"Outlier": 20.0}, max_symbol_exposure_fraction=0.10, parity_factor=1.5)
    assert result.passed is False
    assert result.actual["Outlier"]["allocator_max_symbol_pct"] == 15.0
    assert result.actual["Outlier"]["parity_factor"] == 1.5


def test_expected_text_names_the_parity_factor():
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"S": 15.0}, max_symbol_exposure_fraction=0.10, parity_factor=1.5)
    assert "parity_factor" in result.expected


# --- report.py: der Faktor wird aus backtest.json gelesen -------------------------------------------

def test_report_reads_the_parity_factor_from_backtest_json(tmp_path):
    from automation.optimizer import report as rpt
    (tmp_path / "backtest.json").write_text(json.dumps({
        "live_risk": {"max_symbol_exposure_fraction": 0.10, "trade_amount_pct_parity_factor": 1.5},
    }), "utf-8")
    assert rpt._trade_amount_pct_parity_factor(tmp_path) == 1.5


def test_report_defaults_the_parity_factor_to_1_0_when_absent(tmp_path):
    from automation.optimizer import report as rpt
    (tmp_path / "backtest.json").write_text(json.dumps({
        "live_risk": {"max_symbol_exposure_fraction": 0.10},
    }), "utf-8")
    assert rpt._trade_amount_pct_parity_factor(tmp_path) == 1.0


def test_report_defaults_the_parity_factor_to_1_0_without_a_config_file(tmp_path):
    from automation.optimizer import report as rpt
    assert rpt._trade_amount_pct_parity_factor(tmp_path) == 1.0


def test_check_is_wired_with_the_parity_factor_in_the_report():
    from automation.optimizer import report as rpt
    source = open(rpt.__file__, encoding="utf-8").read()
    assert "parity_factor=_trade_amount_pct_parity_factor()" in source


# --- config: der ausgelieferte Datenstand traegt den dokumentierten Faktor -------------------------

def test_shipped_backtest_json_documents_the_current_1_5_relationship():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    live_risk = cfg["live_risk"]
    assert live_risk["trade_amount_pct_parity_factor"] == 1.5


def test_shipped_config_actually_satisfies_its_own_documented_factor():
    """Der Datenstand selbst ist konsistent: alle strategy_defaults.json trade_amount_pct-Werte
    entsprechen max_symbol_exposure_fraction * 100 * trade_amount_pct_parity_factor -- der Check
    passiert auf dem heutigen Datenstand (Akzeptanzkriterium: PASS oder eine benannte Abweichung)."""
    backtest_cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    live_risk = backtest_cfg["live_risk"]
    strategy_defaults = json.loads(Path("automation/config/strategy_defaults.json").read_text("utf-8"))
    pct_by_strategy = {
        strat: params["trade_amount_pct"]
        for strat, params in strategy_defaults.items()
        if strat != "_schema" and isinstance(params, dict) and "trade_amount_pct" in params
    }
    result = inv.check_sizing_parity_backtest_vs_allocator(
        pct_by_strategy, max_symbol_exposure_fraction=live_risk["max_symbol_exposure_fraction"],
        parity_factor=live_risk["trade_amount_pct_parity_factor"])
    assert result.passed is True
