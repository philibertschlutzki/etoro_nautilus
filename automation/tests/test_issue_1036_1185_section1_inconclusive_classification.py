"""Issue #1036/#1185 (P2) — Abschnitt 1 nennt einen INCONCLUSIVE-Check als "blockierenden FAIL".

Symptom. Abschnitt 1 (``_section_1_summary``) klassifizierte ``not c.get("passed", True)`` — für
``passed=None`` (tri-state INCONCLUSIVE, #995/#1147, Pitfall #413 in AGENTS.md: der Check konnte
seine Grundgesamtheit nicht herstellen) ist ``not None == True``, also GENAU dieselbe Klassifikation
wie ein nachgewiesenes FAIL (``passed=False``). Abschnitt 5.1/5.1b trennt diese beiden Zustände
bereits korrekt in zwei Tabellen -- Abschnitt 1 nannte denselben Check aber unter "BLOCKIERENDE
Invarianten-FAIL(s)".

Fix. Abschnitt 1 speist aus derselben Klassifikation wie 5.1/5.1b: ein eigener Satz für
INCONCLUSIVE-Checks ("Zusätzlich nicht auswertbar (blockierend): ..."), getrennt vom FAIL-Satz.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de


def _minimal_report(**overrides):
    base = {
        "run_id": "run-abc",
        "run_status": "complete",
        "started_at_utc": "2026-07-30T00:00:00Z",
        "wallclock_s": 7200.0,
        "cli_args": {"n_jobs": 8, "n_jobs_source": "CLI"},
        "symbols_completed": None,
        "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {},
            "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [],
            "boundary_solutions": [],
            "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def _section_1(report):
    return summary_de.generate_german_summary(report).split("## 2.")[0]


def test_mixed_fail_and_inconclusive_are_named_in_separate_sentences():
    checks = [
        inv.InvariantResult(
            name="check_trailing_stop_loss_share", passed=False, expected=0.5, actual=0.9,
            detail="ueberschritten.", severity="blocking",
        ).to_dict() | {"scope": "S/A.ETORO"},
        inv.InvariantResult(
            name="check_stop_loss_vs_bar_range", passed=None, expected="auswertbar", actual=None,
            detail="Grundgesamtheit nicht herstellbar.", severity="blocking", evaluable=False,
        ).to_dict() | {"scope": "S/A.ETORO"},
    ]
    report = _minimal_report(invariant_checks=checks)
    section_1 = _section_1(report)
    assert "**BLOCKIERENDE Invarianten-FAIL(s):** check_trailing_stop_loss_share" in section_1
    assert "check_stop_loss_vs_bar_range" not in section_1.split("Zusätzlich nicht auswertbar")[0]
    assert "Zusätzlich nicht auswertbar (blockierend): check_stop_loss_vs_bar_range" in section_1


def test_inconclusive_only_omits_the_fail_sentence():
    checks = [
        inv.InvariantResult(
            name="check_stop_loss_vs_bar_range", passed=None, expected="auswertbar", actual=None,
            detail="Grundgesamtheit nicht herstellbar.", severity="blocking", evaluable=False,
        ).to_dict() | {"scope": "global"},
    ]
    report = _minimal_report(invariant_checks=checks)
    section_1 = _section_1(report)
    assert "**BLOCKIERENDE Invarianten-FAIL(s):**" not in section_1
    assert "Zusätzlich nicht auswertbar (blockierend): check_stop_loss_vs_bar_range" in section_1


def test_fail_only_omits_the_inconclusive_sentence():
    checks = [
        inv.InvariantResult(
            name="check_trailing_stop_loss_share", passed=False, expected=0.5, actual=0.9,
            detail="ueberschritten.", severity="blocking",
        ).to_dict() | {"scope": "global"},
    ]
    report = _minimal_report(invariant_checks=checks)
    section_1 = _section_1(report)
    assert "**BLOCKIERENDE Invarianten-FAIL(s):** check_trailing_stop_loss_share" in section_1
    assert "Zusätzlich nicht auswertbar" not in section_1


def test_non_blocking_inconclusive_check_is_not_named():
    """Nur blockierende Checks tauchen ueberhaupt in Abschnitt 1 auf (unveraendert #849)."""
    checks = [
        inv.InvariantResult(
            name="check_reward_term_variance", passed=None, expected="auswertbar", actual=None,
            detail="nicht auswertbar.", severity="medium", evaluable=False,
        ).to_dict() | {"scope": "S/A.ETORO"},
    ]
    report = _minimal_report(invariant_checks=checks)
    section_1 = _section_1(report)
    assert "check_reward_term_variance" not in section_1


# ── Akzeptanzkriterium: Abschnitt 1 und 5.1/5.1b nennen dieselbe Menge ───────────────────────────
def test_section_1_set_matches_section_5_1_and_5_1b_sets():
    checks = [
        inv.InvariantResult(
            name="check_trailing_stop_loss_share", passed=False, expected=0.5, actual=0.9,
            detail="ueberschritten.", severity="blocking",
        ).to_dict() | {"scope": "S/A.ETORO"},
        inv.InvariantResult(
            name="check_effective_stop_distance", passed=False, expected=1.0, actual=0.2,
            detail="zu klein.", severity="blocking",
        ).to_dict() | {"scope": "S/A.ETORO"},
        inv.InvariantResult(
            name="check_stop_loss_vs_bar_range", passed=None, expected="auswertbar", actual=None,
            detail="Grundgesamtheit nicht herstellbar.", severity="blocking", evaluable=False,
        ).to_dict() | {"scope": "S/A.ETORO"},
    ]
    report = _minimal_report(invariant_checks=checks)
    full_text = summary_de.generate_german_summary(report)
    section_1 = full_text.split("## 2.")[0]

    fail_names_s1 = set()
    if "**BLOCKIERENDE Invarianten-FAIL(s):**" in section_1:
        fail_part = section_1.split("**BLOCKIERENDE Invarianten-FAIL(s):**")[1]
        fail_part = fail_part.split("Zusätzlich nicht auswertbar")[0]
        fail_names_s1 = {"check_trailing_stop_loss_share", "check_effective_stop_distance"}
        for n in fail_names_s1:
            assert n in fail_part
    inconclusive_names_s1 = set()
    if "Zusätzlich nicht auswertbar (blockierend):" in section_1:
        inconclusive_names_s1 = {"check_stop_loss_vs_bar_range"}
        for n in inconclusive_names_s1:
            assert n in section_1.split("Zusätzlich nicht auswertbar (blockierend):")[1]

    section_5_1 = full_text.split("### 5.1 Übersicht")[1].split("### 5.1b")[0]
    section_5_1b = full_text.split("### 5.1b")[1].split("### 5.2")[0]
    assert "check_trailing_stop_loss_share" in section_5_1
    assert "check_effective_stop_distance" in section_5_1
    assert "check_stop_loss_vs_bar_range" in section_5_1b
    # Kein INCONCLUSIVE-Check in der 5.1-FAIL-Tabelle, kein FAIL-Check in 5.1b.
    assert "check_stop_loss_vs_bar_range" not in section_5_1
