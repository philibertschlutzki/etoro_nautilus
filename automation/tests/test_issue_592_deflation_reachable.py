"""Issue #592 — Deflations-Schwelle liegt über der Clip-Grenze ⇒ strukturell unerreichbar.

Auf der geklemmten Sortino-Skala lag die Deflations-Schwelle (σ·Φ⁻¹(0.95^(1/N))) in 4/6 Studies ÜBER
dem maximal erreichbaren Sortino (RATIO_CAP=15) ⇒ garantierter Reject. Fix: die Deflation läuft auf der
UNBESCHRÄNKTEN Reward-Skala (dem tatsächlichen Selektionskriterium argmax(reward)), mit
baseline=median(rewards). Damit ist die Schwelle im beobachteten Wertebereich erreichbar.
"""
import numpy as np

from automation.optimizer.deflation import deflated_reward_threshold


def test_deflation_threshold_reachable_across_six_studies():
    """Über 6 synthetische Studies (Rausch + realer Winner) liegt die Deflations-Schwelle IN allen
    Fällen innerhalb des beobachteten Kandidaten-Wertebereichs (min ≤ thr ≤ max)."""
    for seed in range(6):
        rng = np.random.default_rng(seed)
        noise = rng.normal(-6.8, 2.0, 100).tolist()   # Reward-Skala nicht nullzentriert
        winner = max(noise) + 8.0                       # ein Kandidat mit echtem Edge
        rewards = noise + [winner]
        thr, n, sigma, baseline = deflated_reward_threshold(rewards, confidence=0.95)
        assert thr is not None
        assert min(rewards) <= thr <= max(rewards), (
            f"Schwelle {thr:.3f} nicht im Wertebereich [{min(rewards):.3f}, {max(rewards):.3f}]")
        assert baseline == np.median(rewards)   # baseline = median, NICHT 0.0


def test_deflation_baseline_is_median_not_zero():
    """baseline=0.0 wäre auf einer bei −6.8 zentrierten Skala sinnlos — es ist der Median."""
    rewards = [-8.0, -7.0, -6.5, -6.0, -5.0]
    thr, n, sigma, baseline = deflated_reward_threshold(rewards)
    assert baseline == -6.5
    assert n == 5
    assert sigma > 0.0


def test_deflation_degenerates_below_two_candidates():
    assert deflated_reward_threshold([1.0]) == (None, 1, None, None)
    assert deflated_reward_threshold([]) == (None, 0, None, None)


def test_no_fifty_sentinel_literal_in_automation_py():
    """Issue #592 — grep '!= 50.0' über automation/*.py liefert 0 Treffer (kein driftender Sentinel)."""
    from pathlib import Path
    needle = "!= " + "50.0"   # per Konkatenation, damit DIESE Datei sich nicht selbst matcht
    root = Path(__file__).resolve().parents[1]  # automation/
    hits = []
    for py in root.rglob("*.py"):
        if "closedloop_autotuner" in str(py) or py.resolve() == Path(__file__).resolve():
            continue
        if needle in py.read_text(encoding="utf-8"):
            hits.append(str(py))
    assert hits == [], f"driftender 50.0-Sentinel-Filter noch vorhanden in: {hits}"
