import json
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

    # Issue #468 — Harter Fail-Loud-Mechanismus bei Posterior-Korruption.
    msg = (f"Reward-Semantik-Versionskonflikt: die geladene Study wurde unter Version {existing if existing is not None else 'unversioniert'} "
           f"akkumuliert, aktuell ist Version {current}. Reward-Werte verschiedener Versionen "
           f"sind NICHT vergleichbar. Dies führt zu Posterior-Korruption im TPE-Sampler. "
           f"Bitte löschen Sie die obsolete Study-Datenbank (*.db) und starten Sie neu.")

    if has_trials:
        if existing is None or existing < current:
            raise ValueError(msg)

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

        reward = compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap)

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

    reward_mode = opt_data.get("reward_mode", "auto")
    directions = None
    if reward_mode == "pareto":
        directions = ["maximize", "maximize", "maximize", "maximize", "minimize", "minimize"]
        def constraints_func(trial):
            return trial.user_attrs.get("constraints", (0.0, 0.0))
        sampler = optuna.samplers.NSGAIISampler(constraints_func=constraints_func, seed=seed)
    else:
        sampler = optuna.samplers.TPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed
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
        return "NONE"
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


def _emit_study_summary(study, symbol: str, study_t0: float) -> None:
    """Issue #415 — Per-Study-Timing-/Evaluierbarkeits-Summary nach ``study.optimize``.

    Defensiv gegen Test-Doubles (``DummyStudy`` ohne ``trials``/``best_value``): jeder Zugriff ist
    ``getattr``-/try-gekapselt, sodass die Summary nie den Lauf crasht. Aggregiert die per-Trial
    ``backtest_ms`` (User-Attr) zu Total/Median und zaehlt evaluable Trials (``oos_evaluated``)."""
    trials = list(getattr(study, "trials", None) or [])
    durs = [v for t in trials
            if (v := getattr(t, "user_attrs", {}).get("backtest_ms")) is not None]
    evaluable = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_evaluated") is True)
    try:
        best_value = study.best_value
    except Exception:
        best_value = None
    emit_execution_event(logging.getLogger("optimizer"), "optimizer_study_completed", {
        "study_name": getattr(study, "study_name", None),
        "symbol": symbol,
        "n_trials": len(trials),
        "evaluable_trials": evaluable,
        "best_value": best_value,
        "backtest_ms_total": sum(durs) if durs else 0,
        "backtest_ms_median": int(statistics.median(durs)) if durs else None,
        "wallclock_s": round(time.perf_counter() - study_t0),
    })


def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
                          *, run_backtest=run_backtest, build_trial=build_trial,
                          catalog_newest_ns: int | None = None):
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
            catalog_newest_ns=catalog_newest_ns,
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

        reward = compute_reward(metrics, universe_size=1, risk_dd_cap=risk_dd_cap,
                                sampled=sampled, global_params=global_params, strategy=strategy)

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
                    *, storage: str | None = None, catalog_newest_ns: int | None = None):
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
        sampler = optuna.samplers.TPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed,
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

    # Gate 2 — Warm-Start am globalen Optimum (nur wenn nicht leer).
    global_best = load_global_best(strategy, cfg_dir)
    if global_best:
        study.enqueue_trial(global_best)

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    objective = make_symbol_objective(
        strategy, symbol, global_best,
        run_backtest=run_backtest, build_trial=build_trial,
        catalog_newest_ns=catalog_newest_ns,
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
    _emit_study_summary(study, symbol, study_t0)
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
