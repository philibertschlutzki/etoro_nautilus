"""Load-Management für Massen-Backtests (alle Instrumente), ohne den Paper-Bot zu stören.

Der Bot braucht Rechenzeit und API-Budget zu festen Zeiten: Stundenbalken (Minute :55 bis :05, dort
entstehen die 429-Spitzen) und die US-Handelszeit (Mo-Fr 13:30-20:00 UTC, höchste Tick-Last). Dieses
Modul zerlegt eine Instrumentenliste in Blöcke und fährt sie nur, wenn

* das Zeitfenster ruhig ist (``compute_budget``: Nacht/Wochenende viel, Randzeiten wenig, Handelszeit 0),
* die Maschine Luft hat (Load je Kern, freier RAM, freier Plattenplatz),
* der Bot-Dienst läuft (kein Backtest, wenn der Bot selbst Probleme hat).

Die Blockgröße regelt ein AIMD-Verfahren: Block erfolgreich und schneller als das Zeitziel ⇒ +25 %;
Zeitüberschreitung oder Lastabbruch ⇒ halbieren, der Block geht zurück in die Warteschlange. Der Zustand
liegt atomar in einer JSON-Datei (fortsetzbar nach Neustart/Abbruch), Blöcke laufen mit ``nice``/``ionice``.

Aufruf (Befehlsvorlage mit ``{symbols}`` = Komma-Liste, ``{jobs}`` = erlaubte Worker)::

    python -m automation.backtest_loadmgr --symbols-file results/loadmgr/symbols.txt \
        --cmd "venv/bin/python -m automation.optimizer.run_optimization --strategy X --n-jobs {jobs}" \
        --state results/loadmgr/state.json --once

``--dry-run`` druckt nur Budget und nächsten Block. Nur Standardbibliothek, keine Orders, kein Netzwerk.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BOT_SERVICE = "etoro-paper-bot"

# Anteil der Kerne, den Backtests belegen dürfen
BUDGET_QUIET = 0.60      # Nacht (20:00-13:30 UTC) und Wochenende
BUDGET_SHOULDER = 0.25   # Randzeiten um die US-Handelszeit (13:00-13:30, 20:00-21:00 UTC)
BUDGET_BUSY = 0.0        # US-Handelszeit
MAX_LOAD_PER_CORE = 0.70
MIN_MEM_AVAILABLE_GB = 6.0
MIN_DISK_FREE_GB = 20.0


def compute_budget(now: dt.datetime) -> float:
    """Kernanteil (0..1), den Backtests jetzt belegen dürfen. ``now`` timezone-aware (UTC)."""
    now = now.astimezone(dt.timezone.utc)
    if now.minute >= 55 or now.minute < 5:
        return 0.0                                    # Stundenwechsel: Bot holt Bars, 429-Gefahr
    if now.weekday() >= 5:
        return BUDGET_QUIET
    hm = now.hour * 60 + now.minute
    if 13 * 60 + 30 <= hm < 20 * 60:
        return BUDGET_BUSY
    if 13 * 60 <= hm < 13 * 60 + 30 or 20 * 60 <= hm < 21 * 60:
        return BUDGET_SHOULDER
    return BUDGET_QUIET


@dataclass
class Snapshot:
    load1_per_core: float
    mem_available_gb: float
    disk_free_gb: float
    bot_active: bool


def read_snapshot(path: str = ".") -> Snapshot:
    cores = os.cpu_count() or 1
    load1 = os.getloadavg()[0] / cores
    mem_kb = 0
    with open("/proc/meminfo", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                mem_kb = int(line.split()[1])
                break
    disk_gb = shutil.disk_usage(path).free / 1e9
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", BOT_SERVICE],
                           capture_output=True, text=True, timeout=10)
        active = r.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        active = False
    return Snapshot(load1, mem_kb / 1e6, disk_gb, active)


def may_run(budget: float, snap: Snapshot) -> tuple[bool, str]:
    """(erlaubt, Grund). Jede Bedingung ist hart; der Grund steht im Log."""
    if budget <= 0.0:
        return False, "Zeitfenster gesperrt (US-Handelszeit oder Stundenwechsel)"
    if not snap.bot_active:
        return False, f"{BOT_SERVICE} nicht aktiv"
    if snap.load1_per_core > MAX_LOAD_PER_CORE:
        return False, f"Last {snap.load1_per_core:.2f}/Kern > {MAX_LOAD_PER_CORE}"
    if snap.mem_available_gb < MIN_MEM_AVAILABLE_GB:
        return False, f"RAM frei {snap.mem_available_gb:.1f} GB < {MIN_MEM_AVAILABLE_GB}"
    if snap.disk_free_gb < MIN_DISK_FREE_GB:
        return False, f"Platz frei {snap.disk_free_gb:.0f} GB < {MIN_DISK_FREE_GB}"
    return True, "ok"


def jobs_for(budget: float, snap: Snapshot, cores: int | None = None) -> int:
    """Worker-Zahl: Budget-Kerne abzüglich der schon vorhandenen Last, mindestens 1, höchstens 12."""
    cores = cores or os.cpu_count() or 1
    free = budget * cores - snap.load1_per_core * cores
    return max(1, min(12, int(free)))


@dataclass
class BlockSizer:
    """AIMD: additive/multiplikative Steigerung bei Erfolg, Halbierung bei Fehlschlag."""
    size: int = 8
    min_size: int = 1
    max_size: int = 64
    target_s: float = 40 * 60

    def success(self, duration_s: float) -> None:
        if duration_s < self.target_s * 0.1:
            self.size = min(self.max_size, self.size * 2)          # sehr schnell: Block verdoppeln
        elif duration_s < self.target_s * 0.6:
            self.size = min(self.max_size, max(self.size + 1, int(self.size * 1.25)))

    def failure(self) -> None:
        self.size = max(self.min_size, self.size // 2)


@dataclass
class State:
    pending: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    failed: dict[str, int] = field(default_factory=dict)     # Symbol -> Fehlversuche
    block_size: int = 8

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path, symbols: list[str]) -> "State":
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            st = cls(**d)
            known = set(st.pending) | set(st.done) | set(st.failed)
            st.pending += [s for s in symbols if s not in known]          # neue Instrumente anhängen
            return st
        return cls(pending=list(symbols))


MAX_ATTEMPTS = 3


def next_block(st: State, size: int) -> list[str]:
    return st.pending[:max(1, size)]


def run_block(cmd_tpl: str, block: list[str], jobs: int, *, timeout_s: float, cwd: str | None = None, poll_s: float = 10.0,
              snapshot_fn=read_snapshot, budget_fn=compute_budget, log=print) -> tuple[bool, str]:
    """Führt einen Block aus und bricht ihn bei Lastverletzung ab. (Erfolg, Grund)."""
    cmd = cmd_tpl.format(symbols=",".join(block), jobs=jobs)
    argv = ["nice", "-n", "19"]
    if shutil.which("ionice"):
        argv += ["ionice", "-c3"]
    argv += shlex.split(cmd)
    log(f"[loadmgr] Block {len(block)} Symbole, {jobs} Worker: {block[:3]}...")
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, start_new_session=True, cwd=cwd)
    while True:
        try:
            rc = proc.wait(timeout=poll_s)
            return (rc == 0), f"rc={rc}"
        except subprocess.TimeoutExpired:
            pass
        ok, why = may_run(budget_fn(dt.datetime.now(dt.timezone.utc)), snapshot_fn())
        too_slow = time.monotonic() - t0 > timeout_s
        if not ok or too_slow:
            reason = why if not ok else f"Zeitziel {timeout_s/60:.0f} min überschritten"
            log(f"[loadmgr] Abbruch: {reason}")
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
            return False, reason


def step(st: State, sizer: BlockSizer, cmd_tpl: str, *, now: dt.datetime | None = None, snap: Snapshot | None = None,
         runner=run_block, log=print) -> str:
    """Ein Planungsschritt: ``waiting:<Grund>``, ``done`` oder ``ran:ok|fail``. Reine Logik, testbar."""
    if not st.pending:
        return "done"
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = snap or read_snapshot()
    budget = compute_budget(now)
    ok, why = may_run(budget, snap)
    if not ok:
        return f"waiting:{why}"
    sizer.size = st.block_size
    block = next_block(st, sizer.size)
    t0 = time.monotonic()
    success, reason = runner(cmd_tpl, block, jobs_for(budget, snap), timeout_s=sizer.target_s)
    dur = time.monotonic() - t0
    if success:
        sizer.success(dur)
        st.pending = [s for s in st.pending if s not in block]
        st.done += block
    else:
        sizer.failure()
        for s in block:
            st.failed[s] = st.failed.get(s, 0) + 1
        if len(block) == 1 and st.failed[block[0]] >= MAX_ATTEMPTS:
            st.pending.remove(block[0])                    # Giftsymbol aussortieren statt endlos wiederholen
            log(f"[loadmgr] {block[0]} nach {MAX_ATTEMPTS} Fehlversuchen übersprungen")
    st.block_size = sizer.size
    return "ran:ok" if success else f"ran:fail ({reason})"


def catalog_symbols(root: Path | None = None) -> list[str]:
    """Instrumente der instrument_map, für die Katalogdaten vorliegen (nur dort ist ein Backtest möglich)."""
    root = root or Path(__file__).resolve().parents[1]
    m = json.loads((root / "automation" / "config" / "instrument_map.json").read_text(encoding="utf-8"))
    have = {p.name for p in (root / "data" / "nautilus" / "data" / "cfd").glob("*")}
    return sorted(v["symbol"] for v in m["instruments"].values() if v["symbol"] in have)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols-file", default=None, help="eine Symbolzeile je Instrument; ohne Angabe: alle Instrumente mit Katalogdaten")
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--state", default="results/loadmgr/state.json")
    ap.add_argument("--symbols-cmd", default=None, help="Befehl, der die offenen Einheiten zeilenweise ausgibt (z. B. gen_a.py --list-pending)")
    ap.add_argument("--cwd", default=None, help="Arbeitsverzeichnis des Block-Befehls")
    ap.add_argument("--once", action="store_true", help="nur einen Schritt (für systemd-Timer alle 15 Minuten)")
    ap.add_argument("--burst-s", type=float, default=0.0, help="mit --once: weitere Blöcke nachschieben, solange es klappt und so viele Sekunden nicht um sind")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    symbols = ([s.strip() for s in Path(a.symbols_file).read_text(encoding="utf-8").splitlines()
                if s.strip() and not s.startswith("#")] if a.symbols_file else
                (subprocess.run(shlex.split(a.symbols_cmd), capture_output=True, text=True, check=True, cwd=a.cwd).stdout.split()
                 if a.symbols_cmd else catalog_symbols()))
    state_path = Path(a.state)
    st = State.load(state_path, symbols)
    if a.symbols_cmd:                      # der Befehl kennt den Bedarf (auch Neuberechnung veralteter Ergebnisse)
        st.pending = [s for s in symbols if st.failed.get(s, 0) < MAX_ATTEMPTS]
    sizer = BlockSizer(size=st.block_size)
    if a.dry_run:
        snap = read_snapshot()
        budget = compute_budget(dt.datetime.now(dt.timezone.utc))
        print(f"Budget {budget:.2f}, {may_run(budget, snap)}, Worker {jobs_for(budget, snap)}, "
              f"Block {next_block(st, st.block_size)}, offen {len(st.pending)}, fertig {len(st.done)}")
        return 0
    t_start = time.monotonic()
    while True:
        res = step(st, sizer, a.cmd, runner=functools.partial(run_block, cwd=a.cwd))
        st.save(state_path)
        print(f"[loadmgr] {res}; offen {len(st.pending)}, fertig {len(st.done)}, Blockgröße {st.block_size}")
        if res == "done":
            return 0
        if a.once and not (res == "ran:ok" and time.monotonic() - t_start < a.burst_s):
            return 0
        if res.startswith("waiting"):
            time.sleep(300)


if __name__ == "__main__":
    sys.exit(main())
