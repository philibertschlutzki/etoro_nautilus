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
import hashlib
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
from automation.optimizer.manifest import WORK, PERSISTENT_CACHE_ROOT, RUN_FINGERPRINT_INDEX_PATH, git_commit, catalog_fingerprint, sha256_file, write_json_atomic, library_versions, append_jsonl_atomic, read_jsonl
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


def _cost_model_realism_from_applied(
    studies: list[dict], base_cfg: Path | None = None,
) -> tuple[bool, str, list[str]]:
    """Issue #1077/#1225 (P1) — ``_cost_model_has_zero_realism`` liest ``backtest.json``, die
    KONFIGURIERTEN Platzhalter — seit #1055/#1204 stammt die real ANGEWANDTE Slippage jedoch aus
    dem Kalibrierungs-Cache, nicht mehr aus dieser Konfiguration. Root-Cause des #1225-Symptoms:
    das Flag war in 11/11 Läufen ``true`` (weil ``backtest.json`` unkalibrierte 0,0-Platzhalter
    trägt), obwohl ``full_realism`` auf 7 von 8 Symbolen tatsächlich 45,8–115,5 bps abzog — die
    Warnung war dort falsch, auf dem achten Symbol (TSLA) richtig, aber aus dem falschen Grund
    (Symbol-Override-Lücke, #1075/#1223, nicht die globale Konfiguration).

    Leitet Zero-Realism seither aus den mit #1075/#1223 gestempelten ``applied_*``-Feldern JEDER
    Study ab (dieselbe, einzige erlaubte Datenquelle wie jede andere Zeile in ``summary_de.py``,
    kein zweiter ``backtest.json``-Lesezugriff). Rückgabe ``(cost_model_zero_realism,
    cost_model_realism_source, zero_realism_symbols)``:
      - ``config_zero``: ALLE Studies mit aufgelösten ``applied_*``-Feldern sind 0,0 —
        ``cost_model_zero_realism=True``, identisch zur bisherigen Warnung.
      - ``calibrated_cache``: KEINE Study ist 0,0 — ``cost_model_zero_realism=False``, keine
        Warnung mehr nötig.
      - ``mixed``: ein Teil der Studies ist 0,0 (typischerweise ein Symbol-Override, das die
        Asset-Class-Aufloesung fuer Finanzierung/Slippage umgeht), ein Teil traegt reale Werte —
        ``cost_model_zero_realism=False`` (die Aussage "ALLES ist 0,0" waere falsch), die
        betroffenen Symbole werden namentlich in ``zero_realism_symbols`` zurueckgegeben, damit
        §2.4 sie NENNEN statt sie unter einem pauschalen Verdikt zu verstecken.

    Fallback ohne EINE klassifizierbare Study (kein Run-Studies mit aufgeloesten ``applied_*``-
    Feldern, z. B. ein Report ohne Holdout-Trades) — dieselbe konfigurationsbasierte Heuristik wie
    vor diesem Fix, da keine gemessenen Daten vorliegen, die die Konfiguration widerlegen koennten;
    niemals ``mixed`` ohne mindestens zwei klassifizierbare Studies mit unterschiedlichem Befund."""
    classified: list[tuple[str, bool]] = []
    for r in studies:
        slippage = r.get("applied_slippage_bps")
        financing = r.get("applied_financing_bps_per_day")
        if slippage is None or financing is None:
            continue
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        classified.append((key, float(slippage) == 0.0 and float(financing) == 0.0))
    if not classified:
        legacy = _cost_model_has_zero_realism(base_cfg)
        return legacy, ("config_zero" if legacy else "calibrated_cache"), []
    zero_keys = sorted(k for k, is_zero in classified if is_zero)
    nonzero_keys = [k for k, is_zero in classified if not is_zero]
    if not nonzero_keys:
        return True, "config_zero", []
    if not zero_keys:
        return False, "calibrated_cache", []
    return False, "mixed", zero_keys


def _applied_slippage_bps_median_nonzero(studies: list[dict]) -> float | None:
    """Issue #1267 (GH #1137) — Median der GEMESSENEN ``applied_slippage_bps`` ueber alle Studies
    mit einem von Null verschiedenen effektiven Kostenmodell (dieselbe Klassifikation wie
    ``_cost_model_realism_from_applied``, hier auf den reinen Zahlenwert statt der drei
    Zustandskategorien reduziert) — der repraesentative Wert fuer das ``COST_MODEL_REALISM_FROM_
    CALIBRATION``-Event (Fix Punkt 2)."""
    values = [
        float(r["applied_slippage_bps"]) for r in studies
        if r.get("applied_slippage_bps") is not None and r.get("applied_financing_bps_per_day") is not None
        and not (float(r["applied_slippage_bps"]) == 0.0 and float(r["applied_financing_bps_per_day"]) == 0.0)
    ]
    return statistics.median(values) if values else None


def _emit_cost_model_realism_event(cost_model_realism_source: str, studies: list[dict]) -> None:
    """Issue #1267 (GH #1137) — Root-Cause: ``sweep.warn_if_cost_model_zero_realism()`` feuert am
    SWEEP-START rein aus der statischen ``backtest.json``-Config (vor jeder Kalibrierung, kann die
    spaetere Kalibrierungs-Realitaet strukturell nicht kennen) — ``COST_MODEL_ZERO_REALISM`` blieb
    damit auch dann im Log stehen, wenn jede Study spaeter reale ``applied_slippage_bps`` aus dem
    Kalibrierungs-Cache trug (Symptom: 151,5869 bps auf jeder Study, obwohl das Startup-Event "alle
    Saetze 0.0" meldete — zwei Quellen fuer denselben Begriff).

    Diese Funktion emittiert das NACHTRAEGLICHE, aus den tatsaechlich gemessenen ``applied_*``-
    Feldern abgeleitete Gegenstueck (dieselbe ``_cost_model_realism_from_applied``-Klassifikation,
    die auch ``cross_study.cost_model_realism_source`` speist — EINE Quelle fuer beide):
    ``COST_MODEL_ZERO_REALISM`` feuert NUR NOCH, wenn die EFFEKTIVE Groesse (nicht die Config)
    tatsaechlich null ist (``cost_model_realism_source == 'config_zero'``, Fix Punkt 1 — deckt
    sowohl "alle Studies 0.0" als auch den Legacy-Fallback ohne EINE klassifizierbare Study ab,
    Akzeptanzkriterium 2: "bei tatsaechlich nullen Saetzen UND leerem Cache feuert weiterhin das
    Original-Event"); andernfalls ``COST_MODEL_REALISM_FROM_CALIBRATION`` mit Quelle und dem
    gemessenen Median (Fix Punkt 2)."""
    if cost_model_realism_source == "config_zero":
        emit_execution_event(_log, "COST_MODEL_ZERO_REALISM", {
            "cost_model_realism_source": cost_model_realism_source,
            "detail": "Alle Studies mit aufgeloesten applied_*-Feldern tragen 0.0 fuer Slippage UND "
                     "Finanzierung -- die 'full_realism'-Kostenstress-Stufe ist ein echtes No-Op "
                     "(#1010/#1162, seit #1077/#1225 aus den GEMESSENEN Feldern bestaetigt, nicht "
                     "nur der Config, #1267).",
        }, level=logging.WARNING)
    elif cost_model_realism_source in ("calibrated_cache", "mixed"):
        emit_execution_event(_log, "COST_MODEL_REALISM_FROM_CALIBRATION", {
            "cost_model_realism_source": cost_model_realism_source,
            "applied_slippage_bps_median": _applied_slippage_bps_median_nonzero(studies),
            "detail": "Die 'full_realism'-Kostenstress-Stufe ist KEIN No-Op -- mindestens eine "
                     "Study traegt eine von Null verschiedene, aus dem Kalibrierungs-Cache "
                     "aufgeloeste applied_slippage_bps/applied_financing_bps_per_day (#1055/#1204), "
                     "unabhaengig davon, dass backtest.json selbst nur unkalibrierte 0.0-Platzhalter "
                     "traegt (#1267 — ersetzt ein zuvor am Sweep-Start faelschlich gefeuertes "
                     "COST_MODEL_ZERO_REALISM, siehe cost_model_realism_source).",
        }, level=logging.INFO)


def _resolve_slippage_p50_calibrated(
    asset_class_entry: dict | None, symbol: str | None, strategy: str | None, *,
    min_observations: int = 30,
) -> tuple[float | None, str]:
    """Issue #1276 (GH #1149, Katalog #1272-1297, P0) — dieselbe Fallback-Kette wie
    ``backtest_runner.resolve_slippage_bps``/``resolve_slippage_calibration_scope``
    (``by_strategy_symbol`` → ``by_symbol`` → asset-class-weit), hier gegen die Struktur von
    ``calibrated_slippage.json`` ausgewertet (``'p50'``/``'n_observations'`` je Ebene, siehe
    ``sweep.write_calibrated_slippage_cache``/``calibrate_and_write_slippage_cache``) statt gegen
    ``backtest.json``s ``slippage_bps_by_asset_class`` (die ``'value'``-Struktur, die
    ``resolve_slippage_bps`` konsumiert — eine ANDERE, wenn auch aus derselben Kalibrierung
    gespeiste Repraesentation, siehe dortiger Docstring).

    Root-Cause #1276: ``report.py`` stempelte bisher AUSSCHLIESSLICH die asset-class-weite Wurzel
    (``(entry or {}).get('p50')``) — identisch in allen 14 Studies eines Laufs, waehrend
    ``applied_slippage_bps`` (die tatsaechlich angewandte Groesse, ueber ``resolve_slippage_bps``
    aufgeloest) je Study um den Faktor 11,5 streute UND ``slippage_calibration_scope`` (aus
    ``holdout_metrics``, dieselbe Fallback-Semantik) je Study ``'strategy_symbol'`` meldete — zwei
    Felder mit fast identischem Namen beschrieben zwei verschiedene Aufloesungsebenen.

    Rueckgabe ``(p50, scope)`` mit ``scope ∈ {'strategy_symbol', 'symbol', 'asset_class'}`` — DIE
    Ebene, die tatsaechlich getroffen hat (``min_observations``, Default 30, dieselbe Schwelle wie
    ``resolve_slippage_bps``). ``asset_class_entry is None`` ⇒ ``(None, 'asset_class')``."""
    if not asset_class_entry:
        return None, "asset_class"
    if strategy:
        rec = (asset_class_entry.get("by_strategy_symbol") or {}).get(f"{strategy}|{symbol}")
        if (rec and rec.get("p50") is not None
                and int(rec.get("n_observations") or 0) >= min_observations):
            return float(rec["p50"]), "strategy_symbol"
    rec = (asset_class_entry.get("by_symbol") or {}).get(symbol)
    if (rec and rec.get("p50") is not None
            and int(rec.get("n_observations") or 0) >= min_observations):
        return float(rec["p50"]), "symbol"
    p50 = asset_class_entry.get("p50")
    return (float(p50) if p50 is not None else None), "asset_class"


def compute_run_fingerprint(*, git_commit_simulation, tournament_config_sha256,
                            optimizer_config_sha256, catalog_fingerprint_value, seed,
                            symbols, strategies, reward_semantics_version,
                            simulation_semantics_version, seed_salt=None) -> str:
    """Issue #1252 (GH #1122) — sha256-Fingerabdruck der EINGANGSMENGE eines Sweep-Laufs: zwei
    Läufe mit identischer Eingangsmenge (derselbe simulierte Commit, dieselben Config-Dateien,
    derselbe Katalog-Stand, derselbe Seed, dasselbe Symbol-/Strategie-Universum, dieselbe Reward-/
    Simulations-Semantik) tragen DENSELBEN Fingerabdruck — unabhängig davon, ob ihre ``run_id``s
    verschieden sind.

    Root-Cause (#1252-Symptom): drei aufeinanderfolgende Sweeps lieferten 208 von 218
    Study-Feldern bit-identisch, kein Artefakt wies das aus — drei Reports lasen sich wie drei
    unabhängige Belege. Ein Wiederholungslauf ohne Seed-/Config-/Commit-Änderung traegt keine neue
    Information; ohne einen Fingerabdruck ist das aus keinem einzelnen Report ablesbar
    (``invariants.check_run_is_not_duplicate`` braucht diesen Wert, um über Läufe hinweg zu
    vergleichen).

    Issue #1253 (GH #1123) — ``seed_salt`` (Default ``None``, additive ZEHNTE Komponente) macht
    einen bewusst gesalzenen Wiederholungslauf (``seed_effective(seed, study_name, run_salt)``,
    siehe dortiger Docstring) vom urspruenglichen Lauf UNTERSCHEIDBAR — ohne diese Komponente
    wuerde ``check_run_is_not_duplicate`` einen ECHTEN, unabhaengigen Sampler-Ziehung faelschlich
    als Duplikat des Vorlaufs melden, obwohl er per Definition eine NEUE TPE-Stichprobe ist. Fuer
    die ``search_variance``-Gruppierung (Läufe derselben "Familie", die sich NUR im Salt
    unterscheiden) ruft der Aufrufer diese Funktion ein ZWEITES Mal mit ``seed_salt=None``
    (unabhängig vom tatsächlichen Salt-Wert des Laufs) — der resultierende "Basis-Fingerabdruck"
    ist damit für alle Läufe derselben Familie identisch, während der volle ``run_fingerprint``
    (inkl. echtem Salt) sie weiterhin einzeln unterscheidet.

    Reine Funktion (kein Datei-I/O — die Aufrufer laden/berechnen jede Komponente bereits für den
    Report selbst, siehe ``_build_report``). ``symbols``/``strategies`` werden VOR dem Hashen
    sortiert (deterministisch unabhängig von der Iterationsreihenfolge der Aufrufer-Menge) und mit
    Komma verkettet (Kommas sind in Symbol-/Strategie-Namen verboten). Die ZEHN Komponenten selbst
    werden durch das ASCII-Steuerzeichen ``\\x1e`` (Record Separator — in keinem der Eingabefelder
    gültig) getrennt, damit z. B. eine Verkettung ``('a', 'bc')``/``('ab', 'c')`` nicht denselben
    Payload-String ergibt (Hash-Kollisions-Klasse einer naiven Konkatenation ohne Trennzeichen)."""
    payload = "\x1e".join([
        str(git_commit_simulation), str(tournament_config_sha256), str(optimizer_config_sha256),
        str(catalog_fingerprint_value), str(seed),
        ",".join(sorted(s for s in (symbols or []) if s)),
        ",".join(sorted(s for s in (strategies or []) if s)),
        str(reward_semantics_version), str(simulation_semantics_version), str(seed_salt),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_search_variance(fingerprint_base: str, entries: list[dict]) -> dict | None:
    """Issue #1253 (GH #1123) Fix Punkt 3 — Streuung des TPE-Suchergebnisses ueber unabhaengige
    Ziehungen: liegen >= 3 Eintraege in ``entries`` (der Run-Fingerabdruck-Index PLUS der aktuelle
    Lauf, siehe Aufrufer) mit demselben ``fingerprint_base`` (dieselbe Eingangsmenge, siehe
    ``compute_run_fingerprint``-Docstring — ``fingerprint_base`` ignoriert bewusst ``seed_salt``,
    das ist der ganze Sinn dieser Gruppierung), wird je (Strategie, Symbol) Median/IQR/Spannweite
    von ``best_reward``/``best_eligible_reward``/``n_eligible`` ausgewiesen — Rohmaterial fuer die
    Frage, ob ``best_eligible_reward`` (Symptom-Beispiel: 1,7561 bei ComboTrendVwap) ein STABILER
    Wert oder eine einzelne TPE-Ziehung ist.

    ``None`` bei < 3 Läufen derselben Familie (nicht auswertbar — KEIN Fehler, die Streuung
    existiert schlicht noch nicht als Kennzahl). Jeder ``entries``-Eintrag ohne ``study_summaries``
    (Legacy-Index-Zeile vor #1253) traegt keine (Strategie, Symbol)-Daten bei, zaehlt aber weiterhin
    zur Lauf-ANZAHL der Familie (die Familien-Zugehoerigkeit ist unabhaengig davon, ob die Zeile
    bereits die #1253-Erweiterung trug)."""
    family = [e for e in entries if e.get("fingerprint_base") == fingerprint_base]
    if len(family) < 3:
        return None
    by_pair: dict[tuple[str, str], dict[str, list[float]]] = {}
    for entry in family:
        for s in entry.get("study_summaries") or []:
            key = (s.get("strategy"), s.get("symbol"))
            if not all(key):
                continue
            bucket = by_pair.setdefault(key, {"best_reward": [], "best_eligible_reward": [], "n_eligible": []})
            for field in ("best_reward", "best_eligible_reward", "n_eligible"):
                v = s.get(field)
                if v is not None:
                    bucket[field].append(float(v))
    if not by_pair:
        return None

    def _stats(values: list[float]) -> dict | None:
        if not values:
            return None
        sorted_v = sorted(values)
        return {
            "median": statistics.median(sorted_v),
            "iqr": (
                statistics.quantiles(sorted_v, n=4)[2] - statistics.quantiles(sorted_v, n=4)[0]
                if len(sorted_v) >= 2 else 0.0
            ),
            "range": sorted_v[-1] - sorted_v[0],
            "n": len(sorted_v),
        }

    per_pair = {}
    for (strategy, symbol), bucket in sorted(by_pair.items()):
        per_pair[f"{strategy}/{symbol}"] = {
            field: _stats(values) for field, values in bucket.items()
        }
    return {"n_runs_in_family": len(family), "per_study": per_pair}


def _allow_short_by_strategy(base_cfg: Path | None = None) -> dict[str, bool]:
    """Issue #1256 (GH #1126) — der EFFEKTIVE ``allow_short``-Flag je Strategie (``strategies.json``
    ``[params][allow_short]``, dieselbe Ueberschreibungs-Quelle wie ``_trade_amount_pct_by_strategy``
    direkt unten — ``strategy_defaults.json`` traegt (Stand dieses Fixes) kein ``allow_short``-Feld,
    daher keine zweite Quelle noetig). Fehlt der Key ⇒ ``False`` (long-only, der Default jeder
    Strategie ohne expliziten Short-Support). Rohmaterial für ``invariants.check_beta_exposure_
    plausibility``: nur long-only-Strategien haben ein VORHERSAGBARES Vorzeichen fuer β (positiv,
    proportional zur Exposure) — eine Strategie mit ``allow_short=True`` kann strukturell negatives β
    tragen, ohne dass das ein Fehler waere."""
    cfg_dir = base_cfg or config_dir()
    out: dict[str, bool] = {}
    strategies_cfg = _load_json(cfg_dir / "strategies.json") or {}
    for entry in strategies_cfg.get("strategies") or []:
        if not isinstance(entry, dict):
            continue
        strat = entry.get("strategy_class")
        if not strat:
            continue
        allow_short = (entry.get("params") or {}).get("allow_short")
        out[strat] = bool(allow_short) if allow_short is not None else False
    return out


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
        # Issue #1083/#1231 (P1, Katalog #1247+) Fix Punkt 1 — Anteil der TRIALS mit
        # atr_raw_i < floor_i, wobei floor_i der PER-TRIAL kostengekoppelte Floor ist (jeder Trial
        # samplet sein eigenes k, siehe cost_coupled_atr_floor_bps-Docstring: "kein studienweiter
        # Median — der ist zum Zeitpunkt eines einzelnen Backtests nicht bekannt"). Root-Cause:
        # max(median(raw), median(floor)) ist NICHT median(max(raw_i, floor_i)) — Beispiel Sma/
        # GOOGL: roh 9,246 median, Floor 8,823 median (bindet auf Study-Ebene nicht), aber
        # effektiv 12,037 (30% ueber dem rohen Median) — der Floor bindet dort fuer einen
        # erheblichen Trial-Anteil, ohne dass die Study-Ebene das zeigt.
        _trial_pairs = r.pop("_atr_floor_binding_trial_pairs", None) or []
        if base_floor is None or c_rt is None or not _trial_pairs:
            r["atr_floor_binding_trial_fraction"] = None
        else:
            _n_binding = sum(
                1 for k_i, raw_i in _trial_pairs
                if raw_i < cost_coupled_atr_floor_bps(
                    float(base_floor), atr_trailing_multiplier=k_i,
                    round_trip_cost_bps=float(c_rt), min_stop_to_cost_ratio=min_stop_to_cost_ratio))
            r["atr_floor_binding_trial_fraction"] = round(_n_binding / len(_trial_pairs), 4)


def _stamp_cost_drag_decomposition(studies_out: list[dict]) -> None:
    """Issue #1279 (GH #1152, Katalog #1272-1297, P1) — die oekonomisch entscheidende Zahl des
    Katalogs (der Kostendrag zwischen ``holdout_total_return_gross`` und ``_net``, 0,68 bis 23,88
    pp) existierte bisher in KEINEM Feld und keiner Report-Sektion — nur als Differenz zweier
    Felder rekonstruierbar, ihre Aufteilung auf ``c_rt``, Slippage und Financing gar nicht.

    Stempelt je Study:
      * ``holdout_cost_drag_pct`` — ``holdout_total_return_gross - holdout_total_return_net``
        (Prozentpunkte; ``None`` ohne beide Werte).
      * ``holdout_cost_drag_bps_per_round_trip`` — derselbe Drag, auf EINEN Round-Trip
        umgelegt (``holdout_cost_drag_pct * 100 / holdout_total_trades``, ``None`` ohne
        ``holdout_total_trades > 0``).
      * ``holdout_cost_drag_component_round_trip_bps`` — ``round_trip_cost_bps`` (c_rt), bereits
        eine Pro-Round-Trip-Groesse.
      * ``holdout_cost_drag_component_slippage_bps`` — ``applied_slippage_bps``, ebenfalls bereits
        pro Round-Trip.
      * ``holdout_cost_drag_component_financing_bps`` — ``applied_financing_bps_per_day`` auf die
        GESCHAETZTE Haltedauer EINES Round-Trips umgelegt (``median_bars_held · bar_seconds /
        86400``, aus ``symbol_bar_quality``/``_contracts.BAR_SECONDS_DEFAULT`` aufgeloest — dieselbe
        Kompoundierungs-NAEHERUNG wie die uebrigen Komponenten: additiv statt geometrisch verkettet,
        siehe ``invariants.check_cost_drag_decomposition``s 5-%-Toleranz-Dokumentation).

    Reine additive Telemetrie — keine bestehende Aggregation/Selektion liest diese Felder,
    Zero-Regression fuer jeden bestehenden Konsumenten."""
    for r in studies_out:
        gross = r.get("holdout_total_return_gross")
        net = r.get("holdout_total_return_net")
        if gross is None or net is None:
            r["holdout_cost_drag_pct"] = None
        else:
            r["holdout_cost_drag_pct"] = round(float(gross) - float(net), 4)
        n_trades = r.get("holdout_total_trades")
        if r["holdout_cost_drag_pct"] is not None and n_trades:
            r["holdout_cost_drag_bps_per_round_trip"] = round(
                r["holdout_cost_drag_pct"] * 100.0 / float(n_trades), 4)
        else:
            r["holdout_cost_drag_bps_per_round_trip"] = None
        c_rt = r.get("round_trip_cost_bps")
        r["holdout_cost_drag_component_round_trip_bps"] = (
            round(float(c_rt), 4) if c_rt is not None else None)
        slippage = r.get("applied_slippage_bps")
        r["holdout_cost_drag_component_slippage_bps"] = (
            round(float(slippage), 4) if slippage is not None else None)
        financing_per_day = r.get("applied_financing_bps_per_day")
        _symbol_bar_quality = r.get("symbol_bar_quality")
        bar_seconds = (
            _symbol_bar_quality.get("median_delta_t_s")
            if isinstance(_symbol_bar_quality, dict) and _symbol_bar_quality.get("median_delta_t_s")
            else _contracts.BAR_SECONDS_DEFAULT
        )
        median_bars_held = r.get("median_bars_held")
        if financing_per_day is not None and median_bars_held is not None:
            holding_days = float(median_bars_held) * float(bar_seconds) / 86400.0
            r["holdout_cost_drag_component_financing_bps"] = round(
                float(financing_per_day) * holding_days, 4)
        else:
            r["holdout_cost_drag_component_financing_bps"] = None


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
    # Issue #1066/#1216 (Katalog #1196-1221, P2) — SELBSTREFERENZ: dieser Check prueft, ob DIESES
    # Report-Artefakt geschrieben wurde (``sweep.py`` ruft ihn NACH dem Schreibversuch auf, siehe
    # dortige Aufrufstelle) — er kann strukturell NIE im eigenen ``invariant_checks``-Strom stehen,
    # weil die Frage "wurde DIESE Datei geschrieben" erst beantwortbar ist, nachdem der Report
    # (inklusive seines eigenen Invarianten-Stroms) bereits serialisiert wurde. Sein Ergebnis steht
    # stattdessen in ``run.json['report_artifact']`` (written/path/bytes/sha256, siehe
    # sweep.py-Stempelstelle) UND im ``INVARIANT_STREAM_RESULT``-Log-Event.
    "check_report_artifact_written",
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


def _within_study_stop_calibration_pairs(trial_attrs: list[dict]) -> tuple[list[float], list[float]]:
    """Issue #1056/#1205 — je-Trial-Paare (gesampelter ``atr_trailing_multiplier``, gemessene
    ``oos_stop_distance_bps_median`` aus #1054/#1203) EINER Study, für die WITHIN-STUDY-
    Spearman-Korrelation in ``invariants.check_trailing_stop_risk_calibration_acceptance``.

    Root-Cause #1056: die vormalige Korrelation rechnete über 13-14 STUDY-MEDIANE (ein Punkt je
    Study) — über Studies hinweg ist diese Grösse durch die Strategie-Komposition konfundiert
    (Haltedauer, Session-Verankerung, ATR-Floor-Bindung unterscheiden sich je Strategie/Symbol).
    Innerhalb EINER Study (fixe Strategie, fixes Symbol) variiert nur der gesampelte Multiplikator
    selbst — die hier gelieferten Paare sind die datenseitige Grundlage für eine unkonfundierte
    Korrelation."""
    k_values: list[float] = []
    d_values: list[float] = []
    for a in trial_attrs or []:
        k = (a.get("sampled_params") or {}).get("atr_trailing_multiplier")
        d = a.get("oos_stop_distance_bps_median")
        if k is None or d is None or k <= 0:
            continue
        k_values.append(float(k))
        d_values.append(float(d))
    return k_values, d_values


def _within_study_stop_calibration_spearman(trial_attrs: list[dict]) -> tuple[float | None, int]:
    """Issue #1056/#1205 Fix Punkt 1 — Spearman(atr_trailing_multiplier, gemessene stop_distance_bps)
    INNERHALB einer Study (siehe ``_within_study_stop_calibration_pairs``-Docstring). Rueckgabe
    ``(rho, n_pairs)``; ``rho=None`` bei ``n_pairs < 3`` (Rangkorrelation unter 3 Punkten nicht
    definierbar, dieselbe Untergrenze wie ``reward._spearman_rank_correlation``) — der Aufrufer
    (``invariants.check_trailing_stop_risk_calibration_acceptance``) aggregiert die je-Study-Werte
    n-gewichtet über alle Studies, statt hier bereits ein Study-lokales Urteil zu fällen."""
    k_values, d_values = _within_study_stop_calibration_pairs(trial_attrs)
    if len(k_values) < 3:
        return None, len(k_values)
    return _reward._spearman_rank_correlation(k_values, d_values), len(k_values)


def _effective_stop_ratio_for_trial(trial_attrs_entry: dict) -> float | None:
    """Issue #1053/#1202 — ``realized_stop_loss_ratio`` aus DEMSELBEN Trial (kein Quotient aus zwei
    UNABHAENGIG über verschiedene Trial-Teilmengen gebildeten Aggregaten): (dieses Trials gemessener
    mittlerer Stop-Verlust) / (dieses Trials Stopdistanz).

    Issue #1081/#1229 (P0, Katalog #1247+) — der Nenner ist die GEMESSENE, getaggte Stopdistanz
    DIESES Trials (``oos_stop_distance_bps_median``, #1054/#1203), NICHT mehr das MODELLIERTE
    ``k · ATR``: Median eines Produkts ≠ Produkt der Mediane, UND ``k``/``ATR_eff`` sind über den
    kostengekoppelten Floor korreliert (B-5: die modellierte Distanz weicht Faktor 0,525-3,543 von
    der tatsächlich getaggten ab). ``None`` ohne gemessene Distanz DIESES Trials — kein stiller
    Rückfall auf die modellierte Größe (siehe ``invariants.check_effective_stop_distance``, dessen
    Legacy-Zweig aus demselben Grund seither ebenfalls INCONCLUSIVE statt eines Rückfalls liefert)."""
    distance = trial_attrs_entry.get("oos_stop_distance_bps_median")
    loss = trial_attrs_entry.get("oos_gross_loss_mean_bps_trailing_stop")
    if distance is None or loss is None:
        return None
    denom = float(distance)
    if denom <= 0:
        return None
    return float(loss) / denom


def _effective_stop_ratio_cohort(
    trial_attrs: list[dict], *, min_trailing_stop_exits_per_trial: int = 3,
) -> tuple[float | None, int]:
    """Issue #1053/#1202 (P1) — Median des PER-TRIAL ``_effective_stop_ratio_for_trial`` über die
    "eligible Kohorte" (Trials mit >= ``min_trailing_stop_exits_per_trial`` nachweislichen
    TRAILING_STOP-Exits IN DIESEM EINEN TRIAL), konsumiert von
    ``invariants.check_effective_stop_distance``.

    Symptom (B-7): über vier TSLA-Läufe auf identischer Datenlage FAILte
    ``check_effective_stop_distance`` mal, mal nicht, mit Werten 10,2049-13,3619.

    Root-Cause: die vormalige Berechnung bildete den Quotienten aus ZWEI UNABHÄNGIG über die
    GESAMTE Study gemittelten Grössen (Nenner: ``atr_trailing_multiplier_median · atr_median_bps``
    über ALLE Trials unbedingt; Zähler: ``gross_loss_median_bps_trailing_stop`` NUR über Trials MIT
    mindestens einem Stop-Exit) — Zähler- und Nenner-Median liefen über INKONSISTENTE
    Trial-Teilmengen. Zusätzlich exploriert Optunas TPE-Sampler in jedem Re-Run unterschiedliche
    Punkte im Multiplikator-Raum (empirisch bestätigt: ``atr_trailing_multiplier_median`` driftete
    zwischen vier identisch konfigurierten TSLA-Läufen von 0,84 bis 1,42 bei GLEICHER Trial-Zahl,
    120/100) — der resultierende Quotient driftete dadurch RUN-ZU-RUN, ohne dass sich die
    zugrundeliegende Datenlage änderte (die konkreten Report-Werte 10,2049/13,3619 aus dem
    #1202-Symptom sind in dieser Sandbox über echte Lauf-Artefakte reproduzierbar).

    Fix: Zähler UND Nenner werden IMMER auf demselben Trial gebildet (``_effective_stop_ratio_for_
    trial``); der Median über diese Kohorte ist eine STUDY-EIGENSCHAFT (nicht die Eigenschaft des
    jeweils gewählten Gewinner-Trials, siehe ``winner_effective_stop_ratio`` für dessen separate
    Telemetrie) und robuster gegen die Sampler-Explorationsvarianz als der Quotient zweier getrennt
    gebildeter Aggregate."""
    ratios = [
        ratio for a in (trial_attrs or [])
        if int(a.get("oos_n_trailing_stop_losses") or 0) >= min_trailing_stop_exits_per_trial
        and (ratio := _effective_stop_ratio_for_trial(a)) is not None
    ]
    if not ratios:
        return None, 0
    return statistics.median(ratios), len(ratios)


def _study_record(proposal: dict, study,
                  tournament_cfg: dict | None = None, *,
                  guard_dominance_threshold: float | None = None,
                  symbol_bar_quality_cache: dict | None = None,
                  run_id: str | None = None,
                  trials_override: list | None = None,
                  ) -> tuple[dict[str, Any], list[_inv.InvariantResult]]:
    """Ein ``studies[]``-Eintrag + die für DIESE Study anwendbaren Invarianz-Ergebnisse (#743).

    ``symbol_bar_quality_cache`` (Issue #923) — vom Aufrufer EINMAL gelesenes
    ``sweep.read_symbol_bar_quality_cache(PERSISTENT_CACHE_ROOT)``-Ergebnis (WORK-Pfad seit
    #1270/GH #1140), hier nur je Symbol nachgeschlagen
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
    # Issue #1046/#1195 (Katalog #1195) Fix Punkt 2 — Fallback, wenn der Gate-1-Preflight-Cache
    # KEINEN Eintrag für dieses Symbol trägt (z. B. ``using_real_optimize=False`` im Sweep-Lauf,
    # der diesen Report erzeugte, siehe ``sweep.py``s #1046-Kommentar): ``session_coverage_
    # fraction``/``bars_per_calendar_day`` liegen je Study bereits vor (#1011/#1163, oben in dieser
    # Funktion berechnet) — die "billigste Quelle" laut Fix-Vorgabe, weil sie aus bereits
    # gelaufenen Trials abgeleitet ist, ohne einen zusätzlichen Katalog-Zugriff zu benötigen.
    # Liefert eine reduzierte, aber NICHT-None-Teilmenge des vollen Preflight-Schemas — ``source``
    # macht die Herkunft (Live-Preflight vs. Study-Fallback) im Artefakt selbst unterscheidbar.
    if _symbol_bar_quality is None and session_coverage_fraction_median is not None:
        _symbol_bar_quality = {
            "session_coverage_fraction": session_coverage_fraction_median,
            "bars_per_calendar_day": bars_per_calendar_day_median,
            "source": "study_fallback",
        }
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
    # Issue #1056/#1205 — WITHIN-STUDY-Kalibrierungs-Rohmaterial (siehe
    # _within_study_stop_calibration_spearman-Docstring); konsumiert von
    # invariants.check_trailing_stop_risk_calibration_acceptance Kriterium 1.
    _stop_calibration_spearman, _stop_calibration_n_pairs = _within_study_stop_calibration_spearman(
        trial_attrs)
    # Issue #1053/#1202 — Kohorten-Median (siehe _effective_stop_ratio_cohort-Docstring), konsumiert
    # von invariants.check_effective_stop_distance als PRIMAERE Grundlage (ersetzt den vormaligen
    # Quotienten aus zwei getrennt gebildeten Aggregaten).
    _effective_stop_ratio_cohort_median, _effective_stop_ratio_cohort_n = _effective_stop_ratio_cohort(
        trial_attrs)
    # Issue #1053/#1202 Fix — der GEWINNER-Wert (bestbewerteter Trial) bleibt SEPARAT telemetriert,
    # fliesst aber NICHT mehr in das blockierende Verdikt ein (das ist jetzt eine Study-, keine
    # Trial-Eigenschaft). ``scored``/``best_trial`` wurden weiter oben bereits fuer #1067 berechnet.
    _winner_effective_stop_ratio = (
        _effective_stop_ratio_for_trial(dict(getattr(best_trial, "user_attrs", {}) or {}))
        if scored else None)

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
        # Issue #1086/#1234 (Katalog #1247+, P1) — die STORE-weiten (ueber alle Laeufe auf demselben
        # Optuna-Store akkumulierten) Gegenstuecke zu den vier run-scopeden Zaehlern oben, aus
        # run_optimization._emit_study_summary. Jeder Feldname traegt das ``_store``-Suffix, damit
        # kein Konsument ihn versehentlich mit dem run-scopeden Zaehler verwechselt (#1235-Folgefix
        # bindet die Invarianten-Konsumenten an diesen Vertrag).
        "n_trials_total_store": study_user_attrs.get("n_trials_total_store"),
        "n_trials_informative_store": study_user_attrs.get("n_trials_informative_store"),
        "n_trials_pruned_store": study_user_attrs.get("n_trials_pruned_store"),
        "n_trials_unevaluable_store": study_user_attrs.get("n_trials_unevaluable_store"),
        "n_trials_failed_store": study_user_attrs.get("n_trials_failed_store"),
        # Issue #1063/#1213 (Katalog #1196-1221) — Root-Cause des B-9-"0"-Symptoms (§5.3 zeigte
        # "Guard-dominierte Studies: 0" bei 84-85% Zensur): dieses von run_optimization._emit_
        # study_summary gestempelte User-Attr (Issue #823 Fix Punkt 4) erreichte den Study-Record
        # NIE — die Bruecke fehlte komplett (dasselbe Fehlermuster wie #1022/#1171, Pitfall #421).
        # ``bool(...)`` statt eines rohen ``.get()``, weil das Attr nur bei ``True`` ueberhaupt
        # gestempelt wird (fehlt sonst) — ``False`` ist hier die korrekte, explizite Norm.
        "study_guard_dominated": bool(study_user_attrs.get("study_guard_dominated")),
        # Issue #812 — SHA-256 ueber die effektiv wirksame Gate-Konfiguration dieser Study
        # (reward.selection_rule_fingerprint, gestempelt in run_optimization._emit_study_summary).
        # ``None`` fuer Studies aus einem Lauf vor #812 (rueckwaertskompatibel, analog seed_effective).
        "selection_rule_fingerprint": study_user_attrs.get("selection_rule_fingerprint"),
        # Issue #1250 (GH #1120), Pitfall #451 in AGENTS.md — die effektiv wirksame
        # oos_min_alpha_tstat-Schwelle DIESER Study (reward.resolve_alpha_tstat_gate_threshold,
        # gestempelt in run_optimization.py neben selection_rule_fingerprint) plus ihre Quelle
        # ('static'/'calibrated'). ``None`` fuer Studies aus einem Lauf vor #1250.
        "alpha_tstat_gate_threshold_effective": study_user_attrs.get("alpha_tstat_gate_threshold_effective"),
        "alpha_tstat_gate_threshold_source": study_user_attrs.get("alpha_tstat_gate_threshold_source"),
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
        # Issue #1253 (GH #1123) Fix Punkt 2 — der wirksame Salt-Wert dieses Laufs (None ⇒
        # ungesalzen, bit-identisch zum Pre-#1253-Verhalten), Eingang von report.compute_run_
        # fingerprint (siehe _build_report).
        "seed_salt": study_user_attrs.get("seed_salt"),
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
        # Issue #1067/#1217 — TPE-Surrogat-Fit-Zeit dieser Study (siehe run_optimization.
        # _WindowedTPESampler-Docstring), Rohmaterial fuer invariants.check_search_overhead_share.
        "tpe_fit_seconds": study_user_attrs.get("tpe_fit_seconds"),
        # Issue #1089/#1237 (P1, Katalog #1247+) — die tatsaechlich in den letzten Surrogat-Fit
        # eingegangene (bzw. VOR dem Fenster verfuegbare) Trial-Zahl, Rohmaterial fuer
        # invariants.check_tpe_fit_cost_share und das Akzeptanzkriterium "tpe_fit_trials_used <=
        # tpe_fit_max_trials".
        "tpe_fit_trials_used": study_user_attrs.get("tpe_fit_trials_used"),
        "tpe_fit_trials_available": study_user_attrs.get("tpe_fit_trials_available"),
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
        # Issue #1050/#1199 (Katalog #1196-1221) — RUN-SCOPED Gegenstuecke der beiden Felder oben
        # (run_optimization.floor_plateau_callback, ``completed_run`` statt ``completed``); scope-
        # konsistent mit dem run-scoped ``n_trials`` unten. invariants.check_counter_partition_
        # consistency konsumiert seither AUSSCHLIESSLICH diese Variante.
        "plateau_n_evaluated_run": study_user_attrs.get("plateau_n_evaluated_run"),
        "plateau_counter_breakdown_run": study_user_attrs.get("plateau_counter_breakdown_run"),
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
        # Issue #1059/#1208 — dieselbe Groesse, aber HOLDOUT-skopiert (aus dem promotierten
        # Holdout-Re-Evaluations-Pfad, siehe holdout_total_trades-Feldkommentar unten und
        # _INTENTIONALLY_UNSTAMPED_METRIC_FIELDS["oos_f_turnover_realized_median"] fuer dasselbe
        # Muster).
        # Root-Cause #1208: das obige Feld ist OOS-skopiert (Median ueber ALLE Sweep-Trials dieser
        # Study), stand aber in summary_de.py OHNE Scope-Kennzeichnung direkt unter den
        # Holdout-Ertragszahlen — eine Study mit 0 Holdout-Trades (z. B. verworfen/nicht promotiert)
        # trug dort trotzdem einen (OOS-)Wert. Die Holdout-Spalte in summary_de.py bleibt ``k. A.``,
        # solange ``holdout_total_trades`` 0/None ist.
        "holdout_stop_exit_slippage_bps": holdout_metrics.get("oos_stop_exit_slippage_bps_median"),
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
        # Issue #1259 (GH #1129), Pitfall #442 — bislang gestempelt, aber nie gelesen (analog
        # atr_median_bps, dieselbe Trial-Median-Konvention).
        "atr_min_bps": _median_of_trial_field(trial_attrs, "oos_atr_min_bps"),
        "gross_win_mean_bps": _median_of_trial_field(trial_attrs, "oos_gross_win_mean_bps"),
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
        # Issue #1079/#1227 (Katalog #1247+, P0) — P75 derselben (um Nullspannen-Bars bereinigten)
        # Population wie bar_range_median_bps, und der Anteil der Nullspannen-Bars (``high == low``)
        # an ALLEN Bars, ueber die eine Position lief; Rohmaterial fuer
        # invariants.check_zero_range_bar_share.
        "bar_range_p75_bps": _median_of_trial_field(
            trial_attrs, "oos_bar_range_p75_bps"),
        # Issue #1259 (GH #1129), Pitfall #452 — Populationszaehler derselben bereinigten Serie wie
        # bar_range_median_bps; 0 (nicht None) unterscheidet "Median 0 ueber 0 Bars"
        # (DEGENERATE_ZERO_RANGE, ein eigenstaendiges FAIL fuer check_stop_loss_vs_bar_range) von
        # "nie gemessen" (POPULATION_UNAVAILABLE, None bleibt None ueber _median_of_trial_field).
        "bar_range_population_n": _median_of_trial_field(
            trial_attrs, "oos_bar_range_population_n"),
        "zero_range_bar_fraction": _median_of_trial_field(
            trial_attrs, "oos_zero_range_bar_fraction"),
        # Issue #1054/#1203 (Katalog #1196-1221) — Verlust-Zerlegung "realized_loss_bps =
        # stop_distance_bps_measured + trigger_to_fill_gap_bps" auf Study-Ebene (Median ueber die
        # Trial-Mediane, analog bar_range_median_bps); Rohmaterial fuer Report §2.4 und
        # invariants.check_stop_loss_decomposition_identity. Suffix ``_measured`` bewusst UNGLEICH
        # dem bestehenden ``stop_distance_bps`` (weiter unten in dieser Funktion, algebraisch aus
        # atr_trailing_multiplier_median · atr_median_bps IMPLIZIERT) — dieselbe #989/#1143-
        # Unterscheidung (DIREKT gemessen vs. algebraisch impliziert, Pitfall #412 in AGENTS.md);
        # eine Namenskollision haette hier STILLSCHWEIGEND das direkt gemessene Feld durch das
        # implizierte ueberschrieben (Reihenfolge-Falle, siehe die spaetere Zuweisung).
        "stop_distance_bps_measured": _median_of_trial_field(
            trial_attrs, "oos_stop_distance_bps_median"),
        "trigger_to_fill_gap_bps": _median_of_trial_field(
            trial_attrs, "oos_trigger_to_fill_gap_bps_median"),
        "realized_loss_bps": _median_of_trial_field(
            trial_attrs, "oos_realized_loss_bps_median"),
        "n_stop_loss_identity_checked": sum(
            int(a.get("oos_n_stop_loss_identity_checked") or 0) for a in trial_attrs),
        "n_stop_loss_identity_violations": sum(
            int(a.get("oos_n_stop_loss_identity_violations") or 0) for a in trial_attrs),
        # Issue #1259 (GH #1129), Pitfall #442 — dieselbe gepoolte Summenkonvention wie
        # n_stop_loss_identity_checked; Stichprobengroesse HINTER stop_exit_lag_bars (oben).
        "n_trailing_stop_exits_with_lag_telemetry": sum(
            int(a.get("oos_n_trailing_stop_exits_with_lag_telemetry") or 0) for a in trial_attrs),
        "stop_ratchet_between_trigger_and_submit_bps": _median_of_trial_field(
            trial_attrs, "oos_stop_ratchet_between_trigger_and_submit_bps_median"),
        "n_trailing_stop_exits_with_ratchet_telemetry": sum(
            int(a.get("oos_n_trailing_stop_exits_with_ratchet_telemetry") or 0)
            for a in trial_attrs),
        # Issue #1082/#1230 (P1, Katalog #1247+) — die Anteile werden PRO ROUND-TRIP gebildet
        # (backtest_runner._aggregate_exit_telemetry), dann je Trial und je Study medianisiert —
        # NICHT aus stop_distance_bps_measured/realized_loss_bps oben ableitbar (Median einer
        # Summe != Summe der Mediane; Symptom: Residuum Median +12,00 bps = 16,17% des Median-
        # Verlusts in 151/154 Studies). Rohmaterial fuer Report §2.4 und
        # invariants.check_stop_loss_share_decomposition; summieren sich per Konstruktion auf 1
        # (bis auf Rundung).
        "stop_distance_share_median": _median_of_trial_field(
            trial_attrs, "oos_stop_distance_share_median"),
        "trigger_to_fill_gap_share_median": _median_of_trial_field(
            trial_attrs, "oos_trigger_to_fill_gap_share_median"),
        # Issue #1083/#1231 (P1, Katalog #1247+) — Rohmaterial fuer den PER-TRIAL ATR-Floor-
        # Bindungsanteil (siehe _stamp_atr_floor_bps_derived-Docstring): jeder Trial hat sein
        # EIGENES gesampeltes k (atr_trailing_multiplier) und damit seinen eigenen effektiven
        # Floor (min_stop_to_cost_ratio · c_rt / k) — max(median(raw), median(floor)) ist NICHT
        # median(max(raw_i, floor_i)). Nur ein Zwischenergebnis: wird von
        # _stamp_atr_floor_bps_derived konsumiert und dort wieder entfernt (erscheint NICHT im
        # finalen Report-JSON), da Basis-Floor/c_rt erst NACH diesem Aufruf aufgeloest werden.
        "_atr_floor_binding_trial_pairs": [
            (float((a.get("sampled_params") or {}).get("atr_trailing_multiplier")),
             float(a["oos_atr_raw_median_bps"]))
            for a in trial_attrs
            if (a.get("sampled_params") or {}).get("atr_trailing_multiplier") is not None
            and a.get("oos_atr_raw_median_bps") is not None
        ],
        # Issue #923 Fix 1 — die #900-Preflight-Kennzahlen (frac_zero_true_range, atr_median_bps,
        # bar_coverage_ratio, median_delta_t_s) des SYMBOLS (nicht dieser Study — identisch für
        # jede Strategie auf demselben Symbol), aus dem Gate-1-Cache. Issue #1046/#1195 — fehlt der
        # Cache-Eintrag, aber diese Study selbst hat session_coverage_fraction gemessen, liefert
        # der obige Fallback eine reduzierte Teilmenge (``source: 'study_fallback'``) statt None.
        # None bleibt nur, wenn WEDER der Cache NOCH diese Study selbst je eine Bar sah.
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
        # Issue #1265 (GH #1135) — der Nenner, gegen den exit_reason_histogram (und jeder daraus
        # abgeleitete Anteil, z. B. time_box_exit_fraction) tatsaechlich normiert: Σ der Histogramm-
        # Werte. Dust-Round-Trips erreichen dieses Histogramm strukturell NIE (an der Quelle in
        # backtest_runner._filter_dust_round_trips verworfen, VOR jeder Exit-Telemetrie-Erfassung,
        # siehe dortiger Docstring) — der Nenner ist deshalb bereits bereinigt; dieses Feld macht ihn
        # nur SICHTBAR (Akzeptanzkriterium #1265: "der Nenner ist im Artefakt ablesbar"), statt ihn
        # nur implizit ueber sum(exit_reason_histogram.values()) rekonstruierbar zu lassen.
        "exit_reason_histogram_denominator_n": sum(_study_exit_reason_histogram.values()) or None,
        # Issue #919 — Anteil der Round-Trips, die über die 24-Bar-Zeitbox statt über den
        # Trailing-Stop/Profit-Target/Signal-Reversal schliessen (Eingangsgrösse für die
        # #925-Budgetdiskussion und GR-01, siehe hourly_strategy_base.ExitReason).
        "time_box_exit_fraction": _time_box_exit_fraction(trial_attrs),
        # Issue #1265 (GH #1135) — derselbe Nenner wie exit_reason_histogram_denominator_n oben
        # (time_box_exit_fraction ist ein Anteil DESSELBEN Histogramms); als eigenes Feld direkt
        # neben time_box_exit_fraction gestempelt, damit ein Leser den Nenner nicht aus einem
        # anderen Abschnitt des Records zusammensuchen muss.
        "time_box_exit_fraction_denominator_n": sum(_study_exit_reason_histogram.values()) or None,
        # Issue #897 Fix 3 — Median des je-Trial GESAMPELTEN atr_trailing_multiplier (das
        # Konfigurations-Gegenstueck zur realisierten ATR-Telemetrie oben).
        # Issue #997/#1149 — faellt auf den strategy_defaults.json-Eintrag zurueck, wenn die
        # Strategie diesen Parameter nicht sampelt (z. B. SmaCrossoverStrategy); die Herkunft
        # (source ∈ {"sampled","strategy_default","unavailable"}) macht das UNTERSCHEIDBAR von
        # einem echten, gesampelten Median, statt beide unter demselben Feld zu verstecken.
        "atr_trailing_multiplier_median": _atr_trailing_multiplier_median,
        "atr_trailing_multiplier_median_source": _atr_trailing_multiplier_median_source,
        # Issue #1056/#1205 — je-Study WITHIN-STUDY-Spearman(atr_trailing_multiplier, gemessene
        # stop_distance_bps) + Paarzahl, Rohmaterial fuer die n-gewichtete Studies-Aggregation in
        # invariants.check_trailing_stop_risk_calibration_acceptance.
        "stop_calibration_spearman_within_study": (
            round(_stop_calibration_spearman, 4) if _stop_calibration_spearman is not None else None),
        "stop_calibration_n_pairs_within_study": _stop_calibration_n_pairs,
        # Issue #1053/#1202 — Kohorten-Median (siehe _effective_stop_ratio_cohort-Docstring), die
        # NEUE Grundlage fuer invariants.check_effective_stop_distance (ersetzt den vormaligen
        # Quotienten aus zwei getrennt gebildeten Aggregaten, der RUN-ZU-RUN driftete, obwohl sich
        # die Datenlage nicht aenderte). ``winner_effective_stop_ratio`` bleibt SEPARAT telemetriert
        # (Akzeptanzkriterium aus #1202), fliesst aber NICHT in das blockierende Verdikt ein.
        "effective_stop_ratio_cohort_median": (
            round(_effective_stop_ratio_cohort_median, 4)
            if _effective_stop_ratio_cohort_median is not None else None),
        "effective_stop_ratio_cohort_n": _effective_stop_ratio_cohort_n,
        "winner_effective_stop_ratio": (
            round(_winner_effective_stop_ratio, 4)
            if _winner_effective_stop_ratio is not None else None),
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
        # Issue #989/#1143 (Katalog #986, Pitfall #412 in AGENTS.md), umbenannt #1085/#1233 —
        # DIREKT gemessener Sizing-UMSCHLAG (Summe rt_notional / equity_at_entry ueber ALLE Legs
        # eines Round-Trips). Reine Umschlagsdiagnose (severity low) — NICHT mehr das primaere
        # Kriterium der Sizing-Checks (siehe holdout_f_realized_peak_median/_max unten): zwei
        # Aufstockungen zu je 15% ergeben hier 30%, obwohl nie mehr als 15% GLEICHZEITIG offen
        # waren (Root-Cause #1233, KRYS-Symptom in check_sizing_identity_coherence).
        "holdout_f_turnover_realized_median": holdout_metrics.get("oos_f_turnover_realized_median"),
        "holdout_f_turnover_realized_max": holdout_metrics.get("oos_f_turnover_realized_max"),
        # Issue #1085/#1233 (Katalog #1247+, P0) Fix Punkt 1 — DIREKT gemessenes GLEICHZEITIGES
        # Netto-Exposure (rt_notional_peak / equity_at_entry je Round-Trip) — die zum #1209-
        # Sizing-Deckel passende Groesse (der Deckel begrenzt Exposure, nicht Umschlag). Median ist
        # das primaere Kriterium fuer invariants.check_sizing_identity_coherence (ersetzt dort den
        # bisher konsumierten Umschlagswert); Maximum (Issue #1060/#1209, Katalog #1196-1221 — ein
        # Sizing-Cap-Verstoss ist ein Worst-Case-Ereignis, das der Median strukturell verwaescht)
        # ist das Kriterium fuer invariants.check_sizing_cap_enforcement.
        "holdout_f_realized_peak_median": holdout_metrics.get("oos_f_realized_peak_median"),
        "holdout_f_realized_peak_max": holdout_metrics.get("oos_f_realized_peak_max"),
        # Issue #1075/#1223 (Katalog #1247+, P0) — die tatsaechlich ANGEWANDTEN (nicht die
        # konfigurierten) Kostenkomponenten dieser Study; Rohmaterial fuer
        # invariants.check_applied_cost_components_resolved. Root-Cause des Vorzustands: ein
        # Symbol mit NUR einem Spread-Symbol-Override (z. B. TSLA.ETORO) uebersprang die Asset-
        # Class-Aufloesung fuer Finanzierung/Slippage komplett (siehe backtest_runner-Fix,
        # ``has_symbol_override``-Guard) — die kalibrierte Slippage (#1204) erreichte solche
        # Symbole dadurch nie, ohne dass das im Report je sichtbar war.
        "applied_financing_bps_per_day": holdout_metrics.get("oos_applied_financing_bps_per_day"),
        "applied_slippage_bps": holdout_metrics.get("oos_applied_slippage_bps"),
        # Issue #1266 (GH #1136), Pitfall #453 — welche Kalibrierungsebene tatsaechlich aufgeloest
        # hat; Rohmaterial fuer invariants.check_cost_stress_discriminates.
        "slippage_calibration_scope": holdout_metrics.get("oos_slippage_calibration_scope"),
        # Issue #1268 (GH #1138), Pitfall #442 (siebte Instanz) — Holdout-Exit-Telemetrie: war im
        # Holdout-Re-Evaluationspfad (confirm.py) bereits korrekt GEPARST, erreichte aber nie den
        # Study-Record; Rohmaterial fuer invariants.check_selection_cost_basis_contract.
        "holdout_stop_exit_slippage_bps": holdout_metrics.get("oos_stop_exit_slippage_bps_median"),
        # Issue #1278 (GH #1151, Katalog #1272-1297, P1) — konstantes Literal (siehe
        # backtest_runner._finalize_round_trip, rt_exit_meta-Stempelung): resolve_stop_exit_
        # slippage_bps rechnet strukturell IMMER aus rohen Preisen, nie aus applied_slippage_bps.
        # None ohne jede gemessene stop_exit_slippage_bps (dieselbe Praesenz-Konvention wie das
        # Feld oben) — kein Vorspiegeln einer Messbasis fuer eine nicht-existente Messung.
        "slippage_measurement_basis": (
            "pre_cost_price"
            if holdout_metrics.get("oos_stop_exit_slippage_bps_median") is not None else None),
        "holdout_n_trailing_stop_exits": holdout_metrics.get("oos_n_trailing_stop_losses"),
        "holdout_trigger_to_fill_gap_bps": holdout_metrics.get(
            "oos_trigger_to_fill_gap_bps_median"),
        "holdout_realized_loss_bps": holdout_metrics.get("oos_realized_loss_bps_median"),
        # Issue #945/#1111 — die KANONISCHE Grösse: dieselbe Basis, aus der die Kostenstress-Werte
        # abgeleitet werden UND die seither berichtet/sortiert wird (summary_de.py Abschnitt 2.1).
        "holdout_expectancy_capital_weighted": holdout_metrics.get("oos_expectancy_capital_weighted"),
        # Issue #1265 (GH #1135) — der Nenner von holdout_expectancy_capital_weighted (Σpnl/Σnotional
        # ueber die Nennerboden-gefilterte Round-Trip-Population, siehe backtest_runner._calculate_
        # stats-Docstring zu #1031/expectancy_capital_weighted): oos_total_trades ist bereits die
        # DUST-BEREINIGTE Population (Dust wird AN DER QUELLE verworfen, VOR _calculate_stats,
        # backtest_runner._filter_dust_round_trips — oos_total_trades zaehlt sie nie mit), abzueglich
        # des ZUSAETZLICHEN, expectancy-spezifischen 5%-Median-Notional-Bodens
        # (oos_expectancy_notional_degenerate_count, #1031) — der EINZIGE weitere Ausschluss dieser
        # Population. Macht den Nenner ABLESBAR (Akzeptanzkriterium #1265), statt ihn nur indirekt
        # aus zwei anderen Feldern rekonstruieren zu muessen.
        "holdout_expectancy_capital_weighted_denominator_n": (
            (int(holdout_metrics.get("oos_total_trades") or 0)
             - int(holdout_metrics.get("oos_expectancy_notional_degenerate_count") or 0))
            if holdout_metrics.get("oos_total_trades") is not None else None),
        # Issue #1257 (GH #1127), Pitfall #454 in AGENTS.md — total_return/expectancy_capital_
        # weighted teilen sich seit diesem Fix dieselbe (kalibrierte-Slippage-korrigierte)
        # Kostenbasis (backtest_runner._apply_calibrated_slippage_to_mtm_series). Die _net-Felder
        # sind explizite Aliase von holdout_total_return/holdout_expectancy_capital_weighted oben
        # (Namensparitaet zum Akzeptanzkriterium des Issues UND zu invariants.check_cost_basis_
        # coherence), die _gross-Felder die Kostenbasis DAVOR (Traceability, None ohne aktive
        # Kalibrierung oder ohne Equity-Kurve).
        "holdout_total_return_net": holdout_metrics.get("oos_total_return_net"),
        "holdout_total_return_gross": holdout_metrics.get("oos_total_return_gross"),
        "holdout_expectancy_capital_weighted_net": holdout_metrics.get(
            "oos_expectancy_capital_weighted_net"),
        "holdout_expectancy_capital_weighted_gross": holdout_metrics.get(
            "oos_expectancy_capital_weighted_gross"),
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
        # Issue #1265 (GH #1135) — der Nenner von holdout_win_rate/holdout_profit_factor ist
        # DERSELBE bereits dust-bereinigte oos_total_trades (siehe backtest_runner._calculate_stats:
        # win_rate = wins/n, profit_factor = gross_profit/gross_loss ueber DIESELBE n-grosse
        # pnl_list, n = len(pnl_list) NACH der Dust-Filterung an der Quelle). Als eigenes Feld direkt
        # neben den Kennzahlen gestempelt, statt nur indirekt ueber das entfernte holdout_total_trades
        # (unten im Record) auffindbar zu sein — Akzeptanzkriterium #1265.
        "holdout_win_rate_denominator_n": holdout_metrics.get("oos_total_trades"),
        "holdout_profit_factor": holdout_metrics.get("oos_profit_factor"),
        "holdout_profit_factor_denominator_n": holdout_metrics.get("oos_total_trades"),
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
        # Issue #1078/#1226 (P1, Semantik-Bump, Fix Punkt 2) — welche Kostenbasis dieser Kandidat
        # tatsaechlich durchlief (round_trip_only / round_trip_plus_calibrated_slippage), gestempelt
        # von backtest_runner._apply_calibrated_slippage_deduction. None ⇒ kein Kalibrierungs-Cache/
        # Legacy-Study (siehe confirm._metrics_dict).
        "selection_cost_basis": holdout_metrics.get("oos_selection_cost_basis"),
        # Issue #1277 (GH #1150, Katalog #1272-1297) — WESHALB selection_cost_basis auf
        # round_trip_only zurueckfiel, obwohl apply_calibrated_slippage_in_selection aktiv
        # konfiguriert ist (siehe backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta).
        "selection_cost_basis_downgrade_reason": holdout_metrics.get(
            "oos_selection_cost_basis_downgrade_reason"),
        "holdout_no_alpha_detected": (
            abs(holdout_metrics["oos_alpha_tstat"]) < 1.0
            if holdout_metrics.get("oos_alpha_tstat") is not None else None
        ),
        # Issue #1038/#1187 (Katalog #1187) — ``holdout_alpha`` (Grössenordnung 1e-6/Bar) ist in
        # jeder lesbaren Nachkommastellenzahl faktisch immer "0.00000" — die ökonomisch
        # aussagekräftige Grösse ist das KUMULIERTE Holdout-Alpha ``α·n`` über das gesamte Fenster
        # (n = ``holdout_alpha_n_periods``, dieselbe Regressions-Stichprobengrösse). Beide additiv
        # verknüpft mit β: ``α·n + β·Σ(Benchmark-Log-Returns) == Σ(Strategie-Log-Returns)`` (die
        # OLS-Normalgleichung selbst — kein Rundungsfehler ausser Gleitkomma-Präzision).
        # ``holdout_alpha_times_n_pct`` folgt derselben linearen ln(1+r)≈r-Näherung, die auch die
        # übrigen Prozent-Spalten dieses Berichts verwenden (Größenordnung < 5 %, siehe Issue-
        # Referenzwerte −1,450 % … +0,449 %) — kein zweites, inkonsistentes Rundungsschema.
        "holdout_alpha_n_periods": holdout_metrics.get("oos_alpha_n_periods"),
        # Issue #1255 (GH #1125), Pitfall #454-Klasse — HC3-robuster Schaetzer neben dem
        # (homoskedastie-unterstellenden) holdout_alpha_tstat oben; das oos_min_alpha_tstat-Gate
        # konsumiert seither DIESEN Wert (backtest_runner._evaluate_oos_eligibility). holdout_
        # alpha_tstat_df sind die auf die informative Zeilenzahl gesetzten Freiheitsgrade (statt
        # der Kalender-Bar-Zaehlung holdout_alpha_n_periods).
        "holdout_alpha_tstat_hc3": holdout_metrics.get("oos_alpha_tstat_hc3"),
        "holdout_alpha_tstat_df": holdout_metrics.get("oos_alpha_tstat_df"),
        # Issue #1258 (GH #1128) — Regressions-Grundgesamtheit auditierbar: wie viele der
        # holdout_alpha_n_periods Kalender-Bars ueberhaupt Information trugen (Strategie- oder
        # Benchmark-Seite ungleich Null). n_total ist ein expliziter Alias von holdout_alpha_
        # n_periods (Akzeptanzkriterien-Feldliste des Issues, dieselbe Zahl, siehe backtest_runner.
        # _alpha_regression_diagnostics-Docstring).
        "holdout_alpha_n_total": holdout_metrics.get("oos_alpha_n_total"),
        "holdout_alpha_n_informative": holdout_metrics.get("oos_alpha_n_informative"),
        "holdout_alpha_n_y_nonzero": holdout_metrics.get("oos_alpha_n_y_nonzero"),
        "holdout_alpha_n_x_nonzero": holdout_metrics.get("oos_alpha_n_x_nonzero"),
        "holdout_alpha_n_both_zero": holdout_metrics.get("oos_alpha_n_both_zero"),
        "holdout_alpha_times_n_pct": (
            holdout_metrics["oos_alpha"] * holdout_metrics["oos_alpha_n_periods"] * 100.0
            if (holdout_metrics.get("oos_alpha") is not None
                and holdout_metrics.get("oos_alpha_n_periods") is not None)
            else None
        ),
        # Issue #1038/#1187 Fix — α selbst zusätzlich in bps je Bar (1e-6 → 0.01 bps, lesbar statt
        # einer scheinbaren Null).
        "holdout_alpha_bps_per_bar": (
            holdout_metrics["oos_alpha"] * 10000.0
            if holdout_metrics.get("oos_alpha") is not None else None
        ),
        "holdout_total_trades": holdout_metrics.get("oos_total_trades"),
        # Issue #1074/#1222 (Katalog #1247+) Fix Punkt 2 — HOLDOUT-skopierte Zaehlung nachweislicher
        # TRAILING_STOP-Exits, aus DEMSELBEN Holdout-Pfad wie ``holdout_total_trades`` (beide aus
        # ``holdout_metrics``, der geparsten Holdout-Re-Evaluation, NICHT aus ``trial_attrs`` ueber
        # alle Sweep-Trials). Root-Cause des Vorzustands: ``invariants.check_cost_stress_
        # distinctness`` bildete seinen Skalierungsterm bislang gegen ``oos_n_trailing_stop_losses``
        # auf Study-Ebene (report.py weiter oben, eine SWEEP-WEITE Summe ueber ALLE Trials) im Nenner
        # von ``holdout_total_trades`` (ein EINZELNER Holdout) — zwei verschiedene Grundgesamtheiten
        # unter einer Formel (Quotient bis zu 159,5 statt <= 1). Dieses Feld ist der korrekte,
        # holdout-skopierte Zaehler fuer ``holdout_trailing_stop_exit_share``.
        "holdout_n_trailing_stop_exits": holdout_metrics.get("oos_n_trailing_stop_losses"),
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
    # als ``realized_stop_loss_ratio_mean_per_trial`` erhalten (Zero-Regression fuer bestehende
    # Konsumenten, die explizit den Mittelwert wollen). ``None``, wenn eine der drei Eingangsgrössen
    # fehlt oder die konfigurierte Distanz <= 0 ist (kein Urteil auf einer undefinierten Zahl).
    #
    # Issue #1042/#1191 (Katalog #1191) — umbenannt von ``realized_stop_loss_ratio_mean``: dieses
    # Feld quotientiert ``oos_gross_loss_mean_bps_trailing_stop`` — den MEDIAN der PER-TRIAL-Mittel
    # (jeder Trial mittelt zuerst SEINE eigenen Verlust-Round-Trips, dann Median ueber die Trials
    # dieser Study). Die Invarianten-Nutzlast ``check_effective_stop_distance``s
    # ``realized_stop_loss_ratio_mean_pooled`` (siehe dortiger Docstring) quotientiert dagegen
    # ``oos_gross_loss_mean_bps_trailing_stop_pooled`` — einen EINZIGEN, trade-gewichteten
    # gepoolten Mittelwert ueber ALLE Trades der Study. Beide hiessen zuvor fast identisch
    # (``realized_stop_loss_ratio_mean`` vs. ``ratio_pooled_mean``) und unterschieden sich um bis
    # zu 0,63 (Issue-Referenzwert: DynamicBreakout 14,6036 vs. 13,9747) — derselbe #1005/#1157-
    # Namenskollisions-Fehlerklasse (zwei GETRENNTE Aggregationen unter kaum unterscheidbaren
    # Namen). Der Suffix ``_per_trial`` macht die tatsaechliche Aggregationsebene DIESES Feldes
    # jetzt Teil seines Namens.
    #
    # Issue #1081/#1229 (P0, Katalog #1247+) — Root-Cause: der bisherige Nenner
    # (``atr_trailing_multiplier_median · atr_median_bps``) ist ein PRODUKT zweier UNABHAENGIG
    # medianisierter Groessen — Median eines Produkts ≠ Produkt der Mediane, UND ``k``/``ATR_eff``
    # sind über den kostengekoppelten Floor korreliert (B-5: die so KONFIGURIERTE/MODELLIERTE
    # Distanz weicht Faktor 0,525-3,543 (Median 1,290) von der seit #1054/#1203 tatsaechlich
    # GEMESSENEN, getaggten Distanz ab). ``realized_stop_loss_ratio`` ist seither ``gross_loss_
    # median_bps_trailing_stop / stop_distance_bps_measured`` (DIREKT gemessen); die vormalige
    # MODELLIERTE Distanz bleibt unter ``stop_distance_bps_modelled`` (umbenannt von
    # ``stop_distance_bps``, weiterhin Rohmaterial fuer die ``atr_floor_binding_studies``-
    # Provenance) als Suchraum-Referenz erhalten, der alte Quotient unter ``realized_stop_loss_
    # ratio_vs_modelled`` (Zero-Regression fuer Konsumenten, die explizit den MODELLIERTEN
    # Quotienten wollen). ``realized_stop_loss_ratio_mean_per_trial`` bleibt bewusst auf der
    # MODELLIERTEN Basis — sonst wuerde die direkt anschliessende Mittel/Median-Ausreisser-
    # Diagnose den gemessen/modelliert-Unterschied mit dem Mittel/Median-Unterschied vermischen;
    # ihr Vergleichspartner ist deshalb seither ``realized_stop_loss_ratio_vs_modelled``, nicht
    # mehr das primaere (jetzt gemessene) Feld.
    _rt_atr = record.get("atr_median_bps")
    _rt_k = record.get("atr_trailing_multiplier_median")
    _rt_modelled_distance = (
        float(_rt_k) * float(_rt_atr)
        if (_rt_atr and _rt_k is not None) else None)
    # Issue #1026/#1175 (Katalog #866-2) — die konfigurierte/modellierte Stopdistanz (k_median ·
    # ATR_median, bps) als eigenstaendiges Report-Feld: Rohmaterial fuer die
    # ``atr_floor_binding_studies``-Provenance (siehe invariants.check_atr_scale_homogeneity).
    record["stop_distance_bps_modelled"] = (
        round(_rt_modelled_distance, 4) if _rt_modelled_distance is not None else None)
    # Issue #1273 (GH #1146, Katalog #1272-1297, P0) — unter der jetzt DEKLARIERTEN
    # ``stop_trigger_axis`` (siehe optimizer.json/check_stop_trigger_axis_coherence) ist diese
    # Grösse bei ``'bar_close_only'`` keine Verlustobergrenze mehr, sondern ein AUSLÖSE-
    # Schwellenwert (der Trigger fällt zwangsläufig auf den Bar-Schluss, nicht auf einen
    # Intrabar-Preis) — ``stop_trigger_threshold_bps`` ist der neue, semantisch praezise Name.
    # ``stop_distance_bps_modelled`` bleibt als Alias mit Deprecation-Telemetrie erhalten (Zero-
    # Regression fuer bestehende Konsumenten, siehe deren Docstrings oben); NEUE Konsumenten
    # sollen den neuen Namen verwenden.
    record["stop_trigger_threshold_bps"] = record["stop_distance_bps_modelled"]
    record["stop_distance_bps_modelled_deprecated_alias_of"] = "stop_trigger_threshold_bps"
    _rt_measured_distance = record.get("stop_distance_bps_measured")
    _rt_loss_median = record.get("gross_loss_median_bps_trailing_stop")
    if (_rt_loss_median is not None and _rt_measured_distance is not None
            and float(_rt_measured_distance) > 0):
        record["realized_stop_loss_ratio"] = round(
            float(_rt_loss_median) / float(_rt_measured_distance), 4)
    else:
        record["realized_stop_loss_ratio"] = None
    if _rt_loss_median is not None and _rt_modelled_distance and _rt_modelled_distance > 0:
        record["realized_stop_loss_ratio_vs_modelled"] = round(
            float(_rt_loss_median) / _rt_modelled_distance, 4)
    else:
        record["realized_stop_loss_ratio_vs_modelled"] = None
    _rt_loss_mean = record.get("oos_gross_loss_mean_bps_trailing_stop")
    if _rt_loss_mean is not None and _rt_modelled_distance and _rt_modelled_distance > 0:
        record["realized_stop_loss_ratio_mean_per_trial"] = round(
            float(_rt_loss_mean) / _rt_modelled_distance, 4)
    else:
        record["realized_stop_loss_ratio_mean_per_trial"] = None
    # Issue #972/#1126 Akzeptanzkriterium 3 — relative Abweichung Mittel<->Median; > 0,5 markiert die
    # Study explizit als ausreissergetrieben (der Mittelwert wird durch wenige extreme Trades
    # dominiert, statt die typische Beobachtung wiederzugeben). Issue #1081/#1229 — der Vergleich
    # bleibt auf der MODELLIERTEN Basis (beide Seiten), siehe Kommentar oben.
    if (record["realized_stop_loss_ratio_vs_modelled"] not in (None, 0)
            and record["realized_stop_loss_ratio_mean_per_trial"] is not None):
        _rel_dev = round(
            abs(record["realized_stop_loss_ratio_mean_per_trial"]
                - record["realized_stop_loss_ratio_vs_modelled"])
            / abs(record["realized_stop_loss_ratio_vs_modelled"]), 4)
        record["realized_stop_loss_ratio_mean_median_rel_dev"] = _rel_dev
        record["realized_stop_loss_ratio_outlier_driven"] = _rel_dev > 0.5
    else:
        record["realized_stop_loss_ratio_mean_median_rel_dev"] = None
        record["realized_stop_loss_ratio_outlier_driven"] = None
    return record, checks


def _budget_execution_summary(studies_out: list[dict[str, Any]]) -> dict[str, Any]:
    """Issue #770 — Median + p10 von ``budget_executed_fraction`` ueber alle Studies eines Laufs
    (Sweep-Ebenen-Aggregation, Akzeptanzkriterium #770). ``None``-Felder bei leerer Kohorte.

    Issue #1065/#1215 (P2, Katalog #1196-1221) — Root-Cause: Median UND p10 werden ueber STUDIES
    gebildet, waehrend das eigentliche Defizit eine SUMME ueber TRIALS ist — einzelne Studies mit
    Ausfall (z. B. ein abgebrochener Wallclock-Preflight, siehe ``stop_reason``) verschwinden im
    Median/p10, solange die MEHRHEIT der Studies ihr Budget voll ausfuehrt (Beweis: ``c429c992``
    fehlten 5,4% der Trials — 104 von 1940 — bei ``p10=100,0%``, KEIN Perzentil zeigte das an).
    ``min`` (das striktere, garantiert monoton fallende Minimum ueber ALLE Studies) ergaenzt
    Median/p10 seit diesem Fix — ein einziger vollstaendiger Ausfall macht ``min=0,0%`` sichtbar,
    unabhaengig von der Gesamtzahl der Studies."""
    import statistics as _stats
    fractions = sorted(
        r["budget_executed_fraction"] for r in studies_out
        if r.get("budget_executed_fraction") is not None
    )
    if not fractions:
        return {"median": None, "p10": None, "min": None, "n": 0}
    median = _stats.median(fractions)
    p10_idx = max(0, min(len(fractions) - 1, int(round(0.10 * (len(fractions) - 1)))))
    return {
        "median": round(median, 4), "p10": round(fractions[p10_idx], 4),
        "min": round(fractions[0], 4), "n": len(fractions),
    }


def _budget_deficit_studies(studies_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issue #1065/#1215 Fix Punkt 2 — die NAMENTLICHE Liste der Studies, deren
    ``n_trials_completed`` unter ihrem eigenen ``n_trials_budgeted`` liegt (Ist/Soll/Grund), statt
    nur einer aggregierten Rate.
    Konsumiert von summary_de.py, wenn ``Σ n_trials < Σ n_trials_budget`` (Akzeptanzkriterium:
    "Jeder Lauf mit Σ trials < Σ budget nennt die verantwortlichen Studies").

    ``reason`` (vom Issue als ``abort_reason`` bezeichnet) ist ``record['stop_reason']`` — dasselbe
    Feld, das ``compute_budget_execution`` bereits je Study liefert (kein zweites, paralleles
    Vokabular fuer denselben Sachverhalt)."""
    out = []
    for r in studies_out:
        # Issue #1065/#1215 — dieselben Feldnamen wie die "Trials gesamt: X von Y budgetiert"-Zeile
        # (summary_de.py §3.3), NICHT das rohe ``n_trials`` (das zaehlt JEDEN Optuna-TrialState,
        # ``n_trials_completed``/``n_trials_budgeted`` sind die ``compute_budget_execution``-Groessen,
        # gegen die die Σ-Bedingung des Akzeptanzkriteriums tatsaechlich gebildet wird).
        n_completed = r.get("n_trials_completed")
        n_budgeted = r.get("n_trials_budgeted")
        if n_completed is None or n_budgeted is None or n_completed >= n_budgeted:
            continue
        out.append({
            "strategy": r.get("strategy"), "symbol": r.get("symbol"),
            "n_trials_completed": n_completed, "n_trials_budgeted": n_budgeted,
            "deficit": n_budgeted - n_completed, "stop_reason": r.get("stop_reason"),
        })
    return out


# Issue #1071/#1221 (Katalog #1196-1221, P2) — dieselbe relative Schwelle, die der Issue-Text
# explizit nennt ("n_periods unter 1/6 des Symbol-Medians, typisch Squeeze, n_periods 5,0"). Bewusst
# EIGENSTAENDIG von ``sweep._study_oos_n_periods_median``/``min_oos_periods_for_family`` (der
# DSR-Multiplizitaets-Ausschluss, ein ABSOLUTER Konfig-Schwellenwert fuer einen ANDEREN Zweck,
# siehe dortiger Docstring) — hier geht es um die Kommensurabilitaet der symbolweiten Rangliste
# (relativ zum jeweils EIGENEN Symbol-Median), nicht um eine belastbare DSR-Stichprobengroesse.
_ANNUALIZATION_RANKING_MIN_N_PERIODS_FRACTION = 1.0 / 6.0


def _annualization_excluded_studies(studies_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issue #1071/#1221 — die NAMENTLICHE Liste der Studies, deren ``oos_n_periods_median`` unter
    ``1/6`` des MEDIANS ihres EIGENEN Symbols liegt (typischer Fall laut Issue-Text: Squeeze mit
    ``n_periods≈5,0``, während andere Strategien desselben Symbols deutlich mehr informative
    Perioden sehen). Diese Studies werden von summary_de.py §2.3 separat ausgewiesen statt
    stillschweigend in dieselbe Vergleichstabelle wie ihre gut besetzten Symbol-Geschwister
    gemischt zu werden — ihr annualisierter Sortino beruht auf einer strukturell duennen
    Beobachtungsbasis, unabhängig davon, wie stabil der (seit diesem Fix bar-achsen-basierte,
    symbolweite) Annualisierungsfaktor selbst ist.

    Nur Symbole mit >= 2 Studies mit definiertem ``oos_n_periods_median`` haben ueberhaupt einen
    Median, gegen den eine relative Schwelle sinnvoll ist (ein Einzel-Study-Symbol hat keinen
    Vergleichspartner)."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for r in studies_out:
        symbol = r.get("symbol")
        median = r.get("oos_n_periods_median")
        if symbol is None or median is None:
            continue
        by_symbol.setdefault(symbol, []).append(r)
    out: list[dict[str, Any]] = []
    for symbol, records in by_symbol.items():
        if len(records) < 2:
            continue
        symbol_median = statistics.median(r["oos_n_periods_median"] for r in records)
        threshold = symbol_median * _ANNUALIZATION_RANKING_MIN_N_PERIODS_FRACTION
        for r in records:
            if r["oos_n_periods_median"] < threshold:
                out.append({
                    "strategy": r.get("strategy"), "symbol": symbol,
                    "oos_n_periods_median": r["oos_n_periods_median"],
                    "symbol_oos_n_periods_median": round(symbol_median, 2),
                    "threshold": round(threshold, 2),
                })
    return sorted(out, key=lambda e: (str(e["symbol"]), str(e["strategy"])))


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



# Issue #1096/#1244 (P2, Katalog #1247+) — Root-Cause: #1219 (``_writeback_search_stagnation_
# diagnoses`` unten) hat den Rückschrieb NUR für ``search_made_progress``-Stagnation UND die
# STRUCTURAL_ZERO_ELIGIBLE-Restmenge verdrahtet — ``STRUCTURAL_ALL_UNEVALUABLE`` (0 EVALUABLE
# Trials, eine Stufe VOR ``STRUCTURAL_ZERO_ELIGIBLE``) blieb komplett aussen vor: dieser
# ``stop_reason`` erzeugte NIE einen ``diagnosed_pairs``-Eintrag, unabhängig vom Cache-Schalter.
_STRUCTURAL_DIAGNOSIS_STOP_REASONS = frozenset({
    "STRUCTURAL_ZERO_ELIGIBLE", "STRUCTURAL_ALL_UNEVALUABLE",
})


def _structural_zero_eligible_diagnosed_pairs(
    studies_out: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Issue #1045/#1194 — Root-Cause: der #679/#829/#830/#831-Rückschriebpfad
    (``run_optimization.py`` → ``sweep_diagnostics.record_diagnosed_pair``) schreibt AUSSCHLIESSLICH
    in den ``diagnosed_pairs_cache.json``-Store, dessen Schreibpfad seit #1090 komplett unterdrückt
    ist (``optimizer.json['diagnostic_writeback_enabled'] = false``, siehe
    ``sweep_diagnostics._read_diagnostic_writeback_enabled``-Docstring — eine bewusste, dokumentierte
    Sicherheitsmassnahme gegen Mehrprozess-Korruption, #1086). Symptom: 123/123 AdxAtrMomentum/TSLA-
    Trials trafen dasselbe Gate (``REJECT_OOS_MIN_PSR``) — eine reale, gemessene, hoch-signifikante
    Diagnose —, aber ``diagnosed_pairs`` blieb LEER, weil dieser eine Cache-Schreibpfad global AUS
    ist.

    Fix: dieselbe #1039/#1188-Lektion — ``diagnostic_writeback_enabled`` steuert NUR, ob der
    AUTOMATISCHE Budget-Skip (``enumerate_tunable_pairs``, ANWENDUNG der Empfehlung) über Läufe
    hinweg PERSISTIERT wird, NICHT, ob DIESER Lauf seine eigene Diagnose im Report SICHTBAR machen
    darf. ``studies_out`` trägt bereits ``stop_reason`` UND ``is_rejection_detail_counts`` (siehe
    ``_study_record`` oben) — genug, um ``sweep_diagnostics.diagnose_structural_zero_eligible_gate``
    live, ohne jeden Cache-Zugriff, für DIESEN Lauf auszuwerten. Der Vorschlag wird hier NUR
    GESCHRIEBEN (Report-Sichtbarkeit) — die ANWENDUNG (tatsächliche Denylist-/Bounds-Änderung)
    bleibt exakt wie im Issue-Text gefordert ein separater, bestätigter Schritt (weiterhin über den
    gated Cache-Pfad, sobald #1066/#1086 an einem echten Mehrprozess-Lauf abgenommen sind).

    Issue #1096/#1244 — auch ``STRUCTURAL_ALL_UNEVALUABLE``-Studies durchlaufen jetzt dieselbe
    LIVE-Ableitung (``stop_reason`` wird durchgereicht, siehe ``diagnose_structural_zero_eligible_
    gate``-Docstring für den eigenen, unbedingt frequenzseitigen Zweig dieses ``stop_reason``s)."""
    from automation.optimizer.sweep_diagnostics import diagnose_structural_zero_eligible_gate
    out = []
    for r in studies_out:
        stop_reason = r.get("stop_reason")
        if stop_reason not in _STRUCTURAL_DIAGNOSIS_STOP_REASONS:
            continue
        diagnosis = diagnose_structural_zero_eligible_gate(
            r.get("is_rejection_detail_counts"), stop_reason=stop_reason)
        if diagnosis["binding_cause"] in (None, "none"):
            continue
        out.append({
            "strategy": r.get("strategy"), "symbol": r.get("symbol"),
            "action": diagnosis["proposed_action"], "binding_cause": diagnosis["binding_cause"],
            "n_runs_confirmed": None, "expires_after_runs": None,
            "budget_executed_fraction": r.get("budget_executed_fraction"),
            "dominant_rejection_detail": diagnosis["dominant_rejection_detail"],
            "dominant_fraction": diagnosis["dominant_fraction"],
            "source": "live_derivation",
        })
    return out


def _atr_floor_dominant_diagnosed_pairs(
    studies_out: list[dict[str, Any]], *,
    freeze_threshold: float = 0.60,
    min_trials: int = 30,
) -> list[dict[str, Any]]:
    """Issue #1263 (GH #1133) Fix Punkt 3 — dieselbe LIVE (cache-unabhängige), report-sichtbare
    Ableitung wie ``_structural_zero_eligible_diagnosed_pairs`` (#1045/#1194-Konvention), aber für
    eine ANDERE Ursachen-Achse: eine Study, deren ``atr_floor_binding_trial_fraction`` über
    ``freeze_threshold`` liegt (bei >= ``min_trials`` abgeschlossenen Trials, siehe
    ``invariants.check_atr_floor_dimension_freeze_candidates``-Docstring für den Scope dieses
    Fixes), verschwendet Suchbudget auf eine strukturell wirkungslose Dimension — dieselbe
    "Diagnose ohne Rückschrieb"-Fehlerklasse wie #1244, hier für ``binding_cause=
    'atr_floor_dominant'``. ``action='none'`` (keine Denylist-Konsequenz — die Studie selbst bleibt
    gültig, nur eine EINZELNE Suchdimension ist betroffen)."""
    out = []
    for r in studies_out:
        symbol, strategy = r.get("symbol"), r.get("strategy")
        if not symbol or not strategy:
            continue
        fraction = r.get("atr_floor_binding_trial_fraction")
        n_trials = r.get("n_trials_completed")
        if fraction is None or n_trials is None or int(n_trials) < min_trials:
            continue
        if float(fraction) <= freeze_threshold:
            continue
        out.append({
            "strategy": strategy, "symbol": symbol, "action": "none",
            "binding_cause": "atr_floor_dominant",
            "n_runs_confirmed": None, "expires_after_runs": None,
            "budget_executed_fraction": r.get("budget_executed_fraction"),
            "atr_floor_binding_trial_fraction": fraction,
            "source": "live_derivation",
        })
    return out


def _diagnosed_pairs_section(
    studies_out: list[dict[str, Any]] | None = None, *,
    atr_floor_dimension_freeze_threshold: float = 0.60,
) -> list[dict[str, Any]]:
    """Issue #830 Fix Punkt 4 — ALLE Diagnose-Cache-Einträge (nicht nur die ``'denylist'``-
    Teilmenge von ``_diagnosed_pairs_skipped_section``) mit ``action``, ``binding_cause``,
    ``n_runs_confirmed`` und ``expires_after_runs`` je Eintrag: die Deaktivierungs-/Deprioritisierungs-
    Entscheidungen müssen genauso nachvollziehbar sein wie die Promotion-Entscheidungen, nicht nur
    im Cache-JSON verborgen.

    Issue #1045/#1194 — GEMERGT mit den LIVE (cache-unabhängig) aus ``studies_out`` abgeleiteten
    STRUCTURAL_ZERO_ELIGIBLE-Befunden dieses Laufs (siehe
    ``_structural_zero_eligible_diagnosed_pairs``-Docstring) — derselbe "primär aus studies_out,
    Cache als Anreicherung"-Vertrag wie ``_boundary_solutions_section`` (#1039/#1188). Ein
    Cache-Eintrag (mehr Historie: ``n_runs_confirmed``/``expires_after_runs``) überschreibt den
    Live-Befund für dasselbe (strategy, symbol)-Paar, falls beide existieren.

    Issue #1263 (GH #1133) — ZUSÄTZLICH gemergt mit ``_atr_floor_dominant_diagnosed_pairs`` (eine
    von STRUCTURAL_ZERO_ELIGIBLE unabhängige Ursachen-Achse — eine Study kann beide, eine, oder
    keine der beiden Live-Ableitungen gleichzeitig tragen, da sie unterschiedliche (strategy,
    symbol)-Paare ODER dieselbe Study aus unterschiedlichem Grund treffen können; im seltenen Fall
    identischer Paare gewinnt die zuletzt gemergte Quelle, siehe Merge-Reihenfolge unten)."""
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {
        (e["strategy"], e["symbol"]): e
        for e in _structural_zero_eligible_diagnosed_pairs(studies_out or [])
    }
    for e in _atr_floor_dominant_diagnosed_pairs(
            studies_out or [], freeze_threshold=atr_floor_dimension_freeze_threshold):
        by_key[(e["strategy"], e["symbol"])] = e
    for entry in _diagnosed_pairs_all():
        by_key[(entry.get("strategy"), entry.get("symbol"))] = {
            "strategy": entry.get("strategy"), "symbol": entry.get("symbol"),
            "action": entry.get("action"), "binding_cause": entry.get("binding_cause"),
            "n_runs_confirmed": entry.get("n_runs_confirmed"),
            "expires_after_runs": entry.get("expires_after_runs"),
            "budget_executed_fraction": entry.get("budget_executed_fraction"),
            "source": "diagnosis_cache",
        }
    return sorted(by_key.values(), key=lambda e: (str(e.get("strategy")), str(e.get("symbol"))))


def _writeback_search_stagnation_diagnoses(
    search_made_progress_offenders: dict[str, float] | None,
    structural_zero_eligible_missing: list[str] | None,
    *, run_id: str, work_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Issue #1069/#1219 (P2, Katalog #1196-1221) — "Diagnose ohne Konsequenz": ``check_search_
    made_progress``-FAIL und ``check_structural_zero_eligible_has_diagnosis``-FAIL werden BEIDE
    gemeldet (seit #1194 im Report sichtbar), aber KEINER der beiden Befunde erreicht bisher den
    #681/#761-Diagnose-Rückschrieb — dieselben stagnierenden (strategy, symbol)-Paare werden bei
    JEDEM Lauf neu enumeriert, ohne dass der wiederholte Befund je eine Konsequenz hat (Root-Cause
    #1219, dieselbe Fehlerklasse wie #681 vor seinem eigenen Fix).

    Fix: beide Befundmengen werden hier zu (strategy, symbol)-Paaren vereinigt und über die
    bestehende ``sweep_diagnostics.recommend_diagnosis_action``/``record_diagnosed_pair``-Pipeline
    (denselben Cache wie jeder andere Diagnose-Pfad, ``diagnostic_writeback_enabled`` gilt
    unverändert) zurückgeschrieben — mit einer eigenständigen ``binding_cause`` (``'search_
    stagnation'``, siehe dortiger ``recommend_diagnosis_action``-Zweig), NICHT vermischt mit den
    Trade-Frequenz-Ursachen der PER-STUDY-Rückschriebe in ``run_optimization.py``. ``n_runs_
    confirmed`` wird hier — wie bei jedem anderen Aufrufer (siehe dortige Vorbild-Stellen) — aus
    dem VORHERIGEN Cache-Eintrag gelesen (nur, wenn dessen ``binding_cause`` bereits ``'search_
    stagnation'`` war), NICHT neu erfunden.

    Fail-open je Paar: ein einzelner defekter Eintrag darf den restlichen Report nicht zum Absturz
    bringen (dieselbe Absicherung wie die ``try/except`` um jeden anderen Rückschrieb-Aufruf in
    ``run_optimization.py``). Rückgabe: die Liste der tatsächlich erzeugten Empfehlungen (für
    Tests/Telemetrie), leer, wenn nichts zu tun war."""
    from automation.optimizer.sweep_diagnostics import (
        recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
    )
    pairs: set[tuple[str, str]] = set()
    for key in (search_made_progress_offenders or {}):
        strat, _, sym = str(key).partition("/")
        if strat and sym:
            pairs.add((strat, sym))
    for key in (structural_zero_eligible_missing or []):
        strat, _, sym = str(key).partition("/")
        if strat and sym:
            pairs.add((strat, sym))
    if not pairs:
        return []
    try:
        cache = load_diagnosed_pairs_cache(work_dir)
    except Exception:
        cache = {}
    recommendations: list[dict[str, Any]] = []
    for strategy, symbol in sorted(pairs):
        try:
            prior = cache.get((strategy, symbol))
            n_runs_confirmed = (
                int(prior.get("n_runs_confirmed", 0))
                if prior and prior.get("binding_cause") == "search_stagnation" else 0
            )
            rec = recommend_diagnosis_action(
                strategy, symbol, {"binding_cause": "search_stagnation"},
                n_runs_confirmed=n_runs_confirmed,
            )
            record_diagnosed_pair(rec, work_dir=work_dir, run_id=run_id)
            recommendations.append(rec)
        except Exception:
            _log.debug(
                "Issue #1069/#1219: search_stagnation-Rueckschrieb fuer %s/%s fehlgeschlagen "
                "(non-fatal).", strategy, symbol, exc_info=True)
    return recommendations


# Issue #1039/#1188 (Katalog #1188) — dieselbe Schwelle wie confirm.py's ``boundary_overfit``
# (``#622``: ``boundary_frac > 0.3``), jetzt auch als benannte Konstante hier, wo sie die
# Study-Records-Ableitung von ``boundary_solutions`` speist.
_BOUNDARY_SOLUTION_FRACTION_THRESHOLD = 0.3


def _boundary_solutions_section(studies_out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Issue #831 Fix Punkt 4 — Randlösungen als eigene Report-Sektion: ``{strategy, symbol,
    fraction, params, proposed_bounds, ...}`` je Study, deren Gewinner an der Suchraumgrenze klebt.

    Issue #1039/#1188 (Katalog #1188) — Root-Cause: diese Sektion wurde AUSSCHLIESSLICH aus dem
    #761-Diagnose-Cache (``_diagnosed_pairs_all()``, ``binding_cause == 'boundary_solution'``)
    gespeist — einem SEPARATEN Pfad, der nur schreibt, wenn ``diagnostic_writeback_enabled=True``
    ist (seit #1090 standardmässig ``False`` in der ausgelieferten ``optimizer.json``). Drei
    Studies trugen im Referenzlauf ``winner_outside_default_bounds_after_override``/neun trugen
    ``boundary_hit_fraction > 0`` in ihren EIGENEN Study-Records — aber der Diagnose-Cache blieb
    leer, ``boundary_solutions == []`` widersprach den Study-Feldern DESSELBEN Reports, ohne dass
    der Widerspruch auffiel. Fix: die Menge wird jetzt PRIMÄR aus den bereits gebauten
    ``studies_out``-Records abgeleitet (``winner_outside_default_bounds_after_override`` gesetzt
    ODER ``boundary_hit_fraction > 0.3``) — dieselbe Quelle wie jedes andere Study-Feld, IMMER
    konsistent mit ihnen. Der Diagnose-Cache bleibt als ZUSÄTZLICHE Anreicherung erhalten
    (``proposed_bounds``/``widen_applications``, die NUR dort existieren, wenn Writeback lief) —
    per (strategy, symbol) gemerged, fehlt aber nicht mehr GANZ, nur weil der Cache leer ist.
    ``studies_out=None`` (Legacy-Aufrufer) ⇒ bit-identisch zum Pre-#1039-Verhalten (nur
    Diagnose-Cache)."""
    diagnosed_by_key = {
        (e.get("strategy"), e.get("symbol")): e
        for e in _diagnosed_pairs_all() if e.get("binding_cause") == "boundary_solution"
    }
    if studies_out is None:
        return [
            {
                "strategy": e.get("strategy"), "symbol": e.get("symbol"),
                "fraction": e.get("boundary_hit_fraction"),
                "params": e.get("boundary_params"),
                "proposed_bounds": e.get("proposed_bounds"),
                "boundary_parameter": e.get("boundary_parameter"),
                "boundary_side": e.get("boundary_side"),
                "boundary_veto_evidence": e.get("boundary_veto_evidence"),
                "widen_applications": e.get("widen_applications") or {},
            }
            for e in diagnosed_by_key.values()
        ]
    out: list[dict[str, Any]] = []
    for r in studies_out:
        frac = r.get("boundary_hit_fraction")
        has_strict_override = bool(r.get("winner_outside_default_bounds_after_override"))
        if not (has_strict_override
                or (frac is not None and frac > _BOUNDARY_SOLUTION_FRACTION_THRESHOLD)):
            continue
        cache_entry = diagnosed_by_key.get((r.get("strategy"), r.get("symbol"))) or {}
        out.append({
            "strategy": r.get("strategy"), "symbol": r.get("symbol"),
            "fraction": frac,
            # Issue #1101 (Katalog #934) Akzeptanzkriterium 1 — derselbe klemmende Parameter wie
            # im Study-Record; faellt auf den Diagnose-Cache-Eintrag zurueck, falls die Study-
            # Felder (aeltere Artefakte) den Parameter selbst nicht trugen.
            "boundary_parameter": r.get("boundary_parameter") or cache_entry.get("boundary_parameter"),
            "boundary_side": r.get("boundary_side") or cache_entry.get("boundary_side"),
            # Issue #958/#1124 (Katalog #960) — die volle, benannte Evidenz je klemmendem
            # Parameter, primär aus dem Study-Record selbst (immer verfügbar, sobald das Veto
            # feuerte), sonst aus dem Diagnose-Cache.
            "boundary_veto_evidence": (
                r.get("boundary_veto_evidence") or cache_entry.get("boundary_veto_evidence")),
            # Issue #1067 — der strikte Bounds-Bruch (falls vorhanden) direkt in dieser Sektion
            # sichtbar, nicht nur im Study-Record selbst.
            "winner_outside_default_bounds_after_override": (
                r.get("winner_outside_default_bounds_after_override")),
            # Diese beiden Felder existieren AUSSCHLIESSLICH im #761-Diagnose-Cache (ein
            # konkreter, geweiteter Bounds-VORSCHLAG ist kein Study-Messwert) — None/leer, wenn
            # Writeback fuer dieses Paar (noch) nicht lief.
            "params": cache_entry.get("boundary_params"),
            "proposed_bounds": cache_entry.get("proposed_bounds"),
            # Issue #1101 (Katalog #934) Akzeptanzkriterium 2 — wie oft dieser Parameter bereits
            # nachgeweitet wurde.
            "widen_applications": cache_entry.get("widen_applications") or {},
        })
    return out


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

    # Issue #1044/#1193 Fix Punkt 2 — {store_path, store_found, mtime_utc, entry_count, keys},
    # analog ``symbol_bar_quality_cache_status`` (#1016/#1168): macht "leer" (STORE_EMPTY) von
    # "woanders" (falsches WORK, STORE_PATH_MISSING) unterscheidbar, statt drei aufeinanderfolgende
    # Läufe identisch ``{stored: 0, ...}`` melden zu lassen, ohne dass der Report selbst sagt, WO
    # er nachgesehen hat. Ein frischer Scan (nicht das evtl. veraltete CHAMPION_STORE_SCAN-Ereignis
    # unten) — der aktuelle Store-Zustand JETZT, unabhängig davon, ob ein Ereignisstrom auflösbar ist.
    _store_status = _champions_mod.store_status()

    empty = {"stored": 0, "admissible": 0, "corroborated": 0, "written_back": 0,
             "skipped_by_reason": {}, "semantics_migrated": 0,
             "admissible_despite_simulation_stale": 0, "max_corroboration_count": None,
             "attempts": None, **_store_status}
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

    # Issue #1044/#1193 Fix Punkt 3 — STORE_PATH_MISSING vs. STORE_EMPTY: ``_store_status`` oben ist
    # der Report-ZEIT-Zustand (nach dem Lauf — ``_champions_dir()`` (Zeile oben) hat das Verzeichnis
    # inzwischen laengst angelegt, falls es das nicht schon war). Ob das Verzeichnis VOR DIESEM LAUF
    # existierte, ist NUR aus dem ``CHAMPION_STORE_SCAN``-Ereignis rekonstruierbar (von
    # ``run_per_symbol_sweep`` als ALLERERSTE Champion-Store-Beruehrung emittiert, siehe
    # ``champions.store_status``-Docstring). Drei Faelle: (1) ``_events_path`` traegt ein
    # CHAMPION_STORE_SCAN-Ereignis — die massgebliche Lauf-Start-Evidenz. (2) Kein Ereignisstrom
    # auflösbar (``_events_path is None``, echter ``--report-only``-Prozess) — Fallback auf den
    # aktuellen ``_store_status``-Scan (degradiert, aber bester verfuegbarer Ersatz, analog dem
    # ``attempts``-Fallback oben). (3) Ein Ereignisstrom EXISTIERT, traegt aber KEIN
    # CHAMPION_STORE_SCAN-Ereignis (ein Lauf von VOR diesem Fix, oder ein Test, der nur
    # CHAMPION_WRITEBACK-Ereignisse registriert) — hier gibt es KEINE verlaessliche Lauf-Start-
    # Evidenz; ohne sie NICHT umbenennen (STORE_EMPTY bleibt STORE_EMPTY) statt eines unbelegten
    # Rateversuchs ueber den (moeglicherweise laengst veraenderten) aktuellen Zustand.
    _store_found_at_run_start: bool | None
    if _events_path is None:
        _store_found_at_run_start = _store_status["store_found"]
    else:
        _scan_events = _read_jsonl_events(_events_path, "CHAMPION_STORE_SCAN")
        _store_found_at_run_start = bool(_scan_events[0].get("store_found")) if _scan_events else None
    if _store_found_at_run_start is False and skipped_by_reason.get("STORE_EMPTY"):
        skipped_by_reason["STORE_PATH_MISSING"] = skipped_by_reason.pop("STORE_EMPTY")

    return {
        "stored": stored, "admissible": admissible, "corroborated": corroborated,
        "written_back": written_back, "skipped_by_reason": dict(skipped_by_reason),
        "semantics_migrated": semantics_migrated,
        "admissible_despite_simulation_stale": admissible_despite_simulation_stale,
        "max_corroboration_count": max_corroboration_count, "attempts": attempts,
        **_store_status,
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


def family_n_frozen_stage1_from_proposals(
    proposals: list[dict],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Issue #977/#1131 (Katalog #986) — die EINGEFRORENE Sicht traegt den PER-STRATEGIE-Wert je
    Proposal (siehe ``sweep._run_confirm_and_export``-Stempel-Docstring), NICHT eine bereits
    symbolweit summierte Konstante — MAX PRO STRATEGIE (eine fehlende Study liefert 0 Beitrag, ein
    doppeltes Proposal DERSELBEN Strategie zaehlt nicht doppelt), dann SUMME ueber die Strategien
    je Symbol.

    Issue #1254 (GH #1124) — extrahiert aus ``_build_report`` (vormals inline), damit ``sweep.py``
    dieselbe Aggregation fuer ``sweep_completed.n_family_attempted_frozen`` OHNE einen zweiten,
    unabhaengig gepflegten Berechnungspfad wiederverwenden kann — GENAU die Divergenz-Fehlerklasse
    (zwei Quellen fuer denselben Begriff, hier ``sweep_completed.deflation_n_family`` vs.
    ``run_json.cross_study.n_family.frozen``, Faktor 813 im #1254-Symptom), die dieses Issue an
    anderer Stelle aufdeckt. Gibt ``(stage1, by_symbol)`` zurueck: ``stage1`` ist die
    PER-(Symbol,Strategie)-Zerlegung (Rohmaterial fuer ``invariants.check_family_scope_coherence``
    und den #1091-Stabilitaets-Vergleich), ``by_symbol`` die Summe je Symbol (== ``cross_study
    ['n_family']['frozen']``/``n_family_stage1_sum_frozen``)."""
    stage1: dict[str, dict[str, int]] = {}
    for _p in proposals:
        _frozen = _p.get("deflation_n_family_frozen")
        _sym = _p.get("symbol")
        _strat = _p.get("strategy")
        if _sym and _strat and isinstance(_frozen, (int, float)) and not isinstance(_frozen, bool):
            _per_strategy_frozen = stage1.setdefault(_sym, {})
            _per_strategy_frozen[_strat] = max(_per_strategy_frozen.get(_strat, 0), int(_frozen))
    by_symbol = {symbol: sum(per_strategy.values()) for symbol, per_strategy in stage1.items()}
    return stage1, by_symbol


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


def _compute_work_aborted(run_status: str) -> bool:
    """Issue #1037/#1186 (Katalog #1186) — die DRITTE orthogonale Achse: ``True`` genau dann, wenn
    ``run_status`` einen echten Arbeitsabbruch beschreibt. Dieselbe, bereits etablierte Definition
    wie ``sweep._sweep_completion_event`` ("jeder ``run_status``, der mit ``'aborted_'`` beginnt")
    — EINE Quelle statt einer zweiten, potenziell abweichenden Kopie dieser Regel. Ersetzt das
    vorherige ``fail_fast_triggered``-Feld, dessen NAME faelschlich "ein Abbruch geschah" suggerierte,
    obwohl es lediglich benannte, DASS eine blockierende Invariante gefailt hat — unabhaengig davon,
    ob der Lauf deswegen abbrach (Root-Cause #1037: ``fail_fast_triggered='check_x'`` UND
    ``run_status='completed_invalid'`` — also VOLLSTAENDIG, kein Abbruch — traten gemeinsam auf)."""
    return run_status.startswith("aborted_")


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


def _prior_holdout_total_return(
    prior_run_id: str | None, strategy: str | None, symbol: str | None,
    *, reports_dir: Path | None = None,
) -> float | None:
    """Issue #1090/#1238 (P1, Katalog #1247+) — der HOLDOUT-Rueckgabewert des Vorlaufs
    (``prior_run_id``) fuer DIESELBE (Strategie, Symbol)-Study, gelesen aus dessen bereits
    persistiertem Report-Artefakt (``REPORTS_DIR / f"run_{prior_run_id}.json"``, siehe
    ``generate_sweep_report``). Kein Optuna-Trial traegt ein Holdout-Ergebnis (das ist
    ausschliesslich ``confirm.py``s Re-Evaluations-Pfad, gebunden an den jeweiligen Report-Lauf) —
    dies ist die EINZIGE Quelle fuer den Vorlauf-Referenzwert.

    Fail-open (``None``): fehlendes ``prior_run_id``, kein lesbares/parsbares Report-Artefakt fuer
    diesen Lauf, oder keine (Strategie, Symbol)-Study darin — ein fehlender Vorlauf-Report darf die
    Report-Generierung DIESES Laufs nie zum Absturz bringen (dieselbe Konvention wie jeder andere
    ``_load_json``-Aufrufer in diesem Modul)."""
    if not prior_run_id:
        return None
    _dir = reports_dir if reports_dir is not None else REPORTS_DIR
    _path = Path(_dir) / f"run_{prior_run_id}.json"
    _prior_report = _load_json(_path)
    if not _prior_report:
        return None
    for _r in _prior_report.get("studies") or []:
        if _r.get("strategy") == strategy and _r.get("symbol") == symbol:
            _value = _r.get("holdout_total_return")
            return float(_value) if isinstance(_value, (int, float)) else None
    return None


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
    blocking_invariant_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
    # Issue #1269 (GH #1139) Fix Punkt 3 — Wallclock-Sekunden (seit Sweep-Start), zu denen die
    # In-Prozess-Fail-Fast-Probe auswertete (``sweep.sweep_fail_fast_probe_triggered_at_wallclock_s``),
    # unabhaengig davon, ob sie feuerte. ``None``, wenn keine Probe stattfand ODER dieser Report kein
    # In-Prozess-Signal traegt (Report-Scan/``--report-only``). Treibt zusammen mit ``wallclock_s``
    # (oben) die Telemetrie ``blocking_invariant_probe_triggered_at_wallclock_fraction`` unten.
    probe_triggered_at_wallclock_s: float | None = None,
    reports_dir: Path | None = None,
) -> dict:
    # Issue #942/#1108 (Katalog #960) — ``blocking_invariant_triggered`` (der Name der
    # Fail-Fast-Invariante, die eine LIVE In-Prozess-Probe waehrend des Sweeps ausloeste, oder
    # ``None``) treibt ZUSAMMEN mit den unten berechneten ``work_completed``/``decision_admissible``/
    # ``work_aborted`` die VIER orthogonalen Achsen, die den bisher ueberladenen ``run_status``-
    # String ergaenzen (siehe dortige Feld-Docstrings unten). Root-Cause #1108: derselbe Faktenstand
    # (14/14 Studies, volles Budget, Fail-Fast-Abbruch NACH Abschluss der Arbeit) ergab je nach
    # Report-Erzeugungspfad ZWEI verschiedene ``run_status``-Werte (``completed_invalid`` vs.
    # ``aborted_invariant``, LETZTERER faelschlich als "echter Arbeitsabbruch" gelesen) — die vier
    # neuen Felder werden HIER, EINMAL, aus derselben Quelle (den bereits berechneten
    # ``invariant_checks`` plus den durchgereichten Symbol-Zaehlern plus ``run_status`` selbst)
    # abgeleitet, unabhaengig davon, welcher Pfad (regulaerer Abschluss oder Abbruch-Exception)
    # ``_build_report`` letztlich aufruft.
    #
    # Issue #1037/#1186 (Katalog #1186) — Root-Cause der VORHERIGEN Version dieses Feldes
    # (``fail_fast_triggered``): der NAME selbst behauptete "ein Fail-Fast-ABBRUCH geschah", obwohl
    # das Feld lediglich den Namen einer gefailten blockierenden Invariante trug — VOELLIG
    # unabhaengig davon, ob der Lauf deswegen tatsaechlich abbrach (``sweep.py``s Fail-Fast-Probe
    # kann NACH vollstaendiger Abarbeitung aller geplanten Symbole feuern, siehe #1065-Kommentar in
    # ``sweep.py``). Umbenannt auf ``blocking_invariant_triggered`` (kein "fail_fast" mehr im Namen)
    # — Akzeptanzkriterium #1037/2 ist damit STRUKTURELL erfuellt: kein Feldname mit "fail_fast"
    # existiert mehr im Report, der ohne echten Abbruch (``work_aborted``) gesetzt sein koennte.
    #
    # ``run_status``-Ableitungstabelle (Akzeptanzkriterium #1037/1 — zwei Laeufe mit identischem
    # ``(work_completed, decision_admissible, work_aborted)`` tragen denselben ``run_status``):
    #
    #   work_completed | decision_admissible | work_aborted | run_status
    #   ---------------|----------------------|--------------|----------------------------------
    #   True            True                  False          'complete' (oder eine der rein
    #                                                          INFORMATIVEN, nicht-widerspruechlichen
    #                                                          Sub-Varianten 'completed_with_
    #                                                          quarantine'/'completed_with_failures'/
    #                                                          'resumed_complete' — sie behaupten
    #                                                          KEINEN Abbruch und KEINE Inadmissibilitaet,
    #                                                          nur eine abweichende Teilkohorte/einen
    #                                                          fortgesetzten Lauf)
    #   True            False                 False          'completed_invalid' (KANONISCH — die
    #                                                          EINZIGE Zeichenkette fuer diese Zelle;
    #                                                          vor #1037 divergierten hier zwei
    #                                                          unabhaengige Code-Pfade in
    #                                                          ``sweep.py`` auf 'completed_invalid'
    #                                                          vs. 'complete_with_blocking_
    #                                                          invariants' fuer denselben Faktenstand)
    #   False           beliebig              True           'aborted_disk'/'aborted_wallclock'/
    #                                                          'aborted_signal'/'aborted_error'/
    #                                                          'aborted_invariant' (die SPEZIFISCHE
    #                                                          Abbruch-Ursache bleibt ein eigenes,
    #                                                          vom Aufrufer gesetztes Feld — die drei
    #                                                          booleschen Achsen KONSTRAIEREN
    #                                                          run_status, ersetzen aber nicht dessen
    #                                                          feinere Forensik-Aufloesung, siehe
    #                                                          ``_compute_work_aborted``)
    #   False           beliebig              False          unerreichbar in der Praxis (ein Lauf,
    #                                                          der nicht alle Symbole abschloss, OHNE
    #                                                          eine 'aborted_*'-Ursache zu tragen, hat
    #                                                          keinen definierten run_status-Wert in
    #                                                          diesem System)
    #
    # ``work_completed is None`` (Symbol-Zaehler unbekannt) faellt auf den rohen ``run_status``-
    # String zurueck (Legacy-Verhalten, siehe ``summary_de._run_status_label_de``).
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
    # Symbol-Scope beim Lesen selbst nötig). Issue #1270 (GH #1140) — PERSISTENT_CACHE_ROOT, NICHT
    # WORK (siehe dortige manifest.py-Docstring): derselbe Cache, den sweep.py seit diesem Fix auch
    # dort schreibt (Symptom vor dem Fix: cache_found=false in 3/3 Laeufen).
    _symbol_bar_quality_cache = read_symbol_bar_quality_cache(PERSISTENT_CACHE_ROOT)
    # Issue #1016/#1168 (Katalog #1170) — {cache_path, cache_found}, damit ein leeres/fehlendes
    # symbol_bar_quality NICHT stillschweigend als "None" im Report verschwindet (siehe
    # check_symbol_bar_quality_cache_availability-Docstring).
    _symbol_bar_quality_cache_status = symbol_bar_quality_cache_status(PERSISTENT_CACHE_ROOT)
    # Issue #1028 (Katalog #866) — einmal je Report-Lauf gelesen; Rohmaterial für
    # invariants.check_sizing_identity_coherence.
    _trade_amount_pct_map = _trade_amount_pct_by_strategy()
    # Issue #1256 (GH #1126) — einmal je Report-Lauf gelesen; Rohmaterial für
    # invariants.check_beta_exposure_plausibility.
    _allow_short_map = _allow_short_by_strategy()

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
        # Issue #1256 (GH #1126) — β-Plausibilitäts-Grundlage: bei ausschliesslich LONG-Positionen
        # (``allow_short=False``, der Default) ist die erwartete Marktbeteiligung (β) proportional
        # zur tatsaechlich gemessenen Exposure-Zeit und der konfigurierten Positionsgroesse —
        # ``beta_expected = holdout_exposure_fraction · trade_amount_pct/100`` (Fix-Vorgabe des
        # Issues; ``trade_amount_pct`` ist in Prozent, daher ``/100``). Nur fuer long-only-Strategien
        # gesetzt (``allow_short=True`` kann strukturell negatives/abweichendes β tragen, siehe
        # ``_allow_short_by_strategy``-Docstring); ``None`` ohne aufloesbare Exposure/Sizing-Basis
        # (kein Raten).
        record["allow_short"] = _allow_short_map.get(record.get("strategy"), False)
        _exposure_for_beta = record.get("holdout_exposure_fraction")
        _trade_pct_for_beta = record.get("trade_amount_pct")
        record["beta_expected"] = (
            float(_exposure_for_beta) * float(_trade_pct_for_beta) / 100.0
            if (not record["allow_short"] and _exposure_for_beta is not None
                and _trade_pct_for_beta is not None) else None
        )
        # Issue #1088 (Katalog #921) — nur gestempelt, wenn TATSAECHLICH ein Trial dieser Study den
        # run_id-Nachweis traegt (der Legacy-/Zeitfenster-Fallback-Pfad ohne Nachweis bleibt
        # None — fail-open, siehe ``assert_invariant_scope_uncontaminated``-Docstring).
        record["run_id"] = run_id if _own_run_trials else None
        # Issue #1090/#1238 (P1, Katalog #1247+) — bei nachgewiesener Store-Wiederverwendung
        # (``_foreign_run_trials`` nicht leer) die Vorlauf-Referenzwerte aus dem Store lesen und
        # stempeln: gibt es keine Grösse, die beantwortet, ob ein Wiederholungslauf etwas gebracht
        # hat, bleibt die Antwort nur ueber eine externe Paarbildung ueber mehrere Artefakte
        # gewinnbar (B-11-Symptom). ``prior_run_id`` ist der Fremd-run_id mit dem SPAETESTEN
        # Zeitfenster-Ende (der unmittelbare Vorlauf; die Ueberlappungspruefung oben garantiert
        # bereits, dass kein Fremdfenster mit dem eigenen ueberlappt).
        if _foreign_run_trials:
            _foreign_windows_for_prior = _trial_time_windows_by_run_id(_foreign_run_trials)
            _prior_run_id = None
            _latest_end = None
            for _rid, _win in _foreign_windows_for_prior.items():
                if _rid is None or _win is None:
                    continue
                if _latest_end is None or _win[1] > _latest_end:
                    _latest_end = _win[1]
                    _prior_run_id = _rid
            if _prior_run_id is None:
                # Fail-open auf fehlender Zeitstempel-Evidenz: deterministisch die kleinste
                # bekannte Fremd-run_id waehlen, statt gar keinen Vorlauf zu benennen.
                _candidate_ids = sorted({
                    (getattr(t, "user_attrs", None) or {}).get("run_id") for t in _foreign_run_trials
                    if (getattr(t, "user_attrs", None) or {}).get("run_id") is not None
                })
                _prior_run_id = _candidate_ids[0] if _candidate_ids else None
            record["prior_run_id"] = _prior_run_id
            _prior_run_trials = (
                [t for t in _foreign_run_trials
                 if (getattr(t, "user_attrs", None) or {}).get("run_id") == _prior_run_id]
                if _prior_run_id else _foreign_run_trials)
            _prior_feasible_rewards = [
                float(t.value) for t in _prior_run_trials
                if (getattr(t, "user_attrs", None) or {}).get("oos_eligible") is True
                and isinstance(getattr(t, "value", None), (int, float))
            ]
            try:
                _prior_direction = study.direction.name.lower() if study is not None else "maximize"
            except Exception:
                _prior_direction = "maximize"
            record["prior_best_eligible_reward"] = (
                (max(_prior_feasible_rewards) if _prior_direction == "maximize"
                 else min(_prior_feasible_rewards))
                if _prior_feasible_rewards else None
            )
            record["warm_start_reward_delta"] = (
                record["best_eligible_reward"] - record["prior_best_eligible_reward"]
                if record.get("best_eligible_reward") is not None
                and record["prior_best_eligible_reward"] is not None else None
            )
            record["prior_holdout_total_return"] = _prior_holdout_total_return(
                _prior_run_id, record.get("strategy"), record.get("symbol"),
                reports_dir=reports_dir)
            record["warm_start_holdout_delta"] = (
                record["holdout_total_return"] - record["prior_holdout_total_return"]
                if record.get("holdout_total_return") is not None
                and record["prior_holdout_total_return"] is not None else None
            )
        else:
            record["prior_run_id"] = None
            record["prior_best_eligible_reward"] = None
            record["warm_start_reward_delta"] = None
            record["prior_holdout_total_return"] = None
            record["warm_start_holdout_delta"] = None
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
    #
    # Issue #1051/#1200 (Katalog #1196-1221, P1) — Root-Cause: dieses Set wurde bisher aus dem
    # ROHEN ``proposals``-Parameter gebildet (JEDER vom Aufrufer uebergebene Eintrag, auch wenn er
    # weiter oben als Fremdlauf erkannt und mit ``continue`` von ``studies_out``/
    # ``filtered_proposals`` ausgeschlossen wurde). Bei einem warm-gestarteten Lauf enthaelt
    # ``proposals`` haeufig (strategy, symbol)-Paare AELTERER Laeufe desselben Stores — jedes
    # ``proposal_*.json`` auf der Platte mit einem PASSENDEN Paar-Namen zaehlte dadurch ueber den
    # Fast-Path unten SOFORT als ``n_own``, UNABHAENGIG davon, welcher run_id seine Trials
    # tatsaechlich trugen (der eigentliche Nachweis-Zweig weiter unten wurde nie erreicht). Das
    # erklaert die beobachtete Signatur exakt: ``n_own`` als Vielfaches von ``n_studies`` (56, 70,
    # 98, 112 bei 14 Studies je Lauf — je ein Vielfaches der Anzahl warm gestarteter Vorlaeufe).
    # Fix: NUR ``filtered_proposals`` (die Teilmenge, die den run_id-Eigentumsnachweis oben
    # TATSAECHLICH bestanden hat) zaehlt ueber den Fast-Path unten als per-Konstruktion-eigen.
    # ``_main_loop_foreign_pairs`` sind Paare, die der Aufrufer zwar uebergeben hat, die der
    # Haupt-Loop oben aber bereits POSITIV als Fremdlauf erkannt und in
    # ``studies_excluded_foreign_run`` eingetragen hat — sie zaehlen unten zu ``n_foreign``, ohne
    # ein REDUNDANTES zweites ``studies_excluded_foreign_run``-Listenelement zu erzeugen. Jedes
    # andere Proposal auf der Platte (dem Aufrufer nie uebergeben) durchlaeuft den vollen,
    # trial-scoped Nachweis unten.
    _already_seen_own_pairs = {
        (p.get("strategy"), p.get("symbol")) for p in filtered_proposals if isinstance(p, dict)
    }
    _all_caller_pairs = {
        (p.get("strategy"), p.get("symbol")) for p in proposals if isinstance(p, dict)
    }
    _main_loop_foreign_pairs = _all_caller_pairs - _already_seen_own_pairs
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
    # Issue #1051/#1200 — Akkumulator NUR fuer Paare, die DIESER Scan-Loop selbst bereits
    # klassifiziert hat (mehrere ``proposal_*.json`` fuer dasselbe Paar auf der Platte); getrennt
    # von ``_already_seen_own_pairs``/``_main_loop_foreign_pairs`` (die den Haupt-Loop oben
    # widerspiegeln), damit keine der drei Quellen die andere ueberschreibt.
    _already_seen_pairs: set[tuple] = set()
    # Issue #1067/#1217 Fix Punkt 3 — Wallclock dieses Scans, konsumiert von invariants.check_
    # search_overhead_share (zusammen mit tpe_fit_seconds aus run_optimization.py).
    import time as _time
    _store_scan_t0 = _time.perf_counter()
    for _scan_path in _store_scan_paths:
        _scan_proposal = _load_json(_scan_path) or {}
        _scan_key = (_scan_proposal.get("strategy"), _scan_proposal.get("symbol"))
        if _scan_key in _already_seen_own_pairs:
            _n_store_scan_own += 1
            continue
        if _scan_key in _main_loop_foreign_pairs:
            # Issue #1051/#1200 — der Haupt-Loop oben hat dieses Paar BEREITS positiv als
            # Fremdlauf erkannt und in ``studies_excluded_foreign_run`` eingetragen; hier NUR die
            # Zahl fortschreiben, kein redundanter zweiter Listeneintrag.
            _n_store_scan_foreign += 1
            continue
        if _scan_key in _already_seen_pairs:
            # Ein weiteres proposal_*.json fuer ein Paar, das DIESER Scan-Loop bereits (weiter
            # oben in derselben Iteration) klassifiziert hat — nicht doppelt zaehlen.
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
        # Issue #1067/#1217 — Wallclock DIESES Scans (siehe Docstring an der Zeitmessung oben).
        "store_scan_seconds": round(_time.perf_counter() - _store_scan_t0, 4),
    }
    all_checks.append(("global", _inv.check_store_scan_coherence(store_scan, len(studies_out))))
    # Issue #1067/#1217 Fix Punkt 3 — verwendet DENSELBEN store_scan_seconds-Wert wie oben.
    all_checks.append(("global", _inv.check_search_overhead_share(
        studies_out, store_scan_seconds=store_scan.get("store_scan_seconds"))))
    # Issue #1089/#1237 (P1, Katalog #1247+) — engere, diagnostische Schwelle (5%, severity low)
    # ausschliesslich auf tpe_fit_seconds, siehe dortiger Docstring fuer die Abgrenzung zu
    # check_search_overhead_share (permanente 50%-Obergrenze inkl. store_scan_seconds).
    all_checks.append(("global", _inv.check_tpe_fit_cost_share(studies_out)))
    # Issue #1090/#1238 (P1, Katalog #1247+) — misst, ob ein Wiederholungslauf (Warm-Start) den
    # Reward auf Kosten des Holdout-Ergebnisses verbessert (Ueberanpassungs-Signatur), statt die
    # Wirksamkeit unbelegt anzunehmen.
    all_checks.append(("global", _inv.check_warm_start_efficacy(studies_out)))

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
    # Issue #977/#1131/#1254 (GH #1124) — siehe family_n_frozen_stage1_from_proposals-Docstring
    # (dieselbe Aggregations-Arithmetik wie ``_n_family_by_symbol`` oben, damit beide Seiten des
    # #1091-Stabilitaets-Vergleichs, ``check_family_n_stability``, auf demselben Skalentyp stehen).
    _n_family_frozen_stage1, _n_family_frozen_by_symbol = family_n_frozen_stage1_from_proposals(
        filtered_proposals)

    registry_check = _inv.check_config_key_registry(tournament_cfg)
    all_checks.append(("global", registry_check))

    # Issue #1043/#1192 (Katalog #1192) Akzeptanzkriterium 2 — jede AKTIVE Strategie sampelt
    # beide Risiko-Layer-Parameter oder steht auf der begruendeten Allowlist (siehe
    # invariants.check_risk_layer_parameter_parity-Docstring).
    _active_strategy_names = [
        e.get("strategy_class") for e in ((_load_json(config_dir() / "strategies.json") or {})
                                          .get("strategies") or [])
        if isinstance(e, dict) and e.get("active") and e.get("strategy_class")
    ]
    all_checks.append((
        "global", _inv.check_risk_layer_parameter_parity(_active_strategy_names)))

    # Issue #1080 (Katalog #866-2) — n_family[symbol] muss exakt der Summe seiner eigenen
    # Stage1-Zerlegung entsprechen; eine Luecke beweist, dass mindestens eine Study fehlt.
    all_checks.append(("global", _inv.check_n_family_partition(_n_family_by_symbol, n_family_stage1)))

    # Issue #1091 (Katalog #924) — neue Invariante: weicht die eingefrorene von der zur
    # Berichtszeit beobachteten Zahl um mehr als 5 % ab, ist die Berichtskohorte unvollstaendig
    # (ein Zwischenreport, oder eine erneute #1086-Kontamination).
    # Issue #1052/#1201 (Katalog #1196-1221) — per-Strategie-Zerlegungen (bereits oben fuer die
    # Symbol-Summen berechnet) plus der Zwischenreport-Diskriminator: ``run_status == "in_progress"``
    # ist dieselbe Unterscheidung wie "kein sweep_completed-Ereignis liegt vor" (beide Zustaende
    # bedeuten: dieser Report wurde MITTEN im laufenden Sweep gelesen, bevor alle Studies exportiert
    # waren) — ohne einen zweiten Event-Log-Lesezugriff einzufuehren.
    all_checks.append((
        "global", _inv.check_family_n_stability(
            _n_family_frozen_by_symbol, _n_family_by_symbol,
            frozen_stage1=_n_family_frozen_stage1, observed_stage1=n_family_stage1,
            sweep_completed=(run_status != "in_progress") if run_status is not None else None)))

    # Issue #1254 (GH #1124) — vergleicht das In-Prozess-``sweep_completed``-Ereignis (VOR jeder
    # Report-Generierung emittiert, siehe sweep.py) gegen dieselbe eingefrorene Report-Zahl oben
    # (``_n_family_frozen_by_symbol``) — beide werden ueber ``family_n_frozen_stage1_from_
    # proposals`` aus derselben Aggregation berechnet, duerfen strukturell nicht divergieren.
    _sweep_completed_events = _read_jsonl_events(jsonl_sidecar_path(_log.name), "sweep_completed")
    _n_family_attempted_frozen_by_event = (
        _sweep_completed_events[-1].get("n_family_attempted_frozen")
        if _sweep_completed_events else None
    )
    all_checks.append(("global", _inv.check_family_n_event_report_agreement(
        _n_family_attempted_frozen_by_event, _n_family_frozen_by_symbol)))

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
    # Issue #1074/#1222 (Katalog #1247+) Fix Punkt 1 — ``check_cost_stress_distinctness`` ist HIER
    # NICHT mehr aufgerufen. Root-Cause des Vorzustands: der Aufruf lief an dieser Stelle VOR der
    # ``slippage_p50_bps_calibrated``-Stempelung weiter unten (damals :3934-3953) — die Invariante
    # sah ``slippage_p50_bps_calibrated`` dadurch nie (``if not slippage_p50: continue`` griff fuer
    # JEDEN Record, das Mindestdelta-Kriterium war strukturell wirkungslos). Der Aufruf steht jetzt
    # NACH dieser Stempelung (siehe unten, vor ``atr_scale_homogeneity_check``).
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
    # Issue #1060/#1209 (Katalog #1196-1221) — Abnahmemessung fuer den harten Aggregat-Exposure-
    # Deckel in hourly_strategy_base._compute_quantity: das GEMESSENE Maximum (nicht der Median)
    # darf trade_amount_pct nur um die Rundungs-/Slippage-Toleranz uebersteigen.
    all_checks.append(("global", _inv.check_sizing_cap_enforcement(studies_out)))
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
    # Issue #1261/#1131, Folge von #1260/#1130 — deklarierte (optimizer.json['time_box_bars_axis'])
    # gegen beobachtete (bars_per_calendar_day) Zeitbox-Zaehl-Achse. Direkt nach der Bar-Achsen-
    # Kohaerenzpruefung platziert (dieselbe Symbol-/Gate-Grundlage).
    all_checks.append(("global", _inv.check_timebox_unit_coherence(
        studies_out, declared_axis=str(optimizer_cfg.get("time_box_bars_axis", "calendar_24_7")),
        asset_class_by_symbol=_asset_class_by_symbol(_cost_basis_symbols))))
    _stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol,
        round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        min_stop_to_cost_ratio=_min_stop_to_cost_ratio)
    # Issue #1279 (GH #1152, Katalog #1272-1297) — NACH round_trip_cost_bps (oben gestempelt).
    _stamp_cost_drag_decomposition(studies_out)
    all_checks.append(("global", _inv.check_cost_drag_decomposition(studies_out)))
    # Issue #1055/#1204 (Katalog #1196-1221) Fix Punkt 4 — die kalibrierte p50-Slippage je Study
    # (ueber die Asset-Klasse ihres Symbols aufgeloest), Rohmaterial fuer die verschaerfte
    # invariants.check_cost_stress_distinctness-Mindestdelta-Pruefung unten. Fail-open: kein
    # Kalibrierungs-Cache (frischer Store) ⇒ jede Study traegt None (bit-identisch zum Pre-#1204-
    # Verhalten, die verschaerfte Kriterium wird dann uebersprungen statt zu erraten).
    try:
        from automation.optimizer.sweep import read_calibrated_slippage_cache
        # Issue #1270 (GH #1140) Fix Punkt 3 — PERSISTENT_CACHE_ROOT, NICHT WORK (siehe dortige
        # manifest.py-Docstring), derselbe Store, den sweep.calibrate_and_write_slippage_cache seit
        # diesem Fix auch dort schreibt.
        _slippage_calibration = (read_calibrated_slippage_cache(PERSISTENT_CACHE_ROOT) or {}).get(
            "slippage_bps_by_asset_class") or {}
    except Exception:
        _slippage_calibration = {}
    if _slippage_calibration:
        from automation.backtest_runner import _resolve_asset_class_for_symbol
        for _r in studies_out:
            try:
                _ac = _resolve_asset_class_for_symbol(_r.get("symbol"))
                _p50, _scope = _resolve_slippage_p50_calibrated(
                    _slippage_calibration.get(_ac), _r.get("symbol"), _r.get("strategy"))
                _r["slippage_p50_bps_calibrated"] = _p50
                # Issue #1276 (GH #1149, Katalog #1272-1297, P0) — die tatsaechlich getroffene
                # Aufloesungsebene (siehe _resolve_slippage_p50_calibrated-Docstring), damit
                # check_slippage_scope_agreement sie gegen selection_cost_basis-Feld
                # slippage_calibration_scope (aus holdout_metrics, oben) halten kann.
                _r["slippage_p50_calibration_scope"] = _scope
            except Exception:
                _r["slippage_p50_bps_calibrated"] = None
                _r["slippage_p50_calibration_scope"] = None
    else:
        for _r in studies_out:
            _r["slippage_p50_bps_calibrated"] = None
            _r["slippage_p50_calibration_scope"] = None
    # Issue #1074/#1222 (Katalog #1247+) Fix Punkt 1 — dieser Aufruf steht ABSICHTLICH HIER, NACH
    # der ``slippage_p50_bps_calibrated``-Stempelung direkt oberhalb (vorher lief er vor der
    # Stempelung, siehe Kommentar bei ``check_cost_stress_monotonicity`` weiter oben).
    all_checks.append(("global", _inv.check_cost_stress_distinctness(studies_out)))
    # Issue #1276 (GH #1149, Katalog #1272-1297) — direkt nach der slippage_p50_bps_calibrated-
    # Stempelung platziert (braucht slippage_p50_calibration_scope von dort).
    all_checks.append(("global", _inv.check_slippage_scope_agreement(studies_out)))
    # Issue #1277 (GH #1150, Katalog #1272-1297) — Report-seitiger Regressionswaechter gegen den
    # Selektionspfad-Fix (siehe backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta).
    all_checks.append(("global", _inv.check_selection_cost_basis_admissible(studies_out)))
    # Issue #1278 (GH #1151, Katalog #1272-1297) — schliesst den Kalibrierungs-Kreisschluss aus.
    all_checks.append(("global", _inv.check_slippage_calibration_not_circular(studies_out)))
    # Issue #1075/#1223 (Katalog #1247+, P0) — ebenfalls NACH der Kalibrierungs-Stempelung (braucht
    # slippage_p50_bps_calibrated fuer die Konsistenzpruefung gegen applied_slippage_bps).
    all_checks.append(("global", _inv.check_applied_cost_components_resolved(studies_out)))
    atr_scale_homogeneity_check = _inv.check_atr_scale_homogeneity(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol)
    all_checks.append(("global", atr_scale_homogeneity_check))
    # Issue #1028/#1177 (Katalog #866-2) — die Mikrostruktur-Untergrenze ist erst nach #1171
    # (bar_range_median_bps) messbar; siehe check_stop_distance_microstructure_floor-Docstring.
    all_checks.append(("global", _inv.check_stop_distance_microstructure_floor(studies_out)))
    # Issue #1029/#1178 (Katalog #866-2) — dieselbe Kohorte, macht die gemessene Fill-Slippage
    # materiell sichtbar statt sie stillschweigend zu ignorieren.
    all_checks.append(("global", _inv.check_stop_exit_slippage_materiality(studies_out)))
    # Issue #1078/#1226 (P1, Semantik-Bump) — prueft die interne Konsistenz von
    # selection_cost_basis (oben, aus holdout_metrics gestempelt, siehe _study_record) gegen die
    # bereits gemessene holdout_stop_exit_slippage_bps derselben Kohorte.
    all_checks.append(("global", _inv.check_selection_cost_basis_contract(studies_out)))
    # Issue #1257 (GH #1127), Pitfall #454 — prueft, OB die behauptete Kostenbasis KOHAERENT ueber
    # holdout_total_return_net/holdout_expectancy_capital_weighted_net wirkt (severity='blocking',
    # staerker als der Geschwister-Check oben: ein Vorzeichen-Widerspruch untergraebt die Selektion
    # selbst, keine reine Telemetrie-Abweichung).
    all_checks.append(("global", _inv.check_cost_basis_coherence(studies_out)))
    # Issue #1255 (GH #1125), Pitfall #454-Klasse — Homoskedastie-Diagnose der Alpha-Regression:
    # klassischer vs. HC3-robuster t(alpha), severity='high' (Modell-Diagnose, keine harte
    # Selektions-Inkohaerenz).
    all_checks.append(("global", _inv.check_alpha_tstat_estimator_agreement(studies_out)))
    # Issue #1256 (GH #1126) — β-Plausibilitäts-Diagnose gegen die bekannte Long-only-Exposure,
    # severity='high' (Modell-/Mess-Plausibilitaet, keine harte Selektions-Inkohaerenz).
    all_checks.append(("global", _inv.check_beta_exposure_plausibility(studies_out)))

    # Issue #1252 (GH #1122) — der Lauf-Fingerabdruck der EINGANGSMENGE dieses Sweeps (siehe
    # compute_run_fingerprint-Docstring). symbols/strategies werden aus den TATSAECHLICH in diesen
    # Report aufgenommenen Studies abgeleitet (studies_out — bereits um fremde/foreign_run-Studies
    # bereinigt, siehe studies_excluded_foreign_run oben), nicht aus den rohen proposals. Der Index
    # wird VOR dem Anhaengen des eigenen Eintrags gelesen (sonst wuerde dieser Lauf sich selbst als
    # Duplikat erkennen) und ERST NACH der Pruefung ergaenzt.
    _run_fingerprint_tournament_sha = (
        sha256_file(tournament_path) if tournament_path.exists() else None)
    _run_fingerprint_optimizer_sha = (
        sha256_file(optimizer_path) if optimizer_path.exists() else None)
    # Issue #1253 (GH #1123) — der je Study gestempelte seed_salt (sweep-weit konstant, siehe
    # run_optimization.seed_effective-Docstring); None, wenn kein Lauf diesen Sweep gesalzen hat
    # (der Regelfall, bit-identisch zum Pre-#1253-Verhalten).
    _seed_salt = next((r.get("seed_salt") for r in studies_out if r.get("seed_salt")), None)
    _run_fingerprint_kwargs = dict(
        git_commit_simulation=_git_commit_simulation,
        tournament_config_sha256=_run_fingerprint_tournament_sha,
        optimizer_config_sha256=_run_fingerprint_optimizer_sha,
        catalog_fingerprint_value=catalog_fingerprint(),
        seed=optimizer_cfg.get("seed"),
        symbols={r.get("symbol") for r in studies_out if r.get("symbol")},
        strategies={r.get("strategy") for r in studies_out if r.get("strategy")},
        reward_semantics_version=optimizer_cfg.get("reward_semantics_version"),
        simulation_semantics_version=optimizer_cfg.get("simulation_semantics_version"),
    )
    _run_fingerprint = compute_run_fingerprint(**_run_fingerprint_kwargs, seed_salt=_seed_salt)
    # Issue #1253 (GH #1123) Fix Punkt 3 — der Fingerabdruck OHNE Salt: identifiziert die "Familie"
    # von Läufen, die sich NUR im Salt unterscheiden (Grundlage für search_variance unten).
    _run_fingerprint_base = compute_run_fingerprint(**_run_fingerprint_kwargs, seed_salt=None)
    # Test-/Isolations-Konvention, analog ``_prior_holdout_total_return`` (siehe dortiger
    # Docstring): ein expliziter ``reports_dir``-Override (JEDER bestehende Aufrufer, der
    # Report-Artefakte isoliert — praktisch jeder Test in diesem Repo) isoliert AUTOMATISCH auch
    # den Fingerabdruck-Index, ohne dass jeder Aufrufer eine zweite, eigene Ueberschreibung kennen
    # muesste. Ohne Override (der reale Produktionspfad — ``sweep.py`` uebergibt ``reports_dir``
    # nie) bleibt der Index der ECHTE, PROJECT_ROOT-verankerte ``manifest.RUN_FINGERPRINT_INDEX_
    # PATH`` (muss die WORK-Recycling-Grenze ueberleben, siehe dortiger Docstring). Root-Cause
    # eines in dieser Session gefundenen Test-Kontaminationsbugs: OHNE diese Ableitung schrieb
    # JEDER Test, der ``generate_sweep_report`` mit ``report_source='final'`` (Default) aufrief,
    # in die ECHTE Projekt-Datei — mit Folge-FAILs in voellig unabhaengigen Tests, deren
    # Fingerabdruck zufaellig mit einem bereits akkumulierten Eintrag kollidierte.
    _run_fingerprint_index_path = (
        Path(reports_dir) / "run_fingerprints.jsonl" if reports_dir is not None
        else RUN_FINGERPRINT_INDEX_PATH
    )
    _prior_run_fingerprint_entries = read_jsonl(_run_fingerprint_index_path)
    _run_duplicate_check = _inv.check_run_is_not_duplicate(
        _run_fingerprint, run_id, _prior_run_fingerprint_entries)
    all_checks.append(("global", _run_duplicate_check))
    # Issue #1253 (GH #1123) Fix Punkt 3 — Streuung des Suchergebnisses über >= 3 Läufe DERSELBEN
    # Familie (gleicher fingerprint_base, siehe compute_run_fingerprint-Docstring). Der aktuelle
    # Lauf zaehlt mit (sein eigener study_summaries-Auszug unten), auch wenn er selbst noch nicht
    # im Index steht.
    _current_run_index_entry = {
        "fingerprint": _run_fingerprint, "fingerprint_base": _run_fingerprint_base,
        "run_id": run_id, "started_at_utc": started_at_utc, "seed_salt": _seed_salt,
        "study_summaries": [
            {"strategy": r.get("strategy"), "symbol": r.get("symbol"),
             "best_reward": r.get("best_reward"), "best_eligible_reward": r.get("best_eligible_reward"),
             "n_eligible": r.get("n_eligible")}
            for r in studies_out
        ],
    }
    _search_variance = _compute_search_variance(
        _run_fingerprint_base, _prior_run_fingerprint_entries + [_current_run_index_entry])
    if report_source == "final":
        # Issue #1252 (GH #1122) — nur der FINALE Report dieses Laufs traegt zum Index bei (ein
        # Zwischenstand/eine Probe waere sonst selbst schon ein "Duplikat seiner selbst" fuer den
        # naechsten Zwischenstand DESSELBEN Laufs).
        try:
            append_jsonl_atomic(_run_fingerprint_index_path, _current_run_index_entry)
        except OSError:
            _log.debug("[#1252] run_fingerprints.jsonl-Schreiben fehlgeschlagen (non-fatal).",
                      exc_info=True)

    # Issue #1269 (GH #1139) Fix Punkt 3 — wie weit war die Gesamt-Wallclock bereits verstrichen,
    # als die Fail-Fast-Probe (sofern sie in diesem Lauf feuerte) auswertete? Beide Operanden muessen
    # bekannt sein (ein Zwischen-/Probe-Report kennt ``wallclock_s`` typischerweise noch nicht) —
    # sonst bleibt die Telemetrie ``None`` (nicht 0.0, das behauptete faelschlich "sehr frueh").
    # Feldname bewusst OHNE "fail_fast" (Akzeptanzkriterium #1037/2, siehe dortiger Docstring:
    # "kein Feldname mit 'fail_fast' existiert mehr im Report, der ohne echten Abbruch gesetzt sein
    # koennte") — dieselbe Umbenennungskonvention wie ``blocking_invariant_triggered``.
    _blocking_invariant_probe_triggered_at_wallclock_fraction = (
        probe_triggered_at_wallclock_s / wallclock_s
        if probe_triggered_at_wallclock_s is not None and wallclock_s
        else None
    )
    all_checks.append(("global", _inv.check_fail_fast_probe_timeliness(
        _blocking_invariant_probe_triggered_at_wallclock_fraction)))

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
    effective_stop_distance_check = _inv.suppress_stop_verdict_if_bar_axis_degenerate(
        _inv.check_effective_stop_distance(
            studies_out, min_ratio=stop_distance_min_ratio, max_ratio=stop_distance_max_ratio),
        studies_out)
    all_checks.append(("global", effective_stop_distance_check))

    # Issue #1262 (GH #1132) — die Kohorten-Abdeckung (effective_stop_ratio_cohort_n / n_evaluable)
    # als eigenständiger Wächter neben dem Verdikt selbst; siehe dortiger Docstring.
    all_checks.append(("global", _inv.check_effective_stop_ratio_coverage(studies_out)))

    # Issue #1266 (GH #1136) — ein Kostenstress, der jede Study eines Symbols gleich trifft, ist
    # kein Stress (Pitfall #453 in AGENTS.md); siehe dortiger Docstring.
    all_checks.append(("global", _inv.check_cost_stress_discriminates(studies_out)))

    # Issue #1081/#1229 (P0, Katalog #1247+) Fix Punkt 3 — misst, wie weit die MODELLIERTE (im
    # Suchraum getunte) Stopdistanz von der tatsächlich AUSGEFÜHRTEN, gemessenen Distanz abweicht,
    # jetzt, da check_effective_stop_distance oben ausschliesslich die gemessene Groesse konsumiert.
    all_checks.append(("global", _inv.suppress_stop_verdict_if_bar_axis_degenerate(
        _inv.check_stop_distance_model_fidelity(studies_out), studies_out)))

    # Issue #1072 (Wiederkehr #1050/#1051) — die Stopdistanz muss ein Mindestvielfaches der
    # Round-Trip-Kosten betragen, sonst kann eine Position den Stop strukturell nicht überleben,
    # bevor die Kosten sie auffressen. ``min_stop_to_cost_ratio``/``_round_trip_cost_bps_by_symbol_
    # map`` bereits oben (Issue #951/#1117) für ``_stamp_atr_floor_bps_derived`` aufgelöst —
    # dieselben Werte, wiederverwendet statt erneut gelesen.
    all_checks.append(("global", _inv.check_stop_cost_ratio(
        studies_out, round_trip_cost_bps_by_symbol=_round_trip_cost_bps_by_symbol_map,
        min_stop_to_cost_ratio=_min_stop_to_cost_ratio)))

    # Issue #1058/#1207 (Katalog #1196-1221) — loest den Widerspruch auf, den
    # check_atr_scale_homogeneity (Floor-Bindung, §5.3) und check_stop_cost_ratio (Verhaeltnis <
    # 3.0 fuer dieselbe Study) unabhaengig voneinander melden koennen: eine floor-gebundene Study
    # MUSS das konfigurierte Kostenverhaeltnis einhalten, sonst ist der Floor nicht wirksam
    # simuliert worden. Dieselben aufgeloesten Werte wie oben wiederverwendet.
    all_checks.append(("global", _inv.check_atr_floor_enforcement(
        studies_out, atr_floor_bps_by_symbol=_atr_floor_by_symbol,
        min_stop_to_cost_ratio=_min_stop_to_cost_ratio)))

    # Issue #1093 (Katalog #926) — Kalibrierungswaechter fuer #1092/#1094: der Trailing-Stop darf
    # nicht der haeufigste, verlustreichste UND teuerste Ausgang einer Study sein.
    all_checks.append(("global", _inv.suppress_stop_verdict_if_bar_axis_degenerate(
        _inv.check_trailing_stop_loss_share(
            studies_out,
            max_loss_share=float(optimizer_cfg.get("trailing_stop_max_loss_share", 0.60)),
            # Issue #1024/#1173 — umbenannt von 'trailing_stop_max_mean_loss_ratio': Zaehler UND
            # Nenner sind seither dieselbe Statistik (Median), siehe check_trailing_stop_loss_share-
            # Docstring.
            max_median_loss_ratio=float(
                optimizer_cfg.get("trailing_stop_max_median_loss_ratio", 1.25))),
        studies_out)))

    # Issue #950/#1116 (Katalog #960) — die verbindliche SWEEP-WEITE Abnahmemessung fuer die
    # #1092/#1094-Hypothese (drei Kriterien: Spearman(k*ATR, realisierter Verlust) >= 0.3,
    # realized_stop_loss_ratio in [0.8, 3.0] fuer >= 80% der Studies, gepoolter TRAILING_STOP-
    # Anteil < 35%) — strenger als die permanente check_effective_stop_distance-Schranke und die
    # per-Study check_trailing_stop_loss_share-Symptomschwelle oben, weil sie EIN holistisches
    # Urteil ueber den gesamten Sweep faellt statt je Study einzeln zu urteilen.
    all_checks.append(("global", _inv.suppress_stop_verdict_if_bar_axis_degenerate(
        _inv.check_trailing_stop_risk_calibration_acceptance(studies_out), studies_out)))

    # Issue #953/#1119 (Katalog #960) — blockierender Regressionswaechter gegen die konkurrierende
    # Hypothese zu #950/#1092: ist der Stop-Verlust latenz- statt stopgetrieben (Verlust in
    # derselben Groessenordnung wie EINE Bar-Spanne, UND gleichzeitig ein grosses Vielfaches der
    # konfigurierten Stopdistanz), ist jede Stop-Parametrisierung wirkungslos.
    all_checks.append(("global", _inv.check_stop_loss_vs_bar_range(studies_out)))

    # Issue #1079/#1227 (Katalog #1247+, P0) — die messbare Fassung des Kalenderproblems hinter
    # bar_range_median_bps == 0: FAIL, wenn Nullspannen-Bars in mehr als 20% der Studies die
    # Mehrheit der Bar-Population dieser Position ausmachen.
    all_checks.append(("global", _inv.check_zero_range_bar_share(studies_out)))
    # Issue #1273 (GH #1146, Katalog #1272-1297, P0) — die deklarierte stop_trigger_axis gegen die
    # gemessene zero_range_bar_fraction gehalten (siehe dortiger Docstring).
    all_checks.append(("global", _inv.check_stop_trigger_axis_coherence(
        optimizer_cfg.get("stop_trigger_axis"), studies_out)))

    # Issue #1054/#1203 (Katalog #1196-1221) — die algebraisch garantierte Verlust-Zerlegung
    # realized_loss_bps == stop_distance_bps + trigger_to_fill_gap_bps muss fuer >= 99,9% der
    # TRAILING_STOP-Round-Trips halten; ein FAIL hier entwertet #1204/#1205 (Kostenkalibrierung),
    # die auf denselben Feldern aufbauen.
    all_checks.append(("global", _inv.check_stop_loss_decomposition_identity(studies_out)))

    # Issue #1082/#1230 (P1, Katalog #1247+) — die Report-Zerlegung darf nicht drei unabhaengig
    # medianisierte Groessen addieren (Median einer Summe != Summe der Mediane); die Anteile
    # (je Round-Trip gebildet, dann medianisiert) muessen sich per Konstruktion auf 1 summieren.
    all_checks.append(("global", _inv.check_stop_loss_share_decomposition(studies_out)))

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

    # Issue #1044/#1193 — macht ein Wiederauftreten des #1193-Sichtbarkeits-Regressions selbst
    # sichtbar: der Report muss den Champion-Store-Pfad benennen (siehe
    # invariants.check_champion_store_visibility-Docstring).
    all_checks.append(("global", _inv.check_champion_store_visibility(champions_summary)))

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

    # Issue #1045/#1194 — Akzeptanzkriterium 2: jede STRUCTURAL_ZERO_ELIGIBLE-Study muss einen
    # diagnosed_pairs-Eintrag hinterlassen (siehe check_structural_zero_eligible_has_diagnosis-
    # Docstring). Geprüft gegen dieselbe, gemergte Liste, die der Report unter cross_study.
    # diagnosed_pairs zeigt (_diagnosed_pairs_section, jetzt cache- UND live-derivation-gespeist).
    # Issue #1263 (GH #1133) — dieselbe Config-Schwelle, mit der _diagnosed_pairs_section unten UND
    # der neue check_atr_floor_dimension_freeze_candidates-Aufruf ausgewertet werden (eine Kennzahl,
    # eine Quelle).
    _atr_floor_freeze_threshold = float(
        optimizer_cfg.get("atr_floor_dimension_freeze_threshold", 0.60))
    structural_zero_eligible_diagnosis_check = _inv.check_structural_zero_eligible_has_diagnosis(
        studies_out, _diagnosed_pairs_section(
            studies_out, atr_floor_dimension_freeze_threshold=_atr_floor_freeze_threshold))
    all_checks.append(("global", structural_zero_eligible_diagnosis_check))

    # Issue #1263 (GH #1133) Fix Punkt 3/4 — rein diagnostisch (severity 'medium'), siehe dortiger
    # Docstring fuer den bewusst begrenzten Scope dieses Fixes (Beobachtbarkeit, keine Live-
    # Intervention).
    all_checks.append(("global", _inv.check_atr_floor_dimension_freeze_candidates(
        studies_out, freeze_threshold=_atr_floor_freeze_threshold)))

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

    # Issue #1251 (GH #1121) — dieselbe Grenzbeitrags-Aggregation, aber unter
    # gate_zero_marginal_policy='require_decision' (Default) mit einer KONSEQUENZ statt einer
    # wiederholt ignorierbaren Empfehlung: ein undokumentierter Nullbefund FAILt blockierend.
    all_checks.append(("global", _inv.check_gate_zero_marginal_policy(
        studies_out,
        policy=tournament_cfg.get("gate_zero_marginal_policy", "require_decision"),
        min_observations=int(tournament_cfg.get("gate_redundancy_min_observations", 500)),
        accepted_gates=tournament_cfg.get("gate_zero_marginal_accepted"),
        gate_consolidation_protected=tournament_cfg.get("gate_consolidation_protected"))))

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

    # Issue #1069/#1219 (P2, Katalog #1196-1221) — "Diagnose ohne Konsequenz": beide FAIL-Befunde
    # oben (Suchstagnation UND STRUCTURAL_ZERO_ELIGIBLE ohne Diagnose) werden jetzt zusätzlich in
    # den #681/#761-Rückschrieb-Cache geschrieben (siehe _writeback_search_stagnation_diagnoses-
    # Docstring) — nach 2 Läufen mit demselben Befund für dasselbe Paar 'deprioritized', nach 4
    # 'denylist'. Fail-open (die Funktion selbst fängt Fehler je Paar ab); ein Fehlschlag hier darf
    # den Report nicht verhindern.
    try:
        _writeback_search_stagnation_diagnoses(
            search_made_progress_check.actual,
            (structural_zero_eligible_diagnosis_check.actual or {}).get("missing_diagnosis_for"),
            run_id=run_id,
        )
    except Exception:
        _log.debug("Issue #1069/#1219: search_stagnation-Rueckschrieb-Batch fehlgeschlagen "
                   "(non-fatal).", exc_info=True)

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

    # Issue #1249 (GH #1119) — Schema-vs-Config-Drift-Wächter: jede check_*-Funktion, deren
    # Config-Schema-Dokumentation eine Mitgliedschaft in fail_fast_invariants BEHAUPTET, muss die
    # tatsächliche Liste auch einlösen (Symptom: gate_collinearity_policy's Schema-Text behauptete
    # dies für check_gate_collinearity_decision_required, obwohl der Check fehlte).
    fail_fast_schema_consistency_check = _inv.check_fail_fast_schema_consistency(
        {"tournament.json": tournament_cfg, "optimizer.json": optimizer_cfg},
        fail_fast_invariants=optimizer_cfg.get("fail_fast_invariants"))
    all_checks.append(("global", fail_fast_schema_consistency_check))

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
        if result.passed is False:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": label, "check": result.name,
                "expected": result.expected, "actual": result.actual, "detail": result.detail,
                # Issue #1083 — welche Auswertungswelle dieses Event traegt (siehe Docstring oben).
                "report_source": report_source,
            }, level=logging.ERROR)
        elif result.passed is None:
            emit_execution_event(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                "scope": label, "check": result.name,
                "expected": result.expected, "actual": result.actual, "detail": result.detail,
                "report_source": report_source,
            }, level=logging.WARNING)

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
        val = d.get("passed", True)
        if val is False:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": d.get("scope", "preflight"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.ERROR)
        elif val is None:
            emit_execution_event(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                "scope": d.get("scope", "preflight"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.WARNING)

    # Issue #1015/#1167 (Katalog #1170) — Ergebnisse der AUSSERHALB von ``_build_report`` laufenden
    # Checks (Sweep-Hauptschleife/``run_optimization.py``, siehe ``_read_external_invariant_
    # results``-Docstring), als ``INVARIANT_STREAM_RESULT``-Events aus demselben "optimizer"-Sidecar
    # gelesen, den ``_champions_summary``/``check_event_stream_completeness`` bereits nutzen.
    # Gleiche Behandlung wie der Preflight-Block oben (kein ``cohort``-Stempel — diese Checks
    # pruefen keine Study-Population dieses Reports, sondern Sweep-/Study-weite Bedingungen).
    for d in _read_external_invariant_results():
        invariant_checks.append(d)
        val = d.get("passed", True)
        if val is False:
            emit_execution_event(_log, "INVARIANT_CHECK_FAILED", {
                "scope": d.get("scope"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.ERROR)
        elif val is None:
            emit_execution_event(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                "scope": d.get("scope"), "check": d.get("name"),
                "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                "report_source": report_source,
            }, level=logging.WARNING)

    # Issue #942/#1108/#1037 (Katalog #960/#1186) — zwei der VIER orthogonalen Achsen VORAB
    # berechnet (haengen nur an Funktionsparametern, nicht an ``invariant_checks``), damit der
    # #1037-Regressionswaechter direkt darauf noch VOR der #1015/#1167-Abdeckungspruefung unten in
    # den Strom aufgenommen werden kann (die Abdeckungspruefung braucht den FINALEN Stand, siehe
    # dortiger Kommentar — ``_decision_admissible`` bleibt bewusst NACH ihr, damit sie auch die
    # Abdeckungspruefung selbst mitzaehlt, wie zuvor).
    _work_completed = _compute_work_completed(symbols_completed, symbols_planned)
    _work_aborted = _compute_work_aborted(run_status)
    # Issue #1037/#1186 (Katalog #1186, Akzeptanzkriterium 1/2) — permanenter Regressionswaechter
    # auf den gerade berechneten Achsen selbst (medium, rein diagnostisch — beeinflusst
    # ``_decision_admissible`` nicht, das erst weiter unten aus dem finalen ``invariant_checks``-
    # Stand abgeleitet wird).
    _axes_coherence_check = _inv.check_run_status_axes_coherence({
        "work_completed": _work_completed, "work_aborted": _work_aborted, "run_status": run_status,
    })
    _axes_coherence_dict = _axes_coherence_check.to_dict()
    _axes_coherence_dict["scope"] = "global"
    _axes_coherence_dict["source"] = "report"
    invariant_checks.append(_axes_coherence_dict)

    # Issue #1040/#1189 (Katalog #1189) — lazy importiert (dieselbe Konvention wie die einzige
    # bisherige bounds.py-Aufrufstelle in dieser Datei, ``_boundary_hit_analysis``-Nachbarschaft in
    # confirm.py): ``bounds.py`` importiert ``spaces.py``, das seinerseits ``sweep_diagnostics``
    # lazy laedt — ein Modul-Top-Level-Import wuerde diese Kette unnoetig frueh aufloesen.
    from automation.optimizer.bounds import active_bounds_overrides as _active_bounds_overrides_fn
    _active_bounds_overrides_all_list = _active_bounds_overrides_fn()
    # Issue #1097/#1245 (P3, Katalog #1247+) — Root-Cause: ``cross_study.active_bounds_overrides``
    # zeigte in ALLEN 11 Laeufen dasselbe GESAMTE kuratierte Inventar (alle 18 TSLA-Eintraege, auch
    # im GOOGL-/NVDA-/NATGAS-Lauf) — summary_de.py §5.4 filtert bereits seit #1064/#1214 auf die
    # Symbole DIESES Laufs, das JSON selbst tat es nicht. Dieselbe Filterung, dieselbe Quelle
    # (``studies_out``, wie jede andere Report-Zeile) — die volle, ungefilterte Liste bleibt unter
    # ``active_bounds_overrides_all`` erhalten (Fix-Vorgabe: "vollständige Liste ... erhalten").
    _active_bounds_overrides_run_symbols = {
        r.get("symbol") for r in studies_out if r.get("symbol")
    }
    _active_bounds_overrides_list = [
        o for o in _active_bounds_overrides_all_list
        if o.get("symbol") in _active_bounds_overrides_run_symbols
    ]

    # Issue #1039/#1188 (Katalog #1188) — einmal berechnet (statt zweimal wie zuvor implizit
    # angenommen), damit die Sektion selbst UND die Regressions-Gegenprobe garantiert dieselbe
    # Liste sehen.
    _boundary_solutions_list = _boundary_solutions_section(studies_out)
    _boundary_solutions_check = _inv.check_boundary_solutions_matches_study_records(
        _boundary_solutions_list, studies_out)
    _boundary_solutions_dict = _boundary_solutions_check.to_dict()
    _boundary_solutions_dict["scope"] = "global"
    _boundary_solutions_dict["source"] = "report"
    invariant_checks.append(_boundary_solutions_dict)

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

    # Issue #942/#1108 (Katalog #960) — die dritte orthogonale Achse: ``decision_admissible`` wird
    # ABSICHTLICH ERST HIER, NACH der obigen Abdeckungspruefung, aus dem finalen ``invariant_checks``-
    # Stand abgeleitet (dieselbe Reihenfolge wie vor #1037 — unveraendert).
    _decision_admissible = _compute_decision_admissible(invariant_checks)

    # Issue #1077/#1225 (P1) — aus den gemessenen ``applied_*``-Feldern JEDER Study abgeleitet
    # (siehe _cost_model_realism_from_applied-Docstring), NICHT mehr aus backtest.json direkt.
    (_cost_model_zero_realism, _cost_model_realism_source,
     _cost_model_zero_realism_symbols) = _cost_model_realism_from_applied(studies_out)

    _emit_cost_model_realism_event(_cost_model_realism_source, studies_out)

    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        # Issue #1252 (GH #1122) — siehe compute_run_fingerprint-Docstring. Zwei Läufe mit
        # identischer Eingangsmenge tragen denselben Wert, unabhängig von run_id/started_at_utc.
        "run_fingerprint": _run_fingerprint,
        # Issue #1252 (GH #1122) Fix Punkt 3 — run_id des Vorlaufs mit identischem run_fingerprint
        # (siehe invariants.check_run_is_not_duplicate), oder None (kein Duplikat/nicht auswertbar).
        "duplicate_of": (
            _run_duplicate_check.actual.get("duplicate_of_run_id")
            if _run_duplicate_check.actual else None
        ),
        # Issue #1104 (Katalog #937) — GETRENNTE Felder statt des vorherigen mehrdeutigen
        # ``git_commit``: ``git_commit_simulation`` (wann die TRIALS liefen) vs.
        # ``git_commit_report`` (wann DIESER Report gebaut wurde) — ein nachtraeglich regenerierter
        # Report macht die Divergenz jetzt explizit sichtbar/pruefbar
        # (``invariants.check_commit_coherence``), statt sie unter einem einzigen Feldnamen zu
        # verstecken.
        "git_commit_simulation": _git_commit_simulation,
        "git_commit_report": git_commit(),
        "reward_semantics_version": optimizer_cfg.get("reward_semantics_version"),
        # Issue #1273 (GH #1146, Katalog #1272-1297) — die deklarierte Trigger-Achse dieses Laufs
        # (siehe check_stop_trigger_axis_coherence unten UND optimizer.json-Schema-Dokumentation).
        "stop_trigger_axis": optimizer_cfg.get("stop_trigger_axis"),
        # Issue #802 — Bibliotheksversionen (pandas allen voran) in der Provenienz, damit ein Lauf
        # im Nachhinein einer Installationsumgebung zuordenbar ist.
        "library_versions": library_versions(),
        "tournament_config_sha256": sha256_file(tournament_path) if tournament_path.exists() else None,
        # Issue #1252 (GH #1122) — Config-Gegenstueck zu tournament_config_sha256 oben, Eingang des
        # run_fingerprint (siehe dortiger Docstring).
        "optimizer_config_sha256": sha256_file(optimizer_path) if optimizer_path.exists() else None,
        "catalog_fingerprint": catalog_fingerprint(),
        "started_at_utc": started_at_utc,
        "wallclock_s": wallclock_s,
        # Issue #1269 (GH #1139) Fix Punkt 3 — Anteil der Gesamt-Wallclock, der bereits verstrichen
        # war, als die Fail-Fast-Probe auswertete (``None``, wenn keine Probe feuerte oder
        # ``wallclock_s``/der Zeitpunkt unbekannt ist; siehe invariants.check_fail_fast_probe_
        # timeliness-Docstring fuer die Interpretation).
        "blocking_invariant_probe_triggered_at_wallclock_fraction":
            _blocking_invariant_probe_triggered_at_wallclock_fraction,
        "cli_args": cli_args or {},
        # Issue #833 Fix Punkt 3 — ein Report entsteht seit diesem Fix AUCH bei einem vorzeitigen
        # Sweep-Abbruch (disk_guard/wallclock_guard/SIGINT/SIGTERM/unerwartete Exception, siehe
        # sweep.main()); run_status macht den Abbruchgrund maschinenlesbar, statt nur implizit aus
        # einer unvollstaendigen studies[]-Liste erschlossen werden zu muessen. Default 'complete'
        # (bit-identisch fuer jeden Aufrufer, der die drei neuen Kwargs nicht setzt).
        "run_status": run_status,
        # Issue #942/#1108/#1037 (Katalog #960/#1186) — die VIER orthogonalen Achsen, die
        # ``run_status`` (oben, aus Rueckwaertskompatibilitaetsgruenden UNVERAENDERT erhalten) NICHT
        # eindeutig genug ausdrueckt:
        #   work_completed             — alle geplanten Symbole tatsaechlich abgeschlossen (None =
        #                                 unbekannt, weder Checkpoint noch In-Prozess-Spiegel
        #                                 verfuegbar).
        #   decision_admissible        — keine ``severity='blocking'``-Invariante FAILt in diesem
        #                                 Report.
        #   work_aborted               — ``run_status`` beschreibt einen ECHTEN Arbeitsabbruch
        #                                 (``_compute_work_aborted``, #1037). Ersetzt das Missver-
        #                                 staendnis, das der VORHERIGE Feldname ``fail_fast_
        #                                 triggered`` nahelegte ("etwas wurde abgebrochen") — DIESES
        #                                 Feld ist die tatsaechliche, unbedingt korrekte Antwort auf
        #                                 genau diese Frage.
        #   blocking_invariant_triggered — Name der blockierenden Invariante, deren LIVE In-Prozess-
        #                                 Fail-Fast-Probe waehrend des Sweeps feuerte, oder ``None``.
        #                                 Kann gesetzt sein, OHNE dass ``work_aborted`` wahr ist (die
        #                                 Probe kann NACH vollstaendiger Symbol-Abarbeitung feuern) —
        #                                 das war die #1037-Root-Cause des alten Feldnamens.
        # Root-Cause #1108: derselbe Faktenstand (14/14 Studies, volles Budget, Fail-Fast-Abbruch
        # NACH Abschluss der Arbeit) ergab ``completed_invalid`` ("vollstaendig gerechnet") in zwei
        # Reports und ``aborted_invariant`` ("echter Arbeitsabbruch") in einem dritten — dieselben
        # Fakten, zwei sich WIDERSPRECHENDE Lesarten desselben ueberladenen Strings. Root-Cause
        # #1037: sogar ZWEI Reports, die BEIDE korrekt "kein Abbruch" meinten, trugen zwei
        # verschiedene ``run_status``-Strings (``completed_invalid`` vs. ``complete_with_blocking_
        # invariants``) — siehe die Ableitungstabelle oben im Docstring dieser Funktion.
        # ``summary_de.py`` formuliert seine Kern-Aussage aus DIESEN vier Feldern, nicht mehr aus
        # ``run_status`` allein (siehe dortige Sektion 1).
        "work_completed": _work_completed,
        "decision_admissible": _decision_admissible,
        "work_aborted": _work_aborted,
        "blocking_invariant_triggered": blocking_invariant_triggered,
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
        # unterscheidbar, unabhaengig von blocking_invariant_triggered/report_source.
        "store_scan": store_scan,
        "cross_study": {
            # Issue #1253 (GH #1123) Fix Punkt 3 — siehe _compute_search_variance-Docstring. None,
            # solange < 3 Läufe derselben Eingangsmenge (fingerprint_base) im Index stehen.
            "search_variance": _search_variance,
            # Issue #998/#1150 (Katalog #1170) — macht die Kostenbasis-Aufloesung (ATR-Floor UND
            # c_rt, je Symbol) UNTERSCHEIDBAR von "der Floor bindet nirgends" (siehe
            # check_cost_basis_resolution/_atr_floor_bps_by_symbol-Docstring).
            "cost_model_resolution": _cost_model_resolution,
            # Issue #1010/#1162 (Katalog #1170), verschaerft durch #1077/#1225 — True, wenn die
            # 'full_realism'-Kostenstress-Stufe fuer JEDE Study dieses Laufs ein No-Op ist. Seit
            # #1077/#1225 aus den GEMESSENEN ``applied_*``-Feldern jeder Study abgeleitet (nicht
            # mehr aus ``backtest.json``, den konfigurierten Platzhaltern — seit #1055/#1204 stammt
            # die real angewandte Slippage aus dem Kalibrierungs-Cache). ``cost_model_realism_
            # source`` unterscheidet, WESHALB: ``config_zero`` (wie zuvor), ``calibrated_cache``
            # (keine Study betroffen) oder ``mixed`` (ein Teil der Studies, namentlich in
            # ``cost_model_zero_realism_symbols``) — Traeger fuer summary_de.py Abschnitt 2.4 (die
            # einzige erlaubte Datenquelle dort ist dieses Report-JSON, siehe dortiger Docstring).
            "cost_model_zero_realism": _cost_model_zero_realism,
            "cost_model_realism_source": _cost_model_realism_source,
            "cost_model_zero_realism_symbols": _cost_model_zero_realism_symbols,
            # Issue #1016/#1168 (Katalog #1170) — {cache_path, cache_found}: macht "Cache-Datei
            # fehlt komplett" von "Cache existiert, Feld trotzdem None" unterscheidbar (Root-Cause
            # #1168: symbol_bar_quality war in 28/28 Studies zweier Läufe still None). Traeger fuer
            # check_symbol_bar_quality_cache_availability, siehe dortiger Docstring.
            "symbol_bar_quality_cache": _symbol_bar_quality_cache_status,
            # Issue #1272 (GH #1145, Katalog #1272-1297) Akzeptanzkriterium 2 — die je Symbol
            # gecachten Bar-Qualitaets-/Tick-Dichte-Kennzahlen (siehe
            # sweep.write_symbol_bar_quality_cache/_load_symbol_bar_quality_sample), direkt
            # unter cross_study statt nur je Study eingebettet (die Kennzahl ist symbolweit
            # konstant, nicht studyspezifisch) — macht ``ticks_per_bar_median`` je Symbol OHNE
            # Umweg ueber eine einzelne Study im Report auffindbar.
            "symbol_bar_quality": _symbol_bar_quality_cache or {},
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
            # Issue #1065/#1215 — die namentliche Liste der Studies mit Trial-Defizit (siehe
            # _budget_deficit_studies-Docstring), damit summary_de.py "Σ trials < Σ budget" nicht
            # nur als aggregierte Rate, sondern mit den VERANTWORTLICHEN Studies zeigen kann.
            "budget_deficit_studies": _budget_deficit_studies(studies_out),
            # Issue #1071/#1221 — die namentliche Liste der Studies, deren oos_n_periods_median
            # unter 1/6 des Medians ihres eigenen Symbols liegt (siehe
            # _annualization_excluded_studies-Docstring) — von summary_de.py §2.3 separat
            # ausgewiesen statt in dieselbe Vergleichstabelle gemischt.
            "annualization_excluded_studies": _annualization_excluded_studies(studies_out),
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
            "diagnosed_pairs": _diagnosed_pairs_section(
                studies_out, atr_floor_dimension_freeze_threshold=_atr_floor_freeze_threshold),
            # Issue #831 Fix Punkt 4 — Randlösungen (boundary_hit_fraction > 0.3) mit ihrem
            # konkreten Bounds-Vorschlag, unabhängig davon, ob die Study eligible Trials hatte.
            # Issue #1039/#1188 — primär aus ``studies_out`` selbst abgeleitet (siehe dortiger
            # Docstring), nicht mehr ausschliesslich aus dem #761-Diagnose-Cache.
            "boundary_solutions": _boundary_solutions_list,
            # Issue #1040/#1189 (Katalog #1189) — Inventar der aktiven Suchraum-Overrides
            # (kuratiert UND automatisch vorgeschlagen), je (Strategie, Symbol, Parameter) mit
            # active/default-Bounds, Quelle und Herkunft (siehe bounds.active_bounds_overrides-
            # Docstring). Macht sichtbar, DASS/WORueBER ein Suchraum ueberhaupt geweitet wurde,
            # statt nur ex-post ueber boundary_veto_evidence (nur der GEWINNER-Trial EINER Study)
            # erschliessbar zu sein.
            # Issue #1097/#1245 (P3) — auf die Symbole DIESES Laufs gefiltert (siehe oben); die
            # ungefilterte Fassung bleibt unter ``active_bounds_overrides_all`` verfuegbar.
            "active_bounds_overrides": _active_bounds_overrides_list,
            "active_bounds_overrides_all": _active_bounds_overrides_all_list,
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
    blocking_invariant_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
    probe_triggered_at_wallclock_s: float | None = None,
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
        blocking_invariant_triggered=blocking_invariant_triggered,
        preflight_invariant_checks=preflight_invariant_checks,
        probe_triggered_at_wallclock_s=probe_triggered_at_wallclock_s,
        # Issue #1090/#1238 — dieselbe Verzeichnis-Ueberschreibung wie unten (out_dir): der
        # Vorlauf-Report (``_prior_holdout_total_return``) muss aus DEMSELBEN Verzeichnis gelesen
        # werden, in das dieser Lauf schreibt, nicht aus dem Modul-Default ``REPORTS_DIR``.
        reports_dir=reports_dir,
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
    blocking_invariant_triggered: str | None = None,
    preflight_invariant_checks: list[dict] | None = None,
    probe_triggered_at_wallclock_s: float | None = None,
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
        blocking_invariant_triggered=blocking_invariant_triggered,
        preflight_invariant_checks=preflight_invariant_checks,
        probe_triggered_at_wallclock_s=probe_triggered_at_wallclock_s,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
