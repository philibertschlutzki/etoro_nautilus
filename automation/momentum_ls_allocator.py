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
_MAX_MIN_NOTIONAL_FRACTION = 0.15     # eine Position mit erzwungenem Mindestbetrag darf höchstens 15 % der Equity belegen


def _etoro_id(instrument_id) -> int | None:
    """eToro-InstrumentID zu einer Nautilus-InstrumentId (``None``, wenn unbekannt)."""
    from automation.adapters.instrument_map import ETORO_INSTRUMENTS
    target = str(instrument_id)
    return next((int(k) for k, v in ETORO_INSTRUMENTS.items() if v == target), None)


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
        symbol_capital_fractions: dict[str, float] | None = None,
        min_equity_entry_usd: float = 0.0,
    ):
        """
        Initialize the allocator with the full list of universe symbols.

        ``symbol_capital_fractions`` — Issue #1368 (GH #1265): Anteil der regulären Symbol-Allokation je
        Symbol (``LIVE_SMALL`` ⇒ ``incubation.capital_fraction_small``); fehlende Symbole 1,0, ``None``
        (Default) ⇒ bit-identisch zum Verhalten vor #1368.
        """
        self._universe = [InstrumentId.from_str(sym) for sym in universe]
        self._max_total_exposure_fraction = float(max_total_exposure_fraction)
        self._max_symbol_exposure_fraction = float(max_symbol_exposure_fraction)
        self._symbol_capital_fractions = {
            str(k): min(1.0, max(0.0, float(v))) for k, v in (symbol_capital_fractions or {}).items()}
        self._dd_halt_fraction = float(dd_halt_fraction)
        self._psi_min = float(psi_min)
        # eToro berechnet für Aktien eine Pauschale von ca. 1 USD je Order (öffnen und schließen): kleine Aktien-
        # Einstiege (z. B. Rest vor dem Gesamtdeckel) sind relativ sehr teuer. 0 = aus.
        self._min_equity_entry_usd = max(0.0, float(min_equity_entry_usd))

        # Issue #999 Fix Punkt 2 — vom Circuit-Breaker-Watchdog aktualisierter Zustand (siehe
        # automation/live_risk.py). Default: kein Drawdown bekannt, Breaker nicht ausgeloest.
        self._current_drawdown: float | None = None
        self._breaker_tripped: bool = False
        # Kontosicht direkt von eToro (automation/account_view.py): Equity, Exposure und offene Positionen für die
        # Risikorechnung. ``None`` = Alt-Verhalten (nur Nautilus-Cache), z. B. in Backtests und Tests.
        self._account_view = None

    @property
    def max_symbol_exposure_fraction(self) -> float:
        """Issue #1297 (GH #1170, Katalog #1272-1297, P1) Fix Punkt 4 — der Strategie-seitige
        Post-Fill-Sizing-Deckel (``hourly_strategy_base.on_position_opened``, siehe
        ``live_risk.compute_sizing_cap_correction``) braucht denselben Zielanteil, den
        ``get_allocation`` bereits zur Bemessung verwendet, statt einer zweiten, unabhaengig
        gepflegten Konstante (#1014/#1166-Klasse)."""
        return self._max_symbol_exposure_fraction

    def set_account_view(self, view) -> None:
        """Aktiviert die Risikorechnung auf Basis der direkt von eToro gelesenen Positionen (siehe account_view)."""
        self._account_view = view

    def risk_equity(self) -> float | None:
        """Konto-Equity von eToro (Guthaben + Investiertes + unrealisierter PnL); ``None`` ohne Kontosicht/Stand."""
        if self._account_view is None:
            return None
        snap = self._account_view.snapshot()
        return None if snap is None else snap.equity

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

        snap = None
        if self._account_view is not None:
            # Risikorechnung auf Kontoebene direkt von eToro: fail-closed, wenn der Kontostand nicht lesbar ist.
            snap = self._account_view.snapshot()
            if snap is None:
                logger.warning(f"[RISK] Kontostand von eToro nicht lesbar — kein Einstieg für {instrument_id}.")
                return 0.0
            from automation.adapters.instrument_map import ETORO_INSTRUMENTS
            eid = next((int(k) for k, v in ETORO_INSTRUMENTS.items() if v == str(instrument_id)), None)
            if eid is not None and snap.positions_in(eid):
                return 0.0      # eToro zeigt bereits eine Position in diesem Instrument (Cache evtl. veraltet/fremd)
            account_balance = snap.equity   # Bemessungsgrundlage ist die Equity, nicht das schrumpfende freie Guthaben

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

        if snap is not None:
            # Das Konto zählt: alle offenen eToro-Positionen (auch fremde/nicht übernommene) belegen das Budget, mit dem
            # eToro-Betrag in USD (snap.notional). Der Bot-Cache zählt nur für Instrumente, die eToro (noch) nicht
            # zeigt, also gerade gefüllt und noch nicht abgerufen. NICHT per max(): Bei einer vom Bot übernommenen
            # Position ist die Cache-Menge die echte Einheitenzahl und der Kurs steht in der Quote-Währung (NZDJPY:
            # 3.564,66 NZD × 88,613 JPY = 315.875 statt 1.991 USD), das verfünffachte Exposure auf 668 % und sperrte jeden
            # Einstieg, solange eine JPY-/MXN-Position offen war.
            in_snapshot = {p.instrument_id for p in snap.positions}
            extra_count, extra_notional, extra_unknown = 0, 0.0, False
            for uni_id in self._universe:
                eid = _etoro_id(uni_id)
                if eid is not None and eid in in_snapshot:
                    continue
                for pos in cache.positions_open(instrument_id=uni_id):
                    extra_count += 1
                    notional = _position_notional(pos)
                    if notional is None:
                        extra_unknown = True
                    else:
                        extra_notional += notional
            if extra_unknown:
                extra_notional += self._max_symbol_exposure_fraction * account_balance     # konservativ: volles Symbol-Cap
            open_count = len(snap.positions) + extra_count
            open_notional = snap.notional + extra_notional
            notional_unknown = False
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
        w_symbol = self._max_symbol_exposure_fraction * self._symbol_capital_fractions.get(str(instrument_id), 1.0)
        w_new = min(w_symbol, remaining_fraction)
        if w_new <= 0.0:
            return 0.0

        psi = drawdown_damper(
            self._current_drawdown,
            dd_halt_fraction=self._dd_halt_fraction,
            psi_min=self._psi_min,
        )
        w_new *= psi

        allocation = w_new * account_balance
        if snap is not None:
            allocation = min(allocation, 0.98 * max(snap.credit, 0.0))     # nie mehr als das freie Guthaben

        # eToro-Mindestbetrag je Instrument (Forex 1000 USD): anheben, wenn Gesamtbudget, freies Guthaben und
        # Konzentrationsgrenze es zulassen, sonst überspringen (statt stündlich eine abgelehnte Order zu senden).
        from automation.min_position import min_notional
        from automation.strategies.hourly_strategy_base import _read_instrument_asset_class
        needed = min_notional(str(instrument_id), _read_instrument_asset_class(str(instrument_id)))
        if needed > 0.0 and allocation < needed:
            target = needed * 1.01
            free_cash = 0.98 * max(snap.credit, 0.0) if snap is not None else float("inf")
            if (target <= remaining_fraction * account_balance * psi
                    and target <= _MAX_MIN_NOTIONAL_FRACTION * account_balance and target <= free_cash):
                allocation = target
            else:
                logger.info(f"[RISK] {instrument_id}: eToro-Mindestbetrag {needed:.0f} USD passt nicht ins Budget "
                            f"(Allokation {allocation:.0f} USD) — übersprungen.")
                return 0.0

        # Aktien-Mindestgröße (Pauschale ca. 2 USD Round-Trip): kleinere Einstiege werden übersprungen, nie hochgesetzt
        # (Hochsetzen würde Drawdown-Dämpfer und Gesamtdeckel aushebeln).
        if self._min_equity_entry_usd > 0.0 and allocation < self._min_equity_entry_usd:
            from automation.strategies.hourly_strategy_base import _read_instrument_asset_class
            if str(_read_instrument_asset_class(str(instrument_id)) or "").upper() == "EQUITY":
                logger.info(f"[RISK] {instrument_id}: Aktien-Einstieg {allocation:.0f} USD < Mindestgröße "
                            f"{self._min_equity_entry_usd:.0f} USD (Pauschalgebühr) — übersprungen.")
                return 0.0

        # Apply floor: eToro minimum order is $10, so $11 buffer is used.
        if allocation < _MIN_ORDER_USD:
            logger.warning(
                f"Computed allocation ${allocation:.2f} for {instrument_id} "
                f"is below the ${_MIN_ORDER_USD:.2f} minimum threshold. Returning 0.0."
            )
            return 0.0

        return allocation


def validate_and_reconcile_live_execution(
    executed_orders: list | None = None,
    expected_orders: list | None = None,
    target_size: float = 100.0,
    current_spread: float = 0.0,
    atr_14: float = 1.0,
    broker_position: float = 0.0,
    strategy_position: float = 0.0,
    alpha_spread_tolerance: float = 0.05,
) -> float | dict:
    """Issue #800 — Live execution reconciliation and drift validation."""
    if current_spread > 0.0 and atr_14 > 0.0:
        max_spread = alpha_spread_tolerance * atr_14
        if current_spread > max_spread:
            return 0.0
        dampener = max(0.0, 1.0 - (current_spread / max_spread))
        target_size = target_size * dampener
    if broker_position != strategy_position:
        return strategy_position - broker_position
    if executed_orders is not None or expected_orders is not None:
        n_exec = len(executed_orders or [])
        n_exp = len(expected_orders or [])
        matched = min(n_exec, n_exp)
        drift = abs(n_exec - n_exp)
        return {
            "reconciled": (drift == 0),
            "executed_count": n_exec,
            "expected_count": n_exp,
            "matched_count": matched,
            "order_count_drift": drift,
        }
    return float(target_size)



