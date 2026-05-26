# automation package
"""
automation — eToro Nautilus Automation-Paket.

Standalone-Modus: Kein Import aus adapters/ oder config/ außerhalb dieses Pakets.

Public API (lazy imports um schwere Abhängigkeiten wie aiohttp zu vermeiden):
    run_backfill       — API-Backfiller (von daily_orchestrator verwendet)
    _load_etoro_id_map — Lädt eToro-ID → Symbol-Map aus Universe-JSON
    _fallback_precisions — Precision-Heuristik (Fallback ohne API-Abfrage)
"""

# Leichtgewichtige utils sofort importieren
from automation.utils import _fallback_precisions

# Schwere Abhängigkeiten nur bei Bedarf (lazy)
def __getattr__(name: str):
    if name in ("run_backfill", "_load_etoro_id_map"):
        from automation import api_backfiller as _m
        return getattr(_m, name)
    raise AttributeError(f"module 'automation' has no attribute {name!r}")

__all__ = [
    "run_backfill",
    "_load_etoro_id_map",
    "_fallback_precisions",
]
