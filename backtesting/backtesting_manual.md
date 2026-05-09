# Nautilus Trader Backtesting Handbuch

Willkommen zum Backtesting-Handbuch! Dieses Dokument erklärt, wie Sie die dynamische Backtesting-Engine konfigurieren, ausführen und die Ergebnisse professionell interpretieren.

## 1. Setup & Ausführung

Die Backtest-Engine wird über die Datei `backtesting_config.json` gesteuert. Dadurch müssen Sie keinen Python-Code verändern, um verschiedene Setups zu testen.

### Konfiguration (`backtesting_config.json`)

Die JSON-Datei besteht aus zwei Hauptbereichen:

**Global Settings:**
Hier definieren Sie die Rahmenbedingungen des Backtests.
- `catalog_path`: Der Pfad zu Ihren historischen Parquet-Daten.
- `start_time` / `end_time`: Der Zeitraum für den Test (z. B. "2023-01-01").
- `start_capital`: Ihr Startkapital in USD (z. B. 10000.0).

**Backtests (Aktive Strategien):**
Hier können Sie beliebig viele Strategien gleichzeitig definieren. Für jede Strategie legen Sie Folgendes fest:
- `strategy_module`: Der Pfad zum Python-Modul (z. B. "strategies.sma_crossover").
- `strategy_class`: Der Name der Strategie-Klasse.
- `config_class`: Der Name der zugehörigen Konfigurations-Klasse.
- `instrument_id`: Das Asset (z. B. "TSLA.ETORO").
- `bar_type`: Die Auflösung der Daten (z. B. "TSLA.ETORO-1-MINUTE-MID-INTERNAL").
- `params`: Ein Dictionary mit allen Indikator-Einstellungen (z. B. `sma_period`: 20). Hier können Sie experimentieren!

### Skript starten

Starten Sie den Backtest im Terminal (aus dem Hauptverzeichnis des Projekts):
```bash
python backtesting/run_backtest.py
```

---

## 2. Strategie-Katalog

Folgende Strategien stehen aktuell zur Verfügung:

1. **SMA Crossover (`strategies.sma_crossover`)**
   - **Konzept:** Klassische Trendfolge. Kauft, wenn der Preis über den Simple Moving Average steigt.
   - **Geeignet für:** Starke Trendphasen.

2. **Trend & Pullback (`strategies.trend_pullback`)**
   - **Konzept:** Bestimmt den Haupttrend (EMA 200) und sucht nach kurzfristigen Rücksetzern (RSI überverkauft).
   - **Geeignet für:** Stabile Assets (Tech, Defense), die in einem klaren Trend verlaufen.

3. **Mean Reversion / Keltner Channel (`strategies.mean_reversion`)**
   - **Konzept:** Nutzt Range-Bound-Märkte aus. Kauft am unteren Keltner-Band und verkauft am oberen.
   - **Geeignet für:** Krypto-Seitwärtsmärkte oder Phasen geringer Volatilität.

4. **Dynamic Breakout (`strategies.dynamic_breakout`)**
   - **Konzept:** Reagiert auf extreme Volumenspitzen gepaart mit einem Ausbruch aus einer Preisspanne.
   - **Geeignet für:** Forex und Rohstoffe (z. B. NATGAS), die oft plötzliche Bewegungen zeigen.

---

## 3. Statistik-Interpretation (Deep Dive)

Am Ende eines erfolgreichen Backtests generiert Nautilus Trader eine Auswertung. Hier ist, wie Sie die Metriken interpretieren:

- **Sharpe Ratio:** Misst die Rendite im Verhältnis zum Risiko (Volatilität).
  - < 1.0: Suboptimal, das Risiko ist im Verhältnis zur Rendite zu hoch.
  - > 1.0: Gut, > 2.0: Exzellent.
- **Sortino Ratio:** Ähnlich wie die Sharpe Ratio, bestraft aber nur die *Abwärts*volatilität (echtes Risiko). Meist aussagekräftiger als Sharpe.
- **Max Drawdown:** Der größte prozentuale Wertverlust von einem Höchststand zum nächsten Tiefpunkt.
  - Ein Max Drawdown von > 20-30% ist für die meisten Portfolios schwer zu verkraften und deutet auf eine riskante Strategie hin.
- **Win-Rate (Trefferquote):** Der Prozentsatz der profitablen Trades.
  - Eine hohe Win-Rate (> 60%) fühlt sich psychologisch gut an.
  - Eine Strategie kann aber auch mit 40% Win-Rate hochprofitabel sein, wenn die Gewinner im Durchschnitt viel größer sind als die Verlierer (Risk-Reward-Ratio).
- **Profit Factor:** Bruttogewinn geteilt durch Bruttoverlust. Werte > 1.5 gelten als robust.

---

## 4. Grenzen und nächste Schritte (WICHTIG)

Backtests in isolierten Offline-Umgebungen haben naturgemäß Grenzen. Beachten Sie folgende Punkte für professionelles Trading:

### Overfitting (Überanpassung)
Wenn Sie Indikatoren (`sma_period`, `rsi_oversold`) so lange optimieren, bis die historische Rendite gigantisch ist, haben Sie die Strategie wahrscheinlich "overfittet" – sie ist maßgeschneidert auf die Vergangenheit, wird in der Zukunft aber kläglich scheitern.

### Walk-Forward-Optimierung (Out-of-Sample-Testing)
Um Overfitting zu vermeiden, nutzen Sie einen Teil der Daten (z. B. Jan-Okt) zur Optimierung (In-Sample) und prüfen die gefundenen Parameter auf "ungesehenen" Daten (Nov-Dez, Out-of-Sample). Dies ist der nächste kritische Schritt zur Professionalisierung.

### Slippage und Transaktionskosten
Offline-Tests kaufen oft zum exakten Schlusskurs ("Close"). In der Realität gibt es *Slippage* (der Preis rutscht während der Orderausführung weg) und Gebühren (Spreads). Konfigurieren Sie Nautilus Trader später so, dass Gebühren und Latenzen simuliert werden, um ein realistisches Bild zu erhalten.
