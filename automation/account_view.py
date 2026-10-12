"""Kontosicht direkt von eToro für das Risikomanagement (nur lesend).

Die Risikorechnung (Verlust-Stopp, Exposure-Deckel, Positionsgrösse) liest Guthaben und offene Positionen NICHT
aus dem Bot-Gedächtnis (Nautilus-Cache), sondern aus ``GET .../trading/info/<env>/pnl``. Der Cache kennt weder
Positionen anderer Prozesse auf demselben Konto noch Positionen, die nach einem Neustart nicht übernommen wurden, und
der Adapter meldet als Kontostand nur das freie Guthaben.

  Equity = credit + Σ Betrag der offenen Positionen + Σ unrealisierter PnL
  (mit ``bot_capital``: Equity = festes Kapital + realisiert + unrealisiert, siehe bot_capital.py)

``EToroAccountView.snapshot()`` liefert einen höchstens ``ttl_s`` Sekunden alten Stand (ein Abruf bedient Watchdog-
Thread und Strategien) und ``None``, wenn der Abruf fehlschlägt und der letzte Stand älter als ``max_age_s`` ist:
Aufrufer behandeln ``None`` fail-closed (keine neuen Einstiege) bzw. überspringen den Tick.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, replace

PNL_URLS = {"demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
            "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl"}
FETCH_TIMEOUT_S = 5.0     # läuft im Event-Loop-Thread der Strategien: kurz halten, der Cache fängt die Wiederholung ab


def _number(value) -> float | None:
    """Zahl aus einem Feld, das eToro je nach Endpunkt als Zahl oder als Objekt (``{"pnL": 0.28, ...}``) liefert."""
    if isinstance(value, dict):
        low = {str(k).lower(): v for k, v in value.items()}
        value = next((low[k] for k in ("pnl", "profit", "netprofit") if low.get(k) is not None), None)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EToroPosition:
    position_id: str
    instrument_id: int
    is_buy: bool
    amount: float          # eingesetzter Betrag in USD
    leverage: float
    pnl: float             # unrealisierter PnL in USD
    stop_loss_rate: float | None = None
    open_rate: float | None = None
    opened_iso: str | None = None       # eToro-Öffnungszeit (UTC, ISO-8601)
    external_fees: float = 0.0          # totalExternalFees (Krypto-Einstiegsgebühr)

    @property
    def notional(self) -> float:
        return self.amount * max(self.leverage, 1.0)


@dataclass(frozen=True)
class AccountSnapshot:
    credit: float
    positions: tuple[EToroPosition, ...]
    taken_at: float = field(default_factory=time.time)
    bot_equity: float | None = None     # gesetzt, wenn ein festes Bot-Kapital gilt (automation/bot_capital.py)

    @property
    def invested(self) -> float:
        return sum(p.amount for p in self.positions)

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.pnl for p in self.positions)

    @property
    def equity(self) -> float:
        """Bei festem Bot-Kapital dessen Equity (unabhängig von Copy-Trading/Einzahlungen), sonst Konto-Equity."""
        if self.bot_equity is not None:
            return self.bot_equity
        return self.credit + self.invested + self.unrealized_pnl

    @property
    def notional(self) -> float:
        return sum(p.notional for p in self.positions)

    def positions_in(self, instrument_id: int) -> list[EToroPosition]:
        return [p for p in self.positions if p.instrument_id == int(instrument_id)]

    def unprotected(self) -> list[EToroPosition]:
        return [p for p in self.positions if not p.stop_loss_rate]


def parse_snapshot(pnl_data: dict, *, now: float | None = None) -> AccountSnapshot:
    """Rein, deterministisch: PnL-Antwort -> ``AccountSnapshot`` (Feldnamen case-insensitiv)."""
    data = pnl_data.get("clientPortfolio", pnl_data) if isinstance(pnl_data, dict) else {}
    low_top = {str(k).lower(): v for k, v in data.items()}
    credit = _number(next((low_top[k] for k in ("credit", "credits", "availablecash") if low_top.get(k) is not None),
                          None))
    if credit is None:
        raise ValueError("PnL-Antwort ohne credit")
    raw = low_top.get("positions") or []
    positions = []
    for item in raw:
        low = {str(k).lower(): v for k, v in item.items()}
        pid, eid = low.get("positionid"), low.get("instrumentid")
        amount = _number(low.get("amount") if low.get("amount") is not None else low.get("investedamount"))
        if pid is None or eid is None or amount is None:
            raise ValueError(f"Position nicht lesbar: {pid!r}")
        is_buy = low.get("isbuy", True)
        if isinstance(is_buy, str):
            is_buy = is_buy.lower() == "true"
        pnl = _number(next((low[k] for k in ("unrealizedpnl", "netprofit", "profit") if low.get(k) is not None), None))
        positions.append(EToroPosition(
            position_id=str(pid), instrument_id=int(eid), is_buy=bool(is_buy), amount=amount,
            leverage=_number(low.get("leverage")) or 1.0, pnl=pnl or 0.0,
            stop_loss_rate=_number(low.get("stoplossrate")), open_rate=_number(low.get("openrate")),
            opened_iso=(str(low["opendatetime"]) if low.get("opendatetime") else None),
            external_fees=_number(low.get("totalexternalfees")) or 0.0))
    return AccountSnapshot(credit=credit, positions=tuple(positions), taken_at=time.time() if now is None else now)


def fetch_pnl_sync(api_key: str, user_key: str, environment: str, *, opener=urllib.request.urlopen) -> dict:
    from automation.account_guard import _USER_AGENT
    req = urllib.request.Request(PNL_URLS[environment], headers={
        "x-api-key": api_key, "x-user-key": user_key, "x-request-id": str(uuid.uuid4()), "User-Agent": _USER_AGENT})
    with opener(req, timeout=FETCH_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


RATE_LIMIT_BACKOFF_S = 10.0     # HTTP 429 ohne Retry-After
ERROR_BACKOFF_S = 3.0           # alle anderen Abruffehler
MAX_BACKOFF_S = 60.0


def _backoff_s(exc: Exception) -> float:
    """Wartezeit nach einem Abruffehler: ``Retry-After`` (höchstens 60 s) bzw. 10 s bei 429, sonst 3 s."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        try:
            return min(MAX_BACKOFF_S, max(1.0, float(exc.headers.get("Retry-After"))))
        except (AttributeError, TypeError, ValueError):
            return RATE_LIMIT_BACKOFF_S
    return ERROR_BACKOFF_S


class EToroAccountView:
    """Thread-sicherer, zwischengespeicherter Lesezugriff auf das Konto (siehe Modul-Docstring)."""

    def __init__(self, api_key: str, user_key: str, environment: str, *, fetch=fetch_pnl_sync, ttl_s: float = 10.0,
                 max_age_s: float = 120.0, clock=time.time, log=None, bot_capital=None, observers=None) -> None:
        self._args = (api_key, user_key, environment)
        self._observers = list(observers or [])      # je erfolgreichem Abruf aufgerufen (z. B. Trade-Ledger)
        self._fetch = fetch
        self._ttl_s, self._max_age_s, self._clock = float(ttl_s), float(max_age_s), clock
        self._log = log
        self._bot_capital = bot_capital
        self._lock = threading.Lock()
        self._last: AccountSnapshot | None = None
        self._retry_not_before = 0.0

    @property
    def uses_bot_capital(self) -> bool:
        return self._bot_capital is not None

    def snapshot(self, *, fresh: bool = False) -> AccountSnapshot | None:
        """Stand höchstens ``ttl_s`` alt (``fresh=True`` erzwingt einen Abruf). ``None``, wenn der Abruf scheitert und
        kein Stand jünger als ``max_age_s`` vorliegt."""
        with self._lock:
            now = self._clock()
            if self._last is not None and not fresh and now - self._last.taken_at < self._ttl_s:
                return self._last
            if now < self._retry_not_before:
                # Nach einem Fehler (v. a. HTTP 429) nicht bei jedem Aufruf neu abrufen: das verschärft das Rate-Limit.
                return self._last if self._last is not None and now - self._last.taken_at <= self._max_age_s else None
            try:
                snap = parse_snapshot(self._fetch(*self._args), now=now)
                if self._bot_capital is not None:
                    snap = replace(snap, bot_equity=self._bot_capital.observe(snap.positions))
                self._last = snap
                self._retry_not_before = 0.0
                for observer in self._observers:
                    try:
                        observer(self._last)
                    except Exception as exc:     # ein Beobachter darf die Risikorechnung nie stören
                        if self._log is not None:
                            self._log.warning(f"[ACCOUNT_VIEW] Beobachter fehlgeschlagen: {exc!r}")
            except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
                self._retry_not_before = now + _backoff_s(exc)
                if self._log is not None:
                    self._log.warning(f"[ACCOUNT_VIEW] Kontoabruf fehlgeschlagen: {exc} "
                                      f"(nächster Versuch frühestens in {self._retry_not_before - now:.0f} s)")
            if self._last is not None and now - self._last.taken_at <= self._max_age_s:
                return self._last
            return None
