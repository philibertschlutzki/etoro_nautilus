import json
import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics


def _softplus(z: float) -> float:
    """``ln(1 + e^z)`` — überall streng monoton, C∞, numerisch stabil (kein Overflow für großes z).

    Issue #560 — glatte, streng monotone Abbildung des Rest-Gaps zum Return-Gate im
    return-verankerten Failure-Reward. Für ``z ≫ 0`` ≈ ``z``, für ``z ≪ 0`` ≈ ``e^z`` → 0.
    """
    if z > 30.0:
        return z
    if z < -30.0:
        return math.exp(z)
    return math.log1p(math.exp(z))


def _apply_soft_scale(value: float, scale: float | None) -> float:
    """Wendelt die weiche asinh-Kompression an, wenn eine Scale vorliegt."""
    if scale is not None and scale > 0.0:
        c = float(scale)
        return c * math.asinh(float(value) / c)
    return float(value)


_oos_min_trades_cache: int | None = None
_tournament_cfg_cache: dict | None = None


def _read_tournament_cfg() -> dict:
    global _tournament_cfg_cache
    if _tournament_cfg_cache is not None:
        return _tournament_cfg_cache

    try:
        from automation.optimizer.trial_config import config_dir

        cfg_path = config_dir() / "tournament.json"
        if not cfg_path.exists():
            _tournament_cfg_cache = {}
            return {}
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            _tournament_cfg_cache = data
            return data
    except OSError:
        _tournament_cfg_cache = {}
        return {}
    except ValueError:
        _tournament_cfg_cache = {}
        return {}


def _read_oos_min_trades() -> int:
    global _oos_min_trades_cache
    if _oos_min_trades_cache is not None:
        return _oos_min_trades_cache

    tournament_cfg = _read_tournament_cfg()
    _oos_min_trades_cache = tournament_cfg.get("oos_min_trades", 3)
    return _oos_min_trades_cache


def _cfg_value(weights: dict, tournament_cfg: dict | None, key: str, default=None):
    """Issue #452 — Constraint-Schwelle aufloesen: erst aus ``weights`` (optimizer.json/DI),
    dann aus der ``tournament.json``-Config, sonst ``default``. So bleibt die Distanz-Penalty
    rein deklarativ (Zero-Hardcoding, HI-6) und an dieselben Gates gebunden, die ueber
    ``oos_eligible`` entscheiden."""
    if key in weights:
        return weights[key]
    if tournament_cfg and key in tournament_cfg:
        return tournament_cfg[key]
    return default


def _shortfall_distance(
    actual: float, target: float | None, scale: float | None = None
) -> float:
    """Lineare, auf den Target (oder Scale) normierte Unterschreitungs-Distanz ∈ [0, ∞).
    0.0, wenn ``actual >= target`` (oder kein/nicht-positiver Target).
    Issue #505: Lineare statt quadratische Distanz zur Vermeidung von Term-Dominanz.
    Issue #467: `scale` erlaubt die Entkopplung der Penalty-Skalierung vom Gate-Threshold.
    """
    if target is None or target <= 0.0:
        return 0.0
    denom = float(scale) if scale is not None else float(target)
    if denom <= 0.0:
        raise ValueError("scale/target must be strictly positive")
    return max(0.0, float(target) - float(actual)) / denom


def _excess_distance(actual: float, cap: float | None) -> float:
    """Lineare, auf den Cap normierte Ueberschreitungs-Distanz (z. B. Drawdown > max).
    0.0, wenn ``actual <= cap`` (oder kein/nicht-positiver Cap)."""
    if cap is None or cap <= 0.0:
        return 0.0
    return max(0.0, float(actual) - float(cap)) / float(cap)


def _any_condition_distance(
    m: "TournamentMetrics", weights: dict, tournament_cfg: dict | None
) -> float:
    """Distanz fuer die ``eligible_requires_any``-Klausel (Sortino ODER Profit-Factor).
    Erfuellt der bessere der beiden Quotienten sein Gate (ratio >= 1), ist die Distanz 0;
    sonst linear im Rest-Gap des BESTEN Kandidaten — eine knapp verfehlte ANY-Bedingung
    darf nicht doppelt so hart bestraft werden wie eine knapp verfehlte ALL-Bedingung.
    Issue #467: Strikte Parameter-Isolation. Kein Fallback auf IS-Metriken im OOS-Pfad.
    """
    ratios = []
    req_sortino = _cfg_value(weights, tournament_cfg, "oos_min_sortino")
    if req_sortino and req_sortino > 0.0 and m.oos_sortino is not None:
        ratios.append(max(0.0, m.oos_sortino) / float(req_sortino))

    req_profit_factor = _cfg_value(weights, tournament_cfg, "oos_min_profit_factor")
    if (
        req_profit_factor
        and req_profit_factor > 0.0
        and m.oos_profit_factor is not None
    ):
        ratios.append(max(0.0, m.oos_profit_factor) / float(req_profit_factor))

    if not ratios:
        return 0.0
    return max(0.0, 1.0 - min(1.0, max(ratios)))


def _constraint_distance_penalty(
    m: "TournamentMetrics",
    weights: dict,
    risk_dd_cap: float | None,
    tournament_cfg: dict | None,
) -> float:
    """Issue #452 — kontinuierliche Distanzstrafe fuer OOS-Constraint-Verletzungen.
    Issue #467 — OOS-Parameter-Isolation und Penalty Conditioning.
    Issue #505 — lineare (nicht quadratische) Distanzen gegen Term-Dominanz.
    Issue #534 — konsistente Normalisierung ausschliesslich ueber die AKTIVEN Dimensionen.

    Nur evaluiert-aber-nicht-eligible Trials nutzen diesen Pfad. Die Strafe ist rein metrisch
    (keine Gate-Flags als Reward), linear in der auf Target/Scale normierten Ziel-Distanz und
    config-gewichtet. Aggregiert wird als MITTELWERT der aktiven Distanzen
    (``sum(active_dists) / len(active_dists)``): eine inaktive (bereits erfuellte) Dimension traegt
    NULL Gewicht im Divisor und darf die effektive Strafe pro aktiver Dimension nicht mehr
    verzerren (Issue #534 — vorher fiktive Division durch die feste Gesamtzahl der Dimensionen).
    Damit erhaelt derselbe Return-Shortfall dieselbe Teilstrafe, unabhaengig davon, wie viele
    Nebengates zufaellig erfuellt sind. So unterscheidet TPE wieder 'knapp gescheitert' von
    'katastrophal gescheitert', ohne die Rang-Invariante aufzuweichen: failed Trials bleiben
    strikt unter dem Evaluable-Floor (siehe ``_constraint_failure_reward``)."""
    # Strikte Isolation (kein impliziter Fallback auf IS)
    req_trades = _cfg_value(weights, tournament_cfg, "oos_min_trades")
    req_return = _cfg_value(weights, tournament_cfg, "oos_min_total_return")
    req_expectancy = _cfg_value(weights, tournament_cfg, "oos_min_expectancy")
    req_win_rate = _cfg_value(weights, tournament_cfg, "oos_min_win_rate")

    if None in (req_trades, req_return, req_expectancy, req_win_rate):
        raise ValueError(
            "Missing strict OOS configuration parameters in tournament.json"
        )

    return_penalty_scale = _cfg_value(weights, tournament_cfg, "return_penalty_scale")
    # Issue #547 — Expectancy-Distanz analog zum Return (#467) vom Gate-Threshold entkoppeln.
    # ``_shortfall_distance`` normiert defaultmäßig auf ``target``; für die mikroskopische
    # Expectancy-Schwelle sprengte ein winziger absoluter Miss den Aktiv-Mittelwert (Distanz ≫ 1)
    # und maskierte alle anderen Signale. Mit ``expectancy_penalty_scale`` landet ein typischer
    # Miss im Bereich der übrigen Terme (≈ 0…1.5). Fehlt der Key ⇒ ``scale=None`` ⇒ Legacy-Pfad
    # (Normierung auf ``target``), bit-identisch (Zero-Hardcoding). Siehe AGENTS.md Pitfall #108.
    expectancy_penalty_scale = _cfg_value(
        weights, tournament_cfg, "expectancy_penalty_scale"
    )

    distances = [
        _shortfall_distance(float(m.oos_total_trades), req_trades),
        _shortfall_distance(m.oos_total_return, req_return, scale=return_penalty_scale),
        _shortfall_distance(
            m.oos_expectancy, req_expectancy, scale=expectancy_penalty_scale
        ),
        _shortfall_distance(m.oos_win_rate, req_win_rate),
        _excess_distance(m.oos_max_drawdown, risk_dd_cap),
        _any_condition_distance(m, weights, tournament_cfg),
    ]

    # Issue #547 (robuster Schutz) — Clip-Obergrenze pro Term: kein einzelner Distanz-Term darf
    # den Aktiv-Mittelwert je dominieren, unabhängig von Kalibrierfehlern der Ziel-Schwellen.
    # Fehlt ``distance_term_cap`` (oder <= 0) ⇒ kein Cap ⇒ bit-identisch zum Legacy-Verhalten.
    # Der Cap senkt Distanzen nur (Strafe ≤ ungecappt) ⇒ die Rang-Invariante (failed < Floor)
    # bleibt strikt erhalten.
    term_cap = _cfg_value(weights, tournament_cfg, "distance_term_cap")
    if term_cap is not None and float(term_cap) > 0.0:
        cap = float(term_cap)
        distances = [min(d, cap) for d in distances]

    active_dists = [d for d in distances if d > 0.0]
    if not active_dists:
        return 0.0
    # Issue #534 — Normalisierung strikt ueber die AKTIVEN Dimensionen (mittlere aktive Distanz),
    # NICHT ueber die feste Gesamtzahl len(distances)==6. Inaktive (erfuellte) Gates tragen null
    # Gewicht im Divisor ⇒ derselbe Shortfall ergibt dieselbe Teilstrafe, egal wie viele
    # Nebengates zufaellig erfuellt sind (kein Gradientenrauschen bei der TPE-Suche).
    mean_distance = sum(active_dists) / len(active_dists)
    return mean_distance * float(
        weights.get(
            "constraint_distance_penalty_weight", weights["unevaluable_shaping_span"]
        )
    )


def _constraint_failure_reward(
    m: "TournamentMetrics",
    weights: dict,
    risk_dd_cap: float | None,
    tournament_cfg: dict | None,
) -> float:
    """Issue #452 / #505 — Reward fuer evaluiert-aber-nicht-eligible OOS-Trials.

    Verankert am neuen Feasible-Floor (-sortino_clip_abs) und zieht die
    kontinuierliche Distanzstrafe ab. Damit gilt strikt: jeder Constraint-Failure < Evaluable-Floor
    (``+ epsilon``) ⇒ kein failed Trial kann je einen eligiblen ueberholen (Anti-Gate-Gaming).
    Der tanh-Band-Clamp (Falle 97) wurde zugunsten eines grossen Dynamikbereichs entfernt.
    """
    feasible_min = -float(weights["sortino_clip_abs"])
    failure_ceiling = feasible_min - float(weights["evaluable_floor_epsilon"])
    unevaluable_ceiling = float(weights["penalty_unevaluable_oos"]) + float(
        weights["unevaluable_shaping_span"]
    )

    # Issue #560 — Aggregations-Modus des Failure-Rewards (deklarativ, Zero-Hardcoding).
    #   'legacy_mean'     : Mittel der aktiven Distanz-Terme (Default, bit-identisch zum Status quo).
    #   'return_anchored' : an eine gut konditionierte, ökonomisch monotone Skalarzahl (oos_total_return)
    #                       verankert, statt an einem von Kosten-Sättigungen dominierten 6-Term-Mittel
    #                       (corr(reward,return|ineligible)≈0). softplus bildet den Rest-Gap zum
    #                       Return-Gate stufenlos und streng monoton ab ⇒ corr(reward,return) > 0
    #                       konstruktionsgemäß. Kein Term kann dominieren (es gibt nur einen).
    mode = _cfg_value(weights, tournament_cfg, "failure_reward_mode", "legacy_mean")

    if mode == "return_anchored":
        req_return = _cfg_value(weights, tournament_cfg, "oos_min_total_return")
        if req_return is None:
            raise ValueError(
                "failure_reward_mode='return_anchored' benötigt oos_min_total_return "
                "(tournament.json/weights)."
            )
        s = float(
            _cfg_value(weights, tournament_cfg, "failure_return_softplus_scale", 0.02)
        )
        if s <= 0.0:
            s = 0.02
        w = float(
            _cfg_value(weights, tournament_cfg, "failure_return_penalty_weight", 2.0)
        )
        # z = −(return − gate)/s: großer Rest-Gap (return ≪ gate) ⇒ großes z ⇒ große Strafe;
        # return → gate ⇒ z → 0 ⇒ Strafe → w·ln2. softplus > 0 ⇒ Failure-Reward stets < failure_ceiling
        # (Ordnungsinvariante: max(failure) < −sortino_clip_abs bleibt strikt).
        z = -(float(m.oos_total_return) - float(req_return)) / s
        penalty = _softplus(z) * w
        raw_failure_reward = failure_ceiling - penalty
        return max(unevaluable_ceiling, raw_failure_reward)

    # 'legacy_mean' (Default) — Mittel der aktiven Distanzen (bit-identisch, #452/#505/#534).
    penalty = _constraint_distance_penalty(m, weights, risk_dd_cap, tournament_cfg)
    raw_failure_reward = failure_ceiling - penalty
    return max(unevaluable_ceiling, raw_failure_reward)


def _gate_proximity(m: "TournamentMetrics", weights: dict) -> float:
    """Issue #407 — kontinuierlicher Eligibility-Gradient fuer unevaluable Trials.

    Selbst wenn ein Symbol im OOS nie evaluiert wird (oos_total_trades==0, Pitfall #75), liefert
    die IS-Performance ein normiertes Naehe-Signal zum Gate: ein Symbol mit hohem IS-total_return /
    IS-win_rate ist 'fast eligible' und soll hoeher bewertet werden als eines, das nie performt.

    Jede Komponente wird gegen ihren Target (``shaping_return_target`` / ``shaping_winrate_target``,
    Zero-Hardcoding) auf ``[0, 1]`` normiert und geclippt; negative IS-Rendite traegt 0 bei. Das
    Ergebnis ist der Mittelwert der aktiven Komponenten ⇒ stets ``∈ [0, 1]``. Fehlen beide Targets
    ⇒ ``0.0`` (Legacy-Verhalten). Der Wert ist rein performance-basiert (kein Gate-Flag) ⇒ kein
    Gate-Gaming; durch die ``[0, 1]``-Bindung bleibt das Shaping hart durch ``unevaluable_shaping_span``
    gedeckelt (Anti-Gate-Gaming-Invariante: unevaluable < evaluable-Floor)."""
    components = []
    return_target = weights.get("shaping_return_target")
    if return_target and float(return_target) > 0.0:
        components.append(
            min(1.0, max(0.0, m.is_best_total_return) / float(return_target))
        )
    winrate_target = weights.get("shaping_winrate_target")
    if winrate_target and float(winrate_target) > 0.0:
        components.append(
            min(1.0, max(0.0, m.is_best_win_rate) / float(winrate_target))
        )
    if not components:
        return 0.0

    import math

    val = sum(components) / len(components)
    if math.isnan(val):
        val = 0.0
    return max(0.0, min(1.0, val))


def compute_reward(
    m: "TournamentMetrics",
    universe_size: int,
    weights: dict | None = None,
    risk_dd_cap: float | None = None,
    *,
    sampled: dict | None = None,
    global_params: dict | None = None,
    strategy: str | None = None,
    tournament_cfg: dict | None = None
) -> float | tuple:
    """weights=None  ⇒ aus optimizer.json (penalty_overfit_weight, penalty_dd_weight,
                     bonus_coverage_weight, penalty_unevaluable_oos, sortino_clip_abs).
    risk_dd_cap=None ⇒ aus tournament.json (max_drawdown).
    Falls not m.oos_evaluated oder m.oos_sortino is None: Unevaluable-Pfad (Penalty + Shaping).
    ISSUE #401: Ist ein OOS-Sample evaluated ∧ eligible, aber oos_sortino is None
    (Zero-Loss / n < sortino_min_trades), UND weights['oos_sortino_fallback'] == 'total_return',
    wird statt des Penalty-Pfades der geclippte oos_total_return als evaluable Base genutzt
    (Flat-Reward-Landscape-Fix). Fehlt der Schluessel ⇒ unveraenderter Legacy-Penalty-Pfad.
    ISSUE #452: Ist ein OOS-Sample evaluated, aber NICHT eligible (durchs OOS-Gate gefallen),
    greift _constraint_failure_reward: eine kontinuierliche, quadratische, config-gewichtete
    Distanzstrafe (constraint_distance_penalty_weight) statt Flat-Floor-Clamping — knapp verfehlt
    > katastrophal verfehlt (TPE-Gradient), aber strikt unter dem Evaluable-Floor (kein Gate-
    Gaming). tournament_cfg (optional) liefert die OOS-Schwellen ohne erneutes Datei-IO.

    universe_size > 1 (und reward_mode != 'per_symbol') ⇒ Coverage-Pfad (bit-identisch, A4.3/HI-2):
      reward = base - overfit_gap*penalty_overfit_weight - dd_penalty
               + coverage*bonus_coverage_weight ; coverage = win_count / max(1, universe_size).

    universe_size == 1 ODER weights['reward_mode'] == 'per_symbol' ⇒ Per-Symbol-Pfad (A4.3):
      KEIN Coverage-Term, dafür Shrinkage-Strafe param_pen Richtung global:
      param_pen = lambda_reg * normalized_param_distance(sampled, global_params,
                               bounds.extract_numeric_bounds(strategy))
                  falls (sampled and global_params and strategy), sonst 0.0.
      reward = base - overfit_gap*penalty_overfit_weight - dd_penalty - param_pen.

    base = clip(oos_sortino, ±sortino_clip_abs); overfit_gap = max(0, is_sortino_median - base);
    dd_penalty = penalty_dd_weight * (oos_max_drawdown / risk_dd_cap)^2.
    floor = penalty_unevaluable_oos + unevaluable_shaping_span + evaluable_floor_epsilon;
    return max(reward, floor)  # Ordnungsinvariante: evaluable >= floor > unevaluable.
    """

    loaded_tournament_cfg = tournament_cfg

    if weights is None:
        from automation.optimizer.trial_config import config_dir

        cfg_path = config_dir() / "optimizer.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            weights = json.load(f)
        loaded_tournament_cfg = _read_tournament_cfg()

    if risk_dd_cap is None:
        if loaded_tournament_cfg is None:
            loaded_tournament_cfg = _read_tournament_cfg()
        risk_dd_cap = loaded_tournament_cfg["max_drawdown"]

    reward_mode_config = weights.get("reward_mode", "auto") if weights else "auto"
    if reward_mode_config == "pareto":
        return (
            float(m.oos_total_return),
            float(m.oos_expectancy),
            float(m.oos_win_rate),
            float(m.oos_sortino if m.oos_sortino is not None else 0.0),
            float(m.oos_max_drawdown),
            float(m.oos_total_trades),
        )

    penalty_unevaluable_oos = weights["penalty_unevaluable_oos"]

    # ISSUE #401 — Zero-Loss/Sub-Threshold-Sortino-Fallback (Flat-Reward-Landscape-Fix).
    # Ein OOS-Sample, das gehandelt UND jedes (eingefrorene) OOS-Risiko-Gate bestanden hat
    # (oos_evaluated ∧ oos_eligible), dessen Sortino aber mathematisch undefiniert ist
    # (losses_count == 0 oder n < sortino_min_trades ⇒ oos_sortino is None), darf NICHT auf
    # den flachen Unevaluable-Floor (−9.75) kollabieren — das nivelliert die TPE-Reward-
    # Landschaft (Zero-Gradient). Deklarativ gegated ueber optimizer.json['oos_sortino_fallback']
    # (Zero-Hardcoding); fehlt der Schluessel ⇒ unveraenderter Legacy-Penalty-Pfad. Der Reward-
    # WERT bleibt rein performance-basiert (geclippter OOS-total_return), nie das Gate-Flag —
    # damit kein Gate-Gaming (Falle 2); Micro-Sizing-/Risiko-Gates (Pitfall #58) bleiben ueber
    # oos_eligible wirksam.
    base_source = m.oos_sortino
    if (
        m.oos_evaluated
        and m.oos_eligible
        and m.oos_sortino is None
        and weights.get("oos_sortino_fallback") == "total_return"
    ):
        base_source = m.oos_total_return

    # Issue #452 — evaluiert, aber durchs OOS-Eligibility-Gate gefallen: KEIN Flat-Floor-Clamp,
    # sondern eine kontinuierliche, config-gewichtete Distanzstrafe (near-miss > katastrophal),
    # die strikt unter dem Evaluable-Floor bleibt. Steht VOR dem Unevaluable-Pfad, weil ein
    # evaluiertes-aber-ineligibles Sample (oos_evaluated=True) sonst je nach Sortino-Definiertheit
    # mal in den Evaluable-, mal in den Unevaluable-Pfad fiele (inkonsistenter Gradient).
    if m.oos_evaluated and not m.oos_eligible:
        # Issue #467/#468 (strikte OOS-Isolation): _constraint_distance_penalty verlangt die
        # OOS-Schwellen (oos_min_*) und wirft fail-loud, wenn sie fehlen. Wird compute_reward mit
        # explizitem ``weights``, aber ohne ``tournament_cfg`` aufgerufen (DI-/confirm-Pfade,
        # universe_size==1), bleibt loaded_tournament_cfg sonst None ⇒ die kanonischen oos_min_*
        # aus tournament.json wären unsichtbar und der Lauf crashte statt zu bewerten. Hier die
        # Single-Source-of-Truth-Config nachladen (idempotent, gecached), bevor die Strafe greift.
        if loaded_tournament_cfg is None:
            loaded_tournament_cfg = _read_tournament_cfg()
        return _constraint_failure_reward(
            m, weights, risk_dd_cap, loaded_tournament_cfg
        )

    if not m.oos_evaluated or base_source is None:
        # Avoid IO if possible:
        oos_min_trades = None
        if weights is not None and "oos_min_trades" in weights:
            val = weights["oos_min_trades"]
            if val is not None:
                oos_min_trades = int(val)

        if oos_min_trades is None:
            oos_min_trades = _read_oos_min_trades()

        # OOS trade progress (existing signal).
        trade_progress = min(1.0, m.oos_total_trades / max(1, oos_min_trades))

        # ISSUE-OPT-375: while no symbol is IS-eligible, oos_total_trades is flat 0, so the
        # penalty is a flat plateau and TPE has no gradient toward the eligibility threshold.
        # Couple the shaping to IS activity (sum of IS trades across the universe) as well, so
        # "almost eligible" becomes distinguishable from "never eligible". shaping_trade_target
        # lives in optimizer.json (zero-hardcoding); if absent, behaviour is the legacy OOS-only path.
        progress = trade_progress
        # Issue #406 (Pitfall #75, Defekt 2): shaping_trade_target=50 ist universe-skaliert
        # (~70 Symbole). Im Per-Symbol-Pfad (universe_size==1 ODER reward_mode=='per_symbol') ist
        # is_total_trades die Fold-Summe EINES Symbols (≫ 50) ⇒ activity saettigt sofort auf 1.0
        # ⇒ Zero-Gradient genau im Bedarfsfall. Dort den dedizierten, groesseren
        # per_symbol_shaping_trade_target nutzen (Fallback auf shaping_trade_target, wenn absent).
        reward_mode_uneval = weights.get("reward_mode", "auto")
        if universe_size == 1 or reward_mode_uneval == "per_symbol":
            shaping_trade_target = weights.get(
                "per_symbol_shaping_trade_target"
            ) or weights.get("shaping_trade_target")
        else:
            shaping_trade_target = weights.get("shaping_trade_target")
        # Issue #488 — Reward Shaping Monotonicity Guard
        proximity = _gate_proximity(m, weights)
        has_proximity_targets = (
            "shaping_return_target" in weights or "shaping_winrate_target" in weights
        )

        if shaping_trade_target:
            activity = min(1.0, m.is_total_trades / max(1, int(shaping_trade_target)))
            if has_proximity_targets:
                activity = min(activity, proximity)
            progress = max(progress, activity)

        progress = max(progress, proximity)

        # Floor invariant: progress ∈ [0, 1] ⇒ shaping ≤ unevaluable_shaping_span, hence every
        # unevaluable trial stays ≤ penalty + span, strictly below the evaluable floor below.
        shaping = weights["unevaluable_shaping_span"] * progress
        return penalty_unevaluable_oos + shaping

    sortino_clip_abs = weights["sortino_clip_abs"]
    # Issue #559 — WEICHE Sättigung statt Hard-Clip. Der Hard-Clip ist eine stückweise konstante
    # Funktion mit Gradient 0 oberhalb der Klemmgrenze — genau dort, wo die Winner leben (jeder
    # evaluable Trial mit minimalem Edge klemmte annualisiert auf +5.0 ⇒ TPE erhält ein flaches
    # Plateau und kann den robustesten Kandidaten nicht selektieren). asinh ist linear nahe 0 (kein
    # Informationsverlust unterhalb der Skala), logarithmisch in den Extremen und ÜBERALL streng
    # monoton: base = c·asinh(sortino/c) trägt oberhalb der bisherigen Grenze weiterhin Ordnung
    # (50 > 10 > 5 bleibt erhalten). Fehlt sortino_soft_scale (oder <= 0) ⇒ Legacy-Hard-Clip
    # (bit-identisch, Migrations-sicher). Entschärft strukturell auch die #563-Clip-Sättigung.
    soft_scale = None
    soft_scale_val = weights.get("sortino_soft_scale")
    if soft_scale_val is not None and float(soft_scale_val) > 0.0:
        soft_scale = float(soft_scale_val)

    if soft_scale is not None:
        base = _apply_soft_scale(float(base_source), soft_scale)
    else:
        base = max(-sortino_clip_abs, min(sortino_clip_abs, base_source))

    penalty_overfit_weight = weights["penalty_overfit_weight"]
    penalty_dd_weight = weights["penalty_dd_weight"]
    bonus_coverage_weight = weights["bonus_coverage_weight"]
    penalty_turnover_weight = weights.get("penalty_turnover_weight", 0.0)
    penalty_relative_cap = weights.get("penalty_relative_cap")

    # Issue #565 / #575 — Divergenz-Strafe IS↔OOS. Skalenparität mit asinh erfordert
    # Kompression auch für IS_sortino.
    is_sortino_val = m.is_sortino_median
    if soft_scale is not None:
        is_sortino_val = _apply_soft_scale(is_sortino_val, soft_scale)

    overfit_gap = max(0.0, is_sortino_val - base)
    divergence_mode = weights.get("overfit_divergence_mode")
    if divergence_mode == "symmetric":
        diff = is_sortino_val - base
        if diff >= 0.0:
            divergence_penalty = penalty_overfit_weight * diff
        else:
            oos_luck_w = float(
                weights.get("overfit_oos_luck_weight", penalty_overfit_weight)
            )
            divergence_penalty = oos_luck_w * (-diff)
    else:
        divergence_penalty = penalty_overfit_weight * overfit_gap

    if penalty_relative_cap is not None:
        divergence_penalty = min(
            divergence_penalty, float(penalty_relative_cap) * abs(base)
        )

    dd_penalty = (
        float(penalty_dd_weight) * ((m.oos_max_drawdown / risk_dd_cap) ** 2)
        if risk_dd_cap and float(risk_dd_cap) > 0.0
        else 0.0
    )

    # Issue #509 (Cost Drag & Turnover Churning) - Turnover Penalty
    # The penalty increases linearly with the number of OOS trades.
    turnover_penalty = m.oos_total_trades * penalty_turnover_weight

    # Issue #565 / #575 — Fold-Dispersions-Strafe. Auch die Fold-Sortinos müssen in die
    # komprimierte Skala transformiert werden, bevor pstdev berechnet wird.
    fold_dispersion_penalty = 0.0
    w_disp = weights.get("fold_dispersion_weight")
    fold_sortinos = getattr(m, "oos_fold_sortinos", None) or []
    if w_disp and len(fold_sortinos) >= 2:
        scaled_folds = [_apply_soft_scale(float(s), soft_scale) for s in fold_sortinos]
        fold_dispersion_penalty = float(w_disp) * statistics.pstdev(scaled_folds)
        if penalty_relative_cap is not None:
            fold_dispersion_penalty = min(
                fold_dispersion_penalty, float(penalty_relative_cap) * abs(base)
            )

    # Issue #559 — return-Tie-Breaker: bricht Rest-Plateaus im eligiblen Ast ökonomisch sinnvoll auf
    # (oos_total_return streut, wo der — nun weich gesättigte — Sortino nicht mehr differenziert).
    # w_ret klein wählen, damit die Sortino-Ordnung nicht überstimmt wird. Fehlt w_ret ⇒ 0.0.
    w_ret = float(weights.get("w_ret", 0.0))
    return_tie_breaker = w_ret * float(m.oos_total_return)

    floor = -float(weights["sortino_clip_abs"])

    # A4.3: per-symbol reward path. The coverage term (win_count/universe_size) degenerates
    # when the universe is a single symbol, so drop it and add a shrinkage penalty toward the
    # global optimum instead (Gate 2 in reward space). Triggered by universe_size == 1, or
    # explicitly via reward_mode == 'per_symbol'.
    reward_mode = weights.get("reward_mode", "auto")
    if universe_size == 1 or reward_mode == "per_symbol":
        param_pen = 0.0
        if sampled and global_params and strategy:
            from automation.optimizer import bounds

            b = bounds.extract_numeric_bounds(strategy)
            param_pen = weights["lambda_reg"] * bounds.normalized_param_distance(
                sampled, global_params, b
            )
        reward = (
            base
            - divergence_penalty
            - dd_penalty
            - param_pen
            - turnover_penalty
            - fold_dispersion_penalty
            + return_tie_breaker
        )
        return max(reward, floor)

    # Coverage path (universe_size > 1) — bit-identical to the pre-A4.3 behaviour when the new
    # opt-in shaping keys (sortino_soft_scale/overfit_divergence_mode/fold_dispersion_weight/w_ret)
    # are absent.
    coverage = m.win_count / max(1, universe_size)
    reward = (
        base
        - divergence_penalty
        - dd_penalty
        + coverage * bonus_coverage_weight
        - turnover_penalty
        - fold_dispersion_penalty
        + return_tie_breaker
    )
    return max(reward, floor)
