"""Issue #599 — Stationary-Bootstrap-Konfidenzintervalle statt Punktschätzern.

Jede Gate-Entscheidung basiert auf einem Punktschätzer aus 20–300 Trades. Der Stationary Bootstrap
(Politis & Romano, 1994) resampled die OOS-Return-/Trade-Sequenz mit GEOMETRISCH verteilten Block-
längen (erhält die Autokorrelationsstruktur, anders als der i.i.d.-Bootstrap) und liefert damit ein
Konfidenzintervall für Sortino/PF/Expectancy. Das Gate kann dann die UNTERGRENZE des CI prüfen statt
den Punktschätzer — die statistisch saubere Version dessen, was ``deflated_selection`` heuristisch
approximiert.

Rein & deterministisch (Seed-gesteuert), kein I/O — separat unit-getestet (test_issue_599).
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np


def lag1_autocorrelation(x: Sequence[float]) -> float:
    """Lag-1-Autokorrelation der Sequenz (0.0 bei < 2 Werten oder Nullvarianz)."""
    a = np.asarray(x, dtype=float)
    if a.size < 2:
        return 0.0
    a = a - a.mean()
    denom = float((a * a).sum())
    if denom <= 0.0:
        return 0.0
    return float((a[:-1] * a[1:]).sum() / denom)


def optimal_block_length(x: Sequence[float]) -> int:
    """Erwartete (mittlere) Blocklänge aus der Autokorrelation der Sequenz.

    Für einen AR(1)-Prozess mit Autokorrelation ``rho`` skaliert die integrierte Autokorrelationszeit
    mit ``(1 + |rho|) / (1 - |rho|)``. Auf ``[1, n // 2]`` geklemmt und mit einem ``n^(1/3)``-Floor
    (klassische Bootstrap-Rate), damit auch bei schwacher Autokorrelation genug Blockstruktur bleibt.
    """
    a = np.asarray(x, dtype=float)
    n = a.size
    if n < 3:
        return 1
    rho = min(abs(lag1_autocorrelation(a)), 0.98)
    tau = (1.0 + rho) / (1.0 - rho)
    floor = max(1, round(n ** (1.0 / 3.0)))
    return int(max(floor, min(n // 2, round(tau))))


def stationary_bootstrap_indices(n: int, block_length: float, rng: np.random.Generator) -> np.ndarray:
    """Ein Stationary-Bootstrap-Resample der Indizes ``0..n-1`` (Länge ``n``, Wrap-around).

    Politis/Romano: Startindex uniform; mit Wahrscheinlichkeit ``p = 1/block_length`` beginnt ein neuer
    Block (neuer uniformer Startindex), sonst wird der laufende Block um 1 (mod n) fortgesetzt. Die
    Blocklängen sind damit geometrisch verteilt mit Erwartungswert ``block_length``."""
    if n <= 0:
        return np.empty(0, dtype=int)
    p = 1.0 / max(1.0, float(block_length))
    out = np.empty(n, dtype=int)
    idx = int(rng.integers(0, n))
    for i in range(n):
        if i == 0 or rng.random() < p:
            idx = int(rng.integers(0, n))
        else:
            idx = (idx + 1) % n
        out[i] = idx
    return out


def bootstrap_ci(
    x: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    *,
    n_boot: int = 1000,
    block_length: float | None = None,
    confidence: float = 0.95,
    seed: int | None = 42,
) -> tuple[float, float, float]:
    """Stationary-Bootstrap-Konfidenzintervall für ``statistic(x)``.

    Rückgabe ``(point, lo, hi)``: der Punktschätzer auf der Originalsequenz sowie die
    ``(1-confidence)/2``- und ``(1+confidence)/2``-Perzentile der Bootstrap-Verteilung. ``block_length``
    default aus ``optimal_block_length`` (Autokorrelation). Deterministisch bei gesetztem ``seed``."""
    a = np.asarray(x, dtype=float)
    n = a.size
    point = float(statistic(a)) if n > 0 else float("nan")
    if n < 2:
        return point, point, point
    if block_length is None:
        block_length = optimal_block_length(a)
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        resample = a[stationary_bootstrap_indices(n, block_length, rng)]
        stats[b] = statistic(resample)
    alpha = (1.0 - confidence) / 2.0
    lo = float(np.nanpercentile(stats, 100.0 * alpha))
    hi = float(np.nanpercentile(stats, 100.0 * (1.0 - alpha)))
    return point, lo, hi


def sortino_statistic(returns: np.ndarray, *, mar: float = 0.0, annualization: float = 1.0) -> float:
    """Sortino der Periodenrenditen (Target-Downside-Deviation, RMS ohne Mean-Centering). Konsistent
    zur ``_calculate_stats``-Definition; ``annualization=1.0`` liefert den per-Periode-Sortino."""
    a = np.asarray(returns, dtype=float)
    if a.size == 0:
        return float("nan")
    downside = np.clip(a - mar, a_min=None, a_max=0.0)
    dd = math.sqrt(float((downside ** 2).mean()))
    if dd <= 0.0:
        return float("nan")
    return float((a.mean() - mar) / dd * math.sqrt(annualization))


def ci_lower_bound_passes(lo: float, threshold: float) -> bool:
    """Gate-Kriterium (#599): die UNTERGRENZE des CI muss die Schwelle erreichen (nicht der Punkt-
    schätzer). ``lo`` NaN (undefinierte Statistik) ⇒ False (fail-safe)."""
    return bool(lo == lo and lo >= threshold)
