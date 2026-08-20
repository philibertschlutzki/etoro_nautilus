"""Issue #1052/#1201 (P1, Katalog #1196-1221) — ``check_family_n_stability`` blockiert ohne Zahlen.

Symptom: blockierend in 5/14 Läufen, Detailtext in allen fünf Fällen wortgleich: "1 Symbol(e) mit
Abweichung zwischen eingefrorener und beobachteter Familien-Multiplizitaet — Zwischenreport oder
erneute #1158-Filter-Divergenz." Weder ``frozen``, noch ``observed``, noch das Delta, noch die
betroffene Study werden genannt.

Fix:
1. Detailtext nennt jetzt ``frozen``, ``observed``, ``delta`` UND (wenn ``frozen_stage1``/
   ``observed_stage1`` uebergeben werden) die konkret betroffene(n) Study/Studies je Symbol.
2. Ein ``discriminator``-Feld ("REAL_FILTER_DIVERGENCE"/"INTERMEDIATE_REPORT"/
   "UNKNOWN_SWEEP_COMPLETION_STATE") entscheidet ueber ``sweep_completed`` zwischen den beiden
   Hypothesen (Zwischenreport vs. echte #1158-Filter-Divergenz)."""
from automation.optimizer import invariants as inv


def test_detail_text_names_frozen_observed_delta_and_studies():
    result = inv.check_family_n_stability(
        {"NVDA.ETORO": 36}, {"NVDA.ETORO": 40},
        frozen_stage1={"NVDA.ETORO": {"AdxAtrMomentum": 10, "SqueezeBreakout": 26}},
        observed_stage1={"NVDA.ETORO": {"AdxAtrMomentum": 14, "SqueezeBreakout": 26}},
        sweep_completed=True,
    )
    assert result.passed is False
    assert "frozen=36" in result.detail
    assert "observed=40" in result.detail
    assert "delta=4" in result.detail
    assert "AdxAtrMomentum" in result.detail
    # SqueezeBreakout stimmt ueberein (26==26) und darf NICHT als offending Study erscheinen.
    assert "SqueezeBreakout" not in result.actual["NVDA.ETORO"]["by_study"]
    assert result.actual["NVDA.ETORO"]["by_study"]["AdxAtrMomentum"] == {
        "frozen": 10, "observed": 14, "delta": 4,
    }


def test_discriminator_marks_completed_sweep_as_real_divergence():
    result = inv.check_family_n_stability(
        {"NVDA.ETORO": 36}, {"NVDA.ETORO": 40}, sweep_completed=True)
    assert result.actual["NVDA.ETORO"]["discriminator"] == "REAL_FILTER_DIVERGENCE"


def test_discriminator_marks_intermediate_report_as_benign():
    result = inv.check_family_n_stability(
        {"NVDA.ETORO": 36}, {"NVDA.ETORO": 40}, sweep_completed=False)
    assert result.actual["NVDA.ETORO"]["discriminator"] == "INTERMEDIATE_REPORT"


def test_discriminator_unknown_when_sweep_completed_not_provided():
    result = inv.check_family_n_stability({"NVDA.ETORO": 36}, {"NVDA.ETORO": 40})
    assert result.actual["NVDA.ETORO"]["discriminator"] == "UNKNOWN_SWEEP_COMPLETION_STATE"


def test_by_study_is_empty_without_stage1_breakdowns_backward_compatible():
    """Legacy-Aufrufer (kein frozen_stage1/observed_stage1) — die bestehenden Symbol-Ebene-Felder
    bleiben unveraendert, by_study bleibt schlicht leer statt zu crashen."""
    result = inv.check_family_n_stability({"NVDA.ETORO": 36}, {"NVDA.ETORO": 40})
    assert result.actual["NVDA.ETORO"]["by_study"] == {}
    assert result.actual["NVDA.ETORO"]["frozen"] == 36
    assert result.actual["NVDA.ETORO"]["difference"] == 4
