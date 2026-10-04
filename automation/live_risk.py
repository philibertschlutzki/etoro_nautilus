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

Issue #1362 (GH #1258) — drei Änderungen am Auslöser-Satz:

  * A (Drawdown) rechnet gegen einen PERSISTENTEN Hochwasserstand (``live_equity_state``,
    ``data/state/live_equity_hwm.json``): ein Bot-Neustart löscht das Drawdown-Gedächtnis nicht mehr.
  * C (Tagesverlust): ``1 − E/E_session_start >= daily_loss_halt_fraction`` (Default 0.03), die Basis ist
    die persistierte Equity zum Beginn des Handelstags in Börsen-Lokalzeit.
  * B (Verteilung) je Paar auf der sizing-invarianten Skala: Netto-Rendite je Round-Trip in bps auf das
    Positions-Notional, getestet gegen ``holdout_trade_return_bps_mean/std`` des promovierten Trials;
    auswertbar ab ``circuit_breaker_n_min_round_trips`` (Default 30). Vorher: 30-Sekunden-Equity-Renditen
    ohne Referenz (``momentum_ls_run`` übergab nie ``backtest_mu``/``backtest_sigma`` ⇒ toter Code).

Rein & deterministisch (kein I/O, kein globaler State) — ``LiveCircuitBreakerWatchdog`` (unten) ist
die einzige IO-/Thread-tragende Klasse und bleibt bewusst von dieser reinen Entscheidungslogik
getrennt, damit ``evaluate_circuit_breaker``/``drawdown_damper`` ohne einen laufenden
NautilusTrader-``TradingNode`` unit-testbar sind.
"""
from __future__ import annotations

import logging
import math
import signal
import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitBreakerDecision:
    tripped: bool
    trigger: str | None  # "drawdown" | "daily_loss" | "distribution" | None
    dd_live: float | None
    z_live: float | None
    n_live: int
    # Issue #1362 — Tagesverlust (Auslöser C) und das Paar, dessen Round-Trip-Verteilung den Auslöser B
    # ausgelöst hat; Defaults rückwärtskompatibel.
    daily_loss: float | None = None
    distribution_pair: str | None = None


def evaluate_distribution_trigger(
    live_returns: Sequence[float], backtest_mu: float | None, backtest_sigma: float | None, *,
    z_halt: float = 2.5, n_min_periods: int = 30,
) -> tuple[bool, float | None, int]:
    """Auslöser B (Issue #999, in #1362 herausgelöst): Standardfehler-skalierter Ein-Stichproben-z-Test
    ``z = (mean(R) − μ_ref) / (σ_ref / √n) < −z_halt``, FAIL-OPEN bis ``n >= n_min_periods`` oder ohne
    Referenz. ``(tripped, z, n)``. ``μ_ref``/``σ_ref`` MÜSSEN auf derselben Skala wie ``live_returns``
    liegen (#1362: bps je Round-Trip auf das Notional)."""
    n_live = len(live_returns)
    if (n_live >= n_min_periods and backtest_mu is not None
            and backtest_sigma is not None and backtest_sigma > 0):
        standard_error = backtest_sigma / math.sqrt(n_live)
        if standard_error > 0:
            z_live = (sum(live_returns) / n_live - backtest_mu) / standard_error
            return z_live < -z_halt, z_live, n_live
    return False, None, n_live


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
    equity_day_start: float | None = None,
    daily_loss_halt_fraction: float | None = None,
) -> CircuitBreakerDecision:
    """Reine Entscheidungsfunktion — EIN Aufruf, EIN atomares Urteil (kein Fruehausstieg: alle
    Ausloeser werden unabhaengig ausgewertet, ``trigger`` traegt den ERSTEN, der zutrifft, in fester
    Reihenfolge A (Drawdown) vor C (Tagesverlust, #1362) vor B (Verteilung), falls mehrere
    gleichzeitig zutreffen)."""
    if equity_peak is None or equity_peak <= 0:
        dd_live = None
    else:
        dd_live = max(0.0, 1.0 - (float(equity_now) / float(equity_peak)))
    # Issue #999 — 1e-9-Toleranz gegen Float-Rundung (``1.0 - 9000/10000`` liefert
    # 0.09999999999999998, nicht exakt 0.10): dieselbe Toleranzkonvention wie die
    # check_live_exposure_budget-Invariante (``Σ w_i <= W_max + 1e-9``).
    triggered_a = dd_live is not None and dd_live >= dd_halt_fraction - 1e-9

    # Issue #1362 Fix Punkt 2 — Auslöser C: Tagesverlust gegen die Equity zum Session-Beginn.
    daily_loss = None
    triggered_c = False
    if equity_day_start is not None and equity_day_start > 0:
        daily_loss = max(0.0, 1.0 - (float(equity_now) / float(equity_day_start)))
        triggered_c = (daily_loss_halt_fraction is not None
                       and daily_loss >= daily_loss_halt_fraction - 1e-9)

    triggered_b, z_live, n_live = evaluate_distribution_trigger(
        live_returns, backtest_mu, backtest_sigma, z_halt=z_halt, n_min_periods=n_min_periods)

    if triggered_a:
        trigger = "drawdown"
    elif triggered_c:
        trigger = "daily_loss"
    elif triggered_b:
        trigger = "distribution"
    else:
        trigger = None

    return CircuitBreakerDecision(
        tripped=trigger is not None, trigger=trigger, dd_live=dd_live, z_live=z_live, n_live=n_live,
        daily_loss=daily_loss,
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



# ─── Issue #1358 (GH #1254) — geordnetes Herunterfahren bei SIGTERM/SIGINT ──────────────────────

SHUTDOWN_POLICY_KEEP = "keep"
SHUTDOWN_POLICY_FLATTEN = "flatten"


def _has_broker_stop_tag(tags) -> bool:
    for tag in (tags or []):
        if isinstance(tag, str) and tag.startswith("SL:"):
            try:
                if float(tag[3:]) > 0:
                    return True
            except ValueError:
                continue
    return False


def open_positions_missing_broker_stop(cache) -> list[str]:
    """Issue #1358 Fix Punkt 2 / #1359 — IDs offener Positionen OHNE Broker-Stop. Eine Position trägt
    einen Broker-Stop, wenn ihre Eröffnungs-Order einen ``SL:<pct>``-Tag mit pct > 0 hatte (der
    eToro-Adapter setzt daraus ``StopLossRate``/``IsNoStopLoss = False``). Eine Position, deren
    Eröffnungs-Order nicht im Cache steht (z. B. nach einem Neustart per Reconciliation übernommen),
    gilt FAIL-CLOSED als ungeschützt."""
    missing: list[str] = []
    try:
        positions = list(cache.positions_open())
    except Exception:
        logger.exception("[LiveShutdown] Konnte offene Positionen nicht lesen — fail-closed: unbekannt.")
        return ["<positions_unreadable>"]
    for pos in positions:
        try:
            order = cache.order(pos.opening_order_id)
        except Exception:
            order = None
        if order is None or not _has_broker_stop_tag(getattr(order, "tags", None)):
            missing.append(str(getattr(pos, "id", pos)))
    return missing


def effective_shutdown_policy(configured: str | None, positions_without_broker_stop: Sequence[str]) -> str:
    """Wirksame Policy beim Herunterfahren: ``keep`` (Default) ist nur zulässig, wenn JEDE offene
    Position einen Broker-Stop trägt — sonst automatisch ``flatten`` (eine ungeschützte Position ohne
    laufenden Bot wäre unverwaltet). Unbekannte Werte werden wie ``keep`` behandelt (Default)."""
    policy = (configured or SHUTDOWN_POLICY_KEEP).strip().lower()
    if policy == SHUTDOWN_POLICY_FLATTEN:
        return SHUTDOWN_POLICY_FLATTEN
    return SHUTDOWN_POLICY_FLATTEN if positions_without_broker_stop else SHUTDOWN_POLICY_KEEP


class LiveShutdownCoordinator:
    """Issue #1358 Fix Punkt 2 — ``SIGTERM``/``SIGINT`` ⇒ (1) Entry-Sperre setzen, (2) ``node.stop()``
    über ``loop.call_soon_threadsafe`` (der Signal-Handler läuft im Hauptthread unter dem laufenden
    Event-Loop), (3) Policy ``live_risk.on_shutdown`` (``keep`` | ``flatten``, siehe
    ``effective_shutdown_policy``), (4) Event ``LIVE_BOT_SHUTDOWN`` mit offenen Positionen und der
    gewählten Policy. Idempotent: ein zweites Signal löst nichts erneut aus.

    Duck-typed gegen ``node`` (``.cache``, ``.trader``, ``.stop()``, ``.get_event_loop()``) und
    testbar ohne laufenden ``TradingNode``."""

    def __init__(
        self,
        node,
        *,
        policy: str = SHUTDOWN_POLICY_KEEP,
        block_entries: Callable[[], None] | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._node = node
        self._policy = policy
        self._block_entries = block_entries
        self._emit = emit
        self.requested = threading.Event()
        self.shutdown_payload: dict | None = None
        self._previous_handlers: dict[int, object] = {}
        self._loop = None

    def install(self, signals: Sequence[int] = (signal.SIGTERM, signal.SIGINT), *, loop=None) -> None:
        """Registriert die Handler. Mit ``loop`` (der Event-Loop des Nodes) über
        ``loop.add_signal_handler`` — das ersetzt die Handler, die der NautilusTrader-Kernel selbst
        für SIGTERM/SIGINT/SIGABRT registriert (sie würden nur ``node.stop()`` aufrufen, ohne Policy
        und Event). Ohne ``loop`` über ``signal.signal`` (Hauptthread)."""
        self._loop = loop
        for signum in signals:
            if loop is not None:
                loop.add_signal_handler(signum, self.request_shutdown, signum)
                self._previous_handlers[signum] = None
            else:
                self._previous_handlers[signum] = signal.signal(signum, self._handle_signal)

    def uninstall(self) -> None:
        for signum, previous in self._previous_handlers.items():
            try:
                if getattr(self, "_loop", None) is not None:
                    self._loop.remove_signal_handler(signum)
                else:
                    signal.signal(signum, previous)  # type: ignore[arg-type]
            except (ValueError, TypeError, RuntimeError):
                pass
        self._previous_handlers.clear()

    def _handle_signal(self, signum, _frame) -> None:
        self.request_shutdown(signum)

    def request_shutdown(self, signum: int | None = None) -> bool:
        """True ⇔ dieser Aufruf hat das Herunterfahren ausgelöst (False: bereits angefordert)."""
        if self.requested.is_set():
            return False
        self.requested.set()
        if self._block_entries is not None:
            try:
                self._block_entries()
            except Exception:
                logger.exception("[LiveShutdown] Entry-Sperre konnte nicht gesetzt werden.")
        try:
            loop = self._node.get_event_loop()
            loop.call_soon_threadsafe(self._shutdown_on_loop, signum)
        except Exception:
            logger.exception("[LiveShutdown] Kein Event-Loop erreichbar — direkter Shutdown-Versuch.")
            self._shutdown_on_loop(signum)
        return True

    def _shutdown_on_loop(self, signum: int | None) -> None:
        cache = getattr(self._node, "cache", None)
        try:
            open_ids = [str(getattr(p, "id", p)) for p in cache.positions_open()] if cache else []
        except Exception:
            logger.exception("[LiveShutdown] Konnte offene Positionen nicht lesen.")
            open_ids = []
        missing = open_positions_missing_broker_stop(cache) if (cache is not None and open_ids) else []
        effective = effective_shutdown_policy(self._policy, missing)
        self.shutdown_payload = {
            "signal": int(signum) if signum is not None else None,
            "open_positions": open_ids,
            "positions_without_broker_stop": missing,
            "policy_configured": self._policy,
            "policy": effective,
        }
        if self._emit is not None:
            try:
                self._emit("LIVE_BOT_SHUTDOWN", self.shutdown_payload)
            except Exception:
                logger.exception("[LiveShutdown] Event-Emission fehlgeschlagen.")
        if effective == SHUTDOWN_POLICY_FLATTEN:
            try:
                for strategy_id in list(self._node.trader.strategy_ids):
                    try:
                        self._node.trader.market_exit_strategy(strategy_id)
                    except Exception:
                        logger.exception(f"[LiveShutdown] market_exit_strategy({strategy_id}) fehlgeschlagen.")
            except Exception:
                logger.exception("[LiveShutdown] Konnte strategy_ids nicht lesen — Flatten übersprungen.")
        try:
            self._node.stop()
        except Exception:
            logger.exception("[LiveShutdown] node.stop() fehlgeschlagen.")


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
        # Issue #1362 (GH #1258) — persistentes Drawdown-Gedächtnis, Tagesverlust-Auslöser C und
        # Verteilungs-Auslöser B je Paar (alle optional, Default = bit-identisches Alt-Verhalten).
        equity_state=None,
        daily_loss_halt_fraction: float | None = None,
        day_key_fn: Callable[[Any], str] | None = None,
        distribution_refs: dict[str, dict] | None = None,
        n_min_round_trips: int = 30,
        now_fn: Callable[[], Any] | None = None,
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

        self._equity_state = equity_state
        self._daily_loss_halt_fraction = daily_loss_halt_fraction
        self._day_key_fn = day_key_fn
        self._distribution_refs = distribution_refs
        self._n_min_round_trips = int(n_min_round_trips)
        self._now_fn = now_fn
        self._round_trips = None  # RoundTripReturnCollector, lazy (nur mit distribution_refs)
        self._memory_day_key: str | None = None
        self._memory_day_start: float | None = None
        # Issue #1362 — der Start startet NICHT bei ``None``, sondern beim persistierten Hochwasserstand.
        self._equity_peak: float | None = (
            equity_state.hwm if equity_state is not None else None)
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
            day_start: float | None = None
            if self._equity_state is not None or self._daily_loss_halt_fraction is not None:
                from datetime import datetime, timezone
                now = self._now_fn() if self._now_fn is not None else datetime.now(timezone.utc)
                day_key = self._day_key_fn(now) if self._day_key_fn is not None else None
            if self._equity_state is not None:
                # Persistenter Hochwasserstand + Tagesbasis (Neustart-fest, Issue #1362 Fix Punkt 1/2).
                state = self._equity_state.observe(equity_now, now_utc=now, day_key=day_key)
                self._equity_peak = float(state["hwm"])
                day_start = state.get("day_start_equity")
            else:
                if self._equity_peak is None or equity_now > self._equity_peak:
                    self._equity_peak = equity_now
                if self._daily_loss_halt_fraction is not None and day_key is not None:
                    if self._memory_day_key != day_key:
                        self._memory_day_key, self._memory_day_start = day_key, equity_now
                    day_start = self._memory_day_start
            if self._last_equity is not None and self._last_equity > 0:
                self._live_returns.append((equity_now - self._last_equity) / self._last_equity)
            self._last_equity = equity_now

            decision = evaluate_circuit_breaker(
                equity_now, self._equity_peak, self._live_returns,
                dd_halt_fraction=self._dd_halt_fraction,
                backtest_mu=self._backtest_mu, backtest_sigma=self._backtest_sigma,
                z_halt=self._z_halt, n_min_periods=self._n_min_periods,
                equity_day_start=day_start,
                daily_loss_halt_fraction=self._daily_loss_halt_fraction,
            )
            if self._distribution_refs is not None:
                decision = self._with_distribution_verdict(decision)
            self.last_decision = decision
            if self._on_update is not None:
                try:
                    self._on_update(decision)
                except Exception:
                    logger.exception("[LiveCircuitBreaker] on_update-Callback fehlgeschlagen.")
            if decision.tripped and not self.tripped_event.is_set():
                logger.critical(
                    f"[LiveCircuitBreaker] LIVE_CIRCUIT_BREAKER_TRIPPED trigger={decision.trigger} "
                    f"dd_live={decision.dd_live} daily_loss={decision.daily_loss} "
                    f"z_live={decision.z_live} n_live={decision.n_live} "
                    f"pair={decision.distribution_pair}"
                )
                self.tripped_event.set()
                self._flatten_and_stop(decision)
                return

        if not self._stopped.is_set():
            self._timer = threading.Timer(self._poll_interval_s, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _with_distribution_verdict(self, decision: CircuitBreakerDecision) -> CircuitBreakerDecision:
        """Issue #1362 Fix Punkt 3 — Auslöser B JE PAAR auf der sizing-invarianten Skala: die
        Round-Trip-Renditen (bps auf das Notional) jedes Instruments gegen ``holdout_trade_return_bps_
        mean/std`` des promovierten Trials. Ersetzt die 30-Sekunden-Equity-Renditen (``_live_returns``,
        nur noch diagnostisch), die nie eine vergleichbare Referenz hatten. A und C behalten Vorrang."""
        from automation.live_equity_state import RoundTripReturnCollector

        if self._round_trips is None:
            self._round_trips = RoundTripReturnCollector()
        try:
            self._round_trips.update(self._node.cache.positions_closed())
        except Exception:
            logger.exception("[LiveCircuitBreaker] positions_closed() fehlgeschlagen — B übersprungen.")
            return decision
        if decision.tripped:
            return decision
        for instrument_id, returns in self._round_trips.returns_by_instrument.items():
            symbol = str(instrument_id)
            ref = self._distribution_refs.get(symbol)
            if not ref:
                continue
            tripped, z, n = evaluate_distribution_trigger(
                returns, ref["mean"], ref["std"], z_halt=self._z_halt,
                n_min_periods=self._n_min_round_trips)
            if tripped:
                return CircuitBreakerDecision(
                    tripped=True, trigger="distribution", dd_live=decision.dd_live, z_live=z,
                    n_live=n, daily_loss=decision.daily_loss, distribution_pair=symbol)
        return decision

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
