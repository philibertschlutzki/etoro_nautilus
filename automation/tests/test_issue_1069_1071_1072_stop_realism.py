"""Issue #1069/#1071/#1072 (Katalog #866-2, Kohorte C, Risikoschicht) — der ATR-Trailing-Stop
begrenzt den Verlust nicht; der realisierte Verlust ist von der Stopdistanz unabhängig.

#1069 — ``realized_stop_loss_ratio`` wird als erstklassiges, direkt sichtbares Report-Feld je
Study gemessen (derselbe Quotient, den ``check_effective_stop_distance`` intern bildet), statt nur
innerhalb des Invarianten-Checks verborgen zu bleiben.

#1071 (Pitfall #380-Klasse) — ``check_atr_scale_homogeneity``/``check_n_periods_homogeneity``
melden den MECHANISMUS (Floor-Bindung / degenerierter Nenner), den sie tatsächlich gemessen haben,
statt eine Ursache (Preis-Sprungstelle) zu behaupten, die sie nicht geprüft haben.

#1072 (Wiederkehr #1050/#1051) — neue Invariante ``check_stop_cost_ratio``: der konfigurierte
Stop-Abstand muss ein Mindestvielfaches der Round-Trip-Kosten betragen, sonst kann eine Position
den Stop strukturell nicht überleben, bevor die Kosten sie auffressen.
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record


# ── #1069: realized_stop_loss_ratio surfaced as a first-class per-study field ────────────────────
class _T:
    def __init__(self):
        self.value = 1.0
        self.params = {}
        self.user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_atr_median_bps": 2.0,
            "oos_gross_loss_mean_bps_trailing_stop": 124.22,
            # Issue #972/#1126 — realized_stop_loss_ratio ist seit diesem Fix die Median-Variante
            # (Pitfall #405 in AGENTS.md); fuer diesen Einzel-Trial-Fixture identisch zum Mittel oben.
            "oos_gross_loss_median_bps_trailing_stop": 124.22,
            "oos_n_trailing_stop_losses": 50,
            "sampled_params": {"atr_trailing_multiplier": 1.694},
            # Issue #1081/#1229 — die GEMESSENE, getaggte Stopdistanz (#1054/#1203): seit diesem Fix
            # der primaere Nenner von realized_stop_loss_ratio, nicht mehr k*ATR (siehe unten).
            "oos_stop_distance_bps_median": 3.39,
        }


class _S:
    trials = [_T()]
    best_value = 1.0
    user_attrs = {}


def test_realized_stop_loss_ratio_is_a_first_class_study_field():
    proposal = {"symbol": "TSLA.ETORO", "strategy": "DynamicBreakoutStrategy"}
    record, _checks = _study_record(proposal, _S())
    assert record["realized_stop_loss_ratio"] is not None
    # 124.22 / 3.39 (gemessene Distanz, #1054/#1203) ≈ 36.64 — Beweis B-3 im #866-Katalog
    # (DynamicBreakout, 36,66), seit #1081/#1229 gegen die GEMESSENE statt der modellierten Distanz.
    assert record["realized_stop_loss_ratio"] > 30.0
    # Issue #1081/#1229 — die vormalige, MODELLIERTE Basis (124.22 / (2.0 * 1.694) ≈ 36.67) bleibt
    # unter dem neuen Namen erhalten (Zero-Regression fuer Konsumenten, die sie explizit wollen).
    assert record["realized_stop_loss_ratio_vs_modelled"] is not None
    assert record["realized_stop_loss_ratio_vs_modelled"] > 30.0


def test_realized_stop_loss_ratio_is_none_without_full_telemetry():
    proposal = {"symbol": "X", "strategy": "S"}

    class _EmptyT:
        value = 1.0
        params = {}
        user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _EmptyS:
        trials = [_EmptyT()]
        best_value = 1.0
        user_attrs = {}

    record, _checks = _study_record(proposal, _EmptyS())
    assert record["realized_stop_loss_ratio"] is None


# ── #1071: check_atr_scale_homogeneity reports the mechanism, not a guessed cause ────────────────
def test_atr_scale_homogeneity_names_floor_binding_studies_not_a_price_jump():
    records = [
        {"strategy": "OpeningRangeBreakoutStrategy", "symbol": "TSLA.ETORO", "atr_median_bps": 27.665},
        {"strategy": "DynamicBreakoutStrategy", "symbol": "TSLA.ETORO", "atr_median_bps": 2.0},
    ]
    result = inv.check_atr_scale_homogeneity(
        records, atr_floor_bps_by_symbol={"TSLA.ETORO": 2.0})
    assert result.passed is False
    assert "Preis-Sprungstelle" not in result.detail or "Konfigurationsartefakt" in result.detail
    assert "Konfigurationsartefakt" in result.detail
    assert result.provenance["atr_floor_binding_studies"] == ["DynamicBreakoutStrategy/TSLA.ETORO"]


def test_atr_scale_homogeneity_without_floor_info_stays_cause_agnostic():
    records = [
        {"strategy": "A", "symbol": "X", "atr_median_bps": 30.0},
        {"strategy": "B", "symbol": "X", "atr_median_bps": 2.0},
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.passed is False
    assert "nicht bestätigt" in result.detail
    assert "Preis-Sprungstelle" not in result.detail.split(".")[0]  # keine unbedingte Behauptung


def test_n_periods_homogeneity_names_the_denominator_study():
    records = [
        {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO", "oos_n_periods_median": 1655.5},
        {"strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO", "oos_n_periods_median": 24.0},
    ]
    result = inv.check_n_periods_homogeneity(records)
    assert result.passed is False
    assert result.provenance["denominator_studies"]["TSLA.ETORO"] == "SqueezeBreakoutStrategy/TSLA.ETORO"


# ── #1072: check_stop_cost_ratio ─────────────────────────────────────────────────────────────────
def test_stop_cost_ratio_fails_on_the_866_catalog_reference_offenders():
    # DynamicBreakout: d = 0.85 * c_rt; HourlyMeanReversion: d = 0.87 * c_rt (Beweis B-3/E-1).
    c_rt = 4.0
    records = [
        {"strategy": "DynamicBreakoutStrategy", "symbol": "TSLA.ETORO",
         "atr_median_bps": 2.0, "atr_trailing_multiplier_median": 1.694},  # d=3.39, 3.39/4=0.85
        {"strategy": "HourlyMeanReversionStrategy", "symbol": "TSLA.ETORO",
         "atr_median_bps": 2.361, "atr_trailing_multiplier_median": 1.470},  # d=3.47, 3.47/4=0.87
    ]
    result = inv.check_stop_cost_ratio(
        records, round_trip_cost_bps_by_symbol={"TSLA.ETORO": c_rt}, min_stop_to_cost_ratio=3.0)
    assert result.passed is False
    assert len(result.actual) == 2
    assert all(v < 3.0 for v in result.actual.values())


def test_stop_cost_ratio_passes_with_a_sufficiently_wide_stop():
    records = [{"strategy": "S", "symbol": "X", "atr_median_bps": 20.0,
                "atr_trailing_multiplier_median": 2.0}]  # d=40, c_rt=4 -> ratio=10
    result = inv.check_stop_cost_ratio(
        records, round_trip_cost_bps_by_symbol={"X": 4.0}, min_stop_to_cost_ratio=3.0)
    assert result.passed is True
    assert result.actual["S/X"] == 10.0


def test_stop_cost_ratio_skips_symbols_without_known_cost_basis():
    records = [{"strategy": "S", "symbol": "UNKNOWN.ETORO", "atr_median_bps": 2.0,
                "atr_trailing_multiplier_median": 1.0}]
    result = inv.check_stop_cost_ratio(records, round_trip_cost_bps_by_symbol={})
    assert result.passed is True
    assert result.actual is None
