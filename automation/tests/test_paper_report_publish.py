from __future__ import annotations

import subprocess

from automation import paper_report_publish as pub


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def test_publish_pushes_only_reports_and_is_idempotent(tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        _git(seed, "config", k, v)
    (seed / "README").write_text("x")
    _git(seed, "add", "README")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", str(remote), "main")
    src = tmp_path / "src"
    src.mkdir()
    (src / "paper_report_latest.md").write_text("# Bericht\n")
    (src / "ignore.txt").write_text("nein")
    clone = tmp_path / "clone"
    kw = dict(origin_url=str(remote), use_helper=False)
    assert pub.publish(src, clone, **kw) is True
    assert pub.publish(src, clone, **kw) is False      # nichts Neues
    out = subprocess.run(["git", "-C", str(remote), "ls-tree", "-r", "--name-only", "main"], capture_output=True,
                         text=True, check=True).stdout.split()
    assert "logs/paper_reports/paper_report_latest.md" in out and "README" in out
    assert not any(n.endswith("ignore.txt") for n in out)
    (src / "paper_report_latest.md").write_text("# Bericht 2\n")
    assert pub.publish(src, clone, **kw) is True


def test_publish_without_reports_does_nothing(tmp_path):
    assert pub.publish(tmp_path / "leer", tmp_path / "clone", origin_url="x", use_helper=False) is False
