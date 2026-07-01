#!/usr/bin/env python3
"""
run_backtest.py

NautilusTrader Multi-Strategie Backtesting Engine mit Tournament-Modus.

Optimierungen & Fixes:
  - OmsType.NETTING (Klassischer Netting-Modus für korrekte Order-Exekution)
  - extract_metrics: Behebt den AttributeError (Cache hat kein .fills()). Verwendet
    jetzt robust den offiziellen trader.generate_fills_report() DataFrame.
  - Dynamisches Upscaling von 'trade_amount_usd': Verhindert das lautlose Verwerfen
    von Signalen bei US-Aktien (Bug 1: units < size_increment).
  - Zeitfilter: pd.Timestamp → Nanosekunden (Katalog-Kompatibilität)
  - normalize_parquet_metadata() vor Backtesting (Arrow-Schema-Konflikte)
  - Robuste Fehlerbehandlung bei KeyboardInterrupt im Multiprocessing.
"""

import os
import sys
import os
from pathlib import Path
_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))
import inspect
import json
import math
import argparse
import importlib
import contextlib
import io
import multiprocessing
import atexit
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from automation.utils import _fallback_precisions
import importlib
from dotenv import load_dotenv

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
_THIS_DIR = Path(__file__).resolve().parent
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
load_dotenv(str(ENV_FILE))

def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:
    try:
        # Check if parquet_path is a directory and instrument_id is provided
        if instrument_id and (Path(parquet_path) / "data").exists():
            path = Path(parquet_path) / "data" / "quote_tick" / instrument_id
            # Get first parquet file
            parquet_files = list(path.glob("*.parquet"))
            if not parquet_files:
                raise FileNotFoundError()
            target_path = parquet_files[0]
        else:
            target_path = Path(parquet_path)

        schema = pq.read_schema(str(target_path))
        meta = schema.metadata or {}
        if b"price_precision" in meta and b"size_precision" in meta:
            price_prec = int(meta.get(b"price_precision", b"2"))
            size_prec  = int(meta.get(b"size_precision",  b"0"))
            return price_prec, size_prec
        else:
            raise ValueError("Metadata not found")
    except Exception:
        # Fallback via Instrumentname aus Pfad
        symbol = instrument_id if instrument_id else Path(parquet_path).parent.name
        return _fallback_precisions(symbol)



from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool as _BrokenPool
from collections import deque

import pandas as pd
import pyarrow.parquet as pq
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue, InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType, AssetClass
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import Cfd
try:
    from nautilus_trader.analysis.tearsheet import create_tearsheet
    _HAS_TEARSHEET = True
except ImportError:
    _HAS_TEARSHEET = False
    def create_tearsheet(*args, **kwargs):
        raise ImportError("nautilus_trader.analysis.tearsheet not available in this version.")

# ─── Precision-Heuristik aus automation.utils (kein adapters/-Import) ────────
try:
    from automation.utils import _fallback_precisions
except ImportError:
    # Fallback wenn PROJECT_ROOT noch nicht im sys.path
    _project_root_for_import = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if _project_root_for_import not in sys.path:
        sys.path.insert(0, _project_root_for_import)
    from automation.utils import _fallback_precisions

# ---------------------------------------------------------------------------
# Globale Housekeeping
# ---------------------------------------------------------------------------

_worker_log_files: list[str] = []


def _cleanup_worker_logs() -> None:
    for path in _worker_log_files:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


atexit.register(_cleanup_worker_logs)


# ---------------------------------------------------------------------------
# DualLogger
# ---------------------------------------------------------------------------

class DualLogger:
    def __init__(self, filepath: str) -> None:
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.log.write(message)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()


# ---------------------------------------------------------------------------
# Parquet-Metadaten-Normalisierung
# ---------------------------------------------------------------------------

def normalize_parquet_metadata(catalog_path: str, instrument_id_str: str) -> bool:
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / instrument_id_str
    if not inst_dir.exists():
        return False

    parquet_files = sorted(inst_dir.rglob("*.parquet"))
    if len(parquet_files) <= 1:
        return False

    file_metas: list[tuple[Path, dict]] = []
    for f in parquet_files:
        try:
            schema = pq.read_schema(str(f))
            file_metas.append((f, schema.metadata or {}))
        except Exception:
            pass

    if len(file_metas) <= 1:
        return False

    def get_precision(meta: dict) -> str | None:
        for key in (b'price_precision', 'price_precision'):
            val = meta.get(key)
            if val is not None:
                return val.decode() if isinstance(val, bytes) else str(val)
        return None

    precisions = {get_precision(m) for _, m in file_metas}
    precisions.discard(None)
    all_metas = [m for _, m in file_metas]

    if len(precisions) <= 1 and all(m == all_metas[0] for m in all_metas):
        return False

    ref_meta = file_metas[-1][1]
    ref_precision = get_precision(ref_meta) or '?'
    print(f"  🔧 [{instrument_id_str}] Konflikt {precisions} → precision={ref_precision}")

    patched = 0
    for f, meta in file_metas:
        if meta == ref_meta:
            continue
        try:
            table = pq.read_table(str(f))
            pq.write_table(table.replace_schema_metadata(ref_meta), str(f), compression="snappy")
            patched += 1
        except Exception as e:
            print(f"  ⚠️  [{instrument_id_str}] Patch-Fehler {f.name}: {e}")

    if patched:
        print(f"  ✅ [{instrument_id_str}] {patched}/{len(file_metas)} Dateien gepatcht")
    return patched > 0


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def load_config(filepath: str) -> dict[str, Any]:
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            full = os.path.join(tick_dir, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                clean = entry.replace("instrument_id=", "")
                if clean:
                    instruments.append(clean)
    return sorted(instruments)


def validate_strategy_params(strat: dict) -> list[str]:
    warnings: list[str] = []
    params = strat.get("params", {})
    name = strat.get("strategy_class", "?")

    macd_fast = params.get("macd_fast")
    macd_slow = params.get("macd_slow")
    if macd_fast is not None and macd_slow is not None and macd_fast >= macd_slow:
        warnings.append(
            f"{name}: macd_fast({macd_fast}) >= macd_slow({macd_slow}) — ungültig"
        )
    for key in ("bb_std_dev", "keltner_multiplier", "atr_multiplier", "volume_multiplier"):
        val = params.get(key)
        if val is not None and val <= 0:
            warnings.append(f"{name}: {key}={val} muss > 0 sein")
    return warnings


def ts_to_ns(ts: pd.Timestamp | None) -> int | None:
    return int(ts.value) if ts is not None else None


# ---------------------------------------------------------------------------
# Precision-Hilfsfunktionen (Task 3: aus Parquet-Metadaten lesen)
# ---------------------------------------------------------------------------

def _get_project_root() -> str:
    """Gibt das Projekt-Root-Verzeichnis zurück (parent von backtesting/)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))



def config_dir() -> Path:
    return Path(os.environ.get("ETORO_CONFIG_DIR", str(Path(_get_project_root()) / "automation" / "config")))

def logs_dir() -> Path:
    return Path(os.environ.get("ETORO_LOGS_DIR", str(Path(_get_project_root()) / "logs")))

def load_strategy_defaults(project_root: str | None = None) -> dict:
    """Lädt strategy_defaults.json aus automation/config/.

    Args:
        project_root: Projekt-Root-Pfad. Wenn None, wird auto-detektiert.

    Returns:
        Dict {ClassName: {param: default_value, ...}}
    """
    root = project_root or _get_project_root()
    defaults_path = str(config_dir() / "strategy_defaults.json")
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # _schema-Schlüssel entfernen
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as e:
            print(f"  ⚠️  strategy_defaults.json Ladefehler: {e} — nutze leere Defaults.")
    return {}



def resolve_strategy_params(strategy_entry: dict, defaults: dict, *, is_manifest: bool,
                            instrument: str | None = None) -> dict:
    """is_manifest=True  ⇒ params verbatim (KEIN Defaults-Merge, KEIN Override) — Pitfall #61 bleibt strikt.
       is_manifest=False ⇒ {**defaults, **params, **instrument_overrides.get(instrument, {})}
                           wenn instrument != None, sonst Legacy {**defaults, **params} (unverändert, A4.1)."""
    params = dict(strategy_entry.get("params") or {})
    if is_manifest:
        return params
    merged = {**defaults, **params}
    if instrument is not None:
        overrides = strategy_entry.get("instrument_overrides") or {}
        merged.update(overrides.get(instrument) or {})
    return merged

def restrict_universe(universe: list[str], instruments: list[str] | None) -> list[str]:
    """A4.2: manifest-getriebene Universum-Restriktion (`global_settings.instruments`).

    instruments falsy (None/[]) ⇒ `universe` unverändert (Reihenfolge erhalten).
    sonst ⇒ Schnittmenge unter Beibehaltung der `universe`-Reihenfolge. Unbekannte Symbole
    (nicht im Katalog) werden still gedroppt — der Backtest crasht NICHT sofort.
    """
    if not instruments:
        return universe
    allowed = set(instruments)
    return [s for s in universe if s in allowed]


def apply_strategy_defaults(strategies: list[dict], defaults: dict, is_manifest: bool = False) -> list[dict]:
    """Merged strategy_defaults.json mit Strategie-Params aus der Config.

    Merge-Reihenfolge (niedrig → hoch Priorität):
      1. strategy_defaults.json (Basis)
      2. strategy.params aus der übergebenen Config (Override)

    Args:
        strategies: Liste der Strategie-Dicts (aus dem generierten Config-JSON)
        defaults:   Dict aus strategy_defaults.json

    Returns:
        Neue Liste mit gemergten params-Dicts.
    """
    result = []
    for strat in strategies:
        class_name    = strat.get("strategy_class", "")
        class_defaults = defaults.get(class_name, {})
        merged_params  = resolve_strategy_params(strat, class_defaults, is_manifest=is_manifest)
        result.append({**strat, "params": merged_params})
    return result


# ---------------------------------------------------------------------------
# Tournament-Config-Loader und Scoring (Task 5)
# ---------------------------------------------------------------------------

def load_tournament_config(project_root: str | None = None) -> dict:
    """Lädt tournament.json aus automation/config/.

    Returns:
        Tournament-Konfigurations-Dict.
    """
    root = project_root or _get_project_root()
    cfg_path = str(config_dir() / "tournament.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = {k: v for k, v in data.items() if not k.startswith("_")}
            # Startup-Validierung
            req_all = set(cfg.get("eligible_requires_all", []))
            req_any = set(cfg.get("eligible_requires_any", []))
            used = req_all | req_any
            metric_keys = {k for k in cfg.keys() if k not in ("eligible_requires_all", "eligible_requires_any", "scoring", "sortino_min_trades")}
            for k in metric_keys:
                base_k = k[4:] if k.startswith("oos_") else k
                if base_k not in used:
                    print(f"  ⚠️  Tournament-Kriterium '{k}' ist definiert, aber nicht in eligible_requires_all/any referenziert!")
            normalized_metric_keys = metric_keys | {k[4:] for k in metric_keys if k.startswith("oos_")}
            for u in used:
                if u not in normalized_metric_keys:
                    print(f"  ⚠️  Referenziertes Kriterium '{u}' in eligible_requires_all/any ist nicht definiert!")
            return cfg
        except Exception as e:
            print(f"  ⚠️  tournament.json Ladefehler: {e} — nutze Legacy-Defaults.")
    # Legacy-Defaults (Rückwärts-Kompatibilität)
    return {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.5,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": 0.0,
        "eligible_requires_all": ["min_trades", "max_drawdown"],
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1,
        },
    }


def compute_tournament_score(metrics: dict, scoring: dict) -> float:
    sortino = metrics.get("sortino_ratio")
    pf = metrics.get("profit_factor")
    return (
        (sortino if sortino is not None else 0.0) * scoring.get("sortino_weight", 0.4)
        + (pf if pf is not None else 0.0) * scoring.get("profit_factor_weight", 0.3)
        + metrics.get("win_rate", 0.0)      * scoring.get("win_rate_weight", 0.2)
        - metrics.get("max_drawdown", 0.0)  * scoring.get("drawdown_penalty_weight", 0.1)
    )



def _evaluate_oos_eligibility(oos_metrics: dict | None, tournament_cfg: dict, strat_params: dict = None) -> dict:
    """Wertet OOS-Metriken strukturiert aus und liefert die 4 OOS-Pflicht-Keys."""
    n_trades = oos_metrics.get("total_trades", 0) if oos_metrics else 0
    if n_trades <= 0:
        return {
            "oos_evaluated": False,
            "oos_eligible": False,
            "oos_metrics": None,
            "oos_rejection_reasons": ["oos_not_evaluable: Kein oder zu wenig OOS-Datenmaterial (total_trades <= 0)."]
        }


    max_dd       = oos_metrics.get("max_drawdown", 1.0)
    win_rate     = oos_metrics.get("win_rate", 0.0)
    total_return = oos_metrics.get("total_return", 0.0)
    expectancy   = total_return / n_trades if n_trades > 0 else 0.0

    sortino = oos_metrics.get("sortino_ratio")
    pf = oos_metrics.get("profit_factor")

    t_overrides = strat_params.get("tournament_overrides", {}) if strat_params else {}

    req_trades   = t_overrides.get("oos_min_trades", t_overrides.get("min_trades", tournament_cfg.get("oos_min_trades", tournament_cfg.get("min_trades", 0))))
    req_return   = t_overrides.get("oos_min_total_return", t_overrides.get("min_total_return", tournament_cfg.get("oos_min_total_return", tournament_cfg.get("min_total_return", 0.0))))
    req_exp      = t_overrides.get("oos_min_expectancy", t_overrides.get("min_expectancy", tournament_cfg.get("oos_min_expectancy", tournament_cfg.get("min_expectancy", 0.0))))
    req_sortino  = t_overrides.get("oos_min_sortino", t_overrides.get("min_sortino", tournament_cfg.get("oos_min_sortino", tournament_cfg.get("min_sortino", 0.0))))
    req_pf       = t_overrides.get("oos_min_profit_factor", t_overrides.get("min_profit_factor", tournament_cfg.get("oos_min_profit_factor", tournament_cfg.get("min_profit_factor", 1.0))))
    req_max_dd   = t_overrides.get("oos_max_drawdown", t_overrides.get("max_drawdown", tournament_cfg.get("oos_max_drawdown", tournament_cfg.get("max_drawdown", 1.0))))
    req_win_rate = t_overrides.get("oos_min_win_rate", t_overrides.get("min_win_rate", tournament_cfg.get("oos_min_win_rate", tournament_cfg.get("min_win_rate", 0.0))))

    sortino_valid = True
    sortino_reason = ""
    if req_sortino > 0.0:
        if sortino is None:
             if n_trades < req_trades or win_rate <= 0.0:
                 sortino_valid = False
                 sortino_reason = f"oos_min_sortino: None (all-win/insufficient) < {req_sortino}"
        elif sortino < req_sortino:
             sortino_valid = False
             sortino_reason = f"oos_min_sortino: {sortino:.5f} < {req_sortino}"

    pf_valid = True
    pf_reason = ""
    if req_pf > 0.0:
        if pf is None:
             if n_trades < req_trades or win_rate <= 0.0:
                 pf_valid = False
                 pf_reason = f"oos_min_profit_factor: None (all-win/insufficient) < {req_pf}"
        elif pf < req_pf:
             pf_valid = False
             pf_reason = f"oos_min_profit_factor: {pf:.5f} < {req_pf}"

    condition_map = {
        "min_trades":        (n_trades >= req_trades, f"oos_min_trades: {n_trades} < {req_trades}"),
        "min_total_return":  (total_return >= req_return, f"oos_min_total_return: {total_return:.5f} < {req_return:.5f}"),
        "min_expectancy":    (expectancy >= req_exp, f"oos_min_expectancy: {expectancy:.5f} < {req_exp:.5f}"),
        "max_drawdown":      (max_dd <= req_max_dd, f"oos_max_drawdown: {max_dd:.5f} > {req_max_dd:.5f}"),
        "min_win_rate":      (win_rate >= req_win_rate, f"oos_min_win_rate: {win_rate:.5f} < {req_win_rate:.5f}"),
        "min_sortino":       (sortino_valid, sortino_reason),
        "min_profit_factor": (pf_valid, pf_reason),
    }

    reasons = []
    for cond_name in tournament_cfg.get("eligible_requires_all", []):
        if cond_name in condition_map:
            valid, reason = condition_map[cond_name]
            if not valid:
                reasons.append(reason)

    any_conditions = tournament_cfg.get("eligible_requires_any", [])
    if any_conditions:
        any_valid = False
        any_reasons = []
        for cond_name in any_conditions:
            if cond_name in condition_map:
                valid, reason = condition_map[cond_name]
                if valid:
                    any_valid = True
                    break
                else:
                    if reason:
                        any_reasons.append(reason)
                    else:
                        any_reasons.append(f"{cond_name} failed")
        if not any_valid:
            reasons.append("Requires ANY of " + str(any_conditions) + " failed: " + ", ".join(any_reasons))

    if n_trades > 0:
        median_notional = oos_metrics.get("median_position_notional", 0.0)
        if median_notional < 10.0:
            reasons.append(f"Micro-Sizing: Median notional < 10.0 (value: {median_notional:.4f})")

    return {
        "oos_evaluated": True,
        "oos_eligible": len(reasons) == 0,
        "oos_metrics": oos_metrics,
        "oos_rejection_reasons": reasons
    }

def _is_eligible(result: dict, tournament_cfg: dict, strat_params: dict | None = None, symbol: str = "Unknown", strategy: str = "Unknown", log_rejections: bool = False) -> bool:
    """Prüft ob eine Strategie für das Tournament eligibel ist (In-Sample).

    eligible_requires_all: ALLE Bedingungen müssen erfüllt sein.
    eligible_requires_any: MINDESTENS EINE Bedingung muss erfüllt sein.
    """
    metrics = result.get("metrics", {})
    if not metrics:
        return False
    n_trades = metrics.get("total_trades", 0)
    if n_trades <= 0:
        if log_rejections:
            reason = "no trades executed"
            print(f"⚠️  Rejected IS: {symbol} - {strategy} | Reasons: {reason}")
            metrics["rejection_reason"] = reason
        return False


    sortino = metrics.get("sortino_ratio")
    pf = metrics.get("profit_factor")

    if sortino is None or pf is None:
        if log_rejections:
            n = metrics.get("total_trades", 0)
            losses_count = metrics.get("losses_count", 0)
            win_rate = metrics.get("win_rate", 0.0)

            if win_rate == 0.0:
                reason = "all-loss"
            elif losses_count == 0:
                reason = "all-win (no losses)"
            elif n < 5:
                reason = f"insufficient sample (n={n})"
            elif losses_count < 2 and n < 50:
                reason = f"insufficient loss data (losses={losses_count} for n={n})"
            else:
                reason = "undefined metrics"

            print(f"⚠️  Rejected: {reason} for {symbol} - {strategy}")
            metrics["rejection_reason"] = reason
        return False

    max_dd       = metrics.get("max_drawdown", 1.0)
    win_rate     = metrics.get("win_rate", 0.0)
    total_return = metrics.get("total_return", 0.0)
    expectancy   = total_return / n_trades if n_trades > 0 else 0.0

    t_overrides = strat_params.get("tournament_overrides", {}) if strat_params else {}
    # Low-Sample / All-Win Defensive Handling
    req_trades = t_overrides.get("min_trades", tournament_cfg.get("min_trades", 0))
    sortino_valid = True
    if sortino is None:
        if n_trades < req_trades or win_rate <= 0.0:
            sortino_valid = False
    else:
        sortino_valid = sortino >= t_overrides.get("min_sortino", tournament_cfg.get("min_sortino", 0.0))

    pf_valid = True
    if pf is None:
        if n_trades < req_trades or win_rate <= 0.0:
            pf_valid = False
    else:
        pf_valid = pf >= t_overrides.get("min_profit_factor", tournament_cfg.get("min_profit_factor", 1.0))

    condition_map = {
        "min_trades":        n_trades     >= req_trades,
        "min_sortino":       sortino_valid,
        "min_profit_factor": pf_valid,
        "max_drawdown":      max_dd       <= t_overrides.get("max_drawdown", tournament_cfg.get("max_drawdown", 1.0)),
        "min_win_rate":      win_rate     >= t_overrides.get("min_win_rate", tournament_cfg.get("min_win_rate", 0.0)),
        "min_total_return":  total_return >= t_overrides.get("min_total_return", tournament_cfg.get("min_total_return", 0.0)),
        "min_expectancy":    expectancy   >= t_overrides.get("min_expectancy", tournament_cfg.get("min_expectancy", 0.0)),
    }

    rejections = []
    # Harte Filter: ALLE müssen erfüllt sein
    for cond_name in tournament_cfg.get("eligible_requires_all", []):
        if not condition_map.get(cond_name, True):
            reason = f"{cond_name} failed"
            if cond_name == "min_expectancy":
                reason += f" (value: {expectancy:.6f})"
            rejections.append(reason)

    # Weiche Filter: MINDESTENS EINE muss erfüllt sein
    any_conditions = tournament_cfg.get("eligible_requires_any", [])
    if any_conditions:
        if not any(condition_map.get(c, False) for c in any_conditions):
            rejections.append(f"Requires ANY of {any_conditions} failed")

    # Hard Gatekeeper: Median Position Notional
    if n_trades > 0:
        median_notional = metrics.get("median_position_notional", 0.0)
        if median_notional < 10.0:
            rejections.append(f"Micro-Sizing: Median notional < 10.0 (value: {median_notional:.4f})")

    if rejections:
        if log_rejections:
            print(f"⚠️  Rejected IS: {symbol} - {strategy} | Reasons: {', '.join(rejections)}")
        return False

    return True


def load_ticks_from_catalog(
    catalog: ParquetDataCatalog,
    instrument_id_str: str,
    start_ns: int | None,
    end_ns: int | None,
    spread_bps: float = 0.0,
) -> list:
    try:
        ticks = catalog.quote_ticks(
            instrument_ids=[instrument_id_str],
            start=start_ns,
            end=end_ns,
        )
        if not ticks:
            return []

        # Normalisiere Tick Precision (Pitfall #14)
        # Verify the meta data precision from Parquet I/O instead of hardcoding `sp = 8`.
        _, sp_parquet = read_precisions_from_parquet(str(catalog.path), instrument_id_str)
        sp = _normalize_size_precision(sp_parquet, instrument_id_str)

        needs_normalization = hasattr(ticks[0].bid_size, "precision") and ticks[0].bid_size.precision != sp

        if needs_normalization or spread_bps > 0.0:
            from nautilus_trader.model.data import QuoteTick
            from nautilus_trader.model.objects import Quantity, Price

            # Determine price precision from parquet to correctly instantiate Price objects
            pp_parquet, _ = read_precisions_from_parquet(str(catalog.path), instrument_id_str)
            if hasattr(ticks[0].bid_price, "precision"):
                 pp = ticks[0].bid_price.precision
            else:
                 pp = pp_parquet

            normalized = []
            for t in ticks:
                if spread_bps > 0.0:
                    mid_price = (t.bid_price.as_double() + t.ask_price.as_double()) / 2.0
                    half_spread_pct = (spread_bps / 10000.0) / 2.0
                    new_bid_double = mid_price * (1.0 - half_spread_pct)
                    new_ask_double = mid_price * (1.0 + half_spread_pct)

                    new_bid = Price(new_bid_double, precision=pp)
                    new_ask = Price(new_ask_double, precision=pp)
                else:
                    new_bid = t.bid_price
                    new_ask = t.ask_price

                normalized.append(
                    QuoteTick(
                        instrument_id=t.instrument_id,
                        bid_price=new_bid,
                        ask_price=new_ask,
                        bid_size=Quantity(t.bid_size.as_double(), precision=sp) if needs_normalization else t.bid_size,
                        ask_size=Quantity(t.ask_size.as_double(), precision=sp) if needs_normalization else t.ask_size,
                        ts_event=t.ts_event,
                        ts_init=t.ts_init,
                    )
                )
            return normalized
        return ticks
    except Exception as e:
        raise RuntimeError(f"catalog.quote_ticks() fehlgeschlagen: {e}") from e


def infer_precision_from_ticks(ticks: list) -> int:
    if not ticks:
        return 2
    precisions = [
        t.bid_price.precision
        for t in ticks[:20]
        if hasattr(t.bid_price, 'precision')
    ]
    return int(max(precisions)) if precisions else 2


def _normalize_size_precision(sp: int | None, instrument_id_str: str) -> int:
    from automation.utils import _fallback_precisions, _CRYPTO_SYMBOLS, _FRACTIONAL_SYMBOLS
    _, fallback_sp = _fallback_precisions(instrument_id_str)

    sym = instrument_id_str.split(".")[0]
    if sym in _CRYPTO_SYMBOLS or "SHIB" in sym or "PEPE" in sym or sym in _FRACTIONAL_SYMBOLS:
        return fallback_sp

    if sp is not None and sp > 0:
        return sp
    return fallback_sp

def create_mock_instrument(
    instrument_id_str: str,
    price_precision: int = 2,
    size_precision: int | None = None,
    catalog_path: str | None = None,
) -> Cfd:
    """Erstellt ein Mock-CFD-Instrument für den Backtest-Engine.

    Uses Cfd(asset_class=EQUITY) instead of Equity because NautilusTrader
    Equity.size_increment is always 1.0 regardless of lot_size, causing
    make_qty() to reject fractional units < 1.0. Cfd accepts size_precision
    and size_increment directly, enabling fractional trading simulation.

    Args:
        instrument_id_str: Nautilus-Instrument-ID (z.B. "ETH.ETORO")
        price_precision:   Preis-Precision (Standard: 2)
        size_precision:    0/None -> fallback 8, sonst beibehalten.
        catalog_path:      Ignoriert — kein Parquet-Lookup mehr nötig.

    Returns:
        Cfd-Instrument mit korrigierter size_precision (fractional, eToro by-amount semantics).
    """
    inst_id = InstrumentId.from_str(instrument_id_str)
    price_increment_val = round(10 ** (-price_precision), price_precision)

    # size_precision=0 stems from Parquet metadata that is incorrect for eToro
    # equity-CFDs (by-amount/fractional semantics). Treat 0 (and None) as "use
    # fractional default". Positive values (e.g. 2/6 for forex/crypto) are honored.
    sp = _normalize_size_precision(size_precision, instrument_id_str)
    size_increment_val = round(10 ** (-sp), sp) if sp > 0 else 1.0

    return Cfd(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        asset_class=AssetClass.EQUITY,
        quote_currency=USD,
        price_precision=price_precision,
        size_precision=sp,
        price_increment=Price(price_increment_val, precision=price_precision),
        size_increment=Quantity(size_increment_val, precision=sp),
        ts_event=0,
        ts_init=0,
    )


# ---------------------------------------------------------------------------
# Metriken — Präzisions-FIFO-Matching für Netting Accounts via Fills Report
# ---------------------------------------------------------------------------



def collect_oos_fold_sortinos(per_fold_oos: list[dict]) -> list[float]:
    """Extrahiert je Fold den OOS-Sortino (Reihenfolge erhalten, None-sicher übersprungen)."""
    return [float(f["sortino_ratio"]) for f in per_fold_oos if f is not None and f.get("sortino_ratio") is not None]


def compute_fold_boundaries(start_ns: int, walk_forward_dict: dict) -> list[tuple[int, int, int]]:
    """Issue #490 — die EINZIGE Quelle der Walk-Forward-Fold-Geometrie.

    Einzelpass-Backtest mit fragmentiertem Holdout, KEIN re-trainierender Walk-Forward.
    Rein (kein I/O, kein State, deterministisch). Liefert je Fold ein Tripel
    ``(is_start_ns, oos_start_ns, oos_end_ns)``:

      * ``is_start_ns  = start_ns`` — das IS-Fenster bleibt statisch am Anfang der Daten verankert.
      * ``is_end_ns    = is_start_ns + is_window_ns``
      * ``purge_end_ns = is_end_ns + embargo_period_ns``
      * ``oos_start_ns = purge_end_ns + fold * oos_window_ns`` — kontiguierliche OOS-Sub-Folds.
      * ``oos_end_ns   = oos_start_ns + oos_window_ns``

    Vier Inline-Kopien dieser Arithmetik (Worker per-Trade-Klassifikation, Worker per-Fold-Sortinos,
    oos_trade_records, Aggregat per-Fold) wären eine eingebaute Divergenz-Falle — exakt analog zu
    ``compute_walk_forward_window`` für die äussere Fenster-Grenze (#457). Daher gilt hart: diese
    Geometrie NIE inline nachbauen, IMMER über diese Funktion (Single Source of Truth, #463/#466)."""
    is_window_ns = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
    oos_window_ns = walk_forward_dict.get("oos_window_days", 30) * 86400 * 1_000_000_000
    splits = walk_forward_dict.get("splits", 2)
    embargo_period_ns = walk_forward_dict.get("embargo_period_days", 0) * 86400 * 1_000_000_000

    boundaries: list[tuple[int, int, int]] = []
    is_start_ns = start_ns
    is_end_ns = is_start_ns + is_window_ns
    purge_end_ns = is_end_ns + embargo_period_ns

    for fold in range(splits):
        oos_start_ns = purge_end_ns + fold * oos_window_ns
        oos_end_ns = oos_start_ns + oos_window_ns
        boundaries.append((is_start_ns, oos_start_ns, oos_end_ns))
    return boundaries


_sortino_min_trades_cache: int | None = None

def _read_sortino_min_trades() -> int:
    """Issue #401 (Zero-Hardcoding): Mindest-Round-Trips fuer die Sortino-Berechnung aus
    tournament.json['sortino_min_trades']. Gecached (Hot-Path, je Worker-Subprozess konstant,
    da ETORO_CONFIG_DIR fix). Fehlt der Schluessel oder ist die Datei unlesbar ⇒ Legacy-Default
    5 (rueckwaertskompatibel, Fail-Safe)."""
    global _sortino_min_trades_cache
    if _sortino_min_trades_cache is not None:
        return _sortino_min_trades_cache
    val = 5
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_min_trades")
            if raw is not None:
                val = int(raw)
    except (OSError, ValueError, TypeError):
        val = 5
    _sortino_min_trades_cache = val
    return val


def _calculate_stats(pnl_list: list[float], hold_list: list[tuple[int, float]], starting_capital: float, med_notional: float = 0.0, *, min_trades_for_sortino: int | None = None, mtm_series: pd.Series | None = None) -> dict:
    """
    Berechnet die statistischen Performance-Metriken aus einer Liste von Trade-PnLs.

    Total Return Definition (Issue #465 / Audit #466):
    Liegt eine zeitbasierte MtM-Equity-Kurve (`mtm_series`) vor, ist `total_return` der ECHTE
    Portfolio-Return `equity_end / equity_start − 1`. Nur im Fallback ohne Equity-Kurve wird auf
    das sequentielle Aufzinsen `Π(1 + v/starting_capital)` zurückgegriffen (100 %-Kapital-Annahme;
    rückwärtskompatibel im Sonderfall nicht-überlappender Full-Capital-Trades).

    Drawdown-/Sortino-Basis (Issue #464/#465):
    Liegt `mtm_series` vor, werden `max_drawdown` UND `sortino_ratio` aus der zeitindizierten
    MtM-Equity-Kurve abgeleitet (Intra-Trade-/Floating-Drawdowns erfasst; Sortino mit
    `√(Perioden/Jahr)` aus dem REALEN Zeitspann, nie `√252` auf Trade-sequentiellen Returns).
    Der Fallback ohne Equity-Kurve (realisierte, Trade-geordnete PnL-Kurve + `√252`) ist ein
    Legacy-Pfad und darf NIE die OOS-Gate-/Reward-Metriken speisen (siehe AGENTS.md Pitfall #88).
    """
    import math
    NULL = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
        "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
        "losses_count": 0,
        "median_position_notional": 0.0,
    }
    if not pnl_list:
        return NULL

    n = len(pnl_list)
    wins = sum(1 for v in pnl_list if v > 0)
    gross_profit = sum(v for v in pnl_list if v > 0)
    gross_loss = abs(sum(v for v in pnl_list if v < 0))
    losses_count = sum(1 for v in pnl_list if v < 0)

    EPSILON = 1e-9
    DENOMINATOR_FLOOR = 1e-6
    RATIO_CAP = 50.0

    # Floor `gross_loss` at EPSILON implicitly to protect against division-by-zero, but logic captures it via count.
    if gross_loss <= 0.0:
        profit_factor = None
    elif losses_count < 2 and n < 50:
        profit_factor = None
    else:
        profit_factor = min(gross_profit / max(gross_loss, DENOMINATOR_FLOOR), RATIO_CAP)

    win_rate = wins / n if n > 0 else 0.0

    rets = [v / starting_capital for v in pnl_list]
    # Issue #465 (Audit #466) — total_return als ECHTER zeitbasierter Portfolio-Return aus der
    # MtM-Equity-Kurve (``equity_end / equity_start − 1``), sobald eine Equity-Kurve vorliegt.
    # Das sequentielle Aufzinsen ``Π(1 + pnl_i/C0)`` unterstellt 100 % Kapitaleinsatz je Trade
    # nacheinander und verzerrt damit das OOS-Gate (``oos_min_total_return``) UND — über #461 —
    # die dominante Reward-Penalty bei realer paralleler/fraktionaler Allokation. Fallback (keine
    # Equity-Kurve, z. B. Direkt-Unit-Calls von ``_calculate_stats``): sequentielles Aufzinsen
    # (Abwärtskompatibilität im Sonderfall nicht-überlappender Full-Capital-Trades).
    if (mtm_series is not None and not mtm_series.empty and len(mtm_series) > 1
            and float(mtm_series.iloc[0]) != 0.0):
        total_return = float(mtm_series.iloc[-1]) / float(mtm_series.iloc[0]) - 1.0
    else:
        cum = 1.0
        for r in rets:
            cum *= (1.0 + r)
        total_return = cum - 1.0

    if mtm_series is not None and not mtm_series.empty:
        import numpy as np
        import pandas as pd
        cumulative_max = mtm_series.cummax()
        drawdown = (mtm_series - cumulative_max) / cumulative_max.replace(0, np.nan)
        max_dd = abs(drawdown.min())
        if pd.isna(max_dd): max_dd = 0.0

        # Zwingende Isolation (High-Water Mark darf nicht vom IS-Fenster vererbt werden)
        cumulative_max = mtm_series.cummax()
        # Aber halt, cummax() startet neu am Anfang der Sliced Serie!
        # Falls es eine Series ist, passt cummax() für die aktuell übergebene Sektion.

        # Ableitung der per-Period Returns
        # Erster Return darf kein NaN-Artefakt erzeugen, dropna() erledigt das
        period_rets = mtm_series.pct_change().dropna()

        # Heterogenität des Sortino-Skalars (Dynamische Auflösung statt globalem Hardcoding)
        # Wir berechnen die effektive Frequenz der Datenreihe:
        span_years = (mtm_series.index[-1] - mtm_series.index[0]).total_seconds() / (365.25 * 86400) if len(mtm_series) > 1 else 0.0
        if span_years > 0:
            annualization_factor = len(period_rets) / span_years
        else:
            annualization_factor = 252.0 # fallback
        min_trades_sortino = min_trades_for_sortino if min_trades_for_sortino is not None else _read_sortino_min_trades()

        if n < min_trades_sortino or losses_count == 0 or period_rets.empty:
            sortino = None
        else:
            downside_rets = period_rets[period_rets < 0]
            dd_dev = downside_rets.std()
            if pd.isna(dd_dev) or dd_dev <= 0:
                sortino = None
            else:
                mean_ret = period_rets.mean()
                sortino_raw = (mean_ret / dd_dev) * math.sqrt(annualization_factor)
                sortino = min(sortino_raw, RATIO_CAP)
    else:
        peak = 1.0
        max_dd = 0.0
        cum_tmp = 1.0
        for r in rets:
            cum_tmp *= (1.0 + r)
            peak = max(peak, cum_tmp)
            max_dd = max(max_dd, (peak - cum_tmp) / peak)

    # Issue #401: 'n < 5' war hartcodiert (Zero-Hardcoding-Verstoss + Mismatch zu oos_min_trades);
    # jetzt deklarativ aus tournament.json['sortino_min_trades'] (Default 5). losses_count == 0
    # bleibt None (Zero-Loss ⇒ keine Downside-Deviation, Issue #209) und wird im Reward ueber
    # optimizer.json['oos_sortino_fallback'] aufgefangen, statt hier einen Sortino zu fabrizieren.
    if mtm_series is None or mtm_series.empty:
        min_trades_sortino = min_trades_for_sortino if min_trades_for_sortino is not None else _read_sortino_min_trades()
        if n < min_trades_sortino or losses_count == 0:
            sortino = None
        else:
            down_sq = [min(r, 0.0) ** 2 for r in rets]
            if len(down_sq) == 0 or sum(down_sq) <= 0.0:
                sortino = None
            else:
                # Addition *under* the root as requested by PR review
                # Floor dd_dev at 1e-6 to strictly protect against division-by-zero on micro downside deviations.
                dd_dev = math.sqrt((sum(down_sq) / len(down_sq)) + EPSILON)
                dd_dev = max(dd_dev, DENOMINATOR_FLOOR)
                mean_ret = sum(rets) / n
                sortino_raw = mean_ret / dd_dev * math.sqrt(252.0) # Legacy Fallback
                sortino = min(sortino_raw, RATIO_CAP)

    # Floor max_dd at DENOMINATOR_FLOOR to protect against division-by-zero when computing calmar.
    if max_dd <= 0.0:
        calmar = None
    else:
        calmar = min(total_return / max(max_dd, DENOMINATOR_FLOOR), RATIO_CAP)

    import statistics
    if hold_list:
        holds_s = [h / 1e9 for h, _ in hold_list]
        med_hold = statistics.median(holds_s)

        total_qty = sum(qty for _, qty in hold_list)
        if total_qty > 1e-9:
            avg_hold = sum((h / 1e9) * qty for h, qty in hold_list) / total_qty
        else:
            avg_hold = sum(holds_s) / len(holds_s)
    else:
        avg_hold = 0.0
        med_hold = 0.0

    return {
        "total_trades":  n,
        "win_rate":      float(win_rate),
        "profit_factor": float(profit_factor) if profit_factor is not None else None,
        "sortino_ratio": float(sortino) if sortino is not None else None,
        "calmar_ratio":  float(calmar) if calmar is not None else None,
        "max_drawdown":  float(max_dd),
        "total_return":  float(total_return),
        "avg_holding_time_s": float(avg_hold),
        "median_holding_time_s": float(med_hold),
        "losses_count": losses_count,
        "median_position_notional": float(med_notional),
    }


def _fill_ts_ns(f) -> int:
    """Issue #448 (Pitfall #80) — robuste, *fail-loud* Extraktion des Fill-Zeitstempels in
    absoluten Epoch-Nanosekunden.

    Lese-Reihenfolge: ``ts_event`` (``generate_fills_report``) → ``ts_last`` (der
    ``generate_order_fills_report``-Fallback hat **kein** ``ts_event``, sondern ``ts_last``!) →
    ``ts_init``. Ein ``pd.Timestamp`` wird via ``.value`` zu ns; ein roher int bleibt int.

    KRITISCH: Fehlen ALLE drei Felder (oder sind sie ``NaN``/``NaT``), wird hart geworfen
    (``ValueError``) — **niemals** still auf ``0`` defaulten. Ein ``ts==0`` schiebt jeden
    Round-Trip vor ``start_ns + is_window`` und klassifiziert ihn als In-Sample ⇒ struktureller
    OOS=0-Kollaps über alle Symbole/Strategien (Pitfall #75 Defekt A / #80). Der frühere stille
    ``getattr(f, 'ts_event', getattr(f, 'ts_init', 0))`` verbarg genau diese Domänen-Divergenz.
    """
    for attr in ("ts_event", "ts_last", "ts_init"):
        raw = getattr(f, attr, None)
        if raw is None:
            continue
        # Skalar-NaN/NaT-Guard (deckt float NaN, pd.NaT, np.datetime64('NaT') einheitlich ab).
        try:
            if bool(pd.isna(raw)):
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(raw, pd.Timestamp):
            return int(raw.value)
        return int(raw)
    raise ValueError(
        "Fill ohne verwertbares ts_event/ts_last/ts_init — Walk-Forward-Split nicht möglich "
        "(Pitfall #80). Der Fills-Report liefert keine Zeitstempel-Spalte; ein stiller 0-Default "
        "würde jeden Round-Trip als In-Sample klassifizieren (struktureller OOS-Kollaps)."
    )



from nautilus_trader.common.actor import Actor
from nautilus_trader.model.data import Bar
import pandas as pd

class PortfolioMonitor(Actor):
    def __init__(self, bar_type: str):
        super().__init__()
        from nautilus_trader.model.data import BarType
        self.bar_type = BarType.from_str(bar_type)
        self.equity_curve = []

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        try:
            eq = None
            if hasattr(self.portfolio, 'account'):
                # In NautilusTrader v1.229.0, margin_balance() might only be realized capital.
                # To guarantee we capture floating PnL (Issue #465), we explicitly add the sum of unrealized PnLs
                base_eq = self.portfolio.account.margin_balance().as_double() if hasattr(self.portfolio.account, 'margin_balance') else self.portfolio.account.balance().as_double()
                floating_pnl = sum(pos.unrealized_pnl().as_double() if callable(pos.unrealized_pnl) else float(pos.unrealized_pnl) for pos in self.portfolio.positions()) if hasattr(self.portfolio, 'positions') else 0.0
                eq = base_eq + floating_pnl
            elif hasattr(self.portfolio, 'equity'):
                if callable(self.portfolio.equity):
                    eq = self.portfolio.equity().as_double()
                else:
                    eq = float(self.portfolio.equity)
            if eq is not None:
                self.equity_curve.append((bar.ts_event, eq))
        except Exception:
            pass

    def get_equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self.equity_curve, columns=["ts", "equity"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ns")
        df.set_index("ts", inplace=True)
        # Drop duplicate timestamps, keeping the last recorded equity for the timestamp
        df = df[~df.index.duplicated(keep='last')]
        return df["equity"]

def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None, walk_forward_dict: dict | None = None, start_ns: int | None = None, commission_bps: float = 0.0, mtm_series: 'pd.Series | None' = None) -> dict:
    """
    Extrahiert Tournament-Metriken.

    Korrektur: Nutzt trader.generate_fills_report() statt des fehlerhaften Cache-Zugriffs.
    Unterstützt robustes FIFO-Position-Matching über DataFrames.

    Hinweis zu Drawdown/Return (Issue #464–#466):
    Diese Funktion reicht die per-Fold und aggregierten OOS-Slices der zeitbasierten MtM-Equity-Kurve
    (`mtm_series` aus dem `PortfolioMonitor`) an `_calculate_stats` durch. `max_drawdown`,
    `sortino_ratio` UND `total_return` der OOS-Metriken werden damit aus der zeitindizierten
    Equity-Kurve abgeleitet (Intra-Trade-Drawdowns erfasst, frequenzkorrekt annualisiert, echter
    Portfolio-Return) — nicht aus der Trade-geordneten realisierten PnL.
    """
    NULL = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
        "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
        "losses_count": 0,
        "median_position_notional": 0.0,
    }

    try:
        try:
            df_fills = engine.trader.generate_fills_report()
        except Exception:
            df_fills = pd.DataFrame()



        if df_fills.empty:
            try:
                df_fills = engine.trader.generate_order_fills_report()
            except Exception:
                df_fills = pd.DataFrame()

        if df_fills.empty:
            if log_fn:
                log_fn("[Metriken] Keine Fills oder ausgeführten Orders im Report dokumentiert.")
            if walk_forward_dict and start_ns is not None:
                return {"metrics": NULL, "oos_metrics": NULL,
                        "_oos_window_start_ns": None, "_oos_covered": None}
            return NULL
        instrument_fills: dict[str, list] = {}
        for row in df_fills.itertuples():
            iid = str(getattr(row, 'instrument_id', ''))
            if not iid:
                continue
            instrument_fills.setdefault(iid, []).append(row)

        pnls_with_ts = []
        notionals_with_ts = []

        # Chronologisches FIFO-Matching pro Instrument
        for iid, f_list in instrument_fills.items():
            sorted_fills = sorted(f_list, key=_fill_ts_ns)
            buy_queue: deque[tuple[float, float, int]] = deque()  # (Stückzahl, Preis, Timestamp)
            sell_queue: deque[tuple[float, float, int]] = deque() # (Stückzahl, Preis, Timestamp)

            for f in sorted_fills:
                try:
                    qty = float(getattr(f, 'last_qty', getattr(f, 'filled_qty', getattr(f, 'quantity', 0.0))))
                    price = float(getattr(f, 'last_px', getattr(f, 'avg_px', getattr(f, 'price', 0.0))))
                    side_str = str(getattr(f, 'order_side', getattr(f, 'side', ''))).upper()
                except Exception:
                    continue

                if math.isnan(qty) or qty <= 0:
                    continue

                is_buy = "BUY" in side_str

                if is_buy:
                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        entry_notional = match_qty * s_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price, s_ts)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()
                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        buy_queue.append((qty, price, ts_entry))
                else:
                    while qty > 0 and buy_queue:
                        b_qty, b_price, b_ts = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        entry_notional = match_qty * b_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price, b_ts)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()
                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        sell_queue.append((qty, price, ts_entry))



        if not pnls_with_ts:
            if log_fn:
                log_fn("[Metriken] Fills vorhanden, jedoch keine Trade-Schließungen (FIFO) generiert.")
            if walk_forward_dict and start_ns is not None:
                return {"metrics": NULL, "oos_metrics": NULL,
                        "_oos_window_start_ns": None, "_oos_covered": None}
            return NULL

        if log_fn:
            log_fn(f"[Metriken] FIFO-Extraktion: {len(pnls_with_ts)} Round-Trips erfolgreich berechnet.")

        # Issue #448/#444 — beobachtete Fill-ts-Spanne (min/max der Round-Trip-Exit-ts über alle
        # Instrumente). Wird in die Worker-/Tournament-Telemetrie gehoben (data_window.fill_ts_*),
        # damit ein OOS-Domänen-Defekt (Fills außerhalb von [start_ns, end_ns]) ohne Ad-hoc-
        # Diagnose-Logzeile sichtbar wird (Pitfall #80).
        _all_fill_ts = [t[1] for t in pnls_with_ts]
        fill_ts_min = min(_all_fill_ts) if _all_fill_ts else None
        fill_ts_max = max(_all_fill_ts) if _all_fill_ts else None

        # Issue #448 (Pitfall #80) — Plausibilitäts-Assertion gegen den stillen OOS=0-Kollaps.
        # Im Walk-Forward-Modus MÜSSEN die Fills in der absoluten Epoch-ns-Domäne des Fensters
        # liegen. `fill_ts_max < start_ns` (ALLE Round-Trips lägen vor Fensterbeginn) oder
        # `fill_ts_min <= 0` (Garbage-/0-Timestamp) ist für valide Daten unmöglich und wäre die
        # Signatur einer Zeitstempel-Domänen-Divergenz (Hypothese A/B). Fail-loud statt stiller
        # IS-Klassifizierung — verhindert künftige Regressionen dieser Klasse.
        if walk_forward_dict and start_ns is not None and fill_ts_max is not None:
            if fill_ts_min <= 0 or fill_ts_max < start_ns:
                raise ValueError(
                    f"Implausible Fill-Zeitstempel-Domäne (Pitfall #80): "
                    f"fill_ts∈[{fill_ts_min}, {fill_ts_max}], start_ns={start_ns}. "
                    f"Alle Round-Trips lägen vor dem Fensterbeginn ⇒ struktureller OOS=0-Kollaps. "
                    f"Vermutliche Ursache: Fill-ts aus falscher Clock-Domäne oder fehlendes "
                    f"ts_event/ts_last (siehe _fill_ts_ns)."
                )

        # Issue #455 (Pitfall #82) — OOS-Abdeckungs-Telemetrie. Die früheste OOS-Sub-Fenster-Grenze
        # (fold=0) ist start_ns + is_window_ns. Erreichen die realen Fills (fill_ts_max) diese Grenze
        # NICHT, erhält jedes OOS-Sub-Fenster null Fills ⇒ oos_total_trades=0 strukturell, parameter-
        # unabhängig über alle Strategien (dünner/staler H2-Katalog nach catalog_service-Ausfall).
        # Wir REICHEN das als Telemetrie durch und WARNEN sichtbar — bewusst KEIN raise: ein raise
        # würde über die NULL-Rückgabe genau die Telemetrie (oos_covered) verschlucken, die den
        # Operator DATENseitig statt parameterseitig diagnostizieren lässt. Die harte Vorab-Abweisung
        # gehört ins Gate-1-Preflight (sweep.enumerate_tunable_pairs), nicht in diese Mess-Funktion.
        oos_window_start_ns = None
        oos_covered = None
        if walk_forward_dict and start_ns is not None:
            # Issue #491 — OOS-Abdeckung über echte Fold-Geometrie statt naiver start+is_window Arithmetik.
            fold_boundaries_for_cov = compute_fold_boundaries(start_ns, walk_forward_dict)
            if fold_boundaries_for_cov:
                oos_window_start_ns = fold_boundaries_for_cov[0][1]

                # oos_covered := ∃ fill : ∃ fold k : oos_start_k ≤ fill < oos_end_k
                if _all_fill_ts:
                    oos_covered = False
                    for fill_ts in _all_fill_ts:
                        for _, fold_oos_start, fold_oos_end in fold_boundaries_for_cov:
                            if fold_oos_start <= fill_ts < fold_oos_end:
                                oos_covered = True
                                break
                        if oos_covered:
                            break
                else:
                    oos_covered = False

            if not oos_covered and log_fn:
                if fill_ts_max is not None and oos_window_start_ns is not None:
                    log_fn(
                        f"[OOS-Abdeckung] ⚠️ Keine Fills in den validen OOS-Folds (Pitfall #82). fill_ts_max "
                        f"liegt {'VOR' if fill_ts_max < oos_window_start_ns else 'NACH/AUSSERHALB'} den OOS-Grenzen. "
                        f"Kein Trade ist OOS-klassifizierbar ⇒ oos_total_trades=0 strukturell. Ursache ist "
                        f"DATENseitig (Katalog), nicht parameterseitig."
                    )
                else:
                    log_fn("[OOS-Abdeckung] ⚠️ Keine Fills im Fenster — OOS-Abdeckung nicht gegeben (Pitfall #82).")

        is_pnls = []
        oos_pnls = []
        is_holding_times = []
        oos_holding_times = []

        is_notionals = []
        oos_notionals = []

        if walk_forward_dict and start_ns is not None:
            is_window_ns = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
            oos_window_ns = walk_forward_dict.get("oos_window_days", 30) * 86400 * 1_000_000_000
            splits = walk_forward_dict.get("splits", 2)
            embargo_period_ns = walk_forward_dict.get("embargo_period_days", 0) * 86400 * 1_000_000_000
            # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth (kein Inline-Nachbau).
            fold_boundaries = compute_fold_boundaries(start_ns, walk_forward_dict)

            # IS Window boundaries are deterministic and identical for all folds
            _is_start_ns = start_ns
            is_end_ns = _is_start_ns + is_window_ns

            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_oos = False

                # Check for OOS inclusion across all folds
                for _, split_oos_start_ns, split_oos_end_ns in fold_boundaries:
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        is_oos = True
                        break

                is_in_sample = _is_start_ns <= ts < is_end_ns

                if is_oos:
                    oos_pnls.append(pnl)
                    oos_holding_times.append((ht, m_qty))
                    oos_notionals.append(notional)
                elif is_in_sample:
                    is_pnls.append(pnl)
                    is_holding_times.append((ht, m_qty))
                    is_notionals.append(notional)
        else:
            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_pnls.append(pnl)
                is_holding_times.append((ht, m_qty))
                is_notionals.append(notional)

        import statistics
        is_med_notional = statistics.median(is_notionals) if is_notionals else 0.0
        oos_med_notional = statistics.median(oos_notionals) if oos_notionals else 0.0

        is_mtm = None
        oos_mtm = None
        if mtm_series is not None and not mtm_series.empty and walk_forward_dict and start_ns is not None:
            # Slicing the mtm_series
            is_start_dt = pd.to_datetime(start_ns, unit="ns")
            is_end_dt = pd.to_datetime(start_ns + is_window_ns, unit="ns")
            oos_start_dt = pd.to_datetime(start_ns + is_window_ns + embargo_period_ns, unit="ns")
            oos_end_dt = pd.to_datetime(start_ns + is_window_ns + embargo_period_ns + oos_window_ns * splits, unit="ns")
            is_mtm = mtm_series.loc[is_start_dt:is_end_dt]
            oos_mtm = mtm_series.loc[oos_start_dt:oos_end_dt]
        elif mtm_series is not None and not mtm_series.empty:
            is_mtm = mtm_series

        is_metrics = _calculate_stats(is_pnls, is_holding_times, starting_capital, med_notional=is_med_notional, mtm_series=is_mtm)
        oos_metrics = _calculate_stats(oos_pnls, oos_holding_times, starting_capital, med_notional=oos_med_notional, mtm_series=oos_mtm) if oos_pnls else {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown": 0.0, "total_return": 0.0,
            "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
            "losses_count": 0,
            "median_position_notional": 0.0,
        }

        # Issue #303: Export raw OOS trade records for chronological portfolio aggregation
        per_fold_oos_list = []
        if walk_forward_dict and start_ns is not None:
            # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth (kein Inline-Nachbau).
            for _is_start_ns, split_oos_start_ns, split_oos_end_ns in compute_fold_boundaries(start_ns, walk_forward_dict):
                fold_pnls = []
                fold_holds = []
                fold_notionals = []

                for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        fold_pnls.append(pnl)
                        fold_holds.append((ht, m_qty))
                        fold_notionals.append(notionals_with_ts[i][0])

                import statistics
                fold_med_notional = statistics.median(fold_notionals) if fold_notionals else 0.0
                fold_mtm = None
                if mtm_series is not None and not mtm_series.empty:
                    split_oos_start_dt = pd.to_datetime(split_oos_start_ns, unit="ns")
                    split_oos_end_dt = pd.to_datetime(split_oos_end_ns, unit="ns")
                    fold_mtm = mtm_series.loc[split_oos_start_dt:split_oos_end_dt]
                if fold_pnls:
                    fold_metrics = _calculate_stats(fold_pnls, fold_holds, starting_capital, med_notional=fold_med_notional, mtm_series=fold_mtm)
                else:
                    fold_metrics = None
                per_fold_oos_list.append(fold_metrics)

            oos_metrics["oos_fold_sortinos"] = collect_oos_fold_sortinos(per_fold_oos_list)

        oos_trade_records = []
        if oos_pnls:
            # Reconstruct tuples of (pnl, ts, ht, m_qty, notional) for portfolio merging
            # They were processed in the same order as oos_pnls
            # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth; ohne Walk-Forward
            # (oder ohne start_ns) bleibt die Grenzliste leer ⇒ keine OOS-Records (wie zuvor splits=0).
            boundaries = (compute_fold_boundaries(start_ns, walk_forward_dict)
                          if walk_forward_dict and start_ns is not None else [])

            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_oos = False
                for _is_start_ns, split_oos_start_ns, split_oos_end_ns in boundaries:
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        is_oos = True
                        break
                if is_oos:
                    oos_trade_records.append((pnl, ts, ht, m_qty, notional))
        oos_metrics["_oos_trade_records"] = oos_trade_records

        if walk_forward_dict and start_ns is not None:
            return {
                "metrics": is_metrics,
                "oos_metrics": oos_metrics,
                # Issue #444/#448 — beobachtete Fill-ts-Spanne für die data_window-Telemetrie.
                "_fill_ts_min": fill_ts_min,
                "_fill_ts_max": fill_ts_max,
                # Issue #455 — OOS-Abdeckungs-Grenze + ob die Fills sie erreichen.
                "_oos_window_start_ns": oos_window_start_ns,
                "_oos_covered": oos_covered,
            }
        else:
            # Fallback for backwards compatibility if oos isn't requested
            return is_metrics
    except Exception as e:
        if log_fn:
            import traceback
            log_fn(f"[Metriken-Fehler] FIFO-Verarbeitung fehlgeschlagen: {e}\n{traceback.format_exc()}")
        # Issue #448 — formgleicher Rückgabewert: im Walk-Forward-Modus das nested NULL-Dict, sonst
        # das flache NULL (der Worker behandelt beide, aber Formgleichheit hält die Telemetrie sauber).
        if walk_forward_dict and start_ns is not None:
            return {"metrics": NULL, "oos_metrics": NULL, "_fill_ts_min": None, "_fill_ts_max": None,
                    "_oos_window_start_ns": None, "_oos_covered": None}
        return NULL


# ---------------------------------------------------------------------------
# Tournament-Logik
# ---------------------------------------------------------------------------

def select_winners(
    all_results: list[dict],
    tournament_cfg: dict | None = None,
    start_ns: int | None = None,
) -> tuple[dict, dict | None, list[str], int, int]:
    """Wählt Gewinner pro Symbol anhand der Tournament-Konfiguration.

    Task 5: Robuste Multi-Kriterien-Selektion mit:
      - eligible_requires_all: harte Filter (z.B. min_trades >= 10)
      - eligible_requires_any: weiche Filter (min_sortino ODER min_profit_factor)
      - composite Score für die finale Gewinner-Auswahl
      - score-Feld im Output

    Args:
        all_results:     Liste aller Backtest-Ergebnisse
        tournament_cfg:  Konfig aus tournament.json. Wenn None, wird geladen.

    Returns:
        (per_symbol_winners, aggregate_winner, warnings, is_eligible_count, fully_eligible_count)
    """
    if tournament_cfg is None:
        tournament_cfg = load_tournament_config()

    scoring = tournament_cfg.get("scoring", {})
    warnings_list = []

    # 1. IS-Eligibility filtern
    is_eligible_population = []
    for r in all_results:
        is_is_eligible = _is_eligible(
            r,
            tournament_cfg,
            strat_params=r.get("strat_params", {}),
            symbol=r.get("symbol", "Unknown"),
            strategy=r.get("strategy", "Unknown"),
            log_rejections=True
        )
        if is_is_eligible:
            is_eligible_population.append(r)

    is_eligible_count = len(is_eligible_population)

    # Issue #148: Data Start Alignment (Tournament Gating)
    if is_eligible_population:
        # Using _first_tick_ns for Regime-Bias check is fine, it is NOT used for OOS boundary derivations.
        start_dates = [r.get("_first_tick_ns") for r in is_eligible_population if r.get("_first_tick_ns") is not None]
        if start_dates:
            min_start = min(start_dates)
            max_start = max(start_dates)
            # Threshold: 1 day in nanoseconds
            if max_start - min_start > 86400 * 1_000_000_000:
                msg = "⚠️  WARNING: Tournament aggregiert Symbole mit unterschiedlichen Startdaten / Regime-Bias möglich!"
                print(msg)
                warnings_list.append(msg)

    # 2. Normalisierung der Metriken über alle IS-eligiblen Ergebnisse
    if is_eligible_population:
        def get_ranks(vals, reverse=False):
            # Issue #263 / #288: Handle None/All-Win scenarios logically.
            # Convert any None/0.0 fallback that actually corresponds to an All-Win
            # to a scaled sentinel value in the caller, but here we process the vals correctly.
            # We filter out exactly 50.0 to prevent hard caps from contaminating the distribution.
            # We don't filter the scaled sentinels since they represent legitimate rank differences.
            non_sentinels = [v for v in vals if v != 50.0]
            if len(non_sentinels) == 0:
                return [1.0] * len(vals)
            su = sorted(list(set(vals)), reverse=reverse)
            if len(su) <= 1:
                return [1.0] * len(vals)
            return [(su.index(v)) / (len(su) - 1) for v in vals]

        # Issue #288: Introduce Sample-Size Shrinkage logic for `None` (All-Win) sentinels.
        # We must protect genuine empirical ratios from being degraded by dynamic sentinels.
        def get_sentinel(n_trades, population_ratios):
            # 1. Scale sentinel based on sample size confidence
            scaled = min(50.0, max(2.0, 50.0 * (n_trades / 50.0)))
            # 2. Prevent dynamic sentinel from outranking the highest genuine organic ratio
            organic_ratios = [v for v in population_ratios if v is not None and v != 50.0]
            if organic_ratios:
                return min(scaled, max(organic_ratios))
            return scaled

        k_shrinkage = tournament_cfg.get("k_shrinkage", 20.0) # Parameter for score damping

        def apply_shrinkage(raw_val, baseline, n_trades):
            if raw_val is None:
                return None
            return baseline + (raw_val - baseline) * (n_trades / (n_trades + k_shrinkage))

        raw_sortinos = [
            apply_shrinkage(r["metrics"].get("sortino_ratio"), 0.0, r["metrics"].get("total_trades", 0))
            for r in is_eligible_population
        ]
        raw_pfs = [
            apply_shrinkage(r["metrics"].get("profit_factor"), 1.0, r["metrics"].get("total_trades", 0))
            for r in is_eligible_population
        ]

        sortinos = [(r if r is not None else get_sentinel(is_eligible_population[i]["metrics"].get("total_trades", 0), raw_sortinos)) for i, r in enumerate(raw_sortinos)]
        pfs = [(r if r is not None else get_sentinel(is_eligible_population[i]["metrics"].get("total_trades", 0), raw_pfs)) for i, r in enumerate(raw_pfs)]
        wrs = [(r["metrics"].get("win_rate") or 0.0) for r in is_eligible_population]
        dds = [(r["metrics"].get("max_drawdown") or 0.0) for r in is_eligible_population]

        rs = get_ranks(sortinos)
        rp = get_ranks(pfs)
        rw = get_ranks(wrs)
        rd = get_ranks(dds)

        for i, r in enumerate(is_eligible_population):
            r["norm_metrics"] = {
                "sortino_ratio": rs[i],
                "profit_factor": rp[i],
                "win_rate": rw[i],
                "max_drawdown": rd[i]
            }

            metrics_to_score = r.get("norm_metrics")
            if metrics_to_score is None:
                metrics_to_score = r["metrics"]

            raw_score = compute_tournament_score(metrics_to_score, scoring)
            n_trades = r["metrics"].get("total_trades", 0)

            # Apply shrinkage to composite score based on total_trades
            damped_score = raw_score * (n_trades / (n_trades + k_shrinkage))
            r["_score"] = damped_score

    # 3. Per-Symbol OOS-Gating (Der Entscheidungs-Trail)
    fully_eligible_count = 0
    require_oos = tournament_cfg.get("require_oos", True)

    # Iteration ueber all_results fuer Telemetrie und single_symbol_oos
    for r in all_results:
        strat_params = r.get("strat_params", {})
        # _oos_eval zwingend an jedes Resultat anhaengen, unabhaengig vom IS-Status
        r["_oos_eval"] = _evaluate_oos_eligibility(
            r.get("oos_metrics"),
            tournament_cfg,
            strat_params
        )

    # Pre-evaluate all to get the true fully_eligible_count
    for r in is_eligible_population:
        oos_eval = r["_oos_eval"]
        if oos_eval.get("oos_eligible", False):
            fully_eligible_count += 1

    grouped_by_symbol = {}
    for r in is_eligible_population:
        sym = r["symbol"]
        grouped_by_symbol.setdefault(sym, []).append(r)

    per_symbol_winners = {}
    for sym, candidates in grouped_by_symbol.items():
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (c.get("_score", float("-inf")), c["metrics"].get("total_return", 0.0)),
            reverse=True
        )

        for r in candidates_sorted:
            strat = r["strategy"]
            score = r.get("_score", 0.0)
            oos_eval = r["_oos_eval"]

            if oos_eval.get("oos_eligible", False):
                per_symbol_winners[sym] = {
                    "strategy": strat,
                    "metrics": r["metrics"],
                    "score": round(score, 6),
                    **oos_eval
                }
                break  # Winner found, move to next symbol
            else:
                reasons = ", ".join(oos_eval.get("oos_rejection_reasons", []))
                print(f"  [OOS-Drop] {sym} | {strat} (Score: {score:.4f}) verworfen: {reasons}")

    # Aggregierter Gewinner: Strategie mit den meisten Symbol-Siegen
    win_counts: dict[str, int] = {}
    sortinos_by_strat: dict[str, list] = {}
    for r in per_symbol_winners.values():
        s = r["strategy"]
        win_counts[s] = win_counts.get(s, 0) + 1
        sortinos_by_strat.setdefault(s, []).append(r["metrics"]["sortino_ratio"])

    aggregate_winner = None
    if win_counts:
        def get_median(vals):
            # Issue #288 / #263: Filter out None and exactly 50.0 hard caps to prevent distortion.
            # Scaled sentinels (< 50.0) are deliberately kept to reflect sample size significance.
            vals = [v for v in vals if v is not None]
            non_sentinel_vals = [v for v in vals if v != 50.0]

            # Use non-sentinel values if available, fallback to full list if all are sentinels
            target_vals = non_sentinel_vals if non_sentinel_vals else vals

            sv = sorted(target_vals)
            n = len(sv)
            if n == 0: return 0.0
            if n % 2 == 1: return sv[n//2]
            return (sv[n//2 - 1] + sv[n//2]) / 2.0

        def get_iqr(vals):
            sv = sorted(vals)
            n = len(sv)
            if n < 2: return 0.0
            q1 = sv[n//4]
            q3 = sv[(n*3)//4]
            return q3 - q1

        # Tie-breaker: 1. Max Wins, 2. Max Median Sortino
        max_wins = max(win_counts.values())
        top      = [s for s, w in win_counts.items() if w == max_wins]
        best     = max(top, key=lambda s: get_median([x for x in sortinos_by_strat[s] if x is not None]))
        # Nur OOS-Metriken der Symbole, bei denen die Strategie tatsächlich gewonnen hat
        best_results = [r.get("oos_metrics", {}) for r in per_symbol_winners.values()
                        if r["strategy"] == best and r.get("oos_metrics") and r.get("oos_metrics").get("total_trades", 0) > 0]

        if best_results:
            n_res = len(best_results)

            # Issue #303: Chronological Portfolio Aggregation
            # Collect all _oos_trade_records and sort them by timestamp
            portfolio_trades = []
            for oos in best_results:
                records = oos.get("_oos_trade_records", [])
                portfolio_trades.extend(records)

            # Sort strictly by timestamp (ts is index 1 in the tuple)
            portfolio_trades.sort(key=lambda x: x[1])

            # Reconstruct the lists for _calculate_stats
            # tuple is: (pnl, ts, ht, m_qty, notional)
            portfolio_pnls = [tr[0] for tr in portfolio_trades]
            portfolio_holds = [(tr[2], tr[3]) for tr in portfolio_trades]
            portfolio_notionals = [tr[4] for tr in portfolio_trades]

            import statistics
            portfolio_med_notional = statistics.median(portfolio_notionals) if portfolio_notionals else 0.0

            # Echte Portfolio-Aggregation für Trades und Wins
            portfolio_total_trades = sum((oos.get("total_trades") or 0) for oos in best_results)
            # Rekonstruktion der absoluten Wins pro Symbol und Aufsummierung (typsicher)
            portfolio_wins = sum(
                int(round((oos.get("win_rate") or 0.0) * (oos.get("total_trades") or 0)))
                for oos in best_results
            )
            portfolio_win_rate = portfolio_wins / portfolio_total_trades if portfolio_total_trades > 0 else 0.0

            # Trade-Weighted Portfolio Rendite
            portfolio_mean_return = sum((oos.get("total_return") or 0.0) * (oos.get("total_trades") or 0) for oos in best_results) / portfolio_total_trades if portfolio_total_trades > 0 else 0.0

            span_days = [oos.get("oos_span_days", 0) for oos in best_results]
            med_span = get_median(span_days) if span_days else 0

            # We must use a constant starting_capital here, assuming 100k or default 1.0 (though _calculate_stats uses returns = v/starting_capital).
            # To get accurate ratios, we need the original starting_capital used during the run.
            # Assuming 100_000.0 is the default. We can extract it from the first result's `strat_params` or use a constant since relative differences apply.
            # Best effort to extract the original starting capital or default to 100k
            starting_capital = None
            if is_eligible_population:
                starting_capital = is_eligible_population[0].get("start_capital") or is_eligible_population[0].get("strat_params", {}).get("starting_capital")

            if starting_capital is None:
                try:
                    bt_cfg_path = str(config_dir() / "backtest.json")
                    if os.path.exists(bt_cfg_path):
                        starting_capital = load_config(bt_cfg_path).get("start_capital", 100_000.0)
                except Exception:
                    starting_capital = 100_000.0

            if starting_capital is None:
                starting_capital = 100_000.0


            # Calculate the true portfolio metrics from chronologically ordered trades
            portfolio_metrics = _calculate_stats(portfolio_pnls, portfolio_holds, starting_capital, med_notional=portfolio_med_notional)

            avg_oos = {
                "total_trades": portfolio_total_trades,
                "sortino_ratio": portfolio_metrics.get("sortino_ratio"),
                "profit_factor": portfolio_metrics.get("profit_factor"),
                "max_drawdown": portfolio_metrics.get("max_drawdown", 1.0),
                "win_rate": portfolio_win_rate,
                "total_return": portfolio_mean_return,
                "oos_span_days": med_span,
                "median_position_notional": portfolio_metrics.get("median_position_notional", 0.0),
                "aggregation_basis": "portfolio_equity_curve"
            }

            # Use strat_params from the first result matching the winning strategy
            winner_strat_params = {}
            for r in is_eligible_population:
                if r["strategy"] == best:
                    winner_strat_params = r.get("strat_params", {})
                    break

            # Normalize metrics before gating to avoid the "Trade-Sum Trap"
            avg_oos_for_gate = avg_oos.copy()
            if n_res > 0:
                avg_oos_for_gate["total_trades"] = int(portfolio_total_trades / n_res)

            agg_oos_eval = _evaluate_oos_eligibility(avg_oos_for_gate, tournament_cfg, winner_strat_params)
            # Reattach the unnormalized original values for correct logging/metrics
            agg_oos_eval["oos_metrics"] = avg_oos

            # Task 0b: Extract per-fold OOS sortinos for the aggregate winner
            if is_eligible_population:
                # start_ns is already passed as a kwarg from the single source of truth
                walk_forward_dict = winner_strat_params.get("_walk_forward_dict", {})
                if walk_forward_dict and start_ns is not None:
                    # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth (kein Inline-Nachbau).
                    per_fold_oos_list = []
                    for _is_start_ns, split_oos_start_ns, split_oos_end_ns in compute_fold_boundaries(start_ns, walk_forward_dict):
                        fold_pnls = []
                        fold_holds = []
                        fold_notionals = []

                        for (pnl, ts, ht, m_qty, notional) in portfolio_trades:
                            if split_oos_start_ns <= ts < split_oos_end_ns:
                                fold_pnls.append(pnl)
                                fold_holds.append((ht, m_qty))
                                fold_notionals.append(notional)

                        import statistics
                        fold_med_notional = statistics.median(fold_notionals) if fold_notionals else 0.0
                        # The portfolio aggregate per fold cannot easily have an aggregated MtM curve without combining time series from multiple runs. We'll pass None for the MtM series here.
                        if fold_pnls:
                            fold_metrics = _calculate_stats(fold_pnls, fold_holds, starting_capital, med_notional=fold_med_notional)
                        else:
                            fold_metrics = None
                        per_fold_oos_list.append(fold_metrics)

                    agg_oos_eval["oos_fold_sortinos"] = collect_oos_fold_sortinos(per_fold_oos_list)

        else:

            agg_oos_eval = {
                "oos_evaluated": False,
                "oos_eligible": False,
                "oos_metrics": None,
                "oos_rejection_reasons": ["oos_not_evaluable: Kein OOS-Datenmaterial für die Gewinn-Symbole."]
            }

        # Assertion: Aggregate OOS pass cannot override a per-pair failure
        assert not (agg_oos_eval.get("oos_eligible", False) is True and len(per_symbol_winners) == 0), "Aggregat-OOS-Pass darf nicht das Per-Pair-Gate überstimmen (eligible_pairs == 0)"

        aggregate_winner = {
            "strategy":    best,
            "win_count":   win_counts[best],
            "median_is_sortino": round(
                get_median([x for x in sortinos_by_strat[best] if x is not None]), 4
            ),
            **agg_oos_eval
        }

    # Clean up _oos_trade_records to prevent memory/JSON bloat
    for r in per_symbol_winners.values():
        if "oos_metrics" in r and r["oos_metrics"]:
            r["oos_metrics"].pop("_oos_trade_records", None)
    if aggregate_winner and "oos_metrics" in aggregate_winner and aggregate_winner["oos_metrics"]:
        aggregate_winner["oos_metrics"].pop("_oos_trade_records", None)

    return per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count


def _build_single_symbol_oos(all_results: list[dict]) -> dict | None:
    """Issue #405 — entkoppelt die Per-Symbol-OOS-Evaluierbarkeit vom Tournament-Gewinner-Status.

    Im Single-Symbol-Sweep (universe_size==1) bleibt ``aggregate_winner`` ``null``, solange das
    Symbol das volle Gate-Stack (IS-eligible ∧ OOS-eligible) fuer KEINE Parametrisierung klaert
    (Pitfall #75, Defekt 1) — die Per-Symbol-OOS-Resultate (``_oos_eval``, ``oos_metrics``)
    existieren aber. Dieser Block spiegelt sie — UNGEACHTET des Gewinner-Status — in der Struktur
    eines ``aggregate_winner``, sodass ``parse_tournament`` ``oos_evaluated``/``oos_metrics``
    daraus ableiten kann, wenn kein Aggregat-Gewinner vorliegt.

    Aktiv NUR fuer genau ein Symbol (Multi-Symbol-Laeufe ⇒ ``None`` ⇒ bit-identisch). Gibt es kein
    IS-eligibles Resultat (kein ``_oos_eval``), wird ebenfalls ``None`` zurueckgegeben — der Lauf
    bleibt ehrlich unevaluable (kein erfundener OOS-Record). Bei mehreren Kandidaten wird der
    OOS-staerkste gewaehlt: (oos_eligible, oos_evaluated, _score)."""
    symbols = {r.get("symbol") for r in all_results}
    if len(symbols) != 1:
        return None

    evaluated = [r for r in all_results if isinstance(r.get("_oos_eval"), dict)]
    if not evaluated:
        return None

    def _rank(r: dict):
        ev = r["_oos_eval"]
        return (
            1 if ev.get("oos_eligible") else 0,
            1 if ev.get("oos_evaluated") else 0,
            r.get("_score", float("-inf")),
        )

    best = max(evaluated, key=_rank)
    oos_eval = best["_oos_eval"]
    raw_oos = best.get("oos_metrics") or oos_eval.get("oos_metrics") or {}
    # JSON-Hygiene: interne Trade-Records nicht durchreichen (analog Cleanup fuer den Aggregat-Block).
    oos_metrics = {k: v for k, v in raw_oos.items() if k != "_oos_trade_records"}
    is_metrics = best.get("metrics") or {}

    return {
        "strategy": best.get("strategy"),
        "oos_evaluated": bool(oos_eval.get("oos_evaluated", False)),
        "oos_eligible": bool(oos_eval.get("oos_eligible", False)),
        "oos_rejection_reasons": oos_eval.get("oos_rejection_reasons", []),
        "oos_metrics": oos_metrics,
        "oos_fold_sortinos": oos_metrics.get("oos_fold_sortinos") or [],
        "median_is_sortino": is_metrics.get("sortino_ratio"),
        "win_count": 1 if oos_eval.get("oos_eligible") else 0,
    }


def write_tournament_json(
    all_results: list[dict],
    output_path: str,
    per_symbol_winners: dict,
    aggregate_winner: dict | None,
    warnings_list: list[str],
    is_eligible_count: int,
    fully_eligible_count: int,
    universe_snapshot: str = "",
    tournament_cfg: dict | None = None,
    *,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> None:
    """Schreibt Tournament-Ergebnisse als JSON.

    Task 5: Jeder Gewinner-Eintrag enthält jetzt ein 'score'-Feld.

    Issue #444: ``start_ns``/``end_ns`` (re-anchored Fenster) sind optional; sind sie gesetzt
    ODER liefert mindestens ein Worker eine Fill-ts-Spanne, wird ein ``data_window``-Block mit
    ``start``/``end``/``days`` und ``fill_ts_min``/``fill_ts_max`` geschrieben (Telemetrie-Lücke
    aus #416 geschlossen). Fehlen beide ⇒ kein Block (rückwärtskompatibel).
    """
    if tournament_cfg is None:
        tournament_cfg = load_tournament_config()

    # Count OOS not evaluable and OOS failed pairs
    oos_not_evaluable_pairs = 0
    oos_failed_pairs = 0

    for r in all_results:
        # Check if the pair passed IS gating (has _oos_eval)
        oos_eval = r.get("_oos_eval")
        if oos_eval is not None:
            # We only count OOS rejections, not those that were not IS eligible
            if not oos_eval.get("oos_evaluated", False) and not oos_eval.get("oos_eligible", False):
                # Distinguish based on rejection reason or evaluated flag
                # If oos_evaluated is False, it's not evaluable (trade shortage / missing data)
                oos_not_evaluable_pairs += 1
            elif oos_eval.get("oos_evaluated", False) and not oos_eval.get("oos_eligible", False):
                # Evaluated but failed (performance criteria not met)
                oos_failed_pairs += 1

    output = {
        "generated_at":                datetime.now(timezone.utc).isoformat(),
        "universe_snapshot":           universe_snapshot,
        "total_symbol_strategy_pairs": len(all_results),
        "oos_not_evaluable_pairs":     oos_not_evaluable_pairs,
        "oos_failed_pairs":            oos_failed_pairs,
        "eligible_pairs":              fully_eligible_count,
        "is_eligible_pairs":           is_eligible_count,
        "fully_eligible_pairs":        fully_eligible_count,
        "tournament_criteria":         tournament_cfg,
        "normalization_method":        "rank_based",
        "normalization_population":    is_eligible_count,
        "warnings":                    warnings_list,
        "per_symbol_winners":          per_symbol_winners,
        "aggregate_winner":            aggregate_winner,
        "full_results":                all_results,
    }

    # Issue #405 — Single-Symbol-Pfad (universe_size==1): den Per-Symbol-OOS-Block beilegen, damit
    # parse_tournament die Evaluierbarkeit aus den tatsaechlichen OOS-Resultaten ableiten kann,
    # selbst wenn das Symbol kein Aggregat-Gewinner wurde (Pitfall #75). Multi-Symbol-Laeufe
    # erhalten KEINEN Block ⇒ Output bit-identisch.
    single_symbol_oos = _build_single_symbol_oos(all_results)
    if single_symbol_oos is not None:
        output["single_symbol_oos"] = single_symbol_oos

    # Issue #444 — data_window-Block (Telemetrie-Lücke aus #416, die Schreib-Seite fehlte). Weist
    # das TATSÄCHLICH evaluierte Fenster (nach Re-Anchoring) plus die beobachtete Fill-ts-Spanne
    # über alle Worker aus. parse_tournament liest start/end/days bereits; fill_ts_min/max macht
    # OOS-Domänen-Defekte (Pitfall #80) direkt in der Telemetrie sichtbar.
    _fill_mins = [r.get("_fill_ts_min") for r in all_results if r.get("_fill_ts_min") is not None]
    _fill_maxs = [r.get("_fill_ts_max") for r in all_results if r.get("_fill_ts_max") is not None]
    fill_ts_min = min(_fill_mins) if _fill_mins else None
    fill_ts_max = max(_fill_maxs) if _fill_maxs else None
    # Issue #455 — OOS-Abdeckungs-Grenze aggregieren. oos_window_start_ns ist über alle Worker
    # identisch (hängt nur an start_ns + is_window), daher genügt der erste Nicht-None-Wert. Die
    # Coverage wird auf Aggregat-Ebene gegen den globalen fill_ts_max neu bestimmt (ein einziger
    # Worker mit OOS-Fills genügt, damit das Paar OOS-abgedeckt ist).
    _oos_starts = [r.get("_oos_window_start_ns") for r in all_results if r.get("_oos_window_start_ns") is not None]
    oos_window_start_ns = _oos_starts[0] if _oos_starts else None

    # Issue #491 — Aggregation von oos_covered aus den Worker-Ergebnissen anstatt
    # naiver fill_ts_max >= oos_window_start_ns Rechnung.
    _oos_covereds = [r.get("_oos_covered") for r in all_results if r.get("_oos_covered") is not None]
    oos_covered = any(_oos_covereds) if _oos_covereds else None

    if start_ns is not None or end_ns is not None or fill_ts_min is not None:
        _day_ns = 86400 * 1_000_000_000
        if oos_window_start_ns is not None and fill_ts_max is not None and oos_covered is not None:
            # Wenn fill_ts_max VOR dem ersten OOS-Start liegt, berechnen wir die Lücke.
            # Wenn es NACH dem ersten OOS-Start liegt, aber oos_covered False ist (z.B. nach letztem Fold),
            # ist die Gap 0.0 (es liegt kein "Verfehlen" der Grenze in der Vergangenheit vor).
            if fill_ts_max < oos_window_start_ns:
                oos_coverage_gap_days = round((oos_window_start_ns - fill_ts_max) / _day_ns, 1)
            else:
                oos_coverage_gap_days = 0.0
        else:
            oos_coverage_gap_days = None
        output["data_window"] = {
            "start_ns": start_ns,
            "end_ns":   end_ns,
            "start":    pd.Timestamp(start_ns, unit="ns", tz="UTC").isoformat() if start_ns else None,
            "end":      pd.Timestamp(end_ns,   unit="ns", tz="UTC").isoformat() if end_ns   else None,
            "days":     round((end_ns - start_ns) / _day_ns, 1) if (start_ns and end_ns) else None,
            "fill_ts_min": fill_ts_min,
            "fill_ts_max": fill_ts_max,
            # Issue #455 — OOS-Abdeckung: Grenze (ISO + ns), Flag und Lücke in Tagen. oos_covered=False
            # ⇒ struktureller OOS=0-Kollaps ist DATENseitig (H2-Katalog), nicht parameterseitig.
            "oos_window_start_ns": oos_window_start_ns,
            "oos_window_start": (pd.Timestamp(oos_window_start_ns, unit="ns", tz="UTC").isoformat()
                                 if oos_window_start_ns is not None else None),
            "oos_covered": oos_covered,
            "oos_coverage_gap_days": oos_coverage_gap_days,
        }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n📄 Tournament-Ergebnisse gespeichert: {output_path}")


def print_tournament_table(
    all_results: list[dict],
    per_symbol_winners: dict,
    tournament_cfg: dict | None = None,
) -> tuple[int, list[str]]:
    print(f"\n{'Symbol':<20} | {'Strategy':<30} | {'Sortino':>7} | {'Calmar':>7} | {'PF':>7} | {'Trades':>6} | {'Hold(h)':>7} | Win?")
    print("-" * 105)
    winner_count = 0
    all_symbols: set[str] = set()
    winning_symbols: set[str] = set()

    global_min_trades_req = tournament_cfg.get("min_trades", 20) if tournament_cfg else 20

    for r in sorted(all_results, key=lambda x: (x["symbol"], x["strategy"])):
        sym, strat, m = r["symbol"], r["strategy"], r["metrics"]
        all_symbols.add(sym)
        is_winner = sym in per_symbol_winners and per_symbol_winners[sym]["strategy"] == strat
        if is_winner:
            winner_count += 1
            winning_symbols.add(sym)
        hold_h = m.get('avg_holding_time_s', 0.0) / 3600.0

        strat_params = r.get("strat_params", {})
        t_overrides = strat_params.get("tournament_overrides", {})
        strat_min_trades = t_overrides.get("min_trades", global_min_trades_req)

        def format_metric(val, req_trades):
            if m.get('total_trades', 0) < req_trades:
                return f"{'n/a(<min)':>7}"
            if val is not None:
                return f"{val:>7.2f}"
            if m.get('losses_count', 0) == 0 or m.get('max_drawdown', 0.0) == 0.0:
                return f"{'n/a(win)':>7}"
            return f"{'n/a':>7}"

        sortino_str = format_metric(m['sortino_ratio'], strat_min_trades)
        calmar_str = format_metric(m['calmar_ratio'], strat_min_trades)
        pf_str = format_metric(m['profit_factor'], strat_min_trades)

        print(
            f"{sym:<20} | {strat:<30} | {sortino_str} | "
            f"{calmar_str} | {pf_str} | "
            f"{m['total_trades']:>6} | {hold_h:>7.1f} | {'✓' if is_winner else ''}"
        )
    return winner_count, sorted(all_symbols - winning_symbols)


# ---------------------------------------------------------------------------
# Worker-Prozess
# ---------------------------------------------------------------------------

def _get_normalized_catalog_path(original_catalog_path: str, instrument_id: str) -> str | None:
    """
    Reads a Parquet file for an instrument, normalizes size_precision > 0 else 8,
    and returns a path to a temporary catalog directory if modifications were needed.
    """
    original_path = Path(original_catalog_path) / "data" / "quote_tick" / instrument_id
    if not original_path.exists():
        return None

    parquet_files = list(original_path.glob("*.parquet"))
    if not parquet_files:
        return None

    first_file = parquet_files[0]
    table = pq.read_table(str(first_file))
    meta = table.schema.metadata or {}

    # Extract existing precision from bytes
    val = meta.get(b"size_precision", b"0")
    sp_parquet = int(val.decode("utf-8") if isinstance(val, bytes) else val)

    # Apply the exact same normalization logic
    normalized_sp = _normalize_size_precision(sp_parquet, instrument_id)

    # If it matches, no I/O needed; return None
    if normalized_sp == sp_parquet:
        return None

    # Inject the normalized precision back into the schema
    meta[b"size_precision"] = str(normalized_sp).encode("utf-8")

    # Write to a temporary catalog directory
    temp_catalog = tempfile.mkdtemp(prefix="nautilus_temp_catalog_")
    target_path = Path(temp_catalog) / "data" / "quote_tick" / instrument_id
    target_path.mkdir(parents=True, exist_ok=True)

    for p_file in parquet_files:
        t = pq.read_table(str(p_file))
        t = t.replace_schema_metadata(meta)
        pq.write_table(t, str(target_path / p_file.name))

    return temp_catalog



def _empty_result(symbol: str, strategy: str, strat: dict, start_capital: float = 100000.0) -> dict:
    NULL = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
        "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
        "losses_count": 0,
        "median_position_notional": 0.0,
    }
    return {
        "symbol": symbol,
        "strategy": strategy,
        "metrics": NULL,
        "oos_metrics": NULL if strat.get("_walk_forward_dict") else {},
        "strat_params": strat.get("params", {}),
        "start_capital": start_capital
    }

def check_data_span(ticks: list, required_days: int, span_tolerance_days: float) -> tuple[bool, float, float]:
    """
    Überprüft die Datenspanne der Ticks.
    Gibt (is_sufficient, span_days, required_days) zurück.
    """
    span_ns = ticks[-1].ts_event - ticks[0].ts_event
    span_ns_val = span_ns.value if hasattr(span_ns, 'value') else int(span_ns)
    span_days = span_ns_val / (86400 * 1_000_000_000)
    min_required_ns = (required_days - span_tolerance_days) * 86400 * 1_000_000_000
    return (span_ns_val >= min_required_ns, span_days, required_days)


def _format_backtest_window(first_tick_ns: int, last_tick_ns: int) -> str:
    """Issue #403: formatiert das *tatsaechliche* Daten-Zeitfenster eines Backtests
    (Start-Tick bis End-Tick + Spanne in Tagen) fuer das Worker-Log. Rein funktional und
    damit deterministisch testbar (vorher fehlte das Enddatum/die Dauer komplett — man sah
    nur den ersten Tick)."""
    span_days = (last_tick_ns - first_tick_ns) / (86400 * 1_000_000_000)
    start = pd.Timestamp(first_tick_ns, unit='ns', tz='UTC').strftime('%Y-%m-%d')
    end = pd.Timestamp(last_tick_ns, unit='ns', tz='UTC').strftime('%Y-%m-%d')
    return f"📅 Backtest-Zeitfenster: {start} bis {end} ({span_days:.1f} Tage)"


def run_single_backtest_worker(
    inst_id_str: str,
    bar_type: str,
    strat: dict,
    catalog_path: str,
    start_ns: int | None,
    end_ns: int | None,
    start_capital: float,
    generate_html_report: bool,
    reports_dir: str,
    worker_log_file: str,
    span_tolerance_days: float,
    commission_bps: float = 0.0,
    spread_bps_by_asset_class: dict | None = None,
) -> dict:
    """
    Isolierter Worker-Prozess (1 Instrument × 1 Strategie).
    """
    def wlog(msg: str) -> None:
        with open(worker_log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def wlog_err(msg: str, exc: bool = False) -> None:
        full = f"[ERROR] {msg}"
        if exc:
            full += f"\n{traceback.format_exc()}"
        wlog(full)

    strategy_class_name = strat.get("strategy_class", "UnknownStrategy")
    module_name         = strat.get("strategy_module")
    config_class_name   = strat.get("config_class")

    if not module_name or not config_class_name:
        msg = f"Fehlende Metadaten ('strategy_module' oder 'config_class') für Strategie {strategy_class_name}."
        wlog_err(msg)
        return _empty_result(inst_id_str, strategy_class_name, strat, start_capital)

    wlog(f"\n🚀 {inst_id_str} | {strategy_class_name}")

    temp_catalog_dir = None
    try:
        # --- Ticks laden (mit Schema Injection falls nötig) ---
        try:
            temp_catalog_dir = _get_normalized_catalog_path(catalog_path, inst_id_str)
            effective_catalog_path = temp_catalog_dir if temp_catalog_dir else catalog_path

            catalog = ParquetDataCatalog(effective_catalog_path)

            # Determine asset class for spread
            spread_bps = 0.0
            if spread_bps_by_asset_class:
                import json
                instrument_map_path = str(config_dir() / "instrument_map.json")
                asset_class_key = "DEFAULT"
                try:
                    with open(instrument_map_path, "r", encoding="utf-8") as f:
                        inst_map = json.load(f).get("instruments", {})

                    # find the asset class by matching the symbol
                    for _, inst_data in inst_map.items():
                        if inst_data.get("symbol") == inst_id_str:
                            asset_class_key = inst_data.get("asset_class", "DEFAULT").upper()
                            break
                except Exception as e:
                    pass

                spread_bps = spread_bps_by_asset_class.get(asset_class_key, spread_bps_by_asset_class.get("DEFAULT", 4.0))

            if spread_bps > 0.0:
                wlog(f"   📊 Spread-Modeling: {spread_bps} bps applied to {inst_id_str}")

            ticks = load_ticks_from_catalog(catalog, inst_id_str, start_ns, end_ns, spread_bps)
        except RuntimeError as e:
            wlog_err(f"Tick-Ladefehler: {e}", exc=True)
            return _empty_result(inst_id_str, strategy_class_name, strat)

        if not ticks:
            wlog(f"   ⚠️ 0 Ticks im Zeitraum — überspringe.")
            return _empty_result(inst_id_str, strategy_class_name, strat)

        first_tick_ts = ticks[0].ts_event
        # Falls isinstance(ts_event, pd.Timestamp) oder int (pandas fallback)
        first_tick_ns_val = first_tick_ts.value if hasattr(first_tick_ts, 'value') else int(first_tick_ts)
        wlog(f"   📥 {len(ticks)} Ticks geladen. Erster Tick im Engine: {first_tick_ns_val} ({pd.Timestamp(first_tick_ns_val, unit='ns', tz='UTC').strftime('%Y-%m-%d %H:%M:%S')})")
        # Issue #403: Enddatum + Gesamtdauer explizit loggen (vorher nur erster Tick sichtbar).
        last_tick_ts = ticks[-1].ts_event
        last_tick_ns_val = last_tick_ts.value if hasattr(last_tick_ts, 'value') else int(last_tick_ts)
        wlog("   " + _format_backtest_window(first_tick_ns_val, last_tick_ns_val))

        # --- Check Data Span for Walk-Forward Window ---
        required_days = strat.get("_walk_forward_days")
        if strat.get("_walk_forward_dict"):
            wfd = strat["_walk_forward_dict"]
            required_days = wfd.get("is_window_days", 90) + (wfd.get("splits", 2) * wfd.get("oos_window_days", 30))
        if required_days:
            is_sufficient, span_days, _ = check_data_span(ticks, required_days, span_tolerance_days)
            if not is_sufficient:
                from automation.log_manager import emit_execution_event as emit_json_event
                import logging
                log = logging.getLogger("backtest_worker")
                emit_json_event(log, "WALK_FORWARD_INSUFFICIENT_DATA", {
                    "symbol": inst_id_str,
                    "required_days": required_days,
                    "actual_days": round(span_days, 1)
                })
                msg = f"INSUFFICIENT DATA: Datenspanne beträgt nur {span_days:.1f} Tage (benötigt: ~{required_days} Tage, Toleranz: {span_tolerance_days} Tage). Überspringe Backtest."
                wlog_err(msg)
                res = _empty_result(inst_id_str, strategy_class_name, strat)
                res["error"] = "insufficient_data"
                return res
            elif span_days < required_days:
                wlog(f"   ⚠️ Knappe Datenspanne, fahre fort: {span_days:.1f} Tage (benötigt: {required_days} Tage, Defizit von {required_days - span_days:.1f} Tagen liegt innerhalb der Toleranz von {span_tolerance_days} Tagen).")

        # --- Engine-Setup ---
        try:
            # Task 3: price_precision aus Ticks, size_precision aus Parquet-Metadaten
            price_precision = infer_precision_from_ticks(ticks)
            pp_parquet, sp_parquet = read_precisions_from_parquet(catalog_path, inst_id_str)
            # Verwende Parquet-Precision als Fallback wenn Ticks keine Precision liefern
            if price_precision == 2 and pp_parquet != 2:
                price_precision = pp_parquet

            # Normalize size precision for both ticks AND instrument to prevent mismatch
            size_precision = _normalize_size_precision(sp_parquet, inst_id_str)

            wlog(
                f"   🔬 Precisions: price={price_precision} (ticks), "
                f"size={size_precision} (parquet meta, normalized)"
            )

            engine_config = BacktestEngineConfig(
                trader_id=f"BT-{inst_id_str.replace('.', '_')}-{strategy_class_name}"
            )
            engine = BacktestEngine(config=engine_config)

            # Task 4: Spread-Modeling — NautilusTrader füllt Buy@Ask, Sell@Bid per Default
            engine.add_venue(
                venue=Venue("ETORO"),
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                base_currency=USD,
                starting_balances=[Money(start_capital, USD)],
            )

            # Task 3 Fix: Mock-Instrument mit korrekter size_precision aus Parquet-Metadaten
            mock_inst = create_mock_instrument(
                inst_id_str, price_precision, size_precision=size_precision
            )
            engine.add_instrument(mock_inst)
            engine.add_data(ticks)

        except Exception as e:
            wlog_err(f"Engine-Setup fehlgeschlagen: {e}", exc=True)
            return _empty_result(inst_id_str, strategy_class_name, strat, start_capital)

        # --- Strategie konfigurieren ---
        try:
            module    = importlib.import_module(module_name)
            StratCls  = getattr(module, strategy_class_name)
            ConfigCls = getattr(module, config_class_name)

            params = strat.get("params", {}).copy()
            params["instrument_id"] = inst_id_str
            params["bar_type"]      = bar_type

            # Härtung: Defensives Parsing der Parameter
            if hasattr(ConfigCls, "__struct_fields__"):
                valid_keys = set(ConfigCls.__struct_fields__)
            elif hasattr(ConfigCls, "__dataclass_fields__"):
                valid_keys = set(ConfigCls.__dataclass_fields__)
            else:
                valid_keys = set(inspect.signature(ConfigCls).parameters)

            dropped = {k for k in params if k not in valid_keys}
            if dropped:
                wlog(f"   ⚠️ Unbekannte Strategie-Params ignoriert: {sorted(dropped)}")
                # Auch über das result dict zurückgeben (falls orchestrator das auswertet)
                strat["_dropped_params"] = list(dropped)
            params = {k: v for k, v in params.items() if k in valid_keys}

            # Fix: trade_amount_usd auf 15% des Startkapitals setzen
            try:
                test_params = params.copy()
                test_params["trade_amount_usd"] = 1500.0
                config = ConfigCls(**test_params)
                params["trade_amount_usd"] = max(500.0, start_capital * 0.15)
            except Exception:
                pass

            config = ConfigCls(**params)
            strategy = StratCls(config=config)
            engine.add_strategy(strategy)
        except Exception as e:
            wlog_err(f"Strategie-Setup fehlgeschlagen: {e}", exc=True)
            return _empty_result(inst_id_str, strategy_class_name, strat, start_capital)

        # --- Backtest ausführen ---
        try:
            mtm_monitor = PortfolioMonitor(bar_type)
            engine.add_actor(mtm_monitor)

            engine.run()
        except RuntimeError as e:
            wlog_err(f"Backtest RuntimeError (wahrscheinlich Precision Mismatch): {e}")
            engine.dispose()
            return _empty_result(inst_id_str, strategy_class_name, strat, start_capital)
        except Exception as e:
            wlog_err(f"Backtest gecrasht: {e}", exc=True)
            engine.dispose()
            return _empty_result(inst_id_str, strategy_class_name, strat, start_capital)

        # --- Metriken extrahieren ---
        walk_forward_dict = strat.get("_walk_forward_dict", None)
        try:
            extracted_data = extract_metrics(engine, start_capital, log_fn=wlog, walk_forward_dict=walk_forward_dict, start_ns=start_ns, commission_bps=commission_bps, mtm_series=mtm_monitor.get_equity_series())
        except Exception as e:
            wlog_err(f"Metrik-Extraktion fehlgeschlagen: {e}", exc=True)
            NULL = {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "sortino_ratio": 0.0, "calmar_ratio": 0.0,
                "max_drawdown": 0.0, "total_return": 0.0,
                "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
                "losses_count": 0,
                "median_position_notional": 0.0,
            }
            if walk_forward_dict is not None and start_ns is not None:
                extracted_data = {"metrics": NULL, "oos_metrics": NULL}
            else:
                extracted_data = NULL

        # If extraction failed, extract_metrics returns NULL dictionary instead of nested dict
        if "metrics" in extracted_data:
            metrics = extracted_data["metrics"]
            oos_metrics = extracted_data.get("oos_metrics", {})
        else:
            metrics = extracted_data
            oos_metrics = {}

        # Issue #444 — beobachtete Fill-ts-Spanne aus extract_metrics nach oben reichen (für den
        # data_window-Block der tournament_result.json). None, wenn keine Round-Trips/kein WF-Modus.
        fill_ts_min = extracted_data.get("_fill_ts_min") if isinstance(extracted_data, dict) else None
        fill_ts_max = extracted_data.get("_fill_ts_max") if isinstance(extracted_data, dict) else None
        # Issue #455 — OOS-Abdeckungs-Telemetrie analog nach oben reichen.
        oos_window_start_ns = extracted_data.get("_oos_window_start_ns") if isinstance(extracted_data, dict) else None
        oos_covered = extracted_data.get("_oos_covered") if isinstance(extracted_data, dict) else None

        def format_metric(m_dict, key, min_trades_req):
            if m_dict.get('total_trades', 0) < min_trades_req:
                return f"{'n/a(<min)':>6}"
            val = m_dict.get(key)
            if val is not None:
                return f"{val:>6.2f}"
            if m_dict.get('losses_count', 0) == 0 or m_dict.get('max_drawdown', 0.0) == 0.0:
                return f"{'n/a(win)':>6}"
            return f"{'n/a':>6}"

        wlog(
            f"   📊 [IS]  Trades={metrics.get('total_trades', 0):>4} | "
            f"WinRate={metrics.get('win_rate', 0.0):>6.1%} | "
            f"PF={format_metric(metrics, 'profit_factor', 2)} | "
            f"Sortino={format_metric(metrics, 'sortino_ratio', 5)} | "
            f"Return={metrics.get('total_return', 0.0):>6.2f}%"
        )
        walk_forward_dict = strat.get("_walk_forward_dict", None)
        if walk_forward_dict is not None and start_ns is not None:
            oos_span_days = strat.get("_oos_span_days", 0)
            if oos_metrics and isinstance(oos_metrics, dict):
                oos_metrics["oos_span_days"] = oos_span_days
            wlog(
                f"   📊 [OOS] Trades={oos_metrics.get('total_trades', 0):>4} | "
                f"WinRate={(oos_metrics.get('win_rate') or 0.0):>6.1%} | "
                f"PF={format_metric(oos_metrics, 'profit_factor', 2)} | "
                f"Sortino={format_metric(oos_metrics, 'sortino_ratio', 5)} | "
                f"Return={(oos_metrics.get('total_return') or 0.0):>6.2f}%"
            )

        # --- Optional: HTML Tearsheet ---
        if generate_html_report and metrics.get("total_trades", 0) > 0:
            try:
                name = inst_id_str.replace('.', '_')
                run_ts = datetime.now().strftime('%Y%m%d_%H%M%S')

                # Use specific output path according to Task 5
                report_path = os.path.join(
                    reports_dir,
                    f"{name}_{strategy_class_name}_{run_ts}.html"
                )
                create_tearsheet(
                    engine.trader,
                    title=f"Backtest {inst_id_str} | {strategy_class_name}",
                    output_file=report_path
                )
                wlog(f"   📈 HTML-Report: {report_path}")
            except Exception as e:
                wlog_err(f"HTML-Generierung fehlgeschlagen: {e}", exc=True)
                try:
                    fills_df = engine.trader.generate_fills_report()
                    if not fills_df.empty:
                        fills_df.to_csv(
                            os.path.join(
                                reports_dir,
                                f"{name}_{inst_id_str}_{strategy_class_name}_{run_ts}.csv"
                            ))
                    wlog("   ✅ CSV gespeichert.")
                except Exception as fe:
                    wlog_err(f"CSV-Fallback fehlgeschlagen: {fe}", exc=True)

        engine.dispose()
        return {
            "symbol": inst_id_str,
            "strategy": strategy_class_name,
            "metrics": metrics,
            "oos_metrics": oos_metrics,
            "strat_params": strat.get("params", {}),
            "start_capital": start_capital,
            "_first_tick_ns": first_tick_ns_val,
            # Issue #444 — tatsächlich geladenes Fenster + beobachtete Fill-ts-Spanne pro Worker;
            # write_tournament_json aggregiert daraus den data_window-Block.
            "_last_tick_ns": last_tick_ns_val,
            "_fill_ts_min": fill_ts_min,
            "_fill_ts_max": fill_ts_max,
            # Issue #455 — OOS-Abdeckungs-Grenze + Coverage-Flag pro Worker.
            "_oos_window_start_ns": oos_window_start_ns,
            "_oos_covered": oos_covered,
        }
    finally:
        if temp_catalog_dir and os.path.exists(temp_catalog_dir):
            import shutil
            shutil.rmtree(temp_catalog_dir)


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------


def run_backtest() -> None:
    parser = argparse.ArgumentParser(description="NautilusTrader Backtesting Engine")
    parser.add_argument("--momentum",     action="store_true")

    parser.add_argument("--single-symbol", type=str, help="Nur ein Symbol backtesten (z.B. AERO.ETORO)")
    parser.add_argument("--strategy", type=str, help="Nur eine Strategie testen (z.B. VwapExhaustionStrategy)")

    parser.add_argument("--htmlreport",   action="store_true")
    parser.add_argument("--catalog-path", type=str, default=None)
    parser.add_argument("--config",       type=str, default=None)
    parser.add_argument("--output",       type=str, default=None)
    parser.add_argument("--dry-run",      action="store_true",
                        help="Config-Validierung ohne echten Backtest-Run.")
    args = parser.parse_args()

    _script_dir   = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    logs_dir_path = logs_dir()
    logs_dir_str = str(logs_dir_path)
    os.makedirs(logs_dir_str, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file  = os.path.join(logs_dir_str, f"backtest_{timestamp}.log")
    error_log_file = os.path.join(logs_dir_str, f"errors_{timestamp}.log")

    sys.stdout = DualLogger(log_file)
    sys.stderr = DualLogger(log_file)
    print(f"📝 Log-Datei: {log_file}")
    print(f"🚨 Fehler-Log: {error_log_file}")
    print("=" * 70)

    def log_error(msg: str, exc: bool = False) -> None:
        print(msg)
        with open(error_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            if exc:
                f.write(traceback.format_exc() + "\n")

    # --- backtest.json (Task 4: Spread-Modeling-Flag) ---
    backtest_global_cfg = {}
    _bt_cfg_path = str(config_dir() / "backtest.json")
    if os.path.exists(_bt_cfg_path):
        try:
            with open(_bt_cfg_path, "r", encoding="utf-8") as _f:
                backtest_global_cfg = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}
        except Exception as _e:
            print(f"  ⚠️  backtest.json Ladefehler: {_e}")
    spread_modeling = backtest_global_cfg.get("spread_modeling", True)
    fill_model_str  = backtest_global_cfg.get("fill_model", "bid_ask")
    span_tolerance_days = backtest_global_cfg.get("span_tolerance_days", 3.0)
    commission_bps = backtest_global_cfg.get("commission_bps", 0.0)
    spread_bps_by_asset_class = backtest_global_cfg.get("spread_bps_by_asset_class", {})
    print(f"📊 Spread-Modeling: {spread_modeling} (fill_model={fill_model_str}), Span-Tolerance: {span_tolerance_days}d")
    if spread_modeling:
        print("   ℹ️  Buy-Orders → Ask-Preis | Sell-Orders → Bid-Preis (NautilusTrader Default)")
    else:
        print("   ℹ️  Spread-Modeling deaktiviert (Kompatibilitätsmodus)")

    # --- Tournament-Config (Task 5) ---
    tournament_cfg = load_tournament_config(_project_root)
    req_all = tournament_cfg.get('eligible_requires_all', [])
    req_any = tournament_cfg.get('eligible_requires_any', [])

    print("🏆 Tournament Configuration:")
    print(f"   [ALL REQUIRED] {req_all}")
    for k in req_all:
        print(f"      • {k}: {tournament_cfg.get(k, 'N/A')}")

    print(f"   [ANY REQUIRED] {req_any}")
    for k in req_any:
        print(f"      • {k}: {tournament_cfg.get(k, 'N/A')}")

    if "k_shrinkage" in tournament_cfg:
        print(f"   [SHRINKAGE FACTOR] {tournament_cfg.get('k_shrinkage')}")

    if "oos_min_trades" in tournament_cfg or "oos_min_total_return" in tournament_cfg or "oos_min_expectancy" in tournament_cfg:
        print(f"   [OOS DEFAULTS] min_trades: {tournament_cfg.get('oos_min_trades')}, min_return: {tournament_cfg.get('oos_min_total_return')}, min_expectancy: {tournament_cfg.get('oos_min_expectancy')}")

    # --- Strategie-Defaults laden (Task 2) ---
    strategy_defaults = load_strategy_defaults(_project_root)
    if strategy_defaults:
        print(f"⚙️  Strategy-Defaults geladen für: {', '.join(strategy_defaults.keys())}")

    # --- Config ---
    config_path = args.config or os.path.join(_script_dir, "backtesting_config.json")
    if not os.path.exists(config_path):
        log_error(f"❌ Config nicht gefunden: {config_path}")
        return

    config_data     = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    strategies_list = config_data.get("strategies", [])

    # Task 6: Nur aktive Strategien berücksichtigen
    active_before = len(strategies_list)
    strategies_list = [s for s in strategies_list if s.get("active", True) is not False]
    if len(strategies_list) < active_before:
        print(f"⚙️  {active_before - len(strategies_list)} inaktive Strategie(n) übersprungen.")

    if not strategies_list:
        log_error("⚠️ Keine aktiven Strategien in Config gefunden.")
        return

    # Assert consistency between defaults and active strategies
    loaded_defaults = [k for k in strategy_defaults.keys() if not k.startswith("_")]
    active_classes = {s.get("strategy_class") for s in strategies_list}
    missing_defaults = active_classes - set(loaded_defaults)
    assert not missing_defaults, f"Mismatch: Aktive Strategien {missing_defaults} fehlen in strategy_defaults.json."

    orphaned_defaults = set(loaded_defaults) - active_classes
    if orphaned_defaults:
        print(f"ℹ️ {len(orphaned_defaults)} Defaults ignoriert (Strategien inaktiv): {list(orphaned_defaults)}")

    # Task 2: Strategy-Defaults auf die Strategie-Params anwenden (Overrides behalten Vorrang)
    is_manifest = config_data.get("manifest_version") is not None
    strategies_list = apply_strategy_defaults(strategies_list, strategy_defaults, is_manifest=is_manifest)
    if strategy_defaults:
        for strat in strategies_list:
            cls_name = strat.get("strategy_class", "?")
            params_str = ", ".join(f"{k}={v}" for k, v in (strat.get("params") or {}).items())
            print(f"✅ Defaults angewandt — {cls_name}: {params_str}")

    # --- Parameter-Validierung & Walk-Forward Injektion ---
    param_warnings: list[str] = []
    # ISSUE-OPT-374: the self-describing manifest (global_settings.walk_forward) is the
    # authoritative source; fall back to the trial's backtest.json side-channel only if absent.
    _wf_manifest = global_settings.get("walk_forward")
    walk_forward_cfg = _wf_manifest or backtest_global_cfg.get("walk_forward")

    if walk_forward_cfg:
        _wf_source = "manifest (global_settings)" if _wf_manifest else "backtest.json (side-channel)"
        is_days  = walk_forward_cfg.get("is_window_days", 90)
        oos_days = walk_forward_cfg.get("oos_window_days", 30)
        splits   = walk_forward_cfg.get("splits", 2)
        required_days = is_days + (splits * oos_days)
        _span_tol = span_tolerance_days
        print(f"   • Walk-Forward Quelle: {_wf_source}")
        print(f"   • Effective Data Span Required: {required_days - _span_tol:.1f} days (Required: {required_days}, Max Allowed Deficit: {_span_tol})")

    for strat in strategies_list:
        param_warnings.extend(validate_strategy_params(strat))

        # Einmalige Injektion von _walk_forward_days für den Guard (Issue #121)
        if walk_forward_cfg:
            is_days  = walk_forward_cfg.get("is_window_days", 90)
            oos_days = walk_forward_cfg.get("oos_window_days", 30)
            splits   = walk_forward_cfg.get("splits", 2)
            strat["_walk_forward_days"] = is_days + (splits * oos_days)

    if param_warnings:
        print("\n⚠️  Parameter-Warnungen:")
        for w in param_warnings:
            print(f"   • {w}")
        print()

    # --- Zeitraum ---
    start_time_str = global_settings.get("start_time")
    end_time_str   = global_settings.get("end_time")
    bt_start = pd.Timestamp(start_time_str, tz="UTC") if start_time_str else None
    bt_end   = pd.Timestamp(end_time_str,   tz="UTC") if end_time_str   else None

    start_ns = ts_to_ns(bt_start)
    end_ns   = ts_to_ns(bt_end)

    if bt_start and bt_end:
        print(f"📅 Zeitraum: {bt_start.date()} → {bt_end.date()}")

    # ISSUE-OPT-374: prefer the self-describing manifest, fall back to backtest.json.
    start_capital = global_settings.get("start_capital")
    if start_capital is None:
        start_capital = backtest_global_cfg.get("start_capital", 100_000.0)
    catalog_path = args.catalog_path or global_settings.get("catalog_path", "./data/nautilus")

    # --- Dry-Run: Zeige Konfiguration und exit (Task 2 Acceptance Criterion) ---
    if getattr(args, "dry_run", False):
        print("\n🔍 DRY-RUN: Strategie-Konfiguration nach Defaults-Merge:")
        for strat in strategies_list:
            cls  = strat.get("strategy_class", "?")
            prms = strat.get("params", {})
            print(f"   {cls}: {json.dumps(prms, ensure_ascii=False)}")
        print("\n✅ Dry-Run abgeschlossen (kein Backtest gestartet).")
        return

    expected_data_dir = os.path.join(catalog_path, "data")
    os.makedirs(expected_data_dir, exist_ok=True)

    # --- Filter (CLI Flags) ---
    if args.strategy:
        strategies_list = [s for s in strategies_list if s.get("strategy_class") == args.strategy]
        print(f"🎯 CLI Filter aktiv: Nur Strategie '{args.strategy}' wird getestet.")
        if not strategies_list:
            log_error(f"⚠️ Strategie '{args.strategy}' in Config nicht gefunden oder inaktiv.")
            return

    # --- Instrumente ---
    instrument_ids = discover_instruments_from_catalog(catalog_path)
    # A4.2: manifest-getriebene Universum-Restriktion (global_settings.instruments). Falsy ⇒ volles Universum.
    _instruments_filter = global_settings.get("instruments")
    if _instruments_filter:
        _before = len(instrument_ids)
        instrument_ids = restrict_universe(instrument_ids, _instruments_filter)
        print(f"🎯 Manifest-Filter global_settings.instruments aktiv: {_before} → {len(instrument_ids)} Symbol(e) {sorted(_instruments_filter)}")
    if args.single_symbol:
        if args.single_symbol in instrument_ids:
            instrument_ids = [args.single_symbol]
            print(f"🎯 CLI Filter aktiv: Nur Symbol '{args.single_symbol}' wird getestet.")
        else:
            log_error(f"⚠️ Symbol '{args.single_symbol}' nicht im Catalog {expected_data_dir}/quote_tick gefunden.")
            return
    elif not instrument_ids:
        log_error(f"⚠️ Keine Instrumente in {expected_data_dir}/quote_tick vorhanden.")
        return
    print(f"📋 {len(instrument_ids)} Instrumente gefunden.")

    # --- Issue #148: Data Start Alignment (Pre-flight Check) ---
    print("\n🔍 Analysiere Startdaten der Instrumente (Kohorten-Analyse)...")
    instrument_start_dates = {}
    valid_instrument_ids = []
    max_valid_start_ns = 0

    required_days = 0
    if walk_forward_cfg:
        is_days  = walk_forward_cfg.get("is_window_days", 90)
        oos_days = walk_forward_cfg.get("oos_window_days", 30)
        splits   = walk_forward_cfg.get("splits", 2)
        required_days = is_days + (splits * oos_days)

    import pyarrow.parquet as pq
    from pathlib import Path

    cohorts: dict[str, list[str]] = {}

    for iid in instrument_ids:
        parquet_file = Path(catalog_path) / "data" / "quote_tick" / iid / "data.parquet"
        if not parquet_file.exists():
            continue
        try:
            pf = pq.ParquetFile(str(parquet_file))
            if "ts_event" in pf.schema.names:
                ts_index = pf.schema.names.index("ts_event")
                # O(1) Zugriff auf die Metadaten-Statistiken der ersten Row Group
                oldest_ts = int(pf.metadata.row_group(0).column(ts_index).statistics.min)
                instrument_start_dates[iid] = oldest_ts
                dt_str = pd.Timestamp(oldest_ts, unit="ns", tz="UTC").strftime("%Y-%m-%d")
                cohorts.setdefault(dt_str, []).append(iid)
            else:
                continue
        except Exception:
            continue

    print("   📊 Identifizierte Startdatum-Kohorten:")
    for dt_str, syms in sorted(cohorts.items()):
        print(f"      • {dt_str}: {len(syms)} Symbole")

    if end_ns and required_days > 0:
        required_ns = required_days * 86400 * 1_000_000_000
        for iid in instrument_ids:
            oldest_ts = instrument_start_dates.get(iid)
            if oldest_ts is None:
                continue

            # Drop late-starting symbols if they don't fulfill the absolute minimum window
            if (end_ns - oldest_ts) < required_ns:
                print(f"   ⚠️ Drop (Spätstarter): {iid} hat unzureichend Daten (Start: {pd.Timestamp(oldest_ts, unit='ns', tz='UTC').strftime('%Y-%m-%d')})")
            else:
                valid_instrument_ids.append(iid)
                if oldest_ts > max_valid_start_ns:
                    max_valid_start_ns = oldest_ts
    else:
        valid_instrument_ids = instrument_ids
        if instrument_start_dates:
            max_valid_start_ns = max(instrument_start_dates.values())

    instrument_ids = valid_instrument_ids
    if not instrument_ids:
        log_error("⚠️ Keine Instrumente mit ausreichend Daten nach Startdatum-Analyse übrig.")
        return

    # Align common start ns
    common_start_ns = max_valid_start_ns
    if start_ns is None or common_start_ns > start_ns:
        start_ns = common_start_ns
        print(f"   ✅ Einheitliches Backtest-Startdatum auf spätestes gültiges Datum gesetzt: {pd.Timestamp(start_ns, unit='ns', tz='UTC').strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print(f"   ✅ Konfiguriertes Startdatum {pd.Timestamp(start_ns, unit='ns', tz='UTC').strftime('%Y-%m-%d %H:%M:%S')} deckt alle Instrumente ab.")

    # --- Metadaten-Normalisierung ---
    print("\n🔍 Prüfe Parquet-Schema-Konsistenz...")
    patched = sum(
        1 for iid in instrument_ids
        if normalize_parquet_metadata(catalog_path, iid)
    )
    print(
        f"  ✅ {patched} Instrument(e) gepatcht."
        if patched else
        "  ✅ Alle Schemas konsistent."
    )

    # --- Mock-Instrumente registrieren (Task 3: korrekte Precisions aus Parquet-Metadaten) ---
    catalog = ParquetDataCatalog(catalog_path)
    dummy_instruments = [
        create_mock_instrument(
            iid,
            *read_precisions_from_parquet(catalog_path, iid),
        )
        for iid in instrument_ids
    ]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            catalog.write_data(dummy_instruments)
        except Exception:
            pass

    dynamic_instruments = [
        {"id": iid, "bar_type": f"{iid}-1-HOUR-MID-INTERNAL"}
        for iid in instrument_ids
    ]

    reports_dir = os.path.join(_project_root, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    tournament_output = args.output or os.path.join(
        _project_root, "logs", f"tournament_{datetime.now().strftime('%Y-%m-%d')}.json"
    )

    total_jobs = len(dynamic_instruments) * len(strategies_list)
    print(
        f"\n⏳ Starte {total_jobs} Backtest-Jobs "
        f"({len(dynamic_instruments)} × {len(strategies_list)} Strategien)..."
    )

    # --- Multiprocessing ---
    _use_mp = True
    _max_workers = max(1, min((os.cpu_count() or 1) // 2, 6))
    executor = None
    futures: dict = {}
    all_results: list[dict] = []

    try:
        if _use_mp:
            if sys.version_info >= (3, 11):
                executor = ProcessPoolExecutor(max_workers=_max_workers, max_tasks_per_child=1)
            else:
                executor = ProcessPoolExecutor(max_workers=_max_workers)

        for inst in dynamic_instruments:
            inst_id_str = inst["id"]
            bar_type    = inst["bar_type"]

            for strat in strategies_list:
                safe_strat_class = strat.get("strategy_class", "UnknownStrategy")
                # A4.8: legacy/Matrix per-symbol Override-Auflösung an der Call-Site. Nur der
                # Nicht-Manifest-Pfad und nur, wenn für DIESES Symbol ein Override existiert ⇒
                # sonst bit-identische Params wie heute (HI-2). reine Funktion aus A4.1.
                if not is_manifest and (strat.get("instrument_overrides") or {}).get(inst_id_str):
                    strat = {**strat, "params": resolve_strategy_params(
                        strat, {}, is_manifest=False, instrument=inst_id_str)}
                    self_log = f"Applying micro-tuning override for {inst_id_str} / {safe_strat_class}"
                    print(f"   🎯 {self_log}")
                # ISSUE-OPT-374: reuse the manifest-authoritative walk_forward_cfg resolved above
                # (manifest global_settings, else backtest.json fallback) for fold injection.
                if walk_forward_cfg and end_ns:
                    oos_days = walk_forward_cfg.get("oos_window_days", 30)
                    splits   = walk_forward_cfg.get("splits", 2)
                    span_days = splits * oos_days
                    strat["_walk_forward_dict"] = walk_forward_cfg
                    strat["_oos_span_days"]     = span_days

                wlf = os.path.join(
                    logs_dir_str,
                    f"worker_{inst_id_str.replace('.', '_')}"
                    f"_{safe_strat_class}_{timestamp}.log"
                )
                _worker_log_files.append(wlf)

                if _use_mp and executor is not None:
                    future = executor.submit(
                        run_single_backtest_worker,
                        inst_id_str, bar_type, strat,
                        catalog_path, start_ns, end_ns,
                        start_capital, args.htmlreport, reports_dir, wlf,
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class
                    )
                    futures[future] = (inst_id_str, strat["strategy_class"], wlf)
                else:
                    result = run_single_backtest_worker(
                        inst_id_str, bar_type, strat,
                        catalog_path, start_ns, end_ns,
                        start_capital, args.htmlreport, reports_dir, wlf,
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class
                    )
                    _flush_worker_log(wlf)
                    if result and result.get("metrics"):
                        all_results.append(result)

        if _use_mp and executor is not None:
            done_count = 0
            for future in as_completed(futures):
                inst_id_str, strat_name, wlf = futures[future]
                done_count += 1
                _flush_worker_log(wlf)

                try:
                    result = future.result()
                    if result and result.get("metrics"):
                        all_results.append(result)
                except (ImportError, SyntaxError, NameError, TypeError, AttributeError, ValueError) as e:
                    log_error(f"🚨 FATAL: Systemischer Python-Fehler in Worker {inst_id_str}/{strat_name}: {e}", exc=True)
                    log_error("Backtest wird hart abgebrochen, um fehlerhaftes Live-Deployment zu verhindern (Fail-Fast).")
                    if executor is not None:
                        executor.shutdown(wait=False, cancel_futures=True)
                    sys.exit(1)
                except _BrokenPool:
                    log_error(
                        f"💥 Worker-Pool abgestürzt bei {inst_id_str}/{strat_name}. "
                        "Falle auf sequenziellen Modus zurück."
                    )
                    _use_mp = False
                    _run_remaining_sequentially(
                        futures, future, strategies_list, catalog_path,
                        start_ns, end_ns, start_capital, args.htmlreport,
                        reports_dir, all_results, done_count, total_jobs,
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class
                    )
                    break
                except Exception as e:
                    log_error(f"❌ Worker {inst_id_str}/{strat_name}: {e}", exc=True)

                print(f"   [{done_count:>4}/{total_jobs}] {inst_id_str} / {strat_name}")

            executor.shutdown(wait=True)

    except KeyboardInterrupt:
        print("\n🛑 Backtest manuell abgebrochen. Fahre Subprozesse herunter...")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(1)
    finally:
        _cleanup_worker_logs()

    # --- Tournament (Task 5: robuste Multi-Kriterien-Selektion) ---
    if args.momentum and all_results:
        per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count = select_winners(all_results, tournament_cfg, start_ns=start_ns)
        winner_count, no_winner_symbols = print_tournament_table(
            all_results, per_symbol_winners, tournament_cfg
        )
        total_symbols = len(set(r["symbol"] for r in all_results))
        print(
            f"\n✅ Tournament: {total_symbols} Symbole | "
            f"{is_eligible_count} IS-taugliche Paare | {fully_eligible_count} voll taugliche Paare (IS+OOS) | {winner_count} Gewinner-Symbole"
        )
        if aggregate_winner:
            print(
                f"🏆 {aggregate_winner['strategy']} — "
                f"{aggregate_winner['win_count']} Wins, "
                f"Median IS Sortino: {aggregate_winner['median_is_sortino']}"
            )
        if no_winner_symbols:
            print(f"⚠️  Ohne eindeutigen Gewinner: {', '.join(no_winner_symbols)}")
        write_tournament_json(
            all_results,
            tournament_output,
            per_symbol_winners,
            aggregate_winner,
            warnings_list,
            is_eligible_count,
            fully_eligible_count,
            tournament_cfg=tournament_cfg,
            start_ns=start_ns,  # Issue #444 — re-anchored Fenster für den data_window-Block
            end_ns=end_ns,
        )
    elif all_results:
        print(f"\n📊 {len(all_results)} Ergebnisse gesammelt (kein --momentum Flag aktiv)")

    print("\n✅ Matrix-Backtest vollständig abgeschlossen!")


def _flush_worker_log(worker_log_file: str) -> None:
    if os.path.exists(worker_log_file):
        try:
            with open(worker_log_file, "r", encoding="utf-8") as wf:
                content = wf.read().strip()
            if content:
                print(content)
            os.remove(worker_log_file)
        except OSError:
            pass
    if worker_log_file in _worker_log_files:
        _worker_log_files.remove(worker_log_file)


def _run_remaining_sequentially(
    futures: dict,
    failed_future,
    strategies_list: list,
    catalog_path: str,
    start_ns: int | None,
    end_ns: int | None,
    start_capital: float,
    generate_html: bool,
    reports_dir: str,
    all_results: list,
    done_count: int,
    total_jobs: int,
    span_tolerance_days: float,
    commission_bps: float = 0.0,
    spread_bps_by_asset_class: dict | None = None,
) -> None:
    remaining = {
        f: v for f, v in futures.items()
        if not f.done() and f is not failed_future
    }
    for _, (rem_inst, rem_strat_name, rem_log) in remaining.items():
        rem_strat = next(
            (s for s in strategies_list if s.get("strategy_class", "UnknownStrategy") == rem_strat_name), None
        )
        if rem_strat is None:
            continue
        bar_type = f"{rem_inst}-1-HOUR-MID-INTERNAL"
        res = run_single_backtest_worker(
            rem_inst, bar_type, rem_strat, catalog_path,
            start_ns, end_ns, start_capital, generate_html, reports_dir, rem_log,
            span_tolerance_days, commission_bps, spread_bps_by_asset_class
        )
        _flush_worker_log(rem_log)
        done_count += 1
        print(f"   [{done_count:>4}/{total_jobs}] (seq) {rem_inst} / {rem_strat_name}")
        if res and res.get("metrics"):
            all_results.append(res)


def run_backtest_inprocess(manifest_path, output_path):
    """A4.9: importierbarer, In-Process-Backtest-Entry (kein Subprozess-Spawn).

    Führt denselben Matrix-/Tournament-Flow wie das CLI aus, indem `run_backtest()` mit einem
    konstruierten `argv` aufgerufen wird (kein `python automation/backtest_runner.py`-Spawn +
    Import pro Trial mehr). Fachliche Fehler werden als Exceptions geworfen (der Aufrufer wandelt
    sie in `optuna.TrialPruned`), fundamentale Fehler (ImportError) propagieren (Fail-Fast).

    Trade-off (siehe Kap. 16): Die Fault-Isolation des *äußeren* Prozesses entfällt; globaler
    Modul-State im Hauptprozess wird je Aufruf von `run_backtest()` neu initialisiert. Die
    eigentlichen Per-(Symbol,Strategie)-Backtests laufen weiterhin in einem internen
    `ProcessPoolExecutor` (frische Worker je Job) — Per-Job-State-Isolation bleibt also erhalten.
    """
    from pathlib import Path as _Path
    manifest = json.loads(_Path(manifest_path).read_text("utf-8"))
    catalog_path = manifest.get("global_settings", {}).get("catalog_path")
    if not catalog_path:
        raise ValueError("Missing catalog_path in manifest global_settings")

    argv = [
        "backtest_runner.py", "--momentum",
        "--catalog-path", str(catalog_path),
        "--config", str(manifest_path),
        "--output", str(output_path),
    ]
    _old_argv = sys.argv
    try:
        sys.argv = argv
        run_backtest()
    finally:
        sys.argv = _old_argv

    out = _Path(output_path)
    if not out.exists():
        raise RuntimeError(f"In-process backtest produced no output: {out}")
    return out


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()
