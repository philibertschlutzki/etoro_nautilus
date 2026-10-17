import json

import numpy as np

from automation import hypothesis_loop as hl

MECH = "Zur Aktienauktion weiten sich die Spreads, nach 30 Minuten normalisiert sich die Liquidität und Entries werden billiger."


def _cand(cid="HX", edge=60.0, n=400, nv=10, seed=0, cls="EQUITY", **kw):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 150, n)
    variants = {f"v{i}": (base + (edge if i == 0 else 0.0) + rng.normal(0, 20, n)).tolist() for i in range(nv)}
    c = {"id": cid, "asset_class": cls, "mechanism_class": "liquidity_profile", "mechanism": MECH, "intervention": {"p": cid}, "variants": variants, "primary": "v0"}
    c.update(kw)
    return c


def _cfg(tmp_path):
    p = tmp_path / "bt.json"
    p.write_text(json.dumps({"commission_bps_by_asset_class": {"CRYPTO": 200.0, "EQUITY": 10.0}}))
    return p


def test_cost_per_class(tmp_path):
    cfg = _cfg(tmp_path)
    assert hl.round_trip_cost_bps("equity", cfg) == 17.0
    assert hl.round_trip_cost_bps("CRYPTO", cfg) == 215.0
    try:
        hl.round_trip_cost_bps("BOND", cfg)
        assert False
    except ValueError:
        pass


def test_strong_edge_passes_and_noise_fails(tmp_path):
    cfg = _cfg(tmp_path)
    good = hl.falsify(_cand(edge=400.0), 0, cfg)
    assert good["passed"], good
    noise = hl.falsify(_cand(edge=0.0, seed=3), 0, cfg)
    assert not noise["passed"]


def test_costs_kill_edge_on_crypto(tmp_path):
    # 60 bps Edge, aber 215 bps Round-Trip: netto negativ
    r = hl.falsify(_cand(edge=60.0, cls="CRYPTO"), 0, _cfg(tmp_path))
    assert not r["passed"] and "net_mean_positive" in r["reasons"]


def test_prior_trials_raise_hurdle(tmp_path):
    cfg = _cfg(tmp_path)
    c = _cand(edge=60.0, seed=1)
    lo, hi = hl.falsify(c, 0, cfg)["metrics"]["dsr"], hl.falsify(c, 12920, cfg)["metrics"]["dsr"]
    assert hi <= lo


def test_non_causal_and_short_mechanism_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    assert not hl.falsify(_cand(mechanism_class="rsi_cross"), 0, cfg)["passed"]
    assert not hl.falsify(_cand(mechanism="kurz"), 0, cfg)["passed"]


def test_missing_or_nan_fails_closed(tmp_path):
    cfg = _cfg(tmp_path)
    c = _cand()
    c["variants"]["v0"][5] = float("nan")
    assert not hl.falsify(c, 0, cfg)["passed"]
    assert not hl.falsify({"id": "x"}, 0, cfg)["passed"]
    assert not hl.falsify(_cand(nv=3), 0, cfg)["passed"]  # zu wenige Varianten für PBO


def test_criteria_only_tighten():
    crit = hl.effective_criteria({"falsification": {"min_dsr": 0.5, "max_pbo": 0.9, "min_periods": 500}})
    assert crit["min_dsr"] == 0.95 and crit["max_pbo"] == 0.20 and crit["min_periods"] == 500


def test_parse_tables_and_ingest(tmp_path):
    d = tmp_path / "weekly"
    d.mkdir()
    (d / "2026-10-16.md").write_text("## Forward\n\n| Arm | Rendite | SR | MaxDD |\n|---|---|---|---|\n| C30 | -0.54 % | nan | -12.2 % |\n\n## Spread\n\n| Klasse | Median p50 bps | Modell bps |\n|---|---|---|\n| K1 | 15.5 | 3.0 |\n")
    out = hl.ingest_reports([d])
    assert len(out["notes"]) == 2 and any("MaxDD" in n for n in out["notes"])


def test_run_end_to_end_dedup_and_no_config_write(tmp_path):
    cands, cfg = tmp_path / "c", _cfg(tmp_path)
    cands.mkdir()
    (tmp_path / "reg.json").write_text(json.dumps({"hypotheses": {}}))  # leeres Registry: Deflation nur über die eigenen Varianten
    (cands / "HX.json").write_text(json.dumps(_cand("HX", edge=400.0)))
    (cands / "HY.json").write_text(json.dumps(_cand("HY", edge=0.0, seed=9, intervention={"p": "y"})))
    kw = dict(candidates_dir=cands, out_dir=tmp_path / "out", registry_path=tmp_path / "reg.json", rejected_dir=tmp_path / "rej", report_dirs=[], cfg_path=cfg, today="2026-10-24")
    r = hl.run(**kw)
    assert [v["passed"] for v in r["verdicts"]] == [True, False]
    assert (tmp_path / "out" / "proposals" / "HX.json").exists() and (tmp_path / "rej" / "HY.json").exists()
    assert "HX" in (tmp_path / "out" / "2026-10-24.md").read_text()
    r2 = hl.run(**kw)
    assert r2["verdicts"] == [] and sorted(r2["skipped"]) == ["HX", "HY"]


def test_seed_registry_loaded_and_counts_trials(tmp_path):
    reg = hl.load_registry(tmp_path / "none.json")
    assert {"H1", "H9", "CAL1", "DRIFT1"} <= set(reg["hypotheses"]) and hl.total_trials(reg) >= 12920
