"""Backtest-Weiche: Tages- oder Stunden-Backtest je Instrument, automatisch nach Datenlage.

Ein Instrument läuft im Stunden-Sweep, sobald sein Katalog (``OneHour``) mindestens ``MIN_HOURLY_DAYS`` Tage
abdeckt — dasselbe Kriterium, an dem der Sweep sonst mit ``REJECT_RESOLUTION_HETEROGENEOUS`` abweist (444 Tage
Walk-Forward-Span plus Puffer). Bis dahin (und für alle Instrumente ohne genug Historie) rechnet der
Tages-Backtest (``experiments/ebc/gen_a.py``, ca. 4 Jahre Tageskerzen). Die Weiche wählt je Instrument, eine
Umschaltung braucht keine Konfiguration: sobald genug Historie da ist, wandert das Instrument in die Stunden-Spur.

* ``--list-pending``  offener Bedarf beider Spuren, eine etoro_id je Zeile (für ``backtest_loadmgr --symbols-cmd``)
* ``--run --ids A,B`` führt einen Block aus: Stunden-Spur per ``automation.optimizer.sweep``, Rest per ``gen_a.py``
* ``--status``        Zahl der Instrumente je Spur und Fortschritt bis zur Umschaltung

Der Stunden-Lauf wird je Instrument in ``results/loadmgr/hourly_done.json`` vermerkt und nach
``HOURLY_REFRESH_DAYS`` Tagen erneuert.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CATALOG = ROOT / "data" / "nautilus" / "data" / "quote_tick"
DONE = ROOT / "results" / "loadmgr" / "hourly_done.json"
MIN_HOURLY_DAYS = 450.0
HOURLY_REFRESH_DAYS = 30.0


def hourly_span_days(symbol: str, catalog: Path = CATALOG) -> float:
    """Abdeckung der Stundenhistorie im Katalog in Tagen (0.0 ohne Datei)."""
    f = catalog / symbol / "OneHour" / "data.parquet"
    if not f.exists():
        return 0.0
    import pyarrow.parquet as pq
    names = [n for n in pq.read_schema(f).names if n.startswith("ts")]
    if not names:
        return 0.0
    col = pq.read_table(f, columns=[names[0]]).column(0)
    if len(col) < 2:
        return 0.0
    import pyarrow.compute as pc
    mm = pc.min_max(col).as_py()
    return (mm["max"] - mm["min"]) / 86400e9


def id_symbol_map() -> dict[str, str]:
    m = json.loads((ROOT / "automation" / "config" / "instrument_map.json").read_text(encoding="utf-8"))["instruments"]
    return {k: v["symbol"] for k, v in m.items()}


def load_done() -> dict[str, float]:
    return json.loads(DONE.read_text(encoding="utf-8")) if DONE.exists() else {}


def lane_of(span_days: float, min_days: float = MIN_HOURLY_DAYS) -> str:
    return "hourly" if span_days >= min_days else "daily"


def classify(ids: list[str], idsym: dict[str, str], *, span_fn=hourly_span_days, min_days: float = MIN_HOURLY_DAYS) -> dict[str, str]:
    """etoro_id -> 'hourly' | 'daily'. Ohne Symbol in der instrument_map nur die Tages-Spur (kein Katalog)."""
    return {i: (lane_of(span_fn(idsym[i]), min_days) if i in idsym else "daily") for i in ids}


def hourly_pending(ids: list[str], idsym: dict[str, str], done: dict[str, float], *, now: float | None = None,
                   span_fn=hourly_span_days, min_days: float = MIN_HOURLY_DAYS, refresh_days: float = HOURLY_REFRESH_DAYS) -> list[str]:
    now = now or time.time()
    lanes = classify(ids, idsym, span_fn=span_fn, min_days=min_days)
    return [i for i in ids if lanes[i] == "hourly" and now - done.get(i, 0.0) > refresh_days * 86400]


def daily_pending(py: str, cwd: Path) -> list[str]:
    out = subprocess.run([py, str(ROOT / "experiments" / "ebc" / "gen_a.py"), "--list-pending"],
                         capture_output=True, text=True, check=True, cwd=cwd).stdout
    return out.split()


def all_ids() -> list[str]:
    from automation.raw_recorder import instrument_ids
    return instrument_ids()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-pending", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--ebc-dir", default=str(Path.home() / "ebc_exp"), help="Arbeitsverzeichnis der Tages-Backtests (Daten, cfg_A)")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    idsym, done, ebc = id_symbol_map(), load_done(), Path(a.ebc_dir)
    ids = all_ids()
    if a.status:
        lanes = classify(ids, idsym)
        best = max((hourly_span_days(idsym[i]) for i in ids if i in idsym), default=0.0)
        print(f"{len(ids)} Instrumente: Stunden-Spur {sum(v == 'hourly' for v in lanes.values())}, "
              f"Tages-Spur {sum(v == 'daily' for v in lanes.values())}; längste Stundenhistorie {best:.0f} von {MIN_HOURLY_DAYS:.0f} Tagen")
        return 0
    if a.list_pending:
        pend = hourly_pending(ids, idsym, done) + daily_pending(a.python, ebc)
        print("\n".join(dict.fromkeys(pend)))
        return 0
    block = [i for i in a.ids.split(",") if i]
    lanes = classify(block, idsym)
    hourly = [i for i in block if lanes[i] == "hourly"]
    rc = 0
    if hourly:
        syms = ",".join(idsym[i] for i in hourly)
        r = subprocess.run([a.python, "-m", "automation.optimizer.sweep", "--strategies", "all", "--symbols", syms,
                            "--n-jobs", str(a.workers), "--max-wallclock-h", "0.6"], cwd=ROOT)
        if r.returncode == 0:
            now = time.time()
            done.update({i: now for i in hourly})
            DONE.parent.mkdir(parents=True, exist_ok=True)
            tmp = DONE.with_suffix(".tmp")
            tmp.write_text(json.dumps(done), encoding="utf-8")
            tmp.replace(DONE)
        rc |= r.returncode
    daily = [i for i in block if lanes[i] == "daily"]
    if daily:
        rc |= subprocess.run([a.python, str(ROOT / "experiments" / "ebc" / "gen_a.py"), "--ids", ",".join(daily),
                              "--workers", str(a.workers)], cwd=ebc).returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
