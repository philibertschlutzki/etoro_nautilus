Hier ist die vollständig überarbeitete, technisch absolut präzise und für Anfänger optimierte Version deines Handbuchs. Jedes Thema wurde in ein eigenes, klares Kapitel unterteilt, und alle fachlichen Details wurden exakt auf die Standalone-Pipeline-Architektur (v2.0) deines eToro Nautilus-Systems abgestimmt.

---

```markdown
# Strategie-Optimierung: Das End-to-End Handbuch für Anfänger und Operatoren

> **Dateiname:** manuals/strategie_optimierung.md  
> **System-Version:** v2.0 (Standalone `automation/` & Shift-Left Data Quality)  
> **Zielgruppe:** Operatoren, Einsteiger und AI-Agenten (Jules)  
> **Wichtigste Regel:** Für die reguläre Strategie-Optimierung muss **kein einziger Python-Code** angepasst werden. Alles wird deklarativ über die `.json`-Konfigurationsdateien im Ordner `automation/config/` gesteuert.

Die eToro Nautilus Plattform (v2.0) basiert auf einer automatisierten 5-Phasen-Pipeline. Die Optimierung setzt gezielt an Phase 3 (Matrix-Backtest) und Phase 4 (Tournament) an. Dieses Handbuch beschreibt den vollständigen Workflow, um Strategien robuster zu machen, statistische Fehler zu vermeiden und die Live-Performance zu maximieren.

---

## Kapitel 1: Wichtige Grundbegriffe einfach erklärt

Bevor wir in die Praxis einsteigen, findest du hier die mathematischen und strukturellen Kernkonzepte der eToro Nautilus Engine verständlich aufbereitet:

* **Matrix-Backtest (Phase 3):** Die historische Simulation aller aktiven Handelsstrategien über das gesamte definierte Instrumenten-Universum parallel. Es wird berechnet, wie jede Strategie auf jedem Symbol in der Vergangenheit abgeschnitten hätte.
* **Tournament (Phase 4):** Ein algorithmisches Auswahlverfahren. Es lässt alle Strategien für ein bestimmtes Symbol gegeneinander antreten und wählt anhand einer mathematischen Formel (Composite Score) den optimalen "Gewinner" pro Symbol aus.
* **In-Sample (IS):** Das "Trainings-Zeitfenster" (standardmäßig die letzten 120 Tage). Auf diesen historischen Daten sucht das Turniersystem nach der besten Strategie.
* **Out-of-Sample (OOS):** Das "Validierungs-Zeitfenster" (standardmäßig die darauffolgenden 30 Tage). Die Gewinner-Strategie wird auf diesen für sie völlig unbekannten Daten getestet, um echte Marktbedingungen zu simulieren.
* **Overfitting (Überanpassung):** Ein gefährlicher Zustand, bei dem eine Strategie zu perfekt an die historischen Daten der Vergangenheit (In-Sample) angepasst wurde. Sie erzielt dort überragende Ergebnisse, versagt jedoch im Live-Handel oder im Out-of-Sample-Fenster, da sie reines Marktrauschen gelernt hat.
* **OOS-Gate (Sicherheits-Interlock):** Ein Schutzmechanismus nach dem Prinzip "Fail-Closed". Ist die simulierte Rendite des Turniersiegers im Out-of-Sample-Zeitraum negativ, blockiert das Gate die Live-Bereitstellung (Phase 5), um echtes Kapital vor Overfitting-Verlusten zu schützen.
* **Slippage & Spreads:** In der Realität schwanken Preise zwischen der Signalgenerierung und der Orderausführung. eToro verlangt zudem einen Spread (Differenz zwischen Kauf- und Verkaufspreis). Das Backtesting-System modelliert dies über bid/ask-Preise und zusätzliche Gebührenkomponenten, um "papierne Gewinne" zu verhindern.

---

## Kapitel 2: Die tägliche Log-Analyse (Diagnose)

Jeder Optimierungszyklus startet mit der Sichtung der Reports im Verzeichnis `logs/`. Relevante Daten stehen in der `logs/orchestrator_YYYYMMDD.log` oder in den detaillierten Subprozess-Ausgaben (`logs/backtest_YYYYMMDD_HHMMSS.log`).

Achte bei der täglichen Analyse auf die folgenden drei Kernsymptome:

### Symptom A: Nicht-gehandelte Symbole ("Skipped Assets")
* **Log-Meldung:** `No tournament winner for X.ETORO. Skipping.`
* **Bedeutung:** Keine einzige Strategie konnte für dieses spezifische Instrument die harten Eligibilitätskriterien (z. B. Mindestanzahl an Trades, positive Mindestrendite) im In-Sample-Fenster erfüllen. Das Symbol wird für den aktuellen Tag komplett vom Live-Handel ausgeschlossen.
* **Ursache:** Die globalen Qualitätsfilter im System sind für die Volatilität oder das Liquiditätsprofil dieses Vermögenswerts zu restriktiv eingestellt.

### Symptom B: Häufige Blockaden durch das OOS-Gate
* **Log-Meldung:** Ein Turniersieger wird ermittelt, aber Phase 5 wird mit einer OOS-Warnung oder einem Block abgebrochen.
* **Bedeutung:** Die ermittelte Strategie war im Trainingszeitraum profitabel, bricht aber im ungesehenen Validierungszeitraum ein.
* **Ursache:** Die Strategie-Parameter leiden unter starkem Overfitting. Das Modell ist zu komplex und reagiert zu empfindlich auf kurzfristige Marktphasen.

### Symptom C: Marginale Filter-Fails bei selektiven Strategien
* **Log-Meldung:** Hochwertige Strategien tauchen in den finalen Gewinnerlisten bestimmter Symbole nicht auf, obwohl sie profitabel waren.
* **Bedeutung:** Komplexe Strategien wie die `ComboTrendVwapStrategy` oder die `VwapExhaustionStrategy` besitzen extrem strikte Einmalkriterien. Sie generieren naturgemäß seltener Signale, fliegen jedoch aufgrund starrer Mindest-Trade-Filter (z. B. harte Grenze bei exakt 20 Trades) aus der Wertung.
* **Ursache:** Strategien mit geringer Handelsfrequenz werden durch globale Standard-Turnierregeln benachteiligt.

---

## Kapitel 3: Die Konfigurations-Infrastruktur im Detail

Um die in Kapitel 2 diagnostizierten Ineffizienzen zu beheben, nutzt du die deklarativen Konfigurationsdateien in `automation/config/`. Jede Datei steuert ein präzises mathematisches oder strukturelles Zahnrad:

### 1. `automation/config/tournament.json`
Regelt die harten und weichen Filterkriterien für die Turnierteilnahme sowie die Score-Berechnung.
* `"min_trades"`: Mindestanzahl abgeschlossener Positionen im In-Sample-Fenster (Standard: 20).
* `"min_total_return"`: Harter Mindestgewinn (net-of-spread) zur Turnierzulassung (Standard: 0.005 = 0.5%).
* `"eligible_requires_all"`: Liste von Parametern, die *allesamt* zwingend erfüllt sein müssen (z. B. Mindest-Trefferquote, maximaler Drawdown, Erwartungswert).
* `"eligible_requires_any"`: Bedingungsliste, bei der *mindestens eine* erfüllt sein muss (z. B. `"min_sortino": 0.3` oder `"min_profit_factor": 1.1`).
* `"scoring"`: Bestimmt die Gewichtungskoeffizienten für den Composite Score zur Ermittlung des Gewinners:
  $$\text{Score} = \text{Sortino} \times 0.4 + \text{ProfitFactor} \times 0.3 + \text{WinRate} \times 0.2 - \text{MaxDrawdown} \times 0.1$$

### 2. `automation/config/strategies.json`
Steuert den Aktivierungsstatus der Algorithmen und ermöglicht individuelle Overrides.
* `"active"`: `true/false` schaltet eine Strategie global für den Matrix-Backtest an oder aus.
* `"params"`: Ermöglicht das gezielte Überschreiben von Standardparametern für eine spezifische Strategie, ohne den globalen Katalog zu ändern.
* `"tournament_overrides"`: Erlaubt das Lockern oder Verschärfen von Turnierkriterien für genau diese eine Strategie (wichtig für die Lösung von Symptom C).

### 3. `automation/config/strategy_defaults.json`
Enthält die mathematischen Standard-Eingabeparameter für die technischen Indikatoren aller Strategien (z. B. Längen von gleitenden Durchschnitten, Standardabweichungen für Bollinger Bänder, RSI-Schwellenwerte).

### 4. `automation/config/backtest.json`
Definiert die Rahmenbedingungen der Simulationsumgebung.
* `"walk_forward"`: Regelt die Fenstergrößen für das Testen. `"is_window_days"` bestimmt die In-Sample-Tage (z. B. 120), `"oos_window_days"` die Out-of-Sample-Tage (z. B. 30).
* `"spread_bps_by_asset_class"`: Definiert die künstliche Spread-Weitung in Basispunkten (bps) pro Anlageklasse (`CRYPTO`, `EQUITY`, `FOREX`, `COMMODITY`), um Zero-Spread-Artefakte zu eliminieren und Slippage realistisch abzubilden.

---

## Kapitel 4: Lösungsszenarien für die Praxis

Hier findest du die exakten Handlungsschritte für die in Kapitel 2 identifizierten Probleme:

### Szenario A: Core-Ticker fallen durch "Skipped Assets" aus
**Ziel:** Erhöhung der Systemabdeckung, damit wichtige Symbole wieder gehandelt werden.
1. Öffne `automation/config/tournament.json`.
2. Justiere die weichen Filter in der Sektion `"eligible_requires_any"`. Senke beispielsweise `"min_sortino"` vorsichtig von `0.3` auf `0.2` oder den `"min_profit_factor"` von `1.1` auf `1.05`.
3. Dadurch qualifizieren sich auch in schwierigen Marktphasen rentable Strategien für das Tournament, solange sie die harten Risikofilter (wie maximal erlaubten Drawdown) einhalten.

### Szenario B: Das OOS-Gate blockiert den Live-Deploy (Overfitting)
**Ziel:** Verringerung der Modellkomplexität für eine höhere Robustheit auf unbekannten Daten.
1. **Option 1 (Strengere Validierung):** Öffne `automation/config/backtest.json` und erhöhe das Validierungsfenster unter `"walk_forward"` -> `"oos_window_days"` von `30` auf `45` Tage. Dies zwingt die Strategie dazu, über einen längeren ungesehenen Zeitraum stabil zu performen.
2. **Option 2 (Glättung der Indikatoren):** Öffne `automation/config/strategy_defaults.json` und erhöhe die Periodenlängen der Trendindikatoren (z. B. einen SMA von `5` auf `10` oder `20` Phasen anheben). Dadurch reagiert die Strategie träger auf kurzfristiges Rauschen und fängt übergeordnete Trends ein.

### Szenario C: Strikte Strategien scheitern knapp am Frequenz-Filter
**Ziel:** Gezielte Zulassung von spezialisierten Low-Frequency-Strategien im Tournament.
1. Öffne `automation/config/strategies.json`.
2. Navigiere zum Objekt der betroffenen Strategie (z. B. `ComboTrendVwapStrategy`).
3. Inseriere oder modifiziere das Feld `"tournament_overrides"`, um die Mindestanzahl an Trades spezifisch für diese Strategie abzusenken:
```json
{
  "active": true,
  "strategy_module": "automation.strategies.tesla_combo_strategy",
  "strategy_class": "ComboTrendVwapStrategy",
  "config_class": "ComboTrendVwapConfig",
  "params": {},
  "tournament_overrides": {
    "min_trades": 10
  }
}

```

---

## Kapitel 5: Validierung mittels Dry-Run (Trockenlauf)

Nach jeder Modifikation der Konfigurationsdateien muss die Auswirkung überprüft werden. Um das Live-System nicht zu stören und unfertige Setups einzuspielen, wird die Pipeline im **Dry-Run-Modus** gestartet. Dies führt die Phasen 1 bis 4 vollständig aus (Datenvorbereitung, Backtesting, Turnierberechnung), unterbindet jedoch Phase 5 (Live-Deploy).

Führe aus dem Projekt-Wurzelverzeichnis (`PROJECT_ROOT`) auf deinem lokalen System folgenden Terminal-Befehl aus:

```bash
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

```

**Technische Details der Flags:**

* `--dry-run`: Schaltet die Sicherheitsverriegelung scharf. Es werden Reports und Tournament-JSONs geschrieben, aber es erfolgt kein Prozessstart des Live-Bots und keine Interaktion mit der eToro Execution API.
* `--skip-api-fetch`: Unterdrückt den zeitintensiven Download historischer Candlesticks über das eToro-API-Netzwerk. Die Engine greift direkt auf die hocheffizienten, lokalen PyArrow-Datenkataloge im Nautilus FSB(16)-Format unter `data/nautilus/` zurück.

---

## Kapitel 6: Mathematische Auswertung der Ergebnisse

Nach dem erfolgreichen Abschluss des Dry-Runs liegt die finale Auswertung als strukturierte JSON-Struktur unter `logs/tournament_YYYY-MM-DD.json` vor.

Verwende die folgenden nativen Einzeiler im Terminal, um eine fehlerfreie, aggregierte Auswertung direkt über die Python-Kommandozeile zu generieren:

### 1. Analyse der Gewinner pro Symbol

*Verifiziert, ob ehemals blockierte Symbole nun erfolgreich mit stabilen Strategien besetzt wurden:*

```bash
python3 -c "
import json, datetime
today = datetime.datetime.now().strftime('%Y-%m-%d')
try:
    d = json.load(open(f'logs/tournament_{today}.json'))
    for sym, w in d['per_symbol_winners'].items():
        print(f\"{sym:<30} {w['strategy']:<35} sortino={w['sortino']:.2f} trades={w['total_trades']}\")
except FileNotFoundError:
    print('Reportdatei für heute nicht gefunden. Wurde der Dry-Run fehlerfrei ausgeführt?')
"

```

### 2. Aggregierter Integritäts- und OOS-Check

*Der finale Qualitäts-Check für die Gesamtanlage:*

```bash
python3 -c "
import json, datetime
today = datetime.datetime.now().strftime('%Y-%m-%d')
try:
    d = json.load(open(f'logs/tournament_{today}.json'))
    ag = d['aggregate_winner']
    print(f\"Empfohlene Gesamtstrategie: {ag['strategy']}\")
    print(f\"Anzahl gewonnener Symbole:   {ag['win_count']}\")
    print(f\"Median In-Sample Sortino:    {ag['median_sortino']:.4f}\")
    print(f\"OOS-Gate passiert (Zulassung): {ag.get('oos_eligible', 'n/a')}\")
except Exception as e:
    print(f'Fehler beim Parsen der System-Metriken: {e}')
"

```

### Die drei Erfolgsindikatoren einer Optimierung:

Deine Parameter-Optimierung gilt als statistisch erfolgreich und freigabe-bereit, wenn im Terminal-Output:

1. **`OOS-Gate passiert (Zulassung)` exakt auf `True` steht.** (Absolute Bedingung. Bei `False` greift der Schutzschalter und verhindert den automatischen Bot-Start).
2. Das **`Median In-Sample Sortino`**-Verhältnis im Vergleich zum Vortag gestiegen ist (Verbesserung der risikobereinigten Rendite).
3. Die Anzahl der abgedeckten Instrumente (`win_count`) stabil geblieben oder gestiegen ist, da weniger Symbole aufgrund restriktiver Filter übersprungen wurden.

Wenn alle drei Bedingungen zutreffen, sind die modifizierten JSON-Konfigurationen produktionsbereit und können mittels Git committet und auf das produktive Cloud-VPS-System übertragen werden.

```
***

Dieses Dokument ist damit vollständig strukturiert, fachlich lückenlos an die v2.0-Architektur deines Repositories angepasst und als lesbares Handbuch einsatzbereit.

```
