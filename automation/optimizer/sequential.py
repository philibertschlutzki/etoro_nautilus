"""automation/optimizer/sequential.py
====================================
Issue #1368 (GH #1265, P1, Ertragshebel) — sequenziell korrigierter Promotionstest auf FORWARD-Evidenz.

Ausgangslage: die Stundentiefe wächst höchstens einen Tag je Tag (#1363), die volle Walk-Forward-Geometrie ist
frühestens 2027-09 erreichbar, und ein 60-Tage-Holdout zertifiziert nur Sharpe ≥ 4 p. a. (#1367). Evidenz, die
nicht aus der Historie stammt, entsteht nur vorwärts: ein Paar wird mit eingefrorenen Parametern im Demo-Konto
inkubiert (``automation/incubation.py``), seine Netto-Renditen je Session-Bar landen im Evidenz-Ledger, und
dieser Test entscheidet an GEPLANTEN Prüfzeitpunkten (wöchentlich, höchstens ``K_max``):

* ``PROMOTE``  — ``PSR_boot(ledger; SR* = 0) >= 1 − (1 − conf) / (K_max · n_concurrent)`` (Bonferroni über die
  Prüfzeitpunkte UND die parallel inkubierten Kandidaten; Standardfehler wie ``deflation.bootstrap_psr_z``).
* ``RETIRE``   — der einseitige Test auf SR < 0 schlägt auf demselben Niveau an (``1 − PSR >= Schwelle``).
* ``EXHAUSTED``— ``K_max`` Prüfungen ohne Entscheidung (Rückzug: die Evidenz reicht nicht).
* ``CONTINUE`` — sonst.

Die Schwelle (0,95 familienweit) sinkt nicht — die Stichprobe wächst. Rein (kein I/O), deterministisch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

K_MAX_DEFAULT = 26
N_CONCURRENT_DEFAULT = 3
CONFIDENCE_DEFAULT = 0.95
MIN_LEDGER_BARS = 20

_ND = NormalDist()


def bonferroni_threshold(confidence: float = CONFIDENCE_DEFAULT, k_max: int = K_MAX_DEFAULT,
                         n_concurrent: int = N_CONCURRENT_DEFAULT) -> float:
    """``1 − (1 − confidence) / (k_max · n_concurrent)`` — die je Prüfzeitpunkt und Kandidat geforderte PSR."""
    return 1.0 - (1.0 - float(confidence)) / (max(1, int(k_max)) * max(1, int(n_concurrent)))


@dataclass(frozen=True)
class SequentialDecision:
    decision: str            # PROMOTE | RETIRE | EXHAUSTED | CONTINUE
    psr: float | None
    threshold: float
    look_index: int
    n_bars: int
    reason: str

    def to_dict(self) -> dict:
        return {"decision": self.decision, "psr": self.psr, "threshold": self.threshold,
                "look_index": self.look_index, "n_bars": self.n_bars, "reason": self.reason}


def sequential_decision(
    returns, *, look_index: int, k_max: int = K_MAX_DEFAULT, n_concurrent: int = N_CONCURRENT_DEFAULT,
    confidence: float = CONFIDENCE_DEFAULT, n_boot: int = 500, seed: int = 42,
    min_bars: int = MIN_LEDGER_BARS, psr_fn=None,
) -> SequentialDecision:
    """Entscheidung am Prüfzeitpunkt ``look_index`` (1-basiert) über die Ledger-Renditen ``returns`` (Netto-Rendite
    je Session-Bar, NUR des aktuellen ``params_sha256``). ``psr_fn(returns) -> PSR(SR* = 0)``; Default: der
    Bootstrap-PSR aus ``deflation.bootstrap_psr`` (Stationary Bootstrap, Serienabhängigkeit/Schiefe berücksichtigt)."""
    threshold = bonferroni_threshold(confidence, k_max, n_concurrent)
    values = [float(r) for r in (returns or [])]
    if len(values) < min_bars:
        decision = "EXHAUSTED" if look_index >= k_max else "CONTINUE"
        return SequentialDecision(decision, None, threshold, look_index, len(values),
                                  f"weniger als {min_bars} Ledger-Bars")
    if psr_fn is None:
        from automation.optimizer.deflation import bootstrap_psr

        def psr_fn(r):
            return bootstrap_psr(r, sr_star=0.0, n_boot=n_boot, seed=seed)[0]
    psr = psr_fn(values)
    if psr is not None and psr >= threshold:
        return SequentialDecision("PROMOTE", psr, threshold, look_index, len(values),
                                  "PSR_boot >= Bonferroni-Schwelle")
    if psr is not None and (1.0 - psr) >= threshold:
        return SequentialDecision("RETIRE", psr, threshold, look_index, len(values),
                                  "einseitiger Test auf SR < 0 schlägt an")
    if look_index >= k_max:
        return SequentialDecision("EXHAUSTED", psr, threshold, look_index, len(values),
                                  f"{k_max} Prüfzeitpunkte ohne Entscheidung")
    return SequentialDecision("CONTINUE", psr, threshold, look_index, len(values), "weiter sammeln")


def _analytic_psr_matrix(cum_sum: np.ndarray, cum_sq: np.ndarray, n: int) -> np.ndarray:
    """PSR(SR* = 0) je Pfad aus kumulierten Summen (Normal-Momente: γ₃ = 0, γ₄ = 3), vektorisiert."""
    mean = cum_sum / n
    var = np.maximum(cum_sq / n - mean * mean, 1e-18)
    sr = mean / np.sqrt(var)
    den = np.sqrt(1.0 + 0.5 * sr * sr)
    z = sr * math.sqrt(max(n - 1, 1)) / den
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def simulate_sequential(
    *, n_paths: int = 10_000, k_max: int = K_MAX_DEFAULT, n_concurrent: int = N_CONCURRENT_DEFAULT,
    sr_annual: float = 0.0, bars_per_look: int = 35, bars_per_year: float = 252.0 * 7,
    confidence: float = CONFIDENCE_DEFAULT, seed: int = 7, min_bars: int = MIN_LEDGER_BARS,
) -> dict:
    """Monte-Carlo des sequenziellen Tests (analytische PSR je Look statt Bootstrap — dieselbe Schwelle,
    für 10 000 Pfade praktikabel). Je Familie ``n_concurrent`` unabhängige Kandidaten mit wahrer annualisierter
    Sharpe ``sr_annual`` (Renditen i.i.d. normal). Rückgabe:

    * ``false_promotion_rate`` — Anteil der Familien mit MINDESTENS einer Promotion (unter H0 die familienweite
      Fehlpromotionsrate, Ziel ≤ 1 − confidence),
    * ``promotion_rate`` — Anteil der Kandidaten mit Promotion,
    * ``median_looks_to_promotion``/``median_bars_to_promotion`` — über die promovierten Kandidaten."""
    rng = np.random.default_rng(seed)
    threshold = bonferroni_threshold(confidence, k_max, n_concurrent)
    sr_bar = float(sr_annual) / math.sqrt(bars_per_year)
    n_cand = int(n_paths) * int(n_concurrent)
    promoted_at = np.full(n_cand, -1, dtype=int)
    retired = np.zeros(n_cand, dtype=bool)
    cum_sum = np.zeros(n_cand)
    cum_sq = np.zeros(n_cand)
    for look in range(1, k_max + 1):
        block = rng.normal(loc=sr_bar, scale=1.0, size=(n_cand, bars_per_look))
        cum_sum += block.sum(axis=1)
        cum_sq += (block * block).sum(axis=1)
        n = look * bars_per_look
        if n < min_bars:
            continue
        active = (promoted_at < 0) & ~retired
        if not active.any():
            break
        psr = _analytic_psr_matrix(cum_sum[active], cum_sq[active], n)
        idx = np.flatnonzero(active)
        promote = psr >= threshold
        promoted_at[idx[promote]] = look
        retired[idx[(~promote) & ((1.0 - psr) >= threshold)]] = True
    promoted = promoted_at > 0
    family_any = promoted.reshape(int(n_paths), int(n_concurrent)).any(axis=1)
    looks = promoted_at[promoted]
    return {
        "threshold": threshold,
        "false_promotion_rate": float(family_any.mean()),
        "promotion_rate": float(promoted.mean()),
        "retire_rate": float(retired.mean()),
        "median_looks_to_promotion": float(np.median(looks)) if looks.size else None,
        "median_bars_to_promotion": float(np.median(looks) * bars_per_look) if looks.size else None,
        "n_paths": int(n_paths), "k_max": int(k_max), "n_concurrent": int(n_concurrent),
        "sr_annual": float(sr_annual), "bars_per_look": int(bars_per_look),
    }
