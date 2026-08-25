"""Issue #742 — Sweep-Level-Report-Generator, atomar geschrieben.

Deckt ab:
  - der Report validiert gegen das (Auszugs-)Schema aus der Issue-Beschreibung
  - der Report ist atomar sichtbar (kein ``.tmp``-Rest im Ergebnis-Verzeichnis)
  - ein zweiter, unabhängiger Prozess (``generate_report_for_run``) rekonstruiert denselben
    Report AUSSCHLIESSLICH aus SQLite-Study + Proposal-JSON (Determinismus, kein Live-Lauf nötig)
"""
import json

import optuna
import pytest

from automation.optimizer import report


def _make_study(storage_url: str, study_name: str, n: int = 5, *, run_id: str | None = None):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        eligible = i < 3
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", eligible)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("oos_gate_deltas", {"min_trades": i - 2})
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if eligible else "unevaluable",
            "base": 1.0 + i, "divergence": 0.3 * i, "dd_penalty": 0.25 * i, "param_pen": 0.2 * i,
            "turnover": 0.3 * i, "fold_dispersion": 0.25 * i, "tie_breaker": 0.2 * i,
        })
        # Issue #940/#1106 — die #1088-Identitaetspruefung (``check_report_cohort_coherence``)
        # braucht einen run_id-Stempel auf JEDEM Trial, um eine Study eindeutig diesem Lauf
        # zuzuordnen (siehe test_issue_1086_concurrent_run_isolation.py fuer dasselbe Muster).
        if run_id is not None:
            trial.set_user_attr("run_id", run_id)
        study.tell(trial, float(i))
    return study


def _proposal() -> dict:
    return {
        "strategy": "TestStrat",
        "symbol": "A.ETORO",
        "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {
            "symbol": {
                "deflated_sr0": 0.1,
                "deflated_dsr": 0.8,
                "deflation_dsr_z": 1.2,
                "deflation_n_eligible": 3,
                "deflation_n_family_effective": 3,
                "deflation_n_effective": 3,
            },
        },
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    """Monkeypatcht report.resolve_storage auf eine tmp_path-lokale SQLite-Datei und legt eine
    Study mit realistischen user_attrs an."""
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_A_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, study_name)
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_report_schema_and_atomic_write(wired_storage):
    reports_dir = wired_storage / "reports"
    out_path = report.generate_sweep_report(
        [_proposal()], run_id="testrun1", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=42, cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=reports_dir,
    )

    assert out_path.exists()
    assert out_path.name == "run_testrun1.json"
    assert not list(reports_dir.glob("*.tmp")), "keine Teil-/Tempdatei darf im Ergebnis verbleiben"

    data = json.loads(out_path.read_text("utf-8"))
    assert data["report_schema_version"] == 1
    assert data["run_id"] == "testrun1"
    assert data["wallclock_s"] == 42
    assert data["cli_args"]["strategies"] == "TestStrat"
    assert "git_commit_report" in data and "tournament_config_sha256" in data

    assert len(data["studies"]) == 1
    rec = data["studies"][0]
    assert rec["symbol"] == "A.ETORO"
    assert rec["strategy"] == "TestStrat"
    assert rec["n_trials"] == 5
    assert rec["n_eligible"] == 3
    assert rec["n_evaluable"] == 5
    assert rec["promotion_outcome"] == "REJECTED_ON_HOLDOUT"

    # Issue #785 — decision_chain attribuiert die Ablehnung praezise auf die STUFE, die sie
    # tatsaechlich verursacht hat (REJECT_HOLDOUT_DSR_DROP ⇒ 'deflation', nicht pauschal
    # 'confirm_or_selection' fuer JEDE Holdout-Ablehnung wie vor #785); is_gate ist hier bestanden
    # (n_eligible=3 > 0), erscheint also nicht mehr in der abgeleiteten rejection_chain-Sicht.
    stages = [c["stage"] for c in rec["rejection_chain"]]
    assert stages == ["deflation"]
    assert rec["rejection_chain"][0]["detail"] == "REJECT_HOLDOUT_DSR_DROP"

    # Issue #1001/#1153 (Katalog #1170) Regressionsfix — dieses Alt-Proposal traegt kein
    # ``stage_results`` (Legacy-Fallback in ``_decision_chain``) und ``promote`` ist False
    # (``status='REJECTED_ON_HOLDOUT'``): Stufen VOR der tatsaechlich blockierenden ('deflation')
    # sind seit #1153 NICHT mehr stillschweigend ``True`` ("nachweislich nie ausgewertet" statt
    # einer Ableitungs-Annahme, siehe ``_decision_chain``-Docstring) — nur 'deflation' selbst ist
    # ``False`` (die terminale, tatsaechlich gemessene Ablehnung).
    decision_stages = {c["stage"]: c["passed"] for c in rec["decision_chain"]}
    assert decision_stages["is_gate"] is True
    assert decision_stages["confirm_or_selection"] is None
    assert decision_stages["holdout"] is None
    assert decision_stages["deflation"] is False

    assert "cross_study" in data and "n_family" in data["cross_study"]
    assert isinstance(data["invariant_checks"], list) and data["invariant_checks"]
    assert any(c["name"] == "check_config_key_registry" for c in data["invariant_checks"])
    assert any(c["name"] == "check_sr0_coherence" for c in data["invariant_checks"])


def test_second_process_reconstructs_same_report_from_sqlite_and_proposals(wired_storage):
    """Determinismus-Akzeptanzkriterium: ein Report kann nachträglich, standalone, ohne laufenden
    Sweep, ausschliesslich aus den durablen Artefakten (SQLite-Study + Proposal-JSON)
    rekonstruiert werden — bit-identische studies/cross_study/invariant_checks."""
    live_out = report.generate_sweep_report(
        [_proposal()], run_id="live_run", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=10, cli_args={"strategies": "TestStrat"}, reports_dir=wired_storage / "reports",
    )
    live_data = json.loads(live_out.read_text("utf-8"))

    proposals_dir = wired_storage / "proposals"
    proposals_dir.mkdir()
    (proposals_dir / "proposal_TestStrat_A.ETORO.json").write_text(
        json.dumps(_proposal()), encoding="utf-8")

    # Issue #1252 (GH #1122) — eigener reports_dir (statt des vom Live-Aufruf oben bereits
    # geschriebenen), damit der DORT abgelegte run_fingerprints.jsonl-Index (siehe
    # report._build_report-Docstring zur reports_dir-Isolationskonvention) den Fingerabdruck von
    # "live_run" nicht als Duplikat unter dem hier bewusst ABWEICHENDEN synthetischen
    # "standalone_run"-Namen zurueckmeldet — dieser Test prueft Bit-Identitaet der REKONSTRUKTION,
    # nicht Duplikat-Erkennung ueber zwei echte, unabhaengige Sweeps derselben Eingangsmenge.
    standalone_out = report.generate_report_for_run(
        run_id="standalone_run", proposals_dir=proposals_dir,
        reports_dir=wired_storage / "reports_standalone",
    )
    standalone_data = json.loads(standalone_out.read_text("utf-8"))

    assert standalone_data["studies"] == live_data["studies"]
    assert standalone_data["cross_study"] == live_data["cross_study"]
    # Issue #940/#1106 + #941/#1107 (Katalog #960) Regressionsfix — mehrere Checks (u.a.
    # ``check_report_cohort_coherence``s eigenes ``actual``/``detail``, JEDES ``cohort``-Objekt seit
    # #1107) tragen den ``run_id``-Wert selbst als Inhalt, nicht nur als Lauf-Metadatum — das ist
    # bereits im Kommentar unten als legitim divergierend dokumentiert ("Nur der Lauf-Kontext ...
    # darf legitim divergieren"), wurde hier vor #1106/#1107 aber nicht sichtbar (dieser Vergleich
    # wurde durch einen VORGELAGERTEN Crash nie erreicht). Ersetzt beide run_id-Werte durch denselben
    # Platzhalter VOR dem Vergleich — bit-identisch bis auf den bereits dokumentierten Lauf-Kontext.
    def _without_run_id(checks, run_id):
        text = json.dumps(checks, sort_keys=True).replace(run_id, "<RUN_ID>")
        return json.loads(text)
    assert (_without_run_id(standalone_data["invariant_checks"], standalone_data["run_id"])
           == _without_run_id(live_data["invariant_checks"], live_data["run_id"]))
    # Nur der Lauf-Kontext (run_id/wallclock/cli_args) darf legitim divergieren.
    assert standalone_data["run_id"] != live_data["run_id"]


def test_foreign_run_study_excluded_from_report(tmp_path, monkeypatch):
    """Issue #1023 (Katalog #866) — Regressionstest mit synthetischem Store aus zwei Laeufen: eine
    Study, die vor dem Laufbeginn dieses Sweeps zuletzt optimiert wurde (``study_started_at_utc``
    weit VOR dem ``started_at_utc`` dieses Reports), gehoert zu einem fremden Lauf und darf NICHT
    im Report auftauchen — beobachtete Symptomatik: 98 von 112 Studies eines Ein-Symbol-Laufs
    trugen ``study_started_at_utc`` vom Vortag."""
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()

    old_study_name = "study_OldStrat_OLD_ETORO"
    old_storage_url = f"sqlite:///{sweep_dir / 'old_study.db'}"
    old_study = _make_study(old_storage_url, old_study_name)
    old_study.set_user_attr("study_started_at_utc", "2026-08-11T16:19:34.000+00:00")

    new_study_name = "study_TestStrat_A_ETORO"
    new_storage_url = f"sqlite:///{sweep_dir / 'new_study.db'}"
    new_study = _make_study(new_storage_url, new_study_name, run_id="run_new")
    new_study.set_user_attr("study_started_at_utc", "2026-08-12T04:19:21.000+00:00")

    def _resolve(*, study_name, base_cfg=None):
        return old_storage_url if study_name == old_study_name else new_storage_url

    monkeypatch.setattr(report, "resolve_storage", _resolve)

    old_proposal = {**_proposal(), "strategy": "OldStrat", "symbol": "OLD.ETORO"}
    new_proposal = _proposal()

    reports_dir = tmp_path / "reports"
    out_path = report.generate_sweep_report(
        [old_proposal, new_proposal], run_id="run_new",
        started_at_utc="2026-08-12T04:19:20.000+00:00",
        wallclock_s=2880, cli_args={"strategies": "TestStrat"}, reports_dir=reports_dir,
    )
    data = json.loads(out_path.read_text("utf-8"))

    symbols_in_report = {s["symbol"] for s in data["studies"]}
    assert symbols_in_report == {"A.ETORO"}
    assert "OLD.ETORO" not in symbols_in_report

    assert len(data["studies_excluded_foreign_run"]) == 1
    excluded = data["studies_excluded_foreign_run"][0]
    assert excluded["symbol"] == "OLD.ETORO"
    assert excluded["reason"] == "study_started_before_this_run"

    cohort_check = next(
        c for c in data["invariant_checks"] if c["name"] == "check_report_cohort_coherence")
    assert cohort_check["passed"] is True


def test_all_studies_foreign_run_fails_loud_instead_of_empty_report(tmp_path, monkeypatch):
    """Issue #1023 Akzeptanzkriterium 2 — ist NACH dem Filter keine Study mehr uebrig, obwohl
    Proposals uebergeben wurden, ist das ein Programmierfehler des Aufrufers (falscher run_id/
    started_at_utc), kein leerer Report."""
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    study = _make_study(storage_url, "study_TestStrat_A_ETORO")
    study.set_user_attr("study_started_at_utc", "2026-08-11T16:19:34.000+00:00")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    with pytest.raises(RuntimeError, match=r"\[#1023\]"):
        report.generate_sweep_report(
            [_proposal()], run_id="run_new",
            started_at_utc="2026-08-12T04:19:20.000+00:00",
            wallclock_s=2880, cli_args={}, reports_dir=tmp_path / "reports",
        )


def test_holdout_metrics_fall_back_to_global_route_when_symbol_route_empty(wired_storage):
    """Issue #1030 (Katalog #866) — ``confirm_per_symbol_promotion`` liefert fuer
    HOLDOUT_NO_ELIGIBLE_TRIALS-Ablehnungen mit versuchtem globalem Default ``metrics_symbol={}``
    UND ``metrics_global=_metrics_dict(m_global)`` (ein echter, wenn auch abgelehnter Backtest lief
    auf dem globalen Vektor). Vor dem Fix brach JEDES holdout_*-Feld (inkl.
    holdout_profit_factor_raw) still auf None ab, weil report.py nur ``holdout['symbol']`` las."""
    proposal = {
        "strategy": "TestStrat",
        "symbol": "A.ETORO",
        "status": "REJECTED_ON_HOLDOUT",
        "holdout_reject_detail": "REJECT_NO_EDGE_OVER_GLOBAL",
        "is_rejection_detail": "REJECT_NO_EDGE_OVER_GLOBAL",
        "holdout": {
            "symbol": {},
            "global": {
                "oos_total_return": 0.02,
                "oos_expectancy": 0.001,
                "oos_win_rate": 0.5,
                "oos_profit_factor": 15.0,
                "oos_profit_factor_censored": True,
                "oos_profit_factor_raw": 27.3,
            },
        },
    }
    out_path = report.generate_sweep_report(
        [proposal], run_id="testrun_global_fallback", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=42, cli_args={}, reports_dir=wired_storage / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    rec = data["studies"][0]
    assert rec["holdout_route"] == "global"
    assert rec["holdout_profit_factor_raw"] == 27.3
    assert rec["holdout_profit_factor_censored"] is True
    assert rec["holdout_total_return"] == 0.02


def test_holdout_metrics_prefer_symbol_route_when_present(wired_storage):
    """Eine vorhandene Symbol-Route hat weiterhin Vorrang vor der globalen — der Fallback greift
    NUR, wenn die Symbol-Route wirklich leer ist."""
    proposal = _proposal()
    proposal["holdout"]["global"] = {"oos_total_return": 0.99}
    out_path = report.generate_sweep_report(
        [proposal], run_id="testrun_symbol_route", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=42, cli_args={}, reports_dir=wired_storage / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    rec = data["studies"][0]
    assert rec["holdout_route"] == "symbol"
    assert rec["holdout_total_return"] != 0.99


def test_generate_report_for_run_ignores_non_symbol_proposals(wired_storage):
    """proposal_{strategy}.json (kein 'symbol'-Feld, export_no_viable_proposal/export_proposal)
    muss von der Standalone-Discovery uebersprungen werden."""
    proposals_dir = wired_storage / "proposals"
    proposals_dir.mkdir()
    (proposals_dir / "proposal_TestStrat_A.ETORO.json").write_text(
        json.dumps(_proposal()), encoding="utf-8")
    (proposals_dir / "proposal_GlobalStrat.json").write_text(
        json.dumps({"strategy": "GlobalStrat", "status": "NO_VIABLE_TRIAL"}), encoding="utf-8")

    out_path = report.generate_report_for_run(
        run_id="mixed_run", proposals_dir=proposals_dir, reports_dir=wired_storage / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    assert len(data["studies"]) == 1
    assert data["studies"][0]["strategy"] == "TestStrat"
