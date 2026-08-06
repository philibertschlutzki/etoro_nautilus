"""Issue #913 — Akzeptanzkriterien, die test_issue_862_guard_reference_anchor.py nicht abdeckt:
ein AST-Vertragstest über ALLE Aufrufer von ``_effective_sortino_numeric_guard`` (verlangt
``family_median_n_periods`` als Keyword) und ``assert_guard_reference_injectable()`` selbst.
"""
import ast
import inspect

import pytest


def test_every_call_site_of_effective_guard_passes_family_median_n_periods_keyword():
    import automation.backtest_runner as br
    source = inspect.getsource(br)
    tree = ast.parse(source)
    call_sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "_effective_sortino_numeric_guard":
                call_sites.append(node)
    assert call_sites, "Kein Aufrufer von _effective_sortino_numeric_guard gefunden — Extraktion kaputt?"
    for node in call_sites:
        kw_names = {kw.arg for kw in node.keywords}
        assert "family_median_n_periods" in kw_names, (
            f"Aufruf in Zeile {node.lineno} übergibt family_median_n_periods nicht als Keyword — "
            "Issue #913: eine 'family_median'-Config waere ohne diesen Injektionspunkt folgenlos.")


def test_assert_guard_reference_injectable_is_a_noop_under_absolute_mode(monkeypatch, tmp_path):
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text('{"sortino_numeric_guard_reference": "absolute"}',
                                              encoding="utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    br.assert_guard_reference_injectable()  # must not raise


def test_assert_guard_reference_injectable_passes_under_the_real_wired_source(monkeypatch, tmp_path):
    """Unter 'family_median' (der Produktions-Default) muss die reale, aktuell verdrahtete
    Quelldatei den Preflight bestehen — die eigentliche Regressionssperre gegen #913."""
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        '{"sortino_numeric_guard_reference": "family_median"}', encoding="utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    br.assert_guard_reference_injectable()  # must not raise


def test_assert_guard_reference_injectable_fails_loud_if_the_injection_disappears(monkeypatch, tmp_path):
    """Simuliert die #913-Regression: ein Aufrufer, der family_median_n_periods NICHT als Keyword
    übergibt, muss den Preflight fail-loud abbrechen lassen (REJECT_GUARD_REFERENCE_NOT_WIRED)."""
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        '{"sortino_numeric_guard_reference": "family_median"}', encoding="utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)

    unwired_source = (
        "def _effective_sortino_numeric_guard(g, n, *, family_median_n_periods=None):\n"
        "    pass\n"
        "def caller():\n"
        "    _effective_sortino_numeric_guard(25.0, 100)\n"  # no keyword — the regression
    )
    import types
    fake_module = types.ModuleType("fake_backtest_runner")
    _real_getsource = inspect.getsource
    monkeypatch.setattr(
        inspect, "getsource", lambda mod: unwired_source if mod is fake_module else _real_getsource(mod))
    monkeypatch.setitem(__import__("sys").modules, br.__name__, fake_module)
    try:
        with pytest.raises(ValueError, match="REJECT_GUARD_REFERENCE_NOT_WIRED"):
            br.assert_guard_reference_injectable()
    finally:
        monkeypatch.setitem(__import__("sys").modules, br.__name__, br)
