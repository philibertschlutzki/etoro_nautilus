"""Issue #1075/#1223 (Katalog #1247+, P0) — ``full_realism`` wendet die kalibrierte Slippage auf
dem Symbol-Override-Pfad nicht an.

Symptom: ``capital_weighted − full_realism`` = 0,000 bps in 42/42 TSLA-Studies, gegen 45,8-115,5 bps
auf den sieben uebrigen Symbolen. TSLA ist das einzige Symbol mit ``spread_bps_by_symbol`` in
``backtest.json``.

Root-Cause: ``run_single_backtest_worker`` loeste ``asset_class_key`` NUR auf, wenn
``not has_symbol_override`` — ein Symbol mit NUR einem Spread-Override (kein eigener Override-
Mechanismus fuer Finanzierung/Slippage) behielt ``asset_class_key = "DEFAULT"``, wodurch
``resolve_financing_bps_per_day``/``resolve_slippage_bps`` die DEFAULT-Kostenbasis statt der
echten Asset-Class trafen — die kalibrierte Slippage (#1204) erreichte ein solches Symbol nie.

Fix:
1. Die Asset-Class wird jetzt IMMER aufgeloest, sobald irgendeine asset-class-basierte Kostenkarte
   konfiguriert ist -- unabhaengig von einem Spread-Symbol-Override (der NUR den Spread ersetzt).
2. ``applied_slippage_bps``/``applied_financing_bps_per_day`` je Study gestempelt (die tatsaechlich
   angewandten, nicht die konfigurierten Werte).
3. Neue Invariante ``check_applied_cost_components_resolved`` (severity ``blocking``).
"""
import inspect

from automation import backtest_runner
from automation.optimizer import invariants as inv


def _run_single_backtest_worker_source() -> str:
    return inspect.getsource(backtest_runner.run_single_backtest_worker)


def test_asset_class_resolution_no_longer_skipped_for_symbols_with_a_spread_override():
    """Regressionstest gegen die konkrete Root-Cause: die Bedingung, die asset_class_key aufloest,
    darf NICHT mehr an 'not has_symbol_override' haengen. Nur AUSFUEHRBARE Zeilen werden geprueft
    (Kommentare/Docstrings, die die alte Bedingung zur Dokumentation zitieren, sind erlaubt)."""
    code_lines = [
        line for line in _run_single_backtest_worker_source().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    offending = [line for line in code_lines if "and not has_symbol_override" in line]
    assert not offending, (
        "Die Asset-Class-Aufloesung haengt noch an 'not has_symbol_override' — ein Symbol mit "
        f"NUR einem Spread-Override wuerde Finanzierung/Slippage weiterhin auf DEFAULT verrechnen "
        f"(#1075/#1223-Regression): {offending}"
    )


def test_slippage_p50_asset_class_map_is_part_of_the_resolution_trigger():
    """Zweite Instanz derselben Luecke (Fix Punkt 1, Nebenbefund): slippage_bps_p50_by_asset_class
    fehlte in der urspruenglichen Trigger-Bedingung fuer die Asset-Class-Aufloesung."""
    source = _run_single_backtest_worker_source()
    assert "slippage_bps_p50_by_asset_class):" in source or "or slippage_bps_p50_by_asset_class" in source


def test_applied_cost_components_are_stamped_in_extract_metrics():
    # Issue #1349 (GH #1243, P2) — financing_bps_per_day wurde durch financing_bps_per_day_long/
    # _short ersetzt (Richtungsaufloesung); die gestempelte Study-weite Groesse ist seit diesem Fix
    # _financing_bps_per_day_representative (long/short je nachdem, ob die Study Short-Round-Trips
    # enthielt, siehe dortiger Kommentar).
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert '_level_metrics["applied_slippage_bps"] = slippage_bps' in source
    assert ('_level_metrics["applied_financing_bps_per_day"] = '
           '_financing_bps_per_day_representative') in source


# --- invariants.check_applied_cost_components_resolved ------------------------------------------

def _record(*, applied_slippage_bps, slippage_p50_bps_calibrated=None, trades=10,
            strategy="S", symbol="SYM.ETORO"):
    r = {"strategy": strategy, "symbol": symbol, "holdout_total_trades": trades,
         "applied_slippage_bps": applied_slippage_bps}
    if slippage_p50_bps_calibrated is not None:
        r["slippage_p50_bps_calibrated"] = slippage_p50_bps_calibrated
    return r


def test_passes_when_applied_slippage_matches_a_real_calibration():
    records = [_record(applied_slippage_bps=24.03, slippage_p50_bps_calibrated=24.76)]
    result = inv.check_applied_cost_components_resolved(records)
    assert result.passed is True


def test_fails_when_applied_slippage_is_missing():
    records = [_record(applied_slippage_bps=None)]
    result = inv.check_applied_cost_components_resolved(records)
    assert result.passed is False
    assert "S/SYM.ETORO" in result.provenance["missing"]


def test_fails_reproducing_the_tsla_signature_zero_despite_calibration():
    """Reproduziert exakt das #1223-Symptom: eine kalibrierte Slippage existiert fuer die
    Asset-Klasse dieses Symbols (slippage_p50_bps_calibrated > 0), aber applied_slippage_bps == 0
    (der Symbol-Override-Pfad hat sie nie erreicht)."""
    records = [_record(applied_slippage_bps=0.0, slippage_p50_bps_calibrated=24.03,
                        strategy="AdxAtrMomentumStrategy", symbol="TSLA.ETORO")]
    result = inv.check_applied_cost_components_resolved(records)
    assert result.passed is False
    offender = result.provenance["zero_despite_calibration"]["AdxAtrMomentumStrategy/TSLA.ETORO"]
    assert offender["applied_slippage_bps"] == 0.0
    assert offender["slippage_p50_bps_calibrated"] == 24.03


def test_zero_applied_slippage_without_any_calibration_is_not_an_offender():
    """Ein Symbol ohne jede Kalibrierung (kein Cache fuer seine Asset-Klasse) darf legitim
    applied_slippage_bps == 0.0 tragen -- das ist kein #1223-Symptom."""
    records = [_record(applied_slippage_bps=0.0, slippage_p50_bps_calibrated=None)]
    result = inv.check_applied_cost_components_resolved(records)
    assert result.passed is True


def test_two_symbols_same_asset_class_one_with_one_without_spread_override_are_symmetric():
    """Akzeptanzkriterium — zwei Symbole derselben Asset-Klasse (eines mit, eines ohne
    spread_bps_by_symbol) erhalten identische applied_slippage_bps, sobald beide dieselbe
    kalibrierte Slippage tragen."""
    records = [
        _record(applied_slippage_bps=24.03, slippage_p50_bps_calibrated=24.03,
                strategy="S", symbol="TSLA.ETORO"),  # hat einen Spread-Override
        _record(applied_slippage_bps=24.03, slippage_p50_bps_calibrated=24.03,
                strategy="S", symbol="GOOGL.ETORO"),  # kein Spread-Override
    ]
    result = inv.check_applied_cost_components_resolved(records)
    assert result.passed is True
    assert records[0]["applied_slippage_bps"] == records[1]["applied_slippage_bps"]


def test_not_applicable_without_holdout_trades():
    result = inv.check_applied_cost_components_resolved([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.detail.startswith("Keine Studies")
