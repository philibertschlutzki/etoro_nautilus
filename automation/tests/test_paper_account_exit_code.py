"""Falscher Key (Konto passt nicht zu ETORO_PAPER_ACCOUNT_CID): eigener Exit-Code, den systemd nicht neu startet."""
from pathlib import Path

from automation import momentum_ls_run as run

UNIT = Path(run.__file__).resolve().parent.parent / "deploy" / "systemd" / "etoro-paper-bot.service"


def test_mismatch_exit_code_is_distinct_and_not_restarted_by_systemd():
    codes = {run.EXIT_PAPER_ACCOUNT_MISMATCH, run.EXIT_INCUBATION_NOT_DEMO, run.EXIT_ALREADY_RUNNING}
    assert len(codes) == 3
    line = next(ln for ln in UNIT.read_text("utf-8").splitlines() if ln.startswith("RestartPreventExitStatus="))
    prevented = set(line.split("=", 1)[1].split())
    assert {str(run.EXIT_PAPER_ACCOUNT_MISMATCH), str(run.EXIT_INCUBATION_NOT_DEMO),
            str(run.EXIT_ALREADY_RUNNING)} <= prevented


def test_main_uses_the_mismatch_exit_code():
    src = Path(run.__file__).read_text("utf-8")
    assert "sys.exit(EXIT_PAPER_ACCOUNT_MISMATCH)" in src
