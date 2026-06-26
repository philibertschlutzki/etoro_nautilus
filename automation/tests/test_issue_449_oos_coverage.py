"""Issue #449/#450/#451 — OOS-Abdeckungs-Blindstelle, Floor-Stopp & geteilte Fenster-Arithmetik.

Hintergrund (Pitfall #82): Der Per-Symbol-Sweep kollabiert für ein Symbol auf den Unevaluable-Floor
(oos_total_trades=0), wenn die Daten zwar die geforderte ANZAHL Bars besitzen (Gate-1 (a)–(c)
bestehen), aber ausschließlich Historie in der ersten Fensterhälfte enthalten — das früheste
OOS-Sub-Fenster ``[start + is_window, …]`` bleibt leer, und JEDER Trial ist strukturell unevaluable,
UNABHÄNGIG von den Parametern. Bisher ging genau die diagnostische Zahl (jüngster Tick vs. OOS-Grenze)
vor der Operator-Konsole verloren.

Diese Tests decken die rein funktionalen, nautilus-freien Teile der Behebung ab:
  * #451 — ``compute_walk_forward_window`` ist die EINZIGE Fenster-Arithmetik (auch fürs Preflight).
  * #449 — ``data_reaches_oos_window`` (Gate-Helper) + ``enumerate_tunable_pairs``-Preflight.
  * #450 — ``floor_plateau_callback(stop_on_plateau=True)`` stoppt die Study aktiv.
"""
import datetime as dt
import logging

import optuna

from automation.optimizer.trial_config import compute_walk_forward_window
from automation.optimizer.gate import data_reaches_oos_window
from automation.optimizer import sweep
from automation.optimizer import run_optimization as ro

DAY = 86400 * 1_000_000_000


# ---------------------------------------------------------------------------
# #451 — geteilte Walk-Forward-Fenster-Arithmetik (Single Source of Truth)
# ---------------------------------------------------------------------------
def test_window_reproduces_known_dates():
    """Donnerstag 2026-06-25, Standard-Geometrie ⇒ exakt das im Log beobachtete Fenster."""
    now = dt.datetime(2026, 6, 25, 14, 0, tzinfo=dt.timezone.utc)
    start, end = compute_walk_forward_window(
        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    assert str(start.date()) == "2025-05-16"
    assert str(end.date()) == "2026-05-11"


def test_window_sunday_rollback():
    """Sonntag-Anker rollt auf Samstag zurück, BEVOR das Holdout abgezogen wird."""
    now_sun = dt.datetime(2026, 6, 21, 9, 0, tzinfo=dt.timezone.utc)  # weekday() == 6
    assert now_sun.weekday() == 6
    _start, end = compute_walk_forward_window(
        now=now_sun, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    # Samstag 2026-06-20 − 45 Tage = 2026-05-06
    assert str(end.date()) == "2026-05-06"


def test_build_trial_uses_shared_window(monkeypatch, tmp_path):
    """build_trial darf KEINE zweite Fenster-Implementierung haben — es muss die geteilte
    Funktion aufrufen (sonst lebt die Divergenz-Footgun weiter). Wir patchen die geteilte
    Funktion und prüfen, dass build_trial ihren Rückgabewert verwendet."""
    from automation.optimizer import trial_config as tc

    sentinel_start = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    sentinel_end = dt.datetime(2020, 12, 31, tzinfo=dt.timezone.utc)
    called = {}

    def _fake_window(**kw):
        called.update(kw)
        return sentinel_start, sentinel_end

    monkeypatch.setattr(tc, "compute_walk_forward_window", _fake_window)
    # Minimal-Config, damit build_trial nicht an fehlenden Dateien stirbt.
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "backtest.json").write_text(
        '{"start_capital":10000,"walk_forward":{"is_window_days":180,"oos_window_days":45,'
        '"splits":4,"holdout_days":45,"data_history_days":450}}', "utf-8")
    (cfg / "strategies.json").write_text('{"strategies":[]}', "utf-8")

    # resolve_params/Manifest-Schreiben ist hier Nebensache; wir wollen nur den Fenster-Aufruf prüfen.
    monkeypatch.setattr(tc, "resolve_params", lambda *a, **k: {})
    monkeypatch.setattr(tc, "git_commit", lambda: "deadbeef")
    monkeypatch.setattr(tc, "catalog_fingerprint", lambda *a, **k: "fp")
    monkeypatch.setattr(tc, "sha256_file", lambda *a, **k: "sha")

    try:
        tc.build_trial("SmaCrossoverStrategy", {}, study_name="s", trial_number=0, seed=1,
                       base_cfg=cfg, copy_config=False)
    except Exception:
        pass  # Manifest-Details irrelevant — der Fenster-Aufruf ist das Prüfziel.

    assert called, "build_trial muss compute_walk_forward_window aufrufen (keine Inline-Kopie)"
    assert called.get("holdout_days") == 45 and called.get("is_window_days") == 180


# ---------------------------------------------------------------------------
# #449 — OOS-Erreichbarkeits-Gate-Helper
# ---------------------------------------------------------------------------
def _boundary():
    now = dt.datetime(2026, 6, 25, 14, 0, tzinfo=dt.timezone.utc)
    start, _ = compute_walk_forward_window(
        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    return int(start.timestamp() * 1e9) + 180 * DAY  # = 2025-11-12


def test_reaches_oos_true_when_data_recent():
    b = _boundary()
    newest = int(dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    ok, gap = data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)
    assert ok is True and gap < 0


def test_reaches_oos_false_when_data_stops_in_h1():
    b = _boundary()
    newest = int(dt.datetime(2025, 9, 30, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    ok, gap = data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)
    assert ok is False and gap > 0  # ~43 Tage vor der OOS-Grenze


def test_reaches_oos_failopen_on_unknown():
    b = _boundary()
    ok, gap = data_reaches_oos_window(newest_ns=None, start_ns=b, is_window_days=0)
    assert ok is True and gap == 0.0


def test_reaches_oos_respects_grace():
    b = _boundary()
    # Tick genau 3 Tage vor der Grenze: ohne Karenz NOK, mit 5-Tage-Karenz OK.
    newest = b - 3 * DAY
    assert data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)[0] is False
    assert data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0,
                                   recency_grace_days=5.0)[0] is True


# ---------------------------------------------------------------------------
# #449 — Sweep-Preflight: unerreichbares Symbol wird übersprungen (fail-open ohne Telemetrie)
# ---------------------------------------------------------------------------
_CFG = {"walk_forward": {"is_window_days": 180, "oos_window_days": 45, "splits": 4,
                         "holdout_days": 45},
        "gate1_buffer_days": 30, "min_bars_per_param": 50, "min_oos_bars_per_fold": 200}


def test_preflight_skips_unreachable_symbol(monkeypatch):
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["TSLA.ETORO", "AAPL.ETORO"])
    monkeypatch.setattr(sweep, "n_params_for", lambda strat: 6)
    b = _boundary()
    big = {"TSLA.ETORO": 450 * 24, "AAPL.ETORO": 450 * 24}  # beide bestehen das Count-Gate
    latest = {
        "TSLA.ETORO": int(dt.datetime(2025, 9, 30, tzinfo=dt.timezone.utc).timestamp() * 1e9),  # H1-only
        "AAPL.ETORO": int(dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc).timestamp() * 1e9),   # reicht ins OOS
    }
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], ["TSLA.ETORO", "AAPL.ETORO"], tier="all",
        available_bars=big, config=_CFG, latest_ts=latest, oos_window_start_ns=b)
    kept = sorted({sym for _, sym, _ in pairs})
    assert kept == ["AAPL.ETORO"]  # TSLA strukturell übersprungen


def test_preflight_failopen_without_telemetry(monkeypatch):
    """Ohne latest_ts/oos_window_start_ns ist das Verhalten bit-identisch zu vorher (beide behalten)."""
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["TSLA.ETORO", "AAPL.ETORO"])
    monkeypatch.setattr(sweep, "n_params_for", lambda strat: 6)
    big = {"TSLA.ETORO": 450 * 24, "AAPL.ETORO": 450 * 24}
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], ["TSLA.ETORO", "AAPL.ETORO"], tier="all",
        available_bars=big, config=_CFG)
    assert sorted({sym for _, sym, _ in pairs}) == ["AAPL.ETORO", "TSLA.ETORO"]


# ---------------------------------------------------------------------------
# #450 — Floor-Plateau stoppt die Study aktiv (opt-in)
# ---------------------------------------------------------------------------
class _FakeTrial:
    def __init__(self, value, oos_evaluated=None):
        self.value = value
        self.state = optuna.trial.TrialState.COMPLETE
        self.user_attrs = {} if oos_evaluated is None else {"oos_evaluated": oos_evaluated}


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}
        self.stop_called = 0

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stop_called += 1


_W = {"penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 3}


def test_plateau_stop_optin_calls_stop():
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(-9.90, oos_evaluated=False),
              _FakeTrial(-9.93, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
                              logger=logging.getLogger("t450a"), stop_on_plateau=True)
    assert study.stop_called == 1


def test_plateau_default_does_not_stop():
    """Default (stop_on_plateau=False) bleibt reine Observability — Study läuft weiter."""
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(-9.90, oos_evaluated=False),
              _FakeTrial(-9.93, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
                              logger=logging.getLogger("t450b"))
    assert study.stop_called == 0


def test_plateau_no_stop_when_some_evaluable():
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(0.5, oos_evaluated=True),
              _FakeTrial(-9.90, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
                              logger=logging.getLogger("t450c"), stop_on_plateau=True)
    assert study.stop_called == 0  # ein evaluable Trial ⇒ kein Plateau
