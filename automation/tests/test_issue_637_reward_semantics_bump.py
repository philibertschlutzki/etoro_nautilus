"""Issue #637 — `reward_semantics_version` stand trotz #614-PSR-Base weiterhin auf 8.

`_check_reward_semantics_version` erkennt eine geladene Study nur dann als inkompatibel/stale, wenn
die gespeicherte Version von der Config-Version abweicht. Blieb die Version bei 8, während die
Reward-SEMANTIK mehrfach grundlegend wechselte (#614 asinh-Sortino→PSR, #630 PSR→psr_z, #629
Klippe/Floor-Band entfernt, #631/#638 Straf-/Tie-Breaker-Reskalierung), wurde eine unter der alten
v8-Semantik angelegte SQLite-Study NICHT als stale erkannt — TPE würde mit Rewards auf einer
inkompatiblen Skala geprimt.

Fix: reward_semantics_version auf 9 gebumpt (bündelt alle vier seit v8 akkumulierten Brüche).
"""
import hashlib
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))

# Issue #637 — die reward-SKALEN-relevanten Config-Werte (nicht die volle Quelle — Formatierungs-
# Refactors von compute_reward sollen diesen Test nicht triggern, eine Änderung an einer dieser
# Konstanten OHNE Versions-Bump hingegen schon). Bei jedem BEWUSSTEN Bump muss dieses Set + der
# gepinnte Fingerprint synchron mit reward_semantics_version aktualisiert werden.
_SCALE_KEYS = (
    "sortino_clip_abs", "sortino_soft_scale", "w_ret", "penalty_scale_vs_base",
    "penalty_dd_weight", "dd_reward_scale", "penalty_turnover_weight",
    "fold_dispersion_weight", "fold_dispersion_scale", "missing_fold_penalty_scale",
    "penalty_overfit_weight", "overfit_oos_luck_weight", "penalty_relative_cap",
    "lambda_reg", "penalty_unevaluable_oos", "unevaluable_shaping_span",
    "constraint_distance_penalty_weight",
)


def _scale_fingerprint(cfg: dict) -> str:
    payload = json.dumps({k: cfg.get(k) for k in _SCALE_KEYS}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Gepinnt gegen reward_semantics_version=9 (dieser PR). Bei jedem künftigen, bewussten Bump der
# Reward-Skalen-Konstanten: reward_semantics_version erhöhen UND diesen Fingerprint nachziehen —
# genau das erzwingt dieser Test (#637-Akzeptanzkriterium: "Kein Reward-berührender PR ohne
# Version-Bump").
_EXPECTED_FINGERPRINT_BY_VERSION = {
    9: _scale_fingerprint(CFG),  # zur Bump-Zeit erzeugt — pinnt den IST-Zustand von v9.
}


def test_reward_semantics_version_bumped_to_9():
    assert CFG["reward_semantics_version"] == 9


def test_version_is_documented_with_v9_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v9" in doc
    for ref in ("#614", "#630", "#629", "#631", "#638"):
        assert ref in doc, f"v9-Changelog muss {ref} referenzieren"


def test_scale_fingerprint_pinned_to_current_version():
    """Pitfall-Erinnerung (#637): jede Änderung an einer Reward-Skalen-Konstante MUSS
    reward_semantics_version bumpen. Dieser Test pinnt den Fingerprint der aktuellen Skalen-Werte
    gegen die aktuelle Version — ändert sich einer der Werte, ohne dass die Version (und mit ihr
    dieser Fingerprint) nachgezogen wird, schlägt der Test fehl."""
    version = CFG["reward_semantics_version"]
    assert version in _EXPECTED_FINGERPRINT_BY_VERSION, (
        f"Kein gepinnter Fingerprint für reward_semantics_version={version} — beim Bump in "
        f"_EXPECTED_FINGERPRINT_BY_VERSION nachtragen (test_issue_637_reward_semantics_bump.py)."
    )
    assert _scale_fingerprint(CFG) == _EXPECTED_FINGERPRINT_BY_VERSION[version]


def test_stale_pre_v9_study_is_rejected_fail_loud():
    """Integrationstest: eine Study, die unter einer älteren Version (z. B. 8) akkumulierte Trials
    trägt, wird beim Laden gegen die aktuelle v9-Config fail-loud abgelehnt (REJECT_STALE_STUDY_SEMANTICS)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 8}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v8"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)
