"""Rohdaten-Rekorder: speichert von ALLEN Instrumenten der instrument_map (plus optionalen Zusatz-IDs)
die Daten, aus denen sich jedes Signal und jeder Backtest später neu rechnen lässt.

* ``rates``   Bid/Ask-Schnappschuss (alle Instrumente in 50er-Batches) ⇒ ``data/raw/rates/YYYY-MM-DD.jsonl.gz``
              (Spread-Zeitreihe, Quote-Zeit ``qts``; Gzip-Anhängen ergibt gültige Mehrfach-Member).
* ``candles`` Stunden- und Tageskerzen, inkrementell (nur neue Kerzen) ⇒ ``data/raw/candles_1h|1d/<id>.csv``.

Nur lesende API-Aufrufe, global höchstens 1 Request/s, Backoff bei 429, keine Orders. Keine Läufe von :55 bis :05
(Stundenwechsel des Bots, dort entstehen die 429-Spitzen), ``--max-seconds`` begrenzt die Laufzeit. Schlüssel
werden nie ausgegeben. Aufruf::

    python -m automation.raw_recorder rates
    python -m automation.raw_recorder candles --max-seconds 1500
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BASE = "https://public-api.etoro.com/api/v1"
_last = [0.0]


def _keys() -> tuple[str, str]:
    if "ETORO_API_KEY" not in os.environ:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["ETORO_API_KEY"], os.environ["ETORO_USER_KEY"]


def api_get(url: str, tries: int = 6) -> dict:
    ak, uk = _keys()
    for i in range(tries):
        wait = 1.05 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"x-api-key": ak, "x-user-key": uk, "x-request-id": str(uuid.uuid4()),
                                                   "User-Agent": "etoro-nautilus/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(min(60, 2 ** (i + 1)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** (i + 1))
    raise RuntimeError("API: zu viele Fehlversuche " + url.split("?")[0])


DEFAULT_EXTRA_UNIVERSES = ("results/H1/universe.csv", "results/H4/universe_neu.csv")      # EBC-Backtest-Universum


def instrument_ids(extra_files: list[str] | None = None) -> list[str]:
    """Alle IDs der instrument_map (Source of Truth des Bots) plus IDs aus Zusatz-CSV-Dateien (Spalte etoro_id).
    Ohne Angabe: das EBC-Backtest-Universum (falls vorhanden) — der Rekorder sammelt damit alles, was getestet wird."""
    if extra_files is None:
        extra_files = [str(ROOT / f) for f in DEFAULT_EXTRA_UNIVERSES if (ROOT / f).exists()]
    m = json.loads((ROOT / "automation" / "config" / "instrument_map.json").read_text(encoding="utf-8"))
    ids = [str(k) for k in m["instruments"]]
    for f in extra_files or []:
        with open(f, newline="", encoding="utf-8") as fh:
            ids += [r["etoro_id"] for r in csv.DictReader(fh) if r.get("etoro_id")]
    return sorted(set(ids), key=int)


def in_blackout(now: dt.datetime) -> bool:
    return now.minute >= 55 or now.minute < 5


def rate_rows(payload: dict, ts: str) -> list[dict]:
    out = []
    for x in payload.get("rates", []):
        bid, ask = x.get("bid"), x.get("ask")
        if bid and ask and ask >= bid > 0:
            out.append({"ts": ts, "id": str(x["instrumentID"]), "bid": bid, "ask": ask, "qts": x.get("date")})
    return out


def record_rates(ids: list[str], now: dt.datetime) -> int:
    ts = now.isoformat()
    path = RAW / "rates" / f"{now:%Y-%m-%d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i in range(0, len(ids), 50):
        try:
            rows += rate_rows(api_get(f"{BASE}/market-data/instruments/rates?instrumentIds=" + ",".join(ids[i:i + 50])), ts)
        except Exception as e:                                       # ein Batch darf den Rest nicht kippen
            print("Fehler Batch", i, repr(e)[:100], file=sys.stderr)
    if rows:
        with gzip.open(path, "at", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
    return len(rows)


def new_candles(payload: dict, last_ts: str | None, before: str | None = None) -> list[tuple]:
    """Kerzen aus der API-Antwort, nur jünger als ``last_ts`` und vor ``before`` (die laufende, noch unfertige Kerze
    wird nie gespeichert), aufsteigend sortiert (ISO-String-Vergleich)."""
    out = []
    for blk in payload.get("candles", []):
        for c in blk.get("candles", []):
            ts = c["fromDate"]
            if (last_ts is None or ts > last_ts) and (before is None or ts < before):
                out.append((ts, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)))
    return sorted(set(out))


def append_candles(path: Path, rows: list[tuple]) -> int:
    if not rows:
        return 0
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    return len(rows)


def last_ts_of(path: Path) -> str | None:
    if not path.exists():
        return None
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 512))
        lines = f.read().decode("utf-8", "ignore").strip().splitlines()
    return lines[-1].split(",")[0] if len(lines) > 1 else None


def record_candles(ids: list[str], max_seconds: float) -> tuple[int, int]:
    t0 = time.monotonic()
    done = added = 0
    for iid in ids:
        if time.monotonic() - t0 > max_seconds or in_blackout(dt.datetime.now(dt.timezone.utc)):
            break
        p1h, p1d = RAW / "candles_1h" / f"{iid}.csv", RAW / "candles_1d" / f"{iid}.csv"
        if all(p.exists() and time.time() - p.stat().st_mtime < 20 * 3600 for p in (p1h, p1d)):
            done += 1                                                # heute schon aufgefüllt (Wiederaufnahme-Lauf)
            continue
        for interval, folder, first, topup in (("OneHour", "candles_1h", 1000, 72), ("OneDay", "candles_1d", 1000, 10)):
            path = RAW / folder / f"{iid}.csv"
            last = last_ts_of(path)
            now = dt.datetime.now(dt.timezone.utc)
            before = (now.replace(minute=0, second=0, microsecond=0) if interval == "OneHour"
                      else now.replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%dT%H:%M:%S")
            count = first if last is None else topup                 # erster Lauf: volle Historie, danach Auffüllen
            try:
                payload = api_get(f"{BASE}/market-data/instruments/{iid}/history/candles/desc/{interval}/{count}")
            except Exception as e:
                print("Fehler", iid, interval, repr(e)[:100], file=sys.stderr)
                continue
            added += append_candles(path, new_candles(payload, last, before))
        done += 1
    return done, added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["rates", "candles"])
    ap.add_argument("--extra-universe", action="append", default=None, help="CSV mit Spalte etoro_id (z. B. results/H1/universe.csv)")
    ap.add_argument("--max-seconds", type=float, default=1500)
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    if in_blackout(now):
        print("Stundenwechsel (:55-:05): übersprungen")
        return 0
    ids = instrument_ids(a.extra_universe)
    if a.mode == "rates":
        print(now.isoformat(), "rates", record_rates(ids, now), "Zeilen aus", len(ids), "Instrumenten")
    else:
        done, added = record_candles(ids, a.max_seconds)
        print(now.isoformat(), "candles", done, "von", len(ids), "Instrumenten,", added, "neue Kerzen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
