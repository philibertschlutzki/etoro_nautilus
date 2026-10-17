"""Stufe C — Sicherheitsschicht. KEINE Order ohne: (1) Live-Flag + Bestaetigung der Konto-ID, (2) frische /me-Pruefung gegen ETORO_PAPER_ACCOUNT_CID,
(3) Instrument weder in der Bot-Whitelist noch in einer offenen Konto-Position (ausser eigene, per positionId verwaltete Testposition),
(4) Betragsgrenzen, (5) <= 20 gleichzeitige Testpositionen, (6) Handelszeit. Dry-Run ist Standard und sendet nie einen POST."""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

BASE = "https://public-api.etoro.com/api/v1"
# Pfade je Umgebung: mit ETORO_PAPER_ACCOUNT_CID -> /real/ (nur fuer das angeheftete Paper-Konto, per /me geprueft);
# ohne -> /demo/ (Key mit Demo-Scope, Demo-Konto). Wird in set_environment() gesetzt.
EXEC = f"{BASE}/trading/execution"
PNL = f"{BASE}/trading/info/real/pnl"
ORDER_INFO = f"{BASE}/trading/info/real/orders"
ENV_MODE = "real"


FORCE_ENV = [None]        # "demo" | "real" | None (aus ETORO_PAPER_ACCOUNT_CID abgeleitet)


def set_environment():
    global EXEC, PNL, ORDER_INFO, ENV_MODE
    use_real = bool(os.environ.get("ETORO_PAPER_ACCOUNT_CID", "").strip()) if FORCE_ENV[0] is None else FORCE_ENV[0] == "real"
    if use_real:
        ENV_MODE, EXEC, PNL, ORDER_INFO = "real", f"{BASE}/trading/execution", f"{BASE}/trading/info/real/pnl", f"{BASE}/trading/info/real/orders"
    else:
        ENV_MODE, EXEC, PNL, ORDER_INFO = "demo", f"{BASE}/trading/execution/demo", f"{BASE}/trading/info/demo/pnl", f"{BASE}/trading/info/demo/orders"
CAPS = {"mn": 20.0, "fp": 1000.0, "w8": 10.0, "c1": 50.0, "c4": 20.0, "c5": 50.0, "c2": 100.0, "c3": 100.0}
MAX_TEST_POSITIONS = 20
DEFAULT_CLASSES = ('K1', 'K5')          # FX/Rohstoffe/Indizes: eToro lehnt kleine Betraege ab (FX < 1000 USD); nur mit --classes
FX_FIN_AMOUNT = 1010.0                   # Phili 2026-10-12: FX-Finanzierungsmessung nur mit Order == 1010 USD (eToro-Minimum 1000)
FX_MAX_POSITIONS = 4
MIN_AMOUNT_USD = {'K2': 1000.0}          # vom Bot beobachtet (2026-10-11 21:00 UTC); K3/K4 unbekannt -> Ablehnung wird als Messwert verbucht


class Refused(RuntimeError):
    """Eine Sicherheitsregel verbietet die Aktion; es wurde nichts gesendet."""


def load_env(path=".env"):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _req(url, method="GET", payload=None):
    h = {"x-api-key": os.environ["ETORO_API_KEY"], "x-user-key": os.environ["ETORO_USER_KEY"], "x-request-id": str(uuid.uuid4()),
         "User-Agent": "etoro-nautilus/1.0", "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


_last_get = [0.0]


def get(url, tries=6):
    """GET mit Backoff bei HTTP 429/5xx (nie abbrechen, solange eine Order unbestaetigt sein koennte)."""
    import time
    for i in range(tries):
        wait = 1.1 - (time.time() - _last_get[0])          # >= 1 s Abstand: Rate-Limit mit dem Bot teilen
        if wait > 0: time.sleep(wait)
        _last_get[0] = time.time()
        s, b = _req(url)
        if s == 200:
            return b
        if s in (429, 500, 502, 503, 504):
            time.sleep(min(60, 3 * 2 ** i)); continue
        break
    raise RuntimeError(f"GET {url.split('?')[0]} HTTP {s}")


def verify_account():
    """GET /me muss die angeheftete Paper-Konto-ID liefern, sonst Abbruch (fail-closed)."""
    set_environment()
    pinned = os.environ.get("ETORO_PAPER_ACCOUNT_CID", "").strip()
    if ENV_MODE == "demo":   # Demo-Modus: ausschliesslich /demo/-Pfade; Erreichbarkeit per demo/pnl (/me kann bei Demo-Keys 403 liefern)
        try:
            get(PNL)                       # mit Backoff bei 429/5xx
        except RuntimeError as e:
            raise Refused(f"Demo-pnl nicht erreichbar ({e}) - Key ohne Demo-Scope oder Rate-Limit?")
        return "demo"
    if not pinned:
        raise Refused("ETORO_PAPER_ACCOUNT_CID nicht gesetzt (real-Modus verlangt die angeheftete Paper-Konto-ID)")
    me = get(f"{BASE}/me")
    if str(me.get("realCid", "")) != pinned:
        raise Refused("/me liefert nicht das Paper-Konto - Abbruch")
    return pinned


def account_equity():
    d = get(PNL).get('clientPortfolio', {})
    inv = sum(float(p.get('amount', 0)) + float((p.get('unrealizedPnL') or {}).get('pnL', 0) or 0) for p in d.get('positions') or [])
    return float(d.get('credit') or 0) + inv


def positions(pnl=None):
    d = (pnl or get(PNL)).get("clientPortfolio", {})
    return d.get("positions") or []


def bot_excluded_ids(whitelist_path, map_path):
    """Instrument-IDs der Bot-Whitelist (nur lesend)."""
    wl = json.load(open(whitelist_path)); syms = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and v.endswith(".ETORO"): syms.add(v)
                if isinstance(k, str) and k.endswith(".ETORO"): syms.add(k)
                walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(wl)
    imap = json.load(open(map_path))["instruments"]
    return {int(i) for i, e in imap.items() if e["symbol"] in syms}


def market_open(klasse: str, now: dt.datetime | None = None) -> bool:
    """K1 US-Aktien/ETF: Mo-Fr 9:30-16:00 New York; K2 FX: Sonntag 17:00 - Freitag 17:00 NY; K5 Krypto 24/7; K3/K4 konservativ wie Aktien."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    wd, hm = now.weekday(), now.hour * 60 + now.minute
    if klasse == "K5": return True
    if klasse == "K2": return not (wd == 5 or (wd == 4 and hm >= 17 * 60) or (wd == 6 and hm < 17 * 60))
    return wd < 5 and 9 * 60 + 35 <= hm < 15 * 60 + 55


class Gate:
    def __init__(self, live: bool, confirm_cid: str | None, excluded_ids: set[int], state_path: str, phase: str, log):
        self.live, self.confirm, self.excluded, self.state_path, self.phase, self.log = live, confirm_cid, set(excluded_ids), state_path, phase, log
        self.classes = DEFAULT_CLASSES
        self.fx_max_exposure = 0.04
        self.state = json.load(open(state_path)) if os.path.exists(state_path) else {"positions": {}}
        self.state.setdefault("pending", {})

    def save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        json.dump(self.state, open(self.state_path, "w"), indent=1)

    def check_open(self, instrument_id: int, amount: float, is_buy: bool, klasse: str):
        cap = CAPS[self.phase]
        if klasse not in getattr(self, 'classes', DEFAULT_CLASSES): raise Refused(f'Klasse {klasse} in diesem Lauf nicht freigegeben (--classes)')
        fx_ok = (klasse == 'K2' and self.phase == 'c2' and abs(amount - FX_FIN_AMOUNT) < 1e-6)
        if klasse == 'K2':
            if not fx_ok: raise Refused('FX nur in Phase fin mit genau 1010 USD (Mindestbetrag 1000, andere Caps unveraendert)')
            if sum(1 for v in self.state['positions'].values() if v.get('klasse') == 'K2') >= FX_MAX_POSITIONS: raise Refused('mehr als 4 FX-Testpositionen')
            equity = account_equity()
            used = sum(float(v.get('amount', 0)) for v in self.state['positions'].values())
            if used + amount > self.fx_max_exposure * equity: raise Refused(f'Exposure der Testpositionen {used + amount:.0f} USD > {self.fx_max_exposure:.0%} des Kontos ({equity:.0f} USD); --fx-max-exposure explizit anheben')
        if amount < MIN_AMOUNT_USD.get(klasse, 0.0) and not fx_ok: raise Refused(f'Mindestbetrag {MIN_AMOUNT_USD[klasse]} USD fuer {klasse} > Betrag {amount} (Caps werden nicht erhoeht)')
        if amount > cap and not (klasse == 'K2' and self.phase == 'c2' and abs(amount - FX_FIN_AMOUNT) < 1e-6): raise Refused(f"Betrag {amount} > Limit {cap} ({self.phase})")
        if int(instrument_id) in self.excluded: raise Refused(f"Instrument {instrument_id} wird vom Bot gehandelt (Whitelist)")
        if klasse == "K5" and not is_buy: raise Refused("Krypto nur long")
        if not market_open(klasse): raise Refused(f"Markt fuer {klasse} geschlossen")
        if len(self.state["positions"]) >= MAX_TEST_POSITIONS: raise Refused("mehr als 20 offene Testpositionen")
        verify_account()                                         # frisch vor JEDER Order
        for p in positions():
            if int(p.get("instrumentID", 0)) == int(instrument_id) and str(p.get("positionID")) not in self.state["positions"]:
                raise Refused(f"auf Instrument {instrument_id} liegt eine fremde offene Position")

    def check_close(self, position_id: str):
        if str(position_id) not in self.state["positions"]:
            raise Refused(f"Position {position_id} ist keine eigene Testposition")
        verify_account()

    def post(self, url, payload):
        if not self.live:
            self.log(f"DRYRUN POST {url.replace(EXEC, '.../execution')} {json.dumps(payload)}")
            return 200, {"dryrun": True}
        expected = "demo" if ENV_MODE == "demo" else os.environ.get("ETORO_PAPER_ACCOUNT_CID", "").strip()
        if self.confirm != expected:
            raise Refused("--confirm-account stimmt nicht mit ETORO_PAPER_ACCOUNT_CID ueberein")
        return _req(url, "POST", payload)
