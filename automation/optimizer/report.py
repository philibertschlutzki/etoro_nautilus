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
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import optuna

from automation.log_manager import emit_execution_event
from automation.optimizer import invariants as _inv
from automation.optimizer import _contracts
from automation.optimizer import reward as _reward
from automation.optimizer.manifest import WORK, git_commit, catalog_fingerprint, sha256_file, write_json_atomic, library_versions
from automation.optimizer.run_optimization import (
    _sanitize, resolve_storage, gradient_signal_arm, _modelled_trials,
    _constraint_violation_progress, compute_budget_execution,
)
from automation.optimizer.sweep import _family_n_from_proposals, load_symbol_universe
from automation.optimizer import symbol_coverage as _symbol_coverage
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
    # Issue #847 — 17 REJECT_SELECTION_PBO-Ablehnungen trugen `inference_method.promotion:
    # {applied: False}`, obwohl eine CSCV/PBO-Inferenz TATSÄCHLICH gelaufen war (deflation_
    # inference_method ist DSR-spezifisch und bleibt bei einer reinen PBO-Ablehnung None — dieser
    # Block sah also keine gelaufene Methode). `holdout_metrics['pbo']` ist nicht-None GENAU dann,
    # wenn `_study_pbo` ein Urteil gefällt hat (siehe confirm.py) — das IST die dokumentierte
    # Promotions-Inferenz für diesen Ausgang, unabhängig davon, ob sie zur Ablehnung führte.
    pbo_value = holdout_metrics.get("pbo")
    if method is not None:
        promotion = {"method": method, "applied": True, "skipped_reason": None}
    elif pbo_value is not None:
        promotion = {
            "method": "cscv", "applied": True, "skipped_reason": None,
            "pbo": pbo_value,
            "pbo_n_groups": holdout_metrics.get("pbo_n_groups"),
            "pbo_n_configs_raw": holdout_metrics.get("pbo_n_configs_raw"),
            "pbo_n_configs_effective": holdout_metrics.get("pbo_n_configs"),
            "pbo_threshold": holdout_metrics.get("pbo_threshold"),
        }
    elif promote:
        promotion = {"method": "not_applicable", "applied": True, "skipped_reason": None}
    else:
        promotion = {
            "method": None, "applied": False,
            "skipped_reason": "NO_ELIGIBLE_TRIALS" if n_eligible == 0 else "DEFLATION_NOT_APPLICABLE",
        }
    return {"eligibility": eligibility, "promotion": promotion}


def _median_of_trial_field(trial_attrs: list[dict], field: str) -> float | None:
    """Issue #897 Fix 3 — Median eines numerischen ``trial.user_attrs``-Felds über eine Study
    (None-safe: fehlende/None-Werte werden übersprungen; leere Kohorte ⇒ None)."""
    values = [a[field] for a in (trial_attrs or []) if a.get(field) is not None]
    return statistics.median(values) if values else None


def _median_of_sampled_param(trial_attrs: list[dict], param: str) -> float | None:
    """Issue #897 Fix 3 — Median eines GESAMPELTEN Suchraum-Parameters (``sampled_params[param]``)
    über eine Study. Symmetrisch zu ``_median_of_trial_field``, aber für den Config-Wert statt der
    realisierten Telemetrie (Eingangsgrösse für ``check_effective_stop_distance``)."""
    values = [
        (a.get("sampled_params") or {}).get(param) for a in (trial_attrs or [])
        if (a.get("sampled_params") or {}).get(param) is not None
    ]
    return statistics.median(values) if values else None


def _study_record(proposal: dict, study,
                  tournament_cfg: dict | None = None, *,
                  guard_dominance_threshold: float | None = None,
                  ) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743)."""
    trials = list(getattr(study, "trials", None) or []) if study is not None else []
    trial_attrs = [dict(getattr(t, "user_attrs", {}) or {}) for t in trials]

    n_trials = len(trials)
    n_evaluable = sum(1 for a in trial_attrs if a.get("oos_evaluated") is True)
    n_eligible = sum(1 for a in trial_attrs if a.get("oos_eligible") is True)
    p_eligible = round(n_eligible / n_trials, 4) if n_trials else 0.0
    # Issue #862 — Median der informativen Periodenzahl über die oos_evaluated Trials dieser
    # Study (Rohmaterial für invariants.check_guard_reference_coherence auf Report-Ebene).
    _n_periods_values = [
        a["oos_n_periods"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_n_periods")
    ]
    oos_n_periods_median = statistics.median(_n_periods_values) if _n_periods_values else None
    coherence_violations = sum(1 for a in trial_attrs if a.get("oos_coherence_violation") is True)
    # Issue #804 — Aggregat je Study: wie oft jeder strukturierte Inferenzpfad-Diagnose-Code
    # (EQUITY_NONPOSITIVE/PERIOD_RETURNS_NOT_FINITE/RETURN_SERIES_IDENTITY_*/
    # NON_CONTIGUOUS_FOLD_SEGMENTS/SORTINO_GUARD_TRIPPED/COHERENCE_INVARIANT_VIOLATION) über ALLE
    # Trials dieser Study auftrat — macht sichtbar, OHNE ein Trial-Verzeichnis zu öffnen, ob/wie oft
    # der Subprozess eine Invariante verletzt hat (siehe run_optimization._reemit_inference_
    # diagnostics für die Live-Emission je Trial).
    inference_diagnostics_by_code: dict[str, int] = {}
    # Issue #901 — je Study die beobachteten guard_reference_source-Werte aus SORTINO_GUARD_TRIPPED/
    # SORTINO_GUARD_REFERENCE_UNAVAILABLE-Diagnosen, Eingangsgrösse für
    # invariants.check_guard_reference_coherence unter reference_mode=='family_median'.
    guard_reference_sources: list[str] = []
    for a in trial_attrs:
        for diag in a.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code:
                inference_diagnostics_by_code[code] = inference_diagnostics_by_code.get(code, 0) + 1
            if isinstance(diag, dict) and diag.get("guard_reference_source") is not None:
                guard_reference_sources.append(diag["guard_reference_source"])

    # Issue #832 Fix Punkt 1 — je-Study-Aggregat der Haltedauer (Sekunden): das MAXIMUM über alle
    # oos_evaluated Trials (Rohmaterial für summary_de.py Abschnitt 4 "Trades mit der längsten
    # Haltedauer" — siehe cross_study['longest_holding_studies'], _build_report). P95 folgt
    # demselben Trial mit dem groessten Maximum (repräsentativ für DESSEN Verteilung, keine
    # zusaetzliche Kreuz-Trial-Perzentilberechnung noetig).
    max_holding_time_s: float | None = None
    p95_holding_time_s: float | None = None
    for a in trial_attrs:
        candidate = a.get("oos_max_holding_time_s")
        if candidate is not None and (max_holding_time_s is None or candidate > max_holding_time_s):
            max_holding_time_s = candidate
            p95_holding_time_s = a.get("oos_p95_holding_time_s")

    # Issue #839/#857 — je-Trial-Zeitbox-Verletzung (nicht nur das Study-Maximum aus #832 oben):
    # eine gemessene GR-01-Verletzung erhält hier eine Konsequenz auf TRIAL-Ebene (siehe
    # invariants.compute_trial_timebox_violations; #861 vereinheitlicht diese Berechnung mit
    # ``check_holding_time_cap`` unten über dieselbe Referenz-Auflösung).
    # Issue #902 — bar_seconds ist jetzt Pflichtparameter; #900s Bar-Qualitäts-Telemetrie
    # (median_delta_t_s je Symbol) ist an dieser Stelle nicht verfügbar (Report läuft nach dem
    # Sweep, nicht symbol-gescoped) — der dokumentierte 1h-Bar-Default ist der einzige verfügbare
    # Wert, jetzt aus der EINEN Quelle (_contracts.BAR_SECONDS_DEFAULT) statt eines eigenen Literals.
    timebox = _inv.compute_trial_timebox_violations(
        trial_attrs, strategy=proposal.get("strategy"),
        bar_seconds=_contracts.BAR_SECONDS_DEFAULT)

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
        # Issue #845 — n_periods-Heterogenität innerhalb der DSR-Kohorte muss dieselbe Suppression
        # ausgelöst haben, die confirm.py bei ueberschrittener deflation_max_n_periods_ratio anwendet.
        _inv.check_family_n_periods_homogeneity(
            holdout_metrics,
            max_ratio=float((tournament_cfg or {}).get("deflation_max_n_periods_ratio", 4.0))),
        _inv.check_n_family_consistency(holdout_metrics),
        # Issue #887 — der globale Default (route='global_default_on_symbol') nahm an der
        # Stufe-1-Selektion nicht teil; seine Deflation muss N=1 tragen, nicht deflation_n_family.
        _inv.check_promotion_multiplicity_route(proposal),
        # Issue #813 — deflation_cluster_coverage < 0.9 ist ein Invarianten-FAIL: die familienweite
        # Decluster-Matrix sieht dann nur einen Bruchteil der gezaehlten (oos_evaluated) Kandidaten.
        _inv.check_deflation_cluster_coverage(holdout_metrics),
        _inv.check_rejection_chain_completeness(proposal, decision_chain=decision_chain),
        _inv.check_reward_term_variance(trial_attrs),
        # Issue #756 — nach der Log-Return-Umstellung ist eine verbleibende Kohärenzverletzung ein
        # echter Bug, kein erwartetes Restrauschen mehr; harter Regressionswächter statt WARNING.
        _inv.check_log_return_coherence(trial_attrs),
        # Issue #759 — Missing-Data-Sentinel-Kollaps-Regressionswächter (oos_win_rate).
        _inv.check_metric_sentinel_absence(trial_attrs),
        # Issue #804/#886 — sechster Regressionswächter: strukturierte Inferenzpfad-Diagnosen aus
        # dem Backtest-Subprozess sind jetzt maschinell im #742-Report überprüfbar, nicht nur live
        # geloggt (seit #886 ohne die #863/#864-regulären dritten Ausgänge, siehe unten).
        _inv.check_inference_diagnostics_absent(trial_attrs),
        # Issue #886 — ersetzt die reine Anwesenheit der #863/#864-regulären Ausgänge durch eine
        # Konzentrationsprüfung (analog STUDY_GUARD_DOMINATED, #823), gegen den #885-Nenner
        # n_trials_informative.
        _inv.check_inference_diagnostics_concentration(
            trial_attrs, n_trials_informative=study_user_attrs.get("n_trials_informative"),
            **({"guard_dominance_threshold": guard_dominance_threshold}
               if guard_dominance_threshold is not None else {})),
        # Issue #885 Fix Punkt 3 — die fünf Trial-Kategorien (informativ/geprunt/unauswertbar/
        # fehlgeschlagen/total) müssen die Trial-Menge disjunkt und vollständig zerlegen.
        _inv.check_denominator_coherence(study_user_attrs),
    ]

    record = {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        "n_trials": n_trials,
        "n_evaluable": n_evaluable,
        "n_eligible": n_eligible,
        "p_eligible": p_eligible,
        # Issue #885 Fix Punkt 2 — n_trials_pruned/n_trials_unevaluable als GETRENNTE Telemetrie
        # (vorher kollabierten beide in "nicht evaluiert"); n_trials_informative ist der EINE Nenner
        # für Raten-Meldungen, die die tatsächlich verwertete Suche messen (#885/#886).
        "n_trials_informative": study_user_attrs.get("n_trials_informative"),
        "n_trials_pruned": study_user_attrs.get("n_trials_pruned"),
        "n_trials_unevaluable": study_user_attrs.get("n_trials_unevaluable"),
        "n_trials_failed": study_user_attrs.get("n_trials_failed"),
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
        # Issue #853 Fix Punkt 3 — seed_source als POSITIVE Telemetrie (vorher existierte nur die
        # [#565]-Negativ-WARNUNG im Log): welcher Anker den Warm-Start/param_pen dieser Study
        # tatsächlich speiste (run_optimization.resolve_symbol_shrinkage_seed, Study-User-Attr
        # 'shrinkage_seed_source'). 'champion_quality_stale' (#819) ist seed-fähig wie 'champion',
        # nur die Quality-Telemetrie ist veraltet — unterscheidbar von 'strategy_defaults'/'none'
        # (kein Champion vorhanden).
        "seed_source": study_user_attrs.get("shrinkage_seed_source"),
        # Issue #851 — Study-Zeitstempel-Telemetrie (run_optimization._optimize_symbol_impl setzt
        # diese User-Attrs vor/nach study.optimize, auch bei vorzeitigem Abbruch — #833-Stil).
        # Rohmaterial für summary_de.py Abschnitt 3.2/3.4 (Median-Wallclock je Strategie,
        # Barriere-Wartezeit je Symbol) und report._worker_utilisation/_symbol_barrier_wait.
        "study_started_at_utc": study_user_attrs.get("study_started_at_utc"),
        "study_ended_at_utc": study_user_attrs.get("study_ended_at_utc"),
        "study_wallclock_s": study_user_attrs.get("study_wallclock_s"),
        "worker_id": study_user_attrs.get("worker_id"),
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
        # Issue #901 — Rohmaterial für invariants.check_guard_reference_coherence.
        "guard_reference_sources": guard_reference_sources,
        # Issue #825 Fix Punkt 3 — expliziter Alias auf denselben #804-Zähler: wie viele Trials
        # dieser Study waehrend des OOS-Fensters wirtschaftlich ruiniert wurden (Equity <= 0,
        # backtest_runner.assert_positive_equity/EQUITY_NONPOSITIVE). Diese Trials sind bereits
        # ueber REJECT_OOS_INVALID_METRICS (#801/#803) NIE oos_eligible und damit NIE
        # promotionsfaehig — dieses Feld macht nur die HAEUFIGKEIT sichtbar, ohne die Zaehl-Logik
        # zu duplizieren.
        "liquidated_trials": inference_diagnostics_by_code.get("EQUITY_NONPOSITIVE", 0),
        # Issue #832 Fix Punkt 1 — je-Study Haltedauer-Extrema (Sekunden), siehe Aggregat oben.
        "max_holding_time_s": max_holding_time_s,
        "p95_holding_time_s": p95_holding_time_s,
        # Issue #839 — je-Trial-Zeitbox-Verletzung, aggregiert je Study (siehe
        # invariants.compute_trial_timebox_violations für die Berechnung je Trial).
        # Issue #903 — TRIAL-Ebene (mind. 1 verletzender Round-Trip im Trial; treibt die
        # #878-Study-Toleranz weiter unten in confirm.py) UND ROUND-TRIP-Ebene (Diagnose: welcher
        # ANTEIL der Trades tatsächlich verletzt) GETRENNT — vorher trug ``timebox_violation_trades``
        # trotz des Namens die TRIAL-Zahl, und ``timebox_trials_invalidated`` war einfach derselbe
        # Wert unter zweitem Namen (confirm.py; hier entfallen).
        "timebox_violating_trials": timebox["timebox_violating_trials"],
        "timebox_evaluated_trades": timebox["timebox_evaluated_trials"],
        "timebox_violation_fraction": timebox["timebox_violation_fraction"],
        "timebox_violating_round_trips": timebox["timebox_violating_round_trips"],
        "timebox_evaluated_round_trips": timebox["timebox_evaluated_round_trips"],
        "timebox_round_trip_violation_fraction": timebox["timebox_round_trip_violation_fraction"],
        "timebox_violation_intensity_p95": timebox["timebox_violation_intensity_p95"],
        "timebox_violated": timebox["timebox_violated"],
        # Issue #861 — Verteilung der Deckel-Referenzquelle (sampled/default/global) über die
        # ausgewerteten Trials dieser Study.
        "timebox_cap_source_counts": timebox["timebox_cap_source_counts"],
        # Issue #897 Fix 3 — Rohmaterial für ``invariants.check_effective_stop_distance``: Median
        # des realisierten Ø-Bruttoverlusts (bps) und der ATR-Telemetrie über die Trials dieser
        # Study (#899). None, wenn keine Exit-Telemetrie vorliegt (Pre-#899-JSON/kein Trade).
        "oos_gross_loss_mean_bps": _median_of_trial_field(trial_attrs, "oos_gross_loss_mean_bps"),
        "atr_median_bps": _median_of_trial_field(trial_attrs, "oos_atr_median_bps"),
        # Issue #897 Fix 3 — Median des je-Trial GESAMPELTEN atr_trailing_multiplier (das
        # Konfigurations-Gegenstueck zur realisierten ATR-Telemetrie oben).
        "atr_trailing_multiplier_median": _median_of_sampled_param(
            trial_attrs, "atr_trailing_multiplier"),
        # Issue #862 — Rohmaterial für den globalen check_guard_reference_coherence-Wächter.
        "oos_n_periods_median": oos_n_periods_median,
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
        # Issue #832 Fix Punkt 2/3 — monetäre Holdout-Kennzahlen (confirm._metrics_dict), für
        # summary_de.py Abschnitt 2 ("Monetäres Ergebnis") ohne zweiten Datenzugriff.
        "holdout_total_return": holdout_metrics.get("oos_total_return"),
        "holdout_expectancy": holdout_metrics.get("oos_expectancy"),
        "holdout_win_rate": holdout_metrics.get("oos_win_rate"),
        "holdout_profit_factor": holdout_metrics.get("oos_profit_factor"),
        "holdout_buyhold_return": holdout_metrics.get("oos_buyhold_return"),
        "holdout_excess_return": holdout_metrics.get("oos_excess_return"),
        # Issue #850 — Anteil der Holdout-Fenster-Zeit mit offener Position (siehe
        # backtest_runner._calculate_stats "exposure_fraction"), Rohmaterial für summary_de.py
        # Abschnitt 2.3 (Excess-Return vs. echtes Alpha) und cross_study.excess_variance_decomposition.
        "holdout_exposure_fraction": holdout_metrics.get("oos_exposure_fraction"),
        "holdout_total_trades": holdout_metrics.get("oos_total_trades"),
        # Issue #826 Fix Punkt 2 — N1: die Multiplizität, die TATSÄCHLICH für diese EINE
        # (Strategie, Symbol)-Study an die Deflation ging (sweep._family_n_stage1_from_studies,
        # unter promotion_family_scope='per_strategy' identisch zu holdout_metrics.deflation_n_
        # family). NICHT mit dem (jetzt nicht mehr für die Deflation verwendeten) symbolweiten
        # cross_study['n_family'] verwechseln (#625, post-hoc Sweep-Telemetrie).
        "n_family_stage1": holdout_metrics.get("deflation_n_family"),
        # Issue #846 — gesetzt, wenn confirm.py die DSR-Berechnung fuer diese Study uebersprungen
        # (oder eine Kohaerenzverletzung zwischen deflated_sr0 und deflated_dsr/deflation_dsr_z an
        # der Export-Grenze unterdrueckt) hat: SMALL_COHORT (deflation_n < 2) oder NO_STATISTIC
        # (der promotete Trial selbst trug kein oos_sortino_period). None ⇒ DSR normal berechnet
        # ODER deflated_selection war gar nicht aktiv.
        "deflation_skipped_reason": holdout_metrics.get("deflation_skipped_reason"),
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


def _diagnosed_pairs_all() -> list[dict[str, Any]]:
    """Issue #829 Fix Punkt 5 — ALLE Einträge des Auto-Diagnose-Caches (nicht nur die
    ``action == 'denylist'``-Teilmenge von ``_diagnosed_pairs_skipped_section``), als Eingabe für
    ``invariants.check_diagnosis_actionability``."""
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        cache = load_diagnosed_pairs_cache()
    except Exception:
        return []
    return list(cache.values())


def _diagnosed_pairs_section() -> list[dict[str, Any]]:
    """Issue #830 Fix Punkt 4 — ALLE Diagnose-Cache-Einträge (nicht nur die ``'denylist'``-
    Teilmenge von ``_diagnosed_pairs_skipped_section``) mit ``action``, ``binding_cause``,
    ``n_runs_confirmed`` und ``expires_after_runs`` je Eintrag: die Deaktivierungs-/Deprioritisierungs-
    Entscheidungen müssen genauso nachvollziehbar sein wie die Promotion-Entscheidungen, nicht nur
    im Cache-JSON verborgen."""
    return [
        {
            "strategy": entry.get("strategy"), "symbol": entry.get("symbol"),
            "action": entry.get("action"), "binding_cause": entry.get("binding_cause"),
            "n_runs_confirmed": entry.get("n_runs_confirmed"),
            "expires_after_runs": entry.get("expires_after_runs"),
            "budget_executed_fraction": entry.get("budget_executed_fraction"),
        }
        for entry in _diagnosed_pairs_all()
    ]


def _boundary_solutions_section() -> list[dict[str, Any]]:
    """Issue #831 Fix Punkt 4 — Randlösungen (``binding_cause == 'boundary_solution'``, aus
    ``confirm.py``/``run_optimization._emit_study_summary``, beide seit #831) als eigene
    Report-Sektion: ``{strategy, symbol, fraction, params, proposed_bounds}`` je Study, deren
    Gewinner an der Suchraumgrenze klebt (``boundary_hit_fraction > 0.3``, #597/#763)."""
    return [
        {
            "strategy": e.get("strategy"), "symbol": e.get("symbol"),
            "fraction": e.get("boundary_hit_fraction"),
            "params": e.get("boundary_params"),
            "proposed_bounds": e.get("proposed_bounds"),
        }
        for e in _diagnosed_pairs_all() if e.get("binding_cause") == "boundary_solution"
    ]


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


def _symbol_coverage_summary(opt_data: dict) -> tuple[dict[str, Any], _inv.InvariantResult]:
    """Issue #841 — ``cross_study.symbol_coverage`` + der zugehörige Invarianten-Check. Liest
    ``data/optimizer/symbol_coverage.json`` (das Ledger, das ``sweep_symbol_order_policy=
    'least_recently_covered'`` für die Dispatch-Reihenfolge nutzt) und das aktuelle Universum
    (``sweep.load_symbol_universe``). Fail-open bei jedem Lese-/Enumerationsfehler — ein Report
    darf wegen einer fehlenden/kaputten Coverage-Datei nie crashen."""
    max_age_runs = int(opt_data.get("symbol_coverage_max_age_runs", 3))
    try:
        universe = load_symbol_universe()
    except Exception:
        universe = []
    ledger = _symbol_coverage.load_coverage()
    coverage = _symbol_coverage.coverage_report(ledger, universe, max_age_runs=max_age_runs)
    check = _inv.check_symbol_coverage(ledger, universe, max_age_runs=max_age_runs)
    return coverage, check


def _coverage_ledger_continuity_check() -> _inv.InvariantResult:
    """Issue #892 Fix Punkt 2 — ermittelt ``has_prior_reports`` aus ``REPORTS_DIR`` (mindestens ein
    ``run_*.json`` existiert bereits — dieser Aufruf läuft VOR dem Schreiben des Reports DIESES
    Laufs, siehe ``generate_sweep_report``, also spiegelt die Liste ausschliesslich frühere Läufe)
    und ruft ``invariants.check_coverage_ledger_continuity`` gegen das aktuelle Ledger. Fail-open
    (kein FAIL) bei jedem Lese-/Enumerationsfehler — ein Report darf wegen dieser Zusatzprüfung nie
    crashen."""
    try:
        has_prior_reports = REPORTS_DIR.exists() and any(REPORTS_DIR.glob("run_*.json"))
    except OSError:
        has_prior_reports = False
    ledger = _symbol_coverage.load_coverage()
    return _inv.check_coverage_ledger_continuity(
        ledger.get("total_runs_started", 0), has_prior_reports)


def _champions_summary(opt_data: dict) -> dict[str, Any]:
    """Issue #818 (#742-Report-Zaehlerpaar) — ``cross_study.champions``:
    ``{stored, admissible, corroborated, written_back, skipped_by_reason}`` über den AKTUELLEN
    Champion-Store-Stand (``data/optimizer/champions/*.json``, exkl. ``_stale/``, #821). Liest den
    Store direkt (dieselbe Quelle, aus der ``resolve_symbol_shrinkage_seed`` seedet) statt der
    Sweep-Log-Events — robust gegen einen Report, der nachträglich (``--report-only``, #833) ohne
    Live-Sweep-Kontext erzeugt wird. Fail-open (leere Zusammenfassung) bei jedem Lesefehler — ein
    Report darf wegen des Champion-Stores nie crashen."""
    import collections
    from automation.optimizer import champions as _champions_mod

    empty = {"stored": 0, "admissible": 0, "corroborated": 0, "written_back": 0,
             "skipped_by_reason": {}, "semantics_migrated": 0,
             "admissible_despite_simulation_stale": 0}
    try:
        champions_dir = _champions_mod._champions_dir()
        paths = sorted(p for p in champions_dir.glob("champion_*.json") if p.is_file())
    except OSError:
        return empty

    stored = 0
    admissible = 0
    corroborated = 0
    written_back = 0
    semantics_migrated = 0
    admissible_despite_simulation_stale = 0
    skipped_by_reason: collections.Counter = collections.Counter()
    promote_after = int(opt_data.get("champion_promote_after_runs", 2))
    for path in paths:
        entry = _load_json(path)
        if not isinstance(entry, dict):
            continue
        stored += 1
        lifecycle = entry.get("lifecycle") or {}
        # Issue #834 Akzeptanzkriterium 3 — je Eintrag, ob ``champions.maybe_write_back`` (#819)
        # ihn ueber einen ``reward_semantics_version``-Bump hinweg MIGRIERT hat (params ueberleben
        # den Bump, quality wird 'stale'), statt ihn zu purgen. Der Purge (purge_stale_studies.py)
        # betrifft ausschliesslich die Optuna-SQLite-Studies (WORK/sweep/*.db), NIE den
        # Champion-Store selbst — dieser Zaehler ist der report-seitige Nachweis dafuer.
        if lifecycle.get("semantics_migrated_from") is not None:
            semantics_migrated += 1
        try:
            ok, reason = _champions_mod.champion_is_admissible(entry, opt_data)
        except Exception:
            ok, reason = False, "ADMISSIBILITY_CHECK_ERROR"
        # Issue #854 — Regressionswaechter-Rohmaterial: ein simulation_semantics_version-stale
        # Eintrag MUSS champion_is_admissible bereits verworfen haben (harter Ausschluss, siehe
        # champions.champion_simulation_stale-Docstring) — dieser Zaehler macht sichtbar, ob diese
        # Garantie tatsaechlich haelt, statt sie nur zu behaupten.
        try:
            simulation_stale = _champions_mod.champion_simulation_stale(entry, opt_data)
        except Exception:
            simulation_stale = False
        if simulation_stale and ok:
            admissible_despite_simulation_stale += 1
        if not ok:
            skipped_by_reason[reason or "UNKNOWN"] += 1
            continue
        admissible += 1
        if int(lifecycle.get("corroboration_count", 0) or 0) >= promote_after:
            corroborated += 1
        if lifecycle.get("writeback_applied"):
            written_back += 1
        else:
            try:
                stale = _champions_mod.champion_quality_stale(entry, opt_data)
            except Exception:
                stale = False
            skipped_by_reason["QUALITY_STALE" if stale else "NOT_WRITTEN_BACK"] += 1
    return {
        "stored": stored, "admissible": admissible, "corroborated": corroborated,
        "written_back": written_back, "skipped_by_reason": dict(skipped_by_reason),
        "semantics_migrated": semantics_migrated,
        "admissible_despite_simulation_stale": admissible_despite_simulation_stale,
    }


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


def _excess_variance_decomposition(studies_out: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Issue #850 — einfache Ein-Weg-Varianzzerlegung von ``holdout_excess_return`` NACH SYMBOL
    (Quadratsummen-Zerlegung, analog einer einfaktoriellen ANOVA): ``symbol_share`` ist der Anteil
    der GESAMT-Streuung, der bereits durch die reine Symbol-Gruppierung erklärt wird
    (Zwischen-Gruppen-Quadratsumme / Gesamt-Quadratsumme); ``strategy_share`` ist der Rest
    (Streuung INNERHALB eines Symbols — Strategie-Unterschied UND Restrauschen, hier bewusst NICHT
    weiter getrennt, siehe Issue-Text "Strategie + Rest").

    Root-Cause #850/Pitfall #268: 14 strukturell verschiedene Strategien lieferten auf demselben
    Symbol Holdout-Excess-Returns innerhalb weniger Prozentpunkte, bei einem absoluten Niveau von
    über 20 — der Excess-Return maß im Bärenmarkt näherungsweise NUR ``−Buy&Hold`` (eine
    Symbol-Konstante), nicht die Handelsleistung der Strategie. ``None`` bei < 2 Symbolen ODER
    < 2 Datenpunkten insgesamt (Varianz nicht definiert) — reine additive Diagnose-Telemetrie."""
    rows = [
        (r.get("symbol"), r.get("holdout_excess_return"))
        for r in studies_out
        if r.get("holdout_excess_return") is not None and r.get("symbol") is not None
    ]
    if len(rows) < 2:
        return None
    by_symbol: dict[str, list[float]] = {}
    for symbol, excess in rows:
        by_symbol.setdefault(symbol, []).append(float(excess))
    if len(by_symbol) < 2:
        return None
    all_values = [v for values in by_symbol.values() for v in values]
    grand_mean = sum(all_values) / len(all_values)
    ss_total = sum((v - grand_mean) ** 2 for v in all_values)
    if ss_total <= 0:
        return {"symbol_share": None, "strategy_share": None, "n_rows": len(all_values)}
    ss_between = sum(
        len(values) * (sum(values) / len(values) - grand_mean) ** 2
        for values in by_symbol.values()
    )
    symbol_share = ss_between / ss_total
    return {
        "symbol_share": symbol_share,
        "strategy_share": max(0.0, 1.0 - symbol_share),
        "n_rows": len(all_values),
    }


def _wallclock_by_strategy(studies_out: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Issue #851 — Median/p90 von ``study_wallclock_s`` je Strategie, für summary_de.py Abschnitt
    3.2 ("Laufzeit je Symbol/Strategie") — vorher aus dem #742-Report nicht ableitbar (keine
    Wallclock-Telemetrie je Study), musste aus Log-Zeitstempeln rekonstruiert werden."""
    import statistics as _stats
    by_strategy: dict[str, list[float]] = {}
    for r in studies_out:
        strategy, wc = r.get("strategy"), r.get("study_wallclock_s")
        if strategy is None or wc is None:
            continue
        by_strategy.setdefault(strategy, []).append(float(wc))
    out: dict[str, dict[str, Any]] = {}
    for strategy, times in by_strategy.items():
        times_sorted = sorted(times)
        p90_idx = max(0, min(len(times_sorted) - 1, round(0.9 * (len(times_sorted) - 1))))
        out[strategy] = {
            "median": _stats.median(times_sorted),
            "p90": times_sorted[p90_idx],
            "n": len(times_sorted),
        }
    return out


def _symbol_barrier_wait(studies_out: list[dict[str, Any]]) -> dict[str, float]:
    """Issue #851 — je Symbol: ``max(study_wallclock_s) − min(study_wallclock_s)`` über die
    Strategien-Studies dieses Symbols — die Zeit, die das Symbol auf seine LANGSAMSTE Strategie
    wartet, relativ zu seiner schnellsten (#828-Barriere-Konzept). Symbole mit nur EINER Study
    (kein Warten möglich) sind nicht enthalten."""
    by_symbol: dict[str, list[float]] = {}
    for r in studies_out:
        symbol, wc = r.get("symbol"), r.get("study_wallclock_s")
        if symbol is None or wc is None:
            continue
        by_symbol.setdefault(symbol, []).append(float(wc))
    return {
        symbol: max(times) - min(times)
        for symbol, times in by_symbol.items()
        if len(times) >= 2
    }


def _worker_utilisation(studies_out: list[dict[str, Any]], *, n_jobs: int | None,
                        sweep_wallclock_s: float | None) -> float | None:
    """Issue #851 — Σ Study-Wallclock / (n_jobs × Sweep-Wallclock): der Anteil der theoretisch
    verfügbaren Worker-Zeit, der tatsächlich mit Study-Arbeit gefüllt war (1.0 = perfekte
    Auslastung; auf einem seriellen Referenzlauf, n_jobs=1, ≈ 1.0 abzüglich Preflight-/Dispatch-
    Overhead). None ohne n_jobs/sweep_wallclock_s ODER ohne eine einzige Study mit Wallclock-Daten."""
    if not n_jobs or n_jobs <= 0 or not sweep_wallclock_s or sweep_wallclock_s <= 0:
        return None
    total_study_wallclock = sum(
        r["study_wallclock_s"] for r in studies_out if r.get("study_wallclock_s") is not None)
    if total_study_wallclock <= 0:
        return None
    return total_study_wallclock / (n_jobs * sweep_wallclock_s)


def _seed_source_distribution(studies_out: list[dict[str, Any]]) -> dict[str, int]:
    """Issue #853 Fix Punkt 3/4 — Verteilung von ``seed_source`` über alle Studies dieses Laufs
    (``report._study_record``), Rohmaterial für ``invariants.check_champion_seed_coverage`` und
    die #742-Report-Transparenz ("welcher Anker speiste den Warm-Start tatsächlich"). Studies ohne
    Telemetrie (Pre-#853-Lauf) landen unter ``'unknown'``."""
    out: dict[str, int] = {}
    for r in studies_out:
        source = r.get("seed_source") or "unknown"
        out[source] = out.get(source, 0) + 1
    return out


def _longest_holding_studies(studies_out: list[dict[str, Any]], *, top_k: int = 10) -> list[dict[str, Any]]:
    """Issue #832 Fix Punkt 1/3 — Top-``top_k`` Studies nach ``max_holding_time_s`` absteigend, für
    summary_de.py Abschnitt 4 ("Trades mit der längsten Haltedauer"). Rein additiv aus dem bereits
    je Study berechneten Aggregat (``report._study_record``) — KEINE erneute Trial-Iteration."""
    with_data = [r for r in studies_out if r.get("max_holding_time_s") is not None]
    ranked = sorted(with_data, key=lambda r: r["max_holding_time_s"], reverse=True)
    return [
        {
            "strategy": r.get("strategy"), "symbol": r.get("symbol"),
            "max_holding_time_s": r["max_holding_time_s"],
            "p95_holding_time_s": r.get("p95_holding_time_s"),
        }
        for r in ranked[:top_k]
    ]


def _family_n_stages(studies_out: list[dict[str, Any]]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Issue #826 Fix Punkt 2 — Akzeptanzkriterium 2: ``n_family_stage1``/``n_family_stage2``
    GETRENNT im Report ausgewiesen. ``n_family_stage1``: je Symbol die N1-Zahl JEDER Strategie
    (``record['n_family_stage1']``, aus ``holdout.symbol.deflation_n_family`` — unter dem Default
    ``promotion_family_scope='per_strategy'`` die tatsächlich für die Deflation verwendete Zahl).
    ``n_family_stage2``: je Symbol die Zahl der Strategien mit N1 > 0 (dieselbe Definition wie
    ``sweep._family_n_stage2_from_studies``, hier post-hoc aus den exportierten Proposals statt aus
    den Study-Objekten — reine Telemetrie, siehe deren Docstring für den Deferral-Status von
    ``promotion_family_scope='per_symbol_best'``)."""
    stage1: dict[str, dict[str, int]] = {}
    stage2: dict[str, int] = {}
    for r in studies_out:
        symbol = r.get("symbol")
        strategy = r.get("strategy")
        n1 = r.get("n_family_stage1")
        if not symbol or not strategy or n1 is None:
            continue
        n1 = int(n1)
        stage1.setdefault(symbol, {})[strategy] = n1
        stage2.setdefault(symbol, 0)
        if n1 > 0:
            stage2[symbol] += 1
    return stage1, stage2


def parse_proposal_payloads(proposals: Iterable[Path | dict]) -> list[dict]:
    """Issue #856 — EINZIGE Path→dict-Normalisierung für Proposal-Listen, konsumiert von
    ``generate_sweep_report`` UND der #839-Fail-Fast-Probe (``sweep.py``). Vorher lag diese Logik
    ausschliesslich inline in ``generate_sweep_report`` (#1093-1097 vor diesem Fix) — die Probe rief
    ``_build_report`` direkt mit ``list[Path]`` auf und erzeugte bei JEDEM Aufruf eine
    ``AttributeError`` (Pitfall #269, siebte Wiederkehr von #237).

    Elemente, die bereits ``dict`` sind, werden unverändert übernommen (Test-Pfad); ``Path``-
    Elemente werden von der Platte geladen. Nur Payloads mit einem ``symbol``-Feld gelten als
    valide Proposals (dieselbe Filterregel wie vorher in ``generate_sweep_report``)."""
    parsed: list[dict] = []
    for p in proposals:
        payload = p if isinstance(p, dict) else _load_json(Path(p))
        if isinstance(payload, dict) and payload.get("symbol"):
            parsed.append(payload)
    return parsed


def build_probe_report(proposals: Iterable[Path | dict], *, run_id: str) -> dict:
    """Issue #856 — dünner, öffentlicher Wrapper für die #839-Fail-Fast-Probe in ``sweep.py``:
    parst + baut den Report in einem Aufruf, schreibt NICHTS auf die Platte (reine Lesefunktion).
    Hält ``_build_report`` als modulinternen Kern, dessen einziger externer Konsument dieser
    Wrapper (und ``generate_sweep_report``) ist — die Call-Site in ``sweep.py`` kann die Path→dict-
    Normalisierung dadurch nicht mehr umgehen (Root-Cause #856, Pitfall #269)."""
    return _build_report(
        parse_proposal_payloads(proposals),
        run_id=run_id, started_at_utc=None, wallclock_s=None, cli_args=None,
    )


def _build_report(
    proposals: list[dict],
    *,
    run_id: str,
    started_at_utc: str | None,
    wallclock_s: float | None,
    cli_args: dict | None,
    run_status: str = "complete",
    symbols_completed: int | None = None,
    symbols_planned: int | None = None,
) -> dict:
    # Issue #856 Fix Punkt 4 — fail-loud statt einer nichtssagenden AttributeError in
    # ``_load_study_for_proposal``: ``_build_report`` erwartet ausschliesslich geparste Proposal-
    # Dicts. Ein ``Path``-Element (oder jedes andere Nicht-Dict) an erster Stelle ist ein
    # struktureller Programmierfehler an der Call-Site, keine Laufzeitbedingung — er muss dort
    # sichtbar werden, nicht drei Stack-Frames tiefer als ``'PosixPath' object has no attribute
    # 'get'``.
    if proposals and not isinstance(proposals[0], dict):
        raise TypeError(
            "_build_report erwartet geparste Proposal-Dicts; nutze parse_proposal_payloads() "
            f"(erhalten: {type(proposals[0]).__name__})"
        )

    tournament_path = config_dir() / "tournament.json"
    optimizer_path = config_dir() / "optimizer.json"
    tournament_cfg = _load_json(tournament_path) or {}
    optimizer_cfg = _load_json(optimizer_path) or {}

    studies_out: list[dict[str, Any]] = []
    all_checks: list[tuple[str, _inv.InvariantResult]] = []
    for proposal in proposals:
        study = _load_study_for_proposal(proposal)
        record, checks = _study_record(
            proposal, study, tournament_cfg,
            guard_dominance_threshold=float(
                optimizer_cfg.get("sortino_guard_trip_fraction_warn", 0.10)))
        studies_out.append(record)
        study_label = f"{record['strategy']}/{record['symbol']}"
        all_checks.extend((study_label, c) for c in checks)
        # Issue #791 — REJECT_SELECTION_PBO erfordert eine dokumentierte Promotions-Inferenz.
        all_checks.append((study_label, _inv.check_promotion_inference_coverage(proposal, record)))

    n_family_stage1, n_family_stage2 = _family_n_stages(studies_out)

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

    # Issue #897 Fix 3 — der Trailing-Stop-Anker muss auf seinen eigenen Multiplikator reagieren
    # (Pitfall #286): der Median des realisierten Ø-Bruttoverlusts (#899-Telemetrie) darf
    # ``stop_distance_min_ratio`` (Default 0.4) nicht relativ zum konfigurierten Stop-Abstand
    # (k_median · ATR_median) unterschreiten.
    stop_distance_min_ratio = float(optimizer_cfg.get("stop_distance_min_ratio", 0.4))
    effective_stop_distance_check = _inv.check_effective_stop_distance(
        studies_out, min_ratio=stop_distance_min_ratio)
    all_checks.append(("global", effective_stop_distance_check))

    # Issue #818 — achter Invarianten-Check: der Champion-Store-Writeback-Pfad (Ebene 2, #706)
    # muss NACHWEISLICH erreichbar sein, nicht nur getestet/dokumentiert (Pitfall #237).
    champions_summary = _champions_summary(optimizer_cfg)
    champion_writeback_check = _inv.check_champion_writeback_reachability(champions_summary)
    all_checks.append(("global", champion_writeback_check))

    # Issue #853 Fix Punkt 4 — vierzehnter Invarianten-Check: WARNUNG (severity='low'), wenn der
    # Champion-Seed-Anker fuer > 90% der Studies dieses Laufs auf strategy_defaults zurueckfaellt
    # (der Closed Loop, #702, ist dann nachweislich unwirksam — siehe check_champion_seed_coverage-
    # Docstring fuer den Scope-Hinweis zur Ein-Lauf- statt Zwei-Lauf-Schwelle).
    seed_source_distribution = _seed_source_distribution(studies_out)
    champion_seed_coverage_check = _inv.check_champion_seed_coverage(seed_source_distribution)
    all_checks.append(("global", champion_seed_coverage_check))

    # Issue #854 Fix Punkt 6 — fuenfzehnter Invarianten-Check: kein Champion-Store-Eintrag mit
    # veralteter simulation_semantics_version darf trotzdem als admissible gelten.
    semantics_version_coherence_check = _inv.check_semantics_version_coherence(
        champions_summary.get("admissible_despite_simulation_stale", 0))
    all_checks.append(("global", semantics_version_coherence_check))

    # Issue #829 — neunter Invarianten-Check: ein Evidenzschwellen-Deadlock (Pitfall #258) macht
    # sich als viele diagnosed_pairs_cache-Einträge derselben (strategy, binding_cause)-Kombination
    # mit action=='none' bemerkbar.
    diagnosis_actionability_check = _inv.check_diagnosis_actionability(_diagnosed_pairs_all())
    all_checks.append(("global", diagnosis_actionability_check))

    # Issue #832 Fix Punkt 1 / #861 (Unifikation) — zehnter Invarianten-Check: keine Study darf
    # einen Anteil zeitbox-verletzender Trials ueber ``timebox_violation_study_tolerance`` tragen
    # (dieselbe Schwelle, die #857 fuer die Study-Ebene-Konsequenz in confirm.py verwendet) — ein
    # Treffer ist ein Bug im Exit-Pfad.
    _timebox_study_tolerance = float(tournament_cfg.get("timebox_violation_study_tolerance", 0.25))
    holding_time_cap_check = _inv.check_holding_time_cap(
        studies_out, study_tolerance=_timebox_study_tolerance)
    all_checks.append(("global", holding_time_cap_check))

    # Issue #841 — elfter Invarianten-Check: kein Symbol des aktuellen Universums darf seit mehr
    # als symbol_coverage_max_age_runs abgeschlossenen Läufen unabgedeckt bleiben (least_recently_
    # covered-Rotation, siehe symbol_coverage.py).
    symbol_coverage_summary, symbol_coverage_check = _symbol_coverage_summary(optimizer_cfg)
    all_checks.append(("global", symbol_coverage_check))

    # Issue #892 Fix Punkt 2 — ein bei Laufbeginn auf 1 zurückgesetztes Coverage-Ledger, obwohl
    # bereits frühere Lauf-Reports existieren, ist ein Datenverlust (achte Wiederkehr Pitfall #237).
    all_checks.append(("global", _coverage_ledger_continuity_check()))

    # Issue #862 — der konfigurierte sortino_numeric_guard_min_periods-Referenzwert muss zur
    # tatsächlich beobachteten n_periods-Grössenordnung DIESES Laufs passen (Pitfall #274).
    _guard_min_periods = tournament_cfg.get("sortino_numeric_guard_min_periods")
    _observed_n_periods_medians = [
        r["oos_n_periods_median"] for r in studies_out if r.get("oos_n_periods_median")
    ]
    _observed_guard_reference_sources = [
        s for r in studies_out for s in (r.get("guard_reference_sources") or [])
    ]
    guard_reference_coherence_check = _inv.check_guard_reference_coherence(
        _guard_min_periods, _observed_n_periods_medians,
        reference_mode=tournament_cfg.get("sortino_numeric_guard_reference"),
        observed_guard_reference_sources=_observed_guard_reference_sources)
    all_checks.append(("global", guard_reference_coherence_check))

    # Issue #848 — zwoelfter Invarianten-Check: nach der Entfernung des unerreichbaren
    # min_win_rate-OR-Arms ist mehr als EIN selection_rule_fingerprint je Symbol eine ANDERE,
    # unbekannte Ursache (vorher WARNUNG in sweep.py [#812], jetzt FAIL).
    selection_rule_families = _selection_rule_families(studies_out)
    selection_rule_homogeneity_check = _inv.check_selection_rule_homogeneity(selection_rule_families)
    all_checks.append(("global", selection_rule_homogeneity_check))

    # Issue #852 — dreizehnter Invarianten-Check: eine installierte Bibliotheksversion ausserhalb
    # ihres gepinnten Bereichs (optimizer.json['pinned_library_versions']) macht den numerischen
    # Ausgang der Selektion von der Installationsumgebung statt der Konfiguration abhaengig
    # (dieselbe Fehlerklasse wie #801/#802 bei pandas).
    library_version_drift_check = _inv.check_library_version_drift(
        library_versions(), optimizer_cfg.get("pinned_library_versions") or {})
    all_checks.append(("global", library_version_drift_check))

    # Issue #907 Fix 3 — symmetrischer Meta-Wächter: eine in fail_fast_invariants gelistete
    # Invariante, die in diesem Lauf kein einziges Ergebnis (PASS oder FAIL) meldet, ist nicht
    # verdrahtet. Muss NACH allen anderen Checks stehen (braucht ihre Namen), bevor invariant_checks
    # gebaut wird.
    _already_evaluated_names = [c.name for _label, c in all_checks]
    fail_fast_wired_check = _inv.check_fail_fast_invariants_wired(
        _already_evaluated_names, fail_fast_invariants=optimizer_cfg.get("fail_fast_invariants"))
    all_checks.append(("global", fail_fast_wired_check))

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
        # Issue #833 Fix Punkt 3 — ein Report entsteht seit diesem Fix AUCH bei einem vorzeitigen
        # Sweep-Abbruch (disk_guard/wallclock_guard/SIGINT/SIGTERM/unerwartete Exception, siehe
        # sweep.main()); run_status macht den Abbruchgrund maschinenlesbar, statt nur implizit aus
        # einer unvollstaendigen studies[]-Liste erschlossen werden zu muessen. Default 'complete'
        # (bit-identisch fuer jeden Aufrufer, der die drei neuen Kwargs nicht setzt).
        "run_status": run_status,
        "symbols_completed": symbols_completed,
        "symbols_planned": symbols_planned,
        # Issue #849 — im Report EINGEBETTET (statt eines zweiten config_dir()-Lesezugriffs in
        # summary_de.py, das bewusst reines Rueckgabedict-only bleibt, siehe Moduldocstring dort):
        # Sektion 5.2 zeigt hoechstens so viele Beispiel-Details je Check, bevor sie auf "... und N
        # weitere" kollabiert (Akzeptanzkriterium #849-5, Bericht bleibt bei >= 500 FAILs kompakt).
        "summary_max_details_per_check": int(optimizer_cfg.get("summary_max_details_per_check", 5)),
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
            # Issue #830 Fix Punkt 4 — ALLE Diagnose-Cache-Eintraege (denylist UND deprioritized
            # UND none-mit-Ursache), nicht nur die uebersprungene Teilmenge oben.
            "diagnosed_pairs": _diagnosed_pairs_section(),
            # Issue #831 Fix Punkt 4 — Randlösungen (boundary_hit_fraction > 0.3) mit ihrem
            # konkreten Bounds-Vorschlag, unabhängig davon, ob die Study eligible Trials hatte.
            "boundary_solutions": _boundary_solutions_section(),
            # Issue #812 — je Symbol nach selection_rule_fingerprint gruppierte n_family: macht eine
            # innerhalb eines Symbols heterogene Selektionsregel (verschiedene #668-Policy-Ausgaenge
            # ueber die Studies hinweg) sichtbar, statt sie in EINER Zahl zu verstecken.
            "selection_rule_families": selection_rule_families,
            # Issue #818 — stored/admissible/corroborated/written_back/skipped_by_reason über den
            # aktuellen Champion-Store-Stand (Epic #702 Ebene 1+2 Reachability-Telemetrie).
            "champions": champions_summary,
            # Issue #841 — {never_covered, stale_symbols, oldest_coverage_age_runs,
            # total_runs_started} über das aktuelle Universum (symbol_coverage.json-Ledger).
            "symbol_coverage": symbol_coverage_summary,
            # Issue #826 Fix Punkt 2 (Akzeptanzkriterium 2) — n_family_stage1 (je Symbol/Strategie,
            # die tatsächlich verwendete Per-Strategie-Multiplizität N1) UND n_family_stage2 (je
            # Symbol, Zahl der Strategien mit N1 > 0) GETRENNT ausgewiesen — NICHT zu verwechseln mit
            # dem obigen cross_study['n_family'] (#625, symbolweite Summe über deflation_n_eligible).
            "n_family_stage1": n_family_stage1,
            "n_family_stage2": n_family_stage2,
            # Issue #832 Fix Punkt 1/3 — Top-K Studies nach Haltedauer (Sekunden), fuer
            # summary_de.py Abschnitt 4 "Trades mit der laengsten Haltedauer".
            "longest_holding_studies": _longest_holding_studies(
                studies_out, top_k=int(optimizer_cfg.get("report_longest_trades_k", 10))),
            # Issue #850 — {symbol_share, strategy_share, n_rows} über holdout_excess_return;
            # None bei < 2 Symbolen mit Benchmark-Daten (siehe Docstring).
            "excess_variance_decomposition": _excess_variance_decomposition(studies_out),
            # Issue #851 — Study-Zeitstempel-abgeleitete Kennzahlen (summary_de.py Abschnitt
            # 3.2/3.4); dieselben Felder speisen das #843-LPT-Scheduling (Katalog B).
            "wallclock_by_strategy": _wallclock_by_strategy(studies_out),
            "symbol_barrier_wait_s": _symbol_barrier_wait(studies_out),
            "worker_utilisation": _worker_utilisation(
                studies_out, n_jobs=(cli_args or {}).get("n_jobs"), sweep_wallclock_s=wallclock_s),
            # Issue #853 — {seed_source_value: n_studies}, dieselbe Verteilung, die
            # check_champion_seed_coverage prüft.
            "seed_source_distribution": seed_source_distribution,
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
    run_status: str = "complete",
    symbols_completed: int | None = None,
    symbols_planned: int | None = None,
) -> Path:
    """Baut + schreibt ATOMAR den Report für GENAU DIESEN Sweep-Lauf.

    ``proposals`` sind die von ``run_per_symbol_sweep`` zurückgegebenen Proposal-Pfade (oder
    bereits geparste Dicts, Test-Pfad) — jede referenzierte Study wird FRISCH aus ihrer SQLite-
    Datei geladen (kein Live-Zustand aus dem Sweep-Lauf selbst nötig), was diesen Pfad bit-
    identisch mit ``generate_report_for_run`` macht (Determinismus-Garantie, #742-Akzeptanz).

    Issue #833 Fix Punkt 3 — ``run_status``/``symbols_completed``/``symbols_planned`` werden NUR
    durchgereicht (siehe ``_build_report``); Default ``run_status='complete'`` ⇒ bit-identisch für
    jeden Aufrufer, der einen abgeschlossenen Lauf reportet (der bisherige Normalfall)."""
    parsed = parse_proposal_payloads(proposals)

    report = _build_report(
        parsed, run_id=run_id, started_at_utc=started_at_utc,
        wallclock_s=wallclock_s, cli_args=cli_args,
        run_status=run_status, symbols_completed=symbols_completed,
        symbols_planned=symbols_planned,
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
    run_status: str = "complete",
    symbols_completed: int | None = None,
    symbols_planned: int | None = None,
) -> Path:
    """Standalone/nachträgliche Rekonstruktion — KEINE laufende Sweep-Orchestrierung nötig.

    Entdeckt alle aktuell unter ``proposals_dir`` (Default ``WORK``) liegenden Per-Symbol-
    Proposals (``proposal_{strategy}_{symbol}.json``, unterscheidbar von den strategie-globalen
    ``proposal_{strategy}.json`` am vorhandenen ``symbol``-Feld) und delegiert an denselben Kern
    wie ``generate_sweep_report`` — deckt den Fall "ein alter, bereits gelaufener Sweep soll
    nachträglich reportet werden, für den kein Live-Log mehr existiert" ab (die Proposal-JSONs
    UND die SQLite-Studies sind beide durabel, #742-Ist-Zustand).

    Issue #833 Fix Punkt 3 — DAS ist der Kern des Abbruch-Artefakts (siehe ``sweep.main()``): ein
    Sweep, der abbricht BEVOR er seinen eigenen ``generate_sweep_report``-Aufruf erreicht, hat
    seine bereits abgeschlossenen Symbole trotzdem als ``proposal_*.json`` auf der Platte — diese
    Funktion baut daraus denselben Report, den ein vollständiger Lauf erzeugt hätte, nur mit
    ``run_status`` != 'complete'. Fix Punkt 4 (``--report-only``) ruft exakt diese Funktion auf."""
    base = Path(proposals_dir or WORK)
    proposal_paths = sorted(
        p for p in base.glob("proposal_*.json")
        if (_load_json(p) or {}).get("symbol")
    )
    return generate_sweep_report(
        proposal_paths, run_id=run_id, started_at_utc=started_at_utc,
        wallclock_s=wallclock_s, cli_args=cli_args, reports_dir=reports_dir,
        run_status=run_status, symbols_completed=symbols_completed,
        symbols_planned=symbols_planned,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
