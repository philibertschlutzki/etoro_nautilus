"""Issue #956/#1122 (Katalog #960) — ``gate_inventory.n_rejections`` bleibt invertiert.

Symptom (B-13): ``AdxAtrMomentumStrategy/NVDA.ETORO``: ``gate_inventory[oos_min_psr].n_rejections
== 0`` bei ``is_rejection_detail_counts["REJECT_OOS_MIN_PSR"] == 140`` — das Gate, das JEDEN
einzelnen Trial verwarf, wurde als beitragslos geführt. ``check_gate_inventory_coherence``
(severity ``high``) meldete 18 bzw. 28 betroffene Studies.

Root-Cause: der Inventur-Zähler (``oos_gate_deltas[gate] > 0``, MEHRLABEL) und der Detail-Zähler
(``is_rejection_detail_counts``, EINLABEL, die PRIMÄRE Ursache) zielen auf verschiedene
Grundgesamtheiten (#1076) — ``oos_gate_deltas`` trägt den Key für ein Gate wie ``oos_min_psr``
NUR, wenn die zugrundeliegende Statistik selbst DEFINIERT ist (siehe
``reward._normalized_gate_distances``-Docstring) — ein struktureller blinder Fleck für genau die
Fälle, in denen die Statistik am häufigsten fehlschlägt/undefiniert bleibt.

Fix: ``gate_inventory`` wird aus ``is_rejection_detail_counts`` abgeleitet statt parallel
gepflegt. ``check_gate_inventory_coherence`` wird dadurch zur Tautologie und entfällt (siehe
test_issue_1076_gate_inventory_coherence.py).

Akzeptanzkriterium: für jede Study und jedes Gate gilt
``gate_inventory[g].n_rejections == is_rejection_detail_counts[code(g)]``.
"""
from automation.optimizer import invariants as inv

_GATES = ("oos_min_psr", "oos_max_drawdown", "oos_min_trades")


def _trial(deltas, evaluated=True):
    return {"oos_evaluated": evaluated, "oos_gate_deltas": deltas}


def test_b13_reference_scenario_n_rejections_matches_the_detail_bucket_not_zero():
    """Reproduziert B-13: oos_gate_deltas traegt 'oos_min_psr' fuer KEINEN der 140 Trials (die
    Statistik war fuer sie undefiniert) -- die alte, delta-basierte Zaehlung ergaebe 0. Mit
    is_rejection_detail_counts uebergeben, muss n_rejections den wahren Wert (140) tragen."""
    trials = [_trial({"oos_max_drawdown": -0.1, "oos_min_trades": -5}) for _ in range(140)]
    table = inv.gate_inventory_table(
        trials, list(_GATES),
        is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 140, "NONE": 0})
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert psr_row["n_rejections"] == 140


def test_acceptance_criterion_n_rejections_equals_detail_count_for_every_gate():
    detail_counts = {
        "REJECT_OOS_MIN_PSR": 98, "REJECT_OOS_MAX_DRAWDOWN": 12, "REJECT_OOS_MIN_TRADES": 3,
    }
    trials = [_trial({g: 0.1 for g in _GATES}) for _ in range(200)]
    table = inv.gate_inventory_table(trials, list(_GATES), is_rejection_detail_counts=detail_counts)
    for row in table:
        code = f"REJECT_OOS_{row['gate'][4:].upper()}"
        assert row["n_rejections"] == detail_counts[code]


def test_gate_without_a_prefix_normalizes_correctly():
    # Issue #1003/#1155 — 7 lokal ebenfalls verletzende Trials (statt 1), damit die neue
    # Ordnungs-Invariante (n_rejections <= n_evaluated) bei der injizierten Zahl 7 nicht
    # verletzt wird; der eigentliche Test-Gegenstand (Praefix-Normalisierung) bleibt unveraendert.
    table = inv.gate_inventory_table(
        [_trial({"min_trades": 0.1}) for _ in range(7)], ["min_trades"],
        is_rejection_detail_counts={"REJECT_OOS_MIN_TRADES": 7})
    assert table[0]["n_rejections"] == 7


def test_missing_detail_bucket_yields_zero_not_a_crash():
    # Issue #1003/#1155 — der Trial verletzt ``oos_min_psr`` lokal NICHT (negatives Delta), damit
    # der delta-basierte n_solo-Rueckfall (kein oos_rejection_reasons-Feld in dieser Fixture) mit
    # dem injizierten n_rejections=0 konsistent bleibt (0 <= 0 <= 0 <= 1).
    table = inv.gate_inventory_table(
        [_trial({"oos_min_psr": -0.1})], ["oos_min_psr"],
        is_rejection_detail_counts={})
    assert table[0]["n_rejections"] == 0


def test_solo_rejections_and_marginal_delta_remain_multi_label_regardless_of_the_new_source():
    """n_solo_rejections/marginal_delta beantworten eine Mehrlabel-Frage (welches Gate war
    AUSSCHLIESSLICH verletzt / wie viele Trials waeren OHNE dieses Gate zusaetzlich eligible) --
    sie duerfen sich NICHT aendern, nur weil is_rejection_detail_counts uebergeben wird."""
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5}),
        _trial({"oos_min_psr": -0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5}),
    ]
    without = inv.gate_inventory_table(trials, list(_GATES))
    # Issue #1003/#1155 — die injizierte Zahl muss <= n_evaluated (2) bleiben (neue Ordnungs-
    # Invariante), reicht aber weiterhin, um von der lokal delta-basierten Zaehlung (1) zu
    # divergieren — der eigentliche Test-Gegenstand (Divergenz moeglich, solo/marginal bleiben
    # lokal) ist unveraendert.
    with_detail = inv.gate_inventory_table(
        trials, list(_GATES), is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 2})
    psr_without = next(r for r in without if r["gate"] == "oos_min_psr")
    psr_with = next(r for r in with_detail if r["gate"] == "oos_min_psr")
    assert psr_without["n_solo_rejections"] == psr_with["n_solo_rejections"]
    assert psr_without["marginal_delta"] == psr_with["marginal_delta"]
    # n_rejections selbst ist die einzige Groesse, die divergiert.
    assert psr_with["n_rejections"] == 2
    assert psr_without["n_rejections"] != 2


def test_legacy_callers_without_the_new_argument_keep_the_old_behaviour():
    """Rueckwaertskompatibilitaet: is_rejection_detail_counts=None (Default) faellt auf die alte,
    delta-basierte Zaehlung zurueck -- bestehende Aufrufer/Fixtures bleiben unveraendert."""
    trials = [_trial({"oos_min_psr": 0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5})]
    table = inv.gate_inventory_table(trials, list(_GATES))
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert psr_row["n_rejections"] == 1


def test_check_gate_inventory_coherence_is_removed():
    assert not hasattr(inv, "check_gate_inventory_coherence")
