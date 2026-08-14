"""Issue #1098 (Katalog #931) — ``events.jsonl`` verlor unter Diagnose-Last aus mehreren
gleichzeitigen Sweep-Worker-Threads systematisch Ereignisse (ASML 1814/1814 Δ 0, NVDA 1940/1940
Δ 0, PLTR 1920/1926 Δ −6, TSLA 1910/1924 Δ −14 — korreliert exakt mit dem
``INFERENCE_DIAGNOSTIC``-Volumen). Root-Cause: ``log_manager._append_jsonl_sidecar`` öffnete die
Datei gepuffert (``open(..., "a")``) und rief ZWEI getrennte ``write()``-Aufrufe statt EINEM
atomaren ``os.write()`` auf einem ``O_APPEND``-Deskriptor.

Diese Datei deckt drei Ebenen ab:
  1. ``log_manager``: der atomare Zeilen-Append selbst, unter echter Thread-Nebenläufigkeit
     (die Regressions-Probe für die Root-Cause).
  2. ``report._count_jsonl_events``: die "actual"-Seite der Vollständigkeitsprüfung.
  3. ``invariants.check_event_stream_completeness``: der neue Wächter (severity ``high``), der
     das vom Sweep geschriebene ``EVENTS_MANIFEST``-Ereignis gegen die tatsächliche Zeilenzahl
     vergleicht.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from automation.log_manager import emit_execution_event, jsonl_sidecar_path, setup_bot_logging
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# ── log_manager: atomarer Zeilen-Append ──────────────────────────────────────────────────────────
def test_jsonl_sidecar_path_resolves_after_setup(tmp_path):
    setup_bot_logging("test_1098_path", log_dir=tmp_path)
    path = jsonl_sidecar_path("test_1098_path")
    assert path is not None
    assert path.name.startswith("test_1098_path_")
    assert path.suffixes[-2:] == [".events", ".jsonl"]


def test_jsonl_sidecar_path_none_for_unregistered_logger():
    assert jsonl_sidecar_path("test_1098_never_initialized_xyz") is None


def test_single_event_written_as_one_valid_json_line(tmp_path):
    logger = setup_bot_logging("test_1098_single", log_dir=tmp_path)
    emit_execution_event(logger, "optimizer_trial_completed", {"trial_number": 1})
    path = jsonl_sidecar_path("test_1098_single")
    lines = path.read_text("utf-8").strip("\n").split("\n")
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "optimizer_trial_completed"
    assert payload["trial_number"] == 1


def test_concurrent_appends_lose_no_events_stress(tmp_path):
    """Reproduziert die #1098-Root-Cause direkt: viele gleichzeitige Worker-Threads schreiben in
    denselben Sidecar. Der ALTE, gepufferte Zwei-``write()``-Pfad verlor unter dieser Last
    regelmässig Zeilen (Interleaving zwischen den beiden ``write()``-Aufrufen); der neue,
    einzelne ``os.write()`` auf einem ``O_APPEND``-Deskriptor ist POSIX-atomar je Zeile."""
    logger = setup_bot_logging("test_1098_concurrency", log_dir=tmp_path)
    n_threads = 40
    n_per_thread = 25

    def _emit_many(i):
        for j in range(n_per_thread):
            emit_execution_event(logger, "optimizer_trial_completed",
                                  {"trial_number": i * n_per_thread + j})

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        list(ex.map(_emit_many, range(n_threads)))

    path = jsonl_sidecar_path("test_1098_concurrency")
    lines = path.read_text("utf-8").strip("\n").split("\n")
    assert len(lines) == n_threads * n_per_thread
    trial_numbers = set()
    for line in lines:
        payload = json.loads(line)  # jede Zeile muss fuer sich valides JSON sein (keine Verstuemmelung).
        trial_numbers.add(payload["trial_number"])
    assert len(trial_numbers) == n_threads * n_per_thread  # keine Zeile ging verloren.


# ── report._count_jsonl_events ───────────────────────────────────────────────────────────────────
def test_count_jsonl_events_counts_only_requested_types(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"event_type": "optimizer_trial_completed"}\n'
        '{"event_type": "optimizer_trial_completed"}\n'
        '{"event_type": "optimizer_study_completed"}\n'
        '{"event_type": "INFERENCE_DIAGNOSTIC"}\n',
        "utf-8",
    )
    counts = rpt._count_jsonl_events(
        path, {"optimizer_trial_completed", "optimizer_study_completed"})
    assert counts == {"optimizer_trial_completed": 2, "optimizer_study_completed": 1}


def test_count_jsonl_events_skips_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"event_type": "optimizer_trial_completed"}\n'
        'NOT VALID JSON\n'
        '\n'
        '{"event_type": "optimizer_trial_completed"}\n',
        "utf-8",
    )
    counts = rpt._count_jsonl_events(path, {"optimizer_trial_completed"})
    assert counts == {"optimizer_trial_completed": 2}


def test_count_jsonl_events_zero_for_missing_file(tmp_path):
    counts = rpt._count_jsonl_events(tmp_path / "does_not_exist.jsonl", {"optimizer_trial_completed"})
    assert counts == {"optimizer_trial_completed": 0}


def test_count_jsonl_events_zero_for_none_path():
    counts = rpt._count_jsonl_events(None, {"optimizer_trial_completed"})
    assert counts == {"optimizer_trial_completed": 0}


# ── invariants.check_event_stream_completeness ───────────────────────────────────────────────────
def test_completeness_passes_when_counts_match():
    result = inv.check_event_stream_completeness(1814, 1814, 14, 14)
    assert result.passed is True
    assert result.inconclusive is False
    assert result.actual["delta_trial_events"] == 0


def test_completeness_fails_matching_931_reference_symptom():
    """TSLA-Referenzsymptom: 1910 tatsaechliche gegen 1924 erwartete Ereignisse (Delta -14)."""
    result = inv.check_event_stream_completeness(1924, 1910, 14, 14)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["delta_trial_events"] == -14


def test_completeness_inconclusive_without_manifest():
    result = inv.check_event_stream_completeness(None, 0, None, 0)
    assert result.passed is True
    assert result.inconclusive is True
    assert result.actual is None


def test_completeness_respects_tolerance():
    result = inv.check_event_stream_completeness(100, 99, 5, 5, tolerance=1)
    assert result.passed is True


def test_completeness_flags_surplus_events_too():
    """Nicht nur ein Verlust (Delta < 0), auch ein UEBERSCHUSS (z. B. eine Duplizierung durch eine
    kuenftige Retry-Regression) muss sichtbar werden."""
    result = inv.check_event_stream_completeness(100, 105, 5, 5)
    assert result.passed is False
    assert result.actual["delta_trial_events"] == 5
