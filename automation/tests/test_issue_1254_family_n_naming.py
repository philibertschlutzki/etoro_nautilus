"""Issue #1254 (GH #1124) — sweep_completed.deflation_n_family trägt die Überlebenden unter dem
Namen der Versuche.

Symptom. ``sweep_completed`` publiziert ``deflation_n_family = {TSLA.ETORO: 2}``, der Report
``n_family.frozen = {TSLA.ETORO: 1627}``. Faktor 813, gleicher Name.

Root-Cause. ``sweep._family_n_from_proposals`` summiert ``deflation_n_eligible`` (Überlebende,
seit #784/#822 als Multiplizitätsgrösse veraltet). Der Docstring markiert die Funktion bereits als
reine Rückwärtskompat-Telemetrie; das Feld heisst im Event trotzdem wie die Entscheidungsgrösse.

Fix.
1. Feld im ``sweep_completed``-Event in ``deflation_n_eligible_legacy`` umbenannt.
2. Zusätzlich ``n_family_attempted_frozen`` aus derselben Aggregation emittiert, die
   ``report.family_n_frozen_stage1_from_proposals`` auch für ``cross_study['n_family']['frozen']``
   verwendet (extrahiert, damit beide Seiten strukturell nicht divergieren können).
3. Neuer Check ``invariants.check_family_n_event_report_agreement`` (severity 'medium'):
   ``sweep_completed.n_family_attempted_frozen == run_json.cross_study.n_family.frozen`` je Symbol.
"""
import ast
import json
from pathlib import Path

from automation.optimizer import invariants as inv, report as rpt


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _proposal(symbol, strategy, *, n_family_frozen):
    return {
        "strategy": strategy, "symbol": symbol, "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "deflation_n_family_frozen": n_family_frozen,
        "holdout": {"symbol": {"deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
                               "deflation_n_eligible": 3, "deflation_n_family_effective": 3,
                               "deflation_n_effective": 3}},
    }


# ---------------------------------------------------------------------------------------------
# report.family_n_frozen_stage1_from_proposals — the shared aggregation
# ---------------------------------------------------------------------------------------------

def test_frozen_stage1_max_per_strategy_then_sum_per_symbol():
    proposals = [
        _proposal("TSLA.ETORO", "StratA", n_family_frozen=800),
        _proposal("TSLA.ETORO", "StratB", n_family_frozen=827),
    ]
    stage1, by_symbol = rpt.family_n_frozen_stage1_from_proposals(proposals)
    assert stage1["TSLA.ETORO"] == {"StratA": 800, "StratB": 827}
    assert by_symbol["TSLA.ETORO"] == 1627


def test_frozen_stage1_duplicate_proposal_of_same_strategy_does_not_double_count():
    proposals = [
        _proposal("TSLA.ETORO", "StratA", n_family_frozen=800),
        _proposal("TSLA.ETORO", "StratA", n_family_frozen=800),
    ]
    _, by_symbol = rpt.family_n_frozen_stage1_from_proposals(proposals)
    assert by_symbol["TSLA.ETORO"] == 800


def test_frozen_stage1_missing_field_contributes_nothing():
    proposals = [{"symbol": "TSLA.ETORO", "strategy": "StratA"}]
    stage1, by_symbol = rpt.family_n_frozen_stage1_from_proposals(proposals)
    assert stage1 == {}
    assert by_symbol == {}


# ---------------------------------------------------------------------------------------------
# invariants.check_family_n_event_report_agreement
# ---------------------------------------------------------------------------------------------

def test_no_event_is_inconclusive():
    r = inv.check_family_n_event_report_agreement(None, {"TSLA.ETORO": 1627})
    assert r.passed is True
    assert r.inconclusive is True
    assert r.severity == "medium"


def test_matching_values_pass():
    r = inv.check_family_n_event_report_agreement(
        {"TSLA.ETORO": 1627}, {"TSLA.ETORO": 1627})
    assert r.passed is True
    assert r.inconclusive is False


def test_reference_symptom_diverging_values_fail():
    # Faktor 813 aus dem #1254-Symptom (2 vs. 1627) — hier reproduziert mit den korrigierten
    # Feldnamen: eine kuenstliche Divergenz muss trotzdem sichtbar bleiben.
    # Issue #1322 (GH #1199) — ``actual`` traegt seither zwei getrennte Kategorien
    # (``mismatches``/``missing_with_empty_family``); eine ECHTE Werte-Abweichung (Schluessel in
    # BEIDEN Dicts vorhanden) landet unter ``mismatches``.
    r = inv.check_family_n_event_report_agreement(
        {"TSLA.ETORO": 2}, {"TSLA.ETORO": 1627})
    assert r.passed is False
    assert r.severity == "medium"
    assert r.actual["mismatches"]["TSLA.ETORO"] == {"event": 2, "report": 1627}
    assert "missing_with_empty_family" not in r.actual


def test_symbol_only_in_event_is_a_mismatch():
    """AAPL.ETORO ist im Ereignis, fehlt aber im Report (report=None != event=100) -- eine echte
    MISMATCH. TSLA.ETORO fehlt im Ereignis, traegt aber report-seitig einen NICHT-Null-Wert (1627)
    -- Issue #1322: MISSING bei report != 0 bleibt ein FAIL (mismatches), nur MISSING bei
    report == 0 waere strukturell erwartet (missing_with_empty_family, siehe eigene Tests unten)."""
    r = inv.check_family_n_event_report_agreement(
        {"AAPL.ETORO": 100}, {"TSLA.ETORO": 1627})
    assert r.passed is False
    assert "AAPL.ETORO" in r.actual["mismatches"]
    assert "TSLA.ETORO" in r.actual["mismatches"]
    assert r.actual["mismatches"]["TSLA.ETORO"] == {"event": "MISSING", "report": 1627}


def test_multiple_symbols_all_matching_pass():
    r = inv.check_family_n_event_report_agreement(
        {"TSLA.ETORO": 1627, "AAPL.ETORO": 42}, {"TSLA.ETORO": 1627, "AAPL.ETORO": 42})
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# Issue #1322 (GH #1199, P2) — check_family_n_event_report_agreement vergleicht "fehlt" gegen
# "ist 0". Symptom: {'TSLA.ETORO': {'event': None, 'report': 0}} FAILte, obwohl das
# sweep_completed-Ereignis das Feld bei einer leeren Familie strukturell nie befuellt.
# ---------------------------------------------------------------------------------------------

# ── Akzeptanzkriterium 3 — MISSING + report == 0 ⇒ passed=None, nicht false ──────────────────────

def test_reference_symptom_b14_missing_with_zero_report_is_inconclusive_not_fail():
    """Direkte B-14-Reproduktion: {'TSLA.ETORO': {'event': None, 'report': 0}}."""
    r = inv.check_family_n_event_report_agreement({}, {"TSLA.ETORO": 0})
    assert r.passed is None
    assert r.actual["missing_with_empty_family"]["TSLA.ETORO"] == {
        "event": "MISSING", "report": 0}
    assert "mismatches" not in r.actual


def test_missing_with_nonzero_report_is_still_a_fail():
    """MISSING bei einem NICHT-Null Report-Wert bleibt ein echtes FAIL (mismatches) -- die
    'leere Familie'-Ausnahme gilt NUR bei report == 0."""
    r = inv.check_family_n_event_report_agreement({}, {"TSLA.ETORO": 5})
    assert r.passed is False
    assert r.actual["mismatches"]["TSLA.ETORO"] == {"event": "MISSING", "report": 5}


def test_mismatch_dominates_over_a_coexisting_missing_empty_family_case():
    """Ein Lauf kann BEIDE Kategorien gleichzeitig tragen -- eine echte Abweichung (MISMATCH)
    entscheidet dann trotzdem passed=False, unabhaengig von zusaetzlichen, strukturell erwarteten
    MISSING-Faellen."""
    r = inv.check_family_n_event_report_agreement(
        {"AAPL.ETORO": 2}, {"AAPL.ETORO": 5, "TSLA.ETORO": 0})
    assert r.passed is False
    assert "AAPL.ETORO" in r.actual["mismatches"]
    assert "TSLA.ETORO" in r.actual["missing_with_empty_family"]


# ── Akzeptanzkriterium 2 — der Check unterscheidet MISSING und MISMATCH in actual ────────────────

def test_actual_separates_missing_and_mismatch_categories():
    r = inv.check_family_n_event_report_agreement(
        {"AAPL.ETORO": 2}, {"AAPL.ETORO": 5, "TSLA.ETORO": 0, "NVDA.ETORO": 3})
    assert set(r.actual.keys()) == {"mismatches", "missing_with_empty_family"}
    assert set(r.actual["mismatches"].keys()) == {"AAPL.ETORO", "NVDA.ETORO"}
    assert set(r.actual["missing_with_empty_family"].keys()) == {"TSLA.ETORO"}


def test_symbol_present_in_both_with_matching_zero_values_is_not_flagged_at_all():
    """Ein Symbol, das in BEIDEN Dicts mit demselben Wert 0 auftaucht, ist weder MISSING noch
    MISMATCH -- keine Kategorie faengt es (es stimmt einfach ueberein)."""
    r = inv.check_family_n_event_report_agreement(
        {"TSLA.ETORO": 0}, {"TSLA.ETORO": 0})
    assert r.passed is True
    assert r.actual is None


def test_detail_names_the_correct_reason_for_each_verdict():
    r_inconclusive = inv.check_family_n_event_report_agreement({}, {"TSLA.ETORO": 0})
    assert "strukturell erwartet" in r_inconclusive.detail
    r_fail = inv.check_family_n_event_report_agreement({"TSLA.ETORO": 2}, {"TSLA.ETORO": 1627})
    assert "echter Abweichung" in r_fail.detail


# ── Akzeptanzkriterium 1 — sweep_completed.n_family_attempted[_frozen] enthaelt jedes Symbol,
# auch mit Wert 0 (Quelltext-/Aggregationslogik-Regressionsschutz gegen sweep.py) ─────────────────

def test_sweep_py_zero_fills_every_symbol_of_the_run_source_inspection():
    """sweep.py's Emissionspfad muss JEDES Symbol aus 'pairs' explizit mit 0 vorbelegen, bevor die
    (moeglicherweise unvollstaendige) proposals-Aggregation ueberschreibt -- Quelltextpruefung, da
    ein voller run_per_symbol_sweep-Durchlauf fuer diesen Regressionsschutz zu aufwendig waere."""
    import inspect
    from automation.optimizer import sweep as sweep_mod

    source = inspect.getsource(sweep_mod.run_per_symbol_sweep)
    assert "_all_symbols_this_run = {sym for _, sym, _ in pairs}" in source
    assert 'family_n.setdefault(_sym, 0)' in source
    assert 'n_family_attempted_frozen.setdefault(_sym, 0)' in source


def test_zero_fill_logic_produces_a_complete_zero_stamped_dict():
    """Repliziert die sweep.py-Fuellschleife direkt gegen ein konstruiertes Beispiel: ein Symbol
    OHNE jeden proposals-Beitrag (TSLA.ETORO) erhaelt trotzdem einen 0-Eintrag, ein Symbol MIT
    Beitrag (AAPL.ETORO) bleibt unveraendert."""
    pairs = [("StratA", "TSLA.ETORO", None), ("StratB", "AAPL.ETORO", None)]
    n_family_attempted_frozen = {"AAPL.ETORO": 7}  # TSLA.ETORO fehlt (kein gueltiger Beitrag).
    all_symbols_this_run = {sym for _, sym, _ in pairs}
    for sym in all_symbols_this_run:
        n_family_attempted_frozen.setdefault(sym, 0)
    assert n_family_attempted_frozen == {"AAPL.ETORO": 7, "TSLA.ETORO": 0}


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_is_wired_into_build_report():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "check_family_n_event_report_agreement(" in source
    assert '"sweep_completed"' in source


# ---------------------------------------------------------------------------------------------
# sweep.py — event field naming + grep regression guard
# ---------------------------------------------------------------------------------------------

def _sweep_completed_event_dict_source() -> str:
    src = (REPO_ROOT / "automation/optimizer/sweep.py").read_text("utf-8")
    marker = 'emit_execution_event(_log, "sweep_completed", {'
    start = src.index(marker) + len(marker) - 1
    depth = 0
    i = start
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return src[start:i + 1]


def test_sweep_completed_event_no_longer_uses_the_old_field_name():
    # Akzeptanzkriterium #1254 — kein Konsument im Repo liest deflation_n_family aus dem Event
    # (hier: der EVENT-DICT selbst traegt den Schluessel nicht mehr).
    dict_src = _sweep_completed_event_dict_source()
    assert '"deflation_n_family"' not in dict_src
    assert '"deflation_n_eligible_legacy"' in dict_src
    assert '"n_family_attempted_frozen"' in dict_src
    assert '"n_family_attempted"' in dict_src


def test_no_consumer_reads_the_old_event_field_name_anywhere_in_the_repo():
    # Grep-Test (Akzeptanzkriterium #1254): der EREIGNIS-Zugriffsstil (ein "event"/"evt"-benanntes
    # Payload-Dict, das ``deflation_n_family`` liest) darf nirgends mehr im automation/-Baum
    # vorkommen. Bewusst auf "event"/"evt"-Empfaenger eingegrenzt — die unverwandten Study-/
    # Proposal-Felder (z. B. ``holdout_metrics.get("deflation_n_family")`` in report.py, die
    # ANDERE, weiterhin aktive #822-Grundgesamtheit fuer die per-Study-DSR) tragen nie einen
    # solchen Empfaenger-Variablennamen.
    import re
    pattern = re.compile(
        r'\b\w*(?:event|evt)\w*(?:\[\d+\])?\s*(?:\.get\(|\[)\s*["\']deflation_n_family["\']',
        re.IGNORECASE)
    for py_file in (REPO_ROOT / "automation").rglob("*.py"):
        if "test_issue_1005_1157" in py_file.name or "test_issue_1254" in py_file.name:
            continue
        src = py_file.read_text("utf-8", errors="ignore")
        m = pattern.search(src)
        assert m is None, f"{py_file}: still reads deflation_n_family off an event-like receiver: {m.group(0)!r}"


def test_family_n_from_proposals_docstring_still_names_it_backward_compat_telemetry():
    # Root-Cause-Anker: der Docstring markierte die Funktion bereits vor #1254 als reine
    # Rueckwaertskompat-Telemetrie — die Funktion selbst bleibt unveraendert (nur das Event-Feld,
    # das ihren Wert traegt, wurde umbenannt).
    import inspect
    from automation.optimizer import sweep
    doc = inspect.getdoc(sweep._family_n_from_proposals) or ""
    assert "Rückwärtskompat" in doc or "Rueckwaertskompat" in doc
