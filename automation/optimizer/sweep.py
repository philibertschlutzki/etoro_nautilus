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
from automation.optimizer.gate import is_symbol_tunable, data_reaches_oos_window
from automation.optimizer.trial_config import config_dir, compute_walk_forward_window
from automation.optimizer.manifest import WORK
from automation.optimizer.run_optimization import (
    optimize_symbol as _optimize_symbol,
    load_global_best,
    log_active_config,
    _sanitize,
    _preinit_study_storage,
)
from automation.optimizer.confirm import confirm_per_symbol_promotion as _confirm, export_symbol_proposal
from automation.log_manager import setup_bot_logging, emit_execution_event


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


def compute_oos_window_start_ns(config: dict, *, now: dt.datetime | None = None) -> int | None:
    """Issue #455/#457 — die früheste OOS-Sub-Fenster-Grenze (Epoch-ns) für das Sweep-Preflight.

    Bezieht ``start`` aus der GETEILTEN ``compute_walk_forward_window`` (Single Source of Truth,
    #457) und addiert ``is_window_days`` — exakt die Grenze, gegen die der jüngste Tick je Symbol
    geprüft wird. ``None``, wenn die Walk-Forward-Geometrie unvollständig ist (⇒ Preflight fail-open).
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
    )
    oos_boundary = start + dt.timedelta(days=wf["is_window_days"])
    # Mitternacht-UTC ⇒ ganzzahlige Sekunden ⇒ exakte ns ohne Float-Drift.
    return int(oos_boundary.timestamp()) * 1_000_000_000


def enumerate_tunable_pairs(strategies: list[str], symbols: list[str] | None,
                            *, tier: str, available_bars: dict[str, int],
                            config: dict, latest_ts: dict[str, int | None] | None = None,
                            oos_window_start_ns: int | None = None,
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

    pairs: list[tuple[str, str, str]] = []
    for strategy in strategies:
        if tier == "deployable":
            allowed = set(winners.get(strategy, []))
            candidate_syms = [s for s in syms if s in allowed]
        elif tier == "refine":
            candidate_syms = []   # Platzhalter — echte Refinement-Heuristik ist späterer P3-Ausbau
        else:  # 'all'
            candidate_syms = list(syms)

        n_params = n_params_for(strategy)
        for symbol in candidate_syms:
            ok, _reason = is_symbol_tunable(
                symbol, n_params, available_bars=available_bars.get(symbol, 0), config=config)
            if not ok:
                continue
            # Issue #455 — OOS-Erreichbarkeits-Preflight (fail-open bei fehlender Telemetrie).
            newest_ns = latest_ts.get(symbol) if latest_ts else None
            reachable, oos_reason = data_reaches_oos_window(newest_ns, oos_window_start_ns)
            if not reachable:
                gap_days = round((oos_window_start_ns - newest_ns) / (86400 * 1_000_000_000), 1)
                log.warning(
                    "⏭️  %s/%s übersprungen (%s, Pitfall #82): jüngster Tick liegt %s Tage VOR der "
                    "frühesten OOS-Grenze ⇒ jedes OOS-Sub-Fenster bliebe leer (oos_total_trades=0, "
                    "strukturell). Katalog-H2 auffrischen (Backfill), dann erneut tunen.",
                    strategy, symbol, oos_reason, gap_days,
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


def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, tier: str = "deployable", n_jobs: int = 1,
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

    syms = symbols if symbols is not None else load_symbol_universe()
    config = _load_gate_config()
    available_bars = count_available_bars(syms)

    # Issue #455 — OOS-Erreichbarkeits-Preflight vorbereiten: jüngster Tick je Symbol + die geteilte
    # OOS-Grenze (#457, compute_walk_forward_window). Beide fail-open (None) ⇒ kein Skip.
    latest_ts = latest_ts_by_symbol(syms)
    oos_window_start_ns = compute_oos_window_start_ns(config)

    pairs = enumerate_tunable_pairs(strategies, syms, tier=tier,
                                    available_bars=available_bars, config=config,
                                    latest_ts=latest_ts, oos_window_start_ns=oos_window_start_ns)

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
    def _run_pair(pair: tuple[str, str, str]) -> Path:
        strategy, symbol, _reason = pair
        newest_ns = latest_ts.get(symbol) if latest_ts else None
        study = optimize_symbol(strategy, symbol, catalog_newest_ns=newest_ns)
        global_params = load_global_best(strategy, config_dir())
        promotion = confirm(study, strategy, symbol, global_params, catalog_newest_ns=newest_ns)
        return export_symbol_proposal(study, strategy, symbol, promotion)

    if n_jobs and n_jobs > 1 and len(pairs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(n_jobs, len(pairs))) as executor:
            proposals = list(executor.map(_run_pair, pairs))
    else:
        proposals = [_run_pair(p) for p in pairs]

    # Issue #415 — Per-Sweep-Summary (Wall-Clock + Umfang) als strukturiertes Event in die Datei
    # UND eine menschenlesbare Schlusszeile auf die Konsole (Operator sieht die Gesamtlaufzeit ohne
    # Log-Parsing). Zeitdauer-Pflicht §18: jeder Lauf-Pfad weist seine Wall-Clock aus.
    n_strats = len({s for s, _, _ in pairs})
    n_syms = len({sym for _, sym, _ in pairs})
    wallclock_s = round(time.perf_counter() - sweep_t0)
    emit_execution_event(logging.getLogger("optimizer"), "sweep_completed", {
        "pairs": len(pairs),
        "strategies": n_strats,
        "symbols": n_syms,
        "n_jobs": n_jobs,
        "wallclock_s": wallclock_s,
    })
    mins, secs = divmod(int(wallclock_s), 60)
    print(f"✅ Sweep fertig: {len(pairs)} Paare, {n_strats} Strategien × {n_syms} Symbole, "
          f"n_jobs={n_jobs}, Gesamtlaufzeit {mins}m{secs:02d}s.")
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
    # Issue #414 — Logging EINMALIG initialisieren, BEVOR irgendetwas geloggt wird. Sonst hat
    # getLogger("optimizer") im Standalone-Pfad keinen Handler und Pythons lastResort verwirft alle
    # INFO-`[JSON_EVENT]` (#404-Telemetrie) — nur WARNING+ erreicht stderr. setup_bot_logging haengt
    # einen File- (DEBUG → rotierende JSONL) UND einen Stream-Handler (INFO) an und setzt
    # propagate=False (kollidiert NICHT mit Optunas eigenem Logger; KEIN set_verbosity, Pitfall #74).
    setup_bot_logging("optimizer")

    parser = argparse.ArgumentParser(description="Per-symbol micro-tuning sweep (Ansatz 4). Never enters Phase 5.")
    parser.add_argument("--strategies", default="all", help="'all' (aktive aus strategies.json) oder Komma-Liste")
    parser.add_argument("--symbols", default="all", help="'all' (Universum) oder Komma-Liste")
    parser.add_argument("--tier", default="deployable", choices=["deployable", "refine", "all"])
    parser.add_argument("--n-jobs", type=int, default=1, help="Parallele Studies (je eigene SQLite-Datei)")
    args = parser.parse_args(argv)

    strategies = _resolve_strategies(args.strategies)
    symbols = None if args.symbols == "all" else [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Issue #403: Config-Quellen + Kern-Schwellen einmalig offenlegen, bevor der Sweep in die
    # (subprocess-stummen) iterativen Trials uebergeht.
    log_active_config(f"per-symbol sweep · tier={args.tier}",
                      extra={"n_jobs": args.n_jobs,
                             "strategien": len(strategies),
                             "symbole": "all" if symbols is None else len(symbols)})

    proposals = run_per_symbol_sweep(strategies, symbols, tier=args.tier, n_jobs=args.n_jobs)
    for p in proposals:
        print(p)
    return proposals


if __name__ == "__main__":
    main()
