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


def materialize_papertrading_profile(depth_days: float | None = None, *, project_root: Path | None = None,
                                     oos_floor_days: int = 1, spec: dict | None = None,
                                     dest: Path | None = None) -> Path:
    spec = spec if spec is not None else profile_spec(depth_days, oos_floor_days=oos_floor_days)
    return config_profile.materialize(PROFILE, project_root=project_root, profiles={PROFILE: spec}, dest=dest)


# Eigenes Overlay für die Tages-Selektion der Inkubation: das Stunden-Overlay (executor.sh) bleibt unberührt.
SELECTION_OVERLAY = "config_papertrading_daily"


def materialize_daily_selection_overlay(catalog_path: Path, symbols: list[str] | None = None, *,
                                        project_root: Path | None = None) -> tuple[Path, dict]:
    """Overlay ``automation/config_papertrading_daily/`` (Achse OneDay, ~4 Jahre Historie) für die Auswahl der
    Inkubations-Kandidaten. Rückgabe: ``(overlay, plan)``. Gestempelt ``papertrading`` (nie Evidenz); der Bot
    handelt weiterhin nur auf der Stundenachse, die Tagesachse wählt nur Kandidaten für die Forward-Evidenz."""
    plan = plan_axis(catalog_path, symbols, axis="daily")
    root = (project_root or config_profile.PROJECT_ROOT).resolve()
    overlay = materialize_papertrading_profile(spec=plan["spec"], project_root=root,
                                               dest=root / "automation" / SELECTION_OVERLAY)
    return overlay, plan


# ─── Tagesachse (OneDay): die API liefert je Intervall 1000 Kerzen — 1h reicht ~42-64 Tage zurück, 1d ~4 Jahre ───

DAILY_EMBARGO_DAYS = 10
DAILY_HOLDOUT_EMBARGO_DAYS = 8
DAILY_SPLITS = 3
DAILY_MIN_IS_DAYS = 120
DAILY_MIN_HOLDOUT_DAYS = 60
DAILY_MIN_OOS_SESSION_BARS = 91      # Produktions-Schwelle; darunter relaxed (RELAXED_MIN_OOS_SESSION_BARS)


def measure_daily_depths(catalog_path: Path, symbols: list[str] | None = None) -> dict[str, float]:
    """Kalender-Spanne (Tage) der OneDay-Dateien je Symbol."""
    from automation.optimizer.sweep import _read_oneday_ts_events
    cat = Path(catalog_path)
    if symbols is None:
        qt = cat / "data" / "quote_tick"
        symbols = sorted(p.name for p in qt.iterdir() if p.is_dir()) if qt.is_dir() else []
    out: dict[str, float] = {}
    for sym in symbols:
        ts = _read_oneday_ts_events(sym, cat)
        if len(ts) >= 2:
            out[sym] = (max(ts) - min(ts)) / 86_400e9
    return out


def derive_walk_forward_daily(depth_days: float) -> tuple[dict, int]:
    """``(walk_forward, min_oos_session_bars)`` für die Tagesachse (1 Bar je Handelstag ≈ 5/7 je Kalendertag)."""
    import math
    usable = int(depth_days) - MARGIN_DAYS
    for min_bars in (DAILY_MIN_OOS_SESSION_BARS, RELAXED_MIN_OOS_SESSION_BARS):
        oos = math.ceil(min_bars * 7 / 5)
        rest = usable - DAILY_EMBARGO_DAYS - DAILY_HOLDOUT_EMBARGO_DAYS - DAILY_SPLITS * oos
        if rest >= DAILY_MIN_IS_DAYS + DAILY_MIN_HOLDOUT_DAYS:
            is_days = max(DAILY_MIN_IS_DAYS, round(rest * 0.45))
            return ({"is_window_days": is_days, "embargo_period_days": DAILY_EMBARGO_DAYS, "splits": DAILY_SPLITS,
                     "oos_window_days": oos, "holdout_days": rest - is_days,
                     "holdout_embargo_days": DAILY_HOLDOUT_EMBARGO_DAYS}, min_bars)
    need = (DAILY_EMBARGO_DAYS + DAILY_HOLDOUT_EMBARGO_DAYS + DAILY_MIN_IS_DAYS + DAILY_MIN_HOLDOUT_DAYS
            + DAILY_SPLITS * math.ceil(RELAXED_MIN_OOS_SESSION_BARS * 7 / 5) + MARGIN_DAYS)
    raise PaperTradingError(f"OneDay-Tiefe {depth_days:.1f} d reicht nicht (benötigt mindestens {need} d).")


def daily_profile_spec(depth_days: float) -> dict:
    wf, min_bars = derive_walk_forward_daily(depth_days)
    return {"backtest.json": {"bar_axis": "OneDay", "max_handelstage": 5,
                              "walk_forward": {**wf, "data_history_days": max(int(depth_days), 1)}},
            "optimizer.json": {"time_box_bars": 5.0, "gate1_buffer_days": 0, "champion_enabled": False,
                               "diagnostic_writeback_enabled": False,
                               "min_oos_session_bars_per_fold": min_bars}}


def plan_axis(catalog_path: Path, symbols: list[str] | None = None, *, floor: bool = False,
              axis: str = "auto") -> dict:
    """Wählt die Achse: ``auto`` nimmt die Stundenachse, wenn ihre Tiefe reicht, sonst die Tagesachse.
    Rückgabe: ``{"axis", "depth", "n_symbols", "walk_forward", "spec"}``."""
    if axis not in ("auto", "hourly", "daily"):
        raise PaperTradingError(f"Unbekannte Achse {axis!r} (erlaubt: auto, hourly, daily).")
    hourly_error = None
    if axis in ("auto", "hourly"):
        try:
            depth, n, wf = plan_geometry(catalog_path, symbols, floor=floor)
            return {"axis": "hourly", "depth": depth, "n_symbols": n, "walk_forward": wf,
                    "spec": profile_spec(depth, oos_floor_days=wf["oos_window_days"])}
        except PaperTradingError as exc:
            if axis == "hourly":
                raise
            hourly_error = exc
    depths = measure_daily_depths(catalog_path, symbols)
    depth = min(depths.values()) if (floor and depths) else pick_depth(depths)
    try:
        spec = daily_profile_spec(depth)
    except PaperTradingError as exc:
        raise PaperTradingError(f"{hourly_error or 'Stundenachse nicht angefordert'}; Tagesachse: {exc}") from exc
    return {"axis": "daily", "depth": depth, "n_symbols": len(depths),
            "walk_forward": spec["backtest.json"]["walk_forward"], "spec": spec}


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
    parser.add_argument("--axis", default="auto", choices=["auto", "hourly", "daily"],
                        help="auto: Stundenachse, wenn die 1h-Tiefe reicht, sonst Tagesachse (OneDay, ~4 Jahre)")
    args = parser.parse_args(argv)
    try:
        assert_demo_environment(os.environ.get("ETORO_ENV"))
        syms = [s for s in args.symbols.split(",") if s] if args.symbols else None
        plan = plan_axis(Path(args.catalog_path), syms, floor=bool(syms), axis=args.axis)
        overlay = materialize_papertrading_profile(spec=plan["spec"])
    except (PaperTradingError, config_profile.ConfigProfileError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(f"Achse: {plan['axis']}; Datentiefe: {plan['depth']:.1f} d ({plan['n_symbols']} Symbole); "
          f"Geometrie: {plan['walk_forward']}", file=sys.stderr)
    print(overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
