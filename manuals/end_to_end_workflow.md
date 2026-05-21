# 🔄 End-to-End Workflow & Pipeline

Dieses Handbuch dokumentiert den vollständigen Lifecycle einer algorithmischen Trading-Strategie innerhalb der **eToro Nautilus Plattform** – von der Datengenerierung bis zur Ausführung mit echtem Kapital.

Das System folgt einer strikten 4-Phasen-Pipeline:
1. **Data Collection & Universe Mapping**
2. **Backtesting & Fine-Tuning**
3. **Demo Testing ($10k)**
4. **Live Deployment**

## 🏗️ Architektur-Synergien

Das System bietet zwei Hauptwege für das Live-Trading, welche sich architektonisch ergänzen:

*   **Der "Set-and-Forget" Route (Momentum-LS Orchestrator):** Skripte wie `dev_scripts/momentum_ls_run.py` implementieren eine vollautomatisierte Pipeline (Universum filtern -> Turnier -> Allokation -> Live Node). Dies stellt die Zielarchitektur für zukünftige Skalierung dar.
*   **Der Manuelle Route (Single-Bot Setup):** `run_bot.py` in Kombination mit `config/setups.py` ermöglicht hochspezifisches Fine-Tuning und die Ausführung einzelner fokussierter Strategien. Dieser Weg ist ideal für das Debugging, Forward-Testing neuer Strategien oder spezialisierte Einzel-Assets (z.B. Tesla).

---

## Phase 1: Data Collection & Universe Mapping

Bevor ein Backtest durchgeführt werden kann, müssen historische Preisdaten generiert werden. Die Plattform nutzt Nautilus Parquet-Dateien, die im Verzeichnis `data/nautilus/` gespeichert werden.

### Automatisierte Datengewinnung (Momentum-LS Pipeline)

Für Strategien, die auf einem dynamischen Universum operieren, erfolgt die Datengewinnung in zwei Schritten:

1.  **Universum Filtern:**
    Führe das Skript zur Bestimmung des aktuellen Anlage-Universums aus (z.B. basierend auf eToro Smart Portfolios).
    ```bash
    python3 dev_scripts/momentum_ls_universe.py
    ```
    *Resultat:* Erzeugt oder aktualisiert die Datei `data/universe/momentum_ls.json`.

2.  **Candles / Ticks Fetchen:**
    Das Auto-Fetch-Skript liest das generierte Universum aus und lädt die entsprechenden historischen Daten herunter.
    ```bash
    python3 dev_scripts/momentum_ls_fetch_candles_auto.py
    ```
    *Resultat:* Speichert die Marktdaten als QuoteTicks im Parquet-Format ab.

> **💡 Usability-Optimierung (Backlog):** Aktuell erfordert dieser Prozess das sequentielle Ausführen zweier separater Skripte. Zukünftige Updates sollen diese Schritte in eine einheitliche Data-Pipeline integrieren (z.B. via `make fetch-data`).

---

## Phase 2: Backtesting, Fine-Tuning & Critical System Limits

Sobald die Parquet-Daten im `catalog_path` vorliegen, wird die Backtesting-Engine verwendet, um die Strategien zu evaluieren.

### Konfiguration und Ausführung

1.  **Konfigurieren:** Passe die Strategie-Parameter und Global Settings in `backtesting/backtesting_config.json` an.
2.  **Ausführen:**
    ```bash
    python3 backtesting/run_backtest.py --htmlreport
    ```
    Dies führt die Tests parallel aus und generiert Tearsheets im `reports/`-Ordner zur visuellen Auswertung.

### ⚠️ KRITISCH - Pitfall #14 (Fractional Equities Limitation)

Beim Backtesting und Live-Trading von eToro-Aktien (Equities) gibt es eine fundamentale Einschränkung im aktuellen Framework:

*   **Das Problem:** eToro erlaubt für Aktien den Handel von Bruchteilen (Fractional Shares) **nur** über den "By-Amount"-Endpunkt (USD-Betrag). Unser aktueller `etoro_execution.py` Adapter unterstützt jedoch derzeit nur den **"By-Units"** Fallback (Anzahl der Aktien) für diverse Operationen (z.B. Shorting oder komplexe Schließungen). Da der "By-Units" Endpunkt bei Equities strikt auf ganzen Zahlen besteht, **muss `size_precision` für Equities in `instrument_utils.py` auf `0` erzwungen werden.**
*   **Der Crash (`ValueError`):** Wenn `trade_amount_usd` kleiner als der Preis für eine einzelne Aktie ist (z.B. 100$ Einsatz bei einer 450$ Tesla Aktie), berechnet Nautilus eine Quantity von `< 1`. Da `size_precision=0`, rundet Cython dies auf `0` ab und wirft einen harten `ValueError`, was den Worker-Prozess zum Absturz bringt.
*   **Die Notlösung:** Alle Strategien implementieren in `_compute_quantity()` einen Pre-Check (`if units < float(instrument.size_increment): return None`), der Trades überspringt, wenn das Kapital nicht für mindestens 1 volle Aktie reicht.

### 🚀 Feature Roadmap (Fractional Equities Refactoring)

Um diese Limitierung aufzuheben und Fractional Equities in Nautilus nutzbar zu machen, **MUSS** das Framework in Zukunft wie folgt refaktoriert werden:

1.  **Rewrite des `etoro_execution.py` Adapters:** Die primäre Order-Logik muss von "By-Units" auf "By-Amount" (Investition in USD) umgeschrieben werden, da eToro Bruchstück-Aktien ausschließlich über diesen Endpunkt zuverlässig abwickelt.
2.  **Quantity Abstraction:** Nautilus rechnet intern strikt in Units. Es muss eine Abstraktionsschicht geschaffen werden, die Nautilus "vorgaukelt", es handle in Units, während der Adapter im Hintergrund die korrekten "By-Amount" Payloads an eToro sendet und den tatsächlichen Fill-Preis zurück in Units für Nautilus übersetzt.
3.  **Anpassung `size_precision`:** Sobald der Adapter By-Amount vollständig unterstützt, kann `size_precision` für Equities in `adapters/instrument_utils.py` von `0` auf `5` (oder den eToro Standard) angehoben werden.

---

## Phase 3: Demo Testing ($10,000)

Erfolgreiche Strategien aus dem Backtest werden zunächst im eToro Demo-Konto (Papertrading) validiert.

### Parameter Mapping

Übertrage die profitablen Parameter aus dem Backtest-Tearsheet in das `ACTIVE_BOTS` Dictionary in `config/setups.py`:

```python
"MyWinningBot": {
    "strategy_class": "TrendPullbackStrategy",
    "etoro_id": "1111",
    "symbol": "TSLA.ETORO",
    "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
    "params": {
        "ema_period": 50,
        "rsi_pullback_level": 40.0
    },
    "trade_amount_usd": 100.0,
    "max_open_positions": 1,
}
```

### Strikte Isolation (Safety by API-Key)

Das System verfügt über eine strikte Isolation zwischen Demo- und Echtgeld-Konten. Ein manueller "Reset" der Konten ist nicht nötig.
Die Trennung erfolgt ausschließlich über die Konfiguration:

1.  **API Keys:** Nutze die **Demo API Keys** in deiner `.env` Datei.
2.  **Environment Flag:** Stelle sicher, dass in `config/setups.py` das Demo-Environment konfiguriert ist:
    ```python
    ETORO_EXECUTION = {
        "environment": "demo",
        "dry_run": False,
        "enable_trailing_stop": True,
    }
    ```

Starte den Bot mit `python3 run_bot.py`. Der Bot läuft nun isoliert gegen das virtuelle $10k Portfolio.

---

## Phase 4: Live Deployment

Wenn die Strategie im Demo-Modus profitabel und stabil läuft, erfolgt der Wechsel zum Live-Trading (Echtgeld).

### Safety Interlocks (Echtgeld-Schutzmechanismen)

Um fatale Fehler durch versehentliches Echtgeld-Trading zu verhindern, ist eine harte "Safety Interlock" Kaskade in `run_bot.py` integriert. **Alle** folgenden Bedingungen müssen erfüllt sein, sonst bricht der Start ab (`sys.exit(1)`):

1.  `ETORO_EXECUTION["environment"] = "real"` (in `config/setups.py`)
2.  `ETORO_EXECUTION["dry_run"] = False` (in `config/setups.py`)
3.  `.env` Datei enthält die korrekten, realen eToro API- und User-Keys.
4.  `.env` Datei enthält die explizite Bestätigungs-Variable: `ETORO_CONFIRM_LIVE=1`

### Systemd Process Separation

Auf der Produktions-VM dürfen Live-Trading und Datengenerierung nicht im selben Prozess laufen, um Latenzen oder Crashes gegenseitig zu isolieren.

*   `run_bot.py` (Die Handelslogik) läuft als eigener systemd Service.
*   `run_catalog.py` (Das passive Parquet-Recording) läuft als separater systemd Service.

Weitere Details zur Konfiguration der Linux-VM, Swap-Space und systemd-Diensten findest du im [☁️ Deployment & Operations Guide (`manuals/deployment.md`)](./deployment.md).
