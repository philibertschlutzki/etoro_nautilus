"""Issue #794 — kontinuierliche Trial-Retention statt Wirkungslosigkeit hinter der Phase-1-Barriere.

Root-Cause (Issue-Katalog #794-#800): ``retention.prune_completed_trial_dirs`` (#733) wird bislang
ausschliesslich aus Phase 2 (``sweep._run_confirm_and_export``) aufgerufen; Phase 2 beginnt erst,
nachdem ALLE Studies eines Sweeps abgeschlossen sind (``executor.map(_run_optimize, pairs)``, eine
globale Barriere ueber alle Paare). Zwischen dem Abschluss der ersten Study und dem Beginn von
Phase 2 liegen bei einem grossen Sweep Stunden, in denen kein einziges Trial-Verzeichnis geloescht
wird. Diese Tests decken die drei neuen Bausteine ab:

  1. ``retention.release_trial_dir`` — der Lösch-Primitiv fuer EIN Verzeichnis.
  2. ``retention.release_non_best_trial_dirs`` — der Optuna-Callback-Kern (Trial-Ebene).
  3. ``retention.prune_orphaned_trial_dirs`` — der Lauf-Start-Purge.
  4. Ein Integrationstest ueber eine echte ``study.optimize``-Schleife mit
     ``run_optimization.retention_callback`` zeigt, dass die maximale GLEICHZEITIGE Zahl von
     Trial-Verzeichnissen klein und beschraenkt bleibt statt monoton mit ``n_trials`` zu wachsen.
"""
import json
from pathlib import Path

import optuna
import pytest

from automation.optimizer import retention


def _make_trial_dir(root: Path, study_name: str, trial_number: int) -> Path:
    trial_dir = root / study_name / f"trial_{trial_number:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "experiment_manifest.json").write_text("{}", encoding="utf-8")
    (trial_dir / "logs").mkdir(exist_ok=True)
    (trial_dir / "logs" / "backtest_stderr.log").write_text("boom", encoding="utf-8")
    return trial_dir


# ---------------------------------------------------------------------------
# release_trial_dir — Primitiv
# ---------------------------------------------------------------------------

def test_release_trial_dir_deletes_when_not_keep(tmp_path):
    trial_dir = _make_trial_dir(tmp_path, "study_x", 0)
    released = retention.release_trial_dir(trial_dir, keep=False)
    assert released is True
    assert not trial_dir.exists()


def test_release_trial_dir_preserves_when_keep(tmp_path):
    trial_dir = _make_trial_dir(tmp_path, "study_x", 0)
    released = retention.release_trial_dir(trial_dir, keep=True)
    assert released is False
    assert trial_dir.exists()
    # inklusive logs/ (Forensik eines fehlgeschlagenen Trials, siehe Akzeptanzkriterium #794)
    assert (trial_dir / "logs" / "backtest_stderr.log").exists()


def test_release_trial_dir_idempotent_on_missing_dir(tmp_path):
    missing = tmp_path / "study_x" / "trial_0099"
    assert retention.release_trial_dir(missing, keep=False) is False


# ---------------------------------------------------------------------------
# release_non_best_trial_dirs — der Callback-Kern
# ---------------------------------------------------------------------------

def test_release_non_best_keeps_only_current_best_trial(tmp_path):
    study_name = "study_SmaStrategy_TSLA_ETORO"
    study = optuna.create_study(study_name=study_name, direction="maximize")
    for i, value in enumerate([1.0, 5.0, 3.0]):
        t = study.ask()
        study.tell(t, value)
        _make_trial_dir(tmp_path, study_name, i)
        retention.release_non_best_trial_dirs(study, work_dir=tmp_path)

    study_dir = tmp_path / study_name
    remaining = {p.name for p in study_dir.glob("trial_*")}
    # trial 1 (value=5.0) ist durchgehend der beste ⇒ einziges ueberlebendes Verzeichnis.
    assert remaining == {"trial_0001"}


def test_release_non_best_switches_when_a_new_best_appears(tmp_path):
    study_name = "study_SmaStrategy_AAA_ETORO"
    study = optuna.create_study(study_name=study_name, direction="maximize")

    t0 = study.ask(); study.tell(t0, 1.0)
    _make_trial_dir(tmp_path, study_name, 0)
    retention.release_non_best_trial_dirs(study, work_dir=tmp_path)
    assert (tmp_path / study_name / "trial_0000").exists()

    # neuer, besserer Trial ⇒ trial_0000 (der VORHERIGE beste) wird beim Wechsel freigegeben.
    t1 = study.ask(); study.tell(t1, 9.0)
    _make_trial_dir(tmp_path, study_name, 1)
    retention.release_non_best_trial_dirs(study, work_dir=tmp_path)

    study_dir = tmp_path / study_name
    remaining = {p.name for p in study_dir.glob("trial_*")}
    assert remaining == {"trial_0001"}, "der abgeloeste vorherige beste Trial muss freigegeben werden"


def test_release_non_best_keeps_failed_trials_for_forensics(tmp_path):
    study_name = "study_SmaStrategy_FAIL_ETORO"
    study = optuna.create_study(study_name=study_name, direction="maximize")

    t_ok = study.ask(); study.tell(t_ok, 2.0)
    _make_trial_dir(tmp_path, study_name, 0)

    t_fail = study.ask()
    study.tell(t_fail, state=optuna.trial.TrialState.FAIL)
    _make_trial_dir(tmp_path, study_name, 1)

    retention.release_non_best_trial_dirs(study, work_dir=tmp_path)

    study_dir = tmp_path / study_name
    remaining = {p.name for p in study_dir.glob("trial_*")}
    # trial 0 ist der (einzige) beste COMPLETE Trial, trial 1 ist FAIL ⇒ beide bleiben.
    assert remaining == {"trial_0000", "trial_0001"}


def test_release_non_best_no_error_with_zero_completed_trials(tmp_path):
    """study.best_trial wirft ValueError ohne abgeschlossenen Trial — muss defensiv abgefangen
    werden (kein Crash, keine faelschliche Loeschung eines laufenden PRUNED-Trials)."""
    study_name = "study_SmaStrategy_EMPTY_ETORO"
    study = optuna.create_study(study_name=study_name, direction="maximize")
    t = study.ask()
    study.tell(t, state=optuna.trial.TrialState.PRUNED)
    _make_trial_dir(tmp_path, study_name, 0)

    retention.release_non_best_trial_dirs(study, work_dir=tmp_path)

    assert (tmp_path / study_name / "trial_0000").exists()


# ---------------------------------------------------------------------------
# prune_orphaned_trial_dirs — Lauf-Start-Purge
# ---------------------------------------------------------------------------

def test_prune_orphaned_trial_dirs_across_multiple_studies(tmp_path):
    _make_trial_dir(tmp_path, "study_A", 0)
    _make_trial_dir(tmp_path, "study_A", 1)
    kept = _make_trial_dir(tmp_path, "study_B", 0)

    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    (champions_dir / "champion_B.json").write_text(json.dumps({
        "provenance": {"source_trial_dir": str(kept)}
    }), encoding="utf-8")

    pruned = retention.prune_orphaned_trial_dirs(tmp_path)

    assert len(pruned) == 2
    assert not (tmp_path / "study_A" / "trial_0000").exists()
    assert not (tmp_path / "study_A" / "trial_0001").exists()
    assert kept.exists()


def test_prune_orphaned_trial_dirs_dry_run_deletes_nothing(tmp_path):
    trial_dir = _make_trial_dir(tmp_path, "study_A", 0)
    pruned = retention.prune_orphaned_trial_dirs(tmp_path, dry_run=True)
    assert len(pruned) == 1
    assert trial_dir.exists()


def test_prune_orphaned_trial_dirs_noop_on_missing_work_dir(tmp_path):
    assert retention.prune_orphaned_trial_dirs(tmp_path / "does_not_exist") == []


# ---------------------------------------------------------------------------
# Integration: study.optimize + run_optimization.retention_callback
# ---------------------------------------------------------------------------

def test_optimize_symbol_keeps_peak_trial_dir_count_bounded(tmp_path, monkeypatch):
    """Integrationstest ueber eine echte study.optimize-Schleife (20 Trials, voll gemockter
    Backtest): die maximale GLEICHZEITIGE Zahl von Trial-Verzeichnissen bleibt klein, statt
    monoton mit n_trials zu wachsen (Akzeptanzkriterium #794: sägezahnt statt monoton)."""
    from automation.optimizer import run_optimization as ro
    from automation.optimizer import trial_config

    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))

    peak_counts = []

    def _fake_run_backtest(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        # abwechselnd unterschiedliche Rewards, damit der "beste Trial" wechselt.
        idx = int(str(trial_dir).rsplit("_", 1)[-1])
        sortino = float((idx * 37) % 11)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino],
            "oos_metrics": {"sortino_ratio": sortino, "max_drawdown": 0.05}}}), "utf-8")
        # Zaehlt ALLE derzeit existierenden trial_*-Verzeichnisse ueber JEDE Study in tmp_path.
        peak_counts.append(sum(1 for _ in tmp_path.glob("study_*/trial_*")))
        return out

    monkeypatch.setattr(ro, "run_backtest", _fake_run_backtest)

    ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=20)

    # Ohne kontinuierliche Retention waechst dies monoton auf 20; mit #794 bleibt es klein
    # (der Callback laeuft NACH jedem Trial, greift also erst ab dem naechsten Trial-Aufruf).
    assert max(peak_counts) <= 3, f"Spitzenlast an Trial-Verzeichnissen zu hoch: {peak_counts}"
