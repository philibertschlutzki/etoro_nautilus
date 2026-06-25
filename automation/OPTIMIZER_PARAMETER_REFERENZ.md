# Optimizer-Parameter-Referenz — korrigierte Suchräume (aktive Strategien)

**Stand:** 2026-06-25 · Begleitdokument zu Issue **#446** (GitHub) / Vorschlag **#426**.
**Grundwahrheit:** die `*Config`-Felder (msgspec, `frozen=True`, `kw_only=True`) inkl. der vererbten
`HourlyStrategyConfig`-/nautilus-`StrategyConfig`-Felder — exakt die `valid_keys`, gegen die
`run_single_backtest_worker` filtert.

> **Harte Invariante (Pitfall #81):** Jeder in `automation/optimizer/spaces.py` gesampelte Parameter
> MUSS als Feld im zugehörigen `*Config`-Struct existieren. Andernfalls verwirft der Backtest-Worker
> ihn still (`_dropped_params`), und Optuna tunt einen Parameter, der das Backtest-Ergebnis nie
> beeinflusst (Phantom-Tuning). Der Test `automation/tests/test_search_space_binding.py`
> (`test_sampled_params_bind_to_config`) erzwingt diese Bindung fail-fast für jede aktive Strategie.

Diese Referenz ist die **Soll-Vorgabe nach Behebung von #446** und spiegelt den tatsächlichen
Code-Stand von `spaces.py` und der `*Config`-Structs wider.

---

## Vererbte Felder (`HourlyStrategyConfig` — für alle Strategien verfügbar)

| Feld | Typ | Default | Sinnvolle Tuning-Range |
|------|-----|---------|------------------------|
| `atr_period` | int | 14 | 5–21 |
| `atr_trailing_multiplier` | float | 1.5 | 0.3–4.0 (strategieabhängig) |
| `max_bars_in_trade` | int | 48 | 6–120 (strategieabhängig) |
| `cooldown_bars` | int | 12 | 2–36 |
| `trade_amount_usd` | float | 100.0 | (nicht tunen — Sizing vom Runner injiziert) |
| `trade_amount_pct` | float\|None | None | (nicht tunen — Sizing extern fixiert) |
| `max_open_positions` | int | 1 | (nicht tunen) |
| `profit_target_pct` | float\|None | None | (optional) |
| `trend_filter_period` | int | 0 | (optional) |
| `max_daily_trades` | int\|None | 5 | (optional 3–10) |

---

## 1. `SmaCrossoverStrategy` — ✅ konsistent (unverändert)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| `sma_period` | int | 5–60 | `sma_period` | ✅ |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |

---

## 2. `HourlyMeanReversionStrategy` — ✅ konsistent (unverändert)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| `keltner_period` | int | 6–40 | `keltner_period` | ✅ |
| `keltner_atr_period` | int | 6–40 | `keltner_atr_period` | ✅ (Bindung korrekt) |
| `keltner_multiplier` | float | 1.0–3.5 | `keltner_multiplier` | ✅ |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |
| `atr_trailing_multiplier` | float | 0.3–2.5 | vererbt | ✅ |
| `max_bars_in_trade` | int | 12–96 | vererbt | ✅ |

> **Hinweis:** Ob `keltner_atr_period` im Keltner-Indikator strategie-intern voll verdrahtet ist, ist
> ein eigenständiger Strategie-Logik-Aspekt (siehe `test_keltner_atr_period.py`), **kein** Sampling↔
> Config-Bindungsproblem. Die Bindung selbst ist korrekt.

---

## 3. `ComboTrendVwapStrategy` — ✅ nach #446 (`trend_tolerance_pct` verdrahtet)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| `macd_fast` | int | 3–14 | `macd_fast` | ✅ |
| `macd_gap` → `macd_slow` | int | 4–26 (abgeleitet) | `macd_slow` (= fast+gap) | ✅ (garantiert fast<slow) |
| `macd_signal_period` | int | 5–15 | `macd_signal_period` | ✅ |
| `sma_period` | int | 20–100 | `sma_period` | ✅ |
| `bb_period` | int | 10–40 | `bb_period` | ✅ |
| `bb_std_dev` | float | 1.0–2.5 | `bb_std_dev` | ✅ |
| `atr_period` | int | 7–21 | `atr_period` | ✅ |
| `atr_multiplier` | float | 0.1–1.5 | `atr_multiplier` | ✅ |
| `vwap_period` | int | 10–60 | `vwap_period` | ✅ |
| **`trend_tolerance_pct`** | float | 0.0–0.10 | **`trend_tolerance_pct`** | ✅ **#446: Feld ergänzt + verdrahtet** |
| `bb_touch_window` | int | 6–96 | `bb_touch_window` | ✅ |
| `require_vwap_confirmation` | bool | {True,False} | `require_vwap_confirmation` | ✅ |
| `require_bb_touch` | bool | {True,False} | `require_bb_touch` | ✅ |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |
| `atr_trailing_multiplier` | float | 1.0–4.0 | vererbt | ✅ |
| `max_bars_in_trade` | int | 12–120 | vererbt | ✅ |

> **#446-Entscheid `trend_tolerance_pct` (verdrahten):** zuvor gesampelt, aber weder Config-Feld noch
> genutzt (das Trend-Gate war hart `close > sma*0.98` / `close < sma*1.02` kodiert). Jetzt echtes
> Feld `trend_tolerance_pct: float = 0.02` (Default reproduziert das alte Verhalten) und verdrahtet:
> `trend_bullish = close > sma*(1 − tol)`, `trend_bearish = close < sma*(1 + tol)`.

---

## 4. `FlashCrashReversalStrategy` — 🔧 nach #446 (Phantom entfernt, echte Entry-Felder ergänzt)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| **`bb_period`** | int | 10–40 | `bb_period` | ✅ **#446: ergänzt (echte BB-Crash-Schwelle)** |
| **`bb_std_dev`** | float | 1.5–3.0 | `bb_std_dev` | ✅ **#446: ergänzt (echte BB-Crash-Schwelle)** |
| `rsi_period` | int | 2–14 | `rsi_period` | ✅ |
| `rsi_oversold` | int | 10–30 | `rsi_oversold` | ✅ |
| `atr_period` | int | 5–20 | vererbt | ✅ |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |
| `atr_trailing_multiplier` | float | 0.5–3.0 | vererbt | ✅ |
| `max_bars_in_trade` | int | 6–48 | vererbt | ✅ |

> **#446-Entscheid `vol_surge_multiplier` (entfernt):** war gesampelt, existierte aber weder im
> Config noch in der Strategie-Logik. Die Strategie hat **keinen** Volumen-Pfad; synthetische 1h-Bars
> tragen konstant `volume=1.0` (`hourly_strategy_base.py:174`), sodass ein Volumen-Surge-Filter **nie**
> feuern würde (gleiche Architektur-Realität wie bei `vwap_exhaustion`/`dynamic_breakout`). Statt
> Phantom-Tuning werden die **echten** Entry-Felder `bb_period`/`bb_std_dev` getunt — sie steuern die
> BB-Crash-Schwelle und beeinflussen die Round-Trip-Zahl nachweislich.
> **Bewusst fix:** `rsi_overbought` (Exit-Gate, nicht gesampelt).

---

## 5. `VolatilityBreakoutPumpStrategy` — 🔧 nach #446 (Rename + Phantom entfernt)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| `bb_period` | int | 10–40 | `bb_period` | ✅ |
| **`bb_std_dev`** | float | 1.5–3.0 | `bb_std_dev` | ✅ **#446: `bb_std` → `bb_std_dev` (Rename)** |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |
| `atr_trailing_multiplier` | float | 1.0–4.0 | vererbt | ✅ |
| `max_bars_in_trade` | int | 12–72 | vererbt | ✅ |

> **#446-Entscheid `vol_window`/`vol_threshold` (entfernt):** die Strategie nutzt ausschließlich BB
> (kein Volumen-Pfad; `volume=1.0`-Realität). Beide Phantom-Keys gestrichen.

---

## 6. `VwapExhaustionStrategy` — 🔧 nach #446 (Rename + RSI-Phantom entfernt)

| Sampling-Key | Typ | Range | Config-Feld | Status |
|---|---|---|---|---|
| **`vwap_period`** | int | 10–50 | `vwap_period` | ✅ **#446: `vwap_window` → `vwap_period` (Rename)** |
| `deviation_threshold` | float | 0.005–0.03 | `deviation_threshold` | ✅ |
| `cooldown_bars` | int | 2–36 | vererbt | ✅ |
| `atr_trailing_multiplier` | float | 0.5–3.0 | vererbt | ✅ |
| `max_bars_in_trade` | int | 6–48 | vererbt | ✅ |

> **#446-Entscheid RSI (`rsi_period`/`rsi_extreme`, entfernt):** VwapExhaustion ist bewusst
> **„Price-Deviation only"** und besitzt **keinen** RSI-Indikator (siehe Modul-Docstring; die
> Volumen-Multiplikator-Bedingung wurde bereits früher entfernt, weil `volume=1.0`). Beide
> RSI-Phantom-Keys gestrichen — die Strategie tunt nur ihre echten Felder.

---

## Architektur-Entscheide (für die Nachwelt dokumentiert)

1. **Volumen ist im Backtest konstant `1.0`.** Synthetische 1h-Bars werden aus QuoteTicks gebaut
   (`hourly_strategy_base.py:174`, `volume=Quantity(1.0, 4)`). Jeder volumenbasierte Filter
   (`vol_surge_multiplier`, `vol_window`, `vol_threshold`, Volumen-Multiplikator) ist daher tot und
   wird **nicht verdrahtet, sondern aus dem Sampling entfernt**.
2. **VwapExhaustion nutzt kein RSI.** Reine Price-Deviation-Logik gegen die VWAP; RSI-Parameter sind
   Phantome und entfernt.
3. **`trend_tolerance_pct` in ComboTrendVwap wird verdrahtet** (statt entfernt), weil ein echtes
   Trend-Toleranzband existiert (vormals hart 0.98/1.02) und ein Regressionstest dessen Wirkung
   prüft (`test_backtest_trades_generated.py`).

---

## Verifikation

```bash
python -m pytest automation/tests/test_search_space_binding.py -q
```

Der Test importiert jede aktive Strategie aus `strategies.json`, ruft `spaces.sample_params(...)` und
prüft `set(sample_params) ⊆ set(Config.__struct_fields__)`. Er ist die ausführbare Form dieser
Referenz und fängt jeden künftigen Drift fail-fast ab.
