"""Paper-Trading-Modus: Optimierung mit der maximal vorhandenen Datentiefe + Handel im Demo-Konto.

* ``measure_depth_days`` misst die Historientiefe aus dem Katalog (kleinste Tiefe über die Symbole, die
  überhaupt Daten haben) — nichts ist auf 99 Tage fest verdrahtet.
* ``derive_walk_forward`` leitet daraus eine Walk-Forward-Geometrie ab, die in die Tiefe passt.
* ``materialize_papertrading_profile`` erzeugt das Overlay ``automation/config_papertrading/`` über den
  bestehenden Config-Profil-Mechanismus (#1381): gestempelt ``config_profile = "papertrading"``, kein
  Champion-Store, kein Rückschrieb. Ergebnisse sind NIE Evidenz; die Deployment-Grenze (Klausel
  ``config_profile_production``) blockiert sie weiterhin.
* ``assert_demo_environment`` ist die harte Sperre: Paper-Trading läuft nie gegen ein ``real``-Konto."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from automation.optimizer import config_profile

PROFILE = "papertrading"
MIN_IS_DAYS = 10
# Gate 1 (c) im Overlay: 91 Session-Bars je OOS-Fold sind auf <= 60 Tagen Historie für RTH-Aktien nicht erreichbar
# (3 Wochen je Fold). Das Profil ist nie Evidenz; 49 = 7 Handelstage RTH, damit die Pipeline durchlaufen kann.
RELAXED_MIN_OOS_SESSION_BARS = 49
MIN_HOLDOUT_DAYS = 5
MARGIN_DAYS = 2
EMBARGO_DAYS = 5
HOLDOUT_EMBARGO_DAYS = 3
SPLITS = 2


class PaperTradingError(RuntimeError):
    """Paper-Trading ist in dieser Konfiguration nicht erlaubt bzw. nicht möglich."""


def assert_demo_environment(environment: str | None) -> None:
    """Fail-closed: nur ``demo`` (bzw. nicht gesetzt ⇒ Default ``demo``) ist erlaubt."""
    env = (environment or "demo").strip().lower()
    if env != "demo":
        raise PaperTradingError(
            f"--papertrading verweigert: ETORO_ENV={environment!r} zeigt nicht auf das Demo-Konto. "
            f"Gehandelt wird ausschliesslich im Demo-Konto (fest verdrahtet); ETORO_ENV ist kein Schalter, "
            f"bitte entfernen.")


def measure_depths(catalog_path: Path, symbols: list[str] | None = None) -> dict[str, float]:
    """Kalender-Historientiefe (Tage, dieselbe Messung wie der Sweep-Preflight) je Symbol mit Daten."""
    from automation.optimizer.sweep import count_available_bars
    cat = Path(catalog_path)
    if symbols is None:
        qt = cat / "data" / "quote_tick"
        symbols = sorted(p.name for p in qt.iterdir() if p.is_dir()) if qt.is_dir() else []
    return {s: n / 24.0 for s, n in count_available_bars(symbols, catalog_path=cat).items() if n > 0}


def pick_depth(depths: dict[str, float], quantile: float = 0.25) -> float:
    """Tiefe, die mindestens ``1 - quantile`` der Symbole erreichen (Default: 75 %). Die flacheren Symbole
    (z. B. Krypto mit 1000 Bars ≈ 41,7 d) lehnt der Sweep-Preflight einzeln ab, der Lauf bleibt gültig."""
    if not depths:
        return 0.0
    vals = sorted(depths.values())
    return vals[min(len(vals) - 1, int(len(vals) * quantile))]


def measure_depth_days(catalog_path: Path, symbols: list[str] | None = None) -> float:
    return pick_depth(measure_depths(catalog_path, symbols))


def min_oos_days(symbols: list[str], min_session_bars: int = RELAXED_MIN_OOS_SESSION_BARS) -> int:
    """Kleinste OOS-Fensterlänge (Tage), bei der JEDES Symbol Gate 1 (c) erfüllt (>= ``min_session_bars``
    Session-Bars je Fold; Aktien mit RTH-Achse brauchen mehr Kalendertage als 24/7-Krypto)."""
    from automation.optimizer.gate import oos_session_bars_per_fold
    from automation.optimizer.sweep import _symbol_session_window
    need = 1
    for sym in symbols:
        win = _symbol_session_window(sym)
        d = 1
        while d < 120 and oos_session_bars_per_fold(d, win) < min_session_bars:
            d += 1
        need = max(need, d)
    return need


def derive_walk_forward(depth_days: float, *, oos_floor_days: int = 1) -> dict:
    """Walk-Forward-Geometrie, deren Summe (IS + Embargo + SPLITS·OOS + Holdout + Holdout-Embargo) in
    ``depth_days - MARGIN_DAYS`` passt. Bei 96 Tagen ≈ die Smoke-Geometrie (94 d). ``oos_floor_days`` hebt das
    OOS-Fenster auf die Gate-1-(c)-Untergrenze der Ziel-Symbole an. Passt nichts, nennt der Fehler die
    benötigte Mindesttiefe in Tagen."""
    usable = int(depth_days) - MARGIN_DAYS
    rem = usable - EMBARGO_DAYS - HOLDOUT_EMBARGO_DAYS
    oos = max(int(oos_floor_days), int(rem * 0.20))
    is_days = round(rem * 0.35)
    holdout = rem - is_days - SPLITS * oos
    if holdout < MIN_HOLDOUT_DAYS:
        is_days = max(MIN_IS_DAYS, is_days + holdout - MIN_HOLDOUT_DAYS)
        holdout = rem - is_days - SPLITS * oos
    if holdout < MIN_HOLDOUT_DAYS or is_days < MIN_IS_DAYS:
        need = (EMBARGO_DAYS + HOLDOUT_EMBARGO_DAYS + MIN_IS_DAYS + SPLITS * oos + MIN_HOLDOUT_DAYS + MARGIN_DAYS)
        raise PaperTradingError(
            f"Datentiefe {depth_days:.1f} d reicht nicht für eine Paper-Trading-Geometrie (benötigt "
            f"mindestens {need} d bei OOS-Fenster {oos} d). Erst Daten nachladen (Phase 2 / historical_fetcher).")
    return {"is_window_days": is_days, "embargo_period_days": EMBARGO_DAYS, "splits": SPLITS,
            "oos_window_days": oos, "holdout_days": holdout, "holdout_embargo_days": HOLDOUT_EMBARGO_DAYS}


def profile_spec(depth_days: float, *, oos_floor_days: int = 1) -> dict:
    return {"backtest.json": {"walk_forward": derive_walk_forward(depth_days, oos_floor_days=oos_floor_days)},
            "optimizer.json": {"gate1_buffer_days": 0, "champion_enabled": False,
                               "min_oos_session_bars_per_fold": RELAXED_MIN_OOS_SESSION_BARS,
                               "diagnostic_writeback_enabled": False}}


def materialize_papertrading_profile(depth_days: float, *, project_root: Path | None = None,
                                     oos_floor_days: int = 1) -> Path:
    return config_profile.materialize(
        PROFILE, project_root=project_root,
        profiles={PROFILE: profile_spec(depth_days, oos_floor_days=oos_floor_days)})


def plan_geometry(catalog_path: Path, symbols: list[str] | None = None, *, floor: bool = False) -> tuple[float, int, dict]:
    """``(depth_days, n_symbols, walk_forward)`` für die Ziel-Symbole (Default: ganzer Katalog)."""
    depths = measure_depths(catalog_path, symbols)
    # ``floor``: Sweep-Ziele, jedes Symbol muss passen => flachste Tiefe; sonst das 25-%-Quantil (Universum).
    depth = min(depths.values()) if (floor and depths) else pick_depth(depths)
    reachable = sorted(s for s, d in depths.items() if d >= depth)
    return depth, len(depths), derive_walk_forward(depth, oos_floor_days=min_oos_days(reachable))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-Trading-Overlay mit maximaler Datentiefe erzeugen")
    parser.add_argument("--symbols", default=None, help="Komma-Liste; Default: alle Katalog-Symbole")
    parser.add_argument("--catalog-path", default=str(config_profile.PROJECT_ROOT / "data" / "nautilus"))
    args = parser.parse_args(argv)
    try:
        assert_demo_environment(os.environ.get("ETORO_ENV"))
        syms = [s for s in args.symbols.split(",") if s] if args.symbols else None
        depth, n_syms, geometry = plan_geometry(Path(args.catalog_path), syms, floor=bool(syms))
        overlay = materialize_papertrading_profile(depth, oos_floor_days=geometry["oos_window_days"])
    except (PaperTradingError, config_profile.ConfigProfileError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(f"Datentiefe: {depth:.1f} d ({n_syms} Symbole); Geometrie: {geometry}", file=sys.stderr)
    print(overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
