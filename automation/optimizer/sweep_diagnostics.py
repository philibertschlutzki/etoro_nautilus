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

# Issue #669/#769 — die moeglichen bindenden Ursachen. 'none' ⇒ kein Kollaps (mind. 1 eligible
# Trial). Issue #769 — 'signal_frequency' wurde in 'signal_absent' (parameterunabhaengig, echte
# Datengeometrie) und 'signal_sparse' (parameterabhaengig, tunebar) AUFGESPALTEN: die alte Kategorie
# warf beide Faelle zusammen (median_is_trades==0 UND median_is_trades==9 galten identisch als
# 'signal_frequency'), obwohl sie entgegengesetzte Handlungsempfehlungen verlangen (AGENTS.md
# Pitfall #229).
_BINDING_CAUSES = frozenset(
    {"signal_absent", "signal_sparse", "hold_duration", "signal_quality", "none", "no_data"}
)


def diagnose_trade_frequency(trials: list[dict], *, oos_min_trades: int) -> dict:
    """Bestimmt die BINDENDE Ursache eines 0-evaluable/0-eligible-Kollapses für ein
    (Symbol, Strategie)-Paar aus den per-Trial-Kennzahlen (``oos_evaluated``, ``oos_eligible``,
    ``oos_total_trades``, ``is_total_trades``, ``hit_trade_cap`` — bereits als Trial-User-Attrs
    gestempelt, run_optimization.make_symbol_objective).

    Trennt (Akzeptanzkriterium #669/#769):
      * ``'signal_absent'`` — 0 evaluable UND die IS-Aktivität ist über ALLE abgeschlossenen Trials
        NULL (``median_is_trades == 0`` UND ``max(is_total_trades) == 0``) ⇒ PARAMETERUNABHÄNGIG:
        die Strategie feuert im gesamten Suchraum nie (echte Datengeometrie-/Indikator-Degeneration).
        Ein constrained TPE kann das nicht lösen — Bounds-Kalibrierung ist hier ein No-Op.
      * ``'signal_sparse'`` — 0 evaluable UND ``0 < median_is_trades < oos_min_trades`` ODER
        ``max(is_total_trades) > 0`` (die Strategie feuert bei MANCHEN Parameterkombinationen,
        median bleibt aber unter der Schwelle) ⇒ PARAMETERABHÄNGIG/tunebar (Frequenz-Parameter wie
        ``cooldown_bars``/Signal-Schwellen) — ein constrained TPE ist genau dafür da, diese Richtung
        zu finden (Root-Cause #769: der alte Guard urteilte hier auf NULL modellierten Trials).
      * ``'hold_duration'`` — 0 evaluable, IS-Aktivität AUSREICHEND (``median_is_trades >=
        oos_min_trades``), aber die Mehrheit der Trials trifft die Haltedauer-/Trade-Cap-Grenze
        (``hit_trade_cap``) ⇒ Signale entstehen, überleben aber nicht bis zur Realisierung im
        OOS-Fenster (Haltedauer-Obergrenze zu lang/kurz falsch kalibriert) — ebenfalls tunebar.
      * ``'signal_quality'`` — ALLE Trials wurden evaluiert (echte OOS-Backtests, genug Trades),
        aber KEINER war je eligible ⇒ ein Suchraum-/Bounds-Problem ist NICHT die Ursache; die
        Strategie ist auf diesem Symbol/Tier strukturell nicht kanten-fähig (oder das Gate ist zu
        streng) — Bounds-Kalibrierung würde hier NICHTS beheben.
      * ``'none'`` — mindestens ein Trial war eligible (kein Kollaps).

    Rückgabe: ``{'n_trials', 'n_evaluable', 'n_eligible', 'median_oos_trades', 'median_is_trades',
    'max_is_trades', 'frac_hit_trade_cap', 'binding_cause'}``. Rein, deterministisch, kein I/O."""
    n = len(trials)
    if n == 0:
        return {
            "n_trials": 0, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": None, "median_is_trades": None, "max_is_trades": None,
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
        max_is_trades = max(is_trade_counts) if is_trade_counts else 0
        if median_is_trades < oos_min_trades:
            # Issue #769 — 'signal_absent' NUR, wenn die IS-Aktivität ÜBER JEDEN Trial null ist
            # (echte Datengeometrie); jede Restaktivität (max > 0) ist ein Beleg, dass MANCHE
            # Parameterkombinationen sehr wohl feuern ⇒ 'signal_sparse' (parameterabhängig).
            if median_is_trades == 0 and max_is_trades == 0:
                binding_cause = "signal_absent"
            else:
                binding_cause = "signal_sparse"
        elif frac_hit_cap > 0.5:
            binding_cause = "hold_duration"
        else:
            binding_cause = "signal_sparse"
        return {
            "n_trials": n, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": median_oos_trades, "median_is_trades": median_is_trades,
            "max_is_trades": max_is_trades,
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


# Issue #761 — die frequenz-treibenden Parameter, deren Bounds bei einem ``'signal_frequency'``/
# ``'hold_duration'``-Kollaps ein plausibler Hebel sind (Cooldown/Haltedauer-Obergrenze und die
# Signal-Trägheits-Fenster der Trend-/Regime-Indikatoren). Nicht jede Strategie sampelt jeden
# dieser Parameter — ``propose_bounds_widening`` überspringt fehlende Keys stillschweigend.
_FREQUENCY_DRIVING_PARAMS = (
    "cooldown_bars", "max_bars_in_trade", "ema_period", "adx_period", "keltner_period",
)


def _widen_bounds_toward(direction_by_param: dict[str, str],
                         current_bounds: dict[str, tuple[float, float]], *,
                         widen_fraction: float) -> dict[str, list[float]]:
    """Issue #761/#763 — gemeinsame Weitungs-Arithmetik: ``direction_by_param[param] == "low"``
    senkt die Untergrenze um ``widen_fraction`` der aktuellen Spannweite ab (Obergrenze bleibt),
    ``"high"`` hebt die Obergrenze an (Untergrenze bleibt). Parameter ohne bekannte Bounds werden
    übersprungen (defensiv)."""
    proposals: dict[str, list[float]] = {}
    for param, direction in direction_by_param.items():
        if param not in current_bounds:
            continue
        lo, hi = current_bounds[param]
        span = (hi - lo) or 1.0
        if direction == "low":
            proposals[param] = [round(lo - widen_fraction * span, 6), hi]
        else:
            proposals[param] = [lo, round(hi + widen_fraction * span, 6)]
    return proposals


def propose_bounds_from_boundary_hits(
    directions: dict[str, str], strategy: str, *,
    current_bounds: dict[str, tuple[float, float]] | None = None,
    widen_fraction: float = 0.3,
) -> dict[str, list[float]]:
    """Issue #763 — schliesst die Randlösungs-Diagnose (``run_optimization._boundary_hit_
    directions``) zu einem KONKRETEN Bounds-Vorschlag, analog ``propose_bounds_widening``, aber
    OHNE Korrelationsschätzung: klebt der Gewinner-Parameter an der UNTEREN Grenze
    (``directions[param] == "low"``), wird die Untergrenze um ``widen_fraction`` (Default 30 %) der
    aktuellen Spannweite abgesenkt; bei ``"high"`` wird die Obergrenze angehoben. Root-Cause #763:
    ein Winner, der zur Hälfte an Suchraumgrenzen klebt, könnte dort ein echtes Optimum ODER ein
    Suchraum-Artefakt gefunden haben — nur ein Re-Run mit ausgeweiteten Bounds unterscheidet die
    beiden Fälle; dieser Vorschlag ist die Voraussetzung dafür (schreibt in den #761-Cache).

    ``current_bounds`` Default: ``bounds.extract_numeric_bounds(strategy)``. Rückgabe:
    ``{param: [new_low, new_high]}`` — NUR für Parameter aus ``directions``, deren Bounds bekannt
    sind. Rein, deterministisch, kein I/O (ausser dem optionalen ``extract_numeric_bounds``-
    Lookup)."""
    if current_bounds is None:
        try:
            from automation.optimizer.bounds import extract_numeric_bounds
            current_bounds = extract_numeric_bounds(strategy)
        except Exception:
            current_bounds = {}
    return _widen_bounds_toward(directions, current_bounds, widen_fraction=widen_fraction)


def propose_bounds_widening(
    trial_rows: list[dict], strategy: str, *,
    current_bounds: dict[str, tuple[float, float]] | None = None,
    widen_fraction: float = 0.3, min_samples: int = 8, correlation_threshold: float = 0.3,
) -> dict[str, list[float]]:
    """Issue #761 — schliesst die #669-Diagnose zu einem KONKRETEN Bounds-Vorschlag: für jeden
    frequenz-treibenden Parameter (``_FREQUENCY_DRIVING_PARAMS``), den diese Strategie tatsächlich
    sampelt, wird die Spearman-Rangkorrelation zwischen dem gesampelten Parameterwert und
    ``is_total_trades`` (derselben Aktivitäts-Grösse, die ``diagnose_trade_frequency`` schon für die
    ``'signal_frequency'``-Klassifikation nutzt) über die Trial-Kohorte bestimmt.

    Eine hinreichend starke Korrelation (``|ρ| > correlation_threshold``, Default 0.3, über
    mindestens ``min_samples`` Trials mit definiertem Parameter+Aktivität) zeigt an, IN WELCHE
    RICHTUNG der Suchraum verengt ist: eine NEGATIVE Korrelation (grösserer Parameterwert ⇒ weniger
    Trades, z. B. längerer Cooldown) schlägt vor, die UNTERGRENZE abzusenken (Suchraum nach unten
    weiten); eine POSITIVE Korrelation schlägt vor, die OBERGRENZE anzuheben. Die Weitung selbst ist
    ``widen_fraction`` (Default 30 %) der aktuellen Spannweite — konservativ, kein Sprung ins
    Unbekannte.

    ``trial_rows``: Liste von ``{'params': {...}, 'is_total_trades': int}`` (aus den Trial-User-
    Attrs des STRUCTURAL_ALL_UNEVALUABLE-Kollaps-Cohorts, ``run_optimization.floor_plateau_
    callback``). ``current_bounds`` Default: ``bounds.extract_numeric_bounds(strategy)``.

    Rückgabe: ``{param: [new_low, new_high]}`` — NUR für Parameter mit einer hinreichend starken,
    hinreichend gestützten Korrelation. Leeres Dict, wenn kein Parameter qualifiziert (kein
    Bounds-Problem erkennbar, oder die Strategie sampelt keinen der Kandidaten-Parameter). Rein,
    deterministisch, kein I/O (ausser dem optionalen ``extract_numeric_bounds``-Lookup)."""
    if current_bounds is None:
        try:
            from automation.optimizer.bounds import extract_numeric_bounds
            current_bounds = extract_numeric_bounds(strategy)
        except Exception:
            current_bounds = {}

    from automation.optimizer.reward import _spearman_rank_correlation

    direction_by_param: dict[str, str] = {}
    for param in _FREQUENCY_DRIVING_PARAMS:
        if param not in current_bounds:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for row in trial_rows:
            params = row.get("params") or {}
            trades = row.get("is_total_trades")
            if param not in params or trades is None:
                continue
            try:
                xs.append(float(params[param]))
                ys.append(float(trades))
            except (TypeError, ValueError):
                continue
        if len(xs) < min_samples:
            continue
        rho = _spearman_rank_correlation(xs, ys)
        if rho is None or abs(rho) < correlation_threshold:
            continue
        direction_by_param[param] = "low" if rho < 0 else "high"
    return _widen_bounds_toward(direction_by_param, current_bounds, widen_fraction=widen_fraction)


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
                               previously_recommended_override: bool = False,
                               proposed_bounds: dict | None = None) -> dict:
    """Issue #681 — schliesst die #669-Diagnose zu einer KONKRETEN Aktions-Empfehlung: die
    Diagnose (``diagnose_trade_frequency``) feuert bereits (STRUCTURAL_ALL_UNEVALUABLE /
    ZERO_ELIGIBLE_PLATEAU), schreibt aber nicht zurück — dieselben strukturell toten Paare werden
    bei JEDEM Lauf neu enumeriert (Root-Cause #681).

    Fallunterscheidung nach ``binding_cause`` (#669/#769):
      * ``'signal_quality'`` (ALLE Trials evaluiert, 0 eligible) — Bounds-Kalibrierung hilft NICHT
        (kein Frequenz-, ein Qualitätsproblem) ⇒ ``'denylist'``.
      * ``'signal_absent'`` (0 evaluable, IS-Aktivität über JEDEN Trial null) — PARAMETERUNABHÄNGIG:
        kein Override kann eine Strategie zum Feuern bringen, die im gesamten Suchraum nie feuert
        ⇒ ``'denylist'`` (Bounds-Kalibrierung wäre hier ein No-Op, #769).
      * ``'signal_sparse'``/``'hold_duration'`` (0 evaluable, PARAMETERABHÄNGIG) UND die Strategie
        ist für Symbol-Bounds-Overrides verdrahtet (``WIRED_OVERRIDE_STRATEGIES``) UND es existiert
        noch KEIN Override für dieses Paar ⇒ ``'search_space_override'`` (Bounds-Kalibrierung
        probieren, BEVOR das Paar aufgegeben wird). Existiert bereits ein Override (Bounds-
        Kalibrierung wurde schon versucht) UND das Paar ist TROTZDEM tot ⇒ Eskalation auf
        ``'denylist'``.
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

    Issue #761 — ``proposed_bounds`` (aus ``propose_bounds_widening``, vom Aufrufer berechnet) wird
    NUR bei ``action == 'search_space_override'`` in die Empfehlung übernommen — ein konkreter,
    verengter/geweiteter Bereich statt nur der Empfehlung "probiere einen Override". ``sweep.py``
    wendet ihn im NÄCHSTEN Lauf automatisch an (``SEARCH_SPACE_AUTO_OVERRIDE``-Telemetrie), bis ein
    Operator den kuratierten Override in ``search_space_overrides.json`` einträgt.

    Rein, deterministisch, kein I/O. Rückgabe: ``{'strategy', 'symbol', 'action', 'binding_cause',
    'median_oos_trades', 'median_is_trades', 'proposed_bounds'}``."""
    cause = diagnosis.get("binding_cause")
    if cause in ("none", "no_data", None):
        action = "none"
    elif cause in ("signal_quality", "signal_absent"):
        # Issue #769 — 'signal_absent' ist PARAMETERUNABHÄNGIG (echte Datengeometrie über den
        # gesamten Suchraum) — ein Bounds-Override kann eine Strategie nicht zum Feuern bringen,
        # die nirgends feuert, daher wie 'signal_quality' direkt 'denylist'.
        action = "denylist"
    elif cause in ("signal_sparse", "hold_duration"):
        if strategy in WIRED_OVERRIDE_STRATEGIES and not has_existing_override:
            action = "denylist" if previously_recommended_override else "search_space_override"
        else:
            action = "denylist"
    else:
        action = "denylist"
    result = {
        "strategy": strategy, "symbol": symbol, "action": action, "binding_cause": cause,
        "median_oos_trades": diagnosis.get("median_oos_trades"),
        "median_is_trades": diagnosis.get("median_is_trades"),
    }
    if action == "search_space_override" and proposed_bounds:
        result["proposed_bounds"] = proposed_bounds
    return result


_DEFAULT_EXPIRES_AFTER_RUNS = 10


def _diagnosed_pairs_cache_path(work_dir: Path | None = None) -> Path:
    if work_dir is None:
        from automation.optimizer.manifest import WORK
        work_dir = WORK
    # Issue #761 — unter data/optimizer/diagnostics/ (NICHT direkt unter WORK): der Cache ist
    # orthogonal zur reward_semantics_version-Purge-Grenze (purge_stale_studies.py leert
    # WORK/sweep/*.db + WORK/<study_name>/trial_*/, beides study-namens-adressiert) und soll auch
    # einen manuellen "WORK-Verzeichnis leeren"-Reflex überleben — die Diagnose selbst bleibt
    # gültig, unabhängig davon, ob Studies purged wurden (#733/#701-Anschluss).
    return Path(work_dir) / "diagnostics" / "diagnosed_pairs_cache.json"


def load_diagnosed_pairs_cache(work_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Issue #681/#761 — der AUTOMATISCH gepflegte Diagnose-Cache (``data/optimizer/diagnostics/
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


def _save_diagnosed_pairs_cache(cache: dict[tuple[str, str], dict], *,
                                work_dir: Path | None = None) -> Path:
    path = _diagnosed_pairs_cache_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pairs": list(cache.values())}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def record_diagnosed_pair(recommendation: dict, *, work_dir: Path | None = None) -> Path:
    """Issue #681/#761 — schreibt/aktualisiert den (Symbol, Strategie)-Diagnose-Eintrag im
    automatisch gepflegten Cache (siehe ``load_diagnosed_pairs_cache``-Docstring). Ein No-Op-Eintrag
    (``action == 'none'``) wird NICHT gespeichert (kein Kollaps ⇒ nichts zu cachen). Idempotent:
    ein erneuter Diagnose-Lauf für dasselbe Paar überschreibt den vorherigen Eintrag UND setzt
    ``runs_since_recorded`` auf 0 zurück (frischer Befund, neue Ablauf-Frist beginnt).

    Issue #761 — jeder gespeicherte Eintrag trägt ``expires_after_runs`` (aus der Empfehlung, sonst
    Default 10) und ``runs_since_recorded=0``. ``enumerate_tunable_pairs`` (sweep.py) lässt ein
    Paar, dessen ``runs_since_recorded >= expires_after_runs`` erreicht hat, genau EINMAL wieder zu
    (Re-Test) — ein einmaliger Befund zementiert das Paar sonst dauerhaft, auch wenn Kohorte-A/B-
    Fixes (#753/#756/#757) die Lage grundlegend ändern."""
    if recommendation.get("action") == "none":
        return _diagnosed_pairs_cache_path(work_dir)
    cache = load_diagnosed_pairs_cache(work_dir)
    entry = dict(recommendation)
    entry.setdefault("expires_after_runs", _DEFAULT_EXPIRES_AFTER_RUNS)
    entry["runs_since_recorded"] = 0
    cache[(recommendation["strategy"], recommendation["symbol"])] = entry
    return _save_diagnosed_pairs_cache(cache, work_dir=work_dir)


def age_diagnosed_pairs_cache(*, work_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Issue #761 — EINMAL je Sweep-Lauf aufgerufen (``sweep.run_per_symbol_sweep``, VOR der
    Paar-Enumeration): erhöht ``runs_since_recorded`` jedes Cache-Eintrags um 1. Ein Paar, das DIESEN
    Lauf tatsächlich neu diagnostiziert wird, überschreibt seinen Eintrag danach ohnehin via
    ``record_diagnosed_pair`` (``runs_since_recorded`` zurück auf 0) — das Altern hier betrifft nur
    Paare, die WEGEN des Cache-Skips gar nicht erst re-diagnostiziert wurden. Kein Cache ⇒ No-Op.
    Rückgabe: der gealterte Cache (für ``enumerate_tunable_pairs``, ohne ihn ein zweites Mal von der
    Platte zu laden)."""
    cache = load_diagnosed_pairs_cache(work_dir)
    if not cache:
        return cache
    for entry in cache.values():
        entry["runs_since_recorded"] = int(entry.get("runs_since_recorded", 0)) + 1
    _save_diagnosed_pairs_cache(cache, work_dir=work_dir)
    return cache


def is_diagnosed_pair_expired(entry: dict) -> bool:
    """Issue #761 — ``True``, wenn ein 'denylist'-Cache-Eintrag seine Ablauf-Frist erreicht hat
    (``runs_since_recorded >= expires_after_runs``) und daher DIESEN Lauf wieder regulär enumeriert
    (re-getestet) werden soll, statt automatisch übersprungen zu werden."""
    expires_after = int(entry.get("expires_after_runs", _DEFAULT_EXPIRES_AFTER_RUNS))
    runs_since = int(entry.get("runs_since_recorded", 0))
    return runs_since >= expires_after


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
