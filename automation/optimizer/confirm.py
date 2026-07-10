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


def _holdout_gate_passed(metrics, risk_dd_cap: float, *, sortino_fallback_enabled: bool) -> bool:
    """Issue #533 — Single Source of Truth für die Holdout-Gate-Entscheidung inkl.
    ``oos_sortino_fallback``-Parität zu ``reward.py`` (die »evaluable-but-sortino-undefined«-Regel).

    Der Sortino ist per Definition ``None`` auf einem verlustfreien OOS-Fold (``losses_count == 0``,
    ``backtest_runner.py``). Vor diesem Fix blockierte das Holdout-Gate eine solche verlustfreie,
    profitable OOS-Periode fälschlich (``None → 0.0``, ``0.0 > 0.0`` = False), obwohl sie das beste
    denkbare Ergebnis ist. Analog zu ``reward.py['oos_sortino_fallback'] == 'total_return'``
    (``reward.py`` compute_reward) greift jetzt der ``oos_total_return > 0`` als Pass-Kriterium,
    sobald der Sortino mathematisch undefiniert ist. ``oos_total_return <= 0`` passiert NIEMALS —
    kein Gate-Gaming; Micro-Sizing-/Risiko-Gates bleiben über ``oos_eligible`` und den
    ``risk_dd_cap`` wirksam.
    """
    if not (metrics.oos_evaluated and metrics.oos_eligible):
        return False

    # Drawdown-Cap (None-safe: ein fehlender Drawdown gilt als schlechtestmöglich ⇒ blockiert).
    max_dd = metrics.oos_max_drawdown if metrics.oos_max_drawdown is not None else 1.0
    if max_dd > risk_dd_cap:
        return False

    if metrics.oos_sortino is not None:
        return metrics.oos_sortino > 0.0

    # Sortino undefiniert (Zero-Loss-Fold): total_return-Fallback nur bei aktiviertem Flag
    # (Parität zu reward.py; fehlt/deaktiviert ⇒ Legacy-Verhalten, blockiert).
    if sortino_fallback_enabled:
        return metrics.oos_total_return > 0.0
    return False


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
    best_trial = study.best_trials[0] if len(getattr(study, "directions", ["maximize"])) > 1 else study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"
    seed = 42
    oos_sortino_fallback = None
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f)
            seed = opt_data.get("seed", 42)
            # Issue #533 — oos_sortino_fallback-Parität zu reward.py (Zero-Hardcoding).
            oos_sortino_fallback = opt_data.get("oos_sortino_fallback")

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

    # Issue #533 — Holdout-Gate mit oos_sortino_fallback-Parität: ein verlustfreier (sortino=None),
    # profitabler OOS-Fold passiert über oos_total_return>0, statt fälschlich blockiert zu werden.
    passed = _holdout_gate_passed(
        metrics, risk_dd_cap,
        sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
    )

    return {
        "passed": passed,
        "metrics": {
            "oos_sortino": metrics.oos_sortino,
            "oos_total_return": metrics.oos_total_return,
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

    is_window_days = wf_cfg["is_window_days"]
    holdout_days = wf_cfg["holdout_days"]
    oos_window_days_cfg = wf_cfg["oos_window_days"]
    n_folds = wf_cfg["splits"]
    embargo_period_days = wf_cfg["embargo_period_days"]

    if catalog_newest_ns is not None:
        now = dt.datetime.now(dt.timezone.utc)
        from automation.optimizer.trial_config import compute_walk_forward_window
        # Issue #548 — der Embargo MUSS auch hier im Fenster-Span reserviert werden (dieselbe
        # Single-Source-of-Truth-Funktion wie build_trial), sonst divergiert die Reachability-
        # Geometrie von der real gebauten Holdout-Geometrie. Mit reserviertem Embargo gilt
        # ``oos_lo_ns == end`` (die exakte äussere Fenster-Grenze), konsistent zu #548.
        window_start, _ = compute_walk_forward_window(
            now=now,
            holdout_days=holdout_days,
            is_window_days=is_window_days,
            oos_window_days=oos_window_days_cfg,
            n_folds=n_folds,
            embargo_period_days=embargo_period_days,
            catalog_newest_ns=catalog_newest_ns,
        )
        oos_lo_ns = int((window_start + dt.timedelta(days=is_window_days + (n_folds * oos_window_days_cfg) + embargo_period_days)).timestamp() * 1_000_000_000)
        # verfügbar, sobald catalog_newest_ns >= holdout_oos_start_ns
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

    cfg_dir = config_dir()
    risk_dd_cap = 0.30
    tournament_path = cfg_dir / "tournament.json"
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            risk_dd_cap = (json.load(f) or {}).get("max_drawdown", 0.30)

    promotion_margin = 0.10
    optimizer_path = cfg_dir / "optimizer.json"
    global_weights = None
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            global_weights = json.load(f)
            promotion_margin = global_weights.get("promotion_margin", 0.10)
            global_weights["reward_mode"] = "auto"
    # Issue #533 — oos_sortino_fallback-Parität zu reward.py / confirm_on_holdout (Zero-Hardcoding).
    oos_sortino_fallback = global_weights.get("oos_sortino_fallback") if global_weights else None

    m_global = _holdout_metrics_for_params(strategy, symbol, global_params,
                                           run_backtest=run_backtest, build_trial=build_trial,
                                           catalog_newest_ns=catalog_newest_ns)
    R_global = compute_reward(m_global, universe_size=1, weights=global_weights) if global_weights else compute_reward(m_global, universe_size=1)

    import optuna
    import logging
    # 1. Top-k Holdout Evaluation (Issue #576)
    # Selektiere alle eligiblen Trials basierend auf OOS Gate (oder einfach die besten eligiblen).
    # t.value ist der (korrigierte) Reward.
    eligible_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
                       and t.user_attrs.get("is_rejection_detail") is None
                       and t.value is not None]

    # Sortiere absteigend nach Reward (best_value)
    eligible_trials.sort(key=lambda t: t.value, reverse=True)

    # Tournament cfg (for Top-k und Deflation)
    tournament_cfg = {}
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            tournament_cfg = json.load(f) or {}

    top_k = tournament_cfg.get("holdout_top_k", 5)
    best_trials = eligible_trials[:top_k]

    if not best_trials:
        # Fallback falls keine eligiblen gefunden wurden. Sollte eigentlich nicht passieren,
        # da confirm_per_symbol_promotion nur fuer winner_candidates aufgerufen wird.
        best_trials = study.best_trials if len(getattr(study, "directions", ["maximize"])) > 1 else [study.best_trial]

    # 2. Deflations-Vorfilter (Issue #576)
    deflated_selection = bool(tournament_cfg.get("deflated_selection", False))
    deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))
    deflated_min_sortino = None

    if deflated_selection:
        import statistics as _dstats
        # Nutze alle Trials die OOS-evaluiert wurden, unabhaengig von IS-Eligibility.
        cand_sortinos = [
            t.user_attrs.get("oos_sortino") for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE and t.user_attrs.get("oos_evaluated")
        ]
        # Issue #576: 50-Clip-Sentinels aus der Deflations-Dispersion ausschliessen
        cand_sortinos = [float(s) for s in cand_sortinos if s is not None and float(s) != 50.0]
        if len(cand_sortinos) >= 2:
            dispersion = _dstats.pstdev(cand_sortinos)
            from automation.optimizer.deflation import deflated_threshold
            deflated_min_sortino = deflated_threshold(
                len(cand_sortinos), dispersion,
                confidence=deflation_confidence, baseline=0.0)
            logging.getLogger("optimizer").info(
                f"[Deflated Holdout #576] {symbol}: Schwelle {deflated_min_sortino:.4f} "
                f"(N={len(cand_sortinos)}, σ={dispersion:.4f})"
            )

    # Evaluiere den Holdout ueber die Top-k Trials
    holdout_metrics_list = []

    for trial in best_trials:
        symbol_params = trial.user_attrs.get("sampled_params", trial.params)
        m_symbol = _holdout_metrics_for_params(strategy, symbol, symbol_params,
                                               run_backtest=run_backtest, build_trial=build_trial,
                                               catalog_newest_ns=catalog_newest_ns)
        holdout_metrics_list.append((trial, symbol_params, m_symbol))

    # Bilde den MEDIAN-Holdout aus den Top-k Evaluierungen (Robustheitsmaximierung #576)
    import statistics

    def _median_or_none(vals):
        clean_vals = [v for v in vals if v is not None]
        return statistics.median(clean_vals) if clean_vals else None

    # Aggregiere die Metriken zu einem Median-Holdout-Kandidaten
    median_sortino = _median_or_none([m.oos_sortino for _, _, m in holdout_metrics_list])
    median_max_drawdown = _median_or_none([m.oos_max_drawdown for _, _, m in holdout_metrics_list])
    median_total_return = _median_or_none([m.oos_total_return for _, _, m in holdout_metrics_list])

    # Fuer das Proposal werten wir den 'besten' (d.h. den am hoechsten rankenden IS) aus,
    # aber nutzen die Median-Metriken zur Evaluierung der Holdout-Gate-Robustheit.
    best_trial, best_symbol_params, best_m_symbol = holdout_metrics_list[0]

    # Mocke ein TournamentMetrics-aehnliches Objekt fuer die Median-Evaluierung
    from automation.optimizer.parsing import TournamentMetrics
    median_m_symbol = TournamentMetrics(
        oos_evaluated=all(m.oos_evaluated for _, _, m in holdout_metrics_list),
        oos_eligible=all(m.oos_eligible for _, _, m in holdout_metrics_list),
        is_sortino_median=0.0, # Nicht relevant fuer Holdout-Gate
        oos_sortino=median_sortino,
        oos_max_drawdown=median_max_drawdown if median_max_drawdown is not None else 1.0,
        oos_total_trades=int(_median_or_none([m.oos_total_trades for _, _, m in holdout_metrics_list]) or 0),
        win_count=0,
        fully_eligible_pairs=0,
        is_total_trades=0,
        oos_total_return=median_total_return if median_total_return is not None else 0.0
    )

    R_symbol = compute_reward(median_m_symbol, universe_size=1, weights=global_weights) if global_weights else compute_reward(median_m_symbol, universe_size=1)

    # Gate-Evaluation ZWINGEND ueber den Median-Holdout
    holdout_passed = _holdout_gate_passed(
        median_m_symbol, risk_dd_cap,
        sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
    )

    # Deflations-Vorfilter Check
    if holdout_passed and deflated_min_sortino is not None:
        if median_sortino is None or median_sortino < deflated_min_sortino:
            holdout_passed = False
            logging.getLogger("optimizer").warning(
                f"[Deflated-Drop Holdout #576] {symbol}: Median-Holdout-Sortino {median_sortino} "
                f"< deflated {deflated_min_sortino:.4f}"
            )

    promote = bool(holdout_passed and (R_symbol > R_global + promotion_margin))

    if not holdout_passed:
        status = "REJECTED_ON_HOLDOUT"
    elif promote:
        status = "READY_FOR_PR"
    else:
        status = "REJECTED_NO_EDGE_OVER_GLOBAL"

    best_result = {
        "promote": promote,
        "status": status,
        "is_rejection_detail_override": None,
        "symbol_params": best_symbol_params,
        "R_symbol": R_symbol,
        "R_global": R_global,
        "promotion_margin": promotion_margin,
        "holdout_passed": bool(holdout_passed),
        "metrics_symbol": _metrics_dict(median_m_symbol),
        "metrics_global": _metrics_dict(m_global)
    }

    if deflated_min_sortino is not None:
        best_result["metrics_symbol"]["deflated_min_sortino"] = deflated_min_sortino

    return best_result


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
        "reward": study.best_value if len(getattr(study, "directions", ["maximize"])) == 1 else promotion.get("R_symbol", 0.0),
        "proposed_instrument_override": promotion["symbol_params"],
        "R_symbol": promotion["R_symbol"],
        "R_global": promotion["R_global"],
        "promotion_margin": promotion["promotion_margin"],
        # Issue #408 — modale Gate-Drop-Reason ueber alle Trials (Observability; aendert NIE die
        # Promotion-Entscheidung selbst, die ausschliesslich ueber das Holdout-Gate faellt).
        "dominant_rejection": _dominant_rejection(study),
        # Issue #453 — granularere, dezidierte dominante Ablehnungs-Kategorie (löst den Catch-All
        # 'oos_not_evaluated' in die tatsächliche, handlungsleitende Ursache auf).
        "is_rejection_detail": promotion.get("is_rejection_detail_override") or _dominant_is_rejection_detail(study),
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
    best_trial = study.best_trials[0] if len(getattr(study, "directions", ["maximize"])) > 1 else study.best_trial
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
