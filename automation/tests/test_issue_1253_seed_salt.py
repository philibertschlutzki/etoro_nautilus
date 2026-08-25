"""Issue #1253 (GH #1123) — Seed-Salting für unabhängige Suchvarianz-Stichproben.

Symptom. ``best_eligible_reward`` (Beispiel: 1,7561 bei ComboTrendVwap) ließ sich nie von einer
stabilen Optimumsschätzung gegenüber einer einzelnen TPE-Ziehung unterscheiden — ein
Wiederholungslauf ohne Config-/Seed-Änderung zog IMMER denselben Sampler-Pfad.

Root-Cause. ``seed_effective`` war eine reine Funktion von (``seed``, ``study_name``) — kein
Lauf-Anteil floss ein, ein Wiederholungslauf war damit eine reine Kopie, keine unabhängige
Stichprobe.

Fix.
1. ``run_optimization.seed_effective(seed, study_name, run_salt=None)`` — erweitert um
   ``XOR stable_hash(run_salt)``. ``run_salt=None`` ist bit-identisch zum Pre-#1253-Verhalten.
2. ``--seed-salt``-CLI-Flag / ``OPTIMIZER_SEED_SALT``-Env-Var (``sweep.py``) setzt den Salt
   sweep-weit.
3. ``study.set_user_attr("seed_salt", run_salt)`` je Study, gelesen von
   ``report.compute_run_fingerprint`` (die zehnte Fingerabdruck-Komponente, siehe
   test_issue_1252_run_fingerprint.py) und ``report._compute_search_variance`` (Familien-Gruppierung
   über den saltlosen ``fingerprint_base``).
"""
import argparse

from automation.optimizer.run_optimization import seed_effective


# ---------------------------------------------------------------------------------------------
# seed_effective
# ---------------------------------------------------------------------------------------------

def test_seed_none_returns_none_regardless_of_salt():
    assert seed_effective(None, "study-a") is None
    assert seed_effective(None, "study-a", "some-salt") is None


def test_default_run_salt_matches_pre_1253_signature_call():
    # Ein Aufrufer, der den dritten Positionsparameter nicht kennt, bleibt unveraendert.
    assert seed_effective(42, "study-a") == seed_effective(42, "study-a", None)


def test_empty_string_salt_behaves_like_none():
    # ``run_salt`` ist ein bewusst falsy-gattetes Feld (``if run_salt:``), kein reines
    # ``is not None``-Gate — ein leerer String darf keinen XOR-Beitrag leisten.
    assert seed_effective(42, "study-a", "") == seed_effective(42, "study-a", None)


def test_nonempty_salt_changes_effective_seed():
    unsalted = seed_effective(42, "study-a")
    salted = seed_effective(42, "study-a", "run-salt-1")
    assert unsalted != salted


def test_different_salts_yield_different_effective_seeds():
    a = seed_effective(42, "study-a", "salt-a")
    b = seed_effective(42, "study-a", "salt-b")
    assert a != b


def test_salt_is_deterministic_across_calls():
    a = seed_effective(42, "study-a", "salt-x")
    b = seed_effective(42, "study-a", "salt-x")
    assert a == b


def test_salt_shifts_studies_of_same_run_differently():
    # Derselbe Salt, zwei verschiedene Studies desselben Laufs: die Verschiebung ist
    # studyabhaengig (XOR mit dem bereits vorhandenen stable_hash(study_name)-Term), der Salt
    # allein macht daher NICHT beide Studies auf denselben Effektiv-Seed kollabieren.
    salted_a = seed_effective(42, "study-a", "salt-x")
    salted_b = seed_effective(42, "study-b", "salt-x")
    assert salted_a != salted_b


def test_effective_seed_is_within_numpy_random_state_range():
    for salt in (None, "salt-1", "another-salt"):
        val = seed_effective(2**33 - 1, "study-with-a-fairly-long-name", salt)
        assert 0 <= val <= 2**32 - 1


# ---------------------------------------------------------------------------------------------
# sweep.py --seed-salt CLI-Flag
# ---------------------------------------------------------------------------------------------

def test_sweep_cli_accepts_seed_salt_flag():
    from automation.optimizer import sweep
    parser = argparse.ArgumentParser()
    # sweep.main() baut seinen eigenen Parser intern; wir pruefen hier nur, dass das Modul eine
    # --seed-salt-Option tatsaechlich registriert, ohne main() selbst (Backtest-Abhaengigkeiten)
    # auszufuehren. Reflektiert ueber den Quelltext, da build_parser kein oeffentliches Symbol ist.
    import inspect
    src = inspect.getsource(sweep.main)
    assert "--seed-salt" in src
    assert "OPTIMIZER_SEED_SALT" in src
