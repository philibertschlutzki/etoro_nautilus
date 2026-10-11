import signal
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CODE = textwrap.dedent("""
    import logging, os, signal, sys
    sys.path.insert(0, %r)
    logging.basicConfig(level=logging.INFO, format="%%(levelname)s %%(message)s", stream=sys.stdout)
    from automation.process_diagnostics import install_crash_diagnostics
    install_crash_diagnostics(logging.getLogger("t"))
    {action}
""")


def _run(action: str):
    return subprocess.run([sys.executable, "-c", _CODE.format(action=action) % str(ROOT)],
                          capture_output=True, text=True, timeout=60)


def test_start_und_regulaeres_ende_werden_protokolliert():
    p = _run("pass")
    assert p.returncode == 0
    assert "[PROZESS] Start pid=" in p.stdout and "[PROZESS-ENDE]" in p.stdout


def test_sighup_wird_protokolliert_und_beendet_den_prozess():
    p = _run("os.kill(os.getpid(), signal.SIGHUP)")
    assert p.returncode == -signal.SIGHUP
    assert "Signal SIGHUP" in p.stdout


def test_install_ist_idempotent():
    p = _run("install_crash_diagnostics(logging.getLogger('t'))")
    assert p.stdout.count("[PROZESS] Start pid=") == 1
