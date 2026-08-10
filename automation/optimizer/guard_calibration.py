"""automation/optimizer/guard_calibration.py
===========================================
Issue #943 (Katalog B, P0 HEADLINE, Pitfall #293) — der Sortino-Numerik-Guard
(``backtest_runner._effective_sortino_numeric_guard``) zieht seinen Schwellenwert unter
``sortino_numeric_guard_reference='family_median'`` aus dem Median von ``oos_n_periods`` über
bereits ABGESCHLOSSENE Sibling-Trials DERSELBEN, noch laufenden Study
(``run_optimization._resolve_family_median_n_periods``). Das ist eine SELBSTBEZÜGLICHE Filterung:

  * Ordnungsabhängig — der Anker wandert innerhalb einer Study, je mehr Siblings abschliessen
    (beobachtet: 218.0 -> 260.0, +19.3 % innerhalb einer Study).
  * Nicht reproduzierbar unter Parallelität — bei ``n_jobs > 1`` entscheidet der Scheduler, welche
    Siblings zum Auswertungszeitpunkt eines Trials bereits im Median stecken; zwei Läufe mit
    identischem Optuna-Seed liefern unterschiedliche Guard-Entscheidungen.
  * Endogen — der Trial trägt selbst zum Median bei, an dem seine Nachfolger gemessen werden.

Dieses Modul ist die EXOGENE Alternative: eine VOR dem ersten Trial, aus den Marktdaten selbst
(Stationary-Bootstrap-Nullverteilung der annualisierten Sortino-Statistik) berechnete Tabelle
``{n_periods: guard_quantile}``. Sie ist für alle Trials einer Study identisch (stationär),
deterministisch aus ``(bar_returns, seed, alpha, n_boot, block_mean)`` (reproduzierbar) und hängt
nur von den Marktdaten ab, nie von den Trials, die der Wächter beurteilt (exogen).

Scope-Hinweis (analog #843/#845/#954/#962-Zurückstellungen in AGENTS.md): dieses Modul liefert die
KORREKTE, GETESTETE Berechnung. Die Verdrahtung in den Live-Pfad (``sortino_numeric_guard_reference
='bootstrap_h0'`` als produktiver Default, Aufruf VOR dem ersten Trial einer Study mit den realen
Bar-Returns des Symbols) ist bewusst NICHT Teil dieses Fixes — sie würde eine Restrukturierung der
Study-Erstellung (``run_optimization.optimize_symbol``) voraussetzen, um die realen Bar-Returns aus
dem Daten-Katalog VOR der Trial-Schleife zu laden, ohne einen Live-Lauf zur empirischen
Regressionsverifikation in diesem Environment (dieselbe Zurückhaltung wie #799/#828 bei #962s
globaler Warteschlange). ``sortino_numeric_guard_reference`` bleibt vorerst ``'family_median'``.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def _stationary_bootstrap_sample(x: np.ndarray, n: int, block_mean: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """Politis/Romano Stationary-Bootstrap: zieht eine Länge-``n``-Stichprobe aus ``x`` per
    zirkulärem Block-Resampling mit geometrisch verteilter Blocklänge (Erwartungswert
    ``block_mean``). Erhält Autokorrelation/Heteroskedastizität der Originalserie (im Gegensatz zu
    einem i.i.d.-Bootstrap), zerstört aber jedes gerichtete Signal (H0-Nullverteilung)."""
    m = len(x)
    p = 1.0 / float(block_mean)
    out = np.empty(n, dtype=float)
    idx = int(rng.integers(0, m))
    i = 0
    while i < n:
        out[i] = x[idx]
        i += 1
        idx = int(rng.integers(0, m)) if rng.random() < p else (idx + 1) % m
    return out


def _annualized_sortino(sample: np.ndarray, *, mar: float = 0.0,
                        annualization_factor: float = 1.0) -> float:
    """Dieselbe Konstruktion wie ``backtest_runner._calculate_stats`` (RMS-Downside-Deviation ohne
    Mean-Centering, annualisiert über sqrt(annualization_factor)) — der Guard muss auf DERSELBEN
    Statistik kalibriert sein, die er beurteilt."""
    downside = np.clip(sample - mar, None, 0.0)
    dd_dev = float(np.sqrt(np.mean(downside ** 2)))
    if dd_dev <= 0.0 or not np.isfinite(dd_dev):
        return 0.0
    mean_ret = float(np.mean(sample))
    return (mean_ret - mar) / dd_dev * float(np.sqrt(annualization_factor))


def calibrate_sortino_guard_table(
    bar_returns: Sequence[float] | np.ndarray, *,
    n_grid: Sequence[int],
    alpha: float = 0.001,
    n_boot: int = 20_000,
    block_mean: float = 24.0,
    mar: float = 0.0,
    annualization_factor: float = 1.0,
    seed: int,
) -> dict[int, float]:
    """Quantil der H0-Verteilung von ``|Sortino_annualized|`` je Stichprobenumfang ``n``.

    H0: die Perioden-Returns des Symbols selbst, umgeordnet per Stationary Bootstrap — erhält
    Autokorrelation/Heteroskedastizität, zerstört jedes Signal. Rückgabe: ``{n: q_(1-alpha)}`` für
    ``n`` in ``n_grid``. Deterministisch bei festem ``seed`` (reproduzierbar, Akzeptanzkriterium
    #943-2: identischer Seed liefert bitgleiche Tabelle unabhängig von ``n_jobs``, weil diese
    Funktion NIE von der Trial-Ausführungsreihenfolge abhängt).

    Eigenschaften ggü. der ``family_median``-Konstruktion (siehe Moduldocstring):
    1. Stationär — für alle Trials einer Study identisch, weil VOR dem ersten Trial berechnet.
    2. Reproduzierbar — deterministisch aus ``(bar_returns, seed, alpha, n_boot, block_mean)``.
    3. Exogen — hängt nur von den Marktdaten ab, nie von den beurteilten Trials.
    4. Kalibriert — die Fehlerrate ist per Konstruktion ``alpha``, kein geratener Faktor.

    Leere/degenerierte ``bar_returns`` (alle NaN, oder < 2 endliche Werte) ⇒ ``{}`` (fail-open —
    der Aufrufer behandelt eine leere Tabelle wie "keine Kalibrierung verfügbar", analog
    ``family_median_unavailable``)."""
    x = np.asarray(bar_returns, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 2:
        return {}
    rng = np.random.default_rng(seed)
    table: dict[int, float] = {}
    for n in sorted({int(v) for v in n_grid if v and v > 0}):
        boot_stats = np.empty(n_boot, dtype=float)
        for b in range(n_boot):
            sample = _stationary_bootstrap_sample(x, n, block_mean, rng)
            boot_stats[b] = _annualized_sortino(
                sample, mar=mar, annualization_factor=annualization_factor)
        table[n] = float(np.quantile(np.abs(boot_stats), 1.0 - alpha))
    return table


def guard_value_for_n_periods(table: dict[int, float], n_periods: int) -> float | None:
    """Log-linear interpolierter Lookup in einer kalibrierten Tabelle (siehe
    ``calibrate_sortino_guard_table``) für ein ``n_periods``, das nicht exakt im ``n_grid`` liegt.
    Ausserhalb des kalibrierten Bereichs wird auf den jeweils nächstgelegenen Rand geklemmt (die
    Tabelle deckt per Konstruktion nur den beobachteten ``n_grid``-Bereich ab — Extrapolation über
    eine Bootstrap-Quantilsschätzung hinaus wäre unbegründet). ``None`` bei leerer Tabelle."""
    if not table:
        return None
    ns = sorted(table)
    if n_periods <= ns[0]:
        return table[ns[0]]
    if n_periods >= ns[-1]:
        return table[ns[-1]]
    if n_periods in table:
        return table[n_periods]
    import bisect
    i = bisect.bisect_left(ns, n_periods)
    lo, hi = ns[i - 1], ns[i]
    lo_v, hi_v = table[lo], table[hi]
    if lo_v <= 0.0 or hi_v <= 0.0:
        # log-lineare Interpolation ist fuer nicht-positive Quantile nicht definiert (sollte fuer
        # eine |Sortino|-Quantilstabelle nicht vorkommen) -- fail-open auf lineare Interpolation.
        frac = (n_periods - lo) / (hi - lo)
        return lo_v + frac * (hi_v - lo_v)
    log_lo, log_hi = np.log(lo_v), np.log(hi_v)
    frac = (np.log(n_periods) - np.log(lo)) / (np.log(hi) - np.log(lo))
    return float(np.exp(log_lo + frac * (log_hi - log_lo)))


def guard_table_hash(table: dict[int, float]) -> str:
    """SHA-256 über eine deterministisch serialisierte Kalibrierungstabelle (Issue #943/#948 —
    ``study.user_attrs['sortino_guard_table_hash']``): macht die verwendete Schwelle aus einem
    einzigen ``INFERENCE_DIAGNOSTIC``-Event heraus auditierbar/reproduzierbar (Hash + n + alpha
    ⇒ Wert), statt sie nur durch Handrekonstruktion über mehrere Events erschliessbar zu machen."""
    import hashlib
    import json
    serialized = json.dumps({str(k): v for k, v in sorted(table.items())}, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
