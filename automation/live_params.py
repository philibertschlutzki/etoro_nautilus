"""Issue #1360 (GH #1256, P0) — der Live-Bot handelt die validierten Parameter.

Vor diesem Modul entschied ``deployment_gate.evaluate_deployment_eligibility`` über das Proposal
(``proposal_<strategy>_<symbol>.json``, validierte Parameter in ``proposed_instrument_override``),
der Bot (``momentum_ls_run._build_bots_config``) baute seine Parameter dagegen aus
``strategy_defaults.json`` + ``strategies.json.params`` + ``instrument_overrides[symbol]`` und
übernahm vom Gewinner nur den Strategienamen. Kein Code prüfte, dass das Proposal (Übernahme per PR)
in ``instrument_overrides`` angekommen war: ein zugelassenes Paar handelte live mit Default-Parametern,
solange der PR fehlte — oder mit einem älteren Override nach einer erneuten Promotion.

``resolve_live_params`` ist die EINZIGE Quelle der Live-Parameter für Bot UND Gate (Pitfall #487:
der validierte Kandidat muss der gehandelte sein). Bewusst rein und ohne ``nautilus_trader``-Import
(importierbar aus ``optimizer/deployment_gate.py`` und ``momentum_ls_run.py``).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


def resolve_live_params(
    strategy: str,
    symbol: str,
    defaults: Mapping[str, Mapping[str, Any]],
    strategies_raw: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Die Live-Parameter von ``strategy`` auf ``symbol``. Präzedenz (A4.8):
    ``strategy_defaults.json`` < ``strategies.json['params']`` < ``instrument_overrides[symbol]``.
    ``trade_amount_usd`` entfällt (die Position bemisst der ``MomentumLSAllocator``).

    Reine Funktion; gibt immer eine NEUE Dict-Kopie zurück. Eine Strategie ohne ``strategies.json``-
    Eintrag trägt nur ihre Defaults."""
    strat_defaults = dict((defaults or {}).get(strategy) or {})
    strat_params: dict[str, Any] = {}
    instrument_overrides: Mapping[str, Any] = {}
    for entry in strategies_raw or []:
        if entry.get("strategy_class") == strategy:
            strat_params = dict(entry.get("params") or {})
            instrument_overrides = entry.get("instrument_overrides") or {}
            break
    merged = {**strat_defaults, **strat_params}
    symbol_override = instrument_overrides.get(symbol)
    if symbol_override:
        merged = {**merged, **symbol_override}
    merged.pop("trade_amount_usd", None)
    return merged


def live_params_sha256(params: Mapping[str, Any]) -> str:
    """SHA-256 über das sortierte, kompakte JSON der Live-Parameter (Whitelist-Eintrag
    ``live_params_sha256``; der Bot loggt den Wert je Strategie-Instanz)."""
    blob = json.dumps(dict(params), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def live_param_values_match(live_value: Any, proposed_value: Any) -> bool:
    """``int``/``bool``/``str`` (und alles Nicht-Float) exakt, ``float`` per
    ``math.isclose(rel_tol=1e-9)``. Ein ``bool`` ist nie gleich einem ``int``/``float`` (``True`` ≠ 1)."""
    if isinstance(live_value, bool) or isinstance(proposed_value, bool):
        return live_value is proposed_value
    if isinstance(live_value, float) or isinstance(proposed_value, float):
        try:
            return math.isclose(float(live_value), float(proposed_value), rel_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return live_value == proposed_value


def mismatching_live_params(
    live_params: Mapping[str, Any], proposed_override: Mapping[str, Any] | None,
) -> list[str] | None:
    """Die Keys von ``proposed_override``, deren Live-Wert vom promovierten abweicht (sortiert; ein im
    Live-Satz FEHLENDER Key zählt als Abweichung). ``None`` ⇔ das Proposal-Feld fehlt (kein Dict) ⇒
    nicht prüfbar, FAIL-CLOSED beim Aufrufer (``None`` ist keine bestandene Prüfung)."""
    if not isinstance(proposed_override, Mapping):
        return None
    bad: list[str] = []
    for key, proposed in proposed_override.items():
        if key not in live_params or not live_param_values_match(live_params[key], proposed):
            bad.append(str(key))
    return sorted(bad)


def load_live_param_sources(config_dir: Path) -> tuple[dict, list]:
    """``(strategy_defaults, strategies_raw)`` aus ``config_dir`` — dieselben Dateien, die der Bot
    liest. ``strategy_defaults`` ohne die ``_``-Metablöcke."""
    config_dir = Path(config_dir)
    with open(config_dir / "strategy_defaults.json", "r", encoding="utf-8") as f:
        defaults = {k: v for k, v in (json.load(f) or {}).items() if not k.startswith("_")}
    with open(config_dir / "strategies.json", "r", encoding="utf-8") as f:
        strategies_raw = (json.load(f) or {}).get("strategies", [])
    return defaults, strategies_raw


def resolve_live_atr_floor_bps(symbol: str, params: Mapping[str, Any], *, min_stop_to_cost_ratio: float = 3.0) -> float | None:
    """E1 — kostengekoppelter ATR-Floor (bps) für den Live-Bot, paritätisch zum Backtest.

    Der Backtest (``backtest_runner``, #924/#1096) übergibt der Strategie
    ``cost_coupled_atr_floor_bps(resolve_atr_floor_bps(Asset-Class), atr_trailing_multiplier,
    Round-Trip-Kosten)``. Der Live-Bot setzte ``atr_floor_bps`` nie und fiel auf den Klassen-Default
    2.0 bps zurück: bei engen Bars wird die Stop-Distanz kleiner als die Kosten (Noise-Stop-Outs).
    ``None`` bei jedem Auflösungsfehler (fail-open ⇒ Strategie-Default bleibt, bisheriges Verhalten)."""
    try:
        from automation import backtest_runner as br
        cfg_path = br.config_dir() / "backtest.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            by_class = json.load(f).get("atr_floor_bps_by_asset_class")
        asset_class = br._resolve_asset_class_for_symbol(symbol, policy="default")
        if asset_class == "UNKNOWN":
            asset_class = "DEFAULT"
        base = br.resolve_atr_floor_bps(symbol, by_class, asset_class)
        return br.cost_coupled_atr_floor_bps(
            base, atr_trailing_multiplier=params.get("atr_trailing_multiplier"),
            round_trip_cost_bps=br._read_default_round_trip_cost_bps(symbol),
            min_stop_to_cost_ratio=min_stop_to_cost_ratio)
    except Exception:
        return None
