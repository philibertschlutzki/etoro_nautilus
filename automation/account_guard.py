"""Konto-Sperre für das Paper-Trading.

Standard: Demo-Endpunkte (``.../execution/demo``, ``.../info/demo/pnl``). Manche Spielgeld-/Testkonten sind bei eToro
aber nur über die Real-Endpunkte erreichbar (der Key trägt nur ``real``-Scopes). Das ist ausschliesslich erlaubt,
wenn ``ETORO_PAPER_ACCOUNT_CID`` (die ``realCid`` des Testkontos) gesetzt ist UND ``GET /api/v1/me`` genau diese
Kennung liefert. Jeder andere Key — etwa ein späterer Echtgeld-Key mit anderer ``realCid`` — bricht ab (fail-closed)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

ME_URL = "https://public-api.etoro.com/api/v1/me"
PAPER_CID_ENV = "ETORO_PAPER_ACCOUNT_CID"
_USER_AGENT = "etoro-nautilus/1.0"        # Cloudflare blockt den Standard-urllib-User-Agent (error 1010)


class PaperAccountError(RuntimeError):
    """Der Key gehört nicht zum angehefteten Paper-Konto (oder die Prüfung ist nicht möglich)."""


def pinned_cid(environ=None) -> str | None:
    value = (environ if environ is not None else os.environ).get(PAPER_CID_ENV, "").strip()
    return value or None


def api_environment(environ=None) -> str:
    """Endpunkt-Satz: ``real`` NUR bei gesetzter Konto-ID (siehe ``verify_paper_account``), sonst ``demo``."""
    return "real" if pinned_cid(environ) else "demo"


def fetch_me(api_key: str, user_key: str, *, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(ME_URL, headers={
        "x-api-key": api_key, "x-user-key": user_key, "x-request-id": str(uuid.uuid4()),
        "User-Agent": _USER_AGENT})
    try:
        with opener(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise PaperAccountError(f"Konto-Prüfung (/me) fehlgeschlagen: {exc}") from exc


def verify_paper_account(api_key: str, user_key: str, *, environ=None, fetch=fetch_me) -> str:
    """Gibt ``demo`` zurück (keine Anheftung) oder ``real`` nach erfolgreicher Prüfung der angehefteten
    ``realCid``; sonst ``PaperAccountError``."""
    cid = pinned_cid(environ)
    if cid is None:
        return "demo"
    me = fetch(api_key, user_key)
    actual = str(me.get("realCid", ""))
    if actual != cid:
        raise PaperAccountError(
            f"Der Key gehört zum Konto {actual or '?'}, erlaubt ist nur das Paper-Konto {cid} "
            f"({PAPER_CID_ENV}). Es wird nicht gehandelt.")
    return "real"
