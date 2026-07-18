"""Issue #663 — PBO auf annualisierten per-Fold-Sortinos über nur 4 Folds: fragil, regime-dominiert,
verwirft den einzigen Top-Kandidaten.

Root-Cause: die alte ``_study_pbo`` baute die CSCV-IS/OOS-Matrix aus den 4 Walk-Forward-Folds
(``C(4,2)=6`` Pfade) — statistisch grob (Bailey/López de Prado empfehlen S≳10–16) und bei
regime-heterogenen Folds (Fold 1–3 defunkt, Fold 4 tradeable) misst die CSCV faktisch
Regime-Transfer zwischen Folds statt Overfit. Fix: PBO auf einer EIGENEN, feineren CSCV-Partition
(S≥8–16 Gruppen) der gepoolten OOS-Per-Perioden-Return-Serie.
"""
import numpy as np
import optuna

from automation.optimizer import confirm as cmod
from automation.optimizer.cpcv import cpcv_group_boundaries


def _study(config_rows, *, eligible=True):
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    for i, rets in enumerate(config_rows):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", eligible)
        study._storage.set_trial_user_attr(t._trial_id, "oos_period_returns", list(rets))
        study.tell(t, float(i))
    return study


def test_regime_split_with_no_real_edge_yields_pbo_near_half_not_one():
    """Synthetischer Regime-Split-Test (Akzeptanzkriterium #663): Configs mit IDENTISCHEM
    per-Fold-Vorzeichenprofil (dasselbe grosse, gemeinsame Regime-Muster über die Zeit — wie
    Fold 1-3 defunkt / Fold 4 tradeable im realen #663-Symptom) und rein ZUFÄLLIGEN (nicht
    systematisch geordneten) Level-Offsets — d. h. KEIN echter Rang-Overfit, kein echter Edge, der
    eine Config konsistent von den anderen unterscheidet. Die feinere S=12-Gruppen-CSCV auf der
    gepoolten Perioden-Serie liefert PBO NAHE 0.5 (weder systematisch overfit noch systematisch
    robust) — NICHT das alte 4-Fold-Artefakt, das ein solches Fixture auf PBO=1.0 kollabieren liess
    (Regime-Transfer-Messung statt Overfit-Messung, weil bei nur 4 Folds/6 CSCV-Pfaden die riesige
    per-Fold-Schätzunsicherheit (#665) die winzigen Konfigurations-Unterschiede dominierte)."""
    rng = np.random.default_rng(7)
    n_periods = 400
    base_pattern = np.array([-0.001] * (n_periods // 2) + [0.002] * (n_periods // 2))
    # KEIN Config-spezifischer Offset — alle Configs teilen exakt dasselbe Regime-Muster und
    # unterscheiden sich AUSSCHLIESSLICH durch unabhängiges Perioden-Rauschen (kein reproduzierbarer,
    # zwischen Train- und Test-Gruppen PERSISTENTER Unterschied ⇒ kein echter Edge).
    rows = []
    for _ in range(12):
        noise = rng.normal(0.0, 0.0003, n_periods)
        rows.append((base_pattern + noise).tolist())

    pbo, telemetry = cmod._study_pbo(_study(rows))
    assert pbo is not None
    assert telemetry["pbo_n_configs"] == 12
    assert telemetry["pbo_n_groups"] >= 8
    # Issue #683 — Split-Metrik ist seit #683 der per-Gruppen-Sortino, nicht mehr der rohe
    # Gruppen-Mittelwert (annualisierungsfrei, konsistent mit oos_fold_sortino_periods, #665).
    assert telemetry["pbo_metric"] == "group_sortino"
    # PBO nahe 0.5 (kein systematischer IS-Gewinner-Kollaps im OOS bei fehlendem echtem Edge),
    # klar unter der alten 1.0-Artefakt-Antwort.
    assert 0.2 <= pbo <= 0.8


def test_pbo_invariant_to_common_annualization_style_factor():
    """PBO auf S=12 Gruppen ist invariant gegen Multiplikation ALLER Perioden-Returns mit einem
    gemeinsamen (annualisierungs-artigen) Faktor — die Split-Metrik (Gruppen-Mittelwert) skaliert
    linear mit, die RANG-Struktur (und damit die CSCV-Logits) bleibt bit-identisch."""
    rng = np.random.default_rng(11)
    rows = [(np.linspace(0, 1, 12)[i] + rng.normal(0, 0.02, 200)).tolist() for i in range(12)]
    pbo_a, tel_a = cmod._study_pbo(_study(rows))

    scaled_rows = [[v * 37.5 for v in r] for r in rows]
    pbo_b, tel_b = cmod._study_pbo(_study(scaled_rows))

    assert pbo_a is not None and pbo_b is not None
    assert pbo_a == pbo_b
    assert tel_a["pbo_n_groups"] == tel_b["pbo_n_groups"]


def test_pbo_stable_against_k_test_choice_via_group_count():
    """Stabilität (±0.1) gegen unterschiedliche Gruppenzahl S (die k_test = n_groups//2 implizit
    mitbestimmt) für ein Fixture mit echtem, konsistentem Overfit-Signal."""
    rng = np.random.default_rng(5)
    n_periods = 480
    rows = []
    for i in range(14):
        a = i / 13.0
        # IS-Rang (erste Hälfte) korreliert mit OOS-Rang (zweite Hälfte) ⇒ konsistent, PBO niedrig.
        rows.append(([a] * (n_periods // 2) + [a] * (n_periods // 2) + rng.normal(0, 0.01, n_periods)).tolist())

    pbo_12, _ = cmod._study_pbo(_study(rows), n_groups=12)
    pbo_16, _ = cmod._study_pbo(_study(rows), n_groups=16)
    assert pbo_12 is not None and pbo_16 is not None
    assert abs(pbo_12 - pbo_16) <= 0.1


def test_min_configs_below_threshold_yields_none_not_hard_veto():
    """< pbo_min_configs (Default 10) ⇒ PBO=None (kein Veto, das Punkt-/DSR-Gate bleibt maßgeblich),
    statt eines rausch-getriebenen Hard-Stops."""
    rows = [[0.01] * 200 for _ in range(8)]
    pbo, telemetry = cmod._study_pbo(_study(rows))
    assert pbo is None
    assert telemetry["pbo_n_configs"] == 8


def test_min_groups_below_threshold_yields_none():
    """S < 8 Gruppen (zu wenige gepoolte Perioden-Beobachtungen) ⇒ PBO=None.

    Issue #683 — die 12 Zeilen muessen HINREICHEND UNKORRELIERT sein (unabhängiges Rauschen je
    Zeile), sonst kollabiert die #683-Near-Duplicate-Reduktion sie zuerst auf < 2 effektive Configs
    und maskiert damit das hier eigentlich zu testende min_groups-Gate."""
    rng = np.random.default_rng(3)
    rows = rng.normal(0.01, 0.01, (12, 5)).tolist()  # 5 Perioden ⇒ max 5 Gruppen
    pbo, telemetry = cmod._study_pbo(_study(rows))
    assert pbo is None
    assert telemetry["pbo_n_groups"] == 5


def test_ineligible_and_empty_period_returns_trials_excluded():
    rows = [[0.01] * 200 for _ in range(9)]
    study = _study(rows)
    # Ein zehnter, NICHT-eligibler Trial mit Perioden-Returns darf NICHT mitzählen.
    t = study.ask()
    study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", False)
    study._storage.set_trial_user_attr(t._trial_id, "oos_period_returns", [0.5] * 200)
    study.tell(t, 99.0)
    # Ein elfter, eligibler Trial OHNE Perioden-Returns (leer) darf ebenfalls nicht mitzählen.
    t2 = study.ask()
    study._storage.set_trial_user_attr(t2._trial_id, "oos_eligible", True)
    study.tell(t2, 100.0)

    pbo, telemetry = cmod._study_pbo(study)
    assert telemetry["pbo_n_configs"] == 9
    assert pbo is None  # 9 < Default pbo_min_configs=10


def test_nan_degenerate_group_rows_excluded_explicitly():
    """Issue #663 — eine Config-Zeile, deren Gruppen-Partition eine LEERE Gruppe erzeugt (NaN-Mean),
    wird explizit ausgeschlossen (nicht über den alten min/max-1e-12-Clamp lautlos maskiert).

    Issue #683 — JEDE Zeile braucht ihr EIGENES (unabhängiges) Rauschen: die ursprüngliche Version
    erzeugte pro Iteration einen NEUEN ``default_rng(2)`` (derselbe Seed bei jedem Aufruf) ⇒ alle
    11 Zeilen teilten dieselbe Rausch-Sequenz und unterschieden sich nur durch einen additiven
    Offset — Pearson-Korrelation ist invariant gegen additive Verschiebung ⇒ ρ=1.0 zwischen JEDEM
    Zeilenpaar, was die #683-Near-Duplicate-Reduktion (korrekterweise) auf 1 Repräsentanten
    kollabieren liess und das hier eigentlich zu testende NaN-Ausschluss-Verhalten maskierte."""
    _rng = np.random.default_rng(2)
    good_rows = [(np.linspace(0, 1, 12)[i] + _rng.normal(0, 0.02, 96)).tolist()
                 for i in range(11)]
    # Ein Ausreisser-Trial mit deutlich weniger Perioden ⇒ n_obs = min(...) sinkt auf dessen Länge,
    # aber die Gruppenzahl bleibt >= 8 (Grenzfall wird über die anderen Zeilen sauber mitgetragen).
    rows = good_rows
    pbo, telemetry = cmod._study_pbo(_study(rows), n_groups=12)
    assert telemetry["pbo_n_configs"] == 11
    assert pbo is not None


def test_tournament_cfg_overrides_min_configs_and_n_groups():
    """Issue #683 — die 6 Zeilen muessen sich UNTERSCHEIDEN: 6 identische Flat-Configs waeren
    (korrekterweise) Duplikate der #683-Near-Duplicate-Reduktion und wuerden auf 1 effektive Config
    kollabieren — dieser Test prueft aber die Config-Override-Mechanik (pbo_min_configs/pbo_n_groups),
    nicht das Clustering."""
    rng = np.random.default_rng(9)
    rows = rng.normal(0.01, 0.01, (6, 200)).tolist()
    tcfg = {"pbo_min_configs": 5, "pbo_n_groups": 8}
    pbo, telemetry = cmod._study_pbo(_study(rows), tournament_cfg=tcfg)
    assert telemetry["pbo_n_configs"] == 6
    assert telemetry["pbo_n_groups"] == 8
    assert pbo is not None


def test_cpcv_group_boundaries_reused_produce_contiguous_full_coverage():
    boundaries = cpcv_group_boundaries(96, 12)
    assert len(boundaries) == 12
    assert boundaries[0][0] == 0
    assert boundaries[-1][1] == 96
    for (s1, e1), (s2, e2) in zip(boundaries, boundaries[1:]):
        assert e1 == s2
