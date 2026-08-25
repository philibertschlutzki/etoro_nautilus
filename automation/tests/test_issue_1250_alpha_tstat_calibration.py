"""Issue #1250 (GH #1120), Pitfall #451 in AGENTS.md — ``oos_min_alpha_tstat`` kalibrieren und die
Multiplizität auf den t(α)-Pfad ziehen.

Symptom. Schwelle 2,0 bei beobachtetem p99 = 1,4696 über 1661 Trials; P(t >= 2) = 0,120 %; 13/14
Studies mit ``n_eligible = 0`` und ``binding_gate = oos_min_alpha_tstat``. Der t(α)-Pfad trug KEINE
Multiplizitätskorrektur, obwohl TPE unter N gezogenen Kandidaten je Study das Maximum waehlt (ein
Ein-Trial-5%-Test hat unter Maximum-Selektion keine 5%-Fehlerrate mehr).

Fix. ``calibration.calibrate_alpha_tstat_gate`` (Monte-Carlo unter H0, reine/deterministische
Funktion) + ``reward.resolve_alpha_tstat_gate_threshold`` (löst die effektive Schwelle je Study auf,
fail-open auf 'static' ohne Kalibrier-Fixture/Familiengrösse) + ``tournament.json
['oos_min_alpha_tstat_mode']`` (Default 'static', bit-identisch) + ``reward.selection_rule_
fingerprint`` (nimmt die effektive Schwelle mit auf, Pitfall #248)."""
import json
import math
from pathlib import Path

import pytest

from automation.optimizer import reward
from automation.optimizer.calibration import calibrate_alpha_tstat_gate


def _load_production_tournament_cfg() -> dict:
    """Issue #1247 (GH #1117), Pitfall #449 in AGENTS.md — production-Config laden statt eines
    handgeschriebenen Fixtures (siehe test_issue_1093_1241_alpha_tstat_prefilter.py::_load_
    production_tournament_cfg fuer die volle Begruendung)."""
    return json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


_CALIBRATION_FIXTURE_PATH = Path("automation/tests/fixtures/alpha_tstat_gate_calibration.json")


def _load_calibration_fixture() -> list[dict]:
    payload = json.loads(_CALIBRATION_FIXTURE_PATH.read_text("utf-8"))
    return payload["calibration_points"]


# ---------------------------------------------------------------------------------------------
# calibrate_alpha_tstat_gate
# ---------------------------------------------------------------------------------------------

def test_calibrate_alpha_tstat_gate_reproducible_and_above_2():
    """Akzeptanzkriterium (#1120): calibrate_alpha_tstat_gate(n_configs=280, n_periods=1079) ist
    reproduzierbar (fester Seed) und liefert eine Schwelle > 2,0 (die bisherige unkorrigierte
    Konstante) — Beweis, dass die Multiplizitätskorrektur die Schwelle tatsächlich verschärft."""
    r1 = calibrate_alpha_tstat_gate(n_configs=280, n_periods=1079, seed=42)
    r2 = calibrate_alpha_tstat_gate(n_configs=280, n_periods=1079, seed=42)
    assert r1 == r2
    assert r1["threshold"] > 2.0
    # Šidák-Handrechnung des Issues: t ~ 3.57 für N=280 bei einer echten 5%-Winner-Fehlerrate.
    assert 3.0 < r1["threshold"] < 4.2


def test_calibrate_alpha_tstat_gate_different_seed_still_above_2():
    """Kein Artefakt EINES Seeds: ein zweiter, unabhängiger Seed liefert weiterhin eine deutlich
    über 2,0 liegende Schwelle (die Korrektur ist eine strukturelle Folge von N=280 Ziehungen mit
    Maximum-Selektion, kein Zufallsprodukt des Referenz-Seeds)."""
    r = calibrate_alpha_tstat_gate(n_configs=280, n_periods=1079, seed=7)
    assert r["threshold"] > 2.5


def test_calibrate_alpha_tstat_gate_grows_with_n_configs():
    """Mehr Kandidaten je Study => hoerere Selektions-Schwelle (das E[max_N]-Argument des Issues:
    das Maximum von mehr i.i.d. t-Werten unter H0 waechst monoton in Erwartung)."""
    small = calibrate_alpha_tstat_gate(n_configs=20, n_periods=1079, seed=42)
    large = calibrate_alpha_tstat_gate(n_configs=280, n_periods=1079, seed=42)
    assert large["threshold"] > small["threshold"]


# ---------------------------------------------------------------------------------------------
# resolve_alpha_tstat_gate_threshold
# ---------------------------------------------------------------------------------------------

def test_resolve_defaults_to_static_bit_identical():
    """Akzeptanzkriterium (#1120): mit mode='static' (Default, Key fehlt) bleibt die Schwelle
    exakt die rohe Config-Konstante — bit-identisch zum Pre-#1250-Verhalten."""
    tcfg = {"oos_min_alpha_tstat": 2.0}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(tcfg)
    assert threshold == 2.0
    assert source == "static"


def test_resolve_explicit_static_mode_bit_identical():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "static"}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(
        tcfg, n_family_stage1=280, oos_n_periods_median=1079,
        calibration_fixture=_load_calibration_fixture(),
    )
    assert threshold == 2.0
    assert source == "static"


def test_resolve_unknown_mode_fails_loud():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "bogus"}
    with pytest.raises(ValueError):
        reward.resolve_alpha_tstat_gate_threshold(tcfg)


def test_resolve_multiplicity_adjusted_without_fixture_fails_open_to_static():
    """Fail-open (Docstring): 'multiplicity_adjusted' OHNE Kalibrier-Fixture bricht NICHT ab,
    sondern faellt auf 'static' zurueck — eine fehlende Kalibrierbasis darf den Sweep nie
    blockieren."""
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(tcfg)
    assert threshold == 2.0
    assert source == "static"


def test_resolve_multiplicity_adjusted_without_family_size_fails_open_to_static():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(
        tcfg, calibration_fixture=_load_calibration_fixture())
    assert threshold == 2.0
    assert source == "static"


def test_resolve_multiplicity_adjusted_nearest_point_from_fixture():
    """Mit Fixture UND Familiengroesse: der naechste Kalibrierpunkt (kleinster Log-Abstand) wird
    verwendet, ``source == 'calibrated'``."""
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(
        tcfg, n_family_stage1=280, oos_n_periods_median=1079,
        calibration_fixture=_load_calibration_fixture(),
    )
    assert source == "calibrated"
    assert threshold == pytest.approx(3.5666)


def test_resolve_multiplicity_adjusted_nearest_point_off_grid():
    """Ein (N, T)-Paar, das NICHT exakt im Fixture liegt, waehlt trotzdem den (einzigen, naechsten)
    Kalibrierpunkt statt abzubrechen — das Fixture ist ein Gitter, kein exakter Lookup."""
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    threshold, source = reward.resolve_alpha_tstat_gate_threshold(
        tcfg, n_family_stage1=250, oos_n_periods_median=1000,
        calibration_fixture=_load_calibration_fixture(),
    )
    assert source == "calibrated"
    assert threshold == pytest.approx(3.5666)


def test_calibration_fixture_gate_never_loosens():
    """Akzeptanzkriterium (#1120): 'das Gate wird nicht gelockert' — der kalibrierte Punkt im
    ausgelieferten Fixture liegt fuer die reale (N, T)-Kombination NIE unter der heutigen
    statischen Schwelle aus der Produktions-Config."""
    tcfg = _load_production_tournament_cfg()
    static_threshold = tcfg["oos_min_alpha_tstat"]
    for point in _load_calibration_fixture():
        assert point["threshold"] >= static_threshold


def test_production_config_default_mode_is_static():
    """Akzeptanzkriterium (#1120): Default 'static' mit dem heutigen Wert 2,0 (bit-identisch) —
    bis ein Kalibrierlauf explizit UND bewusst an der Gate-Entscheidungsstelle aktiviert wird."""
    tcfg = _load_production_tournament_cfg()
    assert tcfg.get("oos_min_alpha_tstat_mode", "static") == "static"
    assert tcfg["oos_min_alpha_tstat"] == 2.0


def test_production_config_mode_is_a_known_mode():
    tcfg = _load_production_tournament_cfg()
    mode = tcfg.get("oos_min_alpha_tstat_mode", "static")
    assert mode in reward._ALPHA_TSTAT_GATE_MODES


# ---------------------------------------------------------------------------------------------
# selection_rule_fingerprint
# ---------------------------------------------------------------------------------------------

def test_fingerprint_differs_with_different_effective_alpha_tstat_threshold():
    """Akzeptanzkriterium (#1120): selection_rule_fingerprint unterscheidet zwei Studies mit
    verschiedener effektiver oos_min_alpha_tstat-Schwelle (Voraussetzung fuer eine gueltige
    familienweite DSR-Multiplizitaetskorrektur, Pitfall #248)."""
    tcfg = {"eligible_requires_all": ["oos_min_alpha_tstat", "min_trades"],
            "oos_min_alpha_tstat": 2.0, "min_trades": 20}
    fp_static = reward.selection_rule_fingerprint(tcfg)
    fp_calibrated = reward.selection_rule_fingerprint(
        tcfg, alpha_tstat_gate_threshold_effective=3.5666)
    assert fp_static != fp_calibrated


def test_fingerprint_stable_when_effective_threshold_matches_config():
    """None (kein Override) UND ein Override, der zufaellig exakt dem Config-Wert entspricht,
    liefern denselben Fingerprint (die effektive Schwelle IST der Config-Wert)."""
    tcfg = {"eligible_requires_all": ["oos_min_alpha_tstat", "min_trades"],
            "oos_min_alpha_tstat": 2.0, "min_trades": 20}
    fp_none = reward.selection_rule_fingerprint(tcfg)
    fp_explicit = reward.selection_rule_fingerprint(
        tcfg, alpha_tstat_gate_threshold_effective=2.0)
    assert fp_none == fp_explicit


def test_fingerprint_uses_normalized_clause_for_prefix_variants():
    """Pitfall #448 — ob eligible_requires_all die praefigierte ('oos_min_alpha_tstat') oder
    unpraefigierte ('min_alpha_tstat') Form listet, der Override muss in BEIDEN Faellen greifen
    (reward._normalize_clause ist die eine Stelle, die diese Form definiert)."""
    tcfg_prefixed = {"eligible_requires_all": ["oos_min_alpha_tstat"], "oos_min_alpha_tstat": 2.0}
    tcfg_unprefixed = {"eligible_requires_all": ["min_alpha_tstat"], "oos_min_alpha_tstat": 2.0,
                       "min_alpha_tstat": 2.0}
    fp_prefixed = reward.selection_rule_fingerprint(
        tcfg_prefixed, alpha_tstat_gate_threshold_effective=3.5666)
    fp_prefixed_static = reward.selection_rule_fingerprint(tcfg_prefixed)
    assert fp_prefixed != fp_prefixed_static
    fp_unprefixed = reward.selection_rule_fingerprint(
        tcfg_unprefixed, alpha_tstat_gate_threshold_effective=3.5666)
    fp_unprefixed_static = reward.selection_rule_fingerprint(tcfg_unprefixed)
    assert fp_unprefixed != fp_unprefixed_static
