"""Issue #1049/#1197 (P0, Katalog #1196-1221) — ``deflation_n_family_raw`` skaliert mit der Zahl der
Sweep-Läufe.

Symptom: in allen sieben warm-gestarteten Läufen gilt ``deflation_n_family_raw = k ·
n_statistic_available`` mit k = Anzahl der Läufe im gemeinsamen Store (35/35 Datenpunkte exakt) —
die DSR-Schwelle steigt dadurch von k=1 auf k=6 um 18,3 %, obwohl die Zahl der tatsächlich
verwertbaren Trials konstant bleibt.

Root-Cause: ``sweep._family_n_per_study_from_studies``/``_family_period_returns_from_studies``
zählten über ``study.trials`` — STORE-SCOPED, die GESAMTE Study-Historie über ALLE ``run_id``s, die
je auf diesem Store liefen (Optuna ``load_if_exists``-Warm-Start) — statt über die Trials, die
DIESER Lauf tatsächlich gezogen hat.

Fix: ein neuer, optionaler ``run_id``-Parameter filtert die Trial-Menge VOR der Zählung auf
``trial.user_attrs['run_id'] == run_id`` — dieselbe Konvention wie ``report._build_report``s
Kohorten-Filter (#1086/#1021). ``run_id=None`` (Default) bleibt bit-identisch zum Vorher-Verhalten
(rückwärtskompatibel für Legacy-Aufrufer/-Tests).

Akzeptanzkriterium (wörtlich aus #1197): eine synthetische Study mit 100 Trials, DREIMAL geladen
(simuliert: 3 × 100 Trials mit je eigener run_id im selben Store) ⇒ die run_id-gescopte Zählung
bleibt 100 für JEDEN einzelnen Lauf, während die Legacy-(ungescopte) Zählung weiterhin 300
liefert — genau der Unterschied, den confirm.py jetzt über ``run_id`` auflösen kann."""
from automation.optimizer import sweep


class _T:
    def __init__(self, evaluated=True, run_id=None, rets=None):
        attrs = {"oos_evaluated": evaluated, "oos_eligible": evaluated,
                "oos_selection_statistic_available": evaluated}
        if run_id is not None:
            attrs["run_id"] = run_id
        if rets is not None:
            attrs["oos_period_returns"] = rets
        self.user_attrs = attrs


class _Study:
    def __init__(self, trials, n_trials_budget=None):
        self.trials = trials
        self.user_attrs = {} if n_trials_budget is None else {"n_trials_budget": n_trials_budget}


def _three_warm_started_loads_of_100_trials():
    """Simuliert exakt das #1197-Akzeptanzkriterium: dieselbe Study, dreimal geladen (Optuna
    load_if_exists) -- 300 Trials im Store, aber nur je 100 gehoeren zu run_1/run_2/run_3."""
    trials = (
        [_T(run_id="run_1") for _ in range(100)]
        + [_T(run_id="run_2") for _ in range(100)]
        + [_T(run_id="run_3") for _ in range(100)]
    )
    return [_Study(trials)]


def test_run_scoped_count_stays_100_regardless_of_which_run_is_selected():
    studies = _three_warm_started_loads_of_100_trials()
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    for run_id in ("run_1", "run_2", "run_3"):
        family_n = sweep._family_n_from_studies(
            pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"},
            run_id=run_id)
        assert family_n["TSLA.ETORO"] == 100, (
            f"run_id={run_id!r} sollte NUR seine eigenen 100 Trials zaehlen, nicht die "
            f"kumulierten 300 des Stores (family_n={family_n})"
        )


def test_unscoped_legacy_call_still_reproduces_the_k_times_multiplication_bug_signature():
    """Regressionsschutz der RUeCKWAERTSKOMPATIBILITAeT: ohne run_id (Legacy-Aufrufer) bleibt das
    Verhalten UNVERAeNDERT store-scoped (300) -- die Korrektur ist ausschliesslich additiv/opt-in,
    kein impliziter Verhaltenswechsel fuer Aufrufer, die run_id nicht kennen."""
    studies = _three_warm_started_loads_of_100_trials()
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert family_n["TSLA.ETORO"] == 300


def test_family_n_stage1_is_also_run_scoped():
    studies = _three_warm_started_loads_of_100_trials()
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    stage1 = sweep._family_n_stage1_from_studies(pairs, studies, run_id="run_2")
    assert stage1[("StratA", "TSLA.ETORO")] == 100


def test_family_period_returns_matches_the_same_run_scoped_row_count():
    """Issue #1049/#1197 — die Decluster-Matrix (confirm.py's cluster_effective_configs-Eingang)
    MUSS dieselbe Trial-Menge sehen wie der Zaehler, sonst declustert confirm.py Configs, die nie
    Kandidat fuer DIESEN Lauf waren (derselbe Konsistenz-Kommentar wie #905 im Code)."""
    rets = [1.0, 2.0, 3.0]
    trials = (
        [_T(run_id="run_1", rets=rets) for _ in range(5)]
        + [_T(run_id="run_2", rets=rets) for _ in range(7)]
    )
    studies = [_Study(trials)]
    pairs = [("StratA", "TSLA.ETORO", "OK")]

    returns_run1 = sweep._family_period_returns_from_studies(pairs, studies, run_id="run_1")
    returns_run2 = sweep._family_period_returns_from_studies(pairs, studies, run_id="run_2")
    family_n_run1 = sweep._family_n_from_studies(pairs, studies, run_id="run_1")
    family_n_run2 = sweep._family_n_from_studies(pairs, studies, run_id="run_2")

    assert len(returns_run1["TSLA.ETORO"]) == family_n_run1["TSLA.ETORO"] == 5
    assert len(returns_run2["TSLA.ETORO"]) == family_n_run2["TSLA.ETORO"] == 7
