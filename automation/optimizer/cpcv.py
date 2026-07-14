"""Issue #600 — Combinatorial Purged Cross-Validation (CPCV) + Probability of Backtest Overfitting (PBO).

Der aktuelle Walk-Forward ist ein EINZELPFAD (IS statisch am Anfang, 4 kontiguierliche OOS-Folds).
CPCV (López de Prado, *Advances in Financial Machine Learning*, Kap. 12) erzeugt aus denselben Daten
``C(N, k)`` Train/Test-Pfade und liefert damit eine VERTEILUNG der OOS-Performance statt eines Punkts —
und daraus direkt die PBO (Bailey/López de Prado, CSCV). Ein PBO > 0.5 bedeutet: der In-Sample-Gewinner
ist out-of-sample schlechter als der Median (Selektion überfittet).

Rein & deterministisch, kein I/O — separat unit-getestet (test_issue_600).
"""
from __future__ import annotations

from itertools import combinations
from typing import Sequence

import numpy as np


def cpcv_group_boundaries(n_obs: int, n_groups: int) -> list[tuple[int, int]]:
    """Teilt ``0..n_obs-1`` in ``n_groups`` (annähernd gleich grosse), kontiguierliche Gruppen —
    die atomaren Blöcke, aus denen die CPCV-Train/Test-Pfade kombiniert werden. Rückgabe je Gruppe
    ein halb-offenes ``(start, end)``-Intervall."""
    if n_groups <= 0 or n_obs <= 0:
        return []
    edges = np.linspace(0, n_obs, n_groups + 1).round().astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n_groups)]


def cpcv_paths(n_groups: int, k_test: int) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Alle ``C(n_groups, k_test)`` CPCV-Pfade als ``(train_groups, test_groups)``-Tupel.

    Jede Auswahl von ``k_test`` Gruppen bildet das Test-Set, der Rest das Train-Set. Für
    ``n_groups=6, k_test=2`` sind das ``C(6,2)=15`` Pfade (statt eines einzigen Walk-Forward-Pfades)."""
    if not (0 < k_test < n_groups):
        raise ValueError("cpcv_paths benötigt 0 < k_test < n_groups")
    all_groups = range(n_groups)
    paths = []
    for test in combinations(all_groups, k_test):
        test_set = set(test)
        train = tuple(g for g in all_groups if g not in test_set)
        paths.append((train, tuple(test)))
    return paths


def purged_train_groups(train_groups: Sequence[int], test_groups: Sequence[int],
                        *, embargo_groups: int = 0) -> tuple[int, ...]:
    """Entfernt aus ``train_groups`` alle Gruppen, die an ein Test-Segment angrenzen (Purge) plus
    ``embargo_groups`` unmittelbar NACH jedem Test-Segment (Embargo) — verhindert Leakage über die
    Fold-Grenzen (López de Prado Kap. 7). Rein index-basiert (Gruppen sind kontiguierlich sortiert)."""
    test_set = set(test_groups)
    forbidden = set(test_set)
    for t in test_groups:
        forbidden.add(t - 1)                       # angrenzende Gruppe davor (Purge)
        for e in range(1, embargo_groups + 1):
            forbidden.add(t + e)                   # Embargo danach
    return tuple(g for g in train_groups if g not in forbidden)


def probability_of_backtest_overfitting(is_perf: np.ndarray, oos_perf: np.ndarray) -> float:
    """PBO via die Rang-Logit-Methode (Bailey & López de Prado, CSCV).

    ``is_perf`` / ``oos_perf``: 2D-Arrays der Form ``(n_paths, n_strategies)`` — je CPCV-Pfad die
    In-Sample- bzw. Out-of-Sample-Performance jeder Kandidaten-Konfiguration. Je Pfad wird die
    IS-BESTE Konfiguration gewählt und ihr relativer OOS-Rang bestimmt: ``rank ∈ [1, N]`` mit
    ``N = OOS-bester`` (alle anderen schlechter), ``1 = OOS-schlechtester``. ``w = rank/(N+1)``,
    ``logit λ = ln(w/(1-w))``. ``PBO = P(λ ≤ 0)`` = Anteil der Pfade, in denen der IS-Gewinner OOS im
    unteren Median (w ≤ 0.5) landet. PBO > 0.5 ⇒ die Selektion überfittet systematisch.
    """
    is_arr = np.asarray(is_perf, dtype=float)
    oos_arr = np.asarray(oos_perf, dtype=float)
    if is_arr.ndim != 2 or is_arr.shape != oos_arr.shape:
        raise ValueError("is_perf/oos_perf müssen dieselbe (n_paths, n_strategies)-Form haben")
    n_paths, n_strat = is_arr.shape
    if n_paths == 0 or n_strat < 2:
        return float("nan")
    logits = []
    for p in range(n_paths):
        best = int(np.argmax(is_arr[p]))                     # IS-Gewinner dieses Pfades
        oos_row = oos_arr[p]
        # Relativer OOS-Rang ∈ [1, n_strat]: n_strat = OOS-bester (alle anderen schlechter),
        # 1 = OOS-schlechtester (keiner schlechter).
        rank = int((oos_row < oos_row[best]).sum()) + 1      # #Konfigurationen schlechter als best +1
        w = rank / (n_strat + 1.0)
        w = min(max(w, 1e-12), 1.0 - 1e-12)
        logits.append(np.log(w / (1.0 - w)))
    logits = np.asarray(logits, dtype=float)
    # w=1 (OOS-bester) ⇒ λ ≫ 0 (kein Overfit); w klein (OOS-schlecht) ⇒ λ < 0 (Overfit).
    return float((logits <= 0.0).mean())
