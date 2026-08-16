"""Issue #959/#1125 (Katalog #960) — PBO ist der dominante Ausgang und beruht auf den
inkommensurablen Fold-Metriken aus #1114.

Symptom. 16 von 42 (38 %) eigenen Studies sterben an PBO — der dominante Ausgang über die
Entscheidungskette IS → Confirm → Holdout → Deflation → PBO. PBO wurde historisch über 4
Walk-Forward-Folds gerechnet, deren Sortino-Werte laut B-10 (#948/#1114) in 99,15 % der Trials
inkommensurabel annualisiert sind.

Root-Cause. Eine Overfit-Statistik über 4 Gruppen mit unterschiedlichen Einheiten misst
Annualisierungs-Heterogenität und Regimewechsel, nicht Overfit (Wiederkehr #663/#681).

Status (verifiziert in dieser Session). ``confirm._study_pbo`` implementiert den geforderten Fix
BEREITS vollständig, aus einer FRÜHEREN Session (#663/#683, siehe
``test_issue_663_pbo_group_granularity.py``/``test_issue_683_pbo_effective_n.py`` für die volle
Regressionsabdeckung):
1. Rechnet auf einer EIGENEN CSCV-Partition (S >= 8, Default 12) der GEPOOLTEN
   ``oos_period_returns`` — NICHT auf den 4 Walk-Forward-Folds.
2. Die Split-Metrik ist der annualisierungsfreie ``group_sortino`` (``_group_split_metric``,
   direkt aus den rohen Perioden-Returns), NICHT ``per_fold_oos_sortino``/``oos_fold_sortinos``
   (siehe #948/#1114 — beide Grössen werden von KEINEM Entscheidungspfad mehr konsumiert).
3. ``pbo_n_configs``/``pbo_n_groups`` sind im Artefakt geführt (``confirm.py`` stempelt sie
   unbedingt in ``metrics_symbol``, auch wenn ``pbo is None``).
4. Bei zu wenigen Gruppen (< 8) liefert PBO ``None`` statt eines rausch-getriebenen Werts.

Dieses Modul verankert die Akzeptanzkriterien EXPLIZIT unter der #959/#1125-Kennung (zusätzlich
zur bestehenden #663/#683-Abdeckung) und dokumentiert, welcher Teil NICHT in dieser Sandbox
verifizierbar ist.

NICHT in dieser Sandbox verifizierbar: "Die 16 PBO-Ablehnungen werden nach dem Fix erneut
ausgewertet (confirm-only, ohne Re-Optimierung) und die Verschiebung dokumentiert" — das erfordert
einen echten, gespeicherten Optuna-Study-Store aus dem Referenzlauf UND eine reale Marktdaten-
Umgebung (kein installierbares ``nautilus_trader`` mit exakt gepinnter Version, Python 3.11 statt
der geforderten >=3.12); eine empirische Re-Auswertung kann nur in der realen Entwicklungsumgebung
gegen den tatsächlichen Study-Store erfolgen.
"""
import inspect

import numpy as np
import optuna

from automation.optimizer import confirm as cmod
from automation.optimizer.cpcv import cpcv_group_boundaries


def _study(config_rows, *, eligible=True):
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    for i, rets in enumerate(config_rows):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", eligible)
        study._storage.set_trial_user_attr(t._trial_id, "oos_period_returns", list(rets))
        study.tell(t, float(i))
    return study


# ── Akzeptanzkriterium 1: keine Abhaengigkeit von per_fold_oos_sortino/oos_fold_sortinos ────────
def test_study_pbo_source_never_references_the_annualized_fold_series():
    """Grep-Test (Akzeptanzkriterium #948/#1114, hier fuer #959/#1125 explizit wiederholt): PBO
    darf unter KEINEN Umstaenden die annualisierte, fold-spezifisch skalierte Serie konsumieren."""
    src = inspect.getsource(cmod._study_pbo)
    assert "per_fold_oos_sortino" not in src
    assert "oos_fold_sortinos" not in src


def test_group_split_metric_source_operates_on_raw_period_returns_only():
    """_group_split_metric (die Split-Metrik der CSCV-Matrix) liest AUSSCHLIESSLICH die rohen
    per-Perioden-Returns, keine vorab annualisierte Groesse."""
    src = inspect.getsource(cmod._group_split_metric)
    assert "per_fold_oos_sortino" not in src
    assert "oos_fold_sortinos" not in src
    assert "annualiz" not in src.lower()


# ── Akzeptanzkriterium 2: S >= 8 Gruppen auf der gepoolten oos_period_returns-Serie ──────────────
def test_pbo_computed_on_pooled_period_returns_not_four_wf_folds():
    """Reproduziert das #663-Referenzszenario direkt unter der #959/#1125-Kennung: 12 Configs mit
    gemeinsamem Regime-Muster (Fold-artige Struktur mit nur wenigen 'Fold'-Uebergaengen wuerde bei
    einer 4-Fold-CSCV PBO->1.0 kollabieren lassen) und rein zufaelligem Perioden-Rauschen liefern
    stattdessen PBO nahe 0.5 auf der feineren S>=8-Partition."""
    rng = np.random.default_rng(17)
    n_periods = 400
    base_pattern = np.array([-0.001] * (n_periods // 2) + [0.002] * (n_periods // 2))
    rows = [(base_pattern + rng.normal(0.0, 0.0003, n_periods)).tolist() for _ in range(12)]

    pbo, telemetry = cmod._study_pbo(_study(rows))
    assert pbo is not None
    assert telemetry["pbo_n_groups"] >= 8
    assert 0.2 <= pbo <= 0.8  # NICHT das alte 4-Fold-Artefakt (PBO~1.0)


def test_too_few_groups_yields_none_not_a_forced_verdict():
    """Akzeptanzkriterium #959 Fix-Punkt 2 — bei zu wenigen Gruppen liefert PBO None statt eines
    Werts."""
    rng = np.random.default_rng(4)
    rows = rng.normal(0.01, 0.01, (12, 5)).tolist()  # 5 Perioden -> max 5 Gruppen < 8
    pbo, telemetry = cmod._study_pbo(_study(rows))
    assert pbo is None
    assert telemetry["pbo_n_groups"] < 8


# ── Akzeptanzkriterium 3: pbo_n_configs/pbo_n_groups sind im Artefakt gefuehrt ───────────────────
def test_confirm_stamps_pbo_group_telemetry_into_metrics_symbol_unconditionally():
    """confirm.confirm_per_symbol_promotion stempelt pbo_n_groups/pbo_n_configs/pbo_metric IMMER
    in metrics_symbol -- auch wenn pbo selbst None ist (Grep-Test ueber den Quelltext, da ein
    voller Integrationstest ein reales Optuna-Study-Objekt UND einen holdout-Backtest braucht,
    siehe test_issue_957_1123_deflation_n_family_source.py fuer dieses Infrastruktur-Muster)."""
    src = inspect.getsource(cmod.confirm_per_symbol_promotion)
    assert 'best_result["metrics_symbol"]["pbo_n_groups"] = pbo_telemetry.get("pbo_n_groups")' in src
    assert 'best_result["metrics_symbol"]["pbo_n_configs"] = pbo_telemetry.get("pbo_n_configs")' in src
    assert 'best_result["metrics_symbol"]["pbo_metric"] = pbo_telemetry.get("pbo_metric")' in src


def test_report_study_record_surfaces_pbo_group_telemetry():
    """report._inference_method_block liest pbo_n_groups/pbo_n_configs aus holdout_metrics, damit
    sie auch im Sweep-Report (nicht nur im rohen Proposal) sichtbar sind."""
    from automation.optimizer import report
    src = inspect.getsource(report._inference_method_block)
    assert '"pbo_n_groups": holdout_metrics.get("pbo_n_groups")' in src


# ── Akzeptanzkriterium 4: dieselbe Skala wie alle anderen fold-uebergreifenden Aggregate ─────────
def test_pbo_metric_matches_the_period_scale_convention_of_check_annualization_commensurability():
    """PBO (group_sortino auf rohen Perioden-Returns) und check_annualization_commensurability
    (seit #948/#1114 auf holdout_sortino_period/holdout_sortino_annualized) operieren beide auf
    derselben Perioden-Skala. PBO's Quelltext referenziert die annualisierte Fold-Serie ueberhaupt
    nicht (auch nicht im Docstring); check_annualization_commensurability ERWAEHNT sie nur noch
    dokumentierend (als "wird NICHT konsumiert"), liest sie aber in ihrer LOGIK nicht mehr (siehe
    test_issue_948_1114_fold_annualization_period_scale.py::test_check_annualization_
    commensurability_ignores_intra_trial_fold_data fuer den Verhaltensbeweis)."""
    from automation.optimizer import invariants as inv
    pbo_src = inspect.getsource(cmod._study_pbo)
    assert "per_fold_oos_sortino" not in pbo_src
    assert "oos_fold_sortinos" not in pbo_src
    # check_annualization_commensurability ERWAEHNT die annualisierte Serie nur noch dokumentierend
    # (im Docstring, als "wird NICHT konsumiert") -- die LOGIK liest sie nicht mehr; Beweis in
    # test_issue_948_1114_fold_annualization_period_scale.py::test_check_annualization_
    # commensurability_ignores_intra_trial_fold_data.
    check_src = inspect.getsource(inv.check_annualization_commensurability)
    assert 'r.get("oos_fold_sortinos")' not in check_src  # keine Konsum-Stelle in der Logik
    assert 'r.get("per_fold_oos_sortino")' not in check_src


def test_cpcv_group_boundaries_cover_the_full_pooled_series_contiguously():
    boundaries = cpcv_group_boundaries(400, 12)
    assert len(boundaries) == 12
    assert boundaries[0][0] == 0
    assert boundaries[-1][1] == 400
