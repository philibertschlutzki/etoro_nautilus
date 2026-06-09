# Neue Instrumente hinzufügen (v2.1)

Diese Anleitung erklärt detailliert, wie du neue Aktien, Kryptowährungen oder andere Instrumente in das eToro-Nautilus System einbindest. 

Besonderes Augenmerk liegt auf **Kryptowährungen** und **Instrumenten außerhalb des verfolgten Smart Portfolios**, da diese manuelle Schritte erfordern, um Fehler im Rust-Backend zu vermeiden.

---

## Inhaltsverzeichnis
1. [Der Standardfall: Vollautomatische Integration (Smart Portfolio)](#1-der-standardfall-vollautomatische-integration)
2. [Ausnahme A: Assets außerhalb des Smart Portfolios manuell hinzufügen](#2-ausnahme-a-assets-außerhalb-des-smart-portfolios-manuell-hinzufügen)
3. [Ausnahme B: Kryptowährungen einbinden (Kritisch!)](#3-ausnahme-b-kryptowährungen-einbinden-kritisch)
4. [Schritt-für-Schritt: Datenbeschaffung & Backtest](#4-schritt-für-schritt-datenbeschaffung--backtest)
5. [Häufige Fehlerquellen (Troubleshooting)](#5-häufige-fehlerquellen-troubleshooting)

---

## 1. Der Standardfall: Vollautomatische Integration

Wenn das neue Instrument bereits Teil des eToro Smart Portfolios ist (das du über `MOMENTUM_LS_USERNAME` in der `.env` verfolgst), musst du **nichts** manuell tun.

Der tägliche Orchestrator (`automation/daily_orchestrator.py`) erledigt alles automatisch:
1. **Phase 1:** `universe_fetcher.py` erkennt das neue Asset und trägt es ein.
2. **Phase 2d:** Der Orchestrator merkt, dass historische Daten fehlen und führt automatisch den `historical_fetcher.py` aus.
3. **Phase 3-5:** Das Asset wird sofort ins Tournament aufgenommen und bei Erfolg live gehandelt.

---

## 2. Ausnahme A: Assets außerhalb des Smart Portfolios manuell hinzufügen

Wenn du ein Instrument handeln oder backtesten möchtest, das **nicht** im kopierten Smart Portfolio enthalten ist, schlägt die automatische Erkennung fehl. Du musst das Instrument dem System manuell bekannt machen.

### Schritt 1: eToro Instrument-ID herausfinden
Jedes Asset auf eToro hat eine eindeutige numerische ID. Diese ist für die API zwingend erforderlich.

1. Öffne die Datei `dev_scripts/get_instruments_id.py` in deinem Code-Editor.
2. Suche ganz am Ende der Datei den Testaufruf und trage den gewünschten Ticker (z. B. "AAPL") ein:
   ```python
   if __name__ == "__main__":
       get_etoro_instrument_id("AAPL")  # ← Deinen gewünschten Ticker hier eintragen

```

3. Öffne dein Terminal im Projekt-Root-Verzeichnis und führe das Skript aus:
```bash
python3 dev_scripts/get_instruments_id.py

```


4. Das Terminal gibt nun die ID aus (z. B. `1001`). Notiere dir diese Zahl.

### Schritt 2: Instrument in der Instrument-Map registrieren

Die zentrale Zuordnung zwischen eToro-ID und dem Nautilus-System passiert in der Datei `automation/adapters/instrument_map.py`.

1. Öffne die Datei `automation/adapters/instrument_map.py`.
2. Füge die neue ID und das Symbol im Dictionary `ETORO_INSTRUMENTS` hinzu.

**Wichtige technische Syntaxregels:**

* **String-Zwang für IDs:** Die eToro ID muss als String (in Anführungszeichen) definiert werden (z. B. `"1001"`), da sie als Schlüssel im Dictionary dient.
* **Suffix-Zwang:** Das Nautilus-Symbol **muss** zwingend auf `.ETORO` enden (z. B. `"AAPL.ETORO"`), um ein korrektes Routing innerhalb der Execution-Engine zu gewährleisten.
* **Komma-Regel:** Jede Zeile im Python-Dictionary muss mit einem Komma abgeschlossen werden.

*Beispiel:*

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "1012": "CAT.ETORO",
    "1001": "AAPL.ETORO",  # ← Neues Instrument manuell hinzugefügt
}

```

Nachdem du die Datei gespeichert hast, fahre mit [Abschnitt 4](https://www.google.com/search?q=%234-schritt-f%C3%BCr-schritt-datenbeschaffung--backtest) fort.

---

## 3. Ausnahme B: Kryptowährungen einbinden (Kritisch!)

Kryptowährungen (wie BTC, ETH, SOL, SHIBxM) sind ein extremer Sonderfall. eToro behandelt Krypto im Order-Routing und bei den minimalen Lot-Sizes (Nachkommastellen bzw. `size_precision`) fundamental anders als Aktien.

> ⚠️ **ACHTUNG:** Wenn ein Krypto-Asset nicht explizit registriert wird, wendet das System die Standard-Aktien-Heuristik (`size_precision=2`) an. Das führt unweigerlich zu **Fatalen Crashes im Rust-Backend** von Nautilus, wenn eine Order über z. B. `0.00005 BTC` platziert werden soll.

### Schritt 1: ID und Map registrieren (wie bei Aktien)

Führe zuerst die Schritte aus Abschnitt 2 aus, um die ID herauszufinden und sie in das `ETORO_INSTRUMENTS`-Dictionary in `automation/adapters/instrument_map.py` einzutragen (z. B. `"100063": "SOL.ETORO"`).

### Schritt 2: In Krypto-Set der Hilfsfunktionen eintragen

Das System ermittelt die Nachkommastellen über `automation/utils.py`. Hier muss das Basis-Symbol hinterlegt werden.

1. Öffne die Datei `automation/utils.py`.
2. Suche nach der Variable `_CRYPTO_SYMBOLS` (ein `frozenset`).
3. Füge das **Basis-Symbol** (ohne das Suffix `.ETORO`) der Menge hinzu.

*Beispiel:*

```python
_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "ADA", "DOGE", "SOL", "XRP", "AVAX",
    "HYPE", "ONDO", "SHIBxM", "AERO", "PEPExM",
    "BONK",  # ← Neues Krypto-Basis-Symbol hinzugefügt
})

```

### Schritt 3: Die Price Precision verstehen (Die Fallback-Regeln)

Über die interne Funktion `_fallback_precisions(symbol)` steuert das System die Nachkommastellen automatisch, falls die eToro-API oder Parquet-Metadaten unvollständig sind:

* **Normale Kryptowährungen (BTC, ETH, SOL etc.):** Werden automatisch mit einer Preiskonformität von `price_precision=2` und einer Positionsgrößenkonformität von `size_precision=8` eingestuft.
* **Meme-Coins (SHIB, PEPE etc.):** Da diese Token Bruchteile von Cents kosten, greift eine Namensprüfung (`"SHIB" in sym or "PEPE" in sym`), welche die Precision hart auf `price_precision=8` und `size_precision=8` forciert. Wenn du einen neuen Meme-Coin einpflegst, stelle sicher, dass sein Symbol entweder "SHIB" oder "PEPE" enthält oder erweitere die `if`-Bedingung in `automation/utils.py`.

---

## 4. Schritt-für-Schritt: Datenbeschaffung & Backtest

Egal ob manuell hinzugefügte Aktie oder Krypto – nachdem die Code-Einträge gemacht sind, benötigt das System historische Marktdaten.

### Historische Daten laden

Obwohl der `daily_orchestrator.py` fehlende Daten in Phase 2d selbst bemerkt, ist es bei manuellen Setups dringend empfohlen, den initialen Download einmal selbst anzustoßen, um Syntax- oder API-Fehler sofort im Terminal zu sehen.

Öffne dein Terminal und führe aus:

```bash
python3 automation/historical_fetcher.py --months 12

```

Das Skript lädt nun die Tick-Daten für alle in `instrument_map.py` registrierten Symbole, verpackt sie als `FixedSizeBinary(16)` Parquet-Dateien und legt sie unter `data/nautilus/data/quote_tick/{SYMBOL}/data.parquet` ab.

### Dry-Run testen

Um sicherzugehen, dass die Engine fehlerfrei mit dem neuen Asset umgehen kann, starte den Orchestrator im "Trockenlauf" (Dry-Run). Es werden keine echten Orders platziert.

```bash
python3 automation/daily_orchestrator.py --dry-run

```

Prüfe das angelegte Log (`logs/orchestrator_YYYYMMDD.log`), ob dein neues Symbol im Bereich "Phase 3+4: Matrix-Backtesting & Tournament" sauber verarbeitet wurde.

---

## 5. Häufige Fehlerquellen (Troubleshooting)

Wenn dein Instrument nicht gehandelt wird, obwohl es hinzugefügt wurde:

| Problem | Ursache & Lösung |
| --- | --- |
| **`KeyError: 'AAPL.ETORO' not in ETORO_INSTRUMENTS`** | Du hast die eToro-ID nicht in `automation/adapters/instrument_map.py` eingetragen oder das `.ETORO` Suffix beim Symbol vergessen. |
| **`No parquet data for AAPL.ETORO`** | Du hast vergessen, die historischen Daten zu laden. Führe manuell `python3 automation/historical_fetcher.py --months 12` aus. |
| **Asset wird im Tournament ignoriert** | Keine Sorge, das ist oft kein Bug! Das Instrument hat schlichtweg die harten Out-of-Sample (OOS) Gating-Kriterien (z. B. Sortino-Ratio, Max Drawdown) in der Konfiguration nicht bestanden. Es wird erst live gehandelt, wenn das System es als sicher und profitabel einstuft. |
| **Rust-Backend Crash bei Krypto (`Decimal Precision Error`)** | Du hast ein Krypto-Asset hinzugefügt, es aber nicht in die `_CRYPTO_SYMBOLS` Liste in `automation/utils.py` eingetragen. Das System versucht, Orders mit Aktien-Precision (`size_precision=2`) abzusenden, was fehlschlägt. |
| **SyntaxError beim Start des Bots** | Du hast wahrscheinlich ein Komma oder ein Anführungszeichen in `automation/adapters/instrument_map.py` falsch gesetzt. Python verzeiht keine unvollständigen Dictionaries. |

---

*Zuletzt aktualisiert für Orchestrator v2.0+ und Nautilus-Integration.*
---
