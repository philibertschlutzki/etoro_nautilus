# eToro Nautilus - Code Review & Audit Report

## 1. Datenkonsistenz & Instrument-Mapping (Live vs. Produktivdaten)
**Dateien:** `config/setups.py`, `run_bot.py`, `adapters/instrument_map.py`
**Schweregrad:** Critical

**Problem:**
In `run_bot.py` wird die Konfiguration aus `ACTIVE_BOTS` blind gelesen und in `EToroDataClientConfig` gepackt. Wenn eine `etoro_id` in `setups.py` vorhanden ist, aber in `instrument_map.py` nicht gemappt wurde, stürzt der `_register_instruments` Loop im Datenadapter (`etoro_data.py`) ab oder loggt nur eine Warnung, was später zu KeyError in Strategien führt. Zudem wird in `setups.py` der `bar_type` und das `symbol` festgeschrieben (`BTC.ETORO-1-MINUTE-MID-INTERNAL`), anstatt es dynamisch aus der ID zu generieren.

**Korrigierter Code (`run_bot.py` Refactoring):**
```python
# run_bot.py - Validierung vor dem Start
from adapters.instrument_map import ETORO_INSTRUMENTS

def main():
    ...
    required_etoro_ids = []
    for bot in ACTIVE_BOTS:
        eid = bot.get("etoro_id")
        if eid not in ETORO_INSTRUMENTS:
            print(f"❌ CRITICAL WARNUNG: etoro_id {eid} in setups.py ist NICHT in instrument_map.py definiert. Überspringe Bot!")
            continue
        required_etoro_ids.append(eid)

    required_etoro_ids = list(set(required_etoro_ids))
    ...
```

## 2. Strategie-Robustheit & Hardcoding-Beseitigung
**Dateien:** `strategies/sma_crossover.py`, `strategies/adx_atr_momentum.py`, `strategies/vwap_exhaustion.py`
**Schweregrad:** Critical

**Problem:**
1. **String-basiertes State Management:** In Strategien wird `self.current_signal = "BUY"` statt `self.portfolio.is_flat(self.instrument_id)` genutzt. Das ist gefährlich, da der lokale String bei Bot-Neustarts verloren geht, Nautilus Trader aber `portfolio` Persistenz bietet.
2. **Hardcoded Limits in VWAP:** In `vwap_exhaustion.py` und `tesla_combo_strategy.py` wird durch Null geteilt, falls `cumulative_volume` noch 0 ist und `current_vwap` verwendet wird.
3. **Feste Perioden & Thresholds:** Toleranzen wie `0.005` in `ComboTrendVwapStrategy` sind für extrem volatile Coins vs. ruhige Bluechips nicht passend, besser wäre ein dynamischer ATR-basierter Threshold.

**Korrigierter Code (`sma_crossover.py`):**
```python
    def on_bar(self, bar: Bar):
        self.sma.handle_bar(bar)
        if not self.sma.initialized:
            return

        close_price = float(bar.close)

        # Nutzen von nativem Portfolio-Status statt Strings
        is_flat = self.portfolio.is_flat(self.instrument_id)
        is_long = self.portfolio.has_long_position(self.instrument_id)
        is_short = self.portfolio.has_short_position(self.instrument_id)

        if close_price > self.sma.value and not is_long:
            self._log.info(f"🟢 [{self.instrument_id}] BUY SIGNAL")
            # Implementiere Buy-Order: self.submit_order(...)

        elif close_price < self.sma.value and not is_short:
            self._log.info(f"🔴 [{self.instrument_id}] SELL SIGNAL")
```

## 3. Live-Daten Adapter (`adapters/etoro_data.py`)
**Datei:** `adapters/etoro_data.py`
**Schweregrad:** Critical

**Problem:**
1. **Hardcoded Precisions:** In `_register_instruments` wird rigoros `price_precision=5` und `price_increment=0.00001` gesetzt. Dies führt bei Assets wie `SHIBxM.ETORO` (Preis < 0.00001) oder bei `BTC.ETORO` (Präzision < 5) zu massiven Fehlern im Orderbook oder Order Rejects von Nautilus Trader.
2. **WebSocket Loop Exit:** Wenn `async for raw in self._ws:` abbricht, wird `os._exit(1)` ohne sauberes Triggern von `node.stop()` genutzt. Dadurch droht State-Corruption in Parquet-Dateien.

**Korrigierter Code (`etoro_data.py` - Dynamische Präzision):**
```python
    # Besser: Präzision basierend auf dem ersten erhaltenen Snapshot berechnen
    # Für eToro empfiehlt sich ein Mapping oder eine heuristische Schätzung:
    def get_precision_for_price(price: float) -> tuple[int, Price]:
        if price < 0.001:
            return 8, Price(1e-8, precision=8)
        elif price < 1.0:
            return 5, Price(1e-5, precision=5)
        elif price > 1000:
            return 2, Price(0.01, precision=2)
        return 4, Price(0.0001, precision=4)

    # Anpassen der Equity-Erstellung, sobald Preis bekannt ist, oder über ein erweitertes ETORO_INSTRUMENTS mapping.
```

## 4. Orchestrierung & Systemarchitektur (`run_bot.py`)
**Datei:** `run_bot.py`
**Schweregrad:** Medium

**Problem:**
1. **Hardcoded Strategie-Klassen:** `if strategy_class_name == "SmaCrossoverStrategy":` blockiert alle anderen Strategien (`vwap_exhaustion.py` etc.). Dies zerstört die Modularität.
2. **Exception Handling:** `node.run()` wird nur auf `KeyboardInterrupt` abgesichert. Generelle Exceptions (wie Netzwerkfehler) bringen den Service nicht in die `finally` Klausel, wenn sie falsch im Threading abgewickelt werden.

**Korrigierter Code (`run_bot.py` - Dynamischer Import):**
```python
    import importlib

    for idx, bot_spec in enumerate(ACTIVE_BOTS):
        strategy_class_name = bot_spec.get("strategy_class")

        try:
            # Snake_case Umwandlung oder fixes Mapping, z.B.:
            module_name = f"strategies.{strategy_class_name.lower()}"
            # Besser: Explicit Mapping dictionary in setups.py oder class registry
        except Exception as e:
            print(f"Fehler beim Laden von {strategy_class_name}")
```
