"""Issue #856 (P0, Katalog #856-#861, GitHub-Issue #758) — die #839-Fail-Fast-Probe wirft bei JEDEM
Aufruf und ist damit vollstaendig wirkungslos (0 von 32 erfolgreichen Aufrufen im Referenzlauf
``c00dc3c0``).

Root-Cause: die Call-Site in ``sweep.py`` rief ``report._build_report`` DIREKT mit ``proposals``
(``list[Path]``) auf und umging damit die Path->dict-Normalisierung, die vorher AUSSCHLIESSLICH in
``generate_sweep_report`` inline lag. ``_load_study_for_proposal`` erwartet ein dict und ruft
``proposal.get("strategy")`` auf einem ``PosixPath`` auf ⇒ ``AttributeError``, verschluckt von
einem ``except Exception`` in ``sweep.py``.

Fix:
1. Die Parsing-Logik ist jetzt in ``report.parse_proposal_payloads`` gehoben (Single Source of
   Truth), konsumiert von ``generate_sweep_report`` UND dem neuen Wrapper ``build_probe_report``.
2. Die Probe in ``sweep.py`` ruft ``report.build_probe_report(proposals, run_id=...)`` statt
   ``_build_report`` direkt auf.
3. ``_build_report`` selbst ist fail-loud gehaertet: ein Nicht-dict-Element wirft ``TypeError``
   statt in ``_load_study_for_proposal`` mit einer nichtssagenden ``AttributeError`` zu sterben.
4. Ein zweiter aufeinanderfolgender Fehlschlag der Probe eskaliert auf ERROR +
   ``FAIL_FAST_PROBE_BROKEN`` und deaktiviert die Probe fuer den Rest des Laufs, statt denselben
   Traceback beliebig oft zu wiederholen (Pitfall #270).
"""
import ast
import json
from pathlib import Path

import pytest

from automation.optimizer import report as _report

TESTS_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = TESTS_DIR.parent


def _write_proposal(tmp_path: Path, strategy: str, symbol: str, **extra) -> Path:
    payload = {"strategy": strategy, "symbol": symbol, "status": "REJECTED", **extra}
    path = tmp_path / f"proposal_{strategy}_{symbol}.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


# ── report.parse_proposal_payloads: die neue Single Source of Truth ─────────────────────────────
def test_parse_proposal_payloads_loads_real_paths(tmp_path):
    p1 = _write_proposal(tmp_path, "Rsi2Reversion", "AAPL.ETORO")
    p2 = _write_proposal(tmp_path, "Vwap", "MSFT.ETORO")

    parsed = _report.parse_proposal_payloads([p1, p2])

    assert len(parsed) == 2
    assert {p["symbol"] for p in parsed} == {"AAPL.ETORO", "MSFT.ETORO"}
    assert all(isinstance(p, dict) for p in parsed)


def test_parse_proposal_payloads_passes_through_dicts_unchanged():
    payload = {"strategy": "S", "symbol": "X.ETORO"}
    assert _report.parse_proposal_payloads([payload]) == [payload]


def test_parse_proposal_payloads_drops_payloads_without_symbol(tmp_path):
    no_symbol = tmp_path / "proposal_broken.json"
    no_symbol.write_text(json.dumps({"strategy": "S"}), "utf-8")
    good = _write_proposal(tmp_path, "S", "Y.ETORO")

    parsed = _report.parse_proposal_payloads([no_symbol, good])

    assert len(parsed) == 1
    assert parsed[0]["symbol"] == "Y.ETORO"


# ── Der eigentliche Regressionstest: die #839-Call-Site mit einer ECHTEN list[Path] ─────────────
def test_build_probe_report_handles_real_path_list_without_error(tmp_path):
    """Konstruiert die exakte Eingabe, die ``sweep.py`` an der Fail-Fast-Call-Site sammelt
    (``proposals: list[Path]``, befuellt aus ``export_symbol_proposal``-Rueckgabewerten) und ruft
    denselben Wrapper auf, den die Call-Site jetzt konsumiert. Vor #856 war die aequivalente
    Operation ein direkter ``_build_report(proposals, ...)``-Aufruf, der bei JEDEM Element
    ``AttributeError: 'PosixPath' object has no attribute 'get'`` warf."""
    proposal_paths = [
        _write_proposal(tmp_path, "Rsi2Reversion", "AAPL.ETORO"),
        _write_proposal(tmp_path, "Vwap", "MSFT.ETORO"),
    ]

    report = _report.build_probe_report(proposal_paths, run_id="probe-run")

    assert report["run_id"] == "probe-run"
    assert len(report["studies"]) == 2
    assert isinstance(report.get("invariant_checks"), list)
    assert len(report["invariant_checks"]) > 0


def test_build_probe_report_empty_list_is_a_noop():
    report = _report.build_probe_report([], run_id="probe-empty")
    assert report["studies"] == []


# ── _build_report: fail-loud statt einer nichtssagenden AttributeError ──────────────────────────
def test_build_report_raises_typeerror_on_raw_path_list(tmp_path):
    """Der Kern des Bugs: ``_build_report`` mit unverarbeiteten ``Path``-Objekten aufzurufen (die
    alte, fehlerhafte Call-Site) muss jetzt sofort und verstaendlich fehlschlagen -- nicht drei
    Stack-Frames tiefer mit ``AttributeError``."""
    proposal_path = _write_proposal(tmp_path, "Rsi2Reversion", "AAPL.ETORO")

    with pytest.raises(TypeError, match="parse_proposal_payloads"):
        _report._build_report(
            [proposal_path], run_id="r", started_at_utc=None, wallclock_s=None, cli_args=None,
            reports_dir=tmp_path)


def test_build_report_accepts_parsed_dicts(tmp_path):
    payload = {"strategy": "S", "symbol": "X.ETORO", "status": "REJECTED"}
    report = _report._build_report(
        [payload], run_id="r", started_at_utc=None, wallclock_s=None, cli_args=None,
        reports_dir=tmp_path)
    assert len(report["studies"]) == 1


def test_build_report_empty_list_still_bit_identical(tmp_path):
    report = _report._build_report(
        [], run_id="r1", started_at_utc=None, wallclock_s=None, cli_args=None,
        reports_dir=tmp_path)
    assert report["run_status"] == "complete"
    assert report["studies"] == []


# ── AST-Lint: _build_report wird ausserhalb von report.py nirgends direkt aufgerufen ────────────
def _direct_build_report_call_sites(py_file: Path) -> list[int]:
    tree = ast.parse(py_file.read_text("utf-8"), filename=str(py_file))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name == "_build_report":
            hits.append(node.lineno)
    return hits


def test_no_production_call_site_bypasses_the_probe_wrapper():
    """Ausserhalb von ``report.py`` selbst darf ``_build_report`` in Produktionscode nirgends
    direkt aufgerufen werden -- jeder Aufrufer muss ueber ``generate_sweep_report`` oder
    ``build_probe_report`` gehen, die beide die Path->dict-Normalisierung erzwingen."""
    offenders = {}
    for py_file in AUTOMATION_DIR.rglob("*.py"):
        if py_file.name == "report.py" or "tests" in py_file.relative_to(AUTOMATION_DIR).parts:
            continue
        hits = _direct_build_report_call_sites(py_file)
        if hits:
            offenders[str(py_file.relative_to(AUTOMATION_DIR))] = hits
    assert offenders == {}
