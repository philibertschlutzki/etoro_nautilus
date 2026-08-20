"""Issue #950/#1116 (Katalog #960) — Der Trailing-Stop ist keine Risikogrösse: Abnahmemessung mit
klarem Zielwert.

Symptom (B-11, 39 saubere Studies vor dem Fix): Spearman(k·ATR, realisierter Stop-Verlust) =
−0,6036; k·ATR variiert Faktor 28,3, der realisierte Verlust nur Faktor 3,23; TRAILING_STOP ist mit
46,68 % von 825 064 Round-Trips der häufigste Ausgang.

Status: der Code-Fix (#1092 trailing_stop_anchor_resolution="close", #1094 monotone Ratsche) ist
bereits im HEAD (siehe hourly_strategy_base.py). Dieses Issue liefert die ABNAHMEMESSUNG —
``invariants.check_trailing_stop_risk_calibration_acceptance`` — DREI verbindliche Kriterien, ALLE
müssen bestehen:
1. Spearman(atr_trailing_multiplier, gemessene stop_distance_bps) INNERHALB jeder Study >=
   max(0,3, 2·SE(n)) — siehe Issue #1056/#1205-Fix (Aggregationsebene + n-abhängige Schwelle,
   Regressionsabdeckung in ``test_issue_1056_1205_within_study_spearman_calibration.py``).
2. realized_stop_loss_ratio in [0,8; 3,0] für >= 80 % der Studies.
3. gepoolter TRAILING_STOP-Anteil an allen Exits < 35 %.

Scheitert das Kriterium, ist die #1092-Hypothese widerlegt und #953/#1119 (Ein-Bar-Latenz) ist die
verbleibende Erklärung.

Hinweis: seit #1056/#1205 konsumiert Kriterium 1 die in ``report._study_record`` VORBERECHNETEN
Felder ``stop_calibration_spearman_within_study``/``stop_calibration_n_pairs_within_study`` (je
Study eine WITHIN-STUDY-Korrelation über deren Trials) statt der vormaligen STUDY-MEDIANE
(``atr_trailing_multiplier_median``/``gross_loss_median_bps_trailing_stop``, die weiterhin nur für
Kriterium 2 — den Ratio-Band-Test — verwendet werden). Die Fixtures hier setzen die neuen Felder
DIREKT (simulieren das Ergebnis von ``report._within_study_stop_calibration_spearman``), statt
Trial-Rohdaten zu konstruieren.
"""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, atr_bps, k, loss_bps_trailing_stop, n_ts_losses=40,
           n_ts_exits=50, n_total_exits=100,
           stop_calibration_spearman=None, stop_calibration_n_pairs=0):
    histogram = {"TRAILING_STOP": n_ts_exits, "TIME_BOX": n_total_exits - n_ts_exits}
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_median_bps": atr_bps,
        "atr_trailing_multiplier_median": k,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": loss_bps_trailing_stop,
        # Issue #972/#1126 — die Ratio-Band-Eingangsgroesse (Kriterium 2) ist seit diesem Fix die
        # robuste Median-Variante (Pitfall #405 in AGENTS.md); fuer diese Fixtures identisch zum
        # Mittel oben.
        "gross_loss_median_bps_trailing_stop": loss_bps_trailing_stop,
        "oos_n_trailing_stop_losses": n_ts_losses,
        "exit_reason_histogram": histogram,
        "oos_total_trades_with_exit_telemetry": n_total_exits,
        # Issue #1056/#1205 — WITHIN-STUDY-Kalibrierungs-Rohmaterial fuer Kriterium 1 (siehe
        # report._within_study_stop_calibration_spearman); n_pairs=0/spearman=None simuliert eine
        # Study OHNE ausreichende Trial-Evidenz (wird von der Aggregation ausgeschlossen).
        "stop_calibration_spearman_within_study": stop_calibration_spearman,
        "stop_calibration_n_pairs_within_study": stop_calibration_n_pairs,
    }


def test_inconclusive_with_fewer_than_three_measurable_studies():
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", atr_bps=10.0, k=2.0, loss_bps_trailing_stop=15.0),
        _study("B", "Y.ETORO", atr_bps=8.0, k=1.5, loss_bps_trailing_stop=10.0),
    ])
    # Issue #995/#1147 (Pitfall #413 in AGENTS.md) — INCONCLUSIVE ist seither passed=None (nicht
    # True), unabhaengig vom severity-Wert: fehlende Evidenz ist auf keinem Check ein PASS.
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False
    assert result.severity == "high"


def test_studies_below_min_trailing_stop_exits_are_excluded_from_the_correlation():
    """Studies mit < 30 nachweislichen Stop-Exits duerfen die Ratio-Band-Stichprobe (Kriterium 2)
    nicht verwaessern (dieselbe Evidenz-Untergrenze wie check_effective_stop_distance)."""
    thin = [
        _study("A", f"S{i}.ETORO", atr_bps=10.0 + i, k=1.0 + 0.1 * i,
               loss_bps_trailing_stop=5.0, n_ts_losses=2)
        for i in range(5)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(thin)
    assert result.inconclusive is True


def test_inconclusive_with_fewer_than_eight_within_study_pairs():
    """Issue #1056/#1205 Fix Punkt c) — >= 3 Studies mit Ratio-Band-Evidenz, aber die WITHIN-STUDY-
    Kalibrierungspaare summieren sich auf < 8 -- passed=None (nicht False), mit explizitem
    Unavailability-Grund im actual-Dict (Fix Punkt d)."""
    studies = [
        _study("Strat", f"SYM{i}.ETORO", atr_bps=10.0, k=1.0 + 0.5 * i,
               loss_bps_trailing_stop=(1.0 + 0.5 * i) * 10.0 * 1.5, n_ts_exits=20,
               stop_calibration_spearman=1.0, stop_calibration_n_pairs=2)
        for i in range(3)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False
    assert result.actual["stop_calibration_unavailable_reason"] == (
        "SPEARMAN_UNAVAILABLE_INSUFFICIENT_PAIRS")
    assert result.actual["stop_calibration_spearman"] is None


def test_reproduces_b11_reference_finding_fails_on_inverse_correlation():
    """Reproduziert B-11: die WITHIN-STUDY-Korrelation (atr_trailing_multiplier, gemessene
    stop_distance_bps) ist ueber alle Studies hinweg INVERS (rho=-0.6, wie B-11s berichtetes
    -0.6036) UND TRAILING_STOP-Anteil > 35% -- die Hypothese wird widerlegt."""
    studies = [
        _study("Strat", f"SYM{i}.ETORO", atr_bps=10.0 * (i + 1), k=3.0 - 0.2 * i,
               loss_bps_trailing_stop=20.0 - i, n_ts_exits=60, n_total_exits=100,
               stop_calibration_spearman=-0.6, stop_calibration_n_pairs=10)
        for i in range(6)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is False
    assert result.severity == "high"
    # Issue #974/#1128 (Pitfall #403 in AGENTS.md) — der Meldungstext behauptet keine Ursache mehr;
    # die Ursachenzuweisung steht separat in causal_hypothesis_state (heute UNRESOLVED, siehe
    # _resolve_causal_hypothesis_state-Docstring: die Fill-Latenz-Telemetrie fehlt in diesen
    # Fixtures).
    assert "kalibrierung nicht erreicht" in result.detail.lower()
    assert result.actual["causal_hypothesis_state"] == "UNRESOLVED"
    assert result.actual["trailing_stop_exit_share"] >= 0.35
    # Issue #1056/#1205 Akzeptanzkriterium — n, rho und Schwelle stehen im Detailtext.
    assert "n=60" in result.detail
    assert "-0.6" in result.detail
    assert result.actual["stop_calibration_spearman"] == -0.6
    assert result.actual["stop_calibration_n_pairs"] == 60


def test_passes_when_all_three_criteria_are_met():
    """Konstruiertes Positiv-Beispiel: die WITHIN-STUDY-Korrelation ist ueberall perfekt (rho=1.0),
    alle Ratios liegen im Band, TRAILING_STOP-Anteil bleibt unter 35%."""
    studies = [
        _study("Strat", f"SYM{i}.ETORO", atr_bps=10.0, k=1.0 + 0.5 * i,
               loss_bps_trailing_stop=(1.0 + 0.5 * i) * 10.0 * 1.5,
               n_ts_exits=20, n_total_exits=100,
               stop_calibration_spearman=1.0, stop_calibration_n_pairs=10)
        for i in range(6)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is True
    assert result.actual["stop_calibration_spearman"] == 1.0
    assert result.actual["fraction_studies_ratio_in_band"] == 1.0
    assert result.actual["trailing_stop_exit_share"] < 0.35
    # Issue #1056/#1205 Akzeptanzkriterium — Schwelle und Konfidenzintervall stehen im actual-Dict.
    assert result.actual["stop_calibration_threshold"] == 0.3
    assert len(result.actual["stop_calibration_ci"]) == 2


def test_fails_when_ratio_band_fraction_criterion_alone_is_violated():
    """Perfekte WITHIN-STUDY-Korrelation UND niedriger TRAILING_STOP-Anteil, aber die Mehrheit der
    Ratios liegt ausserhalb [0.8, 3.0] -- muss allein deswegen scheitern (jedes der drei Kriterien
    ist einzeln hinreichend fuer ein FAIL)."""
    studies = [
        _study("Strat", f"SYM{i}.ETORO", atr_bps=10.0, k=1.0 + 0.5 * i,
               loss_bps_trailing_stop=(1.0 + 0.5 * i) * 10.0 * 6.0,  # Ratio 6.0, ausserhalb Band
               n_ts_exits=10, n_total_exits=100,
               stop_calibration_spearman=1.0, stop_calibration_n_pairs=10)
        for i in range(6)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is False
    assert result.actual["fraction_studies_ratio_in_band"] < 0.8


def test_pooled_trailing_stop_share_is_computed_over_all_studies_not_averaged():
    """Der Anteil ist GEPOOLT (Summe TRAILING_STOP / Summe aller Exits ueber ALLE Studies), nicht
    der Mittelwert je-Study-Anteile -- zwei Studies mit sehr unterschiedlichem Exit-Volumen duerfen
    nicht gleich gewichtet werden. Dieses Feld wird bereits im (n<8)-INCONCLUSIVE-Zweig berichtet."""
    studies = [
        _study("A", "BIG.ETORO", atr_bps=10.0, k=1.0, loss_bps_trailing_stop=15.0,
               n_ts_exits=900, n_total_exits=1000, n_ts_losses=40),
        _study("B", "SMALL.ETORO", atr_bps=10.0, k=2.0, loss_bps_trailing_stop=30.0,
               n_ts_exits=1, n_total_exits=10, n_ts_losses=40),
        _study("C", "MID.ETORO", atr_bps=10.0, k=3.0, loss_bps_trailing_stop=45.0,
               n_ts_exits=1, n_total_exits=10, n_ts_losses=40),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    pooled_share = (900 + 1 + 1) / (1000 + 10 + 10)
    assert result.actual["trailing_stop_exit_share"] == round(pooled_share, 4)


def test_provenance_carries_per_study_ratios_and_spearman():
    studies = [
        _study("Strat", f"SYM{i}.ETORO", atr_bps=10.0, k=1.0 + 0.5 * i,
               loss_bps_trailing_stop=(1.0 + 0.5 * i) * 10.0 * 1.5, n_ts_exits=20,
               stop_calibration_spearman=1.0, stop_calibration_n_pairs=10)
        for i in range(4)
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert "Strat/SYM0.ETORO" in result.provenance["ratio_by_study"]
    assert "Strat/SYM0.ETORO" in result.provenance["stop_calibration_spearman_by_study"]
    assert result.provenance["stop_calibration_spearman_by_study"]["Strat/SYM0.ETORO"] == {
        "spearman": 1.0, "n_pairs": 10}
