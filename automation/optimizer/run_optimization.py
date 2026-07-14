import json
import math
import os
import logging
import sqlite3
import statistics
import threading
import time
import warnings
import optuna
import sqlalchemy.exc
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

# Issue #411 — Optuna/SQLite `create_all`-DDL-Race. `RDBStorage.__init__` ruft
# `models.BaseModel.metadata.create_all(self.engine)` (check-then-create, TOCTOU). Zwei Worker, die
# dieselbe FRISCHE SQLite-Datei quasi-gleichzeitig oeffnen, setzen beide `CREATE TABLE studies` ab —
# der zweite crasht mit `table studies already exists`. `load_if_exists=True` schuetzt NICHT (greift
# erst auf Study-Row-Ebene, NACH dem Schema-Bootstrap). Ein prozessweiter Lock serialisiert den
# `create_study`-Aufruf; der Schema-Check dauert nur Millisekunden ⇒ kein relevanter Durchsatz-
# Verlust, aber die `create_all`-Kollision ist ausgeschlossen.
_study_lock = threading.Lock()


def _create_study_with_retry(*, study_name: str, storage: str, sampler=None,
                             direction: str | None = "maximize", directions: list[str] | None = None):
    """Issue #411 — `optuna.create_study` serialisiert (``_study_lock``) und gegen die DDL-Race-
    Exception ``table studies already exists`` GENAU EINMAL retried.

    Kein blindes ``except Exception`` (Fail-Fast, Pitfall #66): ausschliesslich die exakte
    Race-Signatur (``"already exists"`` in einer ``sqlite3``/``sqlalchemy`` ``OperationalError``)
    wird abgefangen; jeder andere Fehler propagiert hart. Beim Retry existiert das Schema garantiert
    ⇒ ``load_if_exists=True`` laedt die Study sauber."""
    def _create():
        kwargs = dict(study_name=study_name, storage=storage, load_if_exists=True)
        if directions is not None:
            kwargs["directions"] = directions
        elif direction is not None:
            kwargs["direction"] = direction
        if sampler is not None:
            kwargs["sampler"] = sampler
        return optuna.create_study(**kwargs)

    with _study_lock:
        try:
            return _create()
        except (sqlite3.OperationalError, sqlalchemy.exc.OperationalError) as e:
            if "already exists" not in str(e):
                raise  # kein Schema-Race → propagieren (Fail-Fast)
            # Schema-Race verloren → Schema existiert jetzt sicher → erneut laden.
            return _create()


def _preinit_study_storage(study_name: str, *, base_cfg: Path | None = None) -> None:
    """Issue #411 — erzwingt den RDBStorage-Schema-Bootstrap (``create_all``) EINMAL und seriell im
    aufrufenden (Haupt-)Thread, BEVOR mehrere Worker dieselbe (frische) Study-Datei oeffnen. Damit
    trifft jeder nachfolgende Worker garantiert den ``exists``-Pfad (kein DDL-Race). Idempotent:
    laeuft das Schema/die Study schon, ist es ein No-Op (``load_if_exists``). Aufzurufen pro
    EINDEUTIGEM ``study_name`` (Eindeutigkeit ist Vorbedingung, vgl. #412/Pitfall #77)."""
    storage = resolve_storage(study_name=study_name, base_cfg=base_cfg)
    # resolve_storage liefert nur die URL; das per-Study-SQLite-Verzeichnis muss existieren.
    if storage.startswith("sqlite:///"):
        Path(storage[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    _create_study_with_retry(study_name=study_name, storage=storage)


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

def _stop_study_safely(study, logger: logging.Logger) -> None:
    """Issue #456 — ``study.stop()`` crash-sicher aufrufen. Eine Study AUSSERHALB eines aktiven
    ``optimize()``-Kontexts (oder ein Test-Double ohne ``stop``) darf nicht zum Crash führen — die
    bereits emittierte Plateau-Warnung genügt dann. Idempotent über ``getattr`` + ``try/except``."""
    stop = getattr(study, "stop", None)
    if stop is None:
        return
    try:
        stop()
    except Exception:  # pragma: no cover - defensiver Schutz außerhalb optimize()
        logger.debug("study.stop() außerhalb eines optimize()-Kontexts ignoriert (Issue #456).")


def floor_plateau_callback(study, trial, *, weights: dict | None = None,
                           n_startup_trials: int | None = None, eps: float = 1e-6,
                           logger: logging.Logger | None = None,
                           stop_on_plateau: bool = False) -> None:
    """Issue #409/#413/#456 (P1/P2) — Guard gegen den Unevaluable-Floor-Kollaps (Pitfall #75).

    Optuna-Callback (Signatur ``(study, trial)``; ``weights``/``n_startup_trials``/``logger``/
    ``stop_on_plateau`` sind rein fuer Tests/DI und werden in Produktion aus der Config gebunden).
    Warnt EINMAL pro Study, sobald der Sweep fuer ein Symbol nichts Promotbares erzeugt — statt ihn
    als teuren Zufallsgenerator (Zero-Gradient) weiterlaufen zu lassen.

    Issue #456 — ``stop_on_plateau`` (Opt-in, Default ``False``): Ist es ``True`` und ein Plateau
    erkannt, ruft der Guard ZUSAETZLICH ``study.stop()`` (in BEIDEN Zweigen, crash-sicher), sodass
    die als aussichtslos erkannte Suche nicht die restlichen ~84 Trials verschwendet (~30 min pro
    Floor-Symbol). Die **Produktion bindet ``True``**; der Default ``False`` haelt alle Bestands-
    Tests mit Fake-Study (ohne ``.stop()``) unveraendert gruen. **Observability-Invariante bleibt:**
    der Guard aendert weiterhin NIE eine Reward- oder Promotion-Entscheidung — er beendet lediglich
    eine bereits als aussichtslos erkannte Suche frueher.

    Issue #413 — PRIMAERER Indikator ist ``oos_evaluated`` (Per-Trial-User-Attr aus
    ``make_symbol_objective``), NICHT die Reward-Wert-Gleichheit: Da #406/#407 unevaluable Trials
    ABSICHTLICH unter −9.75 verteilen (Gradient), ist das alte Praedikat ``all(abs(value−floor)<eps)``
    im v3-Shaping-Regime strukturell unerfuellbar (toter Code, der den realen Sub-Floor-Bereich
    −9.85…−9.93 nie trifft). Der Guard feuert jetzt, wenn KEIN abgeschlossener Trial je evaluable war.
    Fehlt das User-Attr (alte Studies / globaler ``make_objective``-Pfad), greift der Legacy-Wert-Guard
    (Floor = ``penalty_unevaluable_oos + unevaluable_shaping_span``) — kein False-Positive."""
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

    # Issue #488 — Zustandslos via Persistenzschicht abfragen (Parallel-Safety)
    get_trials = getattr(study, "get_trials", None)
    if get_trials:
        completed = [t for t in get_trials(states=[optuna.trial.TrialState.COMPLETE])
                     if t.value is not None]
    else:
        # Fallback for FakeStudy in tests
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None]
    # Issue #488 — Floor-Plateau Guard Hardening: Monitor K trials strictly post n_startup_trials.
    K = int(weights.get("floor_plateau_k", 0)) if weights else 0
    if len(completed) < max(1, int(n_startup_trials)) + K:
        return
    if study.user_attrs.get("floor_plateau_warned"):
        return

    # Issue #413 — evaluable-basierter Primaer-Guard. Tragen die Trials das oos_evaluated-Attr, ist
    # „kein Trial je evaluable" der korrekte Kollaps-Indikator (unabhaengig vom geshapeten Reward-Wert).
    evaluated_flags = [getattr(t, "user_attrs", {}).get("oos_evaluated") for t in completed]
    if any(f is not None for f in evaluated_flags):
        if all(f is False for f in evaluated_flags):
            study.set_user_attr("floor_plateau_warned", True)
            logger.warning(
                "🚨 Floor-Plateau erkannt: kein evaluable Trial nach %d Trials — das Symbol erzeugt "
                "nie evaluierbare OOS-Trades (Pitfall #75-Klasse). Pruefe Daten-Suffizienz, "
                "OOS-Trade-Frequenz und Micro-Sizing-/min_trades-Gate. Per-Symbol-Tuning fuer dieses "
                "Symbol ist derzeit ein No-Op.",
                len(completed),
            )

            # Issue #456 / #488 — aussichtslose Suche frueh beenden (nur Opt-in; crash-sicher).
            should_stop = stop_on_plateau or (weights and weights.get("floor_plateau_k") is not None)
            if should_stop:
                # Log JSON termination event explicitly exactly when stopping (only once).
                # Wait, study.set_user_attr("floor_plateau_warned", True) ensures this block runs ONCE.
                import json as _json
                logger.info("[JSON_EVENT] " + _json.dumps({
                    "event_type": "STUDY_EARLY_STOP",
                    "reason": "STRUCTURAL_ALL_UNEVALUABLE",
                    "current_trial": len(completed),
                    "startup_limit": max(1, int(n_startup_trials)),
                    "k_limit": K
                }))
                _stop_study_safely(study, logger)
        return

    # Legacy-Fallback (kein oos_evaluated-Attr, z. B. alte Studies / globaler make_objective-Pfad):
    # bisheriges Wert-Gleichheits-Praedikat am konstanten Unevaluable-Floor (−9.75).
    if "penalty_unevaluable_oos" not in weights or "unevaluable_shaping_span" not in weights:
        return
    floor = weights["penalty_unevaluable_oos"] + weights["unevaluable_shaping_span"]
    if all(abs(t.value - floor) < eps for t in completed):
        study.set_user_attr("floor_plateau_warned", True)
        logger.warning(
            "🚨 Floor-Plateau erkannt: alle %d abgeschlossenen Trials kleben am Unevaluable-Floor "
            "(%.4f). Der TPE-Sampler hat keinen Gradienten — das Symbol ist vermutlich strukturell "
            "unevaluable (Pitfall #75: OOS erzeugt nie evaluierbare Trades). Pruefe Daten-Suffizienz "
            "und Gates; verwirf ggf. die stale Study (rm data/optimizer/sweep/*.db).",
            len(completed), floor,
        )
        # Issue #456 — auch im Legacy-Wert-Zweig die aussichtslose Suche frueh beenden (Opt-in).
        if stop_on_plateau:
            _stop_study_safely(study, logger)


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

    # Issue #468 / #575 — Harter Fail-Loud-Mechanismus und Purge bei Posterior-Korruption.
    msg = (f"Reward-Semantik-Versionskonflikt: die geladene Study wurde unter Version {existing if existing is not None else 'unversioniert'} "
           f"akkumuliert, aktuell ist Version {current}. Reward-Werte verschiedener Versionen "
           f"sind NICHT vergleichbar. Dies führt zu Posterior-Korruption im TPE-Sampler. "
           f"Initiere Purge der obsoleten Study-Datenbank...")

    if has_trials:
        if existing is None or existing < current:
            logger.warning("♻️ %s", msg)
            try:
                optuna.delete_study(study_name=study.study_name, storage=study._storage)
                logger.warning(f"Obsolete Study '{study.study_name}' erfolgreich gelöscht. Sie wird beim nächsten Versuch neu erstellt.")
            except Exception as e:
                logger.error(f"Fehler beim Löschen der Study: {e}")
            # Issue #591 — fail-loud mit explizitem Fehlercode. v8 (Issues #587–#591) ist mit v7
            # inkompatibel: alte SQLite-Studies MÜSSEN gelöscht werden, kein stilles Weiterlaufen.
            raise ValueError(f"REJECT_STALE_STUDY_SEMANTICS: Study-Semantik Mismatch. {msg}")

    logger.warning("♻️ %s", msg)


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

        _t0 = time.perf_counter()
        try:
            output_path = run_backtest(trial_dir, manifest_path)
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")
        backtest_ms = round((time.perf_counter() - _t0) * 1000)  # Issue #415 — Wall-Clock

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

        reward, reward_terms = compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap, return_terms=True)
        trial.set_user_attr("reward_terms", reward_terms)

        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        import logging
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "trial_number": trial.number,
            "backtest_ms": backtest_ms,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_total_trades": metrics.oos_total_trades,
            "fully_eligible_pairs": metrics.fully_eligible_pairs,
            "win_count": metrics.win_count,
            "is_total_trades": metrics.is_total_trades,
            "hit_trade_cap": metrics.hit_trade_cap,
            "outcome": outcome,
            # Issue #455 — OOS-Abdeckungs-Telemetrie auch im globalen Pfad surfacen (BEIDE Events).
            "fill_ts_max": metrics.fill_ts_max,
            "oos_window_start_ns": metrics.oos_window_start_ns,
            "oos_covered": metrics.oos_covered,
            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
            "oos_anchor_divergence": metrics.oos_anchor_divergence,
            # Issue #569 — Roh-Kennzahlen additiv (rein observational) auch im globalen Pfad.
            "oos_total_return": metrics.oos_total_return,
            "oos_sortino": metrics.oos_sortino,
            "oos_expectancy": metrics.oos_expectancy,
            "oos_win_rate": metrics.oos_win_rate,
            "oos_profit_factor": metrics.oos_profit_factor,
            "is_sortino_median": metrics.is_sortino_median,
            "per_fold_oos_sortino": list(metrics.oos_fold_sortinos),
            # Issue #620 — #589-Kohärenz-Verletzung (sign(oos_sortino)≠sign(oos_total_return)) sichtbar.
            "oos_coherence_violation": bool(metrics.oos_coherence_violation),
            "reward_terms": reward_terms,
        })


        reward_mode = "auto"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                reward_mode = (json.load(f) or {}).get("reward_mode", "auto")
        if reward_mode == "pareto":
            min_trades = 20
            if tournament_path.exists():
                with open(tournament_path, "r", encoding="utf-8") as f:
                    min_trades = (json.load(f) or {}).get("min_trades", 20)
            trades_constraint = min_trades - metrics.oos_total_trades
            dd_constraint = metrics.oos_max_drawdown - risk_dd_cap
            trial.set_user_attr("constraints", (float(trades_constraint), float(dd_constraint)))
        # Issue #612 — Feasibility-Constraint(s) für den TPE-Sampler auch im globalen Pfad.
        trial.set_user_attr("oos_constraint_violations", _compute_oos_constraints(metrics))
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
    # Issue #568 — n_startup_trials dokumentiert an die Parameterzahl koppeln (Legacy ohne den Key).
    n_startup_trials = derive_n_startup_trials(strategy, n_startup_trials, opt_data)

    study_name = f"study_{strategy}"

    reward_mode = opt_data.get("reward_mode", "auto")
    directions = None
    if reward_mode == "pareto":
        directions = ["maximize", "maximize", "maximize", "maximize", "minimize", "minimize"]
        def constraints_func(trial):
            return trial.user_attrs.get("constraints", (0.0, 0.0))
        sampler = optuna.samplers.NSGAIISampler(constraints_func=constraints_func, seed=seed)
    else:
        # Issue #612 — FEASIBILITY GEHÖRT IN DEN SAMPLER, nicht in eine 12-Einheiten-Reward-Klippe.
        # ``constraints_func`` liest die gestempelten, normierten OOS-Gate-Verletzungen
        # (``oos_constraint_violations``, ≤ 0 = feasible). Optuna 4.9 behandelt Feasibility NATIV:
        # ``study.best_trial`` UND das TPE-Sampling bevorzugen feasible strikt vor infeasible; unter den
        # infeasiblen wird nach Gesamtverletzung sortiert. Damit optimiert der Sampler EINE stetige
        # Grösse (die risikoadjustierte OOS-Performance, nach #614 die PSR) statt einer Stufenfunktion.
        sampler = optuna.samplers.TPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed,
            constraints_func=_oos_constraints_func,
        )

    study = _create_study_with_retry(
        study_name=study_name,
        storage=STORAGE,
        direction="maximize" if not directions else None,
        directions=directions,
        sampler=sampler
    )

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)

    # Issue #409 — Fail-Loud-Guard auch im globalen Pfad (gleicher Floor-Kollaps moeglich).
    # Issue #456 — Produktion bindet stop_on_plateau=True: aussichtslose Study früh beenden.
    floor_guard = partial(floor_plateau_callback, weights=opt_data,
                          n_startup_trials=n_startup_trials, stop_on_plateau=True)
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


def load_strategy_defaults_params(strategy: str, base_cfg: Path) -> dict:
    """Issue #565 — die deklarierten Default-Parameter einer Strategie aus strategy_defaults.json
    (ohne den ``_schema``-Block). None-safe ⇒ ``{}``, wenn Datei/Strategie fehlt. Das ist der
    ökonomisch begründete Prior (genau der Vektor, der laut Holdout-Evidenz besser generalisiert
    als der ungezügelt symbol-getunte)."""
    defaults_path = base_cfg / "strategy_defaults.json"
    if defaults_path.exists():
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            params = data.get(strategy)
            if isinstance(params, dict):
                return {k: v for k, v in params.items() if not k.startswith("_")}
        except (OSError, ValueError):
            pass
    return {}


def resolve_symbol_shrinkage_seed(strategy: str, base_cfg: Path) -> tuple[dict, str]:
    """Issue #565 — Shrinkage-/Warm-Start-Referenz für den Per-Symbol-Pfad, mit definiertem
    Fallback statt Silent-Zero.

    Reihenfolge: ``load_global_best`` (Proposal READY_FOR_PR / strategies.json) → strategy_defaults →
    ``{}``. Gibt ``(seed_params, source)`` zurück, ``source ∈ {'global_best', 'strategy_defaults',
    'none'}``. Fehlt das echte globale Optimum, ist ``strategy_defaults`` der Prior, gegen den die
    A4.3-Shrinkage (``param_pen``) zieht — so wird ``param_pen`` NIE still 0 (der Kollaps, bei dem
    der symbol-getunte Vektor völlig ungezügelt Richtung IS/OOS-CV-Rausch tunt, #565)."""
    global_best = load_global_best(strategy, base_cfg)
    if global_best:
        return global_best, "global_best"
    defaults = load_strategy_defaults_params(strategy, base_cfg)
    if defaults:
        return defaults, "strategy_defaults"
    return {}, "none"


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


# Issue #453/#615 — die kanonische »kein Drop«-Kategorie (``is_rejection_detail``-User-Attr eines
# eligiblen Trials). BENANNTE Konstante statt verstreuter "NONE"-String-Literale in zwei Modulen
# (run_optimization + confirm) — genau die Divergenz, die #615 verursachte: confirm filterte auf
# ``is_rejection_detail is None`` (Python-``None``), gestempelt wurde aber der STRING "NONE" ⇒
# eligible_trials war IMMER leer ⇒ Top-k-Holdout (#576) lief nie (k=1 statt k=holdout_top_k).
IS_REJECTION_NONE = "NONE"


def _compute_oos_constraints(metrics) -> tuple:
    """Issue #612 — Feasibility-Constraint(s) für den Sampler (Optuna-Konvention: ``<= 0`` = feasible).

    Ein eligibler (feasibler) Trial ⇒ ``(0.0,)``. Sonst die SUMMIERTE positive OOS-Gate-Verletzung
    (aus ``oos_gate_deltas``, ``delta = actual − threshold`` ⇒ Verletzung ``max(0, −delta)``) — ``> 0``,
    sodass der Sampler unter den infeasiblen nach Gesamtverletzung sortiert. Erfassen die Deltas die
    Ineligibilität nicht (nicht-evaluiert, Micro-Sizing) ⇒ konstante Verletzung ``1.0``. KONSTANTE
    Länge (1) über alle Trials — Optuna verlangt einen fixen Constraint-Vektor."""
    if getattr(metrics, "oos_eligible", False):
        return (0.0,)
    deltas = getattr(metrics, "oos_gate_deltas", None) or {}
    violation = sum(max(0.0, -float(v)) for v in deltas.values())
    if not (violation > 0.0):
        violation = 1.0
    return (float(violation),)


def _oos_constraints_func(trial):
    """Issue #612 — von TPESampler/NSGAIISampler aufgerufen: liest die im Objective gestempelten
    ``oos_constraint_violations``. Fehlt der Stempel (Pruned/Legacy) ⇒ feasible ``(0.0,)``."""
    return trial.user_attrs.get("oos_constraint_violations", (0.0,))


# Issue #453 — Prefix → dezidierte Enum-Kategorie. Die Reason-Strings stammen aus
# backtest_runner._evaluate_oos_eligibility (z. B. "oos_max_drawdown: 0.5 > 0.3"); längere Prefixe
# zuerst, damit "oos_min_total_return" nicht fälschlich von "oos_min_t..." geschluckt wird.
_OOS_REASON_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("oos_min_total_return", "REJECT_OOS_MIN_TOTAL_RETURN"),
    ("oos_min_trades", "REJECT_OOS_MIN_TRADES"),
    ("oos_min_expectancy", "REJECT_OOS_MIN_EXPECTANCY"),
    ("oos_max_drawdown", "REJECT_OOS_MAX_DRAWDOWN"),
    ("oos_min_win_rate", "REJECT_OOS_MIN_WIN_RATE"),
    ("oos_min_sortino", "REJECT_OOS_MIN_SORTINO"),
    ("oos_min_profit_factor", "REJECT_OOS_MIN_PROFIT_FACTOR"),
    ("Micro-Sizing", "REJECT_OOS_MICRO_SIZING"),
    ("oos_not_evaluable", "REJECT_OOS_NOT_EVALUABLE"),
)


def _map_oos_reason(reason: str) -> str:
    """Issue #453 — eine konkrete OOS-Reason-Zeile auf ihre dezidierte Enum-Kategorie abbilden."""
    for prefix, code in _OOS_REASON_PREFIX_MAP:
        if reason.startswith(prefix):
            return code
    return "REJECT_OOS_OTHER"


def _classify_is_rejection_detail(metrics) -> str:
    """Issue #453 — granulare, aggregierbare Ablehnungs-Kategorie (feiner als _classify_trial_rejection).

    Löst den Catch-All ``oos_not_evaluated`` in die TATSÄCHLICHE Ursache auf, sodass systematisches
    Auto-Tuning (Bezug #403/#408) den DATENseitigen Drop (OOS-Fenster nicht abgedeckt, #455) vom
    STRATEGIEseitigen (abgedeckt, aber inaktiv) und vom konkreten OOS-Gate-Drop (Drawdown, Win-Rate,
    Trades …) unterscheiden kann. Rein klassifizierend — ändert KEINE Reward-/Promotion-Entscheidung.

    Kategorien:
      * ``NONE``                          — evaluiert & eligible (kein Drop).
      * ``REJECT_OOS_WINDOW_UNREACHABLE`` — OOS=0 + ``oos_covered is False`` (H2-Katalog, #455).
      * ``REJECT_OOS_INACTIVE``           — OOS=0, aber ``oos_covered is True`` (Strategie handelt
                                            im OOS-Fenster nicht — strategieseitig, separat zu lösen).
      * ``REJECT_OOS_NOT_EVALUATED``      — OOS=0, Abdeckung unbekannt (Legacy/keine #455-Telemetrie).
      * ``REJECT_OOS_<GATE>``             — evaluiert, aber durchs konkrete Eligibility-Gate gefallen.
    """
    if metrics.oos_evaluated and metrics.oos_eligible:
        return IS_REJECTION_NONE
    if not metrics.oos_evaluated:
        covered = getattr(metrics, "oos_covered", None)
        if covered is False:
            return "REJECT_OOS_WINDOW_UNREACHABLE"
        if covered is True:
            if getattr(metrics, "oos_total_trades", 0) > 0:
                return "REJECT_OOS_DISCARDED_BY_IS_GATE"
            return "REJECT_OOS_INACTIVE"
        return "REJECT_OOS_NOT_EVALUATED"
    reasons = getattr(metrics, "oos_rejection_reasons", ()) or ()
    if reasons:
        return _map_oos_reason(reasons[0])
    return "REJECT_OOS_GATE"


def derive_n_startup_trials(strategy: str, base_n_startup: int, opt_data: dict) -> int:
    """Issue #568 — ``n_startup_trials`` an die effektive Dimensionalität koppeln.

    Bei ``multivariate=True, group=True`` sollte ``n_startup_trials ≳ k·dim`` sein (``dim`` = Anzahl
    numerischer Suchraum-Parameter), damit der TPE die Kovarianzstruktur überhaupt schätzen kann;
    für Strategien mit vielen Parametern (ComboTrendVwap ~14) sind fixe 16 knapp. Deklarativ über
    ``n_startup_trials_per_dim`` (k): ``n_startup_trials = max(base, ceil(k·dim))``. Fehlt der Key
    (oder <= 0) ⇒ ``base`` (Legacy, bit-identisch, Zero-Hardcoding)."""
    k = opt_data.get("n_startup_trials_per_dim")
    if not k or float(k) <= 0.0:
        return int(base_n_startup)
    try:
        from automation.optimizer import bounds
        dim = len(bounds.extract_numeric_bounds(strategy))
    except Exception:
        return int(base_n_startup)
    return max(int(base_n_startup), math.ceil(float(k) * dim))


def study_shows_gradient_signal(rewards: list[float], evaluable_fraction: float,
                                tau: float) -> bool:
    """Issue #568 — Gradienten-Gate für die Tier-Eskalation.

    Höheres Trial-Budget (nächstes Tier) rechtfertigt sich nur, wenn die untere Study überhaupt
    Signal zeigt: ``evaluable_fraction > 0`` UND ``pstdev(reward) > τ``. Auf einer flachen
    (Plateau/Deckel-)Landschaft ist zusätzliches Budget wirkungslos (100 → 200 Trials lieferten
    identische best_value). Reine, deterministische Funktion (separat testbar)."""
    if not rewards or evaluable_fraction <= 0.0 or len(rewards) < 2:
        return False
    return statistics.pstdev([float(r) for r in rewards]) > float(tau)


def _boundary_hit_fraction(study, strategy: str | None) -> float | None:
    """Issue #597 — Anteil der numerischen Gewinner-Parameter, die innerhalb von 2 % einer
    Suchraumgrenze liegen. Ein Wert > 0.3 ist ein Alarm: entweder ist der Suchraum falsch gewählt
    oder der Reward drückt die Lösung in die Ecke (Randlösungs-Signatur, z. B. Trade-Frequenz
    maximieren). ``None``, wenn Strategie/Bounds/Winner nicht verfügbar sind (defensiv)."""
    if not strategy:
        return None
    try:
        best = study.best_trial
    except Exception:
        return None
    from automation.optimizer import bounds as _bounds
    try:
        b = _bounds.extract_numeric_bounds(strategy)
    except Exception:
        return None
    params = getattr(best, "params", {}) or {}
    numeric = [(k, v) for k, v in params.items()
               if k in b and isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numeric:
        return None
    hits = 0
    for k, v in numeric:
        lo, hi = b[k]
        span = (hi - lo) or 1.0
        norm = (float(v) - lo) / span
        if norm <= 0.02 or norm >= 0.98:
            hits += 1
    return hits / len(numeric)


def _emit_study_summary(study, symbol: str, study_t0: float, strategy: str | None = None) -> None:
    """Issue #415 — Per-Study-Timing-/Evaluierbarkeits-Summary nach ``study.optimize``.

    Defensiv gegen Test-Doubles (``DummyStudy`` ohne ``trials``/``best_value``): jeder Zugriff ist
    ``getattr``-/try-gekapselt, sodass die Summary nie den Lauf crasht. Aggregiert die per-Trial
    ``backtest_ms`` (User-Attr) zu Total/Median und zaehlt evaluable Trials (``oos_evaluated``).

    Issue #568 — zusätzlich das Gradienten-Signal (``reward_pstdev``, ``evaluable_fraction``,
    ``gradient_signal``) ausweisen, damit eine (aussichtslose) Study NICHT in höhere Tiers
    eskaliert wird und der Eskalations-Entscheid aus dem Log nachvollziehbar ist."""
    trials = list(getattr(study, "trials", None) or [])
    durs = [v for t in trials
            if (v := getattr(t, "user_attrs", {}).get("backtest_ms")) is not None]
    evaluable = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_evaluated") is True)
    # Issue #620 — Zähler der #589-Kohärenz-Verletzungen (sichtbar statt still im Subprozess). > 1 %
    # einer Study ⇒ WARNING mit Study-Name.
    coherence_violations = sum(
        1 for t in trials if getattr(t, "user_attrs", {}).get("oos_coherence_violation") is True)
    if trials and coherence_violations / len(trials) > 0.01:
        logging.getLogger("optimizer").warning(
            "[#620] %s: coherence_violations=%d/%d (> 1 %%) — sign(oos_sortino)≠sign(oos_total_return) "
            "häuft sich; Aggregationspfad prüfen.", getattr(study, "study_name", "?"),
            coherence_violations, len(trials),
        )
    try:
        best_value = study.best_value
    except Exception:
        best_value = None

    # Issue #568 — Gradienten-Signal aus den abgeschlossenen Reward-Werten. tau deklarativ.
    rewards = [getattr(t, "value", None) for t in trials]
    rewards = [float(r) for r in rewards if isinstance(r, (int, float))]
    evaluable_fraction = (evaluable / len(trials)) if trials else 0.0
    tau = 1e-3
    try:
        opt_path = config_dir() / "optimizer.json"
        if opt_path.exists():
            tau = float((json.loads(opt_path.read_text("utf-8")) or {}).get(
                "tier_escalation_min_signal", tau))
    except Exception:
        pass
    reward_pstdev = statistics.pstdev(rewards) if len(rewards) >= 2 else 0.0
    gradient_signal = study_shows_gradient_signal(rewards, evaluable_fraction, tau)
    if not gradient_signal:
        logging.getLogger("optimizer").warning(
            "[#568] %s: kein Gradienten-Signal (evaluable_fraction=%.2f, reward_pstdev=%.4f ≤ τ=%.4f) "
            "⇒ KEINE Tier-Eskalation gerechtfertigt (zusätzliches Budget auf flacher Landschaft ist "
            "wirkungslos).", symbol, evaluable_fraction, reward_pstdev, tau,
        )

    # Issue #592 — Deflations-Telemetrie auf der REWARD-Skala (je Study sichtbar, nicht nur im
    # Holdout-Pfad). Nutzt die evaluable Trial-Rewards (das tatsächliche argmax-Selektionskriterium).
    # Issue #611 — p_eligible (Gate-Passrate) + DSR-Kohorten-Telemetrie auf der ELIGIBLEN PER-PERIODEN-
    # Sortino-Skala. Die alte Reward-Skalen-Deflation über ALLE oos_evaluated-Trials schätzte σ auf einer
    # Zwei-Punkt-Mischung ⇒ Bernoulli-Standardabweichung der Passrate (anti-monoton). Jetzt: die
    # Streuung ÜBER die eligiblen Sortinos (die tatsächlichen argmax-Konkurrenten) ⇒ SR₀ (#618).
    deflated_selection, deflation_confidence = _read_deflation_config()
    n_eligible = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_eligible") is True)
    p_eligible = (n_eligible / len(trials)) if trials else 0.0
    cohort_sr = [getattr(t, "user_attrs", {}).get("oos_sortino_period") for t in trials
                 if getattr(t, "user_attrs", {}).get("oos_eligible") is True
                 and getattr(t, "user_attrs", {}).get("oos_sortino_period") is not None]
    deflation_n = len(cohort_sr)
    deflation_sr0 = deflation_var = None
    if deflated_selection and deflation_n >= 2:
        from automation.optimizer.deflation import sr0_multiple_testing
        deflation_var = statistics.pvariance([float(s) for s in cohort_sr])
        deflation_sr0 = sr0_multiple_testing(deflation_var, deflation_n)

    # Issue #597 — Randlösungs-Telemetrie (Anteil der Gewinner-Parameter an der Suchraumgrenze).
    boundary_hit_fraction = _boundary_hit_fraction(study, strategy)
    if boundary_hit_fraction is not None and boundary_hit_fraction > 0.3:
        logging.getLogger("optimizer").warning(
            "[#597] %s: boundary_hit_fraction=%.2f > 0.3 — der Gewinner klebt an den Suchraumgrenzen "
            "(Randlösung). Suchraum prüfen ODER Reward-Konditionierung (Turnover/Drawdown).",
            symbol, boundary_hit_fraction,
        )

    # Issue #621 — Reward-Term-Dekomposition
    eligible_terms = []
    for t in trials:
        if getattr(t, "user_attrs", {}).get("oos_evaluated") is True:
            terms = getattr(t, "user_attrs", {}).get("reward_terms")
            if terms and terms.get("branch") in ("eligible", "per_symbol", "pareto"):
                eligible_terms.append(terms)

    term_aggregates = {
        "divergence_at_cap": 0.0,
        "floor_clamped": 0.0,
        "terms": {}
    }

    if eligible_terms:
        n_el = len(eligible_terms)
        term_aggregates["divergence_at_cap"] = sum(1 for t in eligible_terms if t.get("divergence_at_cap")) / n_el
        term_aggregates["floor_clamped"] = sum(1 for t in eligible_terms if t.get("floor_clamped")) / n_el

        numeric_keys = ["base", "divergence", "dd_penalty", "param_pen", "turnover", "fold_dispersion", "tie_breaker"]

        rew_vals = []
        for t in eligible_terms:
            r = (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
                 - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
                 + t.get("tie_breaker", 0.0))
            rew_vals.append(r)

        rew_std = statistics.pstdev(rew_vals) if n_el >= 2 else 0.0
        rew_var = rew_std ** 2

        for k in numeric_keys:
            vals = [float(t.get(k, 0.0)) for t in eligible_terms]
            std_k = statistics.pstdev(vals) if n_el >= 2 else 0.0
            med_k = statistics.median(vals) if vals else 0.0

            var_contrib = 0.0
            if n_el >= 2 and rew_var > 0.0:
                mean_k = sum(vals) / n_el
                mean_r = sum(rew_vals) / n_el
                cov = sum((vals[i] - mean_k) * (rew_vals[i] - mean_r) for i in range(n_el)) / n_el
                var_contrib = cov / rew_var

            term_aggregates["terms"][k] = {
                "median": med_k,
                "std": std_k,
                "var_contrib": var_contrib
            }

            if std_k < 0.01 * rew_std:
                logging.getLogger("optimizer").warning("REWARD_TERM_INERT: %s", k)

    emit_execution_event(logging.getLogger("optimizer"), "optimizer_study_completed", {
        "study_name": getattr(study, "study_name", None),
        "symbol": symbol,
        "n_trials": len(trials),
        "evaluable_trials": evaluable,
        "best_value": best_value,
        "backtest_ms_total": sum(durs) if durs else 0,
        "backtest_ms_median": int(statistics.median(durs)) if durs else None,
        "wallclock_s": round(time.perf_counter() - study_t0),
        # Issue #568 — Gradienten-getriebene Eskalations-Telemetrie.
        "reward_pstdev": reward_pstdev,
        "evaluable_fraction": evaluable_fraction,
        "gradient_signal": gradient_signal,
        # Issue #611/#618 — DSR-Telemetrie (per-Perioden-Sortino-Skala) + p_eligible (Gate-Passrate).
        # Monotonie-Invariante (#611): SR₀ steigt NICHT mit p_eligible (kein Bernoulli-Artefakt mehr).
        "p_eligible": p_eligible,
        "deflation_n_eligible": deflation_n,
        "deflation_sr0": deflation_sr0,
        "deflation_var_sr": deflation_var,
        # Issue #620 — #589-Kohärenz-Verletzungen je Study (beobachtbar).
        "coherence_violations": coherence_violations,
        # Issue #597 — Randlösungs-Signatur.
        "boundary_hit_fraction": boundary_hit_fraction,
        "reward_terms_aggregates": term_aggregates,
    })


def _read_deflation_config() -> tuple[bool, float]:
    """Issue #592 — (deflated_selection, deflation_confidence) aus tournament.json (Zero-Hardcoding)."""
    deflated_selection, confidence = False, 0.95
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            deflated_selection = bool(data.get("deflated_selection", False))
            confidence = float(data.get("deflation_confidence", 0.95))
    except Exception:
        pass
    return deflated_selection, confidence


def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
                          *, run_backtest=run_backtest, build_trial=build_trial,
                          catalog_newest_ns: int | None = None,
                          catalog_span_days: float | None = None):
    """Wie make_objective, aber single-symbol: build_trial(instruments=[symbol]) und
       compute_reward(universe_size=1, sampled, global_params, strategy) (Per-Symbol-Reward
       mit param_pen Richtung global_params, A4.3).

       Issue #531: ``catalog_span_days`` (reale Bar-Spanne in Tagen) wird an ``build_trial``
       durchgereicht, damit die Manifest-Konstruktion fail-loud gegen die tatsächliche Datenlage
       prüft (REJECT_DATA_INSUFFICIENT_GEOMETRY statt stiller .loc-Klemmung)."""
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
            catalog_newest_ns=catalog_newest_ns,
            catalog_span_days=catalog_span_days,
        )

        # Issue #415 — Per-Trial-Wall-Clock. perf_counter UM den run_backtest-Aufruf herum (statt via
        # timings-Out-Param), damit ALLE bestehenden run_backtest-Mocks (Signatur
        # ``(trial_dir, manifest_path)``) unveraendert funktionieren (Signatur-Kompat, Pitfall #33).
        _t0 = time.perf_counter()
        try:
            output_path = run_backtest(trial_dir, manifest_path)
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")
        backtest_ms = round((time.perf_counter() - _t0) * 1000)

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                risk_dd_cap = (json.load(f) or {}).get("max_drawdown", 0.30)

        reward, reward_terms = compute_reward(metrics, universe_size=1, risk_dd_cap=risk_dd_cap,
                                sampled=sampled, global_params=global_params, strategy=strategy, return_terms=True)
        trial.set_user_attr("reward_terms", reward_terms)

        # Issue #404 (P0) — Per-Symbol-Telemetrie. Der Sweep emittierte bislang KEIN strukturiertes
        # Per-Trial-Event (nur Optunas native INFO-Zeile, vgl. #402), wodurch der Unevaluable-Floor-
        # Kollaps (Pitfall #75) forensisch unsichtbar blieb. `oos_eligible` trennt den IS-Drop
        # (Symbol nie IS-eligible ⇒ kein OOS evaluiert) vom OOS-Drop (OOS evaluiert, aber durchs
        # Gate gefallen); `is_total_trades`/`hit_trade_cap` machen die Shaping-Saettigung sichtbar.
        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        # Issue #408 — modale Gate-Drop-Reason: pro Trial die kategorisierte Rejection-Reason
        # persistieren, damit confirm._dominant_rejection sie ueber die Study aggregieren kann.
        rejection_reason = _classify_trial_rejection(metrics)
        trial.set_user_attr("rejection_reason", rejection_reason)
        # Issue #453 — granulare, dezidierte Rejection-Kategorie (löst 'oos_not_evaluated' in die
        # tatsächliche Ursache auf) zusätzlich persistieren — für die modale Proposal-Aggregation.
        is_rejection_detail = _classify_is_rejection_detail(metrics)
        trial.set_user_attr("is_rejection_detail", is_rejection_detail)
        # Issue #415 — backtest_ms als User-Attr fuer die Per-Study-Aggregation (optimize_symbol).
        trial.set_user_attr("backtest_ms", backtest_ms)
        # Issue #413 — oos_evaluated als User-Attr: Grundlage des evaluable-basierten Floor-Guards
        # (floor_plateau_callback) UND der `evaluable_trials`-Zaehlung im Per-Study-Summary (#415).
        trial.set_user_attr("oos_evaluated", bool(metrics.oos_evaluated))
        # Issue #615 — oos_eligible EXPLIZIT stempeln (Single Source of Truth für die Top-k-Holdout-
        # Selektion in confirm.confirm_per_symbol_promotion, #576). Vorher filterte confirm auf
        # ``is_rejection_detail is None`` — der gestempelte Wert war aber der STRING "NONE", nie
        # Python-``None`` ⇒ eligible_trials IMMER leer ⇒ Top-k lief nie. Der gestempelte Bool ist die
        # kohärente, direkt filterbare Grösse (identisch zu ``is_rejection_detail == IS_REJECTION_NONE``).
        trial.set_user_attr("oos_eligible", bool(metrics.oos_eligible))
        # Issue #612 — Feasibility-Constraint(s) für den Sampler (≤ 0 = feasible). Damit optimiert der
        # TPE EINE stetige Grösse (die risikoadjustierte OOS-Performance) statt einer Stufenfunktion;
        # die Feasibility-Rangordnung übernimmt Optuna nativ (feasible ≻ infeasible in best_trial).
        trial.set_user_attr("oos_constraint_violations", _compute_oos_constraints(metrics))
        # Issue #618 — der PER-PERIODEN-Sortino + PSR je Trial (für die DSR-Kohorten-Varianz V[ŜR_trials]
        # in confirm; Multiple-Testing-Korrektur auf der per-Perioden-Skala, NICHT der Reward-Skala #611).
        trial.set_user_attr("oos_sortino_period", metrics.oos_sortino_period)
        trial.set_user_attr("oos_psr", metrics.oos_psr)
        # Issue #620 — Kohärenz-Verletzung je Trial persistieren (Study-Zähler coherence_violations).
        trial.set_user_attr("oos_coherence_violation", bool(metrics.oos_coherence_violation))
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "symbol": symbol,
            "trial_number": trial.number,
            "backtest_ms": backtest_ms,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
            "oos_total_trades": metrics.oos_total_trades,
            "oos_total_return": metrics.oos_total_return,
            "is_total_trades": metrics.is_total_trades,
            "hit_trade_cap": metrics.hit_trade_cap,
            "outcome": outcome,
            # Issue #416 — Schluessel-Kennzahlen fuer die Per-Trial-Fehleranalyse zusaetzlich ins
            # strukturierte Event heben: Daten-Zeitfenster (None, falls die JSON keinen Block traegt)
            # und die kategorisierte Gate-Drop-Reason (#408).
            "rejection_reason": rejection_reason,
            "data_window_start": metrics.data_window_start,
            "data_window_end": metrics.data_window_end,
            "data_window_days": metrics.data_window_days,
            # Issue #455 (Pitfall #82) — OOS-Abdeckungs-Telemetrie an die Operator-Konsole heben.
            # oos_covered=False ⇒ der Floor-Grund ist auf einen Blick DATENseitig (H2-Katalog zu
            # dünn/stale), nicht parameterseitig — die einzige diagnostisch relevante Zahl, die
            # bislang vor der Konsole verworfen wurde.
            "fill_ts_max": metrics.fill_ts_max,
            "oos_window_start_ns": metrics.oos_window_start_ns,
            "oos_covered": metrics.oos_covered,
            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
            # Issue #453 — granulare Rejection-Kategorie + die konkreten OOS-Gate-Verletzungen
            # ins strukturierte Event heben (Observability; ändert keine Entscheidung).
            "is_rejection_detail": is_rejection_detail,
            "oos_rejection_reasons": list(metrics.oos_rejection_reasons),
            # Issue #554 — maschinenlesbare Gate-Deltas (metric → actual − threshold) für die
            # forensische Near-Miss-Analyse ohne String-Parsing der Reject-Gründe.
            "oos_gate_deltas": metrics.oos_gate_deltas or {},
            # Issue #569 — Roh-Kennzahlen additiv ins Event heben (rein observational, kein
            # Entscheidungseinfluss), damit #559/#560/#563/#565 direkt aus dem Log verifizierbar sind
            # (statt aus oos_gate_deltas + reward rückgerechnet). null bei mathematisch undefiniertem
            # Sortino/PF (Zero-Loss/Sub-Threshold). per_fold_oos_sortino für die #565-Dispersion.
            "oos_sortino": metrics.oos_sortino,
            "oos_expectancy": metrics.oos_expectancy,
            "oos_win_rate": metrics.oos_win_rate,
            "oos_profit_factor": metrics.oos_profit_factor,
            "is_sortino_median": metrics.is_sortino_median,
            "per_fold_oos_sortino": list(metrics.oos_fold_sortinos),
            # Issue #620 — #589-Kohärenz-Verletzung (sign(oos_sortino)≠sign(oos_total_return)) sichtbar.
            "oos_coherence_violation": bool(metrics.oos_coherence_violation),
            "reward_terms": reward_terms,
        })

        reward_mode = "auto"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                reward_mode = (json.load(f) or {}).get("reward_mode", "auto")
        if reward_mode == "pareto":
            min_trades = 20
            if tournament_path.exists():
                with open(tournament_path, "r", encoding="utf-8") as f:
                    min_trades = (json.load(f) or {}).get("min_trades", 20)
            trades_constraint = min_trades - metrics.oos_total_trades
            dd_constraint = metrics.oos_max_drawdown - risk_dd_cap
            trial.set_user_attr("constraints", (float(trades_constraint), float(dd_constraint)))
        return reward
    return objective


def optimize_symbol(strategy: str, symbol: str, n_trials: int | None = None,
                    *, storage: str | None = None, catalog_newest_ns: int | None = None,
                    catalog_span_days: float | None = None):
    """Single-Symbol-Variante von `optimize`: eigene benannte SQLite-Study unter
       {WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db, Manifest mit instruments=[symbol]
       (universe_size==1 ⇒ Per-Symbol-Reward), Warm-Start am globalen Optimum (Gate 2 via
       study.enqueue_trial). n_jobs=1 wird erzwungen (SQLite-Reproduzierbarkeit, Pitfall #68).
       Das globale `optimize`/`make_objective` bleibt unverändert."""
    study_t0 = time.perf_counter()  # Issue #415 — Per-Study-Wall-Clock
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
    # Issue #568 — n_startup_trials an die Parameterzahl der Strategie koppeln (>= k·dim), damit der
    # TPE bei multivariate=True,group=True genügend Startpunkte hat. Legacy, wenn der Key fehlt.
    n_startup_trials = derive_n_startup_trials(strategy, n_startup_trials, opt_data)

    study_name = f"study_{strategy}_{_sanitize(symbol)}"
    sweep_dir = WORK / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    if storage is None:
        storage = resolve_storage(study_name=study_name)   # A4.7: SQLite-Default, ENV/JSON-Opt-in

    reward_mode = opt_data.get("reward_mode", "auto")
    directions = None
    if reward_mode == "pareto":
        directions = ["maximize", "maximize", "maximize", "maximize", "minimize", "minimize"]
        def constraints_func(trial):
            return trial.user_attrs.get("constraints", (0.0, 0.0))
        sampler = optuna.samplers.NSGAIISampler(constraints_func=constraints_func, seed=seed)
    else:
        # Issue #612 — Feasibility in den Sampler (siehe optimize_symbol): constraints_func liest die
        # gestempelten OOS-Gate-Verletzungen; Optuna 4.9 bevorzugt feasible nativ vor infeasible.
        sampler = optuna.samplers.TPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed,
            constraints_func=_oos_constraints_func,
        )

    # Issue #411 — serialisiert + DDL-Race-fest (table studies already exists). Ersetzt das nackte
    # optuna.create_study, das bei zwei Workern auf derselben frischen SQLite-Datei crasht.
    study = _create_study_with_retry(
        study_name=study_name,
        storage=storage,
        sampler=sampler,
        direction="maximize" if not directions else None,
        directions=directions
    )

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)

    # Gate 2 — Warm-Start + Shrinkage-Referenz. Issue #565: definierter Fallback statt Silent-Zero.
    # Fehlt das echte globale Optimum (load_global_best leer), wird strategy_defaults der
    # Warm-Start-Seed UND die Shrinkage-Referenz (param_pen zieht Richtung Default statt ins Leere).
    # ``shrinkage_inactive`` (Study-User-Attr) markiert LAUT & forensisch sichtbar (analog
    # Floor-Guard #409/#456), dass KEIN echtes globales Optimum als Anker vorliegt — unabhängig vom
    # Defaults-Fallback, damit der fehlende Global-Stage-Anker im Standalone-Sweep nie still bleibt.
    global_best, seed_source = resolve_symbol_shrinkage_seed(strategy, cfg_dir)
    shrinkage_inactive = seed_source != "global_best"
    study.set_user_attr("shrinkage_seed_source", seed_source)
    study.set_user_attr("shrinkage_inactive", shrinkage_inactive)
    if seed_source == "strategy_defaults":
        logging.getLogger("optimizer").warning(
            "[#565] %s/%s: kein global_best im Per-Symbol-Sweep (shrinkage_inactive) — Fallback auf "
            "strategy_defaults als Shrinkage-Referenz & Warm-Start-Seed (param_pen zieht Richtung "
            "Default statt ins Leere).",
            strategy, symbol,
        )
    elif seed_source == "none":
        logging.getLogger("optimizer").warning(
            "[#565] %s/%s: WEDER global_best NOCH strategy_defaults auflösbar (shrinkage_inactive) ⇒ "
            "param_pen ≡ 0, der Per-Symbol-Vektor tunt ungezügelt Richtung CV-Rausch (Overfit-Risiko).",
            strategy, symbol,
        )
    if global_best:
        study.enqueue_trial(global_best)

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    objective = make_symbol_objective(
        strategy, symbol, global_best,
        run_backtest=run_backtest, build_trial=build_trial,
        catalog_newest_ns=catalog_newest_ns,
        catalog_span_days=catalog_span_days,
    )
    # Issue #409 — Fail-Loud-Guard: warnt, sobald nach n_startup_trials alle Trials am
    # Unevaluable-Floor kleben (Pitfall #75). Config einmalig gebunden (kein Per-Trial-IO).
    # Issue #456 — Produktion bindet stop_on_plateau=True: die als aussichtslos erkannte
    # Per-Symbol-Study früh beenden (spart ~84 nutzlose Trials, ~30 min pro Floor-Symbol).
    floor_guard = partial(floor_plateau_callback, weights=opt_data,
                          n_startup_trials=n_startup_trials, stop_on_plateau=True)
    study.optimize(objective, n_trials=n_trials, n_jobs=1,
                   catch=(json.JSONDecodeError, OSError), callbacks=[floor_guard])
    # Issue #415 — Per-Study-Summary (Timing + Evaluierbarkeit) als strukturiertes Event.
    _emit_study_summary(study, symbol, study_t0, strategy=strategy)
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
