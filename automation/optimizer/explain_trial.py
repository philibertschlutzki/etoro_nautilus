"""automation/optimizer/explain_trial.py
=========================================
Issue #745 — CLI, das GENAU EINEN Trial vollständig erklärt.

Diagnose-Bausteine (``oos_gate_deltas``, ``reward_terms``, ``sweep_diagnostics.
diagnose_trade_frequency``) existieren bereits verstreut — jede Forensik-Sitzung baute die
Zusammenschau für einen konkret interessanten Trial bislang von Hand über SQLite/pandas neu.
Dieses Modul bündelt sie zu EINER festen Sektionsstruktur (Reward → Gates → Rejection-Kette →
Near-Miss-Deltas, knappste Marge zuerst → Binding-Cause bei Study-weitem Kollaps) als druckfertige
Markdown- oder JSON-Ausgabe.

Wiederverwendet #742/#743s Datenzugriffs-Bausteine (``run_optimization._sanitize``/
``resolve_storage`` — dieselbe Study-Namens-/Storage-Konvention wie ``report.py``), dupliziert sie
nicht.

Verwendung:
    python -m automation.optimizer.explain_trial --strategy SmaCrossoverStrategy \\
        --symbol TSLA.ETORO --trial 42 [--format markdown|json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import optuna

from automation.optimizer.run_optimization import _sanitize, resolve_storage
from automation.optimizer.trial_config import config_dir


class TrialLookupError(Exception):
    """Fail-loud, menschenlesbar — nie ein rohes Traceback für einen simplen Lookup-Fehler."""


def _load_study(strategy: str, symbol: str):
    study_name = f"study_{strategy}_{_sanitize(symbol)}"
    storage = resolve_storage(study_name=study_name)
    try:
        return optuna.load_study(study_name=study_name, storage=storage), study_name, storage
    except Exception as e:
        raise TrialLookupError(
            f"Study '{study_name}' nicht gefunden (Storage={storage}): {e}") from e


def _find_trial(study, trial_number: int):
    for t in study.trials:
        if t.number == trial_number:
            return t
    known = sorted(t.number for t in study.trials)
    raise TrialLookupError(
        f"Trial #{trial_number} nicht in Study '{getattr(study, 'study_name', '?')}' "
        f"({len(known)} Trials vorhanden: {known[:5]}{'...' if len(known) > 5 else ''})."
    )


def _oos_min_trades(base_cfg: Path | None = None) -> int:
    try:
        cfg_path = (base_cfg or config_dir()) / "tournament.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            return int(data.get("min_trades", data.get("oos_min_trades", 20)))
    except (OSError, ValueError, TypeError):
        pass
    return 20


def _binding_cause_for_study(study) -> dict[str, Any] | None:
    """Nur aussagekräftig bei einem Study-weiten 0-evaluable/0-eligible-Kollaps — wiederverwendet
    ``sweep_diagnostics.diagnose_trade_frequency`` (#669) statt eine zweite Klassifikation zu
    bauen. ``None`` bei Import-/Datenfehlern (nie fatal für den Rest der Erklärung)."""
    try:
        from automation.optimizer.sweep_diagnostics import diagnose_trade_frequency
    except ImportError:
        return None
    trials = [dict(t.user_attrs or {}) for t in getattr(study, "trials", [])]
    if not trials:
        return None
    result = diagnose_trade_frequency(trials, oos_min_trades=_oos_min_trades())
    return result if result.get("binding_cause") not in (None, "none") else None


def build_explanation(study, trial) -> dict[str, Any]:
    """Reine Aggregation über bereits gestempelte ``user_attrs`` — kein Re-Backtest, kein I/O
    ausser dem Study-Load, das der Aufrufer bereits erledigt hat."""
    attrs = dict(trial.user_attrs or {})
    reward_terms = attrs.get("reward_terms") or {}

    gate_deltas = dict(attrs.get("oos_gate_deltas") or {})
    near_miss_deltas = [
        {"metric": k, "delta": v}
        for k, v in sorted(
            gate_deltas.items(),
            key=lambda kv: abs(kv[1]) if isinstance(kv[1], (int, float)) else float("inf"),
        )
    ]

    rejection_chain: list[dict[str, Any]] = []
    is_detail = attrs.get("is_rejection_detail")
    if is_detail and is_detail != "NONE":
        rejection_chain.append({"stage": "is_gate", "detail": is_detail})
    seen = {c["detail"] for c in rejection_chain}
    for reason in attrs.get("oos_rejection_reasons") or []:
        if reason and reason not in seen:
            rejection_chain.append({"stage": "oos_gate_reason", "detail": reason})
            seen.add(reason)

    return {
        "study_name": getattr(study, "study_name", None),
        "trial_number": trial.number,
        "trial_state": str(getattr(trial, "state", "UNKNOWN")),
        "reward": {"value": getattr(trial, "value", None), "terms": reward_terms},
        "gates": {
            "oos_evaluated": attrs.get("oos_evaluated"),
            "oos_eligible": attrs.get("oos_eligible"),
            "oos_total_trades": attrs.get("oos_total_trades"),
            "is_total_trades": attrs.get("is_total_trades"),
            "hit_trade_cap": attrs.get("hit_trade_cap"),
            "oos_coherence_violation": attrs.get("oos_coherence_violation"),
        },
        "rejection_chain": rejection_chain,
        "near_miss_deltas": near_miss_deltas,
        "binding_cause": _binding_cause_for_study(study),
    }


def render_markdown(explanation: dict[str, Any]) -> str:
    lines = [
        f"# Trial #{explanation['trial_number']} — {explanation['study_name']}",
        f"State: `{explanation['trial_state']}`",
        "",
        "## Reward",
        f"- value: `{explanation['reward']['value']}`",
    ]
    for k, v in (explanation["reward"]["terms"] or {}).items():
        lines.append(f"  - {k}: `{v}`")

    lines += ["", "## Gates"]
    for k, v in explanation["gates"].items():
        lines.append(f"- {k}: `{v}`")

    lines += ["", "## Rejection-Kette"]
    if explanation["rejection_chain"]:
        for c in explanation["rejection_chain"]:
            lines.append(f"- [{c['stage']}] `{c['detail']}`")
    else:
        lines.append("- (keine — Trial wurde nicht abgelehnt)")

    lines += ["", "## Near-Miss-Deltas (knappste Marge zuerst)"]
    if explanation["near_miss_deltas"]:
        for d in explanation["near_miss_deltas"]:
            lines.append(f"- {d['metric']}: `{d['delta']}`")
    else:
        lines.append("- (keine Gate-Deltas gestempelt)")

    lines += ["", "## Binding-Cause (Study-weiter Kollaps)"]
    bc = explanation["binding_cause"]
    lines.append(f"- `{bc}`" if bc else "- n/a (kein Study-weiter 0-evaluable/0-eligible-Kollaps)")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Issue #745 — erklärt EINEN Optuna-Trial vollständig (Reward, Gates, "
                    "Rejection-Kette, Near-Miss-Deltas, Binding-Cause).")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--trial", type=int, required=True, dest="trial_number")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args(argv)

    try:
        study, _name, _storage = _load_study(args.strategy, args.symbol)
        trial = _find_trial(study, args.trial_number)
    except TrialLookupError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    explanation = build_explanation(study, trial)
    if args.format == "json":
        print(json.dumps(explanation, indent=2, default=str))
    else:
        print(render_markdown(explanation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
