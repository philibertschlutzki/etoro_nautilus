# Neue Instrumente / Ticker hinzufügen

> **Version 2.2 · Stand 2026-06-09 · Sprache: Deutsch**
>
> Diese Anleitung beschreibt, wie ein neues Symbol (Aktie, Krypto, Forex, Rohstoff) in die Pipeline aufgenommen wird — vom automatischen Normalfall bis zu den beiden Ausnahmen, die manuelle Eingriffe erfordern.

> **Changelog 2.2 (2026-06-09):** Korrektur gemäß [`DOC_CLEANUP_ISSUES.md`](../DOC_CLEANUP_ISSUES.md) **DOC-3** — kanonische Instrument-Quelle ist `automation/config/instrument_map.json` (nicht die Legacy-`automation/adapters/instrument_map.py`). Eintragsformat, Pre-Flight-Checks und Troubleshooting entsprechend aktualisiert.

---

## Inhaltsverzeichnis

1. [Standardfall: automatische Integration](#1-standardfall-automatische-integration)
2. [Ausnahme A — manuelles Asset (Mapping fehlt)](#2-ausnahme-a--manuelles-asset-mapping-fehlt)
3. [Ausnahme B — Krypto (kritisch!)](#3-ausnahme-b--krypto-kritisch)
4. [Datenbeschaffung & Backtest](#4-datenbeschaffung--backtest)
5. [Troubleshooting](#5-troubleshooting)

---

## 1. Standardfall: automatische Integration

Im Regelfall ist **kein** manueller Eingriff nötig. Gehört das Symbol zum Universum des konfigurierten eToro Smart Portfolios, wird es automatisch aufgenommen:

1. `universe_fetcher.py` liest das Smart Portfolio und schreibt das Symbol nach `data/universe/momentum_ls.json`.
2. Existiert für die eToro-ID bereits ein Eintrag in `automation/config/instrument_map.json`, wird das Symbol korrekt auf das Nautilus-Symbol gemappt.
3. `catalog_service.py` / der Backfill sammeln die Tick-Daten, die nächste Orchestrator-Phase nimmt das Symbol in den Matrix-Backtest auf.

```bash
python3 automation/universe_fetcher.py          # Universe aktualisieren
# Prüfen, ob das neue Symbol erschienen ist:
python3 -c "import json; u=json.load(open('data/universe/momentum_ls.json')); print(u['universe'])"
```

Erscheint das Symbol im Universum **und** ist die eToro-ID in `instrument_map.json` bekannt, ist nichts weiter zu tun → weiter mit [Kapitel 4](#4-datenbeschaffung--backtest).

---

## 2. Ausnahme A — manuelles Asset (Mapping fehlt)

Fehlt die eToro-ID in der Instrument-Map, muss der Eintrag **manuell** ergänzt werden.

> ✅ **Kanonische Quelle (DOC-3):** Die Laufzeit-Instrument-Map ist die JSON-Datei **`automation/config/instrument_map.json`**. Sie wird zur Laufzeit über `d['instruments']` gelesen (so auch in `README.md`, `AGENTS.md`, `deployment.md`, `TESTING.md` und allen Pre-Flight-Checks). Trage neue Instrumente **hier** ein — **nicht** in der Legacy-Datei `automation/adapters/instrument_map.py`.

### 2.1 Eintragsformat

Die JSON-Struktur ist ein Objekt unter dem Schlüssel `instruments`, indiziert nach **eToro-ID** (String):

```json
{
  "instruments": {
    "1001": {
      "symbol": "AAPL.ETORO",
      "asset_class": "EQUITY",
      "price_precision": 2,
      "size_precision": 2
    },
    "2042": {
      "symbol": "NATGAS.ETORO",
      "asset_class": "COMMODITY",
      "price_precision": 5,
      "size_precision": 5
    }
  }
}
```

| Feld | Bedeutung | Hinweis |
|------|-----------|---------|
| Schlüssel (`"1001"`) | eToro-Instrument-ID | als String |
| `symbol` | Nautilus-Symbol | Konvention `<TICKER>.ETORO` |
| `asset_class` | `EQUITY` / `CRYPTO` / `FOREX` / `COMMODITY` | steuert Spread-Modell (`spread_bps_by_asset_class`) |
| `price_precision` | Nachkommastellen Preis | siehe Precision-Tabelle unten |
| `size_precision` | Nachkommastellen Ordergröße | **Aktien = 2, Krypto = 8** |

### 2.2 Precision-Referenz

| Kategorie | price_precision | size_precision |
|-----------|-----------------|----------------|
| SHIB / PEPE (Meme-Coins) | 8 | 8 |
| Crypto (BTC, ETH, SOL, …) | 2 | **8** |
| Forex / Rohstoffe | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> ℹ️ Die Werte in `instrument_map.json` haben **Vorrang** vor der Fallback-Heuristik `automation/utils._fallback_precisions`. Der Fallback greift nur, wenn weder API noch Parquet-Metadaten noch die JSON-Map eine Precision liefern. Details zum Fallback und zum FSB(16)-Format: [`automation_manual.md`](automation_manual.md), Kapitel 3.

### 2.3 Eintrag verifizieren

```bash
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente'); print(d['instruments'].get('1001'))"
```

> ℹ️ **Hinweis zur Legacy-`.py` (DOC-3):** Falls `automation/adapters/instrument_map.py` (Dict `ETORO_INSTRUMENTS`) im Repo noch existiert, ist sie historisch der **Generator** der JSON gewesen. Sie ist **nicht** mehr die Laufzeitquelle. Ob sie als JSON-Generator erhalten bleibt (mit explizitem Regenerations-Schritt) oder entfernt wird, ist über Issue **DOC-3** zu klären. Trage produktiv ausschließlich in die JSON ein.

---

## 3. Ausnahme B — Krypto (kritisch!)

> ⚠️ **Pflichtschritt für jedes neue Krypto-Asset.** Wird ein Krypto-Symbol nicht zusätzlich in `_CRYPTO_SYMBOLS` (`automation/utils.py`) registriert, greift im Fallback-Pfad fälschlich die **Aktien-Heuristik** (`size_precision=2`). Folge: Das Rust-Backend lehnt fraktionale Krypto-Orders wie `0.00005 BTC` ab → **Crash**.

Auch wenn die JSON-Map korrekt `size_precision=8` enthält, ist der `_CRYPTO_SYMBOLS`-Eintrag als **Absicherung des Fallback-Pfads** erforderlich (z. B. wenn die JSON-Precision für ein Symbol einmal fehlt).

```python
# automation/utils.py
_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX",
    "LINK",   # ← neues Krypto-Asset hier ergänzen
})
```

Reihenfolge der Fallback-Heuristik beachten (Meme-Coins werden per Substring **vor** dem Set geprüft):

| Symbol-Muster | (price, size) |
|---------------|---------------|
| enthält `SHIB` oder `PEPE` | `(8, 8)` |
| in `_CRYPTO_SYMBOLS` | `(2, 8)` |
| Forex / Commodity | `(5, 5)` |
| sonst (Default) | `(2, 2)` |

Nach Änderungen an Precision oder `_CRYPTO_SYMBOLS` gilt: **`--reset-catalog` ist zwingend**, da ältere Parquets noch falsche `size_precision`-Metadaten tragen können.

```bash
python3 automation/daily_orchestrator.py --reset-catalog
```

---

## 4. Datenbeschaffung & Backtest

Sobald Mapping (und ggf. Krypto-Registrierung) stehen, müssen historische Daten vorhanden sein, bevor das Symbol im Turnier auftauchen kann.

```bash
# Tiefer Erst-Backfill (Monate) für ein frisch aufgenommenes Symbol
python3 automation/historical_fetcher.py --months 12

# Kurzer Lückenschluss der letzten Tage
python3 automation/api_backfiller.py --days 7

# Dry-Run der Pipeline (Phasen 1–4, KEIN Live-Bot) zur Kontrolle
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

Das Backtest-Fenster ist **dynamisch** (~150 Tage bei Standardwerten `is_window_days=120` / `oos_window_days=30`). Liegt für das neue Symbol nicht genügend Historie vor (außerhalb `span_tolerance_days=3.0`), wird es im Backtest übersprungen — dann zuerst den Backfill vervollständigen. Methodik: [`automation_manual.md`](automation_manual.md), Kapitel 4.

---

## 5. Troubleshooting

| Symptom | Ursache | Maßnahme |
|---------|---------|----------|
| Symbol erscheint nicht im Universum | gehört nicht zum Smart Portfolio, oder `universe_fetcher` nicht gelaufen | `python3 automation/universe_fetcher.py`; Smart-Portfolio-Zugehörigkeit prüfen |
| **`KeyError` beim Instrument-Lookup** (eToro-ID nicht gefunden) | eToro-ID fehlt in `automation/config/instrument_map.json` | Eintrag gemäß [Kapitel 2.1](#21-eintragsformat) ergänzen und mit dem Pre-Flight-Check verifizieren |
| Rust-Backend-Crash bei Krypto-Order (`0.00005 …`) | Krypto-Symbol nicht in `_CRYPTO_SYMBOLS`, Aktien-Heuristik greift | Symbol in `_CRYPTO_SYMBOLS` ([Kapitel 3](#3-ausnahme-b--krypto-kritisch)) ergänzen; `--reset-catalog` |
| `ValueError` / Order rundet auf 0 (Aktie) | `size_precision=0` aus altem Parquet/Fallback | JSON-Eintrag auf `size_precision=2` setzen; `--reset-catalog` |
| Symbol wird im Backtest übersprungen | zu wenig Historie (außerhalb `span_tolerance_days=3.0`) | `historical_fetcher.py --months 12` ausführen |
| Falsche Spread-Annahme im Backtest | `asset_class` im JSON-Eintrag falsch/fehlt | `asset_class` korrekt setzen (`EQUITY`/`CRYPTO`/`FOREX`/`COMMODITY`) |

> ℹ️ **Hinweis (DOC-3):** Eine veraltete Fehlermeldung wie `KeyError: <ID> not in ETORO_INSTRUMENTS` deutet darauf hin, dass noch ein Code-Pfad gegen die Legacy-`instrument_map.py` läuft. Die Laufzeitquelle ist `instrument_map.json`; abweichende Pfade sind über Issue DOC-3 zu bereinigen.

---

*Anleitung v2.2 erstellt am 2026-06-09. Kanonische Instrument-Quelle: `automation/config/instrument_map.json`. Bei strukturellen Änderungen mit [`automation/AGENTS.md`](../automation/AGENTS.md) synchron halten.*
