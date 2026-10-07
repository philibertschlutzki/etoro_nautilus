"""Issue #1277 (GH #1150, Katalog #1375, Pitfall #496) — OneDay-Kerzendefinition VERMESSEN, bevor eine Tagesachse
gebaut wird (reiner Messlauf, read-only).

Offen ist, was eine eToro-Tageskerze für US-Aktien darstellt: (a) die Bezugszeit von ``fromDate`` (UTC-Mitternacht,
Börsenöffnung, …), (b) über welchen Zeitraum O/H/L/C gebildet werden (RTH-Session, eToro-Handelszeit 24/5, UTC-Tag),
(c) ob es Kerzen an Wochenenden/Feiertagen gibt. Davon hängen Tick-Expansion, Session-Semantik und jede Rendite der
Tagesachse ab (verallgemeinert Pitfall #485: für eine Kerze, die mindestens so lang ist wie die Session, entscheidet
der Handelstag über die Zugehörigkeit, nicht der Tick-Zeitpunkt).

Methode: je Tageskerze d (Überlappung mit der OneHour-Historie) werden Open/High/Low/Close gegen die aus den
Stundenkerzen unter JEDER Hypothese gebildeten Werte verglichen (Median-|Δ| in bps über die Handelstage):

* ``rth_session``       — Stundenkerzen, die das Börsenfenster ``[open, close)`` des Tages überlappen;
* ``etoro_trading_day`` — Stundenkerzen von 20:00 Börsenzeit des Vortags bis 20:00 Börsenzeit des Tages (24/5);
* ``utc_day``           — Stundenkerzen des UTC-Kalendertags.

Klasse = Hypothese mit dem kleinsten Close-Median-|Δ|; ``inconclusive``, wenn keine Hypothese ≤ 1 bps erreicht.
Es wird NIE in den Katalog geschrieben; Ausgabe: ``reports/oneday_definition_<run_id>.json``."""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter
from pathlib import Path

HYPOTHESES: tuple[str, ...] = ("rth_session", "etoro_trading_day", "utc_day")
CLASS_INCONCLUSIVE = "inconclusive"
CLASSES: tuple[str, ...] = HYPOTHESES + (CLASS_INCONCLUSIVE,)
TIE_BPS = 0.01                               # Hypothesen mit gleichem Close-Δ sind nicht unterscheidbar
INCONCLUSIVE_CLOSE_BPS = 1.0               # Close-Median-|Δ| ≤ 1 bps, sonst ``inconclusive``
DEFAULT_OVERLAP_START = dt.date(2026, 7, 2)  # Beginn der OneHour-Historie (Issue #1375)
ETORO_DAY_END_LOCAL_MIN = 20 * 60            # 20:00 Börsenzeit (24/5-Handel)

_NS_DAY = 86_400_000_000_000
_NS_HOUR = 3_600_000_000_000
_FIELDS = ("open", "high", "low", "close")


def read_candles(symbol: str, interval: str, catalog_path: Path) -> list[dict]:
    """Rekonstruiert OHLC je Kerze (Mid-Preis) aus den O/L/H/C-Ticks einer ``interval``-Datei — nur lesend.
    Gruppiert nach ``ts // interval_ns``; ``start_ns`` = erster Tick der Kerze (= ``fromDate``)."""
    import pyarrow.parquet as pq

    from automation.catalog_paths import (decode_fsb16_price, resolve_quote_tick_columns,
                                          resolve_quote_tick_files)
    step = _NS_DAY if interval == "OneDay" else _NS_HOUR
    rows: list[tuple[int, float]] = []
    for path in resolve_quote_tick_files(catalog_path, symbol, interval=interval):
        cols = resolve_quote_tick_columns(pq.read_schema(str(path)).names)
        if cols is None:
            continue
        table = pq.read_table(str(path), columns=[cols["bid_price"], cols["ask_price"], cols["ts_event"]])
        for bid, ask, ts in zip(table.column(cols["bid_price"]).to_pylist(),
                                table.column(cols["ask_price"]).to_pylist(),
                                table.column(cols["ts_event"]).to_pylist()):
            rows.append((int(ts), (decode_fsb16_price(bid) + decode_fsb16_price(ask)) / 2.0))
    rows.sort(key=lambda r: r[0])
    candles: list[dict] = []
    current: int | None = None
    for ts, mid in rows:
        bucket = ts // step
        if bucket != current:
            candles.append({"bucket": bucket, "start_ns": ts, "open": mid, "high": mid, "low": mid, "close": mid})
            current = bucket
        else:
            c = candles[-1]
            c["high"], c["low"], c["close"] = max(c["high"], mid), min(c["low"], mid), mid
    return candles


def hypothesis_bounds(day: dt.date, hypothesis: str, window) -> tuple[int, int]:
    """``[start_ns, end_ns)`` (UTC) des Zeitraums, über den ``hypothesis`` die Tageskerze von ``day`` bildet."""
    from automation import session_windows as sw
    if hypothesis == "utc_day":
        start = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()) * 10 ** 9
        return start, start + _NS_DAY
    if hypothesis == "rth_session":
        return sw.session_bounds_utc_ns(day, window)
    if hypothesis == "etoro_trading_day":
        tz = sw._tzinfo(window.tz)
        prev = day - dt.timedelta(days=1)
        return (sw._local_wallclock_ns(prev, ETORO_DAY_END_LOCAL_MIN, tz),
                sw._local_wallclock_ns(day, ETORO_DAY_END_LOCAL_MIN, tz))
    raise ValueError(f"Unbekannte Hypothese {hypothesis!r}; bekannt: {HYPOTHESES}.")


def hourly_ohlc(hourly: list[dict], start_ns: int, end_ns: int) -> dict | None:
    """O/H/L/C aus den Stundenkerzen, die ``[start_ns, end_ns)`` überlappen; ``None`` ohne Kerze."""
    sel = [c for c in hourly if c["start_ns"] < end_ns and c["start_ns"] + _NS_HOUR > start_ns]
    if not sel:
        return None
    return {"open": sel[0]["open"], "high": max(c["high"] for c in sel),
            "low": min(c["low"] for c in sel), "close": sel[-1]["close"]}


def classify(close_median_bps: dict[str, float | None]) -> str:
    """Klasse nach kleinstem Close-Median-|Δ|; ``inconclusive``, wenn keiner ≤ ``INCONCLUSIVE_CLOSE_BPS``."""
    valid = {h: v for h, v in close_median_bps.items() if v is not None}
    if not valid:
        return CLASS_INCONCLUSIVE
    best = min(valid, key=lambda h: (valid[h], HYPOTHESES.index(h)))
    return best if valid[best] <= INCONCLUSIVE_CLOSE_BPS else CLASS_INCONCLUSIVE


def _hhmm(ns: int, tz) -> str:
    return dt.datetime.fromtimestamp(ns // 10 ** 9, tz=tz).strftime("%H:%M")


def measure_symbol(daily: list[dict], hourly: list[dict], window, *,
                   overlap_start: dt.date = DEFAULT_OVERLAP_START) -> dict:
    """Δ-Tabelle aller Hypothesen für EIN Symbol (reine Funktion über die rekonstruierten Kerzen)."""
    from automation import session_windows as sw
    tz_local = sw._tzinfo(window.tz)
    deltas: dict[str, dict[str, list[float]]] = {h: {f: [] for f in _FIELDS} for h in HYPOTHESES}
    from_utc: Counter = Counter()
    from_local: Counter = Counter()
    n_days = n_non_trading = 0
    for c in daily:
        day = dt.datetime.fromtimestamp(c["start_ns"] // 10 ** 9, tz=dt.timezone.utc).date()
        if day < overlap_start:
            continue
        n_days += 1
        from_utc[_hhmm(c["start_ns"], dt.timezone.utc)] += 1
        from_local[_hhmm(c["start_ns"], tz_local)] += 1
        if not sw.is_trading_day(day, window):
            n_non_trading += 1
        for hyp in HYPOTHESES:
            ref = hourly_ohlc(hourly, *hypothesis_bounds(day, hyp, window))
            if ref is None:
                continue
            for f in _FIELDS:
                if ref[f]:
                    deltas[hyp][f].append(abs(c[f] - ref[f]) / abs(ref[f]) * 1e4)
    table = {h: {f"{f}_median_abs_delta_bps": (round(statistics.median(v), 4) if v else None)
                 for f, v in per.items()} | {"n_days": len(per["close"])}
             for h, per in deltas.items()}
    closes = {h: table[h]["close_median_abs_delta_bps"] for h in HYPOTHESES}
    klass = classify(closes)
    # In der Sommerzeit (EDT) ist 20:00 Börsenzeit == 00:00 UTC: ``etoro_trading_day`` und ``utc_day`` bilden
    # dann dieselben Stundenkerzen und sind NICHT unterscheidbar — nur im Winter (EST) trennen sie sich.
    tied = sorted(h for h, v in closes.items() if klass != CLASS_INCONCLUSIVE and v is not None
                  and abs(v - closes[klass]) <= TIE_BPS)
    return {
        "oneday_definition": klass, "tied_hypotheses": tied, "delta_table": table, "n_oneday_candles": n_days,
        "non_trading_day_share": round(n_non_trading / n_days, 4) if n_days else None,
        "from_date_utc_mode": from_utc.most_common(1)[0][0] if from_utc else None,
        "from_date_local_mode": from_local.most_common(1)[0][0] if from_local else None,
        "from_date_local_tz": window.tz,
    }


def section_de(result: dict) -> str:
    """Abschnitt für den Messbericht (deutsch), gebaut aus demselben Ergebnis-Dict wie die JSON-Datei."""
    lines = ["## OneDay-Kerzendefinition (Issue #1277)", ""]
    for sym, r in sorted(result.get("symbols", {}).items()):
        tied = r.get("tied_hypotheses") or []
        note = f" [nicht unterscheidbar von: {', '.join(t for t in tied if t != r.get('oneday_definition'))}]" \
            if len(tied) > 1 else ""
        lines.append(f"- **{sym}**: {r.get('oneday_definition')}{note} "
                     f"(fromDate ≈ {r.get('from_date_utc_mode')}Z, {r.get('from_date_local_mode')} "
                     f"{r.get('from_date_local_tz')}; Nicht-Handelstag-Anteil {r.get('non_trading_day_share')})")
    return "\n".join(lines)


def run_oneday_definition(symbols: list[str], *, run_id: str, work_dir: Path, catalog_path: Path,
                          windows: dict | None = None,
                          overlap_start: dt.date = DEFAULT_OVERLAP_START) -> dict:
    """Messlauf über ``symbols``; schreibt ``<work_dir>/reports/oneday_definition_<run_id>.json`` und gibt das
    Ergebnis zurück. ``windows`` = ``{symbol: SessionWindow}`` (Default: ``load_session_window_for_symbol``).
    Der Katalog wird ausschliesslich gelesen."""
    from automation.optimizer.manifest import write_json_atomic
    from automation.optimizer.trial_config import config_dir
    from automation.session_windows import load_session_window_for_symbol
    out: dict[str, dict] = {}
    for sym in symbols:
        window = (windows or {}).get(sym) or load_session_window_for_symbol(sym, config_dir())
        daily = read_candles(sym, "OneDay", catalog_path)
        hourly = read_candles(sym, "OneHour", catalog_path)
        if window is None or not daily or not hourly:
            out[sym] = {"oneday_definition": CLASS_INCONCLUSIVE, "delta_table": {},
                        "reason": "NO_SESSION_WINDOW" if window is None else "NO_OVERLAP_DATA"}
            continue
        out[sym] = measure_symbol(daily, hourly, window, overlap_start=overlap_start)
    result = {"run_id": run_id, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "overlap_start": overlap_start.isoformat(), "inconclusive_close_bps": INCONCLUSIVE_CLOSE_BPS,
              "hypotheses": list(HYPOTHESES), "symbols": out}
    result["section_de"] = section_de(result)
    write_json_atomic(Path(work_dir) / "reports" / f"oneday_definition_{run_id}.json", result)
    return result


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps({"hypotheses": HYPOTHESES}, indent=2))
