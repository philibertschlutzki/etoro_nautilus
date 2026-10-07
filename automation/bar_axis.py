"""Issue #1382 (GH #1284, Pitfall #503) — EINE Quelle für die Bar-Achse.

Die Bar-Achse war an über 15 Stellen als Stunde verdrahtet (Intervall in ns, Bars je Handelstag, Bar-Typ-Suffix,
Katalog-Intervall, Resample-Regel). ``backtest.json["bar_axis"]`` (Default ``"OneHour"``) wählt jetzt die Achse; alles
andere leitet sich aus der Tabelle ``AXES`` ab. ``tests/test_issue_1284_bar_axis.py`` verbietet die Literale
``3_600_000_000_000``, ``-1-HOUR-`` und ``"OneHour"`` in den achsenverbrauchenden Modulen ausserhalb dieser Tabelle
(Abruf-Schicht ``api_backfiller``/``historical_fetcher`` führt die API-Vokabeln der Candle-Intervalle).

Das Modul ist bewusst import-leicht (nur Standardbibliothek): ``optimizer/_contracts.py`` leitet seine Konstanten beim
Import daraus ab — die Achse ist damit je Prozess fest (wie jede andere beim Import gelesene Konfiguration; ein
Profil-Overlay setzt ``ETORO_CONFIG_DIR`` vor dem Start)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AXIS = "OneHour"
LIVE_AXIS = "OneHour"            # Live (momentum_ls_run, Phase 5) ist bis zu einem eigenen Issue auf die Stundenachse gesperrt
MAX_HANDELSTAGE_DEFAULT = 1.0


@dataclass(frozen=True)
class BarAxis:
    name: str
    bar_interval_ns: int
    bars_per_trading_day: int
    bar_type_suffix: str            # Nautilus-BarSpecification-Teil, z. B. "1-HOUR"
    catalog_interval: str           # Unterverzeichnis <symbol>/<interval>/ im Katalog
    resample_rule: str              # pandas-Resample-Regel der Bar-Qualitäts-Stichprobe
    trading_day_bars: bool = False  # Bar ≥ Session: der HANDELSTAG entscheidet über die Zugehörigkeit (kein Punkt-Test)

    @property
    def bars_per_year(self) -> float:
        return 252.0 * self.bars_per_trading_day

    def bar_type(self, instrument_id: str) -> str:
        return f"{instrument_id}-{self.bar_type_suffix}-MID-INTERNAL"


# DIE Achsen-Tabelle (OneHour: 7 RTH-Bars je Handelstag, OneDay: 1).
AXES: dict[str, BarAxis] = {
    "OneHour": BarAxis("OneHour", 3_600_000_000_000, 7, "1-HOUR", "OneHour", "1h"),
    "OneDay": BarAxis("OneDay", 86_400_000_000_000, 1, "1-DAY", "OneDay", "1D", trading_day_bars=True),
}


class BarAxisConfigError(ValueError):
    """Unbekannte ``bar_axis`` in ``backtest.json``."""


def get_axis(name: str | None = None) -> BarAxis:
    key = name or DEFAULT_AXIS
    if key not in AXES:
        raise BarAxisConfigError(f"bar_axis={key!r} unbekannt; erlaubt: {sorted(AXES)}.")
    return AXES[key]


def _backtest_cfg(cfg_dir: Path | None = None) -> dict:
    base = Path(cfg_dir) if cfg_dir is not None else Path(
        os.environ.get("ETORO_CONFIG_DIR") or Path(__file__).resolve().parent / "config")
    try:
        return json.loads((base / "backtest.json").read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}


def active_axis_name(cfg_dir: Path | None = None) -> str:
    """``backtest.json["bar_axis"]`` (fehlender Key ⇒ ``OneHour``)."""
    return get_axis(_backtest_cfg(cfg_dir).get("bar_axis")).name


def active_axis(cfg_dir: Path | None = None) -> BarAxis:
    return get_axis(active_axis_name(cfg_dir))


def active_max_handelstage(cfg_dir: Path | None = None) -> float:
    """``backtest.json["max_handelstage"]`` (Steuergrösse der Zeitbox; Default 1,0)."""
    value = _backtest_cfg(cfg_dir).get("max_handelstage", MAX_HANDELSTAGE_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise BarAxisConfigError(f"max_handelstage={value!r} ist keine positive Zahl.")
    return float(value)


def bar_type(instrument_id: str, axis: str | None = None) -> str:
    """Bar-Typ-String ``<id>-<suffix>-MID-INTERNAL`` der (aktiven) Achse."""
    return get_axis(axis or active_axis_name()).bar_type(instrument_id)


def interval_ns_for(catalog_interval: str) -> int | None:
    """Intervall in ns zu einem Katalog-Intervall-Namen (``None`` für unbekannte Namen)."""
    for axis in AXES.values():
        if axis.catalog_interval == catalog_interval:
            return axis.bar_interval_ns
    return None


def study_suffix(axis: str | None = None) -> str:
    """Suffix der Study-Identität (Issue #1382 Fix Punkt 6): leer für die Default-Achse (bit-identische Namen),
    ``_<Achse>`` sonst — Tages- und Stunden-Studies kollidieren dadurch nie (gleicher Katalog, anderer Name)."""
    name = get_axis(axis or active_axis_name()).name
    return "" if name == DEFAULT_AXIS else f"_{name}"


def fingerprint_component(axis: str | None = None) -> str | None:
    """Zusatzkomponente für ``run_fingerprint``/``result_fingerprint``: ``None`` für die Default-Achse (der Hash
    bleibt bit-identisch), sonst ``bar_axis=<Achse>``."""
    name = get_axis(axis or active_axis_name()).name
    return None if name == DEFAULT_AXIS else f"bar_axis={name}"


HOURLY_INTERVAL_NS = AXES["OneHour"].bar_interval_ns
DAILY_INTERVAL_NS = AXES["OneDay"].bar_interval_ns
