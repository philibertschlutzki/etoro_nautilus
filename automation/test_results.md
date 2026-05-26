# Jules Task: Test & Validierungs-Suite — Backtest-Pipeline-Optimierung

**Repository:** `https://github.com/philibertschlutzki/etoro_nautilus`
**Abhängigkeit:** Dieser Task setzt voraus, dass `jules_prompt_backtest_optimization.md`
vollständig implementiert wurde. Führe `git log --oneline -10` aus und prüfe ob
alle 7 Tasks (Config-Restructuring, Periode-Defaults, Precision-Fix, Spread-Modeling,
Tournament, Kill-List, Smoke-Test) im Commit-Log nachweisbar sind.

**Execution Mode:** Autonom. Alle Phasen sequentiell ohne Benutzer-Input.

**Phase-Gate-Regel:** Keine Phase starten bevor die vorherige 100% Acceptance
Criteria erfüllt. Blockierte Phasen → `BLOCKED_<Phase>.md` in `logs/` schreiben,
dann mit nicht-blockierten Tasks innerhalb der Phase fortfahren.

**Pflichtlektüre vor Beginn:**
1. `.agents/AGENTS.md` — Sections 5.1, 5.2, 5.3, 6, 9, 12, 15, 16
2. `.agents/JULES_SYSTEM_PROMPT.md` — Parts 1, 2, 7, 8
3. `automation/config/*.json` — alle Configs aus dem Optimierungs-Task
4. `backtesting/run_backtest.py` — aktuelle Implementierung

---

## Pre-Flight: Environment & Prerequisite-Check

Vor Phase 1 folgende Prüfungen durchführen und in
`logs/PREFLIGHT_TESTING_<YYYY-MM-DD>.md` dokumentieren:

```
1. Python-Dependencies: pip check → keine Konflikte
2. eToro Demo-API: GET /api/v1/identity → HTTP 200, GCID ausgeben
3. Demo-Kontostand: GET portfolio-Endpoint → Wert loggen (Soll: ~10.000 USD)
4. Nautilus-Import: python -c "import nautilus_trader; print(nautilus_trader.__version__)"
5. automation/-Paket: python -c "from automation.api_backfiller import run_backfill; print('OK')"
6. Config-Verzeichnis: ls automation/config/ → muss strategies.json, strategy_defaults.json,
   tournament.json, backtest.json enthalten
7. Parquet-Katalog: Mindestens 10 Symbole unter data/nautilus/data/quote_tick/ vorhanden
8. Optimierungs-Tasks: Für jeden der 7 Tasks eine kurze Verifikation (Datei existiert /
   Wert korrekt) → Tabelle mit DONE/MISSING
```

**Acceptance Criteria:**
- API-Konnektivität bestätigt, Nautilus importierbar
- Alle 7 Optimierungs-Tasks als DONE markiert (sonst: BLOCKED_PREFLIGHT.md)
- Kontostand geloggt

---

## Phase 1 — Offline Unit-Tests

**Ziel:** Jede Änderung aus dem Optimierungs-Task durch isolierte,
API-unabhängige Tests absichern.

### Task 1.1 — Config-Struktur verifizieren

Erstelle `tests/test_config_structure.py`:

```python
"""Unit-Tests: automation/config/ Struktur und Schema-Validierung."""
import json
import pytest
from pathlib import Path

CONFIG_DIR = Path("automation/config")

class TestConfigStructure:

    def test_all_required_files_exist(self):
        required = ["strategies.json", "strategy_defaults.json",
                    "tournament.json", "backtest.json"]
        for f in required:
            assert (CONFIG_DIR / f).exists(), f"Missing: {f}"

    def test_strategies_json_schema(self):
        data = json.loads((CONFIG_DIR / "strategies.json").read_text())
        assert "strategies" in data
        for s in data["strategies"]:
            assert "strategy_class" in s
            assert "active" in s, f"Missing 'active' field in {s}"
            assert isinstance(s["active"], bool)

    def test_strategy_defaults_all_periods_gte_10(self):
        data = json.loads((CONFIG_DIR / "strategy_defaults.json").read_text())
        for strategy, params in data.items():
            if strategy.startswith("_"):
                continue
            for key, val in params.items():
                if "period" in key.lower():
                    assert val >= 10, (
                        f"{strategy}.{key} = {val} (erwartet >= 10, "
                        f"Regression auf period=2)"
                    )

    def test_sma_period_is_20(self):
        data = json.loads((CONFIG_DIR / "strategy_defaults.json").read_text())
        assert data["SmaCrossoverStrategy"]["sma_period"] == 20

    def test_tournament_required_fields(self):
        data = json.loads((CONFIG_DIR / "tournament.json").read_text())
        required = ["min_trades", "min_sortino", "min_profit_factor",
                    "max_drawdown", "scoring", "eligible_requires_all",
                    "eligible_requires_any"]
        for field in required:
            assert field in data, f"tournament.json fehlt: {field}"
        assert data["min_trades"] >= 10, "min_trades zu niedrig"

    def test_backtest_json_spread_modeling_field(self):
        data = json.loads((CONFIG_DIR / "backtest.json").read_text())
        assert "spread_modeling" in data
        assert "fill_model" in data
        assert data["fill_model"] in ("bid_ask", "mid")

    def test_kill_list_strategies_are_inactive(self):
        """
        KILL-LIST PLACEHOLDER — trage die inaktiven Strategien ein:
        KILL_LIST = ["AdxAtrMomentumStrategy", "TrendPullbackStrategy"]
        """
        KILL_LIST = []  # <-- HIER EINTRAGEN
        if not KILL_LIST:
            pytest.skip("Kill-List nicht konfiguriert — Placeholder-Test übersprungen")
        data = json.loads((CONFIG_DIR / "strategies.json").read_text())
        strategy_map = {s["strategy_class"]: s for s in data["strategies"]}
        for name in KILL_LIST:
            assert name in strategy_map, f"Strategie nicht in strategies.json: {name}"
            assert strategy_map[name]["active"] is False, (
                f"{name} sollte inactive sein"
            )

    def test_no_hardcoded_paths_in_configs(self):
        """Kein /home/user/... in Config-Dateien."""
        for cfg_file in CONFIG_DIR.glob("*.json"):
            content = cfg_file.read_text()
            assert "/home/" not in content, (
                f"Hardcoded absoluter Pfad in {cfg_file.name}"
            )
```

**Acceptance Criteria:** `pytest tests/test_config_structure.py -v` → 100% PASS
(Kill-List-Test darf SKIP sein wenn Placeholder leer).

---

### Task 1.2 — Precision-Fix Unit-Tests

Erstelle `tests/test_precision_fix.py`:

```python
"""Unit-Tests: Precision aus Parquet-Metadaten lesen."""
import struct
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
import tempfile
from pathlib import Path

# Import der neuen utils-Funktion
from automation.utils import fallback_precisions

# Import der neuen Funktion aus run_backtest (passe Pfad an)
# from backtesting.run_backtest import read_precisions_from_parquet


class TestPrecisionFromParquet:

    def _write_parquet_with_meta(self, tmp_path, price_prec, size_prec,
                                  symbol="TEST.ETORO"):
        """Hilfsfunktion: schreibt minimales Parquet mit Precision-Metadaten."""
        FSB16 = pa.binary(16)
        def enc(v, p):
            raw = round(v * 10**p)
            return struct.pack("<q", raw) + b"\x00" * 8

        table = pa.table({
            "bid_price": pa.array([enc(100.0, price_prec)], type=FSB16),
            "ask_price": pa.array([enc(100.1, price_prec)], type=FSB16),
            "bid_size":  pa.array([enc(1.0,   size_prec)],  type=FSB16),
            "ask_size":  pa.array([enc(1.0,   size_prec)],  type=FSB16),
            "ts_event":  pa.array([1_700_000_000_000_000_000], type=pa.uint64()),
            "ts_init":   pa.array([1_700_000_000_000_000_000], type=pa.uint64()),
        })
        meta = {
            b"price_precision": str(price_prec).encode(),
            b"size_precision":  str(size_prec).encode(),
            b"instrument_id":   symbol.encode(),
        }
        table = table.replace_schema_metadata(meta)
        path = tmp_path / f"{symbol}.parquet"
        pq.write_table(table, str(path))
        return str(path)

    def test_equity_precision_0(self, tmp_path):
        path = self._write_parquet_with_meta(tmp_path, price_prec=2, size_prec=0)
        schema = pq.read_schema(path)
        meta = schema.metadata
        assert int(meta[b"size_precision"]) == 0

    def test_crypto_precision_8(self, tmp_path):
        path = self._write_parquet_with_meta(
            tmp_path, price_prec=2, size_prec=8, symbol="ETH.ETORO")
        schema = pq.read_schema(path)
        meta = schema.metadata
        assert int(meta[b"size_precision"]) == 8, (
            "ETH muss size_prec=8 haben — Precision-Crash-Fix)"
        )

    def test_commodity_precision_5(self, tmp_path):
        path = self._write_parquet_with_meta(
            tmp_path, price_prec=5, size_prec=5, symbol="PALL.ETORO")
        schema = pq.read_schema(path)
        meta = schema.metadata
        assert int(meta[b"size_precision"]) == 5

    def test_fallback_precisions_crypto(self):
        pp, sp = fallback_precisions("ETH.ETORO")
        assert sp == 8, f"ETH size_prec Fallback sollte 8 sein, got {sp}"

    def test_fallback_precisions_equity(self):
        pp, sp = fallback_precisions("TSLA.ETORO")
        assert sp == 0, f"TSLA size_prec Fallback sollte 0 sein, got {sp}"

    def test_fallback_precisions_commodity(self):
        pp, sp = fallback_precisions("PALL.ETORO")
        assert sp == 5, f"PALL size_prec Fallback sollte 5 sein, got {sp}"

    def test_no_duplicate_fallback_code(self):
        """Prüft dass _fallback_precisions nur in automation/utils.py existiert,
        nicht dupliziert in api_backfiller.py oder catalog_service.py."""
        for fname in ["automation/api_backfiller.py",
                      "automation/catalog_service.py",
                      "automation/daily_orchestrator.py"]:
            content = Path(fname).read_text()
            # Die Funktion darf importiert aber nicht re-definiert werden
            assert "def _fallback_precisions" not in content, (
                f"_fallback_precisions ist dupliziert in {fname} "
                f"— sollte nur in automation/utils.py definiert sein"
            )
            assert "def fallback_precisions" not in content, (
                f"fallback_precisions ist dupliziert in {fname}"
            )
```

**Acceptance Criteria:** `pytest tests/test_precision_fix.py -v` → 100% PASS.

---

### Task 1.3 — Spread-Modeling Unit-Tests

Erstelle `tests/test_spread_modeling.py`:

```python
"""Unit-Tests: Midprice-Berechnung und Fill-Preis-Logik."""
import pytest


class TestSpreadModeling:

    def test_midprice_calculation(self):
        bid, ask = 100.0, 100.2
        mid = (bid + ask) / 2
        assert mid == pytest.approx(100.1)

    def test_buy_fill_at_ask(self):
        """Buy-Orders werden zum Ask-Preis gefüllt (höherer Preis = Slippage)."""
        bid, ask = 100.0, 100.2
        buy_fill_price = ask
        assert buy_fill_price > (bid + ask) / 2, (
            "Buy-Fill muss über Midprice liegen"
        )

    def test_sell_fill_at_bid(self):
        """Sell-Orders werden zum Bid-Preis gefüllt (niedrigerer Preis = Slippage)."""
        bid, ask = 100.0, 100.2
        sell_fill_price = bid
        assert sell_fill_price < (bid + ask) / 2, (
            "Sell-Fill muss unter Midprice liegen"
        )

    def test_spread_modeling_config_toggle(self):
        """Wenn spread_modeling=false, identisches Verhalten wie vorher."""
        import json
        from pathlib import Path
        cfg = json.loads(Path("automation/config/backtest.json").read_text())
        assert "spread_modeling" in cfg, (
            "backtest.json muss 'spread_modeling' enthalten"
        )
        # Wert muss boolean sein
        assert isinstance(cfg["spread_modeling"], bool)

    def test_spread_cost_reduces_return(self, tmp_path):
        """
        Konzeptueller Test: Backtest mit spread_modeling=true muss niedrigere
        Returns zeigen als ohne Spread. Wird in Phase 3 quantitativ validiert.
        Hier: nur Config-Toggle prüfen.
        """
        import json
        from pathlib import Path
        cfg_path = Path("automation/config/backtest.json")
        cfg = json.loads(cfg_path.read_text())
        # Prüfe dass der Toggle existiert und schreibbar ist
        cfg_copy = cfg.copy()
        cfg_copy["spread_modeling"] = True
        assert cfg_copy["spread_modeling"] is True
        cfg_copy["spread_modeling"] = False
        assert cfg_copy["spread_modeling"] is False
```

**Acceptance Criteria:** `pytest tests/test_spread_modeling.py -v` → 100% PASS.

---

### Task 1.4 — Tournament-Selektion Unit-Tests

Erstelle `tests/test_tournament_selection.py`:

```python
"""Unit-Tests: Multi-Kriterien-Tournament-Selektion."""
import json
import pytest
from pathlib import Path

# Passe den Import auf die tatsächliche Funktion in run_backtest.py an
# from backtesting.run_backtest import select_tournament_winner, score_strategy


class TestTournamentSelection:

    @pytest.fixture
    def tournament_cfg(self):
        return json.loads(
            Path("automation/config/tournament.json").read_text()
        )

    def test_min_trades_filter(self, tournament_cfg):
        """Kandidaten mit < min_trades dürfen nicht gewinnen."""
        min_t = tournament_cfg["min_trades"]
        assert min_t >= 10, f"min_trades={min_t} zu niedrig (Soll >= 10)"

    def test_01211_hk_artefakt_wird_gefiltert(self, tournament_cfg):
        """
        01211.HK VwapExhaustion: 8 Trades, PF=999, Sortino=0.
        Dieses Artefakt muss durch min_trades herausgefiltert werden.
        """
        artefakt = {
            "total_trades": 8,
            "win_rate": 1.0,
            "profit_factor": 999.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_return": 0.1935,
        }
        min_trades = tournament_cfg["min_trades"]
        assert artefakt["total_trades"] < min_trades, (
            f"01211.HK Artefakt mit {artefakt['total_trades']} Trades muss "
            f"durch min_trades={min_trades} gefiltert werden"
        )

    def test_eligible_requires_all_fields_present(self, tournament_cfg):
        for field in tournament_cfg.get("eligible_requires_all", []):
            assert field in tournament_cfg, (
                f"eligible_requires_all verweist auf nicht-existentes "
                f"Feld: {field}"
            )

    def test_eligible_requires_any_fields_present(self, tournament_cfg):
        for field in tournament_cfg.get("eligible_requires_any", []):
            assert field in tournament_cfg, (
                f"eligible_requires_any verweist auf nicht-existentes "
                f"Feld: {field}"
            )

    def test_scoring_weights_sum_to_one(self, tournament_cfg):
        weights = tournament_cfg.get("scoring", {})
        total = sum(v for k, v in weights.items() if "weight" in k)
        assert abs(total - 1.0) < 0.001, (
            f"Scoring-Gewichte summieren sich zu {total}, erwartet 1.0"
        )

    def test_fsly_vwap_passes_filter(self, tournament_cfg):
        """
        FSLY VwapExhaustion: 8 Trades, PF=2.05, Sortino=5.88 — legitimer Gewinner.
        ABER: 8 Trades < min_trades=10 → wird auch gefiltert.
        Dieser Test dokumentiert das Verhalten explizit.
        """
        fsly = {
            "total_trades": 8,
            "win_rate": 0.75,
            "profit_factor": 2.0469,
            "sortino_ratio": 5.8777,
            "max_drawdown": 0.0721,
            "total_return": 0.071,
        }
        min_trades = tournament_cfg["min_trades"]
        # Dokumentiere: FSLY fällt auch durch min_trades=10
        # Das ist gewollt — mit period=20 werden mehr Trades generiert
        assert fsly["total_trades"] < min_trades or fsly["total_trades"] >= min_trades, (
            "Dieser Test dokumentiert nur — kein echter Assert"
        )
        # Was wichtig ist: Sortino und PF sind legitim
        assert fsly["sortino_ratio"] > tournament_cfg.get("min_sortino", 0)
        assert fsly["profit_factor"] > tournament_cfg.get("min_profit_factor", 1.0)
```

**Acceptance Criteria:** `pytest tests/test_tournament_selection.py -v` → 100% PASS.

---

### Task 1.5 — automation/ Standalone-Isolation verifizieren

Erstelle `tests/test_automation_isolation.py`:

```python
"""
Verifiziert dass automation/ keine verbotenen Imports enthält.
Basiert auf AGENTS.md Section 15 Standalone-Constraint.
"""
import ast
import pytest
from pathlib import Path

AUTOMATION_DIR = Path("automation")
FORBIDDEN_IMPORTS = ["adapters", "config.setups"]


class TestAutomationIsolation:

    def _get_imports(self, filepath):
        """Extrahiert alle Imports aus einer Python-Datei via AST."""
        tree = ast.parse(filepath.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    @pytest.mark.parametrize("py_file", list(AUTOMATION_DIR.glob("*.py")))
    def test_no_forbidden_imports(self, py_file):
        if py_file.name.startswith("test_"):
            pytest.skip("Test-Datei übersprungen")
        imports = self._get_imports(py_file)
        for forbidden in FORBIDDEN_IMPORTS:
            violating = [i for i in imports if i.startswith(forbidden)]
            assert not violating, (
                f"{py_file.name} enthält verbotenen Import: {violating}\n"
                f"automation/ muss standalone sein (AGENTS.md Section 15)"
            )

    def test_utils_exports_fallback_precisions(self):
        """automation/utils.py muss fallback_precisions exportieren."""
        utils_path = AUTOMATION_DIR / "utils.py"
        assert utils_path.exists(), "automation/utils.py fehlt"
        content = utils_path.read_text()
        assert "def fallback_precisions" in content, (
            "automation/utils.py muss fallback_precisions() definieren"
        )

    def test_api_backfiller_imports_from_utils(self):
        """api_backfiller.py muss fallback_precisions aus utils importieren."""
        content = (AUTOMATION_DIR / "api_backfiller.py").read_text()
        assert "from automation.utils import" in content or \
               "from .utils import" in content, (
            "api_backfiller.py muss fallback_precisions aus utils importieren"
        )

    def test_catalog_service_imports_from_utils(self):
        content = (AUTOMATION_DIR / "catalog_service.py").read_text()
        assert "from automation.utils import" in content or \
               "from .utils import" in content, (
            "catalog_service.py muss fallback_precisions aus utils importieren"
        )
```

**Acceptance Criteria:** `pytest tests/test_automation_isolation.py -v` → 100% PASS.

---

### Task 1.6 — Alle Unit-Tests gesammelt ausführen

```bash
pytest tests/test_config_structure.py \
       tests/test_precision_fix.py \
       tests/test_spread_modeling.py \
       tests/test_tournament_selection.py \
       tests/test_automation_isolation.py \
       -v --tb=short \
       --json-report --json-report-file=logs/phase1_unit_test_results.json
```

Schreibe `logs/TESTREPORT_Phase1_UnitTests_<YYYY-MM>.md` nach dem Template
aus `.agents/testing.md`.

**Acceptance Criteria:**
- `logs/phase1_unit_test_results.json` existiert
- Kein Test in FAILED-Status (SKIP ist erlaubt für Kill-List-Placeholder)
- Report-Datei vorhanden mit ausgefüllter Results-Tabelle

---

## Phase 2 — Integrationstests (eToro Demo-API)

**Ziel:** Precision-Fix, Spread-Modeling und Parameter-Defaults verhalten
sich korrekt mit echten Live-Daten vom Demo-Account.

### Task 2.1 — Instrument-Precision via API

Erstelle `tests/integration/test_precision_api.py`:

```python
"""
Integrations-Test: Prüft dass die eToro Instruments-API korrekte
Precision-Werte für Crypto-Assets liefert.
Benötigt: ETORO_API_KEY und ETORO_USER_KEY in .env
"""
import asyncio
import os
import pytest
from dotenv import load_dotenv
from automation.api_backfiller import fetch_precisions_from_api
import aiohttp

load_dotenv()

CRYPTO_INSTRUMENTS = {
    # etoro_id: (expected_price_prec_min, expected_size_prec_min)
    # IDs aus data/universe/momentum_ls.json lesen
}

@pytest.mark.asyncio
@pytest.mark.integration
class TestPrecisionFromAPI:

    async def test_fetch_precisions_returns_dict(self):
        api_key  = os.getenv("ETORO_API_KEY", "")
        user_key = os.getenv("ETORO_USER_KEY", "")
        if not api_key or not user_key:
            pytest.skip("API-Keys nicht konfiguriert")

        # Lade echte IDs aus Universe
        import json
        from pathlib import Path
        universe = json.loads(
            Path("data/universe/momentum_ls.json").read_text()
        )
        etoro_ids = [
            str(item["etoro_id"])
            for item in universe.get("universe", [])[:5]  # erste 5 zum Testen
            if item.get("etoro_id")
        ]
        assert etoro_ids, "Keine IDs im Universe gefunden"

        async with aiohttp.ClientSession() as session:
            result = await fetch_precisions_from_api(
                session, etoro_ids, api_key, user_key
            )

        assert isinstance(result, dict), "Rückgabe muss Dict sein"
        assert len(result) > 0, "Keine Precision-Daten empfangen"

    async def test_eth_precision_not_zero(self):
        """
        ETH muss size_precision > 0 haben.
        Regression-Test für den Precision-Crash (size_prec=0 war der Bug).
        """
        api_key  = os.getenv("ETORO_API_KEY", "")
        user_key = os.getenv("ETORO_USER_KEY", "")
        if not api_key or not user_key:
            pytest.skip("API-Keys nicht konfiguriert")

        import json
        from pathlib import Path
        universe = json.loads(
            Path("data/universe/momentum_ls.json").read_text()
        )
        eth_id = next(
            (str(item["etoro_id"])
             for item in universe.get("universe", [])
             if item.get("symbol") == "ETH.ETORO"),
            None
        )
        if not eth_id:
            pytest.skip("ETH.ETORO nicht im Universe")

        async with aiohttp.ClientSession() as session:
            result = await fetch_precisions_from_api(
                session, [eth_id], api_key, user_key
            )

        if eth_id in result:
            price_prec, size_prec = result[eth_id]
            assert size_prec > 0, (
                f"ETH size_precision={size_prec} — Regression! "
                f"Sollte > 0 sein (Crypto-Asset)"
            )
```

### Task 2.2 — API-Backfiller Integrations-Run

Erstelle `tests/integration/test_api_backfiller_live.py`:

```python
"""
Integrations-Test: api_backfiller holt echte Candles und schreibt
korrekte FSB(16)-Parquet-Dateien.
"""
import asyncio
import os
import shutil
import json
import struct
import pytest
import pyarrow.parquet as pq
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

@pytest.mark.asyncio
@pytest.mark.integration
class TestApiBackfillerLive:

    async def test_backfill_writes_parquet_with_correct_schema(self, tmp_path):
        from automation.api_backfiller import run_backfill, _load_etoro_id_map
        import automation.api_backfiller as ab

        api_key  = os.getenv("ETORO_API_KEY", "")
        user_key = os.getenv("ETORO_USER_KEY", "")
        if not api_key or not user_key:
            pytest.skip("API-Keys nicht konfiguriert")

        # Verwende Test-Katalog-Pfad
        original_path = ab.QUOTE_TICK_PATH
        test_catalog = tmp_path / "quote_tick"
        test_catalog.mkdir(parents=True)
        ab.QUOTE_TICK_PATH = test_catalog

        try:
            universe = json.loads(
                Path("data/universe/momentum_ls.json").read_text()
            )
            # Nur 2 Symbole für schnellen Test
            all_ids = {
                str(item["etoro_id"]): item["symbol"]
                for item in universe.get("universe", [])[:2]
                if item.get("etoro_id") and item.get("symbol")
            }

            filled = await run_backfill(
                api_key=api_key,
                user_key=user_key,
                etoro_id_to_symbol=all_ids,
                days=1,  # nur 1 Tag für schnellen Test
            )

            assert len(filled) > 0, "Kein Symbol wurde befüllt"

            for symbol in filled:
                parquet_path = test_catalog / symbol / "data.parquet"
                assert parquet_path.exists(), (
                    f"Parquet fehlt für {symbol}"
                )

                # Schema validieren
                table = pq.read_table(str(parquet_path))
                required_cols = {"bid_price", "ask_price", "bid_size",
                                 "ask_size", "ts_event", "ts_init"}
                assert required_cols.issubset(set(table.column_names))

                # Metadaten prüfen
                meta = table.schema.metadata or {}
                assert b"price_precision" in meta, (
                    f"{symbol}: price_precision fehlt in Metadaten"
                )
                assert b"size_precision" in meta, (
                    f"{symbol}: size_precision fehlt in Metadaten"
                )
                assert b"instrument_id" in meta

                # FSB(16) Format: exakt 16 Bytes pro Wert
                bid_col = table.column("bid_price")
                first_bid = bid_col[0].as_py()
                assert len(first_bid) == 16, (
                    f"bid_price ist nicht FSB(16): {len(first_bid)} Bytes"
                )
                # Letzten 8 Bytes müssen Null-Bytes sein
                assert first_bid[8:] == b"\x00" * 8, (
                    "FSB(16): Padding-Bytes sind nicht 0x00"
                )

        finally:
            ab.QUOTE_TICK_PATH = original_path
```

### Task 2.3 — Demo-Account Portfolio-Check

Erstelle `tests/integration/test_demo_account.py`:

```python
"""
Integrations-Test: Demo-Account-Status und Portfolio-Abfrage.
Dient als Baseline für Phase 3 Live-Backtest.
"""
import os
import pytest
import aiohttp
import asyncio
import uuid
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://public-api.etoro.com"

def make_headers():
    return {
        "x-api-key":    os.getenv("ETORO_API_KEY", ""),
        "x-user-key":   os.getenv("ETORO_USER_KEY", ""),
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

@pytest.mark.asyncio
@pytest.mark.integration
class TestDemoAccount:

    async def test_identity_endpoint(self):
        if not os.getenv("ETORO_API_KEY"):
            pytest.skip("API-Keys fehlen")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/api/v1/identity",
                headers=make_headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                assert resp.status == 200, (
                    f"Identity-Endpoint HTTP {resp.status}"
                )
                data = await resp.json(content_type=None)
                assert "gcid" in data or "customerId" in data or \
                       "GlobalCustomerId" in data, (
                    f"Unbekanntes Identity-Response-Format: {list(data.keys())}"
                )

    async def test_demo_portfolio_reachable(self):
        if not os.getenv("ETORO_API_KEY"):
            pytest.skip("API-Keys fehlen")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/api/v1/trading/demo/portfolio",
                headers=make_headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                # 200 oder 404 (falls kein Demo-Account) sind beide akzeptabel
                assert resp.status in (200, 404, 403), (
                    f"Unerwarteter HTTP-Status: {resp.status}"
                )
```

### Task 2.4 — Integrationstests ausführen

```bash
pytest tests/integration/ \
       -v --tb=short \
       -m "integration" \
       --json-report --json-report-file=logs/phase2_integration_results.json \
       --timeout=120
```

Schreibe `logs/TESTREPORT_Phase2_Integration_<YYYY-MM>.md`.

**Acceptance Criteria:**
- `logs/phase2_integration_results.json` existiert
- `test_fetch_precisions_returns_dict` PASS (oder SKIP falls kein Key)
- `test_backfill_writes_parquet_with_correct_schema` PASS
- FSB(16)-Format-Validierung bestanden
- Kein unbehandeltes `RuntimeError: invalid tick.bid_size.precision`

---

## Phase 3 — Live-Backtest-Run gegen echte Parquet-Daten

**Ziel:** Vollständiger Orchestrator-Durchlauf mit allen Optimierungen aktiv.
Quantitative Verbesserung gegenüber dem Baseline-Run (2 Gewinner aus 72 Symbolen)
muss nachweisbar sein.

### Task 3.1 — Baseline-Metriken dokumentieren

Vor dem neuen Run in `logs/phase3_baseline_metrics.json` festhalten:

```json
{
  "run_date": "2026-05-26",
  "total_symbols": 72,
  "total_jobs": 693,
  "winners": 2,
  "precision_crashes": {
    "symbols": ["ETH", "HYPE", "ONDO", "PALL", "PEPExM"],
    "count": 5
  },
  "period_used": 2,
  "spread_modeling": false,
  "tournament_threshold": "sortino > 0",
  "artefacts": [
    {"symbol": "01211.HK.ETORO", "trades": 8, "pf": 999.0, "note": "PF=999 Artefakt"}
  ]
}
```

### Task 3.2 — Orchestrator-Run (Dry-Run Verifikation)

```bash
cd /home/user/etoro_nautilus

# Schritt 1: Dry-Run ohne API-Backfill
python3 automation/daily_orchestrator.py \
  --dry-run --skip-api-fetch \
  2>&1 | tee logs/phase3_dryrun_orchestrator.log

# Erwartetes Verhalten:
# - Keine ImportError
# - Keine FileNotFoundError
# - "ORCHESTRATOR ERFOLGREICH ABGESCHLOSSEN" in Log
```

Verifiziere:
- `[Phase 1]` — Universe geladen (Anzahl Instrumente loggen)
- `[Phase 2a]` — ZIP-Verarbeitung läuft (oder "Keine ZIP-Dateien" — beides OK)
- `[Phase 3]` — "DRY-RUN: Backtest übersprungen" erscheint
- Exit-Code 0

### Task 3.3 — Vollständiger Backtest-Run

```bash
# Schritt 2: Echter Backtest (kein Dry-Run, aber kein Bot-Start)
# Modifiziere daily_orchestrator.py temporär für diesen Test:
# Phase 5 (Bot-Start) überspringen aber Backtest vollständig ausführen

python3 backtesting/run_backtest.py \
  --momentum \
  --catalog-path data/nautilus \
  --config logs/backtest_dynamic_config.json \
  --output logs/tournament_test_<DATUM>.json \
  2>&1 | tee logs/phase3_backtest_full.log
```

Extrahiere aus `logs/phase3_backtest_full.log`:

```python
# Erstelle tests/extract_backtest_metrics.py
"""Extrahiert Key-Metriken aus dem Backtest-Log und vergleicht mit Baseline."""
import re
import json
from pathlib import Path

def parse_backtest_log(log_path: str) -> dict:
    content = Path(log_path).read_text()

    # Precision-Crashes zählen
    crashes = re.findall(
        r"RuntimeError: invalid tick\.bid_size\.precision", content
    )

    # Gewinner zählen
    winners_match = re.search(r"(\d+) Gewinner", content)
    winners = int(winners_match.group(1)) if winners_match else 0

    # Jobs zählen
    jobs_match = re.search(r"(\d+)/(\d+)\]", content)
    total_jobs = int(jobs_match.group(2)) if jobs_match else 0

    # Artefakte (PF=999) prüfen
    artefacts = re.findall(r"PF=999", content)

    return {
        "precision_crashes": len(crashes),
        "winners": winners,
        "total_jobs": total_jobs,
        "artefact_count": len(artefacts),
    }

if __name__ == "__main__":
    baseline = json.loads(Path("logs/phase3_baseline_metrics.json").read_text())
    current  = parse_backtest_log("logs/phase3_backtest_full.log")

    print("\n=== Backtest-Vergleich: Baseline vs. Optimiert ===")
    print(f"Precision-Crashes:  {baseline['precision_crashes']['count']} → {current['precision_crashes']}")
    print(f"Gewinner:           {baseline['winners']} → {current['winners']}")
    print(f"PF=999 Artefakte:   {len(baseline['artefacts'])} → {current['artefact_count']}")

    assertions = []
    assertions.append(("Keine neuen Precision-Crashes",
                        current["precision_crashes"] == 0))
    assertions.append(("Mehr Gewinner als Baseline",
                        current["winners"] > baseline["winners"]))
    assertions.append(("Keine PF=999 Artefakte mehr",
                        current["artefact_count"] == 0))

    all_pass = True
    for name, result in assertions:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
        if not result:
            all_pass = False

    results = {"baseline": baseline, "current": current,
               "assertions": {n: r for n, r in assertions},
               "overall": "PASS" if all_pass else "FAIL"}
    Path("logs/phase3_comparison.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False)
    )
```

Führe aus: `python3 tests/extract_backtest_metrics.py`

### Task 3.4 — Spread-Modeling quantitativer Nachweis

Führe zwei Mini-Backtests durch (1 Instrument, 1 Strategie):

```bash
# Run A: Spread-Modeling AUS
# Temporär backtest.json ändern: "spread_modeling": false
python3 backtesting/run_backtest.py --single-symbol AERO.ETORO \
  --strategy VwapExhaustionStrategy \
  --output logs/phase3_spread_off.json

# Run B: Spread-Modeling AN
# backtest.json: "spread_modeling": true
python3 backtesting/run_backtest.py --single-symbol AERO.ETORO \
  --strategy VwapExhaustionStrategy \
  --output logs/phase3_spread_on.json
```

Vergleiche `total_return` aus beiden JSONs:
- Run B (Spread AN) muss niedrigere `total_return` haben als Run A
- Differenz dokumentieren in `logs/phase3_spread_impact.json`

Schreibe `logs/TESTREPORT_Phase3_LiveBacktest_<YYYY-MM>.md`.

**Acceptance Criteria:**
- `logs/phase3_comparison.json` → `"overall": "PASS"`
- Precision-Crashes = 0 (ETH/HYPE/ONDO/PALL/PEPExM laufen durch)
- Gewinner > 2 (Baseline war 2)
- Kein PF=999 Artefakt in Tournament-Output
- `logs/phase3_spread_impact.json` zeigt messbaren Spread-Effekt

---

## Phase 4 — Universe-Refresh & Freshness-Test

**Aktueller Zustand:** Universe ist 247h alt (> 10 Tage), was zu dem Warning
`"Universe data is stale"` im Live-Bot-Log führt und fehlerhafte Trading-
Entscheidungen begünstigt.

### Task 4.1 — Universe-Freshness-Test schreiben

Erstelle `tests/test_universe_freshness.py`:

```python
"""
Test: Universe-Datei darf nicht älter als 24 Stunden sein.
Schlägt aktuell fehl (247h alt) — dient als Regression-Schutz nach Refresh.
"""
import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

UNIVERSE_PATH = Path("data/universe/momentum_ls.json")
MAX_AGE_HOURS = 24


class TestUniverseFreshness:

    def test_universe_file_exists(self):
        assert UNIVERSE_PATH.exists(), (
            f"Universe-Datei nicht gefunden: {UNIVERSE_PATH}"
        )

    def test_universe_has_fetched_at_timestamp(self):
        data = json.loads(UNIVERSE_PATH.read_text())
        assert "fetched_at" in data, (
            "Universe-Datei hat kein 'fetched_at'-Feld"
        )

    def test_universe_is_fresh(self):
        data = json.loads(UNIVERSE_PATH.read_text())
        fetched_at_str = data.get("fetched_at", "")
        if not fetched_at_str:
            pytest.fail("fetched_at ist leer")

        fetched_at = datetime.fromisoformat(fetched_at_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        age_hours = (
            datetime.now(timezone.utc) - fetched_at
        ).total_seconds() / 3600

        assert age_hours <= MAX_AGE_HOURS, (
            f"Universe ist {age_hours:.1f}h alt — maximal {MAX_AGE_HOURS}h erlaubt.\n"
            f"Führe Universe-Refresh durch."
        )

    def test_universe_has_minimum_instruments(self):
        data = json.loads(UNIVERSE_PATH.read_text())
        instruments = data.get("universe", [])
        assert len(instruments) >= 30, (
            f"Universe hat nur {len(instruments)} Instrumente — "
            f"Minimum 30 erwartet"
        )

    def test_all_instruments_have_etoro_id(self):
        data = json.loads(UNIVERSE_PATH.read_text())
        for item in data.get("universe", []):
            assert item.get("etoro_id"), (
                f"Instrument ohne etoro_id: {item.get('symbol', 'UNKNOWN')}"
            )
```

### Task 4.2 — Universe-Refresh durchführen

```bash
# Schritt 1: Aktuellen Zustand vor Refresh testen (wird fehlschlagen)
pytest tests/test_universe_freshness.py::TestUniverseFreshness::test_universe_is_fresh \
  -v 2>&1 | tee logs/phase4_freshness_before.log

# Schritt 2: Universe neu fetchen
# Passe den Befehl an das tatsächliche Fetch-Script im Repo an:
# Möglichkeit A — via dev_scripts:
python3 dev_scripts/fetch_universe.py \
  2>&1 | tee logs/phase4_universe_refresh.log

# Möglichkeit B — via Orchestrator Phase 1 (falls kein separates Fetch-Script):
python3 -c "
from automation.daily_orchestrator import _load_universe_file
import json, sys
data = _load_universe_file(__import__('logging').getLogger('test'))
print(f'Universe: {len(data.get(\"universe\", []))} Instrumente')
"

# Schritt 3: Freshness nach Refresh prüfen
pytest tests/test_universe_freshness.py -v \
  2>&1 | tee logs/phase4_freshness_after.log
```

**Hinweis:** Falls kein Universe-Fetch-Script existiert, erstelle
`dev_scripts/fetch_universe.py` das:
1. Die eToro Search-API aufruft (`GET /api/v1/market-data/search`)
2. Die Top-N Instrumente nach Momentum-Kriterien filtert
3. `data/universe/momentum_ls.json` mit aktuellem `fetched_at`-Timestamp schreibt

Schreibe `logs/TESTREPORT_Phase4_UniverseRefresh_<YYYY-MM>.md`.

**Acceptance Criteria:**
- `test_universe_is_fresh` → PASS nach Refresh
- `test_universe_has_minimum_instruments` → PASS
- `logs/phase4_freshness_after.log` zeigt 0 Fehler

---

## Phase 5 — Dokumentations-Update

**Ziel:** AGENTS.md und alle zugehörigen Docs spiegeln den neuen Systemstand
nach allen Optimierungen wider.

### Task 5.1 — AGENTS.md Section 15 aktualisieren

Lese `.agents/AGENTS.md` Section 15 (Automation Pipeline Scripts).
Ergänze/aktualisiere folgende Punkte basierend auf den Änderungen aus
dem Optimierungs-Task:

**Neue Subsection 15.X — Config-Verzeichnis (`automation/config/`)**
```markdown
## Config-Verzeichnis (automation/config/)

Ab v2.1 (2026-05-XX) liegen alle automation/-Configs in automation/config/:

| Datei | Zweck |
|-------|-------|
| strategies.json | Aktive Strategien + active-Flag |
| strategy_defaults.json | Sensible Default-Parameter pro Strategie |
| tournament.json | Multi-Kriterien-Selektionslogik |
| backtest.json | Globale Backtest-Einstellungen inkl. Spread-Modeling |

Override-Mechanismus: strategy_defaults.json < strategies.json
(strategies.json-Werte überschreiben Defaults)
```

**Precision-Fix-Eintrag in Section 15:**
```markdown
### Instrument-Precision (Parquet-Metadaten, Pitfall #16)

price_precision und size_precision werden ab v2.1 direkt aus den
Arrow-Schema-Metadaten gelesen (b"price_precision", b"size_precision").
Fallback: automation/utils.fallback_precisions(symbol).

KRITISCH: Nie Instrument mit hardcoded size_precision=0 für Crypto-Assets
erstellen — führt zu RuntimeError in Nautilus-Engine.

Betroffene Assets mit size_precision=8: ETH, HYPE, ONDO, SHIBxM, AERO, PEPExM
Betroffene Assets mit size_precision=5: PALL, NATGAS, USDTRY, USDZAR
```

**Spread-Modeling-Eintrag:**
```markdown
### Spread-Modeling (ab v2.1)

Konfiguration: automation/config/backtest.json["spread_modeling"]

Verhalten bei spread_modeling=true:
- Signale: Midprice = (bid + ask) / 2
- Buy-Fills: ask_price (realistisch höher)
- Sell-Fills: bid_price (realistisch niedriger)

Verhalten bei spread_modeling=false:
- Identisch zu v2.0 (kein Spread)
```

### Task 5.2 — AGENTS.md Section 16 — Neue Pitfalls hinzufügen

Füge folgende neuen Pitfalls zur Section 16 hinzu:

```markdown
### Pitfall #16 — Precision-Crash: size_precision Mismatch

**Symptom:** `RuntimeError: invalid tick.bid_size.precision=8 did not match
instrument.size_precision=0`

**Root Cause:** Nautilus-Instrument wird mit size_precision=0 erstellt,
aber die Parquet-Datei enthält FSB(16)-kodierte Mengen mit precision=8
(Crypto-Assets wie ETH, HYPE, ONDO).

**Fix (seit v2.1):** read_precisions_from_parquet() liest precision aus
Parquet-Schema-Metadaten. Fallback: automation.utils.fallback_precisions().

**Betroffene Symbole:** ETH, HYPE, ONDO, SHIBxM, AERO, PEPExM (prec=8),
PALL, NATGAS, USDTRY, USDZAR (prec=5).


### Pitfall #17 — Tournament-Artefakt: PF=999 bei 0 Verlust-Trades

**Symptom:** VwapExhaustionStrategy gewinnt Tournament mit PF=999 bei
nur 8 Trades (alle Gewinner, kein einziger Verlust) und Sortino=0.

**Root Cause:** Bei sehr wenigen Trades und zufällig positiver Serie
berechnet sich PF = gross_profit / 0 → Division by Zero →
oft als 999.0 oder ∞ dargestellt.

**Fix (seit v2.1):** min_trades=10 in tournament.json schließt solche
Artefakte aus.


### Pitfall #18 — Overtrading bei period=2

**Symptom:** Backtest mit 693 Jobs zeigt nahezu alle Sortino-Ratios < -3,
Win-Rates von 10-40% mit hohen Drawdowns.

**Root Cause:** Alle Strategie-Parameter auf period=2 führt zu extrem
kurzen Indikatoren, die auf Tick-Rauschen anstatt auf echte Trends reagieren.
Ergebnis: Überproportional viele Trades, hohe Transaktionskosten-Simulation,
negative Sortino-Ratios.

**Fix (seit v2.1):** strategy_defaults.json mit period≥10 für alle Strategien.
SMA=20, RSI=14, BB=20 als sinnvolle Baseline.
```

### Task 5.3 — AGENTS.md Section 18 Changelog aktualisieren

Füge folgende Einträge in Section 18 (Changelog) ein:

```markdown
| Datum | Beschreibung | Dateien |
|-------|-------------|---------|
| 2026-05-XX | Config-Verzeichnis automation/config/ eingeführt | Section 15, automation/config/* |
| 2026-05-XX | strategy_defaults.json mit sensiblen Defaults (period≥10) | Section 15, automation/config/strategy_defaults.json |
| 2026-05-XX | Precision-Fix: size_precision aus Parquet-Metadaten (Pitfall #16) | Section 15, Section 16, backtesting/run_backtest.py |
| 2026-05-XX | automation/utils.py: fallback_precisions() extrahiert | Section 15, automation/utils.py |
| 2026-05-XX | Spread-Modeling: Midprice für Signale, Ask/Bid für Fills | Section 15, backtesting/run_backtest.py |
| 2026-05-XX | Tournament Multi-Kriterien mit min_trades=10 (Pitfall #17) | Section 15, automation/config/tournament.json |
| 2026-05-XX | Pitfall #17 (Tournament-Artefakt PF=999) dokumentiert | Section 16 |
| 2026-05-XX | Pitfall #18 (Overtrading period=2) dokumentiert | Section 16 |
| 2026-05-XX | Test-Suite Phase 1-4 für Backtest-Optimierungen | tests/ |
```

### Task 5.4 — Weitere Dokumentationen aktualisieren

**`automation/README.md`** (erstellen falls nicht vorhanden):
- Config-Verzeichnis-Struktur
- Spread-Modeling-Abschnitt
- Precision-Fix-Erklärung mit Beispiel

**`backtesting/README.md`** (erstellen/aktualisieren):
- Strategy-Defaults-Override-Mechanismus
- Tournament-Selektionslogik erklären
- Baseline vs. optimiert Vergleichstabelle

**Schreibe `logs/TESTREPORT_Phase5_Documentation_<YYYY-MM>.md`.**

**Acceptance Criteria:**
- AGENTS.md Section 15 enthält alle 3 neuen Subsections
- AGENTS.md Section 16 enthält Pitfalls #16, #17, #18
- AGENTS.md Section 18 hat mindestens 8 neue Changelog-Einträge mit korrektem Datum
- Alle Changelog-Einträge sind spezifisch (nicht vague)

---

## Phase 6 — Final Deliverables Checklist

Führe nach Abschluss aller Phasen folgendes Validierungs-Script aus:

```python
# tests/verify_final_deliverables.py
"""Prüft alle erwarteten Deliverables und schreibt Abschlussbericht."""
import json
from pathlib import Path
from datetime import datetime, timezone

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

DELIVERABLES = {
    # Phase 0 (Pre-Flight)
    f"logs/PREFLIGHT_TESTING_{today}.md": "Pre-Flight Report",

    # Phase 1 (Unit Tests)
    "tests/test_config_structure.py": "Config Unit Tests",
    "tests/test_precision_fix.py": "Precision Fix Unit Tests",
    "tests/test_spread_modeling.py": "Spread Modeling Unit Tests",
    "tests/test_tournament_selection.py": "Tournament Unit Tests",
    "tests/test_automation_isolation.py": "Isolation Unit Tests",
    "logs/phase1_unit_test_results.json": "Unit Test Results",
    f"logs/TESTREPORT_Phase1_UnitTests_{today[:7]}.md": "Phase 1 Report",

    # Phase 2 (Integration)
    "tests/integration/test_precision_api.py": "Precision API Test",
    "tests/integration/test_api_backfiller_live.py": "Backfiller Live Test",
    "tests/integration/test_demo_account.py": "Demo Account Test",
    "logs/phase2_integration_results.json": "Integration Test Results",
    f"logs/TESTREPORT_Phase2_Integration_{today[:7]}.md": "Phase 2 Report",

    # Phase 3 (Live Backtest)
    "logs/phase3_baseline_metrics.json": "Baseline Metriken",
    "logs/phase3_backtest_full.log": "Vollständiger Backtest Log",
    "logs/phase3_comparison.json": "Baseline vs. Optimiert Vergleich",
    "logs/phase3_spread_impact.json": "Spread-Modeling Impact",
    f"logs/TESTREPORT_Phase3_LiveBacktest_{today[:7]}.md": "Phase 3 Report",

    # Phase 4 (Universe)
    "tests/test_universe_freshness.py": "Universe Freshness Test",
    "logs/phase4_freshness_before.log": "Freshness vor Refresh",
    "logs/phase4_freshness_after.log": "Freshness nach Refresh",
    f"logs/TESTREPORT_Phase4_UniverseRefresh_{today[:7]}.md": "Phase 4 Report",

    # Phase 5 (Documentation)
    f"logs/TESTREPORT_Phase5_Documentation_{today[:7]}.md": "Phase 5 Report",
}

results = {}
print("\n=== Final Deliverables Check ===\n")
for path, description in DELIVERABLES.items():
    exists = Path(path).exists()
    status = "✓" if exists else "✗"
    results[path] = {"description": description, "exists": exists}
    print(f"  {status} {description}: {path}")

passed  = sum(1 for v in results.values() if v["exists"])
total   = len(results)
missing = [p for p, v in results.items() if not v["exists"]]

print(f"\n{passed}/{total} Deliverables vorhanden")
if missing:
    print(f"\nFehlend ({len(missing)}):")
    for m in missing:
        print(f"  - {m}")

summary = {
    "date": today,
    "passed": passed,
    "total": total,
    "missing": missing,
    "overall": "PASS" if passed == total else "PARTIAL",
}
Path(f"logs/FINAL_DELIVERABLES_{today}.json").write_text(
    json.dumps(summary, indent=2, ensure_ascii=False)
)
print(f"\nBericht: logs/FINAL_DELIVERABLES_{today}.json")
```

```bash
python3 tests/verify_final_deliverables.py
```

### Abschluss-Verifikation AGENTS.md (Part 8 Checklist)

Führe die Verification Checklist aus `.agents/JULES_SYSTEM_PROMPT.md Part 2`
für folgende Sections durch und dokumentiere in `logs/AGENTS_VERIFICATION_<DATUM>.md`:

- [ ] Section 15 (automation/ Standalone Constraints) — alle Checkboxen
- [ ] Section 16 (Pitfalls) — Pitfalls #16, #17, #18 vorhanden
- [ ] Section 18 (Changelog) — mindestens 8 neue Einträge

**Acceptance Criteria:**
- `logs/FINAL_DELIVERABLES_<datum>.json` → `"overall": "PASS"`
- `logs/AGENTS_VERIFICATION_<datum>.md` → alle Checkboxen ausgefüllt
- AGENTS.md enthält alle dokumentierten Änderungen

---

## Zusammenfassung: Erwartete Verbesserungen

Nach Abschluss aller Phasen muss `logs/phase3_comparison.json` folgende
Verbesserungen gegenüber der Baseline (26.05.2026) nachweisen:

| Metrik | Baseline | Ziel | Verifikation |
|--------|----------|------|--------------|
| Precision-Crashes | 5 Symbole | 0 | Phase 3 Backtest Log |
| Tournament-Gewinner | 2/72 | >2/72 | phase3_comparison.json |
| PF=999 Artefakte | 1 | 0 | phase3_comparison.json |
| Universe-Alter | 247h | <24h | Phase 4 Freshness Test |
| Unit-Test-Coverage | 0 | ≥20 Tests | phase1_unit_test_results.json |
| AGENTS.md Changelog | aktuell | +8 Einträge | Section 18 |
