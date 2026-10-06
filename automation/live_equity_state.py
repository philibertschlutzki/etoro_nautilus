"""Issue #1362 (GH #1258, P1) — das Drawdown-Gedächtnis überlebt den Bot-Neustart.

Vor diesem Modul startete ``LiveCircuitBreakerWatchdog._equity_peak`` je Prozess bei ``None``:
``dd_halt_fraction = 0.10`` war damit ein Verlustlimit je Bot-LEBENSDAUER, kein Drawdown-Limit
(−8 % an Tag 1, Neustart durch den täglichen Cron (#1358), −8 % an Tag 2 ⇒ kumuliert −15,4 % ohne
Auslösung; der Allocator-Dämpfer ψ(DD) erbte denselben Reset).

Dieses Modul liefert (rein, ohne ``nautilus_trader``-Import):

* ``PersistentEquityState`` — Hochwasserstand + Tagesbasis in ``data/state/live_equity_hwm.json``
  (``{environment, hwm, hwm_utc, updated_utc, last_equity, day_key, day_start_equity}``), atomar
  geschrieben (``manifest.write_json_atomic``). Der HWM sinkt NIE; zurückgesetzt wird er nur über
  ``reset`` (``momentum_ls_run --reset-hwm``, Event ``LIVE_HWM_RESET``). Eine Datei einer ANDEREN
  Umgebung (``demo`` vs. ``real``) wird nicht still übernommen (``HwmEnvironmentMismatch``).
* ``RoundTripReturnCollector`` — Netto-Rendite je Round-Trip in bps auf das Positions-Notional (die
  sizing-invariante Skala des Verteilungs-Auslösers B; vorher: 30-Sekunden-Equity-Renditen).
* ``exchange_day_key`` — der Handelstag in Börsen-Lokalzeit (Tagesbasis des Auslösers C).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from automation.optimizer.manifest import write_json_atomic

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HWM_PATH = PROJECT_ROOT / "data" / "state" / "live_equity_hwm.json"
DEFAULT_DAILY_LOSS_TZ = "America/New_York"


class HwmEnvironmentMismatch(RuntimeError):
    """Die Zustandsdatei gehört zu einer anderen ``environment`` als der laufende Bot."""


def exchange_day_key(now_utc: datetime, tz_name: str = DEFAULT_DAILY_LOSS_TZ) -> str:
    """ISO-Datum des Handelstags von ``now_utc`` in ``tz_name`` (Börsen-Lokalzeit, ``zoneinfo``)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo(tz_name)).date().isoformat()


class PersistentEquityState:
    """Persistenter Hochwasserstand + Tagesbasis (siehe Moduldocstring)."""

    def __init__(self, path: Path = HWM_PATH, *, environment: str,
                 min_persist_interval_s: float = 0.0) -> None:
        self.path = Path(path)
        self.environment = environment
        self._min_persist_interval_s = float(min_persist_interval_s)
        self._state: dict[str, Any] | None = None
        self._last_persist_ts: float | None = None

    # ── Lesen ──────────────────────────────────────────────────────────────────
    def load(self) -> dict[str, Any] | None:
        """Der gespeicherte Zustand (``None`` ohne Datei). ``HwmEnvironmentMismatch`` bei fremder
        ``environment`` — kein stilles Überschreiben des Gedächtnisses eines anderen Kontos."""
        import json

        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self._state = None
            return None
        if not isinstance(data, dict) or data.get("hwm") is None:
            self._state = None
            return None
        if data.get("environment") != self.environment:
            raise HwmEnvironmentMismatch(
                f"{self.path} gehört zu environment={data.get('environment')!r}, der Bot läuft mit "
                f"{self.environment!r} — `momentum_ls_run --reset-hwm` setzt den Hochwasserstand "
                f"bewusst zurück.")
        self._state = data
        return dict(data)

    @property
    def hwm(self) -> float | None:
        return None if self._state is None else float(self._state["hwm"])

    @property
    def day_start_equity(self) -> float | None:
        if self._state is None or self._state.get("day_start_equity") is None:
            return None
        return float(self._state["day_start_equity"])

    def persisted_drawdown(self) -> float | None:
        """``1 − last_equity / hwm`` des letzten persistierten Stands (Startwert für den
        Allocator-Dämpfer ψ(DD), bevor der erste Watchdog-Tick eine Live-Equity liest)."""
        if self._state is None:
            return None
        hwm, last = self._state.get("hwm"), self._state.get("last_equity")
        if not hwm or last is None or float(hwm) <= 0:
            return None
        return max(0.0, 1.0 - float(last) / float(hwm))

    # ── Fortschreiben ──────────────────────────────────────────────────────────
    def observe(self, equity: float, *, now_utc: datetime | None = None,
                day_key: str | None = None, monotonic: float | None = None) -> dict[str, Any]:
        """Schreibt ``equity`` fort: ``hwm = max(hwm, equity)``; beim ersten Tick eines NEUEN
        ``day_key`` wird ``day_start_equity`` auf diese Equity gesetzt (der HWM bleibt!). Persistiert
        atomar, sobald sich HWM oder Tag ändern, sonst höchstens alle ``min_persist_interval_s``
        (Default 0 ⇒ bei JEDER Beobachtung — der Watchdog beobachtet nur alle ~30 s, und ``last_equity``
        soll beim Neustart den tatsächlich letzten Stand tragen)."""
        now = now_utc or datetime.now(timezone.utc)
        equity = float(equity)
        state = dict(self._state) if self._state else {
            "environment": self.environment, "hwm": equity, "hwm_utc": now.isoformat(),
            "day_key": None, "day_start_equity": None,
        }
        changed = self._state is None
        if equity > float(state["hwm"]):
            state["hwm"], state["hwm_utc"] = equity, now.isoformat()
            changed = True
        if day_key is not None and state.get("day_key") != day_key:
            state["day_key"], state["day_start_equity"] = day_key, equity
            changed = True
        state["last_equity"] = equity
        state["updated_utc"] = now.isoformat()
        state["environment"] = self.environment
        self._state = state

        import time as _time
        mono = _time.monotonic() if monotonic is None else monotonic
        if changed or self._last_persist_ts is None or (
                mono - self._last_persist_ts) >= self._min_persist_interval_s:
            write_json_atomic(self.path, state)
            self._last_persist_ts = mono
        return dict(state)

    def reset(self, *, now_utc: datetime | None = None) -> dict[str, Any] | None:
        """``momentum_ls_run --reset-hwm``: verwirft Hochwasserstand und Tagesbasis (die nächste
        Beobachtung setzt beide neu). Liefert den verworfenen Zustand (für das Event)."""
        previous = None
        try:
            previous = self.load()
        except HwmEnvironmentMismatch:
            import json
            try:
                previous = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError):
                previous = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._state = None
        self._last_persist_ts = None
        return previous


# ─── Round-Trip-Renditen (Verteilungs-Auslöser B) ──────────────────────────────────

def round_trip_return_bps(position) -> float | None:
    """Netto-Rendite eines geschlossenen Round-Trips in bps auf das Positions-Notional:
    ``realized_pnl / (avg_px_open · peak_qty) · 10⁴``. ``None``, wenn das Notional nicht
    bestimmbar ist (defensiv gegen Test-Doubles/API-Drift)."""
    try:
        notional = abs(float(position.avg_px_open) * float(position.peak_qty))
        pnl = position.realized_pnl
        pnl = float(pnl.as_double()) if hasattr(pnl, "as_double") else float(pnl)
    except (AttributeError, TypeError, ValueError):
        return None
    if notional <= 1e-12:
        return None
    return pnl / notional * 10_000.0


class RoundTripReturnCollector:
    """Sammelt je Instrument die Round-Trip-Renditen (bps) aller GESCHLOSSENEN Positionen im Cache —
    jede Position genau einmal (``position.id``)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.returns_by_instrument: dict[str, list[float]] = {}

    def update(self, closed_positions: Iterable[Any]) -> int:
        added = 0
        for pos in closed_positions:
            pid = str(getattr(pos, "id", id(pos)))
            if pid in self._seen:
                continue
            ret = round_trip_return_bps(pos)
            if ret is None:
                continue
            self._seen.add(pid)
            self.returns_by_instrument.setdefault(str(pos.instrument_id), []).append(ret)
            added += 1
        return added


def distribution_references(whitelist_winners: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """``(refs, disabled_reasons)`` aus den Whitelist-Einträgen: ``refs[symbol] = {mean, std, n}``
    für jedes Paar mit ``holdout_trade_return_bps_mean/std/n`` (std > 0); ``disabled_reasons[symbol]``
    nennt für jedes andere Paar den Grund — der Verteilungs-Auslöser B ist damit nie ein STILL toter
    Pfad (das Start-Event trägt ``distribution_breaker_disabled_reason``)."""
    refs: dict[str, dict] = {}
    reasons: dict[str, str] = {}
    for symbol, entry in (whitelist_winners or {}).items():
        mean = (entry or {}).get("holdout_trade_return_bps_mean")
        std = (entry or {}).get("holdout_trade_return_bps_std")
        n = (entry or {}).get("holdout_trade_return_bps_n")
        if mean is None or std is None or n is None:
            reasons[symbol] = "missing_holdout_trade_return_bps_fields"
        elif not float(std) > 0:
            reasons[symbol] = "holdout_trade_return_bps_std_not_positive"
        else:
            refs[symbol] = {"mean": float(mean), "std": float(std), "n": int(n)}
    return refs, reasons
