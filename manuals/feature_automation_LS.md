# Anforderungsspezifikation: Automatisierung der Momentum-LS Smart Portfolio Integration


---
**Implementierungsstand (Stand: 2026-05-17)**

| Phase | Status |
|-------|--------|
| Phase 1: Daily Orchestrator | ⬜ Ausstehend |
| Phase 2: Dynamisches Mapping | ✅ Umgesetzt |
| Phase 3: Auto-Fetch fehlender Daten | ✅ Umgesetzt |
| Phase 4: Logging & Alerting | ⬜ Ausstehend |

*(Status anhand des tatsächlichen Code-Stands im Repository)*
---

## 1. Einleitung & Zielsetzung
Ziel dieses Projekts ist die Überführung der derzeit manuell getriebenen „Momentum-LS Smart Portfolio Integration“ in einen vollständig automatisierten, fehlertoleranten Workflow (Set-and-Forget). Das System soll künftig in der Lage sein, Portfolio-Umschichtungen (Rebalancing) durch eToro selbstständig zu erkennen, neue Finanzinstrumente dynamisch zuzuordnen, fehlende historische Daten nachzuladen und das tägliche Strategie-Turnier sowie den Live-Bot autonom zu starten.

## 2. Detaillierte Anforderungen nach Umsetzungsphasen

### Phase 1: Der "Daily Orchestrator" (Master-Skript)
**Ziel:** Zusammenführung der sequentiellen Einzelschritte in einen überwachten, robusten Gesamtprozess.

* **REQ-1.1:** Es muss ein neues Master-Skript erstellt werden (präferiert `run_daily_orchestrator.py` anstelle eines reinen Bash-Skripts für besseres Error-Handling und Cross-Platform-Kompatibilität).
* **REQ-1.2:** Das Skript ruft nacheinander die Teilprozesse auf: 
    1. `momentum_ls_universe.py`
    2. (Neu) Daten-Abgleich und Auto-Fetch (siehe Phase 3)
    3. `momentum_ls_tournament.py`
    4. `momentum_ls_run.py`
* **REQ-1.3:** Strikte Abhängigkeitsprüfung: Jeder Schritt muss seinen Exit-Code (0 für Success, >0 für Error) an das Master-Skript übergeben. Schlägt ein Schritt fehl, wird die Kette sofort abgebrochen.
* **REQ-1.4:** Der Orchestrator muss so konzipiert sein, dass er problemlos in einen Scheduler (Linux Cronjob oder Windows Task Scheduler) eingehängt werden kann (z.B. Ausführung täglich um 06:00 UTC).

### Phase 2: Dynamisches Mapping neuer eToro-Assets (Auto-Discovery)
**Ziel:** Abschaffung der hartcodierten eToro-IDs und Automatisierung der Asset-Erkennung. **(Umgesetzt)**

* **REQ-2.1:** Die Datei `adapters/instrument_map.py` wird durch `dev_scripts/auto_map_insturments.py` aktualisiert.
* **REQ-2.2:** Das Skript gleicht die fehlenden IDs aus `momentum_ls.json` mit der eToro-Metadaten-API ab.
* **REQ-2.3:** Unbekannte IDs werden über die eToro API abgefragt, um ihr Ticker-Symbol zu ermitteln (z.B. `ADA`).
* **REQ-2.4:** Das neu gefundene Asset wird in das Format `SYMBOL.ETORO` konvertiert und **automatisch persistent** in die `adapters/instrument_map.py` geschrieben.

### Phase 3: Automatischer Download fehlender Historien-Daten
**Ziel:** Gewährleistung, dass für das Backtesting-Turnier lückenlose Parquet-Daten aller (auch neu hinzugekommener) Assets vorliegen. **(Umgesetzt)**

* **REQ-3.1:** Das Skript `dev_scripts/momentum_ls_fetch_candles_auto.py` liest die generierte Datei `data/universe/momentum_ls.json` ein.
* **REQ-3.2:** Für jedes enthaltene Symbol prüft das System, ob das Verzeichnis `data/nautilus/data/quote_tick/<SYMBOL>/` existiert und lädt fehlende Parquet-Daten in einem Delta-Update Modus herunter.
* **REQ-3.3:** Das Skript triggert automatisch den Download via `fetch_candles_chunk` und konsolidiert die Ergebnisse über verschiedene Timeframes.
* **REQ-3.4:** Erst wenn **alle** Symbole aus dem aktuellen Universum verifiziert und ihre Daten heruntergeladen wurden, darf Schritt 3 (Das Turnier) gestartet werden.

### Phase 4: Logging, Alerting & Error-Handling
**Ziel:** Transparenz und sofortige Alarmierung bei kritischen Systemzuständen.

* **REQ-4.1:** Alle `print()`-Ausgaben der Sub-Skripte müssen durch ein standardisiertes Python `logging`-Modul ersetzt oder in rotierende Logfiles (`logs/daily_run_<DATE>.log`) umgeleitet werden.
* **REQ-4.2:** Integration eines Webhook-Alertings (z.B. Telegram, Discord oder Slack).
* **REQ-4.3:** Es müssen Alarme gesendet werden bei:
    * Kritischen Fehlern (z.B. eToro API nicht erreichbar, Token abgelaufen).
    * Daten-Fehlern (Historische Daten konnten nicht geladen werden).
    * Turnier-Fehlern (Keine einzige Strategie erreicht einen Profit Factor > 1.5).
* **REQ-4.4:** (Optional) Nach erfolgreichem Durchlauf sendet das System eine Zusammenfassungsnachricht mit der Tabelle der Turnier-Gewinner und den neuen Kapitalallokationen.

## 3. Akzeptanzkriterien (Definition of Done)
* [x] Ein neues Asset im eToro Portfolio führt nicht mehr zum Abbruch oder zu einem manuellen Eingriff, sondern wird erkannt, geloggt, heruntergeladen und im Turnier berücksichtigt (via `auto_map_insturments.py` und `momentum_ls_fetch_candles_auto.py`).
* [ ] Das Gesamtsystem kann über einen einzigen Befehl (z.B. `python orchestrator.py`) gestartet werden und arbeitet alle Schritte sequentiell ab.
* [ ] Ein Fehlschlag (z.B. kein Internet) führt zum sicheren Abbruch des Workflows und feuert einen Alarm.
* [x] Alle Änderungen sind rückwärtskompatibel zum bestehenden Nautilus-Framework.

## 4. Empfohlene Umsetzungsreihenfolge
1. **Woche 1:** Umsetzung Phase 2 (Dynamisches Mapping JSON + Auto-Discovery in `momentum_ls_universe.py`). Dies löst das dringendste Problem.
2. **Woche 2:** Umsetzung Phase 1 & 3 (Master Orchestrator + Auto-Fetch der Daten).
3. **Woche 3:** Umsetzung Phase 4 (Error Handling, Webhooks und finale Tests auf einem Server / in einer CI/CD Pipeline).


---
## Weiterführende Dokumente
- `manuals/momentum_ls.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
