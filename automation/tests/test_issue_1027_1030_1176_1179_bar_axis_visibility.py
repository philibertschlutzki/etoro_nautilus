"""Issue #1027/#1176 Schritt 1 und #1030/#1179 (P1) — Bar-Kalender-Sichtbarkeit ohne Semantikbruch.

#1027/#1176 Schritt 1: die synthetische 1h-Bar-Erzeugung kennt für EQUITY/COMMODITY keine
Handelszeiten-Maske (24/7-Kalender statt RTH) — Schritt 1 macht das je Study SICHTBAR
(bars_per_calendar_day/session_coverage_fraction in Report-Abschnitt 3), ohne die Simulation selbst
zu verändern (Schritt 2 — RTH-Maske + simulation_semantics_version-Bump + Pflicht-Purge — bleibt
bewusst zurückgestellt, siehe AGENTS.md-Sperrvermerk und die offene Frage in Issue #1047 Abschnitt
7.2: "Sind die 24/7-Bars für EQUITY beabsichtigt, oder ist eine RTH-Maske erwünscht?").

#1030/#1179: TIME_BOX ist mit ~49 % der häufigste Exit-Mechanismus überhaupt und misst dieselbe
ungefilterte Kalender-Bar-Achse — bis zur RTH-Umstellung wird der Median zusätzlich in
Handelsstunden (bars · session_coverage_fraction) ausgewiesen.
"""
from automation.optimizer import summary_de


def _report(studies, cross_study=None):
    return {
        "studies": studies,
        "cross_study": cross_study or {},
        "run_status": "complete",
        "started_at_utc": "2026-08-18T14:44:12.342+00:00",
        "wallclock_s": 1755,
        "cli_args": {},
        "symbols_planned": None,
    }


def test_section_3_5_lists_bar_axis_per_study():
    report = _report([
        {"strategy": "DynamicBreakoutStrategy", "symbol": "TSLA.ETORO",
         "bars_per_calendar_day": 24.0, "session_coverage_fraction": 0.2402},
    ])
    text = summary_de._section_3_duration(report)
    assert "Bar-Achse" in text
    assert "24.00" in text or "24,00" in text
    assert "24.0" in text  # bars_per_calendar_day Wert erscheint
    assert "DynamicBreakoutStrategy" in text


def test_section_3_5_absent_without_bar_axis_telemetry():
    report = _report([{"strategy": "A", "symbol": "B.ETORO"}])
    text = summary_de._section_3_duration(report)
    assert "Keine Bar-Achsen-Telemetrie" in text


def test_section_4_1_shows_calendar_and_trading_hours_bars():
    report = _report([
        {"strategy": "A", "symbol": "B.ETORO", "time_box_exit_fraction": 0.4889,
         "median_bars_held": 20.0, "session_coverage_fraction": 0.2402},
    ])
    text = summary_de._section_4_longest_trades(report)
    assert "Zeitbox" in text
    assert "Handels-Bars" in text
    # 20.0 * 0.2402 = 4.804 -> gerundet 4.8
    assert "4.8" in text


def test_section_4_1_absent_without_timebox_telemetry():
    report = _report([{"strategy": "A", "symbol": "B.ETORO"}])
    text = summary_de._section_4_longest_trades(report)
    assert "4.1 Zeitbox" not in text


def test_max_bars_in_trade_domain_registry_documents_the_calendar_axis():
    from automation.optimizer import spaces
    source = open(spaces.__file__, encoding="utf-8").read()
    assert "#1030/#1179" in source
    assert "Kalender-Bars" in source
