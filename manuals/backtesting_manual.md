# Backtesting Manual: eToro Nautilus

Dieses Handbuch erklärt, wie das Backtesting-System der eToro Nautilus Plattform funktioniert, wie du es konfigurierst und wie du die Ergebnisse auswertest.

> **Wichtig:** In der aktuellen Architektur (v2.0) läuft das Backtesting **nicht** mehr über ein separates Skript, sondern ist fest in die 5-Phasen-Pipeline des `automation/daily_orchestrator.py` integriert (Phase 3+4). Das Modul `automation/backtest_runner.py` übernimmt dabei die eigentliche Ausführung.

---

## 1. Grundprinzip: Wie läuft das Backtesting?

Ein **Backtest** (dt. "historischer Test") simuliert, wie eine Handelsstrategie in der Vergangenheit abgeschnitten hätte. Das System lädt gespeicherte Tick-Daten (QuoteTicks im Parquet-Format) und lässt die Strategie virtuell handeln — ohne echtes Geld zu riskieren.

Das **Tournament** (dt. "Turnier") geht einen Schritt weiter: Es lässt alle konfigurierten Strategien für alle Symbole im Universe gegeneinander antreten und wählt automatisch die beste Strategie pro Symbol aus.

### Ablauf (integriert in den Orchestrator):
```
Phase 3: Matrix-Backtest (N Symbole × M Strategien, parallel)
        │
        ▼
Phase 4: Tournament (Eligibilitätsprüfung + Score-Berechnung)
        │
        ▼
logs/tournament_YYYY-MM-DD.json  ← Enthält Gewinner pro Symbol
        │
        ▼
Phase 5: Live-Bot startet mit diesen Gewinner-Strategien
```

---

## 2. Daten beschaffen (VM → Lokal)

Der `nautilus-catalog.service` auf der Cloud-VM sammelt 24/7 Tick-Daten. Für lokales Backtesting musst du diese Daten zuerst auf deinen PC herunterladen, da das Laden großer Datensätze die kleine Cloud-VM überlasten würde.

### Option A: Via SCP (Terminal)
```bash
scp -r <user>@<server-ip>:/opt/etoro_nautilus/data/nautilus ./data/
```

### Option B: Via API-Backfill (wenn keine VM-Daten vorhanden)
```bash
# Letzte 7 Tage via eToro API laden:
python3 automation/api_backfiller.py --days 7

# Vollständiger historischer Backfill (12 Monate):
python3 automation/historical_fetcher.py --months 12
```

### Datenformat
Die Daten liegen als Parquet-Dateien unter:
```
data/nautilus/data/quote_tick/{SYMBOL}/data.parquet
```
Format: `FixedSizeBinary(16)` (FSB16) — Nautilus-natives QuoteTick-Format.

---

## 3. Konfiguration: `automation/config/backtest.json`

Die Backtest-Engine wird über `automation/config/backtest.json` gesteuert. Du musst keinen Python-Code ändern, um Parameter anzupassen.

> **Hinweis:** In früheren Versionen war die Konfiguration unter `backtesting/backtesting_config.json`. Diese Datei ist Legacy. Die aktuelle, verbindliche Konfiguration liegt unter `automation/config/backtest.json`.

**Wichtige Felder:**
- `catalog_path` — Pfad zum lokalen Daten-Verzeichnis (Standard: `./data/nautilus`)
- `is_window_days` — Anzahl Tage für den In-Sample-Backtest (trainings-Zeitraum)
- `oos_window_days` — Anzahl Tage für den Out-of-Sample-Test (Validierungs-Zeitraum)
- `start_capital` — Startkapital für die Simulation (in USD)

**Weitere Konfigurationsdateien:**
- `automation/config/strategies.json` — welche Strategien aktiv sind (`active: true/false`)
- `automation/config/strategy_defaults.json` — Standard-Parameter aller Strategien
- `automation/config/tournament.json` — Schwellenwerte für Eligibilität und Score-Gewichte

---

## 4. Backtest ausführen

### Vollständiger Lauf via Orchestrator (empfohlen)

```bash
# Phasen 1–5: Universe, Daten, Backtest, Tournament, Live-Bot
python3 automation/daily_orchestrator.py --skip-api-fetch

# Nur Phasen 1–4 (kein Live-Bot-Start):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

### Nur Backtest + Tournament (manuell)

Wenn du nur den Backtest ohne die restliche Pipeline ausführen möchtest:

```bash
# Dry-Run: führt Phasen 1-4 aus (Backtest + Tournament), startet aber keinen Live-Bot
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

Das Tournament-Ergebnis wird gespeichert unter:
```
logs/tournament_YYYY-MM-DD.json
```

---

## 5. Aktive Strategien

Die folgenden Strategien sind im Matrix-Backtest aktiv (konfigurierbar über `automation/config/strategies.json`):

| Strategie | Beschreibung |
|-----------|-------------|
| `SmaCrossoverStrategy` | Einfacher SMA(5)-Crossover |
| `MeanReversionStrategy` | Keltner-Channel-Reversion |
| `DynamicBreakoutStrategy` | Price-Range-Breakout |
| `FlashCrashReversalStrategy` | Bollinger-Band(10) + RSI(7) Reversal |
| `VolatilityBreakoutPumpStrategy` | Bollinger-Band(10)-Pump-Erkennung |
| `ComboTrendVwapStrategy` | SMA + MACD + BB + ATR + VWAP |
| `VwapExhaustionStrategy` | VWAP-Deviation-Erschöpfung |

---

## 6. Tournament-Auswertung: Wer gewinnt?

### Schritt 1: Metriken berechnen
Nach jedem Backtest-Job berechnet das System folgende Kennzahlen aus den abgeschlossenen Trades:

| Kennzahl | Was bedeutet das? | Mindestanforderung |
|----------|------------------|-------------------|
| `total_trades` | Anzahl abgeschlossener Positionen | ≥ 20 |
| `total_return` | Gesamtrendite (% des Startkapitals) | > 0% |
| `sortino` | Rendite / Abwärtsrisiko (höher = besser) | ≥ 0.3 |
| `profit_factor` | Gewinne / Verluste (> 1.0 = profitabel) | ≥ 1.1 |
| `win_rate` | Anteil gewinnender Trades | — |
| `max_drawdown` | Größter prozentualer Wertverlust | — |

> **Sortino-Ratio erklärt:** Anders als die Sharpe-Ratio bestraft die Sortino-Ratio nur Abwärts-Volatilität (echte Verlustrisiken), nicht aber Aufwärts-Schwankungen. Sie ist daher eine aussagekräftigere Risikometrik.

### Schritt 2: Eligibilitätsprüfung (muss ALLES erfüllt sein)
Eine Strategie kommt nur ins Tournament, wenn:
- `min_trades ≥ 20` — genug Trades für statistische Aussagekraft
- `total_return > 0%` — im Backtest profitabel

**Plus mindestens EINE dieser Bedingungen:**
- `min_sortino ≥ 0.3` — ODER
- `min_profit_factor ≥ 1.1`

### Schritt 3: Score-Berechnung
```
Score = sortino × 0.4 + profit_factor × 0.3 + win_rate × 0.2 − max_drawdown × 0.1
```
Die Strategie mit dem **höchsten Score** gewinnt für dieses Symbol.

### Schritt 4: OOS-Gate (Sicherheitscheck)
Bevor der Live-Bot startet, prüft der Orchestrator die Out-of-Sample-Performance des Gesamtgewinners. Ist die OOS-Rendite negativ, blockiert das Gate den Live-Deploy (Sicherheitsprinzip: "Fail-Closed").

---

## 7. Tournament-Ergebnis analysieren

```bash
# Alle Gewinner anzeigen:
python3 -c "
import json
d = json.load(open('logs/tournament_$(date +%Y-%m-%d).json'))
for sym, w in d['per_symbol_winners'].items():
    print(f\"{sym:<30} {w['strategy']:<35} sortino={w['sortino']:.2f} trades={w['total_trades']}\")
"

# Aggregierten Gewinner (für alle Symbole gemeinsam) prüfen:
python3 -c "
import json
d = json.load(open('logs/tournament_$(date +%Y-%m-%d).json'))
ag = d['aggregate_winner']
print(f\"Winner: {ag['strategy']}, Wins: {ag['win_count']}, Median Sortino: {ag['median_sortino']:.4f}\")
print(f\"OOS eligible: {ag.get('oos_eligible', 'n/a')}\")
"
```

**Was tun, wenn kein Gewinner gefunden wird?**
Wenn für ein Symbol keine Strategie die Eligibilitätskriterien erfüllt, wird dieses Symbol **nicht gehandelt** (das ist gewollt, nicht ein Fehler). Im Live-Bot-Log erscheint dann: `No tournament winner for X.ETORO. Skipping.`

---

## 8. Precision-Tabelle (wichtig für korrekte Backtests)

Falsche Precision-Einstellungen können zu Fehlern im Backtest führen. Die aktuelle Tabelle:

| Kategorie | price_precision | size_precision |
|-----------|----------------|----------------|
| SHIB / PEPE | 8 | 8 |
| Krypto (BTC, ETH, …) | 2 | 8 |
| Forex / Rohstoffe | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> **Hinweis (Pitfall #14 — GELÖST):** `size_precision=2` für Aktien war in früheren Versionen `0`, was zu `ValueError`-Crashes bei kleinen Trade-Beträgen führte. Seit v2.0 ist dies behoben.

---

## 9. Warnung: Overfitting & Slippage

- **Overfitting:** Wenn Strategieparameter zu lange auf historischen Daten optimiert werden, passen sie zwar gut zur Vergangenheit, scheitern aber im Live-Trading. Empfehlung: Out-of-Sample-Tests nutzen (das System macht dies automatisch über das OOS-Gate).
- **Slippage:** Backtests gehen von perfekter Order-Ausführung aus. In der Realität schwanken Preise zwischen Signal und Ausführung. Rechne mit etwas schlechteren Live-Ergebnissen.

---

## Weiterführende Dokumente
- [`manuals/end_to_end_workflow.md`](./end_to_end_workflow.md) — Gesamte 5-Phasen-Pipeline
- [`manuals/deployment.md`](./deployment.md) — VM-Setup, systemd und Cron
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS im Detail
- [`manuals/run_bot_manual.md`](./run_bot_manual.md) — Tournament-Selektion und Log-Diagnose

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
