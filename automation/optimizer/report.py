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
gradient_signal_arm``/``_sanitize``/``resolve_storage`` für die Study-Metriken/Storage-
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
from automation.optimizer import reward as _reward
from automation.optimizer.manifest import WORK, git_commit, catalog_fingerprint, sha256_file, write_json_atomic, library_versions
from automation.optimizer.run_optimization import (
    _sanitize, resolve_storage, gradient_signal_arm, _modelled_trials,
    _constraint_violation_progress, compute_budget_execution,
)
from automation.optimizer.sweep import _family_n_from_proposals
from automation.optimizer.trial_config import config_dir

# Issue #785 — die bindend/erwartete Struktur einer Entscheidungs-Stufe. Siehe ``_decision_chain``.
_DECISION_STAGE_NAMES = ("is_gate", "confirm_or_selection", "holdout", "deflation", "pbo", "boundary")

# Issue #785 — welche Confirm-/Holdout-Ablehnungsursache welche Stufe der Entscheidungskette
# blockiert. ``REJECT_NO_EDGE_OVER_GLOBAL`` faellt konzeptionell unter "holdout" (die R-Edge-
# Bedingung wird unmittelbar nach dem Punkt-Gate auf demselben promoteten Holdout-Lauf geprueft);
# ``REJECT_HOLDOUT_BOOTSTRAP_CI`` unter "deflation" (dieselbe statistische Inferenz-Familie wie DSR).
_STAGE_FOR_REJECT_DETAIL = {
    "HOLDOUT_NO_ELIGIBLE_TRIALS": "confirm_or_selection",
    "REJECT_HOLDOUT_UNREACHABLE": "confirm_or_selection",
    # Issue #773 — die Kohaerenz-Invariante wird VOR jedem Holdout-Backtest geprueft.
    "REJECT_COHERENCE_VIOLATION": "confirm_or_selection",
    "REJECT_HOLDOUT_GATE": "holdout",
    "REJECT_NO_EDGE_OVER_GLOBAL": "holdout",
    "REJECT_HOLDOUT_DSR_DROP": "deflation",
    "REJECT_HOLDOUT_BOOTSTRAP_CI": "deflation",
    "REJECT_SELECTION_PBO": "pbo",
    "REJECT_BOUNDARY_SOLUTION": "boundary",
    "HOLD_BOUNDARY_UNRESOLVED": "boundary",
}

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


def _gradient_min_eligible_for_variance(base_cfg: Path | None = None) -> int:
    """Issue #808 — dieselbe Config-Quelle/Default wie ``run_optimization._emit_study_summary`` fuer
    die Entdeckungs-Arm-Schwelle des Gradienten-Gates."""
    min_eligible = 5
    try:
        opt_path = (base_cfg or config_dir()) / "optimizer.json"
        if opt_path.exists():
            min_eligible = int((json.loads(opt_path.read_text("utf-8")) or {}).get(
                "tier_escalation_min_eligible_for_variance", min_eligible))
    except (OSError, ValueError, TypeError):
        pass
    return min_eligible


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


def _decision_chain(proposal: dict, *, n_eligible: int) -> list[dict[str, Any]]:
    """Issue #785 — die VOLLSTAENDIGE Nachweiskette mit POSITIVEN Stufen (``passed=True``), nicht
    nur Ablehnungsgruende (die alte ``rejection_chain``, die als abgeleitete Sicht unten erhalten
    bleibt). Root-Cause #785: ``check_rejection_chain_completeness`` war fuer ``status ==
    'READY_FOR_PR'`` PER KONSTRUKTION ``True`` — genau dort fehlte allen 37 `#682`-Records (heute
    ``PROMOTE_GLOBAL_DEFAULT``, #783) eine ganze Stufe (``confirm_or_selection``), unbemerkt in
    1736/1736 gruenen Studies.

    Jede Stufe ist ``{stage, passed, detail}``. Eine REJECTED Study endet die Kette an der
    tatsaechlich blockierenden Stufe (``_STAGE_FOR_REJECT_DETAIL``); vorangehende Stufen gelten als
    bestanden (der Confirm-Pfad erreicht eine Stufe erst, nachdem die vorherigen bestanden sind).
    Ein promoteter Kandidat traegt ALLE Stufen mit ``passed=True`` — inkl. der `#682`/`#783`-
    Default-Route, die ``confirm_or_selection`` mit ``detail='GLOBAL_DEFAULT'`` traegt (das
    Akzeptanzkriterium #785/2)."""
    holdout_detail = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    route = proposal.get("promotion_route")
    failing_stage = _STAGE_FOR_REJECT_DETAIL.get(holdout_detail) if holdout_detail else None
    promote = proposal.get("status") in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")

    chain: list[dict[str, Any]] = []
    is_gate_passed = bool(n_eligible > 0 or route == "global_default_on_symbol" or promote)
    chain.append({
        "stage": "is_gate", "passed": is_gate_passed,
        "detail": proposal.get("dominant_is_rejection_detail") if not is_gate_passed else None,
    })
    if not is_gate_passed:
        return chain
    for stage in _DECISION_STAGE_NAMES[1:]:
        if failing_stage == stage:
            chain.append({"stage": stage, "passed": False, "detail": holdout_detail})
            break
        detail = "GLOBAL_DEFAULT" if (stage == "confirm_or_selection"
                                      and route == "global_default_on_symbol") else None
        chain.append({"stage": stage, "passed": True, "detail": detail})
    return chain


def _rejection_chain_view(decision_chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issue #785 — ``rejection_chain`` bleibt als RUECKWAERTSKOMPATIBLE, ABGELEITETE Sicht auf
    ``decision_chain`` erhalten (nur die ``passed=False``-Stufen, ohne das ``passed``-Feld selbst —
    bit-identische Form zur alten Struktur ``{stage, detail}``)."""
    return [{"stage": c["stage"], "detail": c.get("detail")}
            for c in decision_chain if c.get("passed") is False]


def _split_near_miss_deltas(raw_deltas: dict, tournament_cfg: dict) -> tuple[dict, dict, str | None]:
    """Issue #790 — trennt AKTIVE Gates (tatsaechlich Teil von ``eligible_requires_all``/``_any``,
    ueber ``reward._active_gate_collinearity_keys`` — dieselbe Laufzeit-Quelle wie der #760-
    Kollinearitaets-Check, KEINE zweite gepflegte Liste) von WEICHEN Distanztermen (deaktivierte
    Gates wie ``oos_min_profitable_folds_frac``/``oos_min_expectancy``, die weiterhin als Near-Miss-
    Telemetrie berechnet werden, aber keine Eligibility-Entscheidung mehr treffen — Root-Cause #790:
    88 % der Studies mit eligiblen Trials meldeten ein "bindendes" Gate, das gar nicht mehr galt).

    Rückgabe ``(binding, soft, binding_gate)`` — ``binding_gate`` ist das Gate mit dem negativsten
    Delta INNERHALB von ``binding`` (niemals aus ``soft``, das ist die #790-Garantie gegen die
    #760-Fehlerklasse auf der Diagnose-Ebene)."""
    active_keys = set(_reward._active_gate_collinearity_keys(tournament_cfg))
    binding = {k: v for k, v in raw_deltas.items() if k in active_keys}
    soft = {k: v for k, v in raw_deltas.items() if k not in active_keys}
    numeric_binding = {k: v for k, v in binding.items() if isinstance(v, (int, float))}
    binding_gate = min(numeric_binding, key=lambda k: numeric_binding[k]) if numeric_binding else None
    return binding, soft, binding_gate


def _inference_method_block(trial_attrs: list[dict], holdout_metrics: dict, proposal: dict,
                            n_eligible: int) -> dict[str, Any]:
    """Issue #791 — ``inference_method`` je Ebene als ``{method, applied, skipped_reason}`` statt
    eines nackten ``None``. Root-Cause #791: 257/1736 Studies durchliefen ueberhaupt keine
    Inferenzmethode, davon eine promotet — ``None`` war von "Methode fehlgeschlagen" nicht
    unterscheidbar UND blockierte keine Promotion. Invariante (Akzeptanzkriterium #791/1):
    ``promote=True`` erfordert ``promotion.applied == True`` — auch fuer die `#682`/`#783`-
    Default-Route. Wo keine ECHTE numerische Deflation vorliegt (Kohorte < 2, Config deaktiviert,
    Sortino strukturell undefiniert), wird die Nichtanwendbarkeit selbst zur dokumentierten
    Methode (``'not_applicable'``) statt einer stillen Luecke — genau die im Issue geforderte
    Unterscheidung "ausdruecklich begruendet" vs. "Fehler"."""
    eligibility_applied = any(a.get("oos_evaluated") is True for a in trial_attrs)
    eligibility = {
        "method": "stationary_bootstrap" if eligibility_applied else None,
        "applied": eligibility_applied,
        "skipped_reason": None if eligibility_applied else "NO_EVALUATED_TRIALS",
    }

    promote = proposal.get("status") in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    method = holdout_metrics.get("deflation_inference_method")
    if method is not None:
        promotion = {"method": method, "applied": True, "skipped_reason": None}
    elif promote:
        promotion = {"method": "not_applicable", "applied": True, "skipped_reason": None}
    else:
        promotion = {
            "method": None, "applied": False,
            "skipped_reason": "NO_ELIGIBLE_TRIALS" if n_eligible == 0 else "DEFLATION_NOT_APPLICABLE",
        }
    return {"eligibility": eligibility, "promotion": promotion}


def _study_record(proposal: dict, study,
                  tournament_cfg: dict | None = None) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743)."""
    trials = list(getattr(study, "trials", None) or []) if study is not None else []
    trial_attrs = [dict(getattr(t, "user_attrs", {}) or {}) for t in trials]

    n_trials = len(trials)
    n_evaluable = sum(1 for a in trial_attrs if a.get("oos_evaluated") is True)
    n_eligible = sum(1 for a in trial_attrs if a.get("oos_eligible") is True)
    p_eligible = round(n_eligible / n_trials, 4) if n_trials else 0.0
    coherence_violations = sum(1 for a in trial_attrs if a.get("oos_coherence_violation") is True)
    # Issue #804 — Aggregat je Study: wie oft jeder strukturierte Inferenzpfad-Diagnose-Code
    # (EQUITY_NONPOSITIVE/PERIOD_RETURNS_NOT_FINITE/RETURN_SERIES_IDENTITY_*/
    # NON_CONTIGUOUS_FOLD_SEGMENTS/SORTINO_GUARD_TRIPPED/COHERENCE_INVARIANT_VIOLATION) über ALLE
    # Trials dieser Study auftrat — macht sichtbar, OHNE ein Trial-Verzeichnis zu öffnen, ob/wie oft
    # der Subprozess eine Invariante verletzt hat (siehe run_optimization._reemit_inference_
    # diagnostics für die Live-Emission je Trial).
    inference_diagnostics_by_code: dict[str, int] = {}
    for a in trial_attrs:
        for diag in a.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code:
                inference_diagnostics_by_code[code] = inference_diagnostics_by_code.get(code, 0) + 1

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
        gradient_signal_arm_value = None
    else:
        # Issue #808 — EINE Klassifikation (gradient_signal_arm), gradient_signal ist deren
        # bool-Projektion (arm != 'none').
        gradient_signal_arm_value = gradient_signal_arm(
            feasible_rewards, p_eligible, _gradient_tau(),
            constraint_improvement_rate=constraint_improvement_rate, tau_c=_gradient_tau_c(),
            min_eligible_for_variance=_gradient_min_eligible_for_variance())
        gradient_signal = gradient_signal_arm_value != "none"

    raw_near_miss_deltas: dict[str, Any] = {}
    scored = [t for t in trials if isinstance(getattr(t, "value", None), (int, float))]
    if scored:
        best_trial = max(scored, key=lambda t: t.value)
        raw_near_miss_deltas = dict(getattr(best_trial, "user_attrs", {}).get("oos_gate_deltas") or {})
    binding_deltas, soft_deltas, binding_gate = _split_near_miss_deltas(
        raw_near_miss_deltas, tournament_cfg or {})

    # Issue #776 — konsumiert den #679-Redundanz-Alarm JE STUDY: meldet, ob ``eligible_requires_all``
    # (aus der Config, NICHT hartcodiert) gegenüber der LIVE-Trial-Kohorte dieser Study noch ein
    # Gate enthält, das ``assert_eligible_requires_all_not_redundant`` als redundant ausweist.
    trial_gate_deltas = [a.get("oos_gate_deltas") for a in trial_attrs if a.get("oos_gate_deltas")]
    gate_collinearity_unconsolidated = _reward.assert_eligible_requires_all_not_redundant(
        trial_gate_deltas, (tournament_cfg or {}).get("eligible_requires_all") or [], tournament_cfg)

    # Issue #770 — dieselbe Berechnung wie ``run_optimization._emit_study_summary`` (Single Source
    # of Truth, siehe compute_budget_execution-Docstring).
    budget_execution = compute_budget_execution(
        trials, n_trials_budget=study_user_attrs.get("n_trials_budget"),
        n_startup_trials=n_startup_for_report, study_user_attrs=study_user_attrs)

    holdout_metrics = (proposal.get("holdout") or {}).get("symbol") or {}
    decision_chain = _decision_chain(proposal, n_eligible=n_eligible)
    checks = [
        _inv.check_sr0_coherence(holdout_metrics),
        _inv.check_n_family_consistency(holdout_metrics),
        _inv.check_rejection_chain_completeness(proposal, decision_chain=decision_chain),
        _inv.check_reward_term_variance(trial_attrs),
        # Issue #756 — nach der Log-Return-Umstellung ist eine verbleibende Kohärenzverletzung ein
        # echter Bug, kein erwartetes Restrauschen mehr; harter Regressionswächter statt WARNING.
        _inv.check_log_return_coherence(trial_attrs),
        # Issue #759 — Missing-Data-Sentinel-Kollaps-Regressionswächter (oos_win_rate).
        _inv.check_metric_sentinel_absence(trial_attrs),
        # Issue #804 — sechster Regressionswächter: strukturierte Inferenzpfad-Diagnosen aus dem
        # Backtest-Subprozess sind jetzt maschinell im #742-Report überprüfbar, nicht nur live geloggt.
        _inv.check_inference_diagnostics_absent(trial_attrs),
    ]

    record = {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        "n_trials": n_trials,
        "n_evaluable": n_evaluable,
        "n_eligible": n_eligible,
        "p_eligible": p_eligible,
        # Issue #812 — SHA-256 ueber die effektiv wirksame Gate-Konfiguration dieser Study
        # (reward.selection_rule_fingerprint, gestempelt in run_optimization._emit_study_summary).
        # ``None`` fuer Studies aus einem Lauf vor #812 (rueckwaertskompatibel, analog seed_effective).
        "selection_rule_fingerprint": study_user_attrs.get("selection_rule_fingerprint"),
        "best_reward": best_reward,
        "gradient_signal": gradient_signal,
        # Issue #808 — welcher der drei Arme (discovery/reward_variance/constraint_progress/none)
        # das obige gradient_signal traegt. None ⇒ wie gradient_signal selbst unbeantwortet
        # (Early-Stop).
        "gradient_signal_arm": gradient_signal_arm_value,
        "constraint_improvement_rate": constraint_improvement_rate,
        # Issue #755 — Per-Study-Seed/Budget-Telemetrie (Determinismus-Nachweis bei n_jobs>1).
        "seed_effective": study_user_attrs.get("seed_effective"),
        "n_trials_budget": study_user_attrs.get("n_trials_budget"),
        # Issue #770 — Budget-Ausfuehrungsgrad als erstklassige Study-Kennzahl.
        "n_trials_budgeted": budget_execution["n_trials_budgeted"],
        "n_trials_completed": budget_execution["n_trials_completed"],
        "budget_executed_fraction": budget_execution["budget_executed_fraction"],
        "stop_reason": budget_execution["stop_reason"],
        "n_modelled_trials_completed": budget_execution["n_modelled_trials_completed"],
        "coherence_violations": coherence_violations,
        # Issue #804 — je Study, wie oft jeder Inferenzpfad-Diagnose-Code auftrat (leeres Dict im
        # Normalfall). Macht eine Subprozess-Invariantenverletzung im #742-Report sichtbar, ohne ein
        # Trial-Verzeichnis zu öffnen oder trial_dir/logs/ zu lesen.
        "inference_diagnostics_by_code": inference_diagnostics_by_code,
        "promotion_outcome": proposal.get("status"),
        # Issue #783 — Pflichtfeld bei ``promote=True``: unterscheidet eine holdout-validierte
        # Symbol-Promotion (``None``) von der ungetunten `#682`-Default-Route
        # (``'global_default_on_symbol'``) in JEDEM Artefakt — nicht nur im Proposal.
        "promotion_route": proposal.get("promotion_route"),
        # Issue #785 — die VOLLSTAENDIGE Entscheidungskette (positive UND negative Stufen);
        # ``rejection_chain`` bleibt als abgeleitete, rueckwaertskompatible Sicht erhalten.
        "decision_chain": decision_chain,
        "rejection_chain": _rejection_chain_view(decision_chain),
        # Issue #790 — near_miss_deltas trennt AKTIVE Gates (binding, eligibility-wirksam) von
        # deaktivierten Gates (soft, reine Distanz-Telemetrie); binding_gate ist NIE aus soft.
        "near_miss_deltas": {"binding": binding_deltas, "soft": soft_deltas},
        "binding_gate": binding_gate,
        # Issue #776 — noch unkonsolidierte (LIVE als redundant ausgewiesene) Mitglieder von
        # ``eligible_requires_all`` dieser Study; leer ⇒ Config konsistent mit dem #679-Alarm.
        "gate_collinearity_unconsolidated": gate_collinearity_unconsolidated,
        # Issue #786 — das bindende HOLDOUT-Gate (negativstes normiertes Delta auf dem Holdout-
        # Fenster, NICHT den OOS-Folds — siehe confirm._holdout_binding_gate) + die zugrunde
        # liegenden Deltas, direkt aus dem Proposal uebernommen (von confirm.py gestempelt).
        "holdout_gate_deltas": holdout_metrics.get("holdout_gate_deltas") or {},
        "holdout_binding_gate": holdout_metrics.get("holdout_binding_gate"),
        # Issue #758/#791 — Eligibility- und Promotion-Inferenzmethode NEBENEINANDER, jetzt als
        # {method, applied, skipped_reason} statt eines nackten Strings/None (#791): ``applied``
        # unterscheidet "Inferenz lief nicht, weil strukturell unanwendbar/dokumentiert
        # ausgelassen" von "vergessen" — ein promoteter Kandidat OHNE dokumentierte
        # Promotions-Inferenz ist ein Fehler, kein legitimer Nullzustand.
        "inference_method": _inference_method_block(trial_attrs, holdout_metrics, proposal, n_eligible),
        # Issue #764 — die vollstaendige Reward-Term-Varianz-Tabelle (var_contrib je Term gegen den
        # [0.02, 0.30]-Zielkorridor), statt nur der binaeren inert-Liste aus check_reward_term_
        # variance (Akzeptanzkriterium #764: "Report enthaelt die Term-Varianz-Tabelle je Study").
        "reward_term_variance": _inv.reward_term_variance_table(trial_attrs),
    }
    return record, checks


def _budget_execution_summary(studies_out: list[dict[str, Any]]) -> dict[str, Any]:
    """Issue #770 — Median + p10 von ``budget_executed_fraction`` ueber alle Studies eines Laufs
    (Sweep-Ebenen-Aggregation, Akzeptanzkriterium #770). ``None``-Felder bei leerer Kohorte."""
    import statistics as _stats
    fractions = sorted(
        r["budget_executed_fraction"] for r in studies_out
        if r.get("budget_executed_fraction") is not None
    )
    if not fractions:
        return {"median": None, "p10": None, "n": 0}
    median = _stats.median(fractions)
    p10_idx = max(0, min(len(fractions) - 1, int(round(0.10 * (len(fractions) - 1)))))
    return {"median": round(median, 4), "p10": round(fractions[p10_idx], 4), "n": len(fractions)}


def _diagnosed_pairs_skipped_section() -> list[dict[str, Any]]:
    """Issue #778 (Umsetzungspunkt 3) — die vom `#681`-Auto-Cache aktuell ``'denylist'``-empfohlenen
    (und damit von ``enumerate_tunable_pairs`` übersprungenen) Paare als eigene Report-Sektion, MIT
    Begründung (``binding_cause``) und Evidenzstand (``budget_executed_fraction``,
    ``n_runs_confirmed``, ``first_seen_run_id``) — macht eine automatische Deaktivierung im Report
    genauso nachvollziehbar wie eine Promotion, statt nur im Cache-JSON verborgen zu sein."""
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        cache = load_diagnosed_pairs_cache()
    except Exception:
        return []
    return [
        {
            "strategy": entry.get("strategy"), "symbol": entry.get("symbol"),
            "binding_cause": entry.get("binding_cause"),
            "budget_executed_fraction": entry.get("budget_executed_fraction"),
            "n_runs_confirmed": entry.get("n_runs_confirmed"),
            "first_seen_run_id": entry.get("first_seen_run_id"),
        }
        for entry in cache.values() if entry.get("action") == "denylist"
    ]


def _promotion_outcome_counts(studies_out: list[dict[str, Any]]) -> dict[str, int]:
    """Issue #783 — Zaehlt ``promotion_outcome`` (inkl. ``PROMOTE_GLOBAL_DEFAULT`` GETRENNT von
    ``READY_FOR_PR``, Akzeptanzkriterium #5). Reine Aggregation, kein Vergleich/Gate."""
    import collections
    counts = collections.Counter(r.get("promotion_outcome") for r in studies_out)
    return {str(k): v for k, v in counts.items()}


def _binding_gate_histogram_by_strategy(studies_out: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Issue #787 — Histogramm des ``binding_gate`` (#790, ausschliesslich AKTIVE Gates) je
    Strategie ueber alle Studies. Ermoeglicht die #787-Ursachenzuordnung fuer 0-eligible-
    Strategien (SqueezeBreakout/TrendPullback/AdxAtrMomentum/Rsi2Reversion), ohne ein
    deaktiviertes Gate faelschlich als dominant auszuweisen."""
    import collections
    out: dict[str, collections.Counter] = {}
    for r in studies_out:
        strategy = r.get("strategy")
        gate = r.get("binding_gate")
        if not strategy or not gate:
            continue
        out.setdefault(strategy, collections.Counter())[gate] += 1
    return {strategy: dict(counter) for strategy, counter in out.items()}


def _selection_rule_families(studies_out: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Issue #812 — gruppiert die Studies je Symbol nach ihrem ``selection_rule_fingerprint``:
    Studies mit demselben Fingerprint wandten garantiert dieselbe EFFEKTIVE Selektionsregel an
    (siehe ``reward.selection_rule_fingerprint``) und dürfen in EINER DSR-Multiplizitäts-Familie
    gezählt werden. Mehr als EIN Fingerprint je Symbol macht sichtbar, dass die Familie tatsächlich
    in getrennte Selektionsprozeduren zerfällt (Pitfall #248, z. B. weil ``any_arm_unreachable_
    policy='drop_arm'`` bei einer Study griff, bei einer anderen desselben Symbols aber nicht) —
    das ist das im Akzeptanzkriterium #812 geforderte "im Report als separate Familien
    ausgewiesen". Studies ohne Fingerprint (ein Lauf vor #812) landen unter ``'unknown'``.

    Rückgabe: ``{symbol: {fingerprint_or_'unknown': n_family}}`` — ``n_family`` je Bucket ist die
    Summe von ``n_evaluable`` (dieselbe ``oos_evaluated``-Zählung wie
    ``sweep._family_n_from_studies``, #784), NICHT die blosse Study-Anzahl."""
    out: dict[str, dict[str, int]] = {}
    for r in studies_out:
        symbol = r.get("symbol")
        if not symbol:
            continue
        fingerprint = r.get("selection_rule_fingerprint") or "unknown"
        bucket = out.setdefault(symbol, {})
        bucket[fingerprint] = bucket.get(fingerprint, 0) + int(r.get("n_evaluable") or 0)
    return out


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
        record, checks = _study_record(proposal, study, tournament_cfg)
        studies_out.append(record)
        study_label = f"{record['strategy']}/{record['symbol']}"
        all_checks.extend((study_label, c) for c in checks)
        # Issue #791 — REJECT_SELECTION_PBO erfordert eine dokumentierte Promotions-Inferenz.
        all_checks.append((study_label, _inv.check_promotion_inference_coverage(proposal, record)))

    registry_check = _inv.check_config_key_registry(tournament_cfg)
    all_checks.append(("global", registry_check))

    # Issue #770 — sweep-weite Budget-Ausfuehrungs-Invariante (siebter Check, siehe #743/#773).
    min_median_budget_execution = float(optimizer_cfg.get("min_median_budget_execution", 0.5))
    budget_check = _inv.check_budget_execution(studies_out, min_median=min_median_budget_execution)
    all_checks.append(("global", budget_check))

    # Issue #776 — sweep-weite Gate-Kollinearitaets-Konsolidierungs-Invariante (konsumiert den
    # #679-Alarm ueber alle Studies statt ihn stumm bleiben zu lassen).
    max_affected_fraction = float(optimizer_cfg.get("max_gate_collinearity_affected_fraction", 0.20))
    gate_collinearity_check = _inv.check_gate_collinearity_consolidation(
        studies_out, max_affected_fraction=max_affected_fraction)
    all_checks.append(("global", gate_collinearity_check))

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
        # Issue #802 — Bibliotheksversionen (pandas allen voran) in der Provenienz, damit ein Lauf
        # im Nachhinein einer Installationsumgebung zuordenbar ist.
        "library_versions": library_versions(),
        "tournament_config_sha256": sha256_file(tournament_path) if tournament_path.exists() else None,
        "catalog_fingerprint": catalog_fingerprint(),
        "started_at_utc": started_at_utc,
        "wallclock_s": wallclock_s,
        "cli_args": cli_args or {},
        "studies": studies_out,
        "cross_study": {
            "n_family": _family_n_from_proposals(proposals),
            # Issue #770 — Budget-Ausfuehrungsgrad-Verteilung ueber alle Studies (Median + p10, wie
            # im Katalog gefordert: die 44,2%/52,6%-Luecken dieses Katalogs waren nur ueber externe
            # Log-Rekonstruktion sichtbar).
            "budget_executed_fraction": _budget_execution_summary(studies_out),
            # Issue #783 (Akzeptanzkriterium #5) — READY_FOR_PR und PROMOTE_GLOBAL_DEFAULT GETRENNT
            # gezaehlt: beide teilten vorher denselben String, ununterscheidbar in jeder
            # nachgelagerten Automatisierung, die auf "READY_FOR_PR" filtert.
            "promotion_outcome_counts": _promotion_outcome_counts(studies_out),
            # Issue #787 — Histogramm des bindenden Gates je Strategie, ausschliesslich ueber
            # binding-Deltas (#790) — Voraussetzung dafuer, dass eine 0-eligible-Strategie ihre
            # tatsaechliche Ursache (trade_frequency/signal_quality/data_geometry) zeigt, statt
            # eines deaktivierten Gates (siehe #787-Umsetzung).
            "binding_gate_histogram_by_strategy": _binding_gate_histogram_by_strategy(studies_out),
            # Issue #778 — automatisch denylist-empfohlene (uebersprungene) Paare MIT Begruendung
            # und Evidenzstand, statt nur im diagnosed_pairs_cache.json verborgen zu sein.
            "diagnosed_pairs_skipped": _diagnosed_pairs_skipped_section(),
            # Issue #812 — je Symbol nach selection_rule_fingerprint gruppierte n_family: macht eine
            # innerhalb eines Symbols heterogene Selektionsregel (verschiedene #668-Policy-Ausgaenge
            # ueber die Studies hinweg) sichtbar, statt sie in EINER Zahl zu verstecken.
            "selection_rule_families": _selection_rule_families(studies_out),
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
