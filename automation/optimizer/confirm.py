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
from automation.log_manager import emit_execution_event


def _holdout_bootstrap_ci_passes(metrics, *, confidence: float = 0.95) -> tuple[bool, float | None]:
    """Issue #619 — Stationary-Bootstrap-CI auf dem per-Perioden-Sortino des Holdout (Politis/Romano).

    Statt des Punktschätzers (``oos_sortino > 0``) prüft das Gate die UNTERE CI-Grenze
    (``ci_lower(sortino) > 0``) — die statistisch saubere Version dessen, was ``deflated_selection``
    heuristisch approximiert, und auf 21 Trades EHRLICH (das CI wird breit sein — genau die Information).
    Rückgabe ``(passes, ci_lower)``; ``passes=True`` bei zu wenigen Returns (< 5, CI nicht schätzbar ⇒
    kein zusätzliches Veto, das ``oos_sortino``-Punkt-Gate bleibt allein maßgeblich)."""
    rets = list(getattr(metrics, "oos_period_returns", ()) or ())
    if len(rets) < 5:
        return True, None
    from automation.optimizer.bootstrap import bootstrap_ci, sortino_statistic, ci_lower_bound_passes
    _, lo, _ = bootstrap_ci(rets, lambda a: sortino_statistic(a, mar=0.0, annualization=1.0),
                            confidence=confidence, seed=42)
    return ci_lower_bound_passes(lo, 0.0), lo


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


def _study_pbo(study, *, min_trials: int = 4) -> float | None:
    """Issue #619 — Probability of Backtest Overfitting (Bailey/López de Prado, CSCV) über die Study.

    Baut aus den per-Fold-OOS-Sortinos der ELIGIBLEN Trials (die getesteten Konfigurationen) eine
    ``(n_paths, n_strategies)``-IS/OOS-Matrix via CSCV über die Folds (``cpcv_paths``): je Pfad ist das
    IS-Set der Train-Folds, das OOS-Set die Test-Folds. ``PBO > 0.5`` ⇒ der IS-Gewinner ist OOS
    schlechter als der Median ⇒ die Selektion überfittet systematisch. ``None``, wenn zu wenige Trials
    oder Folds vorliegen (kein Urteil)."""
    import numpy as _np
    import optuna
    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE or not t.user_attrs.get("oos_eligible"):
            continue
        fs = t.user_attrs.get("oos_fold_sortinos") or []
        if fs:
            rows.append([float(x) for x in fs])
    if len(rows) < min_trials:
        return None
    n_folds = min(len(r) for r in rows)
    if n_folds < 2:
        return None
    mat = _np.array([r[:n_folds] for r in rows], dtype=float)   # (n_strategies, n_folds)
    from automation.optimizer.cpcv import cpcv_paths, probability_of_backtest_overfitting
    k_test = max(1, n_folds // 2)
    if not (0 < k_test < n_folds):
        return None
    is_rows, oos_rows = [], []
    for train, test in cpcv_paths(n_folds, k_test):
        is_rows.append(mat[:, list(train)].mean(axis=1))    # IS-Perf je Strategie (Train-Folds)
        oos_rows.append(mat[:, list(test)].mean(axis=1))    # OOS-Perf je Strategie (Test-Folds)
    return probability_of_backtest_overfitting(_np.array(is_rows), _np.array(oos_rows))


def _median_rank_index(values) -> int:
    """Issue #594 — Index des Laufs mit dem MEDIANEN Rang nach ``values`` (z. B. oos_total_return).

    Dokumentierte, benannte Aggregation mit expliziter Tie-Breaking-Regel (ersetzt das
    undokumentierte ``_lower_median_or_none``): aufsteigend nach ``value`` sortiert (``None`` ⇒ −inf,
    schlechtestmöglich), bei gerader Anzahl der UNTERE Median (konservativ, ``order[(n-1)//2]``). Der
    Rückgabe-Index adressiert EINEN real gelaufenen Backtest, dessen VOLLSTÄNDIGER, kohärenter
    Metrikvektor (nicht ein komponentenweiser Frankenstein-Median) in die Bewertung geht.
    Deterministisch (stabiler Sekundär-Sort nach Original-Index)."""
    n = len(values)
    if n == 0:
        raise ValueError("_median_rank_index: leere Werteliste")
    order = sorted(
        range(n),
        key=lambda i: (float("-inf") if values[i] is None else float(values[i]), i),
    )
    return order[(n - 1) // 2]


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
    m = parse_tournament(output_path)
    # Issue #615 — die trial_dir-Identität AN die Metriken heften (kein Signatur-Bruch ⇒ bestehende
    # Mocks von _holdout_metrics_for_params bleiben gültig). Macht die Kohärenz-Invariante
    # (symbol_params/R_symbol/holdout_passed stammen aus DEMSELBEN trial_dir) nachweisbar.
    try:
        m.holdout_trial_dir = str(trial_dir)
    except Exception:
        pass
    return m



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
    # Issue #594 — Holdout-Reward über denselben Codepfad mit holdout=True (IS-Terme abgeschaltet,
    # kein Platzhalter). Study-Reward und Holdout-Reward laufen damit nachweislich über compute_reward.
    R_global = (compute_reward(m_global, universe_size=1, weights=global_weights, holdout=True)
                if global_weights else compute_reward(m_global, universe_size=1, holdout=True))

    import optuna
    import logging

    # Tournament cfg (für Top-k + Deflation) — VOR der Eligible-Selektion laden (top_k wird bereits im
    # Leer-Fall gebraucht). ``holdout_top_k`` ist in tournament.json deklariert (Zero-Hardcoding, #615).
    tournament_cfg = {}
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            tournament_cfg = json.load(f) or {}
    top_k = int(tournament_cfg.get("holdout_top_k", 5))

    # 1. Top-k-Holdout-Selektion (Issue #576/#615). Filter auf die BEREITS GESTEMPELTE OOS-Eligibility
    # (run_optimization.make_symbol_objective, #615) — NICHT auf ``is_rejection_detail is None``: der
    # gestempelte Wert ist der STRING "NONE" (IS_REJECTION_NONE), nie Python-``None`` ⇒ der alte Filter
    # war in JEDER Study leer ⇒ Top-k (#576) lief nie (faktisch k=1) und der Median-Vektor (#594) war
    # inert (Index immer 0). ``oos_eligible`` ist die kohärente, direkt filterbare Grösse (identisch zu
    # ``is_rejection_detail == IS_REJECTION_NONE``). ``t.value`` ist der (korrigierte) Reward.
    eligible_trials = [t for t in study.trials
                       if t.state == optuna.trial.TrialState.COMPLETE
                       and t.user_attrs.get("oos_eligible")
                       and t.value is not None]
    eligible_trials.sort(key=lambda t: t.value, reverse=True)

    # Issue #615 — FAIL-LOUD statt stillem Floor-Trial-Fallback. Keine eligiblen Trials ⇒ strukturiertes
    # HOLDOUT_NO_ELIGIBLE_TRIALS-Event + Rejection; es wandert KEIN Floor-Trial (argmax reward über ALLE
    # Trials, evtl. ein evaluable_reward_floor-Trial) unbemerkt in den Holdout. Der frühere
    # ``study.best_trial``-Fallback promotete im Leer-Fall genau solche nie-validierten Parameter.
    if not eligible_trials:
        emit_execution_event(logging.getLogger("optimizer"), "HOLDOUT_NO_ELIGIBLE_TRIALS", {
            "symbol": symbol,
            "strategy": strategy,
            "n_trials": len(getattr(study, "trials", []) or []),
            "holdout_top_k": top_k,
        })
        return {
            "promote": False,
            "status": "REJECTED_ON_HOLDOUT",
            "is_rejection_detail_override": "HOLDOUT_NO_ELIGIBLE_TRIALS",
            "symbol_params": {},
            "R_symbol": 0.0,
            "R_global": R_global,
            "promotion_margin": promotion_margin,
            "holdout_passed": False,
            "trial_dir": None,
            "metrics_symbol": {},
            "metrics_global": _metrics_dict(m_global),
        }

    best_trials = eligible_trials[:top_k]

    # 2. Deflations-Vorfilter (Issue #576/#592) — auf der REWARD-Skala (dem tatsächlichen
    # Selektionskriterium argmax(reward)), NICHT auf dem geklemmten Sortino. Der frühere
    # 50.0-Sentinel-Filter (hartcodierte Zahl, die seit der Clip-Änderung #588 ins Leere
    # griff) ist ersatzlos entfallen. baseline = median(rewards) (die Reward-Skala ist nicht
    # nullzentriert). Ein Numerik-Guard-Trial (#588) hat value=None ⇒ fällt komplett aus der Kohorte.
    # Issue #611/#618 — Deflated Sortino Ratio (DSR) statt Reward-Skalen-Deflation. Die alte Kohorte
    # (ALLE oos_evaluated-Trials) war eine Zwei-Punkt-Mischung (Failure-Masse ≈ −13 + eligible Modus ≈ +2);
    # ihr σ = pstdev(bimodal) ≈ gap·√(p(1−p)) war die BERNOULLI-Standardabweichung der Gate-Passrate
    # (maximal bei p=0.5 ⇒ je besser die Strategie, desto höher die Hürde — anti-monoton), und der
    # Median war stets der Rejection-Floor. Fix #611: Kohorte = die ELIGIBLEN Trials, die tatsächlich um
    # argmax konkurrieren. Fix #618: die Statistik ist die vollständige DSR auf der PER-PERIODEN-Sortino-
    # Skala (SR₀ = √V[ŜR_trials]·E[max_N], DSR = PSR relativ zu SR₀), nicht die Reward-Skala.
    deflated_selection = bool(tournament_cfg.get("deflated_selection", False))
    deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))
    deflation_sr0 = deflation_dsr = None
    deflation_n = 0
    deflation_var = None

    if deflated_selection:
        cohort_sr = [
            t.user_attrs.get("oos_sortino_period") for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
            and t.user_attrs.get("oos_eligible")
            and t.user_attrs.get("oos_sortino_period") is not None
        ]
        deflation_n = len(cohort_sr)
        if deflation_n >= 2:
            import statistics as _st
            from automation.optimizer.deflation import sr0_multiple_testing
            deflation_var = _st.pvariance([float(s) for s in cohort_sr])
            deflation_sr0 = sr0_multiple_testing(deflation_var, deflation_n)
            logging.getLogger("optimizer").info(
                f"[DSR #618] {symbol}: SR₀={deflation_sr0:.4f} "
                f"(N_eligible={deflation_n}, V[ŜR]={deflation_var:.6f})"
            )

    # Evaluiere den Holdout ueber die Top-k Trials
    holdout_metrics_list = []

    for trial in best_trials:
        symbol_params = trial.user_attrs.get("sampled_params", trial.params)
        m_symbol = _holdout_metrics_for_params(strategy, symbol, symbol_params,
                                               run_backtest=run_backtest, build_trial=build_trial,
                                               catalog_newest_ns=catalog_newest_ns)
        holdout_metrics_list.append((trial, symbol_params, m_symbol))

    # Issue #594/#615 — KOHÄRENTE Promotion aus EINEM Lauf. Der Lauf mit dem MEDIANEN Rang (nach
    # oos_total_return, dokumentierte Tie-Break-Regel via _median_rank_index) liefert seinen
    # VOLLSTÄNDIGEN, kohärenten Metrikvektor UND wird VOLLSTÄNDIG promotet: Params, Gate, R_symbol und
    # der Deflations-Check stammen ALLE aus DIESEM einen trial_dir. #615-Verbindlichkeit: ein gemischter
    # Vektor (Params von Trial Y, Gate/Reward von Trial X) ist unzulässig — er exportierte nie-validierte
    # Parameter (Params[Y]), während Gate-Entscheidung und R_symbol von Trial X stammten. Der Median-Rang
    # (nicht das argmax) ist die bewusste Robustheits-Wahl (#576/#594): er filtert Holdout-Glück doppelt.
    median_idx = _median_rank_index([m.oos_total_return for _, _, m in holdout_metrics_list])
    promoted_trial, promoted_symbol_params, promoted_m_symbol = holdout_metrics_list[median_idx]
    # Issue #615 — der EINE trial_dir, aus dem der gesamte promotete Vektor stammt (Invarianten-Beleg).
    promoted_trial_dir = getattr(promoted_m_symbol, "holdout_trial_dir", None)
    if not isinstance(promoted_trial_dir, str):
        promoted_trial_dir = None

    # Issue #594 — Holdout-Reward über DENSELBEN Codepfad (compute_reward) mit holdout=True: die
    # IS-abhängigen Terme werden ABGESCHALTET (kein 0.0-Platzhalter, der bei negativem base eine
    # fiktive Overfit-Strafe von 0.5·|base| erzeugte). promoted_m_symbol ist ein REALER Lauf.
    R_symbol = (compute_reward(promoted_m_symbol, universe_size=1, weights=global_weights, holdout=True)
                if global_weights else compute_reward(promoted_m_symbol, universe_size=1, holdout=True))

    # Gate-Evaluation ZWINGEND über denselben promoteten (Median-Rang-)Holdout-Vektor.
    holdout_passed = _holdout_gate_passed(
        promoted_m_symbol, risk_dd_cap,
        sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
    )

    # Issue #618/#615 — DSR-Vorfilter KOHÄRENT auf dem PROMOTETEN (Median-Rang-)Trial: dessen
    # per-Perioden-Holdout-Sortino muss die Multiple-Testing-Schwelle SR₀ mit Konfidenz
    # deflation_confidence schlagen (DSR ≥ conf), sonst HOLD. Ein undefinierter promoteter Sortino
    # (None) ⇒ fail-safe Drop.
    if holdout_passed and deflated_selection and deflation_n >= 2:
        from automation.optimizer.deflation import deflated_sharpe_ratio
        deflation_dsr = deflated_sharpe_ratio(
            getattr(promoted_m_symbol, "oos_sortino_period", None),
            getattr(promoted_m_symbol, "oos_n_periods", 0),
            var_sr_trials=deflation_var, n_trials=deflation_n,
            skew=getattr(promoted_m_symbol, "oos_ret_skew", 0.0),
            kurtosis=getattr(promoted_m_symbol, "oos_ret_kurtosis", 3.0))
        if deflation_dsr is None or deflation_dsr < deflation_confidence:
            holdout_passed = False
            logging.getLogger("optimizer").warning(
                f"[DSR-Drop #618] {symbol}: DSR={deflation_dsr} < {deflation_confidence} "
                f"(SR₀={deflation_sr0:.4f}, N={deflation_n}) ⇒ HOLD"
            )

    # Issue #619 — Stationary-Bootstrap-CI (opt-in) auf dem promoteten Holdout-Sortino: die UNTERE
    # CI-Grenze muss > 0 sein (ci_lower(sortino) > 0), nicht nur der Punktschätzer. Fehlen genug
    # Returns (< 5) ⇒ kein Zusatz-Veto (das Punkt-Gate bleibt maßgeblich).
    if holdout_passed and bool(tournament_cfg.get("holdout_bootstrap_ci", False)):
        ci_ok, ci_lo = _holdout_bootstrap_ci_passes(promoted_m_symbol, confidence=deflation_confidence)
        if not ci_ok:
            holdout_passed = False
            logging.getLogger("optimizer").warning(
                f"[Bootstrap-CI #619] {symbol}: ci_lower(sortino)={ci_lo} ≤ 0 ⇒ HOLD"
            )

    # Issue #619 — Sweep-Level-PBO (Selektions-Overfit): PBO > 0.5 ⇒ der IS-Gewinner ist OOS schlechter
    # als der Median ⇒ Promotion ist per Definition Selektions-Overfit ⇒ HARD-STOP.
    study_pbo = _study_pbo(study)
    pbo_overfit = bool(study_pbo is not None and study_pbo > 0.5)
    if pbo_overfit:
        logging.getLogger("optimizer").warning(
            f"[PBO #619] {symbol}: PBO={study_pbo:.3f} > 0.5 ⇒ REJECTED_SELECTION_OVERFIT"
        )

    # Issue #622 — Randlösungs-Veto: klebt der Gewinner an > 30 % der Suchraumgrenzen, ist die Lösung
    # keine Lösung (Bounds falsch ODER der Reward drückt in die Ecke) ⇒ FAIL-LOUD, kein READY_FOR_PR
    # (vorher nur eine folgenlose #597-WARNING). Lazy-Import (run_optimization importiert confirm).
    boundary_frac = None
    try:
        from automation.optimizer.run_optimization import _boundary_hit_fraction
        boundary_frac = _boundary_hit_fraction(study, strategy)
    except Exception:
        boundary_frac = None
    boundary_overfit = bool(boundary_frac is not None and boundary_frac > 0.3)
    if boundary_overfit:
        logging.getLogger("optimizer").warning(
            f"[Boundary #622] {symbol}: boundary_hit_fraction={boundary_frac:.2f} > 0.3 ⇒ "
            f"REJECTED_BOUNDARY_SOLUTION (Bounds prüfen ODER Reward-Konditionierung)"
        )

    promote = bool(holdout_passed and not pbo_overfit and not boundary_overfit
                   and (R_symbol > R_global + promotion_margin))

    if pbo_overfit:
        status = "REJECTED_SELECTION_OVERFIT"
    elif boundary_overfit:
        status = "REJECTED_BOUNDARY_SOLUTION"
    elif not holdout_passed:
        status = "REJECTED_ON_HOLDOUT"
    elif promote:
        status = "READY_FOR_PR"
    else:
        status = "REJECTED_NO_EDGE_OVER_GLOBAL"

    best_result = {
        "promote": promote,
        "status": status,
        "is_rejection_detail_override": None,
        # Issue #615 — Params, R_symbol, holdout_passed und trial_dir stammen ALLE aus promoted_m_symbol.
        "symbol_params": promoted_symbol_params,
        "R_symbol": R_symbol,
        "R_global": R_global,
        "promotion_margin": promotion_margin,
        "holdout_passed": bool(holdout_passed),
        "trial_dir": promoted_trial_dir,
        "metrics_symbol": _metrics_dict(promoted_m_symbol),
        "metrics_global": _metrics_dict(m_global)
    }

    # Issue #619 — PBO-Telemetrie (Selektions-Overfit-Diagnose).
    if study_pbo is not None:
        best_result["metrics_symbol"]["pbo"] = study_pbo
    # Issue #611/#618 — DSR-Telemetrie (Sortino-Skala) statt der alten Reward-Schwelle.
    if deflation_sr0 is not None:
        best_result["metrics_symbol"]["deflated_sr0"] = deflation_sr0
        best_result["metrics_symbol"]["deflated_dsr"] = deflation_dsr
        best_result["metrics_symbol"]["deflation_n_eligible"] = deflation_n

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
        # Issue #615 — der EINE Holdout-trial_dir, aus dem der promotete Vektor (Params/R_symbol/Gate)
        # stammt: macht die Kohärenz-Invariante im Proposal nachvollziehbar.
        "holdout_trial_dir": promotion.get("trial_dir"),
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
