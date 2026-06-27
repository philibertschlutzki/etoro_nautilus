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
      * ``start`` = ``end`` − (``is_window_days`` + ``n_folds`` × ``oos_window_days``) Tage.

    Die frueheste OOS-Sub-Fenster-Grenze (fold=0) ist damit ``start + is_window_days``.
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
    start = end - dt.timedelta(days=is_window_days + n_folds * oos_window_days)
    return start, end

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
    catalog_newest_ns: int | None = None,
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

    wf = bt_data.get("walk_forward", {})
    if holdout_days is None:
        holdout_days = wf.get("holdout_days", 45)
    if n_folds is None:
        n_folds = wf.get("splits", 1)

    is_window_days = wf.get("is_window_days", 120)
    oos_window_days = oos_window_days_override if oos_window_days_override is not None else wf.get("oos_window_days", 30)

    # Issue #445 — Fail-Loud-Startup-Assertion: die Walk-Forward-Geometrie darf die dokumentierte
    # Datenhistorie nicht übersteigen (Single Source of Truth = walk_forward.data_history_days).
    # Sonst zehrt das Holdout-Fenster die Reserve auf, und der Spätstarter-Filter (backtest_runner)
    # verwirft alle Symbole erst spät und still ("Keine Instrumente"). Hier früh & erklärend abbrechen.
    # No-Op, wenn data_history_days fehlt (z. B. minimale Inline-Test-Configs) ⇒ rückwärtskompatibel.
    data_history_days = wf.get("data_history_days")
    if data_history_days is not None:
        required_total = is_window_days + n_folds * oos_window_days + holdout_days
        if required_total > data_history_days:
            raise ValueError(
                f"Walk-Forward-Geometrie übersteigt die dokumentierte Datenhistorie (Issue #445): "
                f"is_window {is_window_days} + splits {n_folds} × oos {oos_window_days} + holdout "
                f"{holdout_days} = {required_total} Tage > data_history_days {data_history_days}. "
                f"Reduziere die Geometrie ODER erhöhe backtest.json.walk_forward.data_history_days "
                f"(und beschaffe entsprechend mehr Katalog-Historie)."
            )

    # Self-describing manifest (ISSUE-OPT-374): the effective walk-forward geometry and
    # start_capital must travel inside the manifest's global_settings, not only via the
    # copied backtest.json side-channel. Built once and reused for both sinks (DRY).
    start_capital = bt_data.get("start_capital", 10000.0)
    wf_settings = {
        "is_window_days": is_window_days,
        "oos_window_days": oos_window_days,
        "splits": n_folds,
        "holdout_days": holdout_days,
        "walk_forward_active": True,
    }

    # Calculate dates — Issue #457: an die geteilte, reine Fenster-Funktion delegieren (Single
    # Source of Truth). KEINE Inline-Datums-Arithmetik mehr hier; das Sweep-Preflight (#455) nutzt
    # exakt dieselbe Grenze, sodass „start fuers Laden" und „start fuer den Split" nie divergieren.
    start, end = compute_walk_forward_window(
        now=now,
        holdout_days=holdout_days,
        is_window_days=is_window_days,
        oos_window_days=oos_window_days,
        n_folds=n_folds,
        catalog_newest_ns=catalog_newest_ns,
    )

    # Setup directories
    trial_dir = WORK / study_name / f"trial_{trial_number:04d}"
    trial_cfg_dir = trial_dir / "config"
    trial_cfg_dir.mkdir(parents=True, exist_ok=True)

    # NEU: Logs-Verzeichnis für den Backtest-Subprozess anlegen (Fix Issue #346)
    (trial_dir / "logs").mkdir(parents=True, exist_ok=True)

    # A4.9 Config-Sharing: copy_config=False überspringt die Pro-Trial-Kopie (spart 40k+ Kopien
    # pro Sweep). Erlaubt, weil das Manifest seit ISSUE-OPT-374 self-describing ist (walk_forward
    # + start_capital in global_settings); der Aufrufer stellt eine eingefrorene Study-config/ via
    # ETORO_CONFIG_DIR bereit. Default True ⇒ bit-identisches Verhalten (Kopie pro Trial).
    if copy_config:
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
