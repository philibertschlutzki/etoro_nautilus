import json
import os
import logging
import warnings
import optuna
from functools import partial
from pathlib import Path

# Issue #402: Optuna wirft pro Sampler-Instanziierung ExperimentalWarnings fuer die bewusst
# genutzten TPESampler-Features `multivariate`/`group`. In einem Sweep ueber viele Symbole
# spammt das den Terminal zu. Gezielt NUR diese Warn-Kategorie unterdruecken — Optunas native
# Per-Trial-INFO-Logs (Reward-Werte; im Sweep via make_symbol_objective die einzige Per-Trial-
# Rueckmeldung, vgl. Issue #401) bleiben bewusst erhalten (KEIN globales set_verbosity(ERROR),
# um die Observability aus Issue #403 nicht zu untergraben).
warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
from automation.optimizer.manifest import WORK, catalog_fingerprint
from automation.optimizer.spaces import sample_params
from automation.optimizer.trial_config import build_trial, config_dir
from automation.optimizer.runner import run_backtest, BacktestRunError
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import compute_reward
from automation.optimizer.confirm import confirm_on_holdout, export_proposal, export_no_viable_proposal
from automation.log_manager import emit_execution_event

STORAGE = f"sqlite:///{WORK / 'studies.db'}"


def log_active_config(context: str, *, base_cfg: Path | None = None, extra: dict | None = None) -> None:
    """Issue #403 — strukturierter Startup-Header: legt offen, AUS WELCHEN Dateien die aktiven
    Konfigurationen stammen und welche Kern-Schwellen gelten, BEVOR der Lauf in die (bei
    ``capture_output=True`` stummen) iterativen Optuna-Trials uebergeht. Reines stdout
    (Operator-Terminal), defensiv gegen fehlende/kaputte JSONs. Gemeinsam genutzt von der
    globalen Optimierung (``run``) und dem Per-Symbol-Sweep (``sweep.main``)."""
    if base_cfg is None:
        base_cfg = config_dir()

    def _safe(p: Path) -> dict:
        try:
            return json.loads(p.read_text("utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return {}

    opt = _safe(base_cfg / "optimizer.json")
    tour = _safe(base_cfg / "tournament.json")
    print("=" * 60)
    print(f"⚙️  Aktive Konfiguration ({context})")
    print(f"   Verzeichnis        : {base_cfg}")
    for name in ("optimizer.json", "tournament.json", "strategies.json", "backtest.json"):
        p = base_cfg / name
        print(f"   {name:<18}: {p}{'' if p.exists() else '  (FEHLT)'}")
    print(f"   Schwellen (opt)    : n_trials={opt.get('n_trials')}, n_startup_trials={opt.get('n_startup_trials')}, "
          f"seed={opt.get('seed')}, oos_sortino_fallback={opt.get('oos_sortino_fallback')}")
    print(f"   Schwellen (tourn.) : oos_min_trades={tour.get('oos_min_trades')}, "
          f"sortino_min_trades={tour.get('sortino_min_trades')}, max_drawdown={tour.get('max_drawdown')}")
    if extra:
        for k, v in extra.items():
            print(f"   {str(k):<18}: {v}")
    print("=" * 60)

def floor_plateau_callback(study, trial, *, weights: dict | None = None,
                           n_startup_trials: int | None = None, eps: float = 1e-6,
                           logger: logging.Logger | None = None) -> None:
    """Issue #409 (P2) — Fail-Loud-Guard gegen den Unevaluable-Floor-Kollaps (Pitfall #75).

    Optuna-Callback (Signatur ``(study, trial)``; ``weights``/``n_startup_trials``/``logger`` sind
    rein fuer Tests/DI und werden in Produktion aus der Config gebunden). Sobald nach
    ``n_startup_trials`` ALLE abgeschlossenen Trials am Unevaluable-Floor kleben
    (``abs(value - floor) < eps``), wird EINMAL pro Study laut gewarnt — statt den Sweep als teuren
    Zufallsgenerator (Zero-Gradient, ``Best is trial 0`` bewegt sich nie) weiterlaufen zu lassen.

    Der Floor wird aus der Config abgeleitet: ``penalty_unevaluable_oos + unevaluable_shaping_span``
    (die saturierte Obergrenze des Unevaluable-Zweigs, exakt die −9.75-Signatur). Warnung ist reine
    Observability — sie aendert NIE eine Reward-/Promotion-Entscheidung."""
    if weights is None:
        cfg_path = config_dir() / "optimizer.json"
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                weights = json.load(f) or {}
        except (OSError, ValueError):
            weights = {}
    if n_startup_trials is None:
        n_startup_trials = int(weights.get("n_startup_trials", 16))
    if logger is None:
        logger = logging.getLogger("optimizer")

    if "penalty_unevaluable_oos" not in weights or "unevaluable_shaping_span" not in weights:
        return
    floor = weights["penalty_unevaluable_oos"] + weights["unevaluable_shaping_span"]

    completed = [t for t in study.trials
                 if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None]
    if len(completed) < max(1, int(n_startup_trials)):
        return
    if study.user_attrs.get("floor_plateau_warned"):
        return
    if all(abs(t.value - floor) < eps for t in completed):
        study.set_user_attr("floor_plateau_warned", True)
        logger.warning(
            "🚨 Floor-Plateau erkannt: alle %d abgeschlossenen Trials kleben am Unevaluable-Floor "
            "(%.4f). Der TPE-Sampler hat keinen Gradienten — das Symbol ist vermutlich strukturell "
            "unevaluable (Pitfall #75: OOS erzeugt nie evaluierbare Trades). Pruefe Daten-Suffizienz "
            "und Gates; verwirf ggf. die stale Study (rm data/optimizer/sweep/*.db).",
            len(completed), floor,
        )


def _check_reward_semantics_version(study, opt_data: dict,
                                    logger: logging.Logger | None = None) -> None:
    """Issue #410 (P3) — Reward-Semantik-Versionierung & Study-Hygiene.

    Die Fixes #404–#407 aendern die Reward-Semantik des Per-Symbol-Pfads. Reward-Werte
    verschiedener Semantik-Versionen sind NICHT vergleichbar: eine geladene Study, die noch alte
    Floor-Trials (Pitfall #75, konstanter −9.75) enthaelt, wuerde diese mit neuen, gradientenreichen
    Trials mischen und den TPE-Sampler verwirren.

    Frische Studies (keine Trials) werden mit ``optimizer.json['reward_semantics_version']``
    gestempelt. Traegt eine geladene Study eine andere (oder gar keine) Version, obwohl sie bereits
    Trials akkumuliert hat, wird laut gewarnt mit dem Hinweis, die stale DB zu loeschen. Fehlt der
    Config-Key, ist die Pruefung ein No-Op (Rueckwaerts-Kompat). Reine Hygiene/Observability —
    veraendert keine Reward-/Promotion-Entscheidung."""
    if logger is None:
        logger = logging.getLogger("optimizer")
    current = opt_data.get("reward_semantics_version")
    if current is None:
        return  # Versionierung nicht konfiguriert -> No-Op

    existing = study.user_attrs.get("reward_semantics_version")
    has_trials = len(study.trials) > 0

    if existing == current:
        return
    if existing is None and not has_trials:
        study.set_user_attr("reward_semantics_version", current)
        return

    logger.warning(
        "♻️ Reward-Semantik-Versionskonflikt: die geladene Study wurde unter Version %s "
        "akkumuliert, aktuell ist Version %s (Fixes #404–#410). Reward-Werte verschiedener Versionen "
        "sind NICHT vergleichbar — alte Floor-Trials (Pitfall #75, −9.75) wuerden den TPE-Sampler "
        "verfaelschen. Loesche die stale Study und starte neu: rm data/optimizer/sweep/*.db "
        "(globaler Lauf: studies.db).",
        existing if existing is not None else "unversioniert", current,
    )


def make_objective(
    strategy: str,
    *,
    run_backtest=run_backtest,
    build_trial=build_trial,
    parse_tournament=parse_tournament,
    compute_reward=compute_reward
):
    def objective(trial):
        sampled = sample_params(strategy, trial)
        trial.set_user_attr("sampled_params", sampled)

        cfg_dir = config_dir()
        optimizer_path = cfg_dir / "optimizer.json"
        seed = 42
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                opt_data = json.load(f)
                seed = opt_data.get("seed", 42)

        trial_dir, manifest_path = build_trial(
            strategy_class=strategy,
            sampled=sampled,
            study_name=trial.study.study_name,
            trial_number=trial.number,
            seed=seed,
            n_folds=4,
            holdout_days=45
        )

        try:
            output_path = run_backtest(trial_dir, manifest_path)
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                t_data = json.load(f)
                risk_dd_cap = t_data.get("max_drawdown", 0.30)

        universe_path = config_dir().parent.parent / "data" / "universe" / "momentum_ls.json"
        universe_size = 70
        if universe_path.exists():
            with open(universe_path, "r", encoding="utf-8") as f:
                u_data = json.load(f)
                universe_size = len(u_data.get("universe", []))

        reward = compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap)

        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        import logging
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "trial_number": trial.number,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_total_trades": metrics.oos_total_trades,
            "fully_eligible_pairs": metrics.fully_eligible_pairs,
            "win_count": metrics.win_count,
            "is_total_trades": metrics.is_total_trades,
            "is_max_trades": metrics.is_max_trades,
            "outcome": outcome
        })

        return reward
    return objective

def optimize(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    WORK.mkdir(parents=True, exist_ok=True)
    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"

    # Default values
    conf_n_trials = 100
    n_startup_trials = 16
    seed = 42
    opt_data: dict = {}

    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f) or {}
            conf_n_trials = opt_data.get("n_trials", conf_n_trials)
            n_startup_trials = opt_data.get("n_startup_trials", n_startup_trials)
            seed = opt_data.get("seed", seed)

    if n_trials is None:
        n_trials = conf_n_trials

    study_name = f"study_{strategy}"

    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        group=True,
        n_startup_trials=n_startup_trials,
        seed=seed
    )

    study = optuna.create_study(
        study_name=study_name,
        storage=STORAGE,
        direction="maximize",
        sampler=sampler,
        load_if_exists=True
    )

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)

    # Issue #409 — Fail-Loud-Guard auch im globalen Pfad (gleicher Floor-Kollaps moeglich).
    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials)
    study.optimize(
        make_objective(strategy),
        n_trials=n_trials,
        n_jobs=n_jobs,
        catch=(json.JSONDecodeError, OSError),
        callbacks=[floor_guard]
    )
    return study

def _sanitize(symbol: str) -> str:
    """'TSLA.ETORO' → 'TSLA_ETORO' (dateinamenstauglicher Study-/DB-Name)."""
    return symbol.replace(".", "_")


def resolve_storage(*, study_name: str, base_cfg: Path | None = None) -> str:
    """Storage-URL-Auflösung (A4.7, optional). Priorität:
       ENV `ETORO_OPTUNA_STORAGE` > optimizer.json['storage_url'] (falls nicht null)
       > f'sqlite:///{WORK}/sweep/{study_name}.db' (Default).

    **SQLite bleibt strikter Default**; Postgres o. ä. ist reines Opt-In für echte
    Multi-Maschinen-Parallelität (mehrere Hosts gegen *eine* Study) und weicht die
    „ausschließlich SQLite"-Leitplanke (Pitfall #53) bewusst, dokumentiert und begrenzt auf.
    Bei einer non-SQLite-URL wird eine Warnung geloggt (Determinismus pro Study nur bei
    n_jobs=1). Eine via ENV übergebene URL wird **verbatim** genutzt (Fail-Fast: ungültige
    URIs scheitern beim `create_study`-Connect, statt still auf SQLite zurückzufallen)."""
    env_url = os.environ.get("ETORO_OPTUNA_STORAGE")
    if env_url:
        url = env_url
    else:
        if base_cfg is None:
            base_cfg = config_dir()
        url = None
        optimizer_path = base_cfg / "optimizer.json"
        if optimizer_path.exists():
            try:
                with open(optimizer_path, "r", encoding="utf-8") as f:
                    url = (json.load(f) or {}).get("storage_url")
            except (OSError, ValueError):
                url = None
        if not url:
            url = f"sqlite:///{WORK / 'sweep' / (study_name + '.db')}"

    if not url.startswith("sqlite"):
        logging.getLogger("optimizer").warning(
            "Non-SQLite Optuna-Storage '%s' — Determinismus pro Study nur bei n_jobs=1 "
            "garantiert; parallele Writes lockern die SQLite-Leitplanke (Pitfall #53) bewusst auf.",
            url,
        )
    return url


def load_global_best(strategy: str, base_cfg: Path) -> dict:
    """Quelle des globalen Optimums (Warm-Start-Samen, Gate 2):
       proposal_{strategy}.json['proposed_params_override'] falls vorhanden UND status
       'READY_FOR_PR', sonst strategies.json[strategy].params, sonst {} (None-safe).

    Bewusste Entscheidung (A4.5a Rückfrage): Ein Proposal mit status != READY_FOR_PR
    (z. B. REJECTED_ON_HOLDOUT) wird NICHT als Samen genutzt — Fallback auf strategies.json.
    """
    proposal_path = WORK / f"proposal_{strategy}.json"
    if proposal_path.exists():
        try:
            with open(proposal_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if data.get("status") == "READY_FOR_PR":
                override = data.get("proposed_params_override") or {}
                if override:
                    return dict(override)
        except (OSError, ValueError):
            pass

    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        try:
            with open(strats_path, "r", encoding="utf-8") as f:
                strats = json.load(f) or {}
            for s in strats.get("strategies", []):
                if s.get("strategy_class") == strategy:
                    return dict(s.get("params") or {})
        except (OSError, ValueError):
            pass

    return {}


def _classify_trial_rejection(metrics) -> str:
    """Issue #408 — kategorisiert, WARUM ein Per-Symbol-Trial nicht promotebar ist, fuer die modale
    Aggregation im Proposal (confirm._dominant_rejection). Trennt den IS-Drop ('oos_not_evaluated':
    das Symbol erzeugte nie evaluierbare OOS-Trades — die Pitfall-#75-Signatur) vom OOS-Drop
    ('oos_gate_rejected': OOS evaluiert, aber durchs Eligibility-Gate gefallen) und vom Pass
    ('none'). Bewusst grob & stabil, damit die Reasons ueber Trials hinweg aggregierbar bleiben."""
    if metrics.oos_evaluated and metrics.oos_eligible:
        return "none"
    if not metrics.oos_evaluated:
        return "oos_not_evaluated"
    return "oos_gate_rejected"


def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
                          *, run_backtest=run_backtest, build_trial=build_trial):
    """Wie make_objective, aber single-symbol: build_trial(instruments=[symbol]) und
       compute_reward(universe_size=1, sampled, global_params, strategy) (Per-Symbol-Reward
       mit param_pen Richtung global_params, A4.3)."""
    def objective(trial):
        sampled = sample_params(strategy, trial)
        trial.set_user_attr("sampled_params", sampled)

        cfg_dir = config_dir()
        seed = 42
        optimizer_path = cfg_dir / "optimizer.json"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                seed = (json.load(f) or {}).get("seed", 42)

        trial_dir, manifest_path = build_trial(
            strategy_class=strategy,
            sampled=sampled,
            study_name=trial.study.study_name,
            trial_number=trial.number,
            seed=seed,
            n_folds=4,
            holdout_days=45,
            instruments=[symbol],
        )

        try:
            output_path = run_backtest(trial_dir, manifest_path)
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                risk_dd_cap = (json.load(f) or {}).get("max_drawdown", 0.30)

        reward = compute_reward(metrics, universe_size=1, risk_dd_cap=risk_dd_cap,
                                sampled=sampled, global_params=global_params, strategy=strategy)

        # Issue #404 (P0) — Per-Symbol-Telemetrie. Der Sweep emittierte bislang KEIN strukturiertes
        # Per-Trial-Event (nur Optunas native INFO-Zeile, vgl. #402), wodurch der Unevaluable-Floor-
        # Kollaps (Pitfall #75) forensisch unsichtbar blieb. `oos_eligible` trennt den IS-Drop
        # (Symbol nie IS-eligible ⇒ kein OOS evaluiert) vom OOS-Drop (OOS evaluiert, aber durchs
        # Gate gefallen); `is_total_trades`/`is_max_trades` machen die Shaping-Saettigung sichtbar.
        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        # Issue #408 — modale Gate-Drop-Reason: pro Trial die kategorisierte Rejection-Reason
        # persistieren, damit confirm._dominant_rejection sie ueber die Study aggregieren kann.
        rejection_reason = _classify_trial_rejection(metrics)
        trial.set_user_attr("rejection_reason", rejection_reason)
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "symbol": symbol,
            "trial_number": trial.number,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
            "oos_total_trades": metrics.oos_total_trades,
            "oos_total_return": metrics.oos_total_return,
            "is_total_trades": metrics.is_total_trades,
            "is_max_trades": metrics.is_max_trades,
            "outcome": outcome,
        })
        return reward
    return objective


def optimize_symbol(strategy: str, symbol: str, n_trials: int | None = None,
                    *, storage: str | None = None):
    """Single-Symbol-Variante von `optimize`: eigene benannte SQLite-Study unter
       {WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db, Manifest mit instruments=[symbol]
       (universe_size==1 ⇒ Per-Symbol-Reward), Warm-Start am globalen Optimum (Gate 2 via
       study.enqueue_trial). n_jobs=1 wird erzwungen (SQLite-Reproduzierbarkeit, Pitfall #68).
       Das globale `optimize`/`make_objective` bleibt unverändert."""
    cfg_dir = config_dir()
    conf_n_trials, n_startup_trials, seed = 100, 16, 42
    opt_data: dict = {}
    optimizer_path = cfg_dir / "optimizer.json"
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f) or {}
            conf_n_trials = opt_data.get("n_trials", conf_n_trials)
            n_startup_trials = opt_data.get("n_startup_trials", n_startup_trials)
            seed = opt_data.get("seed", seed)
    if n_trials is None:
        n_trials = conf_n_trials

    study_name = f"study_{strategy}_{_sanitize(symbol)}"
    sweep_dir = WORK / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    if storage is None:
        storage = resolve_storage(study_name=study_name)   # A4.7: SQLite-Default, ENV/JSON-Opt-in

    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        group=True,
        n_startup_trials=n_startup_trials,
        seed=seed,
    )

    study = optuna.create_study(
        study_name=study_name,
        storage=storage,
        direction="maximize",
        sampler=sampler,
        load_if_exists=True,
    )

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)

    # Gate 2 — Warm-Start am globalen Optimum (nur wenn nicht leer).
    global_best = load_global_best(strategy, cfg_dir)
    if global_best:
        study.enqueue_trial(global_best)

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    objective = make_symbol_objective(
        strategy, symbol, global_best,
        run_backtest=run_backtest, build_trial=build_trial,
    )
    # Issue #409 — Fail-Loud-Guard: warnt, sobald nach n_startup_trials alle Trials am
    # Unevaluable-Floor kleben (Pitfall #75). Config einmalig gebunden (kein Per-Trial-IO).
    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials)
    study.optimize(objective, n_trials=n_trials, n_jobs=1,
                   catch=(json.JSONDecodeError, OSError), callbacks=[floor_guard])
    return study


def run(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    log_active_config(f"global optimize · {strategy}", extra={"n_jobs": n_jobs, "n_trials_override": n_trials})
    study = optimize(strategy, n_trials=n_trials, n_jobs=n_jobs)
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if not completed:
        export_no_viable_proposal(study, strategy)
        return
    holdout_res = confirm_on_holdout(study, strategy)
    export_proposal(study, strategy, holdout_res)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Hyperparameter Optimization")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy class name to optimize")
    parser.add_argument("--n-trials", type=int, default=None, help="Number of trials (overrides config)")
    parser.add_argument("--n-jobs", type=int, default=1, help="Number of parallel worker jobs")

    args = parser.parse_args()
    run(strategy=args.strategy, n_trials=args.n_trials, n_jobs=args.n_jobs)
