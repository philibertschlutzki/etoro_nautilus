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


def load_strategy_defaults(project_root: str | None = None) -> dict:
    """Lädt strategy_defaults.json aus automation/config/.

    Args:
        project_root: Projekt-Root-Pfad. Wenn None, wird auto-detektiert.

    Returns:
        Dict {ClassName: {param: default_value, ...}}
    """
    root = project_root or _get_project_root()
    defaults_path = os.path.join(root, "automation", "config", "strategy_defaults.json")
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # _schema-Schlüssel entfernen
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as e:
            print(f"  ⚠️  strategy_defaults.json Ladefehler: {e} — nutze leere Defaults.")
    return {}


def apply_strategy_defaults(strategies: list[dict], defaults: dict) -> list[dict]:
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
        merged_params  = {**class_defaults, **strat.get("params", {})}
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
    cfg_path = os.path.join(root, "automation", "config", "tournament.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = {k: v for k, v in data.items() if not k.startswith("_")}
            # Startup-Validierung
            req_all = set(cfg.get("eligible_requires_all", []))
            req_any = set(cfg.get("eligible_requires_any", []))
            used = req_all | req_any
            metric_keys = {k for k in cfg.keys() if k not in ("eligible_requires_all", "eligible_requires_any", "scoring")}
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
        "eligible_requires_all": ["min_trades", "min_win_rate", "max_drawdown"],
        "eligible_requires_any": ["min_profit_factor"],
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
    if not oos_metrics or oos_metrics.get("total_trades", 0) <= 0:
        return {
            "oos_evaluated": False,
            "oos_eligible": False,
            "oos_metrics": None,
            "oos_rejection_reasons": ["oos_not_evaluable: Kein oder zu wenig OOS-Datenmaterial (total_trades <= 0)."]
        }

    n_trades     = oos_metrics.get("total_trades", 0)
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

    reasons = []
    if n_trades < req_trades:
        reasons.append(f"oos_min_trades: {n_trades} < {req_trades}")
    if total_return < req_return:
        reasons.append(f"oos_min_total_return: {total_return:.5f} < {req_return:.5f}")
    if expectancy < req_exp:
        reasons.append(f"oos_min_expectancy: {expectancy:.5f} < {req_exp:.5f}")
    if max_dd > req_max_dd:
        reasons.append(f"oos_max_drawdown: {max_dd:.5f} > {req_max_dd:.5f}")
    if win_rate < req_win_rate:
        reasons.append(f"oos_min_win_rate: {win_rate:.5f} < {req_win_rate:.5f}")

    # None-Sicherheit: Zero-Loss-OOS
    if req_sortino > 0.0:
        if sortino is None:
             reasons.append(f"oos_min_sortino: None (all-win/insufficient) < {req_sortino}")
        elif sortino < req_sortino:
             reasons.append(f"oos_min_sortino: {sortino:.5f} < {req_sortino}")

    if req_pf > 0.0:
        if pf is None:
             reasons.append(f"oos_min_profit_factor: None (all-win/insufficient) < {req_pf}")
        elif pf < req_pf:
             reasons.append(f"oos_min_profit_factor: {pf:.5f} < {req_pf}")

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

    sortino = metrics.get("sortino_ratio")
    pf = metrics.get("profit_factor")

    if sortino is None or pf is None:
        if log_rejections:
            n = metrics.get("total_trades", 0)
            losses_count = metrics.get("losses_count", 0)
            win_rate = metrics.get("win_rate", 0.0)

            if n == 0:
                reason = "no trades executed"
            elif win_rate == 0.0:
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

    n_trades     = metrics.get("total_trades", 0)
    max_dd       = metrics.get("max_drawdown", 1.0)
    win_rate     = metrics.get("win_rate", 0.0)
    total_return = metrics.get("total_return", 0.0)
    expectancy   = total_return / n_trades if n_trades > 0 else 0.0

    t_overrides = strat_params.get("tournament_overrides", {}) if strat_params else {}
    condition_map = {
        "min_trades":        n_trades     >= t_overrides.get("min_trades", tournament_cfg.get("min_trades", 0)),
        "min_sortino":       sortino      >= t_overrides.get("min_sortino", tournament_cfg.get("min_sortino", 0.0)),
        "min_profit_factor": pf           >= t_overrides.get("min_profit_factor", tournament_cfg.get("min_profit_factor", 1.0)),
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


def _calculate_stats(pnl_list: list[float], hold_list: list[tuple[int, float]], starting_capital: float, med_notional: float = 0.0) -> dict:
    """
    Berechnet die statistischen Performance-Metriken aus einer Liste von Trade-PnLs.

    Total Return Definition:
    Der `total_return` wird hier als "Compounded Equity-Normalized Return" berechnet.
    Jeder Trade-PnL (`v`) wird durch das anfängliche Testkapital (`starting_capital`)
    geteilt, um die prozentuale Rendite des Trades bezogen auf die initiale Equity
    zu erhalten (`v / starting_capital`). Diese prozentualen Renditen werden dann
    geometrisch aufgezinst (compounded: `cum *= (1.0 + r)`).
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

    if gross_loss <= 0.0:
        profit_factor = None
    elif losses_count < 2 and n < 50:
        profit_factor = None
    else:
        profit_factor = min(gross_profit / gross_loss, 50.0)

    win_rate = wins / n if n > 0 else 0.0

    rets = [v / starting_capital for v in pnl_list]
    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        cum *= (1.0 + r)
        peak = max(peak, cum)
        max_dd = max(max_dd, (peak - cum) / peak)

    total_return = cum - 1.0

    if n < 5 or (losses_count < 2 and n < 50):
        sortino = None
    else:
        down_sq = [min(r, 0.0) ** 2 for r in rets]
        if len(down_sq) == 0 or sum(down_sq) <= 0.0:
            sortino = None
        else:
            # Addition *under* the root as requested by PR review
            dd_dev = math.sqrt((sum(down_sq) / len(down_sq)) + EPSILON)
            dd_dev = max(dd_dev, 1e-6)
            mean_ret = sum(rets) / n
            sortino = min((mean_ret / dd_dev * math.sqrt(252)), 50.0)

    if max_dd <= 1e-9:
        calmar = None
    else:
        calmar = min(total_return / max_dd, 100.0)

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


def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None, walk_forward_dict: dict | None = None, start_ns: int | None = None, commission_bps: float = 0.0) -> dict:
    """
    Extrahiert Tournament-Metriken.

    Korrektur: Nutzt trader.generate_fills_report() statt des fehlerhaften Cache-Zugriffs.
    Unterstützt robustes FIFO-Position-Matching über DataFrames.
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
                return {"metrics": NULL, "oos_metrics": NULL}
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
            sorted_fills = sorted(f_list, key=lambda x: getattr(x, 'ts_event', getattr(x, 'ts_init', 0)))
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
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price, s_ts)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()
                    if qty > 0:
                        ts_entry = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts_entry, pd.Timestamp):
                            ts_entry = ts_entry.value
                        buy_queue.append((qty, price, int(ts_entry)))
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
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price, b_ts)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()
                    if qty > 0:
                        ts_entry = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts_entry, pd.Timestamp):
                            ts_entry = ts_entry.value
                        sell_queue.append((qty, price, int(ts_entry)))



        if not pnls_with_ts:
            if log_fn:
                log_fn("[Metriken] Fills vorhanden, jedoch keine Trade-Schließungen (FIFO) generiert.")
            if walk_forward_dict and start_ns is not None:
                return {"metrics": NULL, "oos_metrics": NULL}
            return NULL

        if log_fn:
            log_fn(f"[Metriken] FIFO-Extraktion: {len(pnls_with_ts)} Round-Trips erfolgreich berechnet.")

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

            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_oos = False
                for i in range(splits):
                    split_is_start_ns = start_ns + i * oos_window_ns
                    split_oos_start_ns = split_is_start_ns + is_window_ns
                    split_oos_end_ns = split_oos_start_ns + oos_window_ns

                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        is_oos = True
                        break

                if is_oos:
                    oos_pnls.append(pnl)
                    oos_holding_times.append((ht, m_qty))
                    oos_notionals.append(notional)
                else:
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

        is_metrics = _calculate_stats(is_pnls, is_holding_times, starting_capital, med_notional=is_med_notional)
        oos_metrics = _calculate_stats(oos_pnls, oos_holding_times, starting_capital, med_notional=oos_med_notional) if oos_pnls else {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown": 0.0, "total_return": 0.0,
            "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
            "losses_count": 0,
            "median_position_notional": 0.0,
        }

        if walk_forward_dict and start_ns is not None:
            return {
                "metrics": is_metrics,
                "oos_metrics": oos_metrics
            }
        else:
            # Fallback for backwards compatibility if oos isn't requested
            return is_metrics
    except Exception as e:
        if log_fn:
            import traceback
            log_fn(f"[Metriken-Fehler] FIFO-Verarbeitung fehlgeschlagen: {e}\n{traceback.format_exc()}")
        return NULL


# ---------------------------------------------------------------------------
# Tournament-Logik
# ---------------------------------------------------------------------------

def select_winners(
    all_results: list[dict],
    tournament_cfg: dict | None = None,
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
            su = sorted(list(set(vals)), reverse=reverse)
            if len(su) <= 1:
                return [1.0] * len(vals)
            return [(su.index(v)) / (len(su) - 1) for v in vals]

        sortinos = [(r["metrics"].get("sortino_ratio") or 0.0) for r in is_eligible_population]
        pfs = [(r["metrics"].get("profit_factor") or 0.0) for r in is_eligible_population]
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
            r["_score"] = compute_tournament_score(metrics_to_score, scoring)

    # 3. Per-Symbol OOS-Gating (Der Entscheidungs-Trail)
    fully_eligible_count = 0
    require_oos = tournament_cfg.get("require_oos", True)

    # Pre-evaluate all to get the true fully_eligible_count
    for r in is_eligible_population:
        oos_metrics = r.get("oos_metrics")
        if oos_metrics is not None:
            oos_eval = _evaluate_oos_eligibility(oos_metrics, tournament_cfg, r.get("strat_params", {}))
        else:
            is_oos_eligible = not require_oos
            oos_eval = {
                "oos_evaluated": False,
                "oos_eligible": is_oos_eligible,
                "oos_metrics": None,
                "oos_rejection_reasons": ["oos_metrics fehlt"] if not is_oos_eligible else []
            }
        r["_oos_eval"] = oos_eval
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
            vals = [v for v in vals if v is not None]
            sv = sorted(vals)
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

            sortinos = [s for s in (oos.get("sortino_ratio") for oos in best_results) if s is not None]
            med_sortino = get_median(sortinos) if sortinos else None

            pfs = [p for p in (oos.get("profit_factor") for oos in best_results) if p is not None]
            med_pf = get_median(pfs) if pfs else None

            span_days = [oos.get("oos_span_days", 0) for oos in best_results]
            med_span = get_median(span_days) if span_days else 0

            avg_oos = {
                "total_trades": portfolio_total_trades,
                "sortino_ratio": med_sortino,
                "profit_factor": med_pf,
                "max_drawdown": get_median([oos.get("max_drawdown", 1.0) for oos in best_results]),
                "win_rate": portfolio_win_rate,
                "total_return": portfolio_mean_return,
                "oos_span_days": med_span,
                "median_position_notional": get_median([oos.get("median_position_notional", 0.0) for oos in best_results]),
                "aggregation_basis": "portfolio_sum_for_trades_and_trade_weighted_mean_for_return_and_median_for_ratios"
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
            "median_sortino": round(
                get_median([x for x in sortinos_by_strat[best] if x is not None]), 4
            ),
            **agg_oos_eval
        }

    return per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count


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
) -> None:
    """Schreibt Tournament-Ergebnisse als JSON.

    Task 5: Jeder Gewinner-Eintrag enthält jetzt ein 'score'-Feld.
    """
    if tournament_cfg is None:
        tournament_cfg = load_tournament_config()

    output = {
        "generated_at":                datetime.now(timezone.utc).isoformat(),
        "universe_snapshot":           universe_snapshot,
        "total_symbol_strategy_pairs": len(all_results),
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



def _empty_result(symbol: str, strategy: str, strat: dict) -> dict:
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
        "strat_params": strat.get("params", {})
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
    span_tolerance_days: float = 1.0,
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

    strategy_class_name = strat["strategy_class"]
    module_name         = strat["strategy_module"]
    config_class_name   = strat["config_class"]

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
                instrument_map_path = os.path.join(_get_project_root(), "automation", "config", "instrument_map.json")
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

        # --- Check Data Span for Walk-Forward Window ---
        required_days = strat.get("_walk_forward_days")
        if strat.get("_walk_forward_dict"):
            wfd = strat["_walk_forward_dict"]
            required_days = wfd.get("is_window_days", 90) + (wfd.get("splits", 2) * wfd.get("oos_window_days", 30))
        if required_days:
            is_sufficient, span_days, _ = check_data_span(ticks, required_days, span_tolerance_days)
            if not is_sufficient and span_days < required_days * 0.95:
                from automation.utils import emit_json_event
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
                wlog(f"   ⚠️ Knappe Datenspanne, fahre fort: {span_days:.1f} Tage (benötigt: {required_days} Tage, innerhalb der Toleranz von {span_tolerance_days} Tagen).")

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
            return _empty_result(inst_id_str, strategy_class_name, strat)

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
            return _empty_result(inst_id_str, strategy_class_name, strat)

        # --- Backtest ausführen ---
        try:
            engine.run()
        except RuntimeError as e:
            wlog_err(f"Backtest RuntimeError (wahrscheinlich Precision Mismatch): {e}")
            engine.dispose()
            return _empty_result(inst_id_str, strategy_class_name, strat)
        except Exception as e:
            wlog_err(f"Backtest gecrasht: {e}", exc=True)
            engine.dispose()
            return _empty_result(inst_id_str, strategy_class_name, strat)

        # --- Metriken extrahieren ---
        walk_forward_dict = strat.get("_walk_forward_dict", None)
        try:
            extracted_data = extract_metrics(engine, start_capital, log_fn=wlog, walk_forward_dict=walk_forward_dict, start_ns=start_ns, commission_bps=commission_bps)
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
            "_first_tick_ns": first_tick_ns_val,
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
    logs_dir      = os.path.join(_project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file  = os.path.join(logs_dir, f"backtest_{timestamp}.log")
    error_log_file = os.path.join(logs_dir, f"errors_{timestamp}.log")

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
    _bt_cfg_path = os.path.join(_project_root, "automation", "config", "backtest.json")
    if os.path.exists(_bt_cfg_path):
        try:
            with open(_bt_cfg_path, "r", encoding="utf-8") as _f:
                backtest_global_cfg = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}
        except Exception as _e:
            print(f"  ⚠️  backtest.json Ladefehler: {_e}")
    spread_modeling = backtest_global_cfg.get("spread_modeling", True)
    fill_model_str  = backtest_global_cfg.get("fill_model", "bid_ask")
    span_tolerance_days = backtest_global_cfg.get("span_tolerance_days", 1.0)
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
    assert len(loaded_defaults) == len(strategies_list), f"Mismatch: {len(loaded_defaults)} defaults loaded but {len(strategies_list)} strategies executed."

    # Task 2: Strategy-Defaults auf die Strategie-Params anwenden (Overrides behalten Vorrang)
    strategies_list = apply_strategy_defaults(strategies_list, strategy_defaults)
    if strategy_defaults:
        for strat in strategies_list:
            cls_name = strat.get("strategy_class", "?")
            params_str = ", ".join(f"{k}={v}" for k, v in (strat.get("params") or {}).items())
            print(f"✅ Defaults angewandt — {cls_name}: {params_str}")

    # --- Parameter-Validierung & Walk-Forward Injektion ---
    param_warnings: list[str] = []
    walk_forward_cfg = global_settings.get("walk_forward")

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

    start_capital = global_settings.get("start_capital", 100_000.0)
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
                walk_forward_cfg = global_settings.get("walk_forward")
                if walk_forward_cfg and end_ns:
                    oos_days = walk_forward_cfg.get("oos_window_days", 30)
                    splits   = walk_forward_cfg.get("splits", 2)
                    span_days = splits * oos_days
                    strat["_walk_forward_dict"] = walk_forward_cfg
                    strat["_oos_span_days"]     = span_days

                wlf = os.path.join(
                    logs_dir,
                    f"worker_{inst_id_str.replace('.', '_')}"
                    f"_{strat['strategy_class']}_{timestamp}.log"
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
        per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count = select_winners(all_results, tournament_cfg)
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
                f"Median Sortino: {aggregate_winner['median_sortino']}"
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
            tournament_cfg=tournament_cfg
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
    span_tolerance_days: float = 1.0,
    commission_bps: float = 0.0,
    spread_bps_by_asset_class: dict | None = None,
) -> None:
    remaining = {
        f: v for f, v in futures.items()
        if not f.done() and f is not failed_future
    }
    for _, (rem_inst, rem_strat_name, rem_log) in remaining.items():
        rem_strat = next(
            (s for s in strategies_list if s["strategy_class"] == rem_strat_name), None
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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()
