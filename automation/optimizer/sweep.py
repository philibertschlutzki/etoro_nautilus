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
from automation.optimizer.manifest import WORK, write_json_atomic
from automation.optimizer.run_optimization import (
    optimize_symbol as _optimize_symbol,
    load_global_best,
    log_active_config,
    _sanitize,
    _preinit_study_storage,
    _dispose_storage,
    derive_n_trials,
    assert_structural_min_modelled_trials_valid,
)
from automation.optimizer.confirm import confirm_per_symbol_promotion as _confirm, export_symbol_proposal
from automation.optimizer import champions
from automation.optimizer import retention
from automation.optimizer import disk_guard
from automation.optimizer import wallclock_guard
from automation.optimizer.sweep_diagnostics import (
    load_symbol_strategy_denylist, load_diagnosed_pairs_cache,
    load_continuous_bar_invalid_strategies, age_diagnosed_pairs_cache, is_diagnosed_pair_expired,
    check_bar_quality, diagnose_symbol_degeneracy, record_diagnosed_pair,
)
from automation.log_manager import (
    setup_bot_logging, emit_execution_event, emit_gate1_rejection, default_run_id,
)

# Issue #839 — analog wallclock_guard.sweep_wallclock_exceeded/disk_guard.sweep_abort_requested:
# prozessweites Signal, WELCHE Fail-Fast-Invariante (optimizer.json['fail_fast_invariants']) den
# Abbruch ausgeloest hat (None = keiner). Von run_per_symbol_sweep gesetzt, von main() gelesen, um
# run_status='aborted_invariant' zu waehlen.
sweep_fail_fast_invariant: str | None = None


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


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parst die fuehrenden numerischen Komponenten einer Versions-Zeichenkette (z. B. ``'2.3.3'``
    -> ``(2, 3, 3)``); ein nicht-numerischer Suffix (rc/dev/post, z. B. ``'3.0.0.dev0+abc'``) bricht
    das Parsing an dieser Stelle ab und liefert die bis dahin gelesenen Komponenten."""
    parts: list[int] = []
    for chunk in version_str.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def assert_pandas_version_supported() -> None:
    """Issue #802 — FAIL-LOUD beim Sweep-Start: die installierte ``pandas``-Version muss innerhalb
    des in ``requirements.txt`` gepinnten Bereichs (``>=2.2,<4.0``) liegen. #801/#802 zeigten, dass
    ``pct_change()``'s ``fill_method``-Semantik sich zwischen pandas 2.0/2.1/3.0 aenderte und die
    #756-Log-Return-Identitaet dadurch eine Funktion der Installationsumgebung statt der
    Konfiguration war — dieser Guard macht eine ungetestete pandas-Version zu einem expliziten
    Abbruch statt eines stillen numerischen Unterschieds (Pitfall #237)."""
    import pandas as pd
    version = _parse_version_tuple(pd.__version__)
    if version < (2, 2) or version >= (4, 0):
        raise ValueError(
            f"UNSUPPORTED_PANDAS_VERSION (#802): installierte pandas-Version {pd.__version__} "
            f"liegt ausserhalb des unterstuetzten Bereichs >=2.2,<4.0 (siehe "
            f"automation/requirements.txt) — die #756/#801-Log-Return-Identitaet ist nur in diesem "
            f"Bereich versionsstabil verifiziert."
        )


def _assert_gate_reward_parity() -> None:
    """Issue #593 — FAIL-LOUD beim Sweep-Start: ``eligible_requires_any`` und die
    ``_any_condition_distance``-Klauseln müssen dieselbe Menge sein (Gate/Reward-Parität).

    Issue #810 — zusätzlich: JEDES aktive Gate (``eligible_requires_all``/``_any``) MUSS einen
    Eintrag in ``tournament.json['gate_consolidation_priority']`` haben (Root-Cause #810: ein
    fehlender Eintrag fiel bislang auf einen stillen Sentinel, der den Redundanz-Alarm zur
    Entfernung einer harten Risikogrenze verleitete, statt den Sweep-Start abzubrechen)."""
    from automation.optimizer.reward import assert_any_condition_parity, assert_gate_priority_coverage
    try:
        cfg = json.loads((config_dir() / "tournament.json").read_text("utf-8"))
    except (OSError, ValueError):
        return
    assert_any_condition_parity(cfg)
    assert_gate_priority_coverage(cfg)


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


# Issue #807 — Sentinel-"Strategie" fuer symbolweite (statt paar-weise) diagnosed_pairs_cache-
# Eintraege: EIN Eintrag pro degenerierten Symbol statt 14 unabhaengiger Strategie-Eintraege.
_SYMBOL_DEGENERACY_SENTINEL_STRATEGY = "__SYMBOL_DATA_DEGENERATE__"


def _load_symbol_bar_quality_sample(symbol: str, catalog_path: Path | None = None, *,
                                    max_ticks: int = 200_000) -> dict | None:
    """Issue #807 — liest eine BESCHRAENKTE Stichprobe roher Quote-Ticks (``bid_price``/
    ``ask_price``/``ts_event``, direkt via pyarrow — analog ``count_available_bars`` oben, OHNE die
    volle NautilusTrader-``ParquetDataCatalog``-Materialisierung) und aggregiert sie zu
    synthetischen 1h-Mid-Price-Bars (High/Low/Close) fuer die Bar-Qualitaetspruefung
    (``sweep_diagnostics.check_bar_quality``).

    Liest bewusst nur die LETZTEN ``max_ticks`` Zeilen (rueckwaerts ueber die Row-Groups) statt der
    vollen Historie — haelt den Preflight unter der <2s-Vorgabe (#807-Akzeptanzkriterium)
    unabhaengig von der Gesamtgroesse des Katalogs; die juengste Teilspanne ist fuer die
    AKTUELLE Bar-Qualitaet ohnehin die massgebliche.

    Rueckgabe ``None`` bei fehlender Datei, fehlenden Spalten, leerer Bar-Serie oder JEDEM
    Lesefehler (fail-open — ein eigener Lesefehler darf den Sweep nie blockieren; Gate 1
    [Datenspanne] bleibt die unabhaengige, bereits bestehende Absicherung)."""
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
    pq_file = Path(catalog_path) / "data" / "quote_tick" / symbol / "data.parquet"
    if not pq_file.exists():
        return None
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pandas as pd
        pf = pq.ParquetFile(str(pq_file))
        cols = [c for c in ("bid_price", "ask_price", "ts_event") if c in pf.schema.names]
        if len(cols) < 3:
            return None
        tables = []
        n_read = 0
        for rg in range(pf.metadata.num_row_groups - 1, -1, -1):
            t = pf.read_row_group(rg, columns=cols)
            tables.append(t)
            n_read += t.num_rows
            if n_read >= max_ticks:
                break
        table = pa.concat_tables(list(reversed(tables)))
        df = table.to_pandas()
        if len(df) > max_ticks:
            df = df.tail(max_ticks)
        if df.empty:
            return None
        df["mid"] = (df["bid_price"].astype(float) + df["ask_price"].astype(float)) / 2.0
        df["ts"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
        df = df.set_index("ts").sort_index()
        bars = df["mid"].resample("1h").agg(["max", "min", "last"]).dropna()
        if bars.empty:
            return None
        return {
            "highs": bars["max"].tolist(),
            "lows": bars["min"].tolist(),
            "closes": bars["last"].tolist(),
        }
    except Exception:
        return None


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

    Issue #784, Umsetzungspunkt 3 — ``tournament_cfg['deflation_family_floor_mode']`` steuert, ob die
    je-Study-Zahl zusätzlich auf ``n_trials_budget`` angehoben wird (``'budgeted'``), WENN dieser
    Wert (User-Attr, von ``run_optimization`` gestempelt) grösser ist als die tatsächlich
    evaluierten Trials dieser Study, oder nicht (``'attempted'``, SEIT #814 DEFAULT — zählt NUR die
    tatsächlich gezogenen Kandidaten).

    Issue #814 — ``'budgeted'`` ist SEIT #814 NICHT MEHR Default: Root-Cause #814 — ein Trial, der
    NIE gezogen wurde, hat keinen Sharpe-Schätzer und kann das Maximum unter H0 nicht beeinflusst
    haben; ihn über ``n_trials_budget`` mitzuzählen ist keine konservative Wahl, sondern eine
    Fehlspezifikation der Nullverteilung — UND reproduziert denselben Kopplungsfehler, den #784
    beseitigen wollte, nur mit umgekehrtem Vorzeichen (eine früh abgebrochene Study wird jetzt
    HÄRTER deflatiert als eine vollständig durchlaufene). ``'budgeted'`` bleibt als Option für eine
    KONSERVATIVE SENSITIVITÄTSANALYSE erhalten; die Suchraum-Kapazität wandert, falls gewünscht, in
    den separaten, additiven ``deflation.sr0_multiple_testing_robust``-Term
    ``search_space_penalty`` (``tournament.json['deflation_search_space_penalty']``, Default
    ``None`` = aus), statt N selbst zu verzerren.

    Issue #812 — summiert weiterhin PRO SYMBOL (unverändertes Interface für confirm.py), warnt aber
    (WARNING-Log), wenn die Studies eines Symbols unterschiedliche ``selection_rule_fingerprint``
    tragen: eine über die Familie NICHT konstante Selektionsregel (z. B. ``any_arm_unreachable_
    policy='drop_arm'`` griff bei einer Study, aber nicht bei einer anderen) verletzt die
    Voraussetzung der DSR-Multiplizitätskorrektur (Pitfall #248). Die nach Fingerprint
    aufgeschlüsselte ``n_family``-Zahl lebt im #742-Report (``report._selection_rule_families``).

    Issue #822 — zählt seit diesem Fix ``oos_selection_statistic_available is True`` (Trials MIT
    einer verwertbaren Selektions-Teststatistik, ``oos_psr``), NICHT MEHR ``oos_evaluated is True``
    (blosse Aktivität). Root-Cause #822: ein Trial mit ``SORTINO_GUARD_TRIPPED`` (#823) oder
    ``EQUITY_NONPOSITIVE`` (#825) ist ``oos_evaluated=True``, trägt aber keinen Sortino/PSR — er hat
    das Maximum unter H₀ NICHT beeinflusst und darf die Multiplizität nicht erhöhen (dieselbe
    Argumentationslogik wie #814, eine Ebene tiefer: ein Trial, dessen Statistik VERWORFEN wurde,
    ist äquivalent zu einem nie gezogenen Trial). ``oos_evaluated`` bleibt als reine
    Aktivitäts-Telemetrie (#770/#769) unverändert erhalten.

    Issue #826 — diese SYMBOLWEITE Summe wird seit #826 NICHT MEHR direkt an ``confirm(...)``
    durchgereicht (siehe ``_family_n_per_study_from_studies`` für das je-Study-N1, das die
    tatsächlich getroffene Per-Strategie-Entscheidung korrekt bedient). Bleibt als Rückwärtskompat-/
    Telemetrie-Funktion erhalten (u. a. für bestehende Tests und eine potenzielle
    ``promotion_family_scope='per_symbol_best'``-Stufe-2-Referenzgrösse)."""
    per_study, symbol_fingerprints = _family_n_per_study_from_studies(
        pairs, studies, tournament_cfg=tournament_cfg)
    family_n: dict[str, int] = {}
    for (_strategy, symbol), n_evaluated in per_study.items():
        family_n[symbol] = family_n.get(symbol, 0) + n_evaluated
    for symbol, fingerprints in symbol_fingerprints.items():
        if len(fingerprints) > 1:
            logging.getLogger("optimizer").warning(
                "[#812] %s: %d verschiedene selection_rule_fingerprint ueber die Familie — die "
                "DSR-Multiplizitaetskorrektur setzt eine ueber die Familie konstante Selektions-"
                "regel voraus (Pitfall #248). Aufschluesselung: #742-Report['cross_study']"
                "['selection_rule_families'][%r].",
                symbol, len(fingerprints), symbol,
            )
    return family_n


def _family_n_per_study_from_studies(pairs, studies, *, tournament_cfg: dict | None = None,
                                     ) -> tuple[dict[tuple[str, str], int], dict[str, set]]:
    """Issue #826 Fix Punkt 1 — gemeinsamer Zaehl-Kernel: liefert das #822-N JE EINZELNER Study
    (``{(strategy, symbol): n}``), OHNE es über die Strategien eines Symbols zu summieren, plus die
    ``selection_rule_fingerprint``-Mengen je Symbol (für die #812-Heterogenitätswarnung). Sowohl
    ``_family_n_from_studies`` (Rückwärtskompat: symbolweite Summe) als auch
    ``_family_n_stage1_from_studies``/``_family_n_stage2_from_studies`` (#826, die tatsächlich an
    ``confirm(...)`` durchgereichte Per-Strategie-Zahl) bauen auf DIESER EINEN Zählung auf — dieselbe
    Trial-Iteration + dasselbe ``_dispose_storage``-Nachziehen (#747) darf nicht divergieren."""
    # Issue #814 — Default 'attempted' (vorher 'budgeted'): 'budgeted' war keine konservative Wahl,
    # sondern eine Fehlspezifikation der Nullverteilung (ein nie gezogener Trial hat keinen Sharpe-
    # Schaetzer). Ein fehlender Key faellt daher NICHT mehr auf die alte, jetzt als fehlerhaft
    # dokumentierte Config-Wert zurueck.
    floor_mode = (tournament_cfg or {}).get("deflation_family_floor_mode", "attempted")
    per_study: dict[tuple[str, str], int] = {}
    symbol_fingerprints: dict[str, set] = {}
    for (strategy, symbol, _reason), study in zip(pairs, studies):
        trials = getattr(study, "trials", None) or []
        n_evaluated = sum(
            1 for t in trials
            if getattr(t, "user_attrs", {}).get("oos_selection_statistic_available") is True
        )
        if floor_mode == "budgeted":
            n_trials_budget = (getattr(study, "user_attrs", None) or {}).get("n_trials_budget")
            if isinstance(n_trials_budget, (int, float)) and not isinstance(n_trials_budget, bool):
                n_evaluated = max(n_evaluated, int(n_trials_budget))
        per_study[(strategy, symbol)] = per_study.get((strategy, symbol), 0) + n_evaluated
        fingerprint = (getattr(study, "user_attrs", None) or {}).get("selection_rule_fingerprint")
        if fingerprint is not None:
            symbol_fingerprints.setdefault(symbol, set()).add(fingerprint)
        # Issue #747 — ``study.trials`` reconnected die (in optimize_symbol bereits disposte) Engine
        # lazy; ohne erneutes Dispose HIER waeren nach dieser Schleife wieder ALLE Studies gleichzeitig
        # offen (derselbe Erschoepfungs-Mechanismus, nur an eine spaetere Stelle verschoben).
        _dispose_storage(getattr(study, "_etoro_rdb_storage", None))
    return per_study, symbol_fingerprints


def _family_n_stage1_from_studies(pairs, studies, *, tournament_cfg: dict | None = None,
                                  ) -> dict[tuple[str, str], int]:
    """Issue #826 Fix Punkt 1/2 — N1: die Multiple-Testing-Multiplizität EINER EINZELNEN Study
    (Strategie, Symbol), NICHT symbolweit über alle Strategien summiert. Root-Cause #826:
    ``export_symbol_proposal``/die Promotion-Entscheidung ist je (Strategie, Symbol) unabhängig —
    ``_family_n_from_studies`` lieferte bislang trotzdem eine Symbol-weite Summe über ALLE
    Strategien-Studies als Multiplizität für JEDE einzelne davon (Roster-/Budget-Kopplung, siehe
    Katalog #750 #826). Unter ``promotion_family_scope='per_strategy'`` (Default, siehe
    ``_resolve_promotion_family_scope``) ist DIESES N1 die an ``confirm(...)`` durchgereichte Zahl."""
    per_study, _fingerprints = _family_n_per_study_from_studies(pairs, studies, tournament_cfg=tournament_cfg)
    return per_study


def _family_n_stage2_from_studies(pairs, studies, *, tournament_cfg: dict | None = None) -> dict[str, int]:
    """Issue #826 Fix Punkt 1/2 — N2: je Symbol die Zahl der Strategien, die ÜBERHAUPT einen
    Kandidaten mit Selektions-Teststatistik geliefert haben (N1 > 0). Reine Telemetrie
    (``report.cross_study['n_family_stage2']``) für eine HYPOTHETISCHE zweite Korrekturstufe
    (``promotion_family_scope='per_symbol_best'``) — diese Stufe ist NICHT aktiv (siehe
    ``_resolve_promotion_family_scope``: fail-loud, mangels H0-Kalibrierung der Stufen-Komposition,
    Katalog #750 #826 Fix Punkt 4)."""
    per_study, _fingerprints = _family_n_per_study_from_studies(pairs, studies, tournament_cfg=tournament_cfg)
    stage2: dict[str, int] = {}
    for (_strategy, symbol), n in per_study.items():
        stage2.setdefault(symbol, 0)
        if n > 0:
            stage2[symbol] += 1
    return stage2


def _family_fingerprints_from_studies(pairs, studies, *, tournament_cfg: dict | None = None,
                                      ) -> dict[str, set]:
    """Issue #827 Fix Punkt 3 — reine ``selection_rule_fingerprint``-Mengen je Symbol (Kernel-
    Wiederverwendung, siehe ``_family_n_per_study_from_studies``). Genutzt von der Symbol-Schleife
    für ``selection_rule_homogeneity_policy='fail'`` (Symbol-Abbruch VOR jedem Confirm-Aufruf, siehe
    ``_resolve_selection_rule_homogeneity_policy``)."""
    _per_study, fingerprints = _family_n_per_study_from_studies(pairs, studies, tournament_cfg=tournament_cfg)
    return fingerprints


_PROMOTION_FAMILY_SCOPES = ("per_strategy", "per_symbol_best")


def _resolve_promotion_family_scope(tournament_cfg: dict | None) -> str:
    """Issue #826 Fix Punkt 2 — deklariert, welchem Geltungsbereich die familienweite
    Multiplizitätskorrektur tatsächlich folgt (``tournament.json['promotion_family_scope']``,
    Default ``'per_strategy'``): DAS ist der Status quo der tatsächlich getroffenen Entscheidung
    (``export_symbol_proposal`` promotet je (Strategie, Symbol) unabhängig, #826-Root-Cause) und
    verwendet N1 (``_family_n_stage1_from_studies``) statt der bisherigen Symbol-weiten Summe.

    ``'per_symbol_best'`` (zweistufige Korrektur über zusätzlich N2, siehe
    ``_family_n_stage2_from_studies``) ist DEKLARIERT, aber ABSICHTLICH NICHT implementiert: die
    Komposition ``SR₀_gesamt`` aus den beiden Stufen ist laut Katalog #750 #826 Fix Punkt 1
    ausdrücklich NICHT ``E[max_{N1·N2}]`` und setzt eine eigene H0-Kalibrierung voraus (Fix Punkt 4,
    gemeinsam mit #824 Punkt 3/4 geplant — in diesem Environment nicht durchführbar, kein Monte-
    Carlo-Kalibrierlauf möglich). Eine unkalibrierte Formel still anzuwenden wäre keine konservative
    Wahl, sondern dieselbe Fehlspezifikationsklasse wie #814/#822 — daher FAIL-LOUD (analog #810s
    ``_GATE_CONSOLIDATION_PRIORITY``), bis der Kalibrierlauf vorliegt."""
    scope = (tournament_cfg or {}).get("promotion_family_scope", "per_strategy")
    if scope not in _PROMOTION_FAMILY_SCOPES:
        raise ValueError(
            f"promotion_family_scope: unbekannter Wert {scope!r} (tournament.json), erwartet "
            f"eines von {_PROMOTION_FAMILY_SCOPES}."
        )
    if scope == "per_symbol_best":
        raise ValueError(
            "promotion_family_scope='per_symbol_best' ist deklariert, aber die zweistufige SR0-"
            "Komposition (Katalog #750 #826 Fix Punkt 1) ist noch NICHT implementiert — sie "
            "erfordert einen eigenen H0-Kalibrierlauf (Fix Punkt 4), der in diesem Environment "
            "nicht durchführbar ist. 'per_strategy' verwenden, bis der Kalibrierlauf vorliegt."
        )
    return scope


_SELECTION_RULE_HOMOGENEITY_POLICIES = ("partition", "fail")


def _resolve_selection_rule_homogeneity_policy(tournament_cfg: dict | None) -> str:
    """Issue #827 Fix Punkt 3 — Policy für eine INNERHALB eines Symbols heterogene
    ``selection_rule_fingerprint``-Menge (mehrere Studies desselben Symbols wandten NACHWEISLICH
    unterschiedliche effektive Selektionsregeln an, z. B. weil ``any_arm_unreachable_policy=
    'drop_arm'`` bei einer Study griff, bei einer anderen nicht — Pitfall #248/#812).

    ``'partition'`` (Default) — bit-identisch zum Status quo: die ``[#812]``-WARNUNG wird geloggt,
    der Sweep läuft unverändert weiter. Seit #826 ist die aktive DSR-Multiplizität ohnehin JE STUDY
    (N1, ``promotion_family_scope='per_strategy'``) — eine heterogene Familie beeinflusst die
    tatsächlich getroffene Deflations-Entscheidung damit NICHT MEHR direkt (das war die #827-
    Root-Cause: die Voraussetzung war verletzt, wurde aber vor #826 trotzdem auf eine gemeinsame,
    symbolweite Zahl angewandt — nach #826 gibt es diese gemeinsame Zahl für die Deflation nicht
    mehr). ``'partition'`` bleibt als ALLGEMEINE Config-Konsistenz-Warnung sinnvoll (zeigt Drift
    zwischen Studies desselben Symbols, unabhängig von der Deflation).

    ``'fail'`` — bricht NUR DIESES EINE Symbol fail-loud ab (``SYMBOL_ABORTED_ON_SELECTION_
    HETEROGENEITY``: kein Proposal für IRGENDEINE Strategie dieses Symbols, andere Symbole laufen
    unverändert weiter, analog der #799-Per-Symbol-Fehlerisolation) — für Kalibrierläufe, in denen
    eine über die Familie konstante Selektionsregel eine harte Vorbedingung ist (z. B. der #824/#826
    Punkt-4-H0-Kalibrierlauf). Fehlt der Key ⇒ 'partition'."""
    policy = (tournament_cfg or {}).get("selection_rule_homogeneity_policy", "partition")
    if policy not in _SELECTION_RULE_HOMOGENEITY_POLICIES:
        raise ValueError(
            f"selection_rule_homogeneity_policy: unbekannter Wert {policy!r} (tournament.json), "
            f"erwartet eines von {_SELECTION_RULE_HOMOGENEITY_POLICIES}."
        )
    return policy


# Issue #822 — Rückübersetzung eines ``inference_diagnostics``-Codes (#804) auf den Aggregations-
# Grund für ``deflation_n_family_excluded_no_statistic``.
_NO_STATISTIC_DIAGNOSTIC_REASON = {
    "SORTINO_GUARD_TRIPPED": "sortino_guard",
    "EQUITY_NONPOSITIVE": "equity_ruined",
}


def _family_n_excluded_breakdown_from_studies(pairs, studies) -> dict[str, dict[str, int]]:
    """Issue #822 Fix Punkt 3 — je Symbol, wie viele ``oos_evaluated`` Trials AUSGESCHLOSSEN wurden
    (``oos_selection_statistic_available is False``), aufgeschlüsselt nach dem diagnostizierten
    Grund (``sortino_guard``/``equity_ruined``/``other``). Rein additive Telemetrie für
    ``confirm.confirm_per_symbol_promotion``s ``deflation_n_family_excluded_no_statistic`` — ändert
    NIE die gezählte Familien-Multiplizität selbst (``_family_n_from_studies``)."""
    import collections
    breakdown: dict[str, collections.Counter] = {}
    for (_strategy, symbol, _reason), study in zip(pairs, studies):
        trials = getattr(study, "trials", None) or []
        counter = breakdown.setdefault(symbol, collections.Counter())
        for t in trials:
            attrs = getattr(t, "user_attrs", {}) or {}
            if attrs.get("oos_evaluated") is not True:
                continue
            if attrs.get("oos_selection_statistic_available") is True:
                continue
            codes = {d.get("code") for d in (attrs.get("inference_diagnostics") or [])
                    if isinstance(d, dict)}
            reasons = {_NO_STATISTIC_DIAGNOSTIC_REASON[c] for c in codes
                      if c in _NO_STATISTIC_DIAGNOSTIC_REASON}
            if not reasons:
                reasons = {"other"}
            for reason in reasons:
                counter[reason] += 1
    return {symbol: dict(counter) for symbol, counter in breakdown.items()}


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

    Issue #813 — GESCHLOSSENE RESTLÜCKE (war bis #813 offen): ``trial.user_attrs['oos_period_
    returns']`` wurde in ``run_optimization.make_symbol_objective`` bis #813 NUR für ``oos_eligible``-
    Trials gestempelt (#663/#665) — die hier gesammelte Matrix sah die erweiterte ``oos_evaluated``-
    Kohorte (#784) also nur über den erweiterten FILTER, nicht über zusätzliche REIHEN (evaluierte-
    aber-ineligible Trials trugen faktisch fast immer ein leeres ``rets`` und fielen daher aus der
    Matrix) — die Correlation-Declusterung (``cpcv.cluster_effective_configs`` in ``confirm.py``)
    operierte damit weiterhin auf der ALTEN, kleinen Menge, während der ROHE Zähler
    (``_family_n_from_studies``) bereits die grosse #784-Kohorte zählte: ``deflation_n_effective``
    stieg um Faktor ~7,7, die tatsächlich declusterte Config-Zahl blieb konstant ⇒ systematische
    Über-Deflation. Seit #813 stempelt ``run_optimization`` ``oos_period_returns`` für JEDEN
    ``oos_evaluated`` Trial — Zähler und Decluster-Matrix operieren jetzt auf derselben Menge
    (``confirm.deflation_cluster_coverage`` telemetriert den verbleibenden Deckungsgrad,
    ``invariants.check_deflation_cluster_coverage`` bricht bei < 0.9 als Regressionswächter)."""
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


def _attempt_champion_writeback(strategy: str, symbol: str, opt_data: dict) -> None:
    """Issue #818 — ``champions.maybe_write_back`` hatte KEINE Produktions-Call-Site (getestet,
    dokumentiert, nie ausgeführt — exakt die Pitfall-#237-Fehlerklasse). Ebene 2 des Epics #702
    (Default-Nachführung nach ``strategy_symbol_seeds.json``) läuft erst ab hier tatsächlich.

    ``champions.load_champion_entry`` (statt des frisch gebauten Kandidaten aus
    ``store_champion``) liest den MERGE-/Korroborations-STAND, nicht den soeben gebauten
    Kandidaten — nur der Store-Stand trägt den tatsächlichen ``corroboration_count``. Emittiert
    IMMER ein strukturiertes ``CHAMPION_WRITEBACK``-Event (auch bei einem Nicht-Erfolg) —
    ``skipped_reason`` macht den Grund für ein *nicht* erfolgtes Rückschreiben genauso sichtbar
    wie das Rückschreiben selbst (Lehre aus #783/#786: ein unaufgeschlüsselter Nicht-Ausgang ist
    die teuerste Telemetrie-Lücke im System).

    Fail-open: ein Fehler in dieser Funktion darf den Sweep nie crashen (analog Champion-Store/
    Retention) — der Aufrufer (``_run_confirm_and_export``) muss NICHT selbst absichern."""
    log = logging.getLogger("optimizer")
    try:
        entry = champions.load_champion_entry(strategy, symbol, opt_data=opt_data)
        applied = False
        skipped_reason = "NO_ADMISSIBLE_ENTRY"
        advance_days = None
        corroboration_count = None
        if entry is not None:
            lifecycle = entry.get("lifecycle") or {}
            integrity = entry.get("integrity") or {}
            corroboration_count = lifecycle.get("corroboration_count")
            current_ns = integrity.get("catalog_newest_ns")
            first_ns = lifecycle.get("first_seen_catalog_newest_ns")
            if current_ns is not None and first_ns is not None:
                advance_days = (current_ns - first_ns) / 1_000_000_000.0 / 86400.0
            applied = champions.maybe_write_back(entry, opt_data)
            if not applied:
                if champions.champion_quality_stale(entry, opt_data):
                    skipped_reason = "QUALITY_STALE"
                else:
                    skipped_reason = "NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED"
        emit_execution_event(log, "CHAMPION_WRITEBACK", {
            "strategy": strategy, "symbol": symbol,
            "corroboration_count": corroboration_count,
            "advance_days": advance_days,
            "applied": applied,
            "skipped_reason": None if applied else skipped_reason,
        })
    except Exception:
        log.warning("[#818] %s/%s: Champion-Writeback fehlgeschlagen (non-fatal).",
                    strategy, symbol, exc_info=True)


def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, tier: str = "deployable", n_jobs: int = 1,
                         n_jobs_source: str = "DEFAULT",
                         optimize_symbol=None, confirm=None,
                         run_id: str | None = None, bar_quality_fn=None) -> list[Path]:
    """Dispatcht für jedes enumerierte Paar optimize_symbol → confirm_per_symbol_promotion →
    export_symbol_proposal und gibt die Proposal-Pfade zurück. Betritt NIE Phase 5.

    ``optimize_symbol``/``confirm`` sind injizierbar (Default: echte Implementierungen) —
    so bleibt der Dispatch ohne echten Backtest testbar (HI-7). ``n_jobs`` steuert parallele
    *Studies* (je eigene SQLite-Datei), niemals n_jobs>1 innerhalb einer Study.

    Issue #400: ``n_jobs > 1`` verteilt die Paare jetzt tatsaechlich ueber einen
    ``ThreadPoolExecutor`` (vorher wurde der Parameter ignoriert / strikt sequenziell). Die
    Ausgabereihenfolge bleibt deterministisch (``executor.map`` bewahrt die Eingabereihenfolge);
    fuer ``n_jobs <= 1`` bleibt der Pfad bit-identisch sequenziell.

    Issue #799 — der Dispatch ist jetzt PRO SYMBOL transaktional statt einer globalen Zwei-Phasen-
    Barriere über ALLE Paare (siehe Docstring von ``_run_confirm_and_export`` weiter unten für die
    Root-Cause). ``run_id`` (Default ``default_run_id()``) treibt den Fortschritts-Checkpoint
    ``{WORK}/sweep_progress.json``: ruft ein Aufrufer diese Funktion ERNEUT mit DEMSELBEN
    ``run_id`` auf (z. B. nach einem Absturz), werden bereits abgeschlossene Symbole übersprungen
    (deren bereits exportierte Proposals werden von der Platte nachgeladen, keine Duplikat-Arbeit).
    Ein abweichender/fehlender ``run_id`` im Checkpoint ⇒ frischer Lauf (Checkpoint wird verworfen).

    Issue #807 — ``bar_quality_fn`` (Default ``None`` ⇒ ``_load_symbol_bar_quality_sample``, echter
    Katalog-Zugriff) ist injizierbar (HI-7): ein Test uebergibt eine reine Fake-Funktion
    ``symbol -> {"highs", "lows", "closes"} | None`` statt echte Parquet-Dateien zu lesen.

    Issue #828 (Katalog #828-#835, GitHub-Issue #751) — Scope-Entscheidung: der Dispatch bleibt
    weiterhin PRO SYMBOL synchron (die #652-Familieninvariante UND die #799-Transaktionsgrenze
    setzen genau das voraus — alle Strategien-Studies EINES Symbols müssen abgeschlossen sein,
    bevor dessen familienweite Multiplizität/Confirm/Export/Checkpoint laufen). Implementiert sind
    Fix Punkt 3 (``max_workers`` ist nicht mehr an ``len(symbol_pairs)`` gedeckelt) und Fix Punkt 5
    (``wallclock_guard``/``sweep_max_wallclock_h``, siehe dort). BEWUSST NICHT implementiert: Fix
    Punkt 1 (Pipelining — Studies werden über die GESAMTE Laufzeit in einen gemeinsamen Pool mit
    Look-Ahead ``pipeline_depth`` eingereiht, nicht mehr strikt symbolweise seriell gewartet) und
    Fix Punkt 2 (Largest-First-Scheduling, das nur INNERHALB eines solchen gemeinsamen Fensters
    etwas bewirkt). Root-Cause der Deferral: eine Restrukturierung der Dispatch-Schleife, auf die
    > 15 bestehende Tests (#799/#747/#755/#400/#412/#414/#415/#511/#595/#698/#795/#807 u. a.) für
    die KORREKTHEIT der Familien-/Checkpoint-/Determinismus-Invarianten angewiesen sind, ist ohne
    einen echten Mehrsymbol-Produktionslauf (122 Symbole, mehrere Stunden) NICHT empirisch gegen
    die Akzeptanzkriterien (≥ 80 % Worker-Auslastung, ≤ 24 h Hochrechnung) verifizierbar — dieses
    Environment hat keinen realen Marktdaten-Katalog dafür. Eine blind implementierte Pipeline-
    Restrukturierung riskiert eine STILLE Korrektheitsregression (z. B. eine Familien-N-
    Fehlberechnung durch eine Symbolgrenzen-Überschreitung) — strukturell dieselbe Klasse Risiko
    wie die bereits an anderer Stelle in dieser Kohorte zurückgestellten Arbeiten (#823 T-adaptiver
    Guard, #824/#826 H0-Kalibrierung, #825 Liquidations-Engine). Fix Punkt 4 (SuccessiveHalving-
    Pruner) ist ebenfalls NICHT implementiert: der Zwischenwert für ``trial.report()`` (der
    fold-weise Reward) wird erst NACH dem vollständigen Rückkehren des Backtest-Subprozesses
    bekannt (``run_backtest``/``metrics.oos_fold_sortinos``, siehe ``make_symbol_objective`` —
    JEDER Fold läuft bereits im Subprozess, bevor der Elternprozess irgendein Zwischenergebnis
    sieht) — ein Pruner könnte an dieser Stelle KEINE Rechenzeit sparen (die teure Arbeit ist
    bereits erledigt), nur die TPE-Stichprobenwahl nachträglich beeinflussen. Das entspricht nicht
    dem im Issue behaupteten Nutzen ("jeder Trial läuft bis zum Ende") und würde eine echte
    Subprozess-IPC-Restrukturierung (fold-weises Zwischen-Reporting) voraussetzen, um den
    tatsächlichen Durchsatzgewinn zu erzielen — ebenfalls zurückgestellt.
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

    # Issue #703 — vollständige optimizer.json EINMAL vor dem Dispatch geladen (Champion-Store-
    # Gates: reward_semantics_version + champion_*-Keys), wiederverwendet über die Closure von
    # ``_run_confirm_and_export`` statt pro Paar erneut von der Platte gelesen zu werden. Issue #805
    # — jetzt auch VOR dem Preflight-Block geladen, damit assert_structural_min_modelled_trials_valid
    # (unten) sie konsumieren kann, ohne die Datei ein zweites Mal zu lesen.
    opt_data = _load_optimizer_config()

    # Issue #595/#593 — FAIL-LOUD-Preflight VOR dem ersten Trial (nur im echten Pfad; injizierte
    # HI-7-Fakes nutzen frei benannte Strategien und überspringen den Guard). (1) Jede aktive
    # Strategie MUSS einen Suchraum in spaces.py haben. (2) Gate- und Reward-Klauseln müssen
    # dieselbe eligible_requires_any-Menge sehen.
    if using_real_optimize:
        assert_strategy_space_parity(strategies)
        _assert_gate_reward_parity()
        # Issue #802 — pandas-Versions-Preflight (Zero-Hardcoding: Bereich stammt aus
        # requirements.txt, hier nur maschinell durchgesetzt).
        assert_pandas_version_supported()
        # Issue #805 — structural_min_modelled_trials_per_dim<=0 waere derselbe degenerierte
        # Zustand wie das entfernte floor_plateau_k=0 (#488/#753/#769) — fail-loud statt eines
        # stillen NULL-modellierten-Trials-Urteils.
        assert_structural_min_modelled_trials_valid(opt_data)

    syms = symbols if symbols is not None else load_symbol_universe()
    config = _load_gate_config()
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

    # Issue #807 — Bar-QUALITAETS-Preflight VOR Phase 1 je Symbol (Preflight statt Post-Mortem):
    # ``HYPE.ETORO`` bestand Gate 1 (Datenspanne, oben) UND erzeugte trotzdem ueber SECHS
    # strukturell verschiedene Strategien 0 auswertbare Trials — je eigenem
    # ``STRUCTURAL_ALL_UNEVALUABLE``-Ereignis, eigenem Diagnose-Aufruf, eigenem Cache-Eintrag
    # (14 × 16 = 224 verbrannte Trials, 14 falsch etikettierte Cache-Eintraege). Root-Cause war die
    # BAR-QUALITAET (degenerierte/konstante Bars), nicht die Datenspanne. Ein Symbol mit
    # degenerierten Bars wird hier EINMAL abgewiesen (``REJECT_DATA_DEGENERATE``), bevor auch nur
    # eine einzige Study fuer es gestartet wird — analog dem bestehenden Gate-1-Pfad. Nur im echten
    # Storage-Pfad (injizierte HI-7-Fakes haben keinen echten Katalog); ``bar_quality_fn`` bleibt
    # aber unabhaengig davon injizierbar, damit dieser Block selbst ohne echten Katalog testbar ist.
    if (using_real_optimize or bar_quality_fn is not None) and syms:
        _load_sample = bar_quality_fn or _load_symbol_bar_quality_sample
        _bar_quality_cfg = opt_data.get("bar_quality") or {}
        _degenerate_syms = []
        _log = logging.getLogger("optimizer")
        for _sym in syms:
            try:
                _sample = _load_sample(_sym)
            except Exception:
                _sample = None  # fail-open — ein eigener Lesefehler blockiert den Sweep nie.
            if _sample is None:
                continue
            _quality = check_bar_quality(
                _sample["highs"], _sample["lows"], _sample["closes"],
                max_frac_high_eq_low=_bar_quality_cfg.get("max_frac_high_eq_low", 0.5),
                max_frac_identical_consecutive_closes=_bar_quality_cfg.get(
                    "max_frac_identical_consecutive_closes", 0.5),
                min_distinct_closes=_bar_quality_cfg.get("min_distinct_closes", 10),
            )
            if _quality["passed"]:
                continue
            _degenerate_syms.append(_sym)
            emit_execution_event(_log, "REJECT_DATA_DEGENERATE", {"symbol": _sym, **_quality},
                                 level=logging.WARNING)
            _log.warning(
                "[#807] %s: REJECT_DATA_DEGENERATE — Bar-Qualitaet degeneriert (%s). Symbol wird "
                "VOR Phase 1 fuer ALLE Strategien abgewiesen (0 Studies), statt N unabhaengige "
                "Suchraum-Diagnosen zu verbrennen.", _sym, _quality["reason"],
            )
            # Issue #807 (analog #799s Checkpoint) — NICHT auf using_real_optimize gegated: der
            # Rueckschrieb ist billige, deterministische JSON-I/O relativ zu WORK (in Tests bereits
            # isoliert) und soll unabhaengig davon testbar sein, ob optimize_symbol injiziert wurde.
            try:
                record_diagnosed_pair({
                    "strategy": _SYMBOL_DEGENERACY_SENTINEL_STRATEGY, "symbol": _sym,
                    "action": "denylist", "binding_cause": "data_degenerate",
                    "median_is_trades": None, "median_oos_trades": None,
                }, work_dir=WORK, run_id=run_id)
            except Exception:
                _log.debug("[#807] Diagnose-Rueckschrieb fehlgeschlagen (non-fatal).",
                          exc_info=True)
        if _degenerate_syms:
            syms = [s for s in syms if s not in _degenerate_syms]
            available_bars = count_available_bars(syms)

        # Issue #807 — Sekundaer-Signal (rein informativ, blockiert NICHTS): aggregiert bereits
        # gecachte per-Strategie-Diagnosen (aus VORHERIGEN Laeufen, #681-Closed-Loop) je
        # verbleibendem Symbol via diagnose_symbol_degeneracy. Mehrere strukturell verschiedene
        # Strategien mit binding_cause=='signal_absent' UND median_is_trades==0 sind ein Beleg fuer
        # ein Datenproblem, das der Bar-Qualitaets-Preflight (noch) nicht erfasst hat — die
        # Bar-Qualitaet selbst bleibt aber die NOTWENDIGE Bedingung fuer eine tatsaechliche
        # Ablehnung; dieser Check weist selbst nie ein Symbol ab (Akzeptanzkriterium #807).
        if syms:
            _diag_cache = load_diagnosed_pairs_cache(work_dir=WORK)
            _min_strategies = int(opt_data.get("symbol_degeneracy_min_strategies", 3))
            for _sym in syms:
                _per_strategy = [
                    v for (strat, sym), v in _diag_cache.items()
                    if sym == _sym and strat != _SYMBOL_DEGENERACY_SENTINEL_STRATEGY
                ]
                if not _per_strategy:
                    continue
                _agg = diagnose_symbol_degeneracy(_sym, _per_strategy, min_strategies=_min_strategies)
                if _agg["is_degenerate"]:
                    logging.getLogger("optimizer").warning(
                        "[#807] %s: SYMBOL_DATA_DEGENERATE (Sekundaer-Signal aus %d gecachten "
                        "Diagnosen, %d/%d 'signal_absent') — der Bar-Qualitaets-Preflight erfasste "
                        "dies (noch) nicht; das Symbol wird DENNOCH nicht automatisch abgewiesen "
                        "(Bar-Qualitaet ist die notwendige Bedingung, keine Diagnosezahl allein).",
                        _sym, _agg["n_strategies_checked"], _agg["n_signal_absent"],
                        _agg["min_strategies"],
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
    # Issue #799 — PRO SYMBOL transaktional statt einer globalen Zwei-Phasen-Barriere über ALLE
    # Paare (#652). Root-Cause #799: ``executor.map(_run_optimize, pairs)`` war eine Barriere über
    # ALLE 1736 Paare eines vollen Sweeps — ein einziger Fehler (oder eine propagierende Exception)
    # in irgendeinem Paar verwarf die Ergebnisse ALLER bereits abgeschlossenen Studies, und
    # Phase 2 (Retention, #794) lief nie an, solange Phase 1 nicht vollständig durch war (17-21h bei
    # 1736 Paaren). Die familienweite Multiplizität (#652) braucht nur alle Studies EINES Symbols,
    # nicht aller Symbole — die Barriere war also breiter als die fachliche Anforderung.
    #
    # Jetzt: äussere Schleife über Symbole (sequenziell); innere Parallelisierung über die
    # Strategien EINES Symbols (ThreadPoolExecutor(max_workers=n_jobs)); nach jedem Symbol sofort
    # Confirm + Export + Champion-Store + Retention (#794) — ein Fehler kostet höchstens das
    # aktuelle Symbol, nicht den gesamten Lauf.
    pairs_by_symbol: dict[str, list[tuple[str, str, str]]] = {}
    for _pair in pairs:
        pairs_by_symbol.setdefault(_pair[1], []).append(_pair)

    # Issue #799 — der Fortschritts-Checkpoint ist NICHT an using_real_optimize gekoppelt (anders
    # als Champion-Store/Retention/Preinit/Backfill): er verfolgt den Dispatch-Fortschritt selbst,
    # unabhaengig davon, ob optimize_symbol/confirm injizierte HI-7-Fakes oder die echte
    # Implementierung sind — das macht ihn mit injizierten Fakes testbar (siehe
    # test_issue_799_per_symbol_transaction.py) UND nuetzlich fuer einen HI-7-Dry-Run.
    if run_id is None:
        run_id = default_run_id()
    checkpoint_path = WORK / "sweep_progress.json"
    completed_symbols: set[str] = set()
    failed_pairs: list[dict] = []
    try:
        if checkpoint_path.exists():
            _checkpoint = json.loads(checkpoint_path.read_text("utf-8"))
            if _checkpoint.get("run_id") == run_id:
                completed_symbols = set(_checkpoint.get("completed_symbols") or [])
                failed_pairs = list(_checkpoint.get("failed_pairs") or [])
    except (OSError, ValueError):
        pass  # kaputter/fehlender Checkpoint ⇒ frischer Lauf (nicht fatal)

    def _write_checkpoint() -> None:
        # Issue #799 — atomarer Fortschritts-Checkpoint (manifest.write_json_atomic, #742-Muster).
        # Fail-open: ein Schreibfehler darf einen erfolgreichen Sweep nie crashen.
        try:
            write_json_atomic(checkpoint_path, {
                "run_id": run_id,
                "completed_symbols": sorted(completed_symbols),
                "failed_pairs": failed_pairs,
                # Issue #833 Fix Punkt 3 — Gesamtzahl der fuer DIESEN Lauf geplanten Symbole, damit
                # ein Abbruch-Report (sweep.main()) "symbols_completed / symbols_planned" ausweisen
                # kann, OHNE die Enumeration ein zweites Mal auszufuehren.
                "symbols_planned": len(pairs_by_symbol),
            })
        except OSError:
            logging.getLogger("optimizer").warning(
                "[#799] Sweep-Checkpoint konnte nicht geschrieben werden (non-fatal).", exc_info=True)

    def _run_optimize(pair: tuple[str, str, str]):
        strategy, symbol, _reason = pair
        # Issue #799 — Per-Paar-Fehlerisolation: JEDE Exception (nicht nur die vom Backtest-
        # Subprozess) wird hier gefangen, protokolliert (STUDY_FAILED) und liefert None statt die
        # gesamte Symbol-Charge (bzw. vor #799: den gesamten Sweep) zu verwerfen. Ein None-Eintrag
        # fliesst weder in n_family (#652/#784) noch in Phase 2 (siehe _run_confirm_and_export).
        try:
            newest_ns = latest_ts.get(symbol) if latest_ts else None
            # Issue #531 — die REAL vorhandene Bar-Spanne (Tage) an build_trial durchreichen, damit
            # die Manifest-Konstruktion gegen die tatsächliche Datenlage prüft.
            span_days = available_bars.get(symbol, 0) / 24.0
            return optimize_symbol(strategy, symbol, catalog_newest_ns=newest_ns,
                                   catalog_span_days=span_days)
        except Exception as e:
            logging.getLogger("optimizer").error(
                "[#799] STUDY_FAILED: %s/%s (%s): %s", strategy, symbol, type(e).__name__, e,
                exc_info=True,
            )
            emit_execution_event(logging.getLogger("optimizer"), "STUDY_FAILED", {
                "strategy": strategy, "symbol": symbol, "exception_type": type(e).__name__,
            }, level=logging.ERROR)
            failed_pairs.append({"strategy": strategy, "symbol": symbol,
                                 "exception_type": type(e).__name__})
            return None

    def _run_confirm_and_export(pair: tuple[str, str, str], study,
                                n_family_map: dict[str, int],
                                family_returns_map: dict[str, list],
                                n_family_excluded_map: dict[str, dict] | None = None,
                                *,
                                n_family_stage1_map: dict[tuple[str, str], int] | None = None,
                                family_scope: str = "per_strategy") -> Path | None:
        """Issue #652/#799/#822/#826 — Confirm + Export + Champion-Store + Retention EINES Paares.

        Issue #826 — die an ``confirm(...)`` durchgereichte Multiplizität ist seit diesem Fix N1
        (``n_family_stage1_map[(strategy, symbol)]``, die eigene Study-Zahl DIESES Paares), NICHT
        MEHR ``n_family_map.get(symbol, 0)`` (die Symbol-weite Summe über ALLE Strategien-Studies —
        das war die #826-Root-Cause: eine PER-STRATEGIE-Entscheidung erhielt eine SYMBOL-weite
        Multiplizität). ``family_scope`` kommt bereits aufgelöst/validiert von der Symbol-Schleife
        (``_resolve_promotion_family_scope`` — 'per_symbol_best' bricht dort VOR jedem Confirm-Aufruf
        fail-loud ab, siehe deren Docstring); ``n_family_map``/``family_returns_map`` bleiben für die
        Decluster-Matrix (``deflation_family_period_returns``, unverändert symbolweit, #695) und den
        #822-Ausschluss-Breakdown (``n_family_excluded_map``) erhalten."""
        strategy, symbol, _reason = pair
        if study is None:
            # Issue #799 — kein Study-Objekt (Paar in _run_optimize fehlgeschlagen, STUDY_FAILED
            # bereits protokolliert): kein Confirm/Export möglich, kein Proposal.
            return None
        if family_scope != "per_strategy":
            # Unerreichbar in Produktion (siehe _resolve_promotion_family_scope), aber explizit statt
            # eines stillen Fallbacks für direkte Unit-Test-Aufrufer dieser Funktion.
            raise ValueError(f"_run_confirm_and_export: unbekannter family_scope {family_scope!r}")
        try:
            newest_ns = latest_ts.get(symbol) if latest_ts else None
            global_params = load_global_best(strategy, config_dir())
            deflation_n_family_value = (n_family_stage1_map or {}).get((strategy, symbol), 0)
            promotion = confirm(study, strategy, symbol, global_params, catalog_newest_ns=newest_ns,
                                deflation_n_family=deflation_n_family_value,
                                deflation_family_period_returns=family_returns_map.get(symbol),
                                deflation_n_family_excluded_no_statistic=(
                                    (n_family_excluded_map or {}).get(symbol)))
            proposal_path = export_symbol_proposal(study, strategy, symbol, promotion)
            # Issue #703 — Champion-Store: persistiert den Ebene-1-Suchanker für den NÄCHSTEN
            # Sweep-Lauf, unmittelbar NACH dem Proposal-Export. Rein additiv (ändert weder die
            # aktuelle Promotion-Entscheidung noch strategies.json, HI-3); nur im echten Storage-Pfad
            # (injizierte HI-7-Fakes simulieren keinen Katalog/keine reale champions.WORK-Isolation).
            # Fail-open: ein Champion-Store-Fehler darf den Sweep nie crashen (analog #531-Backfill).
            if using_real_optimize:
                try:
                    champions.store_champion(study, strategy, symbol, promotion,
                                             catalog_newest_ns=newest_ns, opt_data=opt_data, tier=tier,
                                             run_id=run_id)
                except Exception:
                    logging.getLogger("optimizer").warning(
                        "[#703] %s/%s: Champion-Store-Schreiben fehlgeschlagen (non-fatal).",
                        strategy, symbol, exc_info=True,
                    )
                else:
                    _attempt_champion_writeback(strategy, symbol, opt_data)
                # Issue #733/#794 — Normalfall-Retention: die Study ist jetzt abgeschlossen (Confirm +
                # Export + Champion-Store gelaufen). Ihr IS-Trial-Baum wird ab hier nicht mehr
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
            # disposen, BEVOR die Study-Referenz aus dem Scope faellt.
            _dispose_storage(getattr(study, "_etoro_rdb_storage", None))

    # Issue #784/#814 — deflation_family_floor_mode steuert die Budget-Untergrenze innerhalb von
    # _family_n_from_studies; fail-open (leere Config) auf den #814-Default 'attempted'.
    try:
        _tournament_cfg_for_family = json.loads((config_dir() / "tournament.json").read_text("utf-8"))
    except (OSError, ValueError):
        _tournament_cfg_for_family = {}
    # Issue #826 Fix Punkt 2 — EINMAL vor jeder Optimierung aufgelöst/validiert (fail-loud VOR
    # kostspieliger Arbeit, nicht erst beim ersten Confirm-Aufruf): 'per_symbol_best' bricht den
    # Sweep hier sofort ab, siehe _resolve_promotion_family_scope-Docstring.
    _family_scope = _resolve_promotion_family_scope(_tournament_cfg_for_family)
    # Issue #827 Fix Punkt 3 — ebenfalls einmal vorab validiert (unbekannter Wert ⇒ sofortiger
    # Abbruch); die eigentliche Heterogenitäts-PRÜFUNG läuft je Symbol (erst NACH dessen Studies).
    _homogeneity_policy = _resolve_selection_rule_homogeneity_policy(_tournament_cfg_for_family)
    # Issue #828 Fix Punkt 5 — Laufzeit-Budget (analog disk_guard, #795). Fehlt der Key ⇒ 24
    # (Issue-Vorschlag, aktiver Default); EXPLIZIT null ⇒ deaktiviert (dict.get mit Default
    # greift nur bei fehlendem Key, nicht bei einem vorhandenen null-Wert). Fail-open (kaputte/
    # fehlende Config-Datei) ⇒ ebenfalls 24, NICHT deaktiviert — dieselbe Fail-safe-Haltung wie
    # disk_budget_gb/disk_reserve_gb (ein Konfigurationsfehler darf die Laufzeit-Absicherung nicht
    # lautlos abschalten).
    try:
        _sweep_max_wallclock_h = (
            json.loads((config_dir() / "optimizer.json").read_text("utf-8")) or {}
        ).get("sweep_max_wallclock_h", 24)
    except (OSError, ValueError):
        _sweep_max_wallclock_h = 24

    # Issue #839 — Fail-Fast-Preflight: statt 24 h Rechenzeit zu verbrennen, bevor eine gebrochene
    # Simulation (z. B. #836/#837-Klasse) als ungültig erkannt wird, wird eine der gelisteten
    # Invarianten bereits nach den ersten ``fail_fast_min_symbols`` abgeschlossenen Symbolen
    # geprüft. Default ``["check_holding_time_cap"]`` (die #714/GR-01-Zeitbox-Invariante) — leer
    # ⇒ deaktiviert (bit-identisch zum Pre-#839-Verhalten).
    global sweep_fail_fast_invariant
    sweep_fail_fast_invariant = None
    _fail_fast_invariants = opt_data.get("fail_fast_invariants", ["check_holding_time_cap"]) or []
    _fail_fast_min_symbols = int(opt_data.get("fail_fast_min_symbols", 2))

    # Issue #833 Fix Punkt 3 — Checkpoint EINMAL vor dem ersten Symbol geschrieben, damit
    # symbols_planned auch dann verfuegbar ist, wenn der Lauf schon im allerersten Symbol
    # abbricht (VOR dem ersten regulaeren _write_checkpoint()-Aufruf weiter unten).
    _write_checkpoint()

    proposals: list[Path] = []
    for symbol, symbol_pairs in pairs_by_symbol.items():
        if symbol in completed_symbols:
            logging.getLogger("optimizer").info(
                "[#799] %s bereits abgeschlossen (Checkpoint, run_id=%s) — übersprungen.",
                symbol, run_id,
            )
            for strategy, sym, _reason in symbol_pairs:
                _existing = WORK / f"proposal_{strategy}_{sym}.json"
                if _existing.exists():
                    proposals.append(_existing)
            continue
        # Issue #795 — zwischen zwei Symbolen geprüft (nicht nur innerhalb von _run_optimize):
        # ein zuvor gesetztes disk_guard.sweep_abort_requested lässt kein neues Symbol mehr
        # beginnen ⇒ geordnetes Sweep-Ende statt eines harten ENOSPC-Absturzes.
        if disk_guard.sweep_abort_requested.is_set():
            logging.getLogger("optimizer").warning(
                "[#795] Sweep-Abbruch angefordert (Speicherbudget überschritten) — verbleibende "
                "Symbole (ab '%s') werden nicht mehr gestartet.", symbol,
            )
            break
        # Issue #828 Fix Punkt 5 — dasselbe Muster für das Laufzeit-Budget: kein hartes
        # ENOSPC-Äquivalent, aber ein 62-h-Lauf ohne Obergrenze ist operativ nicht steuerbar.
        # Laufende Studies werden NICHT abgebrochen, nur keine neuen mehr gestartet.
        if wallclock_guard.check_wallclock_budget(
            time.perf_counter() - sweep_t0, max_hours=_sweep_max_wallclock_h,
        ):
            wallclock_guard.sweep_wallclock_exceeded.set()
            logging.getLogger("optimizer").warning(
                "[#828] Laufzeit-Budget überschritten (sweep_max_wallclock_h=%s) — verbleibende "
                "Symbole (ab '%s') werden nicht mehr gestartet.", _sweep_max_wallclock_h, symbol,
            )
            break

        if n_jobs and n_jobs > 1 and len(symbol_pairs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            # Issue #828 Fix Punkt 3 — max_workers ist NICHT mehr auf len(symbol_pairs) gedeckelt
            # (vorher `min(n_jobs, len(symbol_pairs))`: bei 14 Strategien/Symbol und n_jobs=22
            # blieben 8 der 22 konfigurierten Worker über den GESAMTEN Lauf hinweg ungenutzt, egal
            # wie n_jobs gesetzt war). Die Deckelung war nie die eigentliche Bremse — solange der
            # Dispatch pro Symbol synchron bleibt (Fix Punkt 1, Pipelining über Symbolgrenzen
            # hinweg, ist NICHT Teil dieses Fixes — siehe Docstring von run_per_symbol_sweep),
            # nutzt ein Pool dieser einen Symbol-Batch ohnehin nie mehr als len(symbol_pairs)
            # Worker gleichzeitig; die explizite Entkopplung verhindert nur, dass die Deckelung
            # unbemerkt wiederkehrt, sobald Pipelining nachgerüstet wird.
            with ThreadPoolExecutor(max_workers=n_jobs) as executor:
                symbol_studies = list(executor.map(_run_optimize, symbol_pairs))
        else:
            symbol_studies = [_run_optimize(p) for p in symbol_pairs]

        # Issue #827 Fix Punkt 3 — 'fail': dieses Symbol bricht VOR jedem Confirm-Aufruf ab, wenn
        # seine Studies nachweislich unterschiedliche selection_rule_fingerprint tragen (verletzte
        # DSR-Homogenitäts-Voraussetzung, Pitfall #248/#812). Deterministische, config-/code-
        # bedingte Bedingung (kein transienter Fehler) — completed_symbols wird trotzdem gesetzt,
        # damit ein Checkpoint-Resume nicht endlos denselben Abbruch wiederholt.
        if _homogeneity_policy == "fail":
            symbol_fingerprints_for_abort = _family_fingerprints_from_studies(
                symbol_pairs, symbol_studies, tournament_cfg=_tournament_cfg_for_family)
            n_fingerprints = len(symbol_fingerprints_for_abort.get(symbol, set()))
            if n_fingerprints > 1:
                logging.getLogger("optimizer").error(
                    "[#827] SYMBOL_ABORTED_ON_SELECTION_HETEROGENEITY: %s trägt %d verschiedene "
                    "selection_rule_fingerprint über seine Strategien-Studies "
                    "(selection_rule_homogeneity_policy='fail') — kein Proposal für irgendeine "
                    "Strategie dieses Symbols.",
                    symbol, n_fingerprints,
                )
                emit_execution_event(logging.getLogger("optimizer"),
                    "SYMBOL_ABORTED_ON_SELECTION_HETEROGENEITY",
                    {"symbol": symbol, "n_fingerprints": n_fingerprints}, level=logging.ERROR)
                completed_symbols.add(symbol)
                _write_checkpoint()
                continue

        # Issue #652 — familienweite Multiplizität NUR über die Studies DIESES Symbols (alle
        # Strategien dieses Symbols sind jetzt abgeschlossen); #695 liefert dieselbe Grundlage als
        # Return-Matrix für die Korrelations-Declusterung in confirm.py.
        symbol_n_family = _family_n_from_studies(
            symbol_pairs, symbol_studies, tournament_cfg=_tournament_cfg_for_family)
        symbol_family_returns = _family_period_returns_from_studies(symbol_pairs, symbol_studies)
        # Issue #822 — Aufschlüsselung, wie viele oos_evaluated Trials je Symbol AUS der obigen
        # Zählung ausgeschlossen wurden (keine Selektions-Teststatistik), nach Grund.
        symbol_n_family_excluded = _family_n_excluded_breakdown_from_studies(symbol_pairs, symbol_studies)
        # Issue #826 Fix Punkt 1 — N1 JE STUDY (nicht symbolweit summiert): die tatsächlich an
        # confirm(...) durchgereichte Multiplizität unter promotion_family_scope='per_strategy'.
        symbol_n_family_stage1 = _family_n_stage1_from_studies(
            symbol_pairs, symbol_studies, tournament_cfg=_tournament_cfg_for_family)

        for pair, study in zip(symbol_pairs, symbol_studies):
            proposal = _run_confirm_and_export(pair, study, symbol_n_family, symbol_family_returns,
                                               symbol_n_family_excluded,
                                               n_family_stage1_map=symbol_n_family_stage1,
                                               family_scope=_family_scope)
            if proposal is not None:
                proposals.append(proposal)

        completed_symbols.add(symbol)
        _write_checkpoint()

        # Issue #839 — Fail-Fast-Preflight-Probe: EINMAL, sobald genug Symbole für eine belastbare
        # Aussage abgeschlossen sind. Reine Lesefunktion (``report._build_report`` schreibt nichts
        # auf die Platte) über die bereits im Speicher gesammelten Proposal-Pfade dieses Laufs —
        # kein zusätzlicher Backtest, keine Doppelarbeit.
        if (using_real_optimize and _fail_fast_invariants
                and len(completed_symbols) >= _fail_fast_min_symbols
                and sweep_fail_fast_invariant is None):
            try:
                from automation.optimizer import report as _report_probe_mod
                _probe_report = _report_probe_mod._build_report(
                    proposals, run_id=run_id, started_at_utc=None, wallclock_s=None, cli_args=None)
                sweep_fail_fast_invariant = _first_failing_fail_fast_invariant(
                    _probe_report.get("invariant_checks"), _fail_fast_invariants)
            except Exception:
                logging.getLogger("optimizer").warning(
                    "[#839] Fail-Fast-Invarianten-Probe fehlgeschlagen (non-fatal, Lauf setzt "
                    "fort).", exc_info=True,
                )
            if sweep_fail_fast_invariant is not None:
                logging.getLogger("optimizer").error(
                    "[#839] FAIL_FAST_INVARIANT: %s FAILt nach %d Symbol(en) — Sweep bricht sofort "
                    "ab (run_status=aborted_invariant), statt nach allen Symbolen spät zu "
                    "scheitern.", sweep_fail_fast_invariant, len(completed_symbols),
                )
                emit_execution_event(
                    logging.getLogger("optimizer"), "SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT",
                    {"check": sweep_fail_fast_invariant, "symbols_completed": len(completed_symbols)},
                    level=logging.ERROR,
                )
                break

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
    # Issue #773/#833 — EINE global-Deklaration fuer die GESAMTE Funktion (Python verbietet
    # mehrere `global`-Statements fuer denselben Namen in verschiedenen Zweigen EINER Funktion,
    # wenn dazwischen bereits zugewiesen wurde — "name is assigned to before global declaration").
    global _LAST_REPORT_PATH
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
    # Issue #833 Fix Punkt 4 — rekonstruiert den #742-Report aus den bereits auf der Platte
    # liegenden Proposals/Studies eines FRÜHEREN (ggf. abgebrochenen) Laufs, OHNE selbst zu
    # optimieren. Nutzt exakt denselben Kern (report.generate_report_for_run) wie der Abbruch-Pfad
    # unten — genau der Fall "ein Lauf endete ohne Report" (siehe Katalog-#750-Nachtrag).
    parser.add_argument("--report-only", metavar="RUN_ID", default=None,
                        help="Erzeugt/rekonstruiert nur den Report fuer RUN_ID aus den vorhandenen "
                             "proposal_*.json (keine neue Optimierung).")
    args = parser.parse_args(argv)

    if args.report_only:
        from automation.optimizer import report as _report
        report_path = _report.generate_report_for_run(run_id=args.report_only)
        print(f"📄 Report: {report_path}")
        _LAST_REPORT_PATH = report_path
        return []

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

    # Issue #799 — derselbe run_id treibt sowohl das Logging (oben) als auch den Sweep-Fortschritts-
    # Checkpoint; ein Neustart mit demselben run_id (z. B. ein externes Resume-Tooling, das den
    # letzten Checkpoint ausliest und run_id erneut übergibt) überspringt bereits abgeschlossene
    # Symbole. Ohne einen solchen expliziten Wiederaufruf erzeugt jeder main()-Aufruf einen neuen
    # run_id ⇒ frischer Checkpoint (unverändertes Verhalten für den Normalfall).
    #
    # Issue #833 Fix Punkt 3 — ein Abbruch (disk_guard/wallclock_guard laufen bereits GRACEFUL,
    # via `break`; SIGINT/unerwartete Exceptions propagierten bisher UNGEFANGEN aus main() heraus,
    # BEVOR der #742-Report-Block weiter unten je erreicht wurde) erzeugt seither TROTZDEM ein
    # Report-Artefakt — aus genau den Proposals, die bereits auf der Platte liegen (dieselbe
    # Quelle, die auch ``--report-only``/``generate_report_for_run`` nutzt). SIGTERM wird auf
    # denselben KeyboardInterrupt-Pfad wie SIGINT umgeleitet (Python behandelt SIGINT bereits
    # nativ als KeyboardInterrupt; SIGTERMs Default waere ein sofortiger, unkatalogisierter Exit).
    import signal as _signal

    def _sigterm_to_keyboard_interrupt(signum, frame):
        raise KeyboardInterrupt("SIGTERM")

    run_status = "complete"
    caught_exc: BaseException | None = None
    proposals: list[Path] = []
    _prior_sigterm_handler = _signal.signal(_signal.SIGTERM, _sigterm_to_keyboard_interrupt)
    try:
        proposals = run_per_symbol_sweep(strategies, symbols, tier=args.tier, n_jobs=eff_n_jobs,
                                         n_jobs_source=n_jobs_source, run_id=run_id)
        if sweep_fail_fast_invariant is not None:
            run_status = "aborted_invariant"
        elif wallclock_guard.sweep_wallclock_exceeded.is_set():
            run_status = "aborted_wallclock"
        elif disk_guard.sweep_abort_requested.is_set():
            run_status = "aborted_disk"
    except KeyboardInterrupt as e:
        run_status = "aborted_signal"
        caught_exc = e
        logging.getLogger("optimizer").warning(
            "[#833] Sweep durch SIGINT/SIGTERM unterbrochen — erzeuge Teil-Report aus den bislang "
            "exportierten Proposals.")
    except Exception as e:
        run_status = "aborted_error"
        caught_exc = e
        logging.getLogger("optimizer").error(
            "[#833] Sweep durch eine unerwartete Exception abgebrochen — erzeuge Teil-Report aus "
            "den bislang exportierten Proposals.", exc_info=True)
    finally:
        _signal.signal(_signal.SIGTERM, _prior_sigterm_handler)

    for p in proposals:
        print(p)

    # Issue #833 Fix Punkt 3 — symbols_completed/symbols_planned aus dem #799-Checkpoint (die
    # Enumeration wird NICHT ein zweites Mal ausgefuehrt); fail-open (None/None), falls der
    # Checkpoint fehlt/kaputt ist oder der Sweep VOR dessen erstem Schreiben abbrach.
    symbols_completed: int | None = None
    symbols_planned: int | None = None
    try:
        _checkpoint = json.loads((WORK / "sweep_progress.json").read_text("utf-8"))
        if _checkpoint.get("run_id") == run_id:
            symbols_completed = len(_checkpoint.get("completed_symbols") or [])
            symbols_planned = _checkpoint.get("symbols_planned")
    except (OSError, ValueError):
        pass

    # Issue #742 — EIN aggregiertes Report-Artefakt am Ende des Laufs, atomar geschrieben. Darf den
    # Sweep NIE crashen (non-fatal, analog Champion-Store/Retention/Backfill an anderer Stelle).
    try:
        from automation.optimizer import report as _report
        _cli_args = {"strategies": args.strategies, "tier": args.tier, "symbols": args.symbols,
                    # Issue #755 — n_workers je Lauf im Report nachvollziehbar (Determinismus-Nachweis
                    # bei n_jobs>1, jetzt auch bei gesetztem Seed zulaessig).
                    "n_jobs": eff_n_jobs, "n_jobs_source": n_jobs_source}
        _wallclock_s = round(time.perf_counter() - main_t0)
        if run_status == "complete":
            report_path = _report.generate_sweep_report(
                proposals, run_id=run_id, started_at_utc=started_at_utc,
                wallclock_s=_wallclock_s, cli_args=_cli_args, run_status=run_status,
                symbols_completed=symbols_completed, symbols_planned=symbols_planned,
            )
        else:
            # Issue #833 — die IN-MEMORY proposals-Liste kann bei einer Exception mitten im
            # Symbol-Loop unvollstaendig/veraltet sein (die Exception verliess run_per_symbol_
            # sweep, BEVOR es zurueckkehren konnte); generate_report_for_run entdeckt stattdessen
            # ALLE proposal_*.json, die tatsaechlich auf der Platte liegen (dieselbe Quelle wie
            # --report-only), unabhaengig davon, wo genau der Abbruch stattfand.
            report_path = _report.generate_report_for_run(
                run_id=run_id, started_at_utc=started_at_utc,
                wallclock_s=_wallclock_s, cli_args=_cli_args, run_status=run_status,
                symbols_completed=symbols_completed, symbols_planned=symbols_planned,
            )
        print(f"📄 Report: {report_path}")
        # Issue #773 — der Report war bislang rein informativ; der CLI-Entrypoint (__main__ unten)
        # liest diesen Pfad, um bei mindestens einem FAIL-Invarianten-Check einen Non-Zero-Exit-Code
        # zurueckzugeben, statt eines rein informativen Artefakts.
        _LAST_REPORT_PATH = report_path
        # Issue #832 Fix Punkt 2 — direkt NACH generate_sweep_report/generate_report_for_run,
        # fail-open: liest AUSSCHLIESSLICH das gerade geschriebene Report-JSON (summary_de.py
        # nimmt keine zweite Datenquelle) und erbt damit automatisch dieselbe #833-Abbruchfestigkeit
        # (ein Teilreport erzeugt trotzdem eine — kleinere — Zusammenfassung).
        from automation.optimizer import summary_de as _summary_de
        summary_path = _summary_de.write_german_summary_for_report_path(report_path)
        if summary_path is not None:
            print(f"📝 Zusammenfassung: {summary_path}")
    except Exception:
        logging.getLogger("optimizer").warning(
            "[#742] Sweep-Report-Generierung fehlgeschlagen (non-fatal).", exc_info=True)

    # Issue #833 — der urspruengliche Abbruch (SIGINT/SIGTERM/unerwartete Exception) wird nach dem
    # Report-Versuch WEITERGEREICHT: das Artefakt ist ein Nebeneffekt auf dem Weg nach draussen,
    # kein stilles Verschlucken des eigentlichen Fehlers (Exit-Code/Traceback bleiben sichtbar).
    if caught_exc is not None:
        raise caught_exc

    return proposals


# Issue #773 — siehe Kommentar in main() oben.
_LAST_REPORT_PATH: "Path | None" = None


def _first_failing_fail_fast_invariant(invariant_checks: list[dict], fail_fast_invariants: list[str]) -> str | None:
    """Issue #839 — reine Entscheidungsfunktion: welcher (falls einer) der in
    ``optimizer.json['fail_fast_invariants']`` gelisteten Check-Namen in ``invariant_checks``
    (z. B. aus ``report._build_report(...)['invariant_checks']``) FAILt. ``None`` ⇒ keiner der
    gelisteten Checks ist verletzt (der Sweep läuft weiter)."""
    for chk in invariant_checks or []:
        if chk.get("name") in fail_fast_invariants and not chk.get("passed", True):
            return chk.get("name")
    return None


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
