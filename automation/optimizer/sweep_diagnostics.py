"""Issue #669 — Suchraum-Diagnose-Artefakt + deklarative (Symbol, Strategie)-Deaktivierungsliste.

Symptom: 7 von 10 Strategien tragen auf TSLA-1h NICHTS zur Familie bei — 0 evaluable (nie ≥
``oos_min_trades`` OOS-Round-Trips, ``STRUCTURAL_ALL_UNEVALUABLE``, #656) ODER 0 eligible bei
VOLLER Evaluierbarkeit (``ZERO_ELIGIBLE_PLATEAU``, #656). Der #656-Diagnose-/Early-Stop-Mechanismus
feuert korrekt, unterscheidet aber NICHT zwischen den beiden strukturell unterschiedlichen
Kollaps-Ursachen: ein Suchraum, der zu wenige Trades erzeugt (``spaces.py``-Bounds-Problem), und
ein Suchraum, der genug Trades, aber nie ein risikoadjustiert eligibles Ergebnis erzeugt
(Signal-Qualität, kein Bounds-Problem). Dieses Modul trennt beide Fälle explizit.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

# Issue #669 — die vier möglichen bindenden Ursachen. 'none' ⇒ kein Kollaps (mind. 1 eligible Trial).
_BINDING_CAUSES = frozenset(
    {"signal_frequency", "hold_duration", "signal_quality", "none", "no_data"}
)


def diagnose_trade_frequency(trials: list[dict], *, oos_min_trades: int) -> dict:
    """Bestimmt die BINDENDE Ursache eines 0-evaluable/0-eligible-Kollapses für ein
    (Symbol, Strategie)-Paar aus den per-Trial-Kennzahlen (``oos_evaluated``, ``oos_eligible``,
    ``oos_total_trades``, ``is_total_trades``, ``hit_trade_cap`` — bereits als Trial-User-Attrs
    gestempelt, run_optimization.make_symbol_objective).

    Trennt (Akzeptanzkriterium #669):
      * ``'signal_frequency'`` — 0 evaluable UND die IS-Aktivität (``is_total_trades``, Median)
        bleibt bereits UNTER ``oos_min_trades`` ⇒ der Suchraum erzeugt zu selten überhaupt ein
        Signal (Bounds zu eng, z. B. Signal-Schwellen/Cooldown).
      * ``'hold_duration'`` — 0 evaluable, IS-Aktivität AUSREICHEND, aber die Mehrheit der Trials
        trifft die Haltedauer-/Trade-Cap-Grenze (``hit_trade_cap``) ⇒ Signale entstehen, überleben
        aber nicht bis zur Realisierung im OOS-Fenster (Haltedauer-Obergrenze zu lang/kurz falsch
        kalibriert).
      * ``'signal_quality'`` — ALLE Trials wurden evaluiert (echte OOS-Backtests, genug Trades),
        aber KEINER war je eligible ⇒ ein Suchraum-/Bounds-Problem ist NICHT die Ursache; die
        Strategie ist auf diesem Symbol/Tier strukturell nicht kanten-fähig (oder das Gate ist zu
        streng) — Bounds-Kalibrierung würde hier NICHTS beheben.
      * ``'none'`` — mindestens ein Trial war eligible (kein Kollaps).

    Rückgabe: ``{'n_trials', 'n_evaluable', 'n_eligible', 'median_oos_trades', 'median_is_trades',
    'frac_hit_trade_cap', 'binding_cause'}``. Rein, deterministisch, kein I/O."""
    n = len(trials)
    if n == 0:
        return {
            "n_trials": 0, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": None, "median_is_trades": None,
            "frac_hit_trade_cap": None, "binding_cause": "no_data",
        }

    n_evaluable = sum(1 for t in trials if t.get("oos_evaluated"))
    n_eligible = sum(1 for t in trials if t.get("oos_eligible"))
    oos_trade_counts = [int(t.get("oos_total_trades") or 0) for t in trials]
    median_oos_trades = statistics.median(oos_trade_counts) if oos_trade_counts else 0
    frac_hit_cap = sum(1 for t in trials if t.get("hit_trade_cap")) / n

    if n_evaluable == 0:
        is_trade_counts = [int(t.get("is_total_trades") or 0) for t in trials]
        median_is_trades = statistics.median(is_trade_counts) if is_trade_counts else 0
        if median_is_trades < oos_min_trades:
            binding_cause = "signal_frequency"
        elif frac_hit_cap > 0.5:
            binding_cause = "hold_duration"
        else:
            binding_cause = "signal_frequency"
        return {
            "n_trials": n, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": median_oos_trades, "median_is_trades": median_is_trades,
            "frac_hit_trade_cap": frac_hit_cap, "binding_cause": binding_cause,
        }

    if n_eligible == 0:
        return {
            "n_trials": n, "n_evaluable": n_evaluable, "n_eligible": 0,
            "median_oos_trades": median_oos_trades, "median_is_trades": None,
            "frac_hit_trade_cap": frac_hit_cap, "binding_cause": "signal_quality",
        }

    return {
        "n_trials": n, "n_evaluable": n_evaluable, "n_eligible": n_eligible,
        "median_oos_trades": median_oos_trades, "median_is_trades": None,
        "frac_hit_trade_cap": frac_hit_cap, "binding_cause": "none",
    }


def eligibility_curve(trials: list[dict], *, window: int = 16) -> list[float]:
    """Issue #700 — die per-Trial ``oos_eligible``-Fraktion je kontiguierlichem Fenster von
    ``window`` Trials (in Trial-Reihenfolge), NICHT nur die aggregierte Gesamtzahl. Unterscheidet
    TRANSIENTE Null-Eligibilität (irgendwo zwischenzeitlich eligible Trials, die spaeter — z. B.
    durch eine spaetere confirm-Filterung — nicht mehr im finalen Cohort auftauchen) von
    PERMANENTER Null-Eligibilität (jedes Fenster zeigt 0.0) — genau die im #700-Symptom offene
    Frage: "reproduziert der Squeeze-Fall einen sauberen 16-Trial-Stop, oder weist die Telemetrie
    transiente eligible Trials aus (dann ist es keine strukturelle Ursache, sondern eine spaetere
    Filterung, die selbst zu erklaeren ist)". Das letzte Fenster darf kuerzer als ``window`` sein.

    Rein, deterministisch, kein I/O. Rückgabe: Liste der Fensteranteile ``eligible/n`` (``[]`` bei
    leerer ``trials``-Liste)."""
    fractions = []
    for i in range(0, len(trials), window):
        chunk = trials[i:i + window]
        n_eligible = sum(1 for t in chunk if t.get("oos_eligible") is True)
        fractions.append(n_eligible / len(chunk))
    return fractions


# Issue #681 — Strategien, für die spaces.py._bounds_for tatsächlich Symbol-Overrides auflöst
# (#669). Nur für diese ist ein "search_space_override"-Vorschlag sinnvoll — für jede andere
# Strategie hätte ein Bounds-Override keine Wirkung (fail-open, aber nutzlos), daher direkt Denylist.
WIRED_OVERRIDE_STRATEGIES = frozenset({
    "TrendPullbackStrategy", "AdxAtrMomentumStrategy", "HourlyMeanReversionStrategy",
})


def has_existing_search_space_override(strategy: str, symbol: str, base_cfg: Path | None = None) -> bool:
    """Issue #681 — prüft, ob ``search_space_overrides.json`` bereits einen (nicht-leeren)
    Bounds-Override für ``(strategy, symbol)`` enthält (irgendein Parameter genügt). Fehlt die
    Datei/der Eintrag ⇒ ``False`` (bit-identisch zum Pre-#681-Verhalten, kein Override vorhanden)."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "search_space_overrides.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return False
    entry = (data.get("overrides", {}) or {}).get(strategy, {}).get(symbol)
    return bool(entry)


def recommend_diagnosis_action(strategy: str, symbol: str, diagnosis: dict, *,
                               has_existing_override: bool = False,
                               previously_recommended_override: bool = False) -> dict:
    """Issue #681 — schliesst die #669-Diagnose zu einer KONKRETEN Aktions-Empfehlung: die
    Diagnose (``diagnose_trade_frequency``) feuert bereits (STRUCTURAL_ALL_UNEVALUABLE /
    ZERO_ELIGIBLE_PLATEAU), schreibt aber nicht zurück — dieselben strukturell toten Paare werden
    bei JEDEM Lauf neu enumeriert (Root-Cause #681).

    Fallunterscheidung nach ``binding_cause`` (#669):
      * ``'signal_quality'`` (ALLE Trials evaluiert, 0 eligible) — Bounds-Kalibrierung hilft NICHT
        (kein Frequenz-, ein Qualitätsproblem) ⇒ ``'denylist'``.
      * ``'signal_frequency'``/``'hold_duration'`` (0 evaluable) UND die Strategie ist für
        Symbol-Bounds-Overrides verdrahtet (``WIRED_OVERRIDE_STRATEGIES``) UND es existiert noch
        KEIN Override für dieses Paar ⇒ ``'search_space_override'`` (Bounds-Kalibrierung probieren,
        BEVOR das Paar aufgegeben wird). Existiert bereits ein Override (Bounds-Kalibrierung wurde
        schon versucht) UND das Paar ist TROTZDEM tot ⇒ Eskalation auf ``'denylist'``.
      * Frequenzproblem bei einer NICHT verdrahteten Strategie ⇒ ``'denylist'`` (ein Override hätte
        ohnehin keine Wirkung, spaces.py._bounds_for ist fail-open no-op für sie).
      * ``'none'``/``'no_data'`` ⇒ ``'none'`` (kein Kollaps, nichts zu tun).

    Issue #699 — ``previously_recommended_override`` schliesst die verbleibende Lücke der
    #681-Closed-Loop: eine ``'search_space_override'``-Empfehlung OHNE Eskalationspfad wiederholt
    sich bei JEDEM Lauf identisch, solange niemand tatsächlich einen Override in
    ``search_space_overrides.json`` einträgt — genau das #699-Symptom ("jeder Lauf verbrennt
    3 × 16 Trials für dieselben nicht-viablen Paare"), weil NUR ``'denylist'``-Empfehlungen den
    Budget-Skip in ``enumerate_tunable_pairs`` auslösen. Wurde für dasselbe (``strategy``,
    ``symbol``)-Paar in einem VORHERIGEN Lauf bereits ``'search_space_override'`` empfohlen (aus dem
    ``diagnosed_pairs_cache``, vom Aufrufer geprüft) UND existiert weiterhin KEIN Override UND das
    Paar scheitert WEITERHIN an derselben Frequenz-/Haltedauer-Ursache, wird die Empfehlung dieses
    Mal auf ``'denylist'`` eskaliert — die Override-Chance wird genau EINMAL gewährt, dann greift
    der Budget-Skip. Default ``False`` ⇒ bit-identisch zum Pre-#699-Verhalten.

    Rein, deterministisch, kein I/O. Rückgabe: ``{'strategy', 'symbol', 'action', 'binding_cause',
    'median_oos_trades', 'median_is_trades'}``."""
    cause = diagnosis.get("binding_cause")
    if cause in ("none", "no_data", None):
        action = "none"
    elif cause == "signal_quality":
        action = "denylist"
    elif cause in ("signal_frequency", "hold_duration"):
        if strategy in WIRED_OVERRIDE_STRATEGIES and not has_existing_override:
            action = "denylist" if previously_recommended_override else "search_space_override"
        else:
            action = "denylist"
    else:
        action = "denylist"
    return {
        "strategy": strategy, "symbol": symbol, "action": action, "binding_cause": cause,
        "median_oos_trades": diagnosis.get("median_oos_trades"),
        "median_is_trades": diagnosis.get("median_is_trades"),
    }


def _diagnosed_pairs_cache_path(work_dir: Path | None = None) -> Path:
    if work_dir is None:
        from automation.optimizer.manifest import WORK
        work_dir = WORK
    return Path(work_dir) / "diagnosed_pairs_cache.json"


def load_diagnosed_pairs_cache(work_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Issue #681 — der AUTOMATISCH gepflegte Diagnose-Cache (``data/optimizer/
    diagnosed_pairs_cache.json``) — bewusst GETRENNT von der menschlich-kuratierten
    ``symbol_strategy_denylist.json``: dieser Cache schliesst NUR die Budget-Schleife
    (``enumerate_tunable_pairs`` überspringt ein ``'denylist'``-empfohlenes Paar automatisch ab dem
    NÄCHSTEN Lauf), ändert aber NIE die versionierte, PR-gebundene Config-Denylist selbst. Fehlt die
    Datei ⇒ ``{}`` (bit-identisch zum Pre-#681-Verhalten)."""
    path = _diagnosed_pairs_cache_path(work_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], dict] = {}
    for entry in data.get("pairs", []) or []:
        strat, sym = entry.get("strategy"), entry.get("symbol")
        if strat and sym:
            out[(strat, sym)] = entry
    return out


def record_diagnosed_pair(recommendation: dict, *, work_dir: Path | None = None) -> Path:
    """Issue #681 — schreibt/aktualisiert den (Symbol, Strategie)-Diagnose-Eintrag im automatisch
    gepflegten Cache (siehe ``load_diagnosed_pairs_cache``-Docstring). Ein No-Op-Eintrag
    (``action == 'none'``) wird NICHT gespeichert (kein Kollaps ⇒ nichts zu cachen). Idempotent:
    ein erneuter Diagnose-Lauf für dasselbe Paar überschreibt den vorherigen Eintrag."""
    if recommendation.get("action") == "none":
        return _diagnosed_pairs_cache_path(work_dir)
    path = _diagnosed_pairs_cache_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_diagnosed_pairs_cache(work_dir)
    cache[(recommendation["strategy"], recommendation["symbol"])] = recommendation
    payload = {"pairs": list(cache.values())}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_continuous_bar_invalid_strategies(base_cfg: Path | None = None) -> frozenset[str]:
    """Issue #698 — Strategien, deren Signal auf synthetischen, KONTINUIERLICHEN 24/7-1h-Bars
    (die einzige in diesem System verfügbare Bar-Semantik, kein RTH-Session-Katalog) strukturell
    etwas anderes misst als beabsichtigt — z. B. ``GapContinuationStrategy`` (Variante A,
    Vortagsschluss-vs-Tagesbeginn-Gap): ohne echte Handelspausen zwischen Kalendertagen degeneriert
    der Gap zur Differenz zweier AUFEINANDERFOLGENDER Bars, die keinerlei Continuation-Edge trägt
    (bekannter 24/7-Boundary-Caveat, SPEC_05/#693). Kein Bounds-Weiten behebt eine ungültige
    Messung — die Entscheidung ist an der BAR-SEMANTIK des Systems festzumachen, nicht am
    Backtest-Ergebnis (die Root-Cause-Präzisierung aus #698).

    Deklariert in ``strategies.json`` je Strategie-Eintrag als ``invalid_on_continuous_bars: true``
    (Zero-Hardcoding — keine Python-Konstante). ``sweep.enumerate_tunable_pairs`` überspringt eine
    gelistete Strategie VOLLSTÄNDIG (alle Symbole, EIN strukturiertes Event statt N nutzloser
    Trials) und weist sie im ``sweep_completed``-Event als ``strategies_skipped`` mit dem Grund
    ``SKIPPED_INVALID_ON_CONTINUOUS_BARS`` aus — kein stiller 16/180-Trial-Budget-Verbrauch für ein
    Signal, das auf dieser Datenquelle strukturell ungültig ist. Fehlt der Key (Default) ⇒ die
    Strategie läuft unverändert (bit-identisch zum Pre-#698-Verhalten). Eine RTH-session-bewusste
    Variante B (``session_open_hour``) bliebe der Re-Aktivierungspfad, sollte künftig ein
    Session-Kalender für ein Symbol verfügbar werden — bis dahin ist die Deaktivierung die
    ehrlichere Wahl gegenüber einer strukturell ungültigen Messung."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "strategies.json"
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return frozenset()
    out = set()
    for entry in data.get("strategies", []) or []:
        if entry.get("invalid_on_continuous_bars") is True and entry.get("strategy_class"):
            out.add(entry["strategy_class"])
    return frozenset(out)


def load_symbol_strategy_denylist(base_cfg: Path | None = None) -> dict[tuple[str, str], str]:
    """Issue #669 — deklarative (Strategie, Symbol)-Deaktivierungsliste aus
    ``symbol_strategy_denylist.json`` (Zero-Hardcoding): ``{(strategy, symbol): reason}``.

    Ein Paar in dieser Liste wird in ``sweep.enumerate_tunable_pairs`` VOR dem Sweep übersprungen
    (Log-Zeile mit ``reason``), statt 16 strukturell nutzlose Trials zu verbrennen. Fehlt die Datei
    oder ist ``pairs`` leer ⇒ ``{}`` (bit-identisch zum Pre-#669-Verhalten — KEIN Paar wird ohne
    einen dokumentierten Diagnose-Befund deaktiviert)."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "symbol_strategy_denylist.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], str] = {}
    for entry in data.get("pairs", []) or []:
        strat = entry.get("strategy")
        sym = entry.get("symbol")
        if strat and sym:
            out[(strat, sym)] = entry.get("reason", "DECLARED_NON_VIABLE")
    return out
