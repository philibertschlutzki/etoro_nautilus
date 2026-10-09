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
MIN_DEPTH_DAYS = 40
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
            f"Paper-Trading handelt ausschliesslich mit ETORO_ENV=demo und fällt nie auf ein echtes Konto zurück.")


def measure_depth_days(catalog_path: Path, symbols: list[str] | None = None) -> float:
    """Kleinste 1h-Historientiefe (Tage) über die Symbole mit Daten; 0.0 ohne Daten."""
    from automation.optimizer.sweep import count_available_bars
    cat = Path(catalog_path)
    if symbols is None:
        qt = cat / "data" / "quote_tick"
        symbols = sorted(p.name for p in qt.iterdir() if p.is_dir()) if qt.is_dir() else []
    bars = {s: n for s, n in count_available_bars(symbols, catalog_path=cat).items() if n > 0}
    return min(bars.values()) / 24.0 if bars else 0.0


def derive_walk_forward(depth_days: float) -> dict:
    """Walk-Forward-Geometrie, deren Summe (IS + Embargo + SPLITS·OOS + Holdout + Holdout-Embargo) in
    ``depth_days - MARGIN_DAYS`` passt. Bei 96 Tagen ≈ die Smoke-Geometrie (94 d)."""
    usable = int(depth_days) - MARGIN_DAYS
    if usable < MIN_DEPTH_DAYS:
        raise PaperTradingError(
            f"Datentiefe {depth_days:.1f} d reicht nicht für eine Paper-Trading-Geometrie (Minimum "
            f"{MIN_DEPTH_DAYS + MARGIN_DAYS} d). Erst Daten nachladen (Phase 2 / historical_fetcher).")
    rem = usable - EMBARGO_DAYS - HOLDOUT_EMBARGO_DAYS
    is_days = round(rem * 0.35)
    oos = int(rem * 0.20)
    holdout = rem - is_days - SPLITS * oos
    return {"is_window_days": is_days, "embargo_period_days": EMBARGO_DAYS, "splits": SPLITS,
            "oos_window_days": oos, "holdout_days": holdout, "holdout_embargo_days": HOLDOUT_EMBARGO_DAYS}


def profile_spec(depth_days: float) -> dict:
    return {"backtest.json": {"walk_forward": derive_walk_forward(depth_days)},
            "optimizer.json": {"gate1_buffer_days": 0, "champion_enabled": False,
                               "diagnostic_writeback_enabled": False}}


def materialize_papertrading_profile(depth_days: float, *, project_root: Path | None = None) -> Path:
    return config_profile.materialize(PROFILE, project_root=project_root,
                                      profiles={PROFILE: profile_spec(depth_days)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-Trading-Overlay mit maximaler Datentiefe erzeugen")
    parser.add_argument("--symbols", default=None, help="Komma-Liste; Default: alle Katalog-Symbole")
    parser.add_argument("--catalog-path", default=str(config_profile.PROJECT_ROOT / "data" / "nautilus"))
    args = parser.parse_args(argv)
    try:
        assert_demo_environment(os.environ.get("ETORO_ENV"))
        syms = [s for s in args.symbols.split(",") if s] if args.symbols else None
        depth = measure_depth_days(Path(args.catalog_path), syms)
        overlay = materialize_papertrading_profile(depth)
    except (PaperTradingError, config_profile.ConfigProfileError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(f"Datentiefe: {depth:.1f} d; Geometrie: {derive_walk_forward(depth)}", file=sys.stderr)
    print(overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
