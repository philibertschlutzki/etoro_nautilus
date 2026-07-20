# eToro Nautilus — Gesamthandbuch (Master-README)

> **Was ist das?** Ein autonomes, hermetisches Framework für algorithmisches Trading auf eToro, aufgebaut auf [NautilusTrader](https://nautilustrader.io/). Es deckt den vollständigen Zyklus ab: Universe-Beschaffung → kontinuierliche Tick-Sammlung → historischer Backfill → Matrix-Backtest mit Turnier-Selektion → Live-Deployment.
>
> ⚠️ **Echtgeld-Warnung:** Dieses System kann reale Orders auf echten Finanzmärkten platzieren. Fehler in Order-Logik, Positions-State oder Precision-Handling können **echte monetäre Verluste** verursachen. Lies vor dem produktiven Einsatz mindestens [`automation/AGENTS.md`](automation/AGENTS.md) und das Kapitel [Live-Deployment & Safety-Interlock](#10-live-deployment--safety-interlock) vollständig.

Dieses Dokument ist der **zentrale Einstieg** und verlinkt alle Detail-Handbücher. Es ist bewusst so geschrieben, dass auch Einsteiger den Gesamtablauf verstehen, bleibt dabei aber technisch exakt.

---

## Inhaltsverzeichnis

1. [In einem Satz: Was macht das System?](#1-in-einem-satz-was-macht-das-system)
2. [Die 5-Phasen-Pipeline (Gesamtbild)](#2-die-5-phasen-pipeline-gesamtbild)
3. [Schnellstart (Installation)](#3-schnellstart-installation)
4. [Die tägliche Ausführung](#4-die-tägliche-ausführung)
5. [Befehls-Cheatsheet](#5-befehls-cheatsheet)
6. [Konfiguration: die fünf JSON-Dateien](#6-konfiguration-die-fünf-json-dateien)
7. [Datenformat: FixedSizeBinary(16) kurz erklärt](#7-datenformat-fixedsizebinary16-kurz-erklärt)
8. [Precision-System (Nachkommastellen)](#8-precision-system-nachkommastellen)
9. [Backtest, Walk-Forward & Turnier](#9-backtest-walk-forward--turnier)
10. [Live-Deployment & Safety-Interlock](#10-live-deployment--safety-interlock)
11. [Paper-Trading-Guardrails (GR-01–04)](#11-paper-trading-guardrails-gr-01-04)
12. [Verzeichnis- & Log-Struktur](#12-verzeichnis---log-struktur)
13. [Tests & Pre-Flight-Checks](#13-tests--pre-flight-checks)
14. [Dokumentations-Landkarte](#14-dokumentations-landkarte)
15. [Bekannte offene Punkte & Roadmap](#15-bekannte-offene-punkte--roadmap)
16. [Dokumentations-Bereinigung (offene Issues)](#16-dokumentations-bereinigung-offene-issues)

---

## 1. In einem Satz: Was macht das System?

Das System liest täglich das Symbol-Universum eines eToro **Smart Portfolios**, sammelt und backfillt dafür historische 1-Stunden-Kursdaten, lässt **alle aktiven Handelsstrategien gegeneinander antreten** (Turnier), wählt pro Symbol die beste Strategie auf einem Trainingszeitraum (In-Sample) aus, **validiert** sie auf einem ungesehenen Zeitraum (Out-of-Sample) und startet — nur wenn diese Validierung besteht — einen **Live-Trading-Bot**.

**Kernprinzip „Shift-Left Data Quality":** Alle Datenquellen liefern bereits 100 % Nautilus-kompatible Parquet-Daten im `FixedSizeBinary(16)`-Format. Es gibt **keine** nachgelagerte Typ-Migration im Orchestrator.

**Standalone-Prinzip (hartes Constraint):** Das gesamte Produkt lebt im Verzeichnis `automation/`. Keine Datei darf aus dem Legacy-Root (`adapters/`, `config/`, `strategies/`) importieren — geprüft per AST in `tests/test_automation_isolation.py`. Migrierte Adapter liegen unter `automation/adapters/`.

---

## 2. Die 5-Phasen-Pipeline (Gesamtbild)

```text
automation/universe_fetcher.py ─► data/universe/momentum_ls.json
                                          │
   automation/catalog_service.py (24/7) ─┤   automation/api_backfiller.py (7 Tage)
   stündliche ZIPs in data/import/        │   automation/historical_fetcher.py (12 Monate)
                                          ▼
                          automation/daily_orchestrator.py  (5 Phasen)
   ┌──────────────┬──────────────┬──────────────────────┬─────────────────┐
   ▼              ▼              ▼                       ▼                 ▼
 Phase 1        Phase 2        Phase 3                 Phase 4           Phase 5
 Universe &     ZIP-Merge +    Matrix-Backtest         Turnier           Live-Deploy
 Mapping        Backfill       (Subprozess)            (Sortino/PF/      (momentum_ls_run.py)
 (Stale-Check)  (Dedup)        backtest_runner.py       Calmar-Ranking)   + OOS-Gate
                                          │
                                          ▼
                               logs/tournament_YYYY-MM-DD.json
```

| Phase | Inhalt | Schlüsseldatei |
|-------|--------|----------------|
| **1** | Universe laden + eToro-IDs auf Symbole mappen, Stale-Check (> 24 h) | `universe_fetcher.py` |
| **2** | Alle ZIPs aus `data/import/` mergen + dedupen, optional API-/Deep-Backfill | `daily_orchestrator.py` |
| **3** | Matrix-Backtest aller aktiven Strategien × aller Symbole | `backtest_runner.py` |
| **4** | Turnier-Selektion: bester Kandidat pro Symbol + Aggregat-Gewinner | `backtest_runner.py` |
| **5** | Fail-Closed-Live-Deployment, nur bei bestandenem OOS-Gate | `momentum_ls_run.py` |

> ℹ️ **Backtest-Fenster ist dynamisch**, nicht fix. Es wird aus `backtest.json` berechnet:
> `total_days = is_window_days + (splits × oos_window_days)`, anschließend `start = end − timedelta(days=total_days)`.
> Mit den Standardwerten (`is_window_days=120`, `oos_window_days=30`) ergibt das ein **~150-Tage-Fenster**. Details zum Walk-Forward siehe [Kapitel 9](#9-backtest-walk-forward--turnier).

---

## 3. Schnellstart (Installation)

Voraussetzung: **Python 3.10+**.

```bash
git clone https://github.com/philibertschlutzki/etoro_nautilus.git
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate
pip install -r automation/requirements.txt
```

`.env` im Projekt-Root anlegen:

```env
ETORO_API_KEY=dein_api_key
ETORO_USER_KEY=dein_user_key
MOMENTUM_LS_USERNAME=etoro_username_des_smart_portfolios   # nur für universe_fetcher
ETORO_CONFIRM_LIVE=1                                       # NUR setzen, wenn Live-Trading bewusst aktiviert wird
```

> **Hinweis:** `MOMENTUM_LS_USERNAME` ist der **öffentliche** eToro-Benutzername des kopierten Smart Portfolios (z. B. `OutSmartNSDQ`), **nicht** dein eigener.

Server-/VM-Setup (systemd, Cron, Swap-File für 1 GB-RAM-VMs): siehe [`manuals/deployment.md`](manuals/deployment.md).

---

## 4. Die tägliche Ausführung

Der Normalbetrieb besteht aus genau **einem Dauerdienst** und **einem Cron-Job**:

- **`catalog_service.py`** läuft 24/7 (systemd, `Restart=always`) und legt stündlich Tick-ZIPs in `data/import/` ab.
- **`daily_orchestrator.py`** läuft einmal täglich (Cron) und durchläuft die 5 Phasen.

```bash
# Täglicher Lauf (catalog_service.py hat die ZIPs bereits befüllt)
python3 automation/daily_orchestrator.py --skip-api-fetch

# Dry-Run: Phasen 1–4, KEIN Bot-Start (sicher zum Testen)
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Mit API-Backfill der letzten 7 Tage (z. B. wenn data/import/ leer ist)
python3 automation/daily_orchestrator.py
```

Vollständiger Ablauf Schritt für Schritt: [`manuals/momentum_ls.md`](manuals/momentum_ls.md) und [`manuals/end_to_end_workflow.md`](manuals/end_to_end_workflow.md).

---

## 5. Befehls-Cheatsheet

```bash
# Einzelne Dienste manuell
python3 automation/universe_fetcher.py            # Universe aktualisieren
python3 automation/api_backfiller.py --days 7     # 7-Tage-Backfill
python3 automation/historical_fetcher.py --months 12   # Deep Backfill (Erstbefüllung)
python3 automation/catalog_service.py             # 24/7-Tick-Sammlung (systemd-fähig)

# Live-Bot manuell (erfordert ETORO_CONFIRM_LIVE=1; ohne das Flag automatisch Dry-Run)
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json

# Katalog vollständig neu aufbauen (nach Precision-Fixes zwingend nötig)
python3 automation/daily_orchestrator.py --reset-catalog
```

> ℹ️ **Modul- vs. Skript-Aufruf:** `AGENTS.md` dokumentiert zusätzlich die Modul-Form (`python3 -m automation.daily_orchestrator ...`). Falls systemd-Unit-Files im Repo existieren, müssen deren `ExecStart`-Zeilen konsistent zur gewählten Aufrufform sein.

---

## 6. Konfiguration: die fünf JSON-Dateien

Alle Konfiguration ist **deklarativ** in `automation/config/`. Für die reguläre Strategie-Optimierung muss **kein Python-Code** angefasst werden (siehe [`manuals/strategie_optimierung.md`](manuals/strategie_optimierung.md)).

| Datei | Zweck |
|-------|-------|
| `backtest.json` | Simulationsrahmen: `start_capital`, `walk_forward` (`is_window_days`, `oos_window_days`, `splits`), `span_tolerance_days` (= 3.0), `spread_bps_by_asset_class`, `commission_bps`, `min_bars_for_backtest` |
| `instrument_map.json` | `{etoro_id: {symbol, asset_class, price_precision, size_precision}}` — Zuordnung eToro-ID ↔ Nautilus-Symbol |
| `strategies.json` | Aktive Strategie-Liste (`active`), `params`-Overrides, `tournament_overrides` |
| `strategy_defaults.json` | Per-Strategie-Defaults (1h-optimiert, `trade_amount_pct=15.0`) |
| `tournament.json` | Selektionskriterien (`min_trades=20`, `min_total_return=0.005`, `min_sortino=0.3`, `min_profit_factor=1.1`, `min_win_rate`, `min_expectancy`, `k_shrinkage`) + Score-Gewichte |

**Merge-Reihenfolge der Strategie-Parameter (niedrig → hoch):**
`strategy_defaults.json` → `params` aus `strategies.json` → vom Backtest-Runner injiziert (`instrument_id`, `bar_type`).

> ℹ️ **Score-Gewichtung und Gating-Logik** sind ein zentraler Optimierungs-Hebel. Aktuelle Formel:
> `Score = Sortino·0.4 + ProfitFactor·0.3 + WinRate·0.2 − MaxDrawdown·0.1`.
> Geplante mathematische Verbesserungen (Multiple-Testing-Korrektur, kostengewichteter Score, Stabilitäts-Selektion) siehe [`manuals/feature_roadmap.md`](manuals/feature_roadmap.md).

---

## 7. Datenformat: FixedSizeBinary(16) kurz erklärt

Das Rust-Backend von NautilusTrader erwartet Preise und Größen intern als 128-Bit-Integer im `FixedSizeBinary(16)`-Format:

```
raw = round(value · 10^16)        # NICHT 10^precision!
bytes = struct.pack("<q", raw_lo) + 8 Byte Padding   # 16-Byte-Little-Endian-Layout
```

**Wichtig:** Skaliert wird mit **`10^16`** (High-Precision i128), **nicht** mit `10^price_precision`. Eine falsche Skalierung führte historisch zu „0 Trades" über alle Backtests (siehe `AGENTS.md`, Pitfall #29). Der Encoder lebt in `automation/_serde.py`.

**Pflicht-Spalten:** `bid_price, ask_price, bid_size, ask_size, ts_event, ts_init`.
**Pflicht-Byte-Metadaten je Parquet:** `b"price_precision"`, `b"size_precision"`, `b"instrument_id"`.

Da alle Quellen bereits FSB(16) liefern, ist der Merge nur noch `pa.concat_tables` + `ts_event`-Dedup + atomarer Write. Eine ausführliche Schema-/Encoding-Referenz steht in [`manuals/automation_manual.md`](manuals/automation_manual.md), Kapitel 3.

---

## 8. Precision-System (Nachkommastellen)

Einzige Quelle der Wahrheit: `automation/utils._fallback_precisions(symbol) -> (price_precision, size_precision)`. Sie greift, wenn die eToro-API oder die Parquet-Metadaten keine Precision liefern.

| Kategorie | price_precision | size_precision |
|-----------|-----------------|----------------|
| SHIB / PEPE (Meme-Coins) | 8 | 8 |
| Crypto (BTC, ETH, SOL, …) | 2 | **8** |
| Forex / Rohstoffe (NATGAS, PALL, …) | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> ✅ **Pitfall #14 / #23 — GELÖST:** Aktien nutzen `size_precision=2` (früher fälschlich `0`). Der frühere `ValueError`/0-Trades-Crash bei fractional Equities ist behoben. **Achtung:** Ältere Parquet-Daten können noch `size_precision=0` tragen — nach einem Precision-Fix ist `--reset-catalog` zwingend.

> ⚠️ **Krypto manuell registrieren!** Wird ein Krypto-Asset nicht in `_CRYPTO_SYMBOLS` (`automation/utils.py`) eingetragen, greift fälschlich die Aktien-Heuristik (`size_precision=2`) → **Rust-Backend-Crash** bei Orders wie `0.00005 BTC`. Anleitung: [`manuals/new_tickers.md`](manuals/new_tickers.md), Ausnahme B.

---

## 9. Backtest, Walk-Forward & Turnier

### Walk-Forward-Methodik

Es läuft ein **einziger, durchgehender Engine-Run** über die volle Spanne. Die Aufteilung in **In-Sample (IS)** und **Out-of-Sample (OOS)** erfolgt **retrospektiv** per Timestamp-Filterung in `extract_metrics` — der Nautilus-Rust-Core wird dabei nicht unterbrochen.

> ⚠️ **„State Bleed" (bewusst akzeptierter Kompromiss):** An der IS/OOS-Grenze findet **kein** Engine-Reset statt. Offene Positionen, Kontostand und aufgewärmte Indikatoren (EMAs, RSI …) fließen ungefiltert aus IS in OOS. OOS-Ergebnisse sind dadurch methodisch **nicht 100 % „rein"**. Dieser Kompromiss minimiert Laufzeit/Overhead.
> Ein sauberer Hard-Reset mit Embargo ist als Optimierung dokumentiert — siehe [`manuals/feature_roadmap.md`](manuals/feature_roadmap.md), Abschnitt A.

### FIFO-Matching & Metriken

- PnL via FIFO-Matching über `generate_fills_report()` (Fallback `generate_order_fills_report()`). Die FIFO-Schleife iteriert **immer über das gesamte Datenset (IS + OOS)**; erst *danach* werden die PnL-Tupel am Cutoff separiert (sonst korrumpieren offene Queues — `AGENTS.md`, Pitfall #32).
- Spread-Modellierung: Backtest-Ticks erhalten einen Asset-Class-spezifischen Spread (`spread_bps_by_asset_class`, z. B. EQUITY 8 bps, CRYPTO 15 bps), der direkt in `load_ticks_from_catalog` rekonstruiert wird. Zusätzlich `commission_bps` im FIFO-PnL. Das verhindert „Zero-Spread"-Artefakte (künstlich hohe Sortino/PF).
- Risiko-Ratios werden bei degenerierten Nennern hart gekappt (Sortino/PF auf **50.0**, Calmar auf **100.0**). Bei 1-Loss-Low-Sample-Szenarien (< 50 Trades) gilt ein hartes Cap von **2.0**. All-Win-/zu-wenig-Loss-Szenarien liefern `None` (gerendert als `n/a(win)`).

### Turnier-Gating (Eligibilität)

Ein Strategie-Symbol-Paar ist nur valider Kandidat, wenn **alle** harten Kriterien erfüllt sind …
- `min_trades ≥ 20`
- `min_total_return > 0.005` (0,5 %, net-of-spread)

… **und mindestens eine** der weichen Bedingungen:
- `Sortino ≥ 0.3` **oder** `Profit Factor ≥ 1.1`

**Selektionsreihenfolge: „Rank first, Gate second"** (`AGENTS.md`, Issue #257). Erst wird auf der gesamten IS-tauglichen Population normalisiert/gerankt, dann wird pro Symbol absteigend iteriert, bis der erste Kandidat das OOS-Gate besteht. Der OOS-Decision-Trail wird als `[OOS-Drop]` geloggt.

### Aggregat-Gewinner (hybride, bewusst gemischte Aggregation)

> ℹ️ Diese hybride Struktur ist **gewollt** — nicht „reparieren":
> - **Volumen** (Trades, Wins): absolut aufsummiert; `win_rate` = Portfolio-Wins / Portfolio-Trades (Count-Ratio).
> - **Rendite** (`total_return`): kapital-/trade-gewichteter Mittelwert.
> - **Risiko-Ratios** (Sortino, PF): **Median** (Sentinel-Werte 50.0 werden ausgefiltert).
> - **`max_drawdown`**: aus **chronologisch gemergten OOS-Einzeltrades** (echte Portfolio-Equity-Kurve), **nicht** mehr als Median der Pair-Drawdowns (`AGENTS.md`, Issue #286/#303).
> Die vollständige Begründung der gemischten Aggregations-Basis steht in [`manuals/feature_roadmap.md`](manuals/feature_roadmap.md), Abschnitt G.

---

## 10. Live-Deployment & Safety-Interlock

Der Live-Bot (`momentum_ls_run.py`) startet als **detached Subprozess** und liest `per_symbol_winners` aus dem Turnier-JSON. Der Live-`bar_type` ist zwingend `{symbol}-1-HOUR-MID-INTERNAL` (eToro streamt nur QuoteTicks).

**Zweistufiges Fail-Closed-Verhalten (Phase 5):**
1. **Per-Pair-Check:** `fully_eligible_pairs > 0` **und** `winner_count > 0`, sonst harter Abbruch (`LIVE_DEPLOY_ABORTED`).
2. **Aggregat-OOS-Check:** Der Aggregat-Gewinner muss `oos_evaluated == True` **und** `oos_eligible == True` vorweisen.

> 🔒 **Live-Trading-Sicherheitsregel (absolut):** **Null** OOS-taugliche Paare verhindern jeden Live-Deploy. Ein bestandenes Aggregat-OOS kann ein Per-Pair-Versagen **niemals** überstimmen. Kein Symbol-Strategie-Paar wird live geschaltet, solange seine Strategie nicht im Turnier OOS-tauglich verifiziert wurde (`OOS-DEPLOY-REJECT`-Filter in `_build_bots_config`).

**Dreistufiger Echtgeld-Interlock** (alle drei nötig, sonst `sys.exit(1)`):

| Stufe | Parameter | Erwarteter Wert |
|-------|-----------|-----------------|
| 1 | `environment` | `"real"` |
| 2 | `dry_run` | `False` |
| 3 | `ETORO_CONFIRM_LIVE` | `"1"` (Umgebungsvariable / `.env`) |

Fehlt eine Bedingung, läuft der Bot automatisch im **Dry-Run** (keine echten Orders). Notfall-Abschaltung, Graceful Shutdown und State-Integrität: [`manuals/run_bot_manual.md`](manuals/run_bot_manual.md), Kapitel 8.

**Kapital-Allocator (`MomentumLSAllocator`):**
- **No-Interference:** existiert eine offene Position für ein Symbol → Allokation `0.0`.
- **Dynamisches Slicing:** freies Kapital ÷ Anzahl Symbole ohne offene Position.
- **Floor:** errechneter Betrag < `$11.00` → `0.0` (eToro-Mindestbetrag).

> ℹ️ Das naive Gleichverteilungs-Slicing ist ein bekannter Optimierungspunkt. Risiko-adjustierte Sizing-Verfahren (Volatility-Targeting, fraktionales Kelly) siehe [`manuals/feature_roadmap.md`](manuals/feature_roadmap.md), Abschnitt B.

---

## 11. Paper-Trading-Guardrails (GR-01–04)

> 🟡 **Status: geplant, nicht implementiert.** Dieses Kapitel beschreibt vier Sicherheitsmechanismen, die den Live-/Paper-Betrieb **zusätzlich** zum Safety-Interlock aus [Kapitel 10](#10-live-deployment--safety-interlock) absichern sollen. Sie sind als GitHub-Issues **#710–#718** spezifiziert (Katalog: `ISSUES_paper_trading_guardrails_20260718.md`), aber noch nicht gemergt. Der Abschnitt beantwortet die Frage: *Was ändert sich für den Betrieb, sobald diese Issues umgesetzt sind?*

### Warum das nötig ist

Der bestehende Bot (Kapitel 9–10) trifft eine **einmalige** Entscheidung: Turnier bestehen → live schalten. Was danach mit einer einzelnen offenen Position passiert — wie lange sie offen bleibt, ob der Markt gerade handelbar ist, wie viel Kapital insgesamt riskiert wird, was nach einem Verbindungsabbruch mit ihr geschieht — ist heute **nicht** durchgängig abgesichert. Die vier Guardrails schließen genau diese vier Lücken, unabhängig davon, welche Strategie gerade aktiv ist.

### GR-01 — Die 24-Stunden-Zeitbox

**Regel:** Keine Position bleibt länger als 24 Stunden offen — unabhängig davon, ob sie gerade gewinnt oder verliert.

**Warum:** Eine Position, die "auf Erholung wartet", bindet Kapital und Risiko unbegrenzt. Eine feste Obergrenze erzwingt Diszipliniertheit: Gewinn mitnehmen oder Verlust begrenzen, aber spätestens nach einem Tag raus.

**Wie umgesetzt:** Der Bot zählt bereits heute die Anzahl der 1-Stunden-Kerzen ("Bars"), seit eine Position eröffnet wurde. Nach 24 solchen Bars wird zwangsweise geschlossen. Umgesetzt wird lediglich, dass **kein** Parameter — weder Default noch die vom Optimizer durchsuchten Wertebereiche — diese 24er-Grenze mehr überschreiten darf (heute sind bis zu 120 Bars, also 5 Tage, möglich). Übersteht eine Position einen Neustart des Bots, wird ihr Alter aus dem gespeicherten Zustand rekonstruiert, damit die Zählung nicht bei null neu beginnt (siehe GR-04).

### GR-02 — Der Spread-Filter

**Regel:** Ist der Unterschied zwischen Kauf- und Verkaufspreis (Spread) gerade ungewöhnlich groß, wird **kein** neuer Trade eröffnet.

**Warum:** Ein breiter Spread bedeutet schlechte Handelsbedingungen — oft ausgelöst durch geringe Liquidität, Marktöffnung/-schluss oder Nachrichtenereignisse. Ein Einstieg zu diesem Zeitpunkt kostet überproportional viel allein durch die Differenz zwischen Kauf- und Verkaufspreis, bevor sich die Position überhaupt bewegt hat.

**Wie umgesetzt:** Der Spread wird heute nur **im Backtest** als Kostenmodell simuliert, hat aber im Live-Betrieb keine Wirkung. Neu: Direkt vor jeder Order wird der aktuelle Spread gegen einen Schwellenwert geprüft (abgeleitet aus dem ohnehin je Symbol hinterlegten Referenzwert); liegt er darüber, wird der Einstieg abgelehnt und protokolliert.

### GR-03 — Die Positions- und Kapital-Obergrenze

**Regel:** Es gibt eine feste Obergrenze, wie viele Positionen gleichzeitig über **alle** Strategien hinweg offen sein dürfen, und wie groß eine einzelne Order maximal sein darf.

**Warum:** Jede Strategie hat heute ihre eigene, unabhängige Obergrenze — aber niemand behält den Überblick, wie viel das System als Ganzes gerade riskiert, wenn mehrere Strategien gleichzeitig aktiv sind. Ohne eine Gesamtgrenze kann das System theoretisch weit mehr Kapital binden, als beabsichtigt.

**Wie umgesetzt:** Zusätzlich zur bestehenden Pro-Strategie-Grenze wird eine **system-weite** Obergrenze für die Gesamtzahl offener Positionen eingeführt, plus ein harter Höchstbetrag pro einzelner Order (bisher wird eine Order nur relativ zum verfügbaren Kapital bemessen, nie mit einer absoluten Obergrenze gedeckelt).

### GR-04 — Der Abgleich nach Verbindungsabbruch

**Regel:** Nach jedem Neustart oder Verbindungsabbruch gleicht der Bot seinen eigenen Datenstand **vollständig** mit dem tatsächlichen Kontostand bei eToro ab, bevor er weiterarbeitet.

**Warum:** Verbindungen brechen ab — WLAN-Aussetzer, Server-Neustart, eToro-Wartung. Kommt der Bot zurück online, muss er wissen: Ist eine Position, die er für offen hält, bei eToro vielleicht längst geschlossen worden (durch manuelles Eingreifen oder eine automatische Liquidation)? Und: Ist eine Position während der Downtime unbemerkt über ihre 24-Stunden-Grenze (GR-01) hinausgelaufen?

**Wie umgesetzt:** Heute wird nur auf Order-Ebene abgeglichen, verwaiste "Phantom-Positionen" müssen von Hand bereinigt werden. Neu: Ein automatischer Abgleich bei jedem Reconnect vergleicht den kompletten offenen Positionsbestand. Bei eToro geschlossene, im Bot aber noch offene Positionen werden automatisch nachgezogen; Positionen, deren 24-Stunden-Fenster während der Downtime abgelaufen ist, werden sofort geschlossen. Dafür merkt sich der Bot ab sofort **wann** jede Position eröffnet wurde, nicht nur, dass sie offen ist.

### Zusätzlich: eine "klügere" Gewinnmitnahme

Losgelöst von den vier Guardrails, aber im selben Zuge umgesetzt: Statt eines einmalig fixen Gewinnziels pro Position kann optional ein **Ziel, das mit der Zeit näher an den Einstiegspreis heranrückt**, aktiviert werden. Der Gedanke: Je näher die 24-Stunden-Grenze rückt, desto eher soll auch ein kleinerer, aber sicherer Gewinn mitgenommen werden, statt stur auf das ursprüngliche Ziel zu warten. Dieser Mechanismus ist rein optional (Default: aus) und ändert am bestehenden Verhalten nichts, solange er nicht aktiv eingeschaltet wird.

### Was sich für den Optimierer ändert

Der Prozess, der die besten Strategie-Parameter sucht (Kapitel 9), bewertet Kandidaten künftig nicht mehr nur nach Rendite und Risiko, sondern erhält einen **zusätzlichen, kleinen Abschlag** für Positionen, die unnötig lange offen bleiben — als sanften Anreiz, zügig abzuschließen, statt die 24-Stunden-Grenze routinemäßig auszureizen. Die bestehende, bereits gut erprobte Bewertungsformel wird dabei **erweitert**, nicht ersetzt: Bei ausgeschaltetem Abschlag (Default) verhält sich alles exakt wie bisher.

| Guardrail / Erweiterung | Löst | Issue(s) |
|---|---|---|
| GR-01 — 24h-Zeitbox | Positionen ohne Obergrenze für die Haltedauer | #714, #717 |
| GR-02 — Spread-Filter | Einstiege bei schlechten, teuren Handelsbedingungen | #715 |
| GR-03 — Positions-/Kapital-Cap | Fehlende system-weite Risikogrenze über alle Strategien | #716 |
| GR-04 — Reconnect-Abgleich | Manuelle Nacharbeit + unbemerkt abgelaufene Positionen nach Ausfällen | #717 |
| Dynamisches Gewinnziel | Starres Zielprofit ohne Rücksicht auf die verstreichende Zeit | #712, #713 |
| Zeitbox-Abschlag im Optimizer | Optimierer "belohnt" bisher langes Halten nicht extra, bestraft es aber auch nicht | #710, #711 |

**Betroffene Dateien.** `automation/strategies/hourly_strategy_base.py`, `automation/adapters/etoro_state_manager.py`, `automation/adapters/etoro_execution.py`, `automation/optimizer/spaces.py`, `automation/config/strategy_defaults.json`, `automation/reward.py`. Vollständige technische Spezifikation (Root-Cause, Mathematik, Akzeptanzkriterien pro Issue): `ISSUES_paper_trading_guardrails_20260718.md`.

---

## 12. Verzeichnis- & Log-Struktur

| Pfad | Inhalt |
|------|--------|
| `data/import/` | ZIP-Drop-Zone von `catalog_service.py` (auto-gelöscht nach Merge) |
| `data/nautilus/data/quote_tick/{symbol}/data.parquet` | QuoteTicks (FSB16) |
| `data/nautilus/data/cfd/{symbol}/*.parquet` | Cfd-Instrument-Definitionen (size_precision!) |
| `data/state/execution_mapping.json` | eToro-Order-IDs ↔ Nautilus-Mapping |
| `data/state/size_increment_cache.json` | Precision-Cache |
| `data/state/inception_bounds.json` | Cache für historische Tiefe junger Instrumente |
| `data/state/live_bot.pid` | Aktuelle Bot-PID |
| `data/universe/momentum_ls.json` | Universe-Snapshot (`fetched_at` + `universe[]`) |
| `logs/orchestrator_YYYYMMDD.log` | Pipeline-Hauptlog (RotatingFileHandler, 1 MB, 5 Backups) |
| `logs/live_bot_YYYYMMDD.log` | Bot-Laufzeit-Log |
| `logs/tournament_YYYY-MM-DD.json` | Vollständiges Turnier-Resultat |
| `logs/optimizer_{run_id}.log` | Sweep/Optimizer: EIN Lauf = eine nicht-rotierende Datei (Issue #740) |
| `logs/optimizer_{run_id}.events.jsonl` | Sweep/Optimizer: dieselben strukturierten Events als valides JSONL (Issue #741) |
| `data/optimizer/reports/run_{run_id}.json` | Aggregierter Sweep-Forensik-Report inkl. Invarianz-Checks (Issue #742/#743) |

> **Git-Hygiene:** Lokale `.log`- und `.json`-Dateien aus `logs/` gehören **nicht** ins Git-Tracking (Repo-Bloat / blockierte Pushes). `git checkout origin/main -- logs/` oder die Dateien explizit unstagen.

> **Zweistufige Retention (Issue #746):** `logs/*.log*` und `logs/*.jsonl` (Rohdaten: Prosa-Logs +
> JSONL-Event-Sidecars) werden von `log_manager.cleanup_old_logs` nach 7 Tagen automatisch gelöscht.
> `data/optimizer/reports/*.json` (aggregierte Sweep-Reports) liegt bewusst **ausserhalb** dieses
> Verzeichnisbaums und unterliegt keiner automatischen Löschung — ein Report ist klein und bündelt
> die forensisch relevanten Kennzahlen eines Laufs dauerhaft, auch nachdem dessen Rohlogs längst
> rotiert/gelöscht sind.

---

## 13. Tests & Pre-Flight-Checks

```bash
# Vollständige Test-Suite (Unit-Tests des automation-Pakets)
pytest automation/tests/ -v

# Pre-Flight (schnelle Import-/Konfig-Verifikation)
python3 -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python3 -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente')"
```

Alle Tests respektieren das Standalone-Prinzip (AST-Prüfung in `test_automation_isolation.py`). Roundtrip-Tests und `total_trades > 0`-Assertions stellen sicher, dass echte Fills erzeugt werden (nicht nur „kein Crash").

> ℹ️ **Test-Verzeichnis:** Tests liegen im `automation/`-Paket (`automation/tests/`). Einige ältere Handbücher referenzieren noch `tests/` ohne Präfix — siehe [Kapitel 16](#16-dokumentations-bereinigung-offene-issues). Vollständige Test-Anleitung: [`manuals/TESTING.md`](manuals/TESTING.md).

---

## 14. Dokumentations-Landkarte

| Handbuch | Inhalt | Status |
|----------|--------|--------|
| [`automation/AGENTS.md`](automation/AGENTS.md) | **Autoritative** Architektur-Doku, alle Pitfalls, Changelog. Vor jeder Code-Änderung lesen. | ✅ maßgeblich |
| [`manuals/deployment.md`](manuals/deployment.md) | VM-Setup, systemd-Service, Cron-Job, Swap-File | ✅ aktuell |
| [`manuals/momentum_ls.md`](manuals/momentum_ls.md) | Momentum-LS-Pipeline im Detail | ✅ aktuell |
| [`manuals/strategie_optimierung.md`](manuals/strategie_optimierung.md) | Deklarative Optimierung via JSON, Dry-Run-Auswertung | ✅ aktuell |
| [`manuals/TESTING.md`](manuals/TESTING.md) | Tests, Konnektivität, Order-Ausführungstests | 🟡 Test-Pfad prüfen (Issue DOC-8) |
| [`manuals/run_bot_manual.md`](manuals/run_bot_manual.md) | Standalone-Bot-Betrieb (Raspberry Pi), Notfall-Ops | ✅ aktuell |
| [`manuals/automation_manual.md`](manuals/automation_manual.md) | Technische Pipeline-/Format-Referenz | 🟡 korrigiert (siehe Issues DOC-1, DOC-2, DOC-4, DOC-5) |
| [`manuals/new_tickers.md`](manuals/new_tickers.md) | Neue Instrumente hinzufügen | 🟡 korrigiert (siehe Issue DOC-3) |
| [`manuals/feature_roadmap.md`](manuals/feature_roadmap.md) | **Geplante mathematische/logische Optimierungen** + Klärung unklarer Architekturpunkte | 🆕 neu |
| [`manuals/end_to_end_workflow.md`](manuals/end_to_end_workflow.md) | Vollständige 5-Phasen-Pipeline | (im Repo) |
| [`manuals/backtesting_manual.md`](manuals/backtesting_manual.md) | Backtest-Konfiguration und Auswertung | (im Repo) |
| [`manuals/feature_automation_LS.md`](manuals/feature_automation_LS.md) | Implementierungsstatus | (im Repo) |
| [`ISSUES_paper_trading_guardrails_20260718.md`](ISSUES_paper_trading_guardrails_20260718.md) | Vollspezifikation der Guardrails GR-01–04 + Zeitbox-Optimierung (Issues #710–#718, s. [Kapitel 11](#11-paper-trading-guardrails-gr-01-04)) | 🆕 geplant |

**Faustregel zur Quellenhierarchie:** Bei Widersprüchen gilt immer der **Code** vor `AGENTS.md` vor den übrigen Handbüchern. `AGENTS.md` ist bei jeder strukturellen Änderung aktuell zu halten.

---

## 15. Bekannte offene Punkte & Roadmap

Diese Themen sind bewusste Kompromisse oder Optimierungspotenziale. Sie sind **kein Bug**, aber relevant für die Weiterentwicklung. Vollständige Ausarbeitung mit Mathematik, erwartetem Effekt und Umsetzungsplan: **[`manuals/feature_roadmap.md`](manuals/feature_roadmap.md)**.

- **Selektions-Integrität:** Das Turnier testet (8 Strategien × N Symbole) — massives Multiple-Testing. Der beste Sortino ist durch Selection-Bias überhöht. → Deflated Sharpe Ratio, PBO, Multiple-Testing-Korrektur.
- **Position-Sizing:** Allocator-Gleichverteilung ignoriert Volatilität. → Volatility-Targeting / inverse-Vol / fraktionales Kelly.
- **Long-only-Constraint:** eToro lehnt REAL-Shorts ab → Krypto-Metriken brechen in Bärenmärkten ein (intendiert, organisch im OOS-Gate gefiltert). → Regime-Gate + Cash-Overlay statt synthetischer Shorts.
- **OOS-„State Bleed":** keine Reset/Embargo an der IS/OOS-Grenze. → Hard-Reset + Embargo / Purged CV.
- **Kosten im Score:** Turnover wird nicht penalisiert. → kostengewichteter Net-Score.

---

## 16. Dokumentations-Bereinigung (offene Issues)

Bei der Konsolidierung wurden Inkonsistenzen zwischen den Handbüchern und dem dokumentierten Code-Stand gefunden. Sie sind als GitHub-Issues im AGENTS.md-Format aufbereitet in **[`DOC_CLEANUP_ISSUES.md`](DOC_CLEANUP_ISSUES.md)** (zum direkten Einstellen als GitHub Issues / Jules-Prompts):

| Issue | Kurzbeschreibung | Betroffen | Status |
|-------|------------------|-----------|--------|
| **DOC-1** | `_fallback_precisions` zeigt `return 2, 0` (Equity) — widerspricht size_precision=2 | `automation_manual.md` | ✅ in dieser Lieferung korrigiert |
| **DOC-2** | Veraltetes „Deterministisches 7-Tage-Zeitfenster" statt Walk-Forward | `automation_manual.md` | ✅ korrigiert |
| **DOC-3** | Instrument-Map-Quelle widersprüchlich (`instrument_map.py` vs. `instrument_map.json`) | `new_tickers.md` | ✅ korrigiert |
| **DOC-4** | `PF=999` als Sentinel dokumentiert, real ist Cap = 50.0 | `automation_manual.md`, `momentum_ls.md` | ✅ teils korrigiert |
| **DOC-5** | Safety-Interlock referenziert Root-`config/setups.py` (Standalone-Verletzung) | `automation_manual.md` | ✅ korrigiert |
| **DOC-6** | `max_drawdown`-Aggregation: interne AGENTS-Inkonsistenz (Median vs. Portfolio-Equity) | `automation/AGENTS.md` | 🟡 in DOC_CLEANUP_ISSUES dokumentiert |
| **DOC-7** | NautilusTrader-Versionsdiskrepanz (≥1.200 vs. ≥1.226.0) | `AGENTS.md`, `deployment.md` | 🟡 dokumentiert |
| **DOC-8** | Test-Pfad `tests/` vs. `automation/tests/` | `README`, `TESTING.md` | 🟡 dokumentiert |

---

*Master-README erstellt am 2026-06-09, zuletzt erweitert am 2026-07-18 (Kapitel 11: Paper-Trading-Guardrails). Bei strukturellen Änderungen sind dieses Dokument und `automation/AGENTS.md` synchron zu halten.*
