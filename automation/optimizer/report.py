"""automation/optimizer/report.py
==================================
Issue #742 — Sweep-Level-Forensik-Report, atomar geschrieben (``manifest.write_json_atomic``,
Issue #720/#742).

Bislang rekonstruierte jede Forensik-Sitzung den Gesamtüberblick eines Sweep-Laufs manuell aus
verstreuten Optuna-SQLite-Studies + Proposal-JSONs (siehe ``automation/optimizer/sweep.py``,
``confirm.export_symbol_proposal``). Dieses Modul buendelt genau diese beiden bereits DURABLEN
Quellen zu GENAU EINER Datei ``data/optimizer/reports/run_<run_id>.json`` — der erste und oft
einzige Lese-Schritt einer künftigen Forensik-Sitzung.

Wiederverwendung statt Doppel-Bau: ``manifest.git_commit``/``sha256_file`` für Provenienz,
``sweep._family_n_from_proposals`` für die Cross-Study-Kennzahl, ``run_optimization.
study_shows_gradient_signal``/``_sanitize``/``resolve_storage`` für die Study-Metriken/Storage-
Auflösung, ``invariants.py`` (#743) für die mathematischen Regressionswächter.

Zwei Aufrufpfade, EIN gemeinsamer Kern (``_build_report``), garantieren Determinismus:
  - ``generate_sweep_report`` — am Ende von ``sweep.main()``, mit den frisch geschriebenen
    Proposal-Pfaden DIESES Laufs.
  - ``generate_report_for_run`` — standalone/nachträglich: entdeckt ALLE aktuell auf der Platte
    liegenden ``proposal_*.json`` (keine laufende Sweep-Orchestrierung nötig) und lädt jede
    referenzierte Study frisch aus ihrer SQLite-Datei — funktioniert auch für einen Sweep, dessen
    Live-Log längst durch die 7-Tage-Rotation gelöscht wurde.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import optuna

from automation.log_manager import emit_execution_event
from automation.optimizer import invariants as _inv
from automation.optimizer.manifest import WORK, git_commit, catalog_fingerprint, sha256_file, write_json_atomic
from automation.optimizer.run_optimization import (
    _sanitize, resolve_storage, study_shows_gradient_signal, _modelled_trials,
    _constraint_violation_progress,
)
from automation.optimizer.sweep import _family_n_from_proposals
from automation.optimizer.trial_config import config_dir

REPORT_SCHEMA_VERSION = 1
REPORTS_DIR = WORK / "reports"

_log = logging.getLogger("optimizer")


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text("utf-8")) or {}
    except (OSError, ValueError):
        return None


def _gradient_tau(base_cfg: Path | None = None) -> float:
    """Dieselbe Config-Quelle/Default wie ``run_optimization._emit_study_summary``."""
    tau = 1e-3
    try:
        opt_path = (base_cfg or config_dir()) / "optimizer.json"
        if opt_path.exists():
            tau = float((json.loads(opt_path.read_text("utf-8")) or {}).get(
                "tier_escalation_min_signal", tau))
    except (OSError, ValueError, TypeError):
        pass
    return tau


def _gradient_tau_c(base_cfg: Path | None = None) -> float:
    """Issue #754 — dieselbe Config-Quelle/Default wie ``run_optimization._emit_study_summary`` fuer
    den Constraint-Fortschritts-Arm des Gradienten-Gates."""
    tau_c = 0.05
    try:
        opt_path = (base_cfg or config_dir()) / "optimizer.json"
        if opt_path.exists():
            tau_c = float((json.loads(opt_path.read_text("utf-8")) or {}).get(
                "tier_escalation_min_constraint_progress", tau_c))
    except (OSError, ValueError, TypeError):
        pass
    return tau_c


def _load_study_for_proposal(proposal: dict):
    """Lädt die zu einem Per-Symbol-Proposal gehörige Optuna-Study FRISCH aus ihrer SQLite-Datei
    (dieselbe ``study_name``-/Storage-Konvention wie ``sweep.run_per_symbol_sweep``)."""
    strategy = proposal.get("strategy")
    symbol = proposal.get("symbol")
    if not strategy or not symbol:
        return None
    study_name = f"study_{strategy}_{_sanitize(symbol)}"
    storage = resolve_storage(study_name=study_name)
    try:
        return optuna.load_study(study_name=study_name, storage=storage)
    except Exception:
        _log.warning("[#742] Study '%s' konnte nicht geladen werden (Storage=%s).",
                     study_name, storage, exc_info=True)
        return None


def _rejection_chain(proposal: dict) -> list[dict[str, Any]]:
    """Baut die Ablehnungs-Kette aus den bereits (#654/#671) korrekt attribuierten Proposal-
    Feldern: der modale IS-Study-Grund (Sekundärdiagnose) gefolgt von der TATSÄCHLICHEN Confirm-/
    Holdout-/Selektions-Ursache (die Promotion-blockierende Grösse)."""
    chain: list[dict[str, Any]] = []
    dominant_is = proposal.get("dominant_is_rejection_detail")
    if dominant_is:
        chain.append({"stage": "is_gate", "detail": dominant_is})
    holdout_detail = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    if holdout_detail:
        chain.append({"stage": "confirm_or_selection", "detail": holdout_detail})
    return chain


def _study_record(proposal: dict, study) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743)."""
    trials = list(getattr(study, "trials", None) or []) if study is not None else []
    trial_attrs = [dict(getattr(t, "user_attrs", {}) or {}) for t in trials]

    n_trials = len(trials)
    n_evaluable = sum(1 for a in trial_attrs if a.get("oos_evaluated") is True)
    n_eligible = sum(1 for a in trial_attrs if a.get("oos_eligible") is True)
    p_eligible = round(n_eligible / n_trials, 4) if n_trials else 0.0
    coherence_violations = sum(1 for a in trial_attrs if a.get("oos_coherence_violation") is True)

    best_reward = None
    if study is not None:
        try:
            best_reward = study.best_value
        except Exception:
            best_reward = None

    feasible_rewards = [
        float(t.value) for t in trials
        if getattr(t, "user_attrs", {}).get("oos_eligible") is True
        and isinstance(getattr(t, "value", None), (int, float))
    ]
    # Issue #753/#754/#755 — dieselbe TRI-STATE-Logik wie ``_emit_study_summary``: eine vorzeitig
    # gestoppte Study (floor_plateau_warned/zero_eligible_plateau_warned) liefert gradient_signal=
    # None (Eskalationsfrage unbeantwortet), sonst reward- ODER constraint-fortschritts-basiert.
    study_user_attrs = getattr(study, "user_attrs", None) or {} if study is not None else {}
    n_startup_for_report = study_user_attrs.get("n_startup_trials")
    if n_startup_for_report is not None:
        modelled = _modelled_trials(trials, int(n_startup_for_report))
    else:
        modelled = trials
    _, _, constraint_improvement_rate = _constraint_violation_progress(modelled)
    early_stopped = bool(study_user_attrs.get("floor_plateau_warned")) or bool(
        study_user_attrs.get("zero_eligible_plateau_warned"))
    if early_stopped:
        gradient_signal = None
    else:
        gradient_signal = study_shows_gradient_signal(
            feasible_rewards, p_eligible, _gradient_tau(),
            constraint_improvement_rate=constraint_improvement_rate, tau_c=_gradient_tau_c())

    near_miss_deltas: dict[str, Any] = {}
    scored = [t for t in trials if isinstance(getattr(t, "value", None), (int, float))]
    if scored:
        best_trial = max(scored, key=lambda t: t.value)
        near_miss_deltas = dict(getattr(best_trial, "user_attrs", {}).get("oos_gate_deltas") or {})

    holdout_metrics = (proposal.get("holdout") or {}).get("symbol") or {}
    checks = [
        _inv.check_sr0_coherence(holdout_metrics),
        _inv.check_n_family_consistency(holdout_metrics),
        _inv.check_rejection_chain_completeness(proposal),
        _inv.check_reward_term_variance(trial_attrs),
        # Issue #756 — nach der Log-Return-Umstellung ist eine verbleibende Kohärenzverletzung ein
        # echter Bug, kein erwartetes Restrauschen mehr; harter Regressionswächter statt WARNING.
        _inv.check_log_return_coherence(trial_attrs),
        # Issue #759 — Missing-Data-Sentinel-Kollaps-Regressionswächter (oos_win_rate).
        _inv.check_metric_sentinel_absence(trial_attrs),
    ]

    record = {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        "n_trials": n_trials,
        "n_evaluable": n_evaluable,
        "n_eligible": n_eligible,
        "p_eligible": p_eligible,
        "best_reward": best_reward,
        "gradient_signal": gradient_signal,
        "constraint_improvement_rate": constraint_improvement_rate,
        # Issue #755 — Per-Study-Seed/Budget-Telemetrie (Determinismus-Nachweis bei n_jobs>1).
        "seed_effective": study_user_attrs.get("seed_effective"),
        "n_trials_budget": study_user_attrs.get("n_trials_budget"),
        "coherence_violations": coherence_violations,
        "promotion_outcome": proposal.get("status"),
        "rejection_chain": _rejection_chain(proposal),
        "near_miss_deltas": near_miss_deltas,
        # Issue #758 — Eligibility- und Promotion-Inferenzmethode NEBENEINANDER: Weg B aus #757
        # (Bootstrap fuer beide Stufen) beseitigt den Doppelstandard automatisch — im Regelfall
        # sind beide Werte identisch ("stationary_bootstrap"); "sharpe_formula_fallback" bei der
        # Promotion ist der einzige (dokumentierte) Rest-Abweichungsfall (< 5 Holdout-Perioden-
        # Returns persistiert). Die Eligibility-Seite (backtest_runner._calculate_stats) hat KEINEN
        # Fallback-Zweig — strukturell konstant "stationary_bootstrap", sobald PSR ueberhaupt
        # definiert war (>= 1 eligible/evaluable Trial mit definiertem psr_z).
        "inference_method": {
            "eligibility": ("stationary_bootstrap" if any(
                a.get("oos_evaluated") is True for a in trial_attrs) else None),
            "promotion": holdout_metrics.get("deflation_inference_method"),
        },
    }
    return record, checks


def _build_report(
    proposals: list[dict],
    *,
    run_id: str,
    started_at_utc: str | None,
    wallclock_s: float | None,
    cli_args: dict | None,
) -> dict:
    tournament_path = config_dir() / "tournament.json"
    optimizer_path = config_dir() / "optimizer.json"
    tournament_cfg = _load_json(tournament_path) or {}
    optimizer_cfg = _load_json(optimizer_path) or {}

    studies_out: list[dict[str, Any]] = []
    all_checks: list[tuple[str, _inv.InvariantResult]] = []
    for proposal in proposals:
        study = _load_study_for_proposal(proposal)
        record, checks = _study_record(proposal, study)
        studies_out.append(record)
        study_label = f"{record['strategy']}/{record['symbol']}"
        all_checks.extend((study_label, c) for c in checks)

    registry_check = _inv.check_config_key_registry(tournament_cfg)
    all_checks.append(("global", registry_check))

    invariant_checks = []
    for label, result in all_checks:
        d = result.to_dict()
        d["scope"] = label
        invariant_checks.append(d)
        if not result.passed:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": label, "check": result.name,
                "expected": result.expected, "actual": result.actual, "detail": result.detail,
            }, level=logging.ERROR)

    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "git_commit": git_commit(),
        "reward_semantics_version": optimizer_cfg.get("reward_semantics_version"),
        "tournament_config_sha256": sha256_file(tournament_path) if tournament_path.exists() else None,
        "catalog_fingerprint": catalog_fingerprint(),
        "started_at_utc": started_at_utc,
        "wallclock_s": wallclock_s,
        "cli_args": cli_args or {},
        "studies": studies_out,
        "cross_study": {
            "n_family": _family_n_from_proposals(proposals),
        },
        "invariant_checks": invariant_checks,
    }
    return report


def generate_sweep_report(
    proposals: list[Path | dict],
    *,
    run_id: str,
    started_at_utc: str | None = None,
    wallclock_s: float | None = None,
    cli_args: dict | None = None,
    reports_dir: Path | None = None,
) -> Path:
    """Baut + schreibt ATOMAR den Report für GENAU DIESEN Sweep-Lauf.

    ``proposals`` sind die von ``run_per_symbol_sweep`` zurückgegebenen Proposal-Pfade (oder
    bereits geparste Dicts, Test-Pfad) — jede referenzierte Study wird FRISCH aus ihrer SQLite-
    Datei geladen (kein Live-Zustand aus dem Sweep-Lauf selbst nötig), was diesen Pfad bit-
    identisch mit ``generate_report_for_run`` macht (Determinismus-Garantie, #742-Akzeptanz)."""
    parsed = []
    for p in proposals:
        payload = p if isinstance(p, dict) else _load_json(Path(p))
        if isinstance(payload, dict) and payload.get("symbol"):
            parsed.append(payload)

    report = _build_report(
        parsed, run_id=run_id, started_at_utc=started_at_utc,
        wallclock_s=wallclock_s, cli_args=cli_args,
    )
    out_dir = reports_dir or REPORTS_DIR
    out_path = Path(out_dir) / f"run_{run_id}.json"
    write_json_atomic(out_path, report)
    return out_path


def generate_report_for_run(
    *,
    run_id: str,
    proposals_dir: Path | None = None,
    started_at_utc: str | None = None,
    wallclock_s: float | None = None,
    cli_args: dict | None = None,
    reports_dir: Path | None = None,
) -> Path:
    """Standalone/nachträgliche Rekonstruktion — KEINE laufende Sweep-Orchestrierung nötig.

    Entdeckt alle aktuell unter ``proposals_dir`` (Default ``WORK``) liegenden Per-Symbol-
    Proposals (``proposal_{strategy}_{symbol}.json``, unterscheidbar von den strategie-globalen
    ``proposal_{strategy}.json`` am vorhandenen ``symbol``-Feld) und delegiert an denselben Kern
    wie ``generate_sweep_report`` — deckt den Fall "ein alter, bereits gelaufener Sweep soll
    nachträglich reportet werden, für den kein Live-Log mehr existiert" ab (die Proposal-JSONs
    UND die SQLite-Studies sind beide durabel, #742-Ist-Zustand)."""
    base = Path(proposals_dir or WORK)
    proposal_paths = sorted(
        p for p in base.glob("proposal_*.json")
        if (_load_json(p) or {}).get("symbol")
    )
    return generate_sweep_report(
        proposal_paths, run_id=run_id, started_at_utc=started_at_utc,
        wallclock_s=wallclock_s, cli_args=cli_args, reports_dir=reports_dir,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
