"""Issue #1096/#1244 (P2, Katalog #1247+) — ``STRUCTURAL_ZERO_ELIGIBLE`` ohne Rückschrieb.

Symptom: ``check_structural_zero_eligible_has_diagnosis`` feuert in 5/11 Läufen; ``diagnosed_pairs``
enthält 0–3 Einträge je Lauf. Betroffen u. a. AdxAtr/TSLA (0 eligible in allen drei Läufen, 403
Trials verbrannt) und VolatilityBreakoutPump/GOOGL (0 eligible in beiden Läufen).

Root-Cause: #1219 (``report._writeback_search_stagnation_diagnoses``) hat den Rückschrieb NUR für
``search_made_progress``-Stagnation UND die STRUCTURAL_ZERO_ELIGIBLE-Restmenge verdrahtet —
``stop_reason == 'STRUCTURAL_ALL_UNEVALUABLE'`` (0 EVALUABLE Trials, eine Stufe VOR
STRUCTURAL_ZERO_ELIGIBLE) erzeugte NIE einen ``diagnosed_pairs``-Eintrag, unabhängig vom
Cache-Schalter.

Fix: dieselbe LIVE-Ableitung wie #1045/#1194 (``sweep_diagnostics.diagnose_structural_zero_
eligible_gate`` → ``report._structural_zero_eligible_diagnosed_pairs``) läuft jetzt auch für
``STRUCTURAL_ALL_UNEVALUABLE``-Studies — mit einem eigenen, UNBEDINGT frequenzseitigen Zweig (0
evaluable Trials können architektonisch nie ein Qualitätsbefund sein, siehe ``diagnose_trade_
frequency``s ``n_evaluable==0``-Zweig, der ebenfalls nie 'signal_quality' zurückgibt) — kein
homogeneity-Torwächter, jede betroffene Study bekommt eine Diagnose.
"""
from automation.optimizer import invariants as inv, report
from automation.optimizer.sweep_diagnostics import diagnose_structural_zero_eligible_gate


# ── sweep_diagnostics.diagnose_structural_zero_eligible_gate(stop_reason=...) ───────────────────

# Issue #1303 (GH #1180) — diagnose_structural_zero_eligible_gate erfordert seither max_is_trades/
# median_is_trades (Pflicht-Keywords, siehe test_issue_1303_binding_cause_signal_absent.py). None
# haelt diese vorbestehenden Tests unveraendert auf dem alten "signal_sparse"-Pfad (None == 0 ist
# falsch, siehe dortige Fix-Dokumentation) — sie sagen nichts ueber die IS-Aktivitaet aus.
_NOT_APPLICABLE = {"max_is_trades": None, "median_is_trades": None}


def test_structural_all_unevaluable_is_unconditionally_frequency():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_INACTIVE": 40}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", **_NOT_APPLICABLE)
    assert diagnosis["gate_type"] == "frequency"
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["proposed_action"] == "search_space_override"


def test_structural_all_unevaluable_stays_frequency_even_with_mixed_details():
    """Anders als der STRUCTURAL_ZERO_ELIGIBLE-Zweig gibt es hier KEINE Homogenitaets-Huerde — jede
    Mischung der vier 'nie OOS erreicht'-Codes bleibt frequenzseitig."""
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 25, "REJECT_OOS_INACTIVE": 15},
        stop_reason="STRUCTURAL_ALL_UNEVALUABLE", **_NOT_APPLICABLE)
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["dominant_rejection_detail"] == "REJECT_OOS_WINDOW_UNREACHABLE"
    assert diagnosis["dominant_fraction"] == 0.625


def test_structural_all_unevaluable_with_empty_counts_still_gets_a_verdict():
    """0 evaluable Trials koennen strukturell KEINE is_rejection_detail-Zaehlung tragen (das Feld
    wird erst bei einer OOS-Auswertung gestempelt) — der Befund bleibt trotzdem frequenzseitig,
    nicht 'none' (im Unterschied zum STRUCTURAL_ZERO_ELIGIBLE-Zweig bei leeren counts)."""
    diagnosis = diagnose_structural_zero_eligible_gate(
        None, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", **_NOT_APPLICABLE)
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["dominant_rejection_detail"] is None
    assert diagnosis["dominant_fraction"] is None


def test_structural_zero_eligible_branch_is_unaffected_by_the_new_stop_reason_param():
    """Regressionsschutz: der STRUCTURAL_ZERO_ELIGIBLE-Pfad (stop_reason=None, Legacy-Aufrufer)
    bleibt exakt wie vor #1096/#1244 — insbesondere bleibt eine 60/40-Mischung ZWEIER
    Qualitaets-Gates weiterhin 'none' (kein Rateversuch, siehe test_issue_1045_1194)."""
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_PSR": 60, "REJECT_OOS_MAX_DRAWDOWN": 40}, **_NOT_APPLICABLE)
    assert diagnosis["binding_cause"] == "none"


# ── report._structural_zero_eligible_diagnosed_pairs / _diagnosed_pairs_section ─────────────────

def _study_record(strategy, symbol, *, stop_reason, is_rejection_detail_counts,
                  budget_executed_fraction=1.0):
    return {"strategy": strategy, "symbol": symbol, "stop_reason": stop_reason,
            "is_rejection_detail_counts": is_rejection_detail_counts,
            "budget_executed_fraction": budget_executed_fraction}


def test_structural_all_unevaluable_study_now_yields_a_diagnosed_pairs_entry(monkeypatch, tmp_path):
    """Akzeptanzkriterium: AdxAtr/TSLA erscheint mit benanntem binding_cause."""
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={"REJECT_OOS_INACTIVE": 30}),
    ]
    section = report._diagnosed_pairs_section(studies_out)
    entry = next(e for e in section if e["strategy"] == "AdxAtrMomentumStrategy"
                and e["symbol"] == "TSLA.ETORO")
    assert entry["binding_cause"] == "signal_sparse"
    assert entry["action"] == "search_space_override"
    # Issue #1304 (GH #1181) Fix Punkt 1 — ohne STRUCTURAL_ALL_UNEVALUABLE-Ereignis (kein
    # events_path/kein passendes Ereignis) bleibt der Report-Zweig als FALLBACK aktiv, jetzt
    # gestempelt als "report_fallback" statt des vormaligen "live_derivation".
    assert entry["source"] == "report_fallback"


def test_structural_all_unevaluable_study_with_no_rejection_detail_counts_still_diagnosed(
    monkeypatch, tmp_path,
):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    studies_out = [
        _study_record("VolatilityBreakoutPumpStrategy", "GOOGL.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={}),
    ]
    pairs = report._structural_zero_eligible_diagnosed_pairs(studies_out)
    assert len(pairs) == 1
    assert pairs[0]["binding_cause"] == "signal_sparse"


def test_non_structural_stop_reason_is_still_not_diagnosed():
    studies_out = [
        _study_record("A", "X.ETORO", stop_reason="BUDGET_EXHAUSTED",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 40}),
    ]
    assert report._structural_zero_eligible_diagnosed_pairs(studies_out) == []


def test_both_structural_stop_reasons_coexist_in_the_same_report(monkeypatch, tmp_path):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
        _study_record("VolatilityBreakoutPumpStrategy", "GOOGL.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={"REJECT_OOS_INACTIVE": 20}),
    ]
    section = report._diagnosed_pairs_section(studies_out)
    keys = {(e["strategy"], e["symbol"]) for e in section}
    assert ("AdxAtrMomentumStrategy", "TSLA.ETORO") in keys
    assert ("VolatilityBreakoutPumpStrategy", "GOOGL.ETORO") in keys


# ── invariants.check_structural_zero_eligible_has_diagnosis ─────────────────────────────────────

def test_invariant_now_also_covers_structural_all_unevaluable():
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={"REJECT_OOS_INACTIVE": 30}),
    ]
    result = inv.check_structural_zero_eligible_has_diagnosis(studies_out, [])
    assert result.passed is False
    assert "AdxAtrMomentumStrategy/TSLA.ETORO" in result.actual["missing_diagnosis_for"]


def test_invariant_passes_once_the_live_derivation_covers_the_structural_all_unevaluable_study(
    monkeypatch, tmp_path,
):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={"REJECT_OOS_INACTIVE": 30}),
    ]
    result = inv.check_structural_zero_eligible_has_diagnosis(
        studies_out, report._diagnosed_pairs_section(studies_out))
    assert result.passed is True


def test_invariant_still_passes_trivially_for_unrelated_stop_reasons():
    result = inv.check_structural_zero_eligible_has_diagnosis(
        [_study_record("A", "X.ETORO", stop_reason="BUDGET_EXHAUSTED",
                       is_rejection_detail_counts={})], [])
    assert result.passed is True
