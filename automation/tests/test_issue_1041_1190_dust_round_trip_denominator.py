"""Issue #1041/#1190 (P3) — ``check_dust_round_trip_share`` normiert auf die behaltene Menge.

Symptom. ``expected`` deklariert ``dust_round_trips_filtered / oos_total_trades_with_exit_
telemetry``. ``oos_total_trades_with_exit_telemetry`` ist die Menge NACH dem Filter -- der so
gebildete Quotient ist kein Anteil und kann > 1 werden. Nachgerechnet: AdxAtr 1800/63241 = 0,02846
(gemeldet 0,0285) bestätigt die Formel; der korrekte Anteil wäre 1800/65041 = 0,02768.

Fix. Nenner auf ``oos_total_trades_with_exit_telemetry + dust_round_trips_filtered`` umgestellt;
die Schwelle bleibt unverändert (die Verschiebung ist bei den heutigen Werten < 10 % relativ und
ändert keine Klassifikation).
"""
import pytest

from automation.optimizer import invariants as inv


def test_adxatr_reference_value_matches_the_corrected_formula():
    """Issue-Referenzwert: AdxAtr 1800 Dust-Legs, 63241 behaltene Round-Trips nach dem Filter.
    Die alte Formel lieferte 1800/63241 = 0,02846 (bestaetigt); der korrekte Anteil an der VOLLEN
    (ungefilterten) Population ist 1800/65041 = 0,02768."""
    records = [{"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
               "dust_round_trips_filtered": 1800, "oos_total_trades_with_exit_telemetry": 63241}]
    result = inv.check_dust_round_trip_share(records, max_share=0.01)
    assert result.actual["AdxAtrMomentumStrategy/TSLA.ETORO"] == pytest.approx(1800 / 65041, abs=1e-4)
    old_wrong_value = 1800 / 63241
    assert result.actual["AdxAtrMomentumStrategy/TSLA.ETORO"] < old_wrong_value


# ── Akzeptanzkriterium #1041: der Quotient liegt per Konstruktion in [0, 1] ───────────────────────
def test_quotient_stays_below_one_even_when_dust_exceeds_kept_count():
    """Extremfall (dust > kept): mit der ALTEN Formel (Nenner = nur behaltene Menge) waere der
    Quotient > 1 -- strukturell unmoeglich fuer einen Anteil. Mit dem korrigierten Nenner (behalten
    + dust = die VOLLE Population) bleibt der Quotient IMMER in [0, 1]."""
    records = [{"strategy": "S", "symbol": "X",
               "dust_round_trips_filtered": 500, "oos_total_trades_with_exit_telemetry": 100}]
    result = inv.check_dust_round_trip_share(records, max_share=0.01)
    share = result.actual["S/X"]
    assert 0.0 <= share <= 1.0
    assert share == pytest.approx(500 / 600, abs=1e-4)


def test_zero_dust_and_zero_kept_is_not_applicable_not_a_crash():
    result = inv.check_dust_round_trip_share(
        [{"strategy": "S", "symbol": "X", "dust_round_trips_filtered": 0,
          "oos_total_trades_with_exit_telemetry": 0}])
    assert result.passed is True
    assert result.actual is None


def test_expected_text_documents_the_corrected_denominator():
    result = inv.check_dust_round_trip_share([{"strategy": "S", "symbol": "X"}])
    assert "dust_round_trips_filtered" in result.expected
    assert "oos_total_trades_with_exit_telemetry + dust_round_trips_filtered" in result.expected
