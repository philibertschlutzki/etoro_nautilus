"""Issue #1102 (Katalog #935) — ``n_family[symbol]`` widersprach ``Σ n_family_stage1[symbol][*]``
um Faktor 2,8–5,1 (ASML 622 vs. 1722, NVDA 434 vs. 1737, PLTR 318 vs. 1624, TSLA 435 vs. 1600).

Root-Cause: ``report.py``'s ``cross_study['n_family']['observed_at_report_time']`` kam aus
``sweep._family_n_from_proposals`` (summiert ``deflation_n_eligible`` — eine ENGERE, seit #784/#822
veraltete Grundgesamtheit), waehrend ``n_family_stage1`` aus ``deflation_n_family`` (die
TATSAECHLICH an ``confirm.py`` fuer die DSR-Multiplizitaetskorrektur durchgereichte #822-
Grundgesamtheit) stammt — zwei UNABHAENGIG berechnete Zahlen fuer denselben Begriff.
``check_n_family_partition`` (severity ``high``) feuerte zwar korrekt, hatte aber keine
Entscheidungskonsequenz.

Fix: ``n_family[symbol]`` wird jetzt DIREKT aus der Summe seiner eigenen ``n_family_stage1``-
Zerlegung abgeleitet (genau EINE Quelle) — die Schranke ist damit tautologisch erfuellt, und
``check_n_family_partition`` steht seither auf severity ``blocking`` (kann nur noch durch eine
kuenftige Regression verletzt werden, die die getrennte Berechnung wieder einfuehrt).
"""
import json

import optuna

from automation.optimizer import invariants as inv
from automation.optimizer import report
from automation.optimizer.report import _family_n_stages


# ── Unit-Ebene: dieselbe Zwei-Zeilen-Sequenz wie report._build_report ───────────────────────────
def test_derivation_from_stage1_is_tautologically_coherent_with_the_866_catalog_numbers():
    """Reproduziert das #935-Referenzsymptom: n_family_stage1 traegt die GROESSERE, korrekte Zahl
    (1722 fuer ASML) -- die neue Ableitung liefert SIE, nicht die kleinere 622."""
    studies_out = [
        {"symbol": "ASML.ETORO", "strategy": "DonchianRegimeBreakoutStrategy",
         "n_family_stage1": 900},
        {"symbol": "ASML.ETORO", "strategy": "TrendPullbackStrategy", "n_family_stage1": 822},
    ]
    n_family_stage1, _ = _family_n_stages(studies_out)
    n_family_by_symbol = {
        symbol: sum(per_strategy.values()) for symbol, per_strategy in n_family_stage1.items()
    }
    assert n_family_by_symbol == {"ASML.ETORO": 1722}
    result = inv.check_n_family_partition(n_family_by_symbol, n_family_stage1)
    assert result.passed is True
    assert result.severity == "blocking"


def test_derivation_never_diverges_regardless_of_fixture_shape():
    """Eigenschaftstest: fuer JEDE Kombination aus Studies ist die Ableitung per Konstruktion
    kohaerent -- check_n_family_partition kann nur noch durch eine erneute PARALLELE Berechnung
    (die Regression, die dieser Fix ausschliesst) verletzt werden."""
    studies_out = [
        {"symbol": "PLTR.ETORO", "strategy": "A", "n_family_stage1": 5},
        {"symbol": "PLTR.ETORO", "strategy": "B", "n_family_stage1": 0},
        {"symbol": "PLTR.ETORO", "strategy": "C", "n_family_stage1": None,
         "n_selection_statistic_available": 77},
        {"symbol": "TSLA.ETORO", "strategy": "A", "n_family_stage1": 200},
    ]
    n_family_stage1, _ = _family_n_stages(studies_out)
    n_family_by_symbol = {
        symbol: sum(per_strategy.values()) for symbol, per_strategy in n_family_stage1.items()
    }
    result = inv.check_n_family_partition(n_family_by_symbol, n_family_stage1)
    assert result.passed is True


# ── check_n_family_partition severity ────────────────────────────────────────────────────────────
def test_check_n_family_partition_severity_is_now_blocking():
    result = inv.check_n_family_partition({}, {})
    assert result.severity == "blocking"


def test_check_n_family_partition_still_fails_on_a_genuine_gap_with_blocking_severity():
    """Falls eine kuenftige Regression die getrennte Berechnung reintroduziert, muss der Check
    dies weiterhin erkennen -- jetzt mit Gate-Wirkung (severity blocking)."""
    result = inv.check_n_family_partition(
        {"TSLA.ETORO": 622}, {"TSLA.ETORO": {"A": 900, "B": 822}})
    assert result.passed is False
    assert result.severity == "blocking"


# ── End-to-End: report.generate_sweep_report ─────────────────────────────────────────────────────
def _make_study(storage_url: str, study_name: str, n: int = 4):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", i < 2)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("oos_gate_deltas", {})
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if i < 2 else "unevaluable",
            "base": 1.0, "divergence": 0.0, "dd_penalty": 0.0, "param_pen": 0.0,
            "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0,
        })
        study.tell(trial, float(i))
    return study


def _proposal_with_deflation_n_family(n_family: int) -> dict:
    return {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            # Issue #935-Referenzsymptom: deflation_n_eligible (die ALTE, engere Quelle) traegt
            # bewusst einen ANDEREN, kleineren Wert als deflation_n_family -- der Report darf NICHT
            # mehr aus dem ersten Feld ableiten.
            "deflation_n_eligible": 2, "deflation_n_family": n_family,
            "deflation_n_family_effective": n_family, "deflation_n_effective": n_family,
        }},
    }


def test_report_n_family_matches_stage1_sum_end_to_end(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    out_path = report.generate_sweep_report(
        [_proposal_with_deflation_n_family(400)], run_id="testrun-935",
        started_at_utc="2026-08-14T00:00:00.000+00:00", wallclock_s=1,
        cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    n_family = data["cross_study"]["n_family"]["observed_at_report_time"]
    n_family_stage1 = data["cross_study"]["n_family_stage1"]
    assert n_family["A.ETORO"] == 400
    assert n_family["A.ETORO"] == sum(n_family_stage1["A.ETORO"].values())
    # Die ALTE Quelle (deflation_n_eligible=2) darf NICHT mehr durchscheinen.
    assert n_family["A.ETORO"] != 2
    partition_check = next(
        c for c in data["invariant_checks"] if c["name"] == "check_n_family_partition")
    assert partition_check["passed"] is True
    assert partition_check["severity"] == "blocking"
