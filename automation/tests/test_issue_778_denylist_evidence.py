"""Issue #778 (P2) — Auto-Denylist würde im Folgelauf ~240 Paare still überspringen, ohne dass
eines je mit vollem Budget lief.

Root-Cause: ``recommend_diagnosis_action`` eskalierte ein Paar auf ``'denylist'``, sobald in einem
Vorlauf bereits ``'search_space_override'`` empfohlen wurde und sich nichts geändert hat (#699).
AdxAtrMomentum (116/124 Symbole) und TrendPullback (100/124) wurden bisher AUSSCHLIESSLICH nach 16
Zufallsziehungen (13 % Budgetausführung, #769) beurteilt — eine dauerhafte Deaktivierung auf dieser
Evidenzbasis ist verfrüht.

Fix: ``'signal_sparse'``/``'hold_duration'`` (PARAMETERABHÄNGIG) eskaliert NIE mehr auf 'denylist'.
``'signal_absent'`` (PARAMETERUNABHÄNGIG) eskaliert NUR, wenn der Cache-Eintrag des Vorlaufs
``budget_executed_fraction >= 0.9`` (#770) UND ``n_runs_confirmed >= 2`` trägt. Cache-Einträge
erhalten ``first_seen_run_id``/``n_runs_confirmed``; übersprungene Paare erscheinen im #742-Report
als eigene Sektion mit Begründung und Evidenzstand.
"""
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
)
from automation.optimizer import report as _report


# ── Akzeptanzkriterium #778/1: binding_cause = signal_sparse ⇒ nie Eskalation auf denylist ────────
def test_signal_sparse_never_escalates_regardless_of_evidence():
    for budget_frac, n_confirmed, has_override in [
        (None, 0, False), (0.16, 0, False), (0.95, 5, False), (0.95, 5, True), (1.0, 100, True),
    ]:
        rec = recommend_diagnosis_action(
            "TrendPullbackStrategy", "TSLA.ETORO",
            {"binding_cause": "signal_sparse", "median_oos_trades": 0, "median_is_trades": 3},
            has_existing_override=has_override,
            budget_executed_fraction=budget_frac, n_runs_confirmed=n_confirmed,
        )
        assert rec["action"] != "denylist"


def test_hold_duration_never_escalates_regardless_of_evidence():
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "hold_duration", "median_oos_trades": 0, "median_is_trades": 3},
        has_existing_override=True, budget_executed_fraction=1.0, n_runs_confirmed=100,
    )
    assert rec["action"] != "denylist"


# ── Akzeptanzkriterium #778/2: budget_executed_fraction = 0,16 ⇒ keine Eskalation ────────────────
def test_low_budget_execution_prevents_signal_absent_escalation():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.16, n_runs_confirmed=5,
    )
    assert rec["action"] == "none"


def test_missing_budget_execution_prevents_escalation():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=None, n_runs_confirmed=5,
    )
    assert rec["action"] == "none"


def test_high_budget_but_single_run_prevents_escalation():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.95, n_runs_confirmed=1,
    )
    assert rec["action"] == "none"


# ── Akzeptanzkriterium #778/3: zwei bestätigte Läufe mit signal_absent + vollem Budget ⇒ Eskalation
def test_two_confirmed_runs_with_full_budget_escalates_to_denylist():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.9, n_runs_confirmed=2,
    )
    assert rec["action"] == "denylist"


def test_signal_quality_unaffected_by_the_778_evidence_gate():
    """'signal_quality' (ALLE Trials evaluiert, kein Budget-Zweifel) war zum Zeitpunkt von #778
    unveraendert direkt 'denylist' — der #778-Evidenz-Gate galt damals spezifisch der #769-Ursache
    'signal_absent'. SEIT #830 unterliegt 'signal_quality' einem EIGENEN (aber strukturell
    analogen) Evidenzregime; siehe test_issue_830_signal_quality_deprioritized.py fuer das
    aktuelle Verhalten (n_runs_confirmed=0 -> 'none', ohne Bestaetigung, keine sofortige
    Deaktivierung mehr nach einer einzigen Beobachtung)."""
    rec = recommend_diagnosis_action(
        "HourlyMeanReversionStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_quality", "median_oos_trades": 214, "median_is_trades": None},
    )
    assert rec["action"] == "none"


# ── record_diagnosed_pair: first_seen_run_id / n_runs_confirmed ───────────────────────────────────
def test_first_run_sets_n_runs_confirmed_to_one_and_stamps_first_seen_run_id(tmp_path):
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.5, n_runs_confirmed=0,
    )
    record_diagnosed_pair(rec, work_dir=tmp_path, run_id="run_001")
    entry = load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    assert entry["n_runs_confirmed"] == 1
    assert entry["first_seen_run_id"] == "run_001"


def test_repeated_confirmation_increments_and_preserves_first_seen_run_id(tmp_path):
    rec1 = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.5, n_runs_confirmed=0,
    )
    record_diagnosed_pair(rec1, work_dir=tmp_path, run_id="run_001")

    prior = load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    rec2 = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.95, n_runs_confirmed=prior["n_runs_confirmed"],
    )
    assert rec2["action"] == "none"    # noch nicht 2 bestaetigte Laeufe MIT ausreichendem Budget
    record_diagnosed_pair(rec2, work_dir=tmp_path, run_id="run_002")

    entry = load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    assert entry["n_runs_confirmed"] == 2
    assert entry["first_seen_run_id"] == "run_001"    # bleibt der URSPRUENGLICHE Fund

    # Dritter Lauf: jetzt liegt ausreichende Evidenz vor (2 bestaetigte Laeufe + volles Budget).
    rec3 = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.95, n_runs_confirmed=entry["n_runs_confirmed"],
    )
    assert rec3["action"] == "denylist"


def test_changing_binding_cause_resets_confirmation_count(tmp_path):
    rec1 = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.95, n_runs_confirmed=1,
    )
    record_diagnosed_pair(rec1, work_dir=tmp_path, run_id="run_001")

    rec2 = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 3, "median_is_trades": 30},
    )
    record_diagnosed_pair(rec2, work_dir=tmp_path, run_id="run_002")
    entry = load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    assert entry["n_runs_confirmed"] == 1
    assert entry["first_seen_run_id"] == "run_002"


def test_none_binding_cause_still_not_persisted(tmp_path):
    rec = recommend_diagnosis_action("SmaCrossoverStrategy", "TSLA.ETORO", {"binding_cause": "none"})
    record_diagnosed_pair(rec, work_dir=tmp_path)
    assert load_diagnosed_pairs_cache(tmp_path) == {}


# ── Umsetzungspunkt 3: übersprungene Paare als eigene Report-Sektion ──────────────────────────────
def test_report_section_lists_only_denylisted_pairs_with_evidence(monkeypatch):
    fake_cache = {
        ("AdxAtrMomentumStrategy", "TSLA.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO", "action": "denylist",
            "binding_cause": "signal_absent", "budget_executed_fraction": 0.95,
            "n_runs_confirmed": 2, "first_seen_run_id": "run_001",
        },
        ("TrendPullbackStrategy", "TSLA.ETORO"): {
            "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
            "action": "search_space_override", "binding_cause": "signal_sparse",
        },
    }
    monkeypatch.setattr("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
                        lambda *a, **k: fake_cache)
    section = _report._diagnosed_pairs_skipped_section()
    assert len(section) == 1
    assert section[0]["strategy"] == "AdxAtrMomentumStrategy"
    assert section[0]["binding_cause"] == "signal_absent"
    assert section[0]["budget_executed_fraction"] == 0.95
    assert section[0]["n_runs_confirmed"] == 2
    assert section[0]["first_seen_run_id"] == "run_001"


def test_report_section_is_wired_into_cross_study(monkeypatch):
    monkeypatch.setattr("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
                        lambda *a, **k: {})
    report_dict = _report._build_report(
        [], run_id="testrun", started_at_utc=None, wallclock_s=None, cli_args=None)
    assert "diagnosed_pairs_skipped" in report_dict["cross_study"]
    assert report_dict["cross_study"]["diagnosed_pairs_skipped"] == []


def test_none_action_with_real_binding_cause_is_now_persisted(tmp_path):
    """Issue #778 — eine 'none'-Empfehlung MIT echter Ursache (z. B. unzureichende Evidenz für
    'signal_absent') wird gespeichert, damit n_runs_confirmed ueberhaupt akkumulieren kann."""
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.5, n_runs_confirmed=0,
    )
    assert rec["action"] == "none"
    record_diagnosed_pair(rec, work_dir=tmp_path)
    assert load_diagnosed_pairs_cache(tmp_path) != {}
