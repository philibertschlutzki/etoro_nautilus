"""Issue #566 — EQUITY-Spread-Realismus + symbol-spezifische Override-Map.

Der statische EQUITY-Spread von 8 bps überzeichnete die Kosten für liquide Large-Caps (TSLA real
~1–3 bps) um Faktor ~3–8 und maskierte echten Edge (Kosten-Overstatement). Fix: EQUITY auf 3 bps
rekalibriert PLUS optionale ``spread_bps_by_symbol``-Override-Map (Symbol → Asset-Class → DEFAULT).

Akzeptanzkriterien (Issue #566):
- Dokumentierte Quelle/Begründung für den gewählten EQUITY-/Symbol-Spread.
- Estimator: geschätzter Median-Spread aus synthetischen QuoteTicks trifft den Eingabewert.
"""
import json
import statistics
from pathlib import Path

import pytest

from automation.backtest_runner import resolve_spread_bps

CFG = json.loads((Path("automation/config/backtest.json")).read_text("utf-8"))


# ── Auflösungs-Präzedenz: Symbol-Override → Asset-Class → DEFAULT ─────────────────────────────
def test_symbol_override_wins_over_asset_class():
    ac = {"EQUITY": 3.0, "DEFAULT": 4.0}
    sym = {"TSLA.ETORO": 2.0}
    assert resolve_spread_bps("TSLA.ETORO", ac, sym, "EQUITY") == 2.0  # Override
    assert resolve_spread_bps("AAPL.ETORO", ac, sym, "EQUITY") == 3.0  # Asset-Class
    assert resolve_spread_bps("XYZ.ETORO", ac, sym, "UNKNOWN") == 4.0  # DEFAULT-Fallback


def test_resolution_backward_compatible_without_symbol_map():
    """Fehlt die Symbol-Map ⇒ reine Asset-Class-Auflösung (bit-identisch zum Legacy-Verhalten)."""
    ac = {"EQUITY": 3.0, "DEFAULT": 4.0}
    assert resolve_spread_bps("TSLA.ETORO", ac, None, "EQUITY") == 3.0
    assert resolve_spread_bps("TSLA.ETORO", ac, {}, "EQUITY") == 3.0
    # Ohne beide Maps ⇒ 0.0 (kein Spread-Modeling).
    assert resolve_spread_bps("TSLA.ETORO", None, None) == 0.0


# ── Config-Kalibrierung + Dokumentation ──────────────────────────────────────────────────────
def test_equity_spread_recalibrated_realistic():
    """EQUITY von 8 auf einen realistischen Blue-Chip-Wert (≤ 3 bps) gesenkt."""
    eq = CFG["spread_bps_by_asset_class"]["EQUITY"]
    assert eq <= 3.0, f"EQUITY-Spread {eq} bps immer noch unrealistisch weit (war 8.0)."
    assert eq > 0.0, "Ein Spread von 0 erzeugt Zero-Spread-Artefakte."


def test_tsla_symbol_override_present_and_realistic():
    sym_map = CFG.get("spread_bps_by_symbol", {})
    assert "TSLA.ETORO" in sym_map, "TSLA.ETORO-Override fehlt (Issue #566)."
    assert 1.0 <= sym_map["TSLA.ETORO"] <= 3.0, "TSLA-Spread außerhalb des realistischen 1–3 bps-Bands."


def test_spread_choice_is_documented():
    """Akzeptanzkriterium: dokumentierte Quelle/Begründung im _schema."""
    schema = CFG["_schema"]["fields"]
    assert "spread_bps_by_symbol" in schema
    assert "566" in schema["spread_bps_by_asset_class"]  # Issue-Referenz als Begründung
    assert "566" in schema["spread_bps_by_symbol"]


# ── Estimator: synthetische QuoteTicks → geschätzter Median-Spread trifft den Eingabewert ─────
def _apply_spread(mid: float, spread_bps: float) -> tuple[float, float]:
    """Repliziert exakt die Spread-Anwendung aus load_ticks_from_catalog (halber Spread je Seite)."""
    half_spread_pct = (spread_bps / 10000.0) / 2.0
    return mid * (1.0 - half_spread_pct), mid * (1.0 + half_spread_pct)


def test_effective_spread_estimator_recovers_input():
    """Aus synthetischen (bid, ask)-Paaren geschätzter effektiver Spread == Eingabewert ± Toleranz."""
    for spread_in in (2.0, 3.0, 8.0):
        mids = [100.0, 250.5, 42.0, 1337.0, 7.77]
        eff = []
        for mid in mids:
            bid, ask = _apply_spread(mid, spread_in)
            eff.append((ask - bid) / mid * 10000.0)  # effektiver Spread in bps
        assert statistics.median(eff) == pytest.approx(spread_in, abs=1e-9), (
            f"Estimator {statistics.median(eff)} weicht vom Eingabe-Spread {spread_in} ab."
        )
