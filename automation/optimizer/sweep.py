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
import fcntl
import json
import logging
import os
import statistics
import time
from pathlib import Path

import datetime as dt

from automation.optimizer import bounds
from automation.optimizer._contracts import pair_key, split_pair_key
from automation.optimizer.gate import (
    is_symbol_tunable, data_reaches_oos_window, data_reaches_holdout_window, required_span_days,
)
from automation.optimizer.trial_config import config_dir, compute_walk_forward_window
from automation.optimizer.manifest import WORK, write_json_atomic, catalog_fingerprint, library_versions
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
from automation.optimizer import symbol_coverage
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

# Issue #941/#1107 (Katalog #960) — die ``invariant_checks``-Liste der #839-Fail-Fast-Probe (sofern
# in diesem Lauf eine stattfand), analog ``sweep_fail_fast_invariant`` gesetzt. ``main()`` reicht sie
# an den FINALEN ``generate_sweep_report``-Aufruf durch, damit
# ``invariants.check_cohort_declaration_consistency`` die In-Process-Kohorte der Probe gegen die
# Report-Scan-Kohorte desselben Laufs vergleichen kann (Root-Cause #1107: beide Pfade meldeten
# denselben Check-Namen mit zwei verschiedenen, undeklarierten Grundgesamtheiten).
sweep_fail_fast_probe_invariant_checks: list[dict] | None = None

# Issue #877 — (Strategie, Symbol)-Paare, die WAEHREND dieses Laufs wegen eines Fail-Fast-Offenders
# mit ZU GERINGER Symbol-Streuung quarantänisiert (statt den Sweep abzubrechen) wurden. main() liest
# dies, um run_status='completed_with_quarantine' von 'complete' zu unterscheiden.
sweep_fail_fast_quarantined_pairs: list[tuple[str, str]] = []

# Issue #939 (Katalog A, Pitfall #299) — Symbole, deren Confirm/Export-Abschnitt WAEHREND dieses
# Laufs mit einer isolierten Exception fehlschlug (SYMBOL_FAILED), statt den gesamten Sweep zu
# beenden. main() liest dies, um run_status='completed_with_failures' von 'complete' zu
# unterscheiden, analog sweep_fail_fast_quarantined_pairs.
sweep_failed_symbols: list[str] = []

# Issue #942/#1108 (Katalog #960) — In-Prozess-Spiegel DERSELBEN Werte, die ``_write_checkpoint()``
# (innerhalb ``run_per_symbol_sweep``) auf die Platte schreibt (``sweep_progress.json``), bei JEDEM
# Checkpoint-Schreiben aktualisiert. Root-Cause #1108: ``main()``s Symbol-Zaehler kamen bislang
# AUSSCHLIESSLICH aus einem erneuten Lesen dieser Checkpoint-DATEI — ein stiller Fehlschlag dieses
# Lesevorgangs (Datei fehlt/kaputt, ODER — im geteilten-Store-Betrieb vor #944 — ein
# ``run_id``-Mismatch, weil ein ANDERER gleichzeitiger Prozess zuletzt geschrieben hat) liess
# ``symbols_completed``/``symbols_planned``/``symbols_discovered``/``symbols_gate1_rejected`` auf
# ``None`` fallen, obwohl die Arbeit tatsaechlich vollstaendig war (Report C: 14/14 Studies, aber
# alle vier Zaehler ``null``). Dieser In-Prozess-Spiegel ist von Datei-I/O und fremden Prozessen
# unabhaengig — ``main()`` bevorzugt ihn gegenueber der Checkpoint-Datei, sofern er fuer DIESEN
# ``run_id`` gesetzt wurde (die Datei bleibt der einzige Weg fuer ``--report-only``/eine
# standalone Rekonstruktion, die ``run_per_symbol_sweep`` nie im eigenen Prozess ausfuehrte).
sweep_symbol_funnel: dict | None = None


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


def assert_required_config_keys_valid() -> None:
    """Issue #844 (Pitfall #267, 5. Wiederkehr) — FAIL-LOUD beim Sweep-Start: jeder in
    ``automation/config/_required_keys.json`` als ``required`` markierte Key muss in seiner
    Config-Datei existieren, darf nicht ``null`` sein und keinen ``reject_values``-Eintrag tragen.
    ``sortino_numeric_guard_min_periods`` (#665/#823) blieb trotz eines dokumentierten Fixes NIE
    tatsächlich gesetzt — dieser Preflight verhindert, dass ein registrierter Key erneut still
    fehlen kann, statt den Defekt erst nach einem vollen Sweep-Lauf zu bemerken. Exit-Code 2
    (fail-loud), analog ``main()``'s ``--resume``-Validierung. Fehlt ``_required_keys.json``
    selbst ⇒ No-Op (die Registry ist opt-in additiv, kein Pflichtartefakt)."""
    spec_path = config_dir() / "_required_keys.json"
    if not spec_path.exists():
        return
    try:
        spec = json.loads(spec_path.read_text("utf-8")) or {}
    except (OSError, ValueError) as e:
        raise SystemExit(f"❌ [#844] {spec_path} nicht lesbar: {e} — Exit-Code 2.") from e

    configs: dict[str, dict] = {}
    for filename in spec:
        if filename in ("schema_version", "_comment"):
            continue
        cfg_path = config_dir() / filename
        try:
            configs[filename] = json.loads(cfg_path.read_text("utf-8")) or {} if cfg_path.exists() else {}
        except (OSError, ValueError):
            configs[filename] = {}

    from automation.optimizer import invariants as _inv
    result = _inv.check_required_config_keys(configs, spec)
    if not result.passed:
        import sys as _sys
        print(f"❌ [#844] REQUIRED_CONFIG_KEYS_VIOLATION: {result.detail}", file=_sys.stderr)
        _sys.exit(2)


def assert_pinned_library_versions_valid() -> None:
    """Issue #852 (P2) — FAIL-LOUD beim Sweep-Start, gemeinsam mit ``assert_required_config_keys_
    valid`` (#844): jede installierte Bibliotheksversion, die in ``optimizer.json[
    'pinned_library_versions']`` gepinnt ist, muss innerhalb ihres Bereichs (``requirements.txt``-
    Format, z. B. ``'>=4.9,<5.0'``) liegen. Root-Cause: ``optuna`` stand ohne jede Versionsangabe
    in ``requirements.txt``, obwohl derselbe Kommentar dort (#802) erklärt, warum das für ``pandas``
    inakzeptabel war — der numerische Ausgang der Selektion (TPESampler-Defaults,
    ``constraints_func``-Interaktion, ``TrialState.PRUNED``-Semantik) hängt sonst an der
    Installationsumgebung statt der Konfiguration. Exit-Code 2 (fail-loud), analog
    ``assert_pandas_version_supported``. Fehlt der Config-Key ⇒ No-Op (leeres Pin-Dict, kein
    Drift-Check aktiv — rückwärtskompatibel)."""
    from automation.optimizer import invariants as _inv
    opt_data = _load_optimizer_config()
    pinned = opt_data.get("pinned_library_versions") or {}
    if not pinned:
        return
    result = _inv.check_library_version_drift(library_versions(), pinned)
    if not result.passed:
        import sys as _sys
        print(f"❌ [#852] LIBRARY_VERSION_DRIFT: {result.detail}", file=_sys.stderr)
        _sys.exit(2)


def assert_instrument_metadata_coherence() -> None:
    """Issue #920 — FAIL-LOUD beim Sweep-Start: ``instrument_map.json`` gegen sich selbst geprüft
    (``invariants.check_instrument_metadata_coherence``, Pitfall #298) — ein Instrument mit
    ``size_precision=8`` und ``asset_class='equity'`` (der #920-Defekt: 12 Krypto-Symbole lösten
    seit dem #898-Backfill auf den falschen, um Faktor 4 zu niedrigen Spread auf) bricht den
    Sweep-Start ab, statt 143 Symbole lang unbemerkt zu falschen simulierten Fill-Preisen zu
    führen. Exit-Code 2, analog ``assert_pinned_library_versions_valid``."""
    from automation.optimizer import invariants as _inv
    instrument_map_path = config_dir() / "instrument_map.json"
    backtest_path = config_dir() / "backtest.json"
    try:
        instruments = (json.loads(instrument_map_path.read_text("utf-8")) or {}).get(
            "instruments", {}) if instrument_map_path.exists() else {}
    except (OSError, ValueError):
        instruments = {}
    if not instruments:
        return
    spread_by_asset_class = None
    try:
        if backtest_path.exists():
            spread_by_asset_class = (json.loads(backtest_path.read_text("utf-8")) or {}).get(
                "spread_bps_by_asset_class")
    except (OSError, ValueError):
        spread_by_asset_class = None
    result = _inv.check_instrument_metadata_coherence(
        instruments, spread_bps_by_asset_class=spread_by_asset_class)
    if not result.passed:
        import sys as _sys
        print(f"❌ [#920] INSTRUMENT_METADATA_INCOHERENT: {result.detail}", file=_sys.stderr)
        _sys.exit(2)


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
        # Issue #900 Fix 1 — median_delta_t_s (Sekunden zwischen aufeinanderfolgenden BESETZTEN
        # Bars, nicht der Kalenderraster-Default) und bar_coverage_ratio (besetzte Stunden /
        # Kalenderstunden der Stichprobe) — beide Rohmaterial fuer die #900-Preflight-Kennzahlen
        # und fuer #902s bar_seconds-Aufloesung.
        idx = bars.index
        if len(idx) > 1:
            deltas_s = idx.to_series().diff().dropna().dt.total_seconds()
            median_delta_t_s = float(deltas_s.median()) if not deltas_s.empty else 3600.0
        else:
            median_delta_t_s = 3600.0
        calendar_hours = max(1.0, (idx[-1] - idx[0]).total_seconds() / 3600.0)
        bar_coverage_ratio = len(idx) / calendar_hours
        return {
            "highs": bars["max"].tolist(),
            "lows": bars["min"].tolist(),
            "closes": bars["last"].tolist(),
            "median_delta_t_s": median_delta_t_s,
            "bar_coverage_ratio": bar_coverage_ratio,
            # Issue #900 Fix 4 — Stichprobenumfang und Fenster im Event, sonst ist die Aussage
            # nicht reproduzierbar.
            "n_sample_ticks": int(len(df)),
            "window_start": idx[0].isoformat(),
            "window_end": idx[-1].isoformat(),
        }
    except Exception:
        return None


def _symbol_bar_quality_cache_path(work_dir: Path) -> Path:
    return Path(work_dir) / "symbol_bar_quality.json"


def write_symbol_bar_quality_cache(work_dir: Path, quality_by_symbol: dict[str, dict]) -> Path:
    """Issue #923 Fix 1/3 — persistiert die #900-Bar-Qualitäts-Kennzahlen (``frac_zero_true_range``,
    ``atr_median_bps``, ``bar_coverage_ratio``, ``median_delta_t_s``) je Symbol, GETRENNT von
    ``optimizer.json`` (analog ``wallclock_guard.write_degrade_factor``, #931). Der Gate-1-Preflight
    in ``run_per_symbol_sweep`` berechnet diese Werte ohnehin einmal pro Symbol VOR Phase 1
    (``_load_symbol_bar_quality_sample``) — die Datei macht sie für ``report._study_record``
    verfügbar, das NACH dem Sweep aus gespeicherten Study-Artefakten läuft und keinen eigenen
    Katalog-Zugriff hat (#902-Fallback-Kommentar in report.py)."""
    path = _symbol_bar_quality_cache_path(work_dir)
    write_json_atomic(path, quality_by_symbol)
    return path


def read_symbol_bar_quality_cache(work_dir: Path) -> dict[str, dict]:
    """Gegenstück zu ``write_symbol_bar_quality_cache``. Fehlt die Datei (kein Gate-1-Preflight in
    diesem Lauf, z. B. injizierte Tests ohne echten Katalog) ⇒ {} (fail-open, rückwärtskompatibel —
    Aufrufer fallen auf ``_contracts.BAR_SECONDS_DEFAULT`` zurück)."""
    path = _symbol_bar_quality_cache_path(work_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


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


def earliest_ts_by_symbol(symbols, *, catalog_path: Path | None = None) -> dict[str, int | None]:
    """Issue #909 (Pitfall #291) — ältester ``ts_event`` (Epoch-ns) je Symbol aus den Parquet-Row-
    Group-Statistiken, symmetrisch zu ``latest_ts_by_symbol`` (dieselbe I/O-Struktur, ``.min`` statt
    ``.max``-Statistik).

    Root-Cause #909: der ``[#624]``-Preflight verwechselte die Achse — ``min(latest_ts_by_symbol(...)
    .values())`` ist das Minimum über die JÜNGSTEN Zeitpunkte je Symbol (wie eng die Enddaten über
    die Symbole streuen), nicht der älteste Datenpunkt irgendeines Symbols (dessen Historienlänge).
    Auf einem gut gepflegten Katalog, dessen Symbole alle bis heute reichen, konvergiert dieser Wert
    gegen 0 — der Alarm wird lauter, je gesünder der Katalog ist. Diese Funktion liefert die fehlende
    Hälfte, damit die Spanne je Symbol (``latest - earliest``, DIESELBE Achse) berechenbar wird."""
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
        oldest: int | None = None
        pq_file = Path(catalog_path) / "data" / "quote_tick" / sym / "data.parquet"
        if pq_file.exists():
            try:
                import pyarrow.parquet as pq
                pf = pq.ParquetFile(str(pq_file))
                if "ts_event" in pf.schema.names:
                    idx = pf.schema.names.index("ts_event")
                    for rg in range(pf.metadata.num_row_groups):
                        st = pf.metadata.row_group(rg).column(idx).statistics
                        lo = int(st.min)
                        oldest = lo if oldest is None else min(oldest, lo)
            except Exception:
                oldest = None
        out[sym] = oldest
    return out


def per_symbol_span_stats(latest_ts: dict[str, int | None], earliest_ts: dict[str, int | None],
                          symbols, *, required_span_days: float | None = None) -> dict:
    """Issue #909 (Pitfall #291) — reine Aggregationsfunktion: Katalog-Spanne (Tage) JE SYMBOL
    (``latest - earliest``, dieselbe Achse), dann Minimum/Median/``n_symbols_below_required`` über
    die Symbole. Ein aggregierter Diagnosewert über heterogene Entitäten (min/max über eine Menge
    von ENDZEITPUNKTEN, wie die alte Berechnung es tat) misst die STREUUNG, nicht die Grösse —
    Aggregate über Symbole brauchen die Achse im Namen (hier: die Spanne, nicht der Zeitpunkt).

    Symbole ohne beide Zeitstempel (fehlende Datei/Lesefehler) tragen keine Spanne bei (fail-open,
    dieselbe Semantik wie ``latest_ts_by_symbol``/``earliest_ts_by_symbol``)."""
    spans = [
        (latest_ts[s] - earliest_ts[s]) / 1e9 / 86400.0
        for s in symbols
        if latest_ts.get(s) is not None and earliest_ts.get(s) is not None
    ]
    if not spans:
        return {"min_span_days": None, "median_span_days": None, "n_symbols_below_required": None}
    n_below = (
        sum(1 for d in spans if d < required_span_days) if required_span_days is not None else None
    )
    return {
        "min_span_days": min(spans),
        "median_span_days": statistics.median(spans),
        "n_symbols_below_required": n_below,
    }


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
                            logger: logging.Logger | None = None,
                            gate1_rejected_symbols: set[str] | None = None,
                            stale_symbols: set[str] | None = None,
                            ) -> list[tuple[str, str, str]]:
    """Enumeriert (strategy, symbol, 'OK')-Tripel.

    1. Symbol-Liste = ``symbols`` or ``load_symbol_universe()``.
    2. Tier: 'deployable' (nur Tier-A-Gewinner pro Strategie, PLUS ``stale_symbols`` — siehe unten),
       'refine' (Platzhalter, P3), 'all' (Kreuzprodukt strategies × Symbole).
    3. Gate 1: ``is_symbol_tunable(...)`` muss True sein.
    4. Issue #455 — OOS-Erreichbarkeits-Preflight: Erreicht der jüngste Tick eines Symbols
       (``latest_ts[symbol]``) die früheste OOS-Grenze (``oos_window_start_ns``) NICHT, wird das
       Symbol mit ``OOS_WINDOW_UNREACHABLE`` + WARN-Zeile übersprungen — VOR dem Sweep, statt 100
       strukturell nutzlose Trials zu fahren (Pitfall #82). **Vollständig fail-open**: fehlen
       ``latest_ts`` oder ``oos_window_start_ns`` (Default ``None``), bleibt das Verhalten aus und
       das Verhalten ist bit-identisch zum Ist-Zustand (beide Symbole behalten).
    Ausgeschlossene Paare sind NICHT enthalten.

    Issue #892 Fix Punkt 5 — ``gate1_rejected_symbols`` (optionales Output-Set, IN-PLACE befüllt,
    Default ``None`` ⇒ bit-identisch) sammelt jedes Symbol, für das MINDESTENS ein
    (Strategie, Symbol)-Paar an Gate 1 (``INSUFFICIENT_HISTORY``) scheiterte. Der Aufrufer
    (``run_per_symbol_sweep``) markiert damit ein Symbol, dessen Paare AUSSCHLIESSLICH an Gate 1
    scheitern (kein einziges ``'OK'``-Paar in der Rückgabe), im Coverage-Ledger als
    ``covered_gate1`` (``symbol_coverage.mark_gate1_covered``) — es wurde nachweislich JEDEN Lauf
    geprüft, nur strukturell nie optimiert, und darf ``check_symbol_coverage`` nicht dauerhaft rot
    färben.

    Issue #1040 (Katalog #866) — Root-Cause der stehenden Symbolrotation: unter ``tier='deployable'``
    (dem CLI-Default) war ``candidate_syms`` bislang HART auf ``winners`` (bereits gewonnene Tier-A-
    Paare, ``load_tier_a_winners()``) beschraenkt — ein Symbol OHNE existierenden Tier-A-Gewinn hatte
    in DIESEM Modus buchstaeblich NIE ein einziges Paar in ``pairs``, unabhaengig davon, wie ``stale``
    (``symbol_coverage.coverage_report``) es war. ``symbol_coverage.order_symbols`` (die eigentliche
    Rotationslogik, ``least_recently_covered``) sortiert nur die Symbole, die es ueberhaupt zu sehen
    bekommt — eine Kandidatenmenge, die strukturell auf ~15 wiederkehrende "Champion"-Symbole
    verengt ist, bevor die Rotation ueberhaupt greift, macht die Rotation wirkungslos (beobachtet:
    dieselben 8-10 Symbole in vier aufeinanderfolgenden Laeufen). ``stale_symbols`` (optional, Default
    ``None`` ⇒ bit-identisches Alt-Verhalten) sind Symbole aus ``symbol_coverage.coverage_report()``
    ueber der ``symbol_coverage_max_age_runs``-Schwelle (dieselbe Schwelle wie ``check_symbol_
    coverage`` — EINE Schwelle statt einer zweiten, potenziell abweichenden Konfigurations-Kopie,
    analog der #1027-Lektion "eine Kennzahl, eine Quelle") — sie werden unter ``tier='deployable'``
    ZUSAETZLICH zu ``winners`` zugelassen, damit ein noch nie (oder lange nicht mehr) optimiertes
    Symbol ueberhaupt eine Chance auf einen Tier-A-Gewinn bekommt, statt dauerhaft von der
    Kandidatenmenge ausgeschlossen zu bleiben."""
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

    # Issue #942 (Katalog A) — INSUFFICIENT_HISTORY hängt NUR von available_bars/config['walk_forward']
    # ab (gate.is_symbol_tunable Zweig (a)), NICHT von n_params/strategy — für ein datenknappes
    # Symbol ist das Ergebnis über ALLE Strategien identisch. Ohne dieses Cache-Set feuerte
    # GATE_1_REJECTION bis zu len(strategies)-mal (z. B. 14x) für dasselbe Symbol/denselben Grund.
    # PARAM_DATA_RATIO_TOO_LOW/OOS_FOLD_TOO_SHORT bleiben unberührt (n_params-abhängig, echte
    # Pro-Strategie-Aussagen).
    _gate1_history_rejection_emitted: set[str] = set()

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
            # Issue #1040 — ``stale_symbols`` (Symbole ueber der Coverage-Alters-Schwelle) sind
            # ZUSAETZLICH zugelassen, nicht nur Tier-A-Gewinner: ohne diese Erweiterung kann ein
            # Symbol ohne bestehenden Gewinn den 'deployable'-Kandidatenpool NIE betreten, egal wie
            # veraltet seine Coverage ist — die Rotation (order_symbols) sortiert dann nur innerhalb
            # derselben ~15 wiederkehrenden Symbole (siehe Docstring oben).
            candidate_syms = [s for s in syms if s in allowed or (stale_symbols and s in stale_symbols)]
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
                    # Issue #942 — ein Event je Symbol (nicht je (Strategie, Symbol)-Paar): der
                    # Grund ist strategie-unabhängig, siehe Docstring des Cache-Sets oben.
                    if symbol not in _gate1_history_rejection_emitted:
                        emit_gate1_rejection(
                            log,
                            available_days=available_bars.get(symbol, 0) / 24.0,
                            required_days=required_span_days(config.get("walk_forward") or {}),
                            symbol=symbol,
                        )
                        _gate1_history_rejection_emitted.add(symbol)
                    if gate1_rejected_symbols is not None:
                        gate1_rejected_symbols.add(symbol)
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


def _read_last_backtest_ms_median() -> float | None:
    """Issue #931 — Erfahrungswert für ``backtest_ms_median`` aus dem JÜNGSTEN vorhandenen
    #742-Report (``report._study_record`` stempelt ihn je Study, siehe dortige #931-Ergänzung),
    als Median über die Studien-Mediane. ``None`` bei keinem vorhandenen Report (erster Lauf) —
    der Aufrufer fällt dann auf ``wallclock_guard.DEFAULT_BACKTEST_MS_MEDIAN`` zurück."""
    try:
        from automation.optimizer.report import REPORTS_DIR
        if not REPORTS_DIR.exists():
            return None
        report_files = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not report_files:
            return None
        data = json.loads(report_files[-1].read_text("utf-8")) or {}
        values = [
            s["backtest_ms_median"] for s in (data.get("studies") or [])
            if s.get("backtest_ms_median") is not None
        ]
        return statistics.median(values) if values else None
    except Exception:
        return None


def _read_last_backtest_ms_mean() -> float | None:
    """Issue #983 (Katalog D, P0 HEADLINE) — Erfahrungswert für den MITTELWERT von ``backtest_ms``
    aus dem JÜNGSTEN vorhandenen #742-Report (``report._study_record`` stempelt ``backtest_ms_mean``
    je Study seit #983), als gewichteter Mittelwert über die Studien-Mittelwerte (gewichtet mit
    ``n_trials``, damit eine grosse Study nicht dasselbe Gewicht wie eine kleine trägt). ``None`` bei
    keinem vorhandenen Report — der Aufrufer fällt dann auf ``wallclock_guard.
    DEFAULT_BACKTEST_MS_MEDIAN`` zurück (derselbe Fallback wie #931, konservativ auch für den
    Mittelwert-Fall, da beide Grössen dieselbe Grössenordnung tragen).

    Root-Cause #983: der reine Median (``_read_last_backtest_ms_median``) unterschätzt eine
    rechtsschiefe Backtest-Laufzeitverteilung systematisch (Referenzlauf 46cf5070: Median 7.575s vs.
    Mittelwert 9.690s, Faktor 1.279) — der Preflight muss den Mittelwert konsumieren, weil die
    HOCHRECHNUNG (Summe über viele Trials) vom Mittelwert, nicht vom Median getragen wird."""
    try:
        from automation.optimizer.report import REPORTS_DIR
        if not REPORTS_DIR.exists():
            return None
        report_files = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not report_files:
            return None
        data = json.loads(report_files[-1].read_text("utf-8")) or {}
        weighted_sum = 0.0
        weight_total = 0
        for s in (data.get("studies") or []):
            mean_val, n_trials = s.get("backtest_ms_mean"), s.get("n_trials")
            if mean_val is None or not n_trials:
                continue
            weighted_sum += float(mean_val) * int(n_trials)
            weight_total += int(n_trials)
        return (weighted_sum / weight_total) if weight_total else None
    except Exception:
        return None


def _read_last_study_wallclock_by_strategy() -> dict[str, float]:
    """Issue #932 (Pitfall #305) — Median-Wallclock je Strategie aus dem JÜNGSTEN #742-Report
    (``report._study_record`` stempelt ``wallclock_s`` je Study seit #932), Rohmaterial für den
    LPT-Dispatch (Longest-Processing-Time): die Barriere am Symbolende kostet die Differenz
    zwischen längster und medianer Study — absteigend nach erwarteter Laufzeit dispatchen senkt
    diese Wartezeit, ohne die grössere Pipelining-Restrukturierung (#843/#908/#932, weiterhin
    bewusst zurückgestellt) zu benötigen. Leeres Dict bei keinem vorhandenen Report (erster Lauf)
    — der Aufrufer behandelt eine fehlende Strategie dann als Median-Priorität (0.0)."""
    try:
        from automation.optimizer.report import REPORTS_DIR
        if not REPORTS_DIR.exists():
            return {}
        report_files = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not report_files:
            return {}
        data = json.loads(report_files[-1].read_text("utf-8")) or {}
        by_strategy: dict[str, list[float]] = {}
        for s in data.get("studies") or []:
            strat = s.get("strategy")
            wc = s.get("wallclock_s")
            if strat and wc is not None:
                by_strategy.setdefault(strat, []).append(float(wc))
        return {strat: statistics.median(vals) for strat, vals in by_strategy.items()}
    except Exception:
        return {}


def _apply_search_budget_proposal(*, work_dir: Path | None = None,
                                  run_id: str | None = None) -> list[tuple[str, str]]:
    """Issue #1082 Fix Punkt (a) (Katalog #866-2, Kohorte E) — liest ``cross_study[
    'search_budget_proposal']`` aus dem JÜNGSTEN #742-Report (``report._search_budget_proposal_
    section``, gespeist von ``check_objective_branch_coverage``-FAILs) und schreibt jedes gemeldete
    Paar als ``'deprioritized'`` in den #681/#761-Diagnose-Cache (``record_diagnosed_pair``) — der
    bestehende #830-Pfad (``run_optimization._apply_deprioritized_budget``) reduziert das Budget
    dieses Paars dann automatisch im NÄCHSTEN Lauf, statt dasselbe Budget für einen überwiegend
    zweigklippen-geführten Suchraum noch einmal zu verbrennen.

    Überschreibt NIEMALS einen bestehenden Cache-Eintrag mit einer stärkeren Konsequenz
    (``'denylist'``/``'search_space_override'``/``'quarantined_pending_simulation_review'``) — die
    Branch-Coverage-Diagnose ist additiv/beobachtend (#992-Merge-Order-Konvention), keine
    Eskalation über eine für dasselbe Paar bereits getroffene, stärkere Entscheidung. Leere Liste
    bei keinem vorhandenen Report (erster Lauf) oder leerem Vorschlag — fail-open, ein Lesefehler
    blockiert den Sweep nie. Rückgabe: die tatsächlich (neu) deprioritisierten (strategy,
    symbol)-Paare, für Log/Telemetrie."""
    try:
        from automation.optimizer.report import REPORTS_DIR
        if not REPORTS_DIR.exists():
            return []
        report_files = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not report_files:
            return []
        data = json.loads(report_files[-1].read_text("utf-8")) or {}
        proposal = ((data.get("cross_study") or {}).get("search_budget_proposal")) or []
    except Exception:
        return []
    if not proposal:
        return []
    try:
        _cache = load_diagnosed_pairs_cache(work_dir=work_dir)
    except Exception:
        _cache = {}
    applied: list[tuple[str, str]] = []
    for entry in proposal:
        strategy, symbol = entry.get("strategy"), entry.get("symbol")
        if not strategy or not symbol:
            continue
        _prior = _cache.get((strategy, symbol))
        if _prior and _prior.get("action") not in (None, "none", "deprioritized"):
            continue
        try:
            record_diagnosed_pair({
                "strategy": strategy, "symbol": symbol, "action": "deprioritized",
                "binding_cause": "objective_branch_coverage_low",
                "objective_branch_coverage_fraction": entry.get(
                    "objective_branch_coverage_fraction"),
            }, work_dir=work_dir, run_id=run_id)
            applied.append((strategy, symbol))
        except Exception:
            logging.getLogger("optimizer").debug(
                "[#1082] Suchbudget-Vorschlag-Rückschrieb für %s/%s fehlgeschlagen (non-fatal).",
                strategy, symbol, exc_info=True)
    return applied


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

    Issue #1102 (Katalog #935) — ``deflation_n_eligible`` ist eine ENGERE, seit #784/#822 veraltete
    Grundgesamtheit (nur Trials, die ALLE Eligibilitäts-Gates bestanden) als die TATSÄCHLICH an
    ``confirm.py`` für die DSR-Multiplizitätskorrektur durchgereichte Zahl (``deflation_n_family``,
    ``oos_selection_statistic_available``, #822) — beide Zahlen liefen um Faktor 2,8–5,1 auseinander
    (#1102-Symptom). ``report.py``'s ``cross_study['n_family']`` verwendet diese Funktion daher NICHT
    mehr (dort ist ``n_family[symbol]`` seither die Summe der eigenen ``n_family_stage1``-Zerlegung,
    inkl. der #1080-Rückfallbehandlung für eine Study mit 0 Holdout-Trades). Diese Funktion bleibt
    NUR noch als Rückwärtskompat-/reine Telemetriequelle für das ``sweep_completed``-Event erhalten
    (keine Promotions-/Gate-Wirkung) — für JEDE Entscheidungs- oder Report-Kennzahl ist
    ``report._family_n_stages`` die massgebliche Quelle.
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


def _family_n_frozen_from_studies(pairs, studies) -> dict[str, int]:
    """Issue #1091 (Katalog #924) — die familienweite Multiplizitaet, EINGEFROREN aus dem
    GEPLANTEN Budget (``study.user_attrs['n_trials_budget']``, von ``_optimize_symbol_impl`` VOR
    dem ersten Trial dieser Study gestempelt, #929 Fix 3), nicht aus tatsaechlich gezogenen/
    eligiblen Trials.

    Root-Cause #1091: ``report._family_n_from_proposals`` summiert ``deflation_n_eligible`` ueber
    die BEREITS EXPORTIERTEN Proposals eines Symbols — ein Report, der VOR Abschluss ALLER
    Strategien-Studies dieses Symbols gebaut wird (Zwischenreport/Fail-Fast-Probe, #1083), sieht
    zwangslaeufig nur eine TEILMENGE und meldet eine kleinere Zahl als ein spaeterer Report
    derselben Familie (280 → 321 → 434 ueber drei 18/67-Sekunden auseinanderliegende Lesungen,
    #1091-Symptom). ``n_trials_budget`` ist dagegen ab Study-ERSTELLUNG bekannt (JEDE Study dieses
    Symbols traegt ihn unabhaengig von den Geschwister-Strategien) — die Summe ist bit-identisch,
    egal ob sie vor, waehrend oder nach den Trials gelesen wird.

    ``pairs``/``studies`` sind wie bei ``_family_n_from_studies`` index-parallel; fehlt
    ``n_trials_budget`` auf einer Study (Legacy/Test-Study ohne den Stempel), faellt NUR DIESE
    Study auf ihre tatsaechliche Trial-Zahl zurueck (fail-open, keine Unterzaehlung durch eine
    fehlende Legacy-Study)."""
    family_n: dict[str, int] = {}
    for (_strategy, symbol, _reason), study in zip(pairs, studies):
        if study is None:
            continue
        attrs = getattr(study, "user_attrs", None) or {}
        budget = attrs.get("n_trials_budget")
        if isinstance(budget, (int, float)) and not isinstance(budget, bool):
            n = int(budget)
        else:
            n = len(getattr(study, "trials", None) or [])
        family_n[symbol] = family_n.get(symbol, 0) + n
    return family_n


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
    ``invariants.check_deflation_cluster_coverage`` bricht bei < 0.9 als Regressionswächter).

    Issue #905 (#822-Regression) — Root-Cause: dieser Filter blieb bei ``oos_evaluated is True``
    stehen, während ``_family_n_per_study_from_studies`` (der GEZÄHLTE #822-Wert, der über
    ``_family_n_stage1_from_studies`` an ``confirm(...)`` als ``deflation_n_family`` geht) bereits
    seit #822 auf ``oos_selection_statistic_available is True`` umgestellt wurde — ein Trial mit
    ``SORTINO_GUARD_TRIPPED``/``EQUITY_NONPOSITIVE`` ist ``oos_evaluated=True``, trägt aber KEINEN
    Sortino/PSR und hat das Maximum unter H₀ nicht beeinflusst. Die #904-``max()``-Zeile hat diesen
    Auseinanderlauf (``len(family_rows)`` > ``deflation_n_family``) bislang kaschiert, indem sie
    ``deflation_n_family_raw`` einfach auf die grössere Matrixzahl hochkorrigierte. Jetzt, wo #904
    diese Zeile entfernt hat, MUSS der Filter hier mit der Zählung übereinstimmen — sonst declustert
    ``confirm.py`` Konfigurationen, die nie Kandidat für die Selektion waren (und
    ``deflation_cluster_coverage`` bleibt strukturell < 1)."""
    family_returns: dict[str, list[list[float]]] = {}
    for (_strategy, symbol, _reason), study in zip(pairs, studies):
        trials = getattr(study, "trials", None) or []
        for t in trials:
            if getattr(t, "user_attrs", {}).get("oos_selection_statistic_available") is not True:
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
        # Issue #910 Fix 2 — unterscheidet 'Store leer' (die Kette hat noch nie geschrieben) von
        # 'Store gefüllt, aber Eintrag inadmissibel' (der granulare champion_is_admissible-Reason-
        # Code, z. B. 'EMPTY_PARAMS'/'REJECT_HOLDOUT_GATE') — vorher trugen beide Fälle denselben
        # 'NO_ADMISSIBLE_ENTRY'-String, und der Report konnte nicht sagen, ob die Kette nie startet
        # oder ständig scheitert.
        entry, _no_entry_reason, _no_entry_provenance = champions.load_champion_entry_with_reason(
            strategy, symbol, opt_data=opt_data)
        applied = False
        skipped_reason = _no_entry_reason or "STORE_EMPTY"
        advance_days = None
        corroboration_count = None
        # Issue #1103 (Katalog #936) — WELCHER Store-Schlüssel gesucht wurde und welche tatsächlich
        # existieren (``champions._no_entry_provenance``), NUR gesetzt fuer STORE_EMPTY/
        # NO_ENTRY_FOR_PAIR (der Verdacht auf einen Schlüssel-Mismatch, seit #1084 offen).
        skipped_provenance = _no_entry_provenance if entry is None else None
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
            # Issue #1103 (Katalog #936) — {looked_up_key, available_keys, available_keys_total},
            # NUR bei skipped_reason in {STORE_EMPTY, NO_ENTRY_FOR_PAIR} (siehe
            # champions._no_entry_provenance-Docstring); None sonst (kein Lookup-Fehlschlag).
            "skipped_provenance": None if applied else skipped_provenance,
        })
    except Exception:
        log.warning("[#818] %s/%s: Champion-Writeback fehlgeschlagen (non-fatal).",
                    strategy, symbol, exc_info=True)


def _sweep_run_lock_path() -> Path:
    """Issue #1086 (Katalog #919) — dieselbe ``{WORK}/sweep/``-Study-Store-Wurzel wie
    ``run_optimization.resolve_storage``, siehe dort.

    Dieser Lock ist STORE-WEIT (ein ``.run.lock`` je ``{WORK}/sweep/``), NICHT je Symbol oder je
    (Strategie, Symbol)-Paar — siehe Issue #944/#1110 (die AGENTS.md-Beschreibung als
    „Lock-Datei je Symbol\" war unzutreffend). Ein zweiter Sweep mit ANDEREM ``OPTIMIZER_WORK_DIR``
    (siehe ``manifest.WORK``) ist von diesem Lock nicht betroffen; paralleler Mehr-Symbol-Betrieb
    erfordert deshalb je Symbol ein eigenes ``OPTIMIZER_WORK_DIR`` (siehe
    ``manuals/run_optimizer.md``, Abschnitt „Paralleler Mehr-Symbol-Betrieb\")."""
    return WORK / "sweep" / ".run.lock"


# Issue #944/#1110 — in-Prozess-Zustand des von DIESEM Prozess gehaltenen Locks, je Lock-Pfad
# (nicht nur je run_id, damit Tests/Aufrufer mit wechselndem ``WORK`` einander nicht beeinflussen).
# Wert: {"fd": offener Dateideskriptor, "run_id": str}. Die eigentliche gegenseitige Ausschliessung
# zwischen UNABHAENGIGEN Prozessen erfolgt ausschliesslich ueber ``fcntl.flock`` auf diesem fd
# (kernel-atomar, kein TOCTOU-Fenster zwischen Existenzpruefung und Schreiben wie beim vorherigen
# ``lock_path.exists()``-Ansatz) und wird vom Kernel automatisch freigegeben, sobald der haltende
# Prozess endet (auch bei Absturz) — eine separate PID-Lebendigkeitspruefung fuer verwaiste Locks
# ist damit nicht mehr noetig.
_sweep_run_lock_state: dict[str, dict] = {}


def _sweep_run_lock_payload(run_id: str) -> bytes:
    return json.dumps({
        "run_id": run_id, "pid": os.getpid(),
        "acquired_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
    }).encode("utf-8")


def _write_sweep_run_lock_payload(fd: int, run_id: str) -> None:
    payload = _sweep_run_lock_payload(run_id)
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, payload)
    os.fsync(fd)


def _acquire_sweep_run_lock(run_id: str) -> None:
    """Verhindert, dass zwei UNABHAENGIGE Sweep-Prozesse (verschiedene ``run_id``) gleichzeitig
    denselben ``{WORK}/sweep/``-Optuna-Store beschreiben. Root-Cause #1086: genau dieser Zustand
    (drei zwischen 15:45 und 15:51 gestartete ``--symbols``-Prozesse gegen denselben Store) fuehrte
    dazu, dass jeder Prozess im eigenen Report auch die Studies der ANDEREN Prozesse sah, sobald
    einer der Prozesse ueber ``generate_report_for_run`` (Abbruch-/Standalone-Pfad) ALLE
    ``proposal_*.json`` unter ``WORK`` entdeckte.

    Issue #944/#1110 (TOCTOU-Haertung): der vorherige Ansatz (``lock_path.exists()`` gefolgt von
    einem separaten ``write_json_atomic``) hatte ein Zeitfenster zwischen Pruefung und Schreiben, in
    dem zwei Prozesse beide die Abwesenheit des Locks sehen und beide den Erwerb fuer erfolgreich
    halten koennen. Der Erwerb laeuft jetzt ausschliesslich ueber ``fcntl.flock(..., LOCK_EX |
    LOCK_NB)`` auf einem offen gehaltenen Dateideskriptor — eine einzelne, kernel-atomare Operation.
    Ein toter Halter blockiert den Store nicht mehr dauerhaft, weil der Kernel dessen ``flock`` beim
    Prozessende (auch bei Absturz) automatisch freigibt; eine explizite PID-Lebendigkeitspruefung
    entfaellt damit.

    Ein Lauf mit DEMSELBEN ``run_id`` INNERHALB DESSELBEN Prozesses (der #799-Resume-Pfad, oder ein
    erneuter Aufruf im selben Lauf) erneuert die Lock-Datei, statt sich selbst ueber ``flock`` gegen
    die eigene, bereits gehaltene Sperre laufen zu lassen."""
    lock_path = _sweep_run_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path)

    held = _sweep_run_lock_state.get(key)
    if held is not None:
        if held["run_id"] == run_id:
            _write_sweep_run_lock_payload(held["fd"], run_id)
            return
        raise RuntimeError(
            "[SWEEP_CONCURRENT_RUN_DETECTED] "
            f"{lock_path} wird von run_id={held['run_id']!r} in diesem Prozess gehalten — ein "
            f"zweiter Sweep-Lauf (run_id={run_id!r}) darf denselben {{WORK}}/sweep/-Store nicht "
            "gleichzeitig beschreiben (Issue #1086/#1110)."
        )

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            holder = json.loads(lock_path.read_text("utf-8"))
        except (OSError, ValueError):
            holder = {}
        os.close(fd)
        raise RuntimeError(
            "[SWEEP_CONCURRENT_RUN_DETECTED] "
            f"{lock_path} wird von run_id={holder.get('run_id')!r} (pid={holder.get('pid')}) "
            f"gehalten — ein zweiter Sweep-Prozess (run_id={run_id!r}) darf denselben "
            "{WORK}/sweep/-Store nicht gleichzeitig beschreiben (Issue #1086/#1110). Starte den "
            "zweiten Lauf mit einem eigenen OPTIMIZER_WORK_DIR (empfohlen, siehe „Paralleler "
            "Mehr-Symbol-Betrieb\" in manuals/run_optimizer.md), oder warte, bis der laufende "
            "Sweep abgeschlossen ist."
        ) from None
    _write_sweep_run_lock_payload(fd, run_id)
    _sweep_run_lock_state[key] = {"fd": fd, "run_id": run_id}


def _release_sweep_run_lock(run_id: str) -> None:
    """Gibt die #1086-Lock-Datei frei, sofern sie noch DIESEM ``run_id`` gehoert — ein inzwischen
    von einem anderen (Resume-)Lauf uebernommenes Lock wird NICHT vom vorherigen Halter geloescht."""
    lock_path = _sweep_run_lock_path()
    key = str(lock_path)
    held = _sweep_run_lock_state.get(key)
    if held is not None:
        if held["run_id"] != run_id:
            return
        try:
            fcntl.flock(held["fd"], fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(held["fd"])
        except OSError:
            pass
        del _sweep_run_lock_state[key]
        try:
            lock_path.unlink()
        except OSError:
            pass
        return
    try:
        holder = json.loads(lock_path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    if holder.get("run_id") == run_id:
        try:
            lock_path.unlink()
        except OSError:
            pass


def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, tier: str = "deployable", n_jobs: int = 1,
                         n_jobs_source: str = "DEFAULT",
                         optimize_symbol=None, confirm=None,
                         run_id: str | None = None, bar_quality_fn=None,
                         max_wallclock_h_override: float | None = None) -> list[Path]:
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

    Issue #840/#841/#842 (Katalog #840-#843, GitHub-Issue #754) — implementiert: ``--resume``/
    ``--run-id`` (CLI-Einstieg in den #799-Checkpoint, siehe ``sweep.main()``), das
    Symbol-Abdeckungs-Ledger (``symbol_coverage.py``, treibt ``sweep_symbol_order_policy``) und
    das Laufzeit-Budget 24→72h + ``--max-wallclock-h`` + Vorab-Prognose. Issue #843
    (Pipelining über Symbolgrenzen hinweg, ``pipeline_depth``) bleibt AUS DEMSELBEN, oben
    ausgeführten Grund weiterhin zurückgestellt: dieses Environment hat nach wie vor keinen
    echten Mehrsymbol-Produktionslauf, gegen den eine Restrukturierung der Dispatch-Schleife
    empirisch verifiziert werden könnte (AK-1 in #843 verlangt bitgleiche ``n_family``/
    ``deflation_n_effective``/``selection_rule_fingerprint`` über einen echten 3-Symbol-Referenz-
    lauf) — eine blind implementierte Restrukturierung risikiert exakt die stille
    Korrektheitsregression, die #843 selbst als Grund für die ursprüngliche Zurückstellung nennt.
    #841 (Ledger) + #842 (Budget) lösen die vom Nutzer geforderte Vollabdeckung bereits OHNE
    Pipelining (in einem 72-h-Lauf oder mehreren fortgesetzten Etappen); #843 bleibt ein reiner
    Durchsatz-Hebel für eine spätere Sitzung mit Zugriff auf einen realen Katalog.

    Issue #932 (Pitfall #305) — ``pipeline_depth`` (der Look-Ahead-Schlüssel aus Fix Punkt 1 oben)
    war über drei Kataloge (#843/#871/#893/#908) dokumentiert, in der Registry gefuehrt UND
    komplett unverdrahtet — derselbe Zustand wie #913 (Katalog A), nur eine Konfigurationsdatei
    weiter: ein Key, der existiert, jede Existenzpruefung besteht, dessen Semantik aber fehlt.
    ENTFERNT (Fix-Weg (b) aus #932 — die vollstaendige Restrukturierung (Weg (a)) bleibt aus
    demselben, oben ausgefuehrten Grund zurueckgestellt). STATTDESSEN implementiert: Fix Punkt 2
    (Longest-Processing-Time-Dispatch) — die 14 Studies EINES Symbols werden absteigend nach dem
    aus dem letzten #742-Report gelesenen Erfahrungswert (``_read_last_study_wallclock_by_
    strategy``) dispatcht, statt in Enumerationsreihenfolge. Das ist eine Sortierzeile INNERHALB
    der weiterhin symbolweise synchronen Barriere, keine Restrukturierung, und senkt die
    Barriere-Wartezeit messbar (``SYMBOL_DISPATCH_COMPLETED``-Event, ``barrier_wait_s``) — ohne
    das #843-Korrektheits-Risiko einer echten Pipeline ueber Symbolgrenzen hinweg einzugehen.
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
        # Issue #844 — Config-Key-Registry-Preflight (_required_keys.json): ein Key, der stillschweigend
        # fehlen und einen Mechanismus lautlos deaktivieren kann (Pitfall #267), bricht den Lauf jetzt
        # VOR dem ersten Symbol ab, statt erst nach vollem Durchlauf im Report aufzufallen.
        assert_required_config_keys_valid()
        # Issue #852 — Bibliotheksversions-Drift-Preflight (teilt den Preflight-Einstieg mit #844):
        # eine installierte Version ausserhalb des gepinnten Bereichs bricht VOR dem ersten Symbol ab.
        assert_pinned_library_versions_valid()
        # Issue #913 Fix 3 — ``sortino_numeric_guard_reference='family_median'`` verlangt einen
        # verdrahteten Injektionspfad (kein Aufrufer, der family_median_n_periods nie übergibt);
        # sonst liefe ein 143-Symbol-Lauf 170 h informationsfrei (AGENTS.md Pitfall #296). Lazy
        # Import, um den Import von backtest_runner (nautilus_trader-Engine) nicht in den
        # Elternprozess-Modul-Ladepfad zu ziehen, ausser der Preflight läuft tatsächlich.
        from automation.backtest_runner import assert_guard_reference_injectable
        assert_guard_reference_injectable()
        # Issue #920 — Instrument-Metadaten-Kohärenz (Pitfall #298): ein Symbol mit
        # size_precision>=6 und asset_class != 'crypto' bricht den Lauf ab, statt 143 Symbole
        # lang mit falschen simulierten Fill-Preisen zu laufen.
        assert_instrument_metadata_coherence()

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
    start_ns = compute_oos_window_start_ns(config, catalog_newest_ns=global_catalog_newest_ns)
    holdout_window_reach_target_ns = compute_holdout_window_reach_target_ns(config, catalog_newest_ns=global_catalog_newest_ns)

    # Issue #624/#909 — Holdout-Geometrie vs. TATSÄCHLICHE Katalog-Spanne beim Sweep-Start LOGGEN.
    # Root-Cause #909 (Pitfall #291): die VORHERIGE Berechnung (``min(latest_ts.values())`` als
    # "ältester" Punkt) mass die STREUUNG der Enddaten über die Symbole, nicht die Historienlänge
    # irgendeines Symbols — auf einem gesunden Katalog (alle Symbole enden nahe beieinander)
    # konvergierte der Alarm gegen 0 und war umso lauter, je gesünder der Katalog war. Die Spanne
    # wird jetzt PRO SYMBOL berechnet (``latest - earliest``, dieselbe Achse) und als Minimum/Median
    # über die Symbole berichtet — eine aggregierte Spanne über heterogene Symbole ist ohnehin keine
    # sinnvolle Einzelgrösse.
    _earliest_ts = earliest_ts_by_symbol(syms)
    _wf = config.get("walk_forward") or {}
    _req_span = required_span_days(_wf)
    _span_stats = per_symbol_span_stats(latest_ts, _earliest_ts, syms, required_span_days=_req_span)
    logging.getLogger("optimizer").info(
        "[#624] Holdout-Geometrie: required_span_days=%s (is=%s + embargo=%s + %s×oos=%s + holdout=%s); "
        "min_span_days=%s, median_span_days=%s (je Symbol, latest-earliest), "
        "n_symbols_below_required=%s von %d. 45-d-Holdout ⇒ T≈202 Bars ⇒ PSR(0)≈0.946 < 0.95 "
        "(T≥211 nötig). Promotionsschwelle DSR/PSR wird EXPLIZIT und dokumentiert getragen (siehe "
        "manuals/strategie_optimierung.md §Holdout-Signifikanz).",
        _req_span, _wf.get("is_window_days"), _wf.get("embargo_period_days"),
        _wf.get("splits"), _wf.get("oos_window_days"), _wf.get("holdout_days"),
        None if _span_stats["min_span_days"] is None else round(_span_stats["min_span_days"], 1),
        None if _span_stats["median_span_days"] is None else round(_span_stats["median_span_days"], 1),
        _span_stats["n_symbols_below_required"], len(syms),
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
        # Issue #923 Fix 1/3 — je Symbol persistiert (write_symbol_bar_quality_cache), damit
        # report._study_record den echten median_delta_t_s als bar_seconds auflösen kann, statt
        # unbedingt auf _contracts.BAR_SECONDS_DEFAULT zurückzufallen (report.py läuft nach dem
        # Sweep, ohne eigenen Katalog-Zugriff).
        _quality_by_symbol: dict[str, dict] = {}
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
                max_frac_high_eq_low=_bar_quality_cfg.get("max_frac_high_eq_low", 0.20),
                max_frac_identical_consecutive_closes=_bar_quality_cfg.get(
                    "max_frac_identical_consecutive_closes", 0.5),
                min_distinct_closes=_bar_quality_cfg.get("min_distinct_closes", 10),
                max_frac_zero_true_range=_bar_quality_cfg.get("max_frac_zero_true_range", 0.25),
                min_atr_median_bps=_bar_quality_cfg.get("min_atr_median_bps", 5.0),
                min_bar_coverage_ratio=_bar_quality_cfg.get("min_bar_coverage_ratio", 0.6),
                bar_coverage_ratio=_sample.get("bar_coverage_ratio"),
                median_delta_t_s=_sample.get("median_delta_t_s"),
            )
            _quality_by_symbol[_sym] = {
                "frac_zero_true_range": _quality.get("frac_zero_true_range"),
                "atr_median_bps": _quality.get("atr_median_bps"),
                "bar_coverage_ratio": _quality.get("bar_coverage_ratio"),
                "median_delta_t_s": _quality.get("median_delta_t_s"),
                "passed": _quality["passed"],
            }
            # Issue #900 Fix 3 — BAR_QUALITY_PROFILE-Event je Symbol, UNABHAENGIG vom Pruefergebnis
            # (ein bestandener Preflight ohne Zahlen ist keine Evidenz). Fix 4 — Stichprobenumfang
            # und Fenster im Event, sonst ist die Aussage nicht reproduzierbar.
            emit_execution_event(_log, "BAR_QUALITY_PROFILE", {
                "symbol": _sym,
                "frac_zero_true_range": _quality.get("frac_zero_true_range"),
                "atr_median_bps": _quality.get("atr_median_bps"),
                "bar_coverage_ratio": _quality.get("bar_coverage_ratio"),
                "median_delta_t_s": _quality.get("median_delta_t_s"),
                "n_sample_ticks": _sample.get("n_sample_ticks"),
                "window_start": _sample.get("window_start"),
                "window_end": _sample.get("window_end"),
                "passed": _quality["passed"],
            })
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
        if _quality_by_symbol:
            try:
                write_symbol_bar_quality_cache(WORK, _quality_by_symbol)
            except Exception:
                _log.debug("[#923] symbol_bar_quality-Cache-Schreiben fehlgeschlagen (non-fatal).",
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

    # Issue #892 Fix Punkt 5 — sammelt jedes Symbol mit MINDESTENS einer Gate-1-Ablehnung, damit
    # ein Symbol, dessen Paare AUSSCHLIESSLICH an Gate 1 scheitern (unten: kein 'OK'-Paar), im
    # Coverage-Ledger als 'covered_gate1' markiert werden kann, statt für check_symbol_coverage
    # unsichtbar zu bleiben (es wird nachweislich JEDEN Lauf geprüft, nur nie optimiert).
    _gate1_rejected_symbols: set[str] = set()
    # Issue #1040 (Katalog #866) — VOR der Paar-Enumeration (nicht erst beim spaeteren #841-Ledger-
    # Block unten, der die Rotations-REIHENFOLGE der bereits enumerierten Paare bestimmt): welche
    # Symbole unter ``tier='deployable'` ZUSAETZLICH zu Tier-A-Gewinnern als Kandidat zugelassen
    # werden. Nur-lesend (kein ``start_new_run``/``write_coverage`` hier — der spaetere Block bleibt
    # die alleinige Schreibstelle des Ledgers, siehe dessen #892-Kommentar); ein Lesefehler faellt
    # fail-open auf ``stale_symbols=set()`` zurueck (bit-identisches Alt-Verhalten).
    try:
        _pre_coverage_ledger = symbol_coverage.load_coverage(path=WORK / "symbol_coverage.json")
        _stale_max_age_runs = int(opt_data.get("symbol_coverage_max_age_runs", 3))
        _stale_symbols = set(symbol_coverage.coverage_report(
            _pre_coverage_ledger, syms, max_age_runs=_stale_max_age_runs)["stale_symbols"].keys())
    except Exception:
        _stale_symbols = set()
    pairs = enumerate_tunable_pairs(strategies, syms, tier=tier,
                                    available_bars=available_bars, config=config,
                                    latest_ts=latest_ts, start_ns=start_ns,
                                    holdout_window_reach_target_ns=holdout_window_reach_target_ns,
                                    gate1_rejected_symbols=_gate1_rejected_symbols,
                                    stale_symbols=_stale_symbols)

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

        # Issue #931 (Pitfall #304) — derselbe Preflight-Zeitpunkt kennt bereits expected_trials;
        # der Disk-Preflight oben prüfte bislang nur die komfortable Ressource (Plattenplatz),
        # nicht die knappe (Zeit). WALLCLOCK_BUDGET_PREFLIGHT schliesst diese Lücke.
        wallclock_guard.clear_degrade_state(WORK)
        # Issue #983 (Katalog D, P0 HEADLINE) — MITTELWERT statt Median (rechtsschiefe Verteilung,
        # Faktor 1.279 im Referenzlauf), plus die beiden zusätzlichen Korrekturfaktoren
        # (Nicht-Backtest-Anteil der Study-Wallclock, Scheduling-Verlust). Alle drei sind
        # config-gesteuert (Ledger-Ersatz: konservative Defaults, bis genug Läufe für eine echte
        # Kalibrierung vorliegen — #983 verweist selbst auf einen künftigen Ledger, siehe #988).
        _backtest_ms_mean = _read_last_backtest_ms_mean() or wallclock_guard.DEFAULT_BACKTEST_MS_MEDIAN
        _backtest_share = float(opt_data.get(
            "wallclock_preflight_backtest_share", wallclock_guard.DEFAULT_BACKTEST_SHARE))
        _scheduling_efficiency = float(opt_data.get(
            "wallclock_preflight_scheduling_efficiency",
            wallclock_guard.DEFAULT_SCHEDULING_EFFICIENCY))
        _parallelism_degree = float(n_jobs) if n_jobs and n_jobs > 0 else 1.0
        _expected_wallclock_h = wallclock_guard.estimate_expected_wallclock_h(
            expected_trials=_expected_trials, backtest_ms_median=_backtest_ms_mean,
            parallelism_degree=_parallelism_degree, backtest_share=_backtest_share,
            scheduling_efficiency=_scheduling_efficiency)
        _sweep_max_wallclock_h = opt_data.get("sweep_max_wallclock_h")
        emit_execution_event(logging.getLogger("optimizer"), "WALLCLOCK_BUDGET_PREFLIGHT", {
            "expected_trials": _expected_trials,
            "backtest_ms_mean": _backtest_ms_mean,
            "backtest_share": _backtest_share,
            "scheduling_efficiency": _scheduling_efficiency,
            "parallelism_degree": _parallelism_degree,
            "expected_wallclock_h": round(_expected_wallclock_h, 2),
            "sweep_max_wallclock_h": _sweep_max_wallclock_h,
        })
        _over_budget = (_sweep_max_wallclock_h is not None
                        and _expected_wallclock_h > float(_sweep_max_wallclock_h))
        if _over_budget:
            _policy = opt_data.get("wallclock_budget_policy", "degrade")
            if _policy not in ("abort", "degrade", "warn"):
                raise ValueError(
                    f"optimizer.json['wallclock_budget_policy']={_policy!r} unbekannt — erwartet "
                    "'abort', 'degrade' oder 'warn'.")
            _log = logging.getLogger("optimizer")
            if _policy == "abort":
                raise RuntimeError(
                    f"REJECT_WALLCLOCK_BUDGET_EXCEEDED (#931): expected_wallclock_h="
                    f"{_expected_wallclock_h:.1f} > sweep_max_wallclock_h={_sweep_max_wallclock_h} "
                    f"(expected_trials={_expected_trials}, backtest_ms_median={_backtest_ms_median:.0f}, "
                    f"parallelism_degree={_parallelism_degree:.2f}). wallclock_budget_policy='abort' "
                    "— Budget/Geometrie anpassen ODER Policy auf 'degrade'/'warn' stellen."
                )
            if _policy == "degrade":
                _degrade_factor = max(0.05, float(_sweep_max_wallclock_h) / _expected_wallclock_h)
                wallclock_guard.write_degrade_factor(WORK, _degrade_factor)
                _log.warning(
                    "[#931] WALLCLOCK_BUDGET_DEGRADED: expected_wallclock_h=%.1f > "
                    "sweep_max_wallclock_h=%s — n_trials_budget wird global um Faktor %.3f gekürzt "
                    "(wallclock_budget_policy='degrade').",
                    _expected_wallclock_h, _sweep_max_wallclock_h, _degrade_factor,
                )
            else:
                _log.warning(
                    "[#931] WALLCLOCK_BUDGET_EXCEEDED: expected_wallclock_h=%.1f > "
                    "sweep_max_wallclock_h=%s (wallclock_budget_policy='warn' — keine Konsequenz, "
                    "nur Diagnose).", _expected_wallclock_h, _sweep_max_wallclock_h,
                )

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

    # Issue #841 — Symbol-Dispatch-Reihenfolge: 'least_recently_covered' (Default) rotiert das
    # Universum über mehrere Läufe hinweg, statt bei einem Laufzeit-Budget < Vollabdeckung immer
    # denselben Universums-Kopf zu bedienen. 'universe' reproduziert die bisherige (reine
    # Einfüge-)Reihenfolge bitgleich (Rückwärtskompatibilität). NICHT an using_real_optimize
    # gekoppelt (analog dem #799-Checkpoint direkt darunter) — mit injizierten HI-7-Fakes testbar.
    _coverage_path = WORK / "symbol_coverage.json"
    _symbol_order_policy = opt_data.get("sweep_symbol_order_policy", "least_recently_covered")
    _coverage_ledger = symbol_coverage.load_coverage(path=_coverage_path)
    _coverage_ledger = symbol_coverage.start_new_run(_coverage_ledger)
    # Issue #892 Fix Punkt 1 — SOFORT persistieren, nicht erst beim ersten abgeschlossenen Symbol
    # (``_record_coverage`` unten): ``total_runs_started`` wurde bereits im Speicher erhöht, aber
    # ein Lauf, der VOR dem ersten Symbolabschluss abbricht (Crash, Fail-Fast-Abbruch nach 0
    # Symbolen, SIGTERM), verlor diesen Zähler bisher — der Folgelauf sah denselben Stand und
    # begann wieder bei Symbol 1 der Universums-Reihenfolge (Pitfall #237, achte Wiederkehr).
    try:
        symbol_coverage.write_coverage(_coverage_ledger, path=_coverage_path)
    except OSError:
        logging.getLogger("optimizer").warning(
            "[#892] Symbol-Coverage-Ledger (Laufbeginn) konnte nicht geschrieben werden "
            "(non-fatal).", exc_info=True)
    # Issue #841 — EINMAL berechnet (nicht je Symbol — ``catalog_fingerprint`` durchläuft den
    # gesamten Daten-Katalog) und für alle Symbole dieses Laufs wiederverwendet.
    _coverage_data_snapshot_sha256 = catalog_fingerprint() if using_real_optimize else None
    _ordered_symbols = symbol_coverage.order_symbols(
        list(pairs_by_symbol.keys()), _coverage_ledger, policy=_symbol_order_policy)
    pairs_by_symbol = {sym: pairs_by_symbol[sym] for sym in _ordered_symbols}

    # Issue #799 — der Fortschritts-Checkpoint ist NICHT an using_real_optimize gekoppelt (anders
    # als Champion-Store/Retention/Preinit/Backfill): er verfolgt den Dispatch-Fortschritt selbst,
    # unabhaengig davon, ob optimize_symbol/confirm injizierte HI-7-Fakes oder die echte
    # Implementierung sind — das macht ihn mit injizierten Fakes testbar (siehe
    # test_issue_799_per_symbol_transaction.py) UND nuetzlich fuer einen HI-7-Dry-Run.
    if run_id is None:
        run_id = default_run_id()

    # Issue #1086 (Katalog #919) — Lock gegen einen zweiten, gleichzeitigen Sweep-Prozess auf
    # demselben ``{WORK}/sweep/``-Store (siehe ``_acquire_sweep_run_lock``-Docstring). Nur im
    # echten Storage-Pfad (analog jedem anderen Preflight-Guard oben) — injizierte HI-7-Fakes
    # beruehren keine SQLite-Datei und brauchen keinen Store-Lock.
    if using_real_optimize:
        _acquire_sweep_run_lock(run_id)

    # Issue #892 Fix Punkt 5 — ein Symbol, dessen Paare AUSSCHLIESSLICH an Gate 1 scheiterten (kein
    # einziges 'OK'-Paar in pairs_by_symbol), wird im Coverage-Ledger als 'covered_gate1' markiert
    # (mark_gate1_covered, zuvor Issue #841 Punkt 5 — implementiert, aber NULL Call-Sites) — es
    # wurde nachweislich DIESEN Lauf geprüft, nur strukturell nie optimiert, und darf
    # check_symbol_coverage nicht dauerhaft rot faerben.
    _gate1_only_symbols = sorted(_gate1_rejected_symbols - set(pairs_by_symbol.keys()))
    for _sym in _gate1_only_symbols:
        _coverage_ledger = symbol_coverage.mark_gate1_covered(
            _coverage_ledger, _sym, run_id=run_id,
            run_index=_coverage_ledger.get("total_runs_started", 0),
            completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        )
    if _gate1_only_symbols:
        try:
            symbol_coverage.write_coverage(_coverage_ledger, path=_coverage_path)
        except OSError:
            logging.getLogger("optimizer").warning(
                "[#892] Gate-1-Coverage-Markierung konnte nicht geschrieben werden (non-fatal).",
                exc_info=True)

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
        global sweep_symbol_funnel
        _checkpoint_payload = {
            "run_id": run_id,
            "completed_symbols": sorted(completed_symbols),
            "failed_pairs": failed_pairs,
            # Issue #833 Fix Punkt 3 — Gesamtzahl der fuer DIESEN Lauf geplanten Symbole, damit
            # ein Abbruch-Report (sweep.main()) "symbols_completed / symbols_planned" ausweisen
            # kann, OHNE die Enumeration ein zweites Mal auszufuehren.
            "symbols_planned": len(pairs_by_symbol),
            # Issue #942 (Katalog A) — Funnel-Transparenz: rohes Symbol-Universum DIESES Laufs
            # und die Zahl der an Gate 1 abgelehnten Symbole, damit symbols_planned/
            # symbols_discovered als erreichbare Coverage lesbar wird, statt nur die bereits
            # gefilterte Endzahl zu zeigen.
            "symbols_discovered": len(syms),
            "symbols_gate1_rejected": len(_gate1_rejected_symbols),
            # Issue #840 Punkt 5 — SHA-256 über Strategien + reward_semantics_version +
            # simulation_semantics_version (#854); main() validiert dies gegen den aktuellen
            # Stand, BEVOR ein --resume startet (sonst mischt der Resume Studies zweier
            # Semantiken in dieselbe #652-Familie).
            "strategies_fingerprint": _strategies_fingerprint(strategies, opt_data),
        }
        # Issue #942/#1108 — In-Prozess-Spiegel VOR dem Datei-Schreibversuch aktualisiert: bleibt
        # auch dann korrekt, wenn der Schreibvorgang selbst fehlschlaegt (Disk voll etc.) — main()s
        # Symbol-Zaehler haengen dann nicht mehr von einem erfolgreichen Datei-I/O ab.
        sweep_symbol_funnel = _checkpoint_payload
        try:
            write_json_atomic(checkpoint_path, _checkpoint_payload)
        except OSError:
            logging.getLogger("optimizer").warning(
                "[#799] Sweep-Checkpoint konnte nicht geschrieben werden (non-fatal).", exc_info=True)

    def _record_coverage(symbol: str) -> None:
        # Issue #841 — geschrieben am selben Punkt wie der #799-Checkpoint: JEDES verarbeitete
        # Symbol (auch eines, dessen Pairs vollständig Gate-1-verworfen wurden oder das an der
        # #827-Heterogenität scheiterte) gilt als abgedeckt — es wurde nachweislich diesen Lauf
        # geprüft, unabhängig vom Ergebnis. Fail-open: ein Schreibfehler darf den Sweep nie crashen.
        nonlocal _coverage_ledger
        try:
            _coverage_ledger = symbol_coverage.record_symbol_completion(
                _coverage_ledger, symbol, run_id=run_id,
                run_index=_coverage_ledger.get("total_runs_started", 0),
                completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                data_snapshot_sha256=_coverage_data_snapshot_sha256,
            )
            symbol_coverage.write_coverage(_coverage_ledger, path=_coverage_path)
        except OSError:
            logging.getLogger("optimizer").warning(
                "[#841] Symbol-Coverage-Ledger konnte nicht geschrieben werden (non-fatal).",
                exc_info=True)

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
            # Issue #1015 (Katalog #858, Fix Punkt 1) — derselbe run_id, der bereits den #799-
            # Checkpoint treibt, wird jetzt an jeden neu erzeugten Trial gestempelt
            # (make_symbol_objective), damit compute_budget_execution eine ungepurgte Study (mit
            # Trials VORANGEGANGENER Läufe) nicht mehr in die Budget-Ausführung dieses Laufs mischt.
            return optimize_symbol(strategy, symbol, catalog_newest_ns=newest_ns,
                                   catalog_span_days=span_days, run_id=run_id)
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
                                # Issue #957/#1123 (Katalog #960) — benennt im Artefakt, WELCHE der
                                # (strukturell zwei moeglichen) Quellen die Deflationsschwelle
                                # tatsaechlich gespeist hat: n_family_stage1_map[(strategy, symbol)]
                                # (per-Study-Stage1-N, #826), NICHT die symbolweite Summe.
                                deflation_n_family_source="n_family_stage1_per_strategy",
                                deflation_family_period_returns=family_returns_map.get(symbol),
                                deflation_n_family_excluded_no_statistic=(
                                    (n_family_excluded_map or {}).get(symbol)),
                                # Issue #1027 (Katalog #866) — dieselbe run_id, die den #799-
                                # Checkpoint und den #742-Report treibt, damit die PROMOTE_GLOBAL_
                                # DEFAULT-Budget-Vorbedingung dieselbe Study-Kohorte sieht wie Report
                                # und Invariante.
                                run_id=run_id)
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
    if max_wallclock_h_override is not None:
        # Issue #842 — CLI-Override (--max-wallclock-h) schlägt die Config; <= 0 deaktiviert das
        # Budget fuer diesen Lauf (dieselbe Konvention wie ein expliziter null-Wert in der Config).
        _sweep_max_wallclock_h = max_wallclock_h_override if max_wallclock_h_override > 0 else None
    else:
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
    global sweep_fail_fast_probe_invariant_checks
    sweep_fail_fast_probe_invariant_checks = None
    global sweep_symbol_funnel
    sweep_symbol_funnel = None
    # Issue #939 (Katalog A, Pitfall #299) — Fehler-Isolation je Symbol. Vor diesem Fix hatte die
    # Symbol-Schleife kein try/except: eine EINZELNE Exception in der Confirm/Export-Phase (z.B.
    # #937s Serialisierungsfehler, bevor der Sink selbst nie-werfend gemacht wurde) propagierte
    # nach main() und liess ALLE verbleibenden Symbole unberuehrt — bei p=0.99 Erfolgswahrschein-
    # lichkeit je Symbol und N=143 ist p^N = 23.6% Lauf-Erfolgswahrscheinlichkeit (Pitfall #299).
    # Beide Schwellen muessen VERLETZT sein, sonst wuerde ein Lauf mit 10 Symbolen schon bei einem
    # einzigen Ausfall abbrechen.
    _max_failed_symbols_abs = int(opt_data.get("max_failed_symbols_abs", 5))
    _max_failed_symbols_frac = float(opt_data.get("max_failed_symbols_frac", 0.05))
    _failed_symbols_count = 0
    _symbol_status: dict[str, str] = {}
    global sweep_failed_symbols
    sweep_failed_symbols = []
    _fail_fast_invariants = opt_data.get("fail_fast_invariants", ["check_holding_time_cap"]) or []
    _fail_fast_min_symbols = int(opt_data.get("fail_fast_min_symbols", 2))
    # Issue #877 — die Evidenz-Streuung bestimmt die Reichweite der Konsequenz (Pitfall #282): ein
    # Fail-Fast-Offender, der auf WENIGER als so viele verschiedene Symbole streut, quarantänisiert
    # nur diese (Strategie, Symbol)-Paare (record_diagnosed_pair, "denylist") statt den gesamten
    # Sweep abzubrechen. Erst Streuung über >= diese Anzahl Symbole ist eine Aussage über die
    # BASISKLASSE (HourlyStrategyBase etc.), nicht mehr über ein einzelnes Symbol/dessen Datenlage.
    _fail_fast_min_offending_symbols = int(opt_data.get("fail_fast_min_offending_symbols", 2))
    # Issue #1016 (Katalog #858, Pitfall #349) — beide obigen Schwellen wurden gegen Vollläufe über
    # 143 Symbole kalibriert (#877): ein Ein-Symbol-Lauf kann ``len(completed_symbols) >= 2``
    # (``_fail_fast_min_symbols``-Default) NIE erreichen — das deaktiviert die Fail-Fast-Probe
    # GENAU in dem Modus, in dem ein einzelnes Paar für den Kapitaleinsatz feingetunt wird ("Schutz
    # aus", nicht "Schutz mild"). ``_fail_fast_min_offending_symbols`` wäre danach ohnehin
    # unerreichbar (nie mehr als 1 Symbol vorhanden). Fix: bei genau einem geplanten Symbol läuft
    # die Probe bereits nach dessen erstem abgeschlossenen Symbol, und die Symbol-Streuungs-Schwelle
    # wird durch eine STUDY-Quote ersetzt (unten, an der ``_systemic``-Berechnung) — eine Schwelle,
    # die für den Vollauf kalibriert wurde, braucht für den Ein-Entitäten-Fall eine eigene
    # Formulierung (Pitfall #349), nicht denselben Wert unter einem strukturell unerreichbaren Namen.
    _n_symbols_planned = len(pairs_by_symbol)
    _total_studies_single_symbol = 0
    if _n_symbols_planned == 1:
        _fail_fast_min_symbols = 1
        _total_studies_single_symbol = len(next(iter(pairs_by_symbol.values()), []))
    _fail_fast_min_offending_studies = int(opt_data.get("fail_fast_min_offending_studies", 3))
    _fail_fast_min_offending_studies_frac = float(
        opt_data.get("fail_fast_min_offending_studies_frac", 0.25))
    # Issue #975 (Katalog B, Pitfall — Fail-Fast als Abbruch statt Quarantäne) — VORHER war die
    # Konsequenz einer Streuung >= ``_fail_fast_min_offending_symbols`` IMMER ein globaler Abbruch
    # (``break`` unten, ``run_status=aborted_invariant``), selbst wenn nur 2 von 143 geplanten
    # Symbolen ueberhaupt schon gelaufen waren (Referenzlauf ``46cf5070``: 2/143, Erfolgswahr-
    # scheinlichkeit eines Vollaufs ist dann ``p^143``, nicht ``p^2``). ``fail_fast_policy`` steuert
    # jetzt, ob die BASISKLASSEN-Aussage (>= min_offending_symbols Streuung) denselben Weg wie die
    # bereits bestehende #877-Symbol-Quarantäne nimmt (alle betroffenen (Strategie, Symbol)-Paare
    # werden denylisted, der Sweep laeuft mit den verbleibenden Paaren weiter) oder — wie zuvor
    # bit-identisch — den Sweep hart abbricht. Default 'quarantine': ein Lauf soll bei jedem
    # Fail-Fast-Befund ein Ergebnis liefern, kein Abbruch nach 2 Symbolen (#975-Fix 1). Ein Check
    # ohne parsbare (Strategie, Symbol)-Offender (``not _offending_pairs``, z. B. ein
    # global-scope-Check) bleibt UNABHAENGIG von der Policy ein Abbruch — die Streuung ist dann
    # nicht ermittelbar, eine Quarantäne kann kein konkretes Paar benennen (siehe
    # ``_offending_pairs_for_fail_fast_check``-Docstring).
    _fail_fast_policy = str(opt_data.get("fail_fast_policy", "quarantine"))
    if _fail_fast_policy not in ("abort", "quarantine"):
        raise ValueError(
            f"optimizer.json['fail_fast_policy']={_fail_fast_policy!r} unbekannt — erwartet "
            "'abort' oder 'quarantine'.")
    global sweep_fail_fast_quarantined_pairs
    sweep_fail_fast_quarantined_pairs = []
    # Issue #856 Fix Punkt 3 — ein Wächter, der zweimal in Folge nicht ausführbar ist, ist ein
    # Betriebsvorfall (Pitfall #270), keine wiederholte Randnotiz. Zähler zählt AUFEINANDER-
    # FOLGENDE Fehlschläge der Probe selbst (nicht: der Invariante FAILt — das ist der Erfolgsfall
    # des Wächters); ab dem zweiten Fehlschlag wird ERROR emittiert und die Probe für den Rest des
    # Laufs deaktiviert, statt denselben Traceback bis zu ``symbols_planned``-mal zu erzeugen.
    _fail_fast_probe_errors = 0

    # Issue #842 Punkt 3 — Vorab-Prognose: nach den ersten wallclock_forecast_after_symbols
    # abgeschlossenen Symbolen wird der gemessene Takt gegen das Laufzeit-Budget hochgerechnet und
    # protokolliert (WARNING, falls die Prognose < symbols_planned liegt) — der Operator erfaehrt
    # das nach ~wenigen Symbolen, nicht erst nach dem vollen Lauf.
    _wallclock_forecast_after_symbols = int(opt_data.get("wallclock_forecast_after_symbols", 3))
    _wallclock_forecast_done = False
    # Issue #908 Fix 2 — die Vorab-Prognose bekommt jetzt eine KONSEQUENZ statt nur einer Warnung:
    # überschreitet die Hochrechnung sweep_max_wallclock_h, wird die Symbolliste (bereits
    # least_recently_covered-rotiert, #841) gekürzt, statt den Lauf ins Timeout laufen zu lassen.
    # None ⇒ keine Kürzung (Default/kein Budget/Prognose innerhalb Budget).
    _wallclock_truncation_limit: int | None = None

    # Issue #833 Fix Punkt 3 — Checkpoint EINMAL vor dem ersten Symbol geschrieben, damit
    # symbols_planned auch dann verfuegbar ist, wenn der Lauf schon im allerersten Symbol
    # abbricht (VOR dem ersten regulaeren _write_checkpoint()-Aufruf weiter unten).
    _write_checkpoint()

    # Issue #932 — EINMAL vor der Symbolschleife gelesen (nicht je Symbol): der LPT-Dispatch-Schlüssel
    # ist strategie-, nicht symbolspezifisch (Rohmaterial ist der Median über den letzten Report).
    _study_wallclock_by_strategy = _read_last_study_wallclock_by_strategy()

    # Issue #1082 Fix Punkt (a) — EINMAL vor der Symbolschleife: Paare, deren
    # check_objective_branch_coverage im JÜNGSTEN Report FAILte, werden 'deprioritized' (#830-Pfad)
    # statt im selben Suchbudget wie bisher erneut evaluiert zu werden.
    _search_budget_deprioritized = _apply_search_budget_proposal(run_id=run_id)
    if _search_budget_deprioritized:
        logging.getLogger("optimizer").info(
            "[#1082] %d Paar(e) über check_objective_branch_coverage deprioritisiert (Suchbudget-"
            "Vorschlag des letzten Laufs): %s", len(_search_budget_deprioritized),
            sorted(_search_budget_deprioritized),
        )

    proposals: list[Path] = []
    # Issue #933 (Pitfall #306) — kumulative Zähler für SWEEP_PROGRESS, je Symbol fortgeschrieben
    # (kein zweiter Scan über bereits verarbeitete Symbole nötig).
    _cumulative_trials_done = 0
    _cumulative_eligible_total = 0
    for symbol, symbol_pairs in pairs_by_symbol.items():
        # Issue #908 Fix 2 — die #842-Prognose hat eine Kürzung angeordnet: ab hier keine weiteren
        # Symbole starten (die bereits laufenden/abgeschlossenen bleiben unangetastet). Der Lauf
        # endet geordnet (Report wird trotzdem geschrieben, #833-Pfad) statt ins Timeout zu laufen.
        if (_wallclock_truncation_limit is not None
                and len(completed_symbols) >= _wallclock_truncation_limit):
            logging.getLogger("optimizer").warning(
                "[#908] Symbolliste bei %d/%d Symbolen gekürzt (Vorab-Prognose #842 überschritt "
                "sweep_max_wallclock_h) — verbleibende Symbole werden in einem Folgelauf via "
                "least_recently_covered-Rotation (#841) nachgeholt.",
                len(completed_symbols), len(pairs_by_symbol),
            )
            break
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

        # Issue #932 (Pitfall #305) — Longest-Processing-Time-Dispatch: die Symbol-Barriere
        # (Join VOR dem nächsten Symbol) kostet die Differenz zwischen längster und medianer
        # Study — absteigend nach dem aus dem letzten Report gelesenen Erfahrungswert dispatchen
        # senkt diese Wartezeit messbar, ohne die grössere, bewusst zurückgestellte
        # Pipelining-Restrukturierung zu benötigen. Unbekannte Strategien (kein Vorlauf-Report)
        # sortieren als Median-Priorität (0.0) ans Ende, nicht ans (zufällige) Ende der
        # Enumerationsreihenfolge.
        symbol_pairs = sorted(
            symbol_pairs,
            key=lambda p: _study_wallclock_by_strategy.get(p[0], 0.0),
            reverse=True,
        )
        _dispatch_t0 = time.perf_counter()
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
        # Issue #932 Fix — Barriere-Wartezeit je Symbol telemetriert: die Differenz zwischen der
        # tatsächlichen Batch-Wallclock (alle Studies dieses Symbols) und der Wallclock der
        # MEDIAN-Study — die Zeit, die der Dispatch auf die längste Study wartete, während kürzere
        # Studies bereits fertig waren.
        _symbol_dispatch_wallclock_s = time.perf_counter() - _dispatch_t0
        _study_wallclocks_this_symbol = [
            w for s in symbol_studies
            for w in [(getattr(s, "user_attrs", {}) or {}).get("wallclock_s")] if w is not None
        ]
        _barrier_wait_s = (
            round(_symbol_dispatch_wallclock_s - statistics.median(_study_wallclocks_this_symbol), 1)
            if _study_wallclocks_this_symbol else None
        )
        emit_execution_event(logging.getLogger("optimizer"), "SYMBOL_DISPATCH_COMPLETED", {
            "symbol": symbol,
            "n_studies": len(symbol_pairs),
            "dispatch_wallclock_s": round(_symbol_dispatch_wallclock_s, 1),
            "barrier_wait_s": _barrier_wait_s,
        })

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
                _record_coverage(symbol)
                continue

        # Issue #939 — der komplette Confirm/Export-Abschnitt EINES Symbols laeuft unter einer
        # Fehler-Isolation: vor diesem Fix propagierte JEDE Exception hier (z.B. ein Serialisierungs-
        # fehler in der Telemetrie, eine defekte Study) nach main() und beendete den GESAMTEN Sweep,
        # ohne die verbleibenden Symbole je zu versuchen (#799 isoliert nur die vorgelagerte
        # Optimize-Phase je PAAR, nicht diesen Abschnitt je SYMBOL).
        try:
            # Issue #652 — familienweite Multiplizität NUR über die Studies DIESES Symbols (alle
            # Strategien dieses Symbols sind jetzt abgeschlossen); #695 liefert dieselbe Grundlage
            # als Return-Matrix für die Korrelations-Declusterung in confirm.py.
            symbol_n_family = _family_n_from_studies(
                symbol_pairs, symbol_studies, tournament_cfg=_tournament_cfg_for_family)
            symbol_family_returns = _family_period_returns_from_studies(symbol_pairs, symbol_studies)
            # Issue #822 — Aufschlüsselung, wie viele oos_evaluated Trials je Symbol AUS der obigen
            # Zählung ausgeschlossen wurden (keine Selektions-Teststatistik), nach Grund.
            symbol_n_family_excluded = _family_n_excluded_breakdown_from_studies(
                symbol_pairs, symbol_studies)
            # Issue #826 Fix Punkt 1 — N1 JE STUDY (nicht symbolweit summiert): die tatsächlich an
            # confirm(...) durchgereichte Multiplizität unter promotion_family_scope='per_strategy'.
            symbol_n_family_stage1 = _family_n_stage1_from_studies(
                symbol_pairs, symbol_studies, tournament_cfg=_tournament_cfg_for_family)
            # Issue #1091 (Katalog #924) — die budget-basierte, EINGEFRORENE Symbol-Multiplizität:
            # bit-identisch, unabhängig davon, wie viele Proposals dieser Familie zum Lesezeitpunkt
            # bereits existieren (siehe _family_n_frozen_from_studies-Docstring). Auf JEDE Study
            # dieses Symbols gestempelt, damit auch ein spaeter unabhaengig (SQLite-Neuladen,
            # generate_report_for_run) gelesenes Proposal denselben Wert traegt.
            symbol_n_family_frozen = _family_n_frozen_from_studies(symbol_pairs, symbol_studies)
            for _study in symbol_studies:
                if _study is None:
                    continue
                try:
                    _study.set_user_attr(
                        "deflation_n_family_frozen", symbol_n_family_frozen.get(symbol, 0))
                except Exception:
                    logging.getLogger("optimizer").debug(
                        "[#1091] %s: deflation_n_family_frozen-Stempel fehlgeschlagen (non-fatal).",
                        symbol, exc_info=True)

            _proposals_before_this_symbol = len(proposals)
            for pair, study in zip(symbol_pairs, symbol_studies):
                proposal = _run_confirm_and_export(
                    pair, study, symbol_n_family, symbol_family_returns,
                    symbol_n_family_excluded,
                    n_family_stage1_map=symbol_n_family_stage1,
                    family_scope=_family_scope)
                if proposal is not None:
                    proposals.append(proposal)
            _this_symbol_proposals = proposals[_proposals_before_this_symbol:]
        except Exception as _symbol_exc:
            _failed_symbols_count += 1
            _symbol_status[symbol] = "failed"
            sweep_failed_symbols.append(symbol)
            _remaining = len(pairs_by_symbol) - len(completed_symbols) - 1
            logging.getLogger("optimizer").error(
                "[#939] SYMBOL_FAILED: %s (%s): %s — Sweep setzt mit den verbleibenden %d "
                "Symbolen fort, statt sie zu verwerfen.", symbol, type(_symbol_exc).__name__,
                _symbol_exc, max(0, _remaining), exc_info=True,
            )
            emit_execution_event(logging.getLogger("optimizer"), "SYMBOL_FAILED", {
                "symbol": symbol, "exception_type": type(_symbol_exc).__name__,
                "symbols_completed": len(completed_symbols),
                "failed_symbols_count": _failed_symbols_count,
            }, level=logging.ERROR)
            _failed_frac_now = _failed_symbols_count / max(1, len(pairs_by_symbol))
            if (_failed_symbols_count > _max_failed_symbols_abs
                    and _failed_frac_now > _max_failed_symbols_frac):
                # Issue #939 — Abbruch bleibt möglich, ist jetzt aber eine Politik-Entscheidung mit
                # Schwellenwert (beide Bedingungen verletzt), kein Nebeneffekt einer beliebigen
                # Exception.
                raise RuntimeError(
                    f"[#939] Symbol-Ausfallrate über Schwelle: {_failed_symbols_count} Ausfaelle "
                    f"({_failed_frac_now:.1%}) > max_failed_symbols_abs="
                    f"{_max_failed_symbols_abs} UND max_failed_symbols_frac="
                    f"{_max_failed_symbols_frac:.1%}."
                ) from _symbol_exc
            # completed_symbols wird auch im Fehlerfall gesetzt, damit ein Resume nicht endlos
            # denselben deterministischen Fehler wiederholt (analog #827s Heterogenitäts-Abbruch
            # oben); der Symbolstatus bleibt über SYMBOL_FAILED/symbol_status auditierbar.
            completed_symbols.add(symbol)
            _write_checkpoint()
            continue
        else:
            _symbol_status[symbol] = "completed"

        completed_symbols.add(symbol)
        _write_checkpoint()
        _record_coverage(symbol)

        # Issue #933 (Pitfall #306) — Invarianten, die erst am Ende eines mehrtägigen Laufs
        # auswerten, sind Obduktionen statt Wächter. Jede symbol-lokal auswertbare Invariante wird
        # SOFORT nach diesem Symbol als INVARIANT_RESULT-Event emittiert — der bestehende
        # #839-Fail-Fast-Pfad (oben) bleibt der SPEZIALFALL 'blocking' auf demselben zugrunde
        # liegenden invariant_checks, nicht ein getrennter Mechanismus. Symbol-lokal scoped (nur
        # DIESES Symbols Proposals, nicht die kumulierte Liste) — O(1) je Symbol statt O(n) über
        # den gesamten bisherigen Lauf.
        if _this_symbol_proposals:
            try:
                from automation.optimizer import report as _report_progress_mod
                _symbol_probe = _report_progress_mod.build_probe_report(
                    _this_symbol_proposals, run_id=run_id, report_source="progress_symbol_probe")
                for _chk in _symbol_probe.get("invariant_checks") or []:
                    emit_execution_event(
                        logging.getLogger("optimizer"), "INVARIANT_RESULT",
                        {"symbol": symbol, "name": _chk.get("name"),
                         "status": "PASS" if _chk.get("passed") else "FAIL",
                         "severity": _chk.get("severity"),
                         "observed": _chk.get("actual"), "expected": _chk.get("expected"),
                         "detail": _chk.get("detail")},
                        level=logging.INFO if _chk.get("passed") else logging.WARNING,
                    )
            except Exception:
                logging.getLogger("optimizer").warning(
                    "[#933] Symbol-lokale Invarianten-Probe fehlgeschlagen (non-fatal, Lauf setzt "
                    "fort).", exc_info=True,
                )

        # Issue #933 Fix 2 — SWEEP_PROGRESS je Symbol: die Grösse, an der #931 (Zeitbudget-
        # Überschreitung) zur LAUFZEIT statt erst nach dem gesamten Sweep erkennbar wird.
        _cumulative_trials_done += sum(len(getattr(s, "trials", None) or []) for s in symbol_studies)
        _cumulative_eligible_total += sum(
            1 for s in symbol_studies for t in (getattr(s, "trials", None) or [])
            if (getattr(t, "user_attrs", {}) or {}).get("oos_eligible") is True
        )
        _elapsed_h_progress = (time.perf_counter() - sweep_t0) / 3600.0
        _symbols_done_progress = len(completed_symbols)
        _symbols_total_progress = len(pairs_by_symbol)
        _projected_total_h = (
            _elapsed_h_progress / _symbols_done_progress * _symbols_total_progress
            if _symbols_done_progress else None
        )
        emit_execution_event(logging.getLogger("optimizer"), "SWEEP_PROGRESS", {
            "symbols_done": _symbols_done_progress,
            "symbols_total": _symbols_total_progress,
            "elapsed_h": round(_elapsed_h_progress, 3),
            "projected_total_h": (
                round(_projected_total_h, 3) if _projected_total_h is not None else None),
            "trials_done": _cumulative_trials_done,
            "eligible_total": _cumulative_eligible_total,
        })

        # Issue #933 Fix 4 — Report-Datei nach JEDEM Symbol atomar aktualisiert (statt erst am
        # Sweep-Ende), damit ein laufender Sweep vor seinem Abschluss beobachtbar ist. Dieselbe
        # write_json_atomic-Garantie wie der finale Report (#720/#742); non-fatal, analog der
        # Fail-Fast-Probe oben — ein Zwischenreport-Fehler darf den Sweep nie stoppen.
        try:
            from automation.optimizer import report as _report_incremental_mod
            _report_incremental_mod.generate_sweep_report(
                proposals, run_id=run_id, run_status="in_progress",
                symbols_completed=_symbols_done_progress, symbols_planned=_symbols_total_progress,
                report_source="progress_incremental",
            )
        except Exception:
            logging.getLogger("optimizer").warning(
                "[#933] Zwischen-Report-Schreiben fehlgeschlagen (non-fatal, Lauf setzt fort).",
                exc_info=True,
            )

        if (not _wallclock_forecast_done
                and len(completed_symbols) >= _wallclock_forecast_after_symbols):
            _wallclock_forecast_done = True
            _elapsed_so_far = time.perf_counter() - sweep_t0
            _rate_s_per_symbol = _elapsed_so_far / len(completed_symbols)
            _forecast = _wallclock_forecast(
                _rate_s_per_symbol, len(pairs_by_symbol), _sweep_max_wallclock_h)
            _rate_min = _rate_s_per_symbol / 60.0
            if _sweep_max_wallclock_h is None:
                logging.getLogger("optimizer").info(
                    "[#842] Takt %.1f min/Symbol · kein Laufzeit-Budget gesetzt.", _rate_min)
            elif _forecast["shortfall"] > 0:
                # Issue #908 Fix 2 — Konsequenz statt nur Warnung: die Symbolliste wird auf die
                # erreichbare Zahl gekürzt (siehe Truncation-Check am Schleifenkopf oben).
                _wallclock_truncation_limit = _forecast["reachable_symbols"]
                logging.getLogger("optimizer").warning(
                    "[#842/#908] Takt %.1f min/Symbol · Budget %.1f h ⇒ Prognose %d von %d Symbolen "
                    "erreichbar. %d Symbole werden NICHT gestartet (ab Position %d der aktuellen "
                    "Reihenfolge) — Symbolliste wird gekürzt statt den Lauf ins Timeout laufen zu "
                    "lassen.", _rate_min, _sweep_max_wallclock_h,
                    _forecast["reachable_symbols"], len(pairs_by_symbol), _forecast["shortfall"],
                    _forecast["reachable_symbols"] + 1,
                )
            else:
                logging.getLogger("optimizer").info(
                    "[#842] Takt %.1f min/Symbol · Budget %.1f h ⇒ Prognose %d von %d Symbolen "
                    "erreichbar. OK.", _rate_min, _sweep_max_wallclock_h,
                    _forecast["reachable_symbols"], len(pairs_by_symbol),
                )

        # Issue #839 — Fail-Fast-Preflight-Probe: EINMAL, sobald genug Symbole für eine belastbare
        # Aussage abgeschlossen sind. Reine Lesefunktion (``report._build_report`` schreibt nichts
        # auf die Platte) über die bereits im Speicher gesammelten Proposal-Pfade dieses Laufs —
        # kein zusätzlicher Backtest, keine Doppelarbeit.
        if (using_real_optimize and _fail_fast_invariants
                and len(completed_symbols) >= _fail_fast_min_symbols
                and sweep_fail_fast_invariant is None):
            _probe_invariant_checks = None
            try:
                from automation.optimizer import report as _report_probe_mod
                # Issue #856 — Root-Cause: dieser Call-Site rief zuvor ``_build_report`` DIREKT mit
                # ``proposals`` (``list[Path]``) auf und umging damit die Path→dict-Normalisierung,
                # die ``generate_sweep_report`` über dieselben vier Zeilen durchführt. Die Probe
                # warf dadurch bei JEDEM Aufruf ``AttributeError: 'PosixPath' object has no
                # attribute 'get'`` — 0 von 32 erfolgreichen Aufrufen im Referenzlauf ``c00dc3c0``.
                # ``build_probe_report`` ist der einzige externe Konsument von ``_build_report``
                # neben ``generate_sweep_report`` selbst (Pitfall #269).
                _probe_report = _report_probe_mod.build_probe_report(
                    proposals, run_id=run_id, report_source="fail_fast_probe")
                _probe_invariant_checks = _probe_report.get("invariant_checks")
                # Issue #941/#1107 — festgehalten fuer den FINALEN generate_sweep_report-Aufruf in
                # main() (siehe dortige Verwendung von sweep_fail_fast_probe_invariant_checks):
                # dieselbe In-Process-Kohorte, gegen die der Fail-Fast-Pfad tatsaechlich urteilte.
                sweep_fail_fast_probe_invariant_checks = _probe_invariant_checks
                sweep_fail_fast_invariant = _first_failing_fail_fast_invariant(
                    _probe_invariant_checks, _fail_fast_invariants)
                _fail_fast_probe_errors = 0
            except Exception:
                _fail_fast_probe_errors += 1
                if _fail_fast_probe_errors >= 2:
                    # Issue #856 Fix Punkt 3 / Pitfall #270 — ein zweiter aufeinanderfolgender
                    # Fehlschlag ist kein transientes Rauschen mehr, sondern ein dauerhaft defekter
                    # Wächter. Eskalation auf ERROR + strukturiertes Event, danach Deaktivierung
                    # der Probe für den Rest des Laufs, statt denselben Traceback bis zu
                    # ``symbols_planned``-mal stumm zu wiederholen.
                    logging.getLogger("optimizer").error(
                        "[#856] FAIL_FAST_PROBE_BROKEN: Fail-Fast-Invarianten-Probe ist zum "
                        "%d. Mal in Folge fehlgeschlagen — Probe wird für den Rest des Laufs "
                        "deaktiviert.", _fail_fast_probe_errors, exc_info=True,
                    )
                    emit_execution_event(
                        logging.getLogger("optimizer"), "FAIL_FAST_PROBE_BROKEN",
                        {"consecutive_errors": _fail_fast_probe_errors,
                         "symbols_completed": len(completed_symbols)},
                        level=logging.ERROR,
                    )
                    _fail_fast_invariants = []
                else:
                    logging.getLogger("optimizer").warning(
                        "[#839] Fail-Fast-Invarianten-Probe fehlgeschlagen (non-fatal, Lauf setzt "
                        "fort).", exc_info=True,
                    )
            if sweep_fail_fast_invariant is not None:
                # Issue #877 — die Konsequenz gehört auf die Streuungsebene der Evidenz (Pitfall
                # #282): erst ein Offender, der über >= _fail_fast_min_offending_symbols VERSCHIEDENE
                # Symbole streut, ist eine Aussage über die Basisklasse (HourlyStrategyBase etc.).
                # Issue #975 (Katalog B) — VORHER war "Aussage über die Basisklasse" gleichbedeutend
                # mit "globaler Abbruch". Bei ``fail_fast_policy=='quarantine'`` (Default) ist sie das
                # NICHT mehr: eine Systemklassen-Aussage rechtfertigt, ALLE betroffenen (Strategie,
                # Symbol)-Paare zu denylisten, nicht den gesamten Sweep zu töten — der Rest der
                # geplanten Symbole liefert weiterhin auswertbare Studies. Nur
                # ``fail_fast_policy=='abort'`` reproduziert das alte, bit-identische Abbruchverhalten.
                _offending_pairs, _offending_symbols = _offending_pairs_for_fail_fast_check(
                    _probe_invariant_checks, sweep_fail_fast_invariant)
                _systemic, _fail_fast_policy_effective = _fail_fast_systemic_verdict(
                    n_symbols_planned=_n_symbols_planned,
                    offending_pairs=_offending_pairs, offending_symbols=_offending_symbols,
                    total_studies_single_symbol=_total_studies_single_symbol,
                    min_offending_symbols=_fail_fast_min_offending_symbols,
                    min_offending_studies=_fail_fast_min_offending_studies,
                    min_offending_studies_frac=_fail_fast_min_offending_studies_frac,
                    policy=_fail_fast_policy,
                )
                # Kein parsbarer (Strategie, Symbol)-Offender (Check ohne die "<strategy>/<symbol>"-
                # actual-Konvention, z. B. ein global-scope-Check) ⇒ die Streuung ist nicht
                # ermittelbar ⇒ konservativ global abbrechen (Pre-#877-Verhalten), UNABHAENGIG von
                # ``fail_fast_policy`` — eine Quarantäne kann kein konkretes Paar benennen.
                if not _offending_pairs or (_systemic and _fail_fast_policy_effective == "abort"):
                    if _n_symbols_planned == 1:
                        logging.getLogger("optimizer").error(
                            "[#839/#1016] FAIL_FAST_INVARIANT: %s FAILt auf %d von %d Studies "
                            "des einzigen geplanten Symbols — Sweep bricht sofort ab "
                            "(run_status=aborted_invariant), statt als 'complete' zu erscheinen.",
                            sweep_fail_fast_invariant, len(_offending_pairs),
                            _total_studies_single_symbol,
                        )
                    else:
                        logging.getLogger("optimizer").error(
                            "[#839] FAIL_FAST_INVARIANT: %s FAILt nach %d Symbol(en) auf %d "
                            "verschiedenen Symbolen — Sweep bricht sofort ab "
                            "(run_status=aborted_invariant), statt nach allen Symbolen spät zu "
                            "scheitern.", sweep_fail_fast_invariant, len(completed_symbols),
                            len(_offending_symbols),
                        )
                    emit_execution_event(
                        logging.getLogger("optimizer"), "SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT",
                        {"check": sweep_fail_fast_invariant,
                         "symbols_completed": len(completed_symbols),
                         "offending_symbols": sorted(_offending_symbols),
                         "offending_studies": _offending_pairs,
                         "symbols_probed": len(completed_symbols)},
                        level=logging.ERROR,
                    )
                    break
                else:
                    # Issue #938 — ``_offending_pairs`` ist ab jetzt String-Key-basiert
                    # (``pair_key``); ``split_pair_key`` ist die Kehrfunktion.
                    for _pk in sorted(_offending_pairs):
                        _strat, _sym = split_pair_key(_pk)
                        record_diagnosed_pair({
                            "strategy": _strat, "symbol": _sym, "action": "denylist",
                            "binding_cause": "fail_fast_quarantine",
                            "stop_reason": sweep_fail_fast_invariant,
                            "symbol_quarantined_reason": (
                                f"[#877] {sweep_fail_fast_invariant} FAILt symbol-lokal "
                                f"({_offending_pairs.get(_pk)}) — "
                                + (f"keine ausreichende Streuung ({len(_offending_symbols)} < "
                                   f"{_fail_fast_min_offending_symbols} Symbole) fuer einen "
                                   "globalen Abbruch." if not _systemic else
                                   f"Streuung ueber {len(_offending_symbols)} Symbole erreicht "
                                   "die Systemklassen-Schwelle, aber fail_fast_policy='quarantine' "
                                   "(#975) denylisted statt den gesamten Sweep abzubrechen.")
                            ),
                            "expires_after_runs": _DEFAULT_QUARANTINE_EXPIRES_AFTER_RUNS,
                        }, work_dir=None, run_id=run_id)
                        sweep_fail_fast_quarantined_pairs.append((_strat, _sym))
                    logging.getLogger("optimizer").warning(
                        "[#877/#975] SYMBOL_QUARANTINED_ON_FAIL_FAST_INVARIANT: %s FAILt auf %d "
                        "Symbol(en) (%s, systemic=%s, policy=%s) — quarantänisiert statt Sweep "
                        "abzubrechen, Lauf setzt fort.", sweep_fail_fast_invariant,
                        len(_offending_symbols), sorted(_offending_symbols), _systemic,
                        _fail_fast_policy,
                    )
                    emit_execution_event(
                        logging.getLogger("optimizer"), "SYMBOL_QUARANTINED_ON_FAIL_FAST_INVARIANT",
                        {"check": sweep_fail_fast_invariant,
                         "symbols_completed": len(completed_symbols),
                         "offending_symbols": sorted(_offending_symbols),
                         "offending_studies": _offending_pairs,
                         "symbols_probed": len(completed_symbols),
                         # Issue #975 — sichtbar machen, OB dieser Fall die Systemklassen-Schwelle
                         # erreicht hat (und damit ohne #975 abgebrochen hätte) oder eine reine
                         # Symbol-lokale #877-Quarantäne war.
                         "systemic": _systemic, "fail_fast_policy": _fail_fast_policy},
                        level=logging.WARNING,
                    )
                    # Die Probe darf nach Quarantäne erneut urteilen, sobald weitere Symbole
                    # abgeschlossen sind (Sweep läuft fort statt abzubrechen).
                    sweep_fail_fast_invariant = None

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
    # Issue #1086 (Katalog #919) — Lock-Freigabe am regulaeren Laufende. Der Abbruchpfad
    # (Exception/SIGINT aus dieser Funktion) wird von ``main()``s ``finally``-Block abgedeckt.
    if using_real_optimize:
        _release_sweep_run_lock(run_id)
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


def _strategies_fingerprint(strategies: list[str], opt_data: dict | None) -> str:
    """Issue #840 Punkt 5 — SHA-256 über die aktive Strategien-Liste + reward_semantics_version +
    simulation_semantics_version (#854). Weicht der Fingerprint eines --resume-Checkpoints vom
    aktuell errechneten ab, mischt der Resume Studies zweier Semantiken in dieselbe #652-Familie —
    der Aufrufer (``main()``) lehnt den Resume dann fail-loud ab, statt still fortzufahren."""
    import hashlib
    opt_data = opt_data or {}
    payload = json.dumps({
        "strategies": sorted(strategies),
        "reward_semantics_version": opt_data.get("reward_semantics_version"),
        "simulation_semantics_version": opt_data.get("simulation_semantics_version"),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> list[Path]:
    # Issue #773/#833 — EINE global-Deklaration fuer die GESAMTE Funktion (Python verbietet
    # mehrere `global`-Statements fuer denselben Namen in verschiedenen Zweigen EINER Funktion,
    # wenn dazwischen bereits zugewiesen wurde — "name is assigned to before global declaration").
    global _LAST_REPORT_PATH

    parser = argparse.ArgumentParser(description="Per-symbol micro-tuning sweep (Ansatz 4). Never enters Phase 5.")
    parser.add_argument("--strategies", default="all", help="'all' (aktive aus strategies.json) oder Komma-Liste")
    parser.add_argument("--symbols", default="all", help="'all' (Universum) oder Komma-Liste")
    parser.add_argument("--tier", default="deployable", choices=["deployable", "refine", "all"])
    parser.add_argument("--n-jobs", type=int, default=None,
                        help="Parallele Studies (je eigene SQLite-Datei). Fehlt das Flag, greift "
                             "optimizer.json['sweep_max_workers'] (Default max(1, cpu_count()-2)).")
    # Issue #842 — CLI-Override fuer optimizer.json['sweep_max_wallclock_h']; 0/negativ ⇒ kein
    # Budget (analog dem bestehenden null-Wert in der Config).
    parser.add_argument("--max-wallclock-h", type=float, default=None,
                        help="Ueberschreibt optimizer.json['sweep_max_wallclock_h'] (Stunden). "
                             "<= 0 deaktiviert das Laufzeit-Budget fuer diesen Lauf.")
    # Issue #833 Fix Punkt 4 — rekonstruiert den #742-Report aus den bereits auf der Platte
    # liegenden Proposals/Studies eines FRÜHEREN (ggf. abgebrochenen) Laufs, OHNE selbst zu
    # optimieren. Nutzt exakt denselben Kern (report.generate_report_for_run) wie der Abbruch-Pfad
    # unten — genau der Fall "ein Lauf endete ohne Report" (siehe Katalog-#750-Nachtrag).
    parser.add_argument("--report-only", metavar="RUN_ID", default=None,
                        help="Erzeugt/rekonstruiert nur den Report fuer RUN_ID aus den vorhandenen "
                             "proposal_*.json (keine neue Optimierung).")
    # Issue #840 (Pitfall #264, 6. Wiederkehr von #237) — der #799-Fortschritts-Checkpoint war
    # vollstaendig implementiert, aber ohne CLI-Einstieg unerreichbar: main() erzeugte bei JEDEM
    # Aufruf ueber default_run_id() eine NEUE run_id ⇒ frischer Checkpoint, JEDER Abbruch bei einem
    # Laufzeit-Budget kostete den GESAMTEN Fortschritt.
    _resume_group = parser.add_mutually_exclusive_group()
    _resume_group.add_argument("--run-id", metavar="RUN_ID", default=None,
                               help="Setzt die run_id explizit (treibt Logdatei/#799-Checkpoint/"
                                    "#742-Report gemeinsam).")
    _resume_group.add_argument("--resume", metavar="RUN_ID", nargs="?", const="__LATEST__", default=None,
                               help="Setzt run_id auf den zuletzt geschriebenen Sweep-Checkpoint "
                                    "(ohne Argument) oder validiert die explizit genannte RUN_ID "
                                    "dagegen. Fehlt der Checkpoint oder weicht sein "
                                    "strategies_fingerprint ab ⇒ Exit-Code 2 (fail-loud statt "
                                    "stiller Neustart).")
    args = parser.parse_args(argv)

    if args.report_only:
        from automation.optimizer import report as _report
        report_path = _report.generate_report_for_run(run_id=args.report_only)
        print(f"📄 Report: {report_path}")
        _LAST_REPORT_PATH = report_path
        return []

    strategies = _resolve_strategies(args.strategies)
    symbols = None if args.symbols == "all" else [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Issue #740 — EIN run_id für den gesamten Lauf: treibt sowohl den nicht-rotierenden Pro-Lauf-
    # Logger (unten) als auch den Dateinamen des #742-Sweep-Reports (am Ende dieser Funktion) —
    # dieselbe Provenienz-Kennung verbindet Log-Datei, JSONL-Sidecar (#741) und Report. Issue #840
    # — --run-id/--resume ueberschreiben den frischen Default gezielt.
    run_id = default_run_id()
    if args.run_id:
        run_id = args.run_id
    elif args.resume is not None:
        import sys as _sys
        _checkpoint_path = WORK / "sweep_progress.json"
        if not _checkpoint_path.exists():
            print(f"❌ [#840] --resume: kein Checkpoint unter {_checkpoint_path} gefunden.", file=_sys.stderr)
            _sys.exit(2)
        try:
            _checkpoint_for_resume = json.loads(_checkpoint_path.read_text("utf-8")) or {}
        except (OSError, ValueError) as _e:
            print(f"❌ [#840] --resume: Checkpoint {_checkpoint_path} nicht lesbar: {_e}", file=_sys.stderr)
            _sys.exit(2)
        _checkpoint_run_id = _checkpoint_for_resume.get("run_id")
        if args.resume != "__LATEST__" and args.resume != _checkpoint_run_id:
            print(f"❌ [#840] --resume {args.resume!r}: Checkpoint {_checkpoint_path} gehört zu "
                  f"run_id={_checkpoint_run_id!r}, nicht der angeforderten run_id.", file=_sys.stderr)
            _sys.exit(2)
        run_id = _checkpoint_run_id
        # Issue #840 Punkt 5 — strategies_fingerprint gegen den aktuellen (Strategien +
        # reward_semantics_version + simulation_semantics_version) validieren; ein fehlender
        # Fingerprint (Pre-#840-Checkpoint) ist NICHT prüfbar und wird fail-open zugelassen (mit
        # WARNUNG) statt jeden Alt-Checkpoint hart abzulehnen.
        _checkpoint_fingerprint = _checkpoint_for_resume.get("strategies_fingerprint")
        if _checkpoint_fingerprint is not None:
            _current_fingerprint = _strategies_fingerprint(strategies, _load_optimizer_config())
            if _checkpoint_fingerprint != _current_fingerprint:
                print(f"❌ [#840] --resume: strategies_fingerprint weicht ab (Checkpoint="
                      f"{_checkpoint_fingerprint!r}, aktuell={_current_fingerprint!r}) — ein Resume "
                      "würde Studies zweier Semantiken in dieselbe #652-Familie mischen.",
                      file=_sys.stderr)
                _sys.exit(2)
        completed_from_checkpoint = len(_checkpoint_for_resume.get("completed_symbols") or [])
        symbols_planned_from_checkpoint = _checkpoint_for_resume.get("symbols_planned")
        remaining_from_checkpoint = (
            symbols_planned_from_checkpoint - completed_from_checkpoint
            if symbols_planned_from_checkpoint is not None else None
        )
        # print statt logging: setup_bot_logging() (unten) hat den "optimizer"-Logger an dieser
        # Stelle noch nicht konfiguriert (kein Handler ⇒ Pythons lastResort verwirft INFO, #414).
        print(f"[#840] RESUME run_id={run_id} completed={completed_from_checkpoint}/"
              f"{symbols_planned_from_checkpoint} remaining={remaining_from_checkpoint}")

    main_t0 = time.perf_counter()
    started_at_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    # Issue #414 — Logging EINMALIG initialisieren, BEVOR irgendetwas geloggt wird. Sonst hat
    # getLogger("optimizer") im Standalone-Pfad keinen Handler und Pythons lastResort verwirft alle
    # INFO-`[JSON_EVENT]` (#404-Telemetrie) — nur WARNING+ erreicht stderr. setup_bot_logging haengt
    # einen File- (DEBUG, #740 pro-Lauf NICHT-rotierend) UND einen Stream-Handler (INFO) an und
    # setzt propagate=False (kollidiert NICHT mit Optunas eigenem Logger; KEIN set_verbosity,
    # Pitfall #74).
    setup_bot_logging("optimizer", run_id=run_id)

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
                                         n_jobs_source=n_jobs_source, run_id=run_id,
                                         max_wallclock_h_override=args.max_wallclock_h)
        if sweep_fail_fast_invariant is not None:
            run_status = "aborted_invariant"
        elif wallclock_guard.sweep_wallclock_exceeded.is_set():
            run_status = "aborted_wallclock"
        elif disk_guard.sweep_abort_requested.is_set():
            run_status = "aborted_disk"
        elif sweep_fail_fast_quarantined_pairs:
            # Issue #877 — mindestens ein (Strategie, Symbol)-Paar wurde waehrend dieses Laufs
            # quarantänisiert, statt den Sweep abzubrechen; das ist von einem regulaeren
            # 'complete' unterscheidbar, ohne ein Abbruch-Symptom zu sein (die Symbol-Streuung
            # der Evidenz war zu gering fuer einen globalen Abbruch, siehe check oben).
            run_status = "completed_with_quarantine"
        elif sweep_failed_symbols:
            # Issue #939 — mindestens ein Symbol scheiterte isoliert (SYMBOL_FAILED), der Sweep
            # lief aber mit den verbleibenden Symbolen zu Ende, statt komplett abzubrechen; von
            # einem regulaeren 'complete' unterscheidbar, damit ein Operator die Teil-Abdeckung
            # erkennt, ohne den kompletten Log durchsuchen zu muessen.
            run_status = "completed_with_failures"
        elif args.resume is not None:
            # Issue #840 Punkt 6 — unterscheidet einen fortgesetzten Lauf von einem Ein-Schuss-Lauf
            # im #742-Report, OHNE den kompletten-Fall selbst zu aendern.
            run_status = "resumed_complete"
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
        # Issue #1086 (Katalog #919) — Sicherheitsnetz fuer den Abbruchpfad: bricht
        # ``run_per_symbol_sweep`` per Exception/SIGINT MITTEN im Lauf ab, erreicht es seine
        # eigene Lock-Freigabe (kurz vor ``return proposals``) nicht mehr. Idempotent (prueft den
        # ``run_id``-Besitz), also unschaedlich, falls die Lock-Datei bereits regulaer freigegeben
        # wurde oder nie erworben wurde (Fake-Pfad in Tests).
        _release_sweep_run_lock(run_id)

    for p in proposals:
        print(p)

    # Issue #833 Fix Punkt 3 — symbols_completed/symbols_planned aus dem #799-Checkpoint; fail-open
    # (None/None), falls weder der In-Prozess-Spiegel noch die Checkpoint-Datei etwas hergeben.
    #
    # Issue #942/#1108 (Katalog #960) — PRIMAERE Quelle ist seither ``sweep_symbol_funnel`` (der
    # In-Prozess-Spiegel, den ``_write_checkpoint()`` innerhalb ``run_per_symbol_sweep`` bei jedem
    # Checkpoint aktualisiert): unabhaengig von Datei-I/O und von einem ``run_id``-Mismatch durch
    # einen ANDEREN gleichzeitigen Prozess auf demselben Store. Die Checkpoint-DATEI bleibt Fallback
    # fuer Aufrufer, die ``run_per_symbol_sweep`` nie im eigenen Prozess ausgefuehrt haben
    # (``--report-only``/standalone Rekonstruktion) — Root-Cause #1108: eine reine Datei-Re-Lese OHNE
    # diesen Spiegel liess alle vier Zaehler auf ``None`` fallen, sobald der Lesevorgang aus
    # irgendeinem Grund fehlschlug, obwohl die Arbeit tatsaechlich vollstaendig war (Report C:
    # 14/14 Studies, aber alle vier Zaehler ``null``).
    symbols_completed: int | None = None
    symbols_planned: int | None = None
    symbols_discovered: int | None = None
    symbols_gate1_rejected: int | None = None
    if sweep_symbol_funnel is not None and sweep_symbol_funnel.get("run_id") == run_id:
        symbols_completed = len(sweep_symbol_funnel.get("completed_symbols") or [])
        symbols_planned = sweep_symbol_funnel.get("symbols_planned")
        symbols_discovered = sweep_symbol_funnel.get("symbols_discovered")
        symbols_gate1_rejected = sweep_symbol_funnel.get("symbols_gate1_rejected")
    else:
        try:
            _checkpoint = json.loads((WORK / "sweep_progress.json").read_text("utf-8"))
            if _checkpoint.get("run_id") == run_id:
                symbols_completed = len(_checkpoint.get("completed_symbols") or [])
                symbols_planned = _checkpoint.get("symbols_planned")
                # Issue #942 — optional (aeltere Checkpoints ohne diese Felder bleiben lesbar).
                symbols_discovered = _checkpoint.get("symbols_discovered")
                symbols_gate1_rejected = _checkpoint.get("symbols_gate1_rejected")
        except (OSError, ValueError):
            pass

    # Issue #1065 (Pitfall #? — Vollständigkeit ≠ Gültigkeit) — ``run_status='aborted_invariant'``
    # bedeutet "eine blockierende Invariante hat FAILt", NICHT "Arbeit wurde abgebrochen". Der
    # Fail-Fast-Abbruch (Zeile ~2919) greift erst NACHDEM der letzte geplante Symbol-Durchlauf
    # bereits vollständig abgeschlossen war (der `break` verhindert nur einen NICHT existierenden
    # nächsten Symbol-Start) — in diesem Fall wurden 100 % der geplanten Arbeit tatsächlich
    # gerechnet, nur als nicht entscheidungsfähig verworfen. ``aborted_invariant`` bleibt reserviert
    # für den ECHTEN Abbruch (weniger Symbole abgeschlossen als geplant); vollständige Abdeckung
    # trotz FAIL-Fast-Verdikt wird als eigener Status ``completed_invalid`` unterschieden, damit
    # ``summary_de.py`` "vollständig, aber ungültig" von "abgebrochen, unvollständig" trennen kann.
    if (run_status == "aborted_invariant" and symbols_completed is not None
            and symbols_planned is not None and symbols_completed >= symbols_planned):
        run_status = "completed_invalid"

    # Issue #742 — EIN aggregiertes Report-Artefakt am Ende des Laufs, atomar geschrieben. Darf den
    # Sweep NIE crashen (non-fatal, analog Champion-Store/Retention/Backfill an anderer Stelle).
    try:
        from automation.optimizer import report as _report
        _cli_args = {"strategies": args.strategies, "tier": args.tier, "symbols": args.symbols,
                    # Issue #755 — n_workers je Lauf im Report nachvollziehbar (Determinismus-Nachweis
                    # bei n_jobs>1, jetzt auch bei gesetztem Seed zulaessig).
                    "n_jobs": eff_n_jobs, "n_jobs_source": n_jobs_source,
                    # Issue #985 (Katalog D, P1) — die aufgeloeste Host-Kernzahl neben n_jobs, damit
                    # ein DEFAULT_CPU_MINUS_2-Lauf im Report nachvollziehbar bleibt, auch wenn der
                    # Host zwischen zwei Laeufen wechselt (Reproduzierbarkeits-Telemetrie, #985 Fix 2).
                    "host_cpu_count": __import__("os").cpu_count()}
        _wallclock_s = round(time.perf_counter() - main_t0)
        # Issue #941/#1107 — die In-Process-Kohorte der Fail-Fast-Probe (sofern eine stattfand),
        # damit invariants.check_cohort_declaration_consistency sie gegen die Report-Scan-Kohorte
        # DIESES finalen Artefakts vergleichen kann.
        _prior_probe_checks = sweep_fail_fast_probe_invariant_checks
        # Issue #942/#1108 (Katalog #960) — durchgereicht, damit ``_build_report`` daraus (zusammen
        # mit ``symbols_completed``/``symbols_planned``) die drei orthogonalen Achsen
        # (``work_completed``/``decision_admissible``/``fail_fast_triggered``) EINMAL, an EINER
        # Stelle, fuer BEIDE Pfade (regulaerer Abschluss/Abbruch) ableitet — statt ``run_status``
        # als einzige, ueberladene Wahrheit ueber zwei getrennte Code-Pfade zu setzen.
        _fail_fast_triggered = sweep_fail_fast_invariant
        if run_status == "complete":
            report_path = _report.generate_sweep_report(
                proposals, run_id=run_id, started_at_utc=started_at_utc,
                wallclock_s=_wallclock_s, cli_args=_cli_args, run_status=run_status,
                symbols_completed=symbols_completed, symbols_planned=symbols_planned,
                symbols_discovered=symbols_discovered,
                symbols_gate1_rejected=symbols_gate1_rejected,
                prior_probe_invariant_checks=_prior_probe_checks,
                fail_fast_triggered=_fail_fast_triggered,
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
                symbols_discovered=symbols_discovered,
                symbols_gate1_rejected=symbols_gate1_rejected,
                prior_probe_invariant_checks=_prior_probe_checks,
                fail_fast_triggered=_fail_fast_triggered,
            )
        # Issue #1016 (Katalog #858, Fix Punkt 2, Pitfall #346) — ein Lauf mit mindestens einer
        # blockierenden Invarianten-FAIL darf nicht dasselbe Statuswort 'complete' tragen wie ein
        # sauberer Lauf ("complete" bei zwei blockierenden FAILs UND "complete" bei null FAILs war
        # ununterscheidbar). Die Invarianten sind erst INNERHALB des gerade geschriebenen Reports
        # bekannt (generate_sweep_report berechnet sie selbst) — daher ein Read-Back-Patch statt
        # einer Vorab-Berechnung; dieselbe write_json_atomic-Garantie wie der Erstschrieb.
        if run_status == "complete":
            run_status = _downgrade_run_status_for_blocking_invariants(report_path)
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

    # Issue #933 (Pitfall #306) — die LETZTE Zeile jedes Laufs, egal ob er sauber durchlief oder
    # per SIGINT/SIGTERM/Exception abbrach: vorher war ein Snapshot eines laufenden Sweeps von
    # einem abgestürzten nur über die Trial-Kadenz im Log unterscheidbar (#933-Symptom). status
    # 'completed' NUR bei run_status=='complete' — jeder andere run_status (aborted_*, partial,
    # ...) ist SWEEP_ABORTED, unabhängig vom genauen Grund (der steht in run_status selbst).
    emit_execution_event(
        logging.getLogger("optimizer"),
        "SWEEP_COMPLETED" if run_status == "complete" else "SWEEP_ABORTED",
        {"run_id": run_id, "run_status": run_status,
         "symbols_completed": symbols_completed, "symbols_planned": symbols_planned,
         # Issue #939 — auditierbar, WELCHE Symbole isoliert scheiterten (statt nur, dass
         # run_status von 'complete' abweicht).
         "failed_symbols": sorted(sweep_failed_symbols)},
        level=logging.INFO if run_status == "complete" else logging.WARNING,
    )

    # Issue #833 — der urspruengliche Abbruch (SIGINT/SIGTERM/unerwartete Exception) wird nach dem
    # Report-Versuch WEITERGEREICHT: das Artefakt ist ein Nebeneffekt auf dem Weg nach draussen,
    # kein stilles Verschlucken des eigentlichen Fehlers (Exit-Code/Traceback bleiben sichtbar).
    if caught_exc is not None:
        raise caught_exc

    return proposals


# Issue #773 — siehe Kommentar in main() oben.
_LAST_REPORT_PATH: "Path | None" = None


def _wallclock_forecast(rate_s_per_symbol: float, n_planned: int, max_wallclock_h: float | None) -> dict:
    """Issue #842 Punkt 3 — reine Vorhersagefunktion: der gemessene Median-Takt (Sekunden/Symbol,
    aus den ersten ``wallclock_forecast_after_symbols`` abgeschlossenen Symbolen) wird gegen das
    (ggf. fehlende) Laufzeit-Budget hochgerechnet. ``max_wallclock_h=None`` (kein Budget) ⇒ alle
    ``n_planned`` Symbole gelten als erreichbar (nichts zu warnen)."""
    if max_wallclock_h is None or rate_s_per_symbol <= 0:
        return {"reachable_symbols": n_planned, "shortfall": 0, "budget_h": max_wallclock_h}
    budget_s = float(max_wallclock_h) * 3600.0
    reachable = min(int(budget_s // rate_s_per_symbol), n_planned)
    shortfall = max(0, n_planned - reachable)
    return {"reachable_symbols": reachable, "shortfall": shortfall, "budget_h": max_wallclock_h}


def _first_failing_fail_fast_invariant(invariant_checks: list[dict], fail_fast_invariants: list[str]) -> str | None:
    """Issue #839 — reine Entscheidungsfunktion: welcher (falls einer) der in
    ``optimizer.json['fail_fast_invariants']`` gelisteten Check-Namen in ``invariant_checks``
    (z. B. aus ``report._build_report(...)['invariant_checks']``) FAILt. ``None`` ⇒ keiner der
    gelisteten Checks ist verletzt (der Sweep läuft weiter)."""
    for chk in invariant_checks or []:
        if chk.get("name") in fail_fast_invariants and not chk.get("passed", True):
            return chk.get("name")
    return None


# Issue #877 — Ablauf-Frist eines quarantänisierten (Strategie, Symbol)-Paars (siehe
# record_diagnosed_pair/_EXPIRES_AFTER_RUNS_BY_CAUSE) — nicht permanent, weil die Ursache eine
# behebbare Datenlücke sein kann (siehe #881).
_DEFAULT_QUARANTINE_EXPIRES_AFTER_RUNS = 10


def _offending_pairs_for_fail_fast_check(
    invariant_checks: list[dict] | None, check_name: str,
) -> tuple[dict[str, object], set[str]]:
    """Issue #877 — reine Entscheidungsfunktion: extrahiert die (Strategie, Symbol)-Offender EINES
    benannten, FAILenden Fail-Fast-Checks aus dessen ``actual``-Feld. Die bisherigen Fail-Fast-Checks
    (z. B. ``check_holding_time_cap``) stempeln ihre Offender als
    ``{"<strategy>/<symbol>": <wert>}`` — dieselbe Konvention wird hier geparst. Ein Check ohne diese
    Konvention (kein ``/`` im Key) liefert eine LEERE Symbolmenge — die Streuung ist dann per
    Konstruktion nicht ermittelbar, und der Aufrufer muss (konservativ) global abbrechen, statt eine
    unbekannte Struktur stillschweigend als "1 Symbol" zu werten.

    Issue #938 (Katalog A) — die Rückgabe verwendet ``_contracts.pair_key`` (String-Keys), NICHT
    rohe ``tuple[str, str]``-Keys: ein Tuple-Key hier floss vor diesem Fix direkt in einen
    ``emit_execution_event``-Payload (``"offending_studies": _offending_pairs``) und löste #937s
    ``json.dumps``-``TypeError`` aus, weil ``json`` nur skalare Dict-Keys automatisch koerziert.

    Issue #1063 — bevor auf den konservativen "Struktur unbekannt ⇒ global abbrechen"-Zweig
    zurückgefallen wird, versucht dieser Parser ZUSÄTZLICH ``provenance['magnitude_offenders']``
    (dieselbe Pair-Konvention, siehe ``check_holding_time_cap``s Magnituden-Ast, #1036) — ein Check,
    der seine Offender (noch) nicht direkt in ``actual`` stempelt, aber sie strukturiert unter
    ``provenance`` führt, ist damit ebenfalls auswertbar, statt die #1016-Breitenschwelle
    (Pitfall #349) grundsätzlich ausser Kraft zu setzen. ``check_holding_time_cap`` selbst stempelt
    seit #1063 beide Äste bereits direkt in ``actual`` (siehe dortiger Docstring) — dieser Fallback
    ist die generelle Absicherung für JEDEN aktuellen/künftigen Fail-Fast-Check derselben Bauart.

    Rückgabe: ``({"strategy/symbol": wert, ...}, {symbol, ...})``."""
    def _parse(actual: object) -> tuple[dict[str, object], set[str]] | None:
        if not isinstance(actual, dict) or not actual:
            return None
        pairs: dict[str, object] = {}
        symbols: set[str] = set()
        for key, value in actual.items():
            if not isinstance(key, str) or "/" not in key:
                return None
            strategy, _, symbol = key.partition("/")
            pairs[pair_key(strategy, symbol)] = value
            symbols.add(symbol)
        return pairs, symbols

    for chk in invariant_checks or []:
        if chk.get("name") != check_name:
            continue
        parsed = _parse(chk.get("actual"))
        if parsed is not None:
            return parsed
        parsed = _parse((chk.get("provenance") or {}).get("magnitude_offenders"))
        if parsed is not None:
            return parsed
        return {}, set()
    return {}, set()


def _fail_fast_systemic_verdict(
    *, n_symbols_planned: int, offending_pairs: dict, offending_symbols: set[str],
    total_studies_single_symbol: int, min_offending_symbols: int,
    min_offending_studies: int, min_offending_studies_frac: float, policy: str,
) -> tuple[bool, str]:
    """Issue #1016 (Katalog #858, Fix Punkt 1, Pitfall #349) — reine Entscheidungsfunktion: ist die
    Evidenz-Streuung eines Fail-Fast-Offenders "systemisch" (eine Aussage über die Basisklasse,
    nicht ein einzelnes Symbol), und welche ``fail_fast_policy`` gilt dafür effektiv?

    Root-Cause: die #877-Symbol-Streuungs-Schwelle (``min_offending_symbols``, Default 2) wurde
    gegen Vollläufe über 143 Symbole kalibriert. Bei GENAU EINEM geplanten Symbol ist sie
    strukturell unerreichbar (``offending_symbols`` kann nie mehr als 1 Mitglied haben) — das
    deaktiviert die Fail-Fast-Konsequenz genau in dem Modus, in dem ein einzelnes Paar für den
    Kapitaleinsatz feingetunt wird ("Schutz aus", nicht "Schutz mild"). Bei ``n_symbols_planned
    == 1`` ersetzt eine STUDY-Quote die Symbol-Streuung: >= ``min_offending_studies`` ODER >= der
    konfigurierten ``min_offending_studies_frac`` der Studies DIESES Symbols. Ein Ein-Symbol-Lauf
    hat — anders als ein Vollauf — keine "übrigen Symbole", auf die eine Quarantäne ausweichen
    könnte; eine erreichte Study-Quote erzwingt daher UNBEDINGT ``policy='abort'``, unabhängig vom
    konfigurierten ``policy``-Wert.

    Rückgabe: ``(systemic, effective_policy)``."""
    if n_symbols_planned == 1:
        systemic = bool(offending_pairs) and (
            len(offending_pairs) >= min_offending_studies
            or (total_studies_single_symbol > 0
                and len(offending_pairs) / total_studies_single_symbol >= min_offending_studies_frac)
        )
        return systemic, ("abort" if systemic else policy)
    return len(offending_symbols) >= min_offending_symbols, policy


def _downgrade_run_status_for_blocking_invariants(report_path) -> str:
    """Issue #1016 (Katalog #858, Fix Punkt 2, Pitfall #346) — liest das gerade geschriebene
    #742-Report-Artefakt zurück und korrigiert dessen ``run_status`` von ``'complete'`` auf
    ``'complete_with_blocking_invariants'``, sobald mindestens ein ``severity='blocking'``-Check
    fehlgeschlagen ist ("complete" bei blockierenden FAILs und "complete" bei null FAILs waren
    zuvor ununterscheidbar — ein Cap ist eine Zensur, ein geteiltes Statuswort ist es analog).
    Der Patch ist atomar (``write_json_atomic``, dieselbe Garantie wie der Erstschrieb) und
    fail-open bei jedem Lese-/Parse-Fehler (gibt ``'complete'`` unverändert zurück, non-fatal —
    ein defektes Report-Artefakt darf den Lauf nicht zusätzlich verschlechtern).

    Rückgabe: der (ggf. korrigierte) ``run_status``-String, den der Aufrufer als neue Wahrheit
    übernimmt.

    Issue #942/#1108 (Katalog #960) — der bereits geschriebene Report traegt seit diesem Fix
    zusaetzlich ``decision_admissible`` (in ``report._build_report`` aus DERSELBEN
    ``invariant_checks``-Liste abgeleitet, die diese Funktion hier erneut liest) — beide Werte
    sind IMMER konsistent (dieselbe Quelle), diese Funktion bleibt fuer die Rueckwaertskompatibilitaet
    des ``run_status``-Strings bestehen, ist aber NICHT mehr die kanonische Wahrheit ueber
    Zulaessigkeit; das ist seither ``decision_admissible``."""
    try:
        written_report = json.loads(Path(report_path).read_text("utf-8"))
        blocking_fails = [
            c for c in (written_report.get("invariant_checks") or [])
            if c.get("severity") == "blocking" and not c.get("passed", True)
        ]
        if not blocking_fails:
            return "complete"
        run_status = "complete_with_blocking_invariants"
        written_report["run_status"] = run_status
        write_json_atomic(report_path, written_report)
        logging.getLogger("optimizer").warning(
            "[#1016] %d blockierende Invarianten-FAIL(s) (%s) — run_status auf "
            "'complete_with_blocking_invariants' korrigiert (kein Lauf mit blockierenden FAILs "
            "darf als 'complete' erscheinen).", len(blocking_fails),
            ", ".join(sorted({c.get("name") or c.get("check") for c in blocking_fails})),
        )
        return run_status
    except Exception:
        logging.getLogger("optimizer").warning(
            "[#1016] Blockierende-Invarianten-Nachpruefung fehlgeschlagen (non-fatal, run_status "
            "bleibt 'complete').", exc_info=True,
        )
        return "complete"


def _report_has_failing_invariant(report_path) -> bool:
    """Issue #773 — liest das generierte #742-Report-Artefakt und meldet, ob mindestens ein
    Invarianten-Check FAILED ist. Fail-open (``False``) bei jedem Lese-/Parse-Fehler — ein
    defektes Report-Artefakt soll den Exit-Code nicht zusaetzlich verschlechtern."""
    try:
        data = json.loads(Path(report_path).read_text("utf-8"))
        return any(not c.get("passed", True) for c in data.get("invariant_checks", []))
    except Exception:
        return False


def create_bayesian_tpe_study(study_name: str, storage: str | None = None, storage_url: str | None = None, seed: int = 42) -> "optuna.Study":
    """Issue #796 — Create Bayesian TPE Study for EV CHF Optimization."""
    import optuna
    st = storage or storage_url
    sampler = optuna.samplers.TPESampler(seed=seed, multivariate=True, group=True)
    return optuna.create_study(study_name=study_name, storage=st, sampler=sampler, direction="maximize")


def objective_ev_chf(trial: object) -> float:
    """Issue #796/#980 — EV CHF continuous feasibility objective."""
    if isinstance(trial, dict):
        attrs = trial
    else:
        attrs = getattr(trial, "user_attrs", {}) or {}
    if not attrs.get("oos_evaluated", False):
        return -50.0
    win_rate = float(attrs.get("oos_win_rate", 0.0))
    avg_win = float(attrs.get("oos_avg_win_chf", 0.0))
    avg_loss = float(attrs.get("oos_avg_loss_chf", 0.0))
    turnover = float(attrs.get("oos_turnover_cost_chf", 0.0))
    return (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss) - turnover



if __name__ == "__main__":
    main()
    if _LAST_REPORT_PATH is not None and _report_has_failing_invariant(_LAST_REPORT_PATH):
        import sys as _sys
        logging.getLogger("optimizer").error(
            "[#773] Mindestens ein Invarianten-Check ist FAILED (%s) — Exit-Code 1.",
            _LAST_REPORT_PATH,
        )
        _sys.exit(1)

