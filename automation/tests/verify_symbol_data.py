#!/usr/bin/env python3
"""verify_symbol_data.py — Rohdaten- und Bar-Verifikation je Symbol.

Prüft für jedes angegebene Symbol zuerst die ROHEN Quote-Ticks aus dem Nautilus-Parquet-Katalog,
danach die daraus resampelten 1h-BARS — auf zwei Achsen zum Vergleich:

  1. 24/7-Kalenderachse   — dieselbe Achse, die der aktuelle Preflight
                             (sweep._load_symbol_bar_quality_sample, sweep.py:824) verwendet.
  2. RTH-Session-Achse    — die seit Issue #1275 für die ECHTE Bar-Simulation massgebliche Achse
                             (backtest_runner._filter_ticks_to_session_hours), aufgelöst über
                             instrument_map.json (Asset-Klasse) + backtest.json
                             (session_hours_by_asset_class), inkl. Tick-Raster-Snapping (#1300).

Siehe ISSUES_symbol_scope_fingerprint_integrity_20260831.md, Issue #1329: beide Achsen liefern
für dasselbe Symbol/denselben Lauf aktuell UNTERSCHIEDLICHE bar_coverage_ratio/ticks_per_bar-
Werte — dieses Skript macht die Differenz für jedes Symbol sichtbar.

Bewusst UNABHÄNGIG von automation.catalog_paths/automation.backtest_runner neu implementiert
(keine automation.*-Imports, keine nautilus_trader-Abhängigkeit) — als Verifikationswerkzeug
soll es NICHT denselben Code (und damit potenziell denselben Bug) wie die geprüfte Pipeline
teilen. Nur pandas/pyarrow als Abhängigkeiten, dieselben, die das Repository selbst bereits
voraussetzt.

Nutzung:
    python verify_symbol_data.py --repo-root ~/etoro_nautilus --symbols TSLA.ETORO NVDA.ETORO
    python verify_symbol_data.py --repo-root ~/etoro_nautilus --all
    python verify_symbol_data.py --repo-root ~/etoro_nautilus --all --csv summary.csv
    python verify_symbol_data.py --catalog-path /pfad/zu/data/nautilus --symbols TSLA.ETORO \\
        --no-rth   # nur Rohdaten + 24/7-Bars, keine Session-Aufloesung noetig
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# --------------------------------------------------------------------------------------------
# Katalog-/Spalten-Auflösung (analog automation/catalog_paths.py, absichtlich eigenständig)
# --------------------------------------------------------------------------------------------

_QUOTE_TICK_GLOB_PATTERNS = ("data.parquet", "part-*.parquet", "*.parquet")
_COLUMN_ALIASES = {
    "bid_price": ("bid_price", "bid"),
    "ask_price": ("ask_price", "ask"),
    "ts_event": ("ts_event", "ts_init", "timestamp"),
}
_FSB16_SCALE = 10 ** 16


def resolve_quote_tick_files(catalog_path: Path, symbol: str) -> list[Path]:
    inst_dir = catalog_path / "data" / "quote_tick" / symbol
    if not inst_dir.is_dir():
        return []
    for pattern in _QUOTE_TICK_GLOB_PATTERNS:
        matches = sorted(inst_dir.glob(pattern))
        if matches:
            return matches
    return []


def resolve_quote_tick_columns(schema_names) -> dict[str, str] | None:
    available = set(schema_names)
    resolved = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        hit = next((a for a in aliases if a in available), None)
        if hit is None:
            return None
        resolved[canonical] = hit
    return resolved


def decode_fsb16_price(raw: bytes) -> float:
    return int.from_bytes(raw[:16], "little", signed=True) / _FSB16_SCALE


# --------------------------------------------------------------------------------------------
# Session-Auflösung (analog backtest_runner.py, absichtlich eigenständig)
# --------------------------------------------------------------------------------------------

def resolve_asset_class(symbol: str, instrument_map: dict) -> str | None:
    for _, entry in (instrument_map.get("instruments") or {}).items():
        if entry.get("symbol") == symbol:
            ac = entry.get("asset_class")
            return str(ac).upper() if ac else None
    return None


def resolve_session_hours(asset_class: str | None, session_cfg: dict | None) -> tuple[str, str] | None:
    if not asset_class or not session_cfg:
        return None
    entry = session_cfg.get(asset_class)
    if entry is None:
        return None
    return entry["open_utc"], entry["close_utc"]


def snap_session_window(open_utc: str, close_utc: str, median_delta_t_s: float | None) -> tuple[str, str]:
    if not median_delta_t_s or median_delta_t_s <= 0:
        return open_utc, close_utc
    grid_minutes = max(1, round(median_delta_t_s / 60.0))
    oh, om = (int(x) for x in open_utc.split(":"))
    ch, cm = (int(x) for x in close_utc.split(":"))
    open_total, close_total = oh * 60 + om, ch * 60 + cm
    snapped_open = (open_total // grid_minutes) * grid_minutes
    snapped_close = min(24 * 60, -(-close_total // grid_minutes) * grid_minutes)
    return (f"{snapped_open // 60:02d}:{snapped_open % 60:02d}",
            f"{snapped_close // 60:02d}:{snapped_close % 60:02d}")


def is_within_session_hours(ts: pd.Timestamp, open_utc: str, close_utc: str, *, weekdays_only: bool = True) -> bool:
    if weekdays_only and ts.weekday() >= 5:
        return False
    oh, om = (int(x) for x in open_utc.split(":"))
    ch, cm = (int(x) for x in close_utc.split(":"))
    tod = ts.hour * 60 + ts.minute
    return (oh * 60 + om) <= tod < (ch * 60 + cm)


# --------------------------------------------------------------------------------------------
# Rohdaten laden
# --------------------------------------------------------------------------------------------

def read_raw_ticks(catalog_path: Path, symbol: str, max_ticks: int | None) -> pd.DataFrame | None:
    """Liest bid/ask/ts_event fuer ``symbol``, dekodiert FSB16 zu einem Mid-Preis. ``None`` bei
    fehlender Datei/Spalte/leerem Ergebnis. ``max_ticks`` (optional) begrenzt auf die JUENGSTEN
    N Zeilen (rueckwaerts ueber Row-Groups, wie sweep._load_symbol_bar_quality_sample)."""
    pq_files = resolve_quote_tick_files(catalog_path, symbol)
    if not pq_files:
        print(f"  [FEHLER] kein Parquet unter {catalog_path / 'data' / 'quote_tick' / symbol}", file=sys.stderr)
        return None

    schema_names = pq.read_schema(str(pq_files[0])).names
    col_map = resolve_quote_tick_columns(schema_names)
    if col_map is None:
        print(f"  [FEHLER] Spalten nicht aufloesbar (Schema: {list(schema_names)})", file=sys.stderr)
        return None

    read_cols = [col_map["bid_price"], col_map["ask_price"], col_map["ts_event"]]
    tables = []
    n_read = 0
    for pq_file in reversed(pq_files):
        pf = pq.ParquetFile(str(pq_file))
        for rg in range(pf.metadata.num_row_groups - 1, -1, -1):
            t = pf.read_row_group(rg, columns=read_cols)
            tables.append(t)
            n_read += t.num_rows
            if max_ticks and n_read >= max_ticks:
                break
        if max_ticks and n_read >= max_ticks:
            break

    table = pa.concat_tables(list(reversed(tables)))
    df = table.to_pandas().rename(columns={
        col_map["bid_price"]: "bid_price", col_map["ask_price"]: "ask_price",
        col_map["ts_event"]: "ts_event",
    })
    if max_ticks and len(df) > max_ticks:
        df = df.tail(max_ticks)
    if df.empty:
        print("  [FEHLER] leer nach dem Lesen.", file=sys.stderr)
        return None

    # FSB16 zuerst versuchen (Nautilus High-Precision-Build); bei bereits numerischen Spalten
    # (Standard-Build) faellt der Versuch defensiv auf direkte Numerik zurueck.
    try:
        df["bid"] = df["bid_price"].apply(decode_fsb16_price)
        df["ask"] = df["ask_price"].apply(decode_fsb16_price)
    except (TypeError, AttributeError):
        df["bid"] = pd.to_numeric(df["bid_price"], errors="coerce")
        df["ask"] = pd.to_numeric(df["ask_price"], errors="coerce")

    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["ts"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    df = df.drop(columns=["bid_price", "ask_price", "ts_event"]).set_index("ts").sort_index()
    return df


# --------------------------------------------------------------------------------------------
# Rohdaten-Report
# --------------------------------------------------------------------------------------------

def raw_report(symbol: str, df: pd.DataFrame) -> dict:
    idx = df.index
    n = len(df)
    span_days = max(1e-9, (idx[-1] - idx[0]).total_seconds() / 86400.0)
    deltas_s = idx.to_series().diff().dropna().dt.total_seconds()
    n_dupes = int((deltas_s == 0).sum())
    spread_bps = ((df["ask"] - df["bid"]) / df["mid"] * 1e4).replace([float("inf"), float("-inf")], None).dropna()
    n_crossed = int((df["ask"] < df["bid"]).sum())
    n_nonpositive = int((df["mid"] <= 0).sum())

    metrics = {
        "symbol": symbol,
        "n_ticks": n,
        "window_start": idx[0].isoformat(),
        "window_end": idx[-1].isoformat(),
        "span_days": round(span_days, 1),
        "ticks_per_day": round(n / span_days, 3),
        "median_delta_t_s": round(float(deltas_s.median()), 1) if not deltas_s.empty else None,
        "max_gap_hours": round(float(deltas_s.max()) / 3600.0, 1) if not deltas_s.empty else None,
        "n_duplicate_timestamps": n_dupes,
        "n_crossed_quotes": n_crossed,
        "n_nonpositive_mid": n_nonpositive,
        "spread_bps_median": round(float(spread_bps.median()), 2) if not spread_bps.empty else None,
        "spread_bps_p99": round(float(spread_bps.quantile(0.99)), 2) if not spread_bps.empty else None,
        "mid_min": round(float(df["mid"].min()), 4),
        "mid_max": round(float(df["mid"].max()), 4),
    }

    print(f"  --- Rohdaten ---")
    print(f"  n_ticks={n}  Fenster={metrics['window_start']} .. {metrics['window_end']}  "
          f"({metrics['span_days']} Tage, {metrics['ticks_per_day']} Ticks/Tag)")
    print(f"  median_delta_t_s={metrics['median_delta_t_s']}  groesste_luecke_h={metrics['max_gap_hours']}")
    print(f"  spread_bps median={metrics['spread_bps_median']} p99={metrics['spread_bps_p99']}  "
          f"mid=[{metrics['mid_min']}, {metrics['mid_max']}]")
    if n_dupes:
        print(f"  [WARNUNG] {n_dupes} Ticks mit identischem Zeitstempel zum Vorgaenger.")
    if n_crossed:
        print(f"  [WARNUNG] {n_crossed} gekreuzte Quotes (ask < bid).")
    if n_nonpositive:
        print(f"  [WARNUNG] {n_nonpositive} Ticks mit Mid-Preis <= 0.")
    return metrics


# --------------------------------------------------------------------------------------------
# Bar-Report
# --------------------------------------------------------------------------------------------

def build_bars(df: pd.DataFrame) -> pd.DataFrame:
    agg = df["mid"].resample("1h").agg(["max", "min", "last", "count"])
    return agg.dropna(subset=["max", "min", "last"])


def bar_report(label: str, bars: pd.DataFrame, calendar_hours: float) -> dict:
    if bars.empty:
        print(f"  --- Bars ({label}) --- keine Bars (0 Ticks im Fenster).")
        return {"label": label, "n_bars": 0}

    idx = bars.index
    tick_counts = sorted(int(c) for c in bars["count"].tolist())
    ticks_per_bar_median = statistics.median(tick_counts)
    frac_single_tick = sum(1 for c in tick_counts if c <= 1) / len(tick_counts)
    true_range = bars["max"] - bars["min"]
    frac_zero_range = float((true_range <= 0).mean())
    atr_bps = (true_range / bars["last"] * 1e4)
    coverage_ratio = len(idx) / max(1.0, calendar_hours)

    metrics = {
        "label": label,
        "n_bars": len(idx),
        "coverage_ratio": round(coverage_ratio, 4),
        "ticks_per_bar_median": ticks_per_bar_median,
        "frac_bars_single_tick": round(frac_single_tick, 4),
        "frac_zero_true_range": round(frac_zero_range, 4),
        "atr_median_bps": round(float(atr_bps.median()), 2),
    }
    print(f"  --- Bars ({label}) ---")
    print(f"  n_bars={metrics['n_bars']}  coverage_ratio={metrics['coverage_ratio']} "
          f"(besetzte Stunden / Fenster-Stunden)")
    print(f"  ticks_per_bar_median={metrics['ticks_per_bar_median']}  "
          f"frac_bars_single_tick={metrics['frac_bars_single_tick']}  "
          f"frac_zero_true_range={metrics['frac_zero_true_range']}  "
          f"atr_median_bps={metrics['atr_median_bps']}")
    return metrics


def filter_to_session(df: pd.DataFrame, open_utc: str, close_utc: str, median_delta_t_s: float | None) -> tuple[pd.DataFrame, str, str]:
    open_snap, close_snap = snap_session_window(open_utc, close_utc, median_delta_t_s)
    mask = [is_within_session_hours(ts, open_snap, close_snap) for ts in df.index]
    return df[mask], open_snap, close_snap


def session_calendar_hours(df_full: pd.DataFrame, open_utc: str, close_utc: str) -> float:
    """RTH-Stunden im Gesamtfenster: Handelstage (Mo-Fr) im Fenster * Fensterlaenge in Stunden."""
    oh, om = (int(x) for x in open_utc.split(":"))
    ch, cm = (int(x) for x in close_utc.split(":"))
    window_h = (ch * 60 + cm - (oh * 60 + om)) / 60.0
    days = pd.date_range(df_full.index[0].normalize(), df_full.index[-1].normalize(), freq="D")
    n_weekdays = int((days.weekday < 5).sum())
    return max(1.0, n_weekdays * window_h)


# --------------------------------------------------------------------------------------------
# Orchestrierung je Symbol
# --------------------------------------------------------------------------------------------

def verify_symbol(symbol: str, catalog_path: Path, instrument_map: dict, session_cfg: dict,
                   max_ticks: int | None, do_rth: bool) -> dict:
    print(f"\n{'=' * 70}\n{symbol}\n{'=' * 70}")
    df = read_raw_ticks(catalog_path, symbol, max_ticks)
    if df is None:
        return {"symbol": symbol, "status": "READ_FAILED"}

    raw = raw_report(symbol, df)

    calendar_hours = max(1.0, (df.index[-1] - df.index[0]).total_seconds() / 3600.0)
    bars_247 = build_bars(df)
    b247 = bar_report("24/7-Kalenderachse", bars_247, calendar_hours)

    result = {"symbol": symbol, "status": "OK", "raw": raw, "bars_24_7": b247}

    if not do_rth:
        return result

    asset_class = resolve_asset_class(symbol, instrument_map)
    window = resolve_session_hours(asset_class, session_cfg)
    if window is None:
        print(f"  --- Bars (RTH-Session-Achse) --- kein Session-Fenster fuer Asset-Klasse "
              f"'{asset_class}' konfiguriert (24/7-Markt oder Key fehlt) — identisch zur "
              f"Kalenderachse.")
        result["asset_class"] = asset_class
        result["bars_rth"] = dict(b247, label="RTH-Session-Achse (= 24/7, kein Fenster)")
        return result

    open_utc, close_utc = window
    df_rth, open_snap, close_snap = filter_to_session(df, open_utc, close_utc, raw["median_delta_t_s"])
    if (open_snap, close_snap) != (open_utc, close_utc):
        print(f"  [Hinweis] Session-Fenster {open_utc}-{close_utc} UTC auf Tick-Raster gesnapped "
              f"zu {open_snap}-{close_snap} UTC (#1300).")
    n_session = len(df_rth)
    n_off_session = len(df) - n_session
    print(f"  Asset-Klasse={asset_class}  Session-Fenster={open_snap}-{close_snap} UTC (Mo-Fr)  "
          f"Ticks in Session={n_session}/{len(df)} ({100.0 * n_session / max(1, len(df)):.1f} %)")
    result["asset_class"] = asset_class
    result["n_ticks_session"] = n_session
    result["n_ticks_off_session"] = n_off_session

    if df_rth.empty:
        print(f"  --- Bars (RTH-Session-Achse) --- keine Ticks innerhalb der Session.")
        result["bars_rth"] = {"label": "RTH-Session-Achse", "n_bars": 0}
        return result

    rth_hours = session_calendar_hours(df, open_snap, close_snap)
    bars_rth = build_bars(df_rth)
    brth = bar_report("RTH-Session-Achse", bars_rth, rth_hours)
    result["bars_rth"] = brth

    if b247.get("n_bars") and brth.get("n_bars"):
        delta = brth["coverage_ratio"] - b247["coverage_ratio"]
        print(f"  [Delta] coverage_ratio RTH - 24/7 = {delta:+.4f}  "
              f"(#1329: check_bar_quality misst aktuell nur die 24/7-Spalte)")
    return result


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"[WARNUNG] {path} nicht lesbar ({e}) — nutze leere Config.", file=sys.stderr)
        return {}


def discover_universe(catalog_path: Path) -> list[str]:
    root = catalog_path / "data" / "quote_tick"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=Path("."),
                     help="etoro_nautilus-Repo-Wurzel (fuer automation/config/*.json und den "
                          "Default-Katalogpfad data/nautilus). Default: aktuelles Verzeichnis.")
    ap.add_argument("--catalog-path", type=Path, default=None,
                     help="Expliziter Katalogpfad (ueberschreibt --repo-root/data/nautilus bzw. "
                          "backtest.json['catalog_path']).")
    ap.add_argument("--symbols", nargs="+", default=None, help="Zu pruefende Symbole, z. B. TSLA.ETORO NVDA.ETORO")
    ap.add_argument("--all", action="store_true", help="Alle im Katalog gefundenen Symbole pruefen.")
    ap.add_argument("--max-ticks", type=int, default=None,
                     help="Nur die juengsten N Ticks je Symbol lesen (Default: alle).")
    ap.add_argument("--no-rth", action="store_true",
                     help="RTH-Session-Achse ueberspringen (nur Rohdaten + 24/7-Bars).")
    ap.add_argument("--csv", type=Path, default=None, help="Zusammenfassungstabelle zusaetzlich als CSV schreiben.")
    args = ap.parse_args()

    repo_root = args.repo_root.resolve()
    config_dir = repo_root / "automation" / "config"
    backtest_cfg = load_json(config_dir / "backtest.json")
    instrument_map = load_json(config_dir / "instrument_map.json")
    session_cfg = backtest_cfg.get("session_hours_by_asset_class") or {}

    if args.catalog_path is not None:
        catalog_path = args.catalog_path.resolve()
    else:
        raw = backtest_cfg.get("catalog_path", "data/nautilus")
        catalog_path = (repo_root / raw).resolve()

    if not catalog_path.is_dir():
        print(f"[FEHLER] Katalogpfad existiert nicht: {catalog_path}", file=sys.stderr)
        return 2

    if args.all:
        symbols = discover_universe(catalog_path)
        if not symbols:
            print(f"[FEHLER] keine Symbol-Verzeichnisse unter {catalog_path / 'data' / 'quote_tick'}", file=sys.stderr)
            return 2
    elif args.symbols:
        symbols = args.symbols
    else:
        ap.error("entweder --symbols <SYM ...> oder --all angeben.")
        return 2

    print(f"Katalog: {catalog_path}")
    print(f"Symbole: {len(symbols)}")

    results = []
    for symbol in symbols:
        try:
            results.append(verify_symbol(symbol, catalog_path, instrument_map, session_cfg,
                                          args.max_ticks, do_rth=not args.no_rth))
        except Exception as e:  # ein Symbol darf den Gesamtlauf nie abbrechen
            print(f"  [FEHLER] {symbol}: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"symbol": symbol, "status": "EXCEPTION", "error": str(e)})

    print(f"\n{'=' * 70}\nZusammenfassung\n{'=' * 70}")
    rows = []
    for r in results:
        if r.get("status") != "OK":
            rows.append({"symbol": r["symbol"], "status": r.get("status", "?")})
            continue
        row = {
            "symbol": r["symbol"],
            "status": "OK",
            "n_ticks": r["raw"]["n_ticks"],
            "span_days": r["raw"]["span_days"],
            "coverage_24_7": r["bars_24_7"].get("coverage_ratio"),
            "coverage_rth": r.get("bars_rth", {}).get("coverage_ratio"),
            "ticks_per_bar_median": r["bars_24_7"].get("ticks_per_bar_median"),
        }
        rows.append(row)

    df_summary = pd.DataFrame(rows)
    if not df_summary.empty:
        print(df_summary.to_string(index=False))
        if args.csv:
            df_summary.to_csv(args.csv, index=False)
            print(f"\nCSV geschrieben: {args.csv}")

    n_failed = sum(1 for r in results if r.get("status") != "OK")
    if n_failed:
        print(f"\n{n_failed}/{len(results)} Symbole konnten nicht gelesen werden.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
