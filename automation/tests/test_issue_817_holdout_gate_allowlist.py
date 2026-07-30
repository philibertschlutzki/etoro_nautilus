"""Issue #817 (P0, Katalog #817-#821, GitHub-Issue #749) — ``REJECT_HOLDOUT_GATE`` gehört nicht
auf die Champion-Allowlist.

Root-Cause: 70 von 76 Store-Einträgen trugen ``holdout_reject_detail == 'REJECT_HOLDOUT_GATE'`` —
ein Kandidat, der am Holdout-Gate SELBST gescheitert ist (ein gemessener OOS-Durchfaller), wurde
bislang gleichwertig zu ``REJECT_HOLDOUT_DSR_DROP``/``REJECT_HOLDOUT_BOOTSTRAP_CI`` behandelt
(Kandidaten, die den Holdout bestanden und nur an der Multiplizitäts-/Konfidenzkorrektur
gescheitert sind).

Fix: ``REJECT_HOLDOUT_GATE`` ist aus der einfachen Allowlist entfernt und nur noch zulässig, wenn
der Eintrag das aufgeschlüsselte bindende Gate (``holdout_binding_gate``, #786) trägt UND die
Verletzung innerhalb einer konfigurierbaren, relativen Marge (``champion_max_holdout_gate_
shortfall``) liegt. Die übrige Allowlist ist deklarativ aus
``optimizer.json['champion_admissible_reject_details']`` ableitbar.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import champions


CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_demote_after_runs": 2,
    "champion_enabled": True,
}


def _entry(*, holdout_binding_gate=None, holdout_gate_deltas=None, r_symbol=0.5,
          r_global=0.0) -> dict:
    return {
        "strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO", "tier": "all",
        "params": {"sma_period": 20},
        "quality": {"R_symbol": r_symbol, "R_global": r_global, "reward": 1.0},
        "provenance": {"run_id": "run1", "source_trial_dir": "x",
                       "holdout_reject_detail": "REJECT_HOLDOUT_GATE",
                       "status_at_store": "REJECTED_ON_HOLDOUT",
                       "holdout_binding_gate": holdout_binding_gate,
                       "holdout_gate_deltas": holdout_gate_deltas or {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": None,
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run1", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 1, "last_seen_run": "run1", "last_R_symbol": r_symbol,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }


# ── Unit-Tests direkt auf champion_is_admissible (Akzeptanzkriterien #1/#2) ────────────────────
def test_reject_holdout_gate_without_binding_gate_is_not_allowlisted():
    """Akzeptanzkriterium: Eintrag mit REJECT_HOLDOUT_GATE ohne holdout_binding_gate
    ⇒ (False, 'REJECTION_NOT_ALLOWLISTED')."""
    ok, reason = champions.champion_is_admissible(_entry(), OPT_DATA)
    assert (ok, reason) == (False, "REJECTION_NOT_ALLOWLISTED")


def test_reject_holdout_gate_within_shortfall_margin_is_admissible():
    """Akzeptanzkriterium: holdout_binding_gate='oos_min_psr', Shortfall unterhalb der Schwelle
    ⇒ (True, None). tournament.json oos_min_psr=0.75; delta=-0.03 ⇒ relative shortfall = 0.04."""
    tournament_cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    entry = _entry(holdout_binding_gate="oos_min_psr", holdout_gate_deltas={"oos_min_psr": -0.03})
    opt = {**OPT_DATA, "champion_max_holdout_gate_shortfall": 0.10}
    ok, reason = champions.champion_is_admissible(entry, opt, tournament_cfg=tournament_cfg)
    assert (ok, reason) == (True, None)


def test_reject_holdout_gate_beyond_shortfall_margin_is_rejected():
    """Akzeptanzkriterium: ... oberhalb ⇒ (False, 'HOLDOUT_GATE_SHORTFALL_TOO_LARGE')."""
    tournament_cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    entry = _entry(holdout_binding_gate="oos_min_psr", holdout_gate_deltas={"oos_min_psr": -0.60})
    opt = {**OPT_DATA, "champion_max_holdout_gate_shortfall": 0.10}
    ok, reason = champions.champion_is_admissible(entry, opt, tournament_cfg=tournament_cfg)
    assert (ok, reason) == (False, "HOLDOUT_GATE_SHORTFALL_TOO_LARGE")


def test_default_shortfall_margin_is_closed():
    """Fehlt champion_max_holdout_gate_shortfall in der Config, ist die Marge 0.0 -- praktisch
    geschlossen, bis ein Operator sie explizit oeffnet."""
    tournament_cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    entry = _entry(holdout_binding_gate="oos_min_psr", holdout_gate_deltas={"oos_min_psr": -0.001})
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA, tournament_cfg=tournament_cfg)
    assert (ok, reason) == (False, "HOLDOUT_GATE_SHORTFALL_TOO_LARGE")


def test_shortfall_unresolvable_threshold_is_not_allowlisted():
    """Ein bindendes Gate ohne aufloesbaren Schwellen-Key (z. B. ein unbekannter Name) liefert
    keinen erfundenen Shortfall-Wert -- konservativ nicht zulaessig."""
    entry = _entry(holdout_binding_gate="oos_min_totally_unknown_gate",
                   holdout_gate_deltas={"oos_min_totally_unknown_gate": -0.01})
    ok, reason = champions.champion_is_admissible(entry, {**OPT_DATA, "champion_max_holdout_gate_shortfall": 1.0},
                                                   tournament_cfg={})
    assert (ok, reason) == (False, "REJECTION_NOT_ALLOWLISTED")


def test_champion_admissible_reject_details_ignores_holdout_gate_if_misconfigured():
    """Ein Operator, der REJECT_HOLDOUT_GATE versehentlich in die flache Allowlist eintraegt,
    bekommt NICHT die schwaechere Pruefung -- die Klasse behaelt ihr eigenes, strengeres
    Kriterium (Shortfall-Marge)."""
    opt = {**OPT_DATA, "champion_admissible_reject_details": [
        "REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_GATE"]}
    ok, reason = champions.champion_is_admissible(_entry(), opt)
    assert (ok, reason) == (False, "REJECTION_NOT_ALLOWLISTED")


# ── Trockenlauf gegen den BESTEHENDEN Champion-Store (Akzeptanzkriterium #742/logs/champions) ───
def test_dry_run_against_existing_champion_store_yields_at_most_six_admissible():
    """Akzeptanzkriterium #817: ein Lauf ueber den bestehenden Store weist <= 6 zulaessige
    Eintraege aus, solange keine holdout_binding_gate-Daten vorliegen (die Alt-Einträge unter
    logs/champions/ wurden vor #786/#817 geschrieben und tragen daher niemals
    provenance.holdout_binding_gate)."""
    store_dir = Path("logs/champions")
    if not store_dir.exists():
        pytest.skip("logs/champions/ nicht vorhanden in dieser Umgebung")
    entries = []
    for p in sorted(store_dir.glob("*.json")):
        try:
            entries.append(json.loads(p.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    if not entries:
        pytest.skip("logs/champions/ enthaelt keine lesbaren Eintraege")
    tournament_cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    opt = {**OPT_DATA, "champion_min_R_symbol": 0.0}
    admissible = 0
    for entry in entries:
        ok, _reason = champions.champion_is_admissible(entry, opt, tournament_cfg=tournament_cfg)
        if ok:
            admissible += 1
    assert admissible <= 6, (
        f"{admissible} von {len(entries)} Alt-Eintraegen sind zulaessig -- erwartet <= 6, da keiner "
        "der Alt-Eintraege provenance.holdout_binding_gate traegt (vor #786/#817 geschrieben)."
    )
