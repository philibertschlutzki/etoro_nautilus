# Architektur-Konzept: Instrumenten-spezifisches Strategie-Tuning

> **Dateiname:** `manuals/instrument_specific_tuning_konzept.md`
> **System-Version:** Konzept v2.2 (Umsetzungsplan)
> **Zielgruppe:** Architekten, Quant-Entwickler, Operatoren, Jules-Coding-Agent
> **Status:** Proposal → **Implementation-Ready** für Ansatz 3 & 4
> **Geltungsbereich:** Nur `automation/` (Standalone-Produkt). Standalone-Prinzip (§4 `AGENTS.md`) gilt strikt.
> **Verbindliche Konventionen:** Zero-Hardcoding, TDD, Dependency Injection, AGENTS.md-Pflicht, chirurgische Commits, CI-Gate grün — exakt wie in `closedloop_autotuner/claude.md`.

---

## Kapitel 1: Ist-Analyse & Verankerung im Code

### 1.1 Wo die Universums-Mittelung tatsächlich entsteht

Die ursprüngliche Beobachtung ist korrekt: Der Optimizer tunt Parameter so, dass sie „im Durchschnitt" über das gesamte Universum den höchsten Reward erzielen. Eine forensische Durchsicht des Codes lokalisiert die Mittelung an **genau drei Hebeln** — entscheidend, weil Ansatz 3 und 4 jeweils an unterschiedlichen Hebeln ansetzen:

| Hebel | Fundstelle | Wirkung | Adressiert durch |
|---|---|---|---|
| **H1 — Ein globaler Parametersatz pro Strategie** | `optimizer/trial_config.py::build_trial` erzeugt ein Manifest mit **genau einer** Strategie und **einem** `params`-Block; `run_optimization.py::optimize` führt **eine** Study `study_{strategy}` gegen das **volle** Universum. | Ein einziger Parametervektor muss alle Instrumente bedienen → Kompromiss-Lösung. | **Ansatz 4** (pro Symbol eigener Vektor) |
| **H2 — Coverage-Bonus im Reward** | `optimizer/reward.py::compute_reward`: `coverage = win_count / max(1, universe_size)`, gewichtet mit `bonus_coverage_weight = 1.0`. | Belohnt Parameter, die auf **vielen** Symbolen gewinnen → drängt aktiv zur Breite statt zur Tiefe. | **Ansatz 4** (Coverage entfällt/wird neu definiert) |
| **H3 — Absolute Schwellen statt volatilitäts-relativer** | z. B. `VwapExhaustionStrategy.deviation_threshold = 0.008` (fixe 0,8 %); `ComboTrendVwapStrategy` Trend-Toleranz hartcodiert `* 0.98`/`* 1.02` (= fixe 2 %). | Eine fixe Prozent-Schwelle passt nicht gleichzeitig zu einem ruhigen ETF und zu einem volatilen Meme-Coin. | **Ansatz 3** (Schwellen → ATR-Multiplikator) |

**Konsequenz für die Sequenzierung:** Ansatz 3 macht Parameter **skaleninvariant** und reduziert dadurch den verbleibenden instrument-spezifischen Bedarf. Ansatz 4 fängt anschließend nur noch den **echten Residual-Edge** pro Symbol ab — mit starker Regularisierung, damit er verfeinert statt auswendig lernt. **Ansatz 3 ist damit der natürliche Vorläufer von Ansatz 4.** Diese Reihenfolge ist nicht kosmetisch: Ohne Ansatz 3 muss Ansatz 4 jede Volatilitäts-Anpassung pro Symbol „nacherfinden" und überfittet entsprechend leichter.

### 1.2 Code-Befunde, die den Plan tragen

Aus der Prüfung des Anhangs (relevant für die Umsetzbarkeit, nicht erschöpfend):

- **Einzige Parameter-Resolution für den Optimizer:** `optimizer/resolve.py::resolve_params` (`defaults < strategies.json[params] < sampled`). Dies ist der **einzige** Punkt, an dem die `instrument_overrides`-Hierarchie für die Such-Basis andocken muss.
- **Zweite, getrennte Resolution für Live/Matrix:** `backtest_runner.py::resolve_strategy_params` (`is_manifest=True` ⇒ verbatim; sonst `{**defaults, **params}`). Der **Live-Matrix-Pfad** (Daily-Orchestrator, kein `manifest_version`) und `momentum_ls_run.py` müssen `instrument_overrides[symbol]` zusätzlich auflösen.
- **Reward-Coverage ist der Mittelungs-Hebel:** `coverage = win_count / max(1, universe_size)`. Bei Single-Symbol wird `universe_size = 1` und `win_count ∈ {0,1}` → der Term degeneriert zu binärem Rauschen und **muss** für Ansatz 4 ersetzt werden (siehe §4.5).
- **I/O-Kosten pro Trial:** `build_trial` kopiert **alle** Config-JSONs per `shutil.copy2` in jedes Trial-Verzeichnis. Bei 42 000 Trials (Ansatz 4) ist das ein ernster I/O-Engpass → Optimierung in §4.2.4.
- **Storage aktuell SQLite-exklusiv** (Pitfall #53/#68, Invariante G4d). Pitfall #68 hat zwar einen Busy-Timeout für `n_jobs>1` ergänzt, warnt aber explizit vor Determinismus-/Locking-Problemen. Ansatz 4 in voller Parallelität **erzwingt** den Wechsel auf eine RDB (Postgres) — bereits in `konzept_automatisierte_strategie_optimierung_v2.md` §10 vorgesehen, aber bewusst deferred. Das ist eine explizite, zu dokumentierende Architektur-Entscheidung (siehe §4.9).
- **Bereits ATR-adaptiv (kein Ansatz-3-Bedarf):** `atr_trailing_multiplier` (Exit, `HourlyStrategyBase`), `atr_multiplier` für das BB-Touch-Fenster in `ComboTrendVwapStrategy`, sowie die Keltner-Bänder (`KeltnerChannel` = EMA ± k·ATR) und `bb_std_dev` (σ ist selbst ein Volatilitätsmaß). Ansatz 3 ist daher **kleiner als das Konzept v2.1 suggeriert** und großteils bereits realisiert — siehe Parameter-Inventar §3.2.
- **Bekannter Pfad-Bug, relevant falls Ansatz 3 mehr Indikator-Warmups aktiviert:** `_warmup_trend_filter` liest aus `data/nautilus/quote_tick/...` statt `data/nautilus/data/quote_tick/...` (CODE_AUDIT ISSUE-17). Latent, weil `trend_filter_period` per Default `0`. Vor Aktivierung volatilitäts-relativer Trend-Filter zwingend fixen.
- **Hartcodierte Schwellen sind bereits als Audit-Issues erfasst:** Trend-Toleranz `0.98/1.02` und `bb_touch_window` (ISSUE-11). Ansatz 3 überführt diese sauber nach Config — die R/S-Klassifikation des Audits gilt unverändert.

> **Recon-Pflicht für Jules (nicht im Anhang einsehbar):** `backtest_runner.py` liegt nicht vollständig vor. Vor Umsetzung von §4.4 ist die **Universum-Lade-Naht** zu identifizieren (woher kommt die Symbol-Liste der Matrix — `data/universe/momentum_ls.json`, Katalog-Scan oder CLI?). Die in §4.4 vorgeschlagene Manifest-Restriktion dockt an genau diese Naht an.

---

## Kapitel 2: Die vier Ansätze — Einordnung & Entscheidung

| Ansatz | Hebel | Overfitting-Risiko | Compute | Status in diesem Dokument |
|---|---|---|---|---|
| **1 — Asset-Class-Cluster** | H1 (gröber) | Mittel | + | Bleibt Option (Spezialfall von 4) |
| **2 — Winner-Takes-All-Reward** | H2 | **Hoch** (Glückstreffer) | 0 | Nicht empfohlen als Primärweg |
| **3 — Volatilitäts-normalisierte Parameter** | H3 | Niedrig (reduziert Risiko) | 0 | **Umsetzungsplan §3** |
| **4 — Per-Symbol Micro-Tuning** | H1 + H2 | **Höchstes** (beherrschbar mit §4.6) | Hoch (siehe §4.2) | **Umsetzungsplan §4** |

**Entscheidung:** Umsetzung von **Ansatz 3 zuerst** (skaleninvariante Parameter, geringes Risiko, kein Compute-Aufwand), anschließend **Ansatz 4** als compute-intensiver Hauptweg mit strenger Anti-Overfitting-Maschinerie. Cluster-Tuning (Ansatz 1) fällt als Sonderfall von Ansatz 4 ab (Symbol-Tiering, §4.2.6). Reines Reward-Shaping (Ansatz 2) wird als zu fragil verworfen — die Coverage-Neudefinition in §4.5 deckt den nützlichen Kern davon kontrolliert ab.

---

## Kapitel 3: Ansatz 3 — Volatilitäts-normalisierte Parameter (Umsetzungsplan)

### 3.1 Prinzip & Abgrenzung

**Was Ansatz 3 betrifft:** **Wert-/Schwellen-Parameter**, die eine Distanz, einen Abstand oder ein Ziel in **absoluten** Einheiten (Prozent, fixer Float) ausdrücken. Diese werden in **ATR-relative** Einheiten überführt: `schwelle = k · ATR / price`. Optuna optimiert dann den dimensionslosen Multiplikator `k`, der über alle Instrumente vergleichbar ist.

**Was Ansatz 3 NICHT betrifft:** **Perioden-Parameter** (`sma_period`, `bb_period`, `rsi_period`, `keltner_period`, `vwap_period`, `macd_*`). Perioden liegen auf der **Zeit-Achse**, nicht auf der **Wert-Achse**; ihre Adaptivität ist ein separates, komplexeres Thema (regime-/sample-abhängige Periodenwahl) und **explizit außerhalb** von Ansatz 3. Wer Perioden adaptiv machen will, tut das über Ansatz 4 (per-Symbol-optimierte Periode), nicht über ATR-Normalisierung.

### 3.2 Parameter-Inventar (Ist → Soll)

| Strategie | Parameter | Aktuell | Klassifikation | Soll (ATR-relativ) |
|---|---|---|---|---|
| `VwapExhaustionStrategy` | `deviation_threshold` | `0.008` (fix %) | **S** (ändert OOS-Kalibrierung) | `dev_atr_mult · (ATR/price)` |
| `ComboTrendVwapStrategy` | Trend-Toleranz | hartcodiert `0.98`/`1.02` (= 2 %) | **S** (+ behebt ISSUE-11) | `1 ± trend_atr_mult · (ATR/price)` |
| `HourlyStrategyBase` | `profit_target_pct` | fix % (Default `None`) | **S** (nur falls genutzt) | `pt_atr_mult · (ATR/price)` |
| `HourlyStrategyBase` | `atr_trailing_multiplier` | ✅ bereits Multiplikator | — | unverändert |
| `ComboTrendVwapStrategy` | `atr_multiplier` (BB-Touch) | ✅ bereits Multiplikator | — | unverändert |
| Keltner-Strategien | `keltner_multiplier` | ✅ ATR-intern (Band = EMA ± k·ATR) | — | unverändert |
| BB-Strategien | `bb_std_dev` | ✅ σ ist Volatilitätsmaß | — | unverändert |

> **R/S-Legende** (aus `CODE_AUDIT_ZERO_HARDCODING.md`): **R** = verhaltenswahrender Refactor (gleiche Zahlen, Tests bleiben grün). **S** = Semantik-Fix, ändert Zahlen → frischer Baseline-Lauf + Anpassung erwarteter Test-Werte zwingend. **Alle drei aktiven Kandidaten sind S** — sie verändern Ergebnisse bewusst.

**Befund:** Der echte Ansatz-3-Umfang sind **drei** Schwellen (`deviation_threshold`, Trend-Toleranz, optional `profit_target_pct`). Alles Übrige ist bereits volatilitäts-adaptiv. Der Aufwand ist damit überschaubar und chirurgisch.

### 3.3 Zentrale Normalisierungs-Utility (Contract)

Eine einzige Quelle für ATR-relative Schwellen in `HourlyStrategyBase`, damit keine zweite Heuristik entsteht (Konvention §18 `AGENTS.md`):

```python
# automation/strategies/hourly_strategy_base.py
def _atr_relative(self, k: float, price: float) -> float | None:
    """Liefert k · ATR / price als dimensionslose Schwelle (z. B. 0.012 = 1,2 %).
       Nutzt self._exit_atr (wird in _check_exits_and_update jeden Bar aktualisiert,
       VOR der Signal-Logik der Subklasse → Wert ist aktuell).
       Returns None, solange ATR nicht initialisiert ist (fail-closed: kein Signal)."""
    if not self._exit_atr.initialized or price <= 0:
        return None
    return k * (self._exit_atr.value / price)
```

**Architektur-Hinweise:**
- `self._exit_atr` existiert bereits und wird in `_check_exits_and_update(bar)` aktualisiert. Da Subklassen diese Methode als **erste** Aktion in `on_bar` aufrufen, ist `_exit_atr.value` zum Signalzeitpunkt aktuell. **Kein** zweiter ATR-Indikator nötig.
- **Fail-closed:** Solange ATR nicht initialisiert ist, gibt die Utility `None` zurück und die Strategie generiert **kein** Signal (statt auf einen falschen absoluten Default zu fallen).
- Die ATR-Periode für die Normalisierung ist `config.atr_period` (Default 14) — bereits optimierbar.

### 3.4 Strategie-Refactors (konkret)

**`VwapExhaustionStrategy`** — Kern der Umstellung:
```python
# ALT:
deviation = (close_price - self.current_vwap) / self.current_vwap
if deviation <= -self.config.deviation_threshold and ...:
# NEU:
thr = self._atr_relative(self.config.dev_atr_mult, close_price)
if thr is None:
    return
deviation = (close_price - self.current_vwap) / self.current_vwap
if deviation <= -thr and ...:
```
Config: `deviation_threshold: float` → ersetzt durch `dev_atr_mult: float` (Default-Kalibrierung so wählen, dass bei mittlerer Volatilität ≈ 0,8 % herauskommt — als Startpunkt, nicht als Garantie).

**`ComboTrendVwapStrategy`** — Trend-Toleranz (behebt zugleich ISSUE-11):
```python
# ALT:
trend_bullish = close_price > (self.sma.value * 0.98)
trend_bearish = close_price < (self.sma.value * 1.02)
# NEU:
tol = self._atr_relative(self.config.trend_atr_mult, close_price)
if tol is None:
    return
trend_bullish = close_price > self.sma.value * (1.0 - tol)
trend_bearish = close_price < self.sma.value * (1.0 + tol)
```
Config: neuer `trend_atr_mult: float`. Der bisherige hartcodierte 2-%-Wert entfällt.

**`HourlyStrategyBase.on_position_opened`** — `profit_target_pct` (nur falls aktiv genutzt):
ATR-relatives Profit-Target analog: `target = entry · (1 ± _atr_relative(pt_atr_mult, entry))`. Optional, da Default `None`.

### 3.5 Suchraum-Migration (`spaces.py`)

Die absoluten Ranges werden durch Multiplikator-Ranges ersetzt. Gleichzeitig empfiehlt sich die Umstellung von `spaces.py` auf eine **deklarative Bounds-Struktur** — sie ist (a) zero-hardcoding-freundlicher, (b) Voraussetzung für die Regularisierung in Ansatz 4 (§4.5, normierter Parameter-Abstand braucht die Bounds als Daten):

```python
# automation/optimizer/spaces.py (Refactor-Ziel)
SEARCH_SPACES: dict[str, dict[str, tuple]] = {
    "VwapExhaustionStrategy": {
        "dev_atr_mult":   (0.3, 3.0, "float"),   # ersetzt deviation_threshold
        "vwap_period":    (10, 50, "int"),
        "cooldown_bars":  (2, 36, "int"),
        "atr_trailing_multiplier": (0.5, 3.0, "float"),
        "max_bars_in_trade": (6, 48, "int"),
    },
    "ComboTrendVwapStrategy": {
        "trend_atr_mult": (0.2, 4.0, "float"),    # ersetzt 0.98/1.02
        # ... macd_fast/gap, sma_period, bb_period, bb_std_dev, atr_multiplier, ...
    },
    # ... übrige Strategien
}

def sample_params(strategy: str, trial) -> dict:
    space = SEARCH_SPACES.get(strategy)
    if space is None:
        raise ValueError(f"Unknown strategy: {strategy}")
    out = {}
    for name, (lo, hi, typ) in space.items():
        out[name] = (trial.suggest_int(name, lo, hi) if typ == "int"
                     else trial.suggest_float(name, lo, hi))
    # Constraints (z. B. macd_slow = macd_fast + gap) bleiben als Sonderlogik erhalten.
    return out
```

> **Migrations-Hinweis:** Bestehende Constraints (`macd_slow = macd_fast + gap`) lassen sich nicht rein deklarativ abbilden und bleiben als gezielte Sonderbehandlung im `sample_params`. Die deklarative Struktur deckt nur die unabhängigen Parameter ab.

### 3.6 Zero-Hardcoding, Backward-Compat, Tests

- **Zero-Hardcoding:** Kalibrierungs-Defaults der neuen Multiplikatoren wandern nach `strategy_defaults.json`. Keine Magic Numbers im Strategie-Code (Audit ISSUE-11 wird damit geschlossen).
- **Backward-Compat:** Da `deviation_threshold` durch `dev_atr_mult` **ersetzt** wird (nicht ergänzt), müssen `strategy_defaults.json`, `strategies.json[params]`, der Optuna-Suchraum **und** `AGENTS.md` synchron geändert werden. Ein verwaister Alt-Key in der Config würde von `msgspec.Struct` (frozen) als unbekanntes Feld abgelehnt → CI fängt das.
- **Tests (TDD):**
  - `test_atr_relative_threshold.py`: `_atr_relative` liefert `k·ATR/price`, `None` bei nicht-initialisiertem ATR. Werte aus Fixture, keine Literale im Assert.
  - Anpassung `test_backtest_trades_generated.py`: `total_trades > 0` für `VwapExhaustion` und `ComboTrendVwap` bleibt erzwungen (Konvention §18 — niemals lockern).
  - **Frischer Baseline-Lauf** (S-Issues): erwartete OOS-Werte in den Tournament-Tests neu kalibrieren und dokumentieren.

### 3.7 Jules-Auftragsschnitt für Ansatz 3

| Auftrag | Inhalt | Tests | CI-Tier | Klassifik. |
|---|---|---|---|---|
| **A3.1** | `_atr_relative`-Utility in `HourlyStrategyBase`; Pfad-Bug ISSUE-17 (`_warmup_trend_filter`) gleich mitfixen | `test_atr_relative_threshold.py` | Tier 3 | R + S |
| **A3.2** | `VwapExhaustionStrategy` auf `dev_atr_mult` umstellen; Config + `strategy_defaults.json` + `AGENTS.md` | `test_backtest_trades_generated.py` (≥ Schwelle) | Tier 3/10 | S |
| **A3.3** | `ComboTrendVwapStrategy` Trend-Toleranz auf `trend_atr_mult` (schließt ISSUE-11) | dito | Tier 3/10 | S |
| **A3.4** | `spaces.py` → deklarative `SEARCH_SPACES`; neue Multiplikator-Ranges; Constraints erhalten | `test_optimizer_*` bleiben grün | Tier 10 | R |

> Jeder Auftrag = ein PR, chirurgisch, deutsche Commit-Message, Changelog-Eintrag in `AGENTS.md` §19, Pre-Flight + `pytest`-Gate grün.

---

## Kapitel 4: Ansatz 4 — Per-Symbol Micro-Tuning (Umsetzungsplan)

### 4.1 Zielbild & zentrale Gefahr

**Zielbild:** Für jede Strategie wird je **handelbarem** Symbol ein eigener Parametervektor optimiert und — **nur bei nachgewiesenem Edge gegenüber dem globalen Vektor** — als `instrument_overrides[symbol]` in `strategies.json` promotet. Der Live-Pfad nutzt pro Symbol den spezifischsten verfügbaren Vektor.

**Zentrale Gefahr (das ganze Kapitel kreist darum):** Ein einzelnes Symbol ist ungleich leichter auszuwendiglernen als ein 70-Symbol-Universum. Ohne Gegenmaßnahmen produziert Ansatz 4 spektakuläre In-Sample-Kurven und wertlose Live-Performance. Die **Anti-Overfitting-Maschinerie (§4.6) ist nicht optional, sondern der eigentliche Inhalt** dieses Ansatzes. Die Reihenfolge ist bewusst: erst Compute klären (§4.2), dann das Datenmodell (§4.3–§4.5), **dann** — als Herzstück — die Overfitting-Gates (§4.6).

### 4.2 Compute-Evaluation mit viel Rechenpower

#### 4.2.1 Die Arbeits-Äquivalenz (zentrale Erkenntnis)

Das Konzept v2.1 nennt „70 × 10 = 700 Optuna-Studien" und suggeriert damit eine 70-fache Kostenexplosion. Das ist **irreführend**, weil es *Anzahl Studien* mit *Gesamt-Backtest-Arbeit* verwechselt.

Variablen: `S` = aktive Strategien mit Suchraum (**6**: Sma, FlashCrash, VolBreakout, ComboVwap, VwapExhaustion, HourlyMeanReversion), `N` = Symbole mit ausreichender Historie (≤ 70), `T` = Trials/Study (100), `Folds` = 4.

| | Global (Ist) | Per-Symbol (Ansatz 4) |
|---|---|---|
| Studien | `S` = 6 | `S·N` = 6·70 = **420** |
| Trials gesamt | `S·T` = 600 | `S·N·T` = **42 000** |
| Backtests **pro Trial** | `N` Symbole (Matrix, parallel im Worker-Pool) | **1** Symbol |
| **Einzel-Symbol-Engine-Runs gesamt** | `S·T·N` = **252 000** | `S·N·T` = **252 000** |

**Die Einzel-Symbol-Engine-Arbeit ist identisch** (252 000 Runs in beiden Fällen) — sofern `T` konstant bleibt. Ansatz 4 verteilt dasselbe Backtest-Budget lediglich anders: Global verteilt 100 Trials über das *gemittelte* Ziel; Per-Symbol gibt *jedem Symbol* seine eigenen 100 Trials. **Per-Symbol-Tuning kostet nicht 70× mehr Engine-Arbeit — es kostet (näherungsweise) gleich viel, fein granularer eingesetzt.**

#### 4.2.2 Overhead-Analyse (hier liegt die echte Mehrkost)

Der Unterschied liegt **nicht** in der Engine-Arbeit, sondern im **Pro-Trial-Overhead**: Subprozess-Spawn (`python automation/backtest_runner.py`), Python-/Nautilus-Import, Katalog-Index, `shutil.copy2` der Config (§1.2). Global amortisiert diesen Overhead über `N` Symbole pro Trial; Per-Symbol zahlt ihn `N`-mal häufiger.

Beispielrechnung (Annahmen **vom Operator durch gemessene Werte zu ersetzen**):
- `t_engine` ≈ 6 s (ein Single-Symbol-Backtest über IS + Folds·OOS, ~340 Tage 1h-Bars ≈ 8 000 Bars)
- `t_overhead_naiv` ≈ 8 s (Subprozess + Import + Config-Copy)
- `t_overhead_warm` ≈ 0,5 s (persistenter In-Process-Worker, §4.2.4)

| | Engine-Arbeit | Overhead (naiv, Subprozess/Trial) | Overhead (warm) |
|---|---|---|---|
| Global | 252 000·6 s ≈ **420 h** | 600·8 s ≈ 1,3 h | — |
| Per-Symbol | 252 000·6 s ≈ **420 h** | 42 000·8 s ≈ **93 h** | 42 000·0,5 s ≈ **5,8 h** |

> Korrektur der `t_engine`-Größenordnung: 252 000 · 6 s = 1 512 000 s ≈ **420 h**, identisch in beiden Spalten — das ist der Punkt. Die Differenz sind die 93 h vs. 5,8 h Overhead.

**Befund:** Naiv (Subprozess pro Trial) ist Ansatz 4 **overhead-dominiert** (93 h Overhead zusätzlich). Mit persistenten Workern kollabiert der Overhead auf ~6 h und die Engine-Arbeit (~420 h CPU) dominiert wieder. **Overhead-Reduktion (§4.2.4) ist damit die entscheidende Stellschraube**, nicht die Engine-Optimierung.

#### 4.2.3 Parallelisierungs-Architektur

Die 420 Studien sind **embarrassingly parallel** (kein gemeinsamer Zustand außer dem read-only Katalog). Mit `W` parallelen Workern: Wall-Clock ≈ 420 CPU-h / `W` (engine-gebunden, warm).

- **RDB-Backend statt SQLite (Pflicht bei voller Parallelität):** SQLite + `n_jobs>1` führt zu `database is locked` (Pitfall #68). Für `W ≫ 1` über mehrere Prozesse/Maschinen ist Postgres erforderlich (`konzept v2 §10`). → §4.9.
- **Worker-Modell:** Jeder Worker bearbeitet **ganze (Strategie, Symbol)-Studien** (nicht einzelne Trials), behält damit die Subprozess-/Fault-Isolation auf Study-Ebene (Pitfall #65) und reduziert Cross-Worker-Koordination auf null.
- **Katalog read-only:** Single-Symbol-Parquet (`data/nautilus/data/quote_tick/{symbol}/data.parquet`) ist klein; OS-Page-Cache trägt wiederholte Reads. Kein Kopieren des Katalogs.

#### 4.2.4 Overhead-Reduktion (decisive levers)

1. **Persistente In-Process-Worker statt Subprozess-pro-Trial.** Ein langlebiger Worker importiert Nautilus **einmal** und ruft eine **In-Process-Backtest-Funktion** für viele Trials. Eliminiert Import-/Spawn-Kosten (der größte Posten). Trade-off: Die Subprozess-Fault-Isolation (Pitfall #65) sinkt von Trial- auf Study-Ebene. Mitigation: Trial-Exceptions im Worker abfangen → `optuna.TrialPruned` (Per-Trial-Resilienz), aber fundamentale Fehler (ImportError etc.) crashen die **Study** hart (Fail-Fast, analog Pitfall #355). Das erfordert eine **importierbare** Backtest-Entry-Funktion in `backtest_runner.py` (heute nur CLI). → Recon: prüfen, ob `run_single_backtest_worker` / `run_backtest` sauber importierbar und seiteneffektfrei aufrufbar sind.
2. **Config-Sharing pro Study statt pro Trial.** `build_trial` kopiert heute alle JSONs in **jedes** Trial-Verzeichnis. Umstellen auf **eine** eingefrorene `config/` pro (Strategie, Symbol)-Study; pro Trial unterscheidet sich nur das `experiment_manifest.json`. Spart 42 000 → 420 Config-Kopien.
3. **Katalog-Vorwärmung.** Single-Symbol-Parquet vor Study-Start einmal in den Page-Cache lesen.
4. **Optuna-Pruning.** `MedianPruner` o. ä. bricht aussichtslose Trials früh ab. Da das Single-Symbol-Ziel rauschärmer-pro-Fold, aber querschnittlich ungemittelt ist, vorsichtig kalibrieren (Pruning-Warmup ≥ `n_startup_trials`).

#### 4.2.5 Hardware-Sizing & Laufzeit-Schätzung

Bei ~420 CPU-h Engine-Arbeit (warm) und einzelthread-artigen Backtests:

| Maschine | effektive parallele Single-Symbol-Backtests `W` | Wall-Clock (warm) |
|---|---|---|
| Mac Mini (aktuell, ~8–10 Kerne) | ~8 | ~52 h (mehrere Nächte) |
| Workstation 32 Kerne | ~24 | ~17 h (eine lange Nacht) |
| Cloud 64 vCPU | ~48 | ~9 h (Übernacht-Batch) |
| Cloud 128 vCPU / kleiner Cluster | ~96 | ~4–5 h |

> **Empfehlung für „viel Rechenpower":** Ein **32–64-Kern-Knoten** (oder kleiner Cluster) mit **persistenten Workern + Postgres-Backend** fährt den vollständigen Per-Symbol-Sweep als nächtlichen Batch. Die Zahlen sind Größenordnungen; der Operator ersetzt `t_engine`/`t_overhead` durch gemessene Werte aus einem 5-Trial-Kalibrierungslauf. Die `max_workers`-Budgetierung in `backtest.json` muss so gewählt werden, dass `parallele_Studien × max_workers ≤ verfügbare Kerne` (Konzept v2 §10) — bei In-Process-Workern entfällt der innere `ProcessPoolExecutor` pro Trial, der äußere Worker-Pool übernimmt die Parallelität.

#### 4.2.6 Symbol-Priorisierung (Budget-Reduktion ohne Qualitätsverlust)

Nicht jedes Symbol verdient 100 Trials. Drei Tiers reduzieren `N·T` drastisch:

- **Tier A — Deployable-Set:** Nur Symbole micro-tunen, die unter den **globalen** Parametern bereits Tournament-Gewinner sind (der real handelbare Kern, oft 20–40 statt 70). Das adressiert exakt den ursprünglichen Schmerz („Maximal-Edge dort holen, wo die Strategie ohnehin gewinnt") bei einem Bruchteil der Kosten.
- **Tier B — Refinement:** Symbole, auf denen die globalen Parameter knapp scheitern, bekommen einen **lokalen** Suchraum (enge Ranges um das globale Optimum) mit reduziertem `T` (z. B. 40).
- **Tier C — Ausschluss:** Symbole mit unzureichender Historie (§4.6, Gate 1) werden **gar nicht** micro-getunt und bleiben live auf globalen Parametern.

Dieses Tiering ist die saubere Verschmelzung von Ansatz 1 (Cluster) und Ansatz 4: Cluster ≈ Tier-Gruppierung nach Asset-Klasse.

### 4.3 Datenmodell: `instrument_overrides` (Schema + Resolutionsordnung)

**Schema-Erweiterung in `strategies.json`** (rückwärtskompatibel — Feld optional):
```json
{
  "active": true,
  "strategy_module": "automation.strategies.vwap_exhaustion",
  "strategy_class": "VwapExhaustionStrategy",
  "config_class": "VwapExhaustionConfig",
  "params": { "dev_atr_mult": 1.0, "vwap_period": 24, "cooldown_bars": 3 },
  "instrument_overrides": {
    "TSLA.ETORO": { "dev_atr_mult": 1.6, "vwap_period": 32, "cooldown_bars": 5 },
    "BTC.ETORO":  { "dev_atr_mult": 2.4, "vwap_period": 18, "cooldown_bars": 2 }
  }
}
```

**Resolutionsordnung (niedrig → hoch), pro (Strategie, Symbol):**
1. `strategy_defaults.json[strategy]`
2. `strategies.json[strategy].params` (globales Optimum)
3. `strategies.json[strategy].instrument_overrides[symbol]` (höchste Priorität, **nur** für dieses Symbol)

Erweiterung der **bestehenden** Resolver — **kein** neuer, paralleler Mechanismus:
```python
# optimizer/resolve.py — Such-Basis (Optimizer)
def resolve_params(strategy_class, sampled, base_cfg, *, instrument: str | None = None) -> dict:
    # ... defaults < strategies.json[params] ...
    if instrument is not None:
        ov = strat_entry.get("instrument_overrides", {}).get(instrument, {})
        params.update(ov)              # Override vor sampled, falls als Warm-Start-Basis genutzt
    params.update(sampled)             # sampled bleibt höchste Prio im Such-Trial
    return params
```
```python
# backtest_runner.py — Legacy/Matrix- und Live-Pfad
def resolve_strategy_params(strategy_entry, defaults, *, is_manifest, instrument=None) -> dict:
    if is_manifest:
        return dict(strategy_entry.get("params") or {})   # verbatim, unverändert
    merged = {**defaults, **(strategy_entry.get("params") or {})}
    if instrument is not None:
        merged.update((strategy_entry.get("instrument_overrides") or {}).get(instrument, {}))
    return merged
```

> **Wichtig:** Für die **Such-Trials** des Optimizers ist die Basis das *globale* Optimum (Schritte 1–2); der Override ist das **Ergebnis** (das Proposal). Für **Live/Matrix** ist der Override die maßgebliche Quelle. Die `is_manifest=True`-Semantik (verbatim, kein Re-Merge) bleibt strikt erhalten (Pitfall #61).

### 4.4 Single-Symbol-Manifest & Backtest-Restriktion

`build_trial` muss den Backtest auf **ein** Symbol begrenzen. Manifest-getriebener Weg (reproduzierbar, konform mit der Manifest-Autorität):

**Erweiterung `global_settings`:**
```json
"global_settings": {
  "catalog_path": "data/nautilus",
  "start_time": "...", "end_time": "...",
  "instruments": ["TSLA.ETORO"]
}
```
`backtest_runner.py` baut die Matrix als `(instruments ∩ Universum) × strategies-im-Manifest`. Fehlt `instruments` → volles Universum (rückwärtskompatibel). Bei `["TSLA.ETORO"]` × 1 Strategie = **genau ein** Job.

> **Recon-Naht (Jules):** An der in §1.2 genannten Universum-Lade-Stelle in `backtest_runner.py` einen Filter `if global_settings.get("instruments"): universe = [s for s in universe if s in set(instruments)]` einziehen. Alternativ ein `--instrument`-CLI-Flag — das Manifest-Feld ist jedoch vorzuziehen (vollständig im reproduzierbaren Manifest, kein verstecktes CLI-Verhalten).

### 4.5 Reward-Reformulierung für Single-Symbol

Der Coverage-Term (`win_count/universe_size`) degeneriert bei `universe_size=1`. Neuer Reward-Pfad (per Config-Flag `reward_mode: "per_symbol"` in `optimizer.json` **oder** automatisch bei `universe_size == 1`):

```
if not oos_evaluated or oos_sortino is None:
    return penalty_unevaluable_oos + unevaluable_shaping_span · trade_progress   # wie heute
base        = clip(oos_sortino, ±sortino_clip_abs)            # Median über Folds
overfit_gap = max(0, is_sortino_median − base)
dd_excess   = max(0, oos_max_drawdown − risk_dd_cap)
param_pen   = lambda_reg · normalized_param_distance(sampled, global_params)   # NEU, §4.6 Gate 2
return base − overfit_gap·penalty_overfit_weight
            − dd_excess·penalty_dd_weight
            − param_pen
            # KEIN coverage-Term
```

- **Coverage entfällt** (binär/sinnlos bei einem Symbol).
- **`param_pen`** ist die **Shrinkage-Regularisierung** Richtung globalem Optimum (§4.6, Gate 2). `normalized_param_distance` normiert jeden Parameter über seine `SEARCH_SPACES`-Bounds (§3.5) auf [0,1] und bildet z. B. die mittlere quadrierte Abweichung. `lambda_reg`, `reward_mode` neu in `optimizer.json` (zero-hardcoding).
- Die **Floor-Clamp/Ordnungsinvariante** (nicht-evaluierbare Trials nie als Sieger; AGENTS.md §12.5) bleibt unverändert gültig.

### 4.6 Anti-Overfitting-Maschinerie (Herzstück)

Fünf Gates, von „verhindert Memorieren strukturell" bis „lässt nur bewiesene Edges durch":

**Gate 1 — Daten-Suffizienz (Ausschluss vor dem Tuning).**
Ein (Strategie, Symbol)-Study startet **nur**, wenn die verfügbare Historie das gesamte Fenster (IS + `Folds`·OOS + Holdout) plus Puffer abdeckt. Wiederverwendung von `historical_fetcher.is_symbol_data_sufficient(min_bars)` und `inception_bounds.json` (junge Instrumente, Pitfall #49). Zusätzlich eine **Parameter-zu-Daten-Heuristik**: z. B. ≥ 500 OOS-Bars pro Fold und `N_bars / N_params ≥ schwelle` (Config-Wert). Symbole, die durchfallen → Tier C (§4.2.6), live auf globalen Parametern. **Das ist die wichtigste strukturelle Bremse gegen das „Chartbild-Auswendiglernen".**

**Gate 2 — Hierarchischer Prior / Shrinkage (im Reward).**
Das globale Optimum (`strategies.json[params]`, Ergebnis der bestehenden globalen Studies) ist der **Prior**. Zwei Mechanismen, kombinierbar:
- *Warm-Start:* `study.enqueue_trial(global_best_params)` — der Sucher startet am globalen Optimum.
- *Explizite Strafe:* `param_pen` (§4.5) bestraft große Abweichungen → der Sucher muss einen Edge „erkaufen", bevor er weit vom Prior abweicht. `lambda_reg` steuert die Stärke (Ridge-artig). Das verhindert, dass Rauschen den Vektor in absurde Regionen zieht.

**Gate 3 — Promotion-Margin gegen das globale Baseline (das entscheidende Gate).**
Ein `instrument_overrides[symbol]` wird **nur** promotet, wenn der symbol-getunte Vektor das globale Baseline **auf dem ungesehenen Holdout um eine Marge schlägt**:
```
R_symbol = holdout_reward(symbol_tuned_params, symbol)     # neuer Backtest, holdout_days=0
R_global = holdout_reward(global_params,       symbol)     # neuer Backtest, holdout_days=0
promote = holdout_passed(symbol_tuned)
          and (R_symbol > R_global + promotion_margin)
```
Schlägt der Override das Global nicht **klar** auf unabhängigen Daten, bleibt das Symbol auf globalen Parametern (`status: "REJECTED_NO_EDGE_OVER_GLOBAL"`). **Das ist die zentrale Verteidigung gegen Memorieren:** Ein auswendig gelernter Vektor glänzt im Such-Korridor, scheitert aber an `R_symbol > R_global + margin` auf dem Holdout. Kosten: zwei zusätzliche Holdout-Backtests pro Symbol (2·`S·N` ≈ 840 Runs ≪ 42 000 — vernachlässigbar). `promotion_margin` neu in `optimizer.json`.

**Gate 4 — Multi-Window-Stabilität.**
Der Edge muss über **mehrere** nicht-überlappende OOS-Fenster bestehen, nicht nur in einem. Der bestehende 4-Fold-Walk-Forward mit **Median**-OOS-Sortino (`oos_fold_sortinos`, Pitfall #64 None-safe) leistet das bereits. Für Per-Symbol optional `splits` erhöhen (z. B. 5–6), da ein einzelnes Symbol weniger Querschnitts-Mittelung erfährt. Der Median (statt Mittelwert) dämpft Einzelfenster-Glück.

**Gate 5 — Holdout bleibt unberührt (Architektur-Invariante).**
Wie im globalen Verfahren: Der Holdout (`holdout_days`, Default 45) wird während der **gesamten** Suche nie ausgewertet (`end_time = heute − holdout_days` in `build_trial`). Nur Gate 3 fasst ihn **einmal** an — pro Symbol, pro promotetem Kandidaten. Meta-Overfitting auf den Holdout ist dadurch ausgeschlossen (Konzept v2 §2/§3, AGENTS.md §12).

> **Zusammenspiel:** Gate 1 reduziert die Menge tunebarer Symbole; Gate 2 hält die Suche nahe am robusten Prior; Gate 4 verlangt Persistenz über Fenster; **Gate 3 ist der harte Filter** — nur ein auf unabhängigen Daten beweisbarer Mehrwert wird live. Gate 5 schützt die Integrität des finalen Urteils.

### 4.7 Meta-Orchestrator (`sweep`)

Neues Modul `automation/optimizer/sweep.py` (oder `--sweep`-Modus in `run_optimization.py`):

```python
# automation/optimizer/sweep.py
def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, n_jobs: int = 1, tier: str = "deployable") -> list[Path]:
    """1. Symbol-Universum auflösen (momentum_ls.json / instrument_map.json).
       2. Gate 1 (Daten-Suffizienz) filtern → tunebare (strategy, symbol)-Paare.
       3. Tier-Auswahl (§4.2.6): deployable | refine | all.
       4. Für jedes Paar eine Postgres-gestützte Study optimize_symbol(...) starten;
          parallele Worker ziehen Paare aus der Queue.
       5. Pro Paar confirm_on_holdout + Promotion-Gate (Gate 3) → proposal_{strategy}_{symbol}.json.
       Gibt die Liste der Proposal-Pfade zurück."""
```
- `optimize_symbol(strategy, symbol, ...)` ist die Single-Symbol-Variante von `optimize` (Study-Name `study_{strategy}_{symbol}`, Manifest mit `instruments=[symbol]`, `universe_size=1`, `reward_mode="per_symbol"`, Warm-Start via Gate 2).
- CLI: `python -m automation.optimizer.sweep --strategies all --symbols all --tier deployable --n-jobs 32`.
- **Live-Sicherheit unverändert:** Der Sweep betritt **nie** Phase 5; Promotion ausschließlich per PR (`status: READY_FOR_PR` / `REJECTED_*`).

### 4.8 Live-Integration (Matrix-Backtest + `momentum_ls_run.py`)

Nach Promotion der Overrides ändern sich zwei Pfade:

1. **Daily-Orchestrator Matrix-Backtest** (`backtest_runner.py`, Legacy-Pfad ohne `manifest_version`): Jeder Matrix-Job `(symbol, strategy)` löst Parameter via `resolve_strategy_params(..., instrument=symbol)` (§4.3) auf. Damit läuft jede Strategie pro Symbol mit ihrem symbol-spezifischen Vektor; das Tournament wählt weiterhin **welche Strategie** je Symbol gewinnt — jetzt aber auf Basis symbol-getunter Strategien. Das bestehende OOS-Gating, die Whitelist-Erzeugung (Pitfall #60) und der Fail-Closed-Interlock bleiben unverändert.
2. **`momentum_ls_run.py`** (Live): Bei der Registrierung des per-Symbol-Tournament-Gewinners wird die Config mit `instrument_overrides[symbol]`-aufgelösten Parametern instanziiert. Die dynamische `STRATEGY_REGISTRY` und der Allocator-Hook (Pitfall #22) bleiben.

> **Kein** neuer Live-Deploy-Pfad, **keine** Aufweichung des Gate-Scope-vs-Deployment-Scope-Prinzips (Pitfall #60): Ein per-Symbol-OOS-Verlierer bleibt auch mit Override ausgeschlossen.

### 4.9 Provenienz, Storage, Reproduzierbarkeit

- **Postgres statt SQLite (bewusste Entscheidung, G4d-Relaxation):** Bei voller Parallelität (§4.2.3) ist Postgres erforderlich. `STORAGE` wird von `f"sqlite:///{WORK/'studies.db'}"` auf eine konfigurierbare RDB-URL umgestellt (Default weiterhin SQLite für Einzelmaschinen-/Test-Läufe; Postgres via Env/Config für den Sweep). **`AGENTS.md` G4d / Pitfall #53 müssen entsprechend präzisiert werden** („SQLite für Single-Node; Postgres für parallele Sweeps") — diese Änderung ist explizit, dokumentiert und auf den Sweep-Pfad begrenzt. Pitfall #68 (Busy-Timeout, Determinismus-Warnung) bleibt für den SQLite-Pfad gültig.
- **Determinismus:** Bei `n_jobs>1` ist Reproduzierbarkeit trotz `seed` nicht garantiert (TPE-State unter Concurrency, Pitfall #68). Für reproduzierbare Per-Symbol-Läufe `--n-jobs 1` je Study; Parallelität über **verschiedene** Studies (Symbole), nicht innerhalb einer Study.
- **Provenienz pro Study:** `study_{strategy}_{symbol}`, `data_snapshot_sha256` je Study, `frozen_tournament_sha256` im Manifest (unverändert), Proposals nach `data/optimizer/proposal_{strategy}_{symbol}.json`.
- **Audit-Trail:** Pro Symbol-Study bleiben Manifest, `tournament_result.json`, Logs und Reward erhalten — passt zum forensischen Workflow.

### 4.10 Jules-Auftragsschnitt für Ansatz 4

| Auftrag | Inhalt | Tests (gemockt, `tmp_path`) | CI-Tier | Abhängig von |
|---|---|---|---|---|
| **A4.1** | `instrument_overrides`-Schema + Resolution in `resolve.py` **und** `backtest_runner.py` (`instrument`-Param); `_schema` in `strategies.json` | `test_resolve_instrument_override.py` (Precedence) | Tier 10 | A3.4 |
| **A4.2** | `global_settings.instruments`-Filter in `backtest_runner.py` (Manifest-getrieben); Recon der Universum-Naht | `test_runner_instrument_filter.py` | Tier 3 | A4.1 |
| **A4.3** | Reward `reward_mode="per_symbol"` + `param_pen`/`lambda_reg`/`promotion_margin`/`reward_mode` in `optimizer.json`; Coverage-Drop bei `universe_size==1` | `test_reward_per_symbol.py` (Werte aus JSON) | Tier 10 | A4.1 |
| **A4.4** | Gate 1 (Daten-Suffizienz inkl. Param/Daten-Heuristik) als reine Funktion `is_symbol_tunable(...)` | `test_symbol_tunable_gate.py` | Tier 10 | — |
| **A4.5** | `optimize_symbol` + Warm-Start (Gate 2) + `confirm_on_holdout`-Erweiterung um Promotion-Margin gegen Global (Gate 3) | `test_per_symbol_promotion.py` (passing/rejected/no-edge) | Tier 10 | A4.2, A4.3 |
| **A4.6** | `sweep.py` Meta-Orchestrator + CLI (`--tier`, `--symbols`, `--n-jobs`); enumeriert/dispatcht, **nie** Phase 5 | `test_sweep_enumeration.py` (kein echter Backtest) | Tier 10 | A4.4, A4.5 |
| **A4.7** | Storage-URL konfigurierbar (SQLite-Default, Postgres-Sweep); G4d/Pitfall #53 in `AGENTS.md` präzisieren | `test_storage_url_resolution.py` | Tier 10 | A4.6 |
| **A4.8** | Live-Integration: Matrix-Pfad + `momentum_ls_run.py` lösen `instrument_overrides` auf | `test_live_instrument_override.py` | Tier 3/10 | A4.1 |
| **A4.9** | Overhead-Reduktion: importierbare In-Process-Backtest-Entry + Config-Sharing pro Study (Performance, optional/letzter Schritt) | `test_inprocess_backtest_entry.py` (Fault-Isolation: TrialPruned vs. Fail-Fast) | Tier 10 | A4.2 |

> **Test-Hygiene (G5):** Kein Auftrag startet einen echten Backtest, kontaktiert das Netz oder braucht Secrets. `subprocess.run`/`run_backtest`/Backtest-Entry werden gemockt; `WORK`/`STORAGE` per `monkeypatch` auf `tmp_path`. **Timing-Wächter:** Optimizer-Tests > wenige Sekunden ⇒ undichter Mock ⇒ P0.

---

## Kapitel 5: Kombinierte Roadmap (Ansatz 3 → 4)

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **P-A3** | Ansatz 3 (A3.1–A3.4): Schwellen → ATR-Multiplikatoren; deklarative `SEARCH_SPACES` | Skaleninvariante Parameter; frischer globaler Baseline-Lauf |
| **P0** | Kalibrierungslauf: `t_engine`/`t_overhead` messen (5 Trials, 1 Symbol); Hardware-/Worker-Budget festlegen | Belastbare Compute-Schätzung; `max_workers`-Plan |
| **P1 (MVP)** | A4.1–A4.3 + A4.5 für **eine** Strategie (z. B. `HourlyMeanReversion`), **ein** Symbol, SQLite, sequenziell; Gate 3 verifizieren | Ende-zu-Ende-Per-Symbol-Pfad mit Promotion-Gate |
| **P2 (Härtung)** | A4.4 (Gate 1), Gate 4 (`splits`↑), Plausibilitäts-Wächter; Tier-Logik (§4.2.6) | Overfitting-Maschinerie vollständig |
| **P3 (Skalierung)** | A4.6 (Sweep) + A4.7 (Postgres) + A4.9 (In-Process-Worker); 32–64-Kern-Knoten | Voller nächtlicher Per-Symbol-Sweep |
| **P4 (Live)** | A4.8: Matrix + `momentum_ls_run` lösen Overrides; PR-Promotion erster Symbole | Symbol-getunte Live-Strategien |

**Kritischer Pfad:** P-A3 vor P1 (Ansatz 3 reduziert Ansatz-4-Überfit-Fläche). Gate 3 (Promotion-Margin) muss **vor** der ersten Live-Promotion (P4) stehen — ohne ihn ist Ansatz 4 ein Overfitting-Generator.

---

## Kapitel 6: Sicherheits-Leitplanken & offene Entscheidungen

**Leitplanken (verbindlich, erben aus AGENTS.md §12 / Konzept v2 §12):**
1. **Kein Live-Deploy aus dem Optimierer/Sweep** — Phase 5 wird nie betreten.
2. **Risiko-Gates eingefroren** — `tournament.json` 1:1 kopiert, nie variiert (auch nicht pro Symbol).
3. **Holdout unberührt** während der Suche; einmaliger Zugriff nur im Promotion-Gate (Gate 3).
4. **Human-in-the-Loop** — `instrument_overrides` nur per PR; Review zeigt `R_symbol` vs. `R_global`, Overfit-Gap und Holdout-Metriken.
5. **Plausibilitäts-Wächter** — absurde Metriken (Sortino > Cap, Near-Zero-Sizing) werden markiert, nie promotet (Floor-Clamp/Ordnungsinvariante §12.5 bleibt).
6. **Standalone-Prinzip** — `automation/optimizer/` importiert nichts aus `archive`/`adapters` (G1).
7. **Gate-Scope ≠ Deployment-Scope** — ein per-Symbol-OOS-Verlierer bleibt trotz Override ausgeschlossen (Pitfall #60).

**Offene Entscheidungen (vor P3/P4 zu klären):**
- **Postgres-Betrieb:** Managed-Instanz vs. lokaler Docker-Container; Migration der bestehenden SQLite-Studies (oder Neustart der Studies-Historie bei Katalog-Drift).
- **`lambda_reg`/`promotion_margin`-Kalibrierung:** Startwerte konservativ wählen (hohe Marge, starke Shrinkage), per Backtest-Audit nachjustieren — ein zu kleines `promotion_margin` lässt Rauschen durch.
- **Re-Optimierungs-Kadenz:** Wie oft werden Overrides neu berechnet? Bei jedem Katalog-Freeze, oder seltener? Stale-Overrides bergen ein eigenes Risiko.
- **In-Process-Worker vs. Subprozess:** Fault-Isolation auf Study-Ebene (Pitfall #65) ist akzeptabel, solange fundamentale Fehler Fail-Fast crashen; vor P3 mit einem absichtlich fehlerhaften Trial verifizieren.
- **Tier-Strategie:** Start mit Tier A (Deployable-Set) reduziert Erstkosten erheblich und liefert den größten Edge-Zuwachs zuerst — empfohlener Einstieg.

---

*Stand: 2026-06-13. Konzept v2.2. Bei jeder Umsetzungs-Änderung Version, Roadmap-Status und (nach Merge) `AGENTS.md` §19 aktualisieren.*
