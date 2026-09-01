"""Issue #1338 (GH #1232) — ``BAR_QUALITY_PROFILE`` führt jetzt auch ``frac_high_eq_low``,
``n_distinct_closes``, ``frac_identical_consecutive_closes`` und ``bar_coverage_expected_bins``
(vorher standen zwei der vier Ablehnungsgründe NUR im ``REJECT_DATA_DEGENERATE``-Ereignis, das nur
bei Ablehnung emittiert wird).
"""
import ast
import re
from pathlib import Path

from automation.optimizer.sweep_diagnostics import check_bar_quality


def _bar_quality_profile_dict_source() -> str:
    src = Path("automation/optimizer/sweep.py").read_text("utf-8")
    m = re.search(r'emit_execution_event\(_log, "BAR_QUALITY_PROFILE", \{(.*?)\n\s*\}\)', src, re.DOTALL)
    assert m, "BAR_QUALITY_PROFILE-Emission nicht gefunden"
    return m.group(1)


def test_profile_event_literal_contains_all_reason_capable_fields():
    """Meta-Test (Akzeptanzkriterium #1338): jede Kennzahl, die check_bar_quality in `reason`
    schreiben kann, ist Pflichtfeld des Profil-Ereignisses."""
    profile_src = _bar_quality_profile_dict_source()
    reason_capable_fields = [
        "ticks_per_bar_median", "frac_bars_single_tick", "frac_high_eq_low",
        "frac_identical_consecutive_closes", "n_distinct_closes", "frac_zero_true_range",
        "atr_median_bps", "bar_coverage_ratio", "intrabar_range_median_bps",
    ]
    for field in reason_capable_fields:
        assert f'"{field}"' in profile_src, f"{field} fehlt im BAR_QUALITY_PROFILE-Literal"


def test_profile_event_also_carries_expected_bins_denominator():
    profile_src = _bar_quality_profile_dict_source()
    assert '"bar_coverage_expected_bins"' in profile_src


def test_check_bar_quality_return_dict_is_a_superset_of_profile_fields():
    """Direkter funktionaler Nachweis: alle im Profil referenzierten Felder existieren tatsaechlich
    im Rueckgabewert von check_bar_quality (kein totes Feld im Event-Literal)."""
    result = check_bar_quality(
        [101.0, 102.0, 103.0] * 5, [99.0, 100.0, 101.0] * 5, [100.0, 101.0, 102.0] * 5,
        min_distinct_closes=1)
    for field in ("frac_high_eq_low", "n_distinct_closes", "frac_identical_consecutive_closes",
                  "bar_coverage_expected_bins", "intrabar_range_median_bps"):
        assert field in result
