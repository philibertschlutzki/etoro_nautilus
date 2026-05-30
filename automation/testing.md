# eToro Nautilus — automation/ Testing Guide

Alle Tests müssen das Standalone-Prinzip des `automation/`-Pakets respektieren.
Kein Test darf Module aus `adapters/`, `config/` oder `strategies/` (Root) importieren.

## Pre-Flight Sektion
Führen Sie vor dem Commit folgende Checks aus:
```bash
# automation/-Paket prüfen
python -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"

# universe_fetcher prüfen
python -c "from automation.universe_fetcher import is_universe_stale; print('OK')"

# instrument_map.json prüfen
python -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(f'{len(d[\"instruments\"])} instruments OK')"
```

## Unit- und Integration-Tests

Neue Tests befinden sich im `tests/` Verzeichnis. Das `automation/`-Paket wird von den folgenden Testsuiten abgedeckt:

1. `test_universe_fetcher.py` (inkl. `is_universe_stale` und Integration)
2. `test_backtest_runner.py` (Parquet Precisions, Score-Berechnung, CLI)
3. `test_automation_isolation.py` (Sicherstellung der Standalone-Prinzipien und `instrument_map.json` Validierung)

**Besondere Naming-Vorgaben für Tests:**
- Verwende überall `_fallback_precisions` (mit Underscore). Beispiel:
  ```python
  from automation.utils import _fallback_precisions
  assert _fallback_precisions("ETH.ETORO") == (2, 8)
  ```
- Es gibt **keine Kill-List**. Keine Erwähnung von Kill-List in den Tests!
- Nutze `automation.backtest_runner` für `compute_tournament_score` und `select_winners` Aufrufe.

### Ausführung
```bash
pytest tests/test_universe_fetcher.py tests/test_backtest_runner.py tests/test_automation_isolation.py -v
```

## Environment Variablen für Tests
Alle `@pytest.mark.integration` Tests lesen Credentials aus:
```python
import os
from pathlib import Path
from dotenv import load_dotenv

_ENV = Path("automation/.env")
if not _ENV.exists():
    _ENV = Path(".env")
load_dotenv(str(_ENV))
```
