# automation/ — Isolationstest-Bericht
**Datum:** 2026-05-25T07:25:06Z
**Python:** 3.12.13
**Tester:** QA-Agent

## 1. Import-Violations
| Datei | Zeile | Statement | Disk-Pfad existiert |
|-------|-------|-----------|---------------------|
| `automation/daily_orchestrator.py` | 829 | `from config.setups import ETORO_EXECUTION` | ja |
| `automation/fractional_trading.py` | 91 | `from adapters.instrument_utils import get_size_precision` | ja |
| `automation/fractional_trading.py` | 288 | `from adapters.instrument_utils import get_size_precision` | ja |

## 2. Hard-Coded Path Audit
| Pfad-Konstante | Wert | EXISTS/MISSING |
|----------------|------|----------------|
| `UNIVERSE_PATH` | `data/universe/momentum_ls.json` | EXISTS |
| `QUOTE_TICK_PATH` | `data/nautilus/data/quote_tick/` | EXISTS |
| `IMPORT_PATH` | `data/import/` | MISSING |
| `_SIZE_INCREMENT_CACHE_PATH` | `data/state/size_increment_cache.json` | MISSING |
| `LOG_DIR` | `logs/` | EXISTS |
| `REPORTS_DIR` | `reports/` | MISSING |
| `ENV_FILE` | `.env` | MISSING |

## 3. Import Smoke-Tests
| Modul | Status | Fehlertyp | Detail |
|-------|--------|-----------|--------|
| `automation.api_backfiller` | OK | - | Erfolgreich geladen |
| `automation.catalog_service` | OK | - | Erfolgreich geladen |
| `automation.daily_orchestrator` | OK | - | Erfolgreich geladen |
| `automation.fractional_trading` | OK | - | Erfolgreich geladen |
| `automation.log_manager` | OK | - | Erfolgreich geladen |

*(Hinweis: Externe Module aus `requirements.txt` wie `aiohttp` und `pyarrow` wurden im Test-Environment verifiziert.)*

## 4. CLI Entry-Point Tests
| Skript | --help | --dry-run | Fehler |
|--------|--------|-----------|--------|
| `automation/api_backfiller.py` | OK | OK | Keine Fehler aufgetreten |
| `automation/catalog_service.py` | OK | N/A | Kein `--dry-run` verfügbar |
| `automation/daily_orchestrator.py` | OK | OK | Keine Fehler, saubere Ausführung, sauberer Umgang mit fehlenden Caches |

## 5. Unit-Assertions
| Funktion | PASS/FAIL | Detail |
|----------|-----------|--------|
| `_encode_fsb16` | PASS | Korrekte Konvertierung in 16-Byte Array |
| `_fallback_precisions` | PASS | Fallback Werte greifen korrekt bei Crypto und Equity |
| `_candles_to_arrow_table` | PASS | Schema und FixedSizeBinary korrespondieren |
| `_build_arrow_meta` | PASS | Header Meta-Injection funktioniert |
| `log_manager` | PASS | Logger Setup und File-Writing erfolgreich |
| `_merge_and_save` | PASS | Atomares Schreiben als Parquet File erfolgreich |

## 6. Kritische Blocker (verhindert Standalone-Ausführung)
- [x] Hard-Coded Dependency auf `adapters.instrument_utils.get_size_precision` in `automation/fractional_trading.py`. Bricht die logische Isolation des `automation/` Packages, da es externes Wissen anfordert.
- [x] Hard-Coded Dependency auf `config.setups.ETORO_EXECUTION` in `automation/daily_orchestrator.py`. Greift aus dem `automation` Verzeichnis auf externe Konfigurationen in `config` zu.
- [x] Fehlende Verzeichnis-Erstellung (mkdir) für Laufzeit-Pfade: Wenn `data/import/`, `data/state/` oder `reports/` fehlen, kommt es zu RuntimeErrors, sofern die Skripte diese nicht über `exist_ok=True` selbst anlegen.

## 7. Empfohlene Fixes
1. **`adapters.instrument_utils` Import in `fractional_trading.py`**:
   Refactor-Vorschlag: Kopiere oder verlagere die Logik von `get_size_precision` als isolierte Funktion innerhalb von `automation/fractional_trading.py` oder hole die Parameter dynamisch über die API / eine dedizierte Konfiguration.

2. **`config.setups` Import in `daily_orchestrator.py`**:
   Refactor-Vorschlag: Lade notwendige Environment- oder Execution-Parameter direkt über Umgebungsvariablen (`.env` über `os.environ.get`) anstatt `config.setups` zu importieren, um Abhängigkeiten zu eliminieren.

3. **Laufzeit-Pfade**:
   Füge in den betroffenen Modulen am Initialisierungs-Punkt `Path(...).mkdir(parents=True, exist_ok=True)` hinzu. Besonderes Augenmerk liegt auf `data/import/`, `data/state/` und `reports/`.
