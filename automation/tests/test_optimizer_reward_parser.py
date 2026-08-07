import json
import math
import statistics
import pytest
from pathlib import Path
from automation.optimizer import parsing, reward


def _base(cfg, sortino):
    """Issue #559 — weiche Sättigung (asinh), sonst Legacy-Hard-Clip."""
    ss = cfg.get("sortino_soft_scale")
    if ss:
        return float(ss) * math.asinh(sortino / float(ss))
    return max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], sortino))


def _apply_soft_scale_inline(value, scale):
    if scale is not None and scale > 0.0:
        return float(scale) * math.asinh(float(value) / float(scale))
    return float(value)


def _divergence(cfg, is_median, base):
    """Issue #565 / #575 — symmetrische Divergenz mit Skalenparität und Capping."""
    soft_scale = cfg.get("sortino_soft_scale")
    is_sortino_val = _apply_soft_scale_inline(is_median, soft_scale)

    if cfg.get("overfit_divergence_mode") == "symmetric":
        diff = is_sortino_val - base
        if diff >= 0.0:
            penalty = cfg["penalty_overfit_weight"] * diff
        else:
            penalty = cfg.get(
                "overfit_oos_luck_weight", cfg["penalty_overfit_weight"]
            ) * (-diff)
    else:
        penalty = cfg["penalty_overfit_weight"] * max(0.0, is_sortino_val - base)

    # Issue #591 — der relative Cap bindet an die positive Skalenkonstante soft_scale (Legacy-Fallback
    # sortino_clip_abs), NICHT an |base|.
    cap = cfg.get("penalty_relative_cap")
    if cap is not None:
        cap_scale = soft_scale if soft_scale else cfg["sortino_clip_abs"]
        penalty = min(penalty, float(cap) * float(cap_scale))

    return penalty


def _write_tournament(tmp_path, **agg):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": agg}
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(data), "utf-8")
    return p


def test_parser_uses_pooled_sortino_not_fold_median(tmp_path):
    # Issue #589 — der kanonische OOS-Sortino ist der GEPOOLTE oos_metrics["sortino_ratio"] (kohärent
    # mit total_return), NICHT der Median der oos_fold_sortinos (der einen katastrophalen Fold
    # maskierte). Gate und Reward lesen damit exakt denselben Wert.
    p = _write_tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=3,
        median_is_sortino=2.0,
        oos_fold_sortinos=[1.0, 3.0, 2.0],
        oos_metrics={"sortino_ratio": 9.9, "max_drawdown": 0.1},
    )
    m = parsing.parse_tournament(p)
    assert m.oos_sortino == 9.9  # pooled, nicht median([1.0, 3.0, 2.0])==2.0


def test_reward_uses_config_weights(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))[
        "max_drawdown"
    ]

    # Issue #597 — realistischer OOS-Drawdown (dd_penalty normiert auf dd_reward_scale, nicht auf
    # den Gate-Cap; ein 40 %-DD würde den Reward katastrophal floorten).
    dd = 0.02
    p = _write_tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=5,
        median_is_sortino=3.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": dd},
    )
    m = parsing.parse_tournament(p)

    base = _base(cfg, 1.0)
    # Ein einzelner Fold-Sortino ⇒ keine Dispersions-Strafe; oos_total_return=0 ⇒ kein Tie-Breaker.
    # Issue #597 — dd_penalty normiert auf dd_reward_scale. Issue #631 — zusätzlich mit
    # penalty_scale_vs_base gegen die realisierte Base-Streuung rekalibriert.
    dd_scale = cfg.get("dd_reward_scale", cap)
    penalty_scale = cfg.get("penalty_scale_vs_base", 1.0)
    expected = (
        base
        - _divergence(cfg, 3.0, base)
        - cfg["penalty_dd_weight"] * ((dd / dd_scale) ** 2) * penalty_scale
        + (5 / 100) * cfg["bonus_coverage_weight"]
    )

    assert reward.compute_reward(m, universe_size=100) == __import__("pytest").approx(
        expected, rel=1e-9
    )


def test_reward_unevaluable_penalty(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    p = _write_tournament(tmp_path, oos_evaluated=False, win_count=0)
    m = parsing.parse_tournament(p)

    assert reward.compute_reward(m, universe_size=100) == cfg["penalty_unevaluable_oos"]


# --- ISSUE-OPT-375: IS-activity gradient for non-evaluable trials ----------
def _write_full(dir_path, agg, full_results):
    """Tournament JSON including per-pair full_results (carries IS activity)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    data = {
        "fully_eligible_pairs": 0,
        "aggregate_winner": agg,
        "full_results": full_results,
    }
    p = dir_path / "tournament_result.json"
    p.write_text(json.dumps(data), "utf-8")
    return p


def _pairs(*trade_counts):
    # Issue #488 - Activity gradient requires some IS performance (proximity > 0)
    return [
        {"metrics": {"total_trades": n, "total_return": 0.5, "win_rate": 0.5}}
        for n in trade_counts
    ]


def test_worst_case_eligible_trial_is_unclamped_and_reflects_catastrophic_quality(tmp_path):
    """Issue #629 — kein Reward-Floor mehr. Ein eligibler, aber katastrophal schlechter Trial
    (Sortino am Legacy-Clip) erhält einen entsprechend katastrophalen, UNGEKLEMMTEN Reward und
    darf jetzt legitim UNTER einem gut geshapeten unevaluierten Trial liegen — genau das Gegenteil
    der frueheren, per Floor erzwungenen 'evaluable > unevaluable'-Invariante. Die Feasibility-
    Rangordnung selbst kommt ausschliesslich vom #612-Sampler-Constraint.

    Issue #977 (Katalog C, P0 HEADLINE) — der Drawdown liegt jetzt INNERHALB des Caps (statt weit
    darüber): dd_penalty ist seit #977 dokumentiert inert (penalty_dd_weight=0.0, das Risiko wird
    ausschliesslich über das oos_max_drawdown-GATE kontrolliert) UND ein eligibler Trial umgeht per
    Definition den gate_distance_penalty-Term — ein Drawdown ÜBER dem Cap bei gleichzeitigem
    oos_eligible=True war ohnehin ein unrealistisches Fixture (ein solcher Trial wäre nie eligible).
    Die "katastrophal, ungeklemmt"-Aussage dieses Tests kommt jetzt ausschliesslich vom geclipten
    Sortino (der Base-Term selbst), nicht mehr vom Drawdown-Kanal."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))[
        "max_drawdown"
    ]
    assert "evaluable_reward_floor" not in cfg  # #629 — der Key ist ersatzlos entfallen

    # Best-shaped unevaluable: IS activity saturated far past the target.
    saturated = cfg["shaping_trade_target"] * 1000
    m_uneval = parsing.parse_tournament(
        _write_full(
            tmp_path / "uneval",
            dict(oos_evaluated=False, win_count=0),
            _pairs(saturated),
        )
    )
    r_uneval = reward.compute_reward(m_uneval, universe_size=100)

    # Worst-case evaluable: clipped-negative OOS sortino, high IS sortino, DD over the cap.
    m_eval = parsing.parse_tournament(
        _write_full(
            tmp_path / "eval",
            dict(
                oos_evaluated=True,
                oos_eligible=True,
                win_count=0,
                median_is_sortino=cfg["sortino_clip_abs"],
                oos_fold_sortinos=[-cfg["sortino_clip_abs"]],
                oos_metrics={
                    "sortino_ratio": -cfg["sortino_clip_abs"],
                    "max_drawdown": cap * 0.5,
                    "total_trades": 20,
                },
            ),
            _pairs(20),
        )
    )
    r_eval = reward.compute_reward(m_eval, universe_size=100)

    assert math.isfinite(r_eval)
    # Issue #977 — seit dd_penalty dokumentiert inert ist (Risiko ausschliesslich über das Gate),
    # unterschreitet ein eligibler "worst case" den konstanten unevaluable-Floor nicht mehr
    # zwangsläufig (der Drawdown-Katastrophenkanal ist weg) — das ist die BEABSICHTIGTE Konsequenz
    # von #977, nicht ein Regressionsfund. Die eigentliche #629-Aussage ("kein Reward-Floor")
    # bleibt: ein NOCH schlechterer eligibler Trial (staerker divergierender IS/OOS-Sortino) muss
    # einen NOCH niedrigeren, UNGEKLEMMTEN Reward erhalten, statt an einer Untergrenze zu klemmen.
    m_worse = parsing.parse_tournament(
        _write_full(
            tmp_path / "worse",
            dict(
                oos_evaluated=True,
                oos_eligible=True,
                win_count=0,
                median_is_sortino=cfg["sortino_clip_abs"] * 3,
                oos_fold_sortinos=[-cfg["sortino_clip_abs"]] * 3,
                oos_metrics={
                    "sortino_ratio": -cfg["sortino_clip_abs"],
                    "max_drawdown": cap * 0.5,
                    "total_trades": 20,
                },
            ),
            _pairs(20),
        )
    )
    r_worse = reward.compute_reward(m_worse, universe_size=100)
    assert math.isfinite(r_worse)
    assert r_worse < r_eval  # #629 — kein Floor: ein staerkerer IS/OOS-Divergenz-Malus sinkt weiter
