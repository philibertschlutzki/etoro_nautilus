import logging
from typing import List

from nautilus_trader.model.identifiers import InstrumentId

from automation.live_risk import drawdown_damper
from automation.log_manager import emit_execution_event

logger = logging.getLogger(__name__)

# Issue #999 (P0, HEADLINE) — Defaults aus der Fix-Spezifikation. Diese Klasse konsumiert sie ueber
# Konstruktor-Kwargs statt eines direkten Config-Reads, damit sie ohne Filesystem-Zugriff unit-
# testbar bleibt; ``momentum_ls_run.py`` laedt die tatsaechlichen Werte aus
# ``backtest.json["live_risk"]`` und reicht sie hier durch.
_DEFAULT_MAX_TOTAL_EXPOSURE_FRACTION = 0.60
_DEFAULT_MAX_SYMBOL_EXPOSURE_FRACTION = 0.10
_DEFAULT_DD_HALT_FRACTION = 0.10
_DEFAULT_PSI_MIN = 0.2
_MIN_ORDER_USD = 11.0


def _position_notional(pos) -> float | None:
    """Entry-Notional (``|quantity| * avg_px_open``) — bewusst KEIN Mark-to-Market-Wert (das würde
    einen aktuellen Quote pro offener Position voraussetzen, den der Allocator an dieser Stelle
    nicht hat). ``None`` bei jedem Attributfehler (defensiv gegen Test-Doubles/Nautilus-API-Drift) —
    der Aufrufer behandelt ``None`` fail-closed (siehe ``MomentumLSAllocator.get_allocation``)."""
    try:
        return abs(float(pos.quantity) * float(pos.avg_px_open))
    except (AttributeError, TypeError, ValueError):
        return None


class MomentumLSAllocator:
    """
    Thread-safe capital allocator for Momentum-LS live trading.
    Called by strategies at signal time to get their USD allocation.

    Issue #999 (P0, HEADLINE) — ersetzt die Vor-#999-Formel ``account_balance / pending_signals``
    (Divisor schrumpft mit jeder eroeffneten Position; die LETZTE Position erhielt das GESAMTE
    verfuegbare Kapital, ``a_n = B_{n-1}/1`` — das exakte Gegenteil einer Risikobudgetierung, siehe
    AGENTS.md Pitfall #335) durch ein Budget mit Erhaltungsbedingung:

        w_neu = min(w_sym, W_max − Σ_offen w_i) · ψ(DD)

    Die Allokationsfolge ist damit per Konstruktion monoton FALLEND in der Zahl bereits offener
    Positionen (statt steigend) und Σ w_i ueberschreitet ``max_total_exposure_fraction`` nie.
    """

    def __init__(
        self,
        universe: List[str],
        *,
        max_total_exposure_fraction: float = _DEFAULT_MAX_TOTAL_EXPOSURE_FRACTION,
        max_symbol_exposure_fraction: float = _DEFAULT_MAX_SYMBOL_EXPOSURE_FRACTION,
        dd_halt_fraction: float = _DEFAULT_DD_HALT_FRACTION,
        psi_min: float = _DEFAULT_PSI_MIN,
    ):
        """
        Initialize the allocator with the full list of universe symbols.
        """
        self._universe = [InstrumentId.from_str(sym) for sym in universe]
        self._max_total_exposure_fraction = float(max_total_exposure_fraction)
        self._max_symbol_exposure_fraction = float(max_symbol_exposure_fraction)
        self._dd_halt_fraction = float(dd_halt_fraction)
        self._psi_min = float(psi_min)

        # Issue #999 Fix Punkt 2 — vom Circuit-Breaker-Watchdog aktualisierter Zustand (siehe
        # automation/live_risk.py). Default: kein Drawdown bekannt, Breaker nicht ausgeloest.
        self._current_drawdown: float | None = None
        self._breaker_tripped: bool = False

    def update_risk_state(self, *, current_drawdown: float | None = None, tripped: bool | None = None) -> None:
        """Vom ``LiveCircuitBreakerWatchdog`` (automation/live_risk.py) periodisch aufgerufen. Trennt
        die Allokations-Formel (rein, testbar ohne einen laufenden Watchdog) von der Live-Zustands-
        Aktualisierung (Seiteneffekt)."""
        if current_drawdown is not None:
            self._current_drawdown = current_drawdown
        if tripped is not None:
            self._breaker_tripped = bool(self._breaker_tripped or tripped)

    def get_allocation(
        self,
        instrument_id: InstrumentId,
        cache: object,
        account_balance: float,
    ) -> float:
        """
        Returns the USD amount this strategy instance should allocate for a new position.
        Returns 0.0 if allocation is not possible (no capital, breaker tripped, budget exhausted,
        or position already open).
        """
        # Issue #999 Fix Punkt 2 — der Circuit-Breaker ist der schnellste Ausweg: getrippt ⇒ keine
        # neue Positionsgroesse, unabhaengig vom Rest der Formel.
        if self._breaker_tripped:
            return 0.0

        if instrument_id not in self._universe:
            logger.warning(f"Instrument {instrument_id} is not in the Momentum-LS universe.")
            return 0.0

        # No-interference rule: if there is an open position for this instrument, do not allocate
        if cache.positions_open(instrument_id=instrument_id):
            return 0.0

        if account_balance is None or account_balance <= 0:
            return 0.0

        # Issue #999 Fix Punkt 1 — Σ_offen w_i statt einer Zaehlung freier Slots. Notional aus
        # Entry-Preis (siehe _position_notional); ist es fuer IRGENDEINE offene Position nicht
        # bestimmbar, faellt die Schaetzung auf eine KONSERVATIVE Slot-Naeherung zurueck (jede offene
        # Position beansprucht ihr volles Symbol-Cap) — bleibt monoton fallend in der Positionszahl,
        # unterschaetzt die freie Kapazitaet aber nie zu ihren Gunsten.
        open_notional = 0.0
        open_count = 0
        notional_unknown = False
        for uni_id in self._universe:
            for pos in cache.positions_open(instrument_id=uni_id):
                open_count += 1
                notional = _position_notional(pos)
                if notional is None:
                    notional_unknown = True
                else:
                    open_notional += notional

        if notional_unknown:
            open_exposure_fraction = min(1.0, open_count * self._max_symbol_exposure_fraction)
        else:
            open_exposure_fraction = open_notional / account_balance

        # Issue #999 Fix Punkt 4 — Rohtelemetrie fuer check_live_exposure_budget (invariants.py):
        # jede Allokationsentscheidung stempelt ihr Σ_offen w_i, unabhaengig vom Ausgang.
        emit_execution_event(logger, "LIVE_EXPOSURE_SNAPSHOT", {
            "instrument_id": str(instrument_id),
            "open_exposure_fraction": open_exposure_fraction,
            "open_position_count": open_count,
            "notional_unknown": notional_unknown,
            "max_total_exposure_fraction": self._max_total_exposure_fraction,
        })

        remaining_fraction = max(0.0, self._max_total_exposure_fraction - open_exposure_fraction)
        w_new = min(self._max_symbol_exposure_fraction, remaining_fraction)
        if w_new <= 0.0:
            return 0.0

        psi = drawdown_damper(
            self._current_drawdown,
            dd_halt_fraction=self._dd_halt_fraction,
            psi_min=self._psi_min,
        )
        w_new *= psi

        allocation = w_new * account_balance

        # Apply floor: eToro minimum order is $10, so $11 buffer is used.
        if allocation < _MIN_ORDER_USD:
            logger.warning(
                f"Computed allocation ${allocation:.2f} for {instrument_id} "
                f"is below the ${_MIN_ORDER_USD:.2f} minimum threshold. Returning 0.0."
            )
            return 0.0

        return allocation
