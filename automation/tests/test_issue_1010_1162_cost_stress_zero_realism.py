"""Issue #1010/#1162 (Katalog #1170, P0) — 'full_realism'-Kostenstufe ist auf 0,0 konfiguriert und
damit ein No-Op.

Symptom (B-11): ``holdout_expectancy_cost_stress_full_realism == holdout_expectancy_capital_
weighted`` bit-exakt in 26/26.

Root-Cause: ``backtest.json``: ``overnight_financing_bps_per_day_by_asset_class`` und
``slippage_bps_by_asset_class`` sind fuer alle Asset-Klassen 0,0. Der Mechanismus (#987/#1141) ist
korrekt implementiert, aber ohne Saetze wirkungslos.

Fix (Kalibrierung ausdruecklich NICHT Teil dieses Fixes):
1. Neue Invariante ``check_cost_stress_distinctness`` (severity ``high``): FAIL, wenn
   ``full_realism`` in > 90 % der Studies mit >= 1 Trade identisch zur Basis ist.
2. Startup-Validierung ``sweep.warn_if_cost_model_zero_realism``: sind alle Finanzierungs- UND
   Slippage-Saetze 0,0, wird ein ``COST_MODEL_ZERO_REALISM``-WARNING emittiert.
3. ``summary_de.py`` Abschnitt 2.4 nennt denselben Hinweis explizit.
"""
import json
import logging

from automation.optimizer import invariants as inv
from automation.optimizer import summary_de as sde
from automation.optimizer import sweep


# --- invariants.check_cost_stress_distinctness -----------------------------------------------------

def _record(exp, full_realism, trades=10):
    return {"holdout_total_trades": trades, "holdout_expectancy_capital_weighted": exp,
            "holdout_expectancy_cost_stress_full_realism": full_realism}


def test_reference_symptom_fails_when_full_realism_is_bit_identical_to_the_base_in_26_of_26():
    records = [_record(-21.57, -21.57) for _ in range(26)]
    result = inv.check_cost_stress_distinctness(records)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == 1.0


def test_passes_once_full_realism_diverges_in_at_least_90_percent_of_studies():
    """Akzeptanzkriterium 3: sobald reale Saetze > 0 gesetzt sind, weicht full_realism in >= 90 %
    der Studies von der Basis ab -- hier direkt am Grenzfall (exactly 90% distinct) simuliert.

    Issue #1074/#1222 Fix — das Bit-Identitaets-Kriterium allein (ohne kalibrierte Slippage) ist
    seit diesem Fix INCONCLUSIVE, nicht mehr ``passed=True`` (siehe
    ``test_bit_identity_pass_without_any_calibration_is_inconclusive_not_true`` unten). Die 9
    divergierenden Studies tragen deshalb jetzt zusaetzlich kalibrierte Slippage + den holdout-
    skopierten Zaehler (Delta 0,05 uebersteigt das Mindestdelta 0,001 deutlich — kein Offender).
    Die EINE bit-identische Study bleibt bewusst UNKALIBRIERT: mit echter Kalibrierung waere ein
    Delta von exakt 0 dort ein GENUINER #1204-Befund (das Mindestdelta-Kriterium wuerde zurecht
    failen) — dieser Grenzfalltest prueft ausschliesslich das Bit-Identitaets-Kriterium selbst."""
    def _calibrated_record(exp, full_realism, trades=10):
        r = _record(exp, full_realism, trades=trades)
        r["slippage_p50_bps_calibrated"] = 20.0
        r["holdout_n_trailing_stop_exits"] = trades
        return r
    records = ([_calibrated_record(-20.0, -20.05) for _ in range(9)]
               + [_record(-20.0, -20.0)])
    result = inv.check_cost_stress_distinctness(records)
    assert result.passed is True
    assert result.evaluable is True


def test_bit_identity_pass_without_any_calibration_is_inconclusive_not_true():
    """Issue #1074/#1222 (Katalog #1247+) Fix Punkt 3 — ein leerer Kalibrierungs-Cache darf nicht
    wie ein sauberer PASS aussehen: besteht das Bit-Identitaets-Kriterium, aber KEINE Study traegt
    eine kalibrierte Slippage, ist das Ergebnis INCONCLUSIVE (``passed=None``, ``evaluable=False``),
    nicht ``passed=True`` (kein Fail-open mit einem sweep-weiten Ersatz)."""
    records = [_record(-20.0, -20.05) for _ in range(9)] + [_record(-20.0, -20.0)]
    result = inv.check_cost_stress_distinctness(records)
    assert result.passed is None
    assert result.evaluable is False
    assert result.evaluability["inconclusive_reason"] == "NO_CALIBRATED_SLIPPAGE"
    assert result.evaluability["n_studies_with_calibration"] == 0


def test_studies_without_trades_are_excluded_from_the_denominator():
    records = [_record(-20.0, -20.0, trades=0) for _ in range(50)]
    result = inv.check_cost_stress_distinctness(records)
    assert result.passed is True
    assert result.detail.startswith("Keine Studies")


def test_not_applicable_when_neither_field_is_populated():
    result = inv.check_cost_stress_distinctness([{"holdout_total_trades": 5}])
    assert result.passed is True


def test_check_is_wired_into_the_report():
    from automation.optimizer import report as rpt
    source = rpt.__file__
    text = open(source, encoding="utf-8").read()
    assert "_inv.check_cost_stress_distinctness(" in text


# --- sweep.warn_if_cost_model_zero_realism ---------------------------------------------------------

class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            try:
                self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
            except Exception:
                pass


def _capture_warn_call(monkeypatch, tmp_path, backtest_cfg):
    (tmp_path / "backtest.json").write_text(json.dumps(backtest_cfg), "utf-8")
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        fired = sweep.warn_if_cost_model_zero_realism()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return fired, [e for e in handler.events if e.get("event_type") == "COST_MODEL_ZERO_REALISM"]


def test_warns_when_all_financing_and_slippage_rates_are_zero(monkeypatch, tmp_path):
    fired, events = _capture_warn_call(monkeypatch, tmp_path, {
        "overnight_financing_bps_per_day_by_asset_class": {
            "CRYPTO": 0.0, "EQUITY": 0.0, "FOREX": 0.0, "COMMODITY": 0.0, "DEFAULT": 0.0},
        "slippage_bps_by_asset_class": {
            "CRYPTO": 0.0, "EQUITY": 0.0, "FOREX": 0.0, "COMMODITY": 0.0, "DEFAULT": 0.0},
    })
    assert fired is True
    assert len(events) == 1
    assert "ohne Overnight-Finanzierung, ohne Slippage, ohne Market Impact" in events[0]["detail"]


def test_no_warning_once_at_least_one_rate_is_calibrated(monkeypatch, tmp_path):
    fired, events = _capture_warn_call(monkeypatch, tmp_path, {
        "overnight_financing_bps_per_day_by_asset_class": {"EQUITY": 0.5, "DEFAULT": 0.0},
        "slippage_bps_by_asset_class": {"EQUITY": 0.0, "DEFAULT": 0.0},
    })
    assert fired is False
    assert events == []


def test_no_warning_when_backtest_json_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    assert sweep.warn_if_cost_model_zero_realism() is False


def test_the_shipped_backtest_json_no_longer_triggers_the_warning():
    """Issue #1348/#1349 (GH #1242/#1243) — die ehemals ALLE-0.0-Platzhalter sind seit diesem Fix
    strukturell > 0 (slippage: 0.5*spread-Floor; financing: short-Satz > 0), sodass die
    'full_realism'-Kostenstress-Stufe kein reines No-Op mehr ist. Der Warnungs-Check MUSS diesen
    (bewusst gewaehlten, begruendeten) Long-Nullwert korrekt aus einem {'long','short'}-Dict lesen,
    nicht als reinen Skalar — siehe sweep._flatten_cost_rate_values."""
    from automation.optimizer.sweep import _flatten_cost_rate_values
    cfg = json.loads((__import__("pathlib").Path("automation/config/backtest.json"))
                      .read_text("utf-8"))
    financing = cfg["overnight_financing_bps_per_day_by_asset_class"]
    slippage = cfg["slippage_bps_by_asset_class"]
    all_values = _flatten_cost_rate_values(financing) + _flatten_cost_rate_values(slippage)
    assert any(v > 0.0 for v in all_values)
    assert any(v == 0.0 for v in all_values)  # der begruendete Long-Nullwert bleibt bestehen


# --- report._cost_model_has_zero_realism / summary_de.py Abschnitt 2.4 ----------------------------

def test_report_helper_reads_the_same_backtest_json(monkeypatch, tmp_path):
    from automation.optimizer import report as rpt
    (tmp_path / "backtest.json").write_text(json.dumps({
        "overnight_financing_bps_per_day_by_asset_class": {"DEFAULT": 0.0},
        "slippage_bps_by_asset_class": {"DEFAULT": 0.0},
    }), "utf-8")
    assert rpt._cost_model_has_zero_realism(tmp_path) is True

    (tmp_path / "backtest.json").write_text(json.dumps({
        "overnight_financing_bps_per_day_by_asset_class": {"DEFAULT": 1.2},
        "slippage_bps_by_asset_class": {"DEFAULT": 0.0},
    }), "utf-8")
    assert rpt._cost_model_has_zero_realism(tmp_path) is False


def test_summary_section_2_4_carries_the_exact_required_caveat_phrase():
    report = {
        "cross_study": {"cost_model_zero_realism": True},
        "studies": [], "run_id": "r1",
    }
    section = sde._section_2_monetary_result(report)
    assert "ohne Overnight-Finanzierung, ohne Slippage, ohne Market Impact" in section


def test_summary_section_2_4_omits_the_caveat_when_the_cost_model_is_calibrated():
    report = {
        "cross_study": {"cost_model_zero_realism": False},
        "studies": [], "run_id": "r1",
    }
    section = sde._section_2_monetary_result(report)
    assert "ohne Overnight-Finanzierung" not in section
