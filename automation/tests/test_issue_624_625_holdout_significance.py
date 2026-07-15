"""Issues #624 + #625 — Holdout-Signifikanz ehrlich tragen, familienweise Multiple-Testing-Zahl.

#624: Ein 45-Tage-Holdout (T≈202 MTM-Perioden) kann eine 0.95-PSR-Entscheidung für einen Grenz-
kandidaten (per-Periode-Sortino ≈ 0.114) NICHT erreichen — PSR(0)=0.9464 < 0.95, T≥211 nötig. Die
Schwelle wird bewusst NICHT gesenkt; die Entscheidung ist in manuals/strategie_optimierung.md
§Holdout-Signifikanz dokumentiert und wird beim Sweep-Start geloggt.

#625: Je Symbol konkurrieren mehrere Strategien-Studies; die familienweise Zahl N_family = Σ eligibler
Trials über die Studies desselben Symbols wird aggregiert und in sweep_completed telemetriert.
"""
from math import sqrt
from pathlib import Path
from statistics import NormalDist

import json

from automation.optimizer.sweep import _family_n_from_proposals


# ---------------------------------------------------------------------------- #625

def _proposal(symbol, n_eligible):
    ms = {} if n_eligible is None else {"deflation_n_eligible": n_eligible}
    return {"symbol": symbol, "holdout": {"symbol": ms}}


def test_family_n_sums_studies_per_symbol_dict_path():
    props = [_proposal("AAA.ETORO", 40), _proposal("AAA.ETORO", 30),
             _proposal("BBB.ETORO", 12)]
    assert _family_n_from_proposals(props) == {"AAA.ETORO": 70, "BBB.ETORO": 12}


def test_family_n_reads_written_proposal_paths(tmp_path):
    """Produktionspfad: run_per_symbol_sweep sammelt Path-Objekte, nicht Dicts (Regressionsschutz —
    ein isinstance(dict)-Guard allein würde ALLE Proposals überspringen ⇒ family_n stets leer)."""
    paths = []
    for i, p in enumerate([_proposal("AAA.ETORO", 40), _proposal("AAA.ETORO", 30),
                           _proposal("BBB.ETORO", 12)]):
        fp = tmp_path / f"proposal_{i}.json"
        fp.write_text(json.dumps(p), "utf-8")
        paths.append(fp)
    assert _family_n_from_proposals(paths) == {"AAA.ETORO": 70, "BBB.ETORO": 12}


def test_family_n_absent_deflation_contributes_zero(tmp_path):
    # Kohorte < 2 eligible ⇒ confirm setzt deflation_n_eligible NICHT ⇒ Symbol trägt nicht bei.
    props = [_proposal("AAA.ETORO", None), _proposal("BBB.ETORO", 5)]
    assert _family_n_from_proposals(props) == {"BBB.ETORO": 5}


def test_family_n_robust_to_bad_inputs(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", "utf-8")
    missing = tmp_path / "does_not_exist.json"
    # None, ein Nicht-Pfad-Int, kaputte JSON und ein fehlender Pfad dürfen NICHT werfen.
    assert _family_n_from_proposals([None, 42, bad, missing, {"holdout": None}]) == {}


def test_family_n_empty_and_none():
    assert _family_n_from_proposals([]) == {}
    assert _family_n_from_proposals(None) == {}


def test_family_n_ignores_bool_n_eligible():
    # bool ist ein int-Subtyp; True/False dürfen nicht als Trial-Zahl durchrutschen.
    assert _family_n_from_proposals([{"symbol": "AAA.ETORO",
                                      "holdout": {"symbol": {"deflation_n_eligible": True}}}]) == {}


# ---------------------------------------------------------------------------- #624

_SWEEP_SRC = Path("automation/optimizer/sweep.py").read_text("utf-8")
_MANUAL = Path("manuals/strategie_optimierung.md").read_text("utf-8")


def test_sweep_logs_holdout_geometry_and_references_manual():
    assert "[#624] Holdout-Geometrie" in _SWEEP_SRC
    assert "required_span_days" in _SWEEP_SRC
    assert "§Holdout-Signifikanz" in _SWEEP_SRC


def test_manual_documents_holdout_significance_decision():
    assert "§Holdout-Signifikanz" in _MANUAL
    # Die drei tragenden Aussagen der Entscheidung müssen im Handbuch stehen.
    assert "0.95" in _MANUAL and "211" in _MANUAL
    assert "Typ-I" in _MANUAL or "Type-I" in _MANUAL
    # Der bevorzugte Auflösungspfad (Historie backfillen) statt Schwelle senken.
    assert "backfill" in _MANUAL.lower() or "Historie" in _MANUAL


def test_documented_psr_reference_math_reproduces():
    """Die im Handbuch dokumentierten Zahlen müssen exakt reproduzierbar sein (kein Hand-Waving)."""
    N = NormalDist()
    sr = 0.1136  # per-Periode-Sortino des Grenzkandidaten
    # T=202 (heute): PSR(0) < 0.95
    psr_202 = N.cdf(sr * sqrt(202 - 1))
    assert round(psr_202, 4) == 0.9464
    assert psr_202 < 0.95
    # T=211: gerade über 0.95
    psr_211 = N.cdf(sr * sqrt(211 - 1))
    assert psr_211 >= 0.95
    # Kleinstes T, das 0.95 trägt, ist 211 (T=210 reicht noch nicht).
    assert N.cdf(sr * sqrt(210 - 1)) < 0.95
