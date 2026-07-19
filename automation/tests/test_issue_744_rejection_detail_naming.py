"""Issue #744 — Namens-Entschärfung `is_rejection_detail` vs.
`is_rejection_detail_override`/`holdout_reject_detail`.

Seit #654/#671 ist ``holdout_reject_detail``/``is_rejection_detail_override`` bereits das
erstklassige, korrekte Feld für die tatsächliche Promotion-Ablehnung; der modale IS-Study-Grund
landet separat unter ``dominant_is_rejection_detail``. Dieser Test pinnt die verbleibende #744-
Härtung: (1) ``_dominant_is_rejection_detail`` trägt eine explizite Docstring-Warnung, (2) das
Proposal spiegelt denselben Wert zusätzlich unter dem unmissverständlich benannten
``legacy_modal_is_rejection_detail``.
"""
import json
from pathlib import Path

from automation.optimizer import confirm


class _T:
    def __init__(self, detail):
        self.user_attrs = {"is_rejection_detail": detail} if detail is not None else {}


class _Study:
    study_name = "study_SmaCrossoverStrategy_TSLA_ETORO"
    best_value = -9.9

    def __init__(self, details):
        self.trials = [_T(d) for d in details]


def test_dominant_is_rejection_detail_docstring_warns_against_confusion():
    doc = confirm._dominant_is_rejection_detail.__doc__ or ""
    assert "holdout_reject_detail" in doc
    assert "NICHT" in doc or "NIE" in doc


def test_proposal_mirrors_legacy_modal_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    study = _Study(["REJECT_OOS_WINDOW_UNREACHABLE", "REJECT_OOS_WINDOW_UNREACHABLE",
                    "REJECT_OOS_INACTIVE"])
    promotion = {
        "status": "REJECTED_ON_HOLDOUT", "symbol_params": {"sma_period": 10},
        "R_symbol": -9.9, "R_global": -9.0, "promotion_margin": 0.10,
        "metrics_symbol": {}, "metrics_global": {},
        "is_rejection_detail_override": "REJECT_HOLDOUT_GATE",
    }
    path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion)
    data = json.loads(Path(path).read_text("utf-8"))

    assert data["dominant_is_rejection_detail"] == "REJECT_OOS_WINDOW_UNREACHABLE"
    assert data["legacy_modal_is_rejection_detail"] == "REJECT_OOS_WINDOW_UNREACHABLE"
    # Die Promotion-Ursache (holdout_reject_detail/is_rejection_detail) bleibt strikt getrennt vom
    # modalen IS-Grund — genau die #654-Invariante, die #744 nicht aufweicht.
    assert data["holdout_reject_detail"] == "REJECT_HOLDOUT_GATE"
    assert data["legacy_modal_is_rejection_detail"] != data["holdout_reject_detail"]


def test_proposal_legacy_modal_alias_none_when_no_is_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    study = _Study([None, None])
    promotion = {
        "status": "READY_FOR_PR", "symbol_params": {"sma_period": 10},
        "R_symbol": 1.2, "R_global": 0.5, "promotion_margin": 0.10,
        "metrics_symbol": {}, "metrics_global": {},
    }
    path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion)
    data = json.loads(Path(path).read_text("utf-8"))
    assert data["legacy_modal_is_rejection_detail"] is None
