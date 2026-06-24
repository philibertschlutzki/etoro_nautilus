import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics




_oos_min_trades_cache: int | None = None

def _read_oos_min_trades() -> int:
    global _oos_min_trades_cache
    if _oos_min_trades_cache is not None:
        return _oos_min_trades_cache

    try:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "tournament.json"
        if not cfg_path.exists():
            _oos_min_trades_cache = 3
            return 3
        with open(cfg_path, 'r', encoding='utf-8') as f:
            import json
            tournament_cfg = json.load(f)
            _oos_min_trades_cache = tournament_cfg.get("oos_min_trades", 3)
            return _oos_min_trades_cache
    except OSError:
        _oos_min_trades_cache = 3
        return 3
    except ValueError:
        _oos_min_trades_cache = 3
        return 3


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
    if return_target:
        components.append(min(1.0, max(0.0, m.is_best_total_return) / float(return_target)))
    winrate_target = weights.get("shaping_winrate_target")
    if winrate_target:
        components.append(min(1.0, max(0.0, m.is_best_win_rate) / float(winrate_target)))
    if not components:
        return 0.0
    return sum(components) / len(components)


def compute_reward(m: "TournamentMetrics", universe_size: int,
                   weights: dict | None = None, risk_dd_cap: float | None = None,
                   *, sampled: dict | None = None, global_params: dict | None = None,
                   strategy: str | None = None) -> float:
    """weights=None  ⇒ aus optimizer.json (penalty_overfit_weight, penalty_dd_weight,
                        bonus_coverage_weight, penalty_unevaluable_oos, sortino_clip_abs).
       risk_dd_cap=None ⇒ aus tournament.json (max_drawdown).
       Falls not m.oos_evaluated oder m.oos_sortino is None: Unevaluable-Pfad (Penalty + Shaping).
       ISSUE #401: Ist ein OOS-Sample evaluated ∧ eligible, aber oos_sortino is None
       (Zero-Loss / n < sortino_min_trades), UND weights['oos_sortino_fallback'] == 'total_return',
       wird statt des Penalty-Pfades der geclippte oos_total_return als evaluable Base genutzt
       (Flat-Reward-Landscape-Fix). Fehlt der Schluessel ⇒ unveraenderter Legacy-Penalty-Pfad.

       universe_size > 1 (und reward_mode != 'per_symbol') ⇒ Coverage-Pfad (bit-identisch, A4.3/HI-2):
         reward = base - overfit_gap*penalty_overfit_weight - dd_excess*penalty_dd_weight
                  + coverage*bonus_coverage_weight ; coverage = win_count / max(1, universe_size).

       universe_size == 1 ODER weights['reward_mode'] == 'per_symbol' ⇒ Per-Symbol-Pfad (A4.3):
         KEIN Coverage-Term, dafür Shrinkage-Strafe param_pen Richtung global:
         param_pen = lambda_reg * normalized_param_distance(sampled, global_params,
                                  bounds.extract_numeric_bounds(strategy))
                     falls (sampled and global_params and strategy), sonst 0.0.
         reward = base - overfit_gap*penalty_overfit_weight - dd_excess*penalty_dd_weight - param_pen.

       base = clip(oos_sortino, ±sortino_clip_abs); overfit_gap = max(0, is_sortino_median - base);
       dd_excess = max(0, oos_max_drawdown - risk_dd_cap).
       floor = penalty_unevaluable_oos + unevaluable_shaping_span + evaluable_floor_epsilon;
       return max(reward, floor)  # Ordnungsinvariante: evaluable >= floor > unevaluable."""

    if weights is None:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "optimizer.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            weights = json.load(f)

    if risk_dd_cap is None:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "tournament.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            tournament_cfg = json.load(f)
            risk_dd_cap = tournament_cfg["max_drawdown"]

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
    if (m.oos_evaluated and m.oos_eligible and m.oos_sortino is None
            and weights.get("oos_sortino_fallback") == "total_return"):
        base_source = m.oos_total_return

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
            shaping_trade_target = (weights.get("per_symbol_shaping_trade_target")
                                    or weights.get("shaping_trade_target"))
        else:
            shaping_trade_target = weights.get("shaping_trade_target")
        if shaping_trade_target:
            activity = min(1.0, m.is_total_trades / max(1, int(shaping_trade_target)))
            progress = max(progress, activity)

        # Issue #407 — kontinuierlicher Eligibility-Gradient: die normierte Gate-Naehe (IS-
        # Performance) hebt 'fast eligible' ueber 'nie eligible', auch wenn weder OOS- noch IS-
        # Trade-Aktivitaet allein einen Gradienten liefern. Additiv und gebunden: _gate_proximity
        # ∈ [0,1], also bleibt progress ∈ [0,1] ⇒ Shaping hart durch unevaluable_shaping_span
        # gedeckelt (Anti-Gate-Gaming-Invariante).
        progress = max(progress, _gate_proximity(m, weights))

        # Floor invariant: progress ∈ [0, 1] ⇒ shaping ≤ unevaluable_shaping_span, hence every
        # unevaluable trial stays ≤ penalty + span, strictly below the evaluable floor below.
        shaping = weights["unevaluable_shaping_span"] * progress
        return penalty_unevaluable_oos + shaping

    sortino_clip_abs = weights["sortino_clip_abs"]
    base = max(-sortino_clip_abs, min(sortino_clip_abs, base_source))

    penalty_overfit_weight = weights["penalty_overfit_weight"]
    penalty_dd_weight = weights["penalty_dd_weight"]
    bonus_coverage_weight = weights["bonus_coverage_weight"]

    overfit_gap = max(0.0, m.is_sortino_median - base)
    dd_excess = max(0.0, m.oos_max_drawdown - risk_dd_cap)

    floor = penalty_unevaluable_oos + weights["unevaluable_shaping_span"] + weights["evaluable_floor_epsilon"]

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
            param_pen = weights["lambda_reg"] * bounds.normalized_param_distance(sampled, global_params, b)
        reward = (base
                  - overfit_gap * penalty_overfit_weight
                  - dd_excess * penalty_dd_weight
                  - param_pen)
        return max(reward, floor)

    # Coverage path (universe_size > 1) — bit-identical to the pre-A4.3 behaviour.
    coverage = m.win_count / max(1, universe_size)
    reward = (base
              - overfit_gap * penalty_overfit_weight
              - dd_excess * penalty_dd_weight
              + coverage * bonus_coverage_weight)
    return max(reward, floor)
