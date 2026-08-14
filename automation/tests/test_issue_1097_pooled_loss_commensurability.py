"""Issue #1097 (Katalog #930) — gepoolte, trade-gewichtete Verlust-Mittelwerte
(``report._pooled_mean_of_trial_field``) statt medianbasierter Study-Aggregate, damit sie mit den
SUMMIERTEN Stichprobengrössen (``oos_n_losses``/``oos_n_trailing_stop_losses``) kommensurabel
bleiben. ``invariants.check_loss_metric_commensurability`` bewacht die daraus resultierende
Teilmengen-Schranke.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# ── report._pooled_mean_of_trial_field ───────────────────────────────────────────────────────────
def test_pooled_mean_weights_by_trial_sample_size():
    trial_attrs = [
        {"oos_gross_loss_mean_bps": 100.0, "oos_n_losses": 1},
        {"oos_gross_loss_mean_bps": 10.0, "oos_n_losses": 9},
    ]
    # (100*1 + 10*9) / 10 = 19.0 -- NICHT der einfache Median (55.0) der beiden Trial-Mittelwerte.
    result = rpt._pooled_mean_of_trial_field(
        trial_attrs, mean_field="oos_gross_loss_mean_bps", count_field="oos_n_losses")
    assert result == 19.0


def test_pooled_mean_ignores_trials_missing_either_field():
    trial_attrs = [
        {"oos_gross_loss_mean_bps": 100.0, "oos_n_losses": 1},
        {"oos_gross_loss_mean_bps": None, "oos_n_losses": 9},
        {"oos_gross_loss_mean_bps": 10.0, "oos_n_losses": 0},
    ]
    result = rpt._pooled_mean_of_trial_field(
        trial_attrs, mean_field="oos_gross_loss_mean_bps", count_field="oos_n_losses")
    assert result == 100.0


def test_pooled_mean_none_without_any_valid_trial():
    assert rpt._pooled_mean_of_trial_field(
        [], mean_field="oos_gross_loss_mean_bps", count_field="oos_n_losses") is None
    assert rpt._pooled_mean_of_trial_field(
        [{"oos_gross_loss_mean_bps": None}], mean_field="oos_gross_loss_mean_bps",
        count_field="oos_n_losses") is None


# ── invariants.check_loss_metric_commensurability ────────────────────────────────────────────────
def _rec(mean_all, n_all, mean_stop, n_stop):
    return {
        "strategy": "S", "symbol": "X",
        "oos_gross_loss_mean_bps_pooled": mean_all, "oos_n_losses": n_all,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": mean_stop,
        "oos_n_trailing_stop_losses": n_stop,
    }


def test_commensurability_passes_when_subset_sum_bound_holds():
    # Σ_all = 50*10=500 >= Σ_stop = 60*5=300.
    result = inv.check_loss_metric_commensurability([_rec(50.0, 10, 60.0, 5)])
    assert result.passed is True


def test_commensurability_fails_matching_930_reference_symptom():
    """Reproduziert das Messartefakt-Muster: Σ_stop uebersteigt Σ_all um Faktor bis 3,57."""
    # Σ_all = 10*10=100, Σ_stop = 40*10=400 -- eine Teilmenge kann nie mehr Verlust tragen als die
    # Gesamtmenge (bei nicht-negativen Verlusten).
    result = inv.check_loss_metric_commensurability([_rec(10.0, 10, 40.0, 10)])
    assert result.passed is False
    assert result.severity == "blocking"
    assert "S/X" in result.actual
    assert result.actual["S/X"]["sum_all_losses_bps"] == 100.0
    assert result.actual["S/X"]["sum_trailing_stop_losses_bps"] == 400.0


def test_commensurability_holds_as_a_tautology_when_stop_losses_are_a_true_subset():
    """Wenn die Stop-Verluste tatsaechlich eine Teilmenge der Gesamt-Verluste sind (der reale
    ökonomische Fall), ist die Schranke IMMER erfuellt — unabhaengig von den konkreten Werten."""
    for mean_all, n_all, mean_stop, n_stop in [
        (5.0, 100, 5.0, 100),  # Stop-Verluste == alle Verluste
        (20.0, 56, 20.0, 12),  # Stop-Verluste sind eine echte Teilmenge, gleicher Mittelwert
        (30.0, 200, 15.0, 50),
    ]:
        result = inv.check_loss_metric_commensurability([_rec(mean_all, n_all, mean_stop, n_stop)])
        assert result.passed is True


def test_commensurability_skips_studies_with_missing_pooled_fields():
    result = inv.check_loss_metric_commensurability([
        {"strategy": "S", "symbol": "X", "oos_gross_loss_mean_bps_pooled": None},
    ])
    assert result.passed is True
