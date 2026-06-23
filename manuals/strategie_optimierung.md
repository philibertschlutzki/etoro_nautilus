---

```markdown
# Strategie-Optimierung: Das End-to-End Handbuch für Anfänger und Operatoren

> **Dateiname:** manuals/strategie_optimierung.md  
> **System-Version:** v2.0 (Standalone `automation/` inklusive Per-Symbol Micro-Tuning)  
> **Zielgruppe:** Operatoren, Einsteiger und AI-Agenten (Jules)  
> **Wichtigste Regel:** Für die reguläre Strategie-Optimierung muss **kein einziger Python-Code** angepasst werden. Alles wird deklarativ über die `.json`-Konfigurationsdateien im Ordner `automation/config/` gesteuert oder über einfache Kommandozeilen-Befehle (CLI) ausgeführt.

Die eToro Nautilus Plattform (v2.0) basiert auf einer automatisierten 5-Phasen-Pipeline. Dieses Handbuch erklärt, wie du das System optimierst – von einfachen globalen Filter-Anpassungen bis hin zum hochmodernen **Per-Symbol Micro-Tuning** (Ansatz 4), bei dem Strategien für einzelne Assets maßgeschneidert werden, ohne ins "Overfitting" (Auswendiglernen) zu verfallen.

---

## Kapitel 1: Wichtige Grundbegriffe einfach erklärt

Bevor wir in die Praxis einsteigen, hier die wichtigsten Konzepte:

* **Matrix-Backtest & Tournament:** Das System testet historisch alle Strategien auf allen Instrumenten. Im "Turnier" gewinnt die Strategie, die den besten Risiko-Rendite-Score (Sortino Ratio etc.) auf einem bestimmten Instrument erzielt.
* **In-Sample (IS) & Out-of-Sample (OOS):** "IS" ist das Trainingsfenster (z.B. 120 Tage). "OOS" ist das Validierungsfenster (z.B. 30 Tage). Die Strategie wird auf "IS" trainiert und muss auf den ihr unbekannten "OOS"-Daten beweisen, dass sie funktioniert.
* **Overfitting (Überanpassung):** Ein gefährlicher Zustand, bei dem eine Strategie zu perfekt an die Vergangenheit angepasst wurde, aber in der Zukunft versagt.
* **Holdout-Fenster:** Ein streng geheimer, unberührter Datenbereich *nach* IS und OOS. Er wird **nur** als allerletzte Instanz beim Per-Symbol-Tuning verwendet, um zu beweisen, dass eine maßgeschneiderte Strategie wirklich besser ist.

### Die 3 Schutz-Gates des Per-Symbol-Tunings
Wenn wir Strategien für spezifische Symbole (z. B. nur für Tesla) optimieren, nutzt das System drei eiserne Tore (Gates), um uns vor Overfitting zu schützen:
1. **Gate 1 (Daten-Suffizienz):** Bevor überhaupt optimiert wird, prüft das System, ob das Symbol genug Historie (Kerzen/Bars) hat. Ein brandneuer Coin wird hier direkt abgelehnt.
2. **Gate 2 (Warm-Start & Shrinkage):** Die Optimierung startet nicht bei Null, sondern exakt bei den besten *globalen* Parametern. Sie darf sich nur leicht davon entfernen (Shrinkage-Strafe), andernfalls gibt es Punktabzug im Score.
3. **Gate 3 (Holdout Margin):** Der ultimative Test. Die neue, maßgeschneiderte Strategie muss die globale Standard-Strategie auf dem völlig ungesehenen Holdout-Datensatz um eine definierte Marge (z.B. +10%) schlagen. Nur dann wird ein "Proposal" (Vorschlag) erstellt.

---

## Kapitel 2: Die Konfigurations-Infrastruktur

Alle Optimierungen steuerst du über Dateien in `automation/config/`:

1. **`tournament.json`**: Regelt die harten Filter. 
   * `"min_trades"`: Mindestanzahl an Trades.
   * `"scoring"`: Gewichtung der Formel (z.B. Sortino x 0.4 + WinRate x 0.2).
2. **`strategies.json`**: Hier schaltest du Strategien an/aus (`"active": true`). Hier werden künftig auch die `"instrument_overrides"` (maßgeschneiderte Symbol-Parameter) eingetragen.
3. **`strategy_defaults.json`**: Die globalen Standard-Parameter für Indikatoren (z.B. SMA-Längen).
4. **`optimizer.json` (NEU)**: Das Herzstück des Micro-Tunings.
   * `"lambda_reg"`: Wie stark wird bestraft, wenn sich die Parameter zu weit vom globalen Standard entfernen? (Standard: z.B. 0.25).
   * `"promotion_margin"`: Um wie viel Prozent muss die Spezial-Strategie besser sein als die globale? (Standard: z.B. 0.10).
   * `"min_bars_per_param"`: Mindestanzahl an historischen Datenpunkten pro Parameter für Gate 1.

---

## Kapitel 3: Basis-Diagnose (Globale Parameter)

Wenn das Standard-System nicht gut läuft, schau in die Logs (`logs/orchestrator_YYYYMMDD.log`):

* **Symptom: "Skipped Assets" (Keine Trades auf einem Symbol):** Die globalen Filter in `tournament.json` sind zu streng. Senke vorsichtig den `"min_sortino"` oder `"min_profit_factor"`.
* **Symptom: OOS-Gate blockiert oft:** Deine globale Strategie ist overfitted. Erhöhe `"oos_window_days"` in `backtest.json` (z.B. von 30 auf 45 Tage), um die Strategie zu zwingen, länger stabil zu laufen.

---

## Kapitel 4: Der neue Workflow – Per-Symbol Micro-Tuning

Du hast eine Basis-Strategie (z. B. `SmaCrossoverStrategy`), die global gut läuft, aber du glaubst, dass Tesla (TSLA) andere gleitende Durchschnitte braucht als Gold (GOLD)? Dann nutzt du den Sweep-Orchestrator.

### Schritt 1: Den Sweep starten
Der Sweep-Orchestrator testet automatisch Kombinationen und jagt sie durch die 3 Schutz-Gates. Er führt niemals Live-Trades aus. Öffne dein Terminal und starte den Lauf:

```bash
python -m automation.optimizer.sweep --strategies SmaCrossoverStrategy --tier deployable --n-jobs 2

```

**Was bedeuten die Parameter?**

* `--strategies <Name>`: Welche Strategie soll optimiert werden? (Du kannst auch `all` für alle eingeben).
* `--tier deployable`: Optimiert *nur* Instrumente, auf denen diese Strategie ohnehin schon der Turniersieger ist (Tier A). Das spart enorm Rechenleistung. (Option: `all`).
* `--n-jobs 2`: Wie viele parallele Prozesse sollen genutzt werden? (2 bis 4 sind auf Standard-Rechnern gut).

### Schritt 2: Proposals (Vorschläge) prüfen

Das System arbeitet nun im Hintergrund. Strategien, die an Gate 1 oder Gate 3 scheitern, werden verworfen. Für Gewinner erstellt das System JSON-Dateien im Ordner `data/optimizer/`:
**Beispiel:** `proposal_SmaCrossoverStrategy_TSLA.ETORO.json`

Öffne diese Datei. Dort siehst du:

* `"status"`: Steht hoffentlich auf `READY_FOR_PR`.
* `"R_symbol"` vs `"R_global"`: Der Beweis, wie viel besser die neue Strategie auf den unbekannten Holdout-Daten abgeschnitten hat.
* `"proposed_instrument_override"`: Die neuen, maßgeschneiderten Parameter.

### Schritt 3: Overrides live schalten (Promotion)

Das System ändert niemals automatisch den Live-Code. Du hast die Kontrolle.
Um einen Vorschlag zu akzeptieren, öffne `automation/config/strategies.json`, suche deine Strategie und trage die neuen Werte unter `"instrument_overrides"` ein:

```json
{
  "strategy_class": "SmaCrossoverStrategy",
  "active": true,
  "params": {
    "sma_period": 20
  },
  "instrument_overrides": {
    "TSLA.ETORO": {
      "sma_period": 32,
      "cooldown_bars": 5
    }
  }
}

```

Ab dem nächsten Lauf nutzt Tesla die Werte 32 und 5, während alle anderen Instrumente beim globalen Standard (20) bleiben!

---

## Kapitel 5: Validierung mittels Dry-Run (Trockenlauf)

Bevor du Änderungen auf den Live-Server pusht, teste sie lokal. Der Dry-Run führt Backtests und Turniere aus, ohne echtes Geld zu riskieren oder Orders an eToro zu senden.

```bash
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

```

* `--skip-api-fetch`: Nutzt lokale, schnelle Festplattendaten, statt alles neu von eToro herunterzuladen.

**Auswertung des Dry-Runs im Terminal prüfen:**
Du kannst nach dem Trockenlauf dieses kleine Skript ins Terminal kopieren, um zu prüfen, ob alles geklappt hat:

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

**Wann ist die Optimierung erfolgreich?**

1. **OOS-Gate passiert** steht auf `True` (Live-Handel wäre erlaubt).
2. Der **Median Sortino** ist höher als vor deinen Änderungen.
3. Die **Anzahl gewonnener Symbole** ist gleich geblieben oder gestiegen.

Sind diese Punkte erfüllt, kannst du deine Änderungen (die angepasste `strategies.json`) ins Git-Repository pushen und live nehmen!

```

```
