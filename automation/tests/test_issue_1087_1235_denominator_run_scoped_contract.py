"""Issue #1087/#1235 (P2, Katalog #1247+) — Zählernamen in den Invarianten an den run-scoped
Vertrag binden.

Symptom. Die Fehlermeldung von ``check_denominator_coherence`` nennt „n_trials_total (403)" — einen
Wert, der im selben Record neben ``n_trials_total_study = 140`` steht. Für einen Leser ist nicht
erkennbar, dass zwei verschiedene Grundgesamtheiten verglichen werden.

Root-Cause. Die Invariante las ``n_trials_total`` (bis #1234 store-scoped) statt
``n_trials_total_study``. Nach #1234 sind beide identisch — der Vertrag war aber nicht abgesichert.

Fix. ``check_denominator_coherence`` bevorzugt seither ``n_trials_total_study`` (Fallback auf das
gleichwertige bare ``n_trials_total`` für ältere Aufrufer) und meldet beim Vorhandensein von
``n_trials_total_store`` mit abweichendem Wert, der die Zerlegung erklärt, die eigene, benannte
Diagnose ``STORE_SCOPED_COUNTER_PRESENT`` statt einer irreführenden Partitionsverletzung.
"""
from automation.optimizer import invariants as inv


def _counts(**kw):
    base = {"n_trials_informative": 100, "n_trials_pruned": 20,
            "n_trials_unevaluable": 15, "n_trials_failed": 5}
    base.update(kw)
    return base


# --- n_trials_total_study bevorzugt, bare n_trials_total als Fallback --------------------------

def test_prefers_the_explicit_run_scoped_field_name():
    result = inv.check_denominator_coherence(
        _counts(n_trials_total_study=140, n_trials_total=403))  # store-scoped bare Wert ignoriert
    assert result.passed is True
    assert result.actual["n_trials_total_study"] == 140
    assert "n_trials_total" not in result.actual or result.actual.get("n_trials_total") != 403


def test_falls_back_to_the_bare_field_when_the_explicit_name_is_absent():
    """Rückwärtskompatibilität: ein Report vor dieser Umbenennung (nur das bare Feld, seit
    #1086/#1234 bereits run-scoped) verhält sich bit-identisch."""
    result = inv.check_denominator_coherence(_counts(n_trials_total=140))
    assert result.passed is True
    assert result.actual["n_trials_total"] == 140


def test_error_message_names_the_actual_field_it_compared_against():
    result = inv.check_denominator_coherence(
        _counts(n_trials_total_study=999))  # Zerlegung (140) != 999
    assert result.passed is False
    assert "n_trials_total_study" in result.detail
    assert "999" in result.detail


# --- STORE_SCOPED_COUNTER_PRESENT: gemischte Zähler --------------------------------------------

def test_mixed_counters_produce_the_store_scoped_diagnosis_not_a_partition_mismatch():
    """Reproduziert das Symptom direkt: die vier Kategorien-Zähler summieren sich auf den
    STORE-skopierten Wert (403), während n_trials_total_study korrekt run-skopiert (140) daneben
    steht -- eine erklärte Skopen-Vermischung, keine echte Partitionsverletzung."""
    result = inv.check_denominator_coherence({
        "n_trials_total_study": 140,
        "n_trials_total_store": 403,
        # Diese vier Zähler sind (versehentlich) STORE-skopiert und summieren sich auf 403.
        "n_trials_informative": 280, "n_trials_pruned": 60,
        "n_trials_unevaluable": 43, "n_trials_failed": 20,
    })
    assert result.passed is True
    assert "STORE_SCOPED_COUNTER_PRESENT" in result.detail
    assert "403" in result.detail
    assert "140" in result.detail
    assert "PARTITION_MISMATCH" not in result.detail


def test_store_scoped_diagnosis_stays_visible_even_though_passed_is_true():
    """Die Diagnose ersetzt den Fehlschlag, darf aber nicht in einem stillen 'OK' verschwinden."""
    result = inv.check_denominator_coherence({
        "n_trials_total_study": 140, "n_trials_total_store": 403,
        "n_trials_informative": 280, "n_trials_pruned": 60,
        "n_trials_unevaluable": 43, "n_trials_failed": 20,
    })
    assert result.detail != "OK"
    assert "STORE_SCOPED_COUNTER_PRESENT" in result.detail


def test_genuine_partition_violation_is_not_masked_by_an_unrelated_store_total():
    """n_trials_total_store ist zwar vorhanden, erklärt die Abweichung aber NICHT (die Zerlegung
    summiert sich weder auf n_trials_total_study noch auf n_trials_total_store) -- muss als echte
    Partitionsverletzung FAILen, nicht als STORE_SCOPED_COUNTER_PRESENT durchgehen."""
    result = inv.check_denominator_coherence({
        "n_trials_total_study": 140, "n_trials_total_store": 403,
        "n_trials_informative": 50, "n_trials_pruned": 10,
        "n_trials_unevaluable": 5, "n_trials_failed": 5,  # Summe 70, weder 140 noch 403
    })
    assert result.passed is False
    assert "STORE_SCOPED_COUNTER_PRESENT" not in result.detail
    assert "Zerlegung" in result.detail


def test_store_total_present_but_equal_to_run_total_is_not_flagged():
    """Kein Warm-Start (store == run): n_trials_total_store gleich n_trials_total_study ist der
    Normalfall (#1086/#1234-Docstring: 'kein neues Verhalten fuer Aufrufer ohne run_id') -- die
    Diagnose greift nur bei einer ECHTEN Abweichung."""
    result = inv.check_denominator_coherence(
        _counts(n_trials_total_study=140, n_trials_total_store=140))
    assert result.passed is True
    assert "STORE_SCOPED_COUNTER_PRESENT" not in (result.detail or "")


def test_second_identity_still_evaluated_after_a_store_scoped_first_identity_pass():
    """n_evaluable (die zweite, #1079-Identitaet) bleibt unabhaengig geprueft, selbst wenn die
    erste Identitaet ueber STORE_SCOPED_COUNTER_PRESENT als nicht-fehlschlagend gilt."""
    result = inv.check_denominator_coherence({
        "n_trials_total_study": 140, "n_trials_total_store": 403,
        "n_trials_informative": 280, "n_trials_pruned": 60,
        "n_trials_unevaluable": 43, "n_trials_failed": 20,
        "n_evaluable": 999,  # weit ab von 140 -> zweite Identitaet muss failen
    })
    assert result.passed is False
    assert "Zweite Identität" in result.detail


def test_no_data_still_not_applicable():
    result = inv.check_denominator_coherence({})
    assert result.passed is True
    assert result.actual is None
