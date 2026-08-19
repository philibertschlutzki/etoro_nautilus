"""Issue #1022/#1171 (P0) — ``bar_range_median_bps`` erreichte den Round-Trip-Datensatz nie.

Root-Cause: ``_parse_exit_order_tags`` parst den ``BAR_RANGE_MEDIAN_BPS``-Tag korrekt in
``meta["bar_range_median_bps"]`` (siehe dortiger Docstring), und die Aggregation weiter unten in
``extract_metrics`` liest ``m.get("bar_range_median_bps")`` korrekt aus jedem ``rt_exit_meta``-
Eintrag — aber das je-Round-Trip aufgebaute ``rt_exit_meta.append({...})``-Dict liess genau diesen
Key aus. Zähler und Nenner beider Enden der Kette existierten, die BRÜCKE dazwischen fehlte: 0/14
Studies hatten den Wert befüllt, ``check_stop_loss_vs_bar_range`` (severity ``blocking``) war
dadurch strukturell IMMER INCONCLUSIVE (Pitfall #421 in AGENTS.md: ein je Round-Trip aufgebautes
Meta-Dict ist eine eigene Datenschranke).
"""
import inspect
import re

from automation import backtest_runner

# Issue #1022/#1171 Fix — Vollstaendigkeitstest gegen genau diese Regressionsklasse: ein neuer, von
# ``_parse_exit_order_tags`` geparster Tag muss ENTWEDER direkt unter demselben Schluessel in
# ``rt_exit_meta.append({...})`` landen, ODER hier explizit als "wird zu einem abgeleiteten Feld
# verarbeitet" begruendet werden — sonst wiederholt die naechste Tag-Erweiterung dieselbe Lücke.
_CONSUMED_INTO_DERIVED_FIELD = {
    # Issue #976/#1130 — fliesst in stop_exit_fill_lag_ns (rt_exit_ts - order_submit_ts_ns), nicht
    # als eigener Rohwert.
    "order_submit_ts_ns": "stop_exit_fill_lag_ns (Differenz zu rt_exit_ts)",
    # Issue #976/#1130 — fliesst als Referenzpreis in stop_exit_slippage_bps, nicht als eigener
    # Rohwert.
    "trailing_stop_price": "stop_exit_slippage_bps (Referenzpreis fuer die Slippage-Berechnung)",
}


def _keys_set_by_parse_exit_order_tags() -> set[str]:
    source = inspect.getsource(backtest_runner._parse_exit_order_tags)
    return set(re.findall(r'meta\["([a-z_0-9]+)"\]\s*=', source))


def _keys_in_rt_exit_meta_dict_literal() -> set[str]:
    source = inspect.getsource(backtest_runner.extract_metrics)
    match = re.search(r"rt_exit_meta\.append\(\{(.*?)\n\s*\}\)", source, re.DOTALL)
    assert match, "rt_exit_meta.append({...}) literal nicht gefunden — extract_metrics umgebaut?"
    return set(re.findall(r'"([a-z_0-9]+)":', match.group(1)))


def test_bar_range_median_bps_reaches_rt_exit_meta():
    assert "bar_range_median_bps" in _keys_in_rt_exit_meta_dict_literal()


def test_every_parsed_exit_tag_key_reaches_rt_exit_meta_or_is_documented_as_consumed():
    parsed_keys = _keys_set_by_parse_exit_order_tags()
    rt_exit_meta_keys = _keys_in_rt_exit_meta_dict_literal()
    missing = parsed_keys - rt_exit_meta_keys - set(_CONSUMED_INTO_DERIVED_FIELD)
    assert not missing, (
        f"Von _parse_exit_order_tags geparste Key(s) erreichen rt_exit_meta nicht und sind auch "
        f"nicht in _CONSUMED_INTO_DERIVED_FIELD begruendet: {sorted(missing)} (Pitfall #421 — "
        "das genaue Symptom von #1022/#1171)."
    )


def test_bar_range_median_bps_tag_parses_and_flows_through_rt_exit_meta_dict_shape():
    # End-to-end auf der reinen Parsing-/Dict-Aufbau-Ebene (keine echte Engine noetig): der Tag
    # wird geparst, UND ein rt_exit_meta-Eintrag, der ihn enthaelt, liefert einen Median > 0 in der
    # Aggregation weiter unten in extract_metrics (dieselbe ``m.get("bar_range_median_bps")``-
    # Bedingung, die zuvor strukturell nie True wurde).
    meta = backtest_runner._parse_exit_order_tags(["BAR_RANGE_MEDIAN_BPS:12.5"])
    assert meta["bar_range_median_bps"] == 12.5
