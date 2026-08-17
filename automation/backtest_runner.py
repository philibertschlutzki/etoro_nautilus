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
import statistics
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



from typing import Any, TypedDict
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
        self.log.flush()

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

def _canonical_gate_key(key: str) -> str:
    """Issue #649 — kanonische Registry-Form eines Gate-Namens: entfernt ein optionales
    ``oos_``-Präfix. ``tournament.json``s ``eligible_requires_all``/``_any`` listet manche Klauseln
    MIT (``oos_min_psr``) und manche OHNE (``min_trades``) Präfix; die ``condition_map``-Handler in
    ``_evaluate_oos_eligibility`` sind durchgehend UN-präfigiert. Diese Funktion ist die EINE Stelle,
    die beide Schreibweisen auf dieselbe Handler-Identität abbildet — VOR jedem
    ``in condition_map``-Check, sowohl beim Gate-Loop als auch bei der Startup-Validierung
    (``load_tournament_config``). Vor #649 wurde nirgends normalisiert, wodurch vier präfigierte
    Klauseln (``oos_min_profitable_folds_frac``, ``oos_min_evaluable_folds``, ``oos_min_psr``,
    ``oos_min_excess_return``) niemals auf ihren Handler resolvten und STILL übersprungen wurden."""
    return key[4:] if key.startswith("oos_") else key


# Issue #649 — kanonische Registry der in ``_evaluate_oos_eligibility`` TATSÄCHLICH implementierten
# ``condition_map``-Handler (nach ``_canonical_gate_key``-Normalisierung). ``load_tournament_config``
# validiert JEDEN ``eligible_requires_all``/``_any``-Eintrag gegen diese Menge und bricht fail-loud
# ab, wenn ein Eintrag auf keinen Handler resolved — genau der Drift, der #649 verursachte, blieb
# vorher UNENTDECKT, weil die bisherige Startup-Prüfung nur config-INTERNE Konsistenz (Metrik
# definiert ↔ referenziert) prüfte, nie gegen die tatsächliche Handler-Menge.
OOS_CONDITION_MAP_KEYS = frozenset({
    "min_trades",
    "min_total_return",
    "min_expectancy",
    "max_drawdown",
    "min_win_rate",
    "min_sortino",
    "min_psr",
    "min_profit_factor",
    "min_profitable_folds_frac",
    "min_excess_return",
    "min_evaluable_folds",
})

# Issue #760 — kanonische Registry der ``condition_map``-Handler, die tatsächlich eine
# ``oos_gate_deltas``-Spalte stempeln (siehe die ``oos_gate_deltas[...]=``-Zuweisungen weiter unten
# in dieser Funktion). ``min_evaluable_folds`` ist ein reiner Fold-Zähler-Gate ohne kontinuierliches
# Delta-Signal — strukturell delta-frei, nicht versehentlich vergessen. Konsumiert von
# ``invariants.check_config_key_registry`` (#760), damit ein in ``eligible_requires_all``/``_any``
# reaktivierter Key, der weder einen Handler NOCH eine Delta-Spalte hat, fail-loud auffällt, statt
# lautlos aus der #667/#760-Kollinearitätsdiagnose zu verschwinden.
OOS_GATE_DELTA_KEYS = frozenset({
    "min_trades", "min_total_return", "min_expectancy", "max_drawdown", "min_win_rate",
    "min_sortino", "min_psr", "min_profit_factor", "min_excess_return",
    "min_profitable_folds_frac",
})


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
        except (OSError, ValueError) as e:
            print(f"  ⚠️  tournament.json Ladefehler: {e} — nutze Legacy-Defaults.")
        else:
            # Startup-Validierung
            req_all = set(cfg.get("eligible_requires_all", []))
            req_any = set(cfg.get("eligible_requires_any", []))
            used = req_all | req_any
            metric_keys = {k for k in cfg.keys() if k not in ("eligible_requires_all", "eligible_requires_any", "scoring", "sortino_min_trades", "sortino_mar")}
            for k in metric_keys:
                base_k = k[4:] if k.startswith("oos_") else k
                if base_k not in used:
                    print(f"  ⚠️  Tournament-Kriterium '{k}' ist definiert, aber nicht in eligible_requires_all/any referenziert!")
            normalized_metric_keys = metric_keys | {k[4:] for k in metric_keys if k.startswith("oos_")}
            for u in used:
                if u not in normalized_metric_keys:
                    print(f"  ⚠️  Referenziertes Kriterium '{u}' in eligible_requires_all/any ist nicht definiert!")

            # Issue #649 — Fail-Loud-Validierung GEGEN DIE REGISTRY (nicht nur config-intern). Die
            # obige Prüfung deckt nur auf, ob ein Metrik-Threshold definiert-aber-unreferenziert (oder
            # umgekehrt) ist — sie prüft NICHT, ob ein referenzierter Gate-Name tatsächlich auf einen
            # ``condition_map``-Handler in ``_evaluate_oos_eligibility``/``_is_eligible`` resolved. Ein
            # unbekannter (nach Normalisierung nicht in ``OOS_CONDITION_MAP_KEYS`` enthaltener) Eintrag
            # wurde vorher STILL übersprungen (kein Fehler, keine Warnung — die #649-Root-Cause: vier
            # präfigierte Klauseln waren nie durchsetzbar). Ein bewusst falscher Key MUSS hier abbrechen
            # — DESHALB ausserhalb des obigen ``try/except`` (dieser fängt nur Lade-/Parse-Fehler, kein
            # ``ValueError`` aus einer bewussten Config-Validierung mehr ab).
            unknown_gates = sorted(
                cond_name for cond_name in used
                if _canonical_gate_key(cond_name) not in OOS_CONDITION_MAP_KEYS
            )
            if unknown_gates:
                raise ValueError(
                    "tournament.json: eligible_requires_all/eligible_requires_any referenziert "
                    f"Gate(s) ohne condition_map-Handler (nach oos_-Normalisierung): {unknown_gates}. "
                    f"Bekannte Handler (kanonisch): {sorted(OOS_CONDITION_MAP_KEYS)}."
                )
            return cfg
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
    t_overrides = strat_params.get("tournament_overrides", {}) if strat_params else {}

    req_exp      = t_overrides.get("oos_min_expectancy", t_overrides.get("min_expectancy", tournament_cfg.get("oos_min_expectancy", tournament_cfg.get("min_expectancy", 0.0))))

    # Issue #562 — KOSTENRELATIVES Expectancy-Gate. Statt einer absoluten Magie-Zahl (0.001 = 10 bps),
    # die zufällig exakt auf der Round-Trip-Kostenwand liegt und damit strukturell unerreichbar ist,
    # wird das Gate relativ zur tatsächlichen Kostenbasis definiert: oos_min_expectancy := k_alpha · c_rt
    # ("schlage die Kosten um k_alpha"). c_rt (Round-Trip-Kosten in bps) wird aus dem Kostenmodell
    # ABGELEITET (round_trip_cost_bps, von der Worker-Kostenstelle in die Metriken gestempelt, #562),
    # nicht doppelt gepflegt — ändert man Spread oder Kommission, zieht das effektive Gate automatisch mit.
    # Aktiv NUR, wenn k_alpha konfiguriert ist UND die Kosten-Telemetrie vorliegt; sonst statisches
    # Legacy-Gate (bit-identisch, Zero-Hardcoding). effective_expectancy_gate wird für die Telemetrie
    # zurückgegeben. Issue #577: Stationäres Gate auch bei degenerierten (Zero-Trade) Trials stempeln.
    # Issue #684 — Root-Cause: fehlt die Kosten-Telemetrie (``round_trip_cost_bps`` nicht gestempelt,
    # z. B. degenerierte/Zero-Trade-Trials oder Nicht-Standard-Call-Sites), fiel das Gate bislang
    # STUMM auf das statische ``oos_min_expectancy`` (0.001) zurück — eine ZWEITE, unabhängig
    # gepflegte Schwelle, die zufällig ~13× strenger ist als der kostenrelative Regelpfad
    # (``k_alpha·c_rt`` ≈ 7.5e-5 bei c_rt=3bps). Ist ``k_alpha`` konfiguriert (die Kosten-Relativität
    # ist damit bereits die gewollte Semantik), aber die LIVE-Telemetrie fehlt, wird stattdessen ein
    # aus DEMSELBEN Kostenmodell abgeleiteter DEFAULT-c_rt verwendet (``_read_default_round_trip_cost_bps``,
    # Single Source of Truth mit backtest_runner-Zeile ~3613 ``c_rt = spread_bps + commission_bps``)
    # — der Fallback trägt dadurch dieselbe Grössenordnung wie der Regelpfad (≤ 2× konsistent), statt
    # einer unabhängig geratenen Konstante. ``expectancy_gate_cost_source`` macht die tatsächlich
    # verwendete Quelle telemetrisch nachvollziehbar.
    k_alpha = t_overrides.get("oos_min_expectancy_k_alpha",
                              tournament_cfg.get("oos_min_expectancy_k_alpha"))
    effective_expectancy_gate = None
    expectancy_gate_cost_source = "static"
    if k_alpha is not None and oos_metrics is not None:
        c_rt_bps = oos_metrics.get("round_trip_cost_bps")
        if c_rt_bps is None:
            c_rt_bps = _read_default_round_trip_cost_bps()
            expectancy_gate_cost_source = "config_default"
        else:
            expectancy_gate_cost_source = "telemetry"
        req_exp = float(k_alpha) * float(c_rt_bps) / 10000.0
        effective_expectancy_gate = req_exp

    n_trades = oos_metrics.get("total_trades", 0) if oos_metrics else 0
    # Issue #859 Fix Punkt 4 — ein terminal unrecoverabler Markt-Close (HourlyStrategyBase.
    # _handle_exit_close_order_failure, >= exit_close_max_retries vergebliche Versuche) macht den
    # Trial ungültig, UNABHÄNGIG davon, wie viele Trades vor dem Stillstand ausgeführt wurden — die
    # Simulation hielt danach eine Position, die der Handelsvertrag nie vorsah. Dieser Check läuft
    # HIER (statt nur additiv in ``oos_metrics``), damit BEIDE Konsumenten (Per-Symbol-Kandidat UND
    # die Portfolio-Aggregation, deren ``avg_oos`` die Diagnose separat mitführt) denselben
    # Ausgang sehen, statt dass die Aggregation die Invalidierung eines einzelnen Symbols verliert.
    _exit_close_unrecoverable = any(
        d.get("code") == "EXIT_CLOSE_UNRECOVERABLE" for d in (oos_metrics.get("inference_diagnostics") or [])
    ) if oos_metrics else False
    if n_trades <= 0 or _exit_close_unrecoverable:
        reason = ("oos_not_evaluable: Kein oder zu wenig OOS-Datenmaterial (total_trades <= 0)."
                  if n_trades <= 0 else
                  "oos_not_evaluable: EXIT_CLOSE_UNRECOVERABLE -- terminal unrecoverabler "
                  "Markt-Close (#859).")
        return {
            "oos_evaluated": False,
            "oos_eligible": False,
            "oos_metrics": None,
            "oos_rejection_reasons": [reason],
            # Issue #554 — schemastabil: leeres numerisches Delta-Dict auch im Not-Evaluated-Fall.
            "oos_gate_deltas": {},
            "effective_expectancy_gate": effective_expectancy_gate,
            "expectancy_gate_cost_source": expectancy_gate_cost_source,
        }


    max_dd       = oos_metrics.get("max_drawdown", 1.0)
    win_rate     = oos_metrics.get("win_rate", 0.0)
    total_return = oos_metrics.get("total_return", 0.0)
    expectancy   = oos_metrics.get("expectancy", 0.0)

    sortino = oos_metrics.get("sortino_ratio")
    pf = oos_metrics.get("profit_factor")

    req_trades   = t_overrides.get("oos_min_trades", t_overrides.get("min_trades", tournament_cfg.get("oos_min_trades", tournament_cfg.get("min_trades", 0))))
    req_return   = t_overrides.get("oos_min_total_return", t_overrides.get("min_total_return", tournament_cfg.get("oos_min_total_return", tournament_cfg.get("min_total_return", 0.0))))
    req_sortino  = t_overrides.get("oos_min_sortino", t_overrides.get("min_sortino", tournament_cfg.get("oos_min_sortino", tournament_cfg.get("min_sortino", 0.0))))
    req_pf       = t_overrides.get("oos_min_profit_factor", t_overrides.get("min_profit_factor", tournament_cfg.get("oos_min_profit_factor", tournament_cfg.get("min_profit_factor", 1.0))))
    req_max_dd   = t_overrides.get("oos_max_drawdown", t_overrides.get("max_drawdown", tournament_cfg.get("oos_max_drawdown", tournament_cfg.get("max_drawdown", 1.0))))
    req_win_rate = t_overrides.get("oos_min_win_rate", t_overrides.get("min_win_rate", tournament_cfg.get("oos_min_win_rate", tournament_cfg.get("min_win_rate", 0.0))))
    # Issue #550 — Fold-Konsistenz-Gate: Mindest-Anteil profitabler OOS-Folds. Fehlt der Key
    # (None) ⇒ Bedingung inaktiv (rückwärtskompatibel, Zero-Hardcoding).
    req_profitable_folds_frac = t_overrides.get("oos_min_profitable_folds_frac",
                                                tournament_cfg.get("oos_min_profitable_folds_frac"))
    # Issue #552 — Benchmark-relatives Excess-Return-(Alpha-)Gate. Fehlt der Key (None) ⇒ inaktiv
    # (Legacy-Absolut-Gate über oos_min_total_return bleibt allein maßgeblich).
    req_excess_return = t_overrides.get("oos_min_excess_return",
                                        tournament_cfg.get("oos_min_excess_return"))
    # Issue #590 — Fold-Degenerations-Gate: Mindestzahl VALIDE evaluierbarer Folds (definierter
    # Sortino) bei > 1 Gesamt-Fold. Fehlt der Key (None) ⇒ inaktiv (rückwärtskompatibel).
    req_evaluable_folds = t_overrides.get("oos_min_evaluable_folds",
                                          tournament_cfg.get("oos_min_evaluable_folds"))

    # Issue #617 — der ``None``-Guard darf NICHT schwächer sein als die Bedingung, unter der die
    # Kennzahl überhaupt DEFINIERT ist. ``sortino``/``profit_factor`` werden ``None``, sobald
    # ``n < sortino_min_trades`` (Sortino) bzw. ``gross_loss<=0`` / ``losses_count<2 ∧ n<50`` (PF) —
    # ``_calculate_stats``. Mit ``oos_min_trades = 1`` war der Guard ``n_trades < 1`` VAKUANT: ein
    # Trial mit 9 OOS-Trades und 0 Verlusten (sortino=None, win_rate>0) passierte ``min_sortino``
    # UND ``min_profit_factor`` GRATIS — genau der »Bewertung löschen statt Performance liefern«-Kanal,
    # den #590 schliessen sollte. Der Guard nutzt daher ``max(oos_min_trades, sortino_min_trades)``:
    # unterhalb dieser Stichprobe ist die Kennzahl nicht definierbar ⇒ das Gate MUSS scheitern.
    sortino_min_trades = (t_overrides.get("sortino_min_trades",
                                          tournament_cfg.get("sortino_min_trades", 0)) or 0)
    req_trades_guard = max(int(req_trades or 0), int(sortino_min_trades))
    # Issue #617 — EXPLIZITE, benannte Policy für eine bei AUSREICHENDER Stichprobe
    # (``n_trades >= req_trades_guard``) dennoch mathematisch UNDEFINIERTE (``None``) Pflicht-Kennzahl
    # (verlustfreier, profitabler Fold). KEIN impliziter Pass mehr: ``"fail"`` ⇒ das Gate scheitert;
    # ``"fallback_total_return"`` ⇒ Pass genau dann, wenn ``oos_total_return > 0`` (Parität zu
    # ``reward.py['oos_sortino_fallback']`` und ``confirm._holdout_gate_passed`` — der Sortino ist auf
    # einem verlustfreien Fold per Definition undefiniert, ``oos_total_return>0`` ist dort das
    # ökonomisch korrekte Pass-Kriterium). Fehlt der Key ⇒ ``"fail"`` (strengster, sicherster Default).
    undefined_metric_policy = t_overrides.get("undefined_metric_policy",
                                              tournament_cfg.get("undefined_metric_policy", "fail"))

    sortino_valid = True
    sortino_reason = ""
    if req_sortino > 0.0:
        if sortino is None:
             if n_trades < req_trades_guard or win_rate <= 0.0:
                 sortino_valid = False
                 sortino_reason = (f"oos_min_sortino: None (insufficient) < {req_sortino} "
                                   f"(n_trades={n_trades} < {req_trades_guard})")
             elif undefined_metric_policy == "fallback_total_return":
                 sortino_valid = total_return > 0.0
                 if not sortino_valid:
                     sortino_reason = (f"oos_min_sortino: None (undefined; fallback_total_return "
                                       f"{total_return:.6g} <= 0)")
             else:
                 sortino_valid = False
                 sortino_reason = "oos_min_sortino: None (undefined; undefined_metric_policy=fail)"
        elif sortino < req_sortino:
             sortino_valid = False
             sortino_reason = f"oos_min_sortino: {sortino:.5f} < {req_sortino}"

    pf_valid = True
    pf_reason = ""
    if req_pf > 0.0:
        if pf is None:
             if n_trades < req_trades_guard or win_rate <= 0.0:
                 pf_valid = False
                 pf_reason = (f"oos_min_profit_factor: None (insufficient) < {req_pf} "
                              f"(n_trades={n_trades} < {req_trades_guard})")
             elif undefined_metric_policy == "fallback_total_return":
                 pf_valid = total_return > 0.0
                 if not pf_valid:
                     pf_reason = (f"oos_min_profit_factor: None (undefined; fallback_total_return "
                                  f"{total_return:.6g} <= 0)")
             else:
                 pf_valid = False
                 pf_reason = "oos_min_profit_factor: None (undefined; undefined_metric_policy=fail)"
        elif pf < req_pf:
             pf_valid = False
             pf_reason = f"oos_min_profit_factor: {pf:.5f} < {req_pf}"

    # Issue #550 — Fold-Konsistenz-Bedingung. Nur aktiv, wenn die Schwelle konfiguriert ist UND
    # die Fold-Telemetrie (oos_folds_total, #549/#550) vorliegt (Walk-Forward-Pfad). Fehlt eines
    # von beiden ⇒ trivial erfüllt (inaktiv, rückwärtskompatibel zu Nicht-WF- und Legacy-JSONs).
    # Issue #664 — Gewichtungsmodus des Profitable-Folds-Gates. ``'equal'`` (Default, fehlt der Key)
    # ist BIT-IDENTISCH zum Status quo (Zähler/Gesamt-Fraktion); ``'recency'`` ist eine BEWUSSTE,
    # OPT-IN Entscheidung (kein blinder Default-Wechsel, siehe apply_fold_aggregation-Docstring) —
    # das Gate konsumiert dann die exponentiell-gewichtete Parallel-Fraktion
    # (``oos_profitable_folds_frac_recency``), die jüngere Folds stärker gewichtet.
    profitable_folds_weighting = t_overrides.get(
        "profitable_folds_weighting", tournament_cfg.get("profitable_folds_weighting", "equal"))
    prof_folds_valid = True
    prof_folds_reason = ""
    # Issue #667 — das maschinenlesbare Delta (frac − threshold) auch für die Profitable-Folds-
    # Klausel, damit die Gate-Kollinearitäts-Diagnose (reward.gate_rank_correlation_matrix) sie
    # neben expectancy/psr/any_condition sehen kann (vorher fehlte dieser Delta-Eintrag komplett).
    prof_folds_frac_delta = None
    if req_profitable_folds_frac is not None:
        n_folds_total = oos_metrics.get("oos_folds_total")
        n_folds_prof = oos_metrics.get("oos_profitable_folds", 0)
        # Issue #676 — Nenner = Anzahl EVALUIERBARER Folds (``oos_folds_evaluable``, von
        # ``apply_fold_aggregation`` gestempelt), NICHT ``n_folds_total`` (zählt No-Trade-Folds
        # ungefiltert mit). Fehlt das Feld (Legacy-Metrics-Dict ohne ``apply_fold_aggregation``-Lauf)
        # ⇒ Fallback auf ``n_folds_total`` (bit-identisch zum Pre-#676-Verhalten für solche Aufrufer).
        n_folds_evaluable = oos_metrics.get("oos_folds_evaluable")
        if n_folds_evaluable is None:
            n_folds_evaluable = n_folds_total
        if n_folds_total:
            if profitable_folds_weighting == "recency":
                # Issue #676 — bereits von ``apply_fold_aggregation`` mit dem korrigierten
                # (evaluierbare-Folds-)Nenner berechnet; hier NUR gelesen (Single Source of Truth,
                # keine zweite, divergierende Nenner-Berechnung mehr).
                frac = oos_metrics.get("oos_profitable_folds_frac_recency")
                frac = float(frac) if frac is not None else 0.0
                if frac < req_profitable_folds_frac:
                    prof_folds_valid = False
                    prof_folds_reason = (
                        f"oos_min_profitable_folds (recency): {frac:.3f} < {req_profitable_folds_frac:.2f}")
            else:
                # Issue #676 — dieselbe Grösse, die ``apply_fold_aggregation`` bereits unter
                # ``oos_profitable_folds_frac`` gestempelt hat (Zähler/EVALUIERBARE Folds); hier
                # aus den Rohzählern rekonstruiert (keine Kopplung an die Aufruf-Reihenfolge/das
                # Vorhandensein des Felds), aber mit demselben korrigierten Nenner.
                frac = n_folds_prof / n_folds_evaluable if n_folds_evaluable > 0 else 0.0
                if frac < req_profitable_folds_frac:
                    prof_folds_valid = False
                    prof_folds_reason = (f"oos_min_profitable_folds: {n_folds_prof}/{n_folds_evaluable} "
                                         f"({frac:.2f}) < {req_profitable_folds_frac:.2f}")
            prof_folds_frac_delta = float(frac - req_profitable_folds_frac)

    # Issue #552 — Excess-Return-Bedingung. Nur aktiv, wenn die Schwelle gesetzt ist UND die
    # Benchmark-Telemetrie (oos_excess_return) vorliegt; sonst trivial erfüllt (rückwärtskompatibel).
    excess_valid = True
    excess_reason = ""
    # Issue #554 — adaptive Präzision (.6g) + numerisches Delta im Reject-String. Der frühere fixe
    # :.5f verschluckte bei mikroskopischen Schwellen (5e-05) genau die entscheidenden Stellen
    # ("0.00005 < 0.00005"), sodass ein Near-Miss (Δ=−1e-7) vom groben Miss (Δ=−1e-4) ununterscheidbar
    # wurde. .6g zeigt signifikante Stellen, das Δ macht den Rest-Gap explizit.
    def _reason(label, actual, thresh, op):
        return f"{label}: {actual:.6g} {op} {thresh:.6g} (Δ={actual - thresh:+.3e})"

    # Issue #666 — Bär-Markt-Symmetrie. "Schlage Buy&Hold im absoluten Endpunkt-Return" misst bei
    # FALLENDEM Benchmark nur negatives Beta (Nicht-im-Markt-Sein), kein positives Alpha: fiel B&H
    # z. B. −11 % über das OOS-Fenster, "schlägt" JEDE Strategie, die nicht schlimmer als −11 %
    # verliert, den Benchmark trivial — inklusive einer flachen/Zufalls-Strategie. Fix (|Benchmark|-
    # bewusstes Gate): im Bär-Markt (``oos_buyhold_return < 0``) muss die Strategie einen ECHTEN
    # positiven risikoadjustierten Return liefern (``sortino_period > 0`` — dieselbe, bereits an
    # anderer Stelle gate-/reward-massgebliche annualisierungs-invariante Grösse, #614/#665), NICHT
    # nur "weniger schlecht als der Markt". Im Bull-/Flat-Markt (``oos_buyhold_return >= 0``) bleibt
    # das ursprüngliche absolute Excess-Return-Gate massgeblich (bit-identisch). Fail-open bleibt
    # erhalten: fehlt die Benchmark-Telemetrie (``oos_excess_return is None``), ist die Klausel
    # weiterhin trivial erfüllt.
    if req_excess_return is not None:
        oos_excess = oos_metrics.get("oos_excess_return")
        oos_buyhold = oos_metrics.get("oos_buyhold_return")
        if oos_excess is not None:
            if oos_buyhold is not None and oos_buyhold < 0.0:
                sortino_p = oos_metrics.get("sortino_period")
                if sortino_p is None or sortino_p <= 0.0:
                    excess_valid = False
                    sp_str = "None (insufficient/guard)" if sortino_p is None else f"{sortino_p:.6g}"
                    excess_reason = (
                        f"oos_min_excess_return (bear-regime, oos_buyhold_return={oos_buyhold:.6g}<0): "
                        f"sortino_period={sp_str} <= 0 — absoluter Excess {oos_excess:.6g} allein "
                        f"belegt im Bärenmarkt kein Alpha (nur negatives Beta)"
                    )
            elif oos_excess < req_excess_return:
                excess_valid = False
                excess_reason = _reason("oos_min_excess_return", oos_excess, req_excess_return, "<")

    # Issue #590/#677 — Fold-Degenerations-Bedingung. Nur aktiv, wenn die Schwelle konfiguriert ist
    # UND oos_folds_total > 1 (echter Walk-Forward). Fehlt Telemetrie ⇒ trivial erfüllt
    # (rückwärtskompatibel). Issue #677 — Root-Cause: ein ABSOLUTER Fold-ZÄHLER bestraft
    # frequenz-heterogene Configs strukturell (eine Config, die ihre ≥oos_min_trades Trades in
    # WENIGER Folds konzentriert, ist kein Qualitätsmangel — die Mindest-Stichprobe ist bereits über
    # ``min_trades`` abgedeckt). Fix: eine Schwelle in ``(0, 1]`` wird als Mindest-ANTEIL
    # evaluierbarer Folds MIT SIGNAL (``oos_folds_evaluable``, #676 — Folds mit ≥1 Trade)
    # interpretiert, statt eines absoluten Fold-Zählers — die variable Fold-Anzahl je Trial
    # (#677-Pitfall #144: fold-übergreifende Vergleiche sind bei variabler Fold-Zahl inkommensurabel)
    # wird dadurch explizit zum Nenner gemacht statt ignoriert. Ein Wert ``>= 1`` (Legacy-Default,
    # nur bei expliziter Reaktivierung relevant, siehe eligible_requires_all-Kommentar) bleibt der
    # ABSOLUTE Zähler — bit-identisch zum Pre-#677-Verhalten.
    eval_folds_valid = True
    eval_folds_reason = ""
    if req_evaluable_folds is not None:
        n_folds_total_ev = oos_metrics.get("oos_folds_total")
        n_valid_sortinos = len(oos_metrics.get("oos_fold_sortinos") or [])
        if n_folds_total_ev and n_folds_total_ev > 1:
            if 0.0 < req_evaluable_folds <= 1.0:
                n_folds_signal = oos_metrics.get("oos_folds_evaluable")
                if n_folds_signal is None:
                    n_folds_signal = n_folds_total_ev
                frac_valid = n_valid_sortinos / n_folds_signal if n_folds_signal > 0 else 0.0
                if frac_valid < req_evaluable_folds:
                    eval_folds_valid = False
                    eval_folds_reason = (
                        f"oos_min_evaluable_folds (relativ zu Folds mit Signal): "
                        f"{n_valid_sortinos}/{n_folds_signal} ({frac_valid:.2f}) "
                        f"< {req_evaluable_folds:.2f}")
            elif n_valid_sortinos < req_evaluable_folds:
                eval_folds_valid = False
                eval_folds_reason = (f"oos_min_evaluable_folds: {n_valid_sortinos} valide "
                                     f"< {req_evaluable_folds} (von {n_folds_total_ev} Folds)")

    # Issue #614 — PSR-Gate (skalenfrei, in [0,1], T-bewusst). Der ANNUALISIERTE Sortino ist NUR noch
    # Telemetrie und fliesst NICHT mehr ins Gate (bei T≈200 statistisch bedeutungslos). Ein
    # UNDEFINIERTER PSR (zu wenig Trades ODER Guard-getrippt, |annualized|>25) ist NICHT eligible —
    # kein impliziter Pass (analog #617). Nur aktiv, wenn ``oos_min_psr`` konfiguriert ist (sonst
    # trivial erfüllt, rückwärtskompatibel zu Nicht-PSR-JSONs/Legacy-Gates).
    req_psr = t_overrides.get("oos_min_psr", tournament_cfg.get("oos_min_psr"))
    psr = oos_metrics.get("psr")
    psr_valid = True
    psr_reason = ""
    if req_psr is not None and float(req_psr) > 0.0:
        if psr is None:
            psr_valid = False
            psr_reason = f"oos_min_psr: None (insufficient/guard) < {req_psr}"
        elif float(psr) < float(req_psr):
            psr_valid = False
            psr_reason = _reason("oos_min_psr", float(psr), float(req_psr), "<")

    condition_map = {
        "min_trades":        (n_trades >= req_trades, f"oos_min_trades: {n_trades} < {req_trades}"),
        "min_total_return":  (total_return >= req_return, _reason("oos_min_total_return", total_return, req_return, "<")),
        "min_expectancy":    (expectancy >= req_exp, _reason("oos_min_expectancy", expectancy, req_exp, "<")),
        "max_drawdown":      (max_dd <= req_max_dd, _reason("oos_max_drawdown", max_dd, req_max_dd, ">")),
        "min_win_rate":      (win_rate >= req_win_rate, _reason("oos_min_win_rate", win_rate, req_win_rate, "<")),
        "min_sortino":       (sortino_valid, sortino_reason),
        "min_psr":           (psr_valid, psr_reason),
        "min_profit_factor": (pf_valid, pf_reason),
        # Issue #649 — Schlüssel war zuvor ``min_profitable_folds`` (ohne ``_frac``), während die
        # Config-Klausel ``oos_min_profitable_folds_frac`` heisst. Nach ``_canonical_gate_key``
        # (entfernt nur das ``oos_``-Präfix) blieb ``min_profitable_folds_frac`` übrig — ein ZWEITER,
        # von der reinen Präfix-Normalisierung unabhängiger Namens-Mismatch, der die Klausel selbst
        # nach einer naiven oos_-Strip-Normalisierung noch tot gelassen hätte. Der Handler-Name
        # MUSS exakt der kanonischen Form der Config-Klausel entsprechen.
        "min_profitable_folds_frac": (prof_folds_valid, prof_folds_reason),
        "min_excess_return": (excess_valid, excess_reason),
        "min_evaluable_folds": (eval_folds_valid, eval_folds_reason),
    }

    # Issue #554 — maschinenlesbares Delta-Dict (actual − threshold; für max_drawdown cap − actual,
    # damit einheitlich 'negativ = Gate verfehlt' gilt). Rein additiv/observational, kein Entscheidungs-
    # einfluss. So muss die Auswertung nicht auf String-Parsing der Reject-Gründe zurückfallen.
    oos_gate_deltas: dict[str, float] = {
        "oos_min_trades": float(n_trades - req_trades),
        "oos_min_total_return": float(total_return - req_return),
        "oos_min_expectancy": float(expectancy - req_exp),
        "oos_max_drawdown": float(req_max_dd - max_dd),
        "oos_min_win_rate": float(win_rate - req_win_rate),
    }
    if sortino is not None and req_sortino:
        oos_gate_deltas["oos_min_sortino"] = float(sortino - req_sortino)
    # Issue #614 — PSR-Delta (maschinenlesbar, für die #612-Constraints-Aggregation).
    if psr is not None and req_psr:
        oos_gate_deltas["oos_min_psr"] = float(float(psr) - float(req_psr))
    if pf is not None and req_pf:
        oos_gate_deltas["oos_min_profit_factor"] = float(pf - req_pf)
    if req_excess_return is not None and oos_metrics.get("oos_excess_return") is not None:
        oos_gate_deltas["oos_min_excess_return"] = float(oos_metrics["oos_excess_return"] - req_excess_return)
    if prof_folds_frac_delta is not None:
        oos_gate_deltas["oos_min_profitable_folds_frac"] = prof_folds_frac_delta

    # Issue #649 — kanonische Normalisierung (``_canonical_gate_key``, entfernt ein optionales
    # ``oos_``-Präfix) VOR jedem ``condition_map``-Lookup. ``tournament.json`` schreibt manche
    # Klauseln mit Präfix (``oos_min_psr``), die ``condition_map``-Handler sind durchgehend
    # un-präfigiert — ohne diese Normalisierung wurden die präfigierten Einträge STILL übersprungen
    # (kein Fehler, keine Warnung), obwohl der zugehörige Handler existiert.
    reasons = []
    for cond_name in tournament_cfg.get("eligible_requires_all", []):
        canon = _canonical_gate_key(cond_name)
        if canon in condition_map:
            valid, reason = condition_map[canon]
            if not valid:
                reasons.append(reason)

    any_conditions = tournament_cfg.get("eligible_requires_any", [])
    if any_conditions:
        any_valid = False
        any_reasons = []
        for cond_name in any_conditions:
            canon = _canonical_gate_key(cond_name)
            if canon in condition_map:
                valid, reason = condition_map[canon]
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

    # Issue #803 — ein Trial mit einer nachweislich UNGUELTIGEN Risikokennzahl (Kohaerenz-
    # Verletzung #589/#620/#756/#771, ODER oekonomischer Ruin #801) ist NIE selektionsfaehig,
    # unabhaengig davon, ob er die uebrigen Gates zufaellig besteht. Root-Cause #803: vorher
    # disqualifizierte NUR die STUDY-weite Verletzungsrate (``check_study_coherence_violation_rate``,
    # #773) — ein einzelner ungueltiger Trial blieb individuell eligible und konnte Study-Gewinner
    # werden. Diese Klausel ist bewusst UNBEDINGT (kein ``eligible_requires_all``-Opt-in): eine
    # nachweislich falsche Kennzahl darf niemals ein Gate bestehen, das genau diese Kennzahl prueft.
    if oos_metrics.get("oos_coherence_violation") or oos_metrics.get("equity_ruined"):
        reasons.append(
            "REJECT_OOS_INVALID_METRICS: oos_coherence_violation="
            f"{bool(oos_metrics.get('oos_coherence_violation'))}, equity_ruined="
            f"{bool(oos_metrics.get('equity_ruined'))} — Risikokennzahl nicht selektionsfaehig "
            "(#801/#803)."
        )

    return {
        "oos_evaluated": True,
        "oos_eligible": len(reasons) == 0,
        "oos_metrics": oos_metrics,
        "oos_rejection_reasons": reasons,
        # Issue #554 — numerische Gate-Deltas (metric → actual − threshold) für die maschinen-
        # lesbare Forensik im optimizer_trial_completed-Event.
        "oos_gate_deltas": oos_gate_deltas,
        # Issue #562 — effektive (kostenrelativ abgeleitete) Expectancy-Schwelle für die Telemetrie.
        # None ⇒ statisches Legacy-Gate war maßgeblich (k_alpha nicht gesetzt).
        "effective_expectancy_gate": effective_expectancy_gate,
        # Issue #684 — welche Kostenquelle das effektive Gate tatsächlich gespeist hat: 'telemetry'
        # (round_trip_cost_bps aus dem echten Backtest), 'config_default' (k_alpha gesetzt, aber
        # Telemetrie fehlte ⇒ Config-abgeleiteter Schätzwert, #684-Fix) oder 'static' (k_alpha gar
        # nicht konfiguriert ⇒ Legacy-Gate).
        "expectancy_gate_cost_source": expectancy_gate_cost_source,
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
    expectancy   = metrics.get("expectancy", 0.0)

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


_unknown_asset_class_policy_cache: str | None = None


def _read_unknown_asset_class_policy() -> str:
    """Issue #898 Fix 2 — ``backtest.json['unknown_asset_class_policy']`` ∈ {'reject', 'default'},
    Default 'reject' (fail-loud statt der Pre-#898 fail-open-DEFAULT-Kostenkonstante). Gecached
    (Hot-Path, wie die übrigen ``_read_*``-Konfig-Reader in diesem Modul)."""
    global _unknown_asset_class_policy_cache
    if _unknown_asset_class_policy_cache is not None:
        return _unknown_asset_class_policy_cache
    val = "reject"
    try:
        cfg_path = config_dir() / "backtest.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("unknown_asset_class_policy")
            if raw in ("reject", "default"):
                val = raw
    except (OSError, ValueError, TypeError):
        pass
    _unknown_asset_class_policy_cache = val
    return val


class InstrumentMetadataIncompleteError(ValueError):
    """Issue #898 Fix 2 — ein Symbol ohne aufloesbare asset_class (weder im Instrument-Map noch
    per spread_bps_by_symbol-Override) wird ABGEWIESEN statt still mit einer erfundenen
    Kostenkonstante versorgt zu werden (REJECT_INSTRUMENT_METADATA_INCOMPLETE)."""


def _resolve_asset_class_for_symbol(inst_id_str: str, *, policy: str = "reject") -> str:
    """Issue #566/#775 — Asset-Class-Lookup über ``instrument_map.json`` (Symbol → ``asset_class``),
    Single Source of Truth für JEDEN Aufrufer, der eine Asset-Class-Konstante für die
    Spread-/Kostenauflösung braucht (Worker-Kostenauflösung UND der #775-Kosten-Fallback-Reader —
    vorher zwei potenziell divergierende Kopien derselben Lookup-Schleife).

    Issue #898 — ``'UNKNOWN'`` (nicht mehr still ``'DEFAULT'``) bei fehlendem Eintrag ODER einer
    fehlenden/leeren/wörtlich ``'unknown'`` asset_class im Map. ``policy`` (aus
    ``backtest.json['unknown_asset_class_policy']``, Default ``'reject'``) entscheidet die
    Konsequenz: ``'reject'`` wirft ``InstrumentMetadataIncompleteError`` (fail-loud — das
    47%-des-Universums-Symptom aus #898 darf nie wieder auf DEFAULT=4.0bps statt EQUITY=3.0bps
    verrechnet werden); ``'default'`` reproduziert das alte fail-open-Verhalten explizit opt-in."""
    asset_class_key = "UNKNOWN"
    try:
        instrument_map_path = str(config_dir() / "instrument_map.json")
        with open(instrument_map_path, "r", encoding="utf-8") as f:
            inst_map = json.load(f).get("instruments", {})
        for _, inst_data in inst_map.items():
            if inst_data.get("symbol") == inst_id_str:
                raw = (inst_data.get("asset_class") or "").strip()
                asset_class_key = raw.upper() if raw and raw.lower() != "unknown" else "UNKNOWN"
                break
    except Exception:
        pass
    if asset_class_key == "UNKNOWN" and policy != "default":
        raise InstrumentMetadataIncompleteError(
            f"REJECT_INSTRUMENT_METADATA_INCOMPLETE: {inst_id_str} hat weder einen "
            f"spread_bps_by_symbol-Override noch eine aufgelöste asset_class in "
            f"instrument_map.json (Issue #898). unknown_asset_class_policy='reject' (Default) "
            f"lehnt das Symbol ab, statt es fail-open mit der DEFAULT-Kostenkonstante zu "
            f"verrechnen (vorher: 4.0bps statt 3.0bps EQUITY-Spread, 33% Kostenüberschätzung)."
        )
    return asset_class_key


def tick_floor_spread_bps(median_price: float | None, tick_size: float | None) -> float:
    """Issue #956 (Katalog D, Pitfall #301) — physikalische UNTERGRENZE für den Round-Trip-Spread
    eines Instruments: ``1e4 * tick_size / median_price`` [bps]. Bei einem Mindestpreisschritt
    ``tau`` und Preis ``P`` kann der reale Quote-Spread nie enger sein als ein Tick — eine
    Kostenkonstante je Asset-Klasse (z. B. EQUITY=3.0bps) unterschätzt den Round-Trip bei einem
    $2-Micro-Cap ($0.01-Tick) um Faktor ~17 (50bps physikalische Untergrenze vs. 3.0bps konfiguriert).

    Die Schranke ist bewusst KONSERVATIV (unterstellt genau 1 Tick Quote-Spread, ignoriert
    Broker-Aufschlag/Markttiefe/Slippage) — geeignet als Untergrenze (``max(...)`` mit der
    konfigurierten Kostenkonstante), nie als Punktschätzung des tatsächlichen Spreads.

    ``median_price``/``tick_size`` fehlend oder <= 0 ⇒ 0.0 (kein Floor anwendbar, fail-open)."""
    if not median_price or median_price <= 0 or not tick_size or tick_size <= 0:
        return 0.0
    return 1e4 * float(tick_size) / float(median_price)


def _resolve_price_precision_for_symbol(inst_id_str: str) -> int | None:
    """Issue #956 — Analog zu ``_resolve_asset_class_for_symbol``: liest ``price_precision`` aus
    ``instrument_map.json`` (Single Source of Truth) für die Tick-Grössen-Ableitung
    (``tick_size = 10 ** -price_precision``). ``None`` bei fehlendem Eintrag/Lesefehler
    (fail-open — der Aufrufer behandelt das wie "kein Tick-Floor anwendbar")."""
    try:
        instrument_map_path = str(config_dir() / "instrument_map.json")
        with open(instrument_map_path, "r", encoding="utf-8") as f:
            inst_map = json.load(f).get("instruments", {})
        for _, inst_data in inst_map.items():
            if inst_data.get("symbol") == inst_id_str:
                pp = inst_data.get("price_precision")
                return int(pp) if pp is not None else None
    except Exception:
        pass
    return None


def _quick_median_price_from_catalog(catalog_path, inst_id_str: str,
                                     max_rows: int = 500) -> float | None:
    """Issue #956 — schneller, beschränkter Preis-Sample für die Tick-Untergrenze
    (``tick_floor_spread_bps``): liest NUR die letzte Parquet-Row-Group von bid_price/ask_price
    direkt via pyarrow (analog ``sweep._load_symbol_bar_quality_sample``, ohne die volle
    NautilusTrader-``ParquetDataCatalog``-Materialisierung). Muss VOR ``load_ticks_from_catalog``
    laufen können (der volle Tick-Load braucht bereits den aufgelösten ``spread_bps`` als
    Parameter — zirkuläre Abhängigkeit, siehe Docstring von ``resolve_spread_bps``).

    Fail-open: JEDER Fehler liefert ``None`` (kein Tick-Floor, bit-identisches Verhalten zum
    Pre-#956-Zustand)."""
    try:
        import pyarrow.parquet as pq
        pq_file = Path(catalog_path) / "data" / "quote_tick" / inst_id_str / "data.parquet"
        if not pq_file.exists():
            return None
        pf = pq.ParquetFile(str(pq_file))
        cols = [c for c in ("bid_price", "ask_price") if c in pf.schema.names]
        if len(cols) < 2:
            return None
        last_rg = pf.metadata.num_row_groups - 1
        if last_rg < 0:
            return None
        df = pf.read_row_group(last_rg, columns=cols).to_pandas()
        if df.empty:
            return None
        if len(df) > max_rows:
            df = df.tail(max_rows)
        median_mid = float(((df["bid_price"].astype(float) + df["ask_price"].astype(float)) / 2.0).median())
        return median_mid if median_mid > 0 else None
    except Exception:
        return None


def resolve_spread_bps(inst_id_str: str,
                       spread_bps_by_asset_class: dict | None,
                       spread_bps_by_symbol: dict | None,
                       asset_class_key: str = "DEFAULT",
                       *, tick_floor_bps: float = 0.0) -> float:
    """Issue #566 — Single Source of Truth für die Spread-Auflösung (bps).

    Auflösungsreihenfolge (strikt): Symbol-Override (``spread_bps_by_symbol[inst_id]``) →
    Asset-Class (``spread_bps_by_asset_class[asset_class_key]``) → 0.0. Ein symbol-spezifischer
    Override übersteuert die grobe Asset-Class-Konstante, damit ein zu weiter EQUITY-Spread
    liquide Blue-Chips (z. B. TSLA.ETORO ~2 bps) nicht fälschlich unrentabel macht. Fehlen beide
    Maps ⇒ 0.0 (kein Spread-Modeling, rückwärtskompatibel).

    Issue #898 Fix 3 — ``'UNKNOWN'`` darf NIE still auf ``'DEFAULT'`` abbilden (das war exakt die
    Root-Cause: 47% des Universums lösten über den Fail-Open-Pfad auf DEFAULT=4.0bps statt
    EQUITY=3.0bps auf). Ein ``asset_class_key``, der weder ``'UNKNOWN'`` noch in
    ``spread_bps_by_asset_class`` vorhanden ist, ist ein KONFIGURATIONSFEHLER (ein im
    Instrument-Map registrierter Asset-Class-Wert ohne Kosten-Eintrag) und wirft — nicht die
    Instrument-Metadaten sind hier unvollständig, sondern die Kosten-Konfiguration.

    Issue #956 (Katalog D) — ``tick_floor_bps`` (Default 0.0, additiv opt-in, bit-identisch für
    jeden Aufrufer, der ihn nicht setzt) ist eine physikalische UNTERGRENZE
    (``tick_floor_spread_bps``): das Ergebnis ist NIE kleiner als dieser Wert, unabhängig davon,
    ob die Config-Konstante (Symbol-Override oder Asset-Class) darunter liegt. ``max(...)`` ist
    Absicht — bei Kostenschätzung ist die konservativere (höhere) Zahl die sicherere."""
    if spread_bps_by_symbol and inst_id_str in spread_bps_by_symbol:
        return max(float(spread_bps_by_symbol[inst_id_str]), tick_floor_bps)
    if not spread_bps_by_asset_class:
        return tick_floor_bps
    if asset_class_key not in spread_bps_by_asset_class:
        raise ValueError(
            f"resolve_spread_bps: asset_class_key='{asset_class_key}' ({inst_id_str}) ist nicht in "
            f"spread_bps_by_asset_class ({sorted(spread_bps_by_asset_class)}) — stiller Rückfall auf "
            f"DEFAULT ist seit Issue #898 verboten (Konfigurationsfehler, kein unbekanntes Symbol)."
        )
    return max(float(spread_bps_by_asset_class[asset_class_key]), tick_floor_bps)


def resolve_atr_floor_bps(inst_id_str: str,
                          atr_floor_bps_by_asset_class: dict | None,
                          asset_class_key: str = "DEFAULT") -> float:
    """Issue #924 — asset-class-aufgelöste Untergrenze für den ATR-Wert des Trailing-Stops
    (bps des Preises), Single Source of Truth analog zu ``resolve_spread_bps`` (#566).

    Der Floor verhindert, dass ``hourly_strategy_base._effective_atr_value`` (#897 Fix 4)
    bei einem degenerierten Nautilus-``AverageTrueRange`` (exakt 0, z. B. High==Low==PrevClose)
    den Trailing-Stop auf das Preis-Extremum kollabieren lässt. Vor #924 war der Wert ein
    flacher, in ``HourlyStrategyConfig.atr_floor_bps`` hart kodierter Default (2.0 bps) für
    jedes Symbol — für Krypto (siehe #920, deutlich höhere typische Preis-/ATR-Skalen)
    strukturell zu eng.

    Fehlt ``atr_floor_bps_by_asset_class`` (Key nicht in ``backtest.json``) ⇒ 2.0 (der alte
    flache Default, rückwärtskompatibel). Ein ``asset_class_key``, der weder ``'UNKNOWN'`` noch
    in der Map vorhanden ist, ist — wie bei ``resolve_spread_bps`` (#898 Fix 3) — ein
    KONFIGURATIONSFEHLER und wirft, statt still auf DEFAULT zurückzufallen."""
    if not atr_floor_bps_by_asset_class:
        return 2.0
    if asset_class_key not in atr_floor_bps_by_asset_class:
        raise ValueError(
            f"resolve_atr_floor_bps: asset_class_key='{asset_class_key}' ({inst_id_str}) ist "
            f"nicht in atr_floor_bps_by_asset_class ({sorted(atr_floor_bps_by_asset_class)}) — "
            f"stiller Rückfall würde einen Krypto-Floor unbemerkt auf den Equity-Wert reduzieren "
            f"(Issue #924, analog #898)."
        )
    return float(atr_floor_bps_by_asset_class[asset_class_key])


def cost_coupled_atr_floor_bps(base_floor_bps: float, *, atr_trailing_multiplier: float | None,
                               round_trip_cost_bps: float, min_stop_to_cost_ratio: float = 3.0) -> float:
    """Issue #1096 (Katalog #929) Fix Punkt 1 — hebt ``base_floor_bps`` (die asset-class-
    aufgelöste ``resolve_atr_floor_bps``-Konstante) zusätzlich auf ``min_stop_to_cost_ratio ·
    round_trip_cost_bps / atr_trailing_multiplier`` an, wenn dieser Wert grösser ist.

    Root-Cause #1096: der Asset-Klassen-Floor allein hat keinen Bezug zu den Round-Trip-Kosten —
    ``atr_median_bps`` lag in 18 von 56 Studies exakt auf dem Floor, mit nominalen Stopdistanzen
    von 2-7 bps gegen 100-200 bps realisierte adverse Bewegung UND < 3x der Round-Trip-Kosten
    (``invariants.check_stop_cost_ratio``): die Position kann den Stop dann strukturell nicht
    überleben, bevor die Kosten sie aufzehren. ``min_stop_to_cost_ratio`` ist DERSELBE Schwellenwert,
    den ``check_stop_cost_ratio`` bereits prüft (Default 3.0) — Gate und Konfiguration werden damit
    paritätisch (analog #666).

    ``atr_trailing_multiplier`` ist der für DIESEN Trial gesampelte Wert (kein studienweiter
    Median — der ist zum Zeitpunkt eines einzelnen Backtests nicht bekannt). Fehlt er (Strategie
    ohne Trailing-Stop-Parameter, ``None``) oder ist er <= 0, bleibt der Floor unverändert
    (fail-open — dieselbe Konvention wie ``resolve_atr_floor_bps`` bei fehlender Config)."""
    if not isinstance(atr_trailing_multiplier, (int, float)) or isinstance(atr_trailing_multiplier, bool):
        return base_floor_bps
    if atr_trailing_multiplier <= 0:
        return base_floor_bps
    cost_coupled = (min_stop_to_cost_ratio * round_trip_cost_bps) / float(atr_trailing_multiplier)
    return max(base_floor_bps, cost_coupled)


# Issue #1096 (Katalog #929) — BEWUSST NICHT umgesetzt (dokumentierter Scope-Cut, analog #843/#845
# Punkt 2 in dieser Codebasis): Fix Punkt 2 (der ATR-Schätzer soll ausschliesslich INFORMATIVE Bars
# konsumieren, #823-Analogon, statt der 24/7-aufgefüllten Kalenderachse mit volume=1.0) und Fix
# Punkt 3 (ein spaces.py-Preflight, der einen gesampelten atr_trailing_multiplier mit
# k · ATR_median < 3 · c_rt als infeasible an constraints_func meldet, #612) bleiben offen. Beide
# würden den Nautilus-``AverageTrueRange``-Indikator bzw. den TPE-Constraint-Sampler-Pfad anfassen
# — Root-Cause-Verifikation ohne einen echten Mehrsymbol-Referenzlauf (dasselbe Empirie-Problem wie
# bei #843 Pipelining) riskiert eine stille Korrektheitsregression im GESAMTEN ATR-Schätzer. Fix
# Punkt 1 (dieser Cost-Coupling, oben) behebt bereits den beobachtbaren Symptom-Kern (Floor
# unterschreitet 3x Round-Trip-Kosten) unabhängig von Fix Punkt 2/3.


def resolve_opening_range_session_open_hour(inst_id_str: str,
                                            session_open_hour_by_asset_class: dict | None,
                                            asset_class_key: str = "DEFAULT") -> int:
    """Issue #922 — asset-class-aufgelöste UTC-Stunde des Handelstag-Beginns für
    ``OpeningRangeBreakoutStrategy.opening_range_session_open_hour`` (nur wirksam unter
    ``opening_range_session_anchor='session_open_hour'``), Single Source of Truth analog
    ``resolve_spread_bps``/``resolve_atr_floor_bps``.

    Fehlt ``session_open_hour_by_asset_class`` ⇒ 13 (der Dataclass-Default, ≈ NYSE-Open,
    rückwärtskompatibel). Ein ``asset_class_key``, der weder ``'UNKNOWN'`` noch in der Map
    vorhanden ist, ist — wie bei ``resolve_spread_bps``/``resolve_atr_floor_bps`` — ein
    KONFIGURATIONSFEHLER und wirft, statt still zurückzufallen."""
    if not session_open_hour_by_asset_class:
        return 13
    if asset_class_key not in session_open_hour_by_asset_class:
        raise ValueError(
            f"resolve_opening_range_session_open_hour: asset_class_key='{asset_class_key}' "
            f"({inst_id_str}) ist nicht in opening_range_session_open_hour_by_asset_class "
            f"({sorted(session_open_hour_by_asset_class)}) — Issue #922, analog #898."
        )
    return int(session_open_hour_by_asset_class[asset_class_key])


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
    """Issue #665 — DEPRECATED für fold-übergreifende Aggregation/Vergleiche (PBO, Fold-Median-
    Ranking): extrahiert je Fold den ANNUALISIERTEN OOS-Sortino. ``_get_annualization_factor``
    leitet den Faktor EMPIRISCH aus der Kalender-Span JEDES Folds ab (Wochenenden/Feiertage/
    Lücken variieren pro Fold) ⇒ die zurückgegebenen Werte sind über Folds NICHT kommensurabel
    (ein Fold mit kürzerer Span wird stärker hochskaliert als einer mit längerer, bei identischer
    Perioden-Performance). Bleibt NUR als forensische Telemetrie (``sortino_ratio_fold_median``)
    erhalten. Für jede fold-übergreifende Aggregation IMMER ``collect_oos_fold_sortino_periods``
    (annualisierungs-invariant, per-Perioden) verwenden — siehe AGENTS.md Pitfall (#665)."""
    return [float(f["sortino_ratio"]) for f in per_fold_oos if f is not None and f.get("sortino_ratio") is not None]


def collect_oos_fold_sortino_periods(per_fold_oos: list[dict]) -> list[float]:
    """Issue #665 — die KANONISCHE fold-übergreifende Sortino-Grösse: der PER-PERIODEN (nicht
    annualisierte) OOS-Sortino je Fold (Reihenfolge erhalten, None-sicher übersprungen).

    Anders als ``collect_oos_fold_sortinos`` (annualisiert, fold-spezifisch skaliert) ist dieser
    Wert annualisierungs-INVARIANT: er trägt keinen fold-spezifischen Kalender-Faktor und ist
    daher über Folds UNTERSCHIEDLICHER Kalenderabdeckung direkt vergleichbar/mittelbar. Jede
    fold-übergreifende Aggregation (PBO/CSCV-Eingaben, Fold-Median-Telemetrie) MUSS diese Funktion
    nutzen, nicht ``collect_oos_fold_sortinos``."""
    return [float(f["sortino_period"]) for f in per_fold_oos if f is not None and f.get("sortino_period") is not None]


def collect_oos_fold_returns(per_fold_oos: list[dict]) -> list[float]:
    """Issue #589/#590 — je Fold den OOS-total_return (Reihenfolge erhalten, None-sicher).

    Die Fold-DISPERSION (Reward-seitige Fold-Konsistenz-Strafe) läuft nach #589 über den RETURN,
    nicht über den Fold-Sortino: der Return ist die gut konditionierte Größe, der Fold-Sortino
    (Median n=4, Schätzfehler ±3–5 Einheiten; Explosionen bei verlustarmen Folds) ist es nicht."""
    return [float(f["total_return"]) for f in per_fold_oos if f is not None and f.get("total_return") is not None]


_fold_winsorize_cache: tuple | None = None


def _read_fold_winsorize() -> tuple:
    """Issue #623 — (fold_winsorize_lower, fold_winsorize_upper) aus tournament.json (gecached).
    Fehlen die Keys ⇒ (None, None) ⇒ _winsorize ist ein No-Op (Legacy, bit-identisch, Zero-Hardcoding)."""
    global _fold_winsorize_cache
    if _fold_winsorize_cache is not None:
        return _fold_winsorize_cache
    lo = hi = None
    try:
        cfg = config_dir() / "tournament.json"
        if cfg.exists():
            d = json.loads(cfg.read_text("utf-8")) or {}
            lo, hi = d.get("fold_winsorize_lower"), d.get("fold_winsorize_upper")
    except Exception:
        pass
    _fold_winsorize_cache = (lo, hi)
    return _fold_winsorize_cache


_fold_profit_epsilon_cache: float | None = None


def _read_fold_profit_epsilon() -> float:
    """Issue #634 — Rausch-Boden fuer den Profitable-Folds-Zaehler aus tournament.json (gecached).
    Fehlt der Key ⇒ 0.0 (Legacy striktes ``> 0.0``, bit-identisch, Zero-Hardcoding)."""
    global _fold_profit_epsilon_cache
    if _fold_profit_epsilon_cache is not None:
        return _fold_profit_epsilon_cache
    val = 0.0
    try:
        cfg = config_dir() / "tournament.json"
        if cfg.exists():
            d = json.loads(cfg.read_text("utf-8")) or {}
            v = d.get("fold_profit_epsilon")
            if v is not None and float(v) > 0.0:
                val = float(v)
    except Exception:
        pass
    _fold_profit_epsilon_cache = val
    return _fold_profit_epsilon_cache


def _winsorize(values, lower, upper) -> list[float]:
    """Issue #623 — klemmt eine Werteliste auf ihre ``[lower, upper]``-Perzentile (Extreme gekappt, KEIN
    Entfernen ⇒ Länge/Reihenfolge erhalten). ``lower``/``upper`` None oder leere Liste ⇒ unverändert.
    Bei wenigen Werten (≤ 4 Folds) treffen die 5/95-Perzentile i. d. R. Min/Max ⇒ effektiv No-Op; erst
    bei vielen Folds mit Ausreissern (z. B. Fold-Sortino +227) greift die Klemmung."""
    vals = [float(v) for v in values]
    if not vals or lower is None or upper is None:
        return vals
    s = sorted(vals)
    n = len(s)
    lo_v = s[min(n - 1, max(0, int(round(float(lower) * (n - 1)))))]
    hi_v = s[min(n - 1, max(0, int(round(float(upper) * (n - 1)))))]
    return [min(max(v, lo_v), hi_v) for v in vals]


# Issue #549/#550 — Häufigkeitskennzahlen bleiben GEPOOLT (über alle OOS-Trades).
# Issue #589 — der Sortino wird NICHT mehr zum Fold-Median aggregiert (Kohärenz-Verlust +
# Median-Maskierung katastrophaler Folds), sondern bleibt der GEPOOLTE Wert aus der OOS-Equity-Kurve.
_POOLED_METRICS = ("win_rate", "expectancy", "profit_factor")

# Issue #263/#288/#592 — der "All-Win"-Sentinel für die Score-Normalisierung im Winner-Ranking
# (get_sentinel injiziert bis zu diesem Wert, wenn ein profit_factor/sortino wegen 0 Verlusten
# undefiniert ist). Als BENANNTE Konstante (statt verstreuter 50.0-Literale), damit die
# Sentinel-Filterung nicht an einer hartcodierten Zahl klebt und bei künftiger Änderung nicht still
# bricht (Zero-Hardcoding, vgl. CODE_AUDIT). Bewusst ENTKOPPELT vom (entfernten) Sortino-Clip #588.
_ALL_WIN_SENTINEL = 50.0


def _assert_sortino_return_coherence(oos_metrics: dict, *, tol: float = 1e-4) -> None:
    """Issue #589/#756 — Kohärenz-Invariant (analog zum #528-Coherence-Check): der gepoolte OOS-
    Sortino (Risiko-adjustierter Return aus der OOS-Equity-Kurve) und der OOS-total_return
    beschreiben DENSELBEN Equity-Pfad ⇒ ihre Vorzeichen MÜSSEN übereinstimmen. Eine Verletzung bei
    ``|total_return| > tol`` ist ein Aggregationsdefekt (vorher 245/600 Trials mit return>0 ∧
    sortino<0). ERROR + Telemetrie-Flag (kein Abbruch — der Reward-Pfad bleibt robust).

    Issue #756 — VOR der Log-Return-Umstellung war eine Verletzungsrate von bis zu 43 % einer Study
    KEIN Aggregationsdefekt, sondern strukturell erwartbar (arithmetischer Sortino-Zähler vs.
    geometrischer total_return, Volatilitäts-Drag σ²/2). Seit `period_rets` in `_calculate_stats`
    auf Log-Returns umgestellt ist, gilt die Kohärenz PER KONSTRUKTION (Σ log(1+rᵢ) = log(1+
    total_return)) — eine hier noch auftretende Verletzung ist damit ein ECHTER Bug, kein
    erwartetes Restrauschen mehr. Siehe `automation.optimizer.invariants.check_log_return_coherence`
    (harter Regressionswächter im #742-Report, im Gegensatz zu diesem WARNING-Pfad hier).

    Issue #801 — die „PER KONSTRUKTION"-Garantie gilt NUR unter der Vorbedingung, dass die
    Equity-Kurve während des gesamten Fensters strikt positiv blieb (``assert_positive_equity``)
    UND jeder Log-Return finit ist (``PERIOD_RETURNS_NOT_FINITE``-Guard in ``_calculate_stats``).
    Beide Fälle setzen ``equity_ruined=True`` und leeren ``period_rets`` VOR diesem Check —
    eine Kurve mit Nulldurchgang erreicht diese Funktion also nie mit einem undefinierten
    Log-Return; sie erreicht sie mit ``sortino_ratio=None`` (früher Return oben, kein Trigger)."""
    tr = oos_metrics.get("total_return")
    sr = oos_metrics.get("sortino_ratio")
    if tr is None or sr is None:
        return
    tr = float(tr)
    sr = float(sr)
    if abs(tr) > tol and sr != 0.0 and (tr > 0.0) != (sr > 0.0):
        import logging
        logging.getLogger("optimizer").error(
            "COHERENCE_INVARIANT_VIOLATION (#589): sign(oos_sortino=%.6g) != "
            "sign(oos_total_return=%.6g) bei |return| > %.0e — gepoolter OOS-Sortino und Return "
            "müssen kohärent sein (derselbe Equity-Pfad).", sr, tr, tol,
        )
        oos_metrics["oos_coherence_violation"] = True
        # Issue #804 — strukturierter Rueckkanal: dieselbe Verletzung zusaetzlich in
        # oos_metrics['inference_diagnostics'] (existiert bereits, sobald _calculate_stats zuvor
        # lief) statt ausschliesslich im Subprozess-Log, das der Elternprozess nie sieht.
        oos_metrics.setdefault("inference_diagnostics", []).append({
            "code": "COHERENCE_INVARIANT_VIOLATION",
            "detail": f"sign(oos_sortino={sr:.6g}) != sign(oos_total_return={tr:.6g}) bei "
                     f"|return| > {tol:.0e}.",
            "value": sr,
        })


def assert_positive_equity(mtm_series) -> tuple[bool, int]:
    """Issue #801 — Positivitäts-Gate auf der Equity-Kurve. Ein ``AccountType.MARGIN``-Konto
    (gehebelte 1h-Krypto-Notional) kann während des Backtests durch Null gehen — das ist ein
    REALER Zustand, kein Randfall (empirisch: 44,3 % der Kurven mit Nulldurchgang liefern einen
    ENDLICHEN, aber VORZEICHENVERKEHRTEN Sortino, Monte-Carlo-Beleg im Issue-Katalog). Sobald die
    Kurve einmal ≤ 0 wird, ist jede nachfolgende Log-Rendite (``log(mtm_t/mtm_{t-1})``) undefiniert
    (Division durch/Logarithmus von ≤ 0) — die Inferenz darf dann nicht stillschweigend auf einer
    Teilmenge der Bars weiterrechnen.

    Rückgabe ``(True, -1)``, wenn die gesamte Serie strikt positiv ist, sonst
    ``(False, erster_nicht_positiver_index)``. Rein (kein I/O, keine Mutation)."""
    if mtm_series is None or len(mtm_series) == 0:
        return True, -1
    import numpy as np
    values = mtm_series.to_numpy(dtype=float)
    non_positive = np.where(values <= 0.0)[0]
    if non_positive.size == 0:
        return True, -1
    return False, int(non_positive[0])


def assert_return_series_identity(total_return: float, period_rets, *, tol: float = 1e-9,
                                  diagnostics: list | None = None) -> bool:
    """Issue #771 — die #756-Identität ``Σ log(1+rᵢ) = log(1+total_return)`` MASCHINELL geprüft,
    statt nur im Docstring behauptet (AGENTS.md Pitfall #230). Gilt PER KONSTRUKTION genau dann,
    wenn ``total_return`` und ``period_rets`` aus DERSELBEN Bar-Menge stammen — nach #771 ist das
    im Walk-Forward-Pfad der Fall (beide aus ``mtm_series``), im ``mtm_frames``-Fallback-Pfad
    (nicht-kontiguierliche Segmente) kann eine Restlücke bleiben (siehe ``NON_CONTIGUOUS_FOLD_
    SEGMENTS``-Telemetrie in ``_calculate_stats``).

    ``period_rets`` ist die pandas-Series der LOG-Returns (algebraisch aus ``np.diff(np.log(mtm))``
    seit #801, siehe ``_calculate_stats``). Rückgabe ``True`` bei einer Verletzung (für
    Tests/Telemetrie) — ERROR-Log + ``RETURN_SERIES_IDENTITY_VIOLATION``-Event, ändert selbst NIE
    ``total_return``/``period_rets`` (reine Diagnose, kein Reward-Pfad).

    Issue #801 — ``total_return <= -1`` macht ``math.log1p`` NICHT „keine Verletzung", sondern
    ist der KATASTROPHALSTE Fall: ein Konto, das mehr als sein volles Kapital verloren hat, kann
    per Definition keine wohldefinierte Log-Return-Identität haben. Der frühere Code fing das
    ``ValueError`` ab und lieferte ``False`` — genau der Bug, der 35 Study-Abbrüche mit einer
    NACHWEISLICH FALSCHEN Begründung erzeugte (die Kurve war nicht „inkohärent aggregiert", sie
    war schlicht ruiniert). Dieser Zustand wird jetzt als ``RETURN_SERIES_IDENTITY_UNDEFINED``
    telemetriert und als Verletzung (``True``) gewertet.

    Issue #804 — ``diagnostics`` (optional, Default ``None``) ist die strukturierte Rueckkanal-Liste
    (``metrics['inference_diagnostics']``): jede hier geloggte Verletzung wird ZUSAETZLICH als
    ``{'code', 'detail', 'value'}``-Dict angehaengt, falls eine Liste uebergeben wird. Root-Cause
    #804: alle vier Inferenzpfad-Diagnosen liefen bislang NUR ueber ``logging`` im Backtest-
    SUBPROZESS (``runner.py``, ``subprocess.run(capture_output=True)``) — der Optimizer-Elternprozess
    (und damit der `#742`-Report) sah davon NICHTS, der Log-Stream landete in einer Datei, die kein
    Aggregator liest und die #794 Sekunden spaeter loescht. ``None`` (Default, alle Bestandsaufrufer)
    bleibt bit-identisch (kein Diagnostics-Overhead ausserhalb von `_calculate_stats`)."""
    if period_rets is None or len(period_rets) == 0:
        return False
    try:
        target = math.log1p(float(total_return))
    except ValueError:
        import logging
        logging.getLogger("optimizer").error(
            "RETURN_SERIES_IDENTITY_UNDEFINED (#801): total_return=%.6g <= -1 — log1p(1+total_return) "
            "ist nicht definierbar (Equity-Kurve durch/unter Null). Das ist die STÄRKSTE mögliche "
            "Verletzung der #756-Identität, nicht 'keine Verletzung'.",
            total_return,
        )
        if diagnostics is not None:
            diagnostics.append({
                "code": "RETURN_SERIES_IDENTITY_UNDEFINED",
                "detail": "total_return <= -1 — log1p(1+total_return) nicht definierbar.",
                "value": float(total_return),
            })
        return True
    except (TypeError, OverflowError):
        return False
    try:
        # Issue #801 (Pitfall #240) — skipna=False erzwungen: eine stillschweigend NaN-reduzierte
        # Summe waere eine Aussage ueber eine Teilmenge der Bars, keine ueber die volle Serie.
        log_sum = float(period_rets.sum(skipna=False))
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(log_sum):
        import logging
        logging.getLogger("optimizer").error(
            "PERIOD_RETURNS_NOT_FINITE (#801): Σlog(1+rᵢ) ist nicht endlich (NaN/±inf) — die "
            "Renditeserie der Inferenz enthält einen nicht-finiten Wert.",
        )
        if diagnostics is not None:
            diagnostics.append({
                "code": "PERIOD_RETURNS_NOT_FINITE",
                "detail": "Σlog(1+rᵢ) ist nicht endlich (NaN/±inf).",
                "value": log_sum,
            })
        return True
    # Issue #801 — Toleranz von einer rein ABSOLUTEN (1e-9) auf eine zusätzlich RELATIVE Schranke
    # gehoben: bei T ≈ 4000+ Summanden akkumuliert Gleitkomma-Rundung über eine absolute
    # 1e-9-Schranke hinaus, ohne dass die Identität tatsächlich verletzt ist.
    eff_tol = max(tol, 1e-12 * abs(log_sum))
    gap = log_sum - target
    if abs(gap) > eff_tol:
        import logging
        logging.getLogger("optimizer").error(
            "RETURN_SERIES_IDENTITY_VIOLATION (#771): Σlog(1+rᵢ)=%.10g != log(1+total_return)=%.10g "
            "(Differenz=%.3e > tol=%.3e) — total_return und die Renditeserie der Inferenz stammen "
            "NICHT aus derselben Bar-Menge (#756-Identität verletzt).",
            log_sum, target, gap, eff_tol,
        )
        if diagnostics is not None:
            diagnostics.append({
                "code": "RETURN_SERIES_IDENTITY_VIOLATION",
                "detail": f"Σlog(1+rᵢ)={log_sum:.10g} != log(1+total_return)={target:.10g} "
                         f"(Δ={gap:.3e} > tol={eff_tol:.3e}).",
                "value": gap,
            })
        return True
    return False


def _assert_is_oos_sortino_coherence(is_basis: str | None, is_sortino, oos_basis: str | None, oos_sortino) -> bool:
    """Issue #613 — Kohärenz-Invariant für die Divergenz-Strafe (analog ``_assert_sortino_return_coherence``).

    Der IS- und der OOS-Sortino gehen als ``overfit_gap = is_sortino − oos_base`` in DIESELBE Reward-
    Differenz ein. Sie MÜSSEN daher aus derselben Aggregationsebene stammen (beide ``pooled_equity_curve``):
    ein Fold-/Symbol-Median-IS-Sortino gegen einen gepoolten OOS-Sortino vergleicht Grössen verschiedener
    Fenster/Skalen und treibt den Term systematisch in die Sättigung (#613: corr=0.185, 96 % im oos_luck-Ast).
    Verletzung ⇒ ERROR + Telemetrie-Flag (kein Abbruch — der Reward-Pfad bleibt robust). Rückgabe: True bei
    Verletzung (für Telemetrie/Tests)."""
    if is_sortino is None or oos_sortino is None:
        return False
    if is_basis and oos_basis and is_basis != oos_basis:
        import logging
        logging.getLogger("optimizer").error(
            "IS_OOS_SORTINO_AGGREGATION_INCOHERENCE (#613): is_basis=%s (sortino=%.6g) != "
            "oos_basis=%s (sortino=%.6g) — IS- und OOS-Sortino der Divergenz-Strafe MÜSSEN aus "
            "derselben Aggregationsebene stammen (beide pooled_equity_curve).",
            is_basis, float(is_sortino), oos_basis, float(oos_sortino),
        )
        return True
    return False


_profitable_folds_weighting_cache: str | None = None
_recency_halflife_folds_cache: float | None = None
_recency_halflife_folds_cached: bool = False


def _read_profitable_folds_weighting() -> str:
    """Issue #664 — Gewichtungsmodus für ``oos_min_profitable_folds_frac`` aus
    ``tournament.json['profitable_folds_weighting']`` (gecached). ``'equal'`` (Default, fehlt der
    Key) gewichtet alle Folds gleich — bit-identisch zum Status quo. ``'recency'`` ist opt-in (siehe
    ``apply_fold_aggregation``/``_evaluate_oos_eligibility``). Ein unbekannter Wert fällt defensiv
    auf ``'equal'`` zurück (kein Fail-Loud hier — reine Gewichtungswahl, kein Struktur-Constraint)."""
    global _profitable_folds_weighting_cache
    if _profitable_folds_weighting_cache is not None:
        return _profitable_folds_weighting_cache
    val = "equal"
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("profitable_folds_weighting")
            if raw in ("equal", "recency"):
                val = raw
    except (OSError, ValueError, TypeError):
        val = "equal"
    _profitable_folds_weighting_cache = val
    return val


def _read_recency_halflife_folds() -> float | None:
    """Issue #664 — Halbwertszeit (in Folds) der exponentiellen Recency-Gewichtung
    (``tournament.json['recency_halflife_folds']``), gecached. ``None`` (Default) ⇒
    ``_fold_recency_weights`` fällt auf die milde Default-Halbwertszeit (= n_folds) zurück."""
    global _recency_halflife_folds_cache, _recency_halflife_folds_cached
    if _recency_halflife_folds_cached:
        return _recency_halflife_folds_cache
    val = None
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("recency_halflife_folds")
            if raw is not None and float(raw) > 0.0:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = None
    _recency_halflife_folds_cache = val
    _recency_halflife_folds_cached = True
    return val


def _fold_recency_weights(n_folds: int, halflife: float | None) -> list[float]:
    """Issue #664 — exponentielle Recency-Gewichte über ``n_folds`` (Index 0 = ältester Fold,
    ``n_folds-1`` = jüngster): ``weight_i ∝ 2^{-(n_folds-1-i)/halflife}`` (unnormiert; der Aufrufer
    normiert über die Summe). ``halflife`` None/≤0 ⇒ die (dokumentierte) milde Default-Halbwertszeit
    ``= n_folds`` (jeder Fold verliert über die volle Fold-Spanne die Hälfte seines relativen
    Gewichts). Rein, deterministisch."""
    if n_folds <= 0:
        return []
    hl = float(halflife) if halflife and halflife > 0.0 else float(n_folds)
    return [2.0 ** (-(n_folds - 1 - i) / hl) for i in range(n_folds)]


def _fold_regime_diagnostics(fold_returns: list[float]) -> dict:
    """Issue #664 — Stationaritäts-/Regime-DIAGNOSE der Fold-Return-Sequenz (rein informativ, KEIN
    Gate-/Reward-Einfluss): Vorzeichen-Autokorrelation (Lag-1) + Anteil Fold-Sign-Flips zwischen
    benachbarten Folds.

    ``fold_sign_flip_frac`` ist das ROBUSTE Primärsignal (auch bei sehr wenigen/ungleich verteilten
    Folds gut interpretierbar): wenige Flips ⇒ die Folds gruppieren sich in zusammenhängende
    Regime-Läufe (z. B. mehrere defunkte Alt-Folds gefolgt von einem tradeablen jüngsten Fold) statt
    zufällig zu alternieren. ``fold_sign_autocorr`` (Pearson, Lag-1) ergänzt dies, ist aber bei sehr
    kleiner, UNGLEICH verteilter Fold-Zahl (z. B. 3 negative + 1 positiver Fold bei n=4) statistisch
    sensitiv gegenüber der Klassen-Balance und kann dabei — trotz eines klaren "Lauf"-Musters — ein
    dem Vorzeichen nach unintuitives Ergebnis liefern (bekannte Small-N-Eigenschaft der Pearson-
    Korrelation bei unbalancierten binären Sequenzen); es ist daher ein SEKUNDÄRES Signal.

    Zeigt der Symbol-Datensatz eine strukturelle Nicht-Stationarität, ist das ein SYMBOL-Regime-
    Signal, keine Strategie-Schwäche — das Diagnose-Artefakt trennt diese beiden Interpretationen,
    ohne selbst eine Gate-Entscheidung zu treffen (siehe AGENTS.md, Issue #664).

    ``None``-Werte, wenn zu wenige vergleichbare (nicht-degenerierte) Fold-Paare vorliegen."""
    n = len(fold_returns)
    if n < 2:
        return {"fold_sign_autocorr": None, "fold_sign_flip_frac": None}
    signs = [1.0 if r > 0.0 else (-1.0 if r < 0.0 else 0.0) for r in fold_returns]
    comparable_pairs = [(a, b) for a, b in zip(signs, signs[1:]) if a != 0.0 and b != 0.0]
    flip_frac = (
        sum(1 for a, b in comparable_pairs if a != b) / len(comparable_pairs)
        if comparable_pairs else None
    )
    autocorr = None
    if n >= 3:
        mean_s = sum(signs) / n
        var_s = sum((s - mean_s) ** 2 for s in signs)
        if var_s > 0.0:
            cov = sum((signs[i] - mean_s) * (signs[i + 1] - mean_s) for i in range(n - 1))
            autocorr = cov / var_s
    return {"fold_sign_autocorr": autocorr, "fold_sign_flip_frac": flip_frac}


def apply_fold_aggregation(oos_metrics: dict, per_fold_oos_list: list[dict | None]) -> dict:
    """Issue #549/#550/#589/#590 — mutiert ``oos_metrics`` in place: EINE kohärente
    Aggregationsebene für den Sortino + Fold-Konsistenz-Telemetrie.

    Rein & deterministisch (kein I/O — die frühere doppelte ``tournament.json``-Lesung im Hot-Path
    ist mit dem Fold-Median-Sortino entfallen), damit unit-testbar losgelöst vom BacktestEngine.

    Issue #589 — Vor dem Fix mischte das Gate drei Aggregationslogiken in DERSELBEN Klausel und der
    Sortino wurde zum FOLD-MEDIAN aggregiert. Das (a) entkoppelte den Sortino vom Ergebnis
    (corr(oos_sortino, oos_total_return) −0.44…+0.24 je Study; 245/600 Trials return>0 ∧ sortino<0)
    und (b) maskierte über den Median (n=4, Standardfehler ≈ 0.63σ ⇒ ±3–5 Sortino-Einheiten) einen
    katastrophalen Fold vollständig. Fix: der ``sortino_ratio`` bleibt der GEPOOLTE Wert, den
    ``_calculate_stats`` bereits aus der konkatenierten, purged OOS-Equity-Kurve (``oos_mtm``)
    berechnet hat — DERSELBE Pfad, aus dem ``total_return`` kommt (``mtm_frames``). Damit sind Zähler
    und Nenner per Konstruktion kohärent (Kohärenz-Invariant ``_assert_sortino_return_coherence``).
    Der frühere Fold-Median wird nur noch forensisch als ``sortino_ratio_fold_median`` geführt; der
    gepoolte Wert zusätzlich als ``sortino_ratio_pooled`` (== ``sortino_ratio``).

    Die Fold-KONSISTENZ bleibt ein EIGENSTÄNDIGES Signal (nicht in den Sortino hineingemittelt):
    Gate ``oos_min_profitable_folds_frac`` (#550) + ``oos_min_evaluable_folds`` (#590); Reward
    ``fold_dispersion_weight`` auf ``pstdev(oos_fold_returns)`` (#589) mit Strafe für fehlende Folds
    (#590). ``win_rate``/``expectancy``/``profit_factor`` bleiben pooled; ``total_return``
    compoundiert; ``max_drawdown``/``total_trades``/``median_position_notional`` pooled.

    Für ``splits==1`` (Holdout/Single-Fold) ist pooled == der eine Fold ⇒ bit-identisch. Siehe
    AGENTS.md Pitfall #110/#113.
    """
    import statistics as _stats
    # Issue #623 — WINSORISIERUNG der Fold-Kennzahlen (fold_winsorize_lower/upper, tournament.json):
    # bei Fold-Sortino-Extremen (bis +227) klemmt sie die forensische Fold-Median-/Dispersions-Statistik
    # auf die konfigurierten Perzentile, statt ein falsches Robustheitsversprechen ohne Call-Site zu sein.
    # Fehlen die Keys ⇒ No-Op (Legacy, bit-identisch). Rein index-basiert, deterministisch.
    _w_lo, _w_hi = _read_fold_winsorize()
    oos_metrics["oos_fold_sortinos"] = _winsorize(collect_oos_fold_sortinos(per_fold_oos_list), _w_lo, _w_hi)
    # Issue #665 — die annualisierungs-INVARIANTE, über Folds kommensurable Parallelgrösse. Jede
    # fold-übergreifende Aggregation (PBO/CSCV, Fold-Median-Telemetrie) konsumiert AUSSCHLIESSLICH
    # diese Serie; ``oos_fold_sortinos`` (oben) bleibt nur noch forensische Anzeige-Telemetrie
    # (annualisiert, fold-spezifisch skaliert, siehe collect_oos_fold_sortinos-Docstring).
    oos_metrics["oos_fold_sortino_periods"] = _winsorize(
        collect_oos_fold_sortino_periods(per_fold_oos_list), _w_lo, _w_hi)
    # Issue #665 — literale Telemetrie-Alias (Akzeptanzkriterium): identische Liste unter dem im
    # Issue benannten Feldnamen, damit Log-/Proposal-Konsumenten sie unter beiden Namen finden.
    oos_metrics["per_fold_oos_sortino_period"] = oos_metrics["oos_fold_sortino_periods"]
    winsorized_fold_returns = _winsorize(collect_oos_fold_returns(per_fold_oos_list), _w_lo, _w_hi)
    oos_metrics["oos_fold_returns"] = winsorized_fold_returns

    valid_folds = [f for f in per_fold_oos_list if f is not None]
    n_folds_total = len(per_fold_oos_list)
    # Issue #634 — Profitable-Folds-Zähler ROBUSTIFIZIERT (Rausch-Doppelbestrafung, vgl. #589/#616-
    # Fold-Dispersions-Strafe, die dieselbe Streuung bereits im Reward abbildet):
    # (1) ε-Schwelle statt striktem ">0.0" — ein Fold mit Return +1e-7 (statistisches Rauschen bei
    #     T≈50/Fold) zählte vorher genauso als "profitabel" wie einer mit +0.02; ein Fold mit −1e-7
    #     kippte den Zähler in die andere Richtung, obwohl beide vom wahren Nullpunkt ununterscheidbar
    #     sind. fold_profit_epsilon (tournament.json) definiert den Rausch-Boden; fehlt der Key ⇒ 0.0
    #     (Legacy striktes ">0.0", bit-identisch, Zero-Hardcoding).
    # (2) Zählung auf der WINSORISIERTEN Fold-Return-Sequenz (identisch zu
    #     oos_metrics["oos_fold_returns"]) statt auf den rohen per_fold_oos_list-Werten — die
    #     konfigurierte Winsorisierung (fold_winsorize_lower/upper) war vorher deklariert (#623), aber
    #     im Zähler selbst ungenutzt.
    fold_profit_epsilon = _read_fold_profit_epsilon()
    n_folds_profitable = sum(1 for r in winsorized_fold_returns if r > fold_profit_epsilon)
    oos_metrics["oos_folds_total"] = n_folds_total
    oos_metrics["oos_profitable_folds"] = n_folds_profitable
    # Issue #676 — Nenner der Fraktion ist die Anzahl EVALUIERBARER Folds (Folds mit einem
    # tatsächlich berechneten Return — exakt die Population, über die ``winsorized_fold_returns``
    # bereits läuft, ``collect_oos_fold_returns`` filtert None-/No-Trade-Folds bereits heraus),
    # NICHT ``n_folds_total`` (das No-Trade-Folds — Folds ohne Signal, KEINE Unprofitabilität —
    # ungefiltert mitzählt). Root-Cause: ein Fold ohne Trade (``per_fold_oos_list[i] is None``)
    # zählte vorher als "nicht profitabel" im NENNER, obwohl er in KEINEM Fall in den Zähler
    # einging — eine strukturelle Asymmetrie, die die Fraktion künstlich nach unten zog (1
    # profitabler + 3 No-Trade-Folds ergab 0.25 statt der korrekten 1.0). ``oos_folds_total``
    # bleibt UNVERÄNDERT als Rohtelemetrie (alle Folds, inkl. No-Trade) erhalten.
    n_folds_evaluable = len(winsorized_fold_returns)
    oos_metrics["oos_folds_evaluable"] = n_folds_evaluable
    # Issue #664 — EQUAL-gewichtete Fraktion (bleibt die kanonische ``oos_profitable_folds_frac``,
    # unabhängig vom konfigurierten Gewichtungsmodus).
    oos_metrics["oos_profitable_folds_frac"] = (
        n_folds_profitable / n_folds_evaluable if n_folds_evaluable > 0 else 0.0)

    # Issue #664 — Diagnose-Artefakt: das Profitable-Folds-Gate ist bei nicht-stationären
    # Deployment-Zielen strukturell an Regime-Heterogenität gekoppelt (kein Bug, eine echte
    # Design-Spannung). Zwei rein ADDITIVE Telemetrie-Erweiterungen, beide OHNE Gate-/Reward-
    # Wirkung, solange ``profitable_folds_weighting`` nicht explizit auf ``'recency'`` steht:
    # (a) das Stationaritäts-/Regime-Signal (Vorzeichen-Autokorrelation + Fold-Sign-Flip-Anteil),
    # (b) die RECENCY-gewichtete Parallel-Fraktion (exponentielle Fold-Gewichte, jüngster Fold am
    # stärksten gewichtet) — auf DERSELBEN winsorisierten Return-Sequenz + demselben ε-Rauschboden
    # wie die Equal-Fraktion, damit beide Grössen direkt vergleichbar sind.
    oos_metrics.update(_fold_regime_diagnostics(winsorized_fold_returns))
    if n_folds_total > 0:
        valid_indices = [
            i for i, f in enumerate(per_fold_oos_list)
            if f is not None and f.get("total_return") is not None
        ]
        profitable_by_index = [False] * n_folds_total
        for idx, val in zip(valid_indices, winsorized_fold_returns):
            profitable_by_index[idx] = val > fold_profit_epsilon
        recency_weights = _fold_recency_weights(n_folds_total, _read_recency_halflife_folds())
        # Issue #676 — Nenner NUR über die EVALUIERBAREN Fold-Indizes (``valid_indices``), analog zur
        # Equal-Fraktion oben: ein No-Trade-Fold trägt sein Recency-Gewicht nicht in den Nenner, sonst
        # zieht dieselbe Nenner-Asymmetrie die recency-Fraktion künstlich nach unten.
        total_w = sum(recency_weights[i] for i in valid_indices) or 1.0
        oos_metrics["oos_profitable_folds_frac_recency"] = (
            sum(recency_weights[i] for i in valid_indices if profitable_by_index[i]) / total_w
        )
    else:
        oos_metrics["oos_profitable_folds_frac_recency"] = 0.0
    # Issue #664 — BEWUSSTE Entscheidung (kein blinder Default-Wechsel): welche der beiden
    # Fraktionen das Gate tatsächlich konsumiert, entscheidet ausschliesslich
    # ``_evaluate_oos_eligibility`` über ``profitable_folds_weighting`` (Default ``'equal'``,
    # bit-identisch). Diese Funktion liefert NUR die Zahlen, trifft keine Gate-Entscheidung.

    # Issue #589 — sortino_ratio bleibt der GEPOOLTE Wert (kohärent mit total_return). Forensische
    # Zweitwerte: der gepoolte (redundant, aber explizit benannt) und der frühere Fold-Median.
    # Issue #665 — ``sortino_ratio_fold_median`` (annualisiert) ist DEPRECATED für jede
    # fold-übergreifende Vergleichs-/Aggregationslogik (fold-spezifisch inkommensurabel, siehe
    # collect_oos_fold_sortinos-Docstring) und bleibt NUR als forensische Anzeige-Telemetrie
    # erhalten. ``sortino_period_fold_median`` (per-Perioden) ist die kanonische, annualisierungs-
    # invariante Nachfolgegrösse für jede Konsumenten-Logik (PBO, Dashboards, Diagnose).
    fold_sortino_vals = oos_metrics["oos_fold_sortinos"]
    fold_sortino_period_vals = oos_metrics["oos_fold_sortino_periods"]
    oos_metrics["sortino_ratio_pooled"] = oos_metrics.get("sortino_ratio")
    oos_metrics["sortino_ratio_fold_median"] = (
        _stats.median(fold_sortino_vals) if fold_sortino_vals else None)
    oos_metrics["sortino_period_fold_median"] = (
        _stats.median(fold_sortino_period_vals) if fold_sortino_period_vals else None)

    if valid_folds:
        # Häufigkeitskennzahlen bleiben gepoolt (bereits in oos_metrics); forensische Kopie.
        for key in _POOLED_METRICS:
            if key in oos_metrics:
                oos_metrics[f"{key}_pooled"] = oos_metrics.get(key)

    _assert_sortino_return_coherence(oos_metrics)
    return oos_metrics


def compute_fold_boundaries(start_ns: int, walk_forward_dict: dict) -> list[tuple[int, int, int]]:
    """Issue #490/#675 — die EINZIGE Quelle der Walk-Forward-Fold-Geometrie.

    Einzelpass-Backtest mit fragmentiertem Holdout. Rein (kein I/O, kein State, deterministisch).
    Liefert je Fold ein Tripel ``(is_start_ns, oos_start_ns, oos_end_ns)``. Die OOS-Sub-Fold-Grenzen
    sind IMMER kontiguierlich und UNABHÄNGIG vom ``retrain``-Modus (siehe unten):

      * ``purge_end_ns = start_ns + is_window_ns + embargo_period_ns``
      * ``oos_start_ns = purge_end_ns + fold * oos_window_ns``
      * ``oos_end_ns   = oos_start_ns + oos_window_ns``

    ``is_start_ns`` je Fold hängt vom Modus ``walk_forward_dict.get("retrain", False)`` ab:

      * ``retrain=False`` (Default, Zero-Hardcoding, bit-identisch zum Pre-#675-Verhalten) —
        ``is_start_ns = start_ns`` für ALLE Folds: EIN statisch am Datenanfang verankertes IS-Fenster.
        Das ist KEIN echter Walk-Forward — er testet Regime-PERSISTENZ (ein bis zu
        ``splits * oos_window_days`` Tage altes Referenzfenster wird ungeprüft auf immer weiter
        entfernte OOS-Daten angewandt), nicht Config-ROBUSTHEIT (AGENTS.md Pitfall #142).
      * ``retrain=True`` (Opt-in) — ``is_start_ns = oos_start_ns − embargo_period_ns − is_window_ns``
        je Fold: ein ROLLENDES, dem jeweiligen OOS-Fold unmittelbar vorangehendes IS-Fenster (mit
        demselben Embargo-/Purge-Abstand wie im statischen Modus — die Literal-Formel aus dem
        Issue-Text, ``is_start_ns = oos_start_ns − is_window_ns`` OHNE Embargo-Abzug, würde den
        Purge-Gap dieses Folds auf 0 kollabieren und exakt die Lookback-Leakage reintroduzieren, die
        ``embargo_period_days`` (#466/#548/#596) verhindern soll — das wäre technisch unpräzise trotz
        wörtlicher Issue-Konformität, daher bewusst korrigiert).

    WICHTIGER SCOPE-HINWEIS (#675): Dieses System optimiert EINEN Parametervektor pro Trial über die
    GESAMTE Zeitspanne in einem einzigen kontinuierlichen Backtest (Optuna wählt die Parameter, keine
    Per-Fold-Neuanpassung). ``retrain=True`` liefert daher KEIN echtes Parameter-Refit je Fold —
    das wäre ein grundlegend anderer Architektur-Schnitt (N separate Sub-Backtests je Trial). Es
    liefert die im Issue explizit spezifizierte, minimal-invasive Geometrie-Primitive (rollierendes
    statt statisches IS-Referenzfenster je Fold) als Baustein für rollierende IS/OOS-Divergenz-
    Diagnostik (siehe ``oos_fold_is_sortino_rolling`` in der per-Fold-Aggregation). Die im Issue als
    Alternative genannte CPCV-Eligibility-Basis existiert bereits separat (``cpcv.py``, genutzt für
    PBO in ``confirm._study_pbo``, #663) und bleibt der empfohlene strukturelle Haupt-Hebel.

    Vier Inline-Kopien dieser Arithmetik (Worker per-Trade-Klassifikation, Worker per-Fold-Sortinos,
    oos_trade_records, Aggregat per-Fold) wären eine eingebaute Divergenz-Falle — exakt analog zu
    ``compute_walk_forward_window`` für die äussere Fenster-Grenze (#457). Daher gilt hart: diese
    Geometrie NIE inline nachbauen, IMMER über diese Funktion (Single Source of Truth, #463/#466)."""
    is_window_ns = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
    oos_window_ns = walk_forward_dict.get("oos_window_days", 30) * 86400 * 1_000_000_000
    splits = walk_forward_dict.get("splits", 2)
    embargo_period_ns = walk_forward_dict.get("embargo_period_days", 0) * 86400 * 1_000_000_000
    retrain = bool(walk_forward_dict.get("retrain", False))

    boundaries: list[tuple[int, int, int]] = []
    static_is_start_ns = start_ns
    purge_end_ns = static_is_start_ns + is_window_ns + embargo_period_ns

    for fold in range(splits):
        oos_start_ns = purge_end_ns + fold * oos_window_ns
        oos_end_ns = oos_start_ns + oos_window_ns
        if retrain:
            fold_is_start_ns = oos_start_ns - embargo_period_ns - is_window_ns
        else:
            fold_is_start_ns = static_is_start_ns
        boundaries.append((fold_is_start_ns, oos_start_ns, oos_end_ns))
    return boundaries


def rolling_fold_is_oos_divergence(mtm_series, fold_boundaries: list[tuple[int, int, int]],
                                   is_window_ns: int) -> list[dict]:
    """Issue #675 — per-Fold IS/OOS-Divergenz-Diagnose auf der (ggf. rollierenden,
    ``walk_forward.retrain``-gesteuerten) ``fold_boundaries``-Geometrie. Rein additive Telemetrie,
    OHNE jeden Gate-/Reward-Einfluss (kein Konsument dieser Funktion darf eine Eligibility-
    Entscheidung daraus ableiten — das bleibt #676/#677 vorbehalten).

    Für jeden Fold wird eine einfache, annualisierungsfreie Bar-zu-Bar-Sortino-Näherung auf der
    IS-Vorperiode (``[fold_is_start, fold_is_start + is_window_ns)``) und der OOS-Periode
    (``[oos_start, oos_end)``) DERSELBEN Equity-Kurve berechnet; die Differenz
    (``is_sortino_approx − oos_sortino_approx``) ist der per-Fold Overfit-/Divergenz-Gap.

    Bei ``retrain=False`` (die ``fold_boundaries``, wie von ``compute_fold_boundaries`` geliefert)
    ist ``fold_is_start`` für JEDEN Fold identisch (statisch) — die IS-Referenz ist dann für alle
    Folds dieselbe (weit zurückliegende) Periode. Bei ``retrain=True`` rollt das IS-Fenster mit
    jedem Fold mit (unmittelbar vorangehende Periode) — die Diagnose wird dadurch regime-aktuell
    statt regime-stale (siehe ``compute_fold_boundaries``-Docstring, Pitfall #142).

    Rein, deterministisch, kein I/O. Liefert pro Fold ``{'fold', 'is_sortino_approx',
    'oos_sortino_approx', 'overfit_gap'}`` (``None``-Werte bei < 3 Bars oder fehlender Downside in
    einer Teilperiode — bewusst konservativ, kein erfundener Wert)."""
    import math

    def _slice(s_ns, e_ns):
        if mtm_series is None or mtm_series.empty:
            return None
        start_dt = pd.to_datetime(s_ns, unit="ns")
        end_excl = pd.to_datetime(e_ns, unit="ns") - pd.Timedelta(nanoseconds=1)
        seg = mtm_series.loc[start_dt:end_excl]
        return seg if not seg.empty else None

    def _bar_sortino(seg):
        if seg is None or len(seg) < 3:
            return None
        # Issue #802 — fill_method=None explizit: pct_change()'s Default-Fuellverhalten
        # unterscheidet sich zwischen pandas-Versionen (deprecated seit 2.1, entfernt in 3.0).
        rets = seg.pct_change(fill_method=None).dropna()
        if len(rets) < 2:
            return None
        mean_r = float(rets.mean())
        downside_sq = [float(r) * float(r) for r in rets if r < 0.0]
        if not downside_sq:
            return None
        dd = math.sqrt(sum(downside_sq) / len(rets))
        if dd <= 0.0:
            return None
        return mean_r / dd

    results = []
    for i, (fold_is_start, oos_start, oos_end) in enumerate(fold_boundaries):
        is_seg = _slice(fold_is_start, fold_is_start + is_window_ns)
        oos_seg = _slice(oos_start, oos_end)
        is_sr = _bar_sortino(is_seg)
        oos_sr = _bar_sortino(oos_seg)
        gap = (is_sr - oos_sr) if (is_sr is not None and oos_sr is not None) else None
        results.append({
            "fold": i, "is_sortino_approx": is_sr, "oos_sortino_approx": oos_sr,
            "overfit_gap": gap,
        })
    return results


_sortino_min_trades_cache: int | None = None
_sortino_mar_cache: float | None = None
_max_drawdown_cap_cache: float | None = None
_sortino_downside_floor_cache: float | None = None
_psr_bootstrap_resamples_cache: int | None = None
_sortino_min_downside_observations_cache: float | None = None
_sortino_min_periods_absolute_cache: int | None = None


def _read_sortino_min_downside_observations() -> float:
    """Issue #823/#863 (Zero-Hardcoding) — Mindestzahl an DOWNSIDE-Beobachtungen (Perioden mit
    ``return < mar`` auf der INFORMATIVEN Teilmenge, siehe ``_informative_period_returns``), bevor
    ``_calculate_stats`` einen Sortino/PSR berechnet. Root-Cause #823: eine Downside-Deviation aus
    zu wenigen negativen Beobachtungen ist ein degenerierter Nenner (numerisches Rauschen, kein
    Datenfehler) — die alte Fehlerklasse behandelte das ununterscheidbar vom Numerik-Guard
    (``SORTINO_GUARD_TRIPPED``). Unterschreitung ⇒ ``sortino=None`` mit dem EIGENEN Code
    ``SORTINO_INSUFFICIENT_DOWNSIDE`` (keine Verwechslung mit einem numerischen Ausreisser).

    Issue #863 — dieselbe Fehlerklasse wie #862 (Referenzwert gegen eine ABGELEITETE Grösse, hier
    ``n_periods``, kalibriert, deren Definition sich unter ihm geändert hat): der absolute Default
    30 wurde VOR #823 gewählt, als ``n_periods`` noch die volle Bar-Achse war. Für hochselektive
    Strategien (SqueezeBreakout, Median ~27 informative Perioden je OOS-Fenster) ist die Schwelle
    seither STRUKTURELL unerreichbar — sie kann nicht durch bessere Parameter erfüllt werden, nur
    durch häufigeres Handeln, was die Strategie definitionsgemäss nicht tut. Ein Wert in ``(0, 1]``
    wird jetzt als Mindest-ANTEIL von ``n_periods`` interpretiert (analog #677s relativem
    ``oos_min_evaluable_folds``) statt eines absoluten Zählers; ein Wert ``>= 1`` bleibt der
    ABSOLUTE Zähler (Legacy). ``tournament.json['sortino_min_downside_observations']``. Gecached
    (Hot-Path). Fehlt der Schlüssel ⇒ 0.5 (relativer #863-Default; der frühere absolute Default 30
    bleibt über einen expliziten Wert >= 1 erreichbar)."""
    global _sortino_min_downside_observations_cache
    if _sortino_min_downside_observations_cache is not None:
        return _sortino_min_downside_observations_cache
    val = 0.5
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_min_downside_observations")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 0.5
    _sortino_min_downside_observations_cache = val
    return val


_sortino_downside_shrinkage_m0_cache: float | None = None


def _read_sortino_downside_shrinkage_m0() -> float:
    """Issue #944 (Katalog B, Zero-Hardcoding) — James-Stein-Skala fuer die Downside-Deviation-
    Schrumpfung (``lambda = downside_obs / (downside_obs + m0)``): je kleiner ``m0`` relativ zu
    ``downside_obs``, desto naeher bleibt die Schaetzung am reinen Downside-Nenner; je groesser,
    desto staerker Richtung der robusteren Gesamtstreuung ALLER informativen Perioden.
    ``tournament.json['sortino_downside_shrinkage_m0']``. Gecached (Hot-Path). Fehlt der Schlüssel
    ⇒ 30 (Issue-#944-Vorschlag, begründet über SE(sigma_d)/sigma_d <= 1/sqrt(2*30) ≈ 12,9% als
    Referenzpräzision bei m=30)."""
    global _sortino_downside_shrinkage_m0_cache
    if _sortino_downside_shrinkage_m0_cache is not None:
        return _sortino_downside_shrinkage_m0_cache
    val = 30.0
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_downside_shrinkage_m0")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 30.0
    _sortino_downside_shrinkage_m0_cache = val
    return val


def _read_sortino_min_periods_absolute() -> int:
    """Issue #863 — harte ABSOLUTE Untergrenze für JEDE Sortino-Schätzung
    (``tournament.json['sortino_min_periods_absolute']``), unabhängig vom relativen
    Downside-Anteil: selbst wenn ``downside_obs/n_periods`` die relative #863-Schwelle erfüllt,
    ist eine Schätzung aus extrem wenigen informativen Perioden (z. B. ``n_periods=12``) keine
    belastbare Grundlage. Gecached. Fehlt der Schlüssel ⇒ 20."""
    global _sortino_min_periods_absolute_cache
    if _sortino_min_periods_absolute_cache is not None:
        return _sortino_min_periods_absolute_cache
    val = 20
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_min_periods_absolute")
            if raw is not None:
                val = int(raw)
    except (OSError, ValueError, TypeError):
        val = 20
    _sortino_min_periods_absolute_cache = val
    return val


def _informative_period_returns(period_rets: pd.Series) -> pd.Series:
    """Issue #823 (Root-Cause) — die für Sortino/PSR-INFERENZ informative Teilmenge von
    ``period_rets``: Bars mit tatsächlicher Rendite ungleich 0. Ein durchgehendes Kalenderraster
    (z. B. 24/7-Bars für ein RTH-Instrument, oder Bars ohne offene Position) trägt exakt
    Log-Return 0 pro flacher/Nicht-Handels-Bar — diese Bars gehen NICHT in den ökonomischen
    ``total_return`` verloren (die #801-Summenidentität bleibt exakt gewahrt, 0-Beiträge ändern die
    Summe nicht), verzerren aber JEDEN auf ``len(period_rets)`` normierten Schätzer (Mittelwert,
    Downside-Deviation, Annualisierung) als Funktion eines Nenners, der die Zahl der INFORMATIVEN
    Beobachtungen um ein Vielfaches übersteigt (AGENTS.md Pitfall #255)."""
    return period_rets[period_rets != 0.0]


def generate_event_based_holdout_split(trades: list, min_oos_trades: int = 100, oos_fraction: float = 0.30) -> tuple[list, list]:
    """Issue #791 — Event-based holdout sampling."""
    n = len(trades or [])
    if n < min_oos_trades:
        raise ValueError(f"Insuffiziente Trade-Anzahl ({n} < min_oos_trades {min_oos_trades})")
    n_oos = max(int(min_oos_trades), math.ceil(n * float(oos_fraction)))
    if n_oos >= n:
        n_oos = min_oos_trades
    split_idx = n - n_oos
    return trades[:split_idx], trades[split_idx:]


# Issue #980/#1134 — derselbe Symbol-Cache-Mechanismus wie _get_annualization_factor_with_source,
# ABER eigenstaendig (andere Formel: n_informative statt der vollen Perioden-Zahl) — dieser Wert
# treibt ``sortino_annualized`` TATSAECHLICH (siehe Aufrufstelle unten), waehrend
# _get_annualization_factor eine separate, nicht-informative Variante liefert.
_informative_annualization_factor_by_symbol_cache: dict[str, tuple[float, str]] = {}


def _informative_annualization_factor(mtm_series, n_informative: int, *,
                                      symbol: str | None = None) -> float:
    """Issue #823 — annualisiert die INFORMATIVE Bar-Frequenz (``n_informative`` Bars mit
    tatsächlicher Rendite über dieselbe REALE Zeitspanne des vollen ``mtm_series``-Index), statt
    der vollen (ggf. 24/7-aufgefüllten) Kalender-Bar-Frequenz (``_get_annualization_factor``).
    Derselbe explizite Config-Override (``annualization_periods_per_year``) hat weiterhin höchste
    Präzedenz. Fällt auf ``_get_annualization_factor`` zurück, wenn kein verwertbarer Zeit-Index
    vorliegt (Legacy-Direct-Unit-Calls) oder ``n_informative == 0``.

    Issue #980/#1134 (Katalog #986) — DIESER Faktor treibt ``sortino_annualized`` (siehe
    ``_calculate_stats``-Aufrufstelle) und ist damit die tatsächlich fuer die #1134-Kommensurabilität
    massgebliche Groesse. Ist ``symbol`` angegeben, wird F beim ERSTEN Aufruf fuer dieses Symbol
    bestimmt und fuer JEDE weitere Study desselben Symbols wiederverwendet (siehe
    ``_get_annualization_factor_with_source``-Docstring fuer die vollstaendige Begruendung)."""
    factor, _source = _informative_annualization_factor_with_source(
        mtm_series, n_informative, symbol=symbol)
    return factor


def _informative_annualization_factor_with_source(
    mtm_series, n_informative: int, *, symbol: str | None = None,
) -> tuple[float, str]:
    config_factor = _read_annualization_periods()
    if config_factor is not None:
        return float(config_factor), "config_override"
    if symbol is not None and symbol in _informative_annualization_factor_by_symbol_cache:
        return _informative_annualization_factor_by_symbol_cache[symbol]
    if (mtm_series is not None and len(mtm_series) > 1
            and isinstance(mtm_series.index, pd.DatetimeIndex) and n_informative > 0):
        total_span_seconds = (mtm_series.index[-1] - mtm_series.index[0]).total_seconds()
        if total_span_seconds > 0:
            result = (n_informative * 31_557_600.0 / total_span_seconds,
                     "empirical_first_study_time_index")
            if symbol is not None:
                _informative_annualization_factor_by_symbol_cache[symbol] = result
            return result
    fallback_factor, fallback_source = _get_annualization_factor_with_source(mtm_series, symbol=symbol)
    return fallback_factor, fallback_source


def _read_psr_bootstrap_resamples() -> int:
    """Issue #757 (Zero-Hardcoding): Anzahl der Stationary-Bootstrap-Resamples fuer den PSR/PSR_z-
    Standardfehler (deflation.bootstrap_psr_z) aus tournament.json['psr_bootstrap_resamples'].
    Gecached (Hot-Path — _calculate_stats laeuft je Trial mehrfach: IS/OOS/Fold-Ebene, ~6x/Trial).

    Default 200 (NICHT das Issue-#757-Vorschlagsmaximum "B ≈ 500-1000"): empirisch gemessen kostet
    ein einzelner _calculate_stats-Aufruf mit n_boot=500 ≈83ms (200er-OOS-Fenster), mit n_boot=200
    ≈33ms — bei ~6 Aufrufen/Trial waere 500 ein ~25% Laufzeit-Overhead auf die ~2s Backtest-Zeit
    eines Trials (vs. ~10% bei 200), was den Sweep-Durchsatz (#755) spuerbar zurueckdreht. 200
    Resamples halten den Standardfehler-Schaetzer stabil genug (CV(SE) ≈ sqrt(2/(n_boot-1)) ≈ 10%)
    fuer eine Gate-Entscheidung; Operatoren mit mehr Rechenbudget koennen ueber den Config-Key auf
    500-1000 erhoehen (praezisere SE-Schaetzung, insb. fuer Studien mit wenigen OOS-Perioden)."""
    global _psr_bootstrap_resamples_cache
    if _psr_bootstrap_resamples_cache is not None:
        return _psr_bootstrap_resamples_cache
    val = 200
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("psr_bootstrap_resamples")
            if raw is not None:
                val = int(raw)
    except (OSError, ValueError, TypeError):
        val = 200
    _psr_bootstrap_resamples_cache = val
    return val


_period_returns_cap_cache: int | None = None


def _read_period_returns_cap() -> int:
    """Issue #798 (Speicher-Katalog #794-#800) — Kappungsgrenze der per-Perioden-Renditeserie
    (``oos_period_returns``, Bootstrap-CI im Holdout-Gate) aus ``optimizer.json['period_returns_cap']``.
    Ersetzt die zuvor hartkodierte ``[:2000]``-Konstante (Zero-Hardcoding). Gecached (Hot-Path).
    Fehlt der Key (oder ist ungueltig) ⇒ Default 2000 (bit-identisch zum Vorwert)."""
    global _period_returns_cap_cache
    if _period_returns_cap_cache is not None:
        return _period_returns_cap_cache
    val = 2000
    try:
        cfg_path = config_dir() / "optimizer.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("period_returns_cap")
            if raw is not None:
                val = int(raw)
    except (OSError, ValueError, TypeError):
        val = 2000
    _period_returns_cap_cache = val
    return val
_default_round_trip_cost_bps_cache: dict[str, float] = {}


def _read_default_round_trip_cost_bps(inst_id_str: str | None = None) -> float:
    """Issue #684/#775 — Config-abgeleiteter DEFAULT-Round-Trip-Kostenwert (bps), Single Source of
    Truth mit dem realen Kostenmodell (``backtest.json``: ``commission_bps`` +
    ``resolve_spread_bps`` — identische Auflösungskette wie an der Stelle, die
    ``round_trip_cost_bps`` überhaupt erst stempelt).

    Root-Cause (#684): das kostenrelative Expectancy-Gate (``oos_min_expectancy_k_alpha``) fällt bei
    FEHLENDER Kosten-Telemetrie (``oos_metrics['round_trip_cost_bps'] is None``) auf das STATISCHE
    ``oos_min_expectancy`` (0.001) zurück — eine ZWEITE, unabhängig gepflegte Schwelle, die zufällig
    ~13× strenger ist als der kostenrelative Regelpfad (``k_alpha·c_rt`` ≈ 7.5e-5 bei c_rt=3bps).
    Dieser Reader liefert stattdessen einen aus DEMSELBEN Kostenmodell abgeleiteten Schätzwert, damit
    der Fallback dieselbe Grössenordnung wie der Regelpfad trägt, statt einer unabhängig geratenen
    Konstante.

    Root-Cause (#775): dieser Reader nutzte BISLANG unconditional den ``DEFAULT``-Asset-Class-Spread
    (4.0 bps), obwohl die Symbol→Asset-Class-Auflösungskette (``resolve_spread_bps``/
    ``_resolve_asset_class_for_symbol``, #566) bereits existiert. Für Krypto (16 bps real) war der
    Fallback damit 3,2× zu locker, für FOREX (2,5 bps) 2× zu streng. Ist ``inst_id_str`` gesetzt,
    wird derselbe Auflösungspfad wie im Worker konsultiert (Symbol-Override → Asset-Class →
    DEFAULT); fehlt es (Legacy-Aufrufer ohne bekanntes Symbol), bleibt das DEFAULT-Asset-Class-
    Verhalten bit-identisch zu Pre-#775.

    Gecached PRO SYMBOL (Hot-Path — kein zusätzliches File-I/O je Trial nach dem ersten Treffer).
    Fehlt ``backtest.json``/der Schlüssel ⇒ 5.0 (= DEFAULT-Asset-Class-Spread 4.0 + commission_bps-
    Fallback 1.0, der dokumentierte Cross-Asset-Referenzwert aus backtest.json._schema)."""
    cache_key = inst_id_str or "__default__"
    if cache_key in _default_round_trip_cost_bps_cache:
        return _default_round_trip_cost_bps_cache[cache_key]
    val = 5.0
    try:
        cfg_path = config_dir() / "backtest.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            commission_bps = float(data.get("commission_bps", 1.0))
            spread_by_asset_class = data.get("spread_bps_by_asset_class") or {}
            spread_by_symbol = data.get("spread_bps_by_symbol") or {}
            if inst_id_str:
                asset_class_key = "DEFAULT"
                has_symbol_override = bool(inst_id_str in spread_by_symbol)
                if spread_by_asset_class and not has_symbol_override:
                    asset_class_key = _resolve_asset_class_for_symbol(inst_id_str)
                spread_resolved = resolve_spread_bps(
                    inst_id_str, spread_by_asset_class, spread_by_symbol, asset_class_key)
            else:
                spread_resolved = float(spread_by_asset_class.get("DEFAULT", 4.0))
            val = commission_bps + spread_resolved
    except (OSError, ValueError, TypeError):
        val = 5.0
    _default_round_trip_cost_bps_cache[cache_key] = val
    return val

def _read_sortino_mar() -> float:
    """Issue #545 (Zero-Hardcoding): MAR (Minimum Acceptable Return) fuer die Sortino-Berechnung aus
    tournament.json['sortino_mar']. Gecached (Hot-Path). Fehlt der Schluessel ⇒ Legacy-Default 0.0."""
    global _sortino_mar_cache
    if _sortino_mar_cache is not None:
        return _sortino_mar_cache
    val = 0.0
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_mar")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 0.0
    _sortino_mar_cache = val
    return val

def _read_sortino_downside_floor() -> float:
    global _sortino_downside_floor_cache
    if _sortino_downside_floor_cache is not None:
        return _sortino_downside_floor_cache
    val = 1e-6
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_downside_floor")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 1e-6
    _sortino_downside_floor_cache = val
    return val

_sortino_numeric_guard_cache: float | None = None
_profit_factor_cap_cache: float | None = None
_sortino_numeric_guard_min_periods_cache: float | None = None
_sortino_numeric_guard_min_periods_cached: bool = False
_sortino_numeric_guard_reference_mode_cache: str | None = None
_sortino_numeric_guard_reference_bootstrap_cache: str | None = None
_sortino_guard_family_median_min_siblings_cache: int | None = None


def _read_sortino_numeric_guard() -> float:
    """Issue #588 (Zero-Hardcoding): reiner Numerik-/Datenfehler-Guard fuer den Sortino aus
    tournament.json['sortino_numeric_guard'] (KEINE semantische Saettigung — die passiert
    ausschliesslich in reward.py via sortino_soft_scale). Gecached (Hot-Path). Fehlt der
    Schluessel ⇒ 1e6 (rueckwaertskompatibler Overflow-Schutz)."""
    global _sortino_numeric_guard_cache
    if _sortino_numeric_guard_cache is not None:
        return _sortino_numeric_guard_cache
    val = 1e6
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_numeric_guard")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 1e6
    _sortino_numeric_guard_cache = val
    return val


def _read_sortino_numeric_guard_min_periods() -> float | None:
    """Issue #665 — OPT-IN Referenz-Stichprobengrösse für den T-bewussten Sortino-Numerik-Guard
    (tournament.json['sortino_numeric_guard_min_periods']). Gecached (Hot-Path). Fehlt der
    Schlüssel (Default) ⇒ ``None`` ⇒ der Guard bleibt bit-identisch zum Pre-#665-Verhalten (fixer
    Schwellenwert ``sortino_numeric_guard``, kein T-Skalierung — daher KEIN
    ``reward_semantics_version``-Bump nötig, solange dieser Key ungesetzt bleibt)."""
    global _sortino_numeric_guard_min_periods_cache, _sortino_numeric_guard_min_periods_cached
    if _sortino_numeric_guard_min_periods_cached:
        return _sortino_numeric_guard_min_periods_cache
    val = None
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_numeric_guard_min_periods")
            if raw is not None and float(raw) > 0.0:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = None
    _sortino_numeric_guard_min_periods_cache = val
    _sortino_numeric_guard_min_periods_cached = True
    return val


def _read_sortino_numeric_guard_reference_mode() -> str:
    """Issue #862 — ``tournament.json['sortino_numeric_guard_reference']`` ∈ {'absolute',
    'family_median'}. Gecached. Unbekannter Wert ⇒ fail-loud (ValueError), analog anderen
    Policy-Schaltern dieses Moduls (z. B. ``inference_failure_policy``)."""
    global _sortino_numeric_guard_reference_mode_cache
    if _sortino_numeric_guard_reference_mode_cache is not None:
        return _sortino_numeric_guard_reference_mode_cache
    mode = "absolute"
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_numeric_guard_reference")
            if raw is not None:
                mode = str(raw)
    except (OSError, ValueError, TypeError):
        mode = "absolute"
    if mode not in ("absolute", "family_median"):
        raise ValueError(
            f"tournament.json['sortino_numeric_guard_reference']={mode!r} unbekannt — "
            "erwartet 'absolute' oder 'family_median'.")
    _sortino_numeric_guard_reference_mode_cache = mode
    return mode


def _read_sortino_numeric_guard_reference_bootstrap() -> str:
    """Issue #913 Fix 2 — ``tournament.json['sortino_numeric_guard_reference_bootstrap']`` ∈
    {'absolute', 'defer'} (Default 'absolute'). Steuert das Verhalten der ersten
    ``sortino_guard_family_median_min_siblings`` Trials einer Familie, BEVOR ein belastbarer
    Familien-Median von ``n_periods`` existiert (Kaltstart): 'absolute' prüft in dieser Phase
    gegen den absoluten Anker (``sortino_numeric_guard_min_periods``) und stempelt die Quelle
    als ``'absolute_bootstrap'`` (unterscheidbar vom regulären ``'absolute'``-Modus, damit
    ``check_guard_reference_coherence`` den Zustand nicht mit einer Fehlkonfiguration verwechselt).
    'defer' prunt den Trial (kein Guard, ``sortino/psr=None``), bis der Median verfügbar ist.
    Gecached. Unbekannter Wert ⇒ fail-loud, analog dem Referenz-Modus selbst."""
    global _sortino_numeric_guard_reference_bootstrap_cache
    if _sortino_numeric_guard_reference_bootstrap_cache is not None:
        return _sortino_numeric_guard_reference_bootstrap_cache
    val = "absolute"
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("sortino_numeric_guard_reference_bootstrap")
            if raw is not None:
                val = str(raw)
    except (OSError, ValueError, TypeError):
        val = "absolute"
    if val not in ("absolute", "defer"):
        raise ValueError(
            f"tournament.json['sortino_numeric_guard_reference_bootstrap']={val!r} unbekannt — "
            "erwartet 'absolute' oder 'defer'.")
    _sortino_numeric_guard_reference_bootstrap_cache = val
    return val


def _effective_sortino_numeric_guard(
    sortino_numeric_guard: float, n_periods: int, *, family_median_n_periods: float | None = None,
) -> tuple[float, float | None, str]:
    """Issue #665/#862 — T-bewusste Skalierung des Sortino-Numerik-Guards (opt-in, siehe
    ``_read_sortino_numeric_guard_min_periods``).

    Root-Cause: ein annualisierter Fold-Sortino bei kleinem Stichprobenumfang T (z. B. T≈137
    Bars/Fold) trägt eine enorme Schätz-Unsicherheit — |sortino_annualized| bis knapp unter dem
    fixen Guard (25.0) ist bei diesem T bereits Rauschen, nicht nur bei Verletzung des Guards. Die
    T-Skalierung senkt den effektiven Schwellenwert PROPORTIONAL zu ``sqrt(n_periods / min_periods)``
    unterhalb der Referenzgrösse ``min_periods`` — bei/über der Referenz bleibt der Schwellenwert
    exakt ``sortino_numeric_guard`` (bit-identisch). Inaktiv (Rückgabe unverändert), solange
    ``sortino_numeric_guard_min_periods`` nicht konfiguriert ist.

    Issue #862 — Root-Cause des Referenzwert-Fehlers: ``sortino_numeric_guard_min_periods``
    (1600) wurde gegen die PRE-#823-Definition von ``n_periods`` kalibriert (volle 24/7-Bar-Achse,
    ≈4315), #823 hat ``n_periods`` aber auf die INFORMATIVE Teilmenge umdefiniert (beobachteter
    Median ≈319 in einem Referenzlauf) — der konfigurierte Anker ist seither strukturell zu gross,
    der Guard systematisch zu streng (Faktor ≈2,24 in besagtem Lauf).

    ``sortino_numeric_guard_reference`` (tournament.json) steuert die Referenzquelle:
    - ``'absolute'`` (Default) — bit-identisch zum Pre-#862-Verhalten, ``min_periods`` bleibt der
      statische Konfigurationswert. Reproduktionsläufe bleiben damit deterministisch vergleichbar.
    - ``'family_median'`` — die Referenz WÜRDE der Median von ``n_periods`` über die Familie
      desselben Symbols sein. Scope-Entscheidung (dokumentiert, analog #843/#845-Zurückstellungen):
      diese Funktion läuft in ``_calculate_stats`` INNERHALB eines isolierten Pro-Trial-
      Subprozesses (siehe ``run_backtest``) — die Geschwister-Trials derselben Familie sind zum
      Zeitpunkt DIESES Aufrufs nicht bekannt (Henne-Ei-Problem: der Guard entscheidet über den
      Reward, den TPE für die Sampler-Wahl braucht, BEVOR irgendein Sibling-Trial fertig ist). Ein
      echter ``family_median``-Modus erfordert, dass der Elternprozess (``run_optimization.py``)
      den Median der bereits abgeschlossenen Sibling-Trials VOR dem Start eines neuen Trials
      berechnet und über das Manifest in den Subprozess reicht — eine grössere, hier bewusst NICHT
      umgesetzte Restrukturierung. ``family_median_n_periods`` ist der Injektionspunkt für diese
      künftige Erweiterung (aktuell von keinem Aufrufer befüllt ⇒ Rückfall auf 'absolute').
      ``invariants.check_guard_reference_coherence`` deckt die eigentliche #862-Regression
      (Anker-Drift gegen die reale Verteilung) bereits als Report-Invariante ab, OHNE eine
      Restrukturierung der Trial-Ausführung vorauszusetzen.

    Rückgabe ``(effective_guard, guard_reference_value, guard_reference_source)`` — die beiden
    letzten Werte sind #862-Telemetrie für ``SORTINO_GUARD_TRIPPED``-Events.

    Issue #901 (siebte Wiederkehr Pitfall #267) — Root-Cause: VOR diesem Fix fiel ``mode ==
    'family_median'`` ohne einen bereitgestellten ``family_median_n_periods`` STILL auf den
    ABSOLUTEN Anker (``sortino_numeric_guard_min_periods``, 1600) zurück UND stempelte
    ``source='absolute'`` — eine Config, die ``family_median`` verlangt, wurde im Ergebnis nie
    sichtbar widerlegt (die Telemetrie behauptete exakt das Verhalten, das die Config abgestellt
    hatte). ``check_guard_reference_coherence`` konnte das nicht fangen: ``reference_mode ==
    'family_median'`` machte den Check dort UNBEDINGT PASS (Pitfall #288 — ein existierender,
    korrekt konfigurierter Schalter kann trotzdem tot sein).

    Fix: ``family_median`` ohne bereitgestellten Wert liefert jetzt ``(None, None,
    'family_median_unavailable')`` — ein EHRLICHER dritter Zustand statt einer stillen Lüge. Der
    Aufrufer behandelt ``effective_guard is None`` als nicht-bewertbar (Trial wird geprunt, nicht
    fehlerhaft als bestanden/durchgefallen bewertet, Issue #901 Fix 1). Ein ECHTER
    ``family_median``-Modus (``family_median_n_periods`` bereitgestellt — Injektionspunkt für die
    künftige Restrukturierung, siehe Docstring oben) bleibt unverändert korrekt.

    Issue #913 — der Injektionspfad ist jetzt gebaut: ``run_optimization.make_symbol_objective``
    berechnet den Median von ``oos_n_periods`` über die bereits abgeschlossenen Sibling-Trials
    DERSELBEN Study (``sortino_guard_family_scope='symbol_strategy'``, Default — #916) und reicht
    ihn über das Manifest (``global_settings.family_median_n_periods``) bis hierher durch. Vor
    Erreichen von ``sortino_guard_family_median_min_siblings`` Trials ist ``family_median_n_periods``
    weiterhin ``None`` — dieser Zustand ist jetzt die KALTSTART-Phase (siehe
    ``_read_sortino_numeric_guard_reference_bootstrap``), kein Verdrahtungsfehler mehr."""
    mode = _read_sortino_numeric_guard_reference_mode()
    if mode == "family_median":
        if family_median_n_periods is not None:
            min_periods = float(family_median_n_periods)
            source = "family_median"
        else:
            # Issue #913 Fix 2 — Kaltstart-Semantik: kein Sibling-Median verfügbar (die Familie
            # hat die konfigurierte Mindestzahl noch nicht erreicht, siehe
            # run_optimization._resolve_family_median_n_periods). 'absolute' prüft gegen den
            # statischen Anker unter einer EIGENEN, unterscheidbaren Quelle
            # ('absolute_bootstrap', NIE 'absolute') — sonst würde
            # check_guard_reference_coherence (#915) diesen Zustand fälschlich als korrekt
            # verdrahteten Absolut-Modus lesen. 'defer' prunt den Trial unbewertet (Alt-Verhalten).
            bootstrap_mode = _read_sortino_numeric_guard_reference_bootstrap()
            if bootstrap_mode == "absolute":
                min_periods = _read_sortino_numeric_guard_min_periods()
                source = "absolute_bootstrap"
            else:
                return None, None, "family_median_unavailable"
    else:
        min_periods = _read_sortino_numeric_guard_min_periods()
        source = "absolute"
    if min_periods is None or n_periods is None or n_periods <= 0:
        return sortino_numeric_guard, min_periods, source
    import math as _math
    effective = sortino_numeric_guard * _math.sqrt(min(1.0, float(n_periods) / min_periods))
    return effective, min_periods, source


def assert_guard_reference_injectable() -> None:
    """Issue #913 Fix 3 — Fail-Loud-Startup-Prüfung: ist
    ``tournament.json['sortino_numeric_guard_reference']='family_median'`` konfiguriert, MUSS
    mindestens EIN Aufrufer von ``_effective_sortino_numeric_guard`` in diesem Modul
    ``family_median_n_periods`` als Keyword übergeben — sonst ist die Konfiguration per
    Konstruktion folgenlos (die #913-Root-Cause: der Key existierte, die Registry war grün, der
    Injektionspfad fehlte). Statische AST-Prüfung über den Quelltext DIESES Moduls, kein
    Trial-Lauf nötig. Bricht mit ``REJECT_GUARD_REFERENCE_NOT_WIRED`` ab, BEVOR der erste
    Backtest eines Sweeps startet — ein 143-Symbol-Lauf darf nicht 170 Stunden lang
    informationsfrei laufen, weil ein Keyword-Argument fehlt (AGENTS.md Pitfall #296)."""
    mode = _read_sortino_numeric_guard_reference_mode()
    if mode != "family_median":
        return
    import ast
    source = inspect.getsource(sys.modules[__name__])
    tree = ast.parse(source)
    wired = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "_effective_sortino_numeric_guard" and any(
                kw.arg == "family_median_n_periods" for kw in node.keywords
            ):
                wired = True
                break
    if not wired:
        raise ValueError(
            "REJECT_GUARD_REFERENCE_NOT_WIRED: tournament.json['sortino_numeric_guard_reference']="
            "'family_median' ist konfiguriert, aber KEIN Aufrufer von "
            "_effective_sortino_numeric_guard in backtest_runner.py übergibt "
            "family_median_n_periods als Keyword — die Konfiguration wäre folgenlos (Issue #913). "
            "Injektionspfad reparieren ODER sortino_numeric_guard_reference in tournament.json "
            "auf 'absolute' zurückstellen."
        )


def _read_profit_factor_cap() -> float:
    """Issue #588 (Zero-Hardcoding): EIGENER Cap fuer profit_factor UND calmar_ratio aus
    tournament.json['profit_factor_cap'] (rechtsschiefe Kennzahlen, ein Cap ist hier sinnvoll —
    aber getrennt vom Sortino, der nach #588 nicht mehr geklemmt wird). Gecached. Fehlt der
    Schluessel ⇒ 15.0 (Legacy-Wert des frueheren gemeinsamen RATIO_CAP)."""
    global _profit_factor_cap_cache
    if _profit_factor_cap_cache is not None:
        return _profit_factor_cap_cache
    val = 15.0
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("profit_factor_cap")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 15.0
    _profit_factor_cap_cache = val
    return val


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



_max_drawdown_cap_cache: float | None = None

def _read_max_drawdown_cap() -> float:
    """Liest dynamisch den Max-Drawdown-Schwellenwert der Konkurrenz-Validierung aus
    tournament.json['max_drawdown']. Gecached (Hot-Path, je Worker-Subprozess konstant).
    Fehlt der Schluessel oder ist die Datei unlesbar ⇒ Legacy-Default 0.30."""
    global _max_drawdown_cap_cache
    if _max_drawdown_cap_cache is not None:
        return _max_drawdown_cap_cache
    val = 0.30
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            import json
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("max_drawdown")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        val = 0.30
    _max_drawdown_cap_cache = val
    return val


_annualization_periods_cache: float | None = None
_annualization_periods_cached: bool = False

def _read_annualization_periods() -> float | None:
    """Liest dynamisch den Annualisierungsfaktor für Risk-Metrics aus
    optimizer.json['annualization_periods_per_year']. Gecached (Hot-Path)."""
    global _annualization_periods_cache, _annualization_periods_cached
    if _annualization_periods_cached:
        return _annualization_periods_cache

    val = None
    try:
        cfg_path = config_dir() / "optimizer.json"
        if cfg_path.exists():
            import json
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("annualization_periods_per_year")
            if raw is not None:
                val = float(raw)
    except (OSError, ValueError, TypeError):
        pass
    _annualization_periods_cache = val
    _annualization_periods_cached = True
    return val

# Issue #980/#1134 (Katalog #986, Pitfall #399-Klasse) — F je Symbol EINMAL bestimmen, nicht je
# Study neu aus deren jeweils eigenem (unterschiedlich vielen Positions-Perioden umfassenden)
# mtm_series-Fenster. Prozessweiter Cache, siehe _get_annualization_factor_with_source-Docstring.
_annualization_factor_by_symbol_cache: dict[str, tuple[float, str]] = {}


def _get_annualization_factor(mtm_series=None, *, symbol: str | None = None) -> float:
    """Single-Source-of-Truth Methode zur Bestimmung des annualization_factor (Issues #510/#532/#595).
    Trading-Time-Paradigma: Division durch Kalenderjahre ist restlos eliminiert.

    Issue #532 — Präzedenz invertiert: Die EMPIRISCHE Bar-Frequenz aus dem ``mtm_series``-Index
    ist jetzt der Default; ``annualization_periods_per_year`` (optimizer.json) wirkt nur noch als
    EXPLIZITER Override (non-null), nicht mehr als stiller Default. Ein statischer Tages-Faktor
    (252) darf die real gemessene Intraday-Bar-Frequenz nicht länger überstimmen — ``√252`` auf
    1h-Returns unterschätzt den annualisierten Sortino systematisch (Equity-Marktstunden ⇒ Faktor
    ≫ 252).

    Issue #595 — Die empirische Frequenz wird nun aus der realen Zeitspanne abgeleitet:
    (n_periods · 31_557_600 / total_span_seconds).
    Das liefert für RTH-Instrumente (z. B. Equity) automatisch die Handelszeiten (TSLA ≈ 1638)
    und für 24/7-Krypto (≈ 8766) korrekt skaliert.

    Issue #980/#1134 — dünner Wrapper um ``_get_annualization_factor_with_source`` (Rückwärtskompat:
    reiner Float-Rückgabewert für die zahlreichen bestehenden Aufrufer, die die Quelle nicht
    brauchen)."""
    factor, _source = _get_annualization_factor_with_source(mtm_series, symbol=symbol)
    return factor


def _get_annualization_factor_with_source(mtm_series=None, *, symbol: str | None = None,
                                          ) -> tuple[float, str]:
    """Issue #980/#1134 (Katalog #986) — Root-Cause: Studies DESSELBEN Symbols sehen dieselben
    Marktdaten, aber (je nach Walk-Forward-/OOS-Fenster-Konfiguration der jeweiligen Strategie)
    unterschiedlich viele *Positions*-Perioden im ``mtm_series``-Fenster — F wurde bislang JE STUDY
    aus deren EIGENEM Fenster abgeleitet, wodurch √F zwischen Studies desselben Symbols um Faktor
    2,2–5,7 streute und die annualisierten Sortinos NICHT kommensurabel waren (Report-Ranking,
    Champion-Ranking, symbolweite Deflations-Familie vergleichen aber genau diese Grösse).

    Ist ``symbol`` angegeben: F wird beim ERSTEN Aufruf fuer dieses Symbol in diesem Prozess
    bestimmt (Config-Override ODER empirisch aus dessen mtm_series-Zeitindex) und fuer JEDEN
    weiteren Aufruf desselben Symbols WIEDERVERWENDET, unabhaengig vom mtm_series-Fenster der
    jeweils aufrufenden Study — √F-Spannweite je Symbol wird dadurch exakt 1.0 (Akzeptanzkriterium
    #1134: <= 1.05). Fehlt ``symbol`` (Legacy-/Direkt-Unit-Aufrufer), bleibt das Verhalten wie vor
    #1134 (je Aufruf empirisch aus dem uebergebenen ``mtm_series``).

    Eine echte, handelskalenderbasierte Herleitung (RTH-Handelsstunden vs. 24/7, siehe Artefakt A-4
    im #986-Katalog) bleibt ein offener Folgeschritt — dieser Fix macht F innerhalb eines Prozesses
    STABIL je Symbol, ohne die Kalenderfrage selbst zu beantworten.

    Rückgabe: ``(factor, source)`` mit ``source ∈ {'config_override', 'empirical_first_study_time_
    index', 'neutral_fallback'}``."""
    # 1) Expliziter Config-Override: schlägt die Empirik nur, wenn ausdrücklich (non-null) gesetzt.
    config_factor = _read_annualization_periods()
    if config_factor is not None:
        return float(config_factor), "config_override"

    # 2) Bereits fuer dieses Symbol bestimmt (siehe Docstring) — WIEDERVERWENDEN statt neu leiten.
    if symbol is not None and symbol in _annualization_factor_by_symbol_cache:
        return _annualization_factor_by_symbol_cache[symbol]

    # 3) Empirische Bar-Frequenz aus dem realen ZEIT-Index (bevorzugt, Issue #532/#595).
    #    Ein nicht-zeitlicher Index (z. B. RangeIndex bei Direkt-Unit-Calls von _calculate_stats)
    #    hat keine ableitbare Bar-Frequenz und fällt sauber auf den neutralen Pfad zurück.
    if (mtm_series is not None and len(mtm_series) > 1
            and isinstance(mtm_series.index, pd.DatetimeIndex)):
        # Off-by-One Alignment: Die Anzahl der Rendite-Perioden ist len(mtm_series) - 1
        n_periods = len(mtm_series) - 1
        total_span_seconds = (mtm_series.index[-1] - mtm_series.index[0]).total_seconds()
        if total_span_seconds > 0:
            result = (n_periods * 31_557_600.0 / total_span_seconds,
                     "empirical_first_study_time_index")
            if symbol is not None:
                _annualization_factor_by_symbol_cache[symbol] = result
            return result

    # 4) Kein verwertbarer Zeit-Index: neutral (1.0).
    return 1.0, "neutral_fallback"

def _calculate_stats(pnl_list: list[float], hold_list: list[tuple[int, float]], starting_capital: float, med_notional: float = 0.0, *, min_trades_for_sortino: int | None = None, mtm_series: pd.Series | None = None, mtm_frames: list[pd.Series] | None = None, notional_list: list[float] | None = None, family_median_n_periods: float | None = None, symbol: str | None = None) -> dict:
    """
    Berechnet die statistischen Performance-Metriken aus einer Liste von Trade-PnLs.

    Total Return Definition (Issue #465 / Audit #466):
    Liegt eine zeitbasierte MtM-Equity-Kurve (`mtm_series`) vor, ist `total_return` der ECHTE
    Portfolio-Return `equity_end / equity_start − 1`.

    Expectancy Definition (Issue #546):
    Liegt eine parallele `notional_list` (je Trade eingesetztes Entry-Notional) vor, ist
    `expectancy` der sizing-INVARIANTE Per-Trade-Return auf das eingesetzte Kapital
    `mean(pnl_i / notional_i)`. Ohne `notional_list` (Direkt-Unit-Calls, Legacy) fällt die
    Berechnung bit-identisch auf `mean(pnl_i / starting_capital)` zurück. Die notional-relative
    Definition entkoppelt Expectancy von der Positionsgröße: eine Strategie mit 10 % Einsatz und
    eine mit 100 % Einsatz bei identischem Per-Trade-Edge liefern dieselbe Expectancy (vorher
    Faktor 10). Siehe AGENTS.md Pitfall #107.

    Drawdown-/Sortino-Basis (Issue #464/#465):
    Liegt `mtm_series` vor, werden `max_drawdown` UND `sortino_ratio` aus der zeitindizierten
    MtM-Equity-Kurve abgeleitet (Intra-Trade-/Floating-Drawdowns erfasst; Sortino mit
    `√(Perioden/Jahr)` aus dem REALEN Zeitspann, nie `√252` auf Trade-sequentiellen Returns).
    Der Fallback ohne Equity-Kurve (realisierte, Trade-geordnete PnL-Kurve + Config-Annualisierung) ist ein
    Legacy-Pfad und darf NIE die OOS-Gate-/Reward-Metriken speisen (siehe AGENTS.md Pitfall #88).
    """
    import math
    import statistics
    NULL = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
        "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
        # Issue #710 — Haltedauer-Metrik in Bars, Schema-konsistent mit dem Nicht-Leer-Pfad.
        "median_bars_held": 0.0, "p95_bars_held": 0.0,
        # Issue #832 — Max-/Min-/P95-Haltedauer in Sekunden, Schema-konsistent mit dem Nicht-Leer-Pfad.
        "max_holding_time_s": 0.0, "min_holding_time_s": 0.0, "p95_holding_time_s": 0.0,
        # Issue #903 — rohe Round-Trip-Haltedauern (Sekunden), Rohmaterial für die
        # ROUND-TRIP-Ebene von invariants.compute_trial_timebox_violations (statt nur des
        # Trial-Maximums, das einen einzigen Ausreisser nicht von vielen unterscheiden konnte).
        "holding_times_s": [],
        "losses_count": 0,
        "median_position_notional": 0.0,
    }
    if not pnl_list:
        return NULL

    # Issue #804 — strukturierter Rueckkanal fuer die vier (jetzt: sieben) Inferenzpfad-Diagnosen
    # dieser Funktion. Root-Cause #804: ``_calculate_stats`` laeuft im Backtest-SUBPROZESS
    # (runner.py, ``subprocess.run(capture_output=True)``); ``logging.getLogger("optimizer").error``
    # allein landet in ``trial_dir/logs/backtest_stdout.log`` — einer Datei, die kein Aggregator
    # liest und die #794 Sekunden spaeter loescht. Jede Verletzung wird HIER ZUSAETZLICH als
    # ``{'code', 'detail', 'value'}``-Dict gesammelt und am Ende als ``inference_diagnostics`` in
    # das Rueckgabe-Dict gehoben — dasselbe Dict, das ``tournament_result.json`` persistiert, sodass
    # ``run_optimization`` es im ELTERNPROZESS erneut emittieren kann (siehe parsing.py/
    # run_optimization.py).
    _inference_diagnostics: list[dict] = []

    n = len(pnl_list)
    wins = sum(1 for v in pnl_list if v > 0)
    gross_profit = sum(v for v in pnl_list if v > 0)
    gross_loss = abs(sum(v for v in pnl_list if v < 0))
    losses_count = sum(1 for v in pnl_list if v < 0)

    EPSILON = 1e-9
    DENOMINATOR_FLOOR = 1e-6
    # Issue #588 — der frühere gemeinsame RATIO_CAP=15.0 klemmte Sortino, profit_factor UND calmar
    # mit DERSELBEN Zahl. Der Sortino-Clip war ein struktureller Defekt (stückweise konstant,
    # Gradient 0 ⇒ Rangordnung vernichtet, #559-asinh wirkungslos) und entfällt (nur noch Numerik-
    # Guard). profit_factor/calmar sind naturgemäss rechtsschief ⇒ ein Cap ist dort sinnvoll, aber
    # als EIGENER Key (profit_factor_cap), nicht am Sortino gekoppelt.
    profit_factor_cap = _read_profit_factor_cap()
    sortino_numeric_guard = _read_sortino_numeric_guard()

    # Floor `gross_loss` at EPSILON implicitly to protect against division-by-zero, but logic captures it via count.
    # Issue #1004 (Katalog #858, Pitfall #342) — ``profit_factor_cap`` klemmt den Wert bei einer
    # Konfigurations-Konstante (``tournament.json['profit_factor_cap']``); der geklemmte Wert wurde
    # bislang identisch als "der" Profit-Faktor gemeldet — sowohl im Bericht als auch an jede
    # Gate-/Reward-Konsumstelle, die ihn liest. Ein Cap ist eine Zensur, kein Messwert (Pitfall #342):
    # ``profit_factor_censored`` markiert JEDEN Fall, in dem der gemeldete Wert nicht der wahre
    # (unbeschränkte) Quotient ist — sowohl weil der Cap band ALS AUCH weil der Nenner degeneriert
    # war (``gross_loss`` positiv, aber unterhalb ``DENOMINATOR_FLOOR`` — der wahre PF ist dann
    # numerisch beliebig gross und JEDE gemeldete Zahl trägt effektiv null Information).
    # ``profit_factor`` selbst bleibt bewusst UNVERÄNDERT (weiterhin gecappt) — dieselbe Zahl, die
    # Gate/Scoring/Reward heute konsumieren (Zero-Regression auf jede kalibrierte Schwelle/jeden
    # Reward-Gradienten); die neue Information (roh + Zensur-Flag) ist rein additiv. Downstream darf
    # keine Promotion auf einem zensierten Wert beruhen (siehe
    # ``invariants.check_censored_statistic_in_decision``, #1004 Fix Punkt 4).
    profit_factor_censored = False
    profit_factor_raw = None
    if gross_loss <= 0.0:
        profit_factor = None
    elif losses_count < 2 and n < 50:
        profit_factor = None
    else:
        denominator_degenerate = gross_loss < DENOMINATOR_FLOOR
        profit_factor_raw = gross_profit / max(gross_loss, DENOMINATOR_FLOOR)
        profit_factor = min(profit_factor_raw, profit_factor_cap)
        # Issue #1030 (Katalog #866) — ``>`` statt ``>=`` liess ``profit_factor_raw ==
        # profit_factor_cap`` (der Wert liegt EXAKT auf der Zensurgrenze, min() klemmt ihn also
        # tatsaechlich) als unzensiert durch: Report-Records mit ``holdout_profit_factor == 15.0``
        # (== profit_factor_cap) UND ``profit_factor_censored == False`` sind eine in sich
        # widersprüchliche Kombination (beobachtet im 34b99e6e-Report).
        profit_factor_censored = denominator_degenerate or profit_factor_raw >= profit_factor_cap
        if denominator_degenerate:
            # Issue #1004 Fix Punkt 3 — ``gross_loss`` ist positiv, aber numerisch nicht von Null
            # unterscheidbar: der wahre PF ist nach oben unbeschraenkt, der Nenner selbst ist die
            # oekonomisch wichtige Information (der Verlustpfad wurde im Backtest praktisch nicht
            # realisiert), nicht der geglaettete Quotient. failure_policy='prune' (Registry-Eintrag
            # unten) behandelt einen solchen Trial wie jede andere strukturell nicht messbare
            # Grösse (analog EQUITY_NONPOSITIVE/SORTINO_GUARD_TRIPPED) — TPE lernt "nicht messbar",
            # nicht "maximal schlecht".
            _inference_diagnostics.append({
                "code": "PROFIT_FACTOR_DENOMINATOR_DEGENERATE",
                "detail": f"gross_loss={gross_loss!r} < DENOMINATOR_FLOOR={DENOMINATOR_FLOOR!r} "
                         f"(gross_profit={gross_profit!r}) — Profit-Faktor numerisch unbeschraenkt, "
                         f"gemeldeter Wert traegt keine Information.",
                "value": gross_loss,
            })

    win_rate = wins / n if n > 0 else 0.0

    # Issue #546 — Expectancy als Return auf das je Trade eingesetzte Notional (sizing-invariant),
    # sobald eine längen-kongruente notional_list vorliegt. Trades mit nicht-positivem Notional
    # (defensiv) werden übersprungen. Fehlt die Liste ⇒ Legacy-Pfad (Normierung auf starting_capital),
    # bit-identisch für alle Direkt-Unit-Calls von _calculate_stats.
    #
    # Issue #1031 (Katalog #866) — ``expectancy`` (``mean(pnl_i/notional_i)``) ist ein Mittel von
    # Quotienten OHNE Nennerboden und ohne Winsorisierung: ein einzelner Round-Trip mit degeneriertem
    # Nenner (oder ueber eine Preis-Sprungstelle gehaltene Position) verschiebt den Mittelwert ueber
    # Hunderte Trades beliebig weit (beobachtet: expectancy=0,52 bei implizitem f=0,93 % gegen
    # konfigurierte 15 % — Faktor 16). ``expectancy`` selbst bleibt UNVERAENDERT (Zero-Regression auf
    # jede kalibrierte Schwelle/jeden Reward-Gradienten, der sie heute konsumiert); die neue
    # Information ist additiv:
    #   - ``expectancy_capital_weighted`` = Σpnl/Σnotional — ein Summenquotient ist gegen einen
    #     einzelnen kleinen Nenner unempfindlich (jeder Trade traegt nur mit seinem tatsaechlichen
    #     Notional zur Summe bei, nicht mit dem Kehrwert eines evtl. degenerierten Nenners) und ist
    #     zusaetzlich kohaerent zu ``total_return`` (dieselbe Kapitalbasis).
    #   - Nennerboden ``nz >= 0,05 · median(notional_list)``: Trades darunter speisen weder
    #     ``expectancy_winsorized`` noch ``expectancy_outlier_count`` und werden als
    #     ``EXPECTANCY_NOTIONAL_DEGENERATE`` gezaehlt/diagnostiziert.
    #   - ``expectancy_winsorized``: dieselben 5/95-Grenzen wie ``fold_winsorize_lower/upper``
    #     (``_read_fold_winsorize``), auf die Nennerboden-gefilterte Per-Trade-Return-Liste.
    #   - ``expectancy_outlier_count``: Trades mit ``|pnl/notional| > 10 · median(|pnl/notional|)``
    #     innerhalb derselben gefilterten Liste.
    expectancy_capital_weighted = None
    expectancy_winsorized = None
    expectancy_outlier_count = 0
    expectancy_notional_degenerate_count = 0
    if notional_list is not None and len(notional_list) == len(pnl_list):
        per_trade = [v / nz for v, nz in zip(pnl_list, notional_list) if nz and nz > 0.0]
        expectancy = statistics.mean(per_trade) if per_trade else 0.0

        _positive_notionals = [nz for nz in notional_list if nz and nz > 0.0]
        _median_notional = statistics.median(_positive_notionals) if _positive_notionals else 0.0
        _notional_floor = 0.05 * _median_notional
        _pnl_floored, _notional_floored, _per_trade_floored = [], [], []
        for v, nz in zip(pnl_list, notional_list):
            if not nz or nz <= 0.0:
                continue
            if nz < _notional_floor:
                expectancy_notional_degenerate_count += 1
                _inference_diagnostics.append({
                    "code": "EXPECTANCY_NOTIONAL_DEGENERATE",
                    "detail": f"notional={nz!r} < 5% des Median-Notionals ({_notional_floor!r}) — "
                             "Trade traegt keine Information zu expectancy_winsorized/"
                             "expectancy_outlier_count bei (#1031).",
                    "value": nz,
                })
                continue
            _pnl_floored.append(v)
            _notional_floored.append(nz)
            _per_trade_floored.append(v / nz)

        if _notional_floored:
            expectancy_capital_weighted = sum(_pnl_floored) / sum(_notional_floored)
        _w_lo, _w_hi = _read_fold_winsorize()
        _winsorized_returns = _winsorize(_per_trade_floored, _w_lo, _w_hi)
        expectancy_winsorized = (
            statistics.mean(_winsorized_returns) if _winsorized_returns else None)
        if _per_trade_floored:
            _median_abs_return = statistics.median(abs(r) for r in _per_trade_floored)
            _outlier_threshold = 10.0 * _median_abs_return
            expectancy_outlier_count = sum(
                1 for r in _per_trade_floored if _outlier_threshold > 0 and abs(r) > _outlier_threshold)
    else:
        rets = [v / starting_capital for v in pnl_list]
        expectancy = statistics.mean(rets) if rets else 0.0

    # Issue #465 (Audit #466) — total_return als ECHTER zeitbasierter Portfolio-Return aus der
    # MtM-Equity-Kurve (``equity_end / equity_start − 1``), sobald eine Equity-Kurve vorliegt.
    # Das sequentielle Aufzinsen ``Π(1 + pnl_i/C0)`` unterstellt 100 % Kapitaleinsatz je Trade
    # nacheinander und verzerrt damit das OOS-Gate (``oos_min_total_return``) UND — über #461 —
    # die dominante Reward-Penalty bei realer paralleler/fraktionaler Allokation. Fallback (keine
    # Equity-Kurve, z. B. Direkt-Unit-Calls von ``_calculate_stats``): sequentielles Aufzinsen
    # (Abwärtskompatibilität im Sonderfall nicht-überlappender Full-Capital-Trades).
    #
    # Issue #771 — VOR diesem Fix nahm die per-Segment-Kompoundierung (``mtm_frames``) PRIORITÄT
    # vor der vollen konkatenierten Serie (``mtm_series``), obwohl ``period_rets`` (unten) IMMER
    # aus ``mtm_series`` gebildet wird. Root-Cause: ``mtm_series`` ist im Walk-Forward-Pfad bereits
    # exakt die konkatenierte, deduplizierte ``mtm_frames``-Serie (``sweep._split_and_stats``
    # baut ``oos_mtm = pd.concat(oos_frames)`` und übergibt BEIDE) — die per-Segment-Kompoundierung
    # unterschlägt daher (a) die ``splits − 1`` Nahtstellen-Returns an den Fold-Übergängen und
    # (b) jedes Segment mit ``len(seg) <= 1`` oder ``seg.iloc[0] == 0.0`` VOLLSTÄNDIG — beides
    # Bar-Returns, die in ``period_rets`` (Sortino/PSR/DSR/Bootstrap-CI/PBO) weiterhin auftauchen.
    # ``mtm_series`` ist jetzt die PRIMÄRE Quelle; ``mtm_frames`` bleibt NUR als Fallback für den
    # Fall einer nicht nutzbaren/fehlenden ``mtm_series`` (z. B. Direkt-Unit-Calls, die nur
    # Segmente statt einer vorgebauten konkatenierten Serie übergeben — siehe
    # ``test_segmented_compounding_gap_handling``).
    n_segments_skipped = 0
    if (mtm_series is not None and not mtm_series.empty and len(mtm_series) > 1
            and float(mtm_series.iloc[0]) != 0.0):
        total_return = float(mtm_series.iloc[-1]) / float(mtm_series.iloc[0]) - 1.0
    elif mtm_frames is not None and len(mtm_frames) > 0:
        # Issue #771 — Fallback NUR für nicht nutzbare ``mtm_series``. Ein übersprungenes Segment
        # (``len(seg) <= 1`` oder Startwert 0) ist hier ein Fehlerzustand, kein Normalfall — er wird
        # gezählt und als ``NON_CONTIGUOUS_FOLD_SEGMENTS`` telemetriert (``n_segments_skipped`` im
        # Rückgabe-Dict), statt den fehlenden Beitrag still zu unterschlagen.
        comp = 1.0
        for seg in mtm_frames:
            if len(seg) > 1 and float(seg.iloc[0]) != 0.0:
                seg_ret = float(seg.iloc[-1]) / float(seg.iloc[0]) - 1.0
                comp *= (1.0 + seg_ret)
            else:
                n_segments_skipped += 1
        total_return = comp - 1.0
        if n_segments_skipped > 0:
            import logging
            logging.getLogger("optimizer").warning(
                "NON_CONTIGUOUS_FOLD_SEGMENTS (#771): %d von %d mtm_frames-Segment(en) beim "
                "Fallback-Pfad uebersprungen (leer/Startwert 0) — total_return unterschlaegt deren "
                "Beitrag; period_rets (falls verfuegbar) enthaelt ihn weiterhin.",
                n_segments_skipped, len(mtm_frames),
            )
            _inference_diagnostics.append({
                "code": "NON_CONTIGUOUS_FOLD_SEGMENTS",
                "detail": f"{n_segments_skipped} von {len(mtm_frames)} mtm_frames-Segment(en) "
                         f"uebersprungen (leer/Startwert 0).",
                "value": n_segments_skipped,
            })
    else:
        # Fallback ohne verwertbare Equity-Kurve (z. B. spärliche OOS-Slices mit nur
        # einem Datenpunkt am Fold-Rand): sequentielles Aufzinsen der realisierten,
        # zeitlich geordneten PnLs `Π(1 + v/C0) − 1` statt der fehlerhaften 0.0-Zuweisung.
        # Verhindert Zero-Return-Artefakte, die das OOS-Gate als "Breakeven" fehlinterpretiert
        # und die TPE-Signale verzerren (Issue #529, siehe #521). Der Docstring oben spezifiziert
        # exakt dieses kompoundierte Verhalten.
        comp = 1.0
        for v in pnl_list:
            comp *= (1.0 + v / starting_capital)
        total_return = comp - 1.0

    if mtm_series is not None and not mtm_series.empty:
        import numpy as np
        import pandas as pd
        cumulative_max = mtm_series.cummax()
        drawdown = (mtm_series - cumulative_max) / cumulative_max.replace(0, np.nan)
        max_dd = abs(drawdown.min())
        if pd.isna(max_dd): max_dd = 0.0

        risk_dd_cap = _read_max_drawdown_cap()
        dd_excess = max(0.0, max_dd - risk_dd_cap)

        # Zwingende Isolation (High-Water Mark darf nicht vom IS-Fenster vererbt werden)
        cumulative_max = mtm_series.cummax()
        # Aber halt, cummax() startet neu am Anfang der Sliced Serie!
        # Falls es eine Series ist, passt cummax() für die aktuell übergebene Sektion.

        # Issue #801 — Positivitäts-Gate VOR jeder Log-Rendite-Berechnung. Eine Equity-Kurve, die
        # einmal ≤ 0 wird (reale Konsequenz eines gehebelten MARGIN-Kontos auf 1h-Krypto-Bars, kein
        # Randfall), macht jede nachfolgende log(mtm_t/mtm_{t-1}) undefiniert — die Inferenz darf
        # dann nicht stillschweigend über eine Teilmenge der Bars weiterrechnen (Monte-Carlo-Beleg
        # im Issue-Katalog: 44,3 % Vorzeichen-Flips bei Kurven mit Nulldurchgang, siehe
        # ``assert_positive_equity``-Docstring).
        equity_is_positive, equity_ruin_index = assert_positive_equity(mtm_series)
        equity_ruined = not equity_is_positive
        if equity_ruined:
            import logging
            logging.getLogger("optimizer").error(
                "EQUITY_NONPOSITIVE (#801): Equity-Kurve wird bei Bar-Index %d nicht-positiv "
                "(Wert=%.6g) — der Trial gilt als ökonomisch ruiniert. sortino/psr/period_returns "
                "werden NICHT berechnet (die Log-Return-Identität ist nur unter durchgehend "
                "positiver Equity definiert); total_return bleibt erhalten (Telemetrie 'Konto "
                "vernichtet').",
                equity_ruin_index, float(mtm_series.iloc[equity_ruin_index]),
            )
            _inference_diagnostics.append({
                "code": "EQUITY_NONPOSITIVE",
                "detail": f"Equity-Kurve wird bei Bar-Index {equity_ruin_index} nicht-positiv "
                         f"(Wert={float(mtm_series.iloc[equity_ruin_index]):.6g}).",
                "value": float(mtm_series.iloc[equity_ruin_index]),
            })
            period_rets = pd.Series([], dtype=float)
            _period_returns_list = []
            _period_returns_truncated = False
            return_series_identity_violation = False
        else:
            # Ableitung der per-Period Returns.
            #
            # Issue #756 — LOG-Returns statt einfacher Returns. Root-Cause: `total_return` (oben) ist
            # GEOMETRISCH kompoundiert (Π(1+rᵢ) − 1); der Sortino-Zähler `period_rets.mean()` war bislang
            # das ARITHMETISCHE Mittel derselben Renditesequenz. Die Differenz ist der Volatilitäts-Drag
            # mean(r) − (1/T)·log(1+total_return) ≈ σ²/2 — für jede Strategie mit |mean(r)| < σ²/2
            # (Edge nahe null, Vola dominant — genau das Regime aller hier gehandelten Strategien)
            # divergieren die Vorzeichen von Sortino und total_return, OBWOHL beide aus derselben
            # Equity-Kurve stammen (die #589/#620-„Kohärenzverletzung", bis zu 43 % einer Study).
            # `total_return` selbst bleibt UNVERÄNDERT die geometrische, ökonomisch korrekte
            # Zielgrösse — nur die Renditedefinition der INFERENZ (Sortino-Zähler/-Nenner, PSR/DSR,
            # Bootstrap-CI, PBO-Partitionierung) wechselt auf die additive Log-Skala.
            #
            # Issue #801/#802 — ALGEBRAISCH (``np.diff(np.log(mtm))``) statt über den pandas
            # pct-change/log1p-Umweg: auf einer (durch das Gate oben) garantiert positiven Serie
            # gilt ``Σ period_rets = log(mtm[-1]) − log(mtm[0]) = log(1+total_return)`` EXAKT, ohne
            # Zwischenschritt über ``1+r`` und ohne die ``log1p``-Definitionslücke bei ``r ≤ −1``
            # (die Wurzel der 35 fälschlich abgebrochenen Studies im Issue-Katalog). Umgeht ausserdem
            # ``pct_change()``s ``fill_method``-Semantik, die sich zwischen pandas-Versionen ändert
            # (pandas ≥ 2.1 deprecated, ≥ 3.0 kein Filling mehr — #802).
            log_px = np.log(mtm_series.to_numpy(dtype=float))
            period_rets = pd.Series(np.diff(log_px), index=mtm_series.index[1:])

            # Issue #801 (Pitfall #240) — Endlichkeits-Check VOR jeder Aggregation statt eines
            # impliziten ``skipna=True``: ein nicht-finiter Wert trotz positiver Equity wäre ein
            # unerwarteter Datenfehler, kein Normalfall, der stillschweigend übersprungen werden darf.
            if not np.isfinite(period_rets.to_numpy()).all():
                import logging
                logging.getLogger("optimizer").error(
                    "PERIOD_RETURNS_NOT_FINITE (#801): die algebraische Log-Rendite-Serie enthält "
                    "einen nicht-finiten Wert trotz positiver Equity — unerwarteter Datenfehler; "
                    "sortino/psr werden NICHT berechnet.",
                )
                _inference_diagnostics.append({
                    "code": "PERIOD_RETURNS_NOT_FINITE",
                    "detail": "algebraische Log-Rendite-Serie enthaelt einen nicht-finiten Wert "
                             "trotz positiver Equity.",
                    "value": None,
                })
                equity_ruined = True  # dieselbe Konsequenz: keine Inferenz auf einer Restmenge.
                period_rets = pd.Series([], dtype=float)
                _period_returns_list = []
                _period_returns_truncated = False
                return_series_identity_violation = False
            else:
                # Issue #619 — die per-Perioden-Returns durchreichen (gecappt), damit der Holdout-
                # Pfad einen Stationary-Bootstrap-CI auf dem Sortino rechnen kann.
                _period_returns_cap = _read_period_returns_cap()
                _full_period_rets_list = period_rets.tolist()
                _period_returns_list = [float(x) for x in _full_period_rets_list[:_period_returns_cap]]
                _period_returns_truncated = len(_full_period_rets_list) > _period_returns_cap
                # Issue #771/#801 — die #756-Identität maschinell geprüft (nicht nur behauptet):
                # Σlog(1+rᵢ) muss log(1+total_return) entsprechen (jetzt PER KONSTRUKTION exakt,
                # siehe assert_return_series_identity-Docstring).
                return_series_identity_violation = assert_return_series_identity(
                    total_return, period_rets, diagnostics=_inference_diagnostics)

        # Issue #510: Unified Calculation & Dynamic Frequency Fallback.
        # Issue #980/#1134 — ``symbol`` macht F stabil je Symbol (siehe _get_annualization_factor_
        # with_source-Docstring). Diese Groesse selbst fliesst in KEIN Rueckgabe-Feld (Legacy,
        # bereits vor #1134 unbenutzt) — annualization_factor_source (Rueckgabe-Dict) stammt aus
        # dem tatsaechlich sortino_annualized-treibenden ``_informative_annualization_factor``.
        annualization_factor = _get_annualization_factor(mtm_series, symbol=symbol)
        min_trades_sortino = min_trades_for_sortino if min_trades_for_sortino is not None else _read_sortino_min_trades()
        mar = _read_sortino_mar()

        # Issue #590 — ``losses_count == 0`` ist KEIN Ausstiegsgrund mehr. Ein Fold ohne Verlust hat
        # eine wohldefinierte Downside-Deviation von 0 — der ``sortino_downside_floor`` (#573) ist genau
        # dafür da. Vor dem Fix verschwand ein verlustfreier Fold aus ``oos_fold_sortinos`` (sortino=None)
        # und umging so die Fold-Dispersionsstrafe vollständig (Reward-Hacking: die Bewertung LÖSCHEN
        # statt Performance verbessern). Der ``downside_floor``-Codepfad war für genau diesen Fall
        # gedacht, aber hinter der ``losses_count == 0``-Prüfung toter Code.
        # Issue #614 — PER-PERIODEN-Ratio + PSR. Der ANNUALISIERTE Sortino ist nur noch Telemetrie:
        # die Annualisierung (·√A) multipliziert Punktschätzer UND Standardfehler gleich ⇒ kein
        # Informationsgewinn (bei T≈200 std 20.97, Range [−43, +227] — statistisch bedeutungslos).
        # Gate/Reward nutzen die PSR (skalenfrei, in [0,1], bezieht T + Schiefe + Kurtosis ein).
        # Issue #823 (Root-Cause) — die für die INFERENZ (Mittelwert, Downside-Deviation,
        # Annualisierung) massgebliche Teilmenge sind die Bars MIT tatsächlicher Rendite, nicht
        # die volle (ggf. 24/7-aufgefüllte) Kalender-Bar-Achse. Der ökonomische ``total_return``
        # bleibt UNVERÄNDERT über die volle Kurve (#801-Summenidentität oben bereits gegen die
        # VOLLE Serie geprüft — flache Bars tragen Log-Return exakt 0 bei, ihr Herausfiltern hier
        # ändert die Summe nicht). ``n_periods`` (⇒ ``oos_n_periods``-Telemetrie, DSR-Lo2002-
        # Varianz in confirm.py) ist ab hier die INFORMATIVE Zahl.
        informative_rets = _informative_period_returns(period_rets)
        n_periods = int(len(informative_rets))
        # Issue #980/#1134 — von ``_compute_sortino()`` (unten) gesetzt, sobald der Erfolgspfad die
        # annualisierte Sortino tatsaechlich berechnet; ein Closure-Mutable statt eines zehnten
        # Rueckgabewerts, um die bestehende 9-Tupel-Rueckgabekontrakt von ``_compute_sortino`` (7
        # Rueckgabestellen) nicht anzufassen. Default deckt jeden fruehen Return-Pfad ab (dort ist
        # sortino_annualized ohnehin None — die Quelle ist dann irrelevant, aber IMMER definiert).
        _annualization_factor_source_holder = {"value": "neutral_fallback"}

        def _compute_sortino():
            """Issue #823 — als lokale Funktion gekapselt (statt tief verschachtelter
            if/elif-Bloecke), damit jede Ausschluss-Bedingung per fruehem ``return`` behandelt
            werden kann. Liest/schreibt ausschliesslich die umschliessenden Closures (period_rets,
            mar, mtm_series, sortino_numeric_guard, _inference_diagnostics)."""
            if n < min_trades_sortino or informative_rets.empty:
                # Issue #967 (Katalog A, P0, Pitfall — stumme Rueckgabepfade) — VORHER emittierte
                # dieser Rueckgabepfad KEINE Diagnose: 91.4% der 490 Trials ohne Selektionsstatistik
                # im Referenzlauf 46cf5070 waren dadurch "stumm" (kein INFERENCE_DIAGNOSTIC-Event),
                # obwohl ``oos_psr``/``oos_sortino`` fuer sie ``None`` wurden. Jeder Rueckgabepfad
                # dieser Funktion MUSS jetzt einen Code emittieren (#967-Akzeptanzkriterium 1).
                _inference_diagnostics.append({
                    "code": "SORTINO_INSUFFICIENT_TRADES",
                    "detail": f"n={n} < min_trades_sortino={min_trades_sortino} oder "
                             f"informative_rets leer (n_periods={n_periods}).",
                    "value": n,
                })
                return None, None, None, None, None, 0.0, 3.0, None, None
            # Issue #545: Target-Downside-Deviation (RMS without mean-centering)
            # Issue #801 (Pitfall #240) — skipna=False erzwungen: eine NaN-uebersprungene Aggregation
            # waere eine Aussage ueber eine Teilmenge der Bars, keine ueber die volle Serie.
            downside_diff = (informative_rets - mar).clip(upper=0.0)
            dd_dev = float(np.sqrt((downside_diff ** 2).mean(skipna=False)))
            if pd.isna(dd_dev):
                # Issue #967 — zweiter stummer Rueckgabepfad (degenerierte Downside-Deviation).
                _inference_diagnostics.append({
                    "code": "SORTINO_DOWNSIDE_DEVIATION_UNDEFINED",
                    "detail": f"dd_dev ist NaN (n_periods={n_periods}, n_trades={n}).",
                    "value": None,
                })
                return None, None, None, None, None, 0.0, 3.0, None, None

            # Issue #823 Fix Punkt 2 / #863 — Mindestzahl an DOWNSIDE-Beobachtungen VOR jeder
            # weiteren Berechnung: ein Nenner aus zu wenigen negativen Perioden ist ein
            # degenerierter Schätzer (numerisches Rauschen), kein numerischer Ausreisser — eigener
            # Code (SORTINO_INSUFFICIENT_DOWNSIDE), damit die Telemetrie beides unterscheidet.
            #
            # Issue #863 — derselbe absolute Konstante 30 wurde VOR #823 gewählt (als n_periods
            # noch die volle Bar-Achse war) und ist seither für hochselektive Strategien
            # STRUKTURELL unerreichbar (SqueezeBreakout: Median downside_obs=24 bei Median
            # n_periods=27 — fast alle informativen Perioden SIND Downside-Beobachtungen, der
            # Nenner ist nicht degeneriert, die Stichprobe ist schlicht klein). Ein konfigurierter
            # Wert in (0, 1] wird jetzt als Mindest-ANTEIL an n_periods interpretiert (Default 0.5
            # — mindestens die Hälfte der informativen Perioden muss Downside sein); ein Wert >= 1
            # bleibt der absolute Zähler (Legacy). sortino_min_periods_absolute (Default 20) ist
            # eine davon UNABHÄNGIGE harte Untergrenze für jede Sortino-Schätzung.
            downside_obs = int((downside_diff < 0.0).sum())
            min_periods_absolute = _read_sortino_min_periods_absolute()
            if n_periods < min_periods_absolute:
                import logging
                logging.getLogger("optimizer").info(
                    "SORTINO_INSUFFICIENT_DOWNSIDE: n_periods=%d < sortino_min_periods_absolute=%d "
                    "— Stichprobe zu klein fuer JEDE Sortino-Schaetzung (#863; sortino/psr=None).",
                    n_periods, min_periods_absolute,
                )
                _inference_diagnostics.append({
                    "code": "SORTINO_INSUFFICIENT_DOWNSIDE",
                    "detail": f"n_periods={n_periods} < sortino_min_periods_absolute="
                             f"{min_periods_absolute}.",
                    "value": downside_obs,
                })
                return None, None, None, None, None, 0.0, 3.0, None, downside_obs
            # Issue #944 (Katalog B, P0, Pitfall #296) — die vorherige Fassung VERWARF den Trial
            # (SORTINO_INSUFFICIENT_DOWNSIDE, sortino/psr=None), sobald downside_obs unter die
            # (proportionale ODER absolute) Schwelle fiel. Root-Cause: bei einer PROPORTIONALEN
            # Schwelle (Default 0.5 * n_periods) ist die Verwerfungswahrscheinlichkeit MONOTON
            # WACHSEND in der Qualitaet der Return-Verteilung — eine Strategie mit wenigen
            # Verlustperioden (= gut) wird mit steigender Sicherheit verworfen, ein Anti-Selektions-
            # Filter mit umgekehrtem Vorzeichen (Pitfall #296). Die Schaetzpraezision haengt an der
            # ANZAHL m der Beobachtungen, nicht am Anteil (SE(sigma_d)/sigma_d ~ 1/sqrt(2m)).
            #
            # Fix: STATT zu verwerfen, wird dd_dev James-Stein-artig Richtung der Gesamt-
            # Standardabweichung ALLER informativen Perioden (nicht nur der Downside-Teilmenge)
            # geschrumpft — ein Trial mit duennem Downside-Nenner bleibt im Suchraum, seine
            # Schaetzung wird nur konservativer (naeher an der robusteren Gesamtstreuung), statt
            # komplett zu verschwinden. Kein Trial wird mehr allein wegen eines duennen Downside-
            # Nenners verworfen (die dieselbe Konstante `sortino_min_downside_observations`
            # zuvor gesteuerte Verwerfung wuerde ausserdem fuer hochselektive Strategien wie
            # SqueezeBreakout — Median ~27 informative Perioden — dieselbe strukturelle
            # Unerreichbarkeit reproduzieren, die #863 bereits als Regression identifiziert hatte).
            min_downside_obs_cfg = _read_sortino_min_downside_observations()
            if 0.0 < min_downside_obs_cfg <= 1.0:
                min_downside_obs = min_downside_obs_cfg * n_periods
            else:
                min_downside_obs = min_downside_obs_cfg
            shrinkage_lambda = 1.0
            if downside_obs < min_downside_obs and downside_obs > 0:
                m0 = _read_sortino_downside_shrinkage_m0()
                shrinkage_lambda = float(downside_obs) / float(downside_obs + m0)
                dd_dev_full = float(np.sqrt((informative_rets ** 2).mean(skipna=False)))
                if pd.isna(dd_dev_full):
                    dd_dev_full = dd_dev
                dd_dev = shrinkage_lambda * dd_dev + (1.0 - shrinkage_lambda) * dd_dev_full
                import logging
                logging.getLogger("optimizer").info(
                    "SORTINO_DOWNSIDE_SHRUNK: downside_obs=%d < %.3g (n_periods=%d) — "
                    "Downside-Deviation Richtung Gesamtstreuung geschrumpft (lambda=%.3f) statt "
                    "verworfen (#944).", downside_obs, min_downside_obs, n_periods, shrinkage_lambda,
                )
                _inference_diagnostics.append({
                    "code": "SORTINO_DOWNSIDE_SHRUNK",
                    "detail": f"downside_obs={downside_obs} < {min_downside_obs:.3g} "
                             f"(n_periods={n_periods}), shrinkage_lambda={shrinkage_lambda:.3f}.",
                    "value": downside_obs,
                })

            downside_floor = _read_sortino_downside_floor()
            dd_dev = max(dd_dev, downside_floor)
            mean_ret = informative_rets.mean(skipna=False)
            effective_annualization_factor, _annualization_factor_source_holder["value"] = (
                _informative_annualization_factor_with_source(mtm_series, n_periods, symbol=symbol))
            # Issue #614 — der PER-PERIODEN-Sortino ist die statistisch tragende Grösse (fliesst
            # in die PSR); der annualisierte Wert ist reine Telemetrie (sortino_ratio/annualized).
            sortino_period_v = float((mean_ret - mar) / dd_dev)
            sortino_annualized_v = sortino_period_v * math.sqrt(effective_annualization_factor)
            # Issue #588/#614 — Numerik-/Datenfehler-Guard auf dem ANNUALISIERTEN Sortino, jetzt bei
            # 25.0 (tournament.json): jenseits davon ist ein OOS-Sortino bei T≈200 ein Datenfehler,
            # kein Ergebnis. Fail-loud (SORTINO_GUARD_TRIPPED) und als undefiniert (None) behandeln —
            # sortino UND psr fallen aus (kein extremer Fold-Artefakt passiert das Gate stumm).
            if pd.isna(sortino_annualized_v) or not np.isfinite(sortino_annualized_v):
                # Issue #967 — dritter stummer Rueckgabepfad (nicht-endlicher annualisierter
                # Sortino, z. B. entartete effective_annualization_factor).
                import logging
                logging.getLogger("optimizer").warning(
                    "SORTINO_ANNUALIZED_NONFINITE: sortino_annualized ist NaN/inf "
                    "(effective_annualization_factor=%.6g, n_periods=%d, dd_dev=%.6g) — als "
                    "undefiniert behandelt (#967; sortino/psr=None).",
                    effective_annualization_factor, n_periods, dd_dev,
                )
                _inference_diagnostics.append({
                    "code": "SORTINO_ANNUALIZED_NONFINITE",
                    "detail": f"effective_annualization_factor={effective_annualization_factor:.6g}, "
                             f"n_periods={n_periods}, dd_dev={dd_dev:.6g}.",
                    "value": None,
                })
                return None, None, None, None, None, 0.0, 3.0, None, downside_obs
            _eff_guard, _guard_ref_value, _guard_ref_source = _effective_sortino_numeric_guard(
                sortino_numeric_guard, n_periods, family_median_n_periods=family_median_n_periods)
            if _eff_guard is None:
                # Issue #901 Fix 1 — 'family_median' verlangt, aber (noch) kein family_median_
                # n_periods bereitgestellt: der Trial ist unter dieser Referenz-Semantik nicht
                # bewertbar. Geprunt (sortino/psr=None), NICHT stillschweigend gegen den absoluten
                # Anker bewertet (das war die #901-Root-Cause).
                import logging
                logging.getLogger("optimizer").warning(
                    "SORTINO_GUARD_REFERENCE_UNAVAILABLE: sortino_numeric_guard_reference="
                    "'family_median', aber kein family_median_n_periods bereitgestellt "
                    "(n_periods=%d) — Trial nicht bewertbar unter dieser Referenz-Semantik "
                    "(#901; sortino/psr=None).", n_periods,
                )
                _inference_diagnostics.append({
                    "code": "SORTINO_GUARD_REFERENCE_UNAVAILABLE",
                    "detail": f"sortino_numeric_guard_reference='family_median' ohne "
                             f"family_median_n_periods (n_periods={n_periods}).",
                    "value": None,
                    "guard_reference_value": _guard_ref_value,
                    "guard_reference_source": _guard_ref_source,
                })
                return None, None, None, None, None, 0.0, 3.0, None, downside_obs
            if abs(sortino_annualized_v) > _eff_guard:
                import logging
                logging.getLogger("optimizer").warning(
                    "SORTINO_GUARD_TRIPPED: |sortino_annualized|=%.6g > guard=%.6g "
                    "(effective_guard=%.6g, n_periods=%d, dd_dev=%.6g) — als Datenfehler "
                    "verworfen (#614/#665; sortino/psr=None).",
                    sortino_annualized_v, sortino_numeric_guard, _eff_guard, n_periods, dd_dev,
                )
                _inference_diagnostics.append({
                    "code": "SORTINO_GUARD_TRIPPED",
                    "detail": f"|sortino_annualized|={sortino_annualized_v:.6g} > "
                             f"guard={_eff_guard:.6g} (n_periods={n_periods}, dd_dev={dd_dev:.6g}).",
                    "value": float(sortino_annualized_v),
                    # Issue #862 — welcher Referenzwert/welche Quelle den effektiven Guard trieb.
                    # Ohne diese Felder war die #862-Fehlkalibrierung nur durch Rückrechnung aus
                    # Log-Zeilen erkennbar (689 Zeilen im Referenzlauf).
                    "guard_reference_value": _guard_ref_value,
                    "guard_reference_source": _guard_ref_source,
                })
                return None, None, None, None, None, 0.0, 3.0, None, downside_obs

            # sortino_ratio bleibt (rückwärtskompatibel + Kohärenz-Sign-Check #589) der
            # ANNUALISIERTE Wert; die PSR ist die neue Reward-/Gate-Grösse (#614).
            sortino_v = float(sortino_annualized_v)
            # Issue #757 — PSR/PSR_z mit einem BOOTSTRAP-Standardfehler DER TATSÄCHLICH
            # VERWENDETEN Statistik (sortino_period), statt psr_z/lo2002_sharpe_variance — das sind
            # die Sampling-Varianz-Formeln fuer einen SHARPE-Schätzer (μ̂/σ̂), hergeleitet per
            # Delta-Methode ueber (μ̂, σ̂²); die Downside-Deviation hat eine andere Sampling-
            # Verteilung. Monte-Carlo-Beleg (H0, T=4320): P(PSR>=0.75) lag mit der Substitution
            # bei 31-32% statt der nominellen 25%.
            # Issue #824 — der Bootstrap resampelt seit diesem Fix dieselbe INFORMATIVE Teilmenge
            # (#823) statt der vollen, ggf. 24/7-aufgefuellten Kalender-Bar-Achse: ein Resampling
            # ueber ueberwiegend Null-Beitraege unterschaetzte den Standardfehler um den Faktor
            # sqrt(T/T_informativ) (Pitfall #255 — jede Standardfehler-Rechnung muss die informative
            # Laenge verwenden, nicht nur die Annualisierung/den Punktschätzer aus #823).
            # ``optimal_block_length`` (bootstrap.py, bereits vorhanden seit #619) schaetzt die
            # Bootstrap-Blocklaenge weiterhin AUS der informativen Serie selbst (Serienabhaengigkeit
            # der tatsaechlich gehandelten Perioden, nicht des Kalenderrasters).
            from automation.optimizer.deflation import (
                bootstrap_psr_z as _boot_psr_z, psr_from_z as _psr_from_z,
                sample_skew_kurtosis as _skku)
            informative_arr = informative_rets.to_numpy()
            skew_v, kurtosis_v = _skku(informative_arr)
            psr_z_v, psr_se_boot_v = _boot_psr_z(
                informative_arr, sr_star=0.0, mar=mar,
                n_boot=_read_psr_bootstrap_resamples())
            psr_v = _psr_from_z(psr_z_v)
            if psr_z_v is None:
                # Issue #965/#967 — vierter, bislang komplett UNDIAGNOSTIZIERTER Weg zu
                # ``oos_psr=None``: ``sortino``/``sortino_period`` sind hier bereits gueltig (der
                # Trial hat alle vorherigen Ausschluss-Bedingungen bestanden), aber der Bootstrap-
                # Standardfehler (deflation.bootstrap_psr_z) scheitert unabhaengig (n < 2 informative
                # Perioden, nicht-endlicher Punktschaetzer, oder eine entartete [<= 0] Bootstrap-
                # Streuung ueber die Resamples). Ohne diesen Code war ein Trial mit definiertem
                # Sortino aber undefinierter PSR nicht von einem stummen Programmierfehler
                # unterscheidbar.
                _inference_diagnostics.append({
                    "code": "PSR_BOOTSTRAP_UNDEFINED",
                    "detail": f"bootstrap_psr_z lieferte (None, None) trotz gueltigem "
                             f"sortino_period={sortino_period_v:.6g} (n_periods={n_periods}) — "
                             "degenerierte Bootstrap-Streuung oder < 2 informative Perioden.",
                    "value": None,
                })
            return (sortino_v, sortino_period_v, sortino_annualized_v, psr_v, psr_z_v, skew_v,
                    kurtosis_v, psr_se_boot_v, downside_obs)

        (sortino, sortino_period, sortino_annualized, oos_psr, oos_psr_z,
         ret_skew, ret_kurtosis, oos_psr_se_boot, oos_downside_obs) = _compute_sortino()
        # Issue #980/#1134 — die Quelle, die effective_annualization_factor TATSAECHLICH lieferte
        # (siehe _annualization_factor_source_holder-Docstring oben).
        annualization_factor_source = _annualization_factor_source_holder["value"]
    else:
        # Legacy-Fallback ohne Equity-Kurve
        max_dd = 0.0
        equity_ruined = False  # Issue #801 — ohne Equity-Kurve nicht beurteilbar, Default False.
        sortino = None
        sortino_period = None
        sortino_annualized = None
        oos_psr = None
        oos_psr_z = None
        oos_psr_se_boot = None
        oos_downside_obs = None
        n_periods = 0
        ret_skew, ret_kurtosis = 0.0, 3.0
        _period_returns_list = []
        _period_returns_truncated = False
        dd_excess = 0.0
        return_series_identity_violation = False

        # Call the unified helper to maintain structural symmetry (Issue #510 requirement)
        annualization_factor = _get_annualization_factor(None)
        annualization_factor_source = "neutral_fallback"

    # Floor max_dd at DENOMINATOR_FLOOR to protect against division-by-zero when computing calmar.
    if max_dd <= 0.0:
        calmar = None
    else:
        calmar = min(total_return / max(max_dd, DENOMINATOR_FLOOR), profit_factor_cap)

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

    # Issue #710 — per-Trade-Haltedauer in BARS (nicht Sekunden) für den Time-Box-Penalty-Enabler
    # (#711). Alle Strategien laufen auf 1h-Bars (siehe DEFAULT_MAX_BARS_IN_TRADE, hourly_
    # strategy_base.py) ⇒ dieselbe Sekunden→Bars-Konvention wie bereits an anderer Stelle verwendet
    # (backtest_runner.py: ``hold_h = avg_holding_time_s / 3600.0``). Median statt Mittel (robust
    # gegen die stark schiefen per-Fold-Verteilungen, siehe #710-Docstring); p95 als Deadline-Näherungs-
    # Signal. Reine Telemetrie — KEINE Reward-/Gate-Wirkung an dieser Stelle.
    _BAR_SECONDS = 3600.0
    if hold_list:
        bars_held_sorted = sorted(h / _BAR_SECONDS for h in holds_s)
        median_bars_held = statistics.median(bars_held_sorted)
        p95_idx = max(0, min(len(bars_held_sorted) - 1, round(0.95 * (len(bars_held_sorted) - 1))))
        p95_bars_held = bars_held_sorted[p95_idx]
    else:
        median_bars_held = 0.0
        p95_bars_held = 0.0

    # Issue #832 Fix Punkt 1 (Katalog #828-#835, GitHub-Issue #751) — Max-/Min-/P95-Haltedauer in
    # SEKUNDEN, dieselbe Aggregations-Arithmetik wie median_bars_held/p95_bars_held (#710) direkt
    # darüber, nur ohne die Bars-Konvertierung. Rohmaterial für den #832-Report-Abschnitt "Trades
    # mit der längsten Haltedauer" — AGGREGAT-Statistik über die bereits vorhandene ``hold_list``,
    # bewusst OHNE Einzel-Trade-Identität (Entry-/Exit-Zeitstempel, Richtung): das würde eine neue
    # State-Verfolgung in der FIFO-Match-Schleife von ``extract_metrics`` voraussetzen (die
    # Round-Trip-Aggregation trägt aktuell keinen Entry-Zeitstempel/keine Richtung je Position) —
    # siehe ``automation/optimizer/summary_de.py``-Docstring für die vollständige Scope-Begründung.
    if hold_list:
        holds_s_sorted = sorted(holds_s)
        max_holding_time_s = holds_s_sorted[-1]
        min_holding_time_s = holds_s_sorted[0]
        p95_holding_idx = max(0, min(len(holds_s_sorted) - 1, round(0.95 * (len(holds_s_sorted) - 1))))
        p95_holding_time_s = holds_s_sorted[p95_holding_idx]
    else:
        max_holding_time_s = 0.0
        min_holding_time_s = 0.0
        p95_holding_time_s = 0.0

    # Issue #850 — exposure_fraction: Anteil der Fenster-Zeit mit offener Position. Rohmaterial:
    # dieselbe ``hold_list`` wie oben (avg_holding_time_s/max_holding_time_s), KEINE neue
    # State-Verfolgung in der FIFO-Match-Schleife (dieselbe Scope-Entscheidung wie summary_de.py's
    # longest_trades-Docstring). Summe der Haltezeiten aller Round-Trips geteilt durch die
    # Fenster-Spanne — bei mehreren Folds (``mtm_frames``, z. B. OOS) die SUMME der Einzel-Fold-
    # Spannen (schliesst Embargo-Luecken ZWISCHEN Folds aus der Spanne aus, statt sie faelschlich
    # als "Fenster-Zeit ohne Handelsmoeglichkeit" mitzuzaehlen); sonst die Spanne der einzelnen
    # ``mtm_series``. Auf [0, 1] geklemmt (Schutz gegen > 1 bei — hier nicht erwarteten —
    # ueberlappenden Positionen). None ohne auswertbare Fenster-Spanne (nicht beurteilbar).
    exposure_fraction: float | None = None
    if hold_list:
        _window_span_s = None
        if mtm_frames:
            _fold_spans = [
                (f.index[-1] - f.index[0]).total_seconds() for f in mtm_frames
                if len(f.index) >= 2 and isinstance(f.index, pd.DatetimeIndex)
            ]
            if _fold_spans:
                _window_span_s = sum(_fold_spans)
        elif (mtm_series is not None and len(mtm_series.index) >= 2
              and isinstance(mtm_series.index, pd.DatetimeIndex)):
            _window_span_s = (mtm_series.index[-1] - mtm_series.index[0]).total_seconds()
        if _window_span_s and _window_span_s > 0:
            _total_held_s = sum(holds_s)
            exposure_fraction = max(0.0, min(1.0, _total_held_s / _window_span_s))

    # Coherence Invariant Check (Issue #528, Task 2.2)
    if hold_list is not None and len(hold_list) > 0 and mtm_series is not None:
        # Evaluierung der flachen Endposition über hold_list (Prüfung des terminalen Elements auf Abwesenheit offener Positionen).
        # Wenn wir eine offene Position haben, könnte das terminale Element in `hold_list` z.B. eine qty von 0 oder None haben,
        # oder das Konzept sieht so aus, dass am Ende des Backtests offene Positionen nicht geschlossen werden.
        # "Prüfung des terminalen Elements auf Abwesenheit offener Positionen":
        # Wir prüfen, ob das letzte Element in hold_list keine ungeschlossene Markierung hat (z.B. holding_ns == 0 oder open/closed).
        # Eigentlich, wenn die Endposition flach ist (keine offenen Trades), ist das der Normalfall für pnl_list.
        # Um den Fehler der offenen Position strikt zu handhaben: Wenn es eine offene Position gäbe, würde diese eine Divergenz erlauben.
        # In unserem System wird ein Trade mit offenen Endposition oft ignoriert oder `hold_list` hat eine Markierung.
        # Wenn hold_list das Terminale Element aufweist, nehmen wir eine geschlossene Position an, WENN `qty > 0`
        # (ein Dummy für Open Position könnte qty == 0 sein) ODER wir prüfen einfach, dass `hold_list` existiert,
        # was wir bereits tun. Aber um "offene Endposition" korrekt auszuklammern:
        # (Wir vereinfachen: Ein offenes Terminalelement hätte `holding_ns == 0` oder wir checken `abs(sum(pnl_list)) > 0`).

        sum_pnl = sum(pnl_list)
        # We assume flat position if hold_list[-1] is a regular closed trade (e.g. qty > 0 and holding_ns > 0).
        # To be safe and meet the requirement: "Prüfung des terminalen Elements auf Abwesenheit offener Positionen".
        is_flat_end_position = True
        if hold_list:
            last_holding_time, last_qty = hold_list[-1]
            if last_holding_time == 0 or last_qty == 0.0:
                is_flat_end_position = False

        if is_flat_end_position and abs(sum_pnl) > 1e-9 and abs(total_return) < 1e-9:
            import logging
            logging.warning(f"[Kohärenz-Invariante] ⚠️ KRITISCH (Issue #522): Flache Endposition, "
                            f"aber total_return=0.0 bei sum(PnL)={sum_pnl}. "
                            f"Total Return sign und PnL sign müssen übereinstimmen.")

    # Issue #1042 (Katalog #866) E-3 — CVaR/Expected-Shortfall aus DERSELBEN Perioden-Rendite-Serie
    # (``_period_returns_list``, log-Returns aus der MtM-Equity-Kurve, #756/#801), die bereits den
    # Holdout-Bootstrap-CI speist (#619) — keine zweite Renditedefinition. ``cvar_95`` = Mittelwert
    # der schlechtesten 5 % Perioden, ``es_99`` = Mittelwert der schlechtesten 1 % (beide negativ =
    # Verlust). ``max_drawdown`` (der heutige Risiko-Gate, #1042-Symptom: ``marginal_delta = 0`` über
    # 16 002 Auswertungen — trennt nichts) trifft nur den EINEN schlimmsten Pfad; CVaR/ES nutzen die
    # GESAMTE Tail-Verteilung. Mindeststichprobe 20 Perioden (analog anderer Perioden-Mindestgrössen
    # in dieser Datei, z. B. ``sortino_min_periods_absolute``) — darunter ist ein 5-/1-%-Quantil
    # nicht belastbar, ``None`` statt einer Scheinpräzision.
    _MIN_PERIODS_FOR_TAIL_RISK = 20
    cvar_95, es_99 = None, None
    if len(_period_returns_list) >= _MIN_PERIODS_FOR_TAIL_RISK:
        _rets_arr = np.array(_period_returns_list, dtype=float)
        _p5 = np.percentile(_rets_arr, 5)
        _p1 = np.percentile(_rets_arr, 1)
        _tail_5 = _rets_arr[_rets_arr <= _p5]
        _tail_1 = _rets_arr[_rets_arr <= _p1]
        cvar_95 = float(_tail_5.mean()) if _tail_5.size > 0 else float(_p5)
        es_99 = float(_tail_1.mean()) if _tail_1.size > 0 else float(_p1)

    return {
        "total_trades":  n,
        "win_rate":      float(win_rate),
        "profit_factor": float(profit_factor) if profit_factor is not None else None,
        # Issue #1004 (Katalog #858) — additive Zensur-Telemetrie neben dem unveränderten,
        # weiterhin gecappten ``profit_factor`` (siehe Kommentar an der Berechnungsstelle oben).
        "profit_factor_censored": bool(profit_factor_censored),
        "profit_factor_raw": float(profit_factor_raw) if profit_factor_raw is not None else None,
        "sortino_ratio": float(sortino) if sortino is not None else None,
        # Issue #614 / #630 — PSR + psr_z (Reward-/Gate-Grösse) + per-Perioden-/annualisierter Sortino + T + Momente
        # (Telemetrie). ``psr`` ∈ [0,1] oder None (undefiniert/Guard). ``sortino_annualized`` == sortino_ratio.
        "psr":                float(oos_psr) if oos_psr is not None else None,
        "psr_z":              float(oos_psr_z) if oos_psr_z is not None else None,
        # Issue #757 — der Bootstrap-Standardfehler, der psr/psr_z zugrunde liegt (Telemetrie/
        # Diagnose — macht sichtbar, ob/wie breit der SE fuer diesen Trial geschätzt wurde).
        "psr_se_boot":        float(oos_psr_se_boot) if oos_psr_se_boot is not None else None,
        # Issue #758 — Eligibility- und Promotion-Inferenzmethode nebeneinander im #742-Report
        # ausweisbar (Doppelstandard-Nachweis): die Eligibility-PSR hat KEINEN Sharpe-Formel-
        # Fallback (anders als confirm.py's DSR bei < 5 Perioden-Returns) — ``None`` nur, wenn
        # PSR/PSR_z selbst undefiniert blieben (kein Trade/Guard/NaN).
        "psr_inference_method": "stationary_bootstrap" if oos_psr_z is not None else None,
        "sortino_period":     float(sortino_period) if sortino_period is not None else None,
        "sortino_annualized": float(sortino_annualized) if sortino_annualized is not None else None,
        # Issue #980/#1134 (Katalog #986) — woher effective_annualization_factor (der
        # sortino_annualized treibende Faktor) tatsaechlich kam: 'config_override' |
        # 'empirical_first_study_time_index' (je Symbol EINMAL bestimmt, siehe
        # _get_annualization_factor_with_source-Docstring) | 'neutral_fallback'.
        "annualization_factor_source": annualization_factor_source,
        "n_periods":          int(n_periods),
        # Issue #824 — expliziter Alias: der Stichprobenumfang, den die PSR-Bootstrap-SE (und der
        # #823-Punktschätzer) TATSÄCHLICH gesehen haben (die informative Teilmenge, #823). Separates
        # Feld statt einer stillen Neuinterpretation von ``n_periods`` — beide sind seit #823/#824
        # identisch, ``n_effective_observations`` macht das für Report-Konsumenten explizit benannt.
        "n_effective_observations": int(n_periods),
        # Issue #845 — expliziter Trial-Attribut-Kandidat statt eines nur transienten
        # Closure-Werts: der Downside-Beobachtungs-Nenner (#823 SORTINO_INSUFFICIENT_DOWNSIDE-
        # Schwellenwert), damit n_periods-Heterogenität über eine Familie (Faktor 45 beobachtet)
        # gegen die TATSÄCHLICH downside-tragende Teilmenge geprüft werden kann, nicht nur gegen
        # die volle informative Periodenzahl. None, wenn vor Erreichen dieser Berechnung
        # ausgestiegen wurde (zu wenige Trades/leere Serie/degenerierte dd_dev).
        "downside_obs":       int(oos_downside_obs) if oos_downside_obs is not None else None,
        "ret_skew":           float(ret_skew),
        "ret_kurtosis":       float(ret_kurtosis),
        "period_returns":     _period_returns_list,   # Issue #619 — für den Bootstrap-CI im Holdout.
        # Issue #798 — True, sobald n_periods > period_returns_cap: ein gekappter Bootstrap-Input
        # darf nie stillschweigend als vollstaendig gelten (die Serie selbst bleibt gekappt gleich).
        "period_returns_truncated": bool(_period_returns_truncated),
        # Issue #801 — True, sobald die Equity-Kurve waehrend des Fensters nicht-positiv wurde
        # (siehe assert_positive_equity/EQUITY_NONPOSITIVE oben) — der Trial gilt dann als
        # oekonomisch ruiniert; sortino/psr sind None, total_return bleibt erhalten.
        "equity_ruined": bool(equity_ruined),
        # Issue #804 — strukturierter Rueckkanal der Inferenzpfad-Diagnosen dieser Funktion
        # (EQUITY_NONPOSITIVE/PERIOD_RETURNS_NOT_FINITE/RETURN_SERIES_IDENTITY_*/
        # NON_CONTIGUOUS_FOLD_SEGMENTS/SORTINO_GUARD_TRIPPED), damit run_optimization sie im
        # ELTERNPROZESS-Log erneut emittieren kann (siehe Docstring oben). Leer, wenn keine
        # Verletzung auftrat (Normalfall).
        "inference_diagnostics": list(_inference_diagnostics),
        "calmar_ratio":  float(calmar) if calmar is not None else None,
        "max_drawdown":  float(max_dd),
        "total_return":  float(total_return),
        "expectancy":    float(expectancy),
        # Issue #1031 (Katalog #866) — siehe Docstring am Berechnungsblock oben.
        "expectancy_capital_weighted": (
            float(expectancy_capital_weighted) if expectancy_capital_weighted is not None else None),
        "expectancy_winsorized": (
            float(expectancy_winsorized) if expectancy_winsorized is not None else None),
        "expectancy_outlier_count": int(expectancy_outlier_count),
        "expectancy_notional_degenerate_count": int(expectancy_notional_degenerate_count),
        # Issue #1042 (Katalog #866) E-3 — siehe Docstring am Berechnungsblock oben.
        "cvar_95": cvar_95,
        "es_99": es_99,
        "dd_excess":     float(dd_excess),
        "avg_holding_time_s": float(avg_hold),
        "median_holding_time_s": float(med_hold),
        # Issue #710 — Haltedauer-Metrik in Bars (Enabler für #711 Time-Box-Penalty).
        "median_bars_held": float(median_bars_held),
        "p95_bars_held": float(p95_bars_held),
        # Issue #832 Fix Punkt 1 — Max-/Min-/P95-Haltedauer in Sekunden (#742-Report-Abschnitt
        # "Trades mit der laengsten Haltedauer", siehe summary_de.py).
        "max_holding_time_s": float(max_holding_time_s),
        "min_holding_time_s": float(min_holding_time_s),
        "p95_holding_time_s": float(p95_holding_time_s),
        # Issue #903 — rohe Round-Trip-Haltedauern (Sekunden, ungerundet, ggf. leer bei fehlendem
        # hold_list). Kein separater period_returns_cap-artiger Deckel: die Round-Trip-Zahl je Trial
        # liegt (siehe #771-Katalog) im niedrigen Hundert-Bereich, nicht in der Grössenordnung der
        # Perioden-Renditeserie.
        "holding_times_s": [round(h, 4) for h in holds_s] if hold_list else [],
        # Issue #850 — Anteil der Fenster-Zeit mit offener Position (siehe Berechnungskommentar
        # oben). None ⇒ nicht beurteilbar (keine mtm_series/hold_list, rückwärtskompatibel).
        "exposure_fraction": float(exposure_fraction) if exposure_fraction is not None else None,
        "losses_count": losses_count,
        "median_position_notional": float(med_notional),
        # Issue #771 — Diagnose-Telemetrie der Renditeserien-Identität (siehe
        # assert_return_series_identity-Docstring). n_segments_skipped > 0 nur im mtm_frames-
        # Fallback-Pfad erreichbar (nicht-kontiguierliche Segmente).
        "n_segments_skipped": n_segments_skipped,
        "return_series_identity_violation": return_series_identity_violation,
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


# Issue #710/#899 — dieselbe 1h-Bar-Konvention wie die lokale ``_BAR_SECONDS`` in
# ``_calculate_stats`` (alle Strategien laufen auf 1h-Bars, siehe DEFAULT_MAX_BARS_IN_TRADE in
# hourly_strategy_base.py). Modul-Ebene, weil ``extract_metrics``/``_split_and_stats`` sie
# ausserhalb von ``_calculate_stats`` für ``oos_max_holding_bars`` (#899 Fix 2) braucht.
_BAR_SECONDS_METRICS = 3600.0


def _parse_exit_order_tags(tags) -> dict:
    """Issue #899 — parst die strukturierten ``EXIT_REASON:``/``ATR_MEDIAN_BPS:``/
    ``ATR_MIN_BPS:``-Tags, die ``hourly_strategy_base._execute_market_close`` dem schliessenden
    Markt-Order mitgibt (siehe dortigen Docstring). Robust gegen fehlende/fremde Tags (z. B.
    Entry-Orders, die keine Exit-Klassifikation tragen) — liefert dann ein leeres Dict."""
    meta: dict = {}
    if not tags:
        return meta
    for tag in tags:
        if not isinstance(tag, str) or ":" not in tag:
            continue
        key, _, value = tag.partition(":")
        try:
            if key == "EXIT_REASON":
                meta["exit_reason"] = value
            elif key == "ATR_MEDIAN_BPS":
                meta["atr_median_bps"] = float(value)
            elif key == "ATR_MIN_BPS":
                meta["atr_min_bps"] = float(value)
            elif key == "STOP_EXIT_LAG_BARS":
                # Issue #1095 (Katalog #928) — Bars zwischen Signal und tatsaechlichem Markt-Close
                # (siehe hourly_strategy_base._execute_market_close-Docstring).
                meta["stop_exit_lag_bars"] = int(value)
            elif key == "BAR_RANGE_MEDIAN_BPS":
                # Issue #953/#1119 (Katalog #960) — Median der Bar-Spanne ((high-low)/close, bps)
                # waehrend der Position offen war (siehe hourly_strategy_base._execute_market_close-
                # Docstring); Referenzgroesse fuer invariants.check_stop_loss_vs_bar_range (Latenz-
                # vs. Stop-getriebener Verlust).
                meta["bar_range_median_bps"] = float(value)
            elif key == "ATR_RAW_MEDIAN_BPS":
                # Issue #975/#1129 — der ROHE (nicht via _effective_atr_value/_ratchet_floored_
                # atr_value gefloorte) ATR-Median, parallel zu ATR_MEDIAN_BPS. Macht die #1129-
                # Zirkularitaets-Pruefung moeglich: atr_median_bps misst den EFFEKTIVEN Wert, aus
                # dem der Stop auch gesetzt wird — ein Vergleich gegen den rohen ATR fehlte bislang.
                meta["atr_raw_median_bps"] = float(value)
            elif key == "ORDER_SUBMIT_TS_NS":
                # Issue #976/#1130 — Zeitstempel des Order-Absetzens (hourly_strategy_base.
                # _execute_market_close, self.clock.timestamp_ns() unmittelbar vor order_factory.
                # market(...)), NUR fuer TRAILING_STOP-Exits gesetzt. Zusammen mit dem Fill-
                # Zeitstempel (rt_exit_ts, derselbe Round-Trip) ergibt sich stop_exit_fill_lag_ns —
                # die Absetzen-zu-Fill-Latenz, die stop_exit_lag_bars (Signal-zu-Absetzen) strukturell
                # NICHT erfasst (#407 in AGENTS.md: der Feldname beschreibt, was gemessen werden
                # sollte, nur die Formel beschreibt, was gemessen wird).
                meta["order_submit_ts_ns"] = int(value)
            elif key == "TRAILING_STOP_PRICE":
                # Issue #976/#1130 — der Stop-Level, gegen den der schliessende Markt-Order ausgeloest
                # wurde (hourly_strategy_base._trailing_stop_price zum Zeitpunkt des Absetzens);
                # Referenzpreis fuer stop_exit_slippage_bps = (fill_px - trailing_stop_price) /
                # trailing_stop_price * 10000 (vorzeichenbehaftet).
                meta["trailing_stop_price"] = float(value)
        except (TypeError, ValueError):
            continue
    return meta


def _build_order_exit_meta(engine: "BacktestEngine") -> dict[str, dict]:
    """Issue #899 — client_order_id -> Exit-Telemetrie-Dict, aus den Tags ALLER Orders des
    Engine-Cache (nicht nur der gefüllten — die Auflösung in ``_finalize_round_trip`` schlägt für
    ungetaggte Orders ohnehin defensiv auf ``{}`` fehl). Best-effort: ein Engine ohne ``.cache``
    (Unit-Test-Doubles) liefert ein leeres Dict statt zu werfen — Exit-Telemetrie ist rein additiv
    und darf die primäre Metrik-Extraktion nie zum Absturz bringen."""
    try:
        orders = engine.cache.orders()
    except Exception:
        return {}
    meta: dict[str, dict] = {}
    for order in orders or []:
        try:
            cid = str(order.client_order_id)
            tags = getattr(order, "tags", None)
        except Exception:
            continue
        parsed = _parse_exit_order_tags(tags)
        if parsed:
            meta[cid] = parsed
    return meta


def _pctl(sorted_vals: list[float], p: float) -> float:
    """Issue #972/#1126 — dieselbe Nearest-Rank-Perzentil-Arithmetik wie die bestehenden p95-Stellen
    in diesem Modul (z. B. ``p95_bars_held``), als kleiner wiederverwendbarer Baustein statt einer
    weiteren Ad-hoc-Kopie. ``sorted_vals`` MUSS bereits sortiert und nicht leer sein."""
    n = len(sorted_vals)
    idx = max(0, min(n - 1, round(p * (n - 1))))
    return sorted_vals[idx]


def _aggregate_exit_telemetry(meta_list: list[dict]) -> dict:
    """Issue #899 — reine Aggregationsfunktion über eine Liste von Round-Trip-Exit-Telemetrie-
    Dicts (``{"exit_reason", "atr_median_bps", "atr_min_bps", "pnl_bps"}``, siehe
    ``_finalize_round_trip``) zu den vier Trial-Feldern aus dem Issue:

      * ``exit_reason_histogram`` — Zaehler je exit_reason; Summe == len(meta_list) MINUS der
        Round-Trips ohne Tag (z. B. Legacy-Positionen, die am Datenende offen blieben).
      * ``gross_loss_mean_bps``/``gross_win_mean_bps`` — Ø-Bruttoverlust/-gewinn je Trade in bps
        des Entry-Notionals (Betrag, nicht Vorzeichen behaftet).
      * ``atr_median_bps``/``atr_min_bps`` — Median ueber die per-Position ATR_median/ATR_min-
        Ablesungen (Rohmaterial fuer ``invariants.check_effective_stop_distance``, #897 Fix 3).

    Issue #972/#1126 — ``gross_loss_mean_bps_trailing_stop`` ist ein UNGESCHUETZTES arithmetisches
    Mittel (Pitfall #405 in AGENTS.md, fuenfte Instanz der #304-Klasse): ``gross_loss_median_bps_
    trailing_stop`` (Median) und ``gross_loss_winsorized_mean_bps_trailing_stop`` (5/95-winsorisiert,
    ``_winsorize``) werden ZUSAETZLICH gefuehrt, damit ein nachgelagerter Konsument Mittel und Median
    gegeneinander pruefen kann, statt sich blind auf das Mittel zu verlassen. ``rt_notional_p05/p50/
    p95`` machen den bps-Nenner (der ueber ``_finalize_round_trip``s Notional-Untergrenze 1e-12 hinaus
    KEINEN Dust-Boden hat) auditierbar — der eigentliche Dust-Boden (5 % des Median-Notionals) sitzt
    bereits AN DER QUELLE (``_filter_dust_round_trips``, VOR dieser Funktion), diese Perzentile
    zeigen, ob er fuer eine gegebene Study ausreicht.

    Reine Funktion über plain Dicts (kein Optuna-/Engine-Objekt) — unabhängig unit-testbar,
    analog zum Rest dieses Moduls."""
    histogram: dict[str, int] = {}
    losses_bps: list[float] = []
    wins_bps: list[float] = []
    atr_medians: list[float] = []
    atr_mins: list[float] = []
    # Issue #975/#1129 — die ROHE (ungefloorte) ATR-Ablesung, parallel zu atr_medians (die den
    # EFFEKTIVEN, ratschen-gefloorten Wert traegt). Ein Vergleich der beiden entscheidet, ob
    # ``atr_median_bps`` (die Eingangsgroesse von check_effective_stop_distance/#1129s Spearman-
    # Test) zirkulaer gegen den Stop selbst misst.
    atr_raw_medians: list[float] = []
    # Issue #972/#1126 — das Round-Trip-Notional selbst, UNBEDINGT (nicht auf TRAILING_STOP
    # beschraenkt) — macht den bps-Nenner jeder Study auditierbar.
    rt_notionals: list[float] = []
    # Issue #976/#1130 — Absetzen-zu-Fill-Latenz (ns) und Slippage (bps) NUR bei nachweislichen
    # TRAILING_STOP-Exits mit vollstaendiger Order-/Fill-Telemetrie (siehe _finalize_round_trip).
    stop_exit_fill_lag_ns_values: list[float] = []
    stop_exit_slippage_bps_values: list[float] = []
    # Issue #1035 (Katalog #866) — Root-Cause: der Zaehler unten (``losses_bps``) mittelt ueber
    # ALLE Verlust-Trades, waehrend ``invariants.check_effective_stop_distance`` unterstellt, dass
    # jeder Verlust ein Stop-Exit war. Bei ueberwiegend UNKNOWN-/TIME_BOX-Exits (vor #1034 haeufig
    # > 50 %) hat der Stop den grossen Teil der Trades nie beruehrt — der Check maass die falsche
    # Grundgesamtheit (bestaetigt Hypothese (a) aus #1008: "Invariante misst falsche
    # Grundgesamtheit"). ``losses_bps_trailing_stop`` beschraenkt denselben Zaehler auf
    # NACHWEISLICHE Stop-Exits.
    losses_bps_trailing_stop: list[float] = []
    # Issue #1095 (Katalog #928) — nur ueber NACHWEISLICHE TRAILING_STOP-Exits (analog
    # losses_bps_trailing_stop): die Fill-Verzoegerung eines Zeitbox-/Signalwechsel-Exits ist eine
    # andere Fragestellung als die des Stop-Exits, den #1092/#1094 quantifizieren.
    stop_exit_lag_bars: list[int] = []
    # Issue #953/#1119 (Katalog #960) — UNBEDINGT (nicht auf TRAILING_STOP beschraenkt): die
    # Bar-Spanne ist eine Marktdaten-Eigenschaft der Position-Haltedauer, keine Stop-spezifische
    # Groesse — invariants.check_stop_loss_vs_bar_range vergleicht sie gegen den Stop-Verlust
    # GENAU DESHALB als unabhaengige Referenz.
    bar_range_medians: list[float] = []
    for m in meta_list or []:
        # Issue #899 Akzeptanzkriterium — jeder Round-Trip zaehlt in GENAU einen Bucket, auch ohne
        # aufloesbaren Exit-Tag (z. B. Profit-Target-Limit-Fill, am Datenende noch offene Position),
        # damit die Summe des Histogramms exakt der Round-Trip-Zahl der Ebene entspricht.
        reason = m.get("exit_reason") or "UNKNOWN"
        histogram[reason] = histogram.get(reason, 0) + 1
        pnl_bps = m.get("pnl_bps")
        if pnl_bps is not None:
            if pnl_bps < 0:
                losses_bps.append(abs(pnl_bps))
                if reason == "TRAILING_STOP":
                    losses_bps_trailing_stop.append(abs(pnl_bps))
            elif pnl_bps > 0:
                wins_bps.append(pnl_bps)
        if m.get("atr_median_bps") is not None:
            atr_medians.append(float(m["atr_median_bps"]))
        if m.get("atr_min_bps") is not None:
            atr_mins.append(float(m["atr_min_bps"]))
        if m.get("atr_raw_median_bps") is not None:
            atr_raw_medians.append(float(m["atr_raw_median_bps"]))
        if m.get("rt_notional") is not None:
            rt_notionals.append(float(m["rt_notional"]))
        if reason == "TRAILING_STOP" and m.get("stop_exit_lag_bars") is not None:
            stop_exit_lag_bars.append(int(m["stop_exit_lag_bars"]))
        if reason == "TRAILING_STOP" and m.get("stop_exit_fill_lag_ns") is not None:
            stop_exit_fill_lag_ns_values.append(float(m["stop_exit_fill_lag_ns"]))
        if reason == "TRAILING_STOP" and m.get("stop_exit_slippage_bps") is not None:
            stop_exit_slippage_bps_values.append(float(m["stop_exit_slippage_bps"]))
        if m.get("bar_range_median_bps") is not None:
            bar_range_medians.append(float(m["bar_range_median_bps"]))
    _rt_notionals_sorted = sorted(rt_notionals)
    return {
        "exit_reason_histogram": histogram,
        "gross_loss_mean_bps": statistics.mean(losses_bps) if losses_bps else None,
        "gross_win_mean_bps": statistics.mean(wins_bps) if wins_bps else None,
        "atr_median_bps": statistics.median(atr_medians) if atr_medians else None,
        "atr_min_bps": statistics.median(atr_mins) if atr_mins else None,
        # Issue #1035 (Katalog #866) — dieselbe Groesse wie gross_loss_mean_bps, aber NUR ueber
        # nachweisliche TRAILING_STOP-Exits; n_trailing_stop_losses macht die Stichprobengroesse
        # explizit (invariants.check_effective_stop_distance erklaert sich unterhalb einer
        # Mindestzahl fuer INCONCLUSIVE statt FAIL).
        "gross_loss_mean_bps_trailing_stop": (
            statistics.mean(losses_bps_trailing_stop) if losses_bps_trailing_stop else None),
        # Issue #972/#1126 — robuste Gegenstuecke zum ungeschuetzten Mittel oben: Median und
        # 5/95-winsorisiertes Mittel derselben Grundgesamtheit (siehe Docstring, Pitfall #405).
        "gross_loss_median_bps_trailing_stop": (
            statistics.median(losses_bps_trailing_stop) if losses_bps_trailing_stop else None),
        "gross_loss_winsorized_mean_bps_trailing_stop": (
            statistics.mean(_winsorize(losses_bps_trailing_stop, 0.05, 0.95))
            if losses_bps_trailing_stop else None),
        "n_trailing_stop_losses": len(losses_bps_trailing_stop),
        # Issue #975/#1129 — Median der ROHEN (ungefloorten) ATR-Ablesungen, Gegenstueck zu
        # atr_median_bps (dem EFFEKTIVEN, ratschen-gefloorten Wert).
        "atr_raw_median_bps": (
            statistics.median(atr_raw_medians) if atr_raw_medians else None),
        # Issue #972/#1126 — p05/p50/p95 des Round-Trip-Notionals dieser Ebene; macht den bps-Nenner
        # (siehe Docstring) auditierbar.
        "rt_notional_p05": _pctl(_rt_notionals_sorted, 0.05) if _rt_notionals_sorted else None,
        "rt_notional_p50": _pctl(_rt_notionals_sorted, 0.50) if _rt_notionals_sorted else None,
        "rt_notional_p95": _pctl(_rt_notionals_sorted, 0.95) if _rt_notionals_sorted else None,
        # Issue #976/#1130 — Absetzen-zu-Fill-Latenz (in Bars, dieselbe Konvention wie
        # stop_exit_lag_bars_median) und Slippage, NUR ueber nachweisliche TRAILING_STOP-Exits mit
        # vollstaendiger Order-/Fill-Telemetrie.
        "stop_exit_fill_lag_bars_median": (
            statistics.median(stop_exit_fill_lag_ns_values) / 1e9 / _BAR_SECONDS_METRICS
            if stop_exit_fill_lag_ns_values else None),
        "stop_exit_slippage_bps_median": (
            statistics.median(stop_exit_slippage_bps_values)
            if stop_exit_slippage_bps_values else None),
        "n_trailing_stop_exits_with_fill_lag_telemetry": len(stop_exit_fill_lag_ns_values),
        # Issue #1097 (Katalog #930) — die Stichprobengroesse HINTER gross_loss_mean_bps (ALLE
        # Verlust-Trades, nicht nur Stop-Exits): ohne diesen Zaehler kann report.py keinen
        # trade-gewichteten (statt medianbasierten) Study-Mittelwert bilden, siehe
        # report._pooled_mean_of_trial_field-Docstring.
        "n_losses": len(losses_bps),
        # Issue #1037 (Katalog #866) — bequemer direkter Zugriff auf denselben Wert wie
        # exit_reason_histogram['DATA_END']; Rohmaterial fuer
        # invariants.check_open_position_at_data_end.
        "n_round_trips_data_end": histogram.get("DATA_END", 0),
        # Issue #1095 (Katalog #924/#928) — Median der Bars zwischen Trailing-Stop-Signal und
        # tatsaechlichem Markt-Close-Fill; None ohne einen einzigen getaggten Stop-Exit.
        "stop_exit_lag_bars_median": (
            statistics.median(stop_exit_lag_bars) if stop_exit_lag_bars else None),
        "n_trailing_stop_exits_with_lag_telemetry": len(stop_exit_lag_bars),
        # Issue #953/#1119 (Katalog #960) — Median (ueber die Round-Trips dieses Trials) der
        # je-Position-Bar-Spannen-Mediane (bps); Referenzgroesse fuer
        # invariants.check_stop_loss_vs_bar_range (Verlust = adverse Bewegung EINER Bar vs.
        # Verlust = Stopdistanz + Ueberschiessen).
        "bar_range_median_bps": (
            statistics.median(bar_range_medians) if bar_range_medians else None),
    }



from nautilus_trader.common.actor import Actor
from nautilus_trader.model.data import Bar
import pandas as pd

class PortfolioMonitor(Actor):
    def __init__(self, bar_type: str):
        super().__init__()
        from nautilus_trader.model.data import BarType
        self.bar_type = BarType.from_str(bar_type)
        self.equity_curve = []
        # Issue #552 — parallele Close-Preis-Spur des überwachten Symbols (Buy&Hold-Benchmark).
        # Für den Single-Symbol-Sweep abonniert der Monitor genau EIN bar_type ⇒ die Close-Serie
        # ist die Symbol-Preisreihe, aus der der benchmark-relative Excess-Return (Alpha) folgt.
        self.benchmark_curve = []

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        # Issue #552 — Close-Preis JEDES Bars erfassen (vor dem Equity-Guard, damit die Benchmark-
        # Serie lückenlos ist). Fehlerrobust: schlägt die Erfassung fehl, bleibt benchmark_curve
        # leer ⇒ #552-Telemetrie/Excess-Gate fallen sauber auf das Legacy-Absolut-Gate zurück.
        try:
            self.benchmark_curve.append((bar.ts_event, bar.close.as_double()))
        except Exception:
            pass
        try:
            from nautilus_trader.model.currencies import USD
            venue = self.bar_type.instrument_id.venue
            eq_map = self.portfolio.equity(venue=venue)

            if not eq_map:
                return

            money = eq_map.get(USD)
            if money is None:
                money = next(iter(eq_map.values()), None)

            if money is not None:
                self.equity_curve.append((bar.ts_event, money.as_double()))
        except Exception as e:
            if not getattr(self, "_warned_on_bar_exception", False):
                self._warned_on_bar_exception = True
                import traceback
                self._log.warning(f"[{self.__class__.__name__}] on_bar Exception (Pitfall #90): {e} \n {traceback.format_exc()}")

    def get_equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self.equity_curve, columns=["ts", "equity"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ns")
        df.set_index("ts", inplace=True)
        # Drop duplicate timestamps, keeping the last recorded equity for the timestamp
        df = df[~df.index.duplicated(keep='last')]
        return df["equity"]

    def get_benchmark_series(self) -> pd.Series:
        """Issue #552 — deduplizierte Close-Preis-Serie (Buy&Hold-Benchmark des Symbols)."""
        if not self.benchmark_curve:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self.benchmark_curve, columns=["ts", "close"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ns")
        df.set_index("ts", inplace=True)
        df = df[~df.index.duplicated(keep='last')]
        return df["close"]


# ---------------------------------------------------------------------------
# Issue #508 — Dual-Reporting-Schema: Round-Trip- vs. Fill-Match-Ebene
# ---------------------------------------------------------------------------
# Ein FIFO-Match ist eine *technische* Teilfüllung. Bei Scale-in/Scale-out
# erzeugt eine EINZIGE ökonomische Position (Position-Open → Flat) mehrere
# Matches. Werden die Kernmetriken (`total_trades`, `win_rate`, `expectancy`,
# `profit_factor`) je Match statt je Position gebildet, inflationiert der
# Trade-Count und verzerrt die Gate-Metriken. Deshalb werden die Matches zu
# Round-Trips aggregiert und die primären (Gate-relevanten) Metriken strikt
# auf Round-Trip-Ebene berechnet; die Fill-Match-Ebene bleibt als reine
# Execution-Diagnostik erhalten.

# (pnl, ts_ns, holding_ns, qty, entry_notional) — ein einzelner FIFO-Match.
FillMatchRecord = tuple[float, int, float, float, float]
# (pnl, exit_ts_ns, holding_ns, qty) — eine aggregierte Round-Trip-Position.
RoundTripRecord = tuple[float, int, float, float]
# (entry_notional, exit_ts_ns) — Notional-Spur (parallel zu den PnL-Records).
NotionalRecord = tuple[float, int]


def _round_trip_notional_peak(matches: list["FillMatchRecord"]) -> float:
    """Issue #1032 (Katalog #866) — Spitzenbestand des GLEICHZEITIG offenen Kapitals waehrend eines
    Round-Trips, im Unterschied zu ``rt_notional`` (Summe der Entry-Notionale ALLER FIFO-Matches
    dieses Round-Trips, die Kostenbasis). Fuer eine Position, die innerhalb eines Round-Trips
    mehrfach auf-/abgebaut wird (Pyramidisierung, Teilausstieg + Nachkauf), zaehlt ``rt_notional``
    jede Wiederaufnahme erneut, obwohl dasselbe Kapital recycelt wird — das tatsaechliche maximale
    Exposure ist strikt kleiner als diese Summe.

    Sweep-Line ueber (Entry-Notional bei ``exit_ts − holding_ns``, negiertes Notional bei
    ``exit_ts``) je Match; bei gleichem Zeitstempel werden Entries VOR Exits verarbeitet (maximiert
    den Spitzenbestand bei simultanen Fills statt ihn zufaellig zu unterschaetzen).

    Beispiel (Kauf 100 → Verkauf 30 → Kauf 50 → Verkauf 120, ein Round-Trip mit zwei FIFO-Matches
    ueber 30 bzw. 70 Einheiten des ersten Kaufs plus einem Match ueber 50 Einheiten des zweiten
    Kaufs): der Spitzenbestand ist das Maximum ueber die Zeit, strikt kleiner als die Summe aller
    Entry-Notionale (``rt_notional``)."""
    events: list[tuple[int, float]] = []
    for _pnl, exit_ts, holding_ns, _qty, notional in matches:
        entry_ts = exit_ts - holding_ns
        events.append((entry_ts, notional))
        events.append((exit_ts, -notional))
    events.sort(key=lambda e: (e[0], -e[1]))
    running = 0.0
    peak = 0.0
    for _ts, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def _expectancy_cost_stress(pnls_notionals: list[tuple[float, float]], *,
                            round_trip_cost_bps: float, multiplier: float) -> float | None:
    """Issue #1042 (Katalog #866) E-1 — kapitalgewichtete Expectancy unter einem Kosten-Stress-
    Szenario: ``multiplier``-fache Round-Trip-KOSTEN (Spread + Kommission, ``round_trip_cost_bps``
    — c_rt) statt der im Backtest tatsaechlich angewandten Kosten. Der Backtest hat die Kosten
    bereits EINMAL abgezogen; ein Stress-Multiplikator ``s`` zieht zusaetzlich
    ``(s − 1) · round_trip_cost_bps/10000 · notional`` je Round-Trip ab, statt neu zu simulieren —
    eine reine Nachverarbeitung der bereits extrahierten (PnL, Notional)-Paare. Denselben
    5-%-Median-Notional-Nennerboden wie ``expectancy_capital_weighted`` (#1031): ein einzelner
    Mikro-Trade darf die Kennzahl nicht dominieren. ``None`` ohne positive Notionale (keine
    belastbare Grundlage).

    Issue #1081 (Katalog #866-2) — Root-Cause: dieser Parameter hiess vorher ``commission_bps`` UND
    wurde auch so befuellt (nur die Kommission, 1,0 bps) — der Spread (der GRÖSSERE Kostenblock,
    75 % von c_rt bei EQUITY: 3,0 von 4,0 bps) blieb unangetastet. Der "2×-Kostenstress" erhoehte
    die REALEN Round-Trip-Kosten dadurch nur um 25 % (4,0 → 5,0 bps), nicht um 100 % (→ 8,0 bps) —
    obwohl ``deployment_gate._clause_cost_stress`` den Wert als Kosten-ROBUSTHEITSKLAUSEL konsumiert
    und die Zusammenfassung ihn als vollen Kostenstress ausweist. Der Aufrufer übergibt jetzt den
    vollen c_rt (``_read_default_round_trip_cost_bps``/``spread_bps + commission_bps``, dieselbe
    Auflösungskette wie #775/#684), nicht mehr nur die Kommission."""
    positive = [nz for _, nz in pnls_notionals if nz and nz > 0.0]
    if not positive:
        return None
    floor = 0.05 * statistics.median(positive)
    extra_rate = (multiplier - 1.0) * (round_trip_cost_bps / 10000.0)
    stressed_pnl_sum, notional_sum = 0.0, 0.0
    for pnl, nz in pnls_notionals:
        if not nz or nz < floor:
            continue
        stressed_pnl_sum += pnl - extra_rate * nz
        notional_sum += nz
    return stressed_pnl_sum / notional_sum if notional_sum > 0.0 else None


def _filter_dust_round_trips(
    rt_pnls_with_ts: list[RoundTripRecord],
    rt_notionals_with_ts: list[NotionalRecord],
    rt_notional_peaks: list[float],
    rt_exit_meta: list[dict],
    *, dust_notional_floor_frac: float = 0.05,
) -> tuple[list[RoundTripRecord], list[NotionalRecord], list[float], list[dict],
          list[NotionalRecord], list[dict]]:
    """Issue #946/#1112 (Katalog #960) — verwirft Round-Trips mit einem Notional unterhalb
    ``dust_notional_floor_frac · median(notional)`` AN DER QUELLE (dem vollstaendigen Round-Trip-
    Strom, ``extract_metrics``s ``rt_*_with_ts``-Listen, unmittelbar nach dem FIFO-Matching und VOR
    jeder IS/OOS-/Fold-Aufteilung), statt — wie vor diesem Fix — nur an EINER Konsumstelle
    (``_calculate_stats``s ``expectancy_capital_weighted``-Boden, #1031).

    Root-Cause #1112: ein Leg mit Notional ~1e−13 (Fliesskomma-Residuum eines Netto-Exposure-
    Nulldurchgangs, dieselbe Fehlerklasse wie #1085) blieb in JEDEM ANDEREN stromabwaerts
    abgeleiteten Wert (``holdout_expectancy_notional_weighted`` — Mittel von Quotienten OHNE
    eigenen Boden, siehe ``_calculate_stats``-Docstring — sowie Win-Rate, Profit-Faktor,
    ``exit_reason_histogram``, Zeitbox-Nenner) und dominierte dort den Mittelwert (beobachtet:
    SqueezeBreakout/PLTR, 1 von 8 Trades, ``holdout_expectancy_notional_weighted = -171,34`` bps
    gegen ``holdout_expectancy_capital_weighted = -21,57`` bps). Filterung an der Quelle macht ALLE
    vier parallelen Round-Trip-Listen (PnL/Notional/Notional-Spitze/Exit-Telemetrie, gleicher Index)
    auf einen Schlag konsistent — jeder nachgelagerte Konsument sieht dieselbe bereinigte Menge,
    ohne selbst einen Boden pflegen zu muessen.

    Reine Funktion (keine Engine-/Optuna-Abhaengigkeit) — direkt unit-testbar, analog
    ``_round_trip_notional_peak``/``_expectancy_cost_stress``. Rueckgabe: die vier gefilterten
    Listen (dieselbe relative Reihenfolge, weiterhin index-parallel) plus die VERWORFENEN
    ``(notional, exit_ts)``-Paare (damit der Aufrufer sie — wie jeden anderen Round-Trip — nach
    IS/OOS klassifizieren kann, statt nur eine undifferenzierte Gesamtzahl zu erhalten). Weniger
    als zwei positive Notionale ⇒ kein belastbarer Median, alle vier Listen unveraendert
    zurueckgegeben (nichts verworfen).

    Issue #972/#1126 Fix Punkt 2 — zusaetzlich die VERWORFENE Exit-Telemetrie (``discarded_meta``,
    index-parallel zu ``discarded``): macht ``n_trailing_stop_losses_dust_filtered`` moeglich (wie
    viele der verworfenen Round-Trips NACHWEISLICHE TRAILING_STOP-Verlust-Exits waren), ohne die
    bereits gefilterte ``kept_meta``-Liste ein zweites Mal durchsuchen zu muessen."""
    positive_notionals = [nz for nz, _ts in rt_notionals_with_ts if nz and nz > 0.0]
    if len(positive_notionals) < 2:
        return rt_pnls_with_ts, rt_notionals_with_ts, rt_notional_peaks, rt_exit_meta, [], []
    floor = dust_notional_floor_frac * statistics.median(positive_notionals)
    if floor <= 0.0:
        return rt_pnls_with_ts, rt_notionals_with_ts, rt_notional_peaks, rt_exit_meta, [], []
    kept_pnls: list[RoundTripRecord] = []
    kept_notionals: list[NotionalRecord] = []
    kept_peaks: list[float] = []
    kept_meta: list[dict] = []
    discarded: list[NotionalRecord] = []
    discarded_meta: list[dict] = []
    for i, (nz, ts) in enumerate(rt_notionals_with_ts):
        if nz is not None and nz > 0.0 and nz < floor:
            discarded.append((nz, ts))
            discarded_meta.append(rt_exit_meta[i] if i < len(rt_exit_meta) else {})
            continue
        kept_pnls.append(rt_pnls_with_ts[i])
        kept_notionals.append(rt_notionals_with_ts[i])
        if i < len(rt_notional_peaks):
            kept_peaks.append(rt_notional_peaks[i])
        if i < len(rt_exit_meta):
            kept_meta.append(rt_exit_meta[i])
    return kept_pnls, kept_notionals, kept_peaks, kept_meta, discarded, discarded_meta


class MetricsLevel(TypedDict):
    """Metriken *einer* Aggregationsebene des Dual-Reporting-Schemas (Issue #508).

    Bündelt In-Sample- und Out-of-Sample-Metriken (jeweils ein
    ``_calculate_stats``-Rückgabe-Dict). Es existieren zwei Ebenen:

      * ``round_trips`` — ökonomische Positionen (Position-Open → Flat /
        Net-Exposure-Zero-Crossing). **Primär** für Gate-Eligibility und
        Walk-Forward-Validierung.
      * ``fill_matches`` — technische FIFO-Teilfüllungen. **Sekundär**, reine
        Execution-Diagnostik (Scale-in/Scale-out-Intensität).
    """

    metrics: dict[str, Any]      # In-Sample
    oos_metrics: dict[str, Any]  # Out-of-Sample


def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None, walk_forward_dict: dict | None = None, start_ns: int | None = None, commission_bps: float = 0.0, mtm_series: 'pd.Series | None' = None, benchmark_series: 'pd.Series | None' = None, family_median_n_periods: float | None = None, round_trip_cost_bps: float | None = None, symbol: str | None = None) -> dict:
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

    Round-Trip-Aggregierung & Dual-Reporting (Issue #508):
    Die FIFO-Teilfüllungen werden zu ökonomischen Positionen aggregiert (Position-Open → Flat /
    Net-Exposure-Zero-Crossing). Die PRIMÄREN Metriken (`metrics`/`oos_metrics`, gespiegelt unter
    `round_trips`) werden strikt auf Round-Trip-Ebene gebildet — `total_trades` == Zahl der
    Positionen, `win_rate` == Wins/n_positions, `expectancy` == Σ PnL_positions / n_positions,
    `profit_factor` analog. Damit inflationiert Scale-in/Scale-out den Trade-Count NICHT mehr und
    kann die Gate-Metriken nicht verzerren. Die Fill-Match-Ebene (technische Teilfüllungen) bleibt
    unter `fill_matches` als reine Execution-Diagnostik erhalten. Sämtliche Eligibility- und
    Walk-Forward-Evaluierungen MÜSSEN auf `round_trips` (bzw. `metrics`/`oos_metrics`) operieren.
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

        # Issue #899 — Exit-Klassifikation + ATR-Telemetrie je Order, aus den Order-Tags
        # (hourly_strategy_base._execute_market_close), NICHT aus dem abgeschnittenen
        # Subprozess-Logger. client_order_id -> {"exit_reason", "atr_median_bps", "atr_min_bps"}.
        order_exit_meta = _build_order_exit_meta(engine)

        # Fill-Match-Ebene (technische FIFO-Teilfüllungen — Execution-Diagnostik).
        pnls_with_ts: list[FillMatchRecord] = []
        notionals_with_ts: list[NotionalRecord] = []
        # Round-Trip-Ebene (ökonomische Positionen — primäre Gate-Metriken, Issue #508).
        rt_pnls_with_ts: list[RoundTripRecord] = []
        rt_notionals_with_ts: list[NotionalRecord] = []
        # Issue #899 — Exit-Telemetrie je Round-Trip (exit_reason/ATR der SCHLIESSENDEN Order),
        # parallel zu rt_pnls_with_ts/rt_notionals_with_ts (gleicher Index, gleiche Länge).
        rt_exit_meta: list[dict] = []
        # Issue #1032 (Katalog #866) — Spitzenbestand des gleichzeitig offenen Kapitals je Round-
        # Trip (siehe _round_trip_notional_peak-Docstring), parallel zu rt_notionals_with_ts.
        rt_notional_peaks: list[float] = []

        def _finalize_round_trip(matches: list[FillMatchRecord], order_ids: list[str] | None = None,
                                 *, is_data_end_fallback: bool = False,
                                 closing_price: float | None = None) -> None:
            """Aggregiert die FIFO-Matches EINER zusammenhängenden Position (Position-Open
            → Flat) zu genau einem Round-Trip (Issue #508). Die Round-Trip-PnL ist die Summe
            der Teil-Fill-PnLs (inkl. bereits allokierter Kosten); der Exit-Timestamp ist der
            des schließenden (letzten) Fills und entscheidet die IS/OOS-Klassifikation. Die
            Haltedauer wird mengengewichtet über die Teil-Fills gemittelt, das Notional
            über die Legs summiert (ökonomische Positionsgröße).

            Issue #899 — ``order_ids`` (parallel zu ``matches``) liefert die client_order_id der
            SCHLIESSENDEN Order (letztes Element, chronologisch); ihre Tags (exit_reason/ATR)
            werden über ``order_exit_meta`` aufgelöst und als Round-Trip-Telemetrie mitgeführt.

            Issue #1037 (Katalog #866) — ``is_data_end_fallback=True`` (nur vom Aufruf am Ende der
            Instrument-Fill-Schleife, ``current_rt`` bei Datenende nicht leer) markiert diesen
            Round-Trip als ``ExitReason.DATA_END`` STATT den letzten ``order_ids``-Eintrag (eine
            ENTRY-, keine Exit-Order — es gab nie einen schliessenden Fill) fälschlich über
            ``order_exit_meta`` aufzuloesen. Root-Cause: eine Position, die nie flat wurde,
            akkumuliert Matches bis zum Datenende und wird HIER zu einem Round-Trip mit der vollen
            Zeitspanne finalisiert (#1037-Symptom: ``median_bars_held`` klein, ``max_holding``
            riesig — bimodale Haltedauerverteilung mit einem zweiten Modus zwei Groessenordnungen
            ueber der Zeitbox, siehe backtest_runner-Docstring).

            Issue #976/#1130 — ``closing_price`` (der Fill-Preis des schliessenden Legs, vom
            Aufrufer aus derselben Iteration übergeben, in der die Position flat wurde) plus der
            ``ORDER_SUBMIT_TS_NS``/``TRAILING_STOP_PRICE``-Tag der schliessenden Order (nur bei
            TRAILING_STOP gesetzt, siehe ``hourly_strategy_base._execute_market_close``) ergeben
            ``stop_exit_fill_lag_ns`` (Absetzen → Fill, die von ``stop_exit_lag_bars`` — Signal →
            Absetzen — strukturell NICHT erfasste Latenz) und ``stop_exit_slippage_bps``
            (vorzeichenbehaftet: ``(fill_px − trailing_stop_price) / trailing_stop_price × 10000``)."""
            if not matches:
                return
            rt_pnl = sum(m[0] for m in matches)
            rt_exit_ts = matches[-1][1]  # schließender Fill (Matches sind chronologisch)
            total_qty = sum(m[3] for m in matches)
            if total_qty > 1e-12:
                rt_holding_ns = sum(m[2] * m[3] for m in matches) / total_qty
            else:
                rt_holding_ns = matches[-1][2]
            # Issue #1032 (Katalog #866) — ``rt_notional`` bleibt UNVERAENDERT (Kostenbasis-
            # Definition, Zero-Regression); ``rt_notional_peak`` (siehe Docstring dort) ist additiv.
            rt_notional_peak = _round_trip_notional_peak(matches)
            rt_notional = sum(m[4] for m in matches)
            rt_pnls_with_ts.append((rt_pnl, rt_exit_ts, rt_holding_ns, total_qty))
            rt_notionals_with_ts.append((rt_notional, rt_exit_ts))
            rt_notional_peaks.append(rt_notional_peak)

            closing_order_id = order_ids[-1] if order_ids else None
            meta = order_exit_meta.get(closing_order_id, {}) if closing_order_id else {}
            pnl_bps = (rt_pnl / rt_notional * 10_000.0) if rt_notional > 1e-12 else None
            # Issue #976/#1130 — nur bei einer echten (nicht is_data_end_fallback) TRAILING_STOP-
            # Order gueltig: order_submit_ts_ns/trailing_stop_price werden AUSSCHLIESSLICH fuer
            # TRAILING_STOP-Exits getaggt (siehe _parse_exit_order_tags).
            _submit_ts_ns = None if is_data_end_fallback else meta.get("order_submit_ts_ns")
            stop_exit_fill_lag_ns = (
                (rt_exit_ts - _submit_ts_ns) if _submit_ts_ns is not None else None)
            _stop_px = None if is_data_end_fallback else meta.get("trailing_stop_price")
            stop_exit_slippage_bps = (
                (closing_price - _stop_px) / _stop_px * 10_000.0
                if (closing_price is not None and _stop_px) else None)
            rt_exit_meta.append({
                "exit_reason": "DATA_END" if is_data_end_fallback else meta.get("exit_reason"),
                "atr_median_bps": meta.get("atr_median_bps"),
                "atr_min_bps": meta.get("atr_min_bps"),
                # Issue #975/#1129 — der ROHE (ungefloorte) ATR-Median, siehe _parse_exit_order_tags.
                "atr_raw_median_bps": meta.get("atr_raw_median_bps"),
                "pnl_bps": pnl_bps,
                # Issue #972/#1126 — das Round-Trip-Notional selbst als Telemetrie (Rohmaterial fuer
                # rt_notional_p05/p50/p95, macht den bps-Nenner auditierbar).
                "rt_notional": rt_notional,
                # Issue #1095 (Katalog #928) — nur bei einer echten schliessenden Order gueltig
                # (nicht is_data_end_fallback, die keine STOP_EXIT_LAG_BARS-Order-Tags traegt).
                "stop_exit_lag_bars": (
                    None if is_data_end_fallback else meta.get("stop_exit_lag_bars")),
                "stop_exit_fill_lag_ns": stop_exit_fill_lag_ns,
                "stop_exit_slippage_bps": stop_exit_slippage_bps,
            })

        # Chronologisches FIFO-Matching pro Instrument
        for iid, f_list in instrument_fills.items():
            sorted_fills = sorted(f_list, key=_fill_ts_ns)
            buy_queue: deque[tuple[float, float, int]] = deque()  # (Stückzahl, Preis, Timestamp)
            sell_queue: deque[tuple[float, float, int]] = deque() # (Stückzahl, Preis, Timestamp)
            # Matches der aktuell offenen Position; wird bei Net-Exposure-Zero-Crossing (Flat)
            # zu einem Round-Trip finalisiert. Pro Instrument ist stets nur EINE Seite offen.
            current_rt: list[FillMatchRecord] = []
            # Issue #899 — client_order_id je Match, parallel zu current_rt (fuer _finalize_round_trip).
            current_rt_order_ids: list[str] = []

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
                # Issue #899 — df_fills ist über client_order_id indiziert (ReportProvider.
                # generate_fills_report/generate_order_fills_report); itertuples() liefert ihn als
                # `.Index`. Fällt der Index aus irgendeinem Grund leer aus, bleibt die Exit-
                # Klassifikation dieses Matches None (kein Crash, siehe order_exit_meta.get-Default).
                fill_order_id = str(getattr(f, 'Index', '') or '')

                if is_buy:
                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        entry_notional = match_qty * s_price
                        if commission_bps > 0:
                            # Issue #561 — commission_bps ist per ROUND-TRIP (backtest.json._schema).
                            # Beide Legs (Entry + Exit) zusammen dürfen in Summe genau
                            # commission_bps · notional_avg kosten ⇒ halbe Rate JE Leg. Vor #561 wurde
                            # die volle Rate auf beide Legs verrechnet (2× der dokumentierten Semantik):
                            # commission_bps=1 kostete real 2 bps/Round-Trip und schob die Kostenwand
                            # exakt auf die Expectancy-Schwelle (Pitfall #115).
                            exit_value = match_qty * price
                            per_leg_bps = commission_bps / 2.0
                            pnl -= (entry_notional + exit_value) * (per_leg_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        current_rt.append((pnl, ts, holding_time_ns, match_qty, entry_notional))
                        current_rt_order_ids.append(fill_order_id)
                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price, s_ts)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()
                    # Short vollständig gedeckt (Gegenseite leer) ⇒ Position flat ⇒ Round-Trip zu.
                    if not sell_queue and current_rt:
                        _finalize_round_trip(current_rt, current_rt_order_ids, closing_price=price)
                        current_rt = []
                        current_rt_order_ids = []
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
                            # Issue #561 — commission_bps ist per ROUND-TRIP (backtest.json._schema).
                            # Halbe Rate JE Leg (Entry + Exit), Summe == commission_bps · notional_avg
                            # EINMAL. Symmetrisch zum Buy-Zweig oben (Pitfall #115).
                            exit_value = match_qty * price
                            per_leg_bps = commission_bps / 2.0
                            pnl -= (entry_notional + exit_value) * (per_leg_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        current_rt.append((pnl, ts, holding_time_ns, match_qty, entry_notional))
                        current_rt_order_ids.append(fill_order_id)
                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price, b_ts)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()
                    # Long vollständig verkauft (Gegenseite leer) ⇒ Position flat ⇒ Round-Trip zu.
                    if not buy_queue and current_rt:
                        _finalize_round_trip(current_rt, current_rt_order_ids, closing_price=price)
                        current_rt = []
                        current_rt_order_ids = []
                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        sell_queue.append((qty, price, ts_entry))

            # Position am Datenende noch offen (Teil-Fills realisiert, aber nie flat): die
            # realisierte Teilmenge bildet einen (offenen) Round-Trip — bewahrt die Invariante
            # Σ Round-Trip-PnL == Σ Match-PnL. Issue #1037 — als DATA_END markiert (keine echte
            # Handelsentscheidung).
            if current_rt:
                _finalize_round_trip(current_rt, current_rt_order_ids, is_data_end_fallback=True)



        if not pnls_with_ts:
            if log_fn:
                log_fn("[Metriken] Fills vorhanden, jedoch keine Trade-Schließungen (FIFO) generiert.")
            if walk_forward_dict and start_ns is not None:
                return {"metrics": NULL, "oos_metrics": NULL,
                        "_oos_window_start_ns": None, "_oos_covered": None}
            return NULL

        if log_fn:
            log_fn(f"[Metriken] FIFO-Extraktion: {len(rt_pnls_with_ts)} Round-Trips "
                   f"(aus {len(pnls_with_ts)} Fill-Matches) erfolgreich berechnet.")

        # Issue #946/#1112 (Katalog #960) — Dust-Round-Trip-Boden AN DER QUELLE, VOR jeder IS/OOS-/
        # Fold-Aufteilung und VOR jedem Konsumenten (``_calculate_stats``, ``_aggregate_exit_
        # telemetry``, Kostenstress, Portfolio-/Fold-Metriken) — siehe ``_filter_dust_round_trips``-
        # Docstring fuer die Root-Cause. Die vier parallelen Listen werden HIER, EINMAL, ersetzt;
        # jeder spaetere Codepfad in dieser Funktion liest sie ausschliesslich ueber den Namen
        # (kein Index/Referenz von VOR dieser Zeile bleibt in Gebrauch), die Filterung propagiert
        # deshalb korrekt in JEDEN nachgelagerten Wert.
        (rt_pnls_with_ts, rt_notionals_with_ts, rt_notional_peaks, rt_exit_meta,
         _dust_round_trips_discarded, _dust_round_trips_discarded_meta) = _filter_dust_round_trips(
            rt_pnls_with_ts, rt_notionals_with_ts, rt_notional_peaks, rt_exit_meta)
        if _dust_round_trips_discarded and log_fn:
            log_fn(f"[Metriken] #946: {len(_dust_round_trips_discarded)} Dust-Round-Trip(s) "
                   "(Notional < 5% des Median-Notionals) an der Quelle verworfen.")

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

        # ── Fold-Geometrie & MtM-Slices (gemeinsam für beide Aggregationsebenen) ────────
        _wf = bool(walk_forward_dict and start_ns is not None)
        fold_boundaries: list[tuple[int, int, int]] = []
        _is_start_ns = start_ns
        is_end_ns: int | None = None
        is_window_ns = oos_window_ns = embargo_period_ns = 0
        splits = 0
        if _wf:
            is_window_ns = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
            oos_window_ns = walk_forward_dict.get("oos_window_days", 30) * 86400 * 1_000_000_000
            splits = walk_forward_dict.get("splits", 2)
            embargo_period_ns = walk_forward_dict.get("embargo_period_days", 0) * 86400 * 1_000_000_000
            # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth (kein Inline-Nachbau).
            fold_boundaries = compute_fold_boundaries(start_ns, walk_forward_dict)
            # IS Window boundaries are deterministic and identical for all folds
            is_end_ns = start_ns + is_window_ns

        import statistics

        is_mtm = None
        oos_mtm = None
        oos_frames = None
        oos_buyhold_return = None  # Issue #552 — Buy&Hold-Benchmark-Return über das OOS-Fenster.
        if mtm_series is not None and not mtm_series.empty and _wf:
            # Issue #551 — Equity-Slices HALB-OFFEN [s, e), konsistent zur Trade-Klassifikation
            # (``any(s <= ts < e ...)``). ``pandas.loc[a:b]`` ist auf BEIDEN Seiten geschlossen; da
            # bei kontinuierlichen Folds ``oos_end_k == oos_start_{k+1}`` gilt, läge der Grenz-Bar
            # sonst in ZWEI benachbarten Fold-Segmenten und würde im compoundierten Return (über
            # ``mtm_frames``, das NICHT dedupliziert wird) doppelt gezählt. 1 ns vor dem exklusiven
            # Ende zu schneiden macht die Intervall-Konvention systemweit einheitlich. Siehe
            # AGENTS.md Pitfall #111.
            def _slice_half_open(series, s_ns_, e_ns_):
                start_dt = pd.to_datetime(s_ns_, unit="ns")
                end_excl = pd.to_datetime(e_ns_, unit="ns") - pd.Timedelta(nanoseconds=1)
                return series.loc[start_dt:end_excl]

            def _fold_segments_half_open(series, boundaries) -> list:
                """Issue #772 — gemeinsame Extraktion der NICHT-leeren, halb-offen geschnittenen
                Fold-Segmente einer Zeitserie über ``fold_boundaries`` — Single Source of Truth für
                Strategie-Equity UND Benchmark (vorher zwei inline kopierte Schleifen; Divergenz-
                Falle analog ``compute_fold_boundaries``, AGENTS.md Pitfall #231)."""
                if series is None or series.empty:
                    return []
                out = []
                for _, s_ns_, e_ns_ in boundaries:
                    seg = _slice_half_open(series, s_ns_, e_ns_)
                    if not seg.empty:
                        out.append(seg)
                return out

            def _concat_half_open(frames: list):
                """Issue #772 — konkateniert + dedupliziert + sortiert eine Liste von Fold-
                Segmenten (Sicherheitsnetz gegen doppelt gezählte Grenz-Bars bei kontiguierlichen
                Folds, jetzt i. d. R. No-Op, da die Segmente disjunkt sind)."""
                if not frames:
                    return None
                concat = pd.concat(frames)
                return concat[~concat.index.duplicated(keep="last")].sort_index()

            is_mtm = _slice_half_open(mtm_series, start_ns, start_ns + is_window_ns)

            oos_frames = _fold_segments_half_open(mtm_series, fold_boundaries)
            oos_mtm = _concat_half_open(oos_frames)

            # Issue #552/#772 — Buy&Hold-Benchmark-Return des Symbols über EXAKT dieselbe (halb-
            # offene, konkatenierte, deduplizierte) OOS-Fensterung wie der Strategie-Return —
            # DERSELBE Helper (``_fold_segments_half_open``/``_concat_half_open``) wie für
            # ``oos_mtm`` oben, keine zweite, potenziell divergierende Kopie der Schleife. Damit
            # misst das (opt-in) Excess-Gate ALPHA (Strategie − Markt) statt bloßes Long-Bias-Beta.
            # Rein additive Telemetrie; fehlt die Benchmark-Serie ⇒ oos_buyhold_return bleibt None
            # (Legacy-Gate).
            #
            # Issue #772 — NICHT MEHR per-Fold kompoundiert (das war #632s bewusste Entscheidung,
            # WEIL `total_return` es damals war): #771 stellt `total_return` auf die volle
            # konkatenierte Spanne um (dieselbe Bar-Menge wie `period_rets`) — der Benchmark muss
            # SYMMETRISCH mitgezogen werden, sonst kehrt der #552-Span-Bug mit umgekehrtem Vorzeichen
            # zurück (AGENTS.md Pitfall #231). Zähler (Strategie) und Nenner (Benchmark) decken damit
            # weiterhin BIT-IDENTISCH dieselbe Bar-Menge ab — jetzt die volle Fold-Union statt der
            # Pro-Fold-Kompoundierung.
            if benchmark_series is not None and not benchmark_series.empty:
                bench_frames = _fold_segments_half_open(benchmark_series, fold_boundaries)
                bench_oos_concat = _concat_half_open(bench_frames)
                if (bench_oos_concat is not None and len(bench_oos_concat) > 1
                        and float(bench_oos_concat.iloc[0]) != 0.0):
                    oos_buyhold_return = float(bench_oos_concat.iloc[-1]) / float(bench_oos_concat.iloc[0]) - 1.0
                    # Issue #772 — Index-Gleichheits-Assertion: Strategie- und Benchmark-OOS-Serie
                    # müssen bitgleich dieselbe Bar-Menge beschreiben (Akzeptanzkriterium #772/1) —
                    # nur eine Längen-Prüfung würde eine verschobene, aber gleich lange Indexmenge
                    # nicht erkennen.
                    if oos_mtm is not None and not oos_mtm.index.equals(bench_oos_concat.index):
                        import logging
                        logging.getLogger("optimizer").error(
                            "BENCHMARK_SPAN_MISMATCH (#772): Strategie-OOS-Index (%d Bars) != "
                            "Benchmark-OOS-Index (%d Bars) — excess_return vergleicht keine "
                            "bitgleiche Bar-Menge mehr.",
                            len(oos_mtm), len(bench_oos_concat),
                        )
        elif mtm_series is not None and not mtm_series.empty:
            is_mtm = mtm_series

        def _empty_level_metrics() -> dict[str, Any]:
            return {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "sortino_ratio": 0.0, "calmar_ratio": 0.0,
                "max_drawdown": 0.0, "total_return": 0.0,
                "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
                "losses_count": 0,
                "median_position_notional": 0.0,
            }

        def _split_and_stats(records: list, notionals: list, exit_meta: list[dict] | None = None) -> tuple[dict, dict]:
            """IS/OOS-Split einer Record-Liste (Round-Trip ODER Fill-Match) + ``_calculate_stats``
            je Ebene (Issue #508). Beide Ebenen teilen sich die IDENTISCHE Fold-Geometrie; der
            einzige Unterschied ist die Aggregations-Granularität der übergebenen Records. Jeder
            Record ist ``(pnl, exit_ts_ns, holding_ns, qty)``, ``notionals[i]`` das parallele
            ``(entry_notional, exit_ts_ns)``. Rückgabe: ``(is_metrics, oos_metrics)``.

            Issue #899 — ``exit_meta`` (optional, parallel zu ``records``) liefert die Exit-
            Telemetrie (exit_reason/ATR/pnl_bps) je Record; wird nur für die Round-Trip-Ebene
            übergeben (die Fill-Match-Ebene bleibt reine Execution-Diagnostik, siehe #508-Docstring
            oben im Modul)."""
            split_is_pnls, split_oos_pnls = [], []
            split_is_holds, split_oos_holds = [], []
            split_is_notionals, split_oos_notionals = [], []
            split_is_meta, split_oos_meta = [], []
            for rec_idx, (pnl, ts, ht, rec_qty) in enumerate(records):
                notional = notionals[rec_idx][0]
                if _wf:
                    is_oos = any(s <= ts < e for _, s, e in fold_boundaries)
                    is_in_sample = _is_start_ns <= ts < is_end_ns
                else:
                    is_oos, is_in_sample = False, True
                if is_oos:
                    split_oos_pnls.append(pnl)
                    split_oos_holds.append((ht, rec_qty))
                    split_oos_notionals.append(notional)
                    if exit_meta is not None:
                        split_oos_meta.append(exit_meta[rec_idx])
                elif is_in_sample:
                    split_is_pnls.append(pnl)
                    split_is_holds.append((ht, rec_qty))
                    split_is_notionals.append(notional)
                    if exit_meta is not None:
                        split_is_meta.append(exit_meta[rec_idx])
            is_mn = statistics.median(split_is_notionals) if split_is_notionals else 0.0
            oos_mn = statistics.median(split_oos_notionals) if split_oos_notionals else 0.0
            # Issue #546 — die parallelen Per-Trade-Notionals durchreichen ⇒ sizing-invariante
            # (notional-relative) Expectancy statt Normierung auf das fixe starting_capital.
            level_is = _calculate_stats(split_is_pnls, split_is_holds, starting_capital, med_notional=is_mn, mtm_series=is_mtm, notional_list=split_is_notionals, family_median_n_periods=family_median_n_periods, symbol=symbol)
            if split_oos_pnls:
                level_oos = _calculate_stats(split_oos_pnls, split_oos_holds, starting_capital, med_notional=oos_mn, mtm_series=oos_mtm, mtm_frames=oos_frames, notional_list=split_oos_notionals, family_median_n_periods=family_median_n_periods, symbol=symbol)
            else:
                level_oos = _empty_level_metrics()
            # Issue #613 — Aggregationsebene des Sortino EXPLIZIT stempeln. IS UND OOS werden aus ihrer
            # jeweiligen mtm-Equity-Kurve (pooled) abgeleitet — DERSELBE ``_calculate_stats``-Pfad. Der
            # Basis-Tag macht die IS↔OOS-Kohärenz (Divergenz-Strafe, #613) fail-loud prüfbar; ein
            # ``trade_sequential``-Sortino (Legacy-Fallback ohne Equity-Kurve) darf NIE gegen einen
            # ``pooled_equity_curve``-Sortino verglichen werden (inkommensurable Skalen).
            level_is["sortino_aggregation_basis"] = (
                "pooled_equity_curve" if (is_mtm is not None and not is_mtm.empty) else "trade_sequential")
            level_oos["sortino_aggregation_basis"] = (
                "pooled_equity_curve" if (oos_mtm is not None and not oos_mtm.empty) else "trade_sequential")
            # Issue #899 — Exit-Telemetrie je Ebene (nur, wenn exit_meta übergeben wurde).
            if exit_meta is not None:
                level_is.update(_aggregate_exit_telemetry(split_is_meta))
                level_oos.update(_aggregate_exit_telemetry(split_oos_meta))
                # Issue #947 (Katalog B) — ein via EQUITY_STOPOUT geschlossener Round-Trip macht
                # den gesamten Trial infeasible (wirtschaftlich ruiniert), nicht nur "schlecht
                # bewertet": derselbe TRIAL_RUINED_STOPOUT-Code wie EQUITY_NONPOSITIVE
                # (_contracts.py, failure_policy='prune'), damit run_optimization.py ihn ueber
                # denselben, bereits verdrahteten inference_failure_policy='prune'-Pfad behandelt
                # (#945), OHNE eine zweite, parallele Infeasibility-Kodierung einzufuehren.
                for _level_name, _level_dict in (("is", level_is), ("oos", level_oos)):
                    _stopout_n = (_level_dict.get("exit_reason_histogram") or {}).get(
                        "EQUITY_STOPOUT", 0)
                    if _stopout_n > 0:
                        _level_dict.setdefault("inference_diagnostics", []).append({
                            "code": "TRIAL_RUINED_STOPOUT",
                            "detail": f"{_stopout_n} Round-Trip(s) via EQUITY_STOPOUT geschlossen "
                                     f"({_level_name.upper()}) — Trial wirtschaftlich ruiniert "
                                     "(#947).",
                            "value": _stopout_n,
                        })
                # Issue #899 Fix 2 — oos_max_holding_bars als PRIMÄRE Bar-Messgrösse (statt nur
                # oos_max_holding_time_s / 3600 im Konsumenten). #902 entfernt die verstreuten
                # 3600.0-Literale aus invariants.py/run_optimization.py zugunsten von bar_seconds
                # als Pflichtparameter dort; diese Stelle bleibt bei der bereits bestehenden
                # 1h-Bar-Konvention von median_bars_held/p95_bars_held (siehe _calculate_stats).
                level_is["max_holding_bars"] = (level_is.get("max_holding_time_s") or 0.0) / _BAR_SECONDS_METRICS
                level_oos["max_holding_bars"] = (level_oos.get("max_holding_time_s") or 0.0) / _BAR_SECONDS_METRICS
            return level_is, level_oos

        # ── Primär: Round-Trip-Ebene (Gate-Eligibility & Walk-Forward-Validierung, #508) ─
        is_metrics, oos_metrics = _split_and_stats(rt_pnls_with_ts, rt_notionals_with_ts, exit_meta=rt_exit_meta)

        # Issue #946/#1112 (Katalog #960) — die an der Quelle verworfenen Dust-Round-Trips (siehe
        # oben) DIESELBE IS/OOS-Klassifikation wie jeder andere Round-Trip, damit
        # ``dust_round_trips_filtered`` (report.py, Rohmaterial fuer
        # ``invariants.check_dust_round_trip_share``) auf DERSELBEN Grundgesamtheit steht wie sein
        # Nenner ``oos_total_trades_with_exit_telemetry``.
        _is_dust_count, _oos_dust_count = 0, 0
        # Issue #972/#1126 Fix Punkt 2 — dieselbe IS/OOS-Klassifikation, aber beschraenkt auf Dust-
        # Round-Trips, die ein NACHWEISLICHER TRAILING_STOP-Verlust-Exit waren (dieselbe Teilmenge
        # wie losses_bps_trailing_stop in _aggregate_exit_telemetry) — Zaehler fuer
        # n_trailing_stop_losses_dust_filtered / n_trailing_stop_losses.
        _is_ts_dust_count, _oos_ts_dust_count = 0, 0
        for (_nz, _ts), _meta in zip(_dust_round_trips_discarded, _dust_round_trips_discarded_meta):
            if _wf:
                _is_oos = any(s <= _ts < e for _, s, e in fold_boundaries)
                _is_in_sample = _is_start_ns <= _ts < is_end_ns
            else:
                _is_oos, _is_in_sample = False, True
            _is_ts_loss = (_meta.get("exit_reason") == "TRAILING_STOP"
                          and _meta.get("pnl_bps") is not None and _meta["pnl_bps"] < 0)
            if _is_oos:
                _oos_dust_count += 1
                if _is_ts_loss:
                    _oos_ts_dust_count += 1
            elif _is_in_sample:
                _is_dust_count += 1
                if _is_ts_loss:
                    _is_ts_dust_count += 1
        is_metrics["dust_round_trips_filtered_count"] = _is_dust_count
        oos_metrics["dust_round_trips_filtered_count"] = _oos_dust_count
        is_metrics["n_trailing_stop_losses_dust_filtered"] = _is_ts_dust_count
        oos_metrics["n_trailing_stop_losses_dust_filtered"] = _oos_ts_dust_count

        # Issue #1032 (Katalog #866) — Spitzenbestand-Telemetrie je Ebene, dieselbe IS/OOS-
        # Fold-Aufteilung wie ``_split_and_stats`` (hier separat, da ``rt_notional_peaks`` kein
        # ``_calculate_stats``-Eingang ist, sondern additive Round-Trip-Rohtelemetrie).
        _is_notional_peaks, _oos_notional_peaks = [], []
        for _rt_idx, (_pnl, _ts, _ht, _rt_qty) in enumerate(rt_pnls_with_ts):
            _peak = rt_notional_peaks[_rt_idx] if _rt_idx < len(rt_notional_peaks) else None
            if _peak is None:
                continue
            if _wf:
                _is_oos = any(s <= _ts < e for _, s, e in fold_boundaries)
                _is_in_sample = _is_start_ns <= _ts < is_end_ns
            else:
                _is_oos, _is_in_sample = False, True
            if _is_oos:
                _oos_notional_peaks.append(_peak)
            elif _is_in_sample:
                _is_notional_peaks.append(_peak)
        if _is_notional_peaks:
            is_metrics["median_position_notional_peak"] = statistics.median(_is_notional_peaks)
            is_metrics["max_position_notional_peak"] = max(_is_notional_peaks)
        if _oos_notional_peaks:
            oos_metrics["median_position_notional_peak"] = statistics.median(_oos_notional_peaks)
            oos_metrics["max_position_notional_peak"] = max(_oos_notional_peaks)

        # Issue #1042 (Katalog #866) E-1 — Kosten-Stressband als additive Telemetrie, DIESELBE
        # IS/OOS-Aufteilung wie die ``rt_notional_peaks``-Bloecke oben (kein zweiter
        # ``_calculate_stats``-Eingang: die Stress-PnL ist eine reine Nachverarbeitung der bereits
        # extrahierten Round-Trip-PnL/-Notional-Paare, kein neuer Backtest-Lauf). Issue #1081
        # (Katalog #866-2) — ``round_trip_cost_bps`` (voller c_rt = Spread + Kommission, sofern vom
        # Aufrufer übergeben; sonst Fallback ``commission_bps`` allein, Pre-#1081-Verhalten) ist die
        # bereits im Backtest angewandte Round-Trip-KOSTENGRÖSSE (EINMAL abgezogen); ein
        # Stress-Multiplikator ``s`` zieht zusaetzlich ``(s-1) · round_trip_cost_bps/10000 ·
        # notional`` je Round-Trip ab — ökonomisch: "wie waere die Expectancy, haette der Broker
        # s-mal so hohe Round-Trip-Kosten verlangt". Root-Cause #1081: VOR diesem Fix stresste dieser
        # Block ausschliesslich ``commission_bps`` (1,0 bps bei EQUITY) — der Spread (3,0 bps, 75 %
        # von c_rt) blieb unangetastet; ein "2×-Stress" erhoehte die realen Kosten damit nur um 25 %
        # (4,0 → 5,0 bps), nicht um 100 % (→ 8,0 bps). Denselben 5%-Median-Notional-Nennerboden wie
        # ``expectancy_capital_weighted`` (#1031) — ein einzelner Mikro-Trade darf die
        # kapitalgewichtete Kennzahl nicht dominieren. Berechnung in der standalone, direkt
        # testbaren ``_expectancy_cost_stress`` (analog #1032s ``_round_trip_notional_peak``).
        _round_trip_cost_bps_for_stress = (
            round_trip_cost_bps if round_trip_cost_bps is not None else commission_bps)
        _is_pnl_notional, _oos_pnl_notional = [], []
        for _rt_idx, (_pnl, _ts, _ht, _rt_qty) in enumerate(rt_pnls_with_ts):
            _nz = rt_notionals_with_ts[_rt_idx][0] if _rt_idx < len(rt_notionals_with_ts) else None
            if _nz is None:
                continue
            if _wf:
                _is_oos = any(s <= _ts < e for _, s, e in fold_boundaries)
                _is_in_sample = _is_start_ns <= _ts < is_end_ns
            else:
                _is_oos, _is_in_sample = False, True
            if _is_oos:
                _oos_pnl_notional.append((_pnl, _nz))
            elif _is_in_sample:
                _is_pnl_notional.append((_pnl, _nz))
        for _level_metrics, _level_pnl_notional in ((is_metrics, _is_pnl_notional), (oos_metrics, _oos_pnl_notional)):
            _stress_1_5x = _expectancy_cost_stress(
                _level_pnl_notional, round_trip_cost_bps=_round_trip_cost_bps_for_stress, multiplier=1.5)
            _stress_2x = _expectancy_cost_stress(
                _level_pnl_notional, round_trip_cost_bps=_round_trip_cost_bps_for_stress, multiplier=2.0)
            _level_metrics["expectancy_round_trip_cost_stress_1_5x"] = _stress_1_5x
            _level_metrics["expectancy_round_trip_cost_stress_2x"] = _stress_2x
            # Issue #1081 — Uebergangs-Alias (eine Sitzung lang): der alte Name blieb bestehen,
            # traegt jetzt aber denselben, kosten-VOLLSTAENDIGEN Wert wie der neue Name (vorher nur
            # die Kommission). Bestehende Konsumenten des alten Schluessels lesen automatisch den
            # korrigierten Wert, bis sie auf den neuen Namen migriert sind.
            _level_metrics["expectancy_cost_stress_1_5x"] = _stress_1_5x
            _level_metrics["expectancy_cost_stress_2x"] = _stress_2x

        # Integrity Guard (Issue #528, Task 1.2 & 1.3)
        oos_total_trades = oos_metrics.get("total_trades", 0)
        if oos_total_trades > 0 and (mtm_series is None or mtm_series.empty):
            if log_fn:
                log_fn("[MtM] ⚠️ KRITISCH: OOS-Trades vorhanden, aber Equity-Kurve leer — "
                       "total_return/max_drawdown/sortino sind NICHT vertrauenswürdig (Pitfall #90).")
            oos_metrics["mtm_empty"] = True

        # ── Sekundär: Fill-Match-Ebene (Execution-Diagnostik, #508) ─────────────────────
        fm_is_metrics, fm_oos_metrics = _split_and_stats(pnls_with_ts, notionals_with_ts)

        # Issue #303/#508 — Per-Fold-OOS-Sortinos AUF ROUND-TRIP-EBENE (primäre Gate-Basis).
        per_fold_oos_list = []
        if _wf:
            # Issue #443 — innere Fold-Schleifen heißen `fold`, kein Shadowing der äußeren Iteration.
            for _is_start_ns_fold, split_oos_start_ns, split_oos_end_ns in fold_boundaries:
                fold_pnls = []
                fold_holds = []
                fold_notionals = []
                for fold_idx, (pnl, ts, ht, rt_qty) in enumerate(rt_pnls_with_ts):
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        fold_pnls.append(pnl)
                        fold_holds.append((ht, rt_qty))
                        fold_notionals.append(rt_notionals_with_ts[fold_idx][0])

                fold_med_notional = statistics.median(fold_notionals) if fold_notionals else 0.0
                fold_mtm = None
                if mtm_series is not None and not mtm_series.empty:
                    # Issue #551 — halb-offene Fold-Equity-Slice [start, end), konsistent zur
                    # Trade-Klassifikation oben (``split_oos_start_ns <= ts < split_oos_end_ns``).
                    split_oos_start_dt = pd.to_datetime(split_oos_start_ns, unit="ns")
                    split_oos_end_excl = pd.to_datetime(split_oos_end_ns, unit="ns") - pd.Timedelta(nanoseconds=1)
                    fold_mtm = mtm_series.loc[split_oos_start_dt:split_oos_end_excl]
                if fold_pnls:
                    # Issue #546 — Per-Fold-Expectancy ebenfalls notional-relativ (Fold-Aggregation #550).
                    fold_metrics = _calculate_stats(fold_pnls, fold_holds, starting_capital, med_notional=fold_med_notional, mtm_series=fold_mtm, notional_list=fold_notionals, family_median_n_periods=family_median_n_periods, symbol=symbol)
                else:
                    fold_metrics = None
                per_fold_oos_list.append(fold_metrics)

            # Issue #549/#550 — kanonische Fold-Median-Aggregation der Gate-Kennzahlen + Fold-
            # Konsistenz-Telemetrie an DER Quelle (Single Source of Truth), damit Gate und Reward
            # denselben Sortino sehen und kein Glücks-Sub-Fenster belohnt wird. Reine Funktion,
            # separat unit-getestet (test_issue_549/#550).
            apply_fold_aggregation(oos_metrics, per_fold_oos_list)

            # Issue #675 — rollierende IS/OOS-Divergenz-Diagnose, NUR wenn ``walk_forward.retrain``
            # aktiv ist (Zero-Hardcoding: bei Default/False bleibt der Trial-Output bit-identisch —
            # kein neues Telemetrie-Feld, keine Gate-/Reward-Wirkung). Reine additive Forensik.
            if walk_forward_dict.get("retrain", False):
                oos_metrics["oos_fold_is_oos_divergence"] = rolling_fold_is_oos_divergence(
                    mtm_series, fold_boundaries, is_window_ns)

            # Issue #552 — Benchmark-relative Alpha-Telemetrie: excess = Strategie-OOS-Return −
            # Buy&Hold-OOS-Return. Nur wenn die Benchmark-Serie verfügbar war (sonst greift das
            # Legacy-Absolut-Gate). oos_total_return bleibt der compoundierte Strategie-Return.
            if oos_buyhold_return is not None:
                oos_metrics["oos_buyhold_return"] = oos_buyhold_return
                oos_metrics["oos_excess_return"] = (
                    (oos_metrics.get("total_return") or 0.0) - oos_buyhold_return)

        # Issue #303/#508 — OOS-Trade-Records AUF ROUND-TRIP-EBENE (eine Position == ein Record)
        # für die chronologische Portfolio-Aggregation in select_winners. Tupel-Arity bleibt
        # (pnl, ts, ht, qty, notional). Ohne Walk-Forward bleibt `fold_boundaries` leer ⇒ keine Records.
        oos_trade_records = []
        for rt_idx, (pnl, ts, ht, rt_qty) in enumerate(rt_pnls_with_ts):
            if any(s <= ts < e for _, s, e in fold_boundaries):
                oos_trade_records.append((pnl, ts, ht, rt_qty, rt_notionals_with_ts[rt_idx][0]))
        oos_metrics["_oos_trade_records"] = oos_trade_records

        # Issue #508 — Dual-Reporting-Schema: beide Aggregationsebenen isoliert ausweisen.
        round_trips: MetricsLevel = {"metrics": is_metrics, "oos_metrics": oos_metrics}
        fill_matches: MetricsLevel = {"metrics": fm_is_metrics, "oos_metrics": fm_oos_metrics}

        if _wf:
            return {
                # `metrics`/`oos_metrics` bleiben die PRIMÄREN (round-trip-basierten) Gate-Metriken.
                "metrics": is_metrics,
                "oos_metrics": oos_metrics,
                # Issue #508 — explizite Dual-Reporting-Ebenen (round_trips primär, fill_matches sekundär).
                "round_trips": round_trips,
                "fill_matches": fill_matches,
                # Issue #444/#448 — beobachtete Fill-ts-Spanne für die data_window-Telemetrie.
                "_fill_ts_min": fill_ts_min,
                "_fill_ts_max": fill_ts_max,
                # Issue #455 — OOS-Abdeckungs-Grenze + ob die Fills sie erreichen.
                "_oos_window_start_ns": oos_window_start_ns,
                "_oos_covered": oos_covered,
            }
        else:
            # Fallback for backwards compatibility if oos isn't requested. Die Round-Trip-Ebene
            # bleibt primär; die Fill-Match-Diagnostik wird als Unterschlüssel beigelegt (#508).
            is_metrics["fill_matches"] = fm_is_metrics
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
            # We filter out the All-Win sentinel to prevent it from contaminating the distribution.
            # We don't filter the scaled sentinels since they represent legitimate rank differences.
            non_sentinels = [v for v in vals if v != _ALL_WIN_SENTINEL]
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
            scaled = min(_ALL_WIN_SENTINEL, max(2.0, _ALL_WIN_SENTINEL * (n_trades / _ALL_WIN_SENTINEL)))
            # 2. Prevent dynamic sentinel from outranking the highest genuine organic ratio
            organic_ratios = [v for v in population_ratios if v is not None and v != _ALL_WIN_SENTINEL]
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

    # Issue #553 — Deflated-Sortino-Selektion (OPT-IN, Multiple-Testing-Korrektur). Fehlt das Flag
    # ⇒ deflated_selection=False ⇒ bit-identisch zum Status quo (kein zusätzliches Gate).
    deflated_selection = bool(tournament_cfg.get("deflated_selection", False))
    deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))

    per_symbol_winners = {}
    for sym, candidates in grouped_by_symbol.items():
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (c.get("_score", float("-inf")), c["metrics"].get("total_return", 0.0)),
            reverse=True
        )

        # Issue #553 — deflationierte Rausch-Schwelle für den OOS-Sortino des Winners. Über die
        # Anzahl effektiv getesteter (OOS-evaluierter) Konfigurationen und deren Cross-Trial-
        # Streuung: der Winner muss das erwartete Best-of-N-Rauschen schlagen, nicht nur das
        # statische Gate (Bailey & López de Prado). None ⇒ Bedingung inaktiv (Legacy-Pfad).
        deflated_min_sortino = None
        if deflated_selection:
            import statistics as _dstats
            cand_sortinos = [c.get("oos_metrics", {}).get("sortino_ratio") for c in candidates
                             if c.get("_oos_eval", {}).get("oos_evaluated")]
            # Issue #592 — der frühere 50.0-Sentinel-Filter entfällt ERSATZLOS: nach #588 gibt
            # es keinen Sortino-Clip (und damit keine Clip-Sentinels) mehr; die hartcodierte 50.0
            # (Wert-Drift seit RATIO_CAP 50→15→entfernt) hätte einen legitimen Sortino von exakt 50.0
            # fälschlich verworfen. None bleibt gefiltert (undefinierter Sortino).
            cand_sortinos = [float(s) for s in cand_sortinos if s is not None]
            if len(cand_sortinos) >= 2:
                dispersion = _dstats.pstdev(cand_sortinos)
                from automation.optimizer.deflation import deflated_threshold
                deflated_min_sortino = deflated_threshold(
                    len(cand_sortinos), dispersion,
                    confidence=deflation_confidence, baseline=0.0)
                # Telemetrie: effektive (deflationierte) Schwelle je Study/Symbol.
                print(f"  [Deflated #553] {sym}: effektive OOS-Sortino-Schwelle "
                      f"{deflated_min_sortino:.4f} (N={len(cand_sortinos)}, σ={dispersion:.4f}, "
                      f"conf={deflation_confidence})")

        for r in candidates_sorted:
            strat = r["strategy"]
            score = r.get("_score", 0.0)
            oos_eval = r["_oos_eval"]

            if oos_eval.get("oos_eligible", False):
                # Issue #553 — zusätzlich das deflationierte Rausch-Maximum schlagen (opt-in).
                if deflated_min_sortino is not None:
                    cand_oos_sortino = (r.get("oos_metrics") or {}).get("sortino_ratio")
                    if cand_oos_sortino is None or float(cand_oos_sortino) < deflated_min_sortino:
                        print(f"  [Deflated-Drop #553] {sym} | {strat}: OOS-Sortino "
                              f"{cand_oos_sortino} < deflated {deflated_min_sortino:.4f}")
                        continue
                winner_entry = {
                    "strategy": strat,
                    "metrics": r["metrics"],
                    "score": round(score, 6),
                    **oos_eval
                }
                if deflated_min_sortino is not None:
                    winner_entry["deflated_min_sortino"] = deflated_min_sortino
                per_symbol_winners[sym] = winner_entry
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
            # Issue #288 / #263: Filter out None and the All-Win sentinel to prevent distortion.
            # Scaled sentinels (< sentinel) are deliberately kept to reflect sample size significance.
            vals = [v for v in vals if v is not None]
            non_sentinel_vals = [v for v in vals if v != _ALL_WIN_SENTINEL]

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
            # Issue #546 — notional-relative Expectancy auch auf der aggregierten Portfolio-Ebene.
            portfolio_metrics = _calculate_stats(portfolio_pnls, portfolio_holds, starting_capital, med_notional=portfolio_med_notional, notional_list=portfolio_notionals)

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

            # Issue #859 Fix Punkt 4 — die Portfolio-Aggregation baut ``avg_oos`` frisch aus den
            # gepoolten Trades (``_calculate_stats``); ein je-Symbol ``inference_diagnostics``-
            # Eintrag (z. B. ``EXIT_CLOSE_UNRECOVERABLE``, HourlyStrategyBase.
            # _handle_exit_close_order_failure via run_single_backtest_worker) würde hier sonst
            # spurlos verschwinden. Vor dem Eligibility-Aufruf angehängt, damit
            # ``_evaluate_oos_eligibility`` (EINE Prüfstelle für beide Konsumenten, siehe dort)
            # dieselbe Invalidierung sieht wie der Per-Symbol-Kandidatenpfad.
            avg_oos["inference_diagnostics"] = [
                diag for oos in best_results for diag in (oos.get("inference_diagnostics") or [])
            ]

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
                            # Issue #546 — notional-relative Expectancy auch je Aggregat-Fold.
                            fold_metrics = _calculate_stats(fold_pnls, fold_holds, starting_capital, med_notional=fold_med_notional, notional_list=fold_notionals)
                        else:
                            fold_metrics = None
                        per_fold_oos_list.append(fold_metrics)

                    agg_oos_eval["oos_fold_sortinos"] = collect_oos_fold_sortinos(per_fold_oos_list)
                    # Issue #665 — die annualisierungs-invariante Parallelgrösse (siehe
                    # collect_oos_fold_sortino_periods-Docstring), auch auf der Aggregat-Ebene.
                    agg_oos_eval["oos_fold_sortino_periods"] = collect_oos_fold_sortino_periods(per_fold_oos_list)

        else:

            agg_oos_eval = {
                "oos_evaluated": False,
                "oos_eligible": False,
                "oos_metrics": None,
                "oos_rejection_reasons": ["oos_not_evaluable: Kein OOS-Datenmaterial für die Gewinn-Symbole."]
            }

        # Assertion: Aggregate OOS pass cannot override a per-pair failure
        assert not (agg_oos_eval.get("oos_eligible", False) is True and len(per_symbol_winners) == 0), "Aggregat-OOS-Pass darf nicht das Per-Pair-Gate überstimmen (eligible_pairs == 0)"

        _agg_median_is_sortino = round(
            get_median([x for x in sortinos_by_strat[best] if x is not None]), 4
        )
        aggregate_winner = {
            "strategy":    best,
            "win_count":   win_counts[best],
            "median_is_sortino": _agg_median_is_sortino,
            # Issue #613 — die per-Symbol-IS-Sortinos in ``sortinos_by_strat`` stammen aus der jeweiligen
            # IS-Equity-Kurve (``_split_and_stats``). Der Aggregat-Wert ist ein Symbol-Median GEPOOLTER
            # per-Symbol-Sortinos; explizit als solcher getaggt, damit die Divergenz-Kohärenz-Prüfung
            # (#613) die Aggregationsebene kennt. Der gepoolte OOS-Sortino der Aggregat-Ebene wird ohne
            # mtm berechnet (``trade_sequential``) ⇒ der Divergenz-Term ist primär ein Per-Symbol-Signal.
            "median_is_sortino_pooled": _agg_median_is_sortino,
            "is_sortino_aggregation_basis": "symbol_median_pooled_equity",
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

    # Issue #613 — der gepoolte IS-Sortino stammt aus DERSELBEN mtm-Equity-Kurve wie der OOS-Sortino
    # (``_split_and_stats``, ``is_metrics["sortino_ratio"]``) — die kohärente Divergenz-Grösse. Der
    # frühere ``median_is_sortino`` (Fold-/Symbol-Median) bleibt rein forensisch. Beide Aggregations-
    # Basen mitgeben und die IS↔OOS-Kohärenz fail-loud prüfen.
    is_sortino_pooled = is_metrics.get("sortino_ratio")
    is_basis = is_metrics.get("sortino_aggregation_basis")
    oos_basis = oos_metrics.get("sortino_aggregation_basis")
    _assert_is_oos_sortino_coherence(is_basis, is_sortino_pooled, oos_basis, oos_metrics.get("sortino_ratio"))

    return {
        "strategy": best.get("strategy"),
        "oos_evaluated": bool(oos_eval.get("oos_evaluated", False)),
        "oos_eligible": bool(oos_eval.get("oos_eligible", False)),
        "oos_rejection_reasons": oos_eval.get("oos_rejection_reasons", []),
        # Issue #554 — numerische Gate-Deltas mit durchreichen (maschinenlesbare Forensik).
        "oos_gate_deltas": oos_eval.get("oos_gate_deltas", {}),
        # Issue #562 — effektive (kostenrelative) Expectancy-Schwelle ins Study-Output heben.
        "effective_expectancy_gate": oos_eval.get("effective_expectancy_gate"),
        "oos_metrics": oos_metrics,
        "oos_fold_sortinos": oos_metrics.get("oos_fold_sortinos") or [],
        # Issue #665 — annualisierungs-invariante Parallelgrösse (siehe
        # collect_oos_fold_sortino_periods-Docstring); kanonisch für fold-übergreifende Aggregation.
        "oos_fold_sortino_periods": oos_metrics.get("oos_fold_sortino_periods") or [],
        "median_is_sortino": is_metrics.get("sortino_ratio"),
        # Issue #613 — kohärenter, gepoolter IS-Sortino + Aggregations-Basis für die Divergenz-Strafe.
        "median_is_sortino_pooled": is_sortino_pooled,
        "is_sortino_aggregation_basis": is_basis,
        "win_count": 1 if oos_eval.get("oos_eligible") else 0,
    }


def _prune_result_for_tournament(r: dict) -> dict:
    """Issue #852 — entfernt riesige Zeitreihen-/Equity-Kurven-Arrays aus tournament.json,
    sodass der RAM-Speicherbedarf von 477 MB auf < 15 MB sinkt."""
    pruned = dict(r)
    for heavy_key in ("trades", "equity_curve", "fill_matches", "raw_returns", "tick_data", "per_bar_diagnostics"):
        pruned.pop(heavy_key, None)
    if "metrics" in pruned and isinstance(pruned["metrics"], dict):
        pm = dict(pruned["metrics"])
        for heavy_key in ("trades", "equity_curve", "fill_matches", "raw_returns"):
            pm.pop(heavy_key, None)
        pruned["metrics"] = pm
    if "oos_metrics" in pruned and isinstance(pruned["oos_metrics"], dict):
        po = dict(pruned["oos_metrics"])
        for heavy_key in ("trades", "equity_curve", "fill_matches", "raw_returns"):
            po.pop(heavy_key, None)
        pruned["oos_metrics"] = po
    return pruned


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
    """Schreibt Tournament-Ergebnisse als JSON."""
    if tournament_cfg is None:
        tournament_cfg = load_tournament_config()

    oos_not_evaluable_pairs = 0
    oos_failed_pairs = 0

    for r in all_results:
        oos_eval = r.get("_oos_eval")
        if oos_eval is not None:
            if not oos_eval.get("oos_evaluated", False) and not oos_eval.get("oos_eligible", False):
                oos_not_evaluable_pairs += 1
            elif oos_eval.get("oos_evaluated", False) and not oos_eval.get("oos_eligible", False):
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
        "full_results":                [_prune_result_for_tournament(r) for r in all_results],
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
    spread_bps_by_symbol: dict | None = None,
    atr_floor_bps_by_asset_class: dict | None = None,
    opening_range_session_open_hour_by_asset_class: dict | None = None,
    # Issue #1096 (Katalog #929) Fix Punkt 1 — tournament.json['min_stop_to_cost_ratio'] (Default
    # 3.0, derselbe Wert wie invariants.check_stop_cost_ratio), vom Orchestrator (run_backtest())
    # EINMAL geladen und an jeden isolierten Worker-Prozess durchgereicht (dieselbe Konvention wie
    # commission_bps/atr_floor_bps_by_asset_class oben — der Worker laedt tournament.json nicht
    # selbst neu).
    min_stop_to_cost_ratio: float = 3.0,
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

            # Determine spread for the symbol. Issue #566 — Auflösungsreihenfolge (resolve_spread_bps,
            # Single Source of Truth): Symbol-Override → Asset-Class → DEFAULT. Die Asset-Class wird
            # nur dann per instrument_map.json aufgelöst, wenn KEIN Symbol-Override greift (kein
            # unnötiges I/O). Ein symbol-spezifischer Override (z. B. TSLA.ETORO=2.0) übersteuert die
            # grobe Asset-Class-Konstante, damit ein zu weiter EQUITY-Spread liquide Blue-Chips nicht
            # fälschlich unrentabel macht.
            has_symbol_override = bool(spread_bps_by_symbol and inst_id_str in spread_bps_by_symbol)
            asset_class_key = "DEFAULT"
            # Issue #924/#922 — die Asset-Class wird auch dann aufgelöst, wenn NUR
            # atr_floor_bps_by_asset_class/opening_range_session_open_hour_by_asset_class
            # konfiguriert ist (ein Spread-Symbol-Override allein entbindet diese Auflösungen
            # nicht — dafür gibt es keinen Symbol-Override).
            if (spread_bps_by_asset_class or atr_floor_bps_by_asset_class
                    or opening_range_session_open_hour_by_asset_class) and not has_symbol_override:
                asset_class_key = _resolve_asset_class_for_symbol(
                    inst_id_str, policy=_read_unknown_asset_class_policy())

            # Issue #956 (Katalog D, P0) — physikalische Tick-Untergrenze VOR der Konfig-Auflösung
            # berechnen: ein $2-Micro-Cap mit $0.01-Tick hat eine Round-Trip-Untergrenze von 50bps,
            # weit über der konfigurierten EQUITY-Konstante (3.0bps) — der Backtest darf nie
            # GÜNSTIGER simulieren, als physikalisch möglich ist. Fail-open: fehlt price_precision
            # ODER die Preis-Stichprobe (kein Katalog/keine Ticks/jeder Lesefehler), bleibt
            # tick_floor_bps=0.0 (bit-identisches Pre-#956-Verhalten).
            _tick_floor_bps = 0.0
            _price_precision = _resolve_price_precision_for_symbol(inst_id_str)
            if _price_precision is not None:
                _median_price_sample = _quick_median_price_from_catalog(
                    effective_catalog_path, inst_id_str)
                _tick_floor_bps = tick_floor_spread_bps(
                    _median_price_sample, 10.0 ** -_price_precision)

            spread_bps = resolve_spread_bps(
                inst_id_str, spread_bps_by_asset_class, spread_bps_by_symbol, asset_class_key,
                tick_floor_bps=_tick_floor_bps)
            atr_floor_bps_resolved = resolve_atr_floor_bps(
                inst_id_str, atr_floor_bps_by_asset_class, asset_class_key)
            # Issue #1096 (Katalog #929) Fix Punkt 1 — siehe cost_coupled_atr_floor_bps-Docstring.
            atr_floor_bps_resolved = cost_coupled_atr_floor_bps(
                atr_floor_bps_resolved,
                atr_trailing_multiplier=(strat.get("params") or {}).get("atr_trailing_multiplier"),
                round_trip_cost_bps=float(spread_bps) + float(commission_bps),
                min_stop_to_cost_ratio=float(min_stop_to_cost_ratio))
            opening_range_session_open_hour_resolved = resolve_opening_range_session_open_hour(
                inst_id_str, opening_range_session_open_hour_by_asset_class, asset_class_key)

            if spread_bps > 0.0:
                src = "Symbol-Override" if has_symbol_override else f"Asset-Class {asset_class_key}"
                wlog(f"   📊 Spread-Modeling ({src}): {spread_bps} bps applied to {inst_id_str}")

            # Issue #898 Fix 4 — COST_MODEL_RESOLVED-Telemetrie, unabhängig vom Prüfergebnis (ein
            # bestandener Pfad ohne Zahlen ist keine Evidenz, siehe #900-Docstring-Analogie).
            import logging as _logging_cost_model
            from automation.log_manager import emit_execution_event as _emit_cost_model_event
            # Issue #956 — "source" wird 'tick_floor', wenn die physikalische Untergrenze die
            # Config-Konstante tatsaechlich uebersteuert hat (Faktor sichtbar via tick_floor_bps),
            # sonst bleibt die bisherige Quelle (symbol_override/asset_class) unveraendert.
            _cost_source = "symbol_override" if has_symbol_override else "asset_class"
            if _tick_floor_bps > 0.0 and spread_bps <= _tick_floor_bps + 1e-9:
                _cost_source = "tick_floor"
            _emit_cost_model_event(_logging_cost_model.getLogger("backtest_worker"), "COST_MODEL_RESOLVED", {
                "symbol": inst_id_str,
                "asset_class_key": asset_class_key,
                "spread_bps": spread_bps,
                "source": _cost_source,
                "tick_floor_bps": round(_tick_floor_bps, 4),
                "atr_floor_bps": atr_floor_bps_resolved,
            })

            ticks = load_ticks_from_catalog(catalog, inst_id_str, start_ns, end_ns, spread_bps)
        except InstrumentMetadataIncompleteError as e:
            wlog_err(f"REJECT_INSTRUMENT_METADATA_INCOMPLETE: {e}", exc=False)
            res = _empty_result(inst_id_str, strategy_class_name, strat)
            res["error"] = "instrument_metadata_incomplete"
            return res
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
            # Issue #924 — asset-class-aufgelöster ATR-Floor (oben resolve_atr_floor_bps), NIE
            # ein vom Suchraum gesampelter Wert (atr_floor_bps ist kein Optuna-Parameter) —
            # überschreibt daher bewusst jeden gleichnamigen Eintrag aus strat["params"].
            params["atr_floor_bps"] = atr_floor_bps_resolved
            # Issue #922 — asset-class-aufgelöste Session-Öffnungsstunde (oben
            # resolve_opening_range_session_open_hour). Nur OpeningRangeBreakoutConfig kennt
            # dieses Feld — der valid_keys-Filter unten verwirft es folgenlos für jede andere
            # Strategie.
            params["opening_range_session_open_hour"] = opening_range_session_open_hour_resolved

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
            # Issue #1081 — voller Round-Trip-Kostensatz (Spread + Kommission, dieselbe #561-Formel
            # wie round_trip_cost_bps unten) an extract_metrics durchgereicht, damit der
            # Kostenstress-Block (#1042) die REALEN Kosten stresst, nicht nur die Kommission.
            _round_trip_cost_bps_for_extract = (
                float(spread_bps) + float(commission_bps)
                if spread_bps is not None and commission_bps is not None else None)
            extracted_data = extract_metrics(engine, start_capital, log_fn=wlog, walk_forward_dict=walk_forward_dict, start_ns=start_ns, commission_bps=commission_bps, mtm_series=mtm_monitor.get_equity_series(), benchmark_series=mtm_monitor.get_benchmark_series(), family_median_n_periods=strat.get("_family_median_n_periods"), round_trip_cost_bps=_round_trip_cost_bps_for_extract, symbol=inst_id_str)
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

        # Issue #562 — Round-Trip-Kosten (bps) als Single Source of Truth in die Metriken stempeln,
        # GENAU dort, wo Spread (spread_bps) und Kommission (commission_bps) real angewandt werden.
        # Das kostenrelative Expectancy-Gate (_evaluate_oos_eligibility) liest diese Zahl und leitet
        # oos_min_expectancy = k_alpha · c_rt daraus ab — kein doppelt gepflegter Kostenwert. Nach
        # #561 gilt c_rt = spread_bps + commission_bps (Kommission 1×/Round-Trip).
        if spread_bps is not None and commission_bps is not None:
            round_trip_cost_bps = float(spread_bps) + float(commission_bps)
        else:
            # Fallback to None if not explicitly provided, to ensure downstream processes properly fallback to static config
            round_trip_cost_bps = None

        # Only initialize to {} if they are explicitly None or not a dict. Note that NULL from extraction failure is a valid dict.
        # But if they are missing or None, we force initialize to {} to ensure downstream components have valid dictionaries.
        if metrics is None or not isinstance(metrics, dict):
            metrics = {}
        if oos_metrics is None or not isinstance(oos_metrics, dict):
            oos_metrics = {}

        # Issue #859 Fix Punkt 4 — ein terminal unrecoverabler Markt-Close (>= exit_close_max_retries
        # abgelehnte/verweigerte Versuche, siehe HourlyStrategyBase._handle_exit_close_order_failure)
        # markiert den Trial als ungueltig statt eine offene Position still durch das gesamte
        # Fenster zu tragen — derselbe strukturierte Rueckkanal (oos_metrics['inference_diagnostics'])
        # wie COHERENCE_INVARIANT_VIOLATION oben. ``strategy`` ist die soeben mit engine.run()
        # ausgefuehrte Instanz (noch in Scope, kein zusaetzlicher Cache-Zugriff noetig).
        if getattr(strategy, "_exit_close_unrecoverable", False):
            oos_metrics["oos_evaluated"] = False
            oos_metrics.setdefault("inference_diagnostics", []).append({
                "code": "EXIT_CLOSE_UNRECOVERABLE",
                "detail": f"{getattr(strategy, '_exit_close_retries', 0)} vergebliche "
                         "Markt-Close-Versuche (rejected/denied) -- Trial als ungueltig markiert "
                         "statt einer still durchgehaltenen offenen Position.",
                "value": getattr(strategy, "_exit_close_retries", None),
            })

        if round_trip_cost_bps is not None:
            metrics["round_trip_cost_bps"] = round_trip_cost_bps
            oos_metrics["round_trip_cost_bps"] = round_trip_cost_bps

        # Issue #508 — Fill-Match-Diagnostik (Sekundärebene) nach oben reichen. `metrics`/`oos_metrics`
        # sind bereits die primären Round-Trip-Metriken; `fill_matches` bleibt reine Execution-Diagnostik.
        fill_matches = extracted_data.get("fill_matches") if isinstance(extracted_data, dict) else None

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
        result: dict[str, Any] = {
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
        # Issue #508 — Fill-Match-Diagnostik (Sekundärebene) nur beilegen, wenn vorhanden;
        # `metrics`/`oos_metrics` bleiben die primären Round-Trip-Gate-Metriken.
        if fill_matches is not None:
            result["fill_matches"] = fill_matches
        return result
    finally:
        if temp_catalog_dir and os.path.exists(temp_catalog_dir):
            import shutil
            shutil.rmtree(temp_catalog_dir)
        import gc
        gc.collect()


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
    # Issue #566 — optionale symbol-spezifische Spread-Overrides (übersteuert die Asset-Class-Konstante).
    spread_bps_by_symbol = backtest_global_cfg.get("spread_bps_by_symbol", {})
    # Issue #924 — asset-class-aufgelöste ATR-Trailing-Stop-Untergrenze (resolve_atr_floor_bps).
    atr_floor_bps_by_asset_class = backtest_global_cfg.get("atr_floor_bps_by_asset_class", {})
    # Issue #922 — asset-class-aufgelöste Session-Öffnungsstunde für OpeningRangeBreakoutStrategy
    # (resolve_opening_range_session_open_hour).
    opening_range_session_open_hour_by_asset_class = backtest_global_cfg.get(
        "opening_range_session_open_hour_by_asset_class", {})
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
    _env_max_workers = os.getenv("BACKTEST_MAX_WORKERS")
    if _env_max_workers:
        _max_workers = max(1, int(_env_max_workers))
    else:
        _max_workers = max(1, min((os.cpu_count() or 1) // 4, 3))
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
                # Issue #913 — Injektionspfad: der Elternprozess (run_optimization.py) berechnet
                # den Familien-Median von oos_n_periods über bereits abgeschlossene Sibling-Trials
                # und reicht ihn über das self-describing Manifest durch (global_settings). None,
                # solange die Familie die konfigurierte Mindestzahl an Siblings noch nicht erreicht
                # hat (Kaltstart, siehe _read_sortino_numeric_guard_reference_bootstrap) ODER der
                # Referenz-Modus 'absolute' ist (kein Injektionsbedarf).
                strat["_family_median_n_periods"] = global_settings.get("family_median_n_periods")

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
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class,
                        spread_bps_by_symbol,
                        atr_floor_bps_by_asset_class=atr_floor_bps_by_asset_class,
                        opening_range_session_open_hour_by_asset_class=(
                            opening_range_session_open_hour_by_asset_class),
                        min_stop_to_cost_ratio=float(
                            tournament_cfg.get("min_stop_to_cost_ratio", 3.0)),
                    )
                    futures[future] = (inst_id_str, strat["strategy_class"], wlf)
                else:
                    result = run_single_backtest_worker(
                        inst_id_str, bar_type, strat,
                        catalog_path, start_ns, end_ns,
                        start_capital, args.htmlreport, reports_dir, wlf,
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class,
                        spread_bps_by_symbol,
                        atr_floor_bps_by_asset_class=atr_floor_bps_by_asset_class,
                        opening_range_session_open_hour_by_asset_class=(
                            opening_range_session_open_hour_by_asset_class),
                        min_stop_to_cost_ratio=float(
                            tournament_cfg.get("min_stop_to_cost_ratio", 3.0)),
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
                        span_tolerance_days, commission_bps, spread_bps_by_asset_class,
                        spread_bps_by_symbol,
                        atr_floor_bps_by_asset_class=atr_floor_bps_by_asset_class,
                        opening_range_session_open_hour_by_asset_class=(
                            opening_range_session_open_hour_by_asset_class),
                        min_stop_to_cost_ratio=float(
                            tournament_cfg.get("min_stop_to_cost_ratio", 3.0)),
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
    spread_bps_by_symbol: dict | None = None,
    atr_floor_bps_by_asset_class: dict | None = None,
    opening_range_session_open_hour_by_asset_class: dict | None = None,
    # Issue #1096 (Katalog #929) Fix Punkt 1 — siehe run_single_backtest_worker-Docstring.
    min_stop_to_cost_ratio: float = 3.0,
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
            span_tolerance_days, commission_bps, spread_bps_by_asset_class,
            spread_bps_by_symbol,
            atr_floor_bps_by_asset_class=atr_floor_bps_by_asset_class,
            opening_range_session_open_hour_by_asset_class=(
                opening_range_session_open_hour_by_asset_class),
            min_stop_to_cost_ratio=min_stop_to_cost_ratio,
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
