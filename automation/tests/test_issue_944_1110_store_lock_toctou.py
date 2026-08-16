"""Issue #944/#1110 (Katalog #960) — Store-Lock: Dokumentation vs. Implementierung, TOCTOU-Haertung.

Root-Cause: ``AGENTS.md`` beschrieb den Sweep-Lock als „eine Lock-Datei je Symbol", implementiert
war (und ist) ein STORE-WEITER Lock (``{OPTIMIZER_WORK_DIR}/sweep/.run.lock``). Zusaetzlich hatte
der urspruengliche Erwerb (``lock_path.exists()`` gefolgt von einem separaten
``write_json_atomic``) ein TOCTOU-Fenster zwischen Pruefung und Schreiben.

Deckt ab:
  - ``manifest.WORK`` liest ``OPTIMIZER_WORK_DIR`` (die im Runbook dokumentierte Grundlage fuer
    „je Symbol ein eigenes Arbeitsverzeichnis").
  - Der Lock-Erwerb laeuft ueber ``fcntl.flock`` auf einem offen gehaltenen Deskriptor -- ein
    zweiter, ECHTER Prozess (nicht nur ein zweiter Aufruf im selben Interpreter) mit abweichender
    ``run_id`` gegen denselben Store scheitert mit ``SWEEP_CONCURRENT_RUN_DETECTED``, ein Prozess
    mit einem VOELLIG unabhaengigen ``OPTIMIZER_WORK_DIR`` bleibt unbeeinflusst.
  - Zwei Prozesse, die *gleichzeitig* (ueber eine ``multiprocessing.Barrier`` synchronisiert) um
    denselben Lock konkurrieren, lassen genau einen gewinnen -- das Akzeptanzkriterium aus #944
    („Der TOCTOU-Test (zwei Prozesse, gleichzeitiger Erwerb) laesst genau einen gewinnen.").
"""
import importlib
import multiprocessing
import os

import pytest


def test_manifest_work_respects_optimizer_work_dir_env(tmp_path, monkeypatch):
    custom = tmp_path / "custom_work"
    monkeypatch.setenv("OPTIMIZER_WORK_DIR", str(custom))
    from automation.optimizer import manifest
    importlib.reload(manifest)
    try:
        assert manifest.WORK == custom
    finally:
        monkeypatch.delenv("OPTIMIZER_WORK_DIR", raising=False)
        importlib.reload(manifest)


def test_manifest_work_defaults_without_env(monkeypatch):
    monkeypatch.delenv("OPTIMIZER_WORK_DIR", raising=False)
    from automation.optimizer import manifest
    importlib.reload(manifest)
    assert manifest.WORK == manifest.PROJECT_ROOT / "data" / "optimizer"


def test_independent_work_dirs_are_not_mutually_locked(tmp_path, monkeypatch):
    """Zwei Sweeps mit GETRENNTEM OPTIMIZER_WORK_DIR duerfen gleichzeitig einen Lock halten --
    genau die im Runbook (5.4) empfohlene Betriebsweise fuer parallelen Mehr-Symbol-Betrieb."""
    from automation.optimizer import sweep

    monkeypatch.setattr(sweep, "WORK", tmp_path / "tsla")
    sweep._acquire_sweep_run_lock("run_tsla")

    monkeypatch.setattr(sweep, "WORK", tmp_path / "nvda")
    sweep._acquire_sweep_run_lock("run_nvda")  # darf NICHT an "run_tsla" scheitern

    assert (tmp_path / "tsla" / "sweep" / ".run.lock").exists()
    assert (tmp_path / "nvda" / "sweep" / ".run.lock").exists()

    sweep._release_sweep_run_lock("run_tsla")
    sweep._release_sweep_run_lock("run_nvda")


def _race_for_lock(work_dir: str, run_id: str, barrier, result_queue) -> None:
    """Laeuft in einem EIGENEN Prozess (eigener Python-Interpreter, eigene Open-File-Descriptions)
    -- notwendig, weil ``fcntl.flock`` pro Open-File-Description gilt und ein reiner
    Thread-/In-Prozess-Test die eigentliche Kernel-Atomaritaet nicht pruefen wuerde."""
    import os as _os
    _os.environ["OPTIMIZER_WORK_DIR"] = work_dir
    from automation.optimizer import manifest as _manifest
    importlib.reload(_manifest)
    from automation.optimizer import sweep as _sweep
    importlib.reload(_sweep)

    barrier.wait()  # so gleichzeitig wie moeglich zuschlagen
    try:
        _sweep._acquire_sweep_run_lock(run_id)
        result_queue.put(("ok", run_id))
    except RuntimeError as exc:
        result_queue.put(("SWEEP_CONCURRENT_RUN_DETECTED" in str(exc) and "fail", run_id))


def test_toctou_concurrent_acquire_exactly_one_winner(tmp_path):
    """Akzeptanzkriterium #944: zwei Prozesse, gleichzeitiger Erwerb -- genau einer gewinnt."""
    ctx = multiprocessing.get_context("spawn")
    work_dir = str(tmp_path / "shared_store")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()

    procs = [
        ctx.Process(target=_race_for_lock, args=(work_dir, "run_a", barrier, result_queue)),
        ctx.Process(target=_race_for_lock, args=(work_dir, "run_b", barrier, result_queue)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    outcomes = [result_queue.get(timeout=5) for _ in range(2)]
    statuses = [status for status, _ in outcomes]
    assert statuses.count("ok") == 1, f"Erwartet genau einen Gewinner, erhalten: {outcomes}"
    assert statuses.count("fail") == 1, f"Erwartet genau einen Verlierer, erhalten: {outcomes}"
