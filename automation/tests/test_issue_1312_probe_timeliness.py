"""Issue #1312 (GH #1189, P2) — Fail-Fast-Probe feuert bei 99,46 % der Wallclock.

Symptom. ``blocking_invariant_probe_triggered_at_wallclock_fraction = 0,9946`` in 5/5 Läufen; das
gesamte Rechenbudget war verbraucht (B-10). #1287 hat den Befund benannt, nicht behoben.

Root-Cause. Die Probe-Schwelle war ausschliesslich in SYMBOLEN formuliert
(``fail_fast_min_symbols``), ``executor.sh`` fährt jedoch genau EIN Symbol je Sweep — die Schwelle
wurde strukturell erst am Ende erreicht.

Scope-Entscheidung (bewusst dokumentiert, wie bereits zweimal zuvor für dieselbe Frage — siehe
``test_issue_1269_fail_fast_study_scope.py`` [GH #1139] und ``invariants.check_fail_fast_probe_
timeliness``-Docstring [#1287/GH #1160]). Die Fix-Vorgabe aus GH #1189 ("Probe-Bedingung auf
abgeschlossene STUDIES umstellen, unabhängig von der Symbol-Zahl") würde — wörtlich implementiert —
die Probe MITTEN in einer noch laufenden Symbol-Optimierungs-Batch auslösen können. Genau das haben
BEIDE Vorläufer-Fixe (GH #1139 Fix Punkt 1, dann erneut #1287/GH #1160) explizit NICHT umgesetzt:
die Familien-Statistik-Maschine (``sweep._run_confirm_and_export``/``deflation_n_family_frozen``)
stempelt ihr Ergebnis EINMALIG und NIE NEU BERECHNET auf die echten Optuna-Study-Objekte, UND wird
erst aufgerufen, NACHDEM saemtliche Strategien-Studies eines Symbols bereits abgeschlossen sind
(``symbol_studies = [_run_optimize(p) for p in symbol_pairs]`` ist eine synchrone Batch-Barriere
ohne Zwischen-Checkpoint). Ein Trigger VOR diesem Punkt haette keine zusaetzlichen, sicher
auswertbaren Proposals zur Verfuegung — dasselbe Korrektheitsrisiko wie in beiden Vorlaeufern.

Implementiert ist stattdessen die SICHERE Teilmenge der Fix-Vorgabe: die Probe feuert zusaetzlich
zur Symbol-Schwelle, sobald GENUEGEND STUDIES KUMULATIV ueber bereits VOLLSTAENDIG abgeschlossene
(confirm'te) Symbole exportiert wurden (``fail_fast_min_completed_studies_frac``, Default 0.2 —
bislang dokumentiert, aber nicht verdrahtet, siehe GH #1287) — je nachdem, welche der beiden
Schwellen (Symbole ODER kumulative Studies) zuerst erreicht wird. Das verbessert den DOMINANTEN
Mehrsymbol-Fall messbar (viele kleine Symbol-Batches erreichen die Study-Schwelle typischerweise
lange vor der Symbol-Schwelle), OHNE die dokumentierte Korrektheits-Grenze zu verletzen. Der
Ein-Symbol-Fall (Akzeptanzkriterium 1 aus GH #1189) bleibt dagegen weiterhin durch dieselbe,
zweimal dokumentierte Grenze beschraenkt — siehe ``test_single_symbol_run_is_still_bounded_by_the_
documented_correctness_limit`` unten.
"""
import inspect
import json
import math

from automation.optimizer import sweep
from automation.optimizer.trial_config import config_dir


# ── sweep._fail_fast_probe_study_threshold — reine Arithmetik ───────────────────────────────────

def test_threshold_matches_the_gh_1189_reference_example():
    """Akzeptanzkriterium (Arithmetik-Teil): ceil(0.2 * 14) == 3."""
    assert sweep._fail_fast_probe_study_threshold(14, 0.2) == 3


def test_threshold_is_never_below_one():
    assert sweep._fail_fast_probe_study_threshold(1, 0.2) == 1
    assert sweep._fail_fast_probe_study_threshold(2, 0.01) == 1


def test_threshold_handles_zero_planned_studies_without_dividing_by_zero():
    assert sweep._fail_fast_probe_study_threshold(0, 0.2) == 1


def test_threshold_rounds_up_not_down():
    # ceil(0.2 * 11) = ceil(2.2) = 3, NICHT floor(2.2) = 2.
    assert sweep._fail_fast_probe_study_threshold(11, 0.2) == 3


# ── optimizer.json — Default jetzt 0.2 (Fix-Vorgabe GH #1189), tatsaechlich verdrahtet ───────────

def test_production_config_default_is_0_2():
    cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert cfg.get("fail_fast_min_completed_studies_frac") == 0.2


# ── sweep.run_per_symbol_sweep — Verdrahtung (Textsicherung, siehe test_issue_1269-Konvention) ──

def test_probe_trigger_condition_now_also_checks_the_cumulative_study_threshold():
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    assert "_fail_fast_study_threshold" in src
    assert "len(proposals) >= _fail_fast_study_threshold" in src
    # Die Symbol-Schwelle bleibt ALS Alternative bestehen (ODER-verknuepft, nicht ersetzt).
    assert "len(completed_symbols) >= _fail_fast_min_symbols" in src


def test_probe_trigger_still_requires_using_real_optimize_and_no_prior_verdict():
    """Regressionsschutz: die neue ODER-Bedingung darf die bestehenden UND-Vorbedingungen
    (using_real_optimize, _fail_fast_invariants konfiguriert, noch kein Verdikt) nicht aufweichen."""
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    idx = src.index("len(proposals) >= _fail_fast_study_threshold")
    window = src[max(0, idx - 300):idx + 300]
    assert "using_real_optimize" in window
    assert "sweep_fail_fast_invariant is None" in window


def test_study_threshold_computed_before_the_symbol_loop_starts():
    """Regressionsschutz gegen dieselbe Positionierungsfalle wie in anderen Sessions bereits
    gefunden (vgl. check_invariant_coverage-Reihenfolge): die Schwelle muss VOR der ersten
    Verwendung im Symbol-Loop existieren."""
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    def_idx = src.index("_fail_fast_study_threshold = _fail_fast_probe_study_threshold(")
    use_idx = src.index("len(proposals) >= _fail_fast_study_threshold")
    assert def_idx < use_idx


# ── Ehrliche Grenze: der Ein-Symbol-Fall bleibt architekturell unerreichbar ──────────────────────

def test_single_symbol_run_is_still_bounded_by_the_documented_correctness_limit():
    """Akzeptanzkriterium 1 aus GH #1189 ("Ein-Symbol-Lauf mit 14 Studies löst die Probe nach
    spätestens 3 abgeschlossenen Studies aus") wird von DIESEM Fix NICHT erreicht — bewusst, siehe
    Moduldocstring. Fuer n_symbols_planned == 1 gibt es exakt EINEN sicheren Checkpoint (nach dem
    vollstaendigen Abschluss des einzigen Symbols, inklusive Confirm/Export ALLER seiner Studies) —
    ``len(proposals)`` bleibt bis dahin 0 und springt dann direkt auf die volle Studienzahl, NICHT
    graduell auf 3 von 14. Dieser Test dokumentiert die Grenze als Text-Beleg (nicht als
    Verhaltenstest ueber einen echten Sweep, der eine volle Optuna-Storage-Umgebung braeuchte) —
    ``_run_confirm_and_export`` wird NACHWEISLICH erst nach der vollstaendigen ``symbol_studies``-
    Batch aufgerufen (dieselbe strukturelle Barriere, die #1139/#1287 bereits dokumentiert haben)."""
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    # Die Batch-Dispatch-Zeile (alle Studies EINES Symbols in einem Rutsch) existiert weiterhin
    # unveraendert als synchrone Barriere OHNE Zwischen-Checkpoint.
    assert "symbol_studies = [_run_optimize(p) for p in symbol_pairs]" in src
    dispatch_idx = src.index("symbol_studies = [_run_optimize(p) for p in symbol_pairs]")
    confirm_idx = src.index("proposal = _run_confirm_and_export(")
    assert dispatch_idx < confirm_idx, (
        "die Studies-Optimierung eines Symbols muss weiterhin VOR jedem Confirm-Aufruf vollstaendig "
        "abgeschlossen sein — kein Zwischen-Checkpoint innerhalb der Batch."
    )
