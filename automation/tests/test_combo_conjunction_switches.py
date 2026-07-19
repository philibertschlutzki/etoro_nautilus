"""
Regression tests for ISSUE-OPT-373 — ComboTrendVwap conjunction switches.

The ComboTrendVwap entry gate used to be a hard-wired 4-fold AND conjunction. The
boolean switches ``require_vwap_confirmation`` and ``require_bb_touch`` let the
optimizer relax single conjuncts categorically instead. These tests pin down the
contract:

  * both switches default to ``True`` ⇒ behaviour-neutral vs. the pre-switch master
    (status-quo regression),
  * the switches are part of the msgspec struct contract (validation stays intact),
  * the optimizer search space samples both switches categorically, and
  * the sampled parameter vector still builds a valid strategy config once it has
    passed the same ``__struct_fields__`` filtering the backtest runner applies.

The downstream entry-frequency / ``fully_eligible_pairs`` increase is a Typ-S effect
that requires a fresh baseline run on real market data (see issue); it is therefore
intentionally NOT asserted here against synthetic data.
"""
import optuna
import pytest

from automation.optimizer import spaces
from automation.strategies.tesla_combo_strategy import ComboTrendVwapConfig

# Single source of truth for the field names under test — no duplicated literals.
SWITCHES = ("require_vwap_confirmation", "require_bb_touch")

# Minimal required HourlyStrategyConfig fields to instantiate the config.
_REQUIRED = dict(
    instrument_id="AAPL.ETORO",
    bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
)


def _full_sample(**overrides) -> dict:
    """A complete ComboTrendVwap parameter vector for ``optuna.FixedTrial``.

    ``FixedTrial`` requires every ``suggest_*`` key to be present, including the
    categorical switches.
    """
    params = {
        "macd_fast": 12, "macd_gap": 10, "macd_signal_period": 9,
        "sma_period": 50, "bb_period": 20, "bb_std_dev": 2.0,
        "atr_period": 14, "atr_multiplier": 0.5, "vwap_period": 20,
        "trend_tolerance_pct": 0.02, "bb_touch_window": 24,
        "require_vwap_confirmation": True, "require_bb_touch": True,
        # Issue #714 (GR-01) — Obergrenze 48 → 24 (24-Bar-Zeitbox).
        "cooldown_bars": 12, "atr_trailing_multiplier": 2.0, "max_bars_in_trade": 24,
        # Issue #713 — dyn_tp_enabled ist jetzt Teil JEDES Suchraums (FixedTrial verlangt alle
        # suggest_*-Keys); False hält diesen Test dyn-TP-neutral.
        "dyn_tp_enabled": False,
    }
    params.update(overrides)
    return params


def test_switch_defaults_are_status_quo():
    """Both switches default to True ⇒ behaviour-neutral vs. the pre-switch master."""
    cfg = ComboTrendVwapConfig(**_REQUIRED)
    for name in SWITCHES:
        assert getattr(cfg, name) is True, f"{name} must default to True (status quo)"


def test_switches_part_of_struct_contract():
    """The switches live in the msgspec struct; explicit values are honoured."""
    for name in SWITCHES:
        assert name in ComboTrendVwapConfig.__struct_fields__
    cfg = ComboTrendVwapConfig(
        **_REQUIRED, require_vwap_confirmation=False, require_bb_touch=False
    )
    assert cfg.require_vwap_confirmation is False
    assert cfg.require_bb_touch is False


def test_struct_validation_still_rejects_unknown_field():
    """``__struct_fields__`` validation stays intact: unknown kwargs are rejected."""
    with pytest.raises(TypeError):
        ComboTrendVwapConfig(**_REQUIRED, not_a_real_param=123)


@pytest.mark.parametrize(
    "vwap_flag,bb_flag",
    [(True, True), (False, True), (True, False), (False, False)],
)
def test_spaces_samples_conjunction_switches(vwap_flag, bb_flag):
    """spaces.sample_params surfaces both switches as booleans for every combo."""
    trial = optuna.trial.FixedTrial(
        _full_sample(require_vwap_confirmation=vwap_flag, require_bb_touch=bb_flag)
    )
    sampled = spaces.sample_params("ComboTrendVwapStrategy", trial)
    for name in SWITCHES:
        assert name in sampled, f"{name} must be sampled by the search space"
        assert isinstance(sampled[name], bool)
    assert sampled["require_vwap_confirmation"] is vwap_flag
    assert sampled["require_bb_touch"] is bb_flag


def test_sampled_switches_survive_struct_filter_and_build_config():
    """End-to-end: switches pass the runner's struct filter and build a valid config.

    Mirrors the defensive ``__struct_fields__`` filtering in
    ``backtest_runner.run_single_backtest_worker`` so the test reflects the real
    parameter pipeline (sampled → filter → config). The only remaining sampling-only
    artifact is ``macd_gap`` (translated to ``macd_slow`` inside spaces, never returned).
    Issue #446: ``trend_tolerance_pct`` is now a *real*, wired config field (no longer a
    dropped phantom), so it must be a recognised struct key.
    """
    trial = optuna.trial.FixedTrial(
        _full_sample(require_vwap_confirmation=False, require_bb_touch=False)
    )
    sampled = spaces.sample_params("ComboTrendVwapStrategy", trial)

    valid_keys = set(ComboTrendVwapConfig.__struct_fields__)
    for name in SWITCHES:
        assert name in valid_keys, f"{name} must be a recognised struct field"
    # macd_gap stays a sampling-only artifact (translated to macd_slow, not returned).
    assert "macd_gap" not in sampled
    # Issue #446 — trend_tolerance_pct is now a wired config field and must bind.
    assert "trend_tolerance_pct" in valid_keys
    assert "trend_tolerance_pct" in sampled

    filtered = {k: v for k, v in sampled.items() if k in valid_keys}
    cfg = ComboTrendVwapConfig(**_REQUIRED, **filtered)
    assert cfg.require_vwap_confirmation is False
    assert cfg.require_bb_touch is False
    assert cfg.macd_slow == cfg.macd_fast + 10
