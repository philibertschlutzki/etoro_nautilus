"""Veröffentlicht die Paper-Trading-Berichte (``logs/paper_reports/*.md``) per Git in dieses Repository.

Läuft per Cron (stündlich) NEBEN ``paper_report``. Es arbeitet in einem eigenen, schlanken Klon
(``PAPER_REPORT_REPO_DIR``, Standard ``~/etoro_nautilus_reports``) und rührt Arbeitsverzeichnis, Bot-Checkout und
dessen uncommittete Änderungen nie an. Es werden ausschliesslich ``logs/paper_reports/*.md`` hinzugefügt (kein
``git add -A``), es werden nie Dateien gelöscht und nie Orders gesendet. Ein Push-Fehler bricht nichts ab (Exit 1,
Meldung im Cron-Log). Zugangsdaten: ``gh auth git-credential`` (``gh`` muss eingeloggt sein); es wird keine
Git-Konfiguration verändert und kein Token geschrieben.

Umgebung: ``PAPER_REPORT_REPO_DIR`` (Klon), ``PAPER_REPORT_GIT_BRANCH`` (Standard ``main``).

Cron (stündlich, versetzt zu paper_report)::

    5 * * * * cd /home/<user>/etoro_nautilus && flock -n /tmp/paper_report_publish.lock ./venv/bin/python -m automation.paper_report_publish >> logs/paper_report_publish.log 2>&1
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "logs" / "paper_reports"
TARGET_REL = Path("logs") / "paper_reports"
DEFAULT_CLONE = Path.home() / "etoro_nautilus_reports"
CREDENTIAL_HELPER = "!gh auth git-credential"


class PublishError(RuntimeError):
    pass


def _auth(use_helper: bool) -> list[str]:
    return ["-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}"] if use_helper else []


def _git(clone: Path, *args: str, helper: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(clone), *_auth(helper), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and proc.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} fehlgeschlagen: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc


def ensure_clone(clone: Path, origin_url: str, branch: str, use_helper: bool = True) -> None:
    if (clone / ".git").exists():
        return
    clone.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", *_auth(use_helper), "clone", "-q", "--depth", "1", "--branch", branch, origin_url, str(clone)],
        capture_output=True, text=True, timeout=300, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode != 0:
        raise PublishError(f"git clone fehlgeschlagen: {(proc.stderr or proc.stdout).strip()[:500]}")


def origin_of(project_root: Path = PROJECT_ROOT) -> str:
    proc = subprocess.run(["git", "-C", str(project_root), "remote", "get-url", "origin"], capture_output=True,
                          text=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise PublishError("Kein origin-Remote im Projekt gefunden.")
    return proc.stdout.strip()


def publish(source: Path = SOURCE_DIR, clone: Path | None = None, *, branch: str = "main", origin_url: str | None = None,
            now: datetime | None = None, use_helper: bool = True) -> bool:
    """True, wenn ein Commit gepusht wurde; False, wenn nichts Neues vorlag."""
    now = now or datetime.now(timezone.utc)
    files = sorted(source.glob("*.md")) if source.is_dir() else []
    if not files:
        return False
    clone = clone or Path(os.environ.get("PAPER_REPORT_REPO_DIR") or DEFAULT_CLONE)
    ensure_clone(clone, origin_url or origin_of(), branch, use_helper)
    _git(clone, "fetch", "-q", "--depth", "1", "origin", branch, helper=use_helper)
    _git(clone, "checkout", "-q", "-B", branch, f"origin/{branch}")
    target = clone / TARGET_REL
    target.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copyfile(f, target / f.name)
    _git(clone, "add", "--", *[str(TARGET_REL / f.name) for f in files])
    if _git(clone, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git(clone, "-c", "user.name=paper-report", "-c", "user.email=paper-report@localhost", "commit", "-q", "-m",
         f"Paper-Bericht {now.strftime('%Y-%m-%d %H:%M UTC')}")
    _git(clone, "push", "-q", "origin", f"HEAD:{branch}", helper=use_helper)
    return True


def main() -> int:
    try:
        pushed = publish(branch=os.environ.get("PAPER_REPORT_GIT_BRANCH") or "main")
    except (PublishError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} FEHLER: {exc}", file=sys.stderr)
        return 1
    if pushed:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} Bericht gepusht.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
