"""Issue #1359 (GH #1255, P0) — Katastrophen-Stop: jede Entry-Order trägt einen Broker-Stop.

Vor diesem Modul eröffneten 13 von 14 aktiven Strategien live mit ``IsNoStopLoss: True``; der
ATR-Trailing-Stop ist ein Bar-Schluss-Signal im Bot-Prozess und existiert bei Absturz, Websocket-
Verlust, Maschinen-Neustart oder eToro-Störung nicht. Der Katastrophen-Stop ist ein WEITER, am
Broker hinterlegter Stop (``SL:<pct>``-Tag der Entry-Order → ``StopLossRate`` im eToro-Payload), der
die Position gegen genau diese Ausfälle schützt und im Normalbetrieb NIE binden soll (Invariante
``check_disaster_stop_non_binding``: Anteil ``DISASTER_STOP``-Exits ≤ 1 %).

Dieses Modul ist die EINZIGE Quelle der Stop-Arithmetik (rein, ohne ``nautilus_trader``-Import):

    pct = clamp(k_disaster · stop_distance_at_entry_bps / 10⁴, disaster_stop_min_pct, disaster_stop_max_pct)

``stop_distance_at_entry_bps`` = die effektive Trailing-Distanz beim Entry
(``atr_trailing_multiplier · max(ATR, atr_floor)`` in bps des Preises, siehe
``HourlyStrategyBase._entry_order_tags``). Die Parameter stehen in ``strategy_defaults.json`` unter
``_disaster_stop`` (NICHT im Optimizer-Suchraum — sie sind Sicherheitsparameter, keine Strategie-
Parameter); dieses Modul liest den Block beim Import fail-loud (kein stilles Fehlen eines Stops).
"""
from __future__ import annotations

import json
from pathlib import Path

#: ``broker_attribute`` (Live): nur der ``SL:``-Tag, der Broker hält den Stop — keine separate Order.
DISASTER_STOP_MODE_BROKER = "broker_attribute"
#: ``simulated_order`` (Backtest): beim Positions-Fill eine ``stop_market(reduce_only=True)``-Order
#: auf demselben Niveau, Storno beim Positions-Close, Exit-Tag ``EXIT_REASON:DISASTER_STOP``.
DISASTER_STOP_MODE_SIMULATED = "simulated_order"
DISASTER_STOP_MODES = (DISASTER_STOP_MODE_BROKER, DISASTER_STOP_MODE_SIMULATED)

DISASTER_STOP_EXIT_REASON = "DISASTER_STOP"
SL_TAG_PREFIX = "SL:"

_CONFIG_KEY = "_disaster_stop"
_REQUIRED_KEYS = ("k_disaster", "disaster_stop_min_pct", "disaster_stop_max_pct")
_DEFAULTS_FILE = Path(__file__).resolve().parent / "config" / "strategy_defaults.json"


def parse_disaster_stop_params(block: object) -> dict[str, float]:
    """Validiert den ``_disaster_stop``-Block. Wirft ``ValueError`` bei fehlendem Key oder
    unplausiblen Werten (``0 < min <= max < 1``, ``k > 0``) — ein Stop, der still fehlt oder
    degeneriert (0 % / 100 %), ist schlimmer als ein lauter Abbruch."""
    if not isinstance(block, dict):
        raise ValueError(f"strategy_defaults.json['{_CONFIG_KEY}'] fehlt oder ist kein Objekt.")
    missing = [k for k in _REQUIRED_KEYS if k not in block]
    if missing:
        raise ValueError(f"strategy_defaults.json['{_CONFIG_KEY}'] ohne Key(s): {missing}.")
    params = {k: float(block[k]) for k in _REQUIRED_KEYS}
    if not (params["k_disaster"] > 0 and 0 < params["disaster_stop_min_pct"]
            <= params["disaster_stop_max_pct"] < 1.0):
        raise ValueError(
            f"Unplausible Katastrophen-Stop-Parameter {params}: erwartet k_disaster > 0 und "
            f"0 < disaster_stop_min_pct <= disaster_stop_max_pct < 1."
        )
    return params


def load_disaster_stop_params(defaults_path: Path | None = None) -> dict[str, float]:
    """Liest ``strategy_defaults.json['_disaster_stop']`` (``defaults_path`` oder die Repo-Datei)."""
    path = Path(defaults_path) if defaults_path is not None else _DEFAULTS_FILE
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f) or {}
    return parse_disaster_stop_params(data.get(_CONFIG_KEY))


def resolve_disaster_stop_params(config_dir: Path | None = None) -> dict[str, float]:
    """Die für einen Lauf gültigen Parameter: ``<config_dir>/strategy_defaults.json['_disaster_stop']``
    (Backtest-Worker UND Live-Bot lesen über dieselbe Funktion — kein zweiter Pfad), fehlt die Datei
    oder der Block im übergebenen ``config_dir``, die Repo-Defaults. Eine vorhandene, aber
    ungültige Datei wirft (fail-loud)."""
    if config_dir is not None:
        path = Path(config_dir) / "strategy_defaults.json"
        if path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                block = (json.load(f) or {}).get(_CONFIG_KEY)
            if block is not None:
                return parse_disaster_stop_params(block)
    return dict(_DEFAULTS)


_DEFAULTS = load_disaster_stop_params()
K_DISASTER_DEFAULT: float = _DEFAULTS["k_disaster"]
DISASTER_STOP_MIN_PCT_DEFAULT: float = _DEFAULTS["disaster_stop_min_pct"]
DISASTER_STOP_MAX_PCT_DEFAULT: float = _DEFAULTS["disaster_stop_max_pct"]


def compute_disaster_stop_pct(
    stop_distance_at_entry_bps: float,
    *,
    k_disaster: float = K_DISASTER_DEFAULT,
    disaster_stop_min_pct: float = DISASTER_STOP_MIN_PCT_DEFAULT,
    disaster_stop_max_pct: float = DISASTER_STOP_MAX_PCT_DEFAULT,
) -> float:
    """``clamp(k · distance_bps / 1e4, min, max)`` als Anteil (0.10 = 10 %). Eine nicht endliche oder
    negative Distanz (degenerierter Preis/ATR — im Normalbetrieb durch den ATR-Floor ausgeschlossen)
    fällt auf die OBERE Schranke zurück: der Katastrophen-Stop bleibt gesetzt, verzerrt aber die
    Strategie nicht durch einen zu engen Wert."""
    try:
        raw = float(k_disaster) * float(stop_distance_at_entry_bps) / 10_000.0
    except (TypeError, ValueError):
        raw = float("nan")
    if raw != raw or raw in (float("inf"), float("-inf")) or raw < 0:
        return float(disaster_stop_max_pct)
    return min(max(raw, float(disaster_stop_min_pct)), float(disaster_stop_max_pct))


def format_sl_tag(pct: float) -> str:
    """``SL:<pct>`` im Format, das ``etoro_execution._build_market_open_payload`` liest (Anteil)."""
    return f"{SL_TAG_PREFIX}{pct:.4f}"


def parse_sl_pct_from_tags(tags) -> float | None:
    """Der erste ``SL:<pct>``-Tag mit ``pct > 0`` als float, sonst ``None`` (robust gegen fremde
    Tag-Typen, z. B. Test-Doubles)."""
    if not isinstance(tags, (list, tuple, set, frozenset)):
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(SL_TAG_PREFIX):
            try:
                pct = float(tag[len(SL_TAG_PREFIX):])
            except ValueError:
                continue
            if pct > 0:
                return pct
    return None


def disaster_stop_level(entry_price: float, *, is_long: bool, pct: float) -> float:
    """Stop-Niveau: LONG ``entry · (1 − pct)``, SHORT ``entry · (1 + pct)`` — dieselbe Formel wie der
    eToro-Adapter (``StopLossRate``), damit Backtest und Live auf demselben Niveau stoppen."""
    return float(entry_price) * (1.0 - pct if is_long else 1.0 + pct)
