"""Issue #553 — Deflated-Sortino-Selektion (Multiple-Testing-Korrektur).

Wählt man den besten von ``N`` getesteten Konfigurationen, ist die erwartete beste Performance
auch unter H0 (kein echter Edge) positiv und wächst mit ``N``. Ohne Korrektur ist die
False-Positive-Rate der Winner-Selektion hoch: bei genügend Trials überschreitet irgendeine
Parametrisierung das Gate rein zufällig (Bailey & López de Prado, *Deflated Sharpe Ratio*).

Dieses Modul liefert eine Schwelle, die das kontrolliert. Rein & deterministisch (kein I/O,
kein globaler State) — separat Monte-Carlo-getestet (test_issue_553).
"""
import math
from statistics import NormalDist

_ND = NormalDist()
# Euler–Mascheroni-Konstante (für die Erwartungswert-Approximation des Maximums).
_EULER_MASCHERONI = 0.5772156649015329


def psr_z(sr, n_periods, *, skew: float = 0.0, kurtosis: float = 3.0,
          sr_star: float = 0.0) -> float | None:
    """Issue #630 — z-Score der Probabilistic Sharpe/Sortino Ratio (die unbeschränkte Effektstärke).

    ``z = (ŜR − SR*)·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²)``

    Das unbeschränkte Argument der CDF Φ. Dient als Ranking-Base anstelle der zu
    rasch sättigenden Wahrscheinlichkeit (CDF), da es monoton skaliert und
    nicht an einer 1.0-Decke klemmt.

    ``None`` bei undefinierter Eingabe oder nicht-positivem Varianz-Term (analog PSR).
    """
    if sr is None or n_periods is None or n_periods < 2:
        return None
    sr = float(sr)
    denom_var = 1.0 - float(skew) * sr + ((float(kurtosis) - 1.0) / 4.0) * (sr * sr)
    if denom_var <= 0.0:
        return None
    return (sr - float(sr_star)) * math.sqrt(float(n_periods) - 1.0) / math.sqrt(denom_var)


def probabilistic_sharpe_ratio(sr, n_periods, *, skew: float = 0.0, kurtosis: float = 3.0,
                               sr_star: float = 0.0):
    """Issue #614 — Probabilistic Sharpe/Sortino Ratio (Bailey & López de Prado).

    ``PSR(SR*) = Φ[ (ŜR − SR*)·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²) ]``

    ``ŜR`` = **per-Perioden**-Ratio (Sharpe ODER Sortino — NICHT annualisiert), ``T`` = Anzahl
    Perioden (OOS-Bars), ``γ₃`` = Schiefe, ``γ₄`` = (NICHT-Exzess-)Kurtosis der Periodenrenditen
    (Normal ⇒ 3.0). Skalenfrei und in ``[0, 1]`` beschränkt — die asinh-Sättigung (#559) wird damit
    überflüssig. **Annualisierungs-invariant**: ``ŜR`` ist per-Periode, die Reskalierung mit √A (die
    Punktschätzer UND Standardfehler gleich multipliziert, #614) fällt vollständig heraus.

    ``None`` bei undefinierter Eingabe (``sr is None``, ``T < 2``) oder nicht-positivem Varianz-Term
    (numerisch degeneriert). Referenz (#614/#618): ``ŜR=0.11386, T=202, γ₃=0, γ₄=3, SR*=0 ⇒ 0.9463``.
    """
    z = psr_z(sr, n_periods, skew=skew, kurtosis=kurtosis, sr_star=sr_star)
    if z is None:
        return None
    return float(_ND.cdf(z))


def sample_skew_kurtosis(returns) -> tuple[float, float]:
    """(Schiefe γ₃, NICHT-Exzess-Kurtosis γ₄) einer Renditesequenz. Bei < 2 Werten oder Nullvarianz
    ⇒ (0.0, 3.0) (Normal-Annahme, neutral für die PSR-Korrektur). Population-Momente (kein Bessel)."""
    import numpy as _np
    a = _np.asarray(list(returns), dtype=float)
    if a.size < 2:
        return 0.0, 3.0
    m = a.mean()
    s = a.std()  # Population-Std
    if not (s > 0.0):
        return 0.0, 3.0
    z = (a - m) / s
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    return skew, kurt


def expected_max_standard_normal(n_trials: int) -> float:
    """E[max von ``n_trials`` i.i.d. N(0, 1)] — Bailey/López-de-Prado-Approximation.

    ``E[max_N] ≈ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))`` mit γ = Euler–Mascheroni. Der
    ERWARTUNGSWERT des Maximums (nicht das Quantil) — nützlich für Telemetrie/Intuition.
    """
    if n_trials <= 1:
        return 0.0
    return ((1.0 - _EULER_MASCHERONI) * _ND.inv_cdf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * _ND.inv_cdf(1.0 - 1.0 / (n_trials * math.e)))


def sr0_multiple_testing(var_sr_trials: float, n_trials: int) -> float:
    """Issue #618 — die Multiple-Testing-Nullhypothesen-Ratio SR₀ (Bailey/López de Prado, DSR).

    ``SR₀ = √V[ŜR_trials] · E[max_N standard normal]`` mit ``E[max_N] = expected_max_standard_normal(N)``.
    Der erwartete Bestwert von N getesteten Konfigurationen unter H0 (kein Edge) wächst mit N — genau
    diese Hürde muss der Gewinner schlagen. ``V[ŜR_trials]`` = Streuung der Ratios ÜBER die N Trials
    (die Multiple-Testing-Varianz), NICHT der Standardfehler des Gewinners (der steckt in der PSR/DSR).
    Degeneriert auf 0.0 bei fehlender Streuung oder N ≤ 1 (kein Multiple-Testing)."""
    if var_sr_trials is None or var_sr_trials <= 0.0 or n_trials <= 1:
        return 0.0
    return math.sqrt(float(var_sr_trials)) * expected_max_standard_normal(n_trials)


def lo2002_sharpe_variance(sr: float, n_periods: int) -> float:
    """Issue #653 — Lo (2002): asymptotische Stichprobenvarianz eines geschätzten Sharpe/Sortino-
    Ratios unter i.i.d.-normalverteilten Perioden-Renditen: ``Var[ŜR] ≈ (1 + ŜR²/2) / T``.

    ``T = n_periods`` (OOS-Perioden je Schätzer), ``ŜR`` = per-Perioden-Ratio. Rein deterministisch
    (kein I/O). ``sr=0.0`` (der in ``sr0_multiple_testing_robust`` verwendete Default) ist die
    Varianz UNTER DER NULLHYPOTHESE „kein Edge" — die konservativste, parameterfreie Referenz (keine
    Annahme über den wahren SR-Level nötig, konsistent mit dem Multiple-Testing-Null-Modell selbst).
    Skaliert erwartungsgemäss invers mit T: kürzeres OOS-Fenster (kleineres T) ⇒ höhere
    Schätz-Unsicherheit ⇒ grössere Varianz ⇒ konservativerer Floor."""
    if n_periods is None or n_periods <= 1:
        raise ValueError("lo2002_sharpe_variance: n_periods muss > 1 sein (T = OOS-Perioden)")
    return (1.0 + (float(sr) ** 2) / 2.0) / float(n_periods)


def _cohort_shrinkage_weight(n_trials: int, min_cohort: int) -> float:
    """Issue #653 — STETIGES Shrinkage-Gewicht λ(N) ∈ (0, 1] Richtung der theoretischen
    Referenz-Varianz: ``λ(N) = min_cohort / (min_cohort + N)``.

    ``λ(0) = 1`` (volles Vertrauen in die Theorie bei N=0), ``λ(min_cohort) = 0.5`` (hälftiger
    Blend), ``λ(N) → 0`` für ``N → ∞`` (volles Vertrauen in die empirische Kohorten-Varianz, sobald
    genug Punkte vorliegen). STETIG in N — anders als der alte harte Cutover bei ``min_cohort``
    (N=min_cohort−1 erhielt 100 % Floor, N=min_cohort 0 % Floor: ein Sprung um Grössenordnungen
    zwischen zwei fast identischen Kohorten, #653-Symptom)."""
    n = max(0, int(n_trials))
    k = max(1, int(min_cohort))
    return float(k) / float(k + n)


def sr0_multiple_testing_robust(
    var_sr_trials: float | None, n_trials: int, *,
    min_cohort: int = 10, n_periods: int, sr_estimate: float = 0.0,
    variance_n_trials: int | None = None,
) -> tuple[float, bool, float, str]:
    """Issue #636/#653/#685/#701 — robuste SR₀-Schätzung gegen Small-Cohort-Degeneration, STETIG in N.

    ``V[ŜR_trials]`` aus einer 2-3-Punkte-Kohorte (z. B. VwapExhaustion N=3, Hourly N=2) ist
    statistisch bedeutungslos (beobachtet: ``deflation_var_sr = 2.4e-9`` für Hourly — eine
    Rundungsartefakt-Grössenordnung, keine echte Streuung). Die empirische Varianz wird daher gegen
    eine THEORETISCH begründete Referenz geshrinkt (``_cohort_shrinkage_weight``), NIE unterschritten
    im Erwartungswert, sodass SR₀ nicht durch eine zufällig winzige Stichproben-Varianz unterschätzt
    wird — die Multiple-Testing-Hürde bleibt mindestens so streng wie die Theorie es vorgibt
    (Fail-loud-Log obliegt dem Aufrufer).

    Issue #653 — VOR #653 war der Floor eine einzige HARTE Konstante (``var_floor=0.0018``) mit
    einem DISKONTINUIERLICHEN Cutover bei ``min_cohort`` (``max(observed, var_floor)`` nur unterhalb
    ``min_cohort``, sonst die rohe Stichproben-Varianz) — ein Faktor-~3.5-Sprung zwischen N=9 und
    N=10 bei sonst fast identischen Kohorten, weil die Kohorten-Varianz selbst ein Selektions-
    Artefakt ist (das Gate censoriert die Verteilung). Fix: ``effective_var = λ(N)·theoretical_var +
    (1−λ(N))·observed`` mit dem STETIGEN Shrinkage-Gewicht ``λ(N)`` (``_cohort_shrinkage_weight``) —
    kein Cutover, SR₀(N) ist über JEDEN N-Übergang stetig. ``theoretical_var`` folgt Lo (2002)
    (``lo2002_sharpe_variance``, ``T = n_periods``, T-bewusst: kürzeres OOS-Fenster ⇒ konservativerer
    Floor).

    Issue #701 — ``n_periods`` ist SEIT #701 ein PFLICHT-Parameter (kein Default mehr) und der
    frühere ``var_floor``-Legacy-Fallback (Issue #685-Deprecation) ENTFÄLLT ERSATZLOS: die
    Verifikation belegte, dass ``oos_sortino_period`` an JEDER Produktions-Call-Site (confirm.py,
    run_optimization.py) NIE ohne ein zugehöriges, ebenfalls truthy ``oos_n_periods`` gestempelt
    wird — beide stammen aus DEMSELBEN ``backtest_runner.py``-Berechnungsblock (``sortino_period``
    wird NUR gesetzt, nachdem ``n_periods = len(period_rets)`` bereits > 0 ist, siehe
    ``backtest_runner._calculate_stats``), der Fallback-Zweig war auf jedem Entscheidungs-Pfad
    dieses Systems bereits TOT. Der Key ``deflation_var_floor`` (tournament.json) ist entfernt —
    ein fehlendes/ungültiges ``n_periods`` bricht jetzt FAIL-LOUD ab (``ValueError``), statt still
    auf eine geratene Konstante zurückzufallen (Zero-Hardcoding: kein Entscheidungs-Pfad soll je
    auf einer nicht-konfigurierbaren Magic-Number statt der T-bewussten Theorie beruhen).

    Issue #652 — ``n_trials`` und ``variance_n_trials`` sind ABSICHTLICH ENTKOPPELT: ``n_trials``
    treibt AUSSCHLIESSLICH ``E[max_N]`` (die Multiple-Testing-MULTIPLIZITÄT — bei #652 familienweit,
    also potenziell GRÖSSER als die tatsächliche Kohorte, aus der ``var_sr_trials`` geschätzt wurde);
    ``variance_n_trials`` (Default: ``n_trials``, Rückwärtskompat für Nicht-Familien-Aufrufer) treibt
    AUSSCHLIESSLICH das Shrinkage-Gewicht ``λ`` — die VERLÄSSLICHKEIT der Varianz-SCHÄTZUNG hängt von
    der Anzahl der tatsächlich beobachteten Datenpunkte ab, NICHT von der (grösseren) familienweiten
    Multiplizität. Würden beide dieselbe (familienweite) Zahl teilen, verschöbe eine grosse
    Familien-N das Shrinkage-Gewicht fälschlich Richtung „viele Datenpunkte" und liesse eine winzige,
    unzuverlässige empirische Varianz dominieren — SR₀ könnte dadurch mit wachsendem N_family sogar
    SINKEN (das Gegenteil der beabsichtigten strengeren Hürde).

    Issue #670 — die Rückgabe trägt zusätzlich das TATSÄCHLICH verwendete Shrinkage-Gewicht
    (``shrinkage_lambda``, ``= λ(N)`` — nicht nur das boolesche ``floor_dominant = λ ≥ 0.5``) und die
    theoretische Referenz-QUELLE (``theoretical_var_source`` — SEIT #701 IMMER ``'lo2002'``, als
    Rückwärtskompat-Feld für bestehende Telemetrie-Konsumenten erhalten, keine Fallunterscheidung
    mehr). ``floor_dominant`` bedeutet ausschliesslich „das Shrinkage-Gewicht λ ist ≥ 0.5" (die
    theoretische Lo-2002-Referenz dominiert die Blend-Gewichtung), NICHT „welche Konstante".

    Rückgabe ``(sr0, floor_dominant, shrinkage_lambda, theoretical_var_source)`` — ``floor_dominant``
    bleibt aus Rückwärtskompat-Gründen erhalten (``λ ≥ 0.5``, äquivalent zu
    ``variance_n_trials <= min_cohort``), aber ``shrinkage_lambda``/``theoretical_var_source`` sind
    die PRÄZISEN Grössen, die Log-Messages/Telemetrie referenzieren MÜSSEN (nie ``floor_dominant``
    als Proxy für „welche Konstante wurde verwendet")."""
    if n_periods is None or n_periods <= 1:
        raise ValueError(
            "sr0_multiple_testing_robust: n_periods muss > 1 sein (Issue #701 — der var_floor-"
            "Fallback ohne n_periods wurde als tot verifiziert und ersatzlos entfernt; ein "
            "fehlendes n_periods an dieser Call-Site ist ein Bug im Aufrufer, kein Legacy-Fall)."
        )
    observed = float(var_sr_trials) if var_sr_trials is not None else 0.0
    theoretical_var = lo2002_sharpe_variance(sr_estimate, n_periods)
    theoretical_var_source = "lo2002"
    reliability_n = variance_n_trials if variance_n_trials is not None else n_trials
    weight = _cohort_shrinkage_weight(reliability_n, min_cohort)
    effective_var = weight * theoretical_var + (1.0 - weight) * observed
    floor_dominant = weight >= 0.5
    return (sr0_multiple_testing(effective_var, n_trials), floor_dominant, weight,
            theoretical_var_source)


def deflated_sharpe_ratio(sr, n_periods, *, sr0: float,
                          skew: float = 0.0, kurtosis: float = 3.0):
    """Issue #618/#651 — vollständige Deflated Sharpe/Sortino Ratio: die PSR relativ zur Multiple-
    Testing-Schwelle ``SR₀`` (statt zu 0). ``DSR = Φ[(ŜR − SR₀)·√(T−1)/√(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²)]``.

    Issue #651 — ``sr0`` MUSS vom Aufrufer bereits berechnet übergeben werden (typischerweise via
    ``sr0_multiple_testing`` oder, unterhalb der Mindestkohorte, ``sr0_multiple_testing_robust``),
    NICHT mehr intern aus ``var_sr_trials``/``n_trials`` rekonstruiert. Vor #651 berechnete diese
    Funktion SR₀ INTERN und UNGEFLOORT (``sr0_multiple_testing``, ohne den #636-Small-Cohort-Floor),
    während die parallel geloggte Telemetrie (``deflated_sr0``, ``deflation_dsr_z`` via ``psr_z``)
    das GEFLOORTE SR₀ nutzte — die promotion-ENTSCHEIDENDE DSR und die telemetrierte DSR-Diagnose
    divergierten dadurch bei Small Cohorts (Faktor bis ~3.5×, siehe #651-Referenzfall Hourly N=9).
    Ein einzelnes, vom Aufrufer EINMAL berechnetes ``sr0`` ist die einzige robuste Garantie, dass
    Entscheidung (``deflated_dsr``) und Telemetrie (``deflation_dsr_z``, ``deflated_sr0``) IMMER
    bit-identisch dasselbe SR₀ konsumieren.

    Alle Grössen PER-PERIODE (nicht annualisiert, #614). ``promote ⟺ DSR ≥ deflation_confidence``.
    Referenz (VwapExhaustion): ``ŜR=0.11386, T=202, SR₀=sr0_multiple_testing(1.803e-3, 100)=0.1075``
    ⇒ ``DSR=0.5364`` ⇒ HOLD (< 0.95). ``None`` bei undefinierter Eingabe."""
    return probabilistic_sharpe_ratio(sr, n_periods, skew=skew, kurtosis=kurtosis, sr_star=sr0)


def deflated_threshold(n_trials: int, dispersion: float, *, confidence: float = 0.95,
                       baseline: float = 0.0) -> float:
    """``confidence``-Quantil des MAXIMUMS von ``n_trials`` i.i.d. N(``baseline``, ``dispersion``²).

    Unter H0 (jede Konfiguration hat den wahren Wert ``baseline`` bei Cross-Trial-Streuung
    ``dispersion``) gilt exakt ``P(max_N > threshold) = 1 − confidence``. Ein Winner MUSS diese
    Schwelle schlagen, damit die False-Positive-Winner-Rate auf das nominelle Niveau ``1 − confidence``
    sinkt (nicht nur die statische Gate-Schwelle).

    Herleitung: Für ``X_i ~ N(μ, σ²)`` i.i.d. hat ``max_i X_i`` die CDF ``Φ((x−μ)/σ)^N``. Das
    ``q``-Quantil ist ``x = μ + σ·Φ⁻¹(q^{1/N})``; für die Winner-Kontrolle ``q = confidence``.

    Degeneriert sauf ``baseline``, wenn keine Streuung (``dispersion <= 0``) oder nur ein Trial
    vorliegt (kein Multiple-Testing).
    """
    if dispersion <= 0.0 or n_trials <= 1:
        return baseline
    q = confidence ** (1.0 / n_trials)
    q = min(max(q, 1e-12), 1.0 - 1e-12)
    return baseline + dispersion * _ND.inv_cdf(q)


def deflated_reward_threshold(rewards, *, confidence: float = 0.95):
    """Issue #592 — Deflations-Schwelle auf der REWARD-Skala (dem tatsächlichen Selektionskriterium
    ``argmax(reward)`` über N Trials), NICHT auf einer geklemmten Teil-Kennzahl (Sortino).

    Zwei Fehler behob #592: (1) der 50.0-Sentinel-Filter war eine hartcodierte Zahl, die seit
    einer Clip-Änderung (RATIO_CAP 50 → 15 → entfernt, #588) ins Leere griff und ±Clip-Artefakte
    ungefiltert in die Dispersion liess; (2) die Dispersion wurde auf der GEKLEMMTEN Sortino-Skala
    geschätzt, die ``deflated_threshold`` (Maximum von N i.i.d. UNBESCHRÄNKTEN Normalen) modelliert.
    Auf der Reward-Skala entfällt beides. ``baseline = median(rewards)`` (die Reward-Skala ist NICHT
    nullzentriert — ``baseline=0.0`` wäre auf einer bei −6.8 zentrierten Skala sinnlos).

    Rückgabe ``(threshold, n, sigma, baseline)``; ``(None, n, None, None)`` bei < 2 Werten
    (kein Multiple-Testing)."""
    import statistics
    vals = [float(r) for r in rewards if r is not None]
    n = len(vals)
    if n < 2:
        return None, n, None, None
    sigma = statistics.pstdev(vals)
    baseline = statistics.median(vals)
    thr = deflated_threshold(n, sigma, confidence=confidence, baseline=baseline)
    return thr, n, sigma, baseline
