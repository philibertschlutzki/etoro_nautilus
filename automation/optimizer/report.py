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
    _constraint_violation_progress, compute_budget_execution, _best_completed_value,
)
from automation.optimizer.sweep import (
    _family_n_from_proposals, load_symbol_universe, read_symbol_bar_quality_cache,
)
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


def _trade_amount_pct_by_strategy(base_cfg: Path | None = None) -> dict[str, float]:
    """Issue #1028 (Katalog #866) — der EFFEKTIVE ``trade_amount_pct`` je Strategie
    (``strategy_defaults.json``, ``strategies.json[params]`` hat Vorrang, siehe
    ``strategy_defaults.json['_schema']``), Rohmaterial für
    ``invariants.check_sizing_identity_coherence``. Bewusst vereinfacht gegenüber
    ``resolve.resolve_params`` (kein Instrument-/Champion-Seed-/Sampled-Overlay): die Sizing-
    Identität prüft die STRATEGIEWEITE Konfigurationskonstante, nicht ein per-Symbol gesampeltes
    Sizing (``spaces.py`` sampelt ``trade_amount_pct`` nicht, siehe Katalog-B-1). Kein Caching
    (im Gegensatz zu den ``_read_*``-Helfern in backtest_runner.py): wird genau EINMAL je
    ``_build_report``-Aufruf gelesen, nicht je Trial — der Perf-Vorteil eines Prozess-globalen
    Caches wäre hier vernachlässigbar, ein zwischen ``config_dir()``-Wechseln (Tests) veraltender
    Cache aber ein echtes Korrektheitsrisiko."""
    cfg_dir = base_cfg or config_dir()
    out: dict[str, float] = {}
    defaults = _load_json(cfg_dir / "strategy_defaults.json") or {}
    for strat, params in defaults.items():
        if isinstance(params, dict) and params.get("trade_amount_pct") is not None:
            out[strat] = params["trade_amount_pct"]
    strategies_cfg = _load_json(cfg_dir / "strategies.json") or {}
    for entry in strategies_cfg.get("strategies") or []:
        if not isinstance(entry, dict):
            continue
        override = (entry.get("params") or {}).get("trade_amount_pct")
        if override is not None:
            out[entry.get("strategy_class")] = override
    return out


def _atr_floor_bps_by_symbol(symbols: Iterable[str], base_cfg: Path | None = None) -> dict[str, float]:
    """Issue #1071 (Pitfall #380-Klasse) — löst je Symbol den konfigurierten ATR-Floor auf
    (``backtest_runner.resolve_atr_floor_bps`` über dieselbe Asset-Class-Auflösungskette wie der
    Worker selbst, #924). Rohmaterial für ``invariants.check_atr_scale_homogeneity``s
    ``atr_floor_binding_studies``-Mechanismus-Unterscheidung: eine Study, deren ``atr_median_bps``
    auf diesem Wert liegt, hat einen Nenner an einer KONFIGURIERTEN Konstante, keiner Preis-
    Beobachtung. Lazy-Import (``backtest_runner`` zieht ``nautilus_trader`` — dieselbe Konvention
    wie ``invariants.check_config_key_registry``). Fail-open ({}) bei jedem Lese-/Importfehler —
    ein Report darf wegen dieser Zusatzauflösung nie crashen."""
    try:
        from automation.backtest_runner import resolve_atr_floor_bps, _resolve_asset_class_for_symbol
    except Exception:
        return {}
    cfg_dir = base_cfg or config_dir()
    data = _load_json(cfg_dir / "backtest.json") or {}
    atr_floor_by_asset_class = data.get("atr_floor_bps_by_asset_class") or {}
    out: dict[str, float] = {}
    for symbol in {s for s in symbols if s}:
        try:
            asset_class_key = "DEFAULT"
            if atr_floor_by_asset_class:
                asset_class_key = _resolve_asset_class_for_symbol(symbol)
            out[symbol] = resolve_atr_floor_bps(symbol, atr_floor_by_asset_class, asset_class_key)
        except Exception:
            continue
    return out


def _round_trip_cost_bps_by_symbol(symbols: Iterable[str]) -> dict[str, float]:
    """Issue #1072 — löst je Symbol die config-abgeleitete Round-Trip-Kostenbasis (c_rt) auf
    (``backtest_runner._read_default_round_trip_cost_bps``, dieselbe Auflösungskette wie das
    kostenrelative Expectancy-Gate, #684/#775). Rohmaterial für
    ``invariants.check_stop_cost_ratio``. Lazy-Import (``backtest_runner`` zieht
    ``nautilus_trader``, dieselbe Konvention wie ``_atr_floor_bps_by_symbol``). Fail-open ({}) bei
    jedem Lese-/Importfehler."""
    try:
        from automation.backtest_runner import _read_default_round_trip_cost_bps
    except Exception:
        return {}
    out: dict[str, float] = {}
    for symbol in {s for s in symbols if s}:
        try:
            out[symbol] = _read_default_round_trip_cost_bps(symbol)
        except Exception:
            continue
    return out


def _max_symbol_exposure_fraction(base_cfg: Path | None = None) -> float | None:
    """Issue #1042 (Katalog #866) E-2 — ``backtest.json['live_risk']['max_symbol_exposure_
    fraction']``, dieselbe Konfigurationsquelle, aus der ``momentum_ls_run.py`` den
    ``MomentumLSAllocator`` live konstruiert (siehe dessen Modul-Docstring). Rohmaterial für
    ``invariants.check_sizing_parity_backtest_vs_allocator``. ``None`` ohne Datei/Schlüssel
    (fail-open — der Check selbst behandelt ``None`` als "nicht anwendbar", keine stille
    Default-Annahme über einen Live-Risikoparameter)."""
    cfg_dir = base_cfg or config_dir()
    data = _load_json(cfg_dir / "backtest.json") or {}
    value = (data.get("live_risk") or {}).get("max_symbol_exposure_fraction")
    return float(value) if value is not None else None


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


def _split_near_miss_deltas(
    raw_deltas: dict, tournament_cfg: dict,
) -> tuple[dict, dict, str | None, dict]:
    """Issue #790 — trennt AKTIVE Gates (tatsaechlich Teil von ``eligible_requires_all``/``_any``,
    ueber ``reward._active_gate_collinearity_keys`` — dieselbe Laufzeit-Quelle wie der #760-
    Kollinearitaets-Check, KEINE zweite gepflegte Liste) von WEICHEN Distanztermen (deaktivierte
    Gates wie ``oos_min_profitable_folds_frac``/``oos_min_expectancy``, die weiterhin als Near-Miss-
    Telemetrie berechnet werden, aber keine Eligibility-Entscheidung mehr treffen — Root-Cause #790:
    88 % der Studies mit eligiblen Trials meldeten ein "bindendes" Gate, das gar nicht mehr galt).

    Issue #1074 (Pitfall #375-Klasse, Wiederkehr #631) — ``binding_gate`` ist seit diesem Fix das
    Gate mit dem negativsten NORMIERTEN Delta (``reward.normalize_gate_deltas_for_binding``,
    dimensionslos) INNERHALB von ``binding`` (niemals aus ``soft``, unverändert die #790-Garantie).
    Root-Cause #1074: ``argmin`` über die ROHEN Deltas liess ein grosskaliges Gate (``oos_min_
    trades``, Skala 10²) NIE gewinnen, unabhängig davon, welches Gate ökonomisch tatsächlich band
    (Beweis B-11 im #866-Katalog). Beide Formen (roh UND normiert) bleiben im Report sichtbar,
    damit der Wechsel nachvollziehbar bleibt.

    Rückgabe ``(binding, soft, binding_gate, binding_normalized)``."""
    active_keys = set(_reward._active_gate_collinearity_keys(tournament_cfg))
    binding = {k: v for k, v in raw_deltas.items() if k in active_keys}
    soft = {k: v for k, v in raw_deltas.items() if k not in active_keys}
    binding_normalized = _reward.normalize_gate_deltas_for_binding(binding, tournament_cfg)
    binding_gate = (
        min(binding_normalized, key=lambda k: binding_normalized[k])
        if binding_normalized else None)
    return binding, soft, binding_gate, binding_normalized


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


def _time_box_exit_fraction(trial_attrs: list[dict]) -> float | None:
    """Issue #919 — Anteil der Round-Trips mit ``exit_reason == 'TIME_BOX'`` (hourly_strategy_
    base.ExitReason) am je-Study aufsummierten Exit-Reason-Histogramm. ``None`` ohne jede
    Exit-Telemetrie (leeres Histogramm)."""
    histogram = _sum_exit_reason_histograms(trial_attrs)
    total = sum(histogram.values())
    if not total:
        return None
    return round(histogram.get("TIME_BOX", 0) / total, 4)


def _sum_exit_reason_histograms(trial_attrs: list[dict]) -> dict[str, int]:
    """Issue #919 — summiert ``oos_exit_reason_histogram`` (je Trial, aus Order-Tags via #899)
    über eine Study zu EINEM Histogramm. Leeres Dict, wenn keine Trial-Telemetrie vorliegt
    (Pre-#899-JSON/kein Trade) — Rohmaterial für ``invariants.check_exit_reason_coverage``."""
    total: dict[str, int] = {}
    for a in trial_attrs or []:
        for reason, count in (a.get("oos_exit_reason_histogram") or {}).items():
            total[reason] = total.get(reason, 0) + int(count)
    return total


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
                  symbol_bar_quality_cache: dict | None = None,
                  run_id: str | None = None,
                  ) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743).

    ``symbol_bar_quality_cache`` (Issue #923) — vom Aufrufer EINMAL gelesenes
    ``sweep.read_symbol_bar_quality_cache(WORK)``-Ergebnis, hier nur je Symbol nachgeschlagen
    (kein I/O in dieser Funktion selbst). ``None``/kein Eintrag für ``proposal['symbol']`` ⇒

    ``run_id`` (Issue #1015, Katalog #858, Fix Punkt 1) — durchgereicht an
    ``compute_budget_execution``, damit ``budget_executed_fraction`` ausschliesslich Trials DIESES
    Laufs zählt, statt einer ungepurgten Study mehrerer Läufe (siehe dortiger Docstring). ``None``
    (Default, z. B. Legacy-/Test-Aufrufer) ⇒ bit-identisches Alt-Verhalten (alle Study-Trials).
    derselbe ``_contracts.BAR_SECONDS_DEFAULT``-Fallback wie vor #923."""
    trials = list(getattr(study, "trials", None) or []) if study is not None else []
    trial_attrs = [dict(getattr(t, "user_attrs", {}) or {}) for t in trials]

    n_trials = len(trials)
    # Issue #1079 (Pitfall #377) — ein Trial mit Optuna-state PRUNED kann per Konstruktion keine
    # Selektionsstatistik tragen (#914: geprunte Trials verlassen den Reward-Pfad vollständig,
    # BEVOR eine Auswertung stattfindet). Ihn trotzdem in n_evaluable zu zählen, nur weil sein
    # user_attrs-Dict (von VOR dem Pruning) noch oos_evaluated=True trägt, erzeugt einen
    # garantierten Fehlalarm in jedem Verfügbarkeits-Nenner, der auf n_evaluable aufbaut (Beweis
    # B-13 im #866-Katalog: Squeeze, 74 von 130 "evaluable" Trials waren tatsächlich PRUNED —
    # 130 + n_trials_pruned(78) = 208 > n_trials(180), die Zähler überlappten).
    _pruned_state = getattr(getattr(optuna, "trial", None), "TrialState", None)
    _pruned_state = getattr(_pruned_state, "PRUNED", None) if _pruned_state is not None else None
    n_evaluable = sum(
        1 for t, a in zip(trials, trial_attrs)
        if a.get("oos_evaluated") is True and getattr(t, "state", None) != _pruned_state
    )
    n_eligible = sum(1 for a in trial_attrs if a.get("oos_eligible") is True)
    p_eligible = round(n_eligible / n_trials, 4) if n_trials else 0.0
    # Issue #931 — Median der Per-Trial-Wallclock (#415-Telemetrie), damit ein SPÄTERER Lauf den
    # Wallclock-Preflight (sweep.assert_wallclock_budget_valid) mit einem echten Erfahrungswert
    # statt dem eingefrorenen Fallback (wallclock_guard.DEFAULT_BACKTEST_MS_MEDIAN) füttern kann.
    _backtest_ms_values = [a["backtest_ms"] for a in trial_attrs if a.get("backtest_ms") is not None]
    backtest_ms_median = statistics.median(_backtest_ms_values) if _backtest_ms_values else None
    # Issue #915 — wie viele der oos_evaluated Trials TATSÄCHLICH eine definierte
    # Selektions-Teststatistik (oos_psr) tragen. Rohmaterial für
    # invariants.check_selection_statistic_availability: die WIRKUNGS-Invariante, die prüft, ob
    # der Guard eine benutzbare Schwelle liefert — nicht nur, ob die konfigurierte Referenz
    # verwendet wurde (siehe check_guard_reference_coherence, eine reine Quellen-Invariante).
    n_selection_statistic_available = sum(
        1 for a in trial_attrs if a.get("oos_evaluated") is True and a.get("oos_psr") is not None)
    # Issue #917 Fix 4 — 'ineligible' in zwei disjunkte Klassen zerlegen: nur EINE davon ist eine
    # Aussage über die Strategie. ineligible_unmeasurable zählt REJECT_OOS_STATISTIC_UNAVAILABLE
    # (#917) — ein Gate lief auf einer undefinierten Grösse, keine Messung fand statt.
    n_ineligible_unmeasurable = sum(
        1 for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_eligible") is not True
        and a.get("is_rejection_detail") == "REJECT_OOS_STATISTIC_UNAVAILABLE")
    # ineligible_measured — evaluiert, nicht eligible, aber NICHT wegen einer undefinierten Grösse
    # (ein echtes, wenn auch negatives, Messergebnis).
    n_ineligible_measured = max(0, n_evaluable - n_eligible - n_ineligible_unmeasurable)
    # Issue #862 — Median der informativen Periodenzahl über die oos_evaluated Trials dieser
    # Study (Rohmaterial für invariants.check_guard_reference_coherence auf Report-Ebene).
    _n_periods_values = [
        a["oos_n_periods"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_n_periods")
    ]
    oos_n_periods_median = statistics.median(_n_periods_values) if _n_periods_values else None
    coherence_violations = sum(1 for a in trial_attrs if a.get("oos_coherence_violation") is True)
    # Issue #976 — je Study, wie oft jeder is_rejection_detail-Code über ALLE Trials auftrat.
    # Rohmaterial für invariants.check_window_unreachable_rate.
    is_rejection_detail_counts: dict[str, int] = {}
    for a in trial_attrs:
        detail = a.get("is_rejection_detail")
        if detail:
            is_rejection_detail_counts[detail] = is_rejection_detail_counts.get(detail, 0) + 1
    # Issue #804 — Aggregat je Study: wie oft jeder strukturierte Inferenzpfad-Diagnose-Code
    # (EQUITY_NONPOSITIVE/PERIOD_RETURNS_NOT_FINITE/RETURN_SERIES_IDENTITY_*/
    # NON_CONTIGUOUS_FOLD_SEGMENTS/SORTINO_GUARD_TRIPPED/COHERENCE_INVARIANT_VIOLATION) über ALLE
    # Trials dieser Study auftrat — macht sichtbar, OHNE ein Trial-Verzeichnis zu öffnen, ob/wie oft
    # der Subprozess eine Invariante verletzt hat (siehe run_optimization._reemit_inference_
    # diagnostics für die Live-Emission je Trial).
    inference_diagnostics_by_code: dict[str, int] = {}
    # Issue #1033 (Katalog #866) — der obige Zaehler ist ein Zaehler von EREIGNISSEN (mehrere je
    # Trial moeglich, z. B. je Fold); ``check_inference_diagnostics_concentration``/``check_
    # adaptive_diagnostic_rate`` teilen ihn NICHT durch Ereignisse, sondern zaehlen distinkte
    # TRIALS je Code (ein Trial zaehlt hoechstens einmal) — sonst waere die resultierende "Rate"
    # gegen ihre eigene Schwelle nicht kalibrierbar (eine Rate, die 1,0 ueberschreiten kann, ist
    # keine Rate, Pitfall #356). Diese Aggregation macht die TRIAL-Variante zusaetzlich als
    # eigenstaendiges Report-Feld sichtbar (Intensitaets-Telemetrie bleibt die Ereignis-Variante
    # oben).
    inference_diagnostics_trials_by_code: dict[str, int] = {}
    # Issue #901 — je Study die beobachteten guard_reference_source-Werte aus SORTINO_GUARD_TRIPPED/
    # SORTINO_GUARD_REFERENCE_UNAVAILABLE-Diagnosen, Eingangsgrösse für
    # invariants.check_guard_reference_coherence unter reference_mode=='family_median'.
    guard_reference_sources: list[str] = []
    # Issue #968 (Katalog A, P0 HEADLINE) — dieselben Diagnosen tragen zusätzlich den NUMERISCHEN
    # Referenzwert (``guard_reference_value``); Eingangsgrösse für
    # invariants.check_guard_reference_stability — eine Study, deren Guard-Referenz WANDERT
    # (mehrere verschiedene Werte/Quellen innerhalb eines Laufs), ist trotz festem Seed nicht mehr
    # bitweise reproduzierbar (Pitfall #307 in AGENTS.md).
    guard_reference_values: list[float] = []
    for a in trial_attrs:
        _codes_this_trial: set[str] = set()
        for diag in a.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code:
                inference_diagnostics_by_code[code] = inference_diagnostics_by_code.get(code, 0) + 1
                _codes_this_trial.add(code)
            if isinstance(diag, dict) and diag.get("guard_reference_source") is not None:
                guard_reference_sources.append(diag["guard_reference_source"])
            if isinstance(diag, dict) and diag.get("guard_reference_value") is not None:
                guard_reference_values.append(diag["guard_reference_value"])
        for code in _codes_this_trial:
            inference_diagnostics_trials_by_code[code] = (
                inference_diagnostics_trials_by_code.get(code, 0) + 1)

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
    # Issue #902 — bar_seconds ist Pflichtparameter. Issue #923 — der #900-Gate-1-Preflight
    # persistiert median_delta_t_s je Symbol NUN in symbol_bar_quality.json
    # (sweep.write_symbol_bar_quality_cache); dieser Read ersetzt den vorher unbedingten
    # _contracts.BAR_SECONDS_DEFAULT-Fallback, sobald ein Cache-Eintrag für DIESES Symbol
    # existiert. Kein Cache-Eintrag (Pre-#923-Lauf, injizierter Test ohne echten Katalog) ⇒
    # derselbe fail-loud protokollierte Fallback wie zuvor.
    _symbol_bar_quality = symbol_bar_quality_cache.get(proposal.get("symbol")) if isinstance(
        symbol_bar_quality_cache, dict) else None
    _bar_seconds = (
        _symbol_bar_quality.get("median_delta_t_s")
        if isinstance(_symbol_bar_quality, dict) and _symbol_bar_quality.get("median_delta_t_s")
        else _contracts.BAR_SECONDS_DEFAULT
    )
    timebox = _inv.compute_trial_timebox_violations(
        trial_attrs, strategy=proposal.get("strategy"),
        bar_seconds=_bar_seconds)

    # Issue #929 — best_reward aus ALLEN abgeschlossenen Trials (Optuna-Semantik), nicht aus
    # Optunas eigenem constraint-gefilterten study.best_value (siehe
    # run_optimization._best_completed_value-Docstring: unter oos_eligible=False für JEDEN Trial
    # liefert study.best_value null/wirft, obwohl Optuna intern einen besten rohen Reward kennt).
    try:
        _study_direction = study.direction.name.lower() if study is not None else "maximize"
    except Exception:
        _study_direction = "maximize"
    best_reward = _best_completed_value(trials, direction=_study_direction) if study is not None else None

    feasible_rewards = [
        float(t.value) for t in trials
        if getattr(t, "user_attrs", {}).get("oos_eligible") is True
        and isinstance(getattr(t, "value", None), (int, float))
    ]
    # Issue #929 — getrenntes Feld: der beste Reward NUR über die eligible Kohorte.
    best_eligible_reward = (
        (max(feasible_rewards) if _study_direction == "maximize" else min(feasible_rewards))
        if feasible_rewards else None
    )
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
    binding_deltas, soft_deltas, binding_gate, binding_deltas_normalized = _split_near_miss_deltas(
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
        n_startup_trials=n_startup_for_report, study_user_attrs=study_user_attrs, run_id=run_id)

    # Issue #1030 (Katalog #866) — Root-Cause: fuer Proposals ohne symbolspezifische Holdout-Route
    # (z. B. ``HOLDOUT_NO_ELIGIBLE_TRIALS``, bei der ein globaler Default-Vektor VERSUCHT, aber
    # abgelehnt wurde, siehe ``confirm.confirm_per_symbol_promotion``s ``metrics_symbol={}``/
    # ``metrics_global=_metrics_dict(m_global)``-Rueckgaben) ist ``holdout['symbol']`` ein leeres
    # Dict — JEDES ``holdout_*``-Feld unten (inkl. ``holdout_profit_factor_raw``) brach dadurch
    # still auf ``None`` ab, obwohl ein echter (wenn auch abgelehnter) globaler Holdout-Backtest
    # gelaufen war und in ``holdout['global']`` liegt. Fallback nur, wenn die Symbol-Route
    # WIRKLICH leer ist (kein stiller Vorrang vor einer echten Symbol-Route).
    _holdout_symbol = (proposal.get("holdout") or {}).get("symbol") or {}
    if _holdout_symbol:
        holdout_metrics = _holdout_symbol
        holdout_route = "symbol"
    else:
        holdout_metrics = (proposal.get("holdout") or {}).get("global") or {}
        holdout_route = "global" if holdout_metrics else "none"
    decision_chain = _decision_chain(proposal, n_eligible=n_eligible)
    # Issue #1006 (Katalog #858, Fix Punkt 2) — "Deploybar" (summary_de.py Abschnitt 2.1) behauptete
    # bislang eine Eigenschaft, die deployment_gate.evaluate_deployment_eligibility NIE geprüft
    # hatte (#993 fügte acht weitere, teils STRENGERE Klauseln hinzu, u. a. dsr UNBEDINGT — genau
    # die Klausel, die promotion_correction_mode='dsr_or_robust_pair' im Sweep ersetzbar macht).
    # Jeder Promotionskandidat ruft dieselbe Funktion auf, die auch Phase 5 aufruft (kein Nachbau) —
    # nur für tatsächliche Kandidaten (Perf/IO: catalog_fingerprint() ist ein echter Datei-Hash,
    # nicht für jede Study nötig).
    deployment_decision = None
    if proposal.get("status") in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT"):
        try:
            from automation.optimizer import deployment_gate as _deploy_gate
            _pair = (proposal.get("strategy"), proposal.get("symbol"))
            _deploy_record = _deploy_gate.build_promotion_record_from_proposal(
                proposal, run_id=proposal.get("run_id"))
            deployment_decision = _deploy_gate.evaluate_deployment_eligibility(
                _pair, {_pair: _deploy_record}, tournament_cfg or {}).to_dict()
        except Exception:
            logging.getLogger("optimizer").warning(
                "[#1006] Deployment-Bewertung für %s/%s fehlgeschlagen (non-fatal, Bericht zeigt "
                "deployment_decision=None).", proposal.get("strategy"), proposal.get("symbol"),
                exc_info=True,
            )
            deployment_decision = None
    checks = [
        # Issue #1006 — FAILt (severity 'high'), wenn ein Kandidat READY_FOR_PR/PROMOTE_GLOBAL_
        # DEFAULT ist, aber deployment_gate ihn ablehnt — sichtbar statt implizit.
        _inv.check_promotion_deployment_coherence(proposal, deployment_decision),
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
        # Issue #1004 (Katalog #858, Fix Punkt 4) — keine Promotion darf auf einer zensierten/
        # gecappten Kennzahl beruhen (z. B. profit_factor_censored durch profit_factor_cap oder
        # einen degenerierten Bruttoverlust-Nenner).
        _inv.check_censored_statistic_in_decision(proposal, holdout_metrics),
        # Issue #813 — deflation_cluster_coverage < 0.9 ist ein Invarianten-FAIL: die familienweite
        # Decluster-Matrix sieht dann nur einen Bruchteil der gezaehlten (oos_evaluated) Kandidaten.
        _inv.check_deflation_cluster_coverage(holdout_metrics),
        _inv.check_rejection_chain_completeness(proposal, decision_chain=decision_chain),
        _inv.check_reward_term_variance(trial_attrs),
        # Issue #965 Fix Punkt 4 (Katalog A, P0 HEADLINE) — Verteilungs-Test: eine fehlende
        # Selektionsstatistik darf nicht bevorzugt die profitable Kohorte treffen (Pitfall #306).
        _inv.check_selection_statistic_economic_bias(trial_attrs),
        # Issue #949 (Katalog C, P0 HEADLINE) — der Wächter gegen den Zweig-Indikator-Defekt: die
        # Reward-Varianz einer Study darf nicht vom Failure-/Prune-Zweig getragen werden, sondern
        # muss die Qualitätsordnung innerhalb der zulässigen Region widerspiegeln.
        _inv.check_reward_dynamic_range(trial_attrs),
        # Issue #756 — nach der Log-Return-Umstellung ist eine verbleibende Kohärenzverletzung ein
        # echter Bug, kein erwartetes Restrauschen mehr; harter Regressionswächter statt WARNING.
        _inv.check_log_return_coherence(trial_attrs),
        # Issue #978 (Katalog C, P0) — der Annualisierungsfaktor muss innerhalb eines Trials über
        # alle Folds kommensurabel bleiben (Pitfall #310).
        _inv.check_annualization_commensurability(trial_attrs),
        # Issue #979 (Katalog C, P0) — der ordnende Reward-Zweig darf nicht auf einen winzigen
        # Bruchteil der Auswertungen kollabieren (Pitfall #124, doppelt kodierte Feasibility).
        _inv.check_objective_branch_coverage(trial_attrs),
        # Issue #759 — Missing-Data-Sentinel-Kollaps-Regressionswächter (oos_win_rate).
        _inv.check_metric_sentinel_absence(trial_attrs),
        # Issue #804/#886 — sechster Regressionswächter: strukturierte Inferenzpfad-Diagnosen aus
        # dem Backtest-Subprozess sind jetzt maschinell im #742-Report überprüfbar, nicht nur live
        # geloggt (seit #886 ohne die #863/#864-regulären dritten Ausgänge, siehe unten).
        _inv.check_inference_diagnostics_absent(trial_attrs),
        # Issue #886 — ersetzt die reine Anwesenheit der #863/#864-regulären Ausgänge durch eine
        # Konzentrationsprüfung (analog STUDY_GUARD_DOMINATED, #823). Issue #1078 — der Nenner ist
        # n_trials (die volle, mit dem Zähler kommensurable Trial-Zahl dieser Study), NICHT mehr
        # n_trials_informative (eine zum Zähler DISJUNKTE Teilmenge, siehe Docstring dort).
        _inv.check_inference_diagnostics_concentration(
            trial_attrs, n_trials_informative=study_user_attrs.get("n_trials_informative"),
            n_trials=n_trials,
            **({"guard_dominance_threshold": guard_dominance_threshold}
               if guard_dominance_threshold is not None else {})),
        # Issue #967 Fix Punkt 2 — eigene Rate-Invariante für ADAPTIVE-Diagnosen (SORTINO_DOWNSIDE_
        # SHRUNK), getrennt von der CENSORING-Konzentrationsprüfung oben.
        _inv.check_adaptive_diagnostic_rate(
            trial_attrs, n_trials_informative=study_user_attrs.get("n_trials_informative")),
        # Issue #885 Fix Punkt 3 — die fünf Trial-Kategorien (informativ/geprunt/unauswertbar/
        # fehlgeschlagen/total) müssen die Trial-Menge disjunkt und vollständig zerlegen.
        # Issue #1079 — n_evaluable (dieser Funktion lokaler, trial_attrs-basierter Zähler)
        # zusätzlich zu study_user_attrs übergeben, damit die zweite Identität geprüft werden kann.
        _inv.check_denominator_coherence({**study_user_attrs, "n_evaluable": n_evaluable}),
    ]

    # Issue #949 (Katalog C) Fix 2 — reward_std_total (alle oos_evaluated Trials) vs.
    # reward_std_feasible (ohne spaeter geprunte Trials) als eigene Report-Telemetrie, damit die
    # Straf-Term-Kalibrierung (#951) explizit GEGEN reward_std_feasible kalibriert werden kann,
    # statt gegen ein vom Failure-Zweig verzerrtes Gesamtmass.
    _reward_std_total, _reward_std_feasible, _ = _inv._reward_std_total_and_feasible(trial_attrs)
    _study_exit_reason_histogram = _sum_exit_reason_histograms(trial_attrs)

    # Issue #1067 — meldet je Study, ob der GEWINNER (bestbewerteter Trial) ausserhalb des
    # kuratierten Default-Suchbands liegt — unabhängig davon, ob ein Auto-/kuratierter Override
    # (#761) das erlaubt hat. Macht sichtbar, wenn ein automatischer Rückschrieb bereits produktiv
    # war, bevor er im nächsten Lauf weiter eskaliert (Beweis B-5 im #866-Katalog: der
    # TrendPullback-Gewinner trug ``ema_period=18`` gegen die Default-Untergrenze 50).
    winner_outside_default_bounds: dict[str, list] = {}
    if scored and proposal.get("strategy"):
        try:
            from automation.optimizer.bounds import extract_numeric_bounds
            _default_bounds = extract_numeric_bounds(proposal["strategy"])
            _winner_params = dict(getattr(best_trial, "params", {}) or {})
            for _param, _value in _winner_params.items():
                _bound = _default_bounds.get(_param)
                if _bound is None or not isinstance(_value, (int, float)):
                    continue
                _lo, _hi = _bound
                if _value < _lo or _value > _hi:
                    winner_outside_default_bounds[_param] = [_value, [_lo, _hi]]
        except Exception:
            winner_outside_default_bounds = {}

    record = {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        # Issue #1067 — leer, wenn der Gewinner innerhalb des Default-Suchbands liegt (der weit
        # überwiegende Regelfall, bit-identisch zum Pre-#1067-Bericht).
        "winner_outside_default_bounds": winner_outside_default_bounds or None,
        "n_trials": n_trials,
        "n_evaluable": n_evaluable,
        "n_selection_statistic_available": n_selection_statistic_available,
        # Issue #917 Fix 4 — disjunkte Zerlegung der evaluierten, nicht-eligiblen Trials.
        "n_ineligible_measured": n_ineligible_measured,
        "backtest_ms_median": backtest_ms_median,
        # Issue #1038 (Katalog #866) — Σ tatsaechlicher Backtest-CPU-Zeit dieser Study (Rohmaterial
        # fuer report._worker_utilisation_backtest_ms): anders als study_wallclock_s (Wanduhrzeit,
        # die verschachtelte Study-eigene Worker-Pools UND — vor #1023 — fremde Studies einschloss)
        # ist dies additiv ueber echte Trial-Arbeit, unbeeinflusst von Ueberlappung.
        "backtest_ms_sum": sum(_backtest_ms_values) if _backtest_ms_values else None,
        # Issue #983 — Rohmaterial für sweep._read_last_backtest_ms_mean des NÄCHSTEN Laufs: der
        # Wallclock-Preflight braucht den Mittelwert (rechtsschiefe Verteilung), nicht den Median.
        "backtest_ms_mean": study_user_attrs.get("backtest_ms_mean"),
        # Issue #932 — Per-Study-Wallclock (aus dem Study-User-Attr, #929/#568-Muster), Rohmaterial
        # für den LPT-Dispatch (sweep._read_last_study_wallclock_by_strategy) des NÄCHSTEN Laufs.
        "wallclock_s": study_user_attrs.get("wallclock_s"),
        "n_ineligible_unmeasurable": n_ineligible_unmeasurable,
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
        # Issue #929 — getrenntes Feld: der beste Reward NUR über die eligible Kohorte (None, wenn
        # p_eligible == 0 — die Leermenge ist hier inhaltlich korrekt, kein Constraint-Artefakt).
        "best_eligible_reward": best_eligible_reward,
        # Issue #949 (Katalog C) Fix 2 — siehe check_reward_dynamic_range/_reward_std_total_and_
        # feasible in invariants.py.
        "reward_std_total": round(_reward_std_total, 6) if _reward_std_total is not None else None,
        "reward_std_feasible": (
            round(_reward_std_feasible, 6) if _reward_std_feasible is not None else None),
        "gradient_signal": gradient_signal,
        # Issue #808 — welcher der drei Arme (discovery/reward_variance/constraint_progress/none)
        # das obige gradient_signal traegt. None ⇒ wie gradient_signal selbst unbeantwortet
        # (Early-Stop).
        "gradient_signal_arm": gradient_signal_arm_value,
        "constraint_improvement_rate": constraint_improvement_rate,
        # Issue #981 — die rohen je-Trial-Konstraint-Distanzen der modellierten Kohorte, damit
        # invariants.check_search_made_progress die AUFLÖSUNG seiner eigenen Eingabe prüfen kann
        # (eine dreiwertige Treppenfunktion kann keinen Gradienten anzeigen, siehe #966).
        "constraint_violations_observed": [
            cv[0] for t in modelled
            for cv in [(getattr(t, "user_attrs", {}) or {}).get("oos_constraint_violations")]
            if cv
        ],
        # Issue #929 Fix 3 — Eingangsgrössen für invariants.check_search_made_progress.
        "n_modelled_trials": len(modelled),
        "plateau_min_modelled_trials": study_user_attrs.get("plateau_min_modelled_trials"),
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
        # Issue #1091 (Katalog #924) — die vor dem ersten Trial dieses Symbols eingefrorene
        # familienweite Multiplizitaet (siehe sweep._family_n_frozen_from_studies); identisch
        # ueber jede Study derselben Symbol-Familie und ueber jeden Lesezeitpunkt.
        "deflation_n_family_frozen": study_user_attrs.get("deflation_n_family_frozen"),
        # Issue #770 — Budget-Ausfuehrungsgrad als erstklassige Study-Kennzahl.
        "n_trials_budgeted": budget_execution["n_trials_budgeted"],
        "n_trials_completed": budget_execution["n_trials_completed"],
        # Issue #1015 (Katalog #858, Fix Punkt 1) — die volle Study-SQLite-Historie (alle Läufe)
        # separat von ``n_trials_completed`` (nur dieser Lauf, sofern run_id verfügbar war); eine
        # grosse Lücke macht eine ungepurgte Study im Report sichtbar.
        "n_trials_total_study": budget_execution["n_trials_total_study"],
        "budget_executed_fraction": budget_execution["budget_executed_fraction"],
        # Issue #983 Fix Punkt 3 Akzeptanzkriterium — siehe run_optimization._emit_study_summary.
        "budget_degradation_factor": study_user_attrs.get("budget_degradation_factor", 1.0),
        "stop_reason": budget_execution["stop_reason"],
        "n_modelled_trials_completed": budget_execution["n_modelled_trials_completed"],
        "coherence_violations": coherence_violations,
        # Issue #804 — je Study, wie oft jeder Inferenzpfad-Diagnose-Code auftrat (leeres Dict im
        # Normalfall). Macht eine Subprozess-Invariantenverletzung im #742-Report sichtbar, ohne ein
        # Trial-Verzeichnis zu öffnen oder trial_dir/logs/ zu lesen.
        "inference_diagnostics_by_code": inference_diagnostics_by_code,
        # Issue #1033 (Katalog #866) — distinkte-Trials-Variante desselben Codes (siehe oben);
        # dieselbe Quelle, die check_inference_diagnostics_concentration/check_adaptive_diagnostic_
        # rate als Zaehler verwenden.
        "inference_diagnostics_trials_by_code": inference_diagnostics_trials_by_code,
        # Issue #976 — Rohmaterial für invariants.check_window_unreachable_rate.
        "is_rejection_detail_counts": is_rejection_detail_counts,
        # Issue #901 — Rohmaterial für invariants.check_guard_reference_coherence.
        "guard_reference_sources": guard_reference_sources,
        # Issue #968 — Rohmaterial für invariants.check_guard_reference_stability.
        "guard_reference_values": guard_reference_values,
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
        # Issue #971 (Katalog B, P0 HEADLINE) — EXPLIZITER, NEUER Feldname für die TRADE-(Round-
        # Trip-)Ebene, den ``check_holding_time_cap`` ab sofort konsumiert (siehe dortige
        # Docstring). Der bisher von ``check_holding_time_cap`` gelesene Name
        # (``timebox_violation_fraction``) bleibt unverändert die TRIAL-Ebene — er wird NICHT
        # umgedeutet, sondern hier durch einen neuen, korrekt benannten Alias ERSETZT: ein Trial mit
        # 200 Trades und einem einzigen Ausreisser-Trade ist studienweit kein "kaputter Exit-Pfad",
        # auch wenn er als TRIAL zu 100% "verletzend" zählt (Pitfall #303/#304 in AGENTS.md). Werte
        # sind bit-identisch zu den bestehenden Round-Trip-Feldern oben — reiner Namens-Alias, keine
        # zweite Berechnung (Pitfall #269, Single-Source-of-Truth).
        "timebox_violating_trades_frac": timebox["timebox_round_trip_violation_fraction"],
        "timebox_violating_trades_numerator": timebox["timebox_violating_round_trips"],
        "timebox_violating_trades_denominator": timebox["timebox_evaluated_round_trips"],
        "timebox_violation_intensity_p95": timebox["timebox_violation_intensity_p95"],
        "timebox_violated": timebox["timebox_violated"],
        # Issue #972 — Rohmaterial für invariants.check_counter_partition_consistency (nur gesetzt,
        # wenn diese Study ein Zero-Eligible-Plateau meldete, siehe run_optimization
        # ._optimize_symbol_impl).
        "plateau_n_evaluated": study_user_attrs.get("plateau_n_evaluated"),
        "plateau_counter_breakdown": study_user_attrs.get("plateau_counter_breakdown"),
        # Issue #861 — Verteilung der Deckel-Referenzquelle (sampled/default/global) über die
        # ausgewerteten Trials dieser Study.
        "timebox_cap_source_counts": timebox["timebox_cap_source_counts"],
        # Issue #897 Fix 3 — Rohmaterial für ``invariants.check_effective_stop_distance``: Median
        # des realisierten Ø-Bruttoverlusts (bps) und der ATR-Telemetrie über die Trials dieser
        # Study (#899). None, wenn keine Exit-Telemetrie vorliegt (Pre-#899-JSON/kein Trade).
        "oos_gross_loss_mean_bps": _median_of_trial_field(trial_attrs, "oos_gross_loss_mean_bps"),
        # Issue #1035 (Katalog #866) — dieselbe Groesse, aber NUR ueber nachweisliche TRAILING_
        # STOP-Exits, plus die zugrunde liegende Stichprobengroesse (Summe ueber alle Trials) —
        # Rohmaterial fuer invariants.check_effective_stop_distance (INCONCLUSIVE bei < 30 Stop-
        # Exits statt eines FAILs auf der falschen Grundgesamtheit, #1008/#1035).
        "oos_gross_loss_mean_bps_trailing_stop": _median_of_trial_field(
            trial_attrs, "oos_gross_loss_mean_bps_trailing_stop"),
        "oos_n_trailing_stop_losses": sum(
            int(a.get("oos_n_trailing_stop_losses") or 0) for a in trial_attrs),
        # Issue #1085 (Katalog #866-2) — über alle Trials aufsummierte Dust-Round-Trips
        # (Notional < 5% des Median-Notionals dieser Study, Fliesskomma-Residuen eines Netto-
        # Exposure-Nulldurchgangs) — Rohmaterial für invariants.check_dust_round_trip_share.
        "dust_round_trips_filtered": sum(
            int(a.get("oos_expectancy_notional_degenerate_count") or 0) for a in trial_attrs),
        "atr_median_bps": _median_of_trial_field(trial_attrs, "oos_atr_median_bps"),
        # Issue #923 Fix 1 — die #900-Preflight-Kennzahlen (frac_zero_true_range, atr_median_bps,
        # bar_coverage_ratio, median_delta_t_s) des SYMBOLS (nicht dieser Study — identisch für
        # jede Strategie auf demselben Symbol), aus dem Gate-1-Cache. None ⇒ kein Preflight in
        # diesem Lauf (z. B. injizierte Tests).
        "symbol_bar_quality": _symbol_bar_quality,
        # Issue #919 — je Study aufsummiertes Exit-Reason-Histogramm (aus Order-Tags, #899) +
        # Median der je-Trial-Median-Haltedauer (Bars). Rohmaterial für
        # invariants.check_exit_reason_coverage.
        "exit_reason_histogram": _study_exit_reason_histogram,
        # Issue #1037 (Katalog #866) — bequemer direkter Zugriff auf denselben Wert wie
        # exit_reason_histogram['DATA_END']: Round-Trips, die backtest_runner._finalize_round_trip
        # am Ende der verfuegbaren Daten zwangsweise finalisiert hat (Position nie flat geworden),
        # nicht ueber eine echte Handelsentscheidung. Rohmaterial fuer
        # invariants.check_open_position_at_data_end.
        "n_round_trips_data_end": _study_exit_reason_histogram.get("DATA_END", 0),
        "median_bars_held": _median_of_trial_field(trial_attrs, "oos_median_bars_held"),
        # Issue #919 — Summe der Round-Trips über GENAU die Trials, die auch ein
        # exit_reason_histogram beitrugen (Apples-to-apples für check_exit_reason_coverage; ein
        # Trial ohne Order-Tag-Telemetrie darf die Summe nicht verzerren).
        "oos_total_trades_with_exit_telemetry": sum(
            int(a.get("oos_total_trades") or 0) for a in trial_attrs
            if a.get("oos_exit_reason_histogram")
        ),
        # Issue #919 — Anteil der Round-Trips, die über die 24-Bar-Zeitbox statt über den
        # Trailing-Stop/Profit-Target/Signal-Reversal schliessen (Eingangsgrösse für die
        # #925-Budgetdiskussion und GR-01, siehe hourly_strategy_base.ExitReason).
        "time_box_exit_fraction": _time_box_exit_fraction(trial_attrs),
        # Issue #897 Fix 3 — Median des je-Trial GESAMPELTEN atr_trailing_multiplier (das
        # Konfigurations-Gegenstueck zur realisierten ATR-Telemetrie oben).
        "atr_trailing_multiplier_median": _median_of_sampled_param(
            trial_attrs, "atr_trailing_multiplier"),
        # Issue #862 — Rohmaterial für den globalen check_guard_reference_coherence-Wächter.
        "oos_n_periods_median": oos_n_periods_median,
        "promotion_outcome": proposal.get("status"),
        # Issue #1006 (Katalog #858) — dieselbe Deployment-Bewertung, die Phase 5 aufruft (kein
        # Nachbau); ``None`` für Nicht-Kandidaten. summary_de.py Abschnitt 2.1 liest dies statt
        # implizit "Deploybar" zu behaupten.
        "deployment_decision": deployment_decision,
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
        # Issue #1074 — "binding" bleibt die ROHE (native Einheiten) Ansicht; "binding_normalized"
        # ist die dimensionslose Ansicht, aus der binding_gate abgeleitet wird (argmin), damit der
        # Wechsel gegenüber der alten, einheitenbehafteten Attribution nachvollziehbar bleibt.
        "near_miss_deltas": {
            "binding": binding_deltas, "soft": soft_deltas,
            "binding_normalized": binding_deltas_normalized,
        },
        "binding_gate": binding_gate,
        # Issue #776 — noch unkonsolidierte (LIVE als redundant ausgewiesene) Mitglieder von
        # ``eligible_requires_all`` dieser Study; leer ⇒ Config konsistent mit dem #679-Alarm.
        "gate_collinearity_unconsolidated": gate_collinearity_unconsolidated,
        # Issue #970 (Katalog A, P1) — je Gate n_rejections/n_solo_rejections/marginal_delta über
        # die evaluierte Kohorte dieser Study (siehe invariants.gate_inventory_table-Docstring).
        "gate_inventory": _inv.gate_inventory_table(
            trial_attrs, (tournament_cfg or {}).get("eligible_requires_all") or []),
        # Issue #786 — das bindende HOLDOUT-Gate (negativstes normiertes Delta auf dem Holdout-
        # Fenster, NICHT den OOS-Folds — siehe confirm._holdout_binding_gate) + die zugrunde
        # liegenden Deltas, direkt aus dem Proposal uebernommen (von confirm.py gestempelt).
        "holdout_gate_deltas": holdout_metrics.get("holdout_gate_deltas") or {},
        "holdout_binding_gate": holdout_metrics.get("holdout_binding_gate"),
        # Issue #1075 — nur gesetzt, wenn die Holdout-Stufe BESTANDEN hat (holdout_binding_gate ist
        # dann None, siehe confirm._holdout_tightest_margin): welches aktive Gate am nächsten an
        # seiner Schwelle lag, ohne dass irgendetwas gescheitert ist.
        "holdout_tightest_margin": holdout_metrics.get("holdout_tightest_margin"),
        # Issue #1030 (Katalog #866) — welche Route (siehe oben) die holdout_*-Felder speiste; macht
        # eine "global"-Herkunft (Symbol-Route leer) im Report unterscheidbar von einer echten
        # Symbol-Route, statt beide identisch als "die Holdout-Zahlen" zu behandeln.
        "holdout_route": holdout_route,
        # Issue #832 Fix Punkt 2/3 — monetäre Holdout-Kennzahlen (confirm._metrics_dict), für
        # summary_de.py Abschnitt 2 ("Monetäres Ergebnis") ohne zweiten Datenzugriff.
        "holdout_total_return": holdout_metrics.get("oos_total_return"),
        "holdout_expectancy": holdout_metrics.get("oos_expectancy"),
        # Issue #1031 (Katalog #866) — additive, nennerausreisser-robuste Expectancy-Telemetrie
        # (siehe backtest_runner._calculate_stats-Docstring); holdout_expectancy bleibt unveraendert.
        "holdout_expectancy_capital_weighted": holdout_metrics.get("oos_expectancy_capital_weighted"),
        "holdout_expectancy_winsorized": holdout_metrics.get("oos_expectancy_winsorized"),
        "holdout_expectancy_outlier_count": holdout_metrics.get("oos_expectancy_outlier_count") or 0,
        "holdout_expectancy_notional_degenerate_count": (
            holdout_metrics.get("oos_expectancy_notional_degenerate_count") or 0),
        # Issue #1042 (Katalog #866) E-1/E-3 — Kosten-Stressband + CVaR/ES-Tail-Risiko, additiv
        # neben den unveraenderten Basis-Kennzahlen (siehe backtest_runner-Docstrings).
        "holdout_expectancy_cost_stress_1_5x": holdout_metrics.get("oos_expectancy_cost_stress_1_5x"),
        "holdout_expectancy_cost_stress_2x": holdout_metrics.get("oos_expectancy_cost_stress_2x"),
        "holdout_cvar_95": holdout_metrics.get("oos_cvar_95"),
        "holdout_es_99": holdout_metrics.get("oos_es_99"),
        "holdout_win_rate": holdout_metrics.get("oos_win_rate"),
        "holdout_profit_factor": holdout_metrics.get("oos_profit_factor"),
        # Issue #1004 (Katalog #858) — Zensur-Telemetrie fuer summary_de.py Abschnitt 2.1 (kein
        # zweiter Datenzugriff, dieselbe holdout_metrics-Quelle wie holdout_profit_factor selbst).
        "holdout_profit_factor_censored": holdout_metrics.get("oos_profit_factor_censored") or False,
        "holdout_profit_factor_raw": holdout_metrics.get("oos_profit_factor_raw"),
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
    # Issue #1069 (Katalog #866-2, grösster Risiko-Hebel) — realized_stop_loss_ratio als
    # erstklassiges Report-Feld je Study: derselbe Quotient, den ``invariants.check_effective_
    # stop_distance`` intern bildet (Median des realisierten Ø-Bruttoverlusts bei nachweislichen
    # TRAILING_STOP-Exits / konfigurierter Stop-Abstand k_median·ATR_median), hier aber SICHTBAR
    # als eigener Wert je Study statt nur innerhalb des Invarianten-Checks verborgen. Beweis B-3 im
    # #866-Katalog: das Verhältnis variiert zwischen 1,18 und 36,66 über 13 Studies desselben Laufs
    # — der Trailing-Stop ist keine kalibrierte Risikogrösse, sondern rastet am ATR-Floor auf der
    # vollen adversen Bar-Bewegung. ``None``, wenn eine der drei Eingangsgrössen fehlt oder die
    # konfigurierte Distanz <= 0 ist (kein Urteil auf einer undefinierten Zahl).
    _rt_loss = record.get("oos_gross_loss_mean_bps_trailing_stop")
    _rt_atr = record.get("atr_median_bps")
    _rt_k = record.get("atr_trailing_multiplier_median")
    if _rt_loss is not None and _rt_atr and _rt_k is not None:
        _rt_configured_distance = float(_rt_k) * float(_rt_atr)
        record["realized_stop_loss_ratio"] = (
            round(float(_rt_loss) / _rt_configured_distance, 4)
            if _rt_configured_distance > 0 else None)
    else:
        record["realized_stop_loss_ratio"] = None
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


def _search_budget_proposal_section(
    all_checks: list[tuple[str, "_inv.InvariantResult"]],
) -> list[dict[str, Any]]:
    """Issue #1082 Fix Punkt (a) (Katalog #866-2, Kohorte E) — Studies, deren
    ``check_objective_branch_coverage`` FAILt (der ordnende Reward-Zweig ``branch=='per_symbol'``
    traegt unter der Schwelle des Suchbudgets, Referenzlauf: AdxAtr 4/140, Squeeze 5/180,
    TrendPullback 8/140, DynamicBreakout 9/100, Rsi2 15/160), als eigene Report-Sektion — das
    Rohmaterial fuer den Suchbudget-Vorschlag des NAECHSTEN Laufs. ``sweep._apply_search_budget_
    proposal`` liest diese Sektion aus dem JUENGSTEN #742-Report (analog ``_read_last_study_
    wallclock_by_strategy``) und schreibt jedes Paar ueber den bestehenden #830-``'deprioritized'``-
    Pfad in den Diagnose-Cache — eine Study unter der Schwelle bekommt im naechsten Lauf NICHT
    dasselbe Budget noch einmal (``run_optimization._apply_deprioritized_budget``), statt weiterhin
    ungebremst Trials fuer einen ueberwiegend zweigklippen-gefuehrten Suchraum zu verbrennen."""
    out = []
    for label, result in all_checks:
        if result.name != "check_objective_branch_coverage" or result.passed:
            continue
        if "/" not in label:
            continue
        strategy, symbol = label.split("/", 1)
        out.append({
            "strategy": strategy, "symbol": symbol,
            "objective_branch_coverage_fraction": result.actual,
        })
    return out


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


def _coverage_ledger_continuity_check(run_id: str | None = None, *,
                                      coverage_bootstrap_phase: bool = False) -> _inv.InvariantResult:
    """Issue #892 Fix Punkt 2 — ermittelt ``has_prior_reports`` aus ``REPORTS_DIR`` (mindestens ein
    ANDERER ``run_*.json`` existiert bereits) und ruft ``invariants.check_coverage_ledger_
    continuity`` gegen das aktuelle Ledger. Fail-open (kein FAIL) bei jedem Lese-/
    Enumerationsfehler — ein Report darf wegen dieser Zusatzprüfung nie crashen.

    Issue #1064 (Pitfall #373) — ``run_id`` schliesst den EIGENEN Report DIESES Laufs von der
    ``has_prior_reports``-Zählung aus. Root-Cause #1064: ``_build_report`` wird pro Lauf MEHRFACH
    ausgewertet (Report-Bau, Fail-Fast-Probe, Abbruchpfad, finaler Artefakt-Schreibvorgang, #1083);
    landet der Report DIESES Laufs zwischen zwei dieser Auswertungen bereits in ``REPORTS_DIR``
    (der #933-Zwischenreport-Schreiber aktualisiert ihn nach JEDEM Symbol), kippte
    ``has_prior_reports`` bislang von ``False`` auf ``True``, OBWOHL sich am tatsächlichen
    Lauf-Verlauf nichts geändert hat — derselbe Messwert (``total_runs_started``) erhielt je nach
    Auswertungszeitpunkt ein anderes Urteil. Der Ausschluss über ``run_id`` (statt über die
    Aufrufreihenfolge) macht das Urteil unabhängig davon, WANN innerhalb eines Laufs es ausgewertet
    wird — eine echte Selbstreferenz kann per Konstruktion nicht mehr auftreten.

    ``coverage_bootstrap_phase`` — durchgereicht an ``invariants.check_coverage_ledger_continuity``
    (siehe dortiger Docstring)."""
    try:
        own_name = f"run_{run_id}.json" if run_id else None
        has_prior_reports = REPORTS_DIR.exists() and any(
            p.name != own_name for p in REPORTS_DIR.glob("run_*.json"))
    except OSError:
        has_prior_reports = False
    ledger = _symbol_coverage.load_coverage()
    return _inv.check_coverage_ledger_continuity(
        ledger.get("total_runs_started", 0), has_prior_reports,
        coverage_bootstrap_phase=coverage_bootstrap_phase)


def _champions_summary(opt_data: dict, studies_out: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Issue #818 (#742-Report-Zaehlerpaar) — ``cross_study.champions``:
    ``{stored, admissible, corroborated, written_back, skipped_by_reason, max_corroboration_count,
    attempts}`` über den AKTUELLEN Champion-Store-Stand (``data/optimizer/champions/*.json``, exkl.
    ``_stale/``, #821). Liest den Store direkt (dieselbe Quelle, aus der
    ``resolve_symbol_shrinkage_seed`` seedet) statt der Sweep-Log-Events — robust gegen einen
    Report, der nachträglich (``--report-only``, #833) ohne Live-Sweep-Kontext erzeugt wird.
    Fail-open (leere Zusammenfassung) bei jedem Lesefehler — ein Report darf wegen des
    Champion-Stores nie crashen.

    Issue #1084 Fix Punkt 1/3 (Katalog #866-2, Kohorte E, Root-Cause c) — ``store_champion``
    persistiert NUR bei einem admissiblen Kandidaten (siehe dortiger Docstring); ein Paar OHNE
    admissiblen Kandidaten hinterlässt NIE eine Datei und blieb für die reine
    Verzeichnis-Iteration unsichtbar (12 von 14 Paaren im #866-2-Referenzlauf). ``studies_out``
    (die vom Aufrufer bereits gebauten confirmed Study-Records DIESES Laufs, optional) macht die
    VOLLSTAENDIGE Versuchs-Kohorte sichtbar: ``attempts`` zählt jedes (strategy, symbol)-Paar
    dieses Laufs (14, nicht nur die 2 Store-Einträge); ``skipped_by_reason`` trägt dann
    ``champions.load_champion_entry_with_reason``s granularen Reason-Code je Paar (u. a.
    ``STORE_EMPTY``/``NO_ENTRY_FOR_PAIR``, #1084 Fix Punkt 2, und
    ``NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED`` statt des tautologischen ``NOT_WRITTEN_BACK``).
    ``studies_out=None`` (Legacy-/Report-only-Aufrufer ohne diese Liste) lässt ``skipped_by_reason``
    auf der reinen Verzeichnis-Iteration (bit-identisch zum Pre-#1084-Verhalten); ``attempts``
    bleibt dann ``None`` (unbekannt, nicht 0 — ``check_champion_writeback_reachability`` behandelt
    diese beiden Fälle unterschiedlich)."""
    import collections
    from automation.optimizer import champions as _champions_mod

    empty = {"stored": 0, "admissible": 0, "corroborated": 0, "written_back": 0,
             "skipped_by_reason": {}, "semantics_migrated": 0,
             "admissible_despite_simulation_stale": 0, "max_corroboration_count": None,
             "attempts": None}
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
    max_corroboration_count: int | None = None
    skipped_by_reason: collections.Counter = collections.Counter()
    promote_after = int(opt_data.get("champion_promote_after_runs", 2))
    for path in paths:
        entry = _load_json(path)
        if not isinstance(entry, dict):
            continue
        stored += 1
        lifecycle = entry.get("lifecycle") or {}
        # Issue #1084 Fix Punkt 4 — der HOECHSTE ueber ALLE Store-Eintraege beobachtete
        # corroboration_count, Rohmaterial fuer check_champion_corroboration_reachable
        # (unabhaengig von der Admissibilitaet des jeweiligen Eintrags — auch ein inadmissibler
        # Eintrag traegt einen echten, gemessenen corroboration_count).
        _cc = int(lifecycle.get("corroboration_count", 0) or 0)
        if max_corroboration_count is None or _cc > max_corroboration_count:
            max_corroboration_count = _cc
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
            if studies_out is None:
                skipped_by_reason[reason or "UNKNOWN"] += 1
            continue
        admissible += 1
        if _cc >= promote_after:
            corroborated += 1
        if lifecycle.get("writeback_applied"):
            written_back += 1
        elif studies_out is None:
            try:
                stale = _champions_mod.champion_quality_stale(entry, opt_data)
            except Exception:
                stale = False
            skipped_by_reason["QUALITY_STALE" if stale else "NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED"] += 1

    attempts: int | None = None
    if studies_out is not None:
        # Issue #1084 Fix Punkt 1/3 — die ATTEMPT-skopierte Rekonstruktion: jedes (strategy,
        # symbol)-Paar dieses Laufs, unabhängig davon, ob es einen Store-Eintrag hinterlassen hat.
        # ``load_champion_entry_with_reason`` liest denselben, gerade oben iterierten Store-Stand
        # nochmals GEZIELT je Paar — der zweite Durchlauf ist bewusst getrennt: die
        # Verzeichnis-Iteration oben bleibt die reine "aktueller Store-Zustand"-Sicht (stored/
        # admissible/corroborated/written_back/max_corroboration_count unverändert), diese
        # Kohorte hier ersetzt ausschliesslich ``skipped_by_reason`` um die für #1084 fehlende
        # Versuchs-Vollständigkeit.
        skipped_by_reason = collections.Counter()
        pairs_seen: set[tuple[str, str]] = set()
        for r in studies_out:
            strategy, symbol = r.get("strategy"), r.get("symbol")
            if not strategy or not symbol or (strategy, symbol) in pairs_seen:
                continue
            pairs_seen.add((strategy, symbol))
            try:
                entry, reason = _champions_mod.load_champion_entry_with_reason(
                    strategy, symbol, opt_data=opt_data)
            except Exception:
                skipped_by_reason["ADMISSIBILITY_CHECK_ERROR"] += 1
                continue
            if entry is None:
                skipped_by_reason[reason or "UNKNOWN"] += 1
                continue
            lifecycle = entry.get("lifecycle") or {}
            if lifecycle.get("writeback_applied"):
                continue  # zaehlt bereits als written_back oben, kein skipped_by_reason-Eintrag.
            try:
                stale = _champions_mod.champion_quality_stale(entry, opt_data)
            except Exception:
                stale = False
            skipped_by_reason["QUALITY_STALE" if stale else "NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED"] += 1
        attempts = len(pairs_seen)

    return {
        "stored": stored, "admissible": admissible, "corroborated": corroborated,
        "written_back": written_back, "skipped_by_reason": dict(skipped_by_reason),
        "semantics_migrated": semantics_migrated,
        "admissible_despite_simulation_stale": admissible_despite_simulation_stale,
        "max_corroboration_count": max_corroboration_count, "attempts": attempts,
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
    """Issue #851 — Σ Study-Wallclock / (n_jobs × Sweep-Wallclock).

    Issue #1038 (Katalog #866) — trotz des Namens ist dies KEINE Auslastung im engeren Sinn (ein
    Anteil, der niemals 1.0 uebersteigen kann): der Zaehler ueberlappt sich strukturell, wenn (a)
    eine Study fremder Laeufe eingemischt war (vor #1023) — Σ Study-Wallclock zaehlte dann Sekunden
    mehrfacher, GLEICHZEITIGER Laeufe zusammen, oder (b) jede Study selbst einen EIGENEN Worker-Pool
    oeffnet (``backtest_runner.py``, ``_max_workers = max(1, min(cpu//2, 6))``) — Study-Wallclocks
    verschiedener, parallel dispatchter Studies ueberlappen sich dann untereinander. Beobachtete
    Werte: 151,8 %/246,5 %/332,9 % ueber drei Laeufe. Nach #1023 (fremde Studies ausgeschlossen)
    bleibt Ursache (b) bestehen — ``check_worker_utilisation_plausible`` (invariants.py) meldet
    jeden Wert > 1.0 als FAIL statt ihn unkommentiert anzuzeigen. ``_worker_utilisation_backtest_ms``
    (unten) ist die zweite, ueberlappungsfreie Grösse fuer denselben Zweck.

    None ohne n_jobs/sweep_wallclock_s ODER ohne eine einzige Study mit Wallclock-Daten."""
    if not n_jobs or n_jobs <= 0 or not sweep_wallclock_s or sweep_wallclock_s <= 0:
        return None
    total_study_wallclock = sum(
        r["study_wallclock_s"] for r in studies_out if r.get("study_wallclock_s") is not None)
    if total_study_wallclock <= 0:
        return None
    return total_study_wallclock / (n_jobs * sweep_wallclock_s)


def _worker_utilisation_backtest_ms(studies_out: list[dict[str, Any]], *, n_jobs: int | None,
                                    sweep_wallclock_s: float | None) -> float | None:
    """Issue #1038 (Katalog #866) — Σ ``backtest_ms_sum`` (tatsaechliche, additive Backtest-CPU-Zeit
    je Trial, ``_study_record``) / (n_jobs × Sweep-Wallclock). Anders als ``_worker_utilisation``
    (Study-Wallclock, siehe dortiger Docstring) summiert dies echte Trial-Arbeit statt Wanduhrzeit
    — verschachtelte Study-eigene Worker-Pools koennen diese Zahl NICHT ueber 1.0 durch reine
    Ueberlappung treiben, da jede Millisekunde genau EINEM Trial zugeordnet ist."""
    if not n_jobs or n_jobs <= 0 or not sweep_wallclock_s or sweep_wallclock_s <= 0:
        return None
    total_backtest_s = sum(
        r["backtest_ms_sum"] for r in studies_out if r.get("backtest_ms_sum") is not None) / 1000.0
    if total_backtest_s <= 0:
        return None
    return total_backtest_s / (n_jobs * sweep_wallclock_s)


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
    ``promotion_family_scope='per_symbol_best'``).

    Issue #1080 (Katalog #866-2) — Root-Cause: ``record['n_family_stage1']`` bleibt ``None``, wenn
    eine Study zwar OOS-eligible Trials mit verfügbarer Selektionsstatistik hatte, ihr promoteter
    Gewinner aber im HOLDOUT-Fenster zufällig 0 Trades erzeugte (der Holdout-Backtest ist ein
    ANDERES Zeitfenster als die OOS-Folds) — die Study fehlt dann VOLLSTÄNDIG im
    ``n_family_stage1``-Block, obwohl sie zur familienweiten Multiplizität beitragen MUSS (Beweis
    im #866-Katalog: TrendPullback, 111 Trials mit verfügbarer Selektionsstatistik, aber
    ``n_family_stage1 = null``, weil ``holdout_total_trades = 0``). Fällt ``n_family_stage1``
    NICHT auf die Study zurück, fehlt sie beim familienweiten ``n_family`` (#822s vorgeschriebene
    Grundgesamtheit, ``oos_selection_statistic_available``) — die Deflations-Referenz SR* wird zu
    niedrig angesetzt (Φ⁻¹(1−1/n) unterschätzt), was JEDE Promotionsentscheidung mit familienweiter
    Korrektur begünstigt. Fallback: fehlt ``n_family_stage1``, aber
    ``n_selection_statistic_available`` ist bekannt (dieselbe #822-Grundgesamtheit), wird DIESER
    Wert verwendet, statt die Study stillschweigend auszulassen."""
    stage1: dict[str, dict[str, int]] = {}
    stage2: dict[str, int] = {}
    for r in studies_out:
        symbol = r.get("symbol")
        strategy = r.get("strategy")
        n1 = r.get("n_family_stage1")
        if n1 is None:
            n1 = r.get("n_selection_statistic_available")
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


def build_probe_report(proposals: Iterable[Path | dict], *, run_id: str,
                       report_source: str = "probe") -> dict:
    """Issue #856 — dünner, öffentlicher Wrapper für die #839-Fail-Fast-Probe in ``sweep.py``:
    parst + baut den Report in einem Aufruf, schreibt NICHTS auf die Platte (reine Lesefunktion).
    Hält ``_build_report`` als modulinternen Kern, dessen einziger externer Konsument dieser
    Wrapper (und ``generate_sweep_report``) ist — die Call-Site in ``sweep.py`` kann die Path→dict-
    Normalisierung dadurch nicht mehr umgehen (Root-Cause #856, Pitfall #269).

    Issue #1083 (Pitfall #379) — ``report_source`` (Default ``'probe'``) durchgereicht an
    ``_build_report``: markiert jede Auswertung, die NICHT dem persistierten Artefakt entspricht
    (siehe dortiger Docstring)."""
    return _build_report(
        parse_proposal_payloads(proposals),
        run_id=run_id, started_at_utc=None, wallclock_s=None, cli_args=None,
        report_source=report_source,
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
    symbols_discovered: int | None = None,
    symbols_gate1_rejected: int | None = None,
    report_source: str = "final",
) -> dict:
    # Issue #1083 (Katalog #866-2, Kohorte "Stufe 1 Ergänzung", Pitfall #379) — ``_build_report``
    # wird pro Lauf an MEHREREN Call-Sites erneut ausgewertet (Symbol-Fortschritts-Probe,
    # Zwischenreport-Schreiber, Fail-Fast-Probe, finaler Artefakt-Schreiber, siehe ``sweep.py``).
    # Root-Cause #1083 (Beweis B-17 im #866-Katalog): vier zeitlich getrennte
    # ``INVARIANT_CHECK_FAILED``-Wellen für dieselbe Suite, aber nur EINE davon entspricht dem
    # tatsächlich persistierten ``run.json`` — sobald eine Eingabe zwischen den Wellen vom eigenen
    # Lauf abhängt (#1064), kann jede Welle ein anderes Verdikt tragen. Statt die Suite über einen
    # tiefen Umbau der Aufrufkette nur noch EINMAL auszuwerten (grosser Eingriff in die Symbol-
    # Fortschritts-/Fail-Fast-Architektur), macht dieser Parameter jede Auswertung NACHVOLLZIEHBAR:
    # ``report_source`` (``'final'`` = das Ergebnis, das als ``run.json`` geschrieben wird;
    # ``'probe'``/eine speziellere Bezeichnung = eine Zwischen-/Entscheidungs-Auswertung, die NIE
    # persistiert wird) steht in jedem ``INVARIANT_CHECK_FAILED``-Event UND im Report selbst
    # (``invariant_evaluation_source``) — ein Log-Konsument kann die Wellen damit einander
    # zuordnen, statt vier scheinbar widersprüchliche Ergebnisse ohne Herkunft zu sehen.
    # Issue #1064 (derselbe Fix, dieselbe Abnahme) behebt die KONKRETE Ursache der beobachteten
    # Divergenz zwischen den Wellen (``has_prior_reports``-Selbstreferenz) an der Quelle — mit
    # diesem Fix tragen alle Wellen bereits DASSELBE Verdikt, ``report_source`` bleibt die
    # zusätzliche Diagnose-Spur für den (nicht ausgeschlossenen) Fall einer künftigen, anderen
    # lauf-abhängigen Eingabe.
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

    # Issue #923 — einmal je Report-Lauf gelesen (nicht je Study — dieselbe Datei, kein
    # Symbol-Scope beim Lesen selbst nötig).
    _symbol_bar_quality_cache = read_symbol_bar_quality_cache(WORK)
    # Issue #1028 (Katalog #866) — einmal je Report-Lauf gelesen; Rohmaterial für
    # invariants.check_sizing_identity_coherence.
    _trade_amount_pct_map = _trade_amount_pct_by_strategy()

    studies_out: list[dict[str, Any]] = []
    all_checks: list[tuple[str, _inv.InvariantResult]] = []
    # Issue #1023 (Katalog #866) — Root-Cause: die Study-Auswahl enumerierte bislang JEDES an
    # ``proposals`` haengende Proposal ungefiltert; ein WORK-Verzeichnis-Proposal, das aus einem
    # frueheren Lauf stammt (z. B. ueber den #799-Checkpoint-Resume-Pfad wiederverwendet, siehe
    # ``sweep.run_per_symbol_sweep``), zog dessen komplette Study — inklusive Trials von einem
    # VORTAG — in DIESEN Report. Beobachtet: 98 von 112 Studies eines Ein-Symbol-Laufs trugen
    # ``study_started_at_utc`` 9-12h vor dem Laufbeginn.
    #
    # Kriterium: ``study_started_at_utc`` (von ``_optimize_symbol_impl`` VOR jedem ``study.optimize``
    # gestempelt, #851 — ueberschrieben bei JEDER tatsaechlichen Optimierung dieser Study, auch bei
    # einem #799-Checkpoint-Resume INNERHALB desselben Laufs) muss innerhalb der Sweep-Laufzeit
    # dieses Laufs liegen. Bewusst NICHT ueber den #1025-Trial-Stempel entschieden: eine Study mit
    # ausschliesslich Legacy-Trials (vor #1015, kein run_id-Stempel), aber einem AKTUELLEN
    # ``study_started_at_utc``, gehoert weiterhin zu diesem Lauf — nur eine Study, die in DIESEM
    # Prozess nachweislich NICHT angefasst wurde, ist fremd. Fehlt ``study_started_at_utc`` oder der
    # Lauf-``started_at_utc`` selbst, wird NICHT ausgeschlossen (fail-open auf fehlender Evidenz,
    # analog jedem anderen ``None``-Fall in diesem Modul) — ``check_report_cohort_coherence``
    # (invariants.py) bleibt die zweite, unabhaengige Verteidigungslinie.
    studies_excluded_foreign_run: list[dict[str, Any]] = []
    _run_started_dt = None
    if started_at_utc:
        try:
            _run_started_dt = datetime.fromisoformat(started_at_utc)
        except (TypeError, ValueError):
            _run_started_dt = None
    _foreign_run_tolerance_s = 3600.0
    # Issue #1039 (Katalog #866) — Folgefehler aus #1023: ``cross_study.n_family`` (DSR-
    # Multiplizitaet, siehe unten) muss auf DERSELBEN gefilterten Kohorte laufen wie ``studies_out``,
    # sonst aggregiert es weiterhin ueber die Vortags-Studies, obwohl der Report selbst sie nicht
    # mehr auflistet.
    filtered_proposals: list[dict[str, Any]] = []
    for proposal in proposals:
        study = _load_study_for_proposal(proposal)
        _study_attrs = getattr(study, "user_attrs", None) or {} if study is not None else {}
        _study_started_raw = _study_attrs.get("study_started_at_utc")
        study_trials = list(getattr(study, "trials", None) or [])
        study_name = getattr(study, "study_name", None)

        # Issue #1086 (Katalog #919) — PRIMAERER Kohorten-Filter: der ``run_id``-Stempel, den
        # ``make_symbol_objective`` seit #1015 auf JEDEN Trial schreibt (sofern ``run_id``
        # gesetzt wurde — der Normalfall seit #1015 fuer jeden ``sweep.run_per_symbol_sweep``-
        # Lauf), macht die Zugehoerigkeit einer Study zu DIESEM Lauf direkt nachweisbar, statt sie
        # ueber die STARTZEIT zu erraten. Root-Cause #1086: mehrere gleichzeitig laufende Sweeps
        # (je ein Prozess pro ``--symbols``) teilen sich denselben ``{WORK}/sweep/``-Optuna-Store;
        # ``generate_report_for_run`` (Abbruch-/Standalone-Pfad) entdeckt ALLE ``proposal_*.json``
        # in ``WORK``, unabhaengig davon, welcher Prozess sie geschrieben hat. Die Zeitfenster-
        # Heuristik aus #1023 (unten) allein reicht nicht: bei ueberlappenden Laeufen liegt die
        # fremde Study-Startzeit oft INNERHALB der Toleranz des eigenen Laufs.
        _own_run_trials = [
            t for t in study_trials
            if (getattr(t, "user_attrs", None) or {}).get("run_id") == run_id
        ]
        _foreign_run_trials = [
            t for t in study_trials
            if (getattr(t, "user_attrs", None) or {}).get("run_id") not in (None, run_id)
        ]
        _has_run_id_evidence = bool(_own_run_trials or _foreign_run_trials)

        if _own_run_trials and _foreign_run_trials:
            # Issue #1086 — eine Study, die SOWOHL Trials dieses Laufs ALS AUCH Trials eines
            # anderen ``run_id`` traegt, wurde nachweislich von mindestens zwei Laeufen
            # GLEICHZEITIG angefasst (die #1086-Lock-Datei in ``sweep.py`` verhindert genau das
            # fuer kuenftige Laeufe) — eine studienweite Aggregation (n_trials_completed,
            # Guard-Statistiken, ...) kann in diesem Zustand keinem der beiden Laeufe sauber
            # zugeordnet werden. Fail-loud statt eines Urteils auf vermischter Evidenz.
            _foreign_ids = sorted({
                (getattr(t, "user_attrs", None) or {}).get("run_id") for t in _foreign_run_trials
            })
            raise RuntimeError(
                f"[REPORT_COHORT_UNRESOLVABLE] Study '{study_name}' ({proposal.get('strategy')}/"
                f"{proposal.get('symbol')}) traegt sowohl Trials von run_id={run_id!r} als auch "
                f"von {_foreign_ids!r} — Kohorte nicht auflösbar (vermutlich gleichzeitiger "
                "Zugriff zweier Sweep-Prozesse auf denselben Store ohne Lock-Datei, #1086)."
            )

        if _has_run_id_evidence:
            is_foreign_run = not _own_run_trials
        else:
            # Issue #1023 (Katalog #866) — sekundaere, NACHGELAGERTE Pruefung: nur noch relevant,
            # wenn KEIN Trial dieser Study ueberhaupt einen ``run_id``-Stempel traegt (Legacy-
            # Study vor #1015, oder ein Aufrufer, der ``run_id=None`` an ``make_symbol_objective``
            # uebergeben hat). Kriterium: ``study_started_at_utc`` (von ``_optimize_symbol_impl``
            # VOR jedem ``study.optimize`` gestempelt, #851) muss innerhalb der Sweep-Laufzeit
            # dieses Laufs liegen. Fehlt ``study_started_at_utc`` oder der Lauf-``started_at_utc``
            # selbst, wird NICHT ausgeschlossen (fail-open auf fehlender Evidenz).
            is_foreign_run = False
            if _study_started_raw and _run_started_dt is not None:
                try:
                    _study_started_dt = datetime.fromisoformat(_study_started_raw)
                    is_foreign_run = (
                        (_run_started_dt - _study_started_dt).total_seconds()
                        > _foreign_run_tolerance_s)
                except (TypeError, ValueError):
                    is_foreign_run = False

        if is_foreign_run:
            _run_id_found = None
            if _foreign_run_trials:
                _run_id_found = (getattr(_foreign_run_trials[0], "user_attrs", None) or {}).get(
                    "run_id")
            studies_excluded_foreign_run.append({
                "study_name": study_name,
                "strategy": proposal.get("strategy"),
                "symbol": proposal.get("symbol"),
                "run_id_found": _run_id_found,
                "study_started_at_utc": _study_started_raw,
                "run_started_at_utc": started_at_utc,
                "n_trials_total_study": len(study_trials),
                "reason": "run_id_mismatch" if _run_id_found else "study_started_before_this_run",
            })
            continue
        record, checks = _study_record(
            proposal, study, tournament_cfg,
            guard_dominance_threshold=float(
                optimizer_cfg.get("sortino_guard_trip_fraction_warn", 0.10)),
            symbol_bar_quality_cache=_symbol_bar_quality_cache, run_id=run_id)
        # Issue #1028 (Katalog #866) — Rohmaterial für invariants.check_sizing_identity_coherence.
        record["trade_amount_pct"] = _trade_amount_pct_map.get(record.get("strategy"))
        # Issue #1088 (Katalog #921) — nur gestempelt, wenn TATSAECHLICH ein Trial dieser Study den
        # run_id-Nachweis traegt (der Legacy-/Zeitfenster-Fallback-Pfad ohne Nachweis bleibt
        # None — fail-open, siehe ``assert_invariant_scope_uncontaminated``-Docstring).
        record["run_id"] = run_id if _own_run_trials else None
        studies_out.append(record)
        filtered_proposals.append(proposal)
        study_label = f"{record['strategy']}/{record['symbol']}"
        all_checks.extend((study_label, c) for c in checks)
        # Issue #791 — REJECT_SELECTION_PBO erfordert eine dokumentierte Promotions-Inferenz.
        all_checks.append((study_label, _inv.check_promotion_inference_coverage(proposal, record)))

    # Issue #1023 Akzeptanzkriterium 2 — ist die gefilterte Menge leer, WAEHREND der Store nicht
    # leer war (jedes Proposal wurde als fremder Lauf ausgeschlossen), ist das kein leerer, sondern
    # ein FALSCHER Report: fail-loud statt eines irrefuehrenden "0 Studies"-Artefakts.
    if proposals and not studies_out and studies_excluded_foreign_run:
        raise RuntimeError(
            f"[#1023] generate_sweep_report(run_id={run_id!r}): alle {len(studies_excluded_foreign_run)} "
            "referenzierten Studies wurden VOR dem Laufbeginn dieses Sweeps gestartet (fremder Lauf) "
            "— kein Report geschrieben statt eines Berichts ueber eine fremde Kohorte."
        )

    # Issue #1088 (Katalog #921) — Sicherung VOR jedem einzelnen ``check_*`` unten: der #1086-Fix
    # oben macht ``studies_out`` bereits strukturell einlaeufig, dieser Guard bricht trotzdem hart
    # ab, statt (durch einen kuenftigen Aufrufer/Refactor) je ein Urteil auf einer vermischten
    # Kohorte zu faellen.
    _inv.assert_invariant_scope_uncontaminated(studies_out)

    n_family_stage1, n_family_stage2 = _family_n_stages(studies_out)
    # Issue #1080 — einmal berechnet, wiederverwendet fuer die Invariante UNTEN und das
    # cross_study['n_family']-Feld weiter unten (eine Kennzahl, eine Quelle). Issue #1091
    # (Katalog #924) — dies bleibt die "observed_at_report_time"-Sicht (haengt davon ab, wie viele
    # Proposals dieser Familie zum Lesezeitpunkt bereits existieren); check_n_family_partition
    # unten bleibt bewusst auf DIESER Sicht (unveraendertes #1080-Verhalten).
    _n_family_by_symbol = _family_n_from_proposals(filtered_proposals)
    # Issue #1091 — die EINGEFRORENE, budget-basierte Sicht (siehe
    # sweep._family_n_frozen_from_studies-Docstring): jedes Proposal einer Symbol-Familie traegt
    # bereits denselben, symbolweit summierten Wert — daher hier MAX statt Summe je Symbol (eine
    # fehlende Study liefert 0 Beitrag, keine doppelte Zaehlung durch mehrere Proposals).
    _n_family_frozen_by_symbol: dict[str, int] = {}
    for _p in filtered_proposals:
        _frozen = _p.get("deflation_n_family_frozen")
        _sym = _p.get("symbol")
        if _sym and isinstance(_frozen, (int, float)) and not isinstance(_frozen, bool):
            _n_family_frozen_by_symbol[_sym] = max(
                _n_family_frozen_by_symbol.get(_sym, 0), int(_frozen))

    registry_check = _inv.check_config_key_registry(tournament_cfg)
    all_checks.append(("global", registry_check))

    # Issue #1080 (Katalog #866-2) — n_family[symbol] muss exakt der Summe seiner eigenen
    # Stage1-Zerlegung entsprechen; eine Luecke beweist, dass mindestens eine Study fehlt.
    all_checks.append(("global", _inv.check_n_family_partition(_n_family_by_symbol, n_family_stage1)))

    # Issue #1091 (Katalog #924) — neue Invariante: weicht die eingefrorene von der zur
    # Berichtszeit beobachteten Zahl um mehr als 5 % ab, ist die Berichtskohorte unvollstaendig
    # (ein Zwischenreport, oder eine erneute #1086-Kontamination).
    all_checks.append((
        "global", _inv.check_family_n_stability(_n_family_frozen_by_symbol, _n_family_by_symbol)))

    # Issue #770 — sweep-weite Budget-Ausfuehrungs-Invariante (siebter Check, siehe #743/#773).
    min_median_budget_execution = float(optimizer_cfg.get("min_median_budget_execution", 0.5))
    budget_check = _inv.check_budget_execution(studies_out, min_median=min_median_budget_execution)
    all_checks.append(("global", budget_check))

    # Issue #1023 (Katalog #866) Akzeptanzkriterium 2 — zweite, unabhaengige Verteidigungslinie
    # gegen fremde Studies im Report (siehe der run_id-Filter oben). Issue #1087 (Katalog #920) —
    # ``run_started_at_utc`` durchgereicht, damit die offset-basierten Klauseln (statt nur der
    # blinden Spannweiten-Klausel) auswertbar sind.
    all_checks.append(("global", _inv.check_report_cohort_coherence(
        studies_out, wallclock_s=wallclock_s, run_started_at_utc=started_at_utc)))

    # Issue #1038 (Katalog #866) — vorab berechnet (statt erst im Report-Dict unten), damit die
    # Invariante denselben Wert prueft, der auch angezeigt wird (eine Kennzahl, eine Quelle).
    _worker_utilisation_value = _worker_utilisation(
        studies_out, n_jobs=(cli_args or {}).get("n_jobs"), sweep_wallclock_s=wallclock_s)
    all_checks.append((
        "global", _inv.check_worker_utilisation_plausible(
            _worker_utilisation_value, n_studies=len(studies_out))))

    # Issue #1031 (Katalog #866) — Kohaerenz zwischen expectancy und expectancy_capital_weighted.
    all_checks.append(("global", _inv.check_expectancy_definition_coherence(studies_out)))

    # Issue #1073 (Katalog #866-2) — FAIL bei einem Vorzeichenwechsel zwischen roher und
    # winsorisierter Holdout-Expectancy (das positive Ergebnis haengt dann an wenigen Ausreissern).
    all_checks.append(("global", _inv.check_expectancy_outlier_dependence(studies_out)))

    # Issue #1085 (Katalog #866-2) — Dust-Round-Trips (Notional ~1e-13, Fliesskomma-Residuen)
    # duerfen keinen gepoolten Nenner (exit_reason_histogram, timebox-Quoten) fuellen.
    all_checks.append(("global", _inv.check_dust_round_trip_share(studies_out)))

    # Issue #1037 (Katalog #866) — Round-Trips, die am Datenende zwangsweise finalisiert wurden.
    all_checks.append(("global", _inv.check_open_position_at_data_end(studies_out)))

    # Issue #1028 (Katalog #866) — Sizing-Identität + ATR-Skalenhomogenität (Datenintegritäts-
    # Wächter gegen die TSLA-Signatur des Katalogs; siehe jeweiliger Docstring).
    all_checks.append(("global", _inv.check_sizing_identity_coherence(studies_out)))
    # Issue #1071 — die per-Symbol ATR-Floor-Auflösung macht den Mechanismus (Floor-Bindung vs.
    # echte Sprungstelle) messbar, statt eine Ursache zu behaupten (siehe Docstring dort).
    _atr_floor_by_symbol = _atr_floor_bps_by_symbol(
        (r.get("symbol") for r in studies_out))
    atr_scale_homogeneity_check = _inv.check_atr_scale_homogeneity(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol)
    all_checks.append(("global", atr_scale_homogeneity_check))

    # Issue #1042 (Katalog #866) E-2 — Sichtbarkeits-Wächter: divergiert das im Backtest
    # konfigurierte trade_amount_pct vom live tatsächlich gefahrenen MomentumLSAllocator-Deckel.
    all_checks.append((
        "global", _inv.check_sizing_parity_backtest_vs_allocator(
            _trade_amount_pct_map, max_symbol_exposure_fraction=_max_symbol_exposure_fraction())))

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
    # Issue #1070 (Pitfall #369) — die zweite, symmetrische Schranke: ein Verhältnis WEIT ÜBER dem
    # konfigurierten Abstand beweist genauso, dass der Stop keine kalibrierte Risikogrösse ist.
    stop_distance_max_ratio = float(optimizer_cfg.get("stop_distance_max_ratio", 10.0))
    effective_stop_distance_check = _inv.check_effective_stop_distance(
        studies_out, min_ratio=stop_distance_min_ratio, max_ratio=stop_distance_max_ratio)
    all_checks.append(("global", effective_stop_distance_check))

    # Issue #1072 (Wiederkehr #1050/#1051) — die Stopdistanz muss ein Mindestvielfaches der
    # Round-Trip-Kosten betragen, sonst kann eine Position den Stop strukturell nicht überleben,
    # bevor die Kosten sie auffressen.
    min_stop_to_cost_ratio = float(tournament_cfg.get("min_stop_to_cost_ratio", 3.0))
    _round_trip_cost_bps_by_symbol_map = _round_trip_cost_bps_by_symbol(
        (r.get("symbol") for r in studies_out))
    all_checks.append(("global", _inv.check_stop_cost_ratio(
        studies_out, round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        min_stop_to_cost_ratio=min_stop_to_cost_ratio)))

    # Issue #1068 — vorgezogen (war vorher erst bei check_diagnosis_ledger_coherence unten
    # gelesen): dieselbe Ledger-Zahl treibt seit #1084 zusätzlich check_champion_corroboration_
    # reachable (Champion-Block direkt darunter).
    _diagnosis_ledger_total_runs_started = _symbol_coverage.load_coverage().get("total_runs_started")

    # Issue #818 — achter Invarianten-Check: der Champion-Store-Writeback-Pfad (Ebene 2, #706)
    # muss NACHWEISLICH erreichbar sein, nicht nur getestet/dokumentiert (Pitfall #237).
    # Issue #1084 (Katalog #866-2, Kohorte E) — ``studies_out`` macht die VOLLSTAENDIGE
    # Versuchs-Kohorte dieses Laufs sichtbar (siehe _champions_summary-Docstring), nicht nur die
    # Teilmenge, die tatsächlich einen Store-Eintrag erhielt.
    champions_summary = _champions_summary(optimizer_cfg, studies_out=studies_out)
    champion_writeback_check = _inv.check_champion_writeback_reachability(champions_summary)
    all_checks.append(("global", champion_writeback_check))

    # Issue #1084 Fix Punkt 4 — sechzehnter Invarianten-Check: der Korroborations-Deadlock
    # (Ebene 2 verlangt corroboration_count >= champion_promote_after_runs) wird benannt, statt
    # als generische "unerreichbar"-Diagnose unter check_champion_writeback_reachability zu
    # verschwinden. Issue #1089 (Katalog #922) — ``total_runs_started`` (das globale, prozess-
    # übergreifende Ledger) geht seit diesem Fix NUR NOCH als Provenance ein, nicht mehr in die
    # PASS/FAIL-Entscheidung (siehe Docstring — der frühere ODER-Ast machte den Check unter
    # gleichzeitigen Sweep-Prozessen faelschlich gruen).
    champion_corroboration_check = _inv.check_champion_corroboration_reachable(
        champions_summary, total_runs_started=_diagnosis_ledger_total_runs_started,
        corroboration_threshold=int(optimizer_cfg.get("champion_promote_after_runs", 2)))
    all_checks.append(("global", champion_corroboration_check))

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

    # Issue #1066 (Pitfall #371) — jeder ``proposed_bounds``-Eintrag im #761-Diagnose-Cache muss
    # innerhalb des Domänenregisters (spaces._PARAM_DOMAIN_REGISTRY) liegen; ein Cache-Eintrag von
    # VOR diesem Fix (negative Perioden/Bar-Anzahlen, Beweis B-5 im #866-Katalog) macht diesen
    # Check FAILen, bis er migriert ist (sweep_diagnostics.migrate_search_space_override_cache).
    search_space_admissible_check = _inv.check_search_space_override_admissible(
        _diagnosed_pairs_all())
    all_checks.append(("global", search_space_admissible_check))

    # Issue #1068 — der #761-Diagnose-Cache (n_runs_confirmed je Paar) und das Coverage-Ledger
    # (total_runs_started) sind zwei unabhängig persistierte Zähler über denselben Lauf-Verlauf;
    # ein Paar, das öfter IN FOLGE bestätigt wurde, als das Ledger Läufe gesehen hat, beweist, dass
    # einer der beiden Stores zurückgesetzt/verloren gegangen ist (#1064). ``_diagnosis_ledger_
    # total_runs_started`` selbst ist jetzt weiter oben (Champion-Block, #1084) berechnet.
    diagnosis_ledger_coherence_check = _inv.check_diagnosis_ledger_coherence(
        _diagnosed_pairs_all(), total_runs_started=_diagnosis_ledger_total_runs_started)
    all_checks.append(("global", diagnosis_ledger_coherence_check))

    # Issue #832 Fix Punkt 1 / #861 (Unifikation) — zehnter Invarianten-Check: keine Study darf
    # einen Anteil zeitbox-verletzender Trials ueber ``timebox_violation_study_tolerance`` tragen
    # (dieselbe Schwelle, die #857 fuer die Study-Ebene-Konsequenz in confirm.py verwendet) — ein
    # Treffer ist ein Bug im Exit-Pfad.
    _timebox_study_tolerance = float(tournament_cfg.get("timebox_violation_study_tolerance", 0.25))
    # Issue #1036 (Katalog #866) — der Magnituden-Ast des Checks (siehe Docstring dort).
    _timebox_hard_multiple = float(tournament_cfg.get("timebox_violation_hard_multiple", 3.0))
    holding_time_cap_check = _inv.check_holding_time_cap(
        studies_out, study_tolerance=_timebox_study_tolerance, hard_multiple=_timebox_hard_multiple,
        timebox_execution_slack_bars=float(tournament_cfg.get("timebox_execution_slack_bars", 3.0)))
    all_checks.append(("global", holding_time_cap_check))

    # Issue #972 — Zero-Eligible-Plateau-Zähler-Widerspruch: n_evaluated + Zerlegung der entfernten
    # Trials muss n_trials ergeben, sonst zielen die beiden Zähler nicht auf dieselbe Grundgesamtheit.
    all_checks.append(("global", _inv.check_counter_partition_consistency(studies_out)))

    # Issue #841 — elfter Invarianten-Check: kein Symbol des aktuellen Universums darf seit mehr
    # als symbol_coverage_max_age_runs abgeschlossenen Läufen unabgedeckt bleiben (least_recently_
    # covered-Rotation, siehe symbol_coverage.py).
    symbol_coverage_summary, symbol_coverage_check = _symbol_coverage_summary(optimizer_cfg)
    all_checks.append(("global", symbol_coverage_check))

    # Issue #892 Fix Punkt 2 — ein bei Laufbeginn auf 1 zurückgesetztes Coverage-Ledger, obwohl
    # bereits frühere Lauf-Reports existieren, ist ein Datenverlust (achte Wiederkehr Pitfall #237).
    # Issue #1064 — run_id schliesst den eigenen Report von has_prior_reports aus;
    # coverage_bootstrap_phase (aus derselben symbol_coverage_summary wie oben) macht den Check
    # waehrend der Bootstrap-Phase unbedingt PASS.
    all_checks.append(("global", _coverage_ledger_continuity_check(
        run_id, coverage_bootstrap_phase=bool(symbol_coverage_summary.get("coverage_bootstrap_phase")))))

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

    # Issue #968 (Katalog A, P0 HEADLINE) — Reproduzierbarkeits-Wächter: die Guard-Referenz darf
    # innerhalb EINER Study weder den Wert noch die Quelle wechseln (Pitfall #307).
    all_checks.append(("global", _inv.check_guard_reference_stability(studies_out)))

    # Issue #970 (Katalog A, P1) — kein Gate ohne jeden marginalen Beitrag über eine ausreichend
    # grosse Kohorte darf in eligible_requires_all verbleiben. Issue #1076 — geschützte Gates
    # (tournament.json['gate_consolidation_protected']) erhalten eine Neukalibrierungs- statt
    # Entfernungsempfehlung (SPERRVERMERK: keine weitere Gate-Entfernung vor #1076).
    all_checks.append(("global", _inv.check_gate_marginal_contribution(
        studies_out, gate_consolidation_protected=tournament_cfg.get("gate_consolidation_protected"))))

    # Issue #1076 — Kreuzprüfung: gate_inventory.n_rejections darf nie unter dem gate-spezifischen
    # is_rejection_detail_counts-Wert liegen (sonst liest der Zähler vermutlich den falschen Eimer,
    # z. B. NONE statt des gate-spezifischen — Beweis B-10 im #866-Katalog).
    all_checks.append(("global", _inv.check_gate_inventory_coherence(studies_out)))

    # Issue #976 (Katalog B, P2) — Detektion überproportional vieler unerreichbarer OOS-Fenster
    # (zu weite Lookback-Bounds für die Datenlage).
    all_checks.append(("global", _inv.check_window_unreachable_rate(studies_out)))

    # Issue #915 — die WIRKUNGS-Invariante neben der Quellen-Invariante oben: liefert der Guard
    # tatsächlich eine benutzbare Schwelle (definierter oos_psr), unabhängig davon, ob die
    # konfigurierte Referenz formal verwendet wurde.
    _selection_stat_min_fraction = float(
        tournament_cfg.get("selection_statistic_min_available_fraction", 0.80))
    selection_statistic_availability_check = _inv.check_selection_statistic_availability(
        studies_out, min_available_fraction=_selection_stat_min_fraction)
    all_checks.append(("global", selection_statistic_availability_check))

    # Issue #929 Fix 3 — eigenständiges Frühwarnsignal, unabhängig von p_eligible auswertbar: eine
    # stagnierende/wachsende Constraint-Verletzung trotz ausreichend modellierter Trials belegt,
    # dass der TPE-Sampler keinen Gradienten gefunden hat.
    search_made_progress_check = _inv.check_search_made_progress(studies_out)
    all_checks.append(("global", search_made_progress_check))

    # Issue #919 Fix 4 — jede Lücke zwischen dem je-Study aufsummierten Exit-Reason-Histogramm
    # und der tatsächlichen Round-Trip-Zahl bedeutet einen Exit-Pfad ohne Order-Tag-Attribution.
    exit_reason_coverage_check = _inv.check_exit_reason_coverage(studies_out)
    all_checks.append(("global", exit_reason_coverage_check))

    # Issue #923 Fix 4 — n_periods streut innerhalb desselben Symbols stark je Strategie; ab
    # einem Faktor > deflation_max_n_periods_ratio-Kalibrierpunkt (Default 6.0 hier, 4.0 dort)
    # greift die #865-Heterogenitäts-Suppression vermutlich für praktisch jede Familie.
    n_periods_homogeneity_check = _inv.check_n_periods_homogeneity(studies_out)
    all_checks.append(("global", n_periods_homogeneity_check))

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

    # Issue #1063 (Pitfall #370) — Meta-Wächter: jeder FAILende fail_fast_invariants-Check muss
    # seine Offender in der actual-Pair-Konvention tragen, sonst kann
    # sweep._offending_pairs_for_fail_fast_check die #1016-Breitenschwelle nie auswerten (stiller
    # Konservativ-Abbruch). Muss ebenfalls NACH allen anderen Checks stehen (braucht ihre
    # actual/passed-Werte).
    _already_evaluated_dicts = [c.to_dict() for _label, c in all_checks]
    fail_fast_actual_convention_check = _inv.check_fail_fast_actual_convention(
        _already_evaluated_dicts, fail_fast_invariants=optimizer_cfg.get("fail_fast_invariants"))
    all_checks.append(("global", fail_fast_actual_convention_check))

    invariant_checks = []
    for label, result in all_checks:
        d = result.to_dict()
        d["scope"] = label
        invariant_checks.append(d)
        if not result.passed:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": label, "check": result.name,
                "expected": result.expected, "actual": result.actual, "detail": result.detail,
                # Issue #1083 — welche Auswertungswelle dieses Event traegt (siehe Docstring oben).
                "report_source": report_source,
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
        # Issue #942 (Katalog A) — Funnel-Transparenz: symbols_discovered (rohes Symbol-Universum
        # dieses Laufs) vs. symbols_gate1_rejected (Gate 1 INSUFFICIENT_HISTORY/PARAM_DATA_RATIO_
        # TOO_LOW/OOS_FOLD_TOO_SHORT) vs. symbols_planned (nach Gate-1-Filterung, bereits vorhanden).
        # Vorher war nur symbols_planned sichtbar — ein Operator konnte die erreichbare Coverage
        # (symbols_planned/symbols_discovered) nicht vom Report ablesen.
        "symbols_discovered": symbols_discovered,
        "symbols_gate1_rejected": symbols_gate1_rejected,
        # Issue #849 — im Report EINGEBETTET (statt eines zweiten config_dir()-Lesezugriffs in
        # summary_de.py, das bewusst reines Rueckgabedict-only bleibt, siehe Moduldocstring dort):
        # Sektion 5.2 zeigt hoechstens so viele Beispiel-Details je Check, bevor sie auf "... und N
        # weitere" kollabiert (Akzeptanzkriterium #849-5, Bericht bleibt bei >= 500 FAILs kompakt).
        "summary_max_details_per_check": int(optimizer_cfg.get("summary_max_details_per_check", 5)),
        "studies": studies_out,
        # Issue #1023 (Katalog #866) Fix Punkt 1 — Studies, deren komplette Trial-Historie zu einem
        # ANDEREN run_id gehoert, werden NICHT stillschweigend in ``studies`` aufgenommen, sondern
        # hier mit Grund gelistet — sichtbar statt verschwunden.
        "studies_excluded_foreign_run": studies_excluded_foreign_run,
        "cross_study": {
            # Issue #1091 (Katalog #924) — {frozen, observed_at_report_time} statt eines nackten
            # int je Symbol: "frozen" (budget-basiert, siehe sweep._family_n_frozen_from_studies)
            # ist ueber mehrere Reports DESSELBEN Laufs bit-identisch; "observed_at_report_time"
            # (die Alt-Zahl, #625) bleibt als Diagnose-Telemetrie erhalten — check_family_n_
            # stability (invariants.py) vergleicht beide.
            "n_family": {
                "frozen": _n_family_frozen_by_symbol,
                "observed_at_report_time": _n_family_by_symbol,
            },
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
            # Issue #1082 Fix Punkt (a) — Studies unter der check_objective_branch_coverage-Schwelle
            # als Rohmaterial fuer den Suchbudget-Vorschlag des naechsten Laufs (sweep.py liest
            # diese Sektion aus dem juengsten Report und deprioritisiert die betroffenen Paare).
            "search_budget_proposal": _search_budget_proposal_section(all_checks),
            # Issue #1071 — Studies, deren atr_median_bps auf dem konfigurierten ATR-Floor ihres
            # Symbols liegt (siehe invariants.check_atr_scale_homogeneity-Docstring); leer, wenn der
            # Check PASST oder keine Study floor-gebunden ist.
            "atr_floor_binding_studies": sorted(
                (atr_scale_homogeneity_check.provenance or {}).get("atr_floor_binding_studies", [])
                if atr_scale_homogeneity_check.provenance else []
            ),
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
            "worker_utilisation": _worker_utilisation_value,
            # Issue #1038 (Katalog #866) — zweite, ueberlappungsfreie Auslastungs-Groesse (siehe
            # _worker_utilisation_backtest_ms-Docstring): Σ echte Backtest-CPU-Zeit statt Σ Study-
            # Wanduhrzeit, kann durch verschachtelte Worker-Pools nicht ueber 1.0 getrieben werden.
            "worker_utilisation_backtest_ms": _worker_utilisation_backtest_ms(
                studies_out, n_jobs=(cli_args or {}).get("n_jobs"), sweep_wallclock_s=wallclock_s),
            # Issue #853 — {seed_source_value: n_studies}, dieselbe Verteilung, die
            # check_champion_seed_coverage prüft.
            "seed_source_distribution": seed_source_distribution,
        },
        "invariant_checks": invariant_checks,
        # Issue #1083 — welche Auswertungswelle DIESER Report-Dict traegt ('final' fuer den
        # tatsaechlich persistierten run.json-Aufruf, siehe _build_report-Docstring).
        "invariant_evaluation_source": report_source,
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
    symbols_discovered: int | None = None,
    symbols_gate1_rejected: int | None = None,
    report_source: str = "final",
) -> Path:
    """Baut + schreibt ATOMAR den Report für GENAU DIESEN Sweep-Lauf.

    ``proposals`` sind die von ``run_per_symbol_sweep`` zurückgegebenen Proposal-Pfade (oder
    bereits geparste Dicts, Test-Pfad) — jede referenzierte Study wird FRISCH aus ihrer SQLite-
    Datei geladen (kein Live-Zustand aus dem Sweep-Lauf selbst nötig), was diesen Pfad bit-
    identisch mit ``generate_report_for_run`` macht (Determinismus-Garantie, #742-Akzeptanz).

    Issue #833 Fix Punkt 3 — ``run_status``/``symbols_completed``/``symbols_planned`` werden NUR
    durchgereicht (siehe ``_build_report``); Default ``run_status='complete'`` ⇒ bit-identisch für
    jeden Aufrufer, der einen abgeschlossenen Lauf reportet (der bisherige Normalfall).

    Issue #1083 — ``report_source`` (Default ``'final'``) durchgereicht an ``_build_report``. Diese
    Funktion SCHREIBT ihr Ergebnis auf die Platte — jeder Aufrufer, dessen Schreibvorgang NICHT das
    letztgültige Artefakt dieses Laufs ist (z. B. der #933-Zwischenreport-Schreiber,
    ``run_status='in_progress'``), sollte einen eigenen ``report_source`` übergeben, damit die
    zugehörigen ``INVARIANT_CHECK_FAILED``-Events als Zwischenstand erkennbar sind."""
    parsed = parse_proposal_payloads(proposals)

    report = _build_report(
        parsed, run_id=run_id, started_at_utc=started_at_utc,
        report_source=report_source,
        wallclock_s=wallclock_s, cli_args=cli_args,
        run_status=run_status, symbols_completed=symbols_completed,
        symbols_planned=symbols_planned,
        symbols_discovered=symbols_discovered,
        symbols_gate1_rejected=symbols_gate1_rejected,
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
    symbols_discovered: int | None = None,
    symbols_gate1_rejected: int | None = None,
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
        symbols_discovered=symbols_discovered,
        symbols_gate1_rejected=symbols_gate1_rejected,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
