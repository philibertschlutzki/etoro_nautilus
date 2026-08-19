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
``run_optimization.gradient_signal_arm``/``_sanitize``/``resolve_storage`` für die
Study-Metriken/Storage-Auflösung, ``invariants.py`` (#743) für die mathematischen
Regressionswächter. Issue #1102 (Katalog #935) — die familienweite Cross-Study-Kennzahl
(``cross_study['n_family']``) ist seither die Summe der eigenen ``_family_n_stages``-Zerlegung
(EINE Quelle statt der vorher separat berechneten, veralteten ``sweep._family_n_from_proposals``).

Zwei Aufrufpfade, EIN gemeinsamer Kern (``_build_report``), garantieren Determinismus:
  - ``generate_sweep_report`` — am Ende von ``sweep.main()``, mit den frisch geschriebenen
    Proposal-Pfaden DIESES Laufs.
  - ``generate_report_for_run`` — standalone/nachträglich: entdeckt ALLE aktuell auf der Platte
    liegenden ``proposal_*.json`` (keine laufende Sweep-Orchestrierung nötig) und lädt jede
    referenzierte Study frisch aus ihrer SQLite-Datei — funktioniert auch für einen Sweep, dessen
    Live-Log längst durch die 7-Tage-Rotation gelöscht wurde.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import optuna

from automation.log_manager import emit_execution_event, jsonl_sidecar_path
from automation.optimizer import invariants as _inv
from automation.optimizer import _contracts
from automation.optimizer import reward as _reward
from automation.optimizer.manifest import WORK, git_commit, catalog_fingerprint, sha256_file, write_json_atomic, library_versions
from automation.optimizer.run_optimization import (
    _sanitize, resolve_storage, gradient_signal_arm, _modelled_trials,
    _constraint_violation_progress, compute_budget_execution, _best_completed_value,
    IS_REJECTION_NONE,
)
from automation.optimizer.sweep import (
    load_symbol_universe, read_symbol_bar_quality_cache, _family_members,
    symbol_bar_quality_cache_status,
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
    # Issue #1034/#1183 — Holdout bestanden, aber die Familien-Multiplizitaet fuer die
    # DSR-Korrektur ist unaufloesbar (deflation_n_family <= 0 trotz erreichter Deflationsstufe).
    "REJECT_PROMOTION_FAMILY_UNRESOLVABLE": "deflation",
    "REJECT_SELECTION_PBO": "pbo",
    "REJECT_BOUNDARY_SOLUTION": "boundary",
    "HOLD_BOUNDARY_UNRESOLVED": "boundary",
    # Issue #1101 (Katalog #934) — der terminale Ausgang eines HOLD_BOUNDARY_UNRESOLVED-Kandidaten
    # nach einer bereits geweiteten, aber weiterhin erfolglosen Bounds-Runde.
    "REJECT_BOUNDARY_SOLUTION_PERSISTENT": "boundary",
}

REPORT_SCHEMA_VERSION = 1
REPORTS_DIR = WORK / "reports"

_log = logging.getLogger("optimizer")


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text("utf-8")) or {}
    except (OSError, ValueError):
        return None


def _cost_model_has_zero_realism(base_cfg: Path | None = None) -> bool:
    """Issue #1010/#1162 (Katalog #1170, P0) — dieselbe Erkennung wie ``sweep.warn_if_cost_model_
    zero_realism`` (dort die Startup-WARNING, hier die Report-/Zusammenfassungs-Sicht auf DIESELBE
    ``backtest.json``): ``True``, wenn ``overnight_financing_bps_per_day_by_asset_class`` UND
    ``slippage_bps_by_asset_class`` fuer ALLE Asset-Klassen 0.0 sind — die ``'full_realism'``-
    Kostenstress-Stufe ist dann ein No-Op (siehe ``invariants.check_cost_stress_distinctness``).
    ``summary_de.py`` liest AUSSCHLIESSLICH das bereits geschriebene Report-JSON (keine zweite
    Datenquelle) — dieses Feld ist deshalb der Traeger fuer Abschnitt 2.4, nicht ``backtest.json``
    direkt."""
    cfg_dir = base_cfg or config_dir()
    cfg = _load_json(cfg_dir / "backtest.json") or {}
    financing = cfg.get("overnight_financing_bps_per_day_by_asset_class") or {}
    slippage = cfg.get("slippage_bps_by_asset_class") or {}
    values = [v for v in financing.values() if isinstance(v, (int, float))] + \
             [v for v in slippage.values() if isinstance(v, (int, float))]
    return bool(values) and all(v == 0.0 for v in values)


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
        # Issue #1014/#1166 (Katalog #1170) Regressionsfix — ``_schema`` ist seit diesem Fix
        # selbst ein Dict mit einem ``"trade_amount_pct"``-Schlüssel (Dokumentationstext, kein
        # Zahlenwert, siehe strategy_defaults.json). Ohne diesen Ausschluss (Konvention: ``_schema``
        # ist nie eine echte Strategie, siehe test_issue_623_config_call_sites.py) landete der
        # Prosa-String als "Strategie" ``_schema`` in dieser Map und liess
        # ``check_sizing_parity_backtest_vs_allocator``/``check_sizing_identity_coherence`` mit
        # ``ValueError: could not convert string to float`` crashen.
        if strat == "_schema":
            continue
        if isinstance(params, dict) and isinstance(params.get("trade_amount_pct"), (int, float)):
            out[strat] = params["trade_amount_pct"]
    strategies_cfg = _load_json(cfg_dir / "strategies.json") or {}
    for entry in strategies_cfg.get("strategies") or []:
        if not isinstance(entry, dict):
            continue
        override = (entry.get("params") or {}).get("trade_amount_pct")
        if override is not None:
            out[entry.get("strategy_class")] = override
    return out


def _atr_floor_bps_by_symbol(
    symbols: Iterable[str], base_cfg: Path | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """Issue #1071 (Pitfall #380-Klasse) — löst je Symbol den konfigurierten ATR-Floor auf
    (``backtest_runner.resolve_atr_floor_bps`` über dieselbe Asset-Class-Auflösungskette wie der
    Worker selbst, #924). Rohmaterial für ``invariants.check_atr_scale_homogeneity``s
    ``atr_floor_binding_studies``-Mechanismus-Unterscheidung: eine Study, deren ``atr_median_bps``
    auf diesem Wert liegt, hat einen Nenner an einer KONFIGURIERTEN Konstante, keiner Preis-
    Beobachtung. Lazy-Import (``backtest_runner`` zieht ``nautilus_trader`` — dieselbe Konvention
    wie ``invariants.check_config_key_registry``). Fail-open ({}, {}) bei einem Importfehler —
    ein Report darf wegen dieser Zusatzauflösung nie crashen.

    Issue #998/#1150 (Katalog #1170, Wiederkehr der #380-Pitfall-Klasse) — Root-Cause: der
    PER-SYMBOL-``except Exception: continue`` verschluckte JEDEN Fehler (u. a.
    ``InstrumentMetadataIncompleteError`` aus ``_resolve_asset_class_for_symbol``, ein
    ``ValueError``-Subtyp, #898) spurlos — ein leeres Ergebnis-Dict war dadurch NICHT von "der
    Floor bindet bei keinem Symbol" unterscheidbar (die eigentliche #1096-Abnahme), sondern
    bedeutete "der Floor ist fuer dieses Symbol UNBEKANNT". Fix: Rueckgabe ist seit diesem Fix ein
    ``(resolved, resolution_errors)``-Tupel; ``resolution_errors[symbol]`` traegt die
    Fehlermeldung fuer jedes Symbol, das NICHT aufgeloest werden konnte. Der Except-Block ist auf
    die konkret erwarteten Fehlerklassen eingegrenzt (``ValueError`` deckt sowohl den direkten
    Raise in ``resolve_atr_floor_bps`` als auch ``InstrumentMetadataIncompleteError`` ab, da
    Letztere ``ValueError`` erbt) — ein Programmfehler (z. B. ``AttributeError`` bei einer
    kaputten Aufrufkette) propagiert seither, statt als "0 von N Symbolen aufgeloest" misszudeuten."""
    try:
        from automation.backtest_runner import resolve_atr_floor_bps, _resolve_asset_class_for_symbol
    except Exception:
        return {}, {}
    cfg_dir = base_cfg or config_dir()
    data = _load_json(cfg_dir / "backtest.json") or {}
    atr_floor_by_asset_class = data.get("atr_floor_bps_by_asset_class") or {}
    out: dict[str, float] = {}
    errors: dict[str, str] = {}
    for symbol in {s for s in symbols if s}:
        try:
            asset_class_key = "DEFAULT"
            if atr_floor_by_asset_class:
                asset_class_key = _resolve_asset_class_for_symbol(symbol)
            out[symbol] = resolve_atr_floor_bps(symbol, atr_floor_by_asset_class, asset_class_key)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
    return out, errors


def _asset_class_by_symbol(symbols: Iterable[str]) -> dict[str, str]:
    """Issue #1011/#1163 (Katalog #1170) — asset_class je Symbol (``instrument_map.json``, über
    ``backtest_runner._resolve_asset_class_for_symbol``, dieselbe Lazy-Import-Konvention wie
    ``_atr_floor_bps_by_symbol``: ``backtest_runner`` zieht ``nautilus_trader``). Rohmaterial für
    ``invariants.check_session_calendar_coherence`` (asset-class-GATED FAIL für EQUITY/COMMODITY).
    Fail-open (``{}``) bei Importfehler; ein Symbol, dessen asset_class nicht auflösbar ist, fehlt
    einfach im Ergebnis (``policy='default'`` statt ``'reject'`` — diese Funktion ist NICHT
    gate-entscheidend für die Kostenauflösung selbst, nur eine Zusatz-Klassifikation für eine
    bereits ``severity='high'``-Invariante; ein unauflösbares Symbol wird schlicht nicht bewertet,
    statt den gesamten Report abzubrechen)."""
    try:
        from automation.backtest_runner import _resolve_asset_class_for_symbol
    except Exception:
        return {}
    out: dict[str, str] = {}
    for symbol in {s for s in symbols if s}:
        try:
            out[symbol] = _resolve_asset_class_for_symbol(symbol, policy="default")
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return out


def _round_trip_cost_bps_by_symbol(symbols: Iterable[str]) -> tuple[dict[str, float], dict[str, str]]:
    """Issue #1072 — löst je Symbol die config-abgeleitete Round-Trip-Kostenbasis (c_rt) auf
    (``backtest_runner._read_default_round_trip_cost_bps``, dieselbe Auflösungskette wie das
    kostenrelative Expectancy-Gate, #684/#775). Rohmaterial für
    ``invariants.check_stop_cost_ratio``. Lazy-Import (``backtest_runner`` zieht
    ``nautilus_trader``, dieselbe Konvention wie ``_atr_floor_bps_by_symbol``). Fail-open
    ({}, {}) bei einem Importfehler.

    Issue #998/#1150 (Katalog #1170) — dieselbe ``resolution_errors``-Erweiterung wie
    ``_atr_floor_bps_by_symbol`` (siehe dortiger Docstring): ``_read_default_round_trip_cost_bps``
    kann ``InstrumentMetadataIncompleteError`` (``ValueError``-Subtyp) propagieren, wenn
    ``spread_bps_by_asset_class`` konfiguriert ist, aber die Asset-Class des Symbols unbekannt
    ist — vor diesem Fix verschluckte der PER-SYMBOL-``except Exception`` das spurlos."""
    try:
        from automation.backtest_runner import _read_default_round_trip_cost_bps
    except Exception:
        return {}, {}
    out: dict[str, float] = {}
    errors: dict[str, str] = {}
    for symbol in {s for s in symbols if s}:
        try:
            out[symbol] = _read_default_round_trip_cost_bps(symbol)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
    return out, errors


def _k_min_bar_range_multiple(base_cfg: Path | None = None) -> float:
    """Issue #1028/#1177 (Katalog #866-2) — ``backtest.json['k_min_bar_range_multiple']``: die
    Mikrostruktur-Untergrenze fuer den ATR-Floor wird als ``k_min_bar_range_multiple ·
    bar_range_median_bps`` gebildet (Default 1.0 — die Stopdistanz mindestens eine Median-
    Bar-Spanne). Fehlt der Key ⇒ 1.0 (rueckwaertskompatibel)."""
    cfg_dir = base_cfg or config_dir()
    data = _load_json(cfg_dir / "backtest.json") or {}
    value = data.get("k_min_bar_range_multiple")
    return float(value) if value is not None else 1.0


def _stamp_atr_floor_bps_derived(
    studies_out: list[dict[str, Any]], *,
    atr_floor_bps_by_symbol: dict[str, float],
    round_trip_cost_bps_by_symbol: dict[str, float],
    min_stop_to_cost_ratio: float,
    k_min_bar_range_multiple: float | None = None,
) -> None:
    """Issue #951/#1117 (Katalog #960) — stempelt ``atr_floor_bps_derived`` (den per Study
    COST-GEKOPPELTEN ATR-Floor, ``backtest_runner.cost_coupled_atr_floor_bps``, #1096 Fix Punkt 1)
    in JEDEN ``studies_out``-Eintrag, mutiert in place. Dieses Feld bleibt exakt der Floor, der in
    ``run_single_backtest_worker`` TATSÄCHLICH SIMULIERT wurde — es ändert sich mit diesem Fix
    NICHT (siehe ``atr_floor_bps_recommended`` unten fuer die diagnostische Erweiterung).

    Root-Cause: ``_atr_floor_bps_by_symbol`` (oben) löst NUR die statische, asset-class-aufgelöste
    Konstante auf (``backtest_runner.resolve_atr_floor_bps``) — der tatsächlich SIMULIERTE Floor
    (``run_single_backtest_worker``, dieselbe ``cost_coupled_atr_floor_bps``-Anhebung) hebt diese
    Konstante zusätzlich auf ``min_stop_to_cost_ratio · c_rt / atr_trailing_multiplier`` an, wenn
    das grösser ist — eine PRO-STUDY-Grösse (über ``atr_trailing_multiplier_median``), die die
    reine Asset-Class-Konstante strukturell unterschätzt. Ohne dieses Feld erschien der Floor im
    Report nur als KONFIGURIERTE Grösse (die Asset-Class-Konstante), nie als die tatsächlich
    ABGELEITETE (Akzeptanzkriterium #951). ``None`` je Study, wenn eine der drei Eingangsgrössen
    (Symbol-Floor, Kostenbasis, ``atr_trailing_multiplier_median``) fehlt — kein Rateergebnis auf
    unvollständigen Eingaben.

    Issue #1028/#1177 (Katalog #866-2) — der so gebildete Floor ist REIN kostengekoppelt: er
    garantiert nur "Stopdistanz >= min_stop_to_cost_ratio · c_rt", nicht "Stopdistanz >= eine
    Median-Bar-Spanne" — ein Stop innerhalb der Bar-Spanne ist kein Verlustlimit, sondern ein
    Rausch-Trigger. Eine ZWEITE, unabhängige Untergrenze (Marktmikrostruktur,
    ``k_min_bar_range_multiple · bar_range_median_bps``, siehe ``_k_min_bar_range_multiple``) wird
    hier zusätzlich berechnet und als ``atr_floor_bps_microstructure``/``atr_floor_bps_recommended``/
    ``atr_floor_source`` ausgewiesen — bewusst NUR diagnostisch (additive Report-Telemetrie), NICHT
    in die tatsächliche Simulation zurückgespeist: ``bar_range_median_bps`` (#1022/#1171) ist ein
    RETROSPEKTIVER Round-Trip-Aggregat (Median waehrend einer bereits geschlossenen Position), zum
    Zeitpunkt der Stop-Platzierung eines NEUEN Trials nicht verfügbar — eine live wirksame
    Mikrostruktur-Untergrenze braucht einen eigenen, VORAUSSCHAUENDEN Bar-Spannen-Schätzer (ein
    Rolling-Fenster über bereits abgeschlossene Bars), was denselben Risikoklasse-Eingriff in den
    Kern-ATR-Schätzer/Simulationspfad ist, den #1096 Fix Punkt 2/3 und #1069 Fix Punkte 2-4 bereits
    bewusst zurückgestellt haben (kein echter Mehrsymbol-Referenzlauf in dieser Sandbox
    verfügbar, um eine Korrektheitsregression im GESAMTEN ATR-Schätzer auszuschliessen)."""
    try:
        from automation.backtest_runner import cost_coupled_atr_floor_bps
    except Exception:
        for r in studies_out:
            r["atr_floor_bps_derived"] = None
            r["atr_floor_bps_microstructure"] = None
            r["atr_floor_bps_recommended"] = None
            r["atr_floor_source"] = "none"
        return
    _k_bar_range = (
        k_min_bar_range_multiple if k_min_bar_range_multiple is not None
        else _k_min_bar_range_multiple())
    for r in studies_out:
        base_floor = atr_floor_bps_by_symbol.get(r.get("symbol"))
        c_rt = round_trip_cost_bps_by_symbol.get(r.get("symbol"))
        # Issue #1029/#1178 (Katalog #866-2) — c_rt als erstklassiges Study-Feld, Rohmaterial fuer
        # Report-Abschnitt 2.4 (stop_exit_slippage_bps NEBEN c_rt, damit ein Leser die Groessenordnung
        # der gemessenen Slippage gegen die Kostenbasis einordnen kann).
        r["round_trip_cost_bps"] = c_rt
        k_median = r.get("atr_trailing_multiplier_median")
        if base_floor is None or c_rt is None or k_median is None:
            r["atr_floor_bps_derived"] = None
        else:
            r["atr_floor_bps_derived"] = round(cost_coupled_atr_floor_bps(
                float(base_floor), atr_trailing_multiplier=float(k_median),
                round_trip_cost_bps=float(c_rt),
                min_stop_to_cost_ratio=min_stop_to_cost_ratio), 4)
        # Issue #1028/#1177 — Mikrostruktur-Schranke, rein diagnostisch (siehe Docstring oben).
        _bar_range = r.get("bar_range_median_bps")
        if _bar_range is not None and k_median:
            r["atr_floor_bps_microstructure"] = round(
                _k_bar_range * float(_bar_range) / float(k_median), 4)
        else:
            r["atr_floor_bps_microstructure"] = None
        _cost_floor = r["atr_floor_bps_derived"]
        _micro_floor = r["atr_floor_bps_microstructure"]
        if _cost_floor is None and _micro_floor is None:
            r["atr_floor_bps_recommended"] = None
            r["atr_floor_source"] = "none"
        elif _micro_floor is None or (_cost_floor is not None and _cost_floor >= _micro_floor):
            r["atr_floor_bps_recommended"] = _cost_floor
            r["atr_floor_source"] = "cost"
        else:
            r["atr_floor_bps_recommended"] = _micro_floor
            r["atr_floor_source"] = "bar_range"


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


def _trade_amount_pct_parity_factor(base_cfg: Path | None = None) -> float:
    """Issue #1014/#1166 (Katalog #1170) — ``backtest.json['live_risk']['trade_amount_pct_parity_
    factor']``, der EXPLIZITE, dokumentierte Faktor zwischen ``strategy_defaults.json``'s
    ``trade_amount_pct`` und ``max_symbol_exposure_fraction · 100`` (siehe ``invariants.check_
    sizing_parity_backtest_vs_allocator``-Docstring). Default ``1.0`` (Parität, das Verhalten
    dieser Prüfung vor #1014/#1166) — fehlt der Key, bleibt die alte Erwartung (Backtest ==
    Allocator) bit-identisch bestehen."""
    cfg_dir = base_cfg or config_dir()
    data = _load_json(cfg_dir / "backtest.json") or {}
    value = (data.get("live_risk") or {}).get("trade_amount_pct_parity_factor")
    return float(value) if value is not None else 1.0


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

    Jede Stufe ist ``{stage, passed, detail}``.

    Issue #1001/#1153 (Katalog #1170, P0) — Root-Cause: die VOR diesem Fix EINZIGE Quelle war eine
    ABLEITUNG ("eine REJECTED Study endet die Kette an der tatsaechlich blockierenden Stufe;
    vorangehende Stufen gelten als bestanden, weil der Confirm-Pfad eine Stufe erst erreicht,
    nachdem die vorherigen bestanden sind") — eine Annahme, KEINE Messung, und nachweislich falsch
    fuer B-8 (10 Studies trugen ``{"stage": "holdout", "passed": true}``, obwohl das Holdout-Gate
    selbst scheiterte, weil ein SPAETER geprueftes Kriterium wie PBO/Boundary die Attribution
    gewann — dieselbe Praezedenz-Luecke, die #1000/#1152 in ``confirm.py`` behoben hat).

    Primaere Quelle ist seither ``proposal['stage_results']`` (``confirm.
    confirm_per_symbol_promotion``s TATSAECHLICH gemessene ``{stage: {passed, detail}}``-Eintraege,
    #1153 Fix Punkt 1): eine Stufe, die dort FEHLT, wurde nie ausgewertet und traegt
    ``passed=None`` ("nicht belegt"), NIE ein stillschweigendes ``True``. Nur Alt-Proposals OHNE
    dieses Feld (vor #1153 exportiert) fallen auf die alte Ableitung zurueck — dann aber ebenfalls
    mit ``passed=None`` statt ``True`` fuer nicht nachweislich gemessene Stufen (#1153 Fix Punkt 2),
    ausser bei einem tatsaechlich PROMOTETEN Kandidaten (``promote=True`` ist selbst ein starker,
    unabhaengiger Beleg, dass jede Stufe bestand)."""
    promote = proposal.get("status") in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    route = proposal.get("promotion_route")
    stage_results = proposal.get("stage_results")

    chain: list[dict[str, Any]] = []
    is_gate_passed = bool(n_eligible > 0 or route == "global_default_on_symbol" or promote)
    chain.append({
        "stage": "is_gate", "passed": is_gate_passed,
        "detail": proposal.get("dominant_is_rejection_detail") if not is_gate_passed else None,
    })
    if not is_gate_passed:
        return chain

    if stage_results:
        # Issue #1001/#1153 Fix Punkt 2 — MESSUNG statt Ableitung: eine im Record fehlende Stufe
        # wurde nachweislich nie ausgewertet.
        for stage in _DECISION_STAGE_NAMES[1:]:
            entry = stage_results.get(stage)
            if entry is None:
                chain.append({"stage": stage, "passed": None, "detail": None})
            else:
                chain.append({
                    "stage": stage, "passed": entry.get("passed"), "detail": entry.get("detail"),
                })
        return chain

    # Legacy-Fallback (Alt-Proposal ohne ``stage_results``) — dieselbe Ableitung wie vor #1153,
    # aber ``passed=None`` (statt ``True``) fuer jede Stufe, die NICHT nachweislich (ueber
    # ``promote=True`` oder die terminale Ablehnung selbst) belegt ist.
    holdout_detail = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    failing_stage = _STAGE_FOR_REJECT_DETAIL.get(holdout_detail) if holdout_detail else None
    for stage in _DECISION_STAGE_NAMES[1:]:
        if failing_stage == stage:
            chain.append({"stage": stage, "passed": False, "detail": holdout_detail})
            break
        detail = "GLOBAL_DEFAULT" if (stage == "confirm_or_selection"
                                      and route == "global_default_on_symbol") else None
        chain.append({"stage": stage, "passed": (True if promote else None), "detail": detail})
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


def _pooled_mean_of_trial_field(
    trial_attrs: list[dict], *, mean_field: str, count_field: str,
) -> float | None:
    """Issue #1097 (Katalog #930) — GEPOOLTER, trade-gewichteter Study-Mittelwert aus je-Trial
    Mittelwert + Stichprobengrösse: Σ(mean_i · n_i) / Σ(n_i).

    Root-Cause #1097: ``_median_of_trial_field`` bildet den MEDIAN der TRIAL-Mittelwerte — eine
    Study-Kennzahl, die NICHT kommensurabel mit einer SUMME über dieselben Trials ist (z. B.
    ``oos_n_trailing_stop_losses``, bereits als ``sum(...)`` gebildet). Die Teilmengen-Schranke
    ``mean_alle >= (n_stop_losses/n_exits) · mean_stop`` (für nicht-negative Verluste zwingend
    gültig) war deshalb in 12 von 56 Studies verletzt — nicht, weil die Simulation inkohärent war,
    sondern weil zwei verschiedene Aggregationsarten (Median vs. Summe) über dieselbe Trial-Kohorte
    als vergleichbar behandelt wurden (Wiederkehr von Pitfall #304).

    Jeder Trial trägt seinen EIGENEN Mittelwert (``mean_field``) UND seine EIGENE Stichprobengrösse
    (``count_field``, z. B. ``oos_n_losses``/``oos_n_trailing_stop_losses``) — beide je Trial bereits
    korrekt (der Trial-Mittelwert selbst ist bereits über die Trades DIESES Trials gepoolt, siehe
    ``backtest_runner._aggregate_exit_telemetry``). ``None``, wenn kein Trial beide Felder trägt
    oder die Gesamtzahl 0 ist (kein stiller Default, der eine leere Kohorte verdeckt)."""
    weighted_sum = 0.0
    total_n = 0
    for a in trial_attrs or []:
        mean_val = a.get(mean_field)
        n_val = a.get(count_field)
        if mean_val is None or not isinstance(n_val, (int, float)) or isinstance(n_val, bool) or n_val <= 0:
            continue
        weighted_sum += float(mean_val) * float(n_val)
        total_n += int(n_val)
    if total_n <= 0:
        return None
    return weighted_sum / total_n


def _count_jsonl_events(path: Path | None, event_types: set[str]) -> dict[str, int]:
    """Issue #1098 (Katalog #931) — zählt je ``event_type`` in ``event_types`` die tatsächlich in
    der ``.events.jsonl``-Sidecar-Datei unter ``path`` vorhandenen Zeilen (eine valide JSON-Zeile
    je Ereignis, siehe ``log_manager._append_jsonl_sidecar``). Dient
    ``invariants.check_event_stream_completeness`` als "actual"-Seite gegen das vom Sweep
    geschriebene ``EVENTS_MANIFEST``-Ereignis (dessen ``expected_*``-Zahlen UNABHÄNGIG von dieser
    Datei aus ``studies_out`` berechnet werden — kein Zirkelschluss).

    Robust gegen einzelne defekte Zeilen (übersprungen statt die gesamte Zählung abzubrechen — ein
    Betriebssystem-Crash mitten in einem ``os.write()`` kann theoretisch eine unvollständige letzte
    Zeile hinterlassen, das darf die Zählung der VORHERIGEN, vollständigen Zeilen nicht verhindern).
    ``path is None`` (Logger nie via ``setup_bot_logging`` initialisiert) oder die Datei existiert
    nicht ⇒ alle Zählungen 0."""
    counts = {et: 0 for et in event_types}
    if path is None or not path.exists():
        return counts
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                et = event.get("event_type")
                if et in counts:
                    counts[et] += 1
    except OSError:
        _log.warning("[#1098] events.jsonl (%s) nicht lesbar für die Vollständigkeitsprüfung.",
                     path, exc_info=True)
    return counts


def _read_jsonl_events(path: Path | None, event_type: str) -> list[dict]:
    """Issue #1099 (Katalog #932) — liest alle Zeilen mit ``event_type`` aus der ``.events.jsonl``-
    Sidecar-Datei unter ``path`` und gibt ihre VOLLEN (bereits geparsten) Payloads zurück — im
    Gegensatz zu ``_count_jsonl_events`` (das nur zählt) braucht ``_champions_summary`` hier den
    ``skipped_reason``/``applied``-Inhalt jedes ``CHAMPION_WRITEBACK``-Ereignisses. Dieselbe
    Robustheit wie ``_count_jsonl_events``: einzelne defekte Zeilen werden übersprungen,
    ``path is None``/fehlende Datei ⇒ leere Liste."""
    events: list[dict] = []
    if path is None or not path.exists():
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event_type") == event_type:
                    events.append(event)
    except OSError:
        _log.warning("[#1099] events.jsonl (%s) nicht lesbar für die Attempt-Rekonstruktion.",
                     path, exc_info=True)
    return events


# Issue #984/#1138 (Katalog #986, Pitfall #409 in AGENTS.md) — bewusst NICHT verdrahtete Checks
# (dokumentierter Verzicht statt Vergessen). ``check_invariant_registry_wired`` selbst braucht
# KEINEN Eintrag hier: sein eigener Aufruf unten (``_inv.check_invariant_registry_wired(...)``)
# erzeugt bereits eine textuelle Aufrufstelle in DIESER Datei.
#
# ``check_live_exposure_budget`` — konsumiert ``LIVE_EXPOSURE_SNAPSHOT``-Events
# (``momentum_ls_allocator.py``, Issue #999 Fix Punkt 4), die bereits emittiert werden, aber von
# KEINEM Konsumenten zu einem Invarianten-Urteil zusammengefuehrt werden (derselbe Fehlerklasse-
# Fund wie die fuenf urspruenglichen #1138-Checks, hier aber auf der LIVE-Trading-Seite, nicht im
# Backtest-/Report-Pfad dieses Issue-Batches — eine eigene Verdrahtung braucht einen Live-Event-
# Aggregator, der ausserhalb dieser Sitzung liegt). Dokumentierter Verzicht, kein Vergessen.
#
# ``check_cost_model_resolution``/``check_cost_model_floor`` (Issue #999/#1151, Katalog #1170) —
# beide lesen ``COST_MODEL_RESOLVED``-Events, die AUSSCHLIESSLICH im isolierten Worker-Prozess
# unter dem Logger-Namen ``"backtest_worker"`` emittiert werden; der Report-Pfad liest jedoch den
# Sidecar des ``"optimizer"``-Loggers im Hauptprozess — zwei disjunkte Transportwege, die Checks
# sehen STRUKTURELL IMMER 0 Events. Siehe die ausfuehrliche Begruendung an der (entfernten)
# ehemaligen Aufrufstelle in ``_build_report`` (Kommentar bei der frueheren #984/#1138-Verdrahtung).
#
# ``check_data_span`` (``backtest_runner.py``, Issue #1015/#1167, Katalog #1170) — emittiert seit
# diesem Fix symmetrisch ein ``INVARIANT_STREAM_RESULT``-Event (PASS UND FAIL, ``source="worker"``), aber
# unter demselben ``"backtest_worker"``-Logger wie ``COST_MODEL_RESOLVED`` oben — DERSELBE
# strukturell disjunkte Transportweg, keine neue Ausnahme.
#
# ``check_deployment_gate_completeness`` (``daily_orchestrator.py`` Phase 5, Issue #1015/#1167,
# Katalog #1170) — emittiert seit diesem Fix symmetrisch ein ``INVARIANT_STREAM_RESULT``-Event (``source=
# "orchestrator"``), aber bewertet die Whitelist EINES Phase-5-Laufs (``whitelist_tournament.
# json``, potenziell mehrere Sweep-``run_id``s ueberspannend), nicht die Study-Population eines
# EINZELNEN Sweep-Reports — es gibt kein bestimmtes ``run.json``, in dessen ``invariant_checks``
# dieses Ergebnis eindeutig einsortiert werden koennte, ohne eine Kohorten-Fiktion zu erfinden
# (dieselbe Kohorten-Disziplin wie #941/#1107, siehe ``cohort``-Feld-Docstring in ``invariants.py``).
#
# ``check_invariant_coverage`` selbst (Issue #1015/#1167) — anders als ``check_invariant_registry_
# wired`` (oben, Zeile ~612: dessen STATISCHER Text-Scan trifft die eigene Aufrufstelle automatisch
# mit) arbeitet dieser Check gegen einen zum Auswertungszeitpunkt bereits FERTIGEN Schnappschuss
# von ``invariant_checks`` — sein EIGENES Ergebnis existiert per Konstruktion noch nicht, wenn er
# ausgewertet wird (es WIRD der naechste Eintrag). Ohne diesen Eintrag würde er sich selbst
# permanent als "fehlend" melden — ein Placebo-Fund ueber die eigene Nichtexistenz-zum-
# Messzeitpunkt, keine echte Beobachtung.
_DELIBERATELY_UNWIRED_INVARIANT_CHECKS: tuple[str, ...] = (
    "check_live_exposure_budget",
    "check_cost_model_resolution",
    "check_cost_model_floor",
    "check_data_span",
    "check_deployment_gate_completeness",
    "check_invariant_coverage",
)


def _invariant_registry_wiring_check() -> "_inv.InvariantResult":
    """Issue #984/#1138 — Introspektion: jede in ``invariants.py`` definierte ``check_*``-Funktion
    gegen ihre tatsaechlichen Aufrufstellen. Durchsucht die Kern-Report-/Sweep-/Confirm-Module
    UND die bekannten Nicht-Optimizer-Konsumenten (``daily_orchestrator.py`` ruft z. B.
    ``check_deployment_gate_completeness`` aus Phase 5 auf, ausserhalb des Optimizer-Pakets).
    Eine Aufrufstelle wird über das Regex-Muster ``(?<![\\w]) check_<name> \\(`` erkannt — deckt
    sowohl namespaced Aufrufe (``_inv.check_xxx(...)``) als auch Direktimporte
    (``from ...invariants import check_xxx``, z. B. ``daily_orchestrator.py``) ab, OHNE einen
    reinen Prosa-Verweis (` ``check_xxx`` ` ohne folgende Klammer) oder einen laengeren Namen mit
    ``check_xxx`` als Suffix faelschlich als Treffer zu zaehlen."""
    import inspect
    import re
    defined = [
        name for name, obj in inspect.getmembers(_inv, inspect.isfunction)
        if name.startswith("check_") and getattr(obj, "__module__", None) == _inv.__name__
    ]
    optimizer_dir = Path(__file__).resolve().parent
    automation_dir = optimizer_dir.parent
    candidate_paths = [optimizer_dir / f for f in ("report.py", "sweep.py", "confirm.py")]
    candidate_paths += [automation_dir / f for f in
                        ("daily_orchestrator.py", "live_risk.py", "momentum_ls_allocator.py")]
    combined_source = ""
    for path in candidate_paths:
        try:
            combined_source += path.read_text("utf-8")
        except OSError:
            continue
    wired = [
        name for name in defined
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\(", combined_source)
    ]
    return _inv.check_invariant_registry_wired(
        defined, wired, deliberately_unwired=_DELIBERATELY_UNWIRED_INVARIANT_CHECKS)


def _all_defined_check_names() -> list[str]:
    """Issue #1015/#1167 (Katalog #1170) — ALLE ``check_*``-Funktionsdefinitionen über das
    GESAMTE ``automation``-Paket (nicht nur ``invariants.py``, siehe ``_invariant_registry_
    wiring_check``-Docstring oben): neun der bei Issue-Erstellung definierten Checks leben in
    ``reward.py``/``run_optimization.py``/``wallclock_guard.py``/``disk_guard.py``/
    ``sweep_diagnostics.py``/``backtest_runner.py``, nicht in ``invariants.py``. Text-Scan (KEIN
    Import!) — ``backtest_runner.py`` hängt zur Laufzeit an ``nautilus_trader`` (in manchen
    Testumgebungen nicht installiert); ein Import würde diese reine Introspektion an eine
    Abhängigkeit koppeln, die mit der Frage "welche Funktionen sind DEFINIERT" nichts zu tun hat
    (dasselbe Prinzip wie ``_invariant_registry_wiring_check``s eigener Text-Scan für "wired").
    Rückgabe: sortierte, deduplizierte Namen."""
    import re
    optimizer_dir = Path(__file__).resolve().parent
    automation_dir = optimizer_dir.parent
    candidate_paths = [
        optimizer_dir / f for f in
        ("invariants.py", "reward.py", "run_optimization.py", "wallclock_guard.py",
         "disk_guard.py", "sweep_diagnostics.py")
    ] + [automation_dir / "backtest_runner.py"]
    names: set[str] = set()
    for path in candidate_paths:
        try:
            source = path.read_text("utf-8")
        except OSError:
            continue
        names.update(re.findall(r"(?m)^def (check_[A-Za-z0-9_]+)\(", source))
    return sorted(names)


def _read_external_invariant_results() -> list[dict]:
    """Issue #1015/#1167 (Katalog #1170) — liest ``INVARIANT_STREAM_RESULT``-Events aus dem "optimizer"-
    Sidecar (derselbe, den ``BAR_QUALITY_PROFILE``/``CHAMPION_WRITEBACK`` bereits nutzen): sechs
    der neun in #1167 benannten Checks laufen ausserhalb von ``_build_report`` (Sweep-
    Hauptschleife, ``run_optimization.py`` — beide im SELBEN Prozess wie ``report.py``, siehe
    dortige ``logging.getLogger("optimizer")``-Aufrufe), melden ihr Urteil seit diesem Fix aber
    als eigenes Event statt als in-process ``InvariantResult``-Objekt. Die verbleibenden drei
    (``check_live_exposure_budget``/``check_cost_model_resolution``/``check_cost_model_floor``/
    ``check_data_span``/``check_deployment_gate_completeness`` — fünf, siehe
    ``_DELIBERATELY_UNWIRED_INVARIANT_CHECKS``) landen in strukturell disjunkten Sidecars und
    erscheinen hier folgerichtig NICHT. ``None``-Sidecar (kein echter Sweep-Lauf, z. B. in Tests)
    ⇒ leere Liste, exakt wie ``_read_jsonl_events`` selbst."""
    events = _read_jsonl_events(jsonl_sidecar_path(_log.name), "INVARIANT_STREAM_RESULT")
    results = []
    for event in events:
        d = {k: v for k, v in event.items()
             if k not in ("event_type", "timestamp_utc", "run_id", "strategy", "symbol",
                          "study_name")}
        d.setdefault("name", d.get("check"))
        d.setdefault("check", d.get("name"))
        d.setdefault("severity", "medium")
        d.setdefault("scope", "sweep")
        results.append(d)
    return results


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


def _median_of_sampled_param(
    trial_attrs: list[dict], param: str, *, default_from: dict | None = None,
) -> tuple[float | None, str]:
    """Issue #897 Fix 3 — Median eines GESAMPELTEN Suchraum-Parameters (``sampled_params[param]``)
    über eine Study. Symmetrisch zu ``_median_of_trial_field``, aber für den Config-Wert statt der
    realisierten Telemetrie (Eingangsgrösse für ``check_effective_stop_distance``).

    Issue #997/#1149 (Katalog #1170) — ``SmaCrossoverStrategy`` sampelt ``atr_trailing_multiplier``/
    ``max_bars_in_trade`` NICHT (``spaces.py`` sampelt für diese Strategie nur ``sma_period``/
    ``cooldown_bars``), obwohl die Basisklasse (``HourlyStrategyBase``) trotzdem einen ATR-
    Trailing-Stop mit dem DEFAULT-Multiplikator anwendet — VOR diesem Fix kannte diese Funktion
    keinen Default-Fallback, also blieb das Feld strukturell ``None`` fuer jede Strategie, die den
    Parameter nicht sampelt, und die gesamte Kalibrierkette (``atr_floor_bps_derived``,
    ``realized_stop_loss_ratio*``, ``check_stop_cost_ratio``-Kandidatur) fiel fuer sie aus.

    ``default_from`` (optional, i.d.R. der ``strategy_defaults.json``-Eintrag DIESER Strategie) —
    fehlt der Parameter im Suchraum (keine ``sampled_params``-Werte), aber ``default_from[param]``
    existiert, wird DIESER (der tatsaechlich von ``HourlyStrategyBase`` angewendete) Wert
    verwendet. Rueckgabe ist ein ``(value, source)``-Tupel, ``source ∈ {"sampled",
    "strategy_default", "unavailable"}`` — KEIN stiller Ratewert: fehlt der Parameter in BEIDEN
    Quellen, bleibt ``value=None`` und ``source="unavailable"`` (kein erfundener Wert, siehe
    Docstring-Akzeptanzkriterium 3)."""
    values = [
        (a.get("sampled_params") or {}).get(param) for a in (trial_attrs or [])
        if (a.get("sampled_params") or {}).get(param) is not None
    ]
    if values:
        return statistics.median(values), "sampled"
    default_value = (default_from or {}).get(param)
    if default_value is not None:
        return float(default_value), "strategy_default"
    return None, "unavailable"


def _study_record(proposal: dict, study,
                  tournament_cfg: dict | None = None, *,
                  guard_dominance_threshold: float | None = None,
                  symbol_bar_quality_cache: dict | None = None,
                  run_id: str | None = None,
                  trials_override: list | None = None,
                  ) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743).

    ``symbol_bar_quality_cache`` (Issue #923) — vom Aufrufer EINMAL gelesenes
    ``sweep.read_symbol_bar_quality_cache(WORK)``-Ergebnis, hier nur je Symbol nachgeschlagen
    (kein I/O in dieser Funktion selbst). ``None``/kein Eintrag für ``proposal['symbol']`` ⇒

    ``run_id`` (Issue #1015, Katalog #858, Fix Punkt 1) — durchgereicht an
    ``compute_budget_execution``, damit ``budget_executed_fraction`` ausschliesslich Trials DIESES
    Laufs zählt, statt einer ungepurgten Study mehrerer Läufe (siehe dortiger Docstring). ``None``
    (Default, z. B. Legacy-/Test-Aufrufer) ⇒ bit-identisches Alt-Verhalten (alle Study-Trials).
    derselbe ``_contracts.BAR_SECONDS_DEFAULT``-Fallback wie vor #923.

    ``trials_override`` (Issue #1021/#1196 Fix 4.1/Akzeptanzkriterium 2) — wenn gesetzt, ERSETZT
    diese Liste ``study.trials`` als Grundgesamtheit JEDER Study-Metrik unten (n_trials,
    n_eligible, ...), statt sie aus dem vollen (ggf. per Warm-Start ueber mehrere Laeufe
    gewachsenen) Store zu lesen. ``_build_report`` uebergibt hier ``_own_run_trials``, sobald eine
    Study nachweislich Trials eines VORLAUFS enthaelt (sequenzielle Store-Wiederverwendung, siehe
    ``cross_study.store_reuse``) — der Report dieses Laufs zaehlt dann ausschliesslich SEIN
    eigenes Budget, nicht die kumulierte Store-Groesse. ``None`` (Default) ⇒ bit-identisches
    Alt-Verhalten (alle ``study.trials``)."""
    trials = (
        list(trials_override) if trials_override is not None
        else list(getattr(study, "trials", None) or []) if study is not None else []
    )
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
    # Issue #1025/#1174 (Katalog #866-2) — VORGEZOGEN (war urspruenglich weiter unten, neben den
    # inference_diagnostics-Aggregaten): ``n_ineligible_measured`` unten baut direkt darauf auf,
    # statt es ueber eine Subtraktion aus einer anderen Grundgesamtheit zu erschliessen (siehe dort).
    # Issue #976 — je Study, wie oft jeder is_rejection_detail-Code über ALLE Trials auftrat.
    # Rohmaterial für invariants.check_window_unreachable_rate.
    is_rejection_detail_counts: dict[str, int] = {}
    for a in trial_attrs:
        detail = a.get("is_rejection_detail")
        if detail:
            is_rejection_detail_counts[detail] = is_rejection_detail_counts.get(detail, 0) + 1
    # Issue #917 Fix 4 — 'ineligible' in zwei disjunkte Klassen zerlegen: nur EINE davon ist eine
    # Aussage über die Strategie. ineligible_unmeasurable zählt REJECT_OOS_STATISTIC_UNAVAILABLE
    # (#917) — ein Gate lief auf einer undefinierten Grösse, keine Messung fand statt.
    # Issue #1025/#1174 — denselben PRUNED-Ausschluss wie n_evaluable angewendet (zip mit trials):
    # vorher lief dieser Zaehler ueber die volle trial_attrs-Grundgesamtheit, n_evaluable dagegen
    # bereits ueber die PRUNED-bereinigte — zwei verschiedene Nenner unter demselben Namen.
    n_ineligible_unmeasurable = sum(
        1 for t, a in zip(trials, trial_attrs)
        if a.get("oos_evaluated") is True and a.get("oos_eligible") is not True
        and a.get("is_rejection_detail") == "REJECT_OOS_STATISTIC_UNAVAILABLE"
        and getattr(t, "state", None) != _pruned_state
    )
    # Issue #1025/#1174 (Katalog #866-2) — Root-Cause: ``n_evaluable`` (PRUNED-bereinigt) und die
    # vormalige ``n_ineligible_unmeasurable``-Zaehlung (NICHT PRUNED-bereinigt) liefen ueber ZWEI
    # verschiedene Grundgesamtheiten; ``max(0, n_evaluable - n_eligible - n_ineligible_
    # unmeasurable)`` klemmte die daraus resultierende NEGATIVE Differenz still auf 0, statt den
    # Kohortenbruch zu melden (SqueezeBreakout: 71 - 20 - 78 = -27, ausgewiesen als 0 statt der
    # korrekten 51). Fix: ``n_ineligible_measured`` wird DIREKT aus ``is_rejection_detail_counts``
    # gebildet (Summe aller Codes ausser den drei "keine Messung fand statt"-Sentinels) statt
    # subtrahiert — eine Zaehlgroesse wird gezaehlt, nicht als Rest einer Subtraktion erschlossen
    # (Pitfall #424 in AGENTS.md).
    n_ineligible_measured = sum(
        v for k, v in is_rejection_detail_counts.items()
        if k not in (
            IS_REJECTION_NONE,
            "REJECT_OOS_STATISTIC_UNAVAILABLE",
            "REJECT_OOS_NOT_EVALUATED",
            "REJECT_OOS_WINDOW_UNREACHABLE",
        )
    )
    # Issue #862 — Median der informativen Periodenzahl über die oos_evaluated Trials dieser
    # Study (Rohmaterial für invariants.check_guard_reference_coherence auf Report-Ebene).
    _n_periods_values = [
        a["oos_n_periods"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_n_periods")
    ]
    oos_n_periods_median = statistics.median(_n_periods_values) if _n_periods_values else None
    # Issue #1011/#1163 (Katalog #1170) — Study-Median der Bar-Achsen-Dichte (siehe
    # backtest_runner._bar_calendar_telemetry-Docstring), Rohmaterial für invariants.check_
    # session_calendar_coherence. Dieselbe oos_evaluated-Kohorte/Median-Konvention wie oben.
    _bars_per_calendar_day_values = [
        a["oos_bars_per_calendar_day"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_bars_per_calendar_day") is not None
    ]
    bars_per_calendar_day_median = (
        statistics.median(_bars_per_calendar_day_values) if _bars_per_calendar_day_values else None)
    _session_coverage_values = [
        a["oos_session_coverage_fraction"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_session_coverage_fraction") is not None
    ]
    session_coverage_fraction_median = (
        statistics.median(_session_coverage_values) if _session_coverage_values else None)
    coherence_violations = sum(1 for a in trial_attrs if a.get("oos_coherence_violation") is True)
    # Issue #1025/#1174 — is_rejection_detail_counts wird jetzt WEITER OBEN berechnet (siehe dort,
    # n_ineligible_measured baut direkt darauf auf).
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
        # Issue #958/#1124 (Katalog #960) — "Ohne Evidenz kein Veto": jeder REJECTED_BOUNDARY_
        # SOLUTION/HOLD_BOUNDARY_UNRESOLVED-Ausgang muss einen benannten Parameter mit Wert und
        # beiden Bandgrenzen tragen.
        _inv.check_boundary_veto_has_evidence(proposal),
        # Issue #1004 (Katalog #858, Fix Punkt 4) — keine Promotion darf auf einer zensierten/
        # gecappten Kennzahl beruhen (z. B. profit_factor_censored durch profit_factor_cap oder
        # einen degenerierten Bruttoverlust-Nenner).
        _inv.check_censored_statistic_in_decision(proposal, holdout_metrics),
        # Issue #813 — deflation_cluster_coverage < 0.9 ist ein Invarianten-FAIL: die familienweite
        # Decluster-Matrix sieht dann nur einen Bruchteil der gezaehlten (oos_evaluated) Kandidaten.
        _inv.check_deflation_cluster_coverage(holdout_metrics),
        _inv.check_rejection_chain_completeness(
            proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics,
            tournament_config=tournament_cfg),
        # Issue #1035/#1184 Akzeptanzkriterium 1 — keine decision_chain-Stufe darf den detail-Code
        # einer ANDEREN Stufe tragen (Regressionswächter gegen den geerbten Boundary/Holdout-
        # Detail-Bug).
        _inv.check_decision_chain_stage_detail_isolation(decision_chain),
        _inv.check_reward_term_variance(trial_attrs),
        # Issue #984/#1138 (Katalog #986) — #822-Regressionswaechter, bislang null Aufrufstellen
        # ausserhalb von invariants.py/tests/ (Pitfall #409 in AGENTS.md).
        _inv.check_family_n_statistic_coverage(
            trial_attrs, deflation_n_family_raw=holdout_metrics.get("deflation_n_family_raw"),
            # Issue #1008/#1160 (Katalog #1170) — dieselbe ``holdout_metrics``-Quelle wie
            # ``deflation_n_family_raw`` oben; bewacht, dass die beiden Vokabulare (Quelle/Skip-
            # Grund) nie unter demselben Feldnamen vermischt werden.
            deflation_n_family_source=holdout_metrics.get("deflation_n_family_source"),
            deflation_skipped_reason=holdout_metrics.get("deflation_skipped_reason")),
        # Issue #984/#1138 — dieselbe trial_gate_deltas-Kohorte wie gate_collinearity_unconsolidated
        # oben, jetzt zusaetzlich gegen die Entscheidungspflicht (#907) geprueft: jedes kollineare
        # Gate-Paar dieser Study braucht einen dokumentierten Eintrag in tournament.json
        # ['gate_collinearity_accepted_pairs'], sonst FAILt dieser Check blocking.
        # Issue #1017/#1169 (Katalog #1170) — liest seither NICHT mehr die rohe, ungefilterte
        # gate_rank_correlation_matrix, sondern gate_correlations_requiring_decision: ein Paar mit
        # einem gate_consolidation_protected-Mitglied (z. B. max_drawdown) braucht STRUKTURELL nie
        # einen Entscheidungs-Eintrag (die Schutzliste IST die Entscheidung, siehe dortiger
        # Docstring) — die volle, ungefilterte Matrix bleibt unveraendert forensische #742-Report-
        # Telemetrie (cross_study, siehe an anderer Stelle).
        _inv.check_gate_collinearity_decision_required(
            _reward.gate_correlations_requiring_decision(trial_gate_deltas, tournament_cfg),
            threshold=float((tournament_cfg or {}).get("gate_collinearity_threshold", 0.90)),
            accepted_pairs=(tournament_cfg or {}).get("gate_collinearity_accepted_pairs"),
            policy=str((tournament_cfg or {}).get("gate_collinearity_policy", "require_decision"))),
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
        # Issue #1025/#1174 (Katalog #866-2, Pitfall #424 in AGENTS.md) — Regressionswaechter
        # gegen eine erneute Divergenz von n_eligible/n_ineligible_measured/n_ineligible_
        # unmeasurable/n_evaluable, seit ``n_ineligible_measured`` nicht mehr per Subtraktion
        # erschlossen wird (siehe dortiger Feldkommentar).
        _inv.check_ineligible_cohort_partition_identity({
            "n_trials": n_trials, "n_evaluable": n_evaluable, "n_eligible": n_eligible,
            "n_ineligible_measured": n_ineligible_measured,
            "n_ineligible_unmeasurable": n_ineligible_unmeasurable,
        }),
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
    #
    # Issue #1035/#1184 Fix Punkt 2 (Katalog #1189) — umbenannt von ``winner_outside_default_
    # bounds``: der alte Name suggerierte eine allgemeine Bounds-Diagnose, ist aber STRUKTURELL nur
    # dann ueberhaupt erreichbar, wenn ein aktiver #761-Bounds-Override den Suchraum bereits ueber
    # die kuratierten Default-Bänder hinaus geweitet hat (``_default_bounds`` bleibt die kuratierte
    # Referenz, der Gewinner samplet aus dem GEWEITETEN, ``active``-Raum — siehe run_optimization.
    # _boundary_hit_analysis-Docstring fuer dieselbe active/default-Unterscheidung). Diese Zeile ist
    # daher eine STRIKTE (nicht die 2%-tolerante ``boundary_veto_evidence``-)Teilmenge und dient als
    # eigene Report-Zeile: "der Override hat bereits produktiv gewirkt", unterscheidbar von der
    # blossen Naehe zum (unveraenderten) Default-Rand, die das #622/#763-Veto selbst treibt.
    winner_outside_default_bounds_after_override: dict[str, list] = {}
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
                    winner_outside_default_bounds_after_override[_param] = [_value, [_lo, _hi]]
        except Exception:
            winner_outside_default_bounds_after_override = {}

    # Issue #997/#1149 (Katalog #1170) — strategieeigener strategy_defaults.json-Eintrag als
    # Default-Fallback fuer nicht gesampelte Stop-Parameter (siehe _median_of_sampled_param).
    _strategy_defaults_entry = (
        _load_json(config_dir() / "strategy_defaults.json") or {}
    ).get(proposal.get("strategy")) or {}
    _atr_trailing_multiplier_median, _atr_trailing_multiplier_median_source = (
        _median_of_sampled_param(
            trial_attrs, "atr_trailing_multiplier", default_from=_strategy_defaults_entry))
    _max_bars_in_trade_median, _max_bars_in_trade_median_source = _median_of_sampled_param(
        trial_attrs, "max_bars_in_trade", default_from=_strategy_defaults_entry)

    # Issue #1007/#1159 (Katalog #1170) — siehe ``deflation_n_family_frozen``-Feldkommentar unten:
    # eine rohe ``0`` wird beim Export auf ``None`` + Skip-Grund abgebildet, nie als Zahl exportiert.
    _deflation_n_family_frozen_raw = study_user_attrs.get("deflation_n_family_frozen")
    _family_membership_raw = study_user_attrs.get("family_membership")

    # Issue #1023/#1172 (Katalog #866-2) — Stichprobengroesse VOR dem Median berechnet, damit
    # ``stop_exit_fill_lag_bars`` auf ``None`` faellt, solange kein einziger Trial dieser Study
    # nachweisliche Fill-Lag-Telemetrie traegt, statt eine ``0,0``-Latenz zu suggerieren, die von
    # "nie gemessen" ununterscheidbar waere (Tri-State-Mechanik wie #995/#1147).
    _n_ts_exits_with_fill_lag = sum(
        int(a.get("oos_n_trailing_stop_exits_with_fill_lag_telemetry") or 0) for a in trial_attrs)

    record = {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        # Issue #1067 — leer, wenn der Gewinner innerhalb des Default-Suchbands liegt (der weit
        # überwiegende Regelfall, bit-identisch zum Pre-#1067-Bericht).
        "winner_outside_default_bounds_after_override": winner_outside_default_bounds_after_override or None,
        "n_trials": n_trials,
        "n_evaluable": n_evaluable,
        "n_selection_statistic_available": n_selection_statistic_available,
        # Issue #917 Fix 4 — disjunkte Zerlegung der evaluierten, nicht-eligiblen Trials.
        "n_ineligible_measured": n_ineligible_measured,
        "backtest_ms_median": backtest_ms_median,
        # Issue #1038 (Katalog #866) — Σ tatsaechlicher Backtest-CPU-Zeit dieser Study (Rohmaterial
        # fuer report._cpu_utilisation_backtest): anders als study_wallclock_s (Wanduhrzeit,
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
        # Issue #1025/#1174 Akzeptanzkriterium 3 — der Rest, der bei einer korrekten Zerlegung 0
        # sein MUSS (n_evaluable == n_eligible + n_ineligible_measured + n_ineligible_
        # unmeasurable); vor diesem Fix von ``max(0, …)`` in ``n_ineligible_measured`` verschluckt.
        # ``check_ineligible_cohort_partition_identity`` (invariants.py) prueft dieses Feld auf 0.
        "n_ineligible_cohort_residual": (
            n_evaluable - n_eligible - n_ineligible_measured - n_ineligible_unmeasurable),
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
        # Barriere-Wartezeit je Symbol) und report._worker_occupancy_wallclock/_symbol_barrier_wait.
        "study_started_at_utc": study_user_attrs.get("study_started_at_utc"),
        "study_ended_at_utc": study_user_attrs.get("study_ended_at_utc"),
        "study_wallclock_s": study_user_attrs.get("study_wallclock_s"),
        "worker_id": study_user_attrs.get("worker_id"),
        # Issue #1104 (Katalog #937) — der Commit, auf dem DIESE Study tatsaechlich simuliert
        # wurde (gestempelt vor dem ersten Trial, siehe run_optimization.py), unabhaengig vom
        # REPORT-Commit (report._build_report's top-level git_commit_report).
        "git_commit_simulation": study_user_attrs.get("git_commit_simulation"),
        # Issue #1091 (Katalog #924), Quelle korrigiert #977/#1131 (Katalog #986) — die VOR dem
        # Confirm-Aufruf dieser (Strategie, Symbol)-Study eingefrorene Multiplizitaet, seit #1131
        # PER STRATEGIE aus ``n_family_stage1`` bezogen (nicht mehr die budgetierte, symbolweite
        # Summe); stabil ueber jeden Lesezeitpunkt (SQLite-Neuladen, Confirm-Re-Lauf).
        #
        # Issue #1007/#1159 (Katalog #1170) — Root-Cause: eine ``excluded_degenerate``-Study
        # (#981/#1135) stempelte hier eine rohe ``0`` (``Φ⁻¹(1 − 1/N)`` ist fuer N=0 undefiniert,
        # siehe ``deflation.sr0_multiple_testing_robust``) — latent, weil eine solche Study meist
        # bereits am IS-Gate stirbt, aber FAIL-OPEN. Fix: ``0`` wird HIER (beim Lesen/Exportieren,
        # nicht beim Schreiben in sweep.py) auf ``None`` abgebildet, MIT explizitem Skip-Grund —
        # nie mehr ``deflation_n_family_frozen == 0`` in einem Study-Record.
        "deflation_n_family_frozen": (
            None if _deflation_n_family_frozen_raw is not None
            and _deflation_n_family_frozen_raw <= 0
            else _deflation_n_family_frozen_raw
        ),
        # Issue #1007/#1159 — der Grund, WARUM ``deflation_n_family_frozen`` oben ``None`` statt
        # einer Zahl traegt (bewusst NICHT der gleichnamige ``deflation_skipped_reason`` aus
        # confirm.py's ``metrics_symbol`` — jenes Feld beschreibt, warum die SR0/DSR-Berechnung
        # DIESER Study uebersprungen wurde, ein anderer Skip an einer anderen Stelle; #1005/#1157
        # lehrt, denselben Feldnamen nicht fuer zwei verschiedene Groessen wiederzuverwenden).
        # ``None`` ⇒ ``deflation_n_family_frozen`` ist eine echte Zahl (>= 1) oder war nie gesetzt.
        "deflation_n_family_frozen_skipped_reason": (
            ("FAMILY_EXCLUDED_DEGENERATE" if _family_membership_raw == "excluded_degenerate"
             else "FAMILY_N_ZERO")
            if _deflation_n_family_frozen_raw is not None and _deflation_n_family_frozen_raw <= 0
            else None
        ),
        # Issue #981/#1135 (Katalog #986) — 'excluded_degenerate', wenn diese Study strukturell zu
        # wenige OOS-Perioden hat (< tournament.json['min_oos_periods_for_family']), um in der
        # Familien-Multiplizitaet, im check_n_periods_homogeneity-Nenner oder in der Rangliste
        # mitgezaehlt zu werden (siehe sweep._study_oos_n_periods_median). None ⇒ regulaeres
        # Familienmitglied (kein Config-Key gesetzt, oder Schwelle erreicht).
        "family_membership": _family_membership_raw,
        # Issue #977/#1131 — der Geltungsbereich, den ``deflation_n_family_frozen`` TATSAECHLICH
        # traegt (``'per_strategy'``/``'per_symbol_best'``), gegen ``deflation_n_family_source``
        # und ``tournament.json['promotion_family_scope']`` pruefbar (siehe invariants.check_
        # family_scope_coherence, #1138) — drei Angaben fuer dieselbe Groesse, die nicht mehr
        # auseinanderlaufen duerfen, ohne dass eine Invariante es sieht (Pitfall #408).
        "deflation_n_family_scope": study_user_attrs.get("deflation_n_family_scope"),
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
        # Issue #1024/#1173 (Katalog #866-2, Pitfall #423) — robustes Gegenstueck zu
        # oos_gross_loss_mean_bps (ALLE Verlust-Round-Trips); Nenner fuer
        # invariants.check_trailing_stop_loss_share Bedingung 2 seit diesem Fix (median/median
        # statt median/mean, siehe dortiger Docstring).
        "gross_loss_median_bps": _median_of_trial_field(trial_attrs, "oos_gross_loss_median_bps"),
        # Issue #1035 (Katalog #866) — dieselbe Groesse, aber NUR ueber nachweisliche TRAILING_
        # STOP-Exits, plus die zugrunde liegende Stichprobengroesse (Summe ueber alle Trials) —
        # Rohmaterial fuer invariants.check_effective_stop_distance (INCONCLUSIVE bei < 30 Stop-
        # Exits statt eines FAILs auf der falschen Grundgesamtheit, #1008/#1035).
        "oos_gross_loss_mean_bps_trailing_stop": _median_of_trial_field(
            trial_attrs, "oos_gross_loss_mean_bps_trailing_stop"),
        "oos_n_trailing_stop_losses": sum(
            int(a.get("oos_n_trailing_stop_losses") or 0) for a in trial_attrs),
        # Issue #1097 (Katalog #930) — GEPOOLTE, trade-gewichtete Gegenstuecke zu den beiden
        # medianbasierten Feldern oben: kommensurabel mit oos_n_losses/oos_n_trailing_stop_losses
        # (beide SUMMEN ueber dieselbe Trial-Kohorte), siehe _pooled_mean_of_trial_field-Docstring.
        # invariants.check_effective_stop_distance konsumiert AUSSCHLIESSLICH diese Felder; die
        # Mediane oben bleiben als Robustheits-Telemetrie erhalten.
        "oos_gross_loss_mean_bps_pooled": _pooled_mean_of_trial_field(
            trial_attrs, mean_field="oos_gross_loss_mean_bps", count_field="oos_n_losses"),
        "oos_gross_loss_mean_bps_trailing_stop_pooled": _pooled_mean_of_trial_field(
            trial_attrs, mean_field="oos_gross_loss_mean_bps_trailing_stop",
            count_field="oos_n_trailing_stop_losses"),
        # Issue #972/#1126 (Katalog #986, Pitfall #405 in AGENTS.md) — robuste Gegenstuecke zum
        # ungeschuetzten Mittel: Median-der-Trial-Mediane (Median) und Median-der-Trial-
        # winsorisierten-Mittel (5/95). ``realized_stop_loss_ratio`` unten wird auf die Median-
        # Variante umgestellt; der Mittelwert bleibt als ``realized_stop_loss_ratio_mean`` erhalten.
        "gross_loss_median_bps_trailing_stop": _median_of_trial_field(
            trial_attrs, "oos_gross_loss_median_bps_trailing_stop"),
        "gross_loss_winsorized_mean_bps_trailing_stop": _median_of_trial_field(
            trial_attrs, "oos_gross_loss_winsorized_mean_bps_trailing_stop"),
        # Issue #972/#1126 Akzeptanzkriterium 4 — wie viele der TRAILING_STOP-Verlust-Round-Trips
        # dieser Study der Dust-Boden (an der Quelle, backtest_runner._filter_dust_round_trips)
        # verworfen hat, relativ zu n_trailing_stop_losses (die BEHALTENE Menge).
        "n_trailing_stop_losses_dust_filtered": sum(
            int(a.get("oos_n_trailing_stop_losses_dust_filtered") or 0) for a in trial_attrs),
        # Issue #972/#1126 Akzeptanzkriterium 3 — p05/p50/p95 des Round-Trip-Notionals dieser
        # Study; macht den bps-Nenner der Verlust-/Gewinn-Kennzahlen oben auditierbar.
        "rt_notional_p05": _median_of_trial_field(trial_attrs, "oos_rt_notional_p05"),
        "rt_notional_p50": _median_of_trial_field(trial_attrs, "oos_rt_notional_p50"),
        "rt_notional_p95": _median_of_trial_field(trial_attrs, "oos_rt_notional_p95"),
        # Issue #975/#1129 — Median (über die Trials dieser Study) der je-Trial-Mediane des ROHEN
        # (ungefloorten) ATR; Gegenstueck zu atr_median_bps (dem EFFEKTIVEN, gefloorten Wert) weiter
        # unten. Entscheidet, ob atr_median_bps teilweise zirkulaer gegen den Stop selbst misst.
        "atr_raw_median_bps": _median_of_trial_field(trial_attrs, "oos_atr_raw_median_bps"),
        # Issue #976/#1130 — Median (über die Trials dieser Study) der je-Trial Absetzen-zu-Fill-
        # Latenz (Bars) und Slippage (bps) über nachweisliche TRAILING_STOP-Exits. Zusammen mit
        # bar_range_median_bps/realized_stop_loss_ratio die Zerlegung "Verlust = Stopdistanz +
        # Überschiessen + Slippage" (#976 Akzeptanzkriterium).
        "stop_exit_fill_lag_bars": (
            _median_of_trial_field(trial_attrs, "oos_stop_exit_fill_lag_bars_median")
            if _n_ts_exits_with_fill_lag else None),
        "stop_exit_slippage_bps": _median_of_trial_field(
            trial_attrs, "oos_stop_exit_slippage_bps_median"),
        "n_trailing_stop_exits_with_fill_lag_telemetry": _n_ts_exits_with_fill_lag,
        # Issue #1085 (Katalog #866-2), Quelle umgestellt #946/#1112 (Katalog #960) — über alle
        # Trials aufsummierte Dust-Round-Trips (Notional < 5% des Median-Notionals dieser Study,
        # Fliesskomma-Residuen eines Netto-Exposure-Nulldurchgangs) — Rohmaterial für
        # invariants.check_dust_round_trip_share. Liest seit #946 ``oos_dust_round_trips_filtered_
        # count`` (an der Round-Trip-QUELLE verworfen, VOR jeder Statistik) statt der vormaligen
        # ``oos_expectancy_notional_degenerate_count`` (nur an der Expectancy-Konsumstelle
        # verworfen — strukturell 0 seit #946, weil Dust die Expectancy-Berechnung nie mehr
        # erreicht).
        "dust_round_trips_filtered": sum(
            int(a.get("oos_dust_round_trips_filtered_count") or 0) for a in trial_attrs),
        "atr_median_bps": _median_of_trial_field(trial_attrs, "oos_atr_median_bps"),
        # Issue #1095 (Katalog #928) — Median (über die Trials dieser Study) der je-Trial-Mediane
        # der Bars zwischen Trailing-Stop-Signal und tatsaechlichem Markt-Close-Fill. Macht den in
        # #1092/#1094 quantifizierten Fill-Verzoegerungs-Anteil auf Study-Ebene sichtbar.
        "oos_stop_exit_lag_bars": _median_of_trial_field(
            trial_attrs, "oos_stop_exit_lag_bars_median"),
        # Issue #953/#1119 (Katalog #960) — Median (über die Trials dieser Study) der je-Trial-
        # Mediane der Bar-Spanne ((high-low)/close, bps) während offener Positionen; Referenzgrösse
        # für invariants.check_stop_loss_vs_bar_range (Verlust = adverse Bewegung EINER Bar, nicht
        # Stopdistanz + Überschiessen — Root-Cause-Hypothese #1119).
        "bar_range_median_bps": _median_of_trial_field(
            trial_attrs, "oos_bar_range_median_bps"),
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
        # Issue #997/#1149 — faellt auf den strategy_defaults.json-Eintrag zurueck, wenn die
        # Strategie diesen Parameter nicht sampelt (z. B. SmaCrossoverStrategy); die Herkunft
        # (source ∈ {"sampled","strategy_default","unavailable"}) macht das UNTERSCHEIDBAR von
        # einem echten, gesampelten Median, statt beide unter demselben Feld zu verstecken.
        "atr_trailing_multiplier_median": _atr_trailing_multiplier_median,
        "atr_trailing_multiplier_median_source": _atr_trailing_multiplier_median_source,
        # Issue #997/#1149 Fix Punkt 2 — dasselbe Muster fuer max_bars_in_trade (die Zeitbox greift
        # ebenfalls unabhaengig davon, ob die Strategie sie sampelt).
        "max_bars_in_trade_median": _max_bars_in_trade_median,
        "max_bars_in_trade_median_source": _max_bars_in_trade_median_source,
        # Issue #862 — Rohmaterial für den globalen check_guard_reference_coherence-Wächter.
        "oos_n_periods_median": oos_n_periods_median,
        # Issue #1011/#1163 (Katalog #1170) — Rohmaterial für invariants.check_session_calendar_
        # coherence (asset-class-gated FAIL bei > 8) und Zusatz-Telemetrie zur Session-Abdeckung.
        "bars_per_calendar_day": bars_per_calendar_day_median,
        "session_coverage_fraction": session_coverage_fraction_median,
        "promotion_outcome": proposal.get("status"),
        # Issue #1002/#1154 (Katalog #1170) — die ERSTE verletzte Promotions-Stufe (``None`` bei
        # einer Promotion) UND alle GLEICHZEITIG verletzten Stufen — macht in Abschnitt 2.2 ohne
        # Blick in run.json sichtbar, an welcher Stufe eine Study starb (Akzeptanzkriterium #1154).
        "blocking_stage": proposal.get("blocking_stage"),
        "all_failed_stages": proposal.get("all_failed_stages") or [],
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
        # Issue #956/#1122 (Katalog #960) — n_rejections wird seit diesem Fix DIREKT aus
        # is_rejection_detail_counts (oben in dieser Funktion berechnet) abgeleitet, statt
        # parallel und potenziell inkohärent aus oos_gate_deltas gepflegt zu werden.
        "gate_inventory": _inv.gate_inventory_table(
            trial_attrs, (tournament_cfg or {}).get("eligible_requires_all") or [],
            is_rejection_detail_counts=is_rejection_detail_counts,
            tournament_config=tournament_cfg),
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
        #
        # Issue #1013/#1165 (Katalog #1170) — Root-Cause: ``parsing.TournamentMetrics.oos_total_
        # return`` hat den Dataclass-Default ``0.0`` (KEIN ``float | None``) — ``parse_tournament``
        # koaleszierte ein fehlendes ``oos_metrics['total_return']`` (der Holdout wurde NIE
        # ausgewertet, z. B. weil ``mtm_series``/``_wf`` fehlten) still auf ``0.0``, ununterscheidbar
        # von einer ECHTEN, gemessenen 0%-Rendite. ``oos_buyhold_return`` (Dataclass-Default
        # ``None``) hat denselben Bug NICHT — sie bleibt in genau diesem Fall ``None``. HIER (am
        # Report-Rand, NICHT in parsing.py — eine Signaturänderung dort würde jeden Aufrufer treffen,
        # der ``oos_total_return`` bereits als garantiert-float behandelt) wird die Koaleszenz
        # rückgängig gemacht: ``0.0`` + ``oos_buyhold_return is None`` + ``oos_total_trades == 0``
        # ist exakt die undurchführbare Kombination eines NIE ausgewerteten Holdouts (ein echter
        # Zero-Trade-Holdout mit erfolgreich berechneter mtm_series HAT einen definierten
        # ``oos_buyhold_return``, da Buy&Hold unabhängig von Strategie-Trades ist) — summary_de.py
        # rendert das Ergebnis als "k. A." statt "0,0 %" (Symptom: SqueezeBreakout/NVDA erschien
        # dadurch ÜBER allen echten negativen Kandidaten).
        "holdout_total_return": (
            None if (
                holdout_metrics.get("oos_total_return") == 0.0
                and holdout_metrics.get("oos_buyhold_return") is None
                and (holdout_metrics.get("oos_total_trades") or 0) == 0
            ) else holdout_metrics.get("oos_total_return")
        ),
        # Issue #945/#1111 (Katalog #960) — umbenannt von ``holdout_expectancy``: Root-Cause, vierte
        # Instanz der Klasse #304/#1033/#1097 — der Kostenstress-Ladder
        # (``holdout_expectancy_cost_stress_1_5x``/``_2x`` unten) wird aus
        # ``holdout_expectancy_capital_weighted`` abgeleitet (siehe
        # ``backtest_runner._expectancy_cost_stress``-Docstring, DIESELBE 5-%-Notional-Boden-Logik),
        # waehrend vorher unter dem Namen "holdout_expectancy" berichtet/sortiert wurde (Mittel von
        # Quotienten, KEIN Notional-Boden) — bei SqueezeBreakout/PLTR (Divergenz Faktor 7,9)
        # erschien der "2×-Kostenstress" dadurch als Verbesserung um +145,76 bps. Reine Telemetrie
        # seither (mathematisch weiterhin die korrekte Basis fuer die #1028-Sizing-Identitaet, siehe
        # ``check_sizing_identity_coherence``) — KEIN Entscheidungs-/Sortier-/Gate-Konsument mehr.
        "holdout_expectancy_notional_weighted": holdout_metrics.get("oos_expectancy"),
        # Issue #989/#1143 (Katalog #986, Pitfall #412 in AGENTS.md) — DIREKT gemessener Sizing-
        # Anteil (rt_notional / equity_at_entry, Median), Rohmaterial fuer
        # invariants.check_sizing_identity_coherence — ERSETZT dort den bisher AUSSCHLIESSLICH aus
        # (holdout_total_return, holdout_expectancy_notional_weighted, holdout_total_trades)
        # algebraisch implizierten Wert als primaeres Entscheidungskriterium, sofern verfuegbar.
        "holdout_f_realized_median": holdout_metrics.get("oos_f_realized_median"),
        # Issue #945/#1111 — die KANONISCHE Grösse: dieselbe Basis, aus der die Kostenstress-Werte
        # abgeleitet werden UND die seither berichtet/sortiert wird (summary_de.py Abschnitt 2.1).
        "holdout_expectancy_capital_weighted": holdout_metrics.get("oos_expectancy_capital_weighted"),
        "holdout_expectancy_winsorized": holdout_metrics.get("oos_expectancy_winsorized"),
        "holdout_expectancy_outlier_count": holdout_metrics.get("oos_expectancy_outlier_count") or 0,
        "holdout_expectancy_notional_degenerate_count": (
            holdout_metrics.get("oos_expectancy_notional_degenerate_count") or 0),
        # Issue #946/#1112 (Katalog #960) — Dust-Round-Trips, an der Round-Trip-QUELLE verworfen
        # (VOR jeder Statistik, siehe backtest_runner._filter_dust_round_trips), fuer die HOLDOUT-
        # Bestaetigung. Das Feld oben bleibt strukturell 0 seit diesem Fix (Dust erreicht die
        # Expectancy-Berechnung nicht mehr).
        "holdout_dust_round_trips_filtered_count": (
            holdout_metrics.get("oos_dust_round_trips_filtered_count") or 0),
        # Issue #948/#1114 (Katalog #960) — der EINE studienweite (gepoolte) Annualisierungsfaktor
        # (sqrt(F) = holdout_sortino_annualized / holdout_sortino_period), Rohmaterial fuer
        # invariants.check_annualization_commensurability (misst seit diesem Fix die Streuung
        # DIESES Faktors ueber Studies desselben Symbols, nicht mehr die triviale Intra-Trial-
        # Fold-Streuung des ANNUALISIERTEN Sortino).
        "holdout_sortino_period": holdout_metrics.get("oos_sortino_period"),
        "holdout_sortino_annualized": holdout_metrics.get("oos_sortino_annualized"),
        # Issue #980/#1134 (Katalog #986) — woher der F-Faktor hinter holdout_sortino_annualized
        # tatsaechlich kam (siehe backtest_runner._get_annualization_factor_with_source-Docstring);
        # Rohmaterial fuer invariants.check_annualization_commensurability.
        "annualization_factor_source": holdout_metrics.get("oos_annualization_factor_source"),
        # Issue #1042 (Katalog #866) E-1/E-3 — Kosten-Stressband + CVaR/ES-Tail-Risiko, additiv
        # neben den unveraenderten Basis-Kennzahlen (siehe backtest_runner-Docstrings).
        "holdout_expectancy_cost_stress_1_5x": holdout_metrics.get("oos_expectancy_cost_stress_1_5x"),
        "holdout_expectancy_cost_stress_2x": holdout_metrics.get("oos_expectancy_cost_stress_2x"),
        # Issue #987/#1141 (Katalog #986, Pitfall #412 in AGENTS.md) Fix-Punkt 4 — sechste Kosten-
        # stress-Stufe (Finanzierung + Slippage voll abgezogen, siehe backtest_runner._full_realism_
        # expectancy-Docstring); Akzeptanzkriterium: "die Expectancy unter full_realism ausgewiesen".
        "holdout_expectancy_cost_stress_full_realism": holdout_metrics.get(
            "oos_expectancy_cost_stress_full_realism"),
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
        # Issue #986/#1140 (Katalog #986, Pitfall #412 in AGENTS.md) — ``holdout_excess_return``
        # allein ist im fallenden Markt fuer JEDE Strategie mit Exposure < 100 % trivial positiv
        # (0/39 auf steigenden, 62/65 auf fallenden Symbolen). ``holdout_excess_per_unit_exposure``
        # normiert den Excess auf die tatsaechlich eingegangene Marktzeit — dieselbe
        # ``_min_exposure_for_normalization=0.05``-Guard wie summary_de.py Abschnitt 2.3 (near-
        # Null-Exposure macht die Division numerisch bedeutungslos, nicht nur unbewertbar).
        # ``holdout_alpha``/``holdout_beta``/``holdout_alpha_tstat`` sind die OLS-Regressions-
        # koeffizienten der Strategie- gegen die Benchmark-Perioden-Returns (siehe
        # backtest_runner._alpha_beta_regression); ``holdout_no_alpha_detected=True`` markiert
        # |t(alpha)| < 1 — alpha ist dann statistisch nicht von Null unterscheidbar (ein
        # BETRAGSMAESSIG grosses NEGATIVES t(alpha) ist ein erkannter negativer Effekt, kein
        # "kein Alpha" — deshalb der Betrag, nicht der Rohwert aus der Issue-Formulierung).
        "holdout_excess_per_unit_exposure": (
            (holdout_metrics.get("oos_excess_return") / holdout_metrics.get("oos_exposure_fraction"))
            if (holdout_metrics.get("oos_excess_return") is not None
                and (holdout_metrics.get("oos_exposure_fraction") or 0.0) >= 0.05)
            else None
        ),
        "holdout_alpha": holdout_metrics.get("oos_alpha"),
        "holdout_beta": holdout_metrics.get("oos_beta"),
        "holdout_alpha_tstat": holdout_metrics.get("oos_alpha_tstat"),
        "holdout_no_alpha_detected": (
            abs(holdout_metrics["oos_alpha_tstat"]) < 1.0
            if holdout_metrics.get("oos_alpha_tstat") is not None else None
        ),
        "holdout_total_trades": holdout_metrics.get("oos_total_trades"),
        # Issue #1101 (Katalog #934) Akzeptanzkriterium 1 — WELCHER Parameter (und in welche
        # Richtung) die Randlösung dominiert, siehe confirm.confirm_per_symbol_promotion. ``None``
        # ohne jede Randlösung dieser Study (dieselbe Konvention wie boundary_hit_fraction).
        "boundary_hit_fraction": holdout_metrics.get("boundary_hit_fraction"),
        "boundary_parameter": holdout_metrics.get("boundary_parameter"),
        "boundary_side": holdout_metrics.get("boundary_side"),
        "boundary_directions": holdout_metrics.get("boundary_directions") or {},
        # Issue #958/#1124 (Katalog #960) — die volle, benannte Evidenz je klemmendem Parameter
        # ({sampled_value, active_bounds, default_bounds, distance_to_edge}), damit jede
        # REJECTED_BOUNDARY_SOLUTION/HOLD_BOUNDARY_UNRESOLVED-Entscheidung im Artefakt selbst
        # nachvollziehbar ist (siehe run_optimization._boundary_veto_evidence-Docstring). None ohne
        # jede Randlösung dieser Study (dieselbe Konvention wie boundary_hit_fraction).
        "boundary_veto_evidence": holdout_metrics.get("boundary_veto_evidence"),
        # Issue #1101 (Katalog #934) Akzeptanzkriterium 2 — sichtbar, ob dieser Kandidat bereits
        # unter geweiteten Bounds fuer boundary_parameter evaluiert wurde und die Weitungs-Sperre
        # (sweep_diagnostics._MAX_WIDEN_APPLICATIONS) erreicht ist (⇒ terminaler
        # REJECT_BOUNDARY_SOLUTION_PERSISTENT statt eines erneuten HOLD_BOUNDARY_UNRESOLVED).
        "boundary_resolution_run_id": holdout_metrics.get("boundary_resolution_run_id"),
        "boundary_resolution_exhausted": bool(holdout_metrics.get("boundary_resolution_exhausted")),
        # Issue #826 Fix Punkt 2 — N1: die Multiplizität, die TATSÄCHLICH für diese EINE
        # (Strategie, Symbol)-Study an die Deflation ging (sweep._family_n_stage1_from_studies,
        # unter promotion_family_scope='per_strategy' identisch zu holdout_metrics.deflation_n_
        # family). NICHT mit dem (jetzt nicht mehr für die Deflation verwendeten) symbolweiten
        # cross_study['n_family'] verwechseln (#625, post-hoc Sweep-Telemetrie).
        "n_family_stage1": holdout_metrics.get("deflation_n_family"),
        # Issue #984/#1138 (Katalog #986) — Rohmaterial fuer invariants.check_family_scope_
        # coherence (#904-Regressionswaechter, verdrahtet ueber study_family_records unten).
        "deflation_n_family_raw": holdout_metrics.get("deflation_n_family_raw"),
        # Issue #957/#1123 (Katalog #960) — welche der (strukturell zwei moeglichen) Quellen
        # n_family_stage1 oben tatsaechlich gespeist hat (siehe confirm.confirm_per_symbol_
        # promotion's deflation_n_family_source-Docstring), im Report-Artefakt sichtbar statt nur
        # aus dem Aufrufer-Quelltext erschliessbar.
        "deflation_n_family_source": holdout_metrics.get("deflation_n_family_source"),
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
    # vollen adversen Bar-Bewegung.
    #
    # Issue #972/#1126 (Pitfall #405 in AGENTS.md) — ``gross_loss_mean_bps_trailing_stop`` ist ein
    # UNGESCHUETZTES arithmetisches Mittel. ``realized_stop_loss_ratio`` ist seit diesem Fix die
    # MEDIAN-Variante (robust gegen Ausreisser); der vormalige Mittelwert-Quotient bleibt additiv
    # als ``realized_stop_loss_ratio_mean`` erhalten (Zero-Regression fuer bestehende Konsumenten,
    # die explizit den Mittelwert wollen). ``None``, wenn eine der drei Eingangsgrössen fehlt oder
    # die konfigurierte Distanz <= 0 ist (kein Urteil auf einer undefinierten Zahl).
    _rt_atr = record.get("atr_median_bps")
    _rt_k = record.get("atr_trailing_multiplier_median")
    _rt_configured_distance = (
        float(_rt_k) * float(_rt_atr)
        if (_rt_atr and _rt_k is not None) else None)
    # Issue #1026/#1175 (Katalog #866-2) — die konfigurierte Stopdistanz (k_median · ATR_median,
    # bps) als eigenstaendiges Report-Feld: Rohmaterial fuer die ``atr_floor_binding_studies``-
    # Provenance (siehe invariants.check_atr_scale_homogeneity), vorher nur lokal in dieser
    # Funktion berechnet und nirgends exportiert.
    record["stop_distance_bps"] = (
        round(_rt_configured_distance, 4) if _rt_configured_distance is not None else None)
    _rt_loss_median = record.get("gross_loss_median_bps_trailing_stop")
    if _rt_loss_median is not None and _rt_configured_distance and _rt_configured_distance > 0:
        record["realized_stop_loss_ratio"] = round(float(_rt_loss_median) / _rt_configured_distance, 4)
    else:
        record["realized_stop_loss_ratio"] = None
    _rt_loss_mean = record.get("oos_gross_loss_mean_bps_trailing_stop")
    if _rt_loss_mean is not None and _rt_configured_distance and _rt_configured_distance > 0:
        record["realized_stop_loss_ratio_mean"] = round(float(_rt_loss_mean) / _rt_configured_distance, 4)
    else:
        record["realized_stop_loss_ratio_mean"] = None
    # Issue #972/#1126 Akzeptanzkriterium 3 — relative Abweichung Mittel<->Median; > 0,5 markiert die
    # Study explizit als ausreissergetrieben (der Mittelwert wird durch wenige extreme Trades
    # dominiert, statt die typische Beobachtung wiederzugeben).
    if (record["realized_stop_loss_ratio"] not in (None, 0)
            and record["realized_stop_loss_ratio_mean"] is not None):
        _rel_dev = round(
            abs(record["realized_stop_loss_ratio_mean"] - record["realized_stop_loss_ratio"])
            / abs(record["realized_stop_loss_ratio"]), 4)
        record["realized_stop_loss_ratio_mean_median_rel_dev"] = _rel_dev
        record["realized_stop_loss_ratio_outlier_driven"] = _rel_dev > 0.5
    else:
        record["realized_stop_loss_ratio_mean_median_rel_dev"] = None
        record["realized_stop_loss_ratio_outlier_driven"] = None
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
            # Issue #1101 (Katalog #934) Akzeptanzkriterium 1 — derselbe klemmende Parameter wie
            # im Study-Record (siehe _study_record), hier zusaetzlich neben dem Bounds-Vorschlag.
            "boundary_parameter": e.get("boundary_parameter"),
            "boundary_side": e.get("boundary_side"),
            # Issue #958/#1124 (Katalog #960) — die volle, benannte Evidenz je klemmendem
            # Parameter ({sampled_value, active_bounds, default_bounds, distance_to_edge}, siehe
            # run_optimization._boundary_veto_evidence-Docstring).
            "boundary_veto_evidence": e.get("boundary_veto_evidence"),
            # Issue #1101 (Katalog #934) Akzeptanzkriterium 2 — wie oft dieser Parameter bereits
            # nachgeweitet wurde (sweep_diagnostics.record_diagnosed_pair), damit im Report
            # nachvollziehbar ist, wie nah ein Kandidat an der Weitungs-Sperre
            # (sweep_diagnostics._MAX_WIDEN_APPLICATIONS) steht.
            "widen_applications": e.get("widen_applications") or {},
        }
        for e in _diagnosed_pairs_all() if e.get("binding_cause") == "boundary_solution"
    ]


def _search_budget_proposal_section(
    all_checks: list[tuple[str, "_inv.InvariantResult"]],
) -> list[dict[str, Any]]:
    """Issue #1082 Fix Punkt (a) (Katalog #866-2, Kohorte E) — Studies, deren
    ``check_objective_branch_coverage`` FAILt, als eigene Report-Sektion — das Rohmaterial fuer den
    Suchbudget-Vorschlag des NAECHSTEN Laufs. ``sweep._apply_search_budget_proposal`` liest diese
    Sektion aus dem JUENGSTEN #742-Report (analog ``_read_last_study_wallclock_by_strategy``) und
    schreibt jedes Paar ueber den bestehenden #830-``'deprioritized'``-Pfad in den Diagnose-Cache —
    eine Study unter der Schwelle bekommt im naechsten Lauf NICHT dasselbe Budget noch einmal
    (``run_optimization._apply_deprioritized_budget``).

    Issue #955/#1121 (Katalog #960) — ``check_objective_branch_coverage`` misst seit diesem Fix den
    Anteil ineligibler Trials mit definierter Selektionsstatistik (NICHT mehr
    ``reward_terms.branch == 'per_symbol'``, das sich als blosse Umbenennung von ``p_eligible``
    erwies, siehe dortiger Docstring) — dieselbe Konsequenz (Budget-Deprio via Check-NAME), eine
    nicht mehr duplizierte Eingangsgrösse."""
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
    diese beiden Fälle unterschiedlich).

    Issue #1099 (Katalog #932) — Root-Cause: die #1084-``studies_out``-Rekonstruktion (unten, jetzt
    nur noch Fallback) zählt jedes (strategy, symbol)-Paar der STUDY-Kohorte als "Versuch" — bei
    einem ``--report-only``-Report über eine inzwischen gewachsene/gealterte ``proposal_*.json``-
    Menge (siehe ``generate_report_for_run``) lief diese Zahl (52/53/56 im #932-Referenzlauf) an
    der tatsächlich je Lauf KONSTANTEN Versuchszahl (14, im Ereignisstrom nachweisbar) vorbei — die
    Study-Liste ist die falsche Grundgesamtheit für "wie oft wurde ``maybe_write_back`` versucht".
    ``sweep._attempt_champion_writeback`` emittiert GENAU EIN ``CHAMPION_WRITEBACK``-Ereignis je
    tatsächlichem Versuch (auch bei Nicht-Erfolg, ``skipped_reason`` trägt den Grund) — bevorzugte
    Quelle jetzt DIESER Ereignisstrom, ausgelesen über ``jsonl_sidecar_path``/``_read_jsonl_events``.
    Nur auflösbar im SELBEN Prozess, der den Lauf tatsächlich ausgeführt hat (das Modul-Dict in
    ``log_manager`` ist prozesslokal) — ein frischer ``--report-only``-Prozess fällt automatisch auf
    die ``studies_out``-Rekonstruktion zurück (unverändertes Pre-#1099-Verhalten)."""
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
    # Issue #1099 (Katalog #932) — der Ereignis-Pfad ersetzt AUSSCHLIESSLICH die #1084-
    # studies_out-Rekonstruktion (unten); ``studies_out is None`` bleibt der unveränderte Legacy-
    # Vertrag (reine Verzeichnis-Iteration oben, ``attempts=None``, siehe Docstring) — in der
    # Produktion ruft ausschliesslich ``_build_report`` diese Funktion auf, IMMER mit gesetztem
    # ``studies_out``; ``studies_out=None`` ist ein reiner Test-/Legacy-Aufrufpfad.
    _events_path = jsonl_sidecar_path(_log.name) if studies_out is not None else None
    if _events_path is not None:
        # Issue #1099 (Katalog #932) — bevorzugte Quelle: die tatsächlich emittierten
        # CHAMPION_WRITEBACK-Ereignisse DIESES Laufs (siehe Docstring oben).
        writeback_events = _read_jsonl_events(_events_path, "CHAMPION_WRITEBACK")
        attempts = len(writeback_events)
        skipped_by_reason = collections.Counter()
        for ev in writeback_events:
            if not ev.get("applied"):
                skipped_by_reason[ev.get("skipped_reason") or "UNKNOWN"] += 1
    elif studies_out is not None:
        # Issue #1084 Fix Punkt 1/3 — Fallback für einen frischen Prozess ohne eigenen
        # Ereignisstrom (z. B. ``--report-only``, siehe Docstring oben): die ATTEMPT-skopierte
        # Rekonstruktion über jedes (strategy, symbol)-Paar dieses Laufs, unabhängig davon, ob es
        # einen Store-Eintrag hinterlassen hat. ``load_champion_entry_with_reason`` liest denselben,
        # gerade oben iterierten Store-Stand nochmals GEZIELT je Paar — der zweite Durchlauf ist
        # bewusst getrennt: die Verzeichnis-Iteration oben bleibt die reine "aktueller
        # Store-Zustand"-Sicht (stored/admissible/corroborated/written_back/max_corroboration_count
        # unverändert), diese Kohorte hier ersetzt ausschliesslich ``skipped_by_reason``.
        skipped_by_reason = collections.Counter()
        pairs_seen: set[tuple[str, str]] = set()
        for r in studies_out:
            strategy, symbol = r.get("strategy"), r.get("symbol")
            if not strategy or not symbol or (strategy, symbol) in pairs_seen:
                continue
            pairs_seen.add((strategy, symbol))
            try:
                entry, reason, _no_entry_provenance = _champions_mod.load_champion_entry_with_reason(
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


def _worker_occupancy_wallclock(studies_out: list[dict[str, Any]], *, n_jobs: int | None,
                                sweep_wallclock_s: float | None) -> float | None:
    """Issue #851, umbenannt #949/#1115 (Katalog #960) — Σ Study-Wallclock / (n_jobs ×
    Sweep-Wallclock). Vormals ``_worker_utilisation`` — derselbe Name wie ``_cpu_utilisation_
    backtest`` (unten) fuer zwei GRUNDVERSCHIEDENE Grössen fuehrte im Report-Dokument (§3) UND in
    der zugehoerigen Invariante zu zwei Zahlen unter demselben Begriff "Worker-Auslastung"
    (0,7583/1,1251/1,1360 hier gegen 60,2/89,6/90,5% dort, B-6) — Root-Cause #949.

    Issue #1038 (Katalog #866) — trotz des (alten) Namens ist dies KEINE Auslastung im engeren Sinn
    (ein Anteil, der niemals 1.0 uebersteigen kann): der Zaehler ueberlappt sich strukturell, wenn
    (a) eine Study fremder Laeufe eingemischt war (vor #1023) — Σ Study-Wallclock zaehlte dann
    Sekunden mehrfacher, GLEICHZEITIGER Laeufe zusammen, oder (b) jede Study selbst einen EIGENEN
    Worker-Pool oeffnet (``backtest_runner.py``, ``_max_workers = max(1, min(cpu//2, 6))``) —
    Study-Wallclocks verschiedener, parallel dispatchter Studies ueberlappen sich dann
    untereinander. Beobachtete Werte: 151,8 %/246,5 %/332,9 % ueber drei Laeufe. Nach #1023 (fremde
    Studies ausgeschlossen) bleibt Ursache (b) bestehen — ``check_worker_utilisation_plausible``
    (invariants.py) meldet jeden Wert > 1.0 als FAIL statt ihn unkommentiert anzuzeigen.
    ``_cpu_utilisation_backtest`` (unten) ist die zweite, ueberlappungsfreie Grösse fuer denselben
    Zweck — beide tragen seit #949 GETRENNTE Namen, keine gemeinsame "Auslastung" mehr.

    ``studies_out`` ist bereits die #1086/#940 run_id-verifizierte Kohorte (kein separater Filter
    hier noetig, siehe #949 Akzeptanzkriterium 1). None ohne n_jobs/sweep_wallclock_s ODER ohne eine
    einzige Study mit Wallclock-Daten."""
    if not n_jobs or n_jobs <= 0 or not sweep_wallclock_s or sweep_wallclock_s <= 0:
        return None
    total_study_wallclock = sum(
        r["study_wallclock_s"] for r in studies_out if r.get("study_wallclock_s") is not None)
    if total_study_wallclock <= 0:
        return None
    return total_study_wallclock / (n_jobs * sweep_wallclock_s)


def _cpu_utilisation_backtest(studies_out: list[dict[str, Any]], *, n_jobs: int | None,
                              sweep_wallclock_s: float | None) -> float | None:
    """Issue #1038 (Katalog #866), umbenannt #949/#1115 (Katalog #960) — Σ ``backtest_ms_sum``
    (tatsaechliche, additive Backtest-CPU-Zeit je Trial, ``_study_record``) / (n_jobs ×
    Sweep-Wallclock). Vormals ``_worker_utilisation_backtest_ms``. Anders als
    ``_worker_occupancy_wallclock`` (Study-Wallclock, siehe dortiger Docstring) summiert dies echte
    Trial-Arbeit statt Wanduhrzeit — verschachtelte Study-eigene Worker-Pools koennen diese Zahl
    NICHT ueber 1.0 durch reine Ueberlappung treiben, da jede Millisekunde genau EINEM Trial
    zugeordnet ist. Das ist die Grösse, die tatsaechlich eine physikalische CPU-Auslastung im
    engeren Sinn ist (<= 1.0)."""
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
    Wert verwendet, statt die Study stillschweigend auszulassen.

    Issue #1006/#1158 (Katalog #1170) — Root-Cause: der obige #1080-Fallback kannte den #981/#1135-
    Ausschluss (``family_membership == 'excluded_degenerate'``) nicht — eine degenerierte Study
    (zu wenige OOS-Perioden, ``deflation_n_family`` daher NIE gestempelt, siehe confirm.py's ``if
    deflation_sr0 is not None:``-Guard) fiel auf ihre rohe ``n_selection_statistic_available``-Zahl
    zurück, während ``sweep.py``'s eingefrorener Stempel dieselbe Study auf 0 setzt — Differenz
    exakt 1 je betroffener Study. ``_family_members`` (``sweep.py``, dieselbe Funktion wie dort) wird
    JETZT auch hier angewendet: eine ausgeschlossene Study zaehlt mit N1=0, unabhängig davon, ob
    ``n_family_stage1``/``n_selection_statistic_available`` selbst > 0 wären."""
    stage1: dict[str, dict[str, int]] = {}
    stage2: dict[str, int] = {}
    for r in studies_out:
        symbol = r.get("symbol")
        strategy = r.get("strategy")
        if not _family_members(r.get("family_membership")):
            n1 = 0
        else:
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


def _compute_decision_admissible(invariant_checks: list[dict]) -> bool:
    """Issue #942/#1108 (Katalog #960) — eine der drei orthogonalen Achsen, die den vorher
    ueberladenen ``run_status``-String ersetzen (siehe ``_build_report``-Docstring): ``False``
    sobald mindestens eine ``severity='blocking'``-Invariante in ``invariant_checks`` FAILt.
    Reine Funktion (kein Report-Kontext noetig) — dieselbe Definition, die vormals in
    ``sweep._downgrade_run_status_for_blocking_invariants`` unabhaengig REKONSTRUIERT wurde,
    JETZT die einzige Quelle."""
    return not any(
        c.get("severity") == "blocking" and not c.get("passed", True) for c in invariant_checks)


def _compute_work_completed(
    symbols_completed: int | None, symbols_planned: int | None,
) -> bool | None:
    """Issue #942/#1108 (Katalog #960) — die zweite orthogonale Achse: ``True``, wenn alle
    geplanten Symbole abgeschlossen wurden, ``False`` bei einem echten Abbruch mit weniger
    abgeschlossenen als geplanten Symbolen, ``None`` wenn unbekannt (weder Checkpoint noch
    In-Prozess-Spiegel verfuegbar, siehe ``sweep.sweep_symbol_funnel``) — NIE stillschweigend als
    ``False`` interpretiert (das waere eine falsche Behauptung, kein fehlender Messwert)."""
    if symbols_completed is None or symbols_planned is None:
        return None
    return symbols_completed >= symbols_planned


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Issue #1021/#1196 — normalisiert ein tz-aware oder naives ``datetime`` auf naive UTC, damit
    Optuna-Trial-Zeitstempel (naiv) und aus ``started_at_utc``/``wallclock_s`` abgeleitete
    Lauf-Zeitfenster (tz-aware) miteinander vergleichbar sind, ohne dass eine der beiden Quellen
    ihre eigene Repräsentation ändern muss."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _trial_time_window(trials: list) -> tuple[datetime, datetime] | None:
    """Issue #1021/#1196 — (min(datetime_start), max(datetime_complete oder datetime_start)) über
    ``trials``. ``None``, wenn KEIN Trial dieser Menge einen Zeitstempel trägt (fehlende Evidenz —
    der Aufrufer bleibt dann fail-loud, siehe ``_windows_overlap``-Aufrufstelle im Kohorten-
    Wächter)."""
    starts = [t.datetime_start for t in trials if getattr(t, "datetime_start", None) is not None]
    ends = [t.datetime_complete for t in trials if getattr(t, "datetime_complete", None) is not None]
    ends = ends or starts
    if not starts or not ends:
        return None
    return (min(starts), max(ends))


def _trial_time_windows_by_run_id(
    trials: list,
) -> dict[str | None, tuple[datetime, datetime] | None]:
    """Issue #1021/#1196 — ``trials`` (typischerweise ``_foreign_run_trials`` einer Study) nach
    ``run_id`` gruppiert, je Gruppe das Zeitfenster aus ``_trial_time_window``."""
    by_run_id: dict[str | None, list] = {}
    for t in trials:
        rid = (getattr(t, "user_attrs", None) or {}).get("run_id")
        by_run_id.setdefault(rid, []).append(t)
    return {rid: _trial_time_window(ts) for rid, ts in by_run_id.items()}


def _windows_overlap(
    window_a: tuple[datetime, datetime], window_b: tuple[datetime, datetime],
) -> bool:
    """Issue #1021/#1196 — zwei geschlossene Zeitintervalle überlappen, wenn keins vollständig vor
    dem anderen liegt. Beide Seiten werden vor dem Vergleich auf naive UTC normalisiert (siehe
    ``_as_naive_utc``)."""
    a0, a1 = _as_naive_utc(window_a[0]), _as_naive_utc(window_a[1])
    b0, b1 = _as_naive_utc(window_b[0]), _as_naive_utc(window_b[1])
    return a0 <= b1 and b0 <= a1


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
    prior_probe_invariant_checks: list[dict] | None = None,
    fail_fast_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
) -> dict:
    # Issue #942/#1108 (Katalog #960) — ``fail_fast_triggered`` (der Name der Fail-Fast-Invariante,
    # die den Sweep abgebrochen hat, oder ``None``) treibt ZUSAMMEN mit den unten berechneten
    # ``work_completed``/``decision_admissible`` die drei orthogonalen Achsen, die den bisher
    # ueberladenen ``run_status``-String ersetzen (siehe dortige Feld-Docstrings unten). Root-Cause
    # #1108: derselbe Faktenstand (14/14 Studies, volles Budget, Fail-Fast-Abbruch NACH Abschluss
    # der Arbeit) ergab je nach Report-Erzeugungspfad ZWEI verschiedene ``run_status``-Werte
    # (``completed_invalid`` vs. ``aborted_invariant``, LETZTERER faelschlich als "echter
    # Arbeitsabbruch" gelesen) — die drei neuen Felder werden HIER, EINMAL, aus derselben Quelle
    # (den bereits berechneten ``invariant_checks`` plus den durchgereichten Symbol-Zaehlern)
    # abgeleitet, unabhaengig davon, welcher Pfad (regulaerer Abschluss oder Abbruch-Exception)
    # ``_build_report`` letztlich aufruft.
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
    # Issue #1016/#1168 (Katalog #1170) — {cache_path, cache_found}, damit ein leeres/fehlendes
    # symbol_bar_quality NICHT stillschweigend als "None" im Report verschwindet (siehe
    # check_symbol_bar_quality_cache_availability-Docstring).
    _symbol_bar_quality_cache_status = symbol_bar_quality_cache_status(WORK)
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
    # Issue #1021/#1196 — Lauf-Zeitfenster dieses Reports, als Fallback fuer den Kohorten-Wächter
    # unten, wenn eine Study keine eigenen Trial-Zeitstempel traegt (z. B. eine leere Study). Ende
    # ist ``started_at_utc + wallclock_s`` (finaler Report, praezise) oder "jetzt" (Zwischen-/Probe-
    # Aufrufe waehrend eines laufenden Sweeps, #933/#839 — der Lauf ist zu diesem Zeitpunkt noch
    # nicht fertig, jede fremde Aktivitaet gilt konservativ als moeglicherweise gleichzeitig).
    _run_window: tuple[datetime, datetime] | None = None
    if _run_started_dt is not None:
        _run_finished_dt = None
        if wallclock_s:
            try:
                _run_finished_dt = _run_started_dt + timedelta(seconds=float(wallclock_s))
            except (TypeError, ValueError, OverflowError):
                _run_finished_dt = None
        _run_window = (_run_started_dt, _run_finished_dt or datetime.now(timezone.utc))
    # Issue #1021/#1196 — Aggregat fuer ``cross_study.store_reuse`` (Fix 4.2): macht sichtbar, dass
    # dieser Lauf per Warm-Start auf einem Store mit Trials eines VORLAUFS aufsetzt — das veraendert
    # ``deflation_n_family``, ``constraint_improvement_rate`` und den TPE-Seed und darf nicht
    # unsichtbar bleiben.
    _store_reuse_prior_run_ids: set[str] = set()
    _store_reuse_n_trials_prior = 0
    _store_reuse_n_trials_own = 0
    _store_reuse_studies_affected = 0
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
            # Issue #1021/#1196 — zwei run_ids in EINER Study sind der NORMALFALL bei
            # load_if_exists (sequenzieller Warm-Start, #799/#851): jeder Trial traegt ueber seinen
            # run_id-Stempel eindeutig GENAU eine run_id, die Trennung der Kohorten ist damit
            # exakt, unabhaengig davon, ob eine zweite run_id in derselben Study auftaucht.
            # Unaufloesbar ist die Kohorte NUR, wenn sich die tatsaechlichen Laufzeitfenster der
            # beteiligten run_ids UEBERLAPPEN (#1086 — zwei Sweep-Prozesse haben denselben Store
            # GLEICHZEITIG beschrieben). Der Diskriminator ist das gemessene Zeitfenster
            # (``trial.datetime_start``/``datetime_complete``, mit dem Lauf-Zeitfenster als Fallback
            # fuer eine Study ohne eigene Trial-Zeitstempel), nicht die blosse Anwesenheit einer
            # zweiten run_id, wie die vorherige Fassung dieses Wächters annahm.
            _foreign_ids = sorted({
                (getattr(t, "user_attrs", None) or {}).get("run_id") for t in _foreign_run_trials
            })
            _own_window = _trial_time_window(_own_run_trials) or _run_window
            _foreign_windows = _trial_time_windows_by_run_id(_foreign_run_trials)
            if _own_window is None:
                # Keine Zeitstempel-Evidenz auf der eigenen Seite verfuegbar — bisheriges
                # Fail-Loud-Verhalten (fail-closed auf fehlender Evidenz, #1021 Fix 4.1).
                _overlapping = sorted(_foreign_windows)
            else:
                _overlapping = sorted(
                    rid for rid, win in _foreign_windows.items()
                    if win is None or _windows_overlap(win, _own_window)
                )
            if _overlapping:
                raise _contracts.ReportCohortUnresolvable(
                    f"[REPORT_COHORT_UNRESOLVABLE] Study '{study_name}' ({proposal.get('strategy')}/"
                    f"{proposal.get('symbol')}) traegt sowohl Trials von run_id={run_id!r} als auch "
                    f"von {_foreign_ids!r} — die Laufzeitfenster von run_id={run_id!r} "
                    f"({_own_window!r}) und {_overlapping!r} ueberlappen — zwei Sweep-Prozesse "
                    "haben denselben Store gleichzeitig beschrieben (#1086). Getrennte --work-dir "
                    "verwenden."
                )
            # Sequenzielle Wiederverwendung (kein Fenster ueberlappt): die run_id-Stempel trennen
            # die Kohorten exakt — der beabsichtigte Warm-Start-Normalbetrieb (#1021/#1196 widerlegt
            # die #1086-Vermutung fuer diesen Fall explizit). Telemetrie statt Abbruch (Fix 4.2).
            _store_reuse_prior_run_ids.update(rid for rid in _foreign_windows if rid is not None)
            _store_reuse_n_trials_prior += len(_foreign_run_trials)
            _store_reuse_n_trials_own += len(_own_run_trials)
            _store_reuse_studies_affected += 1

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
            symbol_bar_quality_cache=_symbol_bar_quality_cache, run_id=run_id,
            # Issue #1021/#1196 Akzeptanzkriterium 2 — bei nachgewiesener sequenzieller
            # Store-Wiederverwendung (Trials eines Vorlaufs UND dieses Laufs in derselben Study,
            # der Ueberlappungs-Wächter oben hat NICHT ausgeloest) zaehlt dieser Report
            # ausschliesslich seine EIGENEN Trials, nicht die kumulierte Store-Groesse.
            trials_override=_own_run_trials if _foreign_run_trials else None)
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

    # Issue #982/#1136 (Katalog #986, Pitfall #410 in AGENTS.md) — Store-Scan, UNABHAENGIG von den
    # vom Aufrufer uebergebenen ``proposals``. Root-Cause: der obige Ausschluss-Nachweis sieht NUR
    # Studies, fuer die der Aufrufer ueberhaupt ein Proposal uebergeben hat — die Fail-Fast-Probe
    # uebergibt die im Prozess BISLANG akkumulierte Liste (strukturell unvollstaendig waehrend
    # eines laufenden Sweeps), waehrend NUR ``generate_report_for_run`` (Abbruch-/Standalone-Pfad)
    # zusaetzlich ``{WORK}``/``proposal_*.json`` vollstaendig scannt. Eine Study eines VORHERIGEN,
    # bereits beendeten Fremdlaufs, die diesem Prozess nie als Proposal uebergeben wurde, blieb im
    # Fail-Fast-Pfad dadurch STRUKTURELL unentdeckt — "0 ausgeschlossen" war nicht von "nicht
    # aufgezaehlt" unterscheidbar. Dieser Scan laeuft jetzt HIER, im gemeinsamen Kern, fuer JEDEN
    # Aufrufer identisch — zusaetzliche, im Store gefundene, aber vom Aufrufer nicht uebergebene
    # Proposals werden NUR fuer den Ausschluss-Nachweis klassifiziert (fremd ⇒ in ``studies_
    # excluded_foreign_run`` aufgenommen), nicht in ``studies_out`` — ein Proposal, das der
    # Store-Scan als EIGEN einstuft, aber der Aufrufer nicht kannte, ist eine Aufrufer-
    # Unvollstaendigkeit ausserhalb des Scopes dieses Fixes und wird uebersprungen (kein stiller
    # Study-Zuwachs ausserhalb der vom Aufrufer kontrollierten Kohorte).
    _already_seen_pairs = {
        (p.get("strategy"), p.get("symbol")) for p in proposals if isinstance(p, dict)
    }
    try:
        _store_scan_paths = sorted(
            p for p in Path(WORK).glob("proposal_*.json") if (_load_json(p) or {}).get("symbol"))
    except OSError:
        _store_scan_paths = []
    # Issue #1004/#1156 (Katalog #1170, P1) — Root-Cause: ``n_own`` war FAIL-OPEN
    # (``len(_store_scan_paths) − n_foreign``, ein reiner Komplement-Zaehler): JEDES Proposal, das
    # NICHT nachweislich fremd war, zaehlte automatisch als eigen — inklusive Studies, deren
    # Optuna-Study nicht ladbar war (``_scan_study is None``) oder die 0 Trials hatten (kein
    # Nachweis in IRGENDEINE Richtung). Fix: DREIWERTIGE, POSITIV nachgewiesene Klassifikation.
    # ``n_own`` zaehlt AUSSCHLIESSLICH (a) bereits vom Aufrufer uebergebene Proposals (die
    # ``_already_seen_pairs``, per Konstruktion eigen) UND (b) gescannte Studies mit >= 1 Trial
    # DIESER ``run_id`` — nie mehr per Komplement. ``n_unclassifiable`` macht "nicht ladbar/keine
    # Trials/weder eigen noch fremd" als EIGENE, dritte Zahl sichtbar, statt sie stillschweigend
    # unter ``n_own`` zu verstecken.
    _n_store_scan_own = 0
    _n_store_scan_foreign = 0
    _n_store_scan_unclassifiable = 0
    for _scan_path in _store_scan_paths:
        _scan_proposal = _load_json(_scan_path) or {}
        _scan_key = (_scan_proposal.get("strategy"), _scan_proposal.get("symbol"))
        if _scan_key in _already_seen_pairs:
            _n_store_scan_own += 1
            continue
        _scan_study = _load_study_for_proposal(_scan_proposal)
        _scan_trials = (
            list(getattr(_scan_study, "trials", None) or []) if _scan_study is not None else [])
        if not _scan_trials:
            _n_store_scan_unclassifiable += 1
            _already_seen_pairs.add(_scan_key)
            continue
        _scan_own = [
            t for t in _scan_trials
            if (getattr(t, "user_attrs", None) or {}).get("run_id") == run_id]
        _scan_foreign = [
            t for t in _scan_trials
            if (getattr(t, "user_attrs", None) or {}).get("run_id") not in (None, run_id)]
        if _scan_own:
            _n_store_scan_own += 1
        elif _scan_foreign:
            _n_store_scan_foreign += 1
            _scan_run_id_found = (getattr(_scan_foreign[0], "user_attrs", None) or {}).get("run_id")
            studies_excluded_foreign_run.append({
                "study_name": getattr(_scan_study, "study_name", None),
                "strategy": _scan_proposal.get("strategy"),
                "symbol": _scan_proposal.get("symbol"),
                "run_id_found": _scan_run_id_found,
                "study_started_at_utc": (getattr(_scan_study, "user_attrs", None) or {}).get(
                    "study_started_at_utc"),
                "run_started_at_utc": started_at_utc,
                "n_trials_total_study": len(_scan_trials),
                "reason": "run_id_mismatch",
                "detection": "store_scan",
            })
        else:
            # Weder ein Trial dieser run_id noch eines einer ANDEREN run_id (z. B. alle Trials mit
            # run_id=None, ein Alt-Bestand vor #821) — nachweislich weder eigen noch fremd.
            _n_store_scan_unclassifiable += 1
        _already_seen_pairs.add(_scan_key)
    # Issue #982/#1136 Fix Punkt 2 — macht "0 ausgeschlossen" von "nicht aufgezaehlt" unterscheidbar:
    # ``scan_source`` dokumentiert, OB der Store-Scan ueberhaupt lief (er laeuft jetzt IMMER, seit
    # dieser Fix ihn aus dem alleinigen ``generate_report_for_run``-Pfad in den gemeinsamen Kern
    # gehoben hat).
    store_scan = {
        "n_studies_in_store": len(_store_scan_paths),
        "n_own": _n_store_scan_own,
        "n_foreign": _n_store_scan_foreign,
        # Issue #1004/#1156 — dritte, eigene Zahl statt eines stillen Rueckfalls auf n_own.
        "n_unclassifiable": _n_store_scan_unclassifiable,
        "scan_source": "proposal_glob",
    }
    all_checks.append(("global", _inv.check_store_scan_coherence(store_scan, len(studies_out))))

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

    # Issue #1104 (Katalog #937) — der Commit, auf dem die STUDIES DIESES Laufs tatsaechlich
    # simuliert wurden (vor dem ersten Trial jeder Study gestempelt, siehe run_optimization.py),
    # reduziert auf EINEN Report-weiten Wert: der erste beobachtete nicht-None-Wert (alle Studies
    # eines Laufs laufen unter demselben Checkout). ``None`` ohne jede Study mit dem #1104-Stempel
    # (Legacy-Studies). Einmal berechnet, wiederverwendet fuer die Invariante hier UND das
    # ``git_commit_simulation``-Feld im Report-Dict weiter unten (eine Kennzahl, eine Quelle).
    _git_commit_simulation = next(
        (r.get("git_commit_simulation") for r in studies_out if r.get("git_commit_simulation")),
        None,
    )
    all_checks.append(("global", _inv.check_commit_coherence(_git_commit_simulation, git_commit())))

    n_family_stage1, n_family_stage2 = _family_n_stages(studies_out)
    # Issue #1102 (Katalog #935) — Root-Cause: ``sweep._family_n_from_proposals`` summiert
    # ``deflation_n_eligible`` (die ENGERE, seit #784/#822 veraltete Grundgesamtheit), waehrend
    # ``n_family_stage1`` oben ``deflation_n_family`` (die #822-Grundgesamtheit
    # ``oos_selection_statistic_available``, TATSAECHLICH an confirm.py fuer die DSR-Multiplizitaets-
    # korrektur durchgereicht, samt der #1080-Rueckfall-Behandlung fuer eine Study mit 0
    # Holdout-Trades) traegt — zwei UNABHAENGIG berechnete Zahlen fuer denselben Begriff liefen
    # dadurch um Faktor 2,8-5,1 auseinander (ASML 622 vs. 1722), ``n_family`` war systematisch ZU
    # KLEIN (Φ⁻¹(1-1/n) unterschaetzt SR*, begünstigt jede Promotion mit familienweiter Korrektur).
    # GENAU EINE Quelle jetzt: ``n_family[symbol]`` ist die SUMME seiner eigenen
    # ``n_family_stage1``-Zerlegung — tautologisch kohaerent mit ``check_n_family_partition``
    # (seither ``severity='blocking'``, siehe dort) statt zwei parallel gepflegter Zaehlungen.
    _n_family_by_symbol = {
        symbol: sum(per_strategy.values()) for symbol, per_strategy in n_family_stage1.items()
    }
    # Issue #977/#1131 (Katalog #986) — die EINGEFRORENE Sicht traegt seit diesem Fix den
    # PER-STRATEGIE-Wert je Proposal (siehe sweep._run_confirm_and_export-Stempel-Docstring), NICHT
    # mehr eine bereits symbolweit summierte Konstante — MAX PRO STRATEGIE (dieselbe Idempotenz-
    # Absicherung wie vorher: eine fehlende Study liefert 0 Beitrag, ein doppeltes Proposal
    # DERSELBEN Strategie zaehlt nicht doppelt), dann SUMME ueber die Strategien je Symbol —
    # dieselbe Aggregations-Arithmetik wie ``_n_family_by_symbol`` oben, damit beide Seiten des
    # #1091-Stabilitaets-Vergleichs (``check_family_n_stability``) auf demselben Skalentyp stehen.
    _n_family_frozen_stage1: dict[str, dict[str, int]] = {}
    for _p in filtered_proposals:
        _frozen = _p.get("deflation_n_family_frozen")
        _sym = _p.get("symbol")
        _strat = _p.get("strategy")
        if _sym and _strat and isinstance(_frozen, (int, float)) and not isinstance(_frozen, bool):
            _per_strategy_frozen = _n_family_frozen_stage1.setdefault(_sym, {})
            _per_strategy_frozen[_strat] = max(_per_strategy_frozen.get(_strat, 0), int(_frozen))
    _n_family_frozen_by_symbol: dict[str, int] = {
        symbol: sum(per_strategy.values())
        for symbol, per_strategy in _n_family_frozen_stage1.items()
    }

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

    # Issue #984/#1138 (Katalog #986, Pitfall #409 in AGENTS.md) — #904-Regressionswaechter: bei
    # promotion_family_scope='per_strategy' muessen zwei Studies DESSELBEN Symbols mit
    # verschiedener Trialzahl auch verschiedenes deflation_n_family_raw melden. Diese Invariante
    # ist zugleich der #1131-Fix-Nachweis fuer die Scope-Kohaerenz.
    _study_family_records = [
        {"symbol": r.get("symbol"), "strategy": r.get("strategy"),
         "n_trials": r.get("n_trials_completed"), "deflation_n_family_raw": r.get("deflation_n_family_raw")}
        for r in studies_out
    ]
    all_checks.append(("global", _inv.check_family_scope_coherence(
        _study_family_records,
        promotion_family_scope=(tournament_cfg or {}).get("promotion_family_scope"))))

    # Issue #999/#1151 (Katalog #1170) — Root-Cause der #984/#1138-Verdrahtung: ``COST_MODEL_
    # RESOLVED`` wird im ISOLIERTEN Worker-PROZESS emittiert (backtest_runner.py,
    # ``_logging_cost_model.getLogger("backtest_worker")``, ueber ``ProcessPoolExecutor``) — ein
    # frisches Python-Interpreter-Prozess hat KEINE registrierte JSONL-Sidecar-Pfad-Zuordnung fuer
    # diesen Logger-Namen (``_JSONL_SIDECAR_PATHS`` ist Prozess-lokaler Modul-Zustand, nie ueber
    # ``setup_bot_logging`` im Worker initialisiert). ``_read_jsonl_events(jsonl_sidecar_path(_log.
    # name), ...)`` liest ausserdem den Sidecar des ``"optimizer"``-Loggers (des HAUPT-Prozesses),
    # nicht den des ``"backtest_worker"``-Loggers — selbst ein im Hauptprozess erzeugtes Event
    # würde unter dem falschen Namen gesucht. Beide Checks sahen dadurch STRUKTURELL IMMER 0
    # Events — "emittiert, nie konsumiert", zweite Instanz von #1138, diesmal mit vorhandenem
    # Konsumenten und fehlendem Transportweg.
    #
    # Fix-Entscheidung (beide im Issue als gleichwertig genannten Optionen abgewogen): der
    # transportseitige Fix (Cost-Model-Resolution ueber den Worker-RETURN-Wert an den Hauptprozess
    # zurueckreichen und dort ueber den "optimizer"-Logger REEMITTIEREN, analog
    # ``run_optimization._reemit_inference_diagnostics``/``metrics.inference_diagnostics``) ist die
    # architektonisch korrekte Loesung, aendert aber den heissen Worker-Rueckgabepfad
    # (``TournamentMetrics``/Parsing) an einer Stelle, die in dieser Sandbox (kein installierbares
    # ``nautilus_trader``, kein echter Multi-Prozess-Sweep-Lauf) NICHT end-to-end verifizierbar
    # ist — derselbe Sandbox-Vorbehalt wie #987/#1141 (Katalog #986). Diese Session waehlt deshalb
    # die im Issue EXPLIZIT als gleichwertig zugelassene Alternative: beide Checks werden aus dem
    # Report-Invariantenstrom entfernt und in ``_DELIBERATELY_UNWIRED_INVARIANT_CHECKS``
    # dokumentiert (siehe dort) — der Status quo (Check laeuft, kann strukturell nie etwas sehen,
    # meldet PASS) war die einzige NICHT zulaessige Option. Ein kuenftiger transportseitiger Fix
    # entfernt einfach die beiden Allowlist-Eintraege und fuegt die ``all_checks.append``-Zeilen
    # wieder ein.

    # Issue #984/#1138 — die Verdrahtungs-Meta-Pruefung selbst: haette DIESE Session die fuenf
    # obigen Checks nicht nachgezogen, waere sie hier von sich aus rot gewesen.
    all_checks.append(("global", _invariant_registry_wiring_check()))

    # Issue #1100 (Katalog #933) — symbolweiter Kohaerenz-Waechter: holdout_buyhold_return ist
    # eine reine Preisserien-Kennzahl DES SYMBOLS (PortfolioMonitor.get_benchmark_series), muss
    # also ueber alle Studies desselben Symbols identisch sein, unabhaengig von
    # holdout_total_trades — ein abweichender Nullwert bei 0 Holdout-Trades, waehrend eine
    # Schwester-Study einen echten Marktwert traegt, beweist einen kollabierten Sentinel
    # (#759/#788/#966-Fehlerklasse, siebte Instanz).
    all_checks.append(("global", _inv.check_holdout_buyhold_return_coherence(studies_out)))

    # Issue #1098 (Katalog #931) — Vollstaendigkeits-Wächter für die JSONL-Event-Sidecar (#741):
    # ``expected_*`` wird HIER, UNABHAENGIG von der jsonl-Datei selbst, aus ``studies_out`` (der
    # bereits fuer Report/Invarianten kanonischen Kohorte) berechnet und als EVENTS_MANIFEST-
    # Ereignis geschrieben — NUR fuer ``report_source == 'final'`` (Zwischen-/Probe-Reports sehen
    # strukturell eine Teilmenge der Studies, siehe #1083, ein Manifest darauf waere bedeutungslos
    # und wuerde die jsonl-Datei mit irrefuehrenden Zwischenstaenden fluten). ``check_event_stream_
    # completeness`` vergleicht das Manifest gegen die TATSAECHLICH gezaehlten jsonl-Zeilen
    # (``_count_jsonl_events``) — ein Auseinanderlaufen beweist einen erneuten Ereignisverlust
    # (#1098-Fehlerklasse: nicht-atomarer Zeilen-Append unter Nebenlaeufigkeit).
    if report_source == "final":
        _expected_trial_events = sum(int(r.get("n_trials_completed") or 0) for r in studies_out)
        _expected_study_events = len(studies_out)
        emit_execution_event(_log, "EVENTS_MANIFEST", {
            "expected_trial_events": _expected_trial_events,
            "expected_study_events": _expected_study_events,
        })
        _event_counts = _count_jsonl_events(
            jsonl_sidecar_path(_log.name),
            {"optimizer_trial_completed", "optimizer_study_completed"})
        all_checks.append(("global", _inv.check_event_stream_completeness(
            _expected_trial_events, _event_counts["optimizer_trial_completed"],
            _expected_study_events, _event_counts["optimizer_study_completed"])))
    else:
        all_checks.append(("global", _inv.check_event_stream_completeness(None, 0, None, 0)))

    # Issue #770 — sweep-weite Budget-Ausfuehrungs-Invariante (siebter Check, siehe #743/#773).
    min_median_budget_execution = float(optimizer_cfg.get("min_median_budget_execution", 0.5))
    budget_check = _inv.check_budget_execution(studies_out, min_median=min_median_budget_execution)
    all_checks.append(("global", budget_check))

    # Issue #940/#1106 (Katalog #960) — ``check_report_cohort_coherence`` urteilt seit diesem Fix
    # ausschliesslich ueber Kohorten-IDENTITAET (``record['run_id'] == run_id``), nicht mehr ueber
    # Zeit: die vorherige Zeitfassung war strukturell blind fuer einen zeitlich vollstaendig
    # enthaltenen Nachbarlauf (B-3, siehe Docstring dort). Die ehemaligen drei Zeitklauseln laufen
    # separat als reine Uhr-Drift-Diagnose (``check_cohort_clock_drift``, severity ``low``).
    all_checks.append(("global", _inv.check_report_cohort_coherence(studies_out, run_id=run_id)))
    all_checks.append(("global", _inv.check_cohort_clock_drift(
        studies_out, wallclock_s=wallclock_s, run_started_at_utc=started_at_utc)))
    # Issue #940/#1106 Fix Punkt 3 — die ECHTE zweite Verteidigungslinie auf einer ANDEREN
    # Evidenzachse (der forensische Ereignisstrom statt Trial-``user_attrs``); nur fuer
    # ``report_source == 'final'`` ausgewertet, aus demselben Grund wie die #1098-Vollstaendigkeits-
    # pruefung oben (Zwischen-/Probe-Reports sehen strukturell nur eine Teilmenge der Studies, ein
    # Ereignisabgleich waere dort bedeutungslos).
    if report_source == "final":
        _own_study_completed_events = _read_jsonl_events(
            jsonl_sidecar_path(_log.name), "optimizer_study_completed")
        all_checks.append(("global", _inv.check_report_cohort_event_stream_coherence(
            studies_out, run_id=run_id, study_completed_events=_own_study_completed_events)))
    else:
        all_checks.append(("global", _inv.check_report_cohort_event_stream_coherence(
            studies_out, run_id=None, study_completed_events=None)))

    # Issue #1038 (Katalog #866), umbenannt #949/#1115 — vorab berechnet (statt erst im Report-Dict
    # unten), damit die Invariante denselben Wert prueft, der auch angezeigt wird (eine Kennzahl,
    # eine Quelle).
    _worker_occupancy_wallclock_value = _worker_occupancy_wallclock(
        studies_out, n_jobs=(cli_args or {}).get("n_jobs"), sweep_wallclock_s=wallclock_s)
    all_checks.append((
        "global", _inv.check_worker_utilisation_plausible(
            _worker_occupancy_wallclock_value, n_studies=len(studies_out))))

    # Issue #1031 (Katalog #866) — Kohaerenz zwischen expectancy und expectancy_capital_weighted.
    all_checks.append(("global", _inv.check_expectancy_definition_coherence(studies_out)))

    # Issue #945/#1111 (Katalog #960) — blockierender Regressionswaechter: die Kostenstress-Leiter
    # (abgeleitet aus holdout_expectancy_capital_weighted) muss monoton fallend und gleich gestuft
    # gegenueber DERSELBEN Basis sein, gegen die sie berichtet wird.
    all_checks.append(("global", _inv.check_cost_stress_monotonicity(studies_out)))
    # Issue #1010/#1162 (Katalog #1170) — macht sichtbar, wenn die 'full_realism'-Kostenstufe durch
    # ueberall 0.0 konfigurierte financing_bps/slippage_bps (backtest.json, #987/#1141) faktisch ein
    # No-Op ist, statt es stillschweigend als "keine Wirkung" zu akzeptieren.
    all_checks.append(("global", _inv.check_cost_stress_distinctness(studies_out)))
    # Issue #1013/#1165 (Katalog #1170) — macht sichtbar, wenn Abschnitt 2.3 der Zusammenfassung
    # eine Study spurlos verliert (z. B. ein nicht ausgewerteter Holdout ohne eigenen Bucket).
    all_checks.append(("global", _inv.check_summary_row_completeness(studies_out)))

    # Issue #948/#1114 (Katalog #960, ersetzt #978) — seit diesem Fix eine SWEEP-WEITE Diagnose
    # (severity 'low'): die Streuung des EINEN studienweiten Annualisierungsfaktors ueber Studies
    # DESSELBEN Symbols, statt der frueheren, trivialen Intra-Trial-Fold-Streuung (99,15% der
    # Trials betroffen, B-10) — braucht deshalb ``studies_out`` (alle Studies des Sweeps), nicht
    # mehr die Trials EINER einzelnen Study.
    all_checks.append(("global", _inv.check_annualization_commensurability(studies_out)))

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
    _cost_basis_symbols = sorted({r.get("symbol") for r in studies_out if r.get("symbol")})
    _atr_floor_by_symbol, _atr_floor_resolution_errors = _atr_floor_bps_by_symbol(_cost_basis_symbols)
    # Issue #951/#1117 (Katalog #960) — der Floor ist seit #1096 Fix Punkt 1 selbst
    # COST-GEKOPPELT (backtest_runner.cost_coupled_atr_floor_bps) und variiert damit PRO STUDY
    # (über atr_trailing_multiplier_median), nicht mehr nur pro Symbol/Asset-Klasse. Vorgezogen
    # vor check_atr_scale_homogeneity (statt an seiner alten Stelle nahe check_stop_cost_ratio
    # unten), weil die Homogenitäts-Prüfung diesen abgeleiteten Wert bereits als Floor-Referenz
    # konsumiert (Akzeptanzkriterium #951: "Der Floor-Wert erscheint im Report als abgeleitete,
    # nicht als konfigurierte Grösse").
    _min_stop_to_cost_ratio = float(tournament_cfg.get("min_stop_to_cost_ratio", 3.0))
    _round_trip_cost_bps_by_symbol_map, _c_rt_resolution_errors = _round_trip_cost_bps_by_symbol(
        _cost_basis_symbols)
    # Issue #998/#1150 (Katalog #1170, Pitfall #380-Klasse) — Root-Cause: beide Resolver fingen
    # JEDEN Fehler je Symbol stumm ab (``except Exception: continue``); ein leeres Ergebnis-Dict
    # war dadurch NICHT von "der Floor bindet nirgends" (die #1096-Abnahme) unterscheidbar, sondern
    # bedeutete "der Floor ist unbekannt". ``cross_study.cost_model_resolution`` macht das
    # UNTERSCHEIDBAR: ``n_resolved`` zaehlt Symbole mit MINDESTENS EINER erfolgreich aufgeloesten
    # Kostenbasis (ATR-Floor ODER c_rt); ``errors`` traegt die Fehlermeldung je Symbol UND Quelle.
    _cost_model_resolution_errors = {
        s: {k: v for k, v in {
            "atr_floor": _atr_floor_resolution_errors.get(s),
            "round_trip_cost": _c_rt_resolution_errors.get(s),
        }.items() if v is not None}
        for s in _cost_basis_symbols
        if s in _atr_floor_resolution_errors or s in _c_rt_resolution_errors
    }
    _n_cost_basis_resolved = sum(
        1 for s in _cost_basis_symbols
        if s in _atr_floor_by_symbol or s in _round_trip_cost_bps_by_symbol_map)
    _cost_model_resolution = {
        "n_symbols": len(_cost_basis_symbols),
        "n_resolved": _n_cost_basis_resolved,
        "errors": _cost_model_resolution_errors,
    }
    all_checks.append(("global", _inv.check_cost_basis_resolution(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol,
        round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        resolution_errors=_cost_model_resolution_errors)))
    # Issue #1011/#1163 (Katalog #1170) — macht sichtbar, wenn die synthetische 1h-Bar-Erzeugung
    # fuer EQUITY/COMMODITY ueber einen 24/7-Kalender laeuft (keine Handelszeiten-Maske), statt
    # implizit RTH-Bars zu unterstellen. Nach ``_cost_basis_symbols`` platziert (dieselbe Symbol-
    # Menge wie die Kostenbasis-Aufloesung oben).
    all_checks.append(("global", _inv.check_session_calendar_coherence(
        studies_out, asset_class_by_symbol=_asset_class_by_symbol(_cost_basis_symbols))))
    _stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol,
        round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        min_stop_to_cost_ratio=_min_stop_to_cost_ratio)
    atr_scale_homogeneity_check = _inv.check_atr_scale_homogeneity(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol)
    all_checks.append(("global", atr_scale_homogeneity_check))
    # Issue #1028/#1177 (Katalog #866-2) — die Mikrostruktur-Untergrenze ist erst nach #1171
    # (bar_range_median_bps) messbar; siehe check_stop_distance_microstructure_floor-Docstring.
    all_checks.append(("global", _inv.check_stop_distance_microstructure_floor(studies_out)))
    # Issue #1029/#1178 (Katalog #866-2) — dieselbe Kohorte, macht die gemessene Fill-Slippage
    # materiell sichtbar statt sie stillschweigend zu ignorieren.
    all_checks.append(("global", _inv.check_stop_exit_slippage_materiality(studies_out)))

    # Issue #1042 (Katalog #866) E-2 — Sichtbarkeits-Wächter: divergiert das im Backtest
    # konfigurierte trade_amount_pct vom live tatsächlich gefahrenen MomentumLSAllocator-Deckel.
    # Issue #1014/#1166 (Katalog #1170) — parity_factor durchgereicht, damit eine BEWUSST
    # dokumentierte Abweichung (backtest.json['live_risk']['trade_amount_pct_parity_factor'])
    # nicht mehr als 15 gleichlautende FAILs erscheint (siehe dortiger Docstring).
    all_checks.append((
        "global", _inv.check_sizing_parity_backtest_vs_allocator(
            _trade_amount_pct_map, max_symbol_exposure_fraction=_max_symbol_exposure_fraction(),
            parity_factor=_trade_amount_pct_parity_factor())))

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
    # bevor die Kosten sie auffressen. ``min_stop_to_cost_ratio``/``_round_trip_cost_bps_by_symbol_
    # map`` bereits oben (Issue #951/#1117) für ``_stamp_atr_floor_bps_derived`` aufgelöst —
    # dieselben Werte, wiederverwendet statt erneut gelesen.
    all_checks.append(("global", _inv.check_stop_cost_ratio(
        studies_out, round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        min_stop_to_cost_ratio=_min_stop_to_cost_ratio)))

    # Issue #1093 (Katalog #926) — Kalibrierungswaechter fuer #1092/#1094: der Trailing-Stop darf
    # nicht der haeufigste, verlustreichste UND teuerste Ausgang einer Study sein.
    all_checks.append(("global", _inv.check_trailing_stop_loss_share(
        studies_out,
        max_loss_share=float(optimizer_cfg.get("trailing_stop_max_loss_share", 0.60)),
        # Issue #1024/#1173 — umbenannt von 'trailing_stop_max_mean_loss_ratio': Zaehler UND
        # Nenner sind seither dieselbe Statistik (Median), siehe check_trailing_stop_loss_share-
        # Docstring.
        max_median_loss_ratio=float(
            optimizer_cfg.get("trailing_stop_max_median_loss_ratio", 1.25)))))

    # Issue #950/#1116 (Katalog #960) — die verbindliche SWEEP-WEITE Abnahmemessung fuer die
    # #1092/#1094-Hypothese (drei Kriterien: Spearman(k*ATR, realisierter Verlust) >= 0.3,
    # realized_stop_loss_ratio in [0.8, 3.0] fuer >= 80% der Studies, gepoolter TRAILING_STOP-
    # Anteil < 35%) — strenger als die permanente check_effective_stop_distance-Schranke und die
    # per-Study check_trailing_stop_loss_share-Symptomschwelle oben, weil sie EIN holistisches
    # Urteil ueber den gesamten Sweep faellt statt je Study einzeln zu urteilen.
    all_checks.append(("global", _inv.check_trailing_stop_risk_calibration_acceptance(studies_out)))

    # Issue #953/#1119 (Katalog #960) — blockierender Regressionswaechter gegen die konkurrierende
    # Hypothese zu #950/#1092: ist der Stop-Verlust latenz- statt stopgetrieben (Verlust in
    # derselben Groessenordnung wie EINE Bar-Spanne, UND gleichzeitig ein grosses Vielfaches der
    # konfigurierten Stopdistanz), ist jede Stop-Parametrisierung wirkungslos.
    all_checks.append(("global", _inv.check_stop_loss_vs_bar_range(studies_out)))

    # Issue #973/#1127 (Pitfall #406 in AGENTS.md) — alarmiert VON SICH AUS, wenn ein Telemetriefeld
    # ueber die gesamte Grundgesamtheit konstant null ist (z. B. der 112/112-bar_range_median_bps-
    # Fall), statt dass eine fehlende Emissionskette nur indirekt ueber einen nachgelagerten
    # INCONCLUSIVE-Check (check_stop_loss_vs_bar_range) auffaellt.
    all_checks.append(("global", _inv.check_exit_telemetry_completeness(studies_out)))

    # Issue #1016/#1168 (Katalog #1170) — dieselbe Beobachtbarkeits-Logik wie #973/#1127 direkt
    # oben, hier fuer symbol_bar_quality (Root-Cause #1168: None in 28/28 Studies zweier Läufe).
    all_checks.append(("global", _inv.check_symbol_bar_quality_cache_availability(
        studies_out, cache_path=_symbol_bar_quality_cache_status.get("cache_path"),
        cache_found=_symbol_bar_quality_cache_status.get("cache_found", False))))

    # Issue #1097 (Katalog #930) — Teilmengen-Schranke zwischen gepoolten Verlust-Aggregaten;
    # siehe check_loss_metric_commensurability-Docstring.
    all_checks.append(("global", _inv.check_loss_metric_commensurability(studies_out)))

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

    # Issue #1099 (Katalog #932) — Kalibrierungswaechter fuer den #1099-Fix selbst: eine UNABHAENGIG
    # (ueber _count_jsonl_events statt _read_jsonl_events) erneut ausgezaehlte CHAMPION_WRITEBACK-
    # Ereigniszahl muss exakt ``champions_summary['attempts']`` entsprechen — ein Wiederauftreten der
    # #1099-Fehlerklasse (z. B. eine kuenftige Regression, die die studies_out-Rekonstruktion
    # faelschlich wieder bevorzugt, obwohl ein Ereignisstrom verfuegbar waere) wird sichtbar statt
    # eines erneut stillen Auseinanderlaufens.
    _champion_events_path = jsonl_sidecar_path(_log.name)
    all_checks.append(("global", _inv.check_champion_attempt_coherence(
        champions_summary.get("attempts"),
        _count_jsonl_events(_champion_events_path, {"CHAMPION_WRITEBACK"})["CHAMPION_WRITEBACK"]
        if _champion_events_path is not None else None,
    )))

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

    # Issue #956/#1122 (Katalog #960) — check_gate_inventory_coherence (#1076) entfernt: seit
    # gate_inventory_table die is_rejection_detail_counts-Zaehlung DIREKT uebernimmt (statt
    # parallel aus oos_gate_deltas abzuleiten), ist n_rejections[g] == is_rejection_detail_counts[
    # code(g)] per Konstruktion — die Kreuzpruefung waere jetzt eine Tautologie.

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

    # Issue #923 Fix 4 — n_periods streut innerhalb desselben Symbols stark je Strategie; ab einem
    # Faktor > deflation_max_n_periods_ratio-Kalibrierpunkt (Default 6.0 hier, 4.0 dort) ist die
    # Kommensurabilität der symbolweiten Ranglisten/Annualisierung betroffen.
    # Issue #1012/#1164 (Katalog #1170) — promotion_family_scope durchgereicht, damit der
    # #865-Verweis im Meldungstext NUR erscheint, wenn er unter dem tatsaechlichen Scope
    # ueberhaupt zutreffen kann (siehe check_n_periods_homogeneity-Docstring).
    n_periods_homogeneity_check = _inv.check_n_periods_homogeneity(
        studies_out,
        promotion_family_scope=(tournament_cfg or {}).get("promotion_family_scope"))
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

    # Issue #983/#1137 (Katalog #986, Pitfall #410 in AGENTS.md) — EINE Quelle fuer "darf einen
    # Sweep abbrechen": severity='blocking' IMPLIZIERT fail-fast-Faehigkeit; fail_fast_invariants
    # darf nur Namen von Checks listen, deren beobachtete severity in DIESEM Lauf 'blocking' war.
    # Muss ebenfalls NACH allen anderen Checks stehen (braucht ihre severity-Werte).
    fail_fast_are_blocking_check = _inv.check_fail_fast_invariants_are_blocking(
        _already_evaluated_dicts, fail_fast_invariants=optimizer_cfg.get("fail_fast_invariants"))
    all_checks.append(("global", fail_fast_are_blocking_check))

    # Issue #941/#1107 (Katalog #960) — JEDER Eintrag in ``invariant_checks`` traegt am Ende die
    # Population, die er tatsaechlich gesehen hat (``InvariantResult.cohort``-Docstring): ein Check,
    # der derselbe Fail-Fast-Probe-Auswertung UND dem finalen Report unter demselben Namen begegnet,
    # darf nicht mehr stillschweigend zwei verschiedene Grundgesamtheiten hinter einer Zahl
    # verstecken (Root-Cause B-2: 12/13/13 Offender im Fail-Fast-Pfad gegen 25/38/38 im Report-Pfad
    # fuer ``check_effective_stop_distance``, ohne Kohorten-Deklaration ununterscheidbar von einem
    # echten Widerspruch). Nur Checks stempeln, die noch KEIN ``cohort`` selbst gesetzt haben
    # (``dataclasses.replace`` auf dem frozen ``InvariantResult``).
    _cohort_descriptor = _inv.build_cohort_descriptor(
        studies_out, run_id=run_id, report_source=report_source)
    all_checks = [
        (label, result if result.cohort is not None
         else dataclasses.replace(result, cohort=_cohort_descriptor))
        for label, result in all_checks
    ]

    # Issue #941/#1107 Fix — die Nachlauf-Pruefung: vergleicht die soeben gestempelten Kohorten-
    # Deklarationen gegen eine FRUEHERE In-Process-Probe DESSELBEN Laufs (``sweep.py``s Fail-Fast-
    # Vorlauf, sofern einer stattfand — ``prior_probe_invariant_checks`` wird vom Aufrufer
    # durchgereicht). Muss NACH der Kohorten-Stempelung stehen (braucht ``cohort`` auf jedem
    # bereits ausgewerteten Check), deshalb selbst separat gestempelt statt in der Bulk-Liste oben.
    cohort_consistency_check = dataclasses.replace(
        _inv.check_cohort_declaration_consistency(
            [c.to_dict() for _label, c in all_checks],
            prior_probe_checks=prior_probe_invariant_checks),
        cohort=_cohort_descriptor,
    )
    all_checks.append(("global", cohort_consistency_check))

    invariant_checks = []
    for label, result in all_checks:
        d = result.to_dict()
        d["scope"] = label
        # Issue #1015/#1167 (Katalog #1170) — jeder Eintrag traegt seine Herkunft: diese Checks
        # laufen alle IN ``_build_report`` selbst (Report-Prozess).
        d["source"] = "report"
        invariant_checks.append(d)
        if not result.passed:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": label, "check": result.name,
                "expected": result.expected, "actual": result.actual, "detail": result.detail,
                # Issue #1083 — welche Auswertungswelle dieses Event traegt (siehe Docstring oben).
                "report_source": report_source,
            }, level=logging.ERROR)

    # Issue #985/#1139 (Katalog #986, Pitfall #411 in AGENTS.md) — die Preflight-Check-Ergebnisse
    # (``sweep.assert_required_config_keys_valid``/``assert_instrument_metadata_coherence``, bereits
    # als Dicts mit ``phase="preflight"`` vom Aufrufer gestempelt) direkt in ``invariant_checks``
    # gemischt — VOR diesem Fix bestand fuer einen BESTANDENEN Preflight kein Nachweis im
    # Report-Artefakt, nur der stderr+Exit-Code-2-Pfad beim Scheitern. Kein ``cohort``-Stempel (die
    # Preflight-Checks pruefen Config-/Instrument-Dateien, keine Study-Population) — ``scope`` analog
    # den uebrigen Eintraegen ergaenzt, sofern nicht bereits gesetzt.
    for d in (preflight_invariant_checks or []):
        d = dict(d)
        d.setdefault("scope", "preflight")
        # Issue #1015/#1167 (Katalog #1170) — Preflight-Checks (sweep.assert_required_config_keys_
        # valid/assert_instrument_metadata_coherence) laufen im SWEEP-Prozess, VOR jedem Worker.
        d.setdefault("source", "sweep")
        invariant_checks.append(d)
        if not d.get("passed", True):
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": d.get("scope", "preflight"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.ERROR)

    # Issue #1015/#1167 (Katalog #1170) — Ergebnisse der AUSSERHALB von ``_build_report`` laufenden
    # Checks (Sweep-Hauptschleife/``run_optimization.py``, siehe ``_read_external_invariant_
    # results``-Docstring), als ``INVARIANT_STREAM_RESULT``-Events aus demselben "optimizer"-Sidecar
    # gelesen, den ``_champions_summary``/``check_event_stream_completeness`` bereits nutzen.
    # Gleiche Behandlung wie der Preflight-Block oben (kein ``cohort``-Stempel — diese Checks
    # pruefen keine Study-Population dieses Reports, sondern Sweep-/Study-weite Bedingungen).
    for d in _read_external_invariant_results():
        invariant_checks.append(d)
        if not d.get("passed", True):
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": d.get("scope"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.ERROR)

    # Issue #1015/#1167 (Katalog #1170) — die neue Meta-Invariante selbst: erschien jede definierte
    # check_*-Funktion im soeben zusammengefuehrten Strom oder auf der Allowlist? Muss NACH JEDEM
    # Merge oben stehen (braucht den finalen ``invariant_checks``-Stand), daher direkt angehaengt
    # statt ueber ``all_checks`` (dessen invariant_checks-Aufbau bereits abgeschlossen ist).
    _stream_check_names = sorted({
        d.get("check") or d.get("name") for d in invariant_checks if d.get("check") or d.get("name")
    })
    invariant_coverage_check = _inv.check_invariant_coverage(
        _all_defined_check_names(), _stream_check_names,
        allowlisted_check_names=list(_DELIBERATELY_UNWIRED_INVARIANT_CHECKS))
    _coverage_dict = invariant_coverage_check.to_dict()
    _coverage_dict["scope"] = "global"
    _coverage_dict["source"] = "report"
    invariant_checks.append(_coverage_dict)
    if not invariant_coverage_check.passed:
        emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
            "scope": "global", "check": invariant_coverage_check.name,
            "expected": invariant_coverage_check.expected, "actual": invariant_coverage_check.actual,
            "detail": invariant_coverage_check.detail, "report_source": report_source,
        }, level=logging.ERROR)

    # Issue #942/#1108 (Katalog #960) — die drei orthogonalen Achsen, EINMAL hier aus der bereits
    # vorliegenden Wahrheit abgeleitet (dieselbe Quelle fuer JEDEN Aufrufer/Pfad, siehe Docstring
    # oben): kein zweites, unabhaengig gesetztes Statuswort mehr.
    _decision_admissible = _compute_decision_admissible(invariant_checks)
    _work_completed = _compute_work_completed(symbols_completed, symbols_planned)

    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        # Issue #1104 (Katalog #937) — GETRENNTE Felder statt des vorherigen mehrdeutigen
        # ``git_commit``: ``git_commit_simulation`` (wann die TRIALS liefen) vs.
        # ``git_commit_report`` (wann DIESER Report gebaut wurde) — ein nachtraeglich regenerierter
        # Report macht die Divergenz jetzt explizit sichtbar/pruefbar
        # (``invariants.check_commit_coherence``), statt sie unter einem einzigen Feldnamen zu
        # verstecken.
        "git_commit_simulation": _git_commit_simulation,
        "git_commit_report": git_commit(),
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
        # Issue #942/#1108 (Katalog #960) — die drei orthogonalen Achsen, die ``run_status`` (oben,
        # aus Rueckwaertskompatibilitaetsgruenden UNVERAENDERT erhalten) NICHT eindeutig genug
        # ausdrueckt:
        #   work_completed      — alle geplanten Symbole tatsaechlich abgeschlossen (None = unbekannt,
        #                          weder Checkpoint noch In-Prozess-Spiegel verfuegbar).
        #   decision_admissible — keine ``severity='blocking'``-Invariante FAILt in diesem Report.
        #   fail_fast_triggered — Name der Fail-Fast-Invariante, die den Sweep abgebrochen hat, oder
        #                          None (kein Fail-Fast-Abbruch in diesem Lauf).
        # Root-Cause #1108: derselbe Faktenstand (14/14 Studies, volles Budget, Fail-Fast-Abbruch
        # NACH Abschluss der Arbeit) ergab ``completed_invalid`` ("vollstaendig gerechnet") in zwei
        # Reports und ``aborted_invariant`` ("echter Arbeitsabbruch") in einem dritten — dieselben
        # Fakten, zwei sich WIDERSPRECHENDE Lesarten desselben ueberladenen Strings.
        # ``summary_de.py`` formuliert seine Kern-Aussage aus DIESEN drei Feldern, nicht mehr aus
        # ``run_status`` allein (siehe dortige Sektion 1).
        "work_completed": _work_completed,
        "decision_admissible": _decision_admissible,
        "fail_fast_triggered": fail_fast_triggered,
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
        # Issue #982/#1136 (Katalog #986) — {n_studies_in_store, n_own, n_foreign, scan_source}:
        # macht "0 ausgeschlossen" (Scan lief, Store war sauber) von "nicht aufgezaehlt" (kein Scan)
        # unterscheidbar, unabhaengig von fail_fast_triggered/report_source.
        "store_scan": store_scan,
        "cross_study": {
            # Issue #998/#1150 (Katalog #1170) — macht die Kostenbasis-Aufloesung (ATR-Floor UND
            # c_rt, je Symbol) UNTERSCHEIDBAR von "der Floor bindet nirgends" (siehe
            # check_cost_basis_resolution/_atr_floor_bps_by_symbol-Docstring).
            "cost_model_resolution": _cost_model_resolution,
            # Issue #1010/#1162 (Katalog #1170) — True, wenn die 'full_realism'-Kostenstress-Stufe
            # ein No-Op ist (financing_bps/slippage_bps ueberall 0.0 in backtest.json). Traeger fuer
            # summary_de.py Abschnitt 2.4 (die einzige erlaubte Datenquelle dort ist dieses Report-
            # JSON, siehe dortiger Docstring) und unabhaengig vom check_cost_stress_distinctness-
            # Verdikt selbst (die Methodik-Einschraenkung gilt, sobald konfiguriert, unabhaengig
            # davon, ob genug Studies mit Trades vorlagen, um den Check auszuloesen).
            "cost_model_zero_realism": _cost_model_has_zero_realism(),
            # Issue #1016/#1168 (Katalog #1170) — {cache_path, cache_found}: macht "Cache-Datei
            # fehlt komplett" von "Cache existiert, Feld trotzdem None" unterscheidbar (Root-Cause
            # #1168: symbol_bar_quality war in 28/28 Studies zweier Läufe still None). Traeger fuer
            # check_symbol_bar_quality_cache_availability, siehe dortiger Docstring.
            "symbol_bar_quality_cache": _symbol_bar_quality_cache_status,
            # Issue #1021/#1196 Fix 4.2 — macht sichtbar, dass dieser Lauf per Warm-Start (Optuna
            # ``load_if_exists``) auf Trials eines VORLAUFS aufsetzt, statt es unsichtbar in
            # ``deflation_n_family``/``constraint_improvement_rate``/dem TPE-Seed verschwinden zu
            # lassen. ``reused=False`` bei jeder Study strikt eigener Kohorte (frischer Store).
            "store_reuse": {
                "reused": _store_reuse_studies_affected > 0,
                "prior_run_ids": sorted(_store_reuse_prior_run_ids),
                "n_trials_prior": _store_reuse_n_trials_prior,
                "n_trials_own": _store_reuse_n_trials_own,
                "studies_affected": _store_reuse_studies_affected,
                "warm_start_effective": _store_reuse_studies_affected > 0,
            },
            # Issue #1091 (Katalog #924) — {frozen, observed_at_report_time} statt eines nackten
            # int je Symbol: "frozen" (budget-basiert, siehe sweep._family_n_frozen_from_studies)
            # ist ueber mehrere Reports DESSELBEN Laufs bit-identisch; "observed_at_report_time"
            # (die Alt-Zahl, #625) bleibt als Diagnose-Telemetrie erhalten — check_family_n_
            # stability (invariants.py) vergleicht beide.
            # Issue #1005/#1157 (Katalog #1170) — dieselben zwei Zahlen trugen im selben Lauf-Artefakt
            # DENSELBEN Feldnamen ("n_family") wie das Sweep-Ereignis (siehe sweep.py) UND wie
            # ``n_family_stage1``/``n_family_stage2`` unten (dort eine PER-STRATEGIE-Zerlegung, hier
            # die SYMBOLWEITE Summe) — drei numerisch verschiedene Groessen, ein Name. Eindeutige
            # Namen entlang der bereits dokumentierten Semantik (#1091/#826): ``n_family_stage1_sum_
            # frozen``/``n_family_stage1_sum_observed`` machen explizit, dass beide die SUMME der
            # ``n_family_stage1``-Zerlegung sind (siehe ``_n_family_by_symbol``-Kommentar oben).
            "n_family_stage1_sum_frozen": _n_family_frozen_by_symbol,
            "n_family_stage1_sum_observed": _n_family_by_symbol,
            # Konvention #1081 — Uebergangs-Alias eine Sitzung lang: alte Konsumenten (z. B.
            # test_issue_1102) lesen dieselben, unveraenderten Werte unter dem alten, verschachtelten
            # Namen weiter.
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
            # Issue #1071/#1026/#1175 — Studies, deren ROHER (ungefloorter) ATR-Median unter dem
            # konfigurierten ATR-Floor liegt (siehe invariants.check_atr_scale_homogeneity-
            # Docstring: ``atr_raw_median_bps < atr_floor_bps_derived``, unabhaengig vom
            # Spannweiten-Offender-Status des Symbols). ``evaluable=False`` (statt einer stillen
            # leeren ``studies``-Liste), wenn KEINE Study beide Eingangsfelder traegt — eine leere
            # Liste war zuvor von "nicht gemessen" nicht unterscheidbar (Akzeptanzkriterium 3).
            "atr_floor_binding_studies": (
                lambda _prov: {
                    "evaluable": bool(_prov.get("atr_floor_binding_evaluable", False)),
                    "studies": sorted(_prov.get("atr_floor_binding_studies") or []),
                    "detail": _prov.get("atr_floor_binding_studies_detail") or {},
                }
            )(atr_scale_homogeneity_check.provenance or {}),
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
            # Issue #949/#1115 (Katalog #960) — zwei GETRENNTE, eindeutig benannte Auslastungs-
            # Groessen statt der vorherigen ``worker_utilisation``/``worker_utilisation_backtest_ms``
            # (beide implizit "Worker-Auslastung" genannt, B-6: 0,7583/1,1251/1,1360 hier gegen
            # 60,2/89,6/90,5% bei der jeweils anderen Groesse im selben Dokument):
            #   worker_occupancy_wallclock = Σ Study-Wallclock / (n_jobs × Sweep-Wallclock)
            #       — kann > 1.0 liegen (verschachtelte Worker-Pools/Ueberlappung), siehe
            #       _worker_occupancy_wallclock-Docstring; check_worker_utilisation_plausible prueft
            #       GENAU diese Groesse.
            #   cpu_utilisation_backtest = Σ Backtest-CPU-Zeit je Trial / (n_jobs × Sweep-Wallclock)
            #       — die ueberlappungsfreie, physikalisch <= 1.0 begrenzte Auslastung.
            "worker_occupancy_wallclock": _worker_occupancy_wallclock_value,
            "cpu_utilisation_backtest": _cpu_utilisation_backtest(
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
    prior_probe_invariant_checks: list[dict] | None = None,
    fail_fast_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
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
        prior_probe_invariant_checks=prior_probe_invariant_checks,
        fail_fast_triggered=fail_fast_triggered,
        preflight_invariant_checks=preflight_invariant_checks,
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
    prior_probe_invariant_checks: list[dict] | None = None,
    fail_fast_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
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
        prior_probe_invariant_checks=prior_probe_invariant_checks,
        fail_fast_triggered=fail_fast_triggered,
        preflight_invariant_checks=preflight_invariant_checks,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
