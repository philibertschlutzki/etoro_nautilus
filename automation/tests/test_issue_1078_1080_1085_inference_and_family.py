"""Issue #1078/#1080/#1085 (Katalog #866-2, Kohorte E) — Inferenz, Zähler, Suchhaushalt.

#1078 — ``check_inference_diagnostics_concentration`` teilte den Zähler (Trials MIT einem
censoring-/adaptive-Code) durch ``n_trials_informative`` (Trials OHNE einen solchen Code) — zwei
DISJUNKTE Teilmengen derselben Grundgesamtheit, ein Verhältnis das > 1.0 werden kann (Beweis B-12:
Squeeze, 81/56 = 1,4464). Fix: der Nenner ist ``n_trials`` (die volle, kommensurable Trial-Zahl).

#1080 — ``n_family_stage1`` liess eine Study (z. B. TrendPullback, 0 Holdout-Trades) vollständig
aus, obwohl sie zur familienweiten Multiplizität beitragen muss — die Deflations-Referenz SR* wurde
zu niedrig angesetzt. Fix: Fallback auf ``n_selection_statistic_available`` (#822); neue Invariante
``check_n_family_partition``.

#1085 — Dust-Round-Trips (Notional ~1e-13) füllen jeden gepoolten Nenner. Neue Invariante
``check_dust_round_trip_share`` macht den Anteil je Study sichtbar (Fix an der Quelle selbst bleibt
aus Risikogründen ausserhalb dieses Patches, siehe PR-Beschreibung).
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _family_n_stages


# ── #1078: check_inference_diagnostics_concentration denominator ────────────────────────────────
def _trial_with_code(code):
    return {"inference_diagnostics": [{"code": code}]}


def test_concentration_rate_never_exceeds_1_when_n_trials_is_passed():
    """Beweis B-12 im #866-Katalog: Squeeze, 81 zensierte Trials (SORTINO_INSUFFICIENT_TRADES 67 +
    SORTINO_DOWNSIDE_SHRUNK 3 + SORTINO_INSUFFICIENT_DOWNSIDE 11) gegen 56 informative, aber 180
    Trials insgesamt. Mit n_trials=180 als Nenner: 81/180 = 0.45, kommensurabel."""
    trials = (
        [_trial_with_code("SORTINO_INSUFFICIENT_TRADES")] * 67
        + [_trial_with_code("SORTINO_DOWNSIDE_SHRUNK")] * 3
        + [_trial_with_code("SORTINO_INSUFFICIENT_DOWNSIDE")] * 11
        + [{"inference_diagnostics": []}] * (180 - 81)
    )
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=56, n_trials=180)
    assert result.actual <= 1.0
    assert abs(result.actual - 81 / 180) < 1e-9


def test_concentration_rate_legacy_call_without_n_trials_falls_back_and_can_still_flag_incommensurable():
    """Rueckwaertskompatibilitaet: ohne n_trials bleibt der alte Nenner (n_trials_informative) UND
    das #1033-Sicherheitsnetz (rate > 1.0 -> expliziter FAIL) wirksam."""
    trials = [_trial_with_code("SORTINO_INSUFFICIENT_TRADES")] * 81
    result = inv.check_inference_diagnostics_concentration(trials, n_trials_informative=56)
    assert result.passed is False
    assert abs(result.actual - 81 / 56) < 1e-3


def test_concentration_rate_passes_below_threshold_with_n_trials_denominator():
    trials = [_trial_with_code("SORTINO_GUARD_TRIPPED")] * 5 + [{"inference_diagnostics": []}] * 95
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=90, n_trials=100, guard_dominance_threshold=0.10)
    assert result.passed is True
    assert result.actual == 0.05


# ── #1080: n_family_stage1 fallback + check_n_family_partition ──────────────────────────────────
def test_family_n_stages_falls_back_to_selection_statistic_available_when_stage1_missing():
    studies_out = [
        {"symbol": "TSLA.ETORO", "strategy": "DonchianRegimeBreakoutStrategy",
         "n_family_stage1": 200},
        # TrendPullback: 0 Holdout-Trades -> n_family_stage1 fehlt, aber 111 Trials mit
        # verfuegbarer Selektionsstatistik (Beweis im #866-Katalog).
        {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy",
         "n_family_stage1": None, "n_selection_statistic_available": 111},
    ]
    stage1, stage2 = _family_n_stages(studies_out)
    assert stage1["TSLA.ETORO"]["TrendPullbackStrategy"] == 111
    assert stage1["TSLA.ETORO"]["DonchianRegimeBreakoutStrategy"] == 200


def test_check_n_family_partition_fails_on_a_gap():
    n_family = {"TSLA.ETORO": 467}
    n_family_stage1 = {"TSLA.ETORO": {"DonchianRegimeBreakoutStrategy": 200,
                                      "TrendPullbackStrategy": 111}}
    # Summe (311) != n_family (467) -> Luecke, TrendPullback allein reicht nicht, um die
    # vollstaendige Diskrepanz aus dem Katalog zu schliessen, aber demonstriert die Mechanik.
    result = inv.check_n_family_partition(n_family, n_family_stage1)
    assert result.passed is False
    assert result.actual["TSLA.ETORO"]["n_family"] == 467
    assert result.actual["TSLA.ETORO"]["sum_n_family_stage1"] == 311


def test_check_n_family_partition_passes_when_consistent():
    n_family = {"TSLA.ETORO": 1619}
    n_family_stage1 = {"TSLA.ETORO": {"A": 800, "B": 708, "TrendPullbackStrategy": 111}}
    result = inv.check_n_family_partition(n_family, n_family_stage1)
    assert result.passed is True


def test_check_n_family_partition_not_applicable_without_overlap():
    result = inv.check_n_family_partition({}, {})
    assert result.passed is True
    assert result.actual is None


# ── #1085: check_dust_round_trip_share ───────────────────────────────────────────────────────────
def test_dust_round_trip_share_fails_on_the_866_catalog_reference_case():
    """Issue #1041/#1190 — der Nenner ist seit diesem Fix die VOLLE (ungefilterte) Population
    (oos_total_trades_with_exit_telemetry + dust_round_trips_filtered), nicht mehr nur die bereits
    gefilterte Menge. 1156/(9794+1156) = 0.1056; 3301/(34413+3301) = 0.0875 -- beide Werte sinken
    gegenueber der fehlerhaften Vorher-Formel (0.118 / 0.0959), bleiben aber > max_share=0.01."""
    records = [
        {"strategy": "DonchianRegimeBreakoutStrategy", "symbol": "TSLA.ETORO",
         "dust_round_trips_filtered": 1156, "oos_total_trades_with_exit_telemetry": 9794},
        {"strategy": "Rsi2ReversionStrategy", "symbol": "TSLA.ETORO",
         "dust_round_trips_filtered": 3301, "oos_total_trades_with_exit_telemetry": 34413},
    ]
    result = inv.check_dust_round_trip_share(records)
    assert result.passed is False
    assert result.actual["DonchianRegimeBreakoutStrategy/TSLA.ETORO"] > 0.10
    assert result.actual["Rsi2ReversionStrategy/TSLA.ETORO"] > 0.08


def test_dust_round_trip_share_passes_below_one_percent():
    records = [{"strategy": "S", "symbol": "X",
               "dust_round_trips_filtered": 1, "oos_total_trades_with_exit_telemetry": 1000}]
    result = inv.check_dust_round_trip_share(records)
    assert result.passed is True


def test_dust_round_trip_share_not_applicable_without_telemetry():
    result = inv.check_dust_round_trip_share([{"strategy": "S", "symbol": "X"}])
    assert result.passed is True
    assert result.actual is None
