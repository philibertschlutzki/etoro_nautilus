"""Issue #1005/#1157 (Katalog #1170, P2) — drei Zahlen unter dem Namen ``deflation_n_family``.

Symptom: ``sweep_completed``-Ereignis: ``{PLTR: 581}`` / ``{NVDA: 350}``. ``cross_study.n_family.
frozen``: 1839 / 1743. ``cross_study.n_family.observed_at_report_time``: 1839 / 1744. Alle drei
heissen (im Ereignis- bzw. Berichts-Feldnamen) „familienweite Multiplizität".

Root-Cause: kein Bug in der WERT-Berechnung (jede der drei Zahlen war bereits korrekt fuer ihre
eigene, dokumentierte Semantik, siehe #1102/#1091) — sondern eine reine Namenskollision: derselbe
String ``deflation_n_family`` (im Sweep-Ereignis) bezeichnete eine ANDERE Grundgesamtheit als der
in ``report.py`` verschachtelte ``cross_study['n_family']`` (der wiederum selbst zwei Ansichten
--frozen/observed_at_report_time-- traegt), UND kollidierte semantisch mit ``n_family_stage1``
(PER-STRATEGIE-Zerlegung, keine Summe).

Fix: eindeutige Namen entlang der bereits dokumentierten Semantik:
- ``n_family_attempted`` — das Sweep-Ereignis (``sweep.py``'s ``sweep_completed``, vormals
  ``deflation_n_family``, Quelle ``_family_n_from_proposals``/``deflation_n_eligible``, #1102).
- ``n_family_stage1_sum_frozen`` / ``n_family_stage1_sum_observed`` — ``report.py``'s
  ``cross_study`` (vormals verschachtelt unter ``n_family.frozen``/``n_family.observed_at_report_
  time``), je die SUMME der ``n_family_stage1``-Zerlegung (#1091/#1102).

Konvention #1081 — der alte Feldname/die alte Struktur bleibt eine Sitzung lang als Uebergangs-
Alias mit IDENTISCHEM Wert erhalten (kein zweiter Berechnungspfad).
"""
import json
import sys
import types
import unittest.mock as mock

import optuna

# Issue #1005/#1157 — ``report._build_report`` reaches ``invariants.check_config_key_registry``,
# das einen UNGUARDEN lazy ``from automation.backtest_runner import ...`` durchfuehrt (pre-existing,
# nicht durch diesen Fix eingefuehrt) -- ``backtest_runner`` zieht ``nautilus_trader`` selbst auf
# Modulebene. Dieselbe Mock-Injektion wie ``test_issue_953_1119_stop_loss_vs_bar_range.py``/
# ``test_issue_1004_1156_store_scan_three_valued.py``, damit das ECHTE (reine Python-)
# ``backtest_runner`` in dieser Sandbox laedt (kein installierbares ``nautilus_trader``, Python 3.11
# statt der geforderten >=3.12).
if "nautilus_trader" not in sys.modules:
    class _MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = _MockModule(_mod)

from automation.optimizer import report
from automation.optimizer import sweep


# ── report.py: cross_study traegt die neuen, flachen Namen UND den alten Alias ────────────────────

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


def _proposal(n_family: int, n_family_frozen: int | None = None) -> dict:
    payload = {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            "deflation_n_eligible": 2, "deflation_n_family": n_family,
            "deflation_n_family_effective": n_family, "deflation_n_effective": n_family,
        }},
    }
    if n_family_frozen is not None:
        payload["deflation_n_family_frozen"] = n_family_frozen
    return payload


def test_cross_study_carries_the_new_flat_names_with_the_same_values_as_the_old_alias(
    tmp_path, monkeypatch,
):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    # frozen (777) bewusst != observed (400), damit ein etwaiges Verschmelzen der beiden Namen
    # (statt echter Disjunktheit) sichtbar würde.
    out_path = report.generate_sweep_report(
        [_proposal(400, n_family_frozen=777)], run_id="testrun-1157",
        started_at_utc="2026-08-18T00:00:00.000+00:00", wallclock_s=1,
        cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    cross = data["cross_study"]

    # Neue, eindeutige Namen.
    assert cross["n_family_stage1_sum_observed"]["A.ETORO"] == 400
    assert cross["n_family_stage1_sum_frozen"]["A.ETORO"] == 777

    # Konvention #1081 — Uebergangs-Alias eine Sitzung lang, bit-identischer Wert.
    assert cross["n_family"]["observed_at_report_time"] == cross["n_family_stage1_sum_observed"]
    assert cross["n_family"]["frozen"] == cross["n_family_stage1_sum_frozen"]


# ── sweep.py: sweep_completed traegt n_family_attempted UND den alten Alias ───────────────────────

class _CaptureHandler:
    def __init__(self):
        self.events = []

    def __call__(self, record):
        pass


def _capture_sweep_completed_event(monkeypatch, tmp_path, pairs, family_n_by_symbol):
    import logging

    class _Handler(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.DEBUG)
            self.events = []

        def emit(self, record):
            msg = record.getMessage()
            if "[JSON_EVENT]" in msg:
                try:
                    self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
                except Exception:
                    pass

    _GATE_CFG = {"walk_forward": {}, "gate1_buffer_days": 30,
                 "min_bars_per_param": 200, "min_oos_bars_per_fold": 500}
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"p_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    # Issue #625 — ``_family_n_from_proposals`` liest die Proposal-JSONs von der Platte; hier direkt
    # auf die gewuenschten Fixture-Werte umgeleitet, um den Sweep-Dispatch selbst nicht simulieren zu
    # muessen (derselbe Injektionspunkt, den #1102s End-to-End-Test fuer den Report-Pfad nutzt).
    monkeypatch.setattr(sweep, "_family_n_from_proposals", lambda proposals: dict(family_n_by_symbol))

    handler = _Handler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        sweep.run_per_symbol_sweep(
            [s for s, _, _ in pairs], sorted({sym for _, sym, _ in pairs}), tier="all", n_jobs=1,
            optimize_symbol=lambda *a, **k: object(),
            confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return [e for e in handler.events if e.get("event_type") == "sweep_completed"][0]


def test_sweep_completed_carries_n_family_attempted_and_the_legacy_alias(monkeypatch, tmp_path):
    evt = _capture_sweep_completed_event(
        monkeypatch, tmp_path, [("S1", "A.ETORO", "OK")], {"A.ETORO": 581})
    assert evt["n_family_attempted"] == {"A.ETORO": 581}
    # Issue #1254 (GH #1124) — die Konvention-#1081-Uebergangsfrist ist beendet: der alte Name
    # (``deflation_n_family``, behauptete "familienweite Multiplizitaet") ist umbenannt in
    # ``deflation_n_eligible_legacy`` (benennt die tatsaechliche, ENGERE Grundgesamtheit, die diese
    # Funktion seit jeher liefert), bit-identischer Wert.
    assert evt["deflation_n_eligible_legacy"] == evt["n_family_attempted"]
    assert "deflation_n_family" not in evt


# ── Akzeptanzkriterium: Namensdisjunktheit ueber run.json UND den Ereignisstrom ────────────────────

def test_the_three_renamed_quantities_use_pairwise_distinct_field_names(monkeypatch, tmp_path):
    """Keine zwei numerisch verschiedenen Groessen tragen im selben Lauf denselben Feldnamen: das
    Sweep-Ereignis (``n_family_attempted``), die eingefrorene Report-Summe
    (``n_family_stage1_sum_frozen``) und die zur Berichtszeit beobachtete Report-Summe
    (``n_family_stage1_sum_observed``) sind drei paarweise verschiedene Namen -- konstruiert mit drei
    paarweise verschiedenen Werten fuer dasselbe Symbol, damit eine kuenftige Namenskollision (die
    denselben Bug wie #1157 reintroduzieren wuerde) sofort sichtbar waere."""
    evt = _capture_sweep_completed_event(
        monkeypatch, tmp_path, [("S1", "A.ETORO", "OK")], {"A.ETORO": 350})

    sweep_dir = tmp_path / "study_store"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    out_path = report.generate_sweep_report(
        [_proposal(1743, n_family_frozen=1839)], run_id="testrun-1157-disjoint",
        started_at_utc="2026-08-18T00:00:00.000+00:00", wallclock_s=1,
        cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=tmp_path / "reports",
    )
    cross = json.loads(out_path.read_text("utf-8"))["cross_study"]

    values_by_name = {
        "n_family_attempted": evt["n_family_attempted"]["A.ETORO"],
        "n_family_stage1_sum_frozen": cross["n_family_stage1_sum_frozen"]["A.ETORO"],
        "n_family_stage1_sum_observed": cross["n_family_stage1_sum_observed"]["A.ETORO"],
    }
    # Drei paarweise verschiedene Werte (350 / 1839 / 1743) -- reproduziert die #1157-Symptomzahlen.
    assert len(set(values_by_name.values())) == 3
    # Drei paarweise verschiedene Feldnamen tragen sie -- die eigentliche Fix-Behauptung.
    assert len(set(values_by_name.keys())) == 3
    # ``n_family_stage1`` (die PER-STRATEGIE-Zerlegung, keine Summe) bleibt ein vierter, disjunkter
    # Name -- kollidiert weder mit den beiden Summen-Namen noch mit dem Ereignis-Feld.
    assert "n_family_stage1" in cross
    assert "n_family_stage1" not in values_by_name
