"""Issue #849 (P1) — 519× **None**: summary_de.py liest den falschen Schlüssel.

Root-Cause: ``invariants.InvariantResult.to_dict()`` schreibt den Schlüssel ``name``,
``report.py``'s ``INVARIANT_CHECK_FAILED``-Event mappt korrekt auf ``check``, aber
``summary_de.py:278`` las bislang ``check`` direkt vom ``to_dict()``-Ergebnis — ein Schlüssel, den
``to_dict()`` nie geschrieben hat. Jede FAIL-Zeile in Sektion 5 zeigte deshalb ``**None**`` statt
des Check-Namens.

Fix:
1. ``to_dict()`` gibt jetzt BEIDE Schlüssel aus (``name`` und ``check``, identischer Wert) —
   Übergangs-Vertrag, bis alle Konsumenten auf ``name`` migriert sind.
2. ``summary_de._check_name`` liest ``name`` zuerst, fällt auf ``check`` zurück (Pre-#849-Fixtures).
3. Sektion 5 ist auf zwei Ebenen umgebaut: 5.1 Übersicht (eine Zeile je Check, nach Schweregrad
   sortiert), 5.2 Details (höchstens ``summary_max_details_per_check`` Beispiele je Check).
4. Blockierende FAILs (``severity == 'blocking'``) erscheinen zusätzlich in Abschnitt 1.

Akzeptanzkriterien:
- AK-1: Kein ``**None**`` im erzeugten Bericht.
- AK-2: ``to_dict()['name'] == to_dict()['check']``.
- AK-3: Sektion 5.1 gibt für eine Kohorte mit mehreren Checks deren Zahlen aus.
- AK-4: Abschnitt 1 nennt jeden ``blocking``-FAIL namentlich.
- AK-5: Der Bericht bleibt bei ≥ 500 FAILs unter 200 Zeilen in Sektion 5.
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


# ── AK-2: to_dict()-Uebergangs-Vertrag ──────────────────────────────────────────────────────────
def test_to_dict_name_and_check_are_identical():
    result = inv.InvariantResult(
        name="check_holding_time_cap", passed=False, expected=0, actual=664,
        detail="664 Studies ueberschreiten die Zeitbox.", severity="blocking")
    d = result.to_dict()
    assert d["name"] == "check_holding_time_cap"
    assert d["check"] == "check_holding_time_cap"
    assert d["name"] == d["check"]


# ── AK-1: kein **None** im Bericht ───────────────────────────────────────────────────────────────
def test_no_none_placeholder_using_real_to_dict_output():
    checks = [
        inv.InvariantResult(
            name="check_reward_term_variance", passed=False, expected="varianz > 0", actual=0.0,
            detail="Reward-Term(e) praktisch inert.", severity="medium").to_dict(),
        inv.InvariantResult(
            name="check_holding_time_cap", passed=False, expected=0, actual=664,
            detail="664 Studies ueberschreiten die #714/GR-01-Zeitbox.", severity="blocking").to_dict(),
    ]
    for c in checks:
        c["scope"] = "global"
    report = _minimal_report(invariant_checks=checks)
    text = summary_de.generate_german_summary(report)
    assert "**None**" not in text
    assert "check_reward_term_variance" in text
    assert "check_holding_time_cap" in text


def test_legacy_fixture_with_only_check_key_still_resolves_a_name():
    """Regressionswaechter: ein Pre-#849-Fixture, das nur 'check' setzt (kein 'name'), zeigt
    weiterhin den Namen statt None — die Uebergangs-Fallback-Logik greift."""
    report = _minimal_report(invariant_checks=[
        {"check": "check_sr0_coherence", "passed": False, "scope": "S/A.ETORO", "detail": "…"},
    ])
    text = summary_de.generate_german_summary(report)
    assert "**None**" not in text
    assert "check_sr0_coherence" in text


# ── AK-3: Sektion 5.1 Übersicht mit realer Verteilung (aus dem Issue-Text) ──────────────────────
def test_section_5_1_overview_table_matches_distribution():
    distribution = {
        "check_reward_term_variance": (304, "medium"),
        "check_sr0_coherence": (115, "medium"),
        "check_inference_diagnostics_absent": (80, "medium"),
        "check_promotion_inference_coverage": (17, "medium"),
        "check_champion_writeback_reachability": (1, "medium"),
        "check_diagnosis_actionability": (1, "medium"),
        "check_holding_time_cap": (1, "blocking"),
    }
    checks = []
    for name, (n, severity) in distribution.items():
        for i in range(n):
            checks.append(
                inv.InvariantResult(
                    name=name, passed=False, expected=None, actual=None,
                    detail=f"Detail {i}", severity=severity,
                ).to_dict() | {"scope": f"scope-{i}"}
            )
    report = _minimal_report(invariant_checks=checks)
    text = summary_de.generate_german_summary(report)
    overview = text.split("### 5.1")[1].split("### 5.2")[0]
    for name, (n, severity) in distribution.items():
        assert name in overview
        assert f"| {name} | {n} |" in overview
    # check_holding_time_cap ist 'blocking' -> muss VOR den 'medium'-Checks in der Tabelle stehen.
    assert overview.index("check_holding_time_cap") < overview.index("check_reward_term_variance")


# ── AK-4: blockierende FAILs in Abschnitt 1 ──────────────────────────────────────────────────────
def test_blocking_fail_named_in_section_1():
    checks = [
        inv.InvariantResult(
            name="check_holding_time_cap", passed=False, expected=0, actual=664,
            detail="664 Studies ueberschreiten die Zeitbox.", severity="blocking",
        ).to_dict() | {"scope": "global"},
        inv.InvariantResult(
            name="check_reward_term_variance", passed=False, expected="varianz > 0", actual=0.0,
            detail="inert.", severity="medium",
        ).to_dict() | {"scope": "S/A.ETORO"},
    ]
    report = _minimal_report(invariant_checks=checks)
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "check_holding_time_cap" in section_1
    assert "check_reward_term_variance" not in section_1


def test_no_blocking_fails_omits_the_note_in_section_1():
    checks = [
        inv.InvariantResult(
            name="check_reward_term_variance", passed=False, expected="varianz > 0", actual=0.0,
            detail="inert.", severity="medium",
        ).to_dict() | {"scope": "S/A.ETORO"},
    ]
    report = _minimal_report(invariant_checks=checks)
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "BLOCKIERENDE" not in section_1


# ── AK-5: kompakt bei >= 500 FAILs ───────────────────────────────────────────────────────────────
def test_section_5_stays_compact_with_many_fails():
    checks = []
    for i in range(510):
        checks.append(
            inv.InvariantResult(
                name="check_reward_term_variance", passed=False, expected="varianz > 0",
                actual=0.0, detail=f"inert #{i}.", severity="medium",
            ).to_dict() | {"scope": f"S/{i}.ETORO"}
        )
    for i in range(5):
        checks.append(
            inv.InvariantResult(
                name="check_holding_time_cap", passed=False, expected=0, actual=i,
                detail=f"detail #{i}", severity="blocking",
            ).to_dict() | {"scope": f"S/{i}.ETORO"}
        )
    report = _minimal_report(invariant_checks=checks)
    text = summary_de.generate_german_summary(report)
    section_5 = text.split("## 5. Auffälligkeiten")[1]
    n_lines = section_5.count("\n")
    assert n_lines < 200
    assert "… und 505 weitere" in section_5
    assert "… und 0 weitere" not in section_5  # 5 <= max_details=5, keine "weitere"-Zeile


def test_custom_summary_max_details_per_check_is_honored():
    checks = [
        inv.InvariantResult(
            name="check_reward_term_variance", passed=False, expected=None, actual=None,
            detail=f"inert #{i}.", severity="medium",
        ).to_dict() | {"scope": f"S/{i}.ETORO"}
        for i in range(10)
    ]
    report = _minimal_report(invariant_checks=checks, summary_max_details_per_check=2)
    text = summary_de.generate_german_summary(report)
    details_section = text.split("### 5.2")[1].split("### 5.3")[0]
    assert details_section.count("- (scope=") == 2
    assert "… und 8 weitere" in details_section
