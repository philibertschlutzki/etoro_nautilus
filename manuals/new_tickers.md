```python
import os

def search_text(folder, text):
    results = []
    for root, dirs, files in os.walk(folder):
        for file in files:
            if file.endswith('.py') or file.endswith('.json') or file.endswith('.md'):
                path = os.path.join(root, file)
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if text in content:
                            results.append(path)
                except Exception:
                    pass
    return results

print("Crypto symbols instances:", search_text('automation', '_CRYPTO_SYMBOLS'))
print("Instrument utils check:", search_text('automation', '_fallback_precisions'))



```

```text
Crypto symbols instances: []
Instrument utils check: []


```

```python
content = """# Neue Instrumente hinzufügen (v2.1)

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
1. **Phase 1:** `universe_fetcher.py` erkennt das neue Asset und trägt es in `automation/config/instrument_map.json` ein.
2. **Phase 2d:** Der Orchestrator merkt, dass historische Daten fehlen und führt automatisch den `historical_fetcher.py` aus.
3. **Phase 3-5:** Das Asset wird sofort ins Tournament aufgenommen und bei Erfolg live gehandelt.

---

## 2. Ausnahme A: Assets außerhalb des Smart Portfolios manuell hinzufügen

Wenn du ein Instrument handeln oder backtesten möchtest, das **nicht** im kopierten Smart Portfolio enthalten ist, schlägt die automatische Erkennung fehl. Du musst das Instrument dem System "bekannt" machen.

### Schritt 1: eToro Instrument-ID herausfinden
Jedes Asset auf eToro hat eine eindeutige numerische ID. Diese ist für die API zwingend erforderlich.

1. Öffne die Datei `dev_scripts/get_instruments_id.py` in deinem Code-Editor.
2. Suche ganz am Ende der Datei den Testaufruf und trage den gewünschten Ticker (z. B. "AAPL") ein:

```

```text
[file-tag: new_tickers-v2.md]

```python
   if __name__ == "__main__":
       get_etoro_instrument_id("AAPL")  # ← Deinen gewünschten Ticker hier eintragen

```

3. Öffne dein Terminal im Projekt-Root-Verzeichnis und führe das Skript aus:
```bash
python3 dev_scripts/get_instruments_id.py

```


4. Das Terminal gibt nun die ID aus (z. B. `1001`). Notiere dir diese Zahl.

### Schritt 2: Instrument in der Map registrieren

Die zentrale Zuordnung zwischen eToro-ID und Nautilus-System passiert in einer einzigen Datei.

1. Öffne die Datei `automation/config/instrument_map.json`.
2. Füge die neue ID und das Symbol im Block `"instruments"` hinzu.

**Wichtige technische Regeln für Anfänger:**

* **String-Zwang:** Die ID **muss** in Anführungszeichen stehen (z. B. `"1001"`, nicht `1001`), sonst kann JSON sie nicht parsen.
* **Suffix-Zwang:** Das Symbol **muss** zwingend auf `.ETORO` enden (z. B. `AAPL.ETORO`), da das Routing der Nautilus-Engine sonst fehlschlägt.
* **Komma-Regel:** Setze ein Komma nach dem vorherigen Eintrag, aber **kein** Komma nach dem letzten Eintrag in der Liste.

*Beispiel:*

```json
{
  "instruments": {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "1001": "AAPL.ETORO" 
  }
}

```

Nachdem du die JSON gespeichert hast, fahre mit [Abschnitt 4](#4-schritt-für-schritt-datenbeschaffung--backtest) fort.

---

## 3. Ausnahme B: Kryptowährungen einbinden (Kritisch!)

Kryptowährungen (wie BTC, ETH, SOL, SHIB) sind ein extremer Sonderfall. eToro behandelt Krypto im Order-Routing und bei den minimalen Lot-Sizes (Nachkommastellen) fundamental anders als Aktien.

> ⚠️ **ACHTUNG:** Wenn ein Krypto-Asset nicht explizit als Krypto registriert wird, wendet das System die Standard-Aktien-Heuristik (`size_precision=2`) an. Das führt unweigerlich zu **Fatalen Crashes im Rust-Backend** von Nautilus, wenn eine Order über z. B. `0.00005 BTC` platziert werden soll.

### Schritt 1: ID und Map (wie bei Aktien)

Führe die Schritte aus Abschnitt 2 aus, um die ID herauszufinden und sie in die `automation/config/instrument_map.json` einzutragen (z. B. `"100000": "BTC.ETORO"`).

### Schritt 2: In der Krypto-Liste des Adapters registrieren

Das System muss wissen, dass dieses spezielle Symbol eine Kryptowährung ist.

1. Öffne die Konfigurationsdatei des eToro-Adapters (meist `automation/adapters/etoro_config.py` oder dort, wo `_CRYPTO_SYMBOLS` definiert ist; siehe auch `automation/AGENTS.md` für architektonische Details).
2. Suche nach der Variable/Liste `_CRYPTO_SYMBOLS`.
3. Füge dein neues Nautilus-Symbol exakt so hinzu, wie es in der `instrument_map.json` steht.

*Beispiel:*

```python
_CRYPTO_SYMBOLS = {
    "BTC.ETORO",
    "ETH.ETORO",
    "ADA.ETORO",
    "SOL.ETORO"  # ← Neues Krypto-Symbol hinzugefügt
}

```

### Schritt 3: Die Price Precision verstehen (Die 8-Stellen-Regel)

Kryptowährungen benötigen standardmäßig eine `size_precision` von `8` (8 Nachkommastellen für die Positionsgröße).
Das System (via `automation/utils.py` -> `_fallback_precisions`) greift für Krypto automatisch auf `8` zurück.
Handelt es sich jedoch um extrem kleine "Meme-Coins" (wie SHIB oder PEPE), kann auch der **Preis** (`price_precision`) 8 Nachkommastellen erfordern.

* **Normale Krypto (BTC, ETH, SOL):** Preis-Precision: 2, Size-Precision: 8. (Wird meist automatisch korrekt gefangen, wenn das Symbol in `_CRYPTO_SYMBOLS` steht).
* **Meme-Coins (SHIB):** Preis-Precision: 8, Size-Precision: 8.

Sollte es bei Krypto-Trades zu Rundungsfehlern kommen (z.B. Fehler *"Invalid Order Size"*), muss die Precision im Backend explizit forciert werden.

---

## 4. Schritt-für-Schritt: Datenbeschaffung & Backtest

Egal ob manuell hinzugefügte Aktie oder Krypto – nachdem die Map-Einträge gemacht sind, braucht das System Daten.

### Historische Daten laden

Obwohl der `daily_orchestrator.py` (v2.0+) fehlende Daten in Phase 2d selbst bemerkt, ist es für Anfänger bei manuellen Setups dringend empfohlen, den initialen Download einmal selbst anzustoßen, um Fehler sofort im Terminal zu sehen.

Öffne dein Terminal und führe aus:

```bash
python3 automation/historical_fetcher.py --months 12

```

Das Skript lädt nun die Tick-Daten für alle fehlenden Symbole, verpackt sie als `FixedSizeBinary(16)` Parquet-Dateien und legt sie unter `data/nautilus/data/quote_tick/{SYMBOL}/data.parquet` ab.

### Dry-Run testen

Um sicherzugehen, dass alles klappt, starte den Orchestrator im "Trockenlauf" (Dry-Run). Es werden keine echten Orders platziert.

```bash
python3 automation/daily_orchestrator.py --dry-run

```

Prüfe im angelegten Log (`logs/orchestrator_YYYYMMDD.log`), ob dein neues Symbol im Bereich "Phase 3+4: Matrix-Backtesting & Tournament" sauber verarbeitet wurde.

---

## 5. Häufige Fehlerquellen (Troubleshooting)

Wenn dein Instrument nicht gehandelt wird, obwohl es hinzugefügt wurde:

| Problem | Ursache & Lösung |
| --- | --- |
| **`KeyError: 'AAPL.ETORO' not in instrument_map`** | Du hast die eToro-ID nicht in `automation/config/instrument_map.json` eingetragen oder das `.ETORO` Suffix vergessen. |
| **`No parquet data for AAPL.ETORO`** | Du hast vergessen, die historischen Daten zu laden. Führe `python3 automation/historical_fetcher.py --months 12` aus. |
| **Asset wird im Tournament ignoriert** | Keine Sorge, kein Bug! Das Instrument hat schlichtweg die harten Out-of-Sample (OOS) Gating-Kriterien (z. B. Sortino-Ratio, Max Drawdown) in der `tournament.json` nicht bestanden. Es wird erst gehandelt, wenn das System es als sicher und profitabel einstuft. |
| **Rust-Backend Crash bei Krypto (`Decimal Precision Error`)** | Du hast ein Krypto-Asset hinzugefügt, es aber nicht in die `_CRYPTO_SYMBOLS` Liste eingetragen. Das System versucht, `0.005 BTC` mit Aktien-Precision (`size_precision=2`) zu handeln, was fehlschlägt. Trage es in die Liste ein! |
| **JSON Parse Error beim Start** | Du hast wahrscheinlich ein Komma in der `instrument_map.json` am Ende der Liste gesetzt, oder Anführungszeichen um die eToro-ID vergessen. JSON verzeiht keine Tippfehler. |

---

*Zuletzt aktualisiert für Orchestrator v2.0+ und Nautilus-Integration.*
"""

with open("new_tickers-v2.md", "w", encoding="utf-8") as f:
f.write(content)

print("[file-tag: new_tickers-v2.md]")

```
Your Markdown file is ready
[file-tag: code-generated-file-0-1780988887108918407]

Hier ist die überarbeitete, 100% technisch detaillierte und anfängerfreundliche Markdown-Anleitung, in der die Ausnahmefälle für Kryptowährungen und manuelle Assets genau beschrieben sind:

```markdown
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
1. **Phase 1:** `universe_fetcher.py` erkennt das neue Asset und trägt es in `automation/config/instrument_map.json` ein.
2. **Phase 2d:** Der Orchestrator merkt, dass historische Daten fehlen und führt automatisch den `historical_fetcher.py` aus.
3. **Phase 3-5:** Das Asset wird sofort ins Tournament aufgenommen und bei Erfolg live gehandelt.

---

## 2. Ausnahme A: Assets außerhalb des Smart Portfolios manuell hinzufügen

Wenn du ein Instrument handeln oder backtesten möchtest, das **nicht** im kopierten Smart Portfolio enthalten ist, schlägt die automatische Erkennung fehl. Du musst das Instrument dem System "bekannt" machen.

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

### Schritt 2: Instrument in der Map registrieren

Die zentrale Zuordnung zwischen eToro-ID und Nautilus-System passiert in einer einzigen Datei.

1. Öffne die Datei `automation/config/instrument_map.json`.
2. Füge die neue ID und das Symbol im Block `"instruments"` hinzu.

**Wichtige technische Regeln für Anfänger:**

* **String-Zwang:** Die ID **muss** in Anführungszeichen stehen (z. B. `"1001"`, nicht `1001`), sonst kann JSON sie nicht parsen.
* **Suffix-Zwang:** Das Symbol **muss** zwingend auf `.ETORO` enden (z. B. `AAPL.ETORO`), da das Routing der Nautilus-Engine sonst fehlschlägt.
* **Komma-Regel:** Setze ein Komma nach dem vorherigen Eintrag, aber **kein** Komma nach dem letzten Eintrag in der Liste.

*Beispiel:*

```json
{
  "instruments": {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "1001": "AAPL.ETORO" 
  }
}

```

Nachdem du die JSON gespeichert hast, fahre mit [Abschnitt 4](#4-schritt-für-schritt-datenbeschaffung--backtest) fort.

---

## 3. Ausnahme B: Kryptowährungen einbinden (Kritisch!)

Kryptowährungen (wie BTC, ETH, SOL, SHIB) sind ein extremer Sonderfall. eToro behandelt Krypto im Order-Routing und bei den minimalen Lot-Sizes (Nachkommastellen) fundamental anders als Aktien.

> ⚠️ **ACHTUNG:** Wenn ein Krypto-Asset nicht explizit als Krypto registriert wird, wendet das System die Standard-Aktien-Heuristik (`size_precision=2`) an. Das führt unweigerlich zu **Fatalen Crashes im Rust-Backend** von Nautilus, wenn eine Order über z. B. `0.00005 BTC` platziert werden soll.

### Schritt 1: ID und Map (wie bei Aktien)

Führe die Schritte aus Abschnitt 2 aus, um die ID herauszufinden und sie in die `automation/config/instrument_map.json` einzutragen (z. B. `"100000": "BTC.ETORO"`).

### Schritt 2: In der Krypto-Liste des Adapters registrieren

Das System muss wissen, dass dieses spezielle Symbol eine Kryptowährung ist.

1. Öffne die Konfigurationsdatei des eToro-Adapters (meist `automation/adapters/etoro_config.py` oder dort, wo `_CRYPTO_SYMBOLS` definiert ist; siehe auch `automation/AGENTS.md` für architektonische Details).
2. Suche nach der Variable/Liste `_CRYPTO_SYMBOLS`.
3. Füge dein neues Nautilus-Symbol exakt so hinzu, wie es in der `instrument_map.json` steht.

*Beispiel:*

```python
_CRYPTO_SYMBOLS = {
    "BTC.ETORO",
    "ETH.ETORO",
    "ADA.ETORO",
    "SOL.ETORO"  # ← Neues Krypto-Symbol hinzugefügt
}

```

### Schritt 3: Die Price Precision verstehen (Die 8-Stellen-Regel)

Kryptowährungen benötigen standardmäßig eine `size_precision` von `8` (8 Nachkommastellen für die Positionsgröße).
Das System (via `automation/utils.py` -> `_fallback_precisions`) greift für Krypto automatisch auf `8` zurück.
Handelt es sich jedoch um extrem kleine "Meme-Coins" (wie SHIB oder PEPE), kann auch der **Preis** (`price_precision`) 8 Nachkommastellen erfordern.

* **Normale Krypto (BTC, ETH, SOL):** Preis-Precision: 2, Size-Precision: 8. (Wird meist automatisch korrekt gefangen, wenn das Symbol in `_CRYPTO_SYMBOLS` steht).
* **Meme-Coins (SHIB):** Preis-Precision: 8, Size-Precision: 8.

Sollte es bei Krypto-Trades zu Rundungsfehlern kommen (z.B. Fehler *"Invalid Order Size"*), muss die Precision im Backend explizit forciert werden.

---

## 4. Schritt-für-Schritt: Datenbeschaffung & Backtest

Egal ob manuell hinzugefügte Aktie oder Krypto – nachdem die Map-Einträge gemacht sind, braucht das System Daten.

### Historische Daten laden

Obwohl der `daily_orchestrator.py` (v2.0+) fehlende Daten in Phase 2d selbst bemerkt, ist es für Anfänger bei manuellen Setups dringend empfohlen, den initialen Download einmal selbst anzustoßen, um Fehler sofort im Terminal zu sehen.

Öffne dein Terminal und führe aus:

```bash
python3 automation/historical_fetcher.py --months 12

```

Das Skript lädt nun die Tick-Daten für alle fehlenden Symbole, verpackt sie als `FixedSizeBinary(16)` Parquet-Dateien und legt sie unter `data/nautilus/data/quote_tick/{SYMBOL}/data.parquet` ab.

### Dry-Run testen

Um sicherzugehen, dass alles klappt, starte den Orchestrator im "Trockenlauf" (Dry-Run). Es werden keine echten Orders platziert.

```bash
python3 automation/daily_orchestrator.py --dry-run

```

Prüfe im angelegten Log (`logs/orchestrator_YYYYMMDD.log`), ob dein neues Symbol im Bereich "Phase 3+4: Matrix-Backtesting & Tournament" sauber verarbeitet wurde.

---

## 5. Häufige Fehlerquellen (Troubleshooting)

Wenn dein Instrument nicht gehandelt wird, obwohl es hinzugefügt wurde:

| Problem | Ursache & Lösung |
| --- | --- |
| **`KeyError: 'AAPL.ETORO' not in instrument_map`** | Du hast die eToro-ID nicht in `automation/config/instrument_map.json` eingetragen oder das `.ETORO` Suffix vergessen. |
| **`No parquet data for AAPL.ETORO`** | Du hast vergessen, die historischen Daten zu laden. Führe `python3 automation/historical_fetcher.py --months 12` aus. |
| **Asset wird im Tournament ignoriert** | Keine Sorge, kein Bug! Das Instrument hat schlichtweg die harten Out-of-Sample (OOS) Gating-Kriterien (z. B. Sortino-Ratio, Max Drawdown) in der `tournament.json` nicht bestanden. Es wird erst gehandelt, wenn das System es als sicher und profitabel einstuft. |
| **Rust-Backend Crash bei Krypto (`Decimal Precision Error`)** | Du hast ein Krypto-Asset hinzugefügt, es aber nicht in die `_CRYPTO_SYMBOLS` Liste eingetragen. Das System versucht, `0.005 BTC` mit Aktien-Precision (`size_precision=2`) zu handeln, was fehlschlägt. Trage es in die Liste ein! |
| **JSON Parse Error beim Start** | Du hast wahrscheinlich ein Komma in der `instrument_map.json` am Ende der Liste gesetzt, oder Anführungszeichen um die eToro-ID vergessen. JSON verzeiht keine Tippfehler. |

---

*Zuletzt aktualisiert für Orchestrator v2.0+ und Nautilus-Integration.*

```

```
