"""Issue #1016 (Katalog #858, Pitfall #349) — ``fail_fast_min_symbols``/``fail_fast_min_offending_
symbols`` wurden gegen Vollläufe über 143 Symbole kalibriert (#877). Bei GENAU EINEM geplanten
Symbol ist ``len(completed_symbols) >= fail_fast_min_symbols`` (Default 2) strukturell unerreichbar
— die Fail-Fast-Probe läuft NIE, unabhängig vom Zustand der Studies. Selbst wenn sie liefe, wäre
``len(offending_symbols) >= fail_fast_min_offending_symbols`` (Default 2) ebenfalls unerreichbar
(nie mehr als 1 Symbol vorhanden) — "Schutz aus", nicht "Schutz mild", genau in dem Modus, in dem
ein einzelnes Paar für den Kapitaleinsatz feingetunt wird.

Dieser Test deckt die reine Entscheidungsfunktion ``sweep._fail_fast_systemic_verdict`` ab (die
STUDY-Quote-Ersatzregel für n_symbols_planned==1) sowie ``sweep.main``'s Read-Back-Patch von
``run_status`` auf 'complete_with_blocking_invariants'.
"""
import json

from automation.optimizer import sweep


# ── _fail_fast_systemic_verdict (reine Entscheidungsfunktion) ──────────────────────────────────

def _verdict(**overrides):
    kwargs = dict(
        n_symbols_planned=1, offending_pairs={}, offending_symbols=set(),
        total_studies_single_symbol=14, min_offending_symbols=2,
        min_offending_studies=3, min_offending_studies_frac=0.25, policy="quarantine",
    )
    kwargs.update(overrides)
    return sweep._fail_fast_systemic_verdict(**kwargs)


def test_multi_symbol_run_uses_original_symbol_spread_rule():
    """Regressionsschutz: fuer n_symbols_planned > 1 bleibt das #877-Verhalten bit-identisch."""
    systemic, policy = _verdict(
        n_symbols_planned=5, offending_pairs={"S/A.ETORO": 1}, offending_symbols={"A.ETORO"},
        min_offending_symbols=2, policy="quarantine")
    assert systemic is False
    assert policy == "quarantine"

    systemic, policy = _verdict(
        n_symbols_planned=5,
        offending_pairs={"S/A.ETORO": 1, "S/B.ETORO": 1},
        offending_symbols={"A.ETORO", "B.ETORO"},
        min_offending_symbols=2, policy="quarantine")
    assert systemic is True
    assert policy == "quarantine"  # policy bleibt UNVERAENDERT, nur 'abort' wuerde tatsaechlich abbrechen


def test_single_symbol_below_study_quota_is_not_systemic():
    """5 von 14 Studies (issue-text-Beispiel selbst) LIEGT ueber der Quote -- hier ein Fall
    UNTERHALB (2 von 14, weder >= 3 absolut noch >= 25 % = 3.5)."""
    systemic, policy = _verdict(
        offending_pairs={"S1/A.ETORO": 1, "S2/A.ETORO": 1}, total_studies_single_symbol=14)
    assert systemic is False
    assert policy == "quarantine"  # unveraendert, da nicht systemisch


def test_single_symbol_five_of_fourteen_studies_is_systemic_and_forces_abort():
    """Der Kern-Fall aus #1016: 'Ein Lauf, in dem 5 von 14 Studies unter der Mindestverfuegbarkeit
    liegen, muss abbrechen.' 5 >= min_offending_studies (3) -- UNBEDINGT abort, auch wenn die
    konfigurierte Politik 'quarantine' ist (kein Ausweichen moeglich, es gibt nur dieses eine
    Symbol)."""
    offending = {f"S{i}/A.ETORO": 1 for i in range(5)}
    systemic, policy = _verdict(
        offending_pairs=offending, total_studies_single_symbol=14, policy="quarantine")
    assert systemic is True
    assert policy == "abort"


def test_single_symbol_absolute_threshold_alone_triggers():
    """>= min_offending_studies (3) allein genuegt, auch wenn die Quote (25 %) bei einer sehr
    grossen Study-Zahl fuer diese absolute Zahl nicht erreicht waere."""
    offending = {f"S{i}/A.ETORO": 1 for i in range(3)}
    systemic, policy = _verdict(
        offending_pairs=offending, total_studies_single_symbol=100,
        min_offending_studies=3, min_offending_studies_frac=0.25)
    assert systemic is True
    assert policy == "abort"


def test_single_symbol_fraction_threshold_alone_triggers():
    """Ein Symbol mit wenigen Strategien (< min_offending_studies absolut), bei dem die 25%-Quote
    trotzdem erreicht wird (z. B. 1 von 3 Studies)."""
    systemic, policy = _verdict(
        offending_pairs={"S1/A.ETORO": 1}, total_studies_single_symbol=3,
        min_offending_studies=3, min_offending_studies_frac=0.25)
    assert systemic is True
    assert policy == "abort"


def test_single_symbol_no_offenders_is_not_systemic():
    systemic, policy = _verdict(offending_pairs={}, total_studies_single_symbol=14)
    assert systemic is False
    assert policy == "quarantine"


def test_single_symbol_zero_total_studies_never_divides_by_zero():
    """Defensiv: total_studies_single_symbol==0 (sollte praktisch nie vorkommen) darf nicht
    crashen — nur die absolute Schwelle bleibt wirksam."""
    systemic, policy = _verdict(
        offending_pairs={"S1/A.ETORO": 1, "S2/A.ETORO": 1, "S3/A.ETORO": 1},
        total_studies_single_symbol=0, min_offending_studies=3)
    assert systemic is True  # absolute Schwelle (3) allein greift bereits


# ── optimizer.json Config-Defaults ───────────────────────────────────────────────────────────────

def test_config_defaults_match_issue_proposal():
    cfg = json.loads(open("automation/config/optimizer.json", encoding="utf-8").read())
    assert cfg["fail_fast_min_offending_studies"] == 3
    assert cfg["fail_fast_min_offending_studies_frac"] == 0.25
    for key in ("fail_fast_min_offending_studies", "fail_fast_min_offending_studies_frac"):
        assert key in cfg["_schema"]["fields"]


# ── run_status='completed_invalid' read-back patch ───────────────────────────────────────────────

def test_run_status_patch_upgrades_complete_with_blocking_fails(tmp_path):
    """Issue #1016 Fix Punkt 2 — die Funktion, die sweep.main() nach generate_sweep_report
    aufruft: ein geschriebener Report mit >= 1 severity='blocking'-FAIL wird auf 'completed_invalid'
    nachkorrigiert, atomar, im Report-Artefakt selbst.

    Issue #1037/#1186 — umbenannt von 'complete_with_blocking_invariants' auf 'completed_invalid':
    derselbe kanonische String, den auch der ANDERE Downgrade-Pfad (Symbol-Loop in sweep.main(),
    ``run_status == 'aborted_invariant' and symbols_completed >= symbols_planned``) fuer denselben
    Faktenstand traegt -- vor #1037 divergierten diese beiden Pfade auf zwei verschiedene Strings."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "invariant_checks": [
            {"name": "check_holding_time_cap", "passed": False, "severity": "blocking"},
            {"name": "check_reward_term_variance", "passed": False, "severity": "medium"},
        ],
    }), "utf-8")

    new_status = sweep._downgrade_run_status_for_blocking_invariants(report_path)

    assert new_status == "completed_invalid"
    assert json.loads(report_path.read_text("utf-8"))["run_status"] == "completed_invalid"


def test_run_status_patch_leaves_clean_report_unmodified(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "invariant_checks": [
            {"name": "check_reward_term_variance", "passed": False, "severity": "medium"},
            {"name": "check_holding_time_cap", "passed": True, "severity": "blocking"},
        ],
    }), "utf-8")

    new_status = sweep._downgrade_run_status_for_blocking_invariants(report_path)

    assert new_status == "complete"
    assert json.loads(report_path.read_text("utf-8"))["run_status"] == "complete"


def test_run_status_patch_is_fail_open_on_broken_report(tmp_path):
    """Ein defektes/nicht existentes Report-Artefakt darf run_status nicht verschlechtern, nur
    weil die Nachpruefung selbst fehlschlaegt."""
    missing_path = tmp_path / "does_not_exist.json"
    assert sweep._downgrade_run_status_for_blocking_invariants(missing_path) == "complete"
