"""Per-symbol sweep meta-orchestrator (Ansatz 4 / A4.6).

Enumerates tunable (strategy, symbol) pairs, filters them through Gate 1, picks a
tier (deployable | refine | all) and dispatches optimize_symbol +
confirm_per_symbol_promotion, writing one proposal JSON per pair.

HARD INVARIANTS: this module NEVER enters Phase 5 (no live deploy, no
``subprocess.Popen``) and NEVER writes ``strategies.json``. Parallelism is expressed
only through separate studies (each its own SQLite file via optimize_symbol), never
``n_jobs>1`` inside a single study.
"""
import argparse
import collections
import json
import logging
import time
from pathlib import Path

import datetime as dt

from automation.optimizer import bounds
from automation.optimizer.gate import (
    is_symbol_tunable, data_reaches_oos_window, data_reaches_holdout_window, required_span_days,
)
from automation.optimizer.trial_config import config_dir, compute_walk_forward_window
from automation.optimizer.manifest import WORK
from automation.optimizer.run_optimization import (
    optimize_symbol as _optimize_symbol,
    load_global_best,
    log_active_config,
    _sanitize,
    _preinit_study_storage,
    _dispose_storage,
    derive_n_trials,
)
from automation.optimizer.confirm import confirm_per_symbol_promotion as _confirm, export_symbol_proposal
from automation.optimizer import champions
from automation.optimizer import retention
from automation.optimizer import disk_guard
from automation.optimizer.sweep_diagnostics import (
    load_symbol_strategy_denylist, load_diagnosed_pairs_cache,
    load_continuous_bar_invalid_strategies, age_diagnosed_pairs_cache, is_diagnosed_pair_expired,
)
from automation.log_manager import (
    setup_bot_logging, emit_execution_event, emit_gate1_rejection, default_run_id,
)


def load_symbol_universe(base_cfg: Path | None = None) -> list[str]:
    """Symbol-Universum aus data/universe/momentum_ls.json (robust gegen Dicts)."""
    if base_cfg is None:
        base_cfg = config_dir()
    universe_path = base_cfg.parent.parent / "data" / "universe" / "momentum_ls.json"
    
    if universe_path.exists():
        try:
            with open(universe_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            
            raw_universe = data.get("universe", [])
            
            # Falls das Universum als Dict definiert wurde (z.B. {"TSLA.ETORO": {...}})
            # Dict-Keys sind bereits eindeutig ⇒ kein Dedup noetig.
            if isinstance(raw_universe, dict):
                return list(raw_universe.keys())

            # Falls es eine Liste ist: Entweder reine Strings übernehmen oder das 'symbol'-Feld extrahieren
            parsed_symbols = []
            for item in raw_universe:
                if isinstance(item, str):
                    parsed_symbols.append(item)
                elif isinstance(item, dict):
                    sym = item.get("symbol") or item.get("id")
                    if sym:
                        parsed_symbols.append(sym)
            # Issue #412/#415 — order-preserving Dedup: doppelte Universe-Eintraege (z. B. WDAY.ETORO
            # zweimal) wuerden sonst doppelte (strategy, symbol)-Paare erzeugen, mehrere Worker auf
            # dieselbe per-Study-SQLite-Datei kollabieren (Reproduzierbarkeit kaputt, Pitfall #68) und
            # den DDL-Race #411 ausloesen. Eindeutigkeit ist eine harte Vorbedingung (Pitfall #77).
            return list(dict.fromkeys(parsed_symbols))
        
        except (OSError, ValueError):
            return []
    return []


def load_tier_a_winners(tournament_path: Path | None = None) -> dict[str, list[str]]:
    """{strategy: [symbols, die unter globalen Params Tournament-Gewinner sind]}.

    Quelle: ``per_symbol_winners[symbol].strategy`` aus dem zuletzt geschriebenen
    tournament_result.json (EP-4). Ohne Datei ⇒ {} (None-safe)."""
    if tournament_path is None:
        candidates = []
        work_t = WORK / "tournament_result.json"
        if work_t.exists():
            candidates.append(work_t)
        logs_dir = config_dir().parent.parent / "logs"
        if logs_dir.exists():
            candidates.extend(sorted(logs_dir.glob("tournament_*.json"),
                                     key=lambda p: p.stat().st_mtime, reverse=True))
        tournament_path = candidates[0] if candidates else None

    winners: dict[str, list[str]] = {}
    if tournament_path and Path(tournament_path).exists():
        try:
            with open(tournament_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            return {}
        per_symbol = data.get("per_symbol_winners") or {}
        for symbol, info in per_symbol.items():
            strat = (info or {}).get("strategy")
            if strat:
                winners.setdefault(strat, []).append(symbol)
    return winners


def n_params_for(strategy: str) -> int:
    """Anzahl numerischer Suchraum-Parameter (Gate-1-Heuristik) via bounds."""
    return len(bounds.extract_numeric_bounds(strategy))


def strategy_has_search_space(strategy: str) -> bool:
    """Issue #595 — True, wenn ``spaces.sample_params`` einen Suchraum für ``strategy`` kennt."""
    try:
        n_params_for(strategy)
        return True
    except ValueError:
        return False


def assert_strategy_space_parity(strategies: list[str]) -> None:
    """Issue #595 — FAIL-LOUD (VOR dem ersten Trial): JEDE angeforderte (aktive) Strategie MUSS einen
    Suchraum in ``spaces.py`` haben.

    Eine in ``strategies.json`` aktive, aber in ``spaces.py`` untunbare Strategie ist ein
    Konfigurationswiderspruch: vorher warf ``spaces.sample_params`` ``ValueError: Unknown strategy``,
    das in der Fault-Isolation des Sweeps STILL verschluckt wurde ⇒ 40 % des aktiven Strategieraums
    (4 von 10) wurden nie evaluiert, 0 ERROR-Zeilen. Verletzung ⇒ ``ValueError`` mit Code
    ``STRATEGY_NO_SEARCH_SPACE`` (Entscheidung erzwingen: Suchraum ergänzen ODER active:false)."""
    missing = [s for s in strategies if not strategy_has_search_space(s)]
    if missing:
        raise ValueError(
            f"STRATEGY_NO_SEARCH_SPACE: aktive Strategie(n) ohne Suchraum in spaces.py: {missing}. "
            f"Entweder den Suchraum in spaces.sample_params ergänzen ODER die Strategie in "
            f"strategies.json auf active:false setzen (aktiv-aber-untunbar ist ein "
            f"Konfigurationswiderspruch, Issue #595)."
        )


def _assert_gate_reward_parity() -> None:
    """Issue #593 — FAIL-LOUD beim Sweep-Start: ``eligible_requires_any`` und die
    ``_any_condition_distance``-Klauseln müssen dieselbe Menge sein (Gate/Reward-Parität)."""
    from automation.optimizer.reward import assert_any_condition_parity
    try:
        cfg = json.loads((config_dir() / "tournament.json").read_text("utf-8"))
    except (OSError, ValueError):
        return
    assert_any_condition_parity(cfg)


def count_available_bars(symbols, *, catalog_path: Path | None = None) -> dict[str, int]:
    """Adapter: schätzt verfügbare 1h-Bars je Symbol aus der Parquet-Zeitspanne
    (``(max_ts - min_ts) / 1h``). Robust gegen Tick-Dichte; 0 bei fehlender Datei/Fehler.
    Im CI-Test gemockt (HI-7)."""
    if catalog_path is None:
        base = config_dir()
        raw = "data/nautilus"
        bt = base / "backtest.json"
        if bt.exists():
            try:
                with open(bt, "r", encoding="utf-8") as f:
                    raw = (json.load(f) or {}).get("catalog_path", "data/nautilus")
            except (OSError, ValueError):
                pass
        catalog_path = base.parent.parent / raw

    out: dict[str, int] = {}
    for sym in symbols:
        n = 0
        pq_file = Path(catalog_path) / "data" / "quote_tick" / sym / "data.parquet"
        if pq_file.exists():
            try:
                import pyarrow.parquet as pq
                pf = pq.ParquetFile(str(pq_file))
                if "ts_event" in pf.schema.names:
                    idx = pf.schema.names.index("ts_event")
                    oldest = newest = None
                    for rg in range(pf.metadata.num_row_groups):
                        st = pf.metadata.row_group(rg).column(idx).statistics
                        lo, hi = int(st.min), int(st.max)
                        oldest = lo if oldest is None else min(oldest, lo)
                        newest = hi if newest is None else max(newest, hi)
                    if oldest is not None and newest is not None:
                        n = max(0, int((newest - oldest) / (3600 * 1_000_000_000)))
            except Exception:
                n = 0
        out[sym] = n
    return out


def latest_ts_by_symbol(symbols, *, catalog_path: Path | None = None) -> dict[str, int | None]:
    """Issue #455 — jüngster ``ts_event`` (Epoch-ns) je Symbol aus den Parquet-Row-Group-Statistiken.

    Reines Telemetrie-Read: materialisiert KEINE Ticks, liest nur die ``max``-Statistik der
    ``ts_event``-Spalte je Row-Group (O(#row_groups)). Liefert ``None`` bei fehlender Datei/Spalte
    oder Lesefehler — dann bleibt das OOS-Erreichbarkeits-Preflight für dieses Symbol **fail-open**
    (kein Skip). Im CI-Test gemockt (HI-7), analog zu ``count_available_bars``."""
    if catalog_path is None:
        base = config_dir()
        raw = "data/nautilus"
        bt = base / "backtest.json"
        if bt.exists():
            try:
                with open(bt, "r", encoding="utf-8") as f:
                    raw = (json.load(f) or {}).get("catalog_path", "data/nautilus")
            except (OSError, ValueError):
                pass
        catalog_path = base.parent.parent / raw

    out: dict[str, int | None] = {}
    for sym in symbols:
        newest: int | None = None
        pq_file = Path(catalog_path) / "data" / "quote_tick" / sym / "data.parquet"
        if pq_file.exists():
            try:
                import pyarrow.parquet as pq
                pf = pq.ParquetFile(str(pq_file))
                if "ts_event" in pf.schema.names:
                    idx = pf.schema.names.index("ts_event")
                    for rg in range(pf.metadata.num_row_groups):
                        st = pf.metadata.row_group(rg).column(idx).statistics
                        hi = int(st.max)
                        newest = hi if newest is None else max(newest, hi)
            except Exception:
                newest = None
        out[sym] = newest
    return out


def compute_oos_window_start_ns(config: dict, *, now: dt.datetime | None = None, catalog_newest_ns: int | None = None) -> int | None:
    """Issue #491 — Berechnet start_ns für das OOS Preflight via compute_walk_forward_window.
    """
    wf = config.get("walk_forward") or {}
    needed = ("is_window_days", "oos_window_days", "splits", "holdout_days")
    if not all(k in wf for k in needed):
        return None
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    start, _end = compute_walk_forward_window(
        now=now,
        holdout_days=wf["holdout_days"],
        is_window_days=wf["is_window_days"],
        oos_window_days=wf["oos_window_days"],
        n_folds=wf["splits"],
        catalog_newest_ns=catalog_newest_ns,
    )
    return int(start.timestamp()) * 1_000_000_000


def compute_holdout_window_reach_target_ns(config: dict, *, now: dt.datetime | None = None, catalog_newest_ns: int | None = None) -> int | None:
    """
    Berechnet das zwingend zu erreichende Ziel-Datum (Epoch-ns) für das Holdout-Preflight.
    Verlangt eine Abdeckung von mindestens 50% des Holdout-Fensters oder einen fixen Buffer.
    """
    wf = config.get("walk_forward") or {}
    needed = ("is_window_days", "oos_window_days", "splits", "holdout_days")
    if not all(k in wf for k in needed):
        return None

    if now is None:
        now = dt.datetime.now(dt.timezone.utc)

    _, end = compute_walk_forward_window(
        now=now,
        holdout_days=wf["holdout_days"],
        is_window_days=wf["is_window_days"],
        oos_window_days=wf["oos_window_days"],
        n_folds=wf["splits"],
        catalog_newest_ns=catalog_newest_ns,
    )

    # Zwingende Anpassung: Der Katalog muss mindestens bis zur Mitte des Holdout-Fensters reichen.
    required_coverage_days = wf["holdout_days"] / 2.0
    reach_target = end - dt.timedelta(days=wf["holdout_days"]) + dt.timedelta(days=required_coverage_days)

    return int(reach_target.timestamp()) * 1_000_000_000


def enumerate_tunable_pairs(strategies: list[str], symbols: list[str] | None,
                            *, tier: str, available_bars: dict[str, int],
                            config: dict, latest_ts: dict[str, int | None] | None = None,
                            start_ns: int | None = None,
                            holdout_window_reach_target_ns: int | None = None,
                            logger: logging.Logger | None = None) -> list[tuple[str, str, str]]:
    """Enumeriert (strategy, symbol, 'OK')-Tripel.

    1. Symbol-Liste = ``symbols`` or ``load_symbol_universe()``.
    2. Tier: 'deployable' (nur Tier-A-Gewinner pro Strategie), 'refine' (Platzhalter, P3),
       'all' (Kreuzprodukt strategies × Symbole).
    3. Gate 1: ``is_symbol_tunable(...)`` muss True sein.
    4. Issue #455 — OOS-Erreichbarkeits-Preflight: Erreicht der jüngste Tick eines Symbols
       (``latest_ts[symbol]``) die früheste OOS-Grenze (``oos_window_start_ns``) NICHT, wird das
       Symbol mit ``OOS_WINDOW_UNREACHABLE`` + WARN-Zeile übersprungen — VOR dem Sweep, statt 100
       strukturell nutzlose Trials zu fahren (Pitfall #82). **Vollständig fail-open**: fehlen
       ``latest_ts`` oder ``oos_window_start_ns`` (Default ``None``), bleibt das Preflight aus und
       das Verhalten ist bit-identisch zum Ist-Zustand (beide Symbole behalten).
    Ausgeschlossene Paare sind NICHT enthalten.
    """
    syms = symbols if symbols else load_symbol_universe()
    winners = load_tier_a_winners() if tier == "deployable" else {}
    log = logger or logging.getLogger("optimizer")
    # Issue #669 — deklarative (Strategie, Symbol)-Deaktivierungsliste: bereits diagnostizierte,
    # strukturell nicht-viable Paare werden VOR dem Sweep übersprungen (kein Bounds-Problem, keine
    # 16 nutzlosen Trials je Paar). Leer per Default ⇒ bit-identisch.
    denylist = load_symbol_strategy_denylist()
    # Issue #681/#761 — der AUTOMATISCH gepflegte Diagnose-Cache (aus einem VORHERIGEN Lauf via
    # run_optimization.floor_plateau_callback befüllt): schliesst die Budget-Schleife, OHNE die
    # menschlich-kuratierte Denylist-Config selbst zu mutieren. Nur 'denylist'-empfohlene Paare
    # werden übersprungen — 'search_space_override'-Empfehlungen laufen weiter (Bounds-Kalibrierung
    # ist eine bewusste Kalibrierlauf-/PR-Entscheidung, kein automatischer Skip). Fehlt der Cache
    # ⇒ {} (bit-identisch). Issue #761 — VOR der Enumeration gealtert (runs_since_recorded += 1):
    # ein Paar, das seine expires_after_runs-Frist erreicht hat, wird DIESEN Lauf wieder regulär
    # enumeriert (Re-Test) statt auf ewig auto-denylisted zu bleiben.
    auto_diagnosed = age_diagnosed_pairs_cache()
    # Issue #698 — Strategien, deren Signal auf der (system-weit einzigen) kontinuierlichen
    # 24/7-Bar-Semantik strukturell ungültig ist (z. B. GapContinuation Variante A — kein echter
    # Overnight-Gap ohne Handelspausen). Deklarativ aus strategies.json, leer ⇒ bit-identisch.
    continuous_bar_invalid = load_continuous_bar_invalid_strategies()

    pairs: list[tuple[str, str, str]] = []
    for strategy in strategies:
        # Issue #698 — VOR jeder Symbol-Enumeration: eine auf dieser Bar-Semantik strukturell
        # ungültige Strategie überspringt ALLE Symbole in EINEM Schritt (kein 16/180-Trial-Budget
        # je Symbol) und fällt im sweep_completed-Event unter strategies_skipped.
        if strategy in continuous_bar_invalid:
            emit_execution_event(log, "STRATEGY_INVALID_ON_CONTINUOUS_BARS", {
                "strategy": strategy,
                "reason": "SKIPPED_INVALID_ON_CONTINUOUS_BARS",
            })
            log.warning("⏭️  %s vollständig übersprungen (auf kontinuierlichen 24/7-Bars strukturell "
                       "ungültiges Signal, Issue #698: SKIPPED_INVALID_ON_CONTINUOUS_BARS).", strategy)
            continue
        if tier == "deployable":
            allowed = set(winners.get(strategy, []))
            candidate_syms = [s for s in syms if s in allowed]
        elif tier == "refine":
            # Issue #623 — der frühere `candidate_syms = []`-Platzhalter lieferte STRUKTURELL 0 Paare,
            # ohne Fehler, ohne Warnung ('--tier refine' war ein stiller No-Op). Bis zur echten
            # Refinement-Heuristik (P3-Ausbau) bricht der Modus jetzt FAIL-LOUD ab.
            raise NotImplementedError(
                "'--tier refine' ist noch nicht implementiert (Issue #623): die Refinement-Heuristik "
                "ist ein späterer P3-Ausbau. Nutze '--tier deployable' oder '--tier all'."
            )
        else:  # 'all'
            candidate_syms = list(syms)

        # Issue #595 — n_params_for wirft ValueError für eine Strategie ohne Suchraum. Vorher
        # propagierte das ungefangen und wurde in der Fault-Isolation still verschluckt. Jetzt: als
        # STRATEGY_NO_SEARCH_SPACE (ERROR, strukturiert) emittieren und die Strategie überspringen —
        # der fail-loud-Guard assert_strategy_space_parity fängt den Widerspruch bereits vor dem Sweep;
        # dieser Catch ist die Defense-in-Depth für Direktaufrufe (z. B. Tests) ohne den Guard.
        try:
            n_params = n_params_for(strategy)
        except ValueError:
            emit_execution_event(log, "STRATEGY_NO_SEARCH_SPACE", {
                "strategy": strategy,
                "enabled_in_strategies_json": True,
                "has_space_in_spaces_py": False,
            })
            log.error("STRATEGY_NO_SEARCH_SPACE: %s ist aktiv, hat aber keinen Suchraum in spaces.py "
                      "⇒ übersprungen (Issue #595).", strategy)
            continue
        for symbol in candidate_syms:
            # Issue #669 — deklarativer Deaktivierungs-Skip (VOR jedem anderen Preflight): ein
            # bereits diagnostiziertes, strukturell nicht-viables Paar spart das volle Trial-Budget.
            deny_reason = denylist.get((strategy, symbol))
            if deny_reason is not None:
                emit_execution_event(log, "SYMBOL_STRATEGY_DENYLISTED", {
                    "strategy": strategy, "symbol": symbol, "reason": deny_reason,
                })
                log.warning("⏭️  %s/%s übersprungen (deklariert nicht-viabel, Issue #669: %s).",
                           strategy, symbol, deny_reason)
                continue

            # Issue #681 — automatisch gepflegter Diagnose-Cache: ein Paar, das ein VORHERIGER Lauf
            # als 'denylist'-würdig diagnostiziert hat (binding_cause=signal_quality ODER
            # signal_frequency/hold_duration ohne wirksame Override-Option), wird ab dem NÄCHSTEN
            # Lauf automatisch übersprungen — schliesst die Budget-Schleife, OHNE die menschlich-
            # kuratierte Denylist-Config zu mutieren (die bleibt für die PERMANENTE Governance-
            # Entscheidung reserviert).
            auto_rec = auto_diagnosed.get((strategy, symbol))
            if auto_rec is not None and auto_rec.get("action") == "denylist":
                # Issue #761 — ein Cache-Denylist-Eintrag verfällt nach expires_after_runs (Default
                # 10) und wird DANN genau EINMAL wieder zugelassen (Re-Test), statt das Paar auf
                # ewig zu zementieren, obwohl Kohorte-A/B-Fixes (#753/#756/#757) die Lage inzwischen
                # grundlegend geändert haben könnten.
                if is_diagnosed_pair_expired(auto_rec):
                    emit_execution_event(log, "SYMBOL_STRATEGY_AUTO_DIAGNOSIS_EXPIRED_RETEST", {
                        "strategy": strategy, "symbol": symbol,
                        "binding_cause": auto_rec.get("binding_cause"),
                        "runs_since_recorded": auto_rec.get("runs_since_recorded"),
                        "expires_after_runs": auto_rec.get("expires_after_runs"),
                    })
                    log.warning(
                        "🔁 %s/%s: automatische Denylist-Diagnose ist abgelaufen (Issue #761: "
                        "runs_since_recorded=%s >= expires_after_runs=%s) — wird DIESEN Lauf "
                        "wieder regulär enumeriert (Re-Test).",
                        strategy, symbol, auto_rec.get("runs_since_recorded"),
                        auto_rec.get("expires_after_runs"),
                    )
                else:
                    emit_execution_event(log, "SYMBOL_STRATEGY_AUTO_DIAGNOSED_SKIP", {
                        "strategy": strategy, "symbol": symbol,
                        "binding_cause": auto_rec.get("binding_cause"),
                        "median_oos_trades": auto_rec.get("median_oos_trades"),
                        "median_is_trades": auto_rec.get("median_is_trades"),
                    })
                    log.warning(
                        "⏭️  %s/%s übersprungen (Issue #681: automatisch aus einem vorherigen "
                        "Diagnose-Lauf als strukturell nicht-viabel erkannt, binding_cause=%s). Zur "
                        "PERMANENTEN Deaktivierung symbol_strategy_denylist.json per PR pflegen.",
                        strategy, symbol, auto_rec.get("binding_cause"),
                    )
                    continue
            ok, _reason = is_symbol_tunable(
                symbol, n_params, available_bars=available_bars.get(symbol, 0), config=config)
            if not ok:
                # Issue #531 — Diskrepanz SICHTBAR machen: unzureichende Historie darf nicht still
                # übersprungen (und der letzte OOS-Fold/Holdout still geklemmt) werden. Bei
                # INSUFFICIENT_HISTORY das strukturierte GATE_1_REJECTION-Event mit available_days
                # (reale Bar-Spanne = available_bars / bars_per_day) UND required_days (reine
                # Geometrie is+embargo+splits*oos+holdout) emittieren — genau die im Ist-Zustand
                # fehlende Diskrepanz-Visibilität (Config-450 vs. real-360).
                if _reason == "INSUFFICIENT_HISTORY":
                    emit_gate1_rejection(
                        log,
                        available_days=available_bars.get(symbol, 0) / 24.0,
                        required_days=required_span_days(config.get("walk_forward") or {}),
                        symbol=symbol,
                    )
                else:
                    # Issue #595 — ALLE drei is_symbol_tunable-Ablehnungsgründe loggen (vorher nur
                    # INSUFFICIENT_HISTORY; PARAM_DATA_RATIO_TOO_LOW und OOS_FOLD_TOO_SHORT waren still).
                    log.warning("⏭️  %s/%s übersprungen (Gate 1: %s, Issue #595).",
                                strategy, symbol, _reason)
                continue
            # Issue #455 — OOS-Erreichbarkeits-Preflight (fail-open bei fehlender Telemetrie).
            newest_ns = latest_ts.get(symbol) if latest_ts else None
            wf_dict = config.get("walk_forward")
            reachable, oos_reason, gap_days = data_reaches_oos_window(newest_ns, start_ns, wf_dict)
            if not reachable:
                log.warning(
                    "⏭️  %s/%s übersprungen (%s, Pitfall #82): jüngster Tick liegt %s Tage VOR der "
                    "frühesten OOS-Grenze ⇒ jedes OOS-Sub-Fenster bliebe leer (oos_total_trades=0, "
                    "strukturell). Katalog-H2 auffrischen (Backfill), dann erneut tunen.",
                    strategy, symbol, oos_reason, gap_days,
                )
                continue

            # Issue #462 — Holdout-Erreichbarkeits-Preflight
            holdout_reachable, holdout_reason = data_reaches_holdout_window(newest_ns, holdout_window_reach_target_ns)
            if not holdout_reachable:
                gap_days = round(((holdout_window_reach_target_ns if holdout_window_reach_target_ns is not None else 0) - (newest_ns if newest_ns is not None else 0)) / (86400 * 1_000_000_000), 1)
                log.warning(
                    "⏭️  %s/%s übersprungen (%s): jüngster Tick liegt %s Tage VOR der geforderten "
                    "Holdout-Coverage-Grenze ⇒ Deterministic Holdout-Reject. Katalog-H2 auffrischen.",
                    strategy, symbol, holdout_reason, gap_days,
                )
                continue

            pairs.append((strategy, symbol, "OK"))

    # Issue #412 — order-preserving Dedup der (strategy, symbol)-Tripel. Selbst wenn die Symbol-
    # Liste schon dedupliziert ist (load_symbol_universe), schuetzt dies gegen Duplikate aus einer
    # direkt uebergebenen ``symbols``-Liste. Doppelte Paare wuerden mehrere Worker auf denselben
    # ``study_name`` (= dieselbe SQLite-Datei) verteilen ⇒ #411-DDL-Race + Reproduzierbarkeits-
    # Verlust (Pitfall #77). Die Erst-Vorkommens-Reihenfolge bleibt erhalten (deterministische
    # Proposal-Reihenfolge, Bezug #400).
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for strat, sym, reason in pairs:
        key = (strat, sym)
        if key not in seen:
            seen.add(key)
            unique.append((strat, sym, reason))
    return unique


def _load_gate_config() -> dict:
    """Gate-1-Config aus backtest.json (walk_forward) + optimizer.json (Schwellen)."""
    base = config_dir()
    bt = json.loads((base / "backtest.json").read_text("utf-8"))
    opt = json.loads((base / "optimizer.json").read_text("utf-8"))
    return {"walk_forward": bt["walk_forward"],
            **{k: opt[k] for k in ("gate1_buffer_days", "min_bars_per_param", "min_oos_bars_per_fold")}}


def _load_optimizer_config() -> dict:
    """Issue #703 — vollständige optimizer.json (reward_semantics_version + champion_* Keys) für
    den Champion-Store-Hook. Fail-open (leeres Dict), falls die Datei fehlt/kaputt ist — der
    Champion-Store bleibt dann über ``champion_is_admissible``'s ``reward_semantics_version``-Guard
    (None ⇒ nicht versionssicher) inert, statt den Sweep zu crashen."""
    path = config_dir() / "optimizer.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _family_n_from_proposals(proposals) -> dict[str, int]:
    """Issue #625 — FAMILIENWEISE Multiple-Testing-Zahl je Symbol.

    Die Selektion läuft über ALLE Studies (Strategien) eines Symbols — z. B. 6 Studies × 100 Trials =
    600 Kandidaten je Symbol —, die per-Study-Deflation (DSR) aber nur über N=100. Die familienweise
    Fehlerrate ist entsprechend höher als das nominelle 5 %. N_eff je Study wird konservativ als Anzahl
    *eligibler* Trials angesetzt (TPE-Vorschläge sind nicht i.i.d. ⇒ N_eff < N); N_family = Σ_studies
    N_eff. Diese Zahl ist die dokumentierte konservative Obergrenze der Multiple-Testing-Last (die
    per-Study-DSR bleibt unverändert; PBO #619 ist der orthogonale Hard-Stop).

    ``proposals`` sind die von ``export_symbol_proposal`` geschriebenen Path-Objekte — daher hier je
    Proposal die JSON lesen (``deflation_n_eligible`` liegt unter ``holdout.symbol``). Ein bereits
    geparstes Dict wird defensiv ebenfalls akzeptiert (Test-Pfad). Fehlt der Schlüssel (Kohorte < 2
    eligible ⇒ keine Deflation), trägt das Proposal 0 bei.
    """
    family_n: dict[str, int] = {}
    for _p in (proposals or []):
        try:
            payload = _p if isinstance(_p, dict) else json.loads(Path(_p).read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        sym = payload.get("symbol")
        metrics_symbol = (payload.get("holdout") or {}).get("symbol") or {}
        neff = metrics_symbol.get("deflation_n_eligible")
        if sym and isinstance(neff, (int, float)) and not isinstance(neff, bool):
            family_n[sym] = family_n.get(sym, 0) + int(neff)
    return family_n


def _family_n_from_studies(pairs, studies, *, tournament_cfg: dict | None = None) -> dict[str, int]:
    """Issue #652 — familienweise Multiple-Testing-N je Symbol AUS DEN STUDY-OBJEKTEN SELBST
    (nicht aus den exportierten Proposals — die existieren an dieser Stelle noch nicht, siehe
    ``_family_n_from_proposals`` für die post-hoc-Variante der reinen Sweep-Telemetrie). Summe über
    ALLE Strategien-Studies desselben Symbols, BEVOR irgendeine Promotion-Entscheidung fällt — genau
    das schliesst die #652-Lücke: die Promotions-DSR nutzte bislang ausschliesslich das per-Study-N,
    weil die familienweite Zahl erst NACH allen Promotions bekannt war (``sweep_completed``-Event).
    ``pairs``/``studies`` müssen index-parallel sein (wie von der Phase-1-Dispatch-Schleife in
    ``run_per_symbol_sweep`` erzeugt).

    Issue #784 — zählt ``oos_evaluated is True`` (VERSUCHE), nicht mehr ``oos_eligible is True``
    (ÜBERLEBENDE). Root-Cause #784: die Deflated Sharpe Ratio korrigiert für die Multiplizität der
    SUCHE (N gezogene Kandidaten) — der Eligibility-Filter ist selbst ein Selektionsschritt und
    gehört IN die Korrektur, nicht davor. Über die zählenden eligiblen statt evaluierten Trials
    unterschätzte ``n_family`` die reale Multiplizität um den Kehrwert der Eligibility-Passrate
    (13,0 % Passrate ⇒ Faktor 7,7, #784-Katalog) UND koppelte die Deflationsschwelle invers an die
    Budgetausführung (ein per #768/#769 früh abgebrochener Study wurde dadurch MILDER deflatiert,
    Spearman(n_family, Budgetausführung)=+0,220 — die Korrektur bestrafte gründliche Suche).

    Issue #784, Umsetzungspunkt 3 — ``tournament_cfg['deflation_family_floor_mode']`` (Default
    ``'budgeted'``) hebt die je-Study-Zahl zusätzlich auf ``n_trials_budget`` an, WENN dieser Wert
    (User-Attr, von ``run_optimization`` gestempelt) grösser ist als die tatsächlich evaluierten
    Trials dieser Study: ein Abbruch reduziert die gezogenen Kandidaten, aber der Suchraum, aus dem
    selektiert wurde, war der volle geplante. ``'attempted'`` reproduziert das Verhalten OHNE
    Budget-Untergrenze (bit-identisch bis auf die #784-Umstellung von eligible→evaluated selbst)."""
    floor_mode = (tournament_cfg or {}).get("deflation_family_floor_mode", "budgeted")
    family_n: dict[str, int] = {}
    for (_strategy, symbol, _reason), study in zip(pairs, studies):
        trials = getattr(study, "trials", None) or []
        n_evaluated = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_evaluated") is True)
        if floor_mode == "budgeted":
            n_trials_budget = (getattr(study, "user_attrs", None) or {}).get("n_trials_budget")
            if isinstance(n_trials_budget, (int, float)) and not isinstance(n_trials_budget, bool):
                n_evaluated = max(n_evaluated, int(n_trials_budget))
        family_n[symbol] = family_n.get(symbol, 0) + n_evaluated
        # Issue #747 — ``study.trials`` reconnected die (in optimize_symbol bereits disposte) Engine
        # lazy; ohne erneutes Dispose HIER waeren nach dieser Schleife wieder ALLE Studies gleichzeitig
        # offen (derselbe Erschoepfungs-Mechanismus, nur an eine spaetere Stelle verschoben).
        _dispose_storage(getattr(study, "_etoro_rdb_storage", None))
    return family_n


def _family_period_returns_from_studies(pairs, studies) -> dict[str, list[list[float]]]:
    """Issue #695 — familienweite OOS-Perioden-Return-MATRIX je Symbol (nicht nur ein Zähler): je
    eligiblem Trial ALLER Strategien-Studies desselben Symbols dessen ``oos_period_returns``
    (dieselbe Quelle, die ``confirm._study_pbo`` bereits PER STUDY liest). ``confirm_per_symbol_
    promotion`` deklustert diese Matrix via ``cpcv.cluster_effective_configs`` (Pearson ρ >
    ``pbo_cluster_threshold``) VOR der SR₀-Berechnung — derselbe Mechanismus, den der PBO-Pfad
    bereits je Study anwendet, jetzt konsistent auch familienweit für die DSR-Multiplizität
    (Root-Cause #695: der PBO-Pfad declustert dieselben Configs bereits, der DSR-Pfad zählte sie
    bislang roh — zwei Multiple-Testing-Korrekturen im selben Confirm-Lauf mit inkonsistenter
    Config-Zählung).

    Nur Trials MIT nicht-leeren ``oos_period_returns`` fliessen ein (Korrelation braucht eine
    Return-Serie); ``pairs``/``studies`` müssen index-parallel sein, wie ``_family_n_from_studies``.

    Issue #784 — liest seit dem #784-Fix ``oos_evaluated is True`` (dieselbe erweiterte Menge wie
    ``_family_n_from_studies``), nicht mehr ``oos_eligible is True``: die Korrelations-Declusterung
    (``cpcv.cluster_effective_configs`` in ``confirm.py``) ist das statistisch saubere Mittel gegen
    die dadurch grössere Rohzahl, kein Vorfilter auf Gewinner-Trials VOR der Declusterung.

    BEKANNTE RESTLÜCKE (#784, nicht Teil der gemessenen Akzeptanzkriterien): ``trial.user_attrs
    ['oos_period_returns']`` wird in ``run_optimization.make_symbol_objective`` weiterhin NUR für
    ``oos_eligible``-Trials gestempelt (#663/#665, bewusste Storage-Kosten-Begrenzung) — die hier
    gesammelte Matrix sieht die erweiterte ``oos_evaluated``-Kohorte also nur über den erweiterten
    FILTER, nicht über zusätzliche REIHEN (evaluierte-aber-ineligible Trials tragen faktisch fast
    immer ein leeres ``rets`` und fallen daher aus der Matrix). Der ROHE Zähler
    (``_family_n_from_studies``, das eigentliche #784-Akzeptanzkriterium) ist davon NICHT betroffen
    — nur die per Decluster GEGLÄTTETE ``deflation_n_family_effective`` bleibt konservativ (eher zu
    klein als zu gross, also keine neue Über-Deflations-Gefahr) auf der eligiblen Teilmenge."""
    family_returns: dict[str, list[list[float]]] = {}
    for (_strategy, symbol, _reason), study in zip(pairs, studies):
        trials = getattr(study, "trials", None) or []
        for t in trials:
            if getattr(t, "user_attrs", {}).get("oos_evaluated") is not True:
                continue
            rets = t.user_attrs.get("oos_period_returns") or []
            if rets:
                family_returns.setdefault(symbol, []).append([float(x) for x in rets])
        # Issue #747 — siehe _family_n_from_studies: erneutes Dispose nach dem lazy Reconnect.
        _dispose_storage(getattr(study, "_etoro_rdb_storage", None))
    return family_returns


def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, tier: str = "deployable", n_jobs: int = 1,
                         n_jobs_source: str = "DEFAULT",
                         optimize_symbol=None, confirm=None) -> list[Path]:
    """Dispatcht für jedes enumerierte Paar optimize_symbol → confirm_per_symbol_promotion →
    export_symbol_proposal und gibt die Proposal-Pfade zurück. Betritt NIE Phase 5.

    ``optimize_symbol``/``confirm`` sind injizierbar (Default: echte Implementierungen) —
    so bleibt der Dispatch ohne echten Backtest testbar (HI-7). ``n_jobs`` steuert parallele
    *Studies* (je eigene SQLite-Datei), niemals n_jobs>1 innerhalb einer Study.

    Issue #400: ``n_jobs > 1`` verteilt die Paare jetzt tatsaechlich ueber einen
    ``ThreadPoolExecutor`` (vorher wurde der Parameter ignoriert / strikt sequenziell). Die
    Ausgabereihenfolge bleibt deterministisch (``executor.map`` bewahrt die Eingabereihenfolge);
    fuer ``n_jobs <= 1`` bleibt der Pfad bit-identisch sequenziell.
    """
    sweep_t0 = time.perf_counter()  # Issue #415 — Per-Sweep-Wall-Clock
    # Ob der ECHTE optimize_symbol (und damit echtes SQLite-Storage) genutzt wird. Bei injiziertem
    # Fake (HI-7-Tests) wird der Schema-Pre-Init uebersprungen — ein Fake-optimize_symbol beruehrt
    # keine SQLite-Datei, also gibt es nichts zu bootstrappen (kein Storage-Seiteneffekt im Test).
    using_real_optimize = optimize_symbol is None
    if optimize_symbol is None:
        optimize_symbol = _optimize_symbol
    if confirm is None:
        confirm = _confirm

    # Issue #794 — Lauf-Start-Purge: raeumt jedes trial_*/-Verzeichnis abgebrochener Vorlaeufe ab,
    # BEVOR der neue Lauf beginnt (champions/ und offene proposal_*.json bleiben unberuehrt, siehe
    # retention.collect_referenced_trial_dirs). Nur im echten Storage-Pfad; fail-open, da eine
    # fehlgeschlagene Aufraeumaktion den Lauf nicht verhindern darf (analog #703/#733).
    if using_real_optimize:
        try:
            _orphaned = retention.prune_orphaned_trial_dirs(WORK)
            if _orphaned:
                logging.getLogger("optimizer").info(
                    "[#794] Lauf-Start-Purge: %d verwaiste Trial-Verzeichnis(se) aus vorherigen "
                    "Laeufen entfernt.", len(_orphaned),
                )
        except Exception:
            logging.getLogger("optimizer").warning(
                "[#794] Lauf-Start-Purge fehlgeschlagen (non-fatal).", exc_info=True)

    # Issue #595/#593 — FAIL-LOUD-Preflight VOR dem ersten Trial (nur im echten Pfad; injizierte
    # HI-7-Fakes nutzen frei benannte Strategien und überspringen den Guard). (1) Jede aktive
    # Strategie MUSS einen Suchraum in spaces.py haben. (2) Gate- und Reward-Klauseln müssen
    # dieselbe eligible_requires_any-Menge sehen.
    if using_real_optimize:
        assert_strategy_space_parity(strategies)
        _assert_gate_reward_parity()

    syms = symbols if symbols is not None else load_symbol_universe()
    config = _load_gate_config()
    # Issue #703 — vollständige optimizer.json EINMAL vor dem Dispatch geladen (Champion-Store-
    # Gates: reward_semantics_version + champion_*-Keys), wiederverwendet über die Closure von
    # ``_run_confirm_and_export`` statt pro Paar erneut von der Platte gelesen zu werden.
    opt_data = _load_optimizer_config()
    available_bars = count_available_bars(syms)

    # Issue #531 — Pre-Sweep-Backfill-Hook: Symbole, deren REAL vorhandene Bar-Spanne die volle
    # Walk-Forward-Geometrie + gate1_buffer_days (z. B. < 435 Tage) unterschreiten, VOR dem Sweep
    # synchron nachladen (statt sie später still zu klemmen). Nur im echten Storage-Pfad — injizierte
    # Fakes (HI-7) simulieren keinen Katalog und brauchen keinen Netz-Backfill. Fail-open: ohne
    # API-Keys/Netz ist es ein No-Op, das Gate-1-Preflight entscheidet danach fail-loud.
    if using_real_optimize:
        try:
            from automation.historical_fetcher import ensure_walkforward_history
            _bf_report = ensure_walkforward_history(
                syms, config["walk_forward"],
                span_days_by_symbol={s: available_bars.get(s, 0) / 24.0 for s in syms},
                gate1_buffer_days=config.get("gate1_buffer_days", 0),
                logger=logging.getLogger("optimizer"),
            )
            if _bf_report.get("backfilled"):
                available_bars = count_available_bars(syms)  # nach Backfill neu vermessen
        except Exception as e:  # pragma: no cover - Backfill darf den Sweep nie crashen
            logging.getLogger("optimizer").warning("[#531] Pre-Sweep-Backfill übersprungen: %s", e)

    # Issue #455 — OOS-Erreichbarkeits-Preflight vorbereiten: jüngster Tick je Symbol + die geteilte
    # OOS-Grenze (#457, compute_walk_forward_window). Beide fail-open (None) ⇒ kein Skip.
    latest_ts = latest_ts_by_symbol(syms)
    global_catalog_newest_ns = max((v for v in latest_ts.values() if v is not None), default=None) if latest_ts else None
    global_catalog_oldest_ns = min((v for v in latest_ts.values() if v is not None), default=None) if latest_ts else None
    start_ns = compute_oos_window_start_ns(config, catalog_newest_ns=global_catalog_newest_ns)
    holdout_window_reach_target_ns = compute_holdout_window_reach_target_ns(config, catalog_newest_ns=global_catalog_newest_ns)

    # Issue #624 — Holdout-Geometrie vs. TATSÄCHLICHE Katalog-Spanne beim Sweep-Start LOGGEN. Die
    # unbequeme Kernaussage: auf 45 d Holdout (T≈202 MTM-Perioden) ist selbst der beste Grenzkandidat
    # (per-Periode-Sortino ≈ 0.114) NICHT signifikant für eine 95 %-Entscheidung — PSR(0)=0.9464 < 0.95,
    # T≥211 nötig. Die Promotionsschwelle DSR/PSR wird bewusst NICHT gesenkt; die Entscheidung ist in
    # manuals/strategie_optimierung.md §Holdout-Signifikanz dokumentiert. Der Preflight prüft die
    # Reachability bereits per Symbol; hier die aggregierte Diagnose (verfügbare vs. benötigte Spanne).
    _wf = config.get("walk_forward") or {}
    _req_span = required_span_days(_wf)
    _avail_span = (
        (global_catalog_newest_ns - global_catalog_oldest_ns) / 1e9 / 86400.0
        if global_catalog_newest_ns is not None and global_catalog_oldest_ns is not None
        else None
    )
    _covers = "n/a" if _avail_span is None else ("JA" if _avail_span >= _req_span else "NEIN")
    logging.getLogger("optimizer").info(
        "[#624] Holdout-Geometrie: required_span_days=%s (is=%s + embargo=%s + %s×oos=%s + holdout=%s); "
        "verfügbare Katalog-Spanne=%s d (deckt benötigte Spanne: %s). 45-d-Holdout ⇒ T≈202 Bars ⇒ "
        "PSR(0)≈0.946 < 0.95 (T≥211 nötig). Promotionsschwelle DSR/PSR wird EXPLIZIT und dokumentiert "
        "getragen (siehe manuals/strategie_optimierung.md §Holdout-Signifikanz).",
        _req_span, _wf.get("is_window_days"), _wf.get("embargo_period_days"),
        _wf.get("splits"), _wf.get("oos_window_days"), _wf.get("holdout_days"),
        None if _avail_span is None else round(_avail_span, 1), _covers,
    )

    pairs = enumerate_tunable_pairs(strategies, syms, tier=tier,
                                    available_bars=available_bars, config=config,
                                    latest_ts=latest_ts, start_ns=start_ns,
                                    holdout_window_reach_target_ns=holdout_window_reach_target_ns)

    # Issue #795 — Speicher-Preflight VOR dem ersten Trial: bricht mit einer konkreten
    # Handlungsempfehlung ab, wenn der GEPLANTE Lauf das Budget/den freien Platz übersteigen würde,
    # statt Stunden später in ENOSPC zu laufen. Nur im echten Storage-Pfad (injizierte HI-7-Fakes
    # erzeugen keine echten Trial-Verzeichnisse).
    if using_real_optimize and pairs:
        # ``pairs`` leer ⇒ nichts geplant, nichts zu budgetieren (ein bereits knapper freier Platz
        # unabhaengig von DIESEM Lauf darf einen No-Op-Sweep nicht fail-loud abbrechen).
        _expected_trials = sum(
            derive_n_trials(strategy, opt_data.get("n_trials", 100), opt_data)
            for strategy, _symbol, _reason in pairs
        )
        _expected_bytes = disk_guard.estimate_expected_bytes(
            _expected_trials, opt_data.get("bytes_per_trial_estimate", 30000))
        _budget_gb = float(opt_data.get("disk_budget_gb") or 200)
        _reserve_gb = float(opt_data.get("disk_reserve_gb") or 50)
        emit_execution_event(logging.getLogger("optimizer"), "DISK_BUDGET_PREFLIGHT", {
            "expected_trials": _expected_trials,
            "expected_bytes": _expected_bytes,
            "budget_bytes": int(_budget_gb * disk_guard.GIB),
            "free_bytes": disk_guard.free_bytes(WORK) if WORK.exists() else None,
        })
        disk_guard.assert_preflight_budget(
            WORK, expected_bytes=_expected_bytes, budget_gb=_budget_gb, reserve_gb=_reserve_gb)

    # Issue #412 — harte Eindeutigkeits-Assertion (Fail-Fast statt stiller Kollision, Pitfall #66).
    # enumerate_tunable_pairs dedupliziert bereits; diese Assertion ist der Guertel-und-Hosentraeger-
    # Schutz, falls eine kuenftige Aenderung (oder ein injizierter Paar-Set im Test) doch zwei Paare
    # auf denselben ``study_name`` abbilden wuerde — die wuerden dieselbe SQLite-Datei nebenlaeufig
    # beschreiben (#411/#412, Pitfall #76/#77).
    study_names = [f"study_{s}_{_sanitize(sym)}" for s, sym, _ in pairs]
    dupes = [n for n, c in collections.Counter(study_names).items() if c > 1]
    if dupes:
        raise ValueError(
            "Doppelte Study-Namen im Sweep (wuerden dieselbe SQLite-Datei nebenlaeufig "
            f"beschreiben, vgl. #411/#412): {dupes}"
        )

    # Issue #411 — Schema-Pre-Init: pro EINDEUTIGEM study_name die RDBStorage-Datei einmal seriell
    # (im Hauptthread, VOR dem Pool) anlegen, sodass jeder Worker garantiert den „exists"-Pfad trifft
    # (kein `create_all`-DDL-Race). Idempotent. Nur im echten Storage-Pfad — injizierte Fakes
    # (HI-7) brauchen keinen Bootstrap.
    if using_real_optimize:
        for study_name in dict.fromkeys(study_names):
            _preinit_study_storage(study_name)

    # Issue #400: jedes Paar ist eine eigene Study mit eigener SQLite-Datei (optimize_symbol
    # erzwingt intern n_jobs=1, Pitfall #68); die Paare sind daher unabhaengig und ueber n_jobs
    # Worker parallelisierbar (Ansatz 4). ThreadPoolExecutor statt ProcessPool, weil (1) der
    # eigentliche Backtest als Subprozess laeuft (run_backtest) und die GIL freigibt → echte
    # Nebenlaeufigkeit fuer diesen IO-/Subprozess-gebundenen Workload, und (2) die injizierbaren
    # optimize_symbol/confirm (HI-7) ohne Pickling nutzbar bleiben.
    #
    # Issue #652 — ZWEI Phasen statt eines einzigen optimize+confirm+export-Schritts je Paar: die
    # familienweite Multiple-Testing-Zahl (Σ eligibler Trials über ALLE Strategien-Studies desselben
    # Symbols) muss VOR jeder Promotions-Entscheidung bekannt sein, kann aber erst nach Abschluss
    # ALLER Studies eines Symbols berechnet werden. Phase 1 sammelt die Studies (weiterhin über
    # n_jobs parallelisiert); Phase 2 (Confirm + Export) läuft danach mit der bereits bekannten
    # ``deflation_n_family`` je Symbol.
    def _run_optimize(pair: tuple[str, str, str]):
        strategy, symbol, _reason = pair
        # Issue #795 — ein zuvor gesetztes disk_guard.sweep_abort_requested (DISK_BUDGET_EXCEEDED
        # in einer parallel laufenden Study) laesst NEUE Paare nicht mehr starten; bereits laufende
        # Studies werden durch ihren eigenen disk_budget_callback gestoppt. Geordnetes Sweep-Ende
        # statt eines harten ENOSPC-Absturzes (siehe disk_guard-Moduldoc).
        if disk_guard.sweep_abort_requested.is_set():
            logging.getLogger("optimizer").warning(
                "[#795] Sweep-Abbruch angefordert (Speicherbudget überschritten) — %s/%s wird "
                "übersprungen.", strategy, symbol,
            )
            return None
        newest_ns = latest_ts.get(symbol) if latest_ts else None
        # Issue #531 — die REAL vorhandene Bar-Spanne (Tage) an build_trial durchreichen, damit die
        # Manifest-Konstruktion gegen die tatsächliche Datenlage (nicht nur data_history_days) prüft.
        span_days = available_bars.get(symbol, 0) / 24.0
        return optimize_symbol(strategy, symbol, catalog_newest_ns=newest_ns,
                               catalog_span_days=span_days)

    if n_jobs and n_jobs > 1 and len(pairs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(n_jobs, len(pairs))) as executor:
            studies = list(executor.map(_run_optimize, pairs))
    else:
        studies = [_run_optimize(p) for p in pairs]

    # Issue #652 — familienweite Multiplizität je Symbol, AUS DEN STUDIES (Phase 1 bereits
    # abgeschlossen), BEVOR irgendeine Promotion (Phase 2) läuft.
    # Issue #784 — deflation_family_floor_mode steuert die Budget-Untergrenze innerhalb von
    # _family_n_from_studies; fail-open (leere Config) auf den bit-identischen 'budgeted'-Default.
    try:
        _tournament_cfg_for_family = json.loads((config_dir() / "tournament.json").read_text("utf-8"))
    except (OSError, ValueError):
        _tournament_cfg_for_family = {}
    n_family_pre_promotion = _family_n_from_studies(
        pairs, studies, tournament_cfg=_tournament_cfg_for_family)
    # Issue #695 — dieselbe Phase-1-Grundlage, aber als familienweite Return-MATRIX statt nur eines
    # Zählers, damit confirm.confirm_per_symbol_promotion die rohe Familien-N vor der SR₀-Berechnung
    # korrelations-declustern kann (siehe _family_period_returns_from_studies-Docstring).
    family_returns_pre_promotion = _family_period_returns_from_studies(pairs, studies)

    def _run_confirm_and_export(pair: tuple[str, str, str], study) -> Path | None:
        strategy, symbol, _reason = pair
        if study is None:
            # Issue #795 — kein Study-Objekt (Paar wurde wegen disk_guard.sweep_abort_requested
            # uebersprungen, siehe _run_optimize): kein Confirm/Export moeglich, kein Proposal.
            logging.getLogger("optimizer").warning(
                "[#795] %s/%s übersprungen (kein Study-Objekt aus Phase 1) — kein Proposal "
                "exportiert.", strategy, symbol,
            )
            return None
        try:
            newest_ns = latest_ts.get(symbol) if latest_ts else None
            global_params = load_global_best(strategy, config_dir())
            promotion = confirm(study, strategy, symbol, global_params, catalog_newest_ns=newest_ns,
                                deflation_n_family=n_family_pre_promotion.get(symbol, 0),
                                deflation_family_period_returns=family_returns_pre_promotion.get(symbol))
            proposal_path = export_symbol_proposal(study, strategy, symbol, promotion)
            # Issue #703 — Champion-Store: persistiert den Ebene-1-Suchanker für den NÄCHSTEN
            # Sweep-Lauf, unmittelbar NACH dem Proposal-Export. Rein additiv (ändert weder die
            # aktuelle Promotion-Entscheidung noch strategies.json, HI-3); nur im echten Storage-Pfad
            # (injizierte HI-7-Fakes simulieren keinen Katalog/keine reale champions.WORK-Isolation).
            # Fail-open: ein Champion-Store-Fehler darf den Sweep nie crashen (analog #531-Backfill).
            if using_real_optimize:
                try:
                    champions.store_champion(study, strategy, symbol, promotion,
                                             catalog_newest_ns=newest_ns, opt_data=opt_data, tier=tier)
                except Exception:
                    logging.getLogger("optimizer").warning(
                        "[#703] %s/%s: Champion-Store-Schreiben fehlgeschlagen (non-fatal).",
                        strategy, symbol, exc_info=True,
                    )
                # Issue #733 — Normalfall-Retention: die Study ist jetzt abgeschlossen (Confirm +
                # Export + Champion-Store gelaufen). Ihr IS-Trial-Baum (bis zu n_trials Verzeichnisse,
                # der grösste Einzeltreiber des data/optimizer-Wachstums) wird ab hier nicht mehr
                # gebraucht — ausser ein aktuell referenzierter trial_dir läge (defensiv) darin.
                # Fail-open: ein Retention-Fehler darf den Sweep nie crashen (analog Champion-Store).
                try:
                    study_name = f"study_{strategy}_{_sanitize(symbol)}"
                    retention.prune_completed_trial_dirs(
                        study_name, retention.collect_referenced_trial_dirs())
                except Exception:
                    logging.getLogger("optimizer").warning(
                        "[#733] Trial-Verzeichnis-Retention für %s/%s fehlgeschlagen (non-fatal).",
                        strategy, symbol, exc_info=True,
                    )
            return proposal_path
        finally:
            # Issue #747 — Confirm/Export/Champion-Store lesen study.trials erneut (lazy Reconnect
            # nach dem Dispose in optimize_symbol); die Engine hier ein zweites (letztes) Mal
            # disposen, BEVOR die Study-Referenz mit dem Ende von Phase 2 aus dem Scope faellt.
            _dispose_storage(getattr(study, "_etoro_rdb_storage", None))

    if n_jobs and n_jobs > 1 and len(pairs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(n_jobs, len(pairs))) as executor:
            proposals = list(executor.map(
                lambda ps: _run_confirm_and_export(ps[0], ps[1]), zip(pairs, studies)))
    else:
        proposals = [_run_confirm_and_export(p, s) for p, s in zip(pairs, studies)]
    # Issue #795 — übersprungene Paare (kein Study-Objekt aus Phase 1, siehe _run_optimize/
    # _run_confirm_and_export) tragen kein Proposal; die Rückgabe bleibt eine reine Path-Liste.
    proposals = [p for p in proposals if p is not None]

    # Issue #415 — Per-Sweep-Summary (Wall-Clock + Umfang) als strukturiertes Event in die Datei
    # UND eine menschenlesbare Schlusszeile auf die Konsole (Operator sieht die Gesamtlaufzeit ohne
    # Log-Parsing). Zeitdauer-Pflicht §18: jeder Lauf-Pfad weist seine Wall-Clock aus.
    n_strats = len({s for s, _, _ in pairs})
    n_syms = len({sym for _, sym, _ in pairs})
    wallclock_s = round(time.perf_counter() - sweep_t0)

    # Issue #595 — Strategie-Abdeckungs-Telemetrie: welche angeforderten Strategien wurden
    # tatsächlich enumeriert und welche (mit Grund) übersprungen. Eine Differenz > 0 erzeugt eine
    # WARNING-Zusammenfassung am Sweep-Ende (vorher: 6 von 10 lautlos, 0 Warnungen).
    _log = logging.getLogger("optimizer")
    enumerated = {s for s, _, _ in pairs}
    # Issue #698 — dieselbe deklarative Menge wie in enumerate_tunable_pairs (dort lokal, hier für
    # die Skip-Grund-Auflösung erneut gelesen — billige JSON-Datei, kein Caching nötig).
    continuous_bar_invalid = load_continuous_bar_invalid_strategies()
    strategies_skipped = []
    for s in strategies:
        if s not in enumerated:
            # Issue #698 — VOR der NO_SEARCH_SPACE/NO_ELIGIBLE_SYMBOLS-Fallunterscheidung: eine
            # Strategie, die auf der kontinuierlichen 24/7-Bar-Semantik strukturell ungültig ist
            # (siehe enumerate_tunable_pairs), HAT einen Suchraum UND wäre für Symbole eligibel —
            # der generische Fallback würde sie sonst fälschlich als NO_ELIGIBLE_SYMBOLS ausweisen.
            if s in continuous_bar_invalid:
                reason = "SKIPPED_INVALID_ON_CONTINUOUS_BARS"
            else:
                reason = "NO_SEARCH_SPACE" if not strategy_has_search_space(s) else "NO_ELIGIBLE_SYMBOLS"
            strategies_skipped.append({"strategy": s, "reason": reason})

    # Issue #625 — familienweise Multiple-Testing-Zahl je Symbol (Σ eligibler Trials über die
    # Strategien-Studies desselben Symbols). Siehe _family_n_from_proposals.
    family_n = _family_n_from_proposals(proposals)

    emit_execution_event(_log, "sweep_completed", {
        "pairs": len(pairs),
        "strategies": n_strats,
        "symbols": n_syms,
        "n_jobs": n_jobs,
        "n_jobs_source": n_jobs_source,
        "wallclock_s": wallclock_s,
        # Issue #595 — Strategie-Parität sichtbar machen.
        "strategies_requested": len(strategies),
        "strategies_enumerated": len(enumerated),
        "strategies_skipped": strategies_skipped,
        # Issue #625 — familienweise N_eff je Symbol (Σ eligibler Trials über die Strategien-Studies).
        "deflation_n_family": family_n,
    })
    if strategies_skipped:
        _log.warning("[#595] %d von %d angeforderten Strategien NICHT enumeriert: %s",
                     len(strategies_skipped), len(strategies),
                     ", ".join(f"{d['strategy']}({d['reason']})" for d in strategies_skipped))
    mins, secs = divmod(int(wallclock_s), 60)
    print(f"✅ Sweep fertig: {len(pairs)} Paare, {n_strats} Strategien × {n_syms} Symbole, "
          f"n_jobs={n_jobs} ({n_jobs_source}), Gesamtlaufzeit {mins}m{secs:02d}s.")
    return proposals


def _resolve_strategies(arg: str) -> list[str]:
    """'all' ⇒ alle aktiven strategy_class aus strategies.json, sonst Komma-Liste."""
    if arg != "all":
        return [s.strip() for s in arg.split(",") if s.strip()]
    strats_path = config_dir() / "strategies.json"
    out: list[str] = []
    if strats_path.exists():
        data = json.loads(strats_path.read_text("utf-8")) or {}
        for s in data.get("strategies", []):
            if s.get("active", True) is not False and s.get("strategy_class"):
                out.append(s["strategy_class"])
    return out


def main(argv: list[str] | None = None) -> list[Path]:
    # Issue #740 — EIN run_id für den gesamten Lauf: treibt sowohl den nicht-rotierenden Pro-Lauf-
    # Logger (unten) als auch den Dateinamen des #742-Sweep-Reports (am Ende dieser Funktion) —
    # dieselbe Provenienz-Kennung verbindet Log-Datei, JSONL-Sidecar (#741) und Report.
    run_id = default_run_id()
    main_t0 = time.perf_counter()
    started_at_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    # Issue #414 — Logging EINMALIG initialisieren, BEVOR irgendetwas geloggt wird. Sonst hat
    # getLogger("optimizer") im Standalone-Pfad keinen Handler und Pythons lastResort verwirft alle
    # INFO-`[JSON_EVENT]` (#404-Telemetrie) — nur WARNING+ erreicht stderr. setup_bot_logging haengt
    # einen File- (DEBUG, #740 pro-Lauf NICHT-rotierend) UND einen Stream-Handler (INFO) an und
    # setzt propagate=False (kollidiert NICHT mit Optunas eigenem Logger; KEIN set_verbosity,
    # Pitfall #74).
    setup_bot_logging("optimizer", run_id=run_id)

    parser = argparse.ArgumentParser(description="Per-symbol micro-tuning sweep (Ansatz 4). Never enters Phase 5.")
    parser.add_argument("--strategies", default="all", help="'all' (aktive aus strategies.json) oder Komma-Liste")
    parser.add_argument("--symbols", default="all", help="'all' (Universum) oder Komma-Liste")
    parser.add_argument("--tier", default="deployable", choices=["deployable", "refine", "all"])
    parser.add_argument("--n-jobs", type=int, default=None,
                        help="Parallele Studies (je eigene SQLite-Datei). Fehlt das Flag, greift "
                             "optimizer.json['sweep_max_workers'] (Default max(1, cpu_count()-2)).")
    args = parser.parse_args(argv)

    strategies = _resolve_strategies(args.strategies)
    symbols = None if args.symbols == "all" else [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Issue #511/#755: Concurrency Management. VORHER erzwang ein gesetzter Seed sweep-weit
    # n_jobs=1 (Determinismus auf der falschen Ebene — siehe run_optimization.seed_effective-
    # Docstring: jede Study hat eine EIGENE SQLite-Datei/eigenen Sampler, Determinismus braucht nur
    # einen PER-STUDY deterministischen Seed, nicht serielle Ausfuehrung). Der Sweep-weite Seed wird
    # unveraendert aus optimizer.json gelesen und reicht bis in ``optimize_symbol`` durch, wo er via
    # ``seed_effective`` je Study gehasht wird — hier ist NUR noch die Worker-Anzahl zu bestimmen.
    is_cli_n_jobs = args.n_jobs is not None

    opt_cfg_path = config_dir() / "optimizer.json"
    seed = None
    sweep_max_workers_cfg = None
    try:
        opt_cfg = json.loads(opt_cfg_path.read_text("utf-8")) if opt_cfg_path.exists() else {}
        seed = opt_cfg.get("seed")
        sweep_max_workers_cfg = opt_cfg.get("sweep_max_workers")
    except Exception:
        pass

    if is_cli_n_jobs:
        eff_n_jobs = args.n_jobs
        n_jobs_source = "CLI"
    elif sweep_max_workers_cfg:
        eff_n_jobs = int(sweep_max_workers_cfg)
        n_jobs_source = "CONFIG_SWEEP_MAX_WORKERS"
    else:
        # Issue #755 — Default max(1, cpu_count()-2), konsistent mit der #726-Empfehlung
        # (ISSUES_concurrent_execution_20260719.md). ``sweep_max_workers`` explizit 0/negativ/nicht
        # gesetzt ⇒ derselbe Fallback (Zero-Hardcoding-Konvention dieses Moduls).
        import os
        eff_n_jobs = max(1, (os.cpu_count() or 3) - 2)
        n_jobs_source = "DEFAULT_CPU_MINUS_2"

    # Issue #403: Config-Quellen + Kern-Schwellen einmalig offenlegen, bevor der Sweep in die
    # (subprocess-stummen) iterativen Trials uebergeht.
    log_active_config(f"per-symbol sweep · tier={args.tier}",
                      extra={"Sweep-Level n_jobs": eff_n_jobs,
                             "Study-Level n_jobs": 1,
                             "n_jobs_source": n_jobs_source,
                             "seed": seed,
                             "strategien": len(strategies),
                             "symbole": "all" if symbols is None else len(symbols)})

    proposals = run_per_symbol_sweep(strategies, symbols, tier=args.tier, n_jobs=eff_n_jobs, n_jobs_source=n_jobs_source)
    for p in proposals:
        print(p)

    # Issue #742 — EIN aggregiertes Report-Artefakt am Ende des Laufs, atomar geschrieben. Darf den
    # Sweep NIE crashen (non-fatal, analog Champion-Store/Retention/Backfill an anderer Stelle).
    try:
        from automation.optimizer import report as _report
        report_path = _report.generate_sweep_report(
            proposals, run_id=run_id, started_at_utc=started_at_utc,
            wallclock_s=round(time.perf_counter() - main_t0),
            cli_args={"strategies": args.strategies, "tier": args.tier, "symbols": args.symbols,
                     # Issue #755 — n_workers je Lauf im Report nachvollziehbar (Determinismus-Nachweis
                     # bei n_jobs>1, jetzt auch bei gesetztem Seed zulaessig).
                     "n_jobs": eff_n_jobs, "n_jobs_source": n_jobs_source},
        )
        print(f"📄 Report: {report_path}")
        # Issue #773 — der Report war bislang rein informativ; der CLI-Entrypoint (__main__ unten)
        # liest diesen Pfad, um bei mindestens einem FAIL-Invarianten-Check einen Non-Zero-Exit-Code
        # zurueckzugeben, statt eines rein informativen Artefakts.
        global _LAST_REPORT_PATH
        _LAST_REPORT_PATH = report_path
    except Exception:
        logging.getLogger("optimizer").warning(
            "[#742] Sweep-Report-Generierung fehlgeschlagen (non-fatal).", exc_info=True)

    return proposals


# Issue #773 — siehe Kommentar in main() oben.
_LAST_REPORT_PATH: "Path | None" = None


def _report_has_failing_invariant(report_path) -> bool:
    """Issue #773 — liest das generierte #742-Report-Artefakt und meldet, ob mindestens ein
    Invarianten-Check FAILED ist. Fail-open (``False``) bei jedem Lese-/Parse-Fehler — ein
    defektes Report-Artefakt soll den Exit-Code nicht zusaetzlich verschlechtern."""
    try:
        data = json.loads(Path(report_path).read_text("utf-8"))
        return any(not c.get("passed", True) for c in data.get("invariant_checks", []))
    except Exception:
        return False


if __name__ == "__main__":
    main()
    if _LAST_REPORT_PATH is not None and _report_has_failing_invariant(_LAST_REPORT_PATH):
        import sys as _sys
        logging.getLogger("optimizer").error(
            "[#773] Mindestens ein Invarianten-Check ist FAILED (%s) — Exit-Code 1.",
            _LAST_REPORT_PATH,
        )
        _sys.exit(1)
