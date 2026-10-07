"""Issue #1384 (GH #1286) — Abschluss: AGENTS.md-Block, README, Versionen bleiben (ausser der abgeleiteten
``params_schema_version``-Signatur mit #1383)."""
import json
import re
from pathlib import Path

from automation.optimizer import invariants

_REPO = Path(__file__).resolve().parents[2]
_GH = {1372: 1274, 1373: 1275, 1374: 1276, 1375: 1277, 1376: 1278, 1377: 1279, 1378: 1280, 1379: 1281,
       1380: 1282, 1381: 1283, 1382: 1284, 1383: 1285, 1384: 1286}


def _agents() -> str:
    return (_REPO / "automation" / "AGENTS.md").read_text("utf-8")


def test_agents_block_is_present_once_with_all_gh_numbers():
    text = _agents()
    heading = "## Issue-Katalog #1372–#1384 — Historien-Tiefe, OneDay-Achse, Lauf-Zulässigkeit"
    assert text.count(heading) == 1
    block = text[text.index(heading):]
    assert "#…" not in block                                           # keine offenen Platzhalter
    assert "GitHub-Issues #1274–#1286" in block
    for cat, gh in _GH.items():
        assert f"| **#{cat}** (#{gh}) |" in block, (cat, gh)


def test_agents_block_follows_the_pitfall_483_block_and_carries_pitfalls_494_to_503():
    text = _agents()
    assert text.index("## Neue Pitfalls #483–#493") < text.index("## Issue-Katalog #1372–#1384")
    for n in range(494, 504):
        assert len(re.findall(rf"^### \S+ Pitfall #{n} — ", text, re.M)) == 1, n
    assert "**Sperrvermerke (#1384).**" in text


def test_versions_stay_unchanged_and_params_schema_version_is_not_a_config_key():
    cfg = json.loads((_REPO / "automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] == 27
    assert cfg["simulation_semantics_version"] == 9
    assert "params_schema_version" not in cfg                          # abgeleitete Signatur (#1351)
    from automation.api_backfiller import CATALOG_SCHEMA_VERSION
    assert CATALOG_SCHEMA_VERSION == 2
    assert invariants.check_semantics_version_coherence(0).passed is True


def test_readme_documents_profiles_and_the_fetch_window():
    readme = (_REPO / "README.md").read_text("utf-8")
    for needle in ("config_profile materialize smoke", "ETORO_CONFIG_DIR", "`smoke`", "`daily`",
                   "bar_axis", "inception_bounds.json", "max_catalog_staleness_d_oneday"):
        assert needle in readme, needle
