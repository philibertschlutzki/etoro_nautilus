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
    """Issue #630/#757 — z-Score der Probabilistic SHARPE Ratio (die unbeschränkte Effektstärke).

    ``z = (ŜR − SR*)·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²)``

    Das unbeschränkte Argument der CDF Φ. Dient als Ranking-Base anstelle der zu
    rasch sättigenden Wahrscheinlichkeit (CDF), da es monoton skaliert und
    nicht an einer 1.0-Decke klemmt.

    Issue #757 — ACHTUNG: der ``γ₃``/``γ₄``-Korrekturterm im Nenner ist die Delta-Methoden-
    Herleitung der asymptotischen Sampling-Varianz des **SHARPE**-Schätzers ``μ̂/σ̂`` (Bailey/
    López de Prado 2012, Lo 2002), mit ``σ̂`` = VOLLSTÄNDIGE Standardabweichung. Für einen
    **Sortino**-Punktschätzer ``μ̂/DD`` (``DD`` = Downside-Deviation) ist dieser Nenner NICHT
    hergeleitet — die Sampling-Verteilung von ``DD`` unterscheidet sich von der von ``σ̂``. ``sr``
    MUSS hier der per-Perioden-**Sharpe** (auf denselben Log-Returns wie der Sortino, #756) sein.
    Für einen Sortino-Punktschätzer den BOOTSTRAP-Standardfehler verwenden (siehe
    ``bootstrap_psr_z``/``bootstrap_psr`` unten) — NICHT diese Formel mit einem Sortino-Wert füttern.

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
    """Issue #614/#757 — Probabilistic SHARPE Ratio (Bailey & López de Prado).

    ``PSR(SR*) = Φ[ (ŜR − SR*)·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²) ]``

    ``ŜR`` = **per-Perioden**-SHARPE-Ratio (``μ̂/σ̂`` — NICHT annualisiert; siehe ``psr_z``-Docstring
    zur Sortino-Einschränkung), ``T`` = Anzahl Perioden (OOS-Bars), ``γ₃`` = Schiefe, ``γ₄`` =
    (NICHT-Exzess-)Kurtosis der Periodenrenditen (Normal ⇒ 3.0). Skalenfrei und in ``[0, 1]``
    beschränkt — die asinh-Sättigung (#559) wird damit überflüssig. **Annualisierungs-invariant**:
    ``ŜR`` ist per-Periode, die Reskalierung mit √A (die Punktschätzer UND Standardfehler gleich
    multipliziert, #614) fällt vollständig heraus.

    Issue #757 — für die tatsächlich im Reward/Eligibility-Pfad verwendete Statistik (per-Perioden-
    SORTINO) den Bootstrap-Standardfehler nutzen: ``bootstrap_psr``/``bootstrap_psr_z``.

    ``None`` bei undefinierter Eingabe (``sr is None``, ``T < 2``) oder nicht-positivem Varianz-Term
    (numerisch degeneriert). Referenz (#614/#618): ``ŜR=0.11386, T=202, γ₃=0, γ₄=3, SR*=0 ⇒ 0.9463``.
    """
    z = psr_z(sr, n_periods, skew=skew, kurtosis=kurtosis, sr_star=sr_star)
    if z is None:
        return None
    return float(_ND.cdf(z))


def psr_from_z(z: float | None) -> float | None:
    """Φ(z) — die CDF fuer einen bereits (z. B. per Bootstrap) berechneten z-Score. Reine
    Convenience, damit Aufrufer wie ``bootstrap_psr_z`` nicht ``statistics.NormalDist`` duplizieren
    muessen. ``None``-durchreichend."""
    if z is None:
        return None
    return float(_ND.cdf(z))


def bootstrap_psr_z(period_returns, *, sr_star: float = 0.0, n_boot: int = 200,
                    block_length: float | None = None, seed: int = 42,
                    mar: float = 0.0) -> tuple[float | None, float | None]:
    """Issue #757 — z-Score der PSR mit einem BOOTSTRAP-Standardfehler DER TATSÄCHLICH VERWENDETEN
    Statistik (per-Perioden-Sortino), statt der ``psr_z``-Sharpe-Sampling-Varianz.

    Root-Cause #757: ``psr_z``/``lo2002_sharpe_variance`` sind die asymptotischen Sampling-Varianzen
    des SHARPE-Schätzers ``μ̂/σ̂`` (Delta-Methode über ``(μ̂, σ̂²)``), aber der Eligibility-/Promotion-
    Pfad übergibt ``sortino_period`` — einen Schätzer ``μ̂/DD`` mit ``DD`` = getrimmtes zweites
    Downside-Moment. Monte-Carlo-Beleg (H0, T=4320): ``P(PSR≥0.75)`` mit dieser Substitution liegt
    bei 31–32 % statt der nominellen 25 % — die effektive Eligibility-/Promotion-Fehlerrate ist damit
    systematisch inflationiert.

    Fix: ``z = (ŜR_sortino − SR*) / SE_boot``, wobei ``SE_boot = pstdev(sortino_statistic(r*_b))``
    über ``n_boot`` Stationary-Bootstrap-Resamples (Politis/Romano, dieselbe Infrastruktur wie der
    Holdout-Bootstrap-CI, #619) der übergebenen Perioden-Returns. Das ist der korrekte Standardfehler
    FÜR DIE TATSÄCHLICH VERWENDETE Statistik, berücksichtigt zusätzlich Serienabhängigkeit und
    Schiefe der (Log-)Returns (#756) OHNE separate γ₃/γ₄-Korrektur, und beseitigt zugleich den
    Inferenz-Doppelstandard zwischen Eligibility (vorher: i.i.d.-√T) und Holdout-Promotion (bereits
    Stationary Bootstrap, #758).

    Rückgabe ``(z, se_boot)`` — beide ``None`` bei < 2 Renditen, nicht-finitem Punktschätzer oder
    degenerierter (``<= 0``) Bootstrap-Streuung. Deterministisch bei festem ``seed``."""
    from automation.optimizer.bootstrap import (
        optimal_block_length, stationary_bootstrap_indices, sortino_statistic)
    import numpy as _np
    a = _np.asarray(list(period_returns), dtype=float)
    n = a.size
    if n < 2:
        return None, None
    point = sortino_statistic(a, mar=mar, annualization=1.0)
    if point != point or not math.isfinite(point):  # NaN/inf
        return None, None
    bl = block_length if block_length is not None else optimal_block_length(a)
    rng = _np.random.default_rng(seed)
    stats = _np.empty(int(n_boot), dtype=float)
    for b in range(int(n_boot)):
        resample = a[stationary_bootstrap_indices(n, bl, rng)]
        stats[b] = sortino_statistic(resample, mar=mar, annualization=1.0)
    stats = stats[_np.isfinite(stats)]
    if stats.size < 2:
        return None, None
    se = float(stats.std(ddof=0))
    if not (se > 0.0):
        return None, None
    return (point - float(sr_star)) / se, se


def bootstrap_psr(period_returns, *, sr_star: float = 0.0, n_boot: int = 200,
                  block_length: float | None = None, seed: int = 42,
                  mar: float = 0.0) -> tuple[float | None, float | None]:
    """Issue #757 — Probabilistic Sortino Ratio mit Bootstrap-Standardfehler (siehe
    ``bootstrap_psr_z``-Docstring für die Herleitung/Root-Cause). Rückgabe ``(psr, se_boot)``."""
    z, se = bootstrap_psr_z(period_returns, sr_star=sr_star, n_boot=n_boot,
                            block_length=block_length, seed=seed, mar=mar)
    return psr_from_z(z), se


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
    """Issue #653/#757 — Lo (2002): asymptotische Stichprobenvarianz eines geschätzten SHARPE-
    Ratios (``μ̂/σ̂``, ``σ̂`` = VOLLSTÄNDIGE Standardabweichung) unter i.i.d.-normalverteilten
    Perioden-Renditen: ``Var[ŜR] ≈ (1 + ŜR²/2) / T``.

    Issue #757 — NICHT für einen Sortino-Punktschätzer (``μ̂/DD``) verwenden: die Sampling-Verteilung
    der Downside-Deviation ``DD`` unterscheidet sich von der von ``σ̂``, diese Formel ist dafür nicht
    hergeleitet. Für den in diesem Codebase tatsächlich verwendeten Sortino den Bootstrap-Standard-
    fehler nutzen (``bootstrap_psr_z``). Diese Funktion bleibt als FORMELKONFORME Referenz für einen
    ECHTEN Sharpe-Schätzer erhalten (``sr0_multiple_testing_robust``s Legacy-Blend-Fallback, bevor
    genug Kohorten-Beobachtungen vorliegen — siehe dortiger Docstring).

    ``T = n_periods`` (OOS-Perioden je Schätzer), ``ŜR`` = per-Perioden-SHARPE-Ratio. Rein
    deterministisch (kein I/O). ``sr=0.0`` (der in ``sr0_multiple_testing_robust`` verwendete
    Default) ist die Varianz UNTER DER NULLHYPOTHESE „kein Edge" — die konservativste, parameterfreie
    Referenz (keine Annahme über den wahren SR-Level nötig, konsistent mit dem Multiple-Testing-
    Null-Modell selbst). Skaliert erwartungsgemäss invers mit T: kürzeres OOS-Fenster (kleineres T)
    ⇒ höhere Schätz-Unsicherheit ⇒ grössere Varianz ⇒ konservativerer Floor."""
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
    search_space_penalty: float | None = None,
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

    Issue #814 — ``search_space_penalty`` (Default ``None`` = aus, ``tournament.json['deflation_
    search_space_penalty']``) ist der EXPLIZITE, dokumentierte Ersatz für die vorherige stille
    Aufblähung von ``n_trials`` auf das geplante Budget (``deflation_family_floor_mode='budgeted'``,
    seit #814 nicht mehr Default). Root-Cause #814: ein Trial, der NIE gezogen wurde, hat keinen
    Sharpe-Schätzer und kann das ``max()`` unter H0 nicht beeinflusst haben — ihn über ``n_trials``
    mitzuzählen ist eine Fehlspezifikation der Nullverteilung, keine konservative Wahl. Der
    „Suchraum-Kapazität"-Gedanke (den ``'budgeted'`` eigentlich ausdrücken wollte) bleibt als
    SEPARATER, additiver Term auf ``SR₀`` verfügbar, falls ein Operator ihn explizit will — er
    verzerrt dann NICHT mehr ``E[max_N]`` selbst. Wird auf das nach der Shrinkage berechnete ``SR₀``
    addiert (``sr0_final = sr0_multiple_testing(effective_var, n_trials) + search_space_penalty``);
    ``None``/``0`` ⇒ bit-identisch zum reinen Multiplizitäts-``SR₀`` (kein Term).

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
    sr0 = sr0_multiple_testing(effective_var, n_trials)
    if search_space_penalty:
        sr0 += float(search_space_penalty)
    return (sr0, floor_dominant, weight, theoretical_var_source)


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


# Issue #866 (P0, Katalog #866-#869, GitHub-Issue #760, Pitfall #278) — gültige
# ``promotion_route``-Werte für ``resolve_promotion_multiplicity``.
_VALID_PROMOTION_MULTIPLICITY_ROUTES = frozenset({"per_symbol_tuned", "global_default"})


def resolve_promotion_multiplicity(route: str, *, deflation_n_family: int | None = None,
                                    n_global_default_candidates: int | None = None) -> int:
    """Issue #866 — eine Multiple-Testing-Korrektur gehört zur SELEKTION, nicht zur Study
    (AGENTS.md Pitfall #278). Root-Cause: die globale Default-Route (``PROMOTE_GLOBAL_DEFAULT``,
    #682/#783) wurde bislang mit ``deflation_n_family`` — der Trial-Zahl DER STUDY — deflationiert,
    obwohl der globale Default-Vektor an DIESER Selektion (dem Trial-Sampling der Study) nicht
    teilgenommen hat: er ist ein EINMALIG geprüfter, ungetunter Notfallkandidat (die Route existiert
    seit #682 explizit für den Fall, dass KEIN symbol-eligibler Trial gefunden wurde). Ein DSR-Test
    mit ``N=159`` gegen einen Kandidaten, der nie 159 Mal gesamplet wurde, ist strukturell
    unerreichbar (``E[max₁]=0`` gegen ``E[max₁₅₉]≈2.8σ``), unabhängig von seiner tatsächlichen
    Qualität.

    Diese Funktion bindet die Multiplizität explizit an ``promotion_route`` statt an die Study:

    - ``route='per_symbol_tuned'`` ⇒ ``N = deflation_n_family`` (unverändert, #826-Stufe-1: die
      familienweite Multiplizität DIESER Study).
    - ``route='global_default'`` ⇒ ``N = n_global_default_candidates`` — die Anzahl der Strategien,
      deren globaler Default für dieses Symbol als Notfallkandidat in Frage kam (der Roster-Umfang
      des Symbols), NICHT die Trial-Zahl irgendeiner Study.

    Ein unbekannter ``route``-Wert bricht fail-loud ab (kein stiller Fallback auf eine falsch
    geschriebene Route-Zeichenkette — dieselbe Disziplin wie ``promotion_correction_mode``/
    ``deflation_heterogeneity_policy``)."""
    if route not in _VALID_PROMOTION_MULTIPLICITY_ROUTES:
        raise ValueError(
            f"resolve_promotion_multiplicity: unbekannte promotion_route {route!r}, erwartet eines "
            f"von {sorted(_VALID_PROMOTION_MULTIPLICITY_ROUTES)}."
        )
    if route == "per_symbol_tuned":
        return int(deflation_n_family or 0)
    return int(n_global_default_candidates or 0)


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
