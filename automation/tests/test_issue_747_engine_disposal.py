"""Issue #747 — Optuna/SQLAlchemy-Storage-Engines im Per-Symbol-Sweep nie disposed → deterministische
FD-Erschoepfung.

Root-Cause (siehe Issue #738/#747): `resolve_storage()` liefert je Study eine eigene SQLite-Datei;
`optuna.create_study(storage=<url>)` baute deren zugrundeliegende SQLAlchemy-``Engine`` bislang INTERN
und damit fuer den Aufrufer unreferenzierbar — kein `.dispose()`-Aufruf im gesamten Optimizer-Pfad.
Bei `--tier all --strategies all` (~1700+ Paare) wuchs die Zahl gleichzeitig offener File-Deskriptoren
empirisch verifiziert +2 FDs je verarbeitetem Paar, LINEAR und UNBEGRENZT, bis `OSError(24, "Too many
open files")` — und riss danach den gesamten Sweep-Prozess (inkl. Optunas eigenem naechsten
`create_study`-Schema-Check) mit.

Fix: `optimize_symbol` (run_optimization.py) konstruiert die `RDBStorage` jetzt explizit
(`_resolve_rdb_storage`) und disposed sie (`_dispose_storage`) NACH jedem Studien-Zugriffsfenster —
Phase 1 (`optimize_symbol` selbst, VOR dem Return), die familienweite Aggregation
(`_family_n_from_studies`/`_family_period_returns_from_studies`, sweep.py) UND Phase 2
(`_run_confirm_and_export`, sweep.py). `Engine.dispose()` ist nicht-terminal — ein Studien-Objekt
bleibt nach Dispose weiter benutzbar (lazy Reconnect), nur der Connection-Pool wird geschlossen.
"""
import json
import os
import resource
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import sweep
from automation.optimizer import trial_config


def _open_fd_count() -> int:
    """Zaehlt offene File-Deskriptoren des aktuellen Prozesses (Linux `/proc/self/fd`)."""
    return len(os.listdir("/proc/self/fd"))


@pytest.fixture
def lowered_nofile_limit():
    """Senkt RLIMIT_NOFILE fuer die Dauer des Tests (kuenstliche Verengung, analog dem #747-Repro
    unter echtem `ulimit -n`). Wird danach zuverlaessig zurueckgesetzt (kein Leck in andere Tests).
    Lowering the SOFT limit does not affect already-open descriptors — it only rejects further
    `open()`/`os.pipe()` calls once the (lowered) ceiling is hit, exactly like the real crash."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    lowered = min(soft, 200)
    resource.setrlimit(resource.RLIMIT_NOFILE, (lowered, hard))
    try:
        yield lowered
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest_factory():
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
            "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05}}}), "utf-8")
        return out
    return _fake


def test_resolve_rdb_storage_builds_explicit_instance_for_sqlite():
    """#747 — eine sqlite-URL wird als eigenstaendige `RDBStorage`-Instanz konstruiert (nicht als
    blosse, weiterhin-nur-eine-URL-Zeichenkette), damit der Aufrufer `.engine.dispose()` aufrufen
    kann. Nicht-sqlite-URLs (Postgres-Opt-in) bleiben unveraendert Strings."""
    storage = ro._resolve_rdb_storage("sqlite:///:memory:")
    assert isinstance(storage, ro.optuna.storages.RDBStorage)
    assert hasattr(storage, "engine")

    passthrough = ro._resolve_rdb_storage("postgresql://u:p@db/opt")
    assert passthrough == "postgresql://u:p@db/opt"


def test_dispose_storage_closes_connection_pool(tmp_path):
    """#747 — `_dispose_storage` schliesst den Connection-Pool einer `RDBStorage` (0 offene
    Connections danach) und ist ein No-Op fuer Objekte ohne `.engine` (Test-Doubles, None).

    File-basierte SQLite-URL (nicht `:memory:`): `:memory:` nutzt SQLAlchemys `SingletonThreadPool`
    (kein `checkedin()`); jede REAL im Sweep genutzte Study-Datei (`resolve_storage`) nutzt den
    Datei-Pfad und damit den Standard-`QueuePool`, den auch #747 selbst betrifft."""
    storage = ro.optuna.storages.RDBStorage(f"sqlite:///{tmp_path / 'x.db'}")
    study = ro.optuna.create_study(storage=storage, study_name="t", load_if_exists=True)
    study.optimize(lambda t: t.suggest_float("x", 0, 1), n_trials=3)
    assert storage.engine.pool.checkedin() > 0

    ro._dispose_storage(storage)
    assert storage.engine.pool.checkedin() == 0

    ro._dispose_storage(None)          # no .engine attribute at all
    ro._dispose_storage(object())      # no .engine attribute at all


def test_optimize_symbol_disposes_engine_before_returning(tmp_path, monkeypatch):
    """#747 Kernfix — nach `optimize_symbol` ist die zugehoerige RDBStorage-Engine disposed (leerer
    Connection-Pool), nicht mehr offen gehalten."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory())

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)

    rdb_storage = study._etoro_rdb_storage
    assert isinstance(rdb_storage, ro.optuna.storages.RDBStorage)
    assert rdb_storage.engine.pool.checkedin() == 0
    # study.trials remains usable afterwards (lazy reconnect) — dispose is not terminal.
    assert len(study.trials) == 1


def test_optimize_symbol_disposes_even_on_optimize_exception(tmp_path, monkeypatch):
    """#747 — Dispose greift auch, wenn `study.optimize()` eine nicht von `catch=` abgefangene
    Exception wirft (defense in depth gegen FD-Lecks auf dem Fehlerpfad, orthogonal zu #748)."""
    _isolate(monkeypatch, tmp_path)

    captured = {}

    def _boom(self, *a, **k):
        captured["storage"] = self._storage
        raise RuntimeError("boom")

    monkeypatch.setattr(ro.optuna.study.Study, "optimize", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)

    backend = getattr(captured["storage"], "_backend", None)
    assert backend is not None
    assert backend.engine.pool.checkedin() == 0


def test_family_aggregation_helpers_dispose_after_reading_trials(tmp_path, monkeypatch):
    """#747 — `_family_n_from_studies`/`_family_period_returns_from_studies` lesen `study.trials`
    NACH Phase-1-Dispose (lazy Reconnect) und muessen danach selbst wieder disposen — sonst waeren
    nach dieser Aggregation wieder ALLE Studies gleichzeitig offen (derselbe Erschoepfungs-
    Mechanismus, nur zeitlich verschoben)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory())

    pairs = [("SmaCrossoverStrategy", f"SYM{i}.ETORO", "test") for i in range(5)]
    studies = [ro.optimize_symbol(s, sym, n_trials=1) for s, sym, _ in pairs]
    # Phase 1 already disposed each; nothing open at this point.
    assert all(st._etoro_rdb_storage.engine.pool.checkedin() == 0 for st in studies)

    sweep._family_n_from_studies(pairs, studies)
    assert all(st._etoro_rdb_storage.engine.pool.checkedin() == 0 for st in studies), (
        "_family_n_from_studies must dispose again after its own lazy-reconnect read of study.trials"
    )

    sweep._family_period_returns_from_studies(pairs, studies)
    assert all(st._etoro_rdb_storage.engine.pool.checkedin() == 0 for st in studies), (
        "_family_period_returns_from_studies must dispose again after its own read of study.trials"
    )


def test_per_symbol_studies_fd_count_stays_bounded_under_low_ulimit(tmp_path, monkeypatch, lowered_nofile_limit):
    """#747-Regressionstest (analog dem Issue-eigenen `repro_fd_fix3.py`): 200 synthetische
    Per-Symbol-Studies unter kuenstlich verengtem `ulimit -n` (200) laufen vollstaendig durch, OHNE
    dass die Zahl offener File-Deskriptoren proportional zur Anzahl verarbeiteter Studies waechst.

    Vor dem Fix wuchs dies deterministisch +2 FDs/Study (siehe Issue #747 Abschnitt 1.2) — bei
    ulimit=200 waere das bereits nach ~90-95 Studies uebergelaufen; 200 Studies waeren also mit dem
    alten Code unter diesem Limit gar nicht durchgelaufen.
    """
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory())

    baseline = _open_fd_count()
    n_studies = 200
    for i in range(n_studies):
        ro.optimize_symbol("SmaCrossoverStrategy", f"SYM{i}.ETORO", n_trials=1)
        if i % 25 == 0 or i == n_studies - 1:
            current = _open_fd_count()
            growth = current - baseline
            assert growth < 40, (
                f"FD-Zahl waechst mit der Study-Zahl (i={i}): baseline={baseline}, "
                f"aktuell={current} (+{growth}) — Engine-Disposal (#747) greift nicht."
            )
