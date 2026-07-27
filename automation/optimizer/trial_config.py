import json
import shutil
import datetime as dt
from pathlib import Path
from automation.optimizer.manifest import git_commit, catalog_fingerprint, sha256_file, WORK
from automation.optimizer.resolve import resolve_params

def config_dir() -> Path:
    """Returns the default configuration directory."""
    import os
    if "ETORO_CONFIG_DIR" in os.environ:
        return Path(os.environ["ETORO_CONFIG_DIR"])
    # Default to automation/config from WORK parent
    return WORK.parent.parent / "automation" / "config"


def compute_walk_forward_window(
    *,
    now: dt.datetime,
    holdout_days: int,
    is_window_days: int,
    oos_window_days: int,
    n_folds: int,
    embargo_period_days: int = 0,
    catalog_newest_ns: int | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """Issue #457 (Pitfall #84) — die EINZIGE Quelle der Walk-Forward-Fenster-Arithmetik.

    Rein (kein I/O, kein globaler State, deterministisch). Berechnet das (start, end)-Fenster,
    das ``build_trial`` ins Manifest schreibt UND das das #455-OOS-Erreichbarkeits-Preflight
    (``sweep``) braucht. Zwei parallele Inline-Implementierungen derselben Grenze waeren eine
    eingebaute Divergenz-Falle zwischen „start_ns fuers Daten-Laden" und „start_ns fuer den Split"
    (genau die Wurzel der OOS=0-Bug-Familie, Pitfall #80/#82) — deshalb NIE inline nachbauen,
    immer ueber diese Funktion.

    Geometrie (verifiziert gegen das real beobachtete Sweep-Log, ``now=2026-06-25`` ⇒
    ``start=2025-05-16``, ``end=2026-05-11``):
      * ``end`` = Mitternacht(``now``); faellt ``now`` auf einen Sonntag (``weekday()==6``),
        rollt ``end`` VOR dem Holdout-Abzug auf Samstag zurueck.
      * ``end`` -= ``holdout_days``.
      * ``start`` = ``end`` − (``is_window_days`` + ``embargo_period_days`` + ``n_folds`` × ``oos_window_days``) Tage.

    Issue #548 (Pitfall #109) — der Embargo/Purge-Gap zwischen IS-Ende und OOS-Start MUSS im
    äusseren Fenster-Span reserviert werden, sonst liefe der letzte OOS-Fold ``embargo_period_days``
    über den Datenrand ``end`` hinaus (``compute_fold_boundaries`` startet OOS-Fold 0 bei
    ``is_end + embargo``). Mit der Reservierung endet Fold ``n_folds−1`` exakt bei ``end``:
    ``oos_end_{n-1} = start + is + embargo + n_folds×oos = end`` (kein Overflow). Die früheste
    OOS-Sub-Fenster-Grenze (fold=0) ist damit ``start + is_window_days + embargo_period_days``.
    ``embargo_period_days=0`` reproduziert das Alt-Verhalten bit-identisch.
    """
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if catalog_newest_ns is not None:
        catalog_dt = dt.datetime.fromtimestamp(catalog_newest_ns / 1_000_000_000, tz=dt.timezone.utc)
        catalog_end = catalog_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = min(end, catalog_end)
    # Sonntag (weekday() == 6) → Samstag, BEVOR holdout abgezogen wird.
    if end.weekday() == 6:
        end -= dt.timedelta(days=1)
    end -= dt.timedelta(days=holdout_days)
    start = end - dt.timedelta(days=is_window_days + embargo_period_days + n_folds * oos_window_days)
    return start, end

def _wf_settings_from_bt_data(
    bt_data: dict,
    *,
    holdout_days: int | None = None,
    n_folds: int | None = None,
    oos_window_days_override: int | None = None,
) -> dict:
    """Issue #796 — aus ``build_trial`` extrahiert (rein, kein I/O), damit die Walk-Forward-
    ``wf_settings`` NIE zwischen der Pro-Trial-Manifest-Konstruktion und ``freeze_study_config``
    (EINE eingefrorene Config je Study) divergieren (Pitfall #80/#82, hier auf die Config-
    Harmonisierung statt die Fenster-Arithmetik angewendet). Nimmt ein BEREITS geladenes
    ``bt_data``-Dict entgegen — kein zweiter ``backtest.json``-Read je Trial (siehe
    ``resolve_wf_settings`` fuer die I/O-Variante)."""
    wf = bt_data.get("walk_forward", {})
    if holdout_days is None:
        holdout_days = wf.get("holdout_days", 45)
    if n_folds is None:
        n_folds = wf.get("splits", 1)

    is_window_days = wf.get("is_window_days", 120)
    oos_window_days = oos_window_days_override if oos_window_days_override is not None else wf.get("oos_window_days", 30)
    embargo_period_days = wf.get("embargo_period_days", 0)
    return {
        "is_window_days": is_window_days,
        "oos_window_days": oos_window_days,
        "splits": n_folds,
        "holdout_days": holdout_days,
        "embargo_period_days": embargo_period_days,
        "walk_forward_active": True,
    }


def resolve_wf_settings(
    base_cfg: Path | None = None,
    *,
    holdout_days: int | None = None,
    n_folds: int | None = None,
    oos_window_days_override: int | None = None,
) -> dict:
    """I/O-Variante von ``_wf_settings_from_bt_data`` fuer Aufrufer OHNE bereits geladenes
    ``bt_data`` (``confirm.py``/``run_optimization.py`` vor ``freeze_study_config``, je Study
    EINMAL — kein Hot-Path). ``build_trial`` selbst nutzt ``_wf_settings_from_bt_data`` direkt auf
    seinem ohnehin geladenen ``bt_data``, um den ``backtest.json``-Read nicht je Trial zu
    verdoppeln."""
    if base_cfg is None:
        base_cfg = config_dir()
    backtest_cfg_path = base_cfg / "backtest.json"
    with open(backtest_cfg_path, "r", encoding="utf-8") as f:
        bt_data = json.load(f)
    return _wf_settings_from_bt_data(
        bt_data, holdout_days=holdout_days, n_folds=n_folds,
        oos_window_days_override=oos_window_days_override,
    )


def freeze_study_config(study_name: str, wf_settings: dict, *, base_cfg: Path | None = None) -> Path:
    """Issue #796 — EINE eingefrorene Config je Study statt einer Kopie je Trial (135.067 Bytes ×
    Trials im vollen Sweep, siehe Issue-Katalog #794-#800 / #796). Legt ``WORK/<study_name>/config/``
    idempotent an (``exist_ok=True`` — sicher bei mehrfachem Aufruf, z. B. Retry desselben Symbols),
    kopiert die Basis-JSONs GENAU EINMAL und schreibt ``backtest.json.walk_forward = wf_settings``
    hinein — dieselbe Harmonisierung, die ``build_trial(copy_config=True)`` bisher PRO TRIAL
    wiederholt hat. Der zurückgegebene Pfad wird an ``build_trial(copy_config=False,
    study_config_dir=...)`` UND an ``runner.run_backtest(config_dir=...)`` durchgereicht. Liegt
    unter ``WORK/<study_name>/config`` — kein ``trial_*``-Verzeichnis, damit von der Retention
    (#794) niemals als verwaistes Trial-Verzeichnis geloescht."""
    if base_cfg is None:
        base_cfg = config_dir()
    study_cfg_dir = WORK / study_name / "config"
    study_cfg_dir.mkdir(parents=True, exist_ok=True)
    for p in base_cfg.glob("*.json"):
        shutil.copy2(p, study_cfg_dir / p.name)
    copied_bt_path = study_cfg_dir / "backtest.json"
    if copied_bt_path.exists():
        with open(copied_bt_path, "r", encoding="utf-8") as f:
            copied_bt_data = json.load(f)
        copied_bt_data["walk_forward"] = dict(wf_settings)
        with open(copied_bt_path, "w", encoding="utf-8") as f:
            json.dump(copied_bt_data, f, indent=2)
    return study_cfg_dir


def build_trial(
    strategy_class: str,
    sampled: dict,
    *,
    study_name: str,
    trial_number: int,
    seed: int,
    now: dt.datetime | None = None,
    holdout_days: int | None = None,
    n_folds: int | None = None,
    oos_window_days_override: int | None = None,
    base_cfg: Path | None = None,
    instruments: list[str] | None = None,
    copy_config: bool = True,
    study_config_dir: Path | None = None,
    catalog_newest_ns: int | None = None,
    catalog_span_days: float | None = None,
) -> tuple[Path, Path]:
    """
    Erzeugt isoliertes trial_dir; kopiert config_dir()-Inhalt nach trial_dir/config;
    schreibt experiment_manifest.json (manifest_version='1.0', Provenienz, global_settings,
    genau EINE Strategie mit resolve_params()-Ergebnis). Gibt (trial_dir, manifest_path) zurück.

    Window: end = midnight(now); if end.weekday()==6: end -= 1 Tag; end -= holdout_days;
            start = end - (is_window_days + n_folds*oos_window_days) Tage.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if base_cfg is None:
        base_cfg = config_dir()

    backtest_cfg_path = base_cfg / "backtest.json"
    with open(backtest_cfg_path, "r", encoding="utf-8") as f:
        bt_data = json.load(f)

    # Issue #796 — ``wf_settings`` ueber denselben Pfad wie ``freeze_study_config`` aufloesen
    # (Single Source of Truth, siehe ``_wf_settings_from_bt_data``-Docstring); KEINE zweite Inline-
    # Berechnung mehr (Divergenz-Falle Pitfall #80/#82, hier auf die Config-Harmonisierung statt
    # die Fenster-Arithmetik angewendet). Nutzt das HIER bereits geladene ``bt_data`` — kein
    # zweiter ``backtest.json``-Read je Trial.
    wf_settings = _wf_settings_from_bt_data(
        bt_data, holdout_days=holdout_days, n_folds=n_folds,
        oos_window_days_override=oos_window_days_override,
    )
    is_window_days = wf_settings["is_window_days"]
    oos_window_days = wf_settings["oos_window_days"]
    n_folds = wf_settings["splits"]
    holdout_days = wf_settings["holdout_days"]
    embargo_period_days = wf_settings["embargo_period_days"]

    # Issue #445 — Fail-Loud-Startup-Assertion: die Walk-Forward-Geometrie darf die dokumentierte
    # Datenhistorie nicht übersteigen (Single Source of Truth = walk_forward.data_history_days).
    # Sonst zehrt das Holdout-Fenster die Reserve auf, und der Spätstarter-Filter (backtest_runner)
    # verwirft alle Symbole erst spät und still ("Keine Instrumente"). Hier früh & erklärend abbrechen.
    # No-Op, wenn data_history_days fehlt (z. B. minimale Inline-Test-Configs) ⇒ rückwärtskompatibel.
    wf = bt_data.get("walk_forward", {})
    data_history_days = wf.get("data_history_days")
    if data_history_days is not None:
        required_total = is_window_days + (n_folds * oos_window_days) + embargo_period_days + holdout_days
        if required_total > data_history_days:
            raise ValueError(
                f"Walk-Forward-Geometrie übersteigt die dokumentierte Datenhistorie (Issue #445): "
                f"is_window {is_window_days} + splits {n_folds} × oos {oos_window_days} + embargo {embargo_period_days} + holdout "
                f"{holdout_days} = {required_total} Tage > data_history_days {data_history_days}. "
                f"Reduziere die Geometrie ODER erhöhe backtest.json.walk_forward.data_history_days "
                f"(und beschaffe entsprechend mehr Katalog-Historie)."
            )

    # Issue #531 — Fail-Loud gegen die REAL vorhandene Bar-Spanne (nicht nur gegen den Config-Wert
    # data_history_days, #445 oben). Übergibt der Aufrufer die gemessene Katalog-Spanne (Tage), MUSS
    # sie die volle Walk-Forward-Geometrie (is + splits*oos + holdout) abdecken — sonst würden der
    # letzte OOS-Fold UND der Holdout still via .loc über den Datenrand geklemmt (No-Clamping-Policy).
    # Kein catalog_span_days ⇒ No-Op (rückwärtskompatibel, z. B. minimale Inline-Test-Configs).
    if catalog_span_days is not None:
        from automation.optimizer.gate import assert_walk_forward_geometry, InsufficientGeometryError
        _wf_geometry = {"is_window_days": is_window_days, "oos_window_days": oos_window_days,
                        "splits": n_folds, "holdout_days": holdout_days}
        _symbol = instruments[0] if instruments else None
        try:
            assert_walk_forward_geometry(actual_span_days=catalog_span_days,
                                         walk_forward_dict=_wf_geometry, symbol=_symbol)
        except InsufficientGeometryError as geom_err:
            from automation.log_manager import emit_gate1_rejection
            import logging
            emit_gate1_rejection(logging.getLogger("optimizer"),
                                 available_days=geom_err.actual, required_days=geom_err.required,
                                 symbol=_symbol)
            raise

    # Self-describing manifest (ISSUE-OPT-374): the effective walk-forward geometry and
    # start_capital must travel inside the manifest's global_settings, not only via the
    # copied backtest.json side-channel. Built once (via ``resolve_wf_settings`` above) and
    # reused for both sinks (DRY) — Issue #548 (Pitfall #109): der Embargo reist darin bereits mit.
    start_capital = bt_data.get("start_capital", 10000.0)

    # Calculate dates — Issue #457: an die geteilte, reine Fenster-Funktion delegieren (Single
    # Source of Truth). KEINE Inline-Datums-Arithmetik mehr hier; das Sweep-Preflight (#455) nutzt
    # exakt dieselbe Grenze, sodass „start fuers Laden" und „start fuer den Split" nie divergieren.
    start, end = compute_walk_forward_window(
        now=now,
        holdout_days=holdout_days,
        is_window_days=is_window_days,
        oos_window_days=oos_window_days,
        n_folds=n_folds,
        embargo_period_days=embargo_period_days,
        catalog_newest_ns=catalog_newest_ns,
    )

    # Setup directories
    trial_dir = WORK / study_name / f"trial_{trial_number:04d}"

    # NEU: Logs-Verzeichnis für den Backtest-Subprozess anlegen (Fix Issue #346)
    (trial_dir / "logs").mkdir(parents=True, exist_ok=True)

    # A4.9/#796 Config-Sharing: copy_config=False überspringt die Pro-Trial-Kopie (135.067 Bytes ×
    # Trials im vollen Sweep, siehe Issue-Katalog #794-#800). Erlaubt, weil das Manifest seit
    # ISSUE-OPT-374 self-describing ist (walk_forward + start_capital in global_settings); der
    # Aufrufer MUSS eine per ``freeze_study_config`` eingefrorene Study-``config/`` via
    # ``study_config_dir`` bereitstellen UND denselben Pfad an
    # ``runner.run_backtest(config_dir=...)`` durchreichen. Default ``True`` ⇒ bit-identisches
    # Verhalten (Kopie pro Trial, ``trial_dir/config`` wird angelegt).
    if copy_config:
        trial_cfg_dir = trial_dir / "config"
        trial_cfg_dir.mkdir(parents=True, exist_ok=True)
        # Copy all JSON files from base_cfg
        for p in base_cfg.glob("*.json"):
            shutil.copy2(p, trial_cfg_dir / p.name)

        # Harmonisierung: Nach dem Kopieren Config überschreiben, um Sizing/Splitting zu synchronisieren
        copied_bt_path = trial_cfg_dir / "backtest.json"
        if copied_bt_path.exists():
            with open(copied_bt_path, "r", encoding="utf-8") as f:
                copied_bt_data = json.load(f)
            copied_bt_data["walk_forward"] = dict(wf_settings)
            with open(copied_bt_path, "w", encoding="utf-8") as f:
                json.dump(copied_bt_data, f, indent=2)
    elif study_config_dir is None or not Path(study_config_dir).is_dir():
        # Issue #796 — Fail-Loud-Waechter: KEIN stiller Fallback auf die Repo-Config (sie traegt
        # potenziell ein FALSCHES walk_forward fuer diesen Trial). ``trial_dir/config`` wird in
        # diesem Zweig bewusst NICHT angelegt — genau die Speicherersparnis, die #796 hebt.
        raise ValueError(
            "build_trial(copy_config=False) verlangt einen existierenden study_config_dir "
            f"(Issue #796) — erhalten: {study_config_dir!r}. Zuerst "
            "trial_config.freeze_study_config(study_name, wf_settings) aufrufen und den "
            "zurueckgegebenen Pfad hier UND an runner.run_backtest(config_dir=...) durchreichen."
        )

    resolved_params = resolve_params(strategy_class, sampled, base_cfg)

    strategy_module = ""
    config_class = ""
    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        with open(strats_path, "r", encoding="utf-8") as f:
            strats_data = json.load(f)
            for s in strats_data.get("strategies", []):
                if s.get("strategy_class") == strategy_class:
                    strategy_module = s.get("strategy_module", "")
                    config_class = s.get("config_class", "")
                    break

    # Resolve catalog_path from config or fallback to default
    raw_catalog_path = bt_data.get("catalog_path", "data/nautilus")
    # WORK is PROJECT_ROOT / "data" / "optimizer", so WORK.parent.parent is PROJECT_ROOT
    catalog_path = (WORK.parent.parent / raw_catalog_path).resolve()

    # Manifest payload
    tournament_file = base_cfg / "tournament.json"
    t_hash = sha256_file(tournament_file) if tournament_file.exists() else "unknown"

    manifest_payload = {
        "manifest_version": "1.0",
        "provenance": {
            "git_commit": git_commit(),
            "data_snapshot_sha256": catalog_fingerprint(),
            "frozen_tournament_sha256": t_hash,
            "study_name": study_name,
            "trial_number": trial_number
        },
        "global_settings": {
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seed": seed,
            "catalog_path": str(catalog_path),
            "start_capital": start_capital,
            "walk_forward": dict(wf_settings),
        },
        "strategies": [
            {
                "strategy_class": strategy_class,
                "strategy_module": strategy_module,
                "config_class": config_class,
                "params": resolved_params,
                "active": True
            }
        ]
    }

    # A4.2: manifest-getriebene Single-/Multi-Symbol-Restriktion. instruments=None ⇒ Schlüssel
    # wird NICHT geschrieben (rückwärtskompatibel, volles Universum).
    if instruments is not None:
        manifest_payload["global_settings"]["instruments"] = list(instruments)

    manifest_path = trial_dir / "experiment_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)

    return trial_dir, manifest_path
