"""automation/optimizer/_contracts.py
=====================================
Issue #858 (Pitfall #271) — neutrales Konstantenmodul für Vertragswerte, die mehrere Module
unabhängig voneinander pflegten. Zwei unabhängig gepflegte Konstanten für denselben Vertrag
erzeugen garantiert Fehlalarme in die eine oder Blindheit in die andere Richtung — Single Source
of Truth statt drei Kopien (``invariants._MAX_BARS_IN_TRADE_CAP``, ``spaces._MAX_BARS_IN_TRADE_CAP``,
``hourly_strategy_base.MAX_BARS_IN_TRADE_HARD_CAP``, vor diesem Fix drei unabhängige Literale).

Bewusst OHNE jede Abhängigkeit (kein nautilus_trader, kein Optuna, kein pandas) — importierbar von
``invariants.py`` (deklariert sich im eigenen Moduldocstring als frei von nautilus_trader),
``spaces.py`` UND ``automation.strategies.hourly_strategy_base`` (importiert nautilus_trader
bereits selbst; schliesst sich hier nur der gemeinsamen Konstantenquelle an) ohne einen
Import-Zyklus zu riskieren.
"""
from __future__ import annotations

# Issue #714/GR-01 — die 24-Bar-Zeitbox-Obergrenze für ``max_bars_in_trade``. Der Bar-Zähler-Exit
# in ``HourlyStrategyBase`` erzwingt sie unabhängig vom je Trial gesampelten Wert; der Optuna-
# Suchraum (``spaces.py``) und die Report-Invarianten (``invariants.py``) dürfen NIE grösser
# suchen/prüfen als dieser Deckel.
MAX_BARS_IN_TRADE_HARD_CAP = 24

# Issue #938 (Katalog A, Pitfall #294) — EINZIGER Konstruktor für den (Strategie, Symbol)-Paar-
# Schlüssel. Vor diesem Fix baute jede Stelle (``invariants.py``, ``report.py``, ``confirm.py``,
# ``sweep.py``) das ``f"{strategy}/{symbol}"``-Format unabhängig selbst nach — an GENAU einer
# Stelle (``sweep._offending_pairs_for_fail_fast_check``) wattierte stattdessen ein rohes
# ``tuple[str, str]`` als Dict-Key in einen Telemetrie-Payload, was #937s ``TypeError`` auslöste.
# ``pair_key``/``split_pair_key`` sind ab jetzt die einzige Quelle; interne Rechen-Container
# dürfen weiterhin Tuples verwenden — die Konvertierung erfolgt genau an der Telemetrie-/Report-
# Grenze, nicht verstreut.
PAIR_KEY_SEP = "/"


def pair_key(strategy: str, symbol: str) -> str:
    """Kanonischer (Strategie, Symbol)-Paar-Schlüssel im Hausformat ``"strategy/symbol"``."""
    if PAIR_KEY_SEP in strategy or PAIR_KEY_SEP in symbol:
        raise ValueError(
            f"pair_key: Separator {PAIR_KEY_SEP!r} in Bestandteil: {strategy!r}/{symbol!r}")
    return f"{strategy}{PAIR_KEY_SEP}{symbol}"


def split_pair_key(key: str) -> tuple[str, str]:
    """Kehrfunktion zu :func:`pair_key`. Kein ``PAIR_KEY_SEP`` im Key ⇒ ``(key, "")``."""
    strategy, _, symbol = key.partition(PAIR_KEY_SEP)
    return strategy, symbol

# Issue #902 (Pitfall #271, dritte Instanz nach #714/MAX_BARS_IN_TRADE_HARD_CAP) — die EINZIGE
# Definition der 1h-Bar-Sekundenzahl im Repository. Vorher unabhängig als ``3600.0``-Literal in
# ``invariants.py`` (``_BAR_SECONDS``), ``invariants.py`` (``compute_trial_timebox_violations``-
# Sentinel-Fallback) UND ``run_optimization.py`` (eigene Kopie, umging sogar die #858-Warnung)
# gepflegt. Konsumenten (``confirm.py``/``report.py``/``run_optimization.py``) sollen bevorzugt
# einen SYMBOL-SPEZIFISCHEN ``bar_seconds``-Wert aus der #900-Bar-Qualitäts-Telemetrie
# (``median_delta_t_s``) verwenden; dieser Wert ist NUR der fail-loud zu protokollierende Fallback,
# falls diese Telemetrie fehlt (Issue #902 Fix 3).
BAR_SECONDS_DEFAULT = 3600.0


# Issue #918 (Verallgemeinerung von #914) — EINE Registry für jeden Inferenzpfad-Diagnose-Code, den
# ``backtest_runner._calculate_stats`` in ``inference_diagnostics`` stempelt. Root-Cause: jede der
# fünf unabhängig gepflegten Konsumenten-Stellen (``run_optimization.py::_inference_failure_codes``,
# ``parsing.py``, ``report.py``, ``invariants.py::check_inference_diagnostics_absent``,
# Test-Fixtures) musste bislang manuell nachgezogen werden, sobald ein neuer Code eingeführt wurde —
# genau das unterblieb bei ``SORTINO_GUARD_REFERENCE_UNAVAILABLE`` (#901) und liess #914s
# ``inference_failure_policy='prune'`` folgenlos. Ab jetzt deklariert GENAU DIESE Datei jeden Code;
# alle Konsumenten importieren von hier (AST-Vertragstest in
# ``automation/tests/test_inference_diagnostic_registry.py`` verlangt das).
from dataclasses import dataclass, field


@dataclass(frozen=True)
class InferenceDiagnosticCode:
    """Ein einzelner ``inference_diagnostics``-Code und seine vollständige Handhabungs-Policy.

    ``failure_policy`` ∈ {'prune', 'floor', 'telemetry_only'}:
      * 'prune'/'floor' — der Code markiert einen Trial als NICHT MESSBAR (kein Ergebnis, kein
        Datenfehler); ``run_optimization.py`` behandelt ihn gemäss der GLOBALEN
        ``optimizer.json['inference_failure_policy']`` (pruned ODER gefloored, siehe #864).
      * 'telemetry_only' — reine Diagnose ohne Konsequenz auf Trial-Ebene (z. B. eine Warnung, die
        keinen Metrik-Ausfall bedeutet); wird NIE gepruned/geflort, unabhängig vom globalen Modus.
    """
    code: str
    failure_policy: str
    severity: str
    description: str
    nullifies_metrics: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.failure_policy not in ("prune", "floor", "telemetry_only"):
            raise ValueError(
                f"InferenceDiagnosticCode({self.code!r}).failure_policy={self.failure_policy!r} "
                "unbekannt — erwartet 'prune', 'floor' oder 'telemetry_only'.")


INFERENCE_DIAGNOSTIC_CODES: dict[str, InferenceDiagnosticCode] = {}


def _register_inference_code(code: str, *, failure_policy: str, severity: str, description: str,
                             nullifies_metrics: tuple[str, ...] = ()) -> InferenceDiagnosticCode:
    entry = InferenceDiagnosticCode(
        code=code, failure_policy=failure_policy, severity=severity, description=description,
        nullifies_metrics=nullifies_metrics,
    )
    if code in INFERENCE_DIAGNOSTIC_CODES:
        raise ValueError(f"InferenceDiagnosticCode {code!r} doppelt registriert.")
    INFERENCE_DIAGNOSTIC_CODES[code] = entry
    return entry


_register_inference_code(
    "SORTINO_GUARD_TRIPPED", failure_policy="prune", severity="high",
    description="|sortino_annualized| > effektivem Numerik-Guard — als Datenfehler verworfen "
                "(#614/#665).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
_register_inference_code(
    "SORTINO_INSUFFICIENT_DOWNSIDE", failure_policy="prune", severity="high",
    description="n_periods/downside_obs unter der konfigurierten Mindest-Stichprobe — Sortino "
                "nicht schätzbar (#823/#863).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
_register_inference_code(
    "EQUITY_NONPOSITIVE", failure_policy="prune", severity="blocking",
    description="Portfolio-Equity <= 0 während des OOS-Fensters — wirtschaftlich ruiniert (#825).",
    nullifies_metrics=("oos_sortino_period", "oos_psr", "oos_total_return"),
)
_register_inference_code(
    # Issue #947 (Katalog B) — HourlyStrategyBase erzwingt seit diesem Fix einen Margin-Stop-out
    # (equity <= stop_out_equity_frac * initial_equity ⇒ sofortiger Markt-Close, kein Handel mehr),
    # BEVOR die Equity nicht-positiv werden kann (EQUITY_NONPOSITIVE bleibt als Defense-in-Depth
    # bestehen, sollte aber nach diesem Fix praktisch nie mehr feuern). Ein Trial mit mindestens
    # einem EQUITY_STOPOUT-Round-Trip ist wirtschaftlich ruiniert — dieselbe Konsequenz
    # (failure_policy='prune') wie EQUITY_NONPOSITIVE, nur VOR statt NACH dem Kapitalverlust erkannt.
    "TRIAL_RUINED_STOPOUT", failure_policy="prune", severity="blocking",
    description="Mindestens ein Round-Trip wurde via EQUITY_STOPOUT (Margin-Stop-out, "
                "stop_out_equity_frac) geschlossen — Trial wirtschaftlich ruiniert (#947).",
    nullifies_metrics=("oos_sortino_period", "oos_psr", "oos_total_return"),
)
_register_inference_code(
    # Issue #914 — dieser Code (#901 neu eingeführt) fehlte bislang in JEDEM Konsumenten ausser
    # der Diagnose-Erzeugung selbst; ``inference_failure_policy='prune'`` konnte ihn nie greifen.
    "SORTINO_GUARD_REFERENCE_UNAVAILABLE", failure_policy="prune", severity="blocking",
    description="sortino_numeric_guard_reference='family_median' ohne verfügbaren Familien-Median "
                "(Kaltstart mit sortino_numeric_guard_reference_bootstrap='defer') — Trial unter "
                "dieser Referenz-Semantik nicht bewertbar (#901/#913).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
# Issue #918 (Verallgemeinerung von #914) — die übrigen ``inference_diagnostics``-Codes aus
# ``backtest_runner.py``, bislang NUR im jeweiligen Docstring/Log dokumentiert, nirgends
# registriert. Alle sechs sind 'telemetry_only': sie fliessen NICHT in
# ``run_optimization._inference_failure_codes`` (kein Prune allein aufgrund dieses Codes) — der
# jeweilige Trial wird bereits über einen ANDEREN, direkteren Mechanismus als nicht auswertbar
# markiert (z. B. ``oos_evaluated=False`` direkt im Metrics-Dict), dieser Code ist die zusätzliche,
# forensische Erklärung WARUM.
_register_inference_code(
    "COHERENCE_INVARIANT_VIOLATION", failure_policy="telemetry_only", severity="high",
    description="sign(oos_sortino) != sign(oos_total_return) bei |return| > Toleranz — gepoolter "
                "OOS-Sortino und Return sind inkohärent (#589/#804).",
)
_register_inference_code(
    "RETURN_SERIES_IDENTITY_UNDEFINED", failure_policy="telemetry_only", severity="blocking",
    description="total_return <= -1 — log1p(1+total_return) nicht definierbar (Equity-Kurve durch/"
                "unter Null, die stärkste mögliche #756-Identitätsverletzung) (#801/#804).",
)
_register_inference_code(
    "PERIOD_RETURNS_NOT_FINITE", failure_policy="telemetry_only", severity="blocking",
    description="Σlog(1+rᵢ) ist nicht endlich (NaN/±inf) — die Renditeserie der Inferenz enthält "
                "einen nicht-finiten Wert (#801/#804).",
)
_register_inference_code(
    "RETURN_SERIES_IDENTITY_VIOLATION", failure_policy="telemetry_only", severity="high",
    description="Σlog(1+rᵢ) != log(1+total_return) — total_return und die Renditeserie der "
                "Inferenz stammen nicht aus derselben Bar-Menge (#756/#771/#804).",
)
_register_inference_code(
    "NON_CONTIGUOUS_FOLD_SEGMENTS", failure_policy="telemetry_only", severity="medium",
    description="die mtm_frames-Fold-Segmente sind nicht lückenlos aneinandergereiht — Restlücke "
                "im mtm_frames-Fallback-Pfad (#771).",
)
_register_inference_code(
    "EXIT_CLOSE_UNRECOVERABLE", failure_policy="telemetry_only", severity="blocking",
    description=">= exit_close_max_retries verweigerte/abgelehnte Markt-Close-Versuche — Trial "
                "als ungültig markiert statt einer still durchgehaltenen offenen Position (#859).",
)
_register_inference_code(
    # Issue #944 (Katalog B) — ERSETZT die vorherige SORTINO_INSUFFICIENT_DOWNSIDE-Verwerfung für
    # den Fall "downside_obs unter der (proportionalen/absoluten) Schwelle": der Trial wird NICHT
    # mehr geprunt (das war der Anti-Selektions-Filter, Pitfall #296), sondern bleibt bewertbar,
    # nur mit einer Richtung-Gesamtstreuung geschrumpften Downside-Deviation. 'telemetry_only', weil
    # der Trial weiterhin einen gültigen Sortino/PSR trägt (kein Prune-Grund).
    "SORTINO_DOWNSIDE_SHRUNK", failure_policy="telemetry_only", severity="medium",
    description="downside_obs unter der konfigurierten Schwelle — Downside-Deviation James-Stein-"
                "artig Richtung der Gesamtstreuung aller informativen Perioden geschrumpft, statt "
                "den Trial zu verwerfen (#944, ersetzt die frühere Anti-Selektions-Verwerfung).",
)
# Issue #967 (Katalog A, P0 HEADLINE) — VORHER stumme Rückgabepfade in
# ``backtest_runner._calculate_stats``/``_compute_sortino``: 91,4 % der 490 Trials ohne
# Selektionsstatistik im Referenzlauf 46cf5070 emittierten KEINE Diagnose. Jeder Pfad, der
# ``sortino``/``psr`` auf ``None`` setzt, ist jetzt hier registriert (#965 Fix Punkt 1 — geschlossenes
# Enum statt eines Docstring-dokumentierten, aber nicht durchsetzbaren Vertrags).
_register_inference_code(
    "SORTINO_INSUFFICIENT_TRADES", failure_policy="prune", severity="high",
    description="n_trades < min_trades_sortino oder informative_rets leer — Sortino nicht "
                "schätzbar, bevor irgendeine Momenten-/Downside-Berechnung stattfindet (#967).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
_register_inference_code(
    "SORTINO_DOWNSIDE_DEVIATION_UNDEFINED", failure_policy="prune", severity="high",
    description="dd_dev (Downside-Deviation) ist NaN — degenerierte Renditeserie (#967).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
_register_inference_code(
    "SORTINO_ANNUALIZED_NONFINITE", failure_policy="prune", severity="high",
    description="Der annualisierte Sortino ist NaN/inf (z. B. entartete "
                "effective_annualization_factor) — als undefiniert behandelt, bevor der Numerik-"
                "Guard (SORTINO_GUARD_TRIPPED) überhaupt geprüft wird (#967).",
    nullifies_metrics=("oos_sortino_period", "oos_sortino_annualized", "oos_psr"),
)
_register_inference_code(
    "PSR_BOOTSTRAP_UNDEFINED", failure_policy="prune", severity="high",
    description="deflation.bootstrap_psr_z lieferte (None, None) trotz gültigem sortino_period — "
                "degenerierte Bootstrap-Streuung über die Resamples oder < 2 informative Perioden. "
                "NULLIFIZIERT NUR oos_psr, sortino_period/sortino_annualized bleiben gültig (#965/"
                "#967) — der einzige Code dieser Registry mit dieser asymmetrischen Konsequenz.",
    nullifies_metrics=("oos_psr",),
)
