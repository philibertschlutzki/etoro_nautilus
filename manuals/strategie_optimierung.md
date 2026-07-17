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

---

## Kapitel 6: Holdout-Signifikanz — die unbequeme Wahrheit über kurze Fenster (§Holdout-Signifikanz)

> **Kontext (Issue #624):** Der Sweep protokolliert beim Start eine `[#624] Holdout-Geometrie`-Zeile
> und verweist auf genau dieses Kapitel. Es beantwortet die Frage, die die Promotions-Entscheidung
> ehrlich macht: *Reicht ein 45-Tage-Holdout überhaupt aus, um eine 95‑%‑Aussage zu treffen?*

### 6.1 Worum es geht

Gate 3 promotet ein Symbol nur, wenn die maßgeschneiderte Strategie den globalen Standard auf dem
ungesehenen Holdout schlägt (`promotion_margin`) **und** die Deflation/PSR-Schwelle passiert. Die PSR
(Probabilistic Sharpe/Sortino Ratio) beziffert, mit welcher Wahrscheinlichkeit die *wahre* risiko-
adjustierte Rendite über einer Referenzschwelle liegt — sie ist ∈ [0, 1] und **annualisierungs-
invariant** (sie hängt von der per-Periode-Ratio ŜR und der Stichprobenlänge T ab, nicht von der
gewählten Skalierung). Die Schwelle im Repo ist `oos_min_psr = 0.75` (Eligibility-Gate) bzw. die
strengere DSR/PSR-Promotionslinie von **0.95** in der Confirm-Stufe.

Formel (López de Prado):

```
PSR(SR*) = Φ[ (ŜR − SR*) · √(T − 1) / √(1 − γ₃·ŜR + ((γ₄ − 1)/4)·ŜR²) ]
```

mit ŜR = per-Periode-Sortino, T = Anzahl der Holdout-Perioden (MTM-Bars), γ₃ = Schiefe, γ₄ =
(nicht-exzess) Kurtosis. `PSR(0)` ist die Wahrscheinlichkeit, dass die wahre Ratio > 0 ist.

### 6.2 Die harte Rechnung

Ein 45-Tage-Holdout liefert bei der aktuellen Katalog-/Bar-Geometrie **T ≈ 202** verwertbare
MTM-Perioden. Ein *gerade noch* promotionswürdiger Grenzkandidat hat eine per-Periode-Sortino von
etwa **ŜR ≈ 0.114**. Setzt man das ein (γ₃ = 0, γ₄ = 3, Gauß-Referenz):

| T (Holdout-Perioden) | z = ŜR·√(T−1) | PSR(0) = Φ(z) | ≥ 0.95 ? |
|----------------------|---------------|---------------|----------|
| **202** (heute)      | 1.611         | **0.9464**    | ✗        |
| 205                  | 1.622         | 0.9477        | ✗        |
| **211**              | 1.646         | **0.9501**    | ✓ (grade) |
| 250                  | 1.793         | 0.9635        | ✓        |
| 300                  | 1.964         | 0.9753        | ✓        |

**Kernaussage:** Bei ŜR ≈ 0.114 und T = 202 erreicht selbst der beste Grenzkandidat nur
**PSR(0) = 0.9464 < 0.95**. Um die 0.95-Linie zu überschreiten, braucht es **T ≥ 211** Perioden —
oder, äquivalent bei T = 202, eine per-Periode-Sortino von **≥ 0.116** (statt 0.114). Die fehlende
Signifikanz ist also kein Software-Fehler, sondern eine **geometrische Eigenschaft des zu kurzen
Fensters**. (Vor den Fixes #611/#618/#619 war die PSR/Deflation zudem auf der falschen — Reward- statt
Sortino-Skala und über die falsche Kohorte — berechnet; seither ist die Zahl korrekt, aber eben
ehrlich zu klein.)

### 6.3 Die getroffene Entscheidung (bewusst, dokumentiert, umkehrbar)

Wir haben **drei** Optionen abgewogen und uns bewusst für die erste entschieden:

1. **Die 0.95-Schwelle ehrlich beibehalten (gewählt).** Wir senken die Promotionslinie **nicht**, nur
   damit die aktuellen Daten sie passieren. Konsequenz: Grenzkandidaten um ŜR ≈ 0.114 werden mit der
   heutigen Geometrie **korrekt zurückgehalten (HOLD, nicht promotet)**. Das ist das *dokumentierte,
   akzeptierte* Verhalten — lieber ein ehrliches „noch nicht signifikant" als ein promotetes Overfit.
2. **Mehr Historie backfillen (bevorzugter Auflösungspfad).** Sobald der Katalog so weit zurückreicht,
   dass der Holdout **T ≥ 211** Perioden trägt, passiert derselbe echte Edge die Schwelle — ohne dass
   irgendeine Latte gesenkt wurde. Das ist der einzige Weg, der Signifikanz *gewinnt* statt sie
   *wegzudefinieren*.
3. **`oos_min_psr` / die Promotionslinie explizit senken (nur als bewusster Operator-Eingriff).**
   Technisch möglich über `tournament.json`, aber **nur** mit verstandener Konsequenz: jede Absenkung
   erhöht die Typ-I-Fehlerrate (falsch promotete Strategien) direkt und quantifizierbar. Kein
   stillschweigender Default.

### 6.4 Warum das trotzdem tragbar ist

Die 0.95-PSR ist nicht die einzige Verteidigungslinie. Der akzeptierte Rest-Typ-I-Fehler wird durch
zwei orthogonale Mechanismen beschränkt, die mit #611/#618/#619 scharf geschaltet wurden:

* **DSR-Deflation über die *eligible* Kohorte (#611/#618).** Die Deflated Sharpe/Sortino Ratio zieht
  die Multiple-Testing-Latte `SR₀ = √V[ŜR]·E[max_N]` ab — je mehr Trials, desto höher die Latte. Die
  Kohorte sind ausschließlich die *eligiblen* Trials (nicht die Bernoulli-Reward-Skala von früher).
* **CPCV/PBO-Hard-Stop (#619).** Die Probability of Backtest Overfitting aus Combinatorial-Purged-CV
  ist ein von T unabhängiges Overfit-Signal; ein zu hoher PBO blockt die Promotion
  (`REJECTED_SELECTION_OVERFIT`), selbst wenn die PSR knapp passieren würde.
* **Familienweise Zahl (#625).** Da je Symbol mehrere Strategien-Studies konkurrieren, wird die
  familienweise `deflation_n_family` (Σ eligibler Trials über die Studies) telemetriert — die
  konservative Obergrenze der tatsächlichen Multiple-Testing-Last.

### 6.5 Was der Operator konkret tun sollte

* **Beim Sweep-Start** die `[#624] Holdout-Geometrie`-Logzeile lesen: sie nennt `required_span_days`
  und die Fenster-Zerlegung (is + embargo + splits×oos + holdout). Ist der Katalog knapp, ist ein
  HOLD kein Alarm.
* **Ein HOLD an der 0.95-Linie** bedeutet: *nicht* die Schwelle senken, sondern **Historie
  nachladen**, bis T ≥ 211, und den Sweep erneut fahren.
* **Eine bewusste Absenkung** von `oos_min_psr` gehört in einen dokumentierten PR mit expliziter
  Nennung des in Kauf genommenen Typ-I-Fehlers — niemals als stiller Config-Tweak.

---

## Kapitel 7: Re-Run-Runbook nach einem Eligibility-/Gate-Kalibrier-Katalog (Issue #672)

Ein Katalog von Gate-/Kalibrierungs-Fixes (z. B. #663–#672) verschiebt potenziell die **effektive
OOS-Eligibility-Definition** — welche Trials überhaupt als „feasible" gelten. Alt-Trials in
bestehenden SQLite-Studies wurden unter der ALTEN Semantik bewertet; würden sie mit neuen Trials
gemischt, grundieren sie den TPE-Posterior mit inkommensurablen Rewards (`REJECT_STALE_STUDY_SEMANTICS`).

**Merge-Reihenfolge vor jedem Re-Run:**

1. **Alle Gate-/Kalibrierungs-Fixes zuerst mergen** (z. B. Kohorte A: Selektions-Robustheit; Kohorte
   B: Gate-Kalibrierung/Redundanz; Kohorte C: Suchraum). Telemetrie-/Confirm-only-Fixes (kein
   gespeicherter Trial-Reward betroffen) erzwingen für sich genommen **keinen** Versions-Bump.
2. **`reward_semantics_version` bumpen** (`automation/config/optimizer.json`), sobald **mindestens
   ein** eligibility-veränderndes Issue (ein Default-Gate-Codepfad-Wechsel, kein reines Opt-in)
   gemergt ist. Den neuen Changelog-Eintrag im `_schema`-Docstring ergänzen (welche Issues die
   Semantik brechen, analog den Vorversionen).
3. **Als LETZTE Aktion vor dem Re-Run:** alle Per-Symbol-SQLite-Studies löschen
   (`{WORK}/sweep/*.db` bzw. der konfigurierte `storage_url`-Pfad). Der bestehende
   `_check_reward_semantics_version`-Guard (#410/#468/#575/#658/#672/#686) lehnt eine geladene Study
   mit älterer/keiner Version ohnehin fail-loud ab (`REJECT_STALE_STUDY_SEMANTICS`) — der Purge ist
   die aufgeräumte, geplante Variante desselben Ergebnisses, statt es dem ersten Trial jeder Study zu
   überlassen. Issue #686 — statt N einzelne Sweep-Starts nacheinander fail-loud abbrechen zu lassen,
   führt ein Bulk-Purge-Werkzeug den Schritt in einem Durchgang aus:
   `python -m automation.optimizer.purge_stale_studies --dry-run` (Vorschau) gefolgt von
   `python -m automation.optimizer.purge_stale_studies` (löscht tatsächlich).
4. **Erst danach** den Sweep erneut starten (`python -m automation.optimizer.sweep ...`).

**Warum genau diese Reihenfolge:** Ein Purge VOR dem letzten Gate-Fix würde eine frische Study unter
einer noch nicht vollständigen Gate-Semantik neu befüllen — der zweite, spätere Fix würde dieselbe
Study dann erneut als stale erkennen und ein zweites Mal purgen (doppelte Rechenzeit). Der Purge als
**letzter** Schritt garantiert, dass jede neu angelegte Study von Anfang an unter der finalen,
vollständigen Semantik des gesamten Katalogs läuft.

