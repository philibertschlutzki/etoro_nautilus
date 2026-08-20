"""Issue #1070/#1220 (P2, Katalog #1196-1221) — Champion-Closed-Loop bleibt unwirksam.

Symptom: ``check_champion_seed_coverage`` failt in allen Läufen (92,9-100,0% ``strategy_defaults``).
``check_champion_corroboration_reachable``: ``max(corroboration_count) = 1 < 2``.
``check_champion_writeback_reachability``: 14 Versuche, 0 Writebacks (13× ``NO_ENTRY_FOR_PAIR``,
1× ``NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED``).

Root-Cause: Korroboration verlangt zwei unabhängige Läufe mit demselben Champion. Bei 0 Promotionen
und rotierender Symbolauswahl (bis zu 8 Läufe zwischen zwei Besuchen desselben Symbols) blieb die
Kandidatenmenge, die überhaupt einen admissiblen Champion-Store-Eintrag erreichte, zu schmal, um
genug Wiederholungen zu sehen.

Fix: ``champion_is_admissible`` lässt ``REJECT_DEFLATION_HETEROGENEOUS``/
``REJECT_PROMOTION_FAMILY_UNRESOLVABLE`` (Holdout SELBST bestanden, nur die nachgelagerte
Multiplizitätskorrektur lehnte ab) jetzt BEDINGT zu: ``holdout_return > 0`` UND bestandenes
``oos_min_psr``-Gate. ``_DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS`` selbst bleibt unverändert
(PBO/BOUNDARY seeden weiterhin nicht).
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


def _entry(*, holdout_reject_detail, holdout_return=None, psr_delta=None, r_symbol=0.5,
          r_global=0.0) -> dict:
    return {
        "strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO", "tier": "all",
        "params": {"sma_period": 20},
        "quality": {"R_symbol": r_symbol, "R_global": r_global, "reward": 1.0},
        "provenance": {"run_id": "run1", "source_trial_dir": "x",
                       "holdout_reject_detail": holdout_reject_detail,
                       "status_at_store": "REJECTED_ON_HOLDOUT",
                       "holdout_binding_gate": None,
                       "holdout_gate_deltas": ({"oos_min_psr": psr_delta}
                                               if psr_delta is not None else {}),
                       "holdout_return": holdout_return},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": None,
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run1", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 1, "last_seen_run": "run1", "last_R_symbol": r_symbol,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }


# ── champion_is_admissible: der neue bedingte Zweig ----------------------------------------------

@pytest.mark.parametrize("detail", ["REJECT_DEFLATION_HETEROGENEOUS",
                                    "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"])
def test_qualifying_deflation_rejection_is_admissible(detail):
    entry = _entry(holdout_reject_detail=detail, holdout_return=0.02, psr_delta=0.01)
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert (ok, reason) == (True, None)


@pytest.mark.parametrize("detail", ["REJECT_DEFLATION_HETEROGENEOUS",
                                    "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"])
def test_non_positive_holdout_return_is_not_qualified(detail):
    entry = _entry(holdout_reject_detail=detail, holdout_return=0.0, psr_delta=0.01)
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert (ok, reason) == (False, "DEFLATION_REJECTION_NOT_QUALIFIED")

    entry_neg = _entry(holdout_reject_detail=detail, holdout_return=-0.01, psr_delta=0.01)
    ok, reason = champions.champion_is_admissible(entry_neg, OPT_DATA)
    assert (ok, reason) == (False, "DEFLATION_REJECTION_NOT_QUALIFIED")


@pytest.mark.parametrize("detail", ["REJECT_DEFLATION_HETEROGENEOUS",
                                    "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"])
def test_failed_oos_min_psr_gate_is_not_qualified(detail):
    entry = _entry(holdout_reject_detail=detail, holdout_return=0.02, psr_delta=-0.01)
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert (ok, reason) == (False, "DEFLATION_REJECTION_NOT_QUALIFIED")


@pytest.mark.parametrize("detail", ["REJECT_DEFLATION_HETEROGENEOUS",
                                    "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"])
def test_missing_holdout_return_or_psr_delta_is_conservatively_not_qualified(detail):
    """Fehlende Telemetrie (Legacy-/Test-Aufrufer ohne die neuen Provenance-Felder) erfindet
    keinen Wert -- konservativ nicht zulaessig."""
    entry_missing_return = _entry(holdout_reject_detail=detail, holdout_return=None, psr_delta=0.01)
    assert champions.champion_is_admissible(entry_missing_return, OPT_DATA) == (
        False, "DEFLATION_REJECTION_NOT_QUALIFIED")

    entry_missing_psr = _entry(holdout_reject_detail=detail, holdout_return=0.02, psr_delta=None)
    assert champions.champion_is_admissible(entry_missing_psr, OPT_DATA) == (
        False, "DEFLATION_REJECTION_NOT_QUALIFIED")


def test_zero_psr_delta_exactly_at_the_gate_still_qualifies():
    """delta >= 0 (nicht > 0) -- ein Kandidat exakt an der Schwelle hat das Gate bestanden, nicht
    knapp verfehlt."""
    entry = _entry(holdout_reject_detail="REJECT_DEFLATION_HETEROGENEOUS",
                   holdout_return=0.01, psr_delta=0.0)
    assert champions.champion_is_admissible(entry, OPT_DATA) == (True, None)


def test_default_whitelist_is_unchanged():
    """Akzeptanzkriterium (Issue-Text): _DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS bleibt exakt
    wie vor #1220 -- die beiden neuen Codes werden NICHT hineingemischt."""
    assert champions._DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS == frozenset({
        "REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_BOOTSTRAP_CI",
    })


def test_pbo_and_boundary_rejections_remain_categorically_excluded():
    """Akzeptanzkriterium (Issue-Text): 'PBO/BOUNDARY dürfen weiterhin nicht seeden' -- selbst mit
    einem lupenreinen holdout_return/oos_min_psr-Nachweis bleiben diese Codes ausserhalb des neuen
    bedingten Zweigs (sie sind gar nicht in _CONDITIONALLY_ADMISSIBLE_DEFLATION_REJECT_DETAILS)."""
    for detail in ("REJECT_SELECTION_PBO", "REJECT_BOUNDARY_SOLUTION",
                  "HOLD_BOUNDARY_UNRESOLVED", "REJECT_BOUNDARY_SOLUTION_PERSISTENT"):
        assert detail not in champions._CONDITIONALLY_ADMISSIBLE_DEFLATION_REJECT_DETAILS
        entry = _entry(holdout_reject_detail=detail, holdout_return=0.05, psr_delta=0.05)
        ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
        assert (ok, reason) == (False, "REJECTION_NOT_ALLOWLISTED")


def test_explicit_operator_allowlisting_bypasses_the_quality_gate():
    """Ein Operator, der einen der beiden Codes explizit in
    optimizer.json['champion_admissible_reject_details'] eintraegt, bekommt weiterhin die
    unbedingte (ungegatete) Zulassung -- die neue Konstante ist nur ein DEFAULT, kein Override der
    deklarativen Config-Governance (#817)."""
    opt = {**OPT_DATA, "champion_admissible_reject_details": [
        "REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_BOOTSTRAP_CI", "REJECT_DEFLATION_HETEROGENEOUS"]}
    entry = _entry(holdout_reject_detail="REJECT_DEFLATION_HETEROGENEOUS",
                   holdout_return=-5.0, psr_delta=-5.0)  # eindeutig NICHT qualifiziert
    ok, reason = champions.champion_is_admissible(entry, opt)
    assert (ok, reason) == (True, None)


# ── _build_entry_from_promotion: holdout_return-Stempelung ---------------------------------------

def test_build_entry_from_promotion_stamps_holdout_return():
    promotion = {
        "symbol_params": {"sma_period": 20}, "R_symbol": 0.4, "R_global": 0.1,
        "trial_dir": "x", "is_rejection_detail_override": "REJECT_DEFLATION_HETEROGENEOUS",
        "status": "REJECTED_ON_HOLDOUT",
        "metrics_symbol": {"oos_total_return": 0.033, "holdout_gate_deltas": {"oos_min_psr": 0.02},
                           "holdout_binding_gate": None},
    }

    class _FakeStudy:
        best_value = 1.0
        directions = ["maximize"]

    entry = champions._build_entry_from_promotion(
        _FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
        catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1",
    )
    assert entry["provenance"]["holdout_return"] == pytest.approx(0.033)


# ── Kein Effekt auf die ANDEREN Reject-Details (Regressionsschutz) -------------------------------

def test_reject_holdout_dsr_drop_remains_unconditionally_admissible():
    """Die BEREITS gültige (unbedingte) Zulassung fuer die beiden Original-Codes darf durch den
    neuen Zweig nicht veraendert werden -- kein holdout_return/oos_min_psr noetig."""
    entry = _entry(holdout_reject_detail="REJECT_HOLDOUT_DSR_DROP",
                   holdout_return=None, psr_delta=None)
    assert champions.champion_is_admissible(entry, OPT_DATA) == (True, None)


def test_reject_holdout_gate_path_is_unaffected():
    entry = _entry(holdout_reject_detail="REJECT_HOLDOUT_GATE", holdout_return=0.05, psr_delta=0.05)
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert (ok, reason) == (False, "REJECTION_NOT_ALLOWLISTED")
