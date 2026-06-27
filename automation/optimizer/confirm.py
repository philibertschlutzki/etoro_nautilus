import datetime as dt
import json
import hashlib
from collections import Counter
from pathlib import Path
from automation.optimizer.trial_config import build_trial, config_dir
from automation.optimizer.runner import run_backtest, BacktestRunError
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import compute_reward
from automation.optimizer.manifest import WORK

def confirm_on_holdout(
    study,
    strategy: str,
    *,
    run_backtest=run_backtest,
    build_trial=build_trial
) -> dict:
    """
    Trial mit holdout_days=0, n_folds=1 (Holdout = reguläres OOS).
    Liest risk_dd_cap aus tournament.json.
    passed = oos_evaluated & oos_eligible & oos_sortino>0 & oos_max_drawdown<=cap.
    Rückgabe: {'passed': bool, 'metrics': dict, 'trial_dir': str}.
    """
    best_trial = study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"
    seed = 42
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f)
            seed = opt_data.get("seed", 42)

    # Dynamisch Holdout-Tage auslesen (Zero-Hardcoding)
    backtest_path = cfg_dir / "backtest.json"
    holdout_days_cfg = 45
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            bt_data = json.load(f)
            holdout_days_cfg = bt_data.get("walk_forward", {}).get("holdout_days", 45)

    # Erzeuge Holdout-Trial mit holdout_days=0 und n_folds=1, aber OOS override auf Holdout-Länge
    trial_dir, manifest_path = build_trial(
        strategy_class=strategy,
        sampled=sampled,
        study_name=f"{study.study_name}_holdout",
        trial_number=best_trial.number,
        seed=seed,
        holdout_days=0,
        n_folds=1,
        oos_window_days_override=holdout_days_cfg
    )

    # Subprozess/Backtest ausführen
    try:
        output_path = run_backtest(trial_dir, manifest_path)
        metrics = parse_tournament(output_path)
    except BacktestRunError:
        return {
            "passed": False,
            "metrics": {},
            "trial_dir": str(trial_dir),
            "reason": "holdout_subprocess_failed"
        }

    tournament_path = cfg_dir / "tournament.json"
    risk_dd_cap = 0.30  # Fallback
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
            risk_dd_cap = t_data.get("max_drawdown", 0.30)

    oos_sortino = metrics.oos_sortino if metrics.oos_sortino is not None else 0.0

    passed = (
        metrics.oos_evaluated and
        metrics.oos_eligible and
        oos_sortino > 0.0 and
        metrics.oos_max_drawdown <= risk_dd_cap
    )

    return {
        "passed": passed,
        "metrics": {
            "oos_sortino": metrics.oos_sortino,
            "oos_max_drawdown": metrics.oos_max_drawdown,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
        },
        "trial_dir": str(trial_dir)
    }


def _metrics_dict(m) -> dict:
    """Serialisierbare Teilmenge der Holdout-Metriken (analog confirm_on_holdout)."""
    return {
        "oos_sortino": m.oos_sortino,
        "oos_max_drawdown": m.oos_max_drawdown,
        "oos_evaluated": m.oos_evaluated,
        "oos_eligible": m.oos_eligible,
        "oos_total_trades": m.oos_total_trades,
    }


def _holdout_metrics_for_params(strategy: str, symbol: str, params: dict,
                                *, run_backtest=run_backtest, build_trial=build_trial,
                                catalog_newest_ns: int | None = None):
    """Führt einen Holdout-Backtest für genau `symbol` mit `params` aus und parst die Metriken.

    Wie confirm_on_holdout (holdout_days=0, n_folds=1, oos_window_days_override=holdout_days),
    aber single-symbol (instruments=[symbol]) und mit beliebigem Param-Vektor — so lassen sich
    der symbol-getunte und der globale Vektor auf demselben, nie-optimierten Holdout vergleichen.
    """
    cfg_dir = config_dir()
    seed = 42
    optimizer_path = cfg_dir / "optimizer.json"
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            seed = (json.load(f) or {}).get("seed", 42)

    holdout_days_cfg = 45
    backtest_path = cfg_dir / "backtest.json"
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            holdout_days_cfg = (json.load(f) or {}).get("walk_forward", {}).get("holdout_days", 45)

    # Deterministischer Discriminator, damit symbol- und global-Lauf nicht in dasselbe trial_dir schreiben.
    tag = hashlib.sha1(json.dumps(params or {}, sort_keys=True, default=str).encode()).hexdigest()[:8]
    study_name = f"confirm_{strategy}_{symbol.replace('.', '_')}_{tag}"

    trial_dir, manifest_path = build_trial(
        strategy_class=strategy,
        sampled=params,
        study_name=study_name,
        trial_number=0,
        seed=seed,
        holdout_days=0,
        n_folds=1,
        oos_window_days_override=holdout_days_cfg,
        instruments=[symbol],
        catalog_newest_ns=catalog_newest_ns,
    )
    output_path = run_backtest(trial_dir, manifest_path)
    return parse_tournament(output_path)



def confirm_per_symbol_promotion(study, strategy: str, symbol: str, global_params: dict,
                                 *, run_backtest=run_backtest, build_trial=build_trial,
                                 catalog_newest_ns: int | None = None) -> dict:
    """Gate 3 — das entscheidende Per-Symbol-Promotion-Gate.

    Ein instrument_override wird nur promotet, wenn der symbol-getunte Vektor auf dem
    ungesehenen Holdout (a) das Holdout-Gate selbst besteht UND (b) das globale Baseline um
    `promotion_margin` (optimizer.json) schlägt.

    **Verbindliche Design-Entscheidung:** Der Vergleichs-Score ist die *rohe* risikoadjustierte
    Performance — `compute_reward(..., universe_size=1)` OHNE `sampled`/`global_params` ⇒
    `param_pen = 0`. param_pen ist ein Such-Regularisierer, kein Performance-Maß, und würde den
    fairen Edge-Test verzerren.

    Rückfrage-Klärung: Fällt der globale Vektor im Holdout durch das Risk-Cap, liefert
    compute_reward dennoch einen (entsprechend niedrigen) endlichen R_global; ein symbol-getunter
    Vektor, der das Holdout-Gate selbst besteht und R_global + Marge schlägt, gilt damit als Edge.

    status ∈ {'READY_FOR_PR', 'REJECTED_NO_EDGE_OVER_GLOBAL', 'REJECTED_ON_HOLDOUT'}.
    """
    cfg_dir = config_dir()
    backtest_path = cfg_dir / "backtest.json"
    wf_cfg = {}
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            wf_cfg = (json.load(f) or {}).get("walk_forward", {})

    is_window_days = wf_cfg.get("is_window_days", 120)
    holdout_days = wf_cfg.get("holdout_days", 45)

    if catalog_newest_ns is not None:
        now = dt.datetime.now(dt.timezone.utc)
        from automation.optimizer.trial_config import compute_walk_forward_window
        _, holdout_start = compute_walk_forward_window(
            now=now,
            holdout_days=holdout_days,
            is_window_days=is_window_days,
            oos_window_days=30,
            n_folds=1,
            catalog_newest_ns=catalog_newest_ns,
        )
        oos_lo_ns = int((holdout_start + dt.timedelta(days=is_window_days)).timestamp() * 1_000_000_000)
        if catalog_newest_ns < oos_lo_ns:
            return {
                "promote": False,
                "status": "REJECTED_ON_HOLDOUT",
                "is_rejection_detail_override": "REJECT_HOLDOUT_UNREACHABLE",
                "symbol_params": {},
                "R_symbol": 0.0,
                "R_global": 0.0,
                "promotion_margin": 0.0,
                "metrics_symbol": {},
                "metrics_global": {}
            }

    best_trial = study.best_trial
    symbol_params = best_trial.user_attrs.get("sampled_params", best_trial.params)

    m_symbol = _holdout_metrics_for_params(strategy, symbol, symbol_params,
                                           run_backtest=run_backtest, build_trial=build_trial,
                                           catalog_newest_ns=catalog_newest_ns)
    m_global = _holdout_metrics_for_params(strategy, symbol, global_params,
                                           run_backtest=run_backtest, build_trial=build_trial,
                                           catalog_newest_ns=catalog_newest_ns)

    # Rohe risikoadjustierte Performance (Per-Symbol-Pfad, KEIN param_pen).
    R_symbol = compute_reward(m_symbol, universe_size=1)
    R_global = compute_reward(m_global, universe_size=1)

    cfg_dir = config_dir()
    risk_dd_cap = 0.30
    tournament_path = cfg_dir / "tournament.json"
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            risk_dd_cap = (json.load(f) or {}).get("max_drawdown", 0.30)

    promotion_margin = 0.10
    optimizer_path = cfg_dir / "optimizer.json"
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            promotion_margin = (json.load(f) or {}).get("promotion_margin", 0.10)

    holdout_passed = (
        m_symbol.oos_evaluated and m_symbol.oos_eligible
        and (m_symbol.oos_sortino if m_symbol.oos_sortino is not None else -9.0) > 0.0
        and (m_symbol.oos_max_drawdown if m_symbol.oos_max_drawdown is not None else 1.0) <= risk_dd_cap
    )

    promote = bool(holdout_passed and (R_symbol > R_global + promotion_margin))

    if not holdout_passed:
        status = "REJECTED_ON_HOLDOUT"
    elif promote:
        status = "READY_FOR_PR"
    else:
        status = "REJECTED_NO_EDGE_OVER_GLOBAL"

    return {
        "promote": promote,
        "status": status,
        "R_symbol": R_symbol,
        "R_global": R_global,
        "promotion_margin": promotion_margin,
        "holdout_passed": bool(holdout_passed),
        "metrics_symbol": _metrics_dict(m_symbol),
        "metrics_global": _metrics_dict(m_global),
        "symbol_params": symbol_params,
    }


def _dominant_rejection(study) -> str | None:
    """Issue #408 — modale Per-Trial-Rejection-Reason ueber alle Trials der Study (Counter).

    Macht im Proposal sichtbar, WARUM ein Symbol nicht promotet wurde. Klebt z. B. jeder Trial auf
    'oos_not_evaluated', ist das die Pitfall-#75-Signatur (das Symbol erzeugte nie evaluierbare
    OOS-Trades). Trials ohne `rejection_reason` (Legacy/gepruned) werden ignoriert; gibt es keine,
    ist die Reason `None`."""
    reasons = [t.user_attrs.get("rejection_reason") for t in study.trials
               if t.user_attrs.get("rejection_reason")]
    if not reasons:
        return None
    return Counter(reasons).most_common(1)[0][0]


def _dominant_is_rejection_detail(study) -> str | None:
    """Issue #453 — modale GRANULARE Ablehnungs-Kategorie (``is_rejection_detail``-User-Attr) über
    alle Trials. Wo ``_dominant_rejection`` nur grob 'oos_not_evaluated' liefert, macht dies die
    tatsächliche dominante Ursache sichtbar (z. B. ``REJECT_OOS_WINDOW_UNREACHABLE`` ⇒ Katalog-H2
    auffrischen statt Parameter tunen; ``REJECT_OOS_MAX_DRAWDOWN`` ⇒ Risiko-Constraint). Trials ohne
    das Attr (Legacy/gepruned) werden ignoriert; gibt es keine, ist die Kategorie ``None``."""
    details = [t.user_attrs.get("is_rejection_detail") for t in study.trials
               if t.user_attrs.get("is_rejection_detail")]
    if not details:
        return None
    return Counter(details).most_common(1)[0][0]


def export_symbol_proposal(study, strategy: str, symbol: str, promotion: dict) -> Path:
    """Schreibt data/optimizer/proposal_{strategy}_{symbol}.json. Schreibt NIE in strategies.json —
    Promotion erfolgt ausschließlich per menschlich freigegebenem PR (HI-3)."""
    payload = {
        "strategy": strategy,
        "symbol": symbol,
        "status": promotion["status"],
        "reward": study.best_value,
        "proposed_instrument_override": promotion["symbol_params"],
        "R_symbol": promotion["R_symbol"],
        "R_global": promotion["R_global"],
        "promotion_margin": promotion["promotion_margin"],
        # Issue #408 — modale Gate-Drop-Reason ueber alle Trials (Observability; aendert NIE die
        # Promotion-Entscheidung selbst, die ausschliesslich ueber das Holdout-Gate faellt).
        "dominant_rejection": _dominant_rejection(study),
        # Issue #453 — granularere, dezidierte dominante Ablehnungs-Kategorie (löst den Catch-All
        # 'oos_not_evaluated' in die tatsächliche, handlungsleitende Ursache auf).
        "is_rejection_detail": promotion.get("is_rejection_detail_override", _dominant_is_rejection_detail(study)),
        "holdout": {
            "symbol": promotion["metrics_symbol"],
            "global": promotion["metrics_global"],
        },
    }

    out_path = WORK / f"proposal_{strategy}_{symbol}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def export_no_viable_proposal(study, strategy: str) -> Path:
    """
    Exportiert ein Proposal mit status="NO_VIABLE_TRIAL", falls alle Trials gepruned wurden.
    """
    payload = {
        "strategy": strategy,
        "status": "NO_VIABLE_TRIAL",
        "reward": None,
        "proposed_params_override": {},
        "note": "No valid trial found due to data coverage or OOS gating rejection.",
        "holdout": None
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def export_proposal(study, strategy: str, holdout: dict) -> Path:
    """
    Schreibt data/optimizer/proposal_<strategy>.json mit proposed_params_override
    (best_trial.user_attrs['sampled_params']), Reward, Holdout-Metriken,
    status = 'READY_FOR_PR' wenn holdout['passed'] sonst 'REJECTED_ON_HOLDOUT'.
    """
    best_trial = study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    status = "READY_FOR_PR" if holdout.get("passed") else "REJECTED_ON_HOLDOUT"

    payload = {
        "strategy": strategy,
        "status": status,
        "reward": best_trial.value,
        "proposed_params_override": sampled,
        "holdout": holdout
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path
