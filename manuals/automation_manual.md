# automation/ — Technisches Pipeline- & Format-Handbuch

> **Version 2.1 · Stand 2026-06-09 · Sprache: Deutsch**
>
> Dieses Handbuch ist die **technische Referenz** für das Standalone-Paket `automation/`. Es richtet sich an Entwickler, die den Datenfluss, die Datenformate, die Turnier-Logik und das Live-Deployment im Detail verstehen oder verändern wollen. Für einen Einstieg auf Anfänger-Niveau siehe das Master-[`README.md`](../README.md).
>
> **Quellenhierarchie:** Bei Widersprüchen gilt immer der **Code** vor [`automation/AGENTS.md`](../automation/AGENTS.md) vor diesem Handbuch.

> **Changelog 2.1 (2026-06-09):** Korrektur der in [`DOC_CLEANUP_ISSUES.md`](../DOC_CLEANUP_ISSUES.md) erfassten Inkonsistenzen — **DOC-9** (FSB16-Skalierung `10^16` statt `10^precision`, i128-Serialisierung), **DOC-1** (`_fallback_precisions` Equity → `(2, 2)`, kanonischer Name `_CRYPTO_SYMBOLS`), **DOC-2** (dynamisches Walk-Forward-Fenster statt 7-Tage-Block, `span_tolerance_days=3.0`, Kalender-Awareness), **DOC-4** (Risiko-Ratio-Caps `50.0`/`100.0` statt Sentinel `999`, `None`-Rendering), **DOC-5** (Safety-Interlock aus standalone-Quelle statt Root-`config/setups.py`).

---

## Inhaltsverzeichnis

1. [Standalone-Datenfluss & Architekturprinzipien](#1-standalone-datenfluss--architekturprinzipien)
2. [Pipeline-Komponenten (die ausführbaren Module)](#2-pipeline-komponenten-die-ausführbaren-module)
3. [Datenformate: FixedSizeBinary(16) & Precision](#3-datenformate-fixedsizebinary16--precision)
4. [Turnier-Logik: Walk-Forward, Gating & Metriken](#4-turnier-logik-walk-forward-gating--metriken)
5. [Live-Deployment & Safety-Interlock](#5-live-deployment--safety-interlock)
6. [Systemkonfiguration (systemd & Cron)](#6-systemkonfiguration-systemd--cron)
7. [Diagnose-Tabelle](#7-diagnose-tabelle)

---

## 1. Standalone-Datenfluss & Architekturprinzipien

### 1.1 Hartes Standalone-Constraint

Das gesamte Produkt lebt im Verzeichnis `automation/`. **Keine** Datei darf aus dem Legacy-Root (`adapters/`, `config/`, `strategies/` auf Repo-Ebene) importieren. Migrierte Adapter liegen unter `automation/adapters/`, migrierte Konfiguration unter `automation/config/`. Die Isolation wird per AST-Analyse in `test_automation_isolation.py` erzwungen — ein verbotener Import lässt die Test-Suite fehlschlagen.

### 1.2 Shift-Left Data Quality

Alle Datenquellen liefern bereits **100 % Nautilus-kompatible Parquet-Daten** im `FixedSizeBinary(16)`-Format (siehe [Kapitel 3](#3-datenformate-fixedsizebinary16--precision)). Es gibt **keine** nachgelagerte Typ-Migration im Orchestrator: Der Merge ist reines `pa.concat_tables` + `ts_event`-Dedup + atomarer Write. Datenqualität wird an der Quelle erzeugt, nicht im Pipeline-Lauf repariert.

### 1.3 Die 5-Phasen-Pipeline (Überblick)

```text
Phase 1  Universe & Mapping       universe_fetcher.py     Stale-Check > 24 h
Phase 2  ZIP-Merge + Backfill     daily_orchestrator.py   Dedup über ts_event
Phase 3  Matrix-Backtest          backtest_runner.py      Subprozess je (Strategie × Symbol)
Phase 4  Turnier-Selektion        backtest_runner.py      Sortino / PF / Calmar-Ranking
Phase 5  Live-Deployment          momentum_ls_run.py      Fail-Closed OOS-Gate
```

Der Orchestrator (`daily_orchestrator.py`) ruft den Backtest-Runner als **Subprozess** auf, damit ein Crash in der Nautilus-Engine den Orchestrator-Prozess nicht mitreißt und der Speicher je Lauf sauber freigegeben wird.

---

## 2. Pipeline-Komponenten (die ausführbaren Module)

| Modul | Rolle | Typischer Aufruf |
|-------|-------|------------------|
| `universe_fetcher.py` | Lädt das Symbol-Universum eines eToro Smart Portfolios, mappt eToro-IDs auf Nautilus-Symbole, schreibt `data/universe/momentum_ls.json` mit `fetched_at`-Timestamp. | `python3 automation/universe_fetcher.py` |
| `catalog_service.py` | 24/7-Dauerdienst (systemd, `Restart=always`). Sammelt stündlich QuoteTicks und legt ZIPs in `data/import/` ab. | systemd-Service |
| `api_backfiller.py` | Kurzer Lückenschluss der letzten Tage über die eToro-API. | `python3 automation/api_backfiller.py --days 7` |
| `historical_fetcher.py` | Tiefer Erst-Backfill (Monate) für die Initialbefüllung. | `python3 automation/historical_fetcher.py --months 12` |
| `daily_orchestrator.py` | Orchestriert alle 5 Phasen. Merge, optionaler Backfill, Matrix-Backtest, Turnier, Live-Deploy. | `python3 automation/daily_orchestrator.py --skip-api-fetch` |
| `backtest_runner.py` | Führt den eigentlichen Backtest aus, extrahiert Metriken (`extract_metrics`), berechnet die Turnier-Scores. | als Subprozess durch den Orchestrator |
| `momentum_ls_run.py` | Live-Bot. Liest `per_symbol_winners` aus dem Turnier-JSON und startet das Trading (oder Dry-Run). | siehe [Kapitel 5](#5-live-deployment--safety-interlock) |

**Wichtige Orchestrator-Flags:**

```bash
--skip-api-fetch     # Phase-2-API-Backfill überspringen (catalog_service hat ZIPs bereits geliefert)
--dry-run            # Phasen 1–4 ausführen, KEINEN Live-Bot starten
--reset-catalog      # Katalog komplett neu aufbauen (nach Precision-Fixes zwingend)
```

---

## 3. Datenformate: FixedSizeBinary(16) & Precision

### 3.1 Das FSB(16)-Speicherformat

Das Rust-Backend von NautilusTrader hält Preise und Größen intern als **128-Bit-Integer** im Arrow-Typ `FixedSizeBinary(16)` (16 Byte, little-endian, signed). Die Skalierung ist eine **feste High-Precision-Konstante von `10^16`** — sie ist **unabhängig** von der instrument-spezifischen Anzeige-Precision.

```python
# automation/_serde.py — korrekte Enkodierung
FIXED_PRECISION_SCALE = 10 ** 16   # i128 High-Precision, NICHT 10 ** price_precision

def _encode_fsb16(value: float) -> bytes:
    raw = round(value * FIXED_PRECISION_SCALE)
    # i128-Grenzen statt int64 — große Preise (BTC ~10^21 nach Skalierung)
    # überschreiten int64 und müssen vorzeichenrichtig serialisiert werden:
    return int(raw).to_bytes(16, byteorder="little", signed=True)
```

> ⚠️ **Pitfall #29 (GELÖST) — niemals wieder einführen:**
> 1. **Skalierung muss `10^16` sein, nicht `10^price_precision`.** Eine Skalierung mit `10^precision` dekodiert sämtliche Preise als `0.0` → **0 Trades über alle Backtests**.
> 2. **Serialisierung muss `int(raw).to_bytes(16, "little", signed=True)` sein.** Das früher genutzte `struct.pack("<q", raw) + b"\x00" * 8` (int64 + 8 Null-Bytes) ist nur für nicht-negative, int64-passende Werte korrekt. Bei `10^16`-Skalierung überschreiten große Preise den int64-Bereich, und negative Werte bräuchten Sign-Extension (High-Bytes `0xFF`). Die Padding-Variante korrumpiert beide Fälle.

### 3.2 Pflicht-Schema je Parquet

| Pflicht-Spalten | Pflicht-Byte-Metadaten |
|-----------------|------------------------|
| `bid_price`, `ask_price`, `bid_size`, `ask_size`, `ts_event`, `ts_init` | `b"price_precision"`, `b"size_precision"`, `b"instrument_id"` |

> ℹ️ **Trennung Speicherskalierung ↔ Anzeige-Precision:** Die Byte-Metadaten `b"price_precision"` / `b"size_precision"` sind die **instrument-spezifische** Precision (für die Nautilus-Quantisierung von Orders). Sie sind strikt getrennt von der **festen `10^16`-Speicherskalierung** des FSB(16)-Werts. Beides zu vermischen ist exakt die Wurzel von Pitfall #29.

### 3.3 Precision-Fallback

Liefern weder die eToro-API noch die Parquet-Metadaten eine Precision, greift `automation/utils._fallback_precisions(symbol) -> (price_precision, size_precision)` als **einzige Quelle der Wahrheit** für Default-Werte:

```python
# automation/utils.py — kanonische Fallback-Heuristik
_CRYPTO_SYMBOLS = frozenset({"BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", ...})

def _fallback_precisions(symbol: str) -> tuple[int, int]:
    base = symbol.split(".")[0].upper()

    # Meme-Coins zuerst (Substring-Treffer vor dem Crypto-Set):
    if "SHIB" in base or "PEPE" in base:
        return 8, 8
    # Etablierte Kryptowährungen:
    if base in _CRYPTO_SYMBOLS:
        return 2, 8
    # Forex / Rohstoffe (NATGAS, PALL, XAU, …):
    if _is_forex_or_commodity(base):
        return 5, 5
    # Default: Aktien
    return 2, 2          # KORREKT: size_precision = 2, NICHT 0
```

| Kategorie | price_precision | size_precision |
|-----------|-----------------|----------------|
| SHIB / PEPE (Meme-Coins) | 8 | 8 |
| Crypto (BTC, ETH, SOL, …) | 2 | **8** |
| Forex / Rohstoffe | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> ✅ **Pitfall #14 / #23 (GELÖST):** Aktien nutzen `size_precision=2`. Der frühere Wert `0` ließ `make_qty()` jede fraktionale Equity-Order auf `0` abrunden → `ValueError` bzw. „0 Trades". **Nach jedem Precision-Fix ist `--reset-catalog` zwingend** — ältere Parquets können noch `size_precision=0` in den Byte-Metadaten tragen.

> ⚠️ **Krypto muss in `_CRYPTO_SYMBOLS` registriert sein.** Fehlt ein Krypto-Asset im Frozenset (und greift auch kein Meme-Substring), fällt es in den Aktien-Default (`size_precision=2`) → **Rust-Backend-Crash** bei Orders wie `0.00005 BTC`. Vorgehen: [`manuals/new_tickers.md`](new_tickers.md), Ausnahme B.

---

## 4. Turnier-Logik: Walk-Forward, Gating & Metriken

### 4.1 Dynamisches Backtest-Fenster (Walk-Forward)

Das Backtest-Fenster ist **dynamisch** und wird aus `automation/config/backtest.json` berechnet — es ist **kein** fixes 7-Tage-Fenster:

```python
# Berechnung der Gesamtspanne (Walk-Forward)
total_days = is_window_days + (splits * oos_window_days)
end   = _last_trading_day(datetime.now(timezone.utc))   # kalender-aware, s. u.
start = end - timedelta(days=total_days)
```

Mit den Standardwerten `is_window_days=120`, `oos_window_days=30`, `splits=1` ergibt das ein **~150-Tage-Fenster** (120 IS + 30 OOS).

> ⚠️ **Pitfall (DOC-2) — der alte 7-Tage-Block ist obsolet.** Frühere Versionen dieses Handbuchs zeigten:
> ```python
> # VERALTET — NICHT mehr verwenden:
> today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
> seven_days_ago = today_midnight - timedelta(days=7)
> ```
> Das stammt aus der Pre-Walk-Forward-Ära und steht im Widerspruch zur dynamischen Formel oben und zu [`strategie_optimierung.md`](strategie_optimierung.md) (IS 120 / OOS 30).

**Kalender-Awareness:** Der Endpunkt der Spanne rollt auf den letzten Handelstag zurück (z. B. von einem Wochenende auf Freitag Mitternacht UTC), damit Wochenend-Lücken die Span-Berechnung nicht verfälschen (Issue #271).

**Span-Toleranz:** `span_tolerance_days = 3.0` ist die **Single Source of Truth** in `check_data_span` (Issue #304). Liegt die tatsächliche Datenspanne innerhalb dieser Toleranz zur Soll-Spanne, gilt sie als ausreichend. Der frühere `0.95`-Hard-Guard wurde entfernt und darf nicht reaktiviert werden.

### 4.2 Walk-Forward-Methodik & „State Bleed"

Es läuft ein **einziger, durchgehender Engine-Run** über die volle Spanne. Die Aufteilung in **In-Sample (IS)** und **Out-of-Sample (OOS)** erfolgt **retrospektiv** per Timestamp-Filterung in `extract_metrics`; der Nautilus-Rust-Core wird nicht unterbrochen.

> ⚠️ **„State Bleed" (bewusst akzeptierter Kompromiss):** An der IS/OOS-Grenze findet **kein** Engine-Reset statt. Offene Positionen, Kontostand und aufgewärmte Indikatoren (EMAs, RSI …) fließen ungefiltert aus IS in OOS. OOS-Ergebnisse sind dadurch methodisch **nicht 100 % „rein"**. Der Kompromiss minimiert Laufzeit/Overhead. Ein optionaler Hard-Reset mit Embargo ist in [`feature_roadmap.md`](feature_roadmap.md), Abschnitt A4, ausgearbeitet; die Begründung des aktuellen Kompromisses steht in Abschnitt G3.

### 4.3 FIFO-Matching & PnL

PnL wird per **FIFO-Matching** über `generate_fills_report()` (Fallback `generate_order_fills_report()`) berechnet. Die FIFO-Schleife iteriert **immer über das gesamte Datenset (IS + OOS)**; erst *danach* werden die PnL-Tupel am Cutoff-Timestamp separiert.

> ⚠️ **Pitfall #32:** Würde man die Trades **vor** dem FIFO-Lauf am Cutoff trennen, blieben offene Lot-Queues korrumpiert (ein in IS eröffnetes, in OOS geschlossenes Lot würde falsch oder gar nicht gematcht). Reihenfolge daher zwingend: **erst FIFO über alles, dann separieren.**

### 4.4 Kostenmodellierung (Anti-Zero-Spread)

Backtest-Ticks erhalten einen **Asset-Class-spezifischen Spread** aus `spread_bps_by_asset_class` (z. B. EQUITY 8 bps, CRYPTO 15 bps), der direkt in `load_ticks_from_catalog` rekonstruiert wird. Zusätzlich wird `commission_bps` im FIFO-PnL verrechnet. Das verhindert „Zero-Spread"-Artefakte (künstlich überhöhte Sortino-/PF-Werte).

### 4.5 Metrik-Caps & `None`-Rendering

Risiko-Ratios werden bei degenerierten Nennern (zu wenige oder keine Verlust-Trades) **hart gekappt** bzw. als `None` markiert:

| Situation | Verhalten |
|-----------|-----------|
| Degenerierter Nenner | Sortino/PF → Cap **50.0**, Calmar → Cap **100.0** |
| 1 Verlust-Trade bei Low-Sample (< 50 Trades) | hartes Cap **2.0** |
| All-Win / < 2 Verluste | Metrik = `None` (gerendert als `n/a(win)`) |

> ⚠️ **Pitfall (DOC-4) — der Wert `999` ist obsolet.** Frühere Handbücher beschrieben einen Profit Factor von `999` als „No-Loss-Artefakt". Das ist falsch: Der reale Cap ist **50.0** (Issues #37/#43). Sentinel-Werte (50.0) werden zudem aus den Medianen der Aggregation **ausgefiltert** (Issue #263), und echte All-Win-Fälle liefern `None` statt einer Pseudo-Zahl. Caps und Shrinkage (Issue #288) dürfen **nicht** entfernt werden — sie sind die einzige Absicherung gegen Selektion auf statistisches Rauschen.

### 4.6 Turnier-Gating (Eligibilität)

Ein Strategie-Symbol-Paar ist nur dann valider Kandidat, wenn **alle** harten Kriterien erfüllt sind …

- `min_trades ≥ 20`
- `min_total_return > 0.005` (= **0,5 %**, net-of-spread)

… **und mindestens eine** der weichen Bedingungen:

- `Sortino ≥ 0.3` **oder** `Profit Factor ≥ 1.1`

Ergänzend greifen `min_win_rate` und `min_expectancy` (Default `0.00005`).

> ⚠️ **Korrektur (DOC-2/DOC-4-nah):** Das Selektionskriterium ist `total_return > 0.5 % (0.005)`, **nicht** `> 0.0 %`. Ein 0-%-Schwellwert würde Break-even-Strategien als Gewinner zulassen.

**Selektionsreihenfolge „Rank first, Gate second"** (Issue #257): Erst wird auf der gesamten IS-tauglichen Population normalisiert und gerankt, dann wird pro Symbol absteigend iteriert, bis der erste Kandidat das **OOS-Gate** besteht. Der OOS-Decision-Trail wird als `[OOS-Drop]` geloggt.

**Score-Formel:**

```text
Score = Sortino · 0.4 + ProfitFactor · 0.3 + WinRate · 0.2 − MaxDrawdown · 0.1
```

### 4.7 Aggregat-Gewinner (hybride, bewusst gemischte Aggregation)

> ℹ️ Diese hybride Struktur ist **gewollt** — sie ist **kein** zu behebender Bug (Begründung: [`feature_roadmap.md`](feature_roadmap.md), Abschnitt G1):
> - **Volumen** (Trades, Wins): absolut aufsummiert; `win_rate` = Portfolio-Wins / Portfolio-Trades (Count-Ratio).
> - **Rendite** (`total_return`): kapital-/trade-gewichteter Mittelwert.
> - **Risiko-Ratios** (Sortino, PF): **Median** (Sentinel-Werte 50.0 herausgefiltert).
> - **`max_drawdown`**: aus **chronologisch gemergten OOS-Einzeltrades** (echte Portfolio-Equity-Kurve), **nicht** mehr als Median der Pair-Drawdowns (Issues #286/#303).

> ⚠️ **Geschützter Datenfluss:** Die temporäre Weitergabe der OOS-Einzeltrades (`_oos_trade_records`, vor dem Export per `.pop()` entfernt) ist die Datengrundlage der `max_drawdown`-Portfolio-Kurve und darf **nicht** als „Bloat" wegoptimiert werden.

---

## 5. Live-Deployment & Safety-Interlock

### 5.1 Start des Live-Bots

Der Live-Bot (`momentum_ls_run.py`) startet als **detached Subprozess** und liest `per_symbol_winners` aus dem Turnier-JSON. Der Live-`bar_type` ist zwingend `{symbol}-1-HOUR-MID-INTERNAL` (eToro streamt ausschließlich QuoteTicks, aus denen intern Mid-Bars aggregiert werden).

### 5.2 Zweistufiges Fail-Closed-Verhalten (Phase 5)

1. **Per-Pair-Check:** `fully_eligible_pairs > 0` **und** `winner_count > 0`, sonst harter Abbruch (`LIVE_DEPLOY_ABORTED`).
2. **Aggregat-OOS-Check:** Der Aggregat-Gewinner muss `oos_evaluated == True` **und** `oos_eligible == True` vorweisen.

> 🔒 **Live-Trading-Sicherheitsregel (absolut):** **Null** OOS-taugliche Paare verhindern jeden Live-Deploy. Ein bestandenes Aggregat-OOS kann ein Per-Pair-Versagen **niemals** überstimmen. Kein Symbol-Strategie-Paar wird live geschaltet, solange seine Strategie nicht im Turnier OOS-tauglich verifiziert wurde (`OOS-DEPLOY-REJECT`-Filter in `_build_bots_config`).

### 5.3 Dreistufiger Echtgeld-Interlock

Alle drei Bedingungen müssen erfüllt sein, sonst beendet sich der Prozess mit `sys.exit(1)`:

| Stufe | Parameter | Erwarteter Wert | Herkunft |
|-------|-----------|-----------------|----------|
| 1 | `environment` | `"real"` | standalone-Konfiguration in `automation/` (z. B. `automation/momentum_ls_run.py` / `automation/adapters/`) |
| 2 | `dry_run` | `False` | CLI-Flag bzw. abgeleitet aus `ETORO_CONFIRM_LIVE` |
| 3 | `ETORO_CONFIRM_LIVE` | `"1"` | Umgebungsvariable / `.env` |

> ⚠️ **Korrektur (DOC-5):** Die Herkunft von `environment` ist die **standalone-konforme Quelle innerhalb `automation/`** — **nicht** das Root-`config/setups.py` (`ETORO_EXECUTION`), wie ältere Handbücher angaben. Ein Import aus dem Repo-Root-`config/` verletzt das harte Standalone-Prinzip ([Kapitel 1.1](#11-hartes-standalone-constraint)) und ist seit der Adapter-Migration (Pitfall #19) unzulässig.

Fehlt eine der drei Bedingungen, läuft der Bot automatisch im **Dry-Run** (keine echten Orders). Notfall-Abschaltung, Graceful Shutdown und State-Integrität: [`run_bot_manual.md`](run_bot_manual.md), Kapitel 8.

### 5.4 Kapital-Allocator (`MomentumLSAllocator`)

- **No-Interference:** Existiert bereits eine offene Position für ein Symbol → Allokation `0.0`.
- **Dynamisches Slicing:** freies Kapital ÷ Anzahl Symbole ohne offene Position.
- **Floor:** errechneter Betrag < `$11.00` → `0.0` (eToro-Mindestbetrag).

> ℹ️ Das naive Gleichverteilungs-Slicing ignoriert Volatilität. Risiko-adjustierte Verfahren (Fixed-Fractional-Risk via ATR, inverse-Vol, fraktionales Kelly) sind in [`feature_roadmap.md`](feature_roadmap.md), Abschnitt B, ausgearbeitet. `$11-Floor` und No-Interference bleiben bewusst hart (Begründung: Abschnitt G4).

---

## 6. Systemkonfiguration (systemd & Cron)

Der Normalbetrieb besteht aus genau **einem Dauerdienst** (`catalog_service.py`) und **einem Cron-Job** (`daily_orchestrator.py`). Vollständige VM-Einrichtung inklusive Swap-File für RAM-arme VMs: [`deployment.md`](deployment.md).

### 6.1 systemd-Service (24/7-Tick-Sammlung)

```ini
# /etc/systemd/system/etoro-catalog.service
[Unit]
Description=eToro Catalog Service (24/7 Tick Collection)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=etoro
WorkingDirectory=/opt/etoro_nautilus
EnvironmentFile=/opt/etoro_nautilus/.env
ExecStart=/opt/etoro_nautilus/venv/bin/python3 automation/catalog_service.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now etoro-catalog.service
sudo journalctl -u etoro-catalog.service -f      # Live-Log
```

> ℹ️ **Aufrufform konsistent halten:** `AGENTS.md` dokumentiert zusätzlich die Modul-Form (`python3 -m automation.catalog_service`). Die `ExecStart`-Zeile und alle manuellen Aufrufe müssen dieselbe Form verwenden (Skript- **oder** Modul-Aufruf), damit die Import-Pfade konsistent aufgelöst werden.

### 6.2 Cron-Job (täglicher Orchestrator-Lauf)

```cron
# crontab -e  — täglich 06:00 UTC; catalog_service hat die ZIPs bereits geliefert
0 6 * * * cd /opt/etoro_nautilus && /opt/etoro_nautilus/venv/bin/python3 automation/daily_orchestrator.py --skip-api-fetch >> logs/cron_orchestrator.log 2>&1
```

---

## 7. Diagnose-Tabelle

| Symptom | Wahrscheinliche Ursache | Maßnahme |
|---------|-------------------------|----------|
| **0 Trades über alle Backtests** | FSB16-Skalierung verwendet `10^precision` statt `10^16` ([§3.1](#31-das-fsb16-speicherformat), Pitfall #29) | `_serde.py` auf feste `10^16`-Skalierung + `to_bytes(16, "little", signed=True)` prüfen; danach `--reset-catalog` |
| **`ValueError` / Order rundet auf 0** bei fractional Equity | `size_precision=0` aus altem Fallback oder altem Parquet ([§3.3](#33-precision-fallback)) | `_fallback_precisions` muss Equity `(2, 2)` liefern; `--reset-catalog` ausführen |
| **Rust-Backend-Crash** bei Krypto-Order (`0.00005 BTC`) | Krypto-Symbol nicht in `_CRYPTO_SYMBOLS` → Aktien-Heuristik greift | Symbol in `_CRYPTO_SYMBOLS` registrieren ([`new_tickers.md`](new_tickers.md), Ausnahme B) |
| **Backtest meldet zu kurze Datenspanne** | Datenspanne außerhalb `span_tolerance_days=3.0` ([§4.1](#41-dynamisches-backtest-fenster-walk-forward)) | Backfill ausführen (`historical_fetcher.py --months 12`); Span-Toleranz nicht ad hoc erhöhen |
| **Implausibel hohe Sortino/PF** (z. B. > 50) | degenerierter Nenner / zu wenige Verluste; Sentinel-Cap greift ([§4.5](#45-metrik-caps--none-rendering)) | erwartetes Verhalten; Caps/Shrinkage nicht entfernen; Trade-Anzahl prüfen |
| **Live-Deploy bricht mit `LIVE_DEPLOY_ABORTED` ab** | keine OOS-tauglichen Paare ([§5.2](#52-zweistufiges-fail-closed-verhalten-phase-5)) | erwartetes Fail-Closed-Verhalten; Turnier-JSON auf `oos_eligible` prüfen |
| **Bot startet im Dry-Run trotz Live-Absicht** | eine der drei Interlock-Bedingungen fehlt ([§5.3](#53-dreistufiger-echtgeld-interlock)) | `environment=="real"`, `dry_run==False`, `ETORO_CONFIRM_LIVE=="1"` verifizieren |
| **Import-/Isolations-Test schlägt fehl** | verbotener Import aus Root-`adapters/`/`config/`/`strategies/` ([§1.1](#11-hartes-standalone-constraint)) | Import auf `automation/`-interne Quelle umstellen |
| **Git-Push blockiert / Repo-Bloat** | lokale `logs/*.log` / `*.json` getrackt | `git checkout origin/main -- logs/` bzw. Dateien unstagen |

---

*Handbuch v2.1 erstellt am 2026-06-09. Bei strukturellen Änderungen sind dieses Dokument und [`automation/AGENTS.md`](../automation/AGENTS.md) synchron zu halten.*
