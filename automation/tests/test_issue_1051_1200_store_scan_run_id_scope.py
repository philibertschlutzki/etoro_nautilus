"""Issue #1051/#1200 (P1, Katalog #1196-1221) — ``check_store_scan_coherence`` ist per Konstruktion
unerfuellbar.

Symptom: ``n_own`` in {56, 70, 98, 98, 112} — ausschliesslich Vielfache von ``n_studies`` (14).

Root-Cause: ``report._build_report`` baute ``_already_seen_pairs`` (den Fast-Path, der ein
``proposal_*.json``-Fund SOFORT als 'eigen' zaehlt, OHNE dessen Trials auf ``run_id`` zu pruefen)
aus dem ROHEN ``proposals``-Parameter — jedem vom Aufrufer uebergebenen Eintrag, AUCH wenn dessen
Study weiter oben als Fremdlauf erkannt und via ``continue`` von ``studies_out``/
``filtered_proposals`` ausgeschlossen wurde. Bei einem warm-gestarteten Lauf enthaelt ``proposals``
haeufig (strategy, symbol)-Paare AELTERER Laeufe desselben Stores; jedes ``proposal_*.json`` mit
passendem Paar-Namen auf der Platte zaehlte dadurch als 'eigen', unabhaengig von den TATSAECHLICHEN
``run_id``-Trial-Stempeln — das erklaert exakt die beobachtete "Vielfaches von n_studies"-Signatur.

Fix: ``_already_seen_pairs`` wird jetzt aus ``filtered_proposals`` gebildet (nur die Teilmenge, die
den run_id-Eigentumsnachweis TATSAECHLICH bestanden hat) — jedes andere Proposal auf der Platte
durchlaeuft den echten, trial-scoped Nachweis im Store-Scan-Loop."""
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

import optuna

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

from automation.optimizer import report as rpt


def _write_proposal(work: Path, strategy: str, symbol: str) -> Path:
    payload = {"strategy": strategy, "symbol": symbol, "status": "REJECTED_ON_HOLDOUT"}
    path = work / f"proposal_{strategy}_{symbol}.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


def _wire_minimal_config(tmp_path, monkeypatch, storage_url: str):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "tournament.json").write_text(json.dumps({}), "utf-8")
    monkeypatch.setattr(rpt, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(rpt, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)


def test_proposal_for_a_purely_foreign_run_study_is_not_double_counted_as_own(tmp_path, monkeypatch):
    """Reproduziert die #1051-Signatur: eine Study, deren Trials AUSSCHLIESSLICH einem VORLAUF
    gehoeren, wird trotzdem als ``proposals``-Eintrag an ``_build_report`` uebergeben (z. B. weil
    der Aufrufer den Store-weiten Proposal-Bestand eines warm-gestarteten Sweeps mitfuehrt). Vor
    dem Fix zaehlte diese Study trotzdem als ``n_own`` (Fast-Path ueber die blosse Paar-Anwesenheit
    in ``proposals``); nach dem Fix durchlaeuft sie den echten, trial-scoped Nachweis und wird
    korrekt als ``n_foreign`` klassifiziert. Ein zweites, GENUIN eigenes Proposal haelt
    ``studies_out`` nicht-leer (sonst greift der unabhaengige #1023-Fail-Loud-Wächter fuer
    "ausschliesslich Fremdlauf-Kohorte", der dieses Szenario sonst verdeckt)."""
    work = tmp_path / "work"
    work.mkdir()
    storage_url = f"sqlite:///{work / 'shared.db'}"
    _wire_minimal_config(tmp_path, monkeypatch, storage_url)
    monkeypatch.setattr(rpt, "WORK", work)

    old_study_name = "study_OldStrategy_SYM_ETORO"
    old_study = optuna.create_study(study_name=old_study_name, storage=storage_url,
                                    direction="maximize", load_if_exists=True)
    for i in range(3):
        trial = old_study.ask()
        trial.set_user_attr("run_id", "run_prior_warm_start")
        old_study.tell(trial, float(i))
    _write_proposal(work, "OldStrategy", "SYM.ETORO")

    new_study_name = "study_NewStrategy_SYM_ETORO"
    new_study = optuna.create_study(study_name=new_study_name, storage=storage_url,
                                    direction="maximize", load_if_exists=True)
    for i in range(3):
        trial = new_study.ask()
        trial.set_user_attr("run_id", "run_current")
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("reward_terms", {
            "branch": "eligible", "base": 1.0, "divergence": 0.1, "dd_penalty": 0.1,
            "param_pen": 0.1, "turnover": 0.1, "fold_dispersion": 0.1, "tie_breaker": 0.1,
        })
        new_study.tell(trial, float(i))
    _write_proposal(work, "NewStrategy", "SYM.ETORO")

    old_proposal = {"strategy": "OldStrategy", "symbol": "SYM.ETORO",
                    "status": "REJECTED_ON_HOLDOUT"}
    new_proposal = {"strategy": "NewStrategy", "symbol": "SYM.ETORO",
                    "status": "REJECTED_ON_HOLDOUT",
                    "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
                    "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
                    "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
                    "holdout": {"symbol": {"deflated_sr0": 0.1, "deflated_dsr": 0.8,
                                           "deflation_dsr_z": 1.2, "deflation_n_eligible": 3,
                                           "deflation_n_family_effective": 3,
                                           "deflation_n_effective": 3}}}
    report = rpt._build_report(
        proposals=[old_proposal, new_proposal], run_id="run_current",
        started_at_utc="2026-08-20T00:00:00Z", wallclock_s=1.0, cli_args={},
        reports_dir=tmp_path,
    )
    store_scan = report["store_scan"]
    assert store_scan["n_own"] == 1, (
        f"Nur NewStrategy ist genuin eigen; OldStrategy (rein fremdlauf) darf nicht mitzaehlen "
        f"(store_scan={store_scan})"
    )
    assert store_scan["n_foreign"] == 1
    assert len(report["studies"]) == 1
    assert report["studies"][0]["strategy"] == "NewStrategy"
    assert len(report["studies_excluded_foreign_run"]) == 1


def test_own_run_proposal_still_counts_as_own(tmp_path, monkeypatch):
    """Regressionsschutz: der Fix darf den genuinen Eigentumsfall (die Study TRAEGT Trials dieses
    Laufs) nicht brechen."""
    work = tmp_path / "work"
    work.mkdir()
    storage_url = f"sqlite:///{work / 'shared.db'}"
    _wire_minimal_config(tmp_path, monkeypatch, storage_url)
    monkeypatch.setattr(rpt, "WORK", work)

    study_name = "study_NewStrategy_SYM_ETORO"
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    for i in range(3):
        trial = study.ask()
        trial.set_user_attr("run_id", "run_current")
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("reward_terms", {
            "branch": "eligible", "base": 1.0, "divergence": 0.1, "dd_penalty": 0.1,
            "param_pen": 0.1, "turnover": 0.1, "fold_dispersion": 0.1, "tie_breaker": 0.1,
        })
        study.tell(trial, float(i))

    _write_proposal(work, "NewStrategy", "SYM.ETORO")

    proposal = {"strategy": "NewStrategy", "symbol": "SYM.ETORO", "status": "REJECTED_ON_HOLDOUT",
                "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
                "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
                "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
                "holdout": {"symbol": {"deflated_sr0": 0.1, "deflated_dsr": 0.8,
                                       "deflation_dsr_z": 1.2, "deflation_n_eligible": 3,
                                       "deflation_n_family_effective": 3,
                                       "deflation_n_effective": 3}}}
    report = rpt._build_report(
        proposals=[proposal], run_id="run_current", started_at_utc="2026-08-20T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    store_scan = report["store_scan"]
    assert store_scan["n_own"] == 1
    assert store_scan["n_foreign"] == 0
    assert len(report["studies"]) == 1
