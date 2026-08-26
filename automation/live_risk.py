"""Issue #999 (P0, HEADLINE) — Live-Circuit-Breaker fuer den Momentum-LS-Bot.

Vor diesem Modul wurde der Live-Bot nach dem Start als detached Subprozess NICHT mehr auf seinen
Equity-Verlauf ueberwacht (``grep -rn "drawdown" automation/momentum_ls_run.py`` fand nur die
BACKTEST-seitige Telemetrie in ``daily_orchestrator.py``, keine Live-Entsprechung). ``max_drawdown
<= 0.3`` ist ein reines Backtest-Gate; im Livebetrieb existierte kein Aequivalent.

Zwei unabhaengige, ODER-verknuepfte Ausloeser (Issue #999 Symptom 2):

  Ausloeser A (absolut, FAIL-CLOSED):
      DD_live(t) = 1 − E(t) / max_{tau<=t} E(tau)  >=  dd_halt_fraction   (Default 0.10)

  Ausloeser B (Verteilung, FAIL-OPEN bis n_live >= n_min_periods):
      z(t) = (mean(R_live) − mu_backtest) / (sigma_backtest / sqrt(n_live))  <  −z_halt
      (Standardfehler-skalierter Ein-Stichproben-z-Test — dieselbe Form, die die #999-Vorgabe
      "erst ab n_live >= n_min auswertbar" ueberhaupt sinnvoll macht: ohne die 1/sqrt(n)-Skalierung
      haette ``n_min`` keinen Einfluss auf die Zuverlaessigkeit der Teststatistik selbst.)
      ``mu_backtest``/``sigma_backtest`` MUESSEN auf derselben Periodenskala wie ``R_live`` geschaetzt
      sein (dieselbe Kommensurabilitaets-Anforderung wie Issue #996, hier auf der Live-Seite).

Rein & deterministisch (kein I/O, kein globaler State) — ``LiveCircuitBreakerWatchdog`` (unten) ist
die einzige IO-/Thread-tragende Klasse und bleibt bewusst von dieser reinen Entscheidungslogik
getrennt, damit ``evaluate_circuit_breaker``/``drawdown_damper`` ohne einen laufenden
NautilusTrader-``TradingNode`` unit-testbar sind.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitBreakerDecision:
    tripped: bool
    trigger: str | None  # "drawdown" | "distribution" | None
    dd_live: float | None
    z_live: float | None
    n_live: int


def evaluate_circuit_breaker(
    equity_now: float,
    equity_peak: float | None,
    live_returns: Sequence[float] = (),
    *,
    dd_halt_fraction: float = 0.10,
    backtest_mu: float | None = None,
    backtest_sigma: float | None = None,
    z_halt: float = 2.5,
    n_min_periods: int = 30,
) -> CircuitBreakerDecision:
    """Reine Entscheidungsfunktion — EIN Aufruf, EIN atomares Urteil (kein Fruehausstieg: beide
    Ausloeser werden unabhaengig ausgewertet, ``trigger`` traegt den ERSTEN, der zutrifft, in fester
    Reihenfolge A vor B, falls beide gleichzeitig zutreffen)."""
    if equity_peak is None or equity_peak <= 0:
        dd_live = None
    else:
        dd_live = max(0.0, 1.0 - (float(equity_now) / float(equity_peak)))
    # Issue #999 — 1e-9-Toleranz gegen Float-Rundung (``1.0 - 9000/10000`` liefert
    # 0.09999999999999998, nicht exakt 0.10): dieselbe Toleranzkonvention wie die
    # check_live_exposure_budget-Invariante (``Σ w_i <= W_max + 1e-9``).
    triggered_a = dd_live is not None and dd_live >= dd_halt_fraction - 1e-9

    n_live = len(live_returns)
    z_live = None
    triggered_b = False
    if (n_live >= n_min_periods and backtest_mu is not None
            and backtest_sigma is not None and backtest_sigma > 0):
        r_live_mean = sum(live_returns) / n_live
        standard_error = backtest_sigma / math.sqrt(n_live)
        if standard_error > 0:
            z_live = (r_live_mean - backtest_mu) / standard_error
            triggered_b = z_live < -z_halt

    if triggered_a:
        trigger = "drawdown"
    elif triggered_b:
        trigger = "distribution"
    else:
        trigger = None

    return CircuitBreakerDecision(
        tripped=trigger is not None, trigger=trigger, dd_live=dd_live, z_live=z_live, n_live=n_live,
    )


@dataclass(frozen=True)
class SizingCapCorrection:
    correction_needed: bool
    target_notional: float | None
    excess_notional: float
    overshoot_factor: float | None


def compute_sizing_cap_correction(
    *, realized_notional: float, equity_at_entry: float | None, target_fraction: float | None,
    tolerance: float = 0.02,
) -> SizingCapCorrection:
    """Issue #1297 (GH #1170, Katalog #1272-1297, P1) — der Sizing-Deckel aus #1209
    (``hourly_strategy_base._compute_quantity``) wird auf Basis des Equity-Standes und Preises ZUM
    SIGNALZEITPUNKT gerechnet; der Fill erfolgt zum naechsten Bar-Schluss. Bei einer adversen
    Kursbewegung zwischen Sizing und Fill ueberschreitet das REALISIERTE Notional
    (``quantity * fill_price``) den Zielanteil, ohne dass vor diesem Fix ein Nachpruefpfad
    existierte (Symptom: Vwap/TSLA 15,94-16,05 %, AdxAtr/NVDA 16,19 % gegen ``trade_amount_pct =
    15,0`` -- Ueberschreitungsfaktoren 1,06-1,08 in 4/4 Laeufen).

    Reine, deterministische Entscheidungsfunktion (kein I/O, kein State) -- der GEMEINSAME Deckel
    fuer Backtest- (``hourly_strategy_base.on_position_opened``, Pfad C: ``trade_amount_pct``) UND
    Live-Pfad (dieselbe Methode, Pfad A: ``MomentumLSAllocator.max_symbol_exposure_fraction`` als
    ``target_fraction``) -- EIN Aufrufort in beiden Faellen (``on_position_opened``), kein
    Duplikat (Fix Punkt 4). ``target_fraction`` ist ein Anteil (0.15 fuer 15 %), nicht ein
    Prozentwert.

    FAIL-OPEN (``correction_needed=False``) ohne auswertbare Basis (``equity_at_entry``/
    ``target_fraction`` fehlend oder <= 0) -- dieselbe Konvention wie ``_apply_sizing_cap``
    (#1209): kein erfundener Eingriff ohne reale Grundlage. Toleranz (Default 0.02, Issue-Text Fix
    Punkt 2 -- ``optimizer.json['sizing_cap_tolerance']``, ersetzt die zuvor implizite 1,05x/5 %-
    Toleranz aus ``invariants.check_sizing_cap_enforcement``s ``max_overshoot_factor``, die
    UNVERAENDERT als reine Abnahmemessung bestehen bleibt) wird MULTIPLIKATIV auf den Zielanteil
    angewandt: eine Korrektur greift erst, wenn das realisierte Notional
    ``target_fraction * equity_at_entry * (1 + tolerance)`` uebersteigt."""
    if (equity_at_entry is None or equity_at_entry <= 0
            or target_fraction is None or target_fraction <= 0):
        return SizingCapCorrection(False, None, 0.0, None)
    target_notional = equity_at_entry * target_fraction
    overshoot_factor = realized_notional / target_notional if target_notional > 0 else None
    if realized_notional <= target_notional * (1.0 + tolerance):
        return SizingCapCorrection(False, target_notional, 0.0, overshoot_factor)
    return SizingCapCorrection(
        True, target_notional, realized_notional - target_notional, overshoot_factor)


def drawdown_damper(dd_current: float | None, *, dd_halt_fraction: float = 0.10, psi_min: float = 0.2) -> float:
    """``psi(DD) = max(psi_min, 1 − DD_current/DD_halt)`` — skaliert die Positionsgroesse
    kontinuierlich herunter, WAEHREND sich der Live-Drawdown dem harten Ausloeser A naehert, statt
    erst am Schwellwert selbst von voller Groesse auf Null zu springen."""
    if dd_current is None or dd_halt_fraction <= 0:
        return 1.0
    return max(psi_min, 1.0 - (dd_current / dd_halt_fraction))


class LiveCircuitBreakerWatchdog:
    """Issue #999 Fix Punkt 2 — periodischer Wächter-Thread fuer eine laufende NautilusTrader-
    ``TradingNode``. Getrennt von ``evaluate_circuit_breaker`` (siehe Moduldocstring): diese Klasse
    ist reine Integrations-Verdrahtung (Equity abfragen, Positionen flatten, Node stoppen) und wird
    daher gegen ein Duck-typed Fake-Objekt getestet, nicht gegen einen echten laufenden Node.

    ``node`` muss (mindestens) tragen: ``.portfolio.equity(venue) -> dict[Any, Any-mit-.as_double()
    -oder-float-konvertierbar]``, ``.trader.strategy_ids``, ``.trader.market_exit_strategy(id)``,
    ``.stop()`` — exakt die Attribute/Methoden, die die installierte ``nautilus_trader``-API unter
    ``TradingNode``/``Portfolio``/``Trader`` tatsaechlich bereitstellt (verifiziert gegen die
    installierte Version; siehe PR-Beschreibung fuer die Namen)."""

    def __init__(
        self,
        node,
        *,
        venue=None,
        poll_interval_s: float = 30.0,
        dd_halt_fraction: float = 0.10,
        backtest_mu: float | None = None,
        backtest_sigma: float | None = None,
        z_halt: float = 2.5,
        n_min_periods: int = 30,
        on_trip: Callable[[CircuitBreakerDecision], None] | None = None,
        on_update: Callable[[CircuitBreakerDecision], None] | None = None,
    ) -> None:
        self._node = node
        self._venue = venue
        self._poll_interval_s = poll_interval_s
        self._dd_halt_fraction = dd_halt_fraction
        self._backtest_mu = backtest_mu
        self._backtest_sigma = backtest_sigma
        self._z_halt = z_halt
        self._n_min_periods = n_min_periods
        self._on_trip = on_trip
        # Issue #999 — auf JEDEM erfolgreichen Tick aufgerufen (nicht nur beim Trip), damit ein
        # Aufrufer (typischerweise ``allocator.update_risk_state``) den aktuellen Live-Drawdown fuer
        # den ψ(DD)-Daempfer kontinuierlich aktuell haelt, statt nur binaer "getrippt/nicht".
        self._on_update = on_update

        self._equity_peak: float | None = None
        self._live_returns: list[float] = []
        self._last_equity: float | None = None
        self.tripped_event = threading.Event()
        self.last_decision: CircuitBreakerDecision | None = None
        self._timer: threading.Timer | None = None
        self._stopped = threading.Event()

    def _read_equity(self) -> float | None:
        try:
            from nautilus_trader.model.identifiers import Venue
            venue_arg = (
                Venue(self._venue) if isinstance(self._venue, str)
                else (self._venue or Venue("ETORO"))
            )
            equity_by_currency = self._node.portfolio.equity(venue_arg)
        except Exception:
            logger.exception("[LiveCircuitBreaker] Equity-Abfrage fehlgeschlagen (uebersprungen).")
            return None
        if not equity_by_currency:
            return None
        try:
            # Issue #999 — bei genau einer Waehrung (der Regelfall fuer diesen Bot) eindeutig; bei
            # mehreren die Summe der Einzel-Money-Betraege (konservative Naeherung ohne FX-Konversion
            # — der Bot handelt aktuell auf einer Quote-Waehrung je Konfiguration).
            return sum(float(v) for v in equity_by_currency.values())
        except (TypeError, ValueError):
            logger.exception("[LiveCircuitBreaker] Equity-Werte nicht in float konvertierbar.")
            return None

    def _tick(self) -> None:
        if self._stopped.is_set():
            return
        equity_now = self._read_equity()
        if equity_now is not None:
            if self._equity_peak is None or equity_now > self._equity_peak:
                self._equity_peak = equity_now
            if self._last_equity is not None and self._last_equity > 0:
                self._live_returns.append((equity_now - self._last_equity) / self._last_equity)
            self._last_equity = equity_now

            decision = evaluate_circuit_breaker(
                equity_now, self._equity_peak, self._live_returns,
                dd_halt_fraction=self._dd_halt_fraction,
                backtest_mu=self._backtest_mu, backtest_sigma=self._backtest_sigma,
                z_halt=self._z_halt, n_min_periods=self._n_min_periods,
            )
            self.last_decision = decision
            if self._on_update is not None:
                try:
                    self._on_update(decision)
                except Exception:
                    logger.exception("[LiveCircuitBreaker] on_update-Callback fehlgeschlagen.")
            if decision.tripped and not self.tripped_event.is_set():
                logger.critical(
                    f"[LiveCircuitBreaker] LIVE_CIRCUIT_BREAKER_TRIPPED trigger={decision.trigger} "
                    f"dd_live={decision.dd_live} z_live={decision.z_live} n_live={decision.n_live}"
                )
                self.tripped_event.set()
                self._flatten_and_stop(decision)
                return

        if not self._stopped.is_set():
            self._timer = threading.Timer(self._poll_interval_s, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _flatten_and_stop(self, decision: CircuitBreakerDecision) -> None:
        # Issue #999 — dieser Wächter laeuft in einem eigenen Python-Thread (threading.Timer), nicht
        # auf dem asyncio-Event-Loop-Thread des Nodes (``node.run()``/``node.get_event_loop()``).
        # ``call_soon_threadsafe`` ist die dafuer vorgesehene Uebergabe an den Loop-Thread; ohne sie
        # waere ein direkter Cross-Thread-Aufruf von market_exit_strategy()/stop() ein nicht
        # abgesichertes Race gegen den laufenden Node. Faellt der Loop-Zugriff selbst aus (Node noch
        # nicht gebaut/bereits gestoppt), wird defensiv direkt aufgerufen — besser ein riskanter
        # Versuch zu flatten als GAR keiner.
        def _do_flatten_and_stop():
            try:
                for strategy_id in list(self._node.trader.strategy_ids):
                    try:
                        self._node.trader.market_exit_strategy(strategy_id)
                    except Exception:
                        logger.exception(f"[LiveCircuitBreaker] market_exit_strategy({strategy_id}) fehlgeschlagen.")
            except Exception:
                logger.exception("[LiveCircuitBreaker] Konnte strategy_ids nicht lesen — Flatten uebersprungen.")
            try:
                self._node.stop()
            except Exception:
                logger.exception("[LiveCircuitBreaker] node.stop() fehlgeschlagen.")

        try:
            loop = self._node.get_event_loop()
            loop.call_soon_threadsafe(_do_flatten_and_stop)
        except Exception:
            logger.exception("[LiveCircuitBreaker] Kein Event-Loop erreichbar — direkter Flatten-Versuch.")
            _do_flatten_and_stop()

        if self._on_trip is not None:
            try:
                self._on_trip(decision)
            except Exception:
                logger.exception("[LiveCircuitBreaker] on_trip-Callback fehlgeschlagen.")

    def start(self) -> None:
        self._stopped.clear()
        self._tick()

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
