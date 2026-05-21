# Technical Design Document: eToro Nautilus Platform Refactoring & Orchestration

Dieses Dokument definiert das technische Design für die Orchestrierung, das "By-Amount"-Order-Routing und die Telegram-Alert-Infrastruktur des eToro Nautilus Trading-Systems. Das Ziel ist es, von sequenziellen Skripten zu einem konsolidierten und fehlerresistenten Service zu migrieren.

## A. Orchestration & Automation (`run_daily_orchestrator.py`)

### 1. Architektur & Sequenz
Der Orchestrator konsolidiert die Skripte `dev_scripts/momentum_ls_universe.py`, `dev_scripts/momentum_ls_fetch_candles_auto.py` (oder ähnlich), `dev_scripts/momentum_ls_tournament.py` und den Bot-Start (`run_bot.py` oder `momentum_ls_run.py`) in eine einzige asynchrone Pipeline.

**Ablauf (Cron-gesteuert, z.B. 23:00 UTC):**
1. **Universe Update**: Führt die Logik von `momentum_ls_universe.py` aus (als importierte Funktion oder Subprozess), um das Universum zu aktualisieren.
2. **Data Fetch**: Startet den Download fehlender oder neuer Kerzendaten für das aktualisierte Universum.
3. **Tournament Simulation**: Führt das Backtesting über `momentum_ls_tournament.py` aus.
4. **Validation**: Der Orchestrator validiert das Ergebnis:
    - Sind die Dateien `logs/momentum_ls.json` und `logs/tournament_YYYY-MM-DD.json` erfolgreich generiert worden und jünger als 2 Stunden?
    - Sind ausreichend Ergebnisse/Assets für das Trading verfügbar?
5. **Config Injection**: (siehe Punkt 2)
6. **Execution/Trading**: Beendet eine eventuell laufende Trading-Session und startet die aktuelle Session (`run_bot.py`) mit der neuen Allokation neu.

### 2. Automated Parameter-Transfer
Um den manuellen Transfer der Tournament-Ergebnisse in `config/setups.py` zu eliminieren, wird ein Config-Loader-Pattern implementiert.
- In `config/setups.py` wird eine Funktion `load_dynamic_momentum_config()` erstellt.
- Diese Funktion sucht nach der aktuellsten `logs/tournament_*.json`-Datei.
- Sie liest die Top-Strategien/Assets dynamisch ein und hängt diese an die `ACTIVE_BOTS`-Liste in `config/setups.py` an.
- Dadurch kann der Trading-Node nach einem Neustart direkt die optimalen Parameter aus dem letzten Backtest verwenden, ohne menschliche Interaktion.

## B. Execution Engine ("By-Amount" Design)

### 1. Modulares Order-Routing in `adapters/etoro_execution.py`
Das Order-Routing in `_build_market_open_payload()` muss modifiziert werden, um den `ValueError` bei Equities (`Pitfall #14`, `size_precision=0`) zu vermeiden. Da eToro für Equities keine Bruchteile (Fractions) erlaubt, schlagen Quantity-Kalkulationen fehl, die auf Rundungen basieren. Anstelle von "By-Units" muss für Equities der "By-Amount" (USD) Endpoint genutzt werden.

**Geplante Routing-Logik:**
```python
def _build_market_open_payload(self, order, etoro_id: int) -> tuple[dict, str]:
    # ... Base payload ...
    precision = get_size_precision(order.instrument_id.symbol)

    # 0 = Equity. We MUST use By-Amount.
    if precision == 0:
        last_quote = self._cache.quote_tick(order.instrument_id)
        if not last_quote:
             raise RuntimeError(f"Cannot route By-Amount without last quote for {order.instrument_id}")

        # Amount = Units * Price. Rekonstruktion des USD-Betrags aus der Menge.
        exec_price = float(last_quote.ask_price if order.side == OrderSide.BUY else last_quote.bid_price)
        usd_amount = round(float(order.quantity) * exec_price, 2)
        payload["Amount"] = usd_amount
        url = f"{self._rest_base}/market-open-orders/by-amount"
    else:
        # Crypto/Forex/Commodities -> By-Units (supports fractions)
        payload["AmountInUnits"] = float(order.quantity)
        url = f"{self._rest_base}/market-open-orders/by-units"

    # ... Apply SL/TP ...
```

### 2. Anpassung in `adapters/instrument_utils.py`
Die bestehende `get_size_precision()` Funktion ordnet bereits `Crypto` (8), `Forex/Commodities` (5) und `Equities` (0) korrekt zu. `adapters/etoro_execution.py` wird modifiziert, um diese Funktion zu importieren und die Routing-Entscheidung (`url` Zuweisung) basierend auf diesem Rückgabewert zu fällen.

## C. Reliability & Telegram Alerting

### 1. Design `utils/telegram_bot.py`
Implementierung eines asynchronen Alerters zur proaktiven Fehlerüberwachung.
**Klasse:** `TelegramAlerter`
- **Konfiguration:** Liest `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` aus der `.env`.
- **Methoden:** `send_alert(level: str, message: str)`, `send_daily_summary(file_path: str)`

### 2. Integration in Orchestrator & Live-Node
- **Globaler Exception Handler:** In `run_bot.py` und `run_daily_orchestrator.py` wird `sys.excepthook` überschrieben. Jeder Crash oder OOM-Fehler sendet ein `CRITICAL` Alert via Telegram, bevor der Prozess beendet wird.
- **Bot/Orchestrator Logging:** Der Orchestrator sendet ein `INFO` Level Alert nach Abschluss des täglichen Tournaments (z.B. "Tournament abgeschlossen. Starte Trading.").

### 3. Alert Levels
- **(1) INFO:** Regulieres Reporting (z.B. "Daily Tournament abgeschlossen. Top Assets: TSLA, AAPL. Bot startet.").
- **(2) WARNING:** Temporäre Probleme (z.B. "API Reconnect. Failed to fetch PnL (Retry 3/3)" oder "Slippage-Warnung.").
- **(3) CRITICAL:** Systemausfall, Bot Crash, OOM-Kills oder Unhandled Exceptions (z.B. "Bot Crash! Unhandled Exception: ValueError in strategy").

## D. Testing Strategy

### 1. Orchestrator Pipeline
- **Ort:** `tests/test_orchestrator.py`
- **Ansatz:** Mocking von Subprozessen/Funktionsaufrufen (`unittest.mock`).
- **Prüfung:** Sicherstellen, dass die Pipeline bei fehlenden oder alten JSON-Dateien abbricht und dass bei Erfolg die richtige Neustart-Routine des Trading-Nodes getriggert wird.

### 2. By-Amount Execution Routing
- **Ort:** `tests/test_etoro_execution_routing.py`
- **Ansatz:** Dry-Run Simulation (`dry_run = True` in der eToro Exec Config).
- **Prüfung:** Instanziieren des Adapters mit Mocks für `_cache.quote_tick`. Aufrufen von `_build_market_open_payload` mit einer Equity-Order (Symbol z.B. AAPL) und verifizieren, dass der zurückgegebene Endpunkt `/by-amount` lautet und `Amount` (nicht `AmountInUnits`) gesetzt ist. Dasselbe mit einer Crypto-Order (Endpunkt `/by-units`).

### 3. Telegram Alerting
- **Ort:** `tests/test_telegram_alerting.py`
- **Ansatz:** Skript `simulate_crash.py`, das einen künstlichen Fehler wirft (z.B. `1/0`).
- **Prüfung:** Mocken von `aiohttp.ClientSession.post` innerhalb von `TelegramAlerter`. Verifizieren, dass der Hook auslöst und die erwartete Payload (mit Prefix "CRITICAL") an die korrekte URL geschickt wird.

## E. Feature Roadmap (User Stories)

### Story 1: Telegram Alerting Infrastructure
- **Aufgabe:** Erstellen von `utils/telegram_bot.py`. Überschreiben von `sys.excepthook` in den Haupteinstiegspunkten.
- **Testing & Verification:** Ausführen von `simulate_crash.py`. Überprüfen der Mock-Assertions. Hinzufügen von Dummy-Keys zu `.env.example`.

### Story 2: Execution Engine "By-Amount" Refactoring
- **Aufgabe:** Importieren von `get_size_precision` in `adapters/etoro_execution.py`. Umbau der `_build_market_open_payload` Methode für die dynamische Routen-Wahl.
- **Testing & Verification:** Ausführen der neuen Unit-Tests `test_etoro_execution_routing.py`. Einrichten eines Dry-Runs mit echten Symbolen (Equity und Crypto) über die `dev_scripts`, um die Payloads ohne tatsächliche Order-Ausführung zu verifizieren.

### Story 3: Daily Orchestrator & Config Injection
- **Aufgabe:** Implementieren von `run_daily_orchestrator.py` zur Verkettung der Skripte. Ergänzen von `config/setups.py` mit `load_dynamic_momentum_config()`.
- **Testing & Verification:** Starten des Orchestrators im Test-Modus (mit gemockten Sub-Tasks). Prüfen der Validierungslogik bei defekten JSONs. Verifizieren, dass nach Abschluss das INFO-Telegram-Alert gesendet wird.
