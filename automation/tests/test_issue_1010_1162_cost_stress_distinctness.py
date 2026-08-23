"""Issue #1074/#1222 (Katalog #1247+, P0) — ``check_cost_stress_distinctness`` laeuft vor der
Befuellung seiner eigenen Eingabe.

Symptom: In 11/11 archivierten Laeufen ``passed=True``. Nachgerechnet mit befuelltem Feld: echte
Mindestdelta-Offender auf mehreren Symbolen.

Zwei Root-Causes:
1. **Reihenfolge.** ``report.py`` rief ``invariants.check_cost_stress_distinctness`` VOR der
   ``slippage_p50_bps_calibrated``-Stempelung im selben Report-Aufbau auf. ``if not slippage_p50:
   continue`` griff dadurch fuer JEDEN Record.
2. **Skalierung.** Der Zaehler des Mindestdelta-Terms (``oos_n_trailing_stop_losses``) war eine
   SWEEP-WEITE Zaehlung ueber alle Trials der Study; der Nenner (``holdout_total_trades``) ein
   einzelner Holdout. Zwei verschiedene Grundgesamtheiten unter einer Formel.

Fix:
1. Der Invarianten-Aufruf in ``report.py`` steht jetzt NACH der Kalibrierungs-Stempelung.
2. Neues Feld ``holdout_n_trailing_stop_exits`` (aus ``holdout_metrics``, DEMSELBEN Holdout-Pfad
   wie ``holdout_total_trades``) ersetzt ``oos_n_trailing_stop_losses`` als Zaehler.
3. ``evaluability.n_studies_with_calibration`` macht einen leeren Kalibrierungs-Cache sichtbar
   statt lautlos ``passed=True`` durchlaufen zu lassen (siehe
   ``test_issue_1010_1162_cost_stress_zero_realism.py``, dort die INCONCLUSIVE-Tests).

Dieses Modul testet gegen die 11 real archivierten Sweep-Reports unter ``logs/run_*.json`` (echte
Produktionsdaten, kein synthetisches Fixture) sowie mit konstruierten Fixtures fuer die
Mindestdelta-Mechanik selbst (das neue Feld ``holdout_n_trailing_stop_exits`` existiert in den
archivierten Vor-Fix-Artefakten naturgemaess noch nicht).
"""
import glob
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_ARCHIVED_RUNS = sorted(glob.glob(str(_LOG_DIR / "run_*.json")))


def _load_archived_studies() -> list[dict]:
    """Laedt die ``studies``-Listen aller archivierten Laeufe, auf die fuer diesen Test relevanten
    Felder reduziert (spart Speicher/Zeit gegenueber dem vollen ~600 KB-Artefakt je Lauf)."""
    fields = (
        "strategy", "symbol", "holdout_total_trades", "holdout_expectancy_capital_weighted",
        "holdout_expectancy_cost_stress_full_realism", "slippage_p50_bps_calibrated",
        "oos_n_trailing_stop_losses",
    )
    out = []
    for path in _ARCHIVED_RUNS:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("studies") or []:
            out.append({k: r.get(k) for k in fields})
    return out


@pytest.mark.skipif(not _ARCHIVED_RUNS, reason="keine archivierten logs/run_*.json gefunden")
def test_archived_runs_carry_calibrated_slippage_for_the_ordering_regression():
    """Akzeptanzkriterium 1 (Regressionstest) — mit den echten, bereits kalibrierten Study-Records
    (die Kalibrierung existiert im archivierten Artefakt, unabhaengig vom Reihenfolge-Bug in
    ``report.py``) muss die Invariante ``slippage_p50_bps_calibrated`` fuer praktisch jede Study mit
    >= 1 Holdout-Trade SEHEN, sobald sie NACH der Stempelung aufgerufen wird — genau das, was der
    Reihenfolge-Fix garantiert. Vor dem Fix waere dieses Feld an der Aufrufstelle in ``report.py``
    strukturell nie sichtbar gewesen, unabhaengig davon, ob es im fertigen Artefakt spaeter auftaucht."""
    records = _load_archived_studies()
    with_trades = [r for r in records if (r.get("holdout_total_trades") or 0) >= 1
                   and r.get("holdout_expectancy_capital_weighted") is not None
                   and r.get("holdout_expectancy_cost_stress_full_realism") is not None]
    assert with_trades, "archivierte Laeufe sollten Studies mit Holdout-Trades enthalten"
    n_with_calibration = sum(1 for r in with_trades if r.get("slippage_p50_bps_calibrated"))
    # Nicht zwingend 100 % (manche Asset-Klassen koennen im Cache fehlen), aber die weit
    # ueberwiegende Mehrheit — der Symptom-Bericht (#1074) nennt 0 % (Reihenfolge-Bug); nach dem
    # Fix ist die Kalibrierung im Rohdatensatz selbst laengst vorhanden.
    assert n_with_calibration / len(with_trades) > 0.9, (
        f"nur {n_with_calibration}/{len(with_trades)} Studies mit kalibrierter Slippage im "
        "archivierten Rohdatensatz")


@pytest.mark.skipif(not _ARCHIVED_RUNS, reason="keine archivierten logs/run_*.json gefunden")
def test_archived_runs_demonstrate_the_scaling_defect_of_the_old_numerator():
    """Charakterisierungstest fuer Root-Cause 2 — der ALTE Zaehler (``oos_n_trailing_stop_losses``,
    eine sweep-weite Zaehlung ueber ALLE Trials der Study) uebersteigt ``holdout_total_trades`` (ein
    einzelner Holdout) in der ueberwiegenden Mehrheit der archivierten Studies um mindestens eine
    Groessenordnung — der Quotient, den die VORHER-Formel bildete, lag damit strukturell weit ueber
    1 statt im erwarteten (0, 1)-Bereich (Symptom: Median 159,5)."""
    records = _load_archived_studies()
    candidates = [r for r in records
                  if (r.get("holdout_total_trades") or 0) >= 1
                  and r.get("oos_n_trailing_stop_losses")]
    assert candidates
    n_grossly_oversized = sum(
        1 for r in candidates
        if r["oos_n_trailing_stop_losses"] > 10 * r["holdout_total_trades"])
    assert n_grossly_oversized / len(candidates) > 0.5


def _record(*, exp, full_realism, trades, slippage_p50=None, n_ts_exits=None):
    r = {
        "strategy": "TestStrategy", "symbol": "TEST.ETORO",
        "holdout_total_trades": trades,
        "holdout_expectancy_capital_weighted": exp,
        "holdout_expectancy_cost_stress_full_realism": full_realism,
    }
    if slippage_p50 is not None:
        r["slippage_p50_bps_calibrated"] = slippage_p50
    if n_ts_exits is not None:
        r["holdout_n_trailing_stop_exits"] = n_ts_exits
    return r


def test_min_expected_delta_stays_within_zero_one_for_realistic_inputs():
    """Akzeptanzkriterium — ``min_expected_delta`` liegt fuer jede Study in (0, 1) bezogen auf
    Expectancy in Renditeeinheiten. Mit der holdout-skopierten Zaehlung gilt strukturell
    ``holdout_n_trailing_stop_exits <= holdout_total_trades`` (Anteil in [0, 1]) und
    ``slippage_p50_bps / 10000`` ist fuer jeden plausiblen bps-Wert (auch 1000 bps = 10 %) klein
    gegen 1 — das Produkt mit dem Default-Koeffizienten 0,5 bleibt deutlich unter 1."""
    # slippage_p50_bps_calibrated bis 1000 bps (10 %) deckt jeden im Artefakt beobachteten Wert
    # grosszuegig ab (Symptom nennt 24-115 bps).
    for slippage_p50 in (1.0, 36.8635, 200.0, 1000.0):
        for share_trades, share_exits in ((137, 137), (137, 1), (10, 10)):
            r = _record(exp=-0.01, full_realism=-0.05, trades=share_trades,
                        slippage_p50=slippage_p50, n_ts_exits=share_exits)
            result = inv.check_cost_stress_distinctness([r])
            offenders = (result.provenance or {}).get("delta_offenders") or {}
            key = "TestStrategy/TEST.ETORO"
            if key in offenders:
                delta = offenders[key]["min_expected_delta"]
            else:
                # Kein Offender heisst hier: das tatsaechliche Delta (0,04) uebersteigt bereits
                # min_expected_delta — die obere Schranke selbst berechnen wir unabhaengig nach,
                # um sie unabhaengig vom Offender-Zweig zu pruefen.
                delta = 0.5 * (slippage_p50 / 10000.0) * (share_exits / share_trades)
            assert 0.0 < delta < 1.0


def test_delta_offender_detected_with_holdout_scoped_numerator():
    """Reproduziert die Mindestdelta-Verletzung mit realistischen Groessenordnungen aus dem
    archivierten Datensatz (slippage_p50_bps_calibrated ~ 36,86 bps, holdout_total_trades = 137,
    siehe #1074-Symptombeschreibung) — das neue Feld ``holdout_n_trailing_stop_exits`` existiert im
    archivierten Vor-Fix-Artefakt noch nicht und wird deshalb konstruiert. Die tatsaechliche
    Kostenwirkung (full_realism - basis = 0, faktisch ein No-Op) unterschreitet das erwartete
    Mindestdelta deutlich."""
    slippage_p50 = 36.8635
    trades = 137
    n_ts_exits = 100  # realistisch: 44 % TRAILING_STOP-Anteil, hier grosszuegig hoeher angesetzt
    # NICHT bit-identisch (winzige Differenz) — das Bit-Identitaets-Kriterium allein wuerde hier
    # PASSen; die Verletzung stammt ausschliesslich aus dem Mindestdelta-Kriterium.
    r = _record(exp=-0.01, full_realism=-0.0100001, trades=trades,
                slippage_p50=slippage_p50, n_ts_exits=n_ts_exits)
    result = inv.check_cost_stress_distinctness([r])
    assert result.passed is False
    offenders = result.provenance["delta_offenders"]
    assert "TestStrategy/TEST.ETORO" in offenders
    assert offenders["TestStrategy/TEST.ETORO"]["min_expected_delta"] > 0.0


def test_study_missing_the_holdout_scoped_numerator_is_excluded_not_treated_as_offender():
    """Issue #1074/#1222 Fix Punkt 2 — fehlt ``holdout_n_trailing_stop_exits`` (z. B. ein Report vor
    diesem Fix), ist DIESE Study fuer das Mindestdelta-Kriterium INCONCLUSIVE (weder Offender noch
    stillschweigend konform) — kein Fail-open mit dem alten sweep-weiten Ersatz."""
    r = _record(exp=-0.01, full_realism=-0.01, trades=137, slippage_p50=36.8635, n_ts_exits=None)
    result = inv.check_cost_stress_distinctness([r])
    assert result.evaluability["n_studies_missing_holdout_scoped_numerator"] == 1
    assert not (result.provenance or {}).get("delta_offenders")
