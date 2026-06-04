# 📊 Backtesting & Tearsheet Manual: Nautilus Trader

Dieses Handbuch beschreibt den Workflow, um historische Marktdaten (gesammelt in Parquet-Dateien) für performante, risikofreie Backtests zu nutzen. Es deckt den Prozess von der Konfiguration über die Ausführung bis hin zur Auswertung mittels interaktiver HTML-Tearsheets ab.

---

## 1. Warum lokales Backtesting?

Der `nautilus-catalog.service` auf der Cloud-VM sammelt 24/7 Tick- und Bar-Daten und speichert sie als hochkomprimierte `.parquet`-Dateien. Da Backtesting-Engines diese historischen Daten komplett in den Arbeitsspeicher (RAM) laden, würde eine kleine Cloud-VM (wie die `e2-micro` mit 1GB RAM) sofort abstürzen. Daher lagern wir den Backtesting-Prozess auf lokale PCs/Laptops aus.

### 1.1 Marktdaten herunterladen (VM ➔ Lokal)

Synchronisiere die Marktdaten von deinem Server in dein lokales Projektverzeichnis.


**Via Terminal (SCP):**
```bash
scp -r <user>@<ip>:/data/nautilus ./data/
```

**Daten direkt via eToro API abrufen (Fallback):**
Falls keine Parquet-Daten von der VM vorhanden sind, können diese per Skript geholt werden:

```bash
# Für ein einzelnes Symbol (z.B. Tesla, 6 Monate)
python3 dev_scripts/momentum_ls_fetch_candles.py --etoro-id 1111 --symbol TSLA.ETORO --months 6

# Automatisch alle fehlenden Daten für das Momentum-LS Universum laden
python3 dev_scripts/momentum_ls_fetch_candles_auto.py --universe data/universe/momentum_ls.json
```


**Via VS Code:**
Nutze die "Remote - SSH" Erweiterung, navigiere zu `/data/nautilus` und lade den Ordner per Rechtsklick herunter.

---

## 2. Konfiguration des Backtests (`backtesting_config.json`)

Die Backtest-Engine wird über die Datei `backtesting/backtesting_config.json` gesteuert. Sie müssen keinen Python-Code ändern, um Parameter zu optimieren.

Die JSON-Datei ist wie folgt aufgebaut:

*   **Global Settings (`catalog_path`, `start_time`, `end_time`, `start_capital`):** Definieren die Rahmenbedingungen. Achte darauf, dass `catalog_path` auf dein lokales Datenverzeichnis (z.B. `./data/nautilus`) zeigt.
*   **Backtests (Aktive Strategien):** Eine Liste von Strategien, die simuliert werden sollen.
    *   `strategy_module` / `strategy_class` / `config_class`: Pfade und Namen der Python-Klassen.
    *   `instrument_id` / `bar_type`: Das zu testende Asset (z. B. `TSLA.ETORO`).
    *   `params`: Ein Dictionary mit allen Indikator-Einstellungen. Beispiele:
        *   `SmaCrossoverStrategy`: `sma_period`
        *   `TrendPullbackStrategy`: `ema_period`, `rsi_period`, `rsi_oversold`, `rsi_overbought`
        *   `MeanReversionStrategy`: `keltner_period`, `keltner_atr_period`, `keltner_multiplier`

---

## 3. Ausführung und Workflow

### Regulärer Backtest

Starten Sie den Backtest im lokalen Terminal:

```bash
python3 backtesting/run_backtest.py
```

### Momentum-LS Tournament Backtest

Statt eines manuellen Backtests lässt das Tournament alle Strategien gegeneinander antreten, um die beste für jedes Symbol zu finden:

```bash
python3 dev_scripts/momentum_ls_tournament.py \
    --universe data/universe/momentum_ls.json \
    --output logs/tournament_$(date +%Y-%m-%d).json
```

### Der Optimierungs-Workflow

1.  Öffne `backtesting_config.json` und ändere die Parameter im `params` Block (z. B. den `sma_period`).
2.  Führe `python backtesting/run_backtest.py` erneut aus.
3.  Bewerte die generierten Ergebnisse (siehe Kapitel 4).
4.  Wenn ein Setup profitabel ist, übernimm es in die Live-Konfiguration (`config/setups.py`).

*Tipp bei RAM-Problemen (32GB+):* Nutze die `start_time` und `end_time` in der JSON-Config, um spezifische Zeitfenster (z.B. nur eine Woche) zu testen und RAM zu sparen.

---

## 4. Auswertung: Nautilus Tearsheets

Am Ende eines erfolgreichen Backtests generiert Nautilus Trader automatisch einen umfassenden Performance-Bericht. In Versionen >= 1.226.0 erfolgt dies über die funktionale API, welche eine interaktive HTML-Datei (das "Tearsheet") erstellt.

Der Code in `run_backtest.py` kümmert sich bereits darum:

```python
from nautilus_trader.analysis.tearsheet import create_tearsheet

# ... (Engine Setup und Run)

report_filename = os.path.join(reports_dir, f"tearsheet_{inst_id_str}_{strategy_class_name}_{timestamp}.html")
create_tearsheet(
    engine=engine,
    output_path=report_filename,
    title="eToro Strategie Backtest"
)
```

### 4.1 Interpretation des Tearsheets

Öffne die erzeugte HTML-Datei im Browser. Sie enthält Plotly-Charts (inkl. Equity, Drawdown und markierten Order-Fills auf den Candlesticks). Achte auf folgende Metriken:

*   **Sharpe Ratio:** Misst die Rendite im Verhältnis zur Volatilität. (> 1.0 ist gut, > 2.0 exzellent).
*   **Sortino Ratio:** Bestraft nur Abwärtsvolatilität (echtes Risiko) und ist oft aussagekräftiger als die Sharpe Ratio. (Hauptmetrik im Momentum-LS).
*   **Max Drawdown:** Der größte prozentuale Wertverlust vom Hoch zum Tief. (> 20-30% ist meist riskant).
*   **Win-Rate & Profit Factor:** Win-Rate (> 50%) kombiniert mit einem Profit Factor (Bruttogewinn / Bruttoverlust) > 1.5 deutet auf eine robuste Strategie hin.

### 4.2 Interpretation des Tournaments
Wenn du `momentum_ls_tournament.py` ausgeführt hast, wird eine Tabelle auf der Konsole sowie eine JSON generiert:
*   **Win?:** Ist in der Tabelle mit einem `✓` markiert, wenn die Strategie der absolute Gewinner für das spezifische Symbol ist.
*   **Profit Factor Schwelle:** Jede Strategie muss einen PF > 1.5 aufweisen. Wenn keine Strategie dies für ein Symbol schafft, wird das Symbol für diesen Tag nicht gehandelt.
*   **JSON Output:** Das resultierende JSON listet die Gewinner-Konfigurationen detailliert auf. Diese Datei wird später von `momentum_ls_run.py` eingelesen.

### 4.3 Fallback (CSV Berichte)

Falls die HTML-Generierung fehlschlägt, können rohe CSV-Daten exportiert werden (dies kann im Skript auskommentiert werden):

```python
positions_df = engine.trader.generate_positions_report()
positions_df.to_csv("reports/positions_etoro.csv")
```

### 4.4 Return-Definition (Total Return)

Der "Total Return" (Gesamtrendite) im Tournament und in den Log-Ausgaben wird als **Compounded Equity-Normalized Return** berechnet.

*   **Berechnung:** Für jeden abgeschlossenen Trade wird der erzielte Profit/Verlust (PnL in USD) durch das Startkapital (`start_capital`) dividiert. Dies ergibt die prozentuale Rendite des Trades bezogen auf die initiale Equity.
*   **Compounding:** Diese einzelnen Trade-Renditen werden anschließend geometrisch multipliziert (aufgezinst), um den Gesamtwertzuwachs über den Backtest-Zeitraum zu ermitteln (`cum *= (1.0 + r)`).
*   **Wichtig:** Dies bedeutet, dass die Sizing-Parameter (wie `trade_amount_pct` oder `trade_amount_usd`) so gewählt sein müssen, dass sie relativ zum Startkapital realistische Schwankungen erzeugen. Ist die Positionsgröße mikroskopisch klein im Vergleich zum Startkapital, konvergiert der Total Return gegen 0 %.

---

## 5. Warnung: Overfitting & Slippage

*   **Overfitting:** Wenn du Parameter extrem lange auf historischen Daten optimierst, passen sie perfekt auf die Vergangenheit, scheitern aber live. Nutze "Out-of-Sample" Tests (Optimiere auf Jan-Okt, teste auf Nov-Dez).
*   **Slippage:** Offline-Tests gehen oft von perfekten Ausführungen aus. In der Realität schwanken Preise zwischen Ordererteilung und Ausführung. Berücksichtige dies bei deinen Erwartungen.

---

## 5. Synthetische Daten

[In Entwicklung]


---
## Weiterführende Dokumente
- `manuals/deployment.md`
- `manuals/momentum_ls.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
