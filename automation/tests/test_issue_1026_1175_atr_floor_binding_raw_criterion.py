"""Issue #1026/#1175 (P0) — ``atr_floor_binding_studies`` leer, obwohl der Floor in 5 von 14
Studies bindet.

Root-Cause: ``check_atr_scale_homogeneity`` leitete ``floor_binding_studies`` (a) NUR innerhalb
eines bereits spannweiten-offending Symbols ab und (b) über einen GLEICHHEITS-Vergleich des
EFFEKTIVEN (ratschen-gefloorten) ``atr_median_bps`` gegen den Floor — der Ratschen-Mechanismus haelt
den Study-Median des effektiven ATR haeufig knapp ueber dem Floor, selbst wenn dieser ueber grosse
Teile des Fensters band. Fix: das Kriterium ist jetzt ``atr_raw_median_bps < atr_floor_bps_derived``
(der ROHE, ungefloorte Median gegen den Floor selbst), unabhaengig vom Spannweiten-Offender-Status,
ueber JEDE Study mit beiden Feldern; fehlen sie ueberall, ist das Ergebnis explizit nicht auswertbar
statt einer stillen leeren Liste.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def _study(strategy, symbol, *, atr_raw=None, atr_floor_derived=None, atr_median_bps=None,
          stop_distance_bps_modelled=None, realized_stop_loss_ratio=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_raw_median_bps": atr_raw,
        "atr_floor_bps_derived": atr_floor_derived,
        "atr_median_bps": atr_median_bps,
        "stop_distance_bps_modelled": stop_distance_bps_modelled,
        "realized_stop_loss_ratio": realized_stop_loss_ratio,
    }


def test_raw_below_floor_binds_even_when_effective_atr_sits_above_the_floor_via_ratchet():
    """Der Ratschen-Fall aus #1026: der EFFEKTIVE atr_median_bps (9.5) liegt UEBER dem Floor
    (7.98) — die alte Gleichheits-Pruefung haette das NICHT als floor-gebunden erkannt. Der ROHE
    Median (0.1627) liegt weit UNTER dem Floor -> der Floor band tatsaechlich ueber weite Teile
    des Fensters."""
    records = [
        _study("DynamicBreakoutStrategy", "TSLA.ETORO", atr_raw=0.1627, atr_floor_derived=7.9820,
              atr_median_bps=9.5, stop_distance_bps_modelled=9.22, realized_stop_loss_ratio=7.67),
        _study("OpeningRangeBreakoutStrategy", "TSLA.ETORO", atr_raw=27.665,
              atr_floor_derived=7.9820, atr_median_bps=27.665),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    binding = result.provenance["atr_floor_binding_studies"]
    assert "DynamicBreakoutStrategy/TSLA.ETORO" in binding
    assert "OpeningRangeBreakoutStrategy/TSLA.ETORO" not in binding
    detail = result.provenance["atr_floor_binding_studies_detail"]["DynamicBreakoutStrategy/TSLA.ETORO"]
    assert detail["raw"] == 0.1627
    assert detail["floor"] == 7.982
    assert detail["faktor"] == round(7.9820 / 0.1627, 2)
    assert detail["stopdistanz_bps"] == 9.22
    assert detail["realized_stop_loss_ratio"] == 7.67


def test_floor_binding_is_independent_of_the_spread_offender_status():
    """Selbst wenn die Spannweitenpruefung PASST (kein Symbol ueber max_ratio), muss eine floor-
    gebundene Study weiterhin ausgewiesen werden — vor #1026 wurde floor_binding_studies NUR
    innerhalb eines bereits offending Symbols berechnet."""
    records = [
        _study("A", "TSLA.ETORO", atr_raw=1.0, atr_floor_derived=8.0, atr_median_bps=8.0),
        _study("B", "TSLA.ETORO", atr_raw=9.0, atr_floor_derived=8.0, atr_median_bps=9.0),
    ]
    result = inv.check_atr_scale_homogeneity(records, max_ratio=6.0)
    assert result.passed is True  # Spannweite 9.0/8.0 = 1.125, weit unter 6.0
    assert "A/TSLA.ETORO" in result.provenance["atr_floor_binding_studies"]


def test_evaluable_false_when_neither_raw_nor_derived_floor_is_present_anywhere():
    records = [{"strategy": "A", "symbol": "X.ETORO"}, {"strategy": "B", "symbol": "X.ETORO"}]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.provenance["atr_floor_binding_evaluable"] is False
    assert result.provenance["atr_floor_binding_studies"] == []


def test_stop_distance_bps_modelled_is_exported_as_a_first_class_study_field():
    """Rohmaterial fuer die atr_floor_binding_studies-Provenance (stopdistanz_bps) — vorher nur
    lokal in _study_record berechnet, nirgends exportiert. Issue #1081/#1229 — umbenannt von
    ``stop_distance_bps`` zu ``stop_distance_bps_modelled`` (die MODELLIERTE k*ATR-Groesse)."""
    import optuna

    class _T:
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_atr_median_bps": 2.0,
            "sampled_params": {"atr_trailing_multiplier": 1.5},
        }
        value = 1.0
        params = {}
        state = optuna.trial.TrialState.COMPLETE

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {}

    proposal = {"symbol": "TSLA.ETORO", "strategy": "A"}
    record, _checks = rpt._study_record(proposal, _S())
    assert record["stop_distance_bps_modelled"] == 3.0  # 2.0 * 1.5
