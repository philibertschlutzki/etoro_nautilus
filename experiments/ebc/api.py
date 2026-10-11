"""Minimaler read-only eToro-Client (Candles, Rates, Metadaten). Schluessel nie ausgeben. <=1 Req/s, Backoff bei 429."""
import json, os, threading, time, uuid, urllib.request, urllib.error
_lock = threading.Lock()

BASE = "https://public-api.etoro.com/api/v1"
_last = [0.0]

def _env():
    if "ETORO_API_KEY" not in os.environ:
        for line in open(os.path.join(os.path.dirname(__file__), "..", "..", ".env")):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["ETORO_API_KEY"], os.environ["ETORO_USER_KEY"]

def get(url, min_interval=1.05, tries=6):
    ak, uk = _env()
    for i in range(tries):
        with _lock:   # globaler Takt: insgesamt <= 1 Request/s ueber alle Threads
            wait = min_interval - (time.time() - _last[0])
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

def candles(etoro_id, interval="OneDay", count=1000):
    return get(f"{BASE}/market-data/instruments/{etoro_id}/history/candles/desc/{interval}/{count}")

def rates(ids):
    return get(f"{BASE}/market-data/instruments/rates?instrumentIds=" + ",".join(str(i) for i in ids))
