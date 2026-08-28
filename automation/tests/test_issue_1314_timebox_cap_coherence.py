"""Issue #1314 (GH #1191, P0) — Harter Bar-Cap (6) liegt über der Zeitbox-Deadline (5,76).

Symptom. Eine Position, die den erlaubten ``max_bars_in_trade``-Cap ausschöpft, ist per
Konstruktion eine Zeitbox-Verletzung: ``t_norm = 6 / 5,76 = 1,042`` (B-12). Vor #1275 galt
Cap = Deadline = 24.

Root-Cause. ``_contracts.MAX_BARS_IN_TRADE_HARD_CAP`` wurde gerundet (``round(24 × 0,24) = 6``),
``optimizer.json['time_box_bars']`` nicht (``24,0 × 0,24 = 5,76``). Zwei unabhängig gepflegte
Ableitungen desselben Faktors.

Fix.
1. ``_contracts.py`` erhält ``RTH_AXIS_FACTOR = 0.24`` und ``TIME_BOX_BARS = RTH_AXIS_FACTOR *
   24.0`` als Single Source of Truth, plus ``MAX_BARS_IN_TRADE_HARD_CAP = math.ceil(TIME_BOX_
   BARS)`` — der Cap ist damit per Konstruktion **≥** der Deadline und beide bewegen sich
   gemeinsam.
2. ``optimizer.json['time_box_bars']`` wird aus derselben Konstante validiert: neue Invariante
   ``check_timebox_cap_coherence`` (severity ``blocking``), die ``MAX_BARS_IN_TRADE_HARD_CAP >=
   time_box_bars`` und ``MAX_BARS_IN_TRADE_HARD_CAP - time_box_bars < 1.0`` prüft.
"""
from automation.optimizer import _contracts
from automation.optimizer import invariants as inv


# ── _contracts.py — EINE Quelle fuer Faktor/Deadline/Cap ─────────────────────────────────────────

def test_time_box_bars_matches_the_rth_axis_factor():
    assert _contracts.TIME_BOX_BARS == _contracts.RTH_AXIS_FACTOR * 24.0
    assert _contracts.TIME_BOX_BARS == 5.76


def test_hard_cap_is_derived_from_time_box_bars_via_ceil():
    import math
    assert _contracts.MAX_BARS_IN_TRADE_HARD_CAP == math.ceil(_contracts.TIME_BOX_BARS)


def test_hard_cap_stays_bit_identical_to_the_pre_fix_value():
    """Regressionsschutz: der Cap bleibt 6 (round(24*0.24) und ceil(5.76) stimmten bereits vor
    diesem Fix zufaellig ueberein) — dieser Fix aendert die HERKUNFT, nicht den WERT."""
    assert _contracts.MAX_BARS_IN_TRADE_HARD_CAP == 6


def test_hard_cap_is_never_below_the_deadline_by_construction():
    assert _contracts.MAX_BARS_IN_TRADE_HARD_CAP >= _contracts.TIME_BOX_BARS


# ── invariants.check_timebox_cap_coherence ────────────────────────────────────────────────────

def test_passes_for_the_shipped_production_config():
    """Akzeptanzkriterium: PASSt für die ausgelieferte Config."""
    result = inv.check_timebox_cap_coherence()
    assert result.passed is True
    assert result.severity == "blocking"


def test_cap_below_deadline_fails():
    """Akzeptanzkriterium: time_box_bars = 5.76, Cap 5 ⇒ FAIL."""
    result = inv.check_timebox_cap_coherence(5, 5.76)
    assert result.passed is False


def test_cap_matching_deadline_within_slack_passes():
    """Akzeptanzkriterium: time_box_bars = 5.76, Cap 6 ⇒ PASS."""
    result = inv.check_timebox_cap_coherence(6, 5.76)
    assert result.passed is True


def test_cap_too_far_above_deadline_fails():
    """Akzeptanzkriterium: time_box_bars = 5.76, Cap 12 ⇒ FAIL (zu weit)."""
    result = inv.check_timebox_cap_coherence(12, 5.76)
    assert result.passed is False


def test_actual_carries_the_computed_slack_on_failure():
    result = inv.check_timebox_cap_coherence(12, 5.76)
    assert result.actual["slack"] == 6.24


def test_detail_distinguishes_below_deadline_from_too_far_above():
    below = inv.check_timebox_cap_coherence(5, 5.76)
    above = inv.check_timebox_cap_coherence(12, 5.76)
    assert "UNTER" in below.detail
    assert "über" in above.detail
    assert below.detail != above.detail


def test_custom_slack_bound_is_respected():
    # Mit max_slack_bars=10.0 wird Cap 12 gegen Deadline 5.76 (Slack 6.24) zulaessig.
    result = inv.check_timebox_cap_coherence(12, 5.76, max_slack_bars=10.0)
    assert result.passed is True


# ── report.py — Verdrahtung ────────────────────────────────────────────────────────────────────

def test_report_wires_the_check_using_the_configured_time_box_bars():
    import inspect
    from automation.optimizer import report
    src = inspect.getsource(report._build_report)
    assert "check_timebox_cap_coherence" in src
    assert 'optimizer_cfg.get("time_box_bars"' in src


# ── Akzeptanzkriterium 3 — ein Trade ueber exakt dem Cap ist keine (binaere) Zeitbox-Verletzung ──

def test_a_trial_held_for_exactly_the_hard_cap_bars_is_not_flagged_as_a_violation():
    """``compute_trial_timebox_violations`` ist der TATSAECHLICHE binaere Verletzungs-Wächter
    (Eligibility-/Reward-relevant, #839/#903) — er vergleicht gegen ``(cap_bars + tolerance_bars) *
    bar_seconds``, NICHT gegen die ungerundete ``time_box_bars``-Deadline direkt. Ein Trial, dessen
    Haltedauer EXAKT ``MAX_BARS_IN_TRADE_HARD_CAP`` Bars entspricht, bleibt dank der bestehenden
    Ausfuehrungs-Toleranz (``tolerance_bars``, Default 3.0) unterhalb der Verletzungsschwelle —
    unabhaengig von der (unvermeidlichen) Rundungs-Restluecke zwischen dem ganzzahligen Cap und der
    gebrochenen ``time_box_bars``-Deadline (0,24 Bars, weit innerhalb der 3-Bar-Toleranz)."""
    bar_seconds = 3600.0
    holding_s = _contracts.MAX_BARS_IN_TRADE_HARD_CAP * bar_seconds
    result = inv.compute_trial_timebox_violations(
        [{"oos_max_holding_time_s": holding_s, "oos_holding_times_s": [holding_s]}],
        bar_seconds=bar_seconds,
        max_bars_in_trade_cap=_contracts.MAX_BARS_IN_TRADE_HARD_CAP,
    )
    assert result["timebox_violating_trials"] == 0
    assert result["timebox_violating_round_trips"] == 0
