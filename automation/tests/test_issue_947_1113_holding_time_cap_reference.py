"""Issue #947/#1113 (Katalog #960) — ``check_holding_time_cap`` nennt im Meldungstext eine andere
Referenz als in der Rechnung.

Symptom: der gemeldete Faktor je Offender ist gegen ``cap_s`` (z. B. 97 200 s) gerechnet, der Text
nennt ``hard_threshold_s`` (z. B. 291 600 s, das 3-fache) als "die Zeitbox" — jeder gemeldete Wert
ist gegen die im Text genannte Referenz exakt Faktor ``hard_multiple`` (3.0) zu gross, ueber 16
Offender bit-genau reproduziert (B-9).

Fix: der Text nennt beide Groessen explizit UND benennt den tatsaechlich verwendeten Nenner
(``cap_s``); die Offender-Map erhaelt zusaetzlich ``max_holding_time_s`` im Klartext (via
``provenance``), damit kein Leser zurueckrechnen muss.
"""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, max_holding_time_s):
    return {"strategy": strategy, "symbol": symbol, "max_holding_time_s": max_holding_time_s}


def test_reported_factor_times_cap_s_equals_max_holding_time_s():
    """Akzeptanzkriterium #947: fuer jeden Offender rechnet gemeldeter Faktor * cap_s ==
    max_holding_time_s bis auf Rundung (2 Nachkommastellen im Faktor)."""
    cap_s = 97_200.0  # (24 + 3) Bars * 3600s, Default-Konfiguration
    records = [
        _study("AdxAtrMomentumStrategy", "NVDA.ETORO", cap_s * 3.7),
        _study("SqueezeBreakoutStrategy", "TSLA.ETORO", cap_s * 5.2),
    ]
    result = inv.check_holding_time_cap(records)
    assert result.passed is False
    for key, factor in result.actual.items():
        max_holding_time_s = result.provenance["magnitude_offenders_max_holding_time_s"][key]
        assert abs(factor * cap_s - max_holding_time_s) < 0.05 * cap_s  # Rundung auf 2 Nachkommastellen


def test_detail_text_names_the_denominator_actually_used():
    """Der Text muss cap_s EXPLIZIT als den verwendeten Nenner benennen, nicht nur
    hard_threshold_s."""
    cap_s = 97_200.0
    records = [_study("A", "B.ETORO", cap_s * 4.0)]
    result = inv.check_holding_time_cap(records)
    assert result.passed is False
    assert "cap_s=97200s" in result.detail
    assert "hard_threshold_s=291600s" in result.detail
    assert "NICHT von hard_threshold_s" in result.detail


def test_provenance_carries_max_holding_time_s_in_plain_text():
    cap_s = 97_200.0
    records = [_study("A", "B.ETORO", cap_s * 4.0)]
    result = inv.check_holding_time_cap(records)
    assert result.provenance["magnitude_offenders_max_holding_time_s"]["A/B.ETORO"] == cap_s * 4.0
    assert result.provenance["magnitude_cap_s"] == cap_s
    assert result.provenance["magnitude_hard_threshold_s"] == cap_s * 3.0


def test_b9_reference_scenario_factor_is_computed_against_cap_s_not_hard_threshold():
    """Reproduziert B-9: eine Study mit max_holding_time_s knapp ueber hard_threshold_s (291 600s)
    muss einen Faktor nahe 3.0 (gegen cap_s) melden, NICHT nahe 1.0 (was ein Leser faelschlich aus
    dem alten Text -- 'Vielfaches der Zeitbox (291600s)' -- ableiten wuerde)."""
    cap_s = 97_200.0
    hard_threshold_s = cap_s * 3.0
    records = [_study("SqueezeBreakoutStrategy", "PLTR.ETORO", hard_threshold_s * 1.05)]
    result = inv.check_holding_time_cap(records)
    factor = result.actual["SqueezeBreakoutStrategy/PLTR.ETORO"]
    assert factor > 3.0  # gegen cap_s, nicht gegen hard_threshold_s (waere sonst ~1.05)


def test_passes_below_hard_threshold():
    cap_s = 97_200.0
    records = [_study("A", "B.ETORO", cap_s * 2.9)]  # unter hard_multiple=3.0
    result = inv.check_holding_time_cap(records)
    assert result.passed is True
