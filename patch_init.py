with open("automation/__init__.py", "r", encoding="utf-8") as f:
    content = f.read()

replacement = """
# automation/__init__.py
\"\"\"
automation — eToro Nautilus Automation-Paket (Standalone-Produkt).

Kein Import aus adapters/, config/ (Root), strategies/ (Root).
Alle Abhängigkeiten sind intern in automation/ verfügbar.

Public API:
    run_backfill        — API-Backfiller
    run_fetch           — Universe-Fetcher
    is_universe_stale   — Universe-Freshness-Check
    _load_etoro_id_map  — eToro-ID → Symbol-Map
    _fallback_precisions — Precision-Heuristik
\"\"\"
from automation.utils import _fallback_precisions

def __getattr__(name: str):
    if name in ("run_backfill", "_load_etoro_id_map"):
        from automation.api_backfiller import run_backfill, _load_etoro_id_map
        if name == "run_backfill": return run_backfill
        return _load_etoro_id_map
    if name in ("run_fetch", "is_universe_stale", "load_instrument_map"):
        from automation import universe_fetcher as _m
        return getattr(_m, name)
    raise AttributeError(f"module 'automation' has no attribute {name!r}")

__all__ = [
    "run_backfill",
    "run_fetch",
    "is_universe_stale",
    "load_instrument_map",
    "_load_etoro_id_map",
    "_fallback_precisions",
]
"""

with open("automation/__init__.py", "w", encoding="utf-8") as f:
    f.write(replacement)
