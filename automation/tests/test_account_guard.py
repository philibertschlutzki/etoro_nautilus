"""Konto-Sperre: Real-Endpunkte nur für das per Konto-ID angeheftete Paper-Konto."""
from __future__ import annotations

import pytest

from automation import account_guard as ag


def test_no_pin_means_demo_endpoints_and_no_network():
    def boom(*_a, **_k):
        raise AssertionError("kein Netzzugriff ohne Anheftung")
    assert ag.verify_paper_account("k", "u", environ={}, fetch=boom) == "demo"
    assert ag.api_environment({}) == "demo"


def test_pinned_account_matching_realcid_is_allowed():
    env = {ag.PAPER_CID_ENV: "48264806"}
    assert ag.verify_paper_account("k", "u", environ=env, fetch=lambda *_: {"realCid": 48264806}) == "real"
    assert ag.api_environment(env) == "real"


@pytest.mark.parametrize("me", [{"realCid": 1}, {}, {"realCid": None}])
def test_other_account_is_refused(me):
    with pytest.raises(ag.PaperAccountError, match="nicht gehandelt"):
        ag.verify_paper_account("k", "u", environ={ag.PAPER_CID_ENV: "48264806"}, fetch=lambda *_: me)


def test_failed_lookup_is_fail_closed():
    import urllib.error

    def opener(*_a, **_k):
        raise urllib.error.URLError("offline")
    with pytest.raises(ag.PaperAccountError):
        ag.fetch_me("k", "u", opener=opener)
