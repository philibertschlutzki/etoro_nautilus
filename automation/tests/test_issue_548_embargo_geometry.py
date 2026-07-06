"""Issue #548 — Embargo/Purge-Periode im Sweep-Pfad reservieren (Leakage-Schutz #466 wirksam).

Vor dem Fix ließ ``wf_settings`` ``embargo_period_days`` weg (Subprozess las ``.get(key, 0)``)
UND ``compute_walk_forward_window`` reservierte keinen Embargo-Platz im äusseren Fenster. Der
Purge-Gap zwischen IS-Ende und OOS-Start war damit wirkungslos. Der Fix ändert BEIDE Stellen
konsistent, sodass Fold ``n_folds−1`` exakt bei ``end`` endet (kein Overflow über den Datenrand).
"""
import datetime as dt
import json
from pathlib import Path

from automation.optimizer import trial_config
from automation.optimizer.trial_config import compute_walk_forward_window
from automation.backtest_runner import compute_fold_boundaries

UTC = dt.timezone.utc
DAY_NS = 86400 * 1_000_000_000


def test_window_reserves_embargo_span():
    """Der äussere Span wächst um genau embargo_period_days (Fenster verschoben, nicht geschrumpft)."""
    now = dt.datetime(2026, 6, 25, tzinfo=UTC)  # Donnerstag (kein Sonntag-Rollback)
    kw = dict(now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)

    start0, end0 = compute_walk_forward_window(embargo_period_days=0, **kw)
    start21, end21 = compute_walk_forward_window(embargo_period_days=21, **kw)

    # end ist embargo-invariant; nur start wandert um exakt embargo Tage nach vorn.
    assert end0 == end21
    assert (start0 - start21).days == 21
    assert (end21 - start21).days == 180 + 21 + 4 * 45  # = 381


def test_last_fold_ends_exactly_at_end_no_overflow():
    """Geometrie-Gegenrechnung (#548): Fold n−1 endet exakt bei ``end`` — kein Bar jenseits end."""
    now = dt.datetime(2026, 6, 25, tzinfo=UTC)
    is_days, oos_days, n_folds, embargo = 180, 45, 4, 21
    start, end = compute_walk_forward_window(
        now=now, holdout_days=45, is_window_days=is_days,
        oos_window_days=oos_days, n_folds=n_folds, embargo_period_days=embargo)

    start_ns = int(start.timestamp() * 1_000_000_000)
    wf = {"is_window_days": is_days, "oos_window_days": oos_days,
          "splits": n_folds, "embargo_period_days": embargo}
    boundaries = compute_fold_boundaries(start_ns, wf)

    span_days = (end - start).days
    # Letzter OOS-Fold endet exakt am äusseren Rand ``end``.
    assert boundaries[-1][2] - start_ns == span_days * DAY_NS
    # OOS-Fenster-Start (fold 0) = start + (is + embargo) ⇒ 21-Tage-Purge-Gap sichtbar.
    assert boundaries[0][1] - start_ns == (is_days + embargo) * DAY_NS


def test_no_oos_fill_in_purge_gap():
    """Integritäts-Check: kein OOS-Fold deckt das Intervall [is_end, is_end + embargo) ab."""
    is_days, oos_days, n_folds, embargo = 180, 45, 4, 21
    start_ns = 0
    wf = {"is_window_days": is_days, "oos_window_days": oos_days,
          "splits": n_folds, "embargo_period_days": embargo}
    boundaries = compute_fold_boundaries(start_ns, wf)

    is_end_ns = start_ns + is_days * DAY_NS
    ts_in_gap = is_end_ns + (embargo * DAY_NS) // 2  # mitten im Purge-Gap
    assert not any(s <= ts_in_gap < e for _, s, e in boundaries)


def test_embargo_zero_is_bit_identical_legacy():
    """Regressionstest: embargo_period_days=0 reproduziert das Alt-Verhalten bit-identisch."""
    now = dt.datetime(2026, 6, 25, tzinfo=UTC)
    kw = dict(now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    start, end = compute_walk_forward_window(embargo_period_days=0, **kw)
    # Alt-Formel: start = end − (is + n_folds*oos)
    assert (end - start).days == 180 + 4 * 45


def test_wf_settings_and_manifest_carry_embargo():
    """wf_settings (kopierte backtest.json) UND Manifest global_settings.walk_forward tragen embargo."""
    now = dt.datetime(2026, 6, 10, 15, 0, tzinfo=UTC)  # Mittwoch
    trial_dir, mpath = trial_config.build_trial(
        "SmaCrossoverStrategy", {"sma_period": 8},
        study_name="s_embargo", trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4)

    gs = json.loads(Path(mpath).read_text("utf-8"))["global_settings"]
    assert "embargo_period_days" in gs["walk_forward"], "Manifest walk_forward muss embargo tragen"
    # Repo-Config trägt embargo=21 ⇒ propagiert.
    assert gs["walk_forward"]["embargo_period_days"] == 21

    # Kopierte backtest.json (Subprozess-Seitenkanal) trägt denselben Wert.
    copied = json.loads((Path(trial_dir) / "config" / "backtest.json").read_text("utf-8"))
    assert copied["walk_forward"]["embargo_period_days"] == 21
