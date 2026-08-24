"""Issue #1077/#1225 (P1) — ``cost_model_zero_realism`` wurde aus der Konfiguration statt aus den
angewandten Werten abgeleitet.

Symptom: das Flag war in 11/11 Läufen ``true``, und §2.4 warnte entsprechend, dass die
Kostenstress-Stufe ``full_realism`` ein No-Op sei. Auf sieben von acht Symbolen zog ``full_realism``
tatsächlich 45,8–115,5 bps ab — die Warnung war dort falsch; auf TSLA war sie richtig, aber aus dem
falschen Grund.

Root-Cause: das Flag las ``backtest.json['slippage_bps_by_asset_class']``/
``['overnight_financing_bps_per_day_by_asset_class']`` — die KONFIGURIERTEN Platzhalter. Seit
#1055/#1204 stammt die real angewandte Slippage aus dem Kalibrierungs-Cache, nicht aus dieser
Konfiguration.

Fix: ``cost_model_zero_realism`` wird aus den mit #1075/#1223 gestempelten ``applied_*``-Feldern
JEDER Study abgeleitet: ``true`` genau dann, wenn für JEDE Study sowohl ``applied_slippage_bps`` als
auch ``applied_financing_bps_per_day`` 0,0 sind. Zusätzlich ``cost_model_realism_source`` ausgewiesen
(``config_zero`` / ``calibrated_cache`` / ``mixed``); bei ``mixed`` werden die betroffenen Symbole in
``cost_model_zero_realism_symbols`` benannt. §2.4-Text ist an das Flag gekoppelt: der volle
Warnblock erscheint nur noch bei ``config_zero``, ``mixed`` nennt die betroffenen Symbole namentlich,
``calibrated_cache`` zeigt keine Warnung.
"""
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde


def _study(strategy, symbol, *, slippage, financing):
    return {
        "strategy": strategy, "symbol": symbol,
        "applied_slippage_bps": slippage, "applied_financing_bps_per_day": financing,
    }


# --- report._cost_model_realism_from_applied: die drei Zustände --------------------------------

def test_all_studies_zero_yields_config_zero():
    studies = [
        _study("A", "X.ETORO", slippage=0.0, financing=0.0),
        _study("B", "Y.ETORO", slippage=0.0, financing=0.0),
    ]
    zero_realism, source, symbols = rpt._cost_model_realism_from_applied(studies)
    assert zero_realism is True
    assert source == "config_zero"
    assert symbols == []


def test_no_studies_zero_yields_calibrated_cache():
    studies = [
        _study("A", "X.ETORO", slippage=4.2, financing=0.5),
        _study("B", "Y.ETORO", slippage=3.1, financing=0.3),
    ]
    zero_realism, source, symbols = rpt._cost_model_realism_from_applied(studies)
    assert zero_realism is False
    assert source == "calibrated_cache"
    assert symbols == []


def test_reference_batch_reproduces_mixed_with_tsla_named():
    """Akzeptanzkriterium: auf dem archivierten Batch ergibt sich 'mixed' mit TSLA als benanntem
    Symbol -- sieben Symbole tragen reale Slippage/Finanzierung, TSLA (Symbol-Override-Luecke,
    #1075/#1223) bleibt bei 0,0."""
    calibrated_symbols = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "PLTR"]
    studies = [
        _study("Strat", f"{sym}.ETORO", slippage=5.0, financing=0.4) for sym in calibrated_symbols
    ] + [_study("Strat", "TSLA.ETORO", slippage=0.0, financing=0.0)]
    zero_realism, source, symbols = rpt._cost_model_realism_from_applied(studies)
    assert zero_realism is False
    assert source == "mixed"
    assert symbols == ["Strat/TSLA.ETORO"]


def test_studies_without_resolved_applied_fields_are_excluded_from_classification():
    """Eine Study ohne aufgeloeste applied_*-Felder (z. B. keine Holdout-Trades) darf die
    Klassifikation der uebrigen Studies nicht verfaelschen."""
    studies = [
        _study("A", "X.ETORO", slippage=4.2, financing=0.5),
        {"strategy": "B", "symbol": "Y.ETORO"},  # applied_* fehlt vollstaendig
    ]
    zero_realism, source, symbols = rpt._cost_model_realism_from_applied(studies)
    assert zero_realism is False
    assert source == "calibrated_cache"


def test_no_classifiable_study_falls_back_to_the_legacy_config_based_heuristic(tmp_path):
    """Ohne EINE Study mit aufgeloesten applied_*-Feldern (z. B. ein Report ohne Holdout-Trades)
    faellt die Erkennung auf die vormalige konfigurationsbasierte Heuristik zurueck -- niemals
    'mixed' ohne mindestens zwei klassifizierbare Studies."""
    import json
    (tmp_path / "backtest.json").write_text(json.dumps({
        "overnight_financing_bps_per_day_by_asset_class": {"DEFAULT": 0.0},
        "slippage_bps_by_asset_class": {"DEFAULT": 0.0},
    }), "utf-8")
    zero_realism, source, symbols = rpt._cost_model_realism_from_applied([], tmp_path)
    assert zero_realism is True
    assert source == "config_zero"
    assert symbols == []


# --- summary_de.py Abschnitt 2.4: an cost_model_realism_source gekoppelt -----------------------

def _report(cross_study_extra):
    return {"cross_study": cross_study_extra, "studies": [], "run_id": "r1"}


def test_section_2_4_shows_the_full_warning_only_for_config_zero():
    report = _report({
        "cost_model_zero_realism": True, "cost_model_realism_source": "config_zero",
        "cost_model_zero_realism_symbols": [],
    })
    section = sde._section_2_monetary_result(report)
    assert "ohne Overnight-Finanzierung, ohne Slippage, ohne Market Impact" in section
    assert "Kalibrierungslücke (teilweise)" not in section


def test_section_2_4_shows_no_warning_for_calibrated_cache():
    report = _report({
        "cost_model_zero_realism": False, "cost_model_realism_source": "calibrated_cache",
        "cost_model_zero_realism_symbols": [],
    })
    section = sde._section_2_monetary_result(report)
    assert "Kalibrierungslücke" not in section


def test_section_2_4_names_the_affected_symbols_for_mixed():
    report = _report({
        "cost_model_zero_realism": False, "cost_model_realism_source": "mixed",
        "cost_model_zero_realism_symbols": ["Strat/TSLA.ETORO"],
    })
    section = sde._section_2_monetary_result(report)
    assert "Kalibrierungslücke (teilweise)" in section
    assert "Strat/TSLA.ETORO" in section
    # Der VOLLE (pauschale) Warnblock aus dem config_zero-Fall darf hier nicht erscheinen.
    assert "für alle Asset-Klassen mit 0,0 bps angewandt" not in section


def test_section_2_4_backward_compatible_with_pre_1225_reports_carrying_only_the_bool():
    """Ein Report-JSON vor #1077/#1225 traegt nur das alte Bool-Feld, kein
    cost_model_realism_source -- muss sich weiterhin wie 'config_zero' verhalten."""
    report = _report({"cost_model_zero_realism": True})
    section = sde._section_2_monetary_result(report)
    assert "ohne Overnight-Finanzierung, ohne Slippage, ohne Market Impact" in section
