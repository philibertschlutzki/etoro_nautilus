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

import pytest

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


# ── Issue #909: per_symbol_span_stats — die Achsen-Verwechslung ─────────────────────────────────
def test_per_symbol_span_stats_uses_min_span_not_endpoint_spread():
    """Akzeptanzkriterium #909 — drei Symbole mit Spannen 500/450/200 Tagen und IDENTISCHEM
    Enddatum ⇒ min_span_days=200, nicht 0 (die alte Berechnung hätte min(latest)-... == 0 ergeben,
    weil alle drei Symbole am selben Tag enden)."""
    from automation.optimizer.sweep import per_symbol_span_stats

    day_ns = 86_400 * 1_000_000_000
    end = 2_000 * day_ns  # identisches Enddatum für alle drei Symbole
    latest_ts = {"A": end, "B": end, "C": end}
    earliest_ts = {"A": end - 500 * day_ns, "B": end - 450 * day_ns, "C": end - 200 * day_ns}

    result = per_symbol_span_stats(latest_ts, earliest_ts, ["A", "B", "C"])
    assert result["min_span_days"] == pytest.approx(200.0, abs=0.01)
    assert result["median_span_days"] == pytest.approx(450.0, abs=0.01)


def test_per_symbol_span_stats_counts_symbols_below_required():
    from automation.optimizer.sweep import per_symbol_span_stats

    day_ns = 86_400 * 1_000_000_000
    end = 1_000 * day_ns
    latest_ts = {"A": end, "B": end, "C": end}
    earliest_ts = {"A": end - 500 * day_ns, "B": end - 450 * day_ns, "C": end - 200 * day_ns}

    result = per_symbol_span_stats(latest_ts, earliest_ts, ["A", "B", "C"], required_span_days=426)
    assert result["n_symbols_below_required"] == 1  # nur C (200 d) liegt unter 426


def test_per_symbol_span_stats_skips_symbols_with_missing_timestamps():
    from automation.optimizer.sweep import per_symbol_span_stats

    day_ns = 86_400 * 1_000_000_000
    latest_ts = {"A": 1000 * day_ns, "B": None}
    earliest_ts = {"A": 500 * day_ns, "B": 100 * day_ns}

    result = per_symbol_span_stats(latest_ts, earliest_ts, ["A", "B"])
    assert result["min_span_days"] == pytest.approx(500.0, abs=0.01)


def test_per_symbol_span_stats_all_missing_returns_none():
    from automation.optimizer.sweep import per_symbol_span_stats

    result = per_symbol_span_stats({}, {}, ["A", "B"])
    assert result["min_span_days"] is None
    assert result["median_span_days"] is None
    assert result["n_symbols_below_required"] is None


def test_earliest_ts_by_symbol_reads_min_statistic(tmp_path, monkeypatch):
    """Issue #909 — earliest_ts_by_symbol liest die .min-Statistik, symmetrisch zu
    latest_ts_by_symbol (.max)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from automation.optimizer import sweep as sweep_mod

    day_ns = 86_400 * 1_000_000_000
    sym_dir = tmp_path / "data" / "quote_tick" / "XOM.ETORO"
    sym_dir.mkdir(parents=True)
    table = pa.table({
        "ts_event": pa.array([100 * day_ns, 200 * day_ns, 300 * day_ns], type=pa.int64()),
        "bid_price": pa.array([1.0, 2.0, 3.0]),
    })
    pq.write_table(table, sym_dir / "data.parquet")

    earliest = sweep_mod.earliest_ts_by_symbol(["XOM.ETORO"], catalog_path=tmp_path)
    latest = sweep_mod.latest_ts_by_symbol(["XOM.ETORO"], catalog_path=tmp_path)
    assert earliest["XOM.ETORO"] == 100 * day_ns
    assert latest["XOM.ETORO"] == 300 * day_ns
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
