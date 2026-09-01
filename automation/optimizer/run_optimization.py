import json
import math
import os
import logging
import sqlite3
import statistics
import threading
import time
import warnings
import optuna
import sqlalchemy.exc
from collections import Counter
from functools import partial
from pathlib import Path

# Issue #402: Optuna wirft pro Sampler-Instanziierung ExperimentalWarnings fuer die bewusst
# genutzten TPESampler-Features `multivariate`/`group`. In einem Sweep ueber viele Symbole
# spammt das den Terminal zu. Gezielt NUR diese Warn-Kategorie unterdruecken — Optunas native
# Per-Trial-INFO-Logs (Reward-Werte; im Sweep via make_symbol_objective die einzige Per-Trial-
# Rueckmeldung, vgl. Issue #401) bleiben bewusst erhalten (KEIN globales set_verbosity(ERROR),
# um die Observability aus Issue #403 nicht zu untergraben).
warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
from automation.optimizer.manifest import WORK, catalog_fingerprint, git_commit
from automation.optimizer.spaces import sample_params
from automation.optimizer.trial_config import build_trial, config_dir, freeze_study_config, resolve_wf_settings
from automation.optimizer.runner import run_backtest, BacktestRunError
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import (
    compute_reward, assert_penalty_scale_calibrated, check_any_arm_reachability,
    check_any_arm_reachability_live, resolve_any_arm_policy, assert_gate_collinearity_guard,
    gate_collinearity_redundancy_alarm, selection_rule_fingerprint,
    check_mandatory_gate_reachability_live, _normalize_clause as _reward_normalize_clause,
    resolve_alpha_tstat_gate_threshold,
)
from automation.optimizer.confirm import confirm_on_holdout, export_proposal, export_no_viable_proposal
from automation.optimizer import retention
from automation.optimizer import disk_guard
from automation.optimizer import wallclock_guard
from automation.optimizer import invariants as _inv
from automation.optimizer import _contracts
from automation.log_manager import emit_execution_event, bind_study_context, default_run_id

STORAGE = f"sqlite:///{WORK / 'studies.db'}"

# Issue #411 — Optuna/SQLite `create_all`-DDL-Race. `RDBStorage.__init__` ruft
# `models.BaseModel.metadata.create_all(self.engine)` (check-then-create, TOCTOU). Zwei Worker, die
# dieselbe FRISCHE SQLite-Datei quasi-gleichzeitig oeffnen, setzen beide `CREATE TABLE studies` ab —
# der zweite crasht mit `table studies already exists`. `load_if_exists=True` schuetzt NICHT (greift
# erst auf Study-Row-Ebene, NACH dem Schema-Bootstrap). Ein prozessweiter Lock serialisiert den
# `create_study`-Aufruf; der Schema-Check dauert nur Millisekunden ⇒ kein relevanter Durchsatz-
# Verlust, aber die `create_all`-Kollision ist ausgeschlossen.
_study_lock = threading.Lock()

# Issue #994/#1146 (Katalog #1170) — Regressionswaechter gegen eine Wiederkehr des #1126/#1130-
# Stempel-Luecken-Fehlers: 14 in ``parsing.TournamentMetrics`` geparste ``oos_*``-Felder erreichten
# NIE ``trial.user_attrs`` (der Merge, der das #1126/#1130-Feldblock einfuehrte, zog die Stempelung
# unvollstaendig nach) — ``report._median_of_trial_field`` liest AUSSCHLIESSLICH ``trial.
# user_attrs``, also blieben die abgeleiteten Report-Felder in 28/28 Studies ``None``, obwohl der
# Backtest-Runner die Rohwerte berechnete UND ``parsing.py`` sie korrekt parste. Der Kontrakt-Test
# (``test_issue_994_1146_metric_stamping_contract.py``) iteriert ALLE ``oos_*``-Felder von
# ``TournamentMetrics`` und verlangt fuer jedes entweder eine Aufrufstelle
# ``trial.set_user_attr("<feld>", ...)`` in diesem Modul ODER einen Eintrag hier — mit Begruendung,
# WARUM das Feld legitim NICHT trial-gestempelt wird (kein stiller Drift mehr moeglich).
_INTENTIONALLY_UNSTAMPED_METRIC_FIELDS: dict[str, str] = {
    # Holdout-only: diese Felder werden NIE im Rahmen des IS/OOS-Sweep-Trial-Objectives gefuellt,
    # sondern ausschliesslich von confirm.py's promotiertem Holdout-Re-Evaluation-Pfad
    # (``_metrics_dict(promoted_m_symbol)`` -> ``report.py``s ``holdout_metrics.get("oos_...")``,
    # siehe dortige ``holdout_*``-Feldzuordnung). Ein Trial-User-Attr wuerde nie gesetzt, weil die
    # Groesse strukturell erst NACH dem Sweep, am promotierten Kandidaten, existiert.
    "oos_profit_factor_censored": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_profit_factor_censored)",
    "oos_profit_factor_raw": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_profit_factor_raw)",
    "oos_expectancy_capital_weighted": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_capital_weighted)",
    # Issue #1257 (GH #1127), Pitfall #454 — analog oos_expectancy_capital_weighted direkt oben:
    # reine Traceability-/Kohaerenz-Check-Felder (invariants.check_cost_basis_coherence), die nur
    # am promotierten Holdout-Kandidaten (report.py holdout_total_return_net/_gross,
    # holdout_expectancy_capital_weighted_net/_gross) gebraucht werden, nicht je Sweep-Trial.
    "oos_total_return_net": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_total_return_net, #1257)",
    "oos_total_return_gross": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_total_return_gross, #1257)",
    "oos_expectancy_capital_weighted_net": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_capital_weighted_net, #1257)",
    "oos_expectancy_capital_weighted_gross": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_capital_weighted_gross, #1257)",
    "oos_expectancy_winsorized": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_winsorized)",
    "oos_expectancy_outlier_count": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_outlier_count)",
    "oos_expectancy_cost_stress_1_5x": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_cost_stress_1_5x)",
    "oos_expectancy_cost_stress_2x": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_expectancy_cost_stress_2x)",
    "oos_expectancy_cost_stress_full_realism": "holdout-only (confirm.py-Re-Evaluation, siehe #1162/Issue 1010)",
    "oos_cvar_95": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_cvar_95)",
    "oos_es_99": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_es_99)",
    "oos_sortino_annualized": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_sortino_annualized)",
    "oos_annualization_factor_source": "holdout-only (confirm.py-Re-Evaluation, siehe report.py annualization_factor_source)",
    "oos_excess_return": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_excess_return, #986/#1140)",
    "oos_exposure_fraction": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_exposure_fraction, #986/#1140)",
    "oos_alpha": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_beta_regression, #986/#1140)",
    "oos_beta": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_beta_regression, #986/#1140)",
    # Issue #1093/#1241 — ENTFERNT (vormals hier als "holdout-only" allowlisted): das Feld wird
    # seit dem neuen oos_min_alpha_tstat-Gate tatsaechlich per Sweep-Trial gestempelt (siehe
    # Stempelstelle oben, neben oos_win_rate) — Grundlage fuer reward.check_mandatory_gate_
    # reachability_live. oos_alpha/oos_beta bleiben holdout-only (kein Gate braucht sie live).
    "oos_alpha_n_periods": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_beta_regression, #1038/#1187)",
    # Issue #1255/#1258 (GH #1125/#1128) — additive Audit-/Diagnostik-Felder der Alpha-Regression
    # (backtest_runner._alpha_regression_diagnostics), holdout-only wie oos_alpha_n_periods direkt
    # oben (kein Gate braucht sie live — anders als oos_alpha_tstat_hc3 selbst, das per Sweep-Trial
    # gestempelt wird, siehe Stempelstelle neben oos_alpha_tstat).
    "oos_alpha_tstat_df": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1255)",
    # Issue #1284 (GH #1157, Katalog #1272-1297, P3) — die tatsaechlich fuer oos_alpha_tstat_df
    # verwendete Stichprobengroesse, holdout-only wie oos_alpha_tstat_df selbst (kein Gate braucht
    # sie live — Rohmaterial fuer invariants.check_alpha_df_consistency).
    "oos_alpha_n_used": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1284)",
    "oos_alpha_n_total": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1258)",
    "oos_alpha_n_informative": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1258)",
    "oos_alpha_n_y_nonzero": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1258)",
    "oos_alpha_n_x_nonzero": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1258)",
    "oos_alpha_n_both_zero": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1258)",
    # Issue #1283 (GH #1156, Katalog #1272-1297, P0) — Kovarianz-Zerlegungs-Rohmaterial fuer
    # invariants.check_alpha_regression_identifiability, holdout-only wie die #1258-Audit-Felder
    # direkt oben (kein Gate braucht sie live).
    "oos_alpha_corr_xy": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_sd_x": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_sd_y": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_cov_xy": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_cov_in_market": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_cov_out_of_market": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_alpha_cov_exit_bars": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283 — stets None, siehe dortiger Docstring)",
    "oos_alpha_n_in_market": "holdout-only (confirm.py-Re-Evaluation, backtest_runner._alpha_regression_diagnostics, #1283)",
    "oos_f_turnover_realized_median": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_f_turnover_realized_median, #989/#1143, umbenannt #1085/#1233)",
    "oos_f_turnover_realized_max": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_f_turnover_realized_max, #989/#1143, umbenannt #1085/#1233)",
    "oos_f_realized_peak_median": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_f_realized_peak_median, #1085/#1233 check_sizing_identity_coherence)",
    "oos_f_realized_peak_max": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_f_realized_peak_max, #1085/#1233 check_sizing_cap_enforcement)",
    "oos_sizing_cap_corrections_count": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_sizing_cap_corrections_count, #1297 check_sizing_cap_enforcement)",
    "oos_sizing_cap_max_overshoot_pre_correction": "holdout-only (confirm.py-Re-Evaluation, siehe report.py holdout_sizing_cap_max_overshoot_pre_correction, #1297 check_sizing_cap_enforcement)",
    "oos_applied_financing_bps_per_day": "holdout-only (confirm.py-Re-Evaluation, siehe report.py applied_financing_bps_per_day, #1075/#1223 check_applied_cost_components_resolved)",
    "oos_applied_slippage_bps": "holdout-only (confirm.py-Re-Evaluation, siehe report.py applied_slippage_bps, #1075/#1223 check_applied_cost_components_resolved)",
    "oos_slippage_calibration_scope": "holdout-only (confirm.py-Re-Evaluation, siehe report.py slippage_calibration_scope, #1266/GH #1136 check_cost_stress_discriminates)",
    "oos_selection_cost_basis": "holdout-only (confirm.py-Re-Evaluation, siehe report.py selection_cost_basis, #1078/#1226 check_selection_cost_basis_contract)",
    # Issue #1023/#1172 — ENTFERNT (vormals hier als "holdout-only" allowlisted): das Feld wird
    # tatsaechlich per Sweep-Trial gestempelt (siehe Stempelstelle oben, neben den beiden
    # Nachbarfeldern) und von report._study_record aus trial_attrs summiert — die vorherige
    # Begruendung war die Bruchstelle selbst, keine gueltige Ausnahme.
    # In-Prozess konsumiert, ohne Persistenzbedarf: der Wert wird SYNCHRON innerhalb derselben
    # Trial-Objective-Auswertung verbraucht (Reward-/Constraint-Berechnung, Rejection-Detail,
    # ``optimizer_trial_completed``-Log-Event) — es existiert kein nachgelagerter Report-Konsument,
    # der ihn aus ``trial.user_attrs`` zurueckliest.
    "oos_max_drawdown": "synchron in Reward-/DD-Constraint-Berechnung verbraucht (run_optimization.py), kein trial_attrs-Ruecklesepfad",
    "oos_window_start_ns": "nur im optimizer_trial_completed-Log-Event (nicht trial.user_attrs), Issue #455",
    "oos_covered": "nur im optimizer_trial_completed-Log-Event (nicht trial.user_attrs), Issue #455",
    "oos_coverage_gap_days": "nur im optimizer_trial_completed-Log-Event (nicht trial.user_attrs), Issue #455",
    "oos_anchor_divergence": "nur im optimizer_trial_completed-Log-Event (nicht trial.user_attrs), Issue #455",
    # Issue #1032/#1181 — ENTFERNT (vormals hier als "kein trial_attrs-Konsument" allowlisted):
    # invariants.gate_inventory_table (#1003/#1155) IST ein trial_attrs-Konsument. Siehe
    # Stempelstelle oben, neben is_rejection_detail.
    "oos_ret_skew": "synchron via getattr(metrics,...) in confirm.py's DSR-Berechnung konsumiert, kein trial_attrs-Ruecklesepfad",
    "oos_ret_kurtosis": "synchron via getattr(metrics,...) in confirm.py's DSR-Berechnung konsumiert, kein trial_attrs-Ruecklesepfad",
    "oos_psr_z": "synchron via getattr(metrics,...) in reward.compute_reward konsumiert, kein trial_attrs-Ruecklesepfad",
    # Kein identifizierter Konsument (Stand #1146) — weder trial_attrs noch ein direkter
    # In-Prozess-Verbrauch. Kandidat fuer eine kuenftige Verdrahtung ODER Entfernung aus
    # ``parsing.TournamentMetrics``, aber KEIN Symptom dieses Fixes (kein Report-Feld erwartet sie).
    "oos_fold_returns": "kein identifizierter Konsument (Stand #1146) — weder Report noch In-Prozess-Verbrauch",
    "oos_folds_total": "kein identifizierter Konsument in Report/Confirm (Stand #1146) — nur backtest_runner-intern beim Parsen selbst",
    "oos_sortino_aggregation_basis": "kein identifizierter Konsument (Stand #1146) — weder Report noch In-Prozess-Verbrauch",
    "oos_p95_bars_held": "kein identifizierter Konsument (Stand #1146) — weder Report noch In-Prozess-Verbrauch",
    "oos_equity_ruined": "kein identifizierter Konsument (Stand #1146) — weder Report noch In-Prozess-Verbrauch",
}


def _create_study_with_retry(*, study_name: str, storage: str, sampler=None,
                             direction: str | None = "maximize", directions: list[str] | None = None):
    """Issue #411 — `optuna.create_study` serialisiert (``_study_lock``) und gegen die DDL-Race-
    Exception ``table studies already exists`` GENAU EINMAL retried.

    Kein blindes ``except Exception`` (Fail-Fast, Pitfall #66): ausschliesslich die exakte
    Race-Signatur (``"already exists"`` in einer ``sqlite3``/``sqlalchemy`` ``OperationalError``)
    wird abgefangen; jeder andere Fehler propagiert hart. Beim Retry existiert das Schema garantiert
    ⇒ ``load_if_exists=True`` laedt die Study sauber."""
    def _create():
        kwargs = dict(study_name=study_name, storage=storage, load_if_exists=True)
        if directions is not None:
            kwargs["directions"] = directions
        elif direction is not None:
            kwargs["direction"] = direction
        if sampler is not None:
            kwargs["sampler"] = sampler
        return optuna.create_study(**kwargs)

    with _study_lock:
        try:
            return _create()
        except (sqlite3.OperationalError, sqlalchemy.exc.OperationalError) as e:
            if "already exists" not in str(e):
                raise  # kein Schema-Race → propagieren (Fail-Fast)
            # Schema-Race verloren → Schema existiert jetzt sicher → erneut laden.
            return _create()


def _resolve_rdb_storage(storage: str):
    """Issue #747 — eine SQLite-Storage-URL explizit als ``optuna.storages.RDBStorage`` konstruieren,
    STATT ``optuna.create_study(storage=<url>)`` die zugrundeliegende SQLAlchemy-``Engine`` intern
    (und damit fuer den Aufrufer unreferenzierbar) bauen zu lassen. Nur eine explizit gehaltene
    ``RDBStorage``-Instanz erlaubt spaeter ``.engine.dispose()`` — ohne das haeuft der Per-Symbol-
    Sweep (``sweep.py``, Phase 1 haelt jede Study in einer Liste) eine offene Engine pro verarbeitetem
    Paar an (empirisch verifiziert: +2 FDs/Study, unbegrenzt, bis ``OSError(24, "Too many open
    files")``). Nicht-SQLite-URLs (Postgres-Opt-in, siehe ``resolve_storage``) sowie bereits
    aufgeloeste Storage-Objekte (Test-Fakes) unveraendert durchreichen — kein Dispose-Bedarf dafuer."""
    if isinstance(storage, str) and storage.startswith("sqlite"):
        return optuna.storages.RDBStorage(storage)
    return storage


def _dispose_storage(storage) -> None:
    """Issue #747 — ``Engine.dispose()`` schliesst den SQLAlchemy-Connection-Pool (und damit dessen
    offene File-Deskriptoren) einer ``RDBStorage``. Die Engine selbst ist danach NICHT tot — ein
    nachfolgender Zugriff (z. B. ``study.trials``) baut transparent eine neue Connection auf; Dispose
    ist daher risikofrei mehrfach aufrufbar und MUSS nach jedem Zugriffsfenster erneut erfolgen.
    No-Op fuer Storages ohne ``.engine`` (``InMemoryStorage``, Test-Doubles)."""
    engine = getattr(storage, "engine", None)
    if engine is not None:
        engine.dispose()


def _preinit_study_storage(study_name: str, *, base_cfg: Path | None = None) -> None:
    """Issue #411 — erzwingt den RDBStorage-Schema-Bootstrap (``create_all``) EINMAL und seriell im
    aufrufenden (Haupt-)Thread, BEVOR mehrere Worker dieselbe (frische) Study-Datei oeffnen. Damit
    trifft jeder nachfolgende Worker garantiert den ``exists``-Pfad (kein DDL-Race). Idempotent:
    laeuft das Schema/die Study schon, ist es ein No-Op (``load_if_exists``). Aufzurufen pro
    EINDEUTIGEM ``study_name`` (Eindeutigkeit ist Vorbedingung, vgl. #412/Pitfall #77)."""
    storage = resolve_storage(study_name=study_name, base_cfg=base_cfg)
    # resolve_storage liefert nur die URL; das per-Study-SQLite-Verzeichnis muss existieren.
    if storage.startswith("sqlite:///"):
        Path(storage[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    _create_study_with_retry(study_name=study_name, storage=storage)


def log_active_config(context: str, *, base_cfg: Path | None = None, extra: dict | None = None) -> None:
    """Issue #403 — strukturierter Startup-Header: legt offen, AUS WELCHEN Dateien die aktiven
    Konfigurationen stammen und welche Kern-Schwellen gelten, BEVOR der Lauf in die (bei
    ``capture_output=True`` stummen) iterativen Optuna-Trials uebergeht. Reines stdout
    (Operator-Terminal), defensiv gegen fehlende/kaputte JSONs. Gemeinsam genutzt von der
    globalen Optimierung (``run``) und dem Per-Symbol-Sweep (``sweep.main``)."""
    if base_cfg is None:
        base_cfg = config_dir()

    def _safe(p: Path) -> dict:
        try:
            return json.loads(p.read_text("utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return {}

    opt = _safe(base_cfg / "optimizer.json")
    tour = _safe(base_cfg / "tournament.json")
    print("=" * 60)
    print(f"⚙️  Aktive Konfiguration ({context})")
    print(f"   Verzeichnis        : {base_cfg}")
    for name in ("optimizer.json", "tournament.json", "strategies.json", "backtest.json"):
        p = base_cfg / name
        print(f"   {name:<18}: {p}{'' if p.exists() else '  (FEHLT)'}")
    print(f"   Schwellen (opt)    : n_trials={opt.get('n_trials')}, n_startup_trials={opt.get('n_startup_trials')}, "
          f"seed={opt.get('seed')}, oos_sortino_fallback={opt.get('oos_sortino_fallback')}")
    print(f"   Schwellen (tourn.) : oos_min_trades={tour.get('oos_min_trades')}, "
          f"sortino_min_trades={tour.get('sortino_min_trades')}, max_drawdown={tour.get('max_drawdown')}")
    if extra:
        for k, v in extra.items():
            print(f"   {str(k):<18}: {v}")
    print("=" * 60)

def _emit_any_arm_reachability_result(logger: logging.Logger, unreachable: list[str], *,
                                      check_name: str, scope: str | None) -> None:
    """Issue #1015/#1167 (Katalog #1170) — ``check_any_arm_reachability``/``_live`` (reward.py)
    warnten intern bereits JE UNERREICHBARER Klausel, meldeten ihr GESAMT-Urteil aber nie
    strukturiert — ein Report konnte "alle Klauseln erreichbar" nicht von "Check nie ausgefuehrt"
    unterscheiden. Symmetrisch (PASS UND FAIL), ``source="sweep"`` (laeuft im selben Prozess wie
    sweep.py, "optimizer"-Sidecar, die ``report.py`` bereits liest)."""
    passed = not unreachable
    emit_execution_event(logger, "INVARIANT_STREAM_RESULT", {
        "name": check_name, "check": check_name,
        "passed": passed, "source": "sweep", "scope": scope,
        "expected": "jede eligible_requires_any-Klausel liegt unter dem p99 der Referenzverteilung "
                   "(strukturell erreichbar).",
        "actual": {"unreachable_clauses": unreachable} if not passed else None,
        "detail": (f"OR-Arm-Klausel(n) strukturell unerreichbar: {', '.join(unreachable)}."
                  if not passed else "Alle eligible_requires_any-Klauseln erreichbar."),
        "severity": "medium",
    }, level=logging.INFO if passed else logging.WARNING)


def _emit_mandatory_gate_reachability_result(
    logger: logging.Logger, unreachable: list[str], *, scope: str | None,
) -> None:
    """Issue #1280/#1281 (GH #1153/#1154, Katalog #1272-1297, P0) — eigenstaendiger Emit fuer
    ``check_mandatory_gate_reachability_live`` (#1093/#1241, ``reward.py``): existiert, wird
    aufgerufen, lieferte in 55/56 Studies eines Referenzkatalogs einen Befund, der aber bislang
    NIE unter dem eigenen Namen erschien (Root-Cause #1280: der Emit wurde in
    ``_emit_any_arm_reachability_result``/``check_any_arm_reachability_live`` gemergt) — der
    Report meldete stattdessen dasselbe Ergebnis unter dem Namen UND Text der ``requires_any``-
    Pruefung (Root-Cause #1281: ``oos_min_alpha_tstat`` ist Mitglied von ``eligible_requires_
    all``, nicht von ``eligible_requires_any``, das leer ist).

    ``severity='high'`` (statt der ``'medium'``-Schwere des ``requires_any``-Pendants) UND ein
    eigener Text: eine ``requires_all``-Klausel, die strukturell unerreichbar ist, lehnt JEDEN
    Trial unabhaengig von jeder anderen Kennzahl ab — keine Disjunktion, die "auf die uebrigen
    Arme kollabiert" (das beschreibt nur ``requires_any``). Der globale, laufweite
    ``severity='blocking``-Befund bei >= 80 % betroffener Studies lebt in
    ``invariants.check_mandatory_gate_reachability_global`` (report.py, braucht die volle
    Study-Liste eines Laufs — nicht in dieser Pro-Study-Funktion auswertbar)."""
    passed = not unreachable
    emit_execution_event(logger, "INVARIANT_STREAM_RESULT", {
        "name": "check_mandatory_gate_reachability_live",
        "check": "check_mandatory_gate_reachability_live",
        "passed": passed, "source": "sweep", "scope": scope,
        "expected": "jede eligible_requires_all-Klausel liegt unter dem p99 der Referenzverteilung "
                   "(strukturell erreichbar).",
        "actual": {"unreachable_clauses": unreachable} if not passed else None,
        "detail": (
            f"MANDATORY-Gate {', '.join(unreachable)} ist für diese Study strukturell "
            "unerreichbar — jeder Trial wird unabhängig von jeder anderen Kennzahl abgelehnt."
            if not passed else "Alle eligible_requires_all-Klauseln erreichbar."),
        "severity": "high",
    }, level=logging.INFO if passed else logging.WARNING)


def _reemit_inference_diagnostics(logger: logging.Logger, metrics, trial_number: int) -> None:
    """Issue #804 — re-emittiert jede in ``backtest_runner._calculate_stats`` (laeuft im Backtest-
    SUBPROZESS, ``runner.py``) gesammelte Inferenzpfad-Diagnose (``EQUITY_NONPOSITIVE``,
    ``PERIOD_RETURNS_NOT_FINITE``, ``RETURN_SERIES_IDENTITY_VIOLATION``/``_UNDEFINED``,
    ``NON_CONTIGUOUS_FOLD_SEGMENTS``, ``SORTINO_GUARD_TRIPPED``, ``COHERENCE_INVARIANT_VIOLATION``)
    als EIGENES ``INFERENCE_DIAGNOSTIC``-ERROR-Ereignis im ELTERNPROZESS-Log.

    Root-Cause #804: alle diese Diagnosen liefen bislang NUR ueber ``logging`` im Subprozess — der
    Stream landet in ``trial_dir/logs/backtest_stdout.log``, einer Datei, die kein Aggregator liest
    und die #794 Sekunden spaeter loescht (0 Treffer ueber ein vollstaendiges 5490-Zeilen-Lauf-Log,
    trotz 35 ``STUDY_ABORTED_ON_INVARIANT`` im Elternprozess). ``strategy``/``symbol``/``study_name``
    werden von ``emit_execution_event`` automatisch aus ``bind_study_context`` injiziert (#780) —
    hier nur ``trial_number`` zusaetzlich, das kein ambienter Kontext ist. No-Op, wenn
    ``metrics.inference_diagnostics`` leer ist (Normalfall, Pre-#804-JSONs eingeschlossen)."""
    for diag in metrics.inference_diagnostics or ():
        payload = {
            "trial_number": trial_number,
            "code": diag.get("code"),
            "detail": diag.get("detail"),
            "value": diag.get("value"),
        }
        # Issue #862 — zusätzliche Referenzwert-Telemetrie auf SORTINO_GUARD_TRIPPED-Diagnosen
        # (siehe backtest_runner._effective_sortino_numeric_guard) unverändert durchreichen.
        if "guard_reference_value" in diag:
            payload["guard_reference_value"] = diag.get("guard_reference_value")
            payload["guard_reference_source"] = diag.get("guard_reference_source")
        emit_execution_event(logger, "INFERENCE_DIAGNOSTIC", payload, level=logging.ERROR)


def _stop_study_safely(study, logger: logging.Logger) -> None:
    """Issue #456 — ``study.stop()`` crash-sicher aufrufen. Eine Study AUSSERHALB eines aktiven
    ``optimize()``-Kontexts (oder ein Test-Double ohne ``stop``) darf nicht zum Crash führen — die
    bereits emittierte Plateau-Warnung genügt dann. Idempotent über ``getattr`` + ``try/except``."""
    stop = getattr(study, "stop", None)
    if stop is None:
        return
    try:
        stop()
    except Exception:  # pragma: no cover - defensiver Schutz außerhalb optimize()
        logger.debug("study.stop() außerhalb eines optimize()-Kontexts ignoriert (Issue #456).")


def seed_effective(seed, study_name: str, run_salt: str | None = None) -> int | None:
    """Issue #755 — deterministischer PER-STUDY-Seed statt sweep-weiter Serialisierung.

    Root-Cause #755: ``sweep.main`` erzwang bislang ``n_jobs=1`` fuer den GESAMTEN Sweep, sobald
    ``optimizer.json['seed']`` gesetzt war — Determinismus wurde auf der falschen Ebene erzwungen.
    Jede Study hat eine EIGENE SQLite-Datei (``resolve_storage``, #400) und einen eigenen Sampler;
    zwei Studies unterschiedlicher (Strategie, Symbol)-Paare teilen keinerlei Sampler-Zustand.
    Reproduzierbarkeit erfordert nur, dass JEDE Study einen deterministischen Seed erhaelt — nicht,
    dass sie nacheinander laufen.

    ``seed_effective = seed XOR stable_hash(study_name)`` (Blake2b, 8 Byte) ist fuer einen gegebenen
    ``study_name`` konstant — ueber Prozessgrenzen und unabhaengig von der Ausfuehrungsreihenfolge —
    und fuer verschiedene ``study_name`` unterschiedlich (kein sweep-weit identischer Sampler-Seed
    mehr, der die Studies bei identischer Trial-Struktur korreliert haette). ``seed is None`` (kein
    Determinismus verlangt) bleibt ``None`` (Optuna waehlt einen echten Zufalls-Seed).

    Issue #1253 (GH #1123) — ``run_salt`` (Default ``None``) erweitert die Formel auf
    ``seed XOR stable_hash(study_name) XOR stable_hash(run_salt)``: ein Wiederholungslauf OHNE
    Salt zieht denselben TPE-Sampler-Pfad wie sein Vorlauf und ist damit eine reine KOPIE, keine
    unabhaengige STICHPROBE der Suchvarianz (Root-Cause #1253: ``seed_effective`` war eine reine
    Funktion von (``seed``, ``study_name``) — kein Lauf-Anteil, ``best_eligible_reward`` liess sich
    dadurch nie von einer stabilen Optimumsschaetzung gegenueber einer einzelnen TPE-Ziehung
    unterscheiden). ``run_salt=None`` (Default) ist BIT-IDENTISCH zum Pre-#1253-Verhalten (der
    zweite XOR-Term entfaellt vollstaendig, nicht nur numerisch neutral) — ein Wiederholungslauf
    OHNE ``--seed-salt``/``OPTIMIZER_SEED_SALT`` bleibt exakt reproduzierbar, wie vor diesem Fix.
    Mit gesetztem ``run_salt`` zieht JEDE Study desselben Laufs denselben Salt-Beitrag (sweep-weit
    konstant), aber XOR-verknuepft mit dem je Study bereits unterschiedlichen ``stable_hash(
    study_name)`` — der Salt allein macht daher NICHT alle Studies eines Laufs identisch
    verschoben (die Verschiebung ist studyabhaengig durch die bereits vorhandene XOR-Kette)."""
    if seed is None:
        return None
    import hashlib
    h = int.from_bytes(hashlib.blake2b(study_name.encode("utf-8"), digest_size=8).digest(), "big")
    effective = int(seed) ^ h
    if run_salt:
        h_salt = int.from_bytes(
            hashlib.blake2b(str(run_salt).encode("utf-8"), digest_size=8).digest(), "big")
        effective ^= h_salt
    # numpy.random.RandomState (Optuna-Sampler-Backend) verlangt einen Seed in [0, 2**32 - 1] — der
    # volle 64-Bit-Hash wird daher maskiert, NICHT nur XOR-verknuepft (sonst ValueError bei Studies,
    # deren Hash das obere Byte setzt).
    return effective & 0xFFFFFFFF


def _trial_number(idx: int, t) -> int:
    """Issue #753/#754 — Optuna-Trial-Nummer eines Trials, mit Fallback auf die Position in der
    aufrufenden Liste fuer Test-Doubles ohne ``.number`` (dort ist Erzeugungsreihenfolge == Nummer)."""
    n = getattr(t, "number", None)
    return int(n) if n is not None else idx


def _modelled_trials(completed: list, n_startup_trials: int) -> list:
    """Issue #753/#754 — die Teilmenge der ``completed``-Kohorte, die vom TPESampler MODELLIERT
    (nicht als Zufalls-Startup gezogen) wurde: Trial-Nummer >= n_startup_trials."""
    return [t for idx, t in enumerate(completed) if _trial_number(idx, t) >= int(n_startup_trials)]


def _best_completed_value(trials: list, *, direction: str = "maximize") -> float | None:
    """Issue #929 — ``study.best_value`` (Optuna-nativ) ist unter aktiver Constraint-Führung
    (``constraints_func``, Issue #612) auf FEASIBLE Trials beschränkt — bei ``oos_eligible=False``
    für JEDEN Trial (der #913-Zustand: ``oos_constraint_violations=(1.0,)`` überall) hat Optuna
    keinen feasiblen Kandidaten und ``study.best_value`` liefert ``None``/wirft, OBWOHL Optuna für
    jede Study intern einen besten ROHEN Reward-Wert kennt. ``best_value=null`` ist damit NICHT
    dasselbe wie 'kein eligibler Trial' — es verschluckt die einzige Grösse, an der ohne
    Eligibility ablesbar wäre, ob die Suche überhaupt einen Gradienten gefunden hat.

    Berechnet den besten ``trial.value`` direkt über ALLE ``TrialState.COMPLETE``-Trials
    (Optuna-Semantik für 'abgeschlossen'), UNABHÄNGIG von Constraint-Feasibility/Eligibility.
    ``None`` bei 0 abgeschlossenen Trials mit definiertem Wert (echte Leermenge, kein
    Constraint-Artefakt)."""
    values = [
        float(t.value) for t in trials
        if getattr(t, "state", None) == optuna.trial.TrialState.COMPLETE
        and isinstance(getattr(t, "value", None), (int, float))
    ]
    if not values:
        return None
    return max(values) if direction == "maximize" else min(values)


def _best_completed_trial_number(trials: list, *, direction: str = "maximize") -> int | None:
    """Issue #929 Fix 2 — dieselbe constraint-unabhängige Auswahl wie ``_best_completed_value``,
    aber die TRIAL-NUMMER statt des Werts: macht ein Study-Ergebnis OHNE Promotion trotzdem
    inspizierbar (``explain_trial.py`` braucht eine konkrete Trial-Referenz, ``study.best_trial``
    ist unter aktiver Constraint-Führung derselben Feasibility-Blindheit unterworfen wie
    ``study.best_value``)."""
    candidates = [
        (float(t.value), getattr(t, "number", None)) for t in trials
        if getattr(t, "state", None) == optuna.trial.TrialState.COMPLETE
        and isinstance(getattr(t, "value", None), (int, float))
    ]
    if not candidates:
        return None
    best = max(candidates) if direction == "maximize" else min(candidates)
    return best[1]


_STOP_REASONS = frozenset({
    "BUDGET_EXHAUSTED", "STRUCTURAL_ZERO_ELIGIBLE", "STRUCTURAL_ALL_UNEVALUABLE",
    "NO_GRADIENT_SIGNAL", "EXCEPTION", "UNKNOWN_INCOMPLETE",
})


def compute_budget_execution(trials: list, *, n_trials_budget: int | None,
                             n_startup_trials: int | None,
                             study_user_attrs: dict | None = None,
                             run_id: str | None = None) -> dict:
    """Issue #770 — der Budget-Ausfuehrungsgrad als ERSTKLASSIGE, EINMALIG berechnete Study-Kennzahl,
    gemeinsam genutzt von ``_emit_study_summary`` (Live-Event) UND ``report._study_record``
    (persistierter Report) — dieselbe Zahl an beiden Stellen statt zweier potenziell divergierender
    Rekonstruktionen (dieselbe Lektion wie #670: eine Kennzahl, eine Quelle).

    Root-Cause #770: weder das ``optimizer_study_completed``-Event noch der ``#742``-Report trugen
    ein Feld, das "wie viel des konfigurierten Budgets wurde tatsaechlich ausgefuehrt" beantwortet —
    die #768-Luecke (44,2 % statt 100 %) blieb nach dem #753-Merge deshalb unbemerkt (das Symptom
    "Study wurde frueh gestoppt" sah nach beabsichtigtem Verhalten aus).

    Issue #1015 (Katalog #858, Fix Punkt 1) — Root-Cause eines ZWEITEN, entgegengesetzten Defekts:
    ``trials`` ist typischerweise ``study.trials`` — die GESAMTE SQLite-Historie der Study, nicht
    nur die dieses ``sweep.run_per_symbol_sweep``-Laufs. Wurde eine Study zwischen zwei Läufen
    NICHT gepurged (z. B. weil ``reward_semantics_version``/etc. unverändert blieben), zählt
    ``n_completed`` Trials VORANGEGANGENER Läufe mit — beobachtet wurde eine Median-
    Budgetausführung von 362,1 % (7280 von 1940 Trials) bei Faktor-3,92-Diskrepanzen zwischen
    ``n_trials``/den plateau-/gate-Zählern. ``run_id`` (Default ``None``, bit-identisch zum
    Pre-Fix-Verhalten): wenn gesetzt, werden ausschliesslich Trials gezählt, deren
    ``user_attrs['run_id']`` diesem Wert entspricht (der Stempel, den ``make_symbol_objective``
    seit #1015 setzt — Legacy-Trials ohne den Stempel fallen dabei ersatzlos heraus, nicht
    fälschlich als "dieser Lauf" mit). ``n_trials_total_study`` bleibt UNABHÄNGIG davon die volle
    Study-Zählung — eine grosse Lücke zwischen beiden Zahlen macht eine ungepurgte Study sichtbar,
    statt sie stillschweigend in ``budget_executed_fraction`` zu verstecken.

    Issue #1027 (Katalog #866) — Root-Cause: der #1015-Fix fuehrte ``run_id`` ein, erreichte aber nur
    2 von 5 Aufrufstellen (Report + Live-Event); die drei entscheidungstragenden Aufrufer (Promotion-
    Route ``global_default_on_symbol`` in ``confirm.py``, Denylist-/Bounds-Override-Rueckschrieb in
    ``run_optimization.py``) blieben ungefiltert und erhielten damit eine ANDERE Zahl derselben
    Kennzahl als Bericht/Invariante fuer dieselbe Study. ``budget_executed_fraction_all_runs`` macht
    die ungefilterte Zahl an JEDER Aufrufstelle EXPLIZIT verfuegbar, statt sie ueber einen weggelassenen
    ``run_id``-Parameter versehentlich zu erben — ein Aufrufer, der die All-Runs-Semantik tatsaechlich
    braucht, waehlt sie jetzt bewusst, statt sie stillschweigend zu bekommen.

    Issue #1026 (Katalog #866) — ``stop_reason == 'EXCEPTION'`` war bislang der ``else``-Zweig dieser
    Fallunterscheidung: JEDE Study, die weder ein Plateau-Flag noch ihr volles Budget zeigte, wurde
    als abgestuerzt gemeldet — auch eine Study, die ihr Budget EXAKT ausgefuehrt hatte, aber (durch
    den #1025-Defekt) keinen ``run_id``-Stempel auf ihren Trials trug. ``EXCEPTION`` wird jetzt nur
    noch gemeldet, wenn ``study_user_attrs['n_trials_exception']`` (von ``_optimize_symbol_impl``
    tatsaechlich gezaehlte, von ``study.optimize(..., catch=...)`` gefangene Exceptions) > 0 ist;
    andernfalls ``UNKNOWN_INCOMPLETE`` — eine ehrliche Restkategorie statt einer behaupteten Ursache.

    Rueckgabe: ``{n_trials_budgeted, n_trials_completed, n_trials_total_study,
    budget_executed_fraction, budget_executed_fraction_all_runs, stop_reason,
    n_modelled_trials_completed, n_trials_exception, exception_types}``.
    ``budget_executed_fraction`` ist ``None``, wenn kein Budget bekannt ist (z. B. globaler Pfad/
    Legacy-Tests ohne ``n_trials_budget``-User-Attr) — kein stiller Default, der eine Luecke
    verdeckt."""
    attrs = study_user_attrs or {}
    n_trials_total_study = len(trials)
    run_trials = trials
    if run_id is not None:
        run_trials = [t for t in trials if getattr(t, "user_attrs", {}).get("run_id") == run_id]
    n_completed = len(run_trials)
    n_completed_all_runs = len(trials)
    n_modelled = len(_modelled_trials(run_trials, int(n_startup_trials) if n_startup_trials is not None else 0))
    budgeted = None
    if n_trials_budget is not None:
        try:
            budgeted = int(n_trials_budget)
        except (TypeError, ValueError):
            budgeted = None
    fraction = (n_completed / budgeted) if budgeted else None
    fraction_all_runs = (n_completed_all_runs / budgeted) if budgeted else None
    n_trials_exception = int(attrs.get("n_trials_exception") or 0)
    exception_types = attrs.get("exception_types") or {}
    if attrs.get("floor_plateau_warned"):
        stop_reason = "STRUCTURAL_ALL_UNEVALUABLE"
    elif attrs.get("zero_eligible_plateau_warned"):
        stop_reason = "STRUCTURAL_ZERO_ELIGIBLE"
    elif budgeted is None or n_completed >= budgeted:
        stop_reason = "BUDGET_EXHAUSTED"
    elif n_trials_exception > 0:
        # Issue #1026 — nur EIN Codepfad zaehlt tatsaechlich gefangene Exceptions
        # (``_optimize_symbol_impl``); nur diese Zaehlung darf die Ursachenbehauptung tragen.
        stop_reason = "EXCEPTION"
    else:
        # Issue #1026 — weder ein Plateau-Flag noch das volle Budget noch eine gezaehlte Exception:
        # die Study wurde nicht im laufenden Prozess dieses Laufs zu Ende gefuehrt (z. B. ``run_id``
        # fehlt auf ihren Trials, #1025). Ehrliche Restkategorie statt einer unbelegten Ursache.
        stop_reason = "UNKNOWN_INCOMPLETE"
    return {
        "n_trials_budgeted": budgeted,
        "n_trials_completed": n_completed,
        "n_trials_total_study": n_trials_total_study,
        "budget_executed_fraction": round(fraction, 4) if fraction is not None else None,
        "budget_executed_fraction_all_runs": (
            round(fraction_all_runs, 4) if fraction_all_runs is not None else None),
        "stop_reason": stop_reason,
        "n_modelled_trials_completed": n_modelled,
        "n_trials_exception": n_trials_exception,
        "exception_types": dict(exception_types),
    }


def _trial_constraint_violation(t) -> float | None:
    """Issue #753/#754 — die im Objective gestempelte ``oos_constraint_violations``-Tupel (#612/#635,
    ``<= 0`` = feasible) EINES Trials auf einen Skalar (Summe der Komponenten) reduziert. ``None``,
    wenn der Stempel fehlt (Pruned/Legacy/Test-Fixture ohne Constraint-Telemetrie)."""
    v = getattr(t, "user_attrs", {}).get("oos_constraint_violations")
    if not v:
        return None
    try:
        return float(sum(v))
    except (TypeError, ValueError):
        return None


def _constraint_violation_progress(trials) -> tuple[float | None, float | None, float | None]:
    """Issue #753/#754 — minimale Constraint-Verletzung ueber die erste/zweite Haelfte einer
    (typischerweise: MODELLIERTEN) Trial-Kohorte, plus die relative Verbesserung dazwischen.
    ``constraint_improvement_rate = (min_first - min_last) / min_first`` — misst "naehert sich der
    Sampler der feasiblen Region an?", unabhaengig davon, ob die feasible Region je erreicht wurde
    (im Gegensatz zur reward-basierten Streuung, die eine NICHT-LEERE feasible Region voraussetzt).
    ``(None, None, None)``, wenn keine Trial-Constraint-Telemetrie vorliegt."""
    values = [v for v in (_trial_constraint_violation(t) for t in trials) if v is not None]
    if not values:
        return None, None, None
    half = max(1, len(values) // 2)
    first = min(values[:half])
    tail = values[half:]
    last = min(tail) if tail else first
    rate = (first - last) / first if first > 0 else 0.0
    return first, last, rate


def plateau_stop_missed_probability(m_modelled: int, remaining_budget: int) -> tuple[float, float]:
    """Issue #806 — sequentielles Abbruchkriterium (Rule of Three) fuer das ZERO_ELIGIBLE-Plateau,
    statt einer festen Trialzahl ohne Fehlermodell (``derive_plateau_min_modelled_trials`` selbst
    ist ein Skalierungsgesetz, keine statistische Aussage — Root-Cause #806: die Frage "kann ich
    jetzt schliessen, dass es keinen eligiblen Punkt gibt?" ist eine WAHRSCHEINLICHKEITSFRAGE, keine
    Budgetfrage, siehe HEAD ~55 % Budget-Plateau ueber alle Dimensionen).

    Bei 0 eligiblen Trials aus ``m_modelled`` modellierten Versuchen ist die obere 95-%-Konfidenz-
    grenze fuer die (unbekannte) Eligibility-Rate ``p_hi = 3/m_modelled`` (Rule of Three). Mit
    ``remaining_budget`` (``r``) verbleibenden Trials ist
    ``P(mindestens 1 eligibler Trial im Rest) ≈ 1 − (1 − p_hi)^r`` die Wahrscheinlichkeit, dass ein
    Weiterlaufen noch etwas findet.

    Rueckgabe ``(p_hi, missed_probability)`` mit ``missed_probability`` genau dieser Grösse
    (trotz des Namens des zugehoerigen Config-Keys ``plateau_stop_max_missed_probability`` — der
    Abbruch feuert, sobald sie UNTER die Schranke faellt, siehe Akzeptanzkriterium #806). Rein,
    deterministisch. ``m_modelled <= 0`` ⇒ ``(1.0, 1.0)`` (keine Information ⇒ niemals abbrechen).
    ``remaining_budget <= 0`` ⇒ ``missed_probability = 0.0`` (kein Restbudget mehr, in dem noch
    etwas gefunden werden koennte)."""
    if m_modelled <= 0:
        return 1.0, 1.0
    p_hi = min(1.0, 3.0 / m_modelled)
    if remaining_budget <= 0:
        return p_hi, 0.0
    return p_hi, 1.0 - (1.0 - p_hi) ** remaining_budget


def plateau_stop_expected_yield(m_modelled: int, remaining_budget: int) -> tuple[float, float]:
    """Issue #925 (Pitfall #300) — geschlossene Alternative zu ``plateau_stop_missed_probability``:
    dieselbe Rule-of-Three-Obergrenze ``p_hi = 3/m_modelled``, aber als ERWARTUNGSWERT statt als
    Risiko formuliert. ``expected_yield = p_hi · remaining_budget`` ist die erwartete Zahl noch zu
    findender eligibler Trials im Restbudget.

    Root-Cause #925 (bewiesen in geschlossener Form): ``missed_probability`` steht in ``r`` UND
    ``m`` monoton, aber ``r`` (das Restbudget) erscheint zugleich im NENNER des Risikos UND ist der
    Ertrag des Abbruchs — ein Kriterium, dessen Risikoterm das gesparte Restbudget selbst enthält,
    kann per Konstruktion erst feuern, wenn kaum noch etwas zu sparen ist (bewiesen: der Stopp kann
    unter ``missed_probability`` hoechstens ~1,43 % des Budgets einsparen, unabhaengig von den
    konkreten Parametern). ``expected_yield`` trennt Ertrag und Risiko: der Abbruch feuert, sobald
    der ERWARTETE Gewinn (nicht die Eintrittswahrscheinlichkeit EINES Treffers) unter die
    Opportunitaetskosten faellt (``plateau_stop_min_expected_eligible``, Default 0.5) — bei
    ``m = plateau_min_modelled_trials`` (typischerweise 48) feuert der Abbruch dann bei
    ``r < m/6 ≈ 8``, also nach ~57 statt ~99 Trials (43 % Ersparnis statt ~1 %).

    Rueckgabe ``(p_hi, expected_yield)``, rein, deterministisch. ``m_modelled <= 0`` ⇒
    ``(1.0, inf)`` (keine Information ⇒ niemals abbrechen, konsistent zu
    ``plateau_stop_missed_probability``). ``remaining_budget <= 0`` ⇒ ``expected_yield = 0.0``."""
    if m_modelled <= 0:
        return 1.0, float("inf")
    p_hi = min(1.0, 3.0 / m_modelled)
    if remaining_budget <= 0:
        return p_hi, 0.0
    return p_hi, p_hi * remaining_budget


def plateau_stop_clopper_pearson(m_modelled: int, remaining_budget: int, *,
                                 alpha: float = 0.05) -> tuple[float, float]:
    """Issue #953 (Katalog C, P1) — die EXAKTE einseitige Clopper-Pearson-Obergrenze für die
    Eligibility-Rate bei 0 Erfolgen aus ``m_modelled`` Versuchen: ``p_hi(t) = 1 - alpha^(1/t)``,
    statt der Rule-of-Three-NÄHERUNG ``3/m`` (``plateau_stop_missed_probability``/
    ``plateau_stop_expected_yield``, beide implizit ``alpha≈0.05``). Erwarteter Restertrag
    ``expected_yield = p_hi(t) * (N - t)``; Abbruch, sobald dieser Wert unter 1 fällt UND
    ``constraint_improvement_rate <= 0`` (siehe ``floor_plateau_callback``s ``clopper_pearson``-
    Zweig) — die Regel hat keinen freien Parameter ausser ``alpha``, ist monoton in ``t``/``N``
    und passt sich automatisch an kleine Budgets an.

    Additiv/opt-in (``plateau_stop_mode='clopper_pearson'``, siehe ``optimizer.json
    ['plateau_stop_alpha']``, Default 0.05) — der produktive Default bleibt ``'expected_yield'``
    (#925, empirisch mit 43 % Budget-Ersparnis validiert); diese Funktion ist die literaturgetreue
    Umsetzung des #953-Vorschlags für Operatoren, die den Standardschätzer bewusst gegen die exakte
    Formel tauschen wollen, ohne den bereits validierten Default zu verändern.

    Rückgabe ``(p_hi, expected_yield)``, rein, deterministisch. ``m_modelled <= 0`` ⇒
    ``(1.0, inf)`` (keine Information ⇒ niemals abbrechen). ``remaining_budget <= 0`` ⇒
    ``expected_yield = 0.0``."""
    if m_modelled <= 0:
        return 1.0, float("inf")
    p_hi = 1.0 - float(alpha) ** (1.0 / m_modelled)
    if remaining_budget <= 0:
        return p_hi, 0.0
    return p_hi, p_hi * remaining_budget


def floor_plateau_callback(study, trial, *, weights: dict | None = None,
                           n_startup_trials: int | None = None, eps: float = 1e-6,
                           logger: logging.Logger | None = None,
                           stop_on_plateau: bool = False,
                           strategy: str | None = None, symbol: str | None = None,
                           run_id: str | None = None) -> None:
    """Issue #409/#413/#456 (P1/P2) — Guard gegen den Unevaluable-Floor-Kollaps (Pitfall #75).

    Optuna-Callback (Signatur ``(study, trial)``; ``weights``/``n_startup_trials``/``logger``/
    ``stop_on_plateau`` sind rein fuer Tests/DI und werden in Produktion aus der Config gebunden).
    Warnt EINMAL pro Study, sobald der Sweep fuer ein Symbol nichts Promotbares erzeugt — statt ihn
    als teuren Zufallsgenerator (Zero-Gradient) weiterlaufen zu lassen.

    Issue #456 — ``stop_on_plateau`` (Opt-in, Default ``False``): Ist es ``True`` und ein Plateau
    erkannt, ruft der Guard ZUSAETZLICH ``study.stop()`` (in BEIDEN Zweigen, crash-sicher), sodass
    die als aussichtslos erkannte Suche nicht die restlichen ~84 Trials verschwendet (~30 min pro
    Floor-Symbol). Die **Produktion bindet ``True``**; der Default ``False`` haelt alle Bestands-
    Tests mit Fake-Study (ohne ``.stop()``) unveraendert gruen. **Observability-Invariante bleibt:**
    der Guard aendert weiterhin NIE eine Reward- oder Promotion-Entscheidung — er beendet lediglich
    eine bereits als aussichtslos erkannte Suche frueher.

    Issue #413 — PRIMAERER Indikator ist ``oos_evaluated`` (Per-Trial-User-Attr aus
    ``make_symbol_objective``), NICHT die Reward-Wert-Gleichheit: Da #406/#407 unevaluable Trials
    ABSICHTLICH unter −9.75 verteilen (Gradient), ist das alte Praedikat ``all(abs(value−floor)<eps)``
    im v3-Shaping-Regime strukturell unerfuellbar (toter Code, der den realen Sub-Floor-Bereich
    −9.85…−9.93 nie trifft). Der Guard feuert jetzt, wenn KEIN abgeschlossener Trial je evaluable war.
    Fehlt das User-Attr (alte Studies / globaler ``make_objective``-Pfad), greift der Legacy-Wert-Guard
    (Floor = ``penalty_unevaluable_oos + unevaluable_shaping_span``) — kein False-Positive."""
    if weights is None:
        cfg_path = config_dir() / "optimizer.json"
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                weights = json.load(f) or {}
        except (OSError, ValueError):
            weights = {}
    if n_startup_trials is None:
        n_startup_trials = int(weights.get("n_startup_trials", 16))
    if logger is None:
        logger = logging.getLogger("optimizer")

    # Issue #488 — Zustandslos via Persistenzschicht abfragen (Parallel-Safety)
    get_trials = getattr(study, "get_trials", None)
    if get_trials:
        completed = [t for t in get_trials(states=[optuna.trial.TrialState.COMPLETE])
                     if t.value is not None]
    else:
        # Fallback for FakeStudy in tests
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None]
    # Issue #1050/#1199 (Katalog #1196-1221, Pitfall #430-Klasse) — ``completed`` (oben) ist
    # STORE-SCOPED (``get_trials``/``study.trials`` lesen die GESAMTE Study-Historie, ueber ALLE
    # ``run_id``s hinweg, die je auf diesem Store liefen — der Plateau-Stop-Mechanismus BRAUCHT
    # diese volle Sicht bewusst fuer seine Abbruch-Entscheidung, siehe #925/#805-Kommentare oben).
    # ``completed_run`` ist die dazu PARALLELE, RUN-SCOPED Teilmenge (nur Trials mit
    # ``run_id``-Stempel == diesem Lauf) — AUSSCHLIESSLICH fuer die ``plateau_n_evaluated_run``/
    # ``plateau_counter_breakdown_run``-Telemetrie unten, die scope-konsistent mit dem run-scoped
    # ``n_trials``-Feld des Reports sein muss (Scope gehoert in den Feldnamen, ``_run``/``_store``).
    # Kein ``run_id`` uebergeben (Legacy-Aufrufer) ⇒ ``completed_run`` faellt auf die volle Menge
    # zurueck (bit-identisch zum Vorher-Verhalten, keine Regression fuer Aufrufer ohne run_id).
    completed_run = (
        [t for t in completed if getattr(t, "user_attrs", {}).get("run_id") == run_id]
        if run_id is not None else completed)
    # Issue #805 — ERSETZT das entfernte ``floor_plateau_k`` (dritte Wiederkehr derselben
    # Fehlerklasse: #488 -> #753 -> #769 -> #805): eine dimensionsskalierte Mindestzahl MODELLIERTER
    # Trials statt einer flachen (und auf 0 stehen gebliebenen) Konstante — siehe
    # ``derive_structural_min_modelled_trials``-Docstring fuer die volle Root-Cause.
    structural_extra = derive_structural_min_modelled_trials(strategy, weights or {})
    # Issue #753 — ZWEI GETRENNTE Schwellen statt einer gemeinsamen Kopplung an n_startup_trials.
    # Root-Cause #753: der alte Guard band den ZERO_ELIGIBLE-Abbruch an dieselbe Grösse
    # (n_startup_trials + K), die bei TPESampler(n_startup_trials=...) die UNTERE Grenze markiert, ab
    # der der Sampler ueberhaupt erst modelliert (die ersten n_startup_trials Trials sind reine
    # Zufallsziehungen). Der Guard toetete die Study damit GENAU an dem Punkt, an dem die
    # Bayes-Optimierung beginnen wuerde — "0 von n_startup_trials Zufallsziehungen feasible" ist KEINE
    # Aussage ueber den Suchraum (die feasible Region ist per Konstruktion klein; ein constrained TPE
    # ist genau dafuer da, sie ueber mehr als eine Zufallsstichprobe zu finden). Der
    # STRUCTURAL_ALL_UNEVALUABLE-Zweig ist davon NICHT betroffen (Aussage ueber Datengeometrie/
    # Trade-Frequenz, nicht Signalqualitaet) und bleibt bei n_startup_trials + structural_extra —
    # NUR der additive Zuschlag selbst ist seit #805 nie mehr 0 (siehe dort).
    _pmt_raw = weights.get("plateau_min_modelled_trials") if weights else None
    try:
        plateau_min_modelled_trials = int(_pmt_raw) if _pmt_raw is not None else None
    except (TypeError, ValueError):
        plateau_min_modelled_trials = None
    if plateau_min_modelled_trials is None:
        # Fehlender/ungueltiger Key ⇒ Fallback max(32, 2·n_startup_trials) — NICHT 0 (der Legacy-Wert
        # IST der Bug, siehe #753 Root-Cause 1).
        plateau_min_modelled_trials = max(32, 2 * int(n_startup_trials))
    # Issue #768 — die FLACHE Basis oben an die effektive Suchraum-Dimension koppeln (dieselbe
    # Fehlerklasse wie #753 selbst, eine Ebene hoeher): ohne diese Kopplung faellt der vor dem
    # Zero-Eligible-Urteil ausgefuehrte Budgetanteil monoton mit dim (64% bei dim=2, 32% bei dim=14).
    plateau_min_modelled_trials = derive_plateau_min_modelled_trials(
        strategy, plateau_min_modelled_trials, weights or {})
    # Issue #929 Fix 3 — als Study-User-Attr gestempelt, damit report._study_record die exakte,
    # zur Laufzeit dieser Study verwendete Schwelle liest (statt sie erneut herzuleiten) —
    # Eingangsgrösse für invariants.check_search_made_progress.
    try:
        study.set_user_attr("plateau_min_modelled_trials", int(plateau_min_modelled_trials))
    except Exception:
        pass
    min_for_structural = max(1, int(n_startup_trials)) + structural_extra
    min_for_zero_eligible = max(1, int(n_startup_trials)) + plateau_min_modelled_trials
    # Issue #768/#805 — Obergrenze gegen Budget-Ueberschreitung: der Guard darf nie NACH dem
    # regulaeren Budget-Ende urteilen. ``n_trials_budget`` wird von ``optimize_symbol`` als
    # Study-User-Attr gestempelt (VOR dem ersten Callback-Aufruf); fehlt es (z. B. globaler
    # Pfad/Legacy-Tests), bleibt die Schwelle ungedeckelt (bit-identisch zu HEAD).
    _n_trials_budget = (getattr(study, "user_attrs", None) or {}).get("n_trials_budget")
    if _n_trials_budget is not None:
        try:
            min_for_zero_eligible = min(min_for_zero_eligible, int(_n_trials_budget))
            min_for_structural = min(min_for_structural, int(_n_trials_budget))
        except (TypeError, ValueError):
            pass
    # Issue #805 — dieser Vorab-Kurzschluss gate(t) AUCH den Legacy-Fallback-Zweig weiter unten
    # (kein oos_evaluated-Attr, Issue #409), der KEINE Aussage ueber "modellierte" Trials trifft und
    # daher weiterhin bei genau ``n_startup_trials`` urteilen darf/soll (unveraendert seit #409) —
    # NICHT erst beim (seit #805 stets >= n_startup_trials + 1) hoeheren ``min_for_structural``.
    if len(completed) < min(int(n_startup_trials), min_for_structural, min_for_zero_eligible):
        return
    if study.user_attrs.get("floor_plateau_warned") or study.user_attrs.get("zero_eligible_plateau_warned"):
        return

    # Issue #753 — die "modellierten" Trials (Index >= n_startup_trials, ueber die Optuna-Trial-Nummer,
    # NICHT ueber die Position in ``completed`` — ausgefallene/gepruente Trials duerfen die Zaehlung
    # nicht verschieben). FakeTrial-Doubles ohne ``.number`` (Bestandstests) fallen auf die
    # Listenposition zurueck (dort ist Erzeugungsreihenfolge == Trial-Nummer).
    modelled_completed = _modelled_trials(completed, int(n_startup_trials))

    # Issue #753 (Umsetzung 5) — min./max. Constraint-Verletzung ueber die erste/zweite Haelfte der
    # MODELLIERTEN Trials — macht im STUDY_EARLY_STOP-Event nachvollziehbar, ob der TPE der feasiblen
    # Region naeher kommt (Stagnation) oder ob die Suche schlicht nie stattfand (0 modellierte Trials).
    # Issue #806 — ``constraint_improvement_rate`` (drittes Element, vorher verworfen als ``_``) ist
    # der Constraint-Arm des sequentiellen Plateau-Stopps unten: naehert sich der Sampler der
    # feasiblen Region an, wird der Rule-of-Three-Abbruch unterdrueckt.
    min_constraint_violation_first, min_constraint_violation_last, constraint_improvement_rate = (
        _constraint_violation_progress(modelled_completed))

    # Issue #413 — evaluable-basierter Primaer-Guard. Tragen die Trials das oos_evaluated-Attr, ist
    # „kein Trial je evaluable" der korrekte Kollaps-Indikator (unabhaengig vom geshapeten Reward-Wert).
    evaluated_flags = [getattr(t, "user_attrs", {}).get("oos_evaluated") for t in completed]
    if any(f is not None for f in evaluated_flags):
        # Issue #753/#769 — der STRUCTURAL_ALL_UNEVALUABLE-Zweig urteilt ab ``min_for_structural``
        # (n_startup_trials + K) NUR, wenn die Nicht-Evaluierbarkeit PARAMETERUNABHAENGIG ist
        # (binding_cause=='signal_absent', echte Datengeometrie/Indikator-Degeneration). Ist sie
        # PARAMETERABHAENGIG ('signal_sparse'/'hold_duration' — die Strategie feuert, erreicht aber
        # oos_min_trades nicht, eine Funktion tunebarer Frequenz-/Haltedauer-Parameter), gilt
        # dieselbe hoehere Modellierungsschwelle wie im ZERO_ELIGIBLE-Zweig (min_for_zero_eligible,
        # #768) — Root-Cause #769: die alte, unbedingte Kopplung an min_for_structural urteilte auf
        # NULL TPE-modellierten Trials (n_startup_trials + K=0), obwohl AGENTS.md-Pitfall #219/#220
        # genau das als behoben markiert. Die Diagnose muss daher VOR der Abbruch-Entscheidung
        # stehen, nicht erst danach.
        if all(f is False for f in evaluated_flags) and len(completed) >= min_for_structural:
            # Issue #669 — Suchraum-Diagnose-Artefakt: trennt Signal-Frequenz von Haltedauer als
            # bindende Ursache des STRUCTURAL_ALL_UNEVALUABLE-Kollapses (statt nur "kein Trial je
            # evaluable" zu melden) — macht sichtbar, OB eine Bounds-Kalibrierung (spaces.py) hier
            # überhaupt greifen könnte.
            from automation.optimizer.sweep_diagnostics import diagnose_trade_frequency
            _oos_min_trades = 20
            try:
                _tcfg_path = config_dir() / "tournament.json"
                if _tcfg_path.exists():
                    _oos_min_trades = int(
                        (json.loads(_tcfg_path.read_text("utf-8")) or {}).get("oos_min_trades", 20))
            except Exception:
                pass
            _trial_dicts = [{
                "oos_evaluated": getattr(t, "user_attrs", {}).get("oos_evaluated"),
                "oos_eligible": getattr(t, "user_attrs", {}).get("oos_eligible"),
                "oos_total_trades": getattr(t, "user_attrs", {}).get("oos_total_trades"),
                "is_total_trades": getattr(t, "user_attrs", {}).get("is_total_trades"),
                "hit_trade_cap": getattr(t, "user_attrs", {}).get("hit_trade_cap"),
            } for t in completed]
            diagnosis = diagnose_trade_frequency(_trial_dicts, oos_min_trades=_oos_min_trades)
            # Issue #769 — 'signal_absent' (parameterunabhaengig) bleibt bei der engeren
            # min_for_structural-Schwelle; jede andere Ursache erfordert min_for_zero_eligible.
            required_for_structural = (
                min_for_structural if diagnosis["binding_cause"] == "signal_absent"
                else min_for_zero_eligible)
            if len(completed) < required_for_structural:
                return
            study.set_user_attr("floor_plateau_warned", True)
            logger.warning(
                "🚨 Floor-Plateau erkannt: kein evaluable Trial nach %d Trials — das Symbol erzeugt "
                "nie evaluierbare OOS-Trades (Pitfall #75-Klasse). Bindende Ursache (#669): %s "
                "(median_is_trades=%s, frac_hit_trade_cap=%s). Pruefe Daten-Suffizienz, "
                "OOS-Trade-Frequenz und Micro-Sizing-/min_trades-Gate. Per-Symbol-Tuning fuer dieses "
                "Symbol ist derzeit ein No-Op.",
                len(completed), diagnosis["binding_cause"], diagnosis["median_is_trades"],
                diagnosis["frac_hit_trade_cap"],
            )
            emit_execution_event(logger, "STRUCTURAL_ALL_UNEVALUABLE", {
                "n_trials": len(completed), **diagnosis,
            })

            # Issue #681 — schliesst die Diagnose zu einer Aktion: die Empfehlung (denylist /
            # search_space_override / none) wird in den AUTOMATISCH gepflegten Cache geschrieben
            # (NICHT die menschlich-kuratierte symbol_strategy_denylist.json selbst) — der
            # naechste Lauf überspringt ein 'denylist'-empfohlenes Paar automatisch
            # (enumerate_tunable_pairs), waehrend die permanente Governance-Entscheidung weiterhin
            # per PR getroffen wird. Nur wirksam, wenn strategy/symbol übergeben wurden (Production-
            # Bindung in optimize_symbol; Legacy-/Test-Aufrufer ohne diese Kwargs bleiben No-Op).
            if strategy is not None and symbol is not None:
                try:
                    from automation.optimizer.sweep_diagnostics import (
                        recommend_diagnosis_action, record_diagnosed_pair,
                        has_existing_search_space_override, load_diagnosed_pairs_cache,
                        propose_bounds_widening, study_fingerprint,
                    )
                    # Issue #699 — Eskalations-Check: wurde für dieses Paar bereits in einem
                    # VORHERIGEN Lauf 'search_space_override' empfohlen (und nichts hat sich seither
                    # geändert), diesen Lauf auf 'denylist' eskalieren statt die identische
                    # Empfehlung endlos zu wiederholen (siehe recommend_diagnosis_action-Docstring).
                    _prior = load_diagnosed_pairs_cache().get((strategy, symbol))
                    # Issue #761 — konkreter Bounds-Vorschlag aus der Trial-Kohorte (Richtung, in
                    # der oos_total_trades-Proxy is_total_trades mit dem Parameter steigt), statt
                    # nur der Empfehlung "probiere einen Override".
                    try:
                        _trial_param_rows = [{
                            "params": getattr(t, "params", None) or {},
                            "is_total_trades": getattr(t, "user_attrs", {}).get("is_total_trades"),
                        } for t in completed]
                        # Issue #777 — deklarativer Weitungsfaktor statt des Funktions-Parameter-
                        # Defaults (0.3); fehlt der Key ⇒ 0.3 (bit-identisch, Zero-Hardcoding).
                        _widen_fraction = float(weights.get("bounds_widening_factor", 0.3)) if weights else 0.3
                        _proposed_bounds = propose_bounds_widening(
                            _trial_param_rows, strategy, widen_fraction=_widen_fraction)
                    except Exception:
                        _proposed_bounds = {}
                    # Issue #778 — Evidenz fuer eine 'signal_absent'-Eskalation: das Budget DIESES
                    # Laufs (#770) UND wie oft dieselbe (action, binding_cause) bereits IN FOLGE
                    # bestaetigt wurde (aus dem Vorlauf-Cache-Eintrag, VOR diesem Lauf).
                    _budget_execution_for_diagnosis = compute_budget_execution(
                        completed, n_trials_budget=_n_trials_budget,
                        n_startup_trials=n_startup_trials, study_user_attrs=study.user_attrs,
                        run_id=run_id)
                    rec = recommend_diagnosis_action(
                        strategy, symbol, diagnosis,
                        # Issue #1296 (GH #1169, Katalog #1272-1297, P1) — dieselbe Groesse, die
                        # diagnose_trade_frequency bereits im diagnosis-Dict liefert (n_evaluable=0
                        # fuer JEDEN signal_sparse-Befund aus DIESER Quelle, siehe dortiger
                        # Docstring); Voraussetzung fuer die neue signal_sparse-Denylist-Eskalation.
                        n_evaluable=diagnosis.get("n_evaluable"),
                        has_existing_override=has_existing_search_space_override(strategy, symbol),
                        previously_recommended_override=bool(
                            _prior and _prior.get("action") == "search_space_override"),
                        proposed_bounds=_proposed_bounds,
                        budget_executed_fraction=_budget_execution_for_diagnosis["budget_executed_fraction"],
                        n_runs_confirmed=(
                            int(_prior.get("n_runs_confirmed", 0))
                            if _prior and _prior.get("binding_cause") == diagnosis.get("binding_cause")
                            else 0
                        ),
                        # Issue #829 — derselbe study.set_user_attr("floor_plateau_warned", True)-
                        # Aufruf (oben, VOR diesem Block) macht compute_budget_execution's
                        # stop_reason bereits zu 'STRUCTURAL_ALL_UNEVALUABLE'; dieser Wert BEWEIST,
                        # dass die Study ihre eigene len(completed) >= required_for_structural-
                        # Vorbedingung (Zeile 490 oben) bereits erfuellt hat.
                        stop_reason=_budget_execution_for_diagnosis["stop_reason"],
                        # Issue #911 — konfigurierbare Konsekutiv-Laeufe-Schwelle statt eines
                        # eingefrorenen Literals; simulation_semantics_version fuer den #911 Fix 2
                        # Gueltigkeitsstempel einer 'signal_quality'-Quarantaene.
                        max_consecutive_structural_runs=int(
                            (weights or {}).get("max_consecutive_structural_runs", 2)),
                        simulation_semantics_version=(weights or {}).get("simulation_semantics_version"),
                    )
                    # Issue #1090 (Katalog #923) — Fingerprint DIESER Study-Beobachtung: dedupliziert
                    # n_runs_confirmed gegen mehrfache record_diagnosed_pair-Aufrufe fuer denselben
                    # realen Trial-Datensatz (z. B. Nebenprozesse auf demselben Store, #1086).
                    rec["study_fingerprint"] = study_fingerprint(
                        getattr(study, "study_name", None),
                        study.user_attrs.get("study_started_at_utc"),
                        _budget_execution_for_diagnosis["n_trials_completed"],
                    )
                    record_diagnosed_pair(rec, run_id=run_id)
                except Exception:
                    logger.debug("Issue #681: diagnosis writeback fehlgeschlagen (non-fatal).", exc_info=True)

            # Issue #456 / #488 — aussichtslose Suche frueh beenden (nur Opt-in; crash-sicher).
            should_stop = stop_on_plateau or (
                weights and weights.get("structural_min_modelled_trials_per_dim") is not None)
            if should_stop:
                # Log JSON termination event explicitly exactly when stopping (only once).
                # Wait, study.set_user_attr("floor_plateau_warned", True) ensures this block runs ONCE.
                import json as _json
                logger.info("[JSON_EVENT] " + _json.dumps({
                    "event_type": "STUDY_EARLY_STOP",
                    "reason": "STRUCTURAL_ALL_UNEVALUABLE",
                    "current_trial": len(completed),
                    "startup_limit": max(1, int(n_startup_trials)),
                    # Issue #805 — ersetzt "k_limit" (floor_plateau_k, entfernt): der dimensions-
                    # skalierte additive Zuschlag auf n_startup_trials (nie mehr 0, siehe
                    # derive_structural_min_modelled_trials).
                    "structural_min_modelled_trials": structural_extra,
                    # Issue #753 — im STRUCTURAL_ALL_UNEVALUABLE-Zweig strukturell fast immer 0 (die
                    # Study kollabiert bereits VOR n_startup_trials abgeschlossenen modellierten Trials).
                    "n_modelled_trials": len(modelled_completed),
                    "min_constraint_violation_first": min_constraint_violation_first,
                    "min_constraint_violation_last": min_constraint_violation_last,
                }))
                _stop_study_safely(study, logger)
            return

        # Issue #656/#700 — ZERO-ELIGIBLE-PLATEAU: mindestens ein Trial WURDE evaluiert (oos_
        # evaluated=True, echte OOS-Backtests liefen durch) und traf tatsaechlich eine oos_eligible-
        # Determination, aber KEINER dieser evaluierten Trials war je eligible — ein STRUKTURELL
        # ANDERES Kollaps-Muster als Pitfall #75 (der #413-Guard oben, kein Trial je evaluable).
        #
        # Root-Cause #700: dieser Zweig verlangte vorher STRIKT ``all(evaluated_flags is True)`` —
        # ein GEMISCHTER Cohort (einige Trials evaluable, einige nicht, z. B. durch Trade-Cap-
        # Treffer/hohe Frequenz bei SqueezeBreakout) fiel dadurch durch BEIDE Netze (weder "kein
        # Trial evaluable" oben noch "alle Trials evaluable" hier) und verbrannte das VOLLE
        # 180-Trial-Budget, waehrend GapContinuation (zufaellig ein homogener 0-evaluable-Cohort)
        # korrekt bei 16 Trials stoppte. Fix: die Bedingung ist jetzt ausschliesslich
        # ``p_eligible(evaluierte Trials) == 0`` — UNABHAENGIG davon, ob ALLE oder nur EIN TEIL der
        # Trials evaluiert wurden (woertliches #700-Akzeptanzkriterium: ob gestoppt wird, haengt
        # NICHT an der binding_cause-Klassifikation, die nur die URSACHE telemetriert).
        eligible_flags_of_evaluated = [
            getattr(t, "user_attrs", {}).get("oos_eligible") for t in completed
            if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
        ]
        # Issue #753 (Umsetzung 3) — die ABBRUCH-ENTSCHEIDUNG wird AUSSCHLIESSLICH ueber die
        # MODELLIERTEN Trials (Index >= n_startup_trials) getroffen: die Zufalls-Startup-Phase darf
        # das Urteil "der Suchraum erzeugt strukturell keinen eligiblen Lauf" nicht mitbestimmen — das
        # waere exakt der #753-Fehlschluss (16 uniforme Ziehungen verfehlen die feasible Region per
        # Konstruktion; das ist erwartet, keine Aussage ueber den Suchraum). Telemetrie (n_evaluated,
        # median_oos_trades, ...) bleibt auf der VOLLEN Kohorte fuer Diagnose-Kontext.
        eligible_flags_of_evaluated_modelled = [
            getattr(t, "user_attrs", {}).get("oos_eligible") for t in modelled_completed
            if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
        ]
        if len(completed) < min_for_zero_eligible:
            return
        if (eligible_flags_of_evaluated_modelled
                and any(f is not None for f in eligible_flags_of_evaluated_modelled)
                and all(f is not True for f in eligible_flags_of_evaluated_modelled)):
            # Issue #806 — ``min_for_zero_eligible`` ist seither nur noch die UNTERGRENZE (der Test
            # darf nicht VOR einer Mindestmenge modellierter Trials greifen); OB tatsaechlich
            # abgebrochen wird, entscheidet ab hier ein sequentielles Kriterium (Rule of Three) statt
            # der festen Trialzahl selbst — Root-Cause #806: HEAD urteilte bei einem flachen,
            # dimensionsunabhaengigen ~55%-Budgetanteil ohne jedes Fehlermodell.
            m_modelled = len(modelled_completed)
            remaining_budget = None
            if _n_trials_budget is not None:
                try:
                    remaining_budget = max(0, int(_n_trials_budget) - len(completed))
                except (TypeError, ValueError):
                    remaining_budget = None
            p_hi = missed_probability = expected_yield = None
            # Issue #925 — plateau_stop_mode entscheidet, welches der beiden Kriterien den Abbruch
            # triggert. 'expected_yield' (Default) ersetzt das strukturell auf ~1,43 % Ersparnis
            # begrenzte 'missed_probability' (siehe plateau_stop_expected_yield-Docstring,
            # geschlossener Beweis). 'missed_probability' bleibt fuer Reproduktionslaeufe
            # verfuegbar. Validiert UNABHAENGIG von remaining_budget, damit eine Fehlkonfiguration
            # nicht erst sichtbar wird, sobald ein Budget bekannt ist.
            _plateau_stop_mode = (weights or {}).get("plateau_stop_mode", "expected_yield")
            if _plateau_stop_mode not in ("missed_probability", "expected_yield", "clopper_pearson"):
                raise ValueError(
                    f"optimizer.json['plateau_stop_mode']={_plateau_stop_mode!r} unbekannt — "
                    "erwartet 'missed_probability', 'expected_yield' oder 'clopper_pearson'.")
            if remaining_budget is not None:
                p_hi, missed_probability = plateau_stop_missed_probability(
                    m_modelled, remaining_budget)
                _, expected_yield = plateau_stop_expected_yield(m_modelled, remaining_budget)
                # Issue #806 — Constraint-Arm: naehert sich der Sampler der feasiblen Region an
                # (dieselbe Groesse wie in ``study_shows_gradient_signal``), wird der sequentielle
                # Abbruch unterdrueckt — genau der Fall, den Rule-of-Three allein nicht sieht.
                tau_c = float((weights or {}).get("tier_escalation_min_constraint_progress", 0.05))
                constraint_signal = (constraint_improvement_rate is not None
                                     and constraint_improvement_rate > tau_c)
                if _plateau_stop_mode == "expected_yield":
                    min_expected_eligible = float((weights or {}).get(
                        "plateau_stop_min_expected_eligible", 0.5))
                    stop_condition = expected_yield < min_expected_eligible
                elif _plateau_stop_mode == "clopper_pearson":
                    # Issue #953 — exakte Formel statt der Rule-of-Three-Naeherung; dieselbe
                    # constraint_signal-Unterdrueckung wie die beiden anderen Modi (Fix-Punkt
                    # "die zweite Klausel verhindert den Abbruch einer Study, die sich dem Gate
                    # messbar naehert").
                    _cp_alpha = float((weights or {}).get("plateau_stop_alpha", 0.05))
                    _, cp_expected_yield = plateau_stop_clopper_pearson(
                        m_modelled, remaining_budget, alpha=_cp_alpha)
                    expected_yield = cp_expected_yield
                    stop_condition = cp_expected_yield < 1.0
                else:
                    max_missed_probability = float((weights or {}).get(
                        "plateau_stop_max_missed_probability", 0.05))
                    # Trotz des Namens: der Abbruch feuert, sobald missed_probability UNTER die
                    # Schranke faellt (siehe plateau_stop_missed_probability-Docstring) —
                    # bit-identisch zum Pre-#925-Verhalten in diesem Modus.
                    stop_condition = missed_probability < max_missed_probability
                if not stop_condition or constraint_signal:
                    return
            study.set_user_attr("zero_eligible_plateau_warned", True)
            n_evaluated = len(eligible_flags_of_evaluated)
            study.set_user_attr("plateau_n_evaluated", n_evaluated)
            oos_trade_counts = [
                getattr(t, "user_attrs", {}).get("oos_total_trades") for t in completed
                if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
            ]
            oos_trade_counts = [int(c) for c in oos_trade_counts if c is not None]
            hit_cap_flags = [
                getattr(t, "user_attrs", {}).get("hit_trade_cap") for t in completed
                if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
            ]
            n_hit_cap = sum(1 for f in hit_cap_flags if f is True)
            median_oos_trades = statistics.median(oos_trade_counts) if oos_trade_counts else None

            # Issue #972 (Katalog B, Pitfall #304 in AGENTS.md) — Root-Cause der widersprüchlichen
            # "0/N Trials trafen die Haltedauer-/Trade-Cap-Grenze"-Meldung: ``n_hit_cap`` lief NUR
            # über die bereits als ``oos_evaluated=True`` ÜBERLEBENDEN Trials — genau die Trials, die
            # eine Zeitbox-Verletzung (#857) per Konstruktion NICHT mehr sein können (sie wurden
            # deswegen ja gerade auf ``oos_evaluated=False`` umgestempelt und verlassen damit VOR
            # diesem Zähler die Grundgesamtheit). Ein Zähler, dessen Grundgesamtheit durch exakt das
            # Kriterium vorgefiltert ist, das er messen soll, liefert IMMER null (Pitfall #304) — das
            # ist keine Aussage über die Zeitbox, sondern eine Tautologie. Die Zerlegung unten läuft
            # über ALLE ``completed`` Trials (die tatsächliche Grundgesamtheit) und nutzt die seit
            # #971 unverwechselbare ``is_rejection_detail``-Kategorie ``REJECT_OOS_TIMEBOX_VIOLATION``.
            _all_rejection_details = [
                getattr(t, "user_attrs", {}).get("is_rejection_detail") for t in completed
            ]
            _timebox_invalidated_count = sum(
                1 for r in _all_rejection_details if r == "REJECT_OOS_TIMEBOX_VIOLATION")
            plateau_counter_breakdown = {
                "invalidated_timebox": _timebox_invalidated_count,
                "invalidated_trade_cap": n_hit_cap,
                "discarded_is_gate": sum(
                    1 for r in _all_rejection_details if r == "REJECT_OOS_DISCARDED_BY_IS_GATE"),
                "window_unreachable": sum(
                    1 for r in _all_rejection_details if r == "REJECT_OOS_WINDOW_UNREACHABLE"),
                "not_evaluated": sum(
                    1 for r in _all_rejection_details
                    if r in ("REJECT_OOS_NOT_EVALUATED", "REJECT_OOS_INACTIVE")),
            }
            study.set_user_attr("plateau_counter_breakdown", plateau_counter_breakdown)

            # Issue #1050/#1199 (Katalog #1196-1221) — dieselben beiden Zaehler, ABER ueber
            # ``completed_run`` (RUN-SCOPED) statt ``completed`` (STORE-SCOPED) gebildet.
            # invariants.check_counter_partition_consistency vergleicht gegen ``n_trials``
            # (report._study_record, run-scoped) — ein Vergleich gegen die store-scoped Variante
            # war strukturell unerfuellbar (176 vs. 36 bei einem 5-fach warm-gestarteten Store,
            # #1198-Klasse: zwei Zaehler derselben Identitaet mit verschiedenem Scope). Die
            # store-scoped Felder oben bleiben UNVERAENDERT (sie tragen die tatsaechliche
            # Plateau-Stop-Entscheidung, siehe deren Kommentare) — diese hier sind rein additive
            # Berichts-/Invarianten-Telemetrie.
            _eligible_flags_of_evaluated_run = [
                getattr(t, "user_attrs", {}).get("oos_eligible") for t in completed_run
                if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
            ]
            n_evaluated_run = len(_eligible_flags_of_evaluated_run)
            study.set_user_attr("plateau_n_evaluated_run", n_evaluated_run)
            _hit_cap_flags_run = [
                getattr(t, "user_attrs", {}).get("hit_trade_cap") for t in completed_run
                if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
            ]
            n_hit_cap_run = sum(1 for f in _hit_cap_flags_run if f is True)
            _all_rejection_details_run = [
                getattr(t, "user_attrs", {}).get("is_rejection_detail") for t in completed_run
            ]
            plateau_counter_breakdown_run = {
                "invalidated_timebox": sum(
                    1 for r in _all_rejection_details_run if r == "REJECT_OOS_TIMEBOX_VIOLATION"),
                "invalidated_trade_cap": n_hit_cap_run,
                "discarded_is_gate": sum(
                    1 for r in _all_rejection_details_run
                    if r == "REJECT_OOS_DISCARDED_BY_IS_GATE"),
                "window_unreachable": sum(
                    1 for r in _all_rejection_details_run
                    if r == "REJECT_OOS_WINDOW_UNREACHABLE"),
                "not_evaluated": sum(
                    1 for r in _all_rejection_details_run
                    if r in ("REJECT_OOS_NOT_EVALUATED", "REJECT_OOS_INACTIVE")),
            }
            study.set_user_attr("plateau_counter_breakdown_run", plateau_counter_breakdown_run)

            # Issue #700 — per-16-Trial-Fenster p_eligible-Kurve (Diagnose-Akzeptanzkriterium):
            # unterscheidet TRANSIENTE (irgendwo zwischenzeitlich eligible Trials) von PERMANENTER
            # (jedes Fenster 0.0) Null-Eligibilitaet.
            from automation.optimizer.sweep_diagnostics import (
                eligibility_curve, resolve_ineligible_binding_cause)
            p_eligible_windows = eligibility_curve(
                [{"oos_eligible": getattr(t, "user_attrs", {}).get("oos_eligible")} for t in completed],
                window=16,
            )

            # Issue #926/#921 — 'signal_quality' war hier UNBEDINGT vergeben, sobald alle Trials
            # evaluiert wurden — eine nicht durchgefuehrte Messung (#917, undefinierter oos_psr)
            # wurde damit als negatives Messergebnis interpretiert. resolve_ineligible_binding_cause
            # trennt jetzt 'inference_unavailable' (#913-Klasse) und 'signal_sparse' (#921,
            # median_oos_total_trades <= 2) vom echten Qualitaetsurteil.
            _evaluated_trial_dicts = [
                {"is_rejection_detail": getattr(t, "user_attrs", {}).get("is_rejection_detail")}
                for t in completed if getattr(t, "user_attrs", {}).get("oos_evaluated") is True
            ]
            binding_cause, _binding_detail = resolve_ineligible_binding_cause(
                _evaluated_trial_dicts, median_oos_trades=median_oos_trades)

            logger.warning(
                "🚨 Zero-Eligible-Plateau erkannt: %d/%d Trials wurden evaluiert (echte OOS-"
                "Backtests), aber KEINER war oos_eligible — der Suchraum erzeugt strukturell "
                "keinen eligiblen Lauf (median oos_total_trades=%s, %d/%d ALLER Trials trafen die "
                "Haltedauer-/Trade-Cap-Grenze: %s). binding_cause=%s (#926). p_eligible je "
                "16-Trial-Fenster: %s. Suchraum-Bounds pruefen (spaces.py) ODER die Strategie fuer "
                "dieses Symbol/Tier deaktivieren, statt die restlichen Trials nutzlos "
                "durchlaufen zu lassen.",
                n_evaluated, len(completed), median_oos_trades,
                _timebox_invalidated_count + n_hit_cap, len(completed), plateau_counter_breakdown,
                binding_cause, p_eligible_windows,
            )
            import json as _json
            emit_execution_event(logger, "ZERO_ELIGIBLE_PLATEAU", {
                "n_trials": len(completed),
                "n_evaluated": n_evaluated,
                "median_oos_total_trades": median_oos_trades,
                "hit_trade_cap_count": n_hit_cap,
                # Issue #972 — Zerlegung über ALLE Trials (n_trials), nicht nur die Überlebenden
                # (n_evaluated); Summe der Werte + n_evaluated + verbleibender Rest == n_trials.
                "plateau_counter_breakdown": plateau_counter_breakdown,
                "p_eligible_windows": p_eligible_windows,
                # Issue #669/#921/#926 — innerhalb der EVALUIERTEN Trials: 'signal_quality' (echte
                # Qualitätsmessung), 'signal_sparse' (#921, das Signal tritt kaum auf) oder
                # 'inference_unavailable' (#926, die Messung selbst war nicht durchführbar) —
                # niemals mehr unbedingt 'signal_quality'.
                "binding_cause": binding_cause,
                **_binding_detail,
            })

            # Issue #681/#830 — dieselbe Closed-Loop-Anbindung wie im STRUCTURAL_ALL_UNEVALUABLE-
            # Zweig: 'signal_quality' unterliegt seit #830 demselben Evidenzregime wie 'signal_
            # absent' (#829) — n_runs_confirmed>=2 UND budget_executed_fraction>=0.9, BEIDE hier
            # tatsaechlich erreichbar (diese Studies fuehren fast immer das VOLLE Budget aus, siehe
            # #830-Root-Cause). Root-Cause #830: vorher deaktivierte eine EINZIGE Beobachtung das
            # Paar unbedingt fuer 10 Laeufe (Typ-II-Verstaerker) — in den Auto-Cache geschrieben,
            # NICHT in die menschlich-kuratierte Denylist-Config.
            if strategy is not None and symbol is not None:
                try:
                    from automation.optimizer.sweep_diagnostics import (
                        recommend_diagnosis_action, record_diagnosed_pair,
                        load_diagnosed_pairs_cache,
                    )
                    _prior_quality = load_diagnosed_pairs_cache().get((strategy, symbol))
                    _budget_execution_for_quality = compute_budget_execution(
                        completed, n_trials_budget=_n_trials_budget,
                        n_startup_trials=n_startup_trials, study_user_attrs=study.user_attrs,
                        run_id=run_id)
                    rec = recommend_diagnosis_action(
                        strategy, symbol, {"binding_cause": binding_cause,
                                           "median_oos_trades": median_oos_trades,
                                           "median_is_trades": None},
                        budget_executed_fraction=_budget_execution_for_quality["budget_executed_fraction"],
                        n_runs_confirmed=(
                            int(_prior_quality.get("n_runs_confirmed", 0))
                            if _prior_quality and _prior_quality.get("binding_cause") == binding_cause
                            else 0
                        ),
                        stop_reason=_budget_execution_for_quality["stop_reason"],
                        max_consecutive_structural_runs=int(
                            (weights or {}).get("max_consecutive_structural_runs", 2)),
                        simulation_semantics_version=(weights or {}).get("simulation_semantics_version"),
                    )
                    # Issue #1090 (Katalog #923) — siehe Docstring des STRUCTURAL_ALL_UNEVALUABLE-
                    # Zweigs oben: derselbe Fingerprint-Dedup gegen mehrfach gezaehlte Bestaetigungen.
                    from automation.optimizer.sweep_diagnostics import study_fingerprint as _study_fp
                    rec["study_fingerprint"] = _study_fp(
                        getattr(study, "study_name", None),
                        study.user_attrs.get("study_started_at_utc"),
                        _budget_execution_for_quality["n_trials_completed"],
                    )
                    record_diagnosed_pair(rec, run_id=run_id)
                except Exception:
                    logger.debug("Issue #681: diagnosis writeback fehlgeschlagen (non-fatal).", exc_info=True)

            should_stop = stop_on_plateau or (
                weights and weights.get("structural_min_modelled_trials_per_dim") is not None)
            if should_stop:
                logger.info("[JSON_EVENT] " + _json.dumps({
                    "event_type": "STUDY_EARLY_STOP",
                    "reason": "STRUCTURAL_ZERO_ELIGIBLE",
                    "current_trial": len(completed),
                    "startup_limit": max(1, int(n_startup_trials)),
                    # Issue #805 — ersetzt "k_limit" (floor_plateau_k, entfernt).
                    "structural_min_modelled_trials": structural_extra,
                    # Issue #753 — belegt im Report, ob abgebrochen wurde, weil die Suche stagnierte
                    # (viele modellierte Trials, keine Annaeherung an die feasible Region) oder weil
                    # sie nie stattfand (n_modelled_trials klein/0).
                    "n_modelled_trials": len(modelled_completed),
                    "min_constraint_violation_first": min_constraint_violation_first,
                    "min_constraint_violation_last": min_constraint_violation_last,
                    "plateau_min_modelled_trials": plateau_min_modelled_trials,
                    # Issue #806 — sequentielles Abbruchkriterium (Rule of Three): None, wenn
                    # n_trials_budget unbekannt war (Fallback auf die feste Untergrenze, bit-
                    # identisch zu Pre-#806).
                    "p_hi": p_hi,
                    "remaining_budget": remaining_budget,
                    "missed_probability": missed_probability,
                    # Issue #925 — welcher Modus tatsaechlich entschieden hat + der Erwartungswert-
                    # Kandidat, unabhaengig vom aktiven Modus (Vergleichbarkeit ueber Laeufe).
                    "plateau_stop_mode": _plateau_stop_mode,
                    "expected_yield": expected_yield,
                }))
                _stop_study_safely(study, logger)
        return

    # Legacy-Fallback (kein oos_evaluated-Attr, z. B. alte Studies / globaler make_objective-Pfad):
    # bisheriges Wert-Gleichheits-Praedikat am konstanten Unevaluable-Floor (−9.75).
    if "penalty_unevaluable_oos" not in weights or "unevaluable_shaping_span" not in weights:
        return
    floor = weights["penalty_unevaluable_oos"] + weights["unevaluable_shaping_span"]
    if all(abs(t.value - floor) < eps for t in completed):
        study.set_user_attr("floor_plateau_warned", True)
        logger.warning(
            "🚨 Floor-Plateau erkannt: alle %d abgeschlossenen Trials kleben am Unevaluable-Floor "
            "(%.4f). Der TPE-Sampler hat keinen Gradienten — das Symbol ist vermutlich strukturell "
            "unevaluable (Pitfall #75: OOS erzeugt nie evaluierbare Trades). Pruefe Daten-Suffizienz "
            "und Gates; verwirf ggf. die stale Study (rm data/optimizer/sweep/*.db).",
            len(completed), floor,
        )
        # Issue #456 — auch im Legacy-Wert-Zweig die aussichtslose Suche frueh beenden (Opt-in).
        if stop_on_plateau:
            _stop_study_safely(study, logger)


def retention_callback(study, trial, *, logger: logging.Logger | None = None) -> None:
    """Issue #794 — Optuna-Callback (Signatur ``(study, trial)``, analog ``floor_plateau_callback``):
    gibt nach JEDEM abgeschlossenen Trial jedes ``trial_*``-Verzeichnis dieser Study frei, das weder
    der/die aktuell beste(n) eligible(n) Trial(s) noch ein fehlgeschlagener (PRUNED/FAIL) Trial ist
    (siehe ``retention.release_non_best_trial_dirs``-Docstring). Damit liegt je Study zu jedem
    Zeitpunkt höchstens 1 Trial-Verzeichnis (plus die fehlgeschlagenen) — die Spitzenlast sinkt von
    O(n_trials) auf O(1) statt erst nach Abschluss der gesamten Study (#733) freigegeben zu werden.

    Fail-open: ein Retention-Fehler darf einen laufenden Sweep nie crashen (analog #703/#733)."""
    log = logger or logging.getLogger("optimizer")
    try:
        retention.release_non_best_trial_dirs(study, work_dir=WORK, logger=log)
    except Exception:
        log.warning("[#794] Trial-Retention-Callback fehlgeschlagen (non-fatal).", exc_info=True)


def disk_budget_callback(study, trial, *, opt_data: dict | None = None,
                         logger: logging.Logger | None = None) -> None:
    """Issue #795 — Optuna-Callback (Signatur ``(study, trial)``, analog ``floor_plateau_callback``/
    ``retention_callback``): misst alle ``disk_check_interval_trials`` Trials den Verbrauch von
    ``data/optimizer`` gegen ``disk_budget_gb``/``disk_reserve_gb`` (``disk_guard.check_budget``).

    * ``STATUS_PRESSURE`` ⇒ WARNING + sofortige aggressive Räumung über ALLE abgeschlossenen
      Studies (``retention.prune_orphaned_trial_dirs``).
    * ``STATUS_EXCEEDED`` ⇒ ERROR + ``study.stop()`` UND ``disk_guard.sweep_abort_requested.set()``
      — der Sweep-Dispatcher (``sweep.py``, #799) prüft dieses Flag zwischen zwei Symbolen für ein
      geordnetes Lauf-Ende statt eines harten ``ENOSPC``-Absturzes.

    Fail-open: ein Fehler in dieser Prüfung darf einen laufenden Sweep nie crashen (analog
    #703/#733/#794)."""
    log = logger or logging.getLogger("optimizer")
    opt_data = opt_data or {}
    interval = int(opt_data.get("disk_check_interval_trials") or 200)
    # Issue #795 — (trial.number + 1) statt trial.number: trial.number ist 0-indiziert, ``% interval``
    # allein wuerde auf JEDEM Trial 0 feuern (0 % irgendetwas == 0) statt erst nach ``interval``
    # abgeschlossenen Trials. Das Gate soll NACH dem 200./400./...-ten Trial pruefen, nicht sofort.
    if interval <= 0 or (trial.number + 1) % interval != 0:
        return
    try:
        budget_gb = float(opt_data.get("disk_budget_gb") or 200)
        reserve_gb = float(opt_data.get("disk_reserve_gb") or 50)
        status = disk_guard.check_budget(WORK, budget_gb=budget_gb, reserve_gb=reserve_gb)
        # Issue #1015/#1167 (Katalog #1170) — vorher nur bei STATUS_PRESSURE/STATUS_EXCEEDED ein
        # Event, STATUS_OK spurlos: ein Lauf, in dem das Budget nie eng wurde, und einer, in dem
        # diese Pruefung nie ausgefuehrt wurde, waren im Report ununterscheidbar. Symmetrisch (PASS
        # UND FAIL), source="sweep" (laeuft im selben Prozess wie sweep.py, "optimizer"-Sidecar).
        emit_execution_event(log, "INVARIANT_STREAM_RESULT", {
            "name": "check_budget", "check": "check_budget",
            "passed": status == disk_guard.STATUS_OK, "source": "sweep",
            "scope": getattr(study, "study_name", None),
            "expected": f"data/optimizer-Verbrauch <= budget_gb={budget_gb} UND freie Reserve "
                       f">= reserve_gb={reserve_gb}.",
            "actual": {"status": status, "budget_gb": budget_gb, "reserve_gb": reserve_gb,
                      "trial_number": trial.number} if status != disk_guard.STATUS_OK else None,
            "detail": f"disk_guard.check_budget ⇒ {status}.",
            "severity": "high" if status == disk_guard.STATUS_EXCEEDED else "medium",
        }, level=logging.INFO if status == disk_guard.STATUS_OK else logging.WARNING)
        if status == disk_guard.STATUS_PRESSURE:
            log.warning(
                "[#795] DISK_BUDGET_PRESSURE: data/optimizer nähert sich dem Budget (%.0f GB) "
                "oder der freien Reserve (%.0f GB) — räume verwaiste Trial-Verzeichnisse "
                "aggressiv.", budget_gb, reserve_gb,
            )
            emit_execution_event(log, "DISK_BUDGET_PRESSURE", {
                "budget_gb": budget_gb, "reserve_gb": reserve_gb, "trial_number": trial.number,
            })
            retention.prune_orphaned_trial_dirs(WORK, logger=log)
        elif status == disk_guard.STATUS_EXCEEDED:
            log.error(
                "[#795] DISK_BUDGET_EXCEEDED: data/optimizer hat das Budget (%.0f GB) oder die "
                "freie Reserve (%.0f GB) überschritten — Study wird gestoppt, geordnetes "
                "Sweep-Ende angefordert.", budget_gb, reserve_gb,
            )
            emit_execution_event(log, "DISK_BUDGET_EXCEEDED", {
                "budget_gb": budget_gb, "reserve_gb": reserve_gb, "trial_number": trial.number,
            }, level=logging.ERROR)
            disk_guard.sweep_abort_requested.set()
            _stop_study_safely(study, log)
    except Exception:
        log.warning("[#795] Disk-Budget-Callback fehlgeschlagen (non-fatal).", exc_info=True)


@_inv.invariant_scope("study")
def check_study_coherence_violation_rate(study, opt_data: dict, *,
                                         logger: logging.Logger | None = None) -> bool:
    """Issue #773 — Study-Abschluss-Check: bricht eine Study fail-loud aus dem Promotions-Pfad,
    wenn der Anteil ``oos_coherence_violation``-markierter Trials (#589/#620/#756/#771) ueber
    ``optimizer.json.max_coherence_violation_rate`` liegt.

    Root-Cause #773: ``invariants.check_log_return_coherence`` (#743) war bis dahin ein reiner
    REPORT-Nachtrag — er entsteht erst NACH Abschluss des gesamten Sweeps und wird nicht
    ausgewertet. Ein Lauf konnte Stunden mit hunderten Verletzungen der #756-Identitaet
    durchlaufen, ohne dass irgendetwas anschlug (dieselbe Fehlerklasse hat zwei Kataloge
    ueberlebt: #589/#620 → #756 → #771). Diese Pruefung macht die Invariante SELBST fail-loud,
    nicht nur ihre nachtraegliche Meldung.

    Setzt bei Ueberschreitung ``study.user_attrs['coherence_violation_rate_exceeded'] = True``
    (von ``export_symbol_proposal``/``confirm.py`` als Promotions-Sperre zu konsultieren) und
    emittiert ``INVARIANT_CHECK_FAILED`` + ``STUDY_ABORTED_ON_INVARIANT``. Aendert NIE einen
    Reward-Wert (Observability-Invariante wie bei ``floor_plateau_callback``). Rueckgabe: ``True``,
    wenn die Schwelle ueberschritten wurde."""
    if logger is None:
        logger = logging.getLogger("optimizer")
    # Issue #1015/#1167 (Katalog #1170) — dieser Check emittierte bisher NUR beim Ueberschreiten
    # (unten). "nicht konfiguriert"/"keine Daten"/"unterhalb der Schwelle" waren im Report
    # ununterscheidbar von "nie ausgefuehrt". ``_emit_result`` haelt den PASS-Pfad symmetrisch zum
    # bestehenden FAIL-Pfad, ohne dessen Rueckgabewert/Kontrollfluss zu aendern.
    def _emit_result(passed: bool, *, detail: str, actual=None) -> None:
        emit_execution_event(logger, "INVARIANT_STREAM_RESULT", {
            "name": "check_study_coherence_violation_rate",
            "check": "check_study_coherence_violation_rate",
            "passed": passed, "source": "sweep",
            "scope": getattr(study, "study_name", None),
            "expected": f"oos_coherence_violation-Rate <= max_coherence_violation_rate"
                       f"={max_rate}." if max_rate is not None else
                       "kein max_coherence_violation_rate konfiguriert (Check inaktiv, "
                       "Default-PASS).",
            "actual": actual, "detail": detail, "severity": "high",
        }, level=logging.INFO if passed else logging.WARNING)

    max_rate = opt_data.get("max_coherence_violation_rate")
    if max_rate is None:
        _emit_result(True, detail="max_coherence_violation_rate nicht konfiguriert — Check inaktiv.")
        return False
    trials = [t for t in getattr(study, "trials", None) or []
             if getattr(t, "user_attrs", {}).get("oos_evaluated") is True]
    n_evaluated = len(trials)
    if n_evaluated == 0:
        _emit_result(True, detail="Keine oos_evaluated Trials — Rate nicht messbar (Default-PASS).")
        return False
    violations = sum(1 for t in trials if t.user_attrs.get("oos_coherence_violation") is True)
    rate = violations / n_evaluated
    if rate <= float(max_rate):
        _emit_result(True, detail=f"{violations}/{n_evaluated} Trials mit "
                                  f"oos_coherence_violation <= max_coherence_violation_rate="
                                  f"{max_rate}.")
        return False
    # Issue #803 — Budget-Ausfuehrungsgrad ZUM AKTUELLEN AUFRUFZEITPUNKT: macht den frueheren
    # Abbruch (periodischer Callback, siehe coherence_violation_early_abort_callback) messbar,
    # statt implizit erst am vollen Budget zu urteilen.
    all_trials = getattr(study, "trials", None) or []
    n_trials_when_aborted = len(all_trials)
    n_trials_budget = (getattr(study, "user_attrs", None) or {}).get("n_trials_budget")
    budget_executed_fraction = None
    if n_trials_budget:
        try:
            budget_executed_fraction = round(n_trials_when_aborted / float(n_trials_budget), 4)
        except (TypeError, ValueError, ZeroDivisionError):
            budget_executed_fraction = None
    _emit_result(False, actual={"rate": rate, "n_evaluated": n_evaluated, "n_violations": violations},
                 detail=f"{violations}/{n_evaluated} Trials mit oos_coherence_violation "
                       f"(#756-Identitaet verletzt) > max_coherence_violation_rate={max_rate}.")
    emit_execution_event(logger, "INVARIANT_CHECK_FAILED", {
        "scope": getattr(study, "study_name", None), "check": "check_log_return_coherence",
        "expected": f"<= {max_rate}", "actual": rate,
        "detail": f"{violations}/{n_evaluated} Trials mit oos_coherence_violation "
                 f"(#756-Identitaet verletzt) > max_coherence_violation_rate={max_rate}.",
    }, level=logging.ERROR)
    logger.error(
        "[#773] %s: coherence_violation_rate=%.4f (%d/%d) > max_coherence_violation_rate=%s ⇒ "
        "STUDY_ABORTED_ON_INVARIANT — Study wird nicht promotet.",
        getattr(study, "study_name", None), rate, violations, n_evaluated, max_rate,
    )
    logger.info("[JSON_EVENT] " + json.dumps({
        "event_type": "STUDY_ABORTED_ON_INVARIANT",
        "check": "check_log_return_coherence",
        "coherence_violation_rate": rate, "n_evaluated": n_evaluated,
        "n_violations": violations, "threshold": float(max_rate),
        # Issue #803 — frueher Abbruch statt vollem Budget (Akzeptanzkriterium: 35/64
        # Verletzungen brechen spaetestens bei Trial 32 ab ⇒ budget_executed_fraction <= 0.55).
        "n_trials_when_aborted": n_trials_when_aborted,
        "budget_executed_fraction": budget_executed_fraction,
    }))
    try:
        study.set_user_attr("coherence_violation_rate_exceeded", True)
    except Exception:
        pass
    return True


def coherence_violation_early_abort_callback(study, trial, *, opt_data: dict | None = None,
                                             logger: logging.Logger | None = None,
                                             check_interval_trials: int = 32) -> None:
    """Issue #803 — Optuna-Callback (Signatur ``(study, trial)``, analog ``disk_budget_callback``/
    ``retention_callback``): prueft alle ``check_interval_trials`` Trials (Default 32), ob die
    Kohaerenz-Verletzungsrate (``check_study_coherence_violation_rate``, #773) bereits ueberschritten
    ist, statt erst NACH ``study.optimize()`` (nach dem VOLLEN Budget, Root-Cause #803). Eine
    pathologische Study (z. B. 35/64 Verletzungen) endet damit nach ~10 % statt 100 % des Budgets.

    Fail-open: ein Fehler in dieser Pruefung darf einen laufenden Sweep nie crashen (analog
    #703/#733/#794/#795)."""
    log = logger or logging.getLogger("optimizer")
    opt_data = opt_data or {}
    # Issue #803 — (trial.number + 1), analog disk_budget_callback (#795): trial.number ist
    # 0-indiziert, ``% interval`` allein wuerde auf JEDEM Trial 0 feuern.
    if check_interval_trials <= 0 or (trial.number + 1) % check_interval_trials != 0:
        return
    try:
        if check_study_coherence_violation_rate(study, opt_data, logger=log):
            _stop_study_safely(study, log)
    except Exception:
        log.warning("[#803] Kohaerenz-Fruehabbruch-Callback fehlgeschlagen (non-fatal).", exc_info=True)


def _check_reward_semantics_version(study, opt_data: dict,
                                    logger: logging.Logger | None = None) -> None:
    """Issue #410 (P3) — Reward-Semantik-Versionierung & Study-Hygiene.

    Die Fixes #404–#407 aendern die Reward-Semantik des Per-Symbol-Pfads. Reward-Werte
    verschiedener Semantik-Versionen sind NICHT vergleichbar: eine geladene Study, die noch alte
    Floor-Trials (Pitfall #75, konstanter −9.75) enthaelt, wuerde diese mit neuen, gradientenreichen
    Trials mischen und den TPE-Sampler verwirren.

    Frische Studies (keine Trials) werden mit ``optimizer.json['reward_semantics_version']``
    gestempelt. Traegt eine geladene Study eine andere (oder gar keine) Version, obwohl sie bereits
    Trials akkumuliert hat, wird laut gewarnt mit dem Hinweis, die stale DB zu loeschen. Fehlt der
    Config-Key, ist die Pruefung ein No-Op (Rueckwaerts-Kompat). Reine Hygiene/Observability —
    veraendert keine Reward-/Promotion-Entscheidung."""
    if logger is None:
        logger = logging.getLogger("optimizer")
    current = opt_data.get("reward_semantics_version")
    if current is None:
        return  # Versionierung nicht konfiguriert -> No-Op

    existing = study.user_attrs.get("reward_semantics_version")
    has_trials = len(study.trials) > 0

    if existing == current:
        return
    if existing is None and not has_trials:
        study.set_user_attr("reward_semantics_version", current)
        return

    # Issue #468 / #575 — Harter Fail-Loud-Mechanismus und Purge bei Posterior-Korruption.
    msg = (f"Reward-Semantik-Versionskonflikt: die geladene Study wurde unter Version {existing if existing is not None else 'unversioniert'} "
           f"akkumuliert, aktuell ist Version {current}. Reward-Werte verschiedener Versionen "
           f"sind NICHT vergleichbar. Dies führt zu Posterior-Korruption im TPE-Sampler. "
           f"Initiere Purge der obsoleten Study-Datenbank (.db)...")

    if has_trials:
        if existing is None or existing < current:
            logger.warning("♻️ %s", msg)
            try:
                optuna.delete_study(study_name=study.study_name, storage=study._storage)
                logger.warning(f"Obsolete Study '{study.study_name}' erfolgreich gelöscht. Sie wird beim nächsten Versuch neu erstellt.")
            except Exception as e:
                logger.error(f"Fehler beim Löschen der Study: {e}")
            # Issue #591 — fail-loud mit explizitem Fehlercode. v8 (Issues #587–#591) ist mit v7
            # inkompatibel: alte SQLite-Studies MÜSSEN gelöscht werden, kein stilles Weiterlaufen.
            raise ValueError(f"REJECT_STALE_STUDY_SEMANTICS: Study-Semantik Mismatch. {msg}")

    logger.warning("♻️ %s", msg)


def _check_simulation_semantics_version(study, opt_data: dict,
                                        logger: logging.Logger | None = None) -> None:
    """Issue #854 (P0) — Simulations-Semantik-Versionierung & Study-Hygiene, dieselbe MECHANIK wie
    ``_check_reward_semantics_version`` (#410), aber eine ORTHOGONALE Achse (siehe
    ``optimizer.json['simulation_semantics_version']``-Schema für die vollständige reward/
    simulation/params_schema-Abgrenzung): ``reward_semantics_version`` versioniert, WIE ein Trial
    bewertet wird; ``simulation_semantics_version`` versioniert, WAS überhaupt gemessen wurde
    (Exit-Pfad #836-#838, Zeitbox-Konsequenz #839, T-abhängige Guard-Schwelle #844). Eine Study mit
    einer ALTEN simulation_semantics_version enthält Trials, deren Metriken unter einem ANDEREN
    Handelsvertrag simuliert wurden — kein Reward-Fix kann das reparieren, dieselbe fail-loud +
    Purge-Konsequenz wie bei einem reward_semantics_version-Mismatch, nur unter einem eigenen,
    unterscheidbaren Fehlercode (``REJECT_STALE_SIMULATION_SEMANTICS``), damit ein Operator die
    beiden Ursachen im Log auseinanderhalten kann.

    Frische Studies werden mit ``optimizer.json['simulation_semantics_version']`` gestempelt. Fehlt
    der Config-Key, ist die Prüfung ein No-Op (rückwärtskompatibel zu Pre-#854-Configs)."""
    if logger is None:
        logger = logging.getLogger("optimizer")
    current = opt_data.get("simulation_semantics_version")
    if current is None:
        return  # Versionierung nicht konfiguriert -> No-Op

    existing = study.user_attrs.get("simulation_semantics_version")
    has_trials = len(study.trials) > 0

    if existing == current:
        return
    if existing is None and not has_trials:
        study.set_user_attr("simulation_semantics_version", current)
        return

    msg = (f"Simulations-Semantik-Versionskonflikt: die geladene Study wurde unter Version "
           f"{existing if existing is not None else 'unversioniert'} simuliert, aktuell ist "
           f"Version {current}. Die gemessenen Metriken verschiedener Simulations-Versionen sind "
           f"NICHT vergleichbar (ein anderer Handelsvertrag wurde ausgeführt). Initiere Purge der "
           f"obsoleten Study-Datenbank (.db)...")

    if has_trials:
        if existing is None or existing < current:
            logger.warning("♻️ %s", msg)
            try:
                optuna.delete_study(study_name=study.study_name, storage=study._storage)
                logger.warning(f"Obsolete Study '{study.study_name}' erfolgreich gelöscht. Sie wird beim nächsten Versuch neu erstellt.")
            except Exception as e:
                logger.error(f"Fehler beim Löschen der Study: {e}")
            raise ValueError(f"REJECT_STALE_SIMULATION_SEMANTICS: Study-Simulations-Semantik Mismatch. {msg}")

    logger.warning("♻️ %s", msg)


def _check_inference_semantics_version(study, opt_data: dict,
                                       logger: logging.Logger | None = None) -> None:
    """Issue #968 (Katalog A, P0 HEADLINE, GitHub-Issue #785) — Inferenz-Semantik-Versionierung &
    Study-Hygiene, dieselbe MECHANIK wie ``_check_reward_semantics_version``/``_check_simulation_
    semantics_version``, aber eine DRITTE, orthogonale Achse (siehe ``optimizer.json[
    'inference_semantics_version']``-Schema für die vollständige Abgrenzung): ``reward_semantics_
    version`` versioniert WIE ein Trial bewertet wird, ``simulation_semantics_version`` WAS
    gemessen wurde — ``inference_semantics_version`` versioniert, welches URTEIL (``oos_psr``/
    ``oos_sortino`` definiert vs. ``None``, Guard getrippt ja/nein) eine bereits simulierte
    Trade-Serie erhält (#965/#967-Diagnose-Vollständigkeit). Eine Study mit einer ALTEN Version
    enthält Trials, deren Selektionsstatistik unter einem ANDEREN Inferenzregime bewertet wurde —
    dieselbe fail-loud + Purge-Konsequenz, unter einem eigenen Fehlercode
    (``REJECT_STALE_INFERENCE_SEMANTICS``).

    Frische Studies werden mit ``optimizer.json['inference_semantics_version']`` gestempelt. Fehlt
    der Config-Key, ist die Prüfung ein No-Op (rückwärtskompatibel zu Pre-#968-Configs)."""
    if logger is None:
        logger = logging.getLogger("optimizer")
    current = opt_data.get("inference_semantics_version")
    if current is None:
        return  # Versionierung nicht konfiguriert -> No-Op

    existing = study.user_attrs.get("inference_semantics_version")
    has_trials = len(study.trials) > 0

    if existing == current:
        return
    if existing is None and not has_trials:
        study.set_user_attr("inference_semantics_version", current)
        return

    msg = (f"Inferenz-Semantik-Versionskonflikt: die geladene Study wurde unter Version "
           f"{existing if existing is not None else 'unversioniert'} bewertet, aktuell ist "
           f"Version {current}. Die Selektionsstatistik-Urteile verschiedener Inferenz-Versionen "
           f"sind NICHT vergleichbar (Guard-/PSR-Referenz hat sich geändert). Initiere Purge der "
           f"obsoleten Study-Datenbank (.db)...")

    if has_trials:
        if existing is None or existing < current:
            logger.warning("♻️ %s", msg)
            try:
                optuna.delete_study(study_name=study.study_name, storage=study._storage)
                logger.warning(f"Obsolete Study '{study.study_name}' erfolgreich gelöscht. Sie wird beim nächsten Versuch neu erstellt.")
            except Exception as e:
                logger.error(f"Fehler beim Löschen der Study: {e}")
            raise ValueError(f"REJECT_STALE_INFERENCE_SEMANTICS: Study-Inferenz-Semantik Mismatch. {msg}")

    logger.warning("♻️ %s", msg)


def make_objective(
    strategy: str,
    *,
    run_backtest=run_backtest,
    build_trial=build_trial,
    parse_tournament=parse_tournament,
    compute_reward=compute_reward,
    study_config_dir: Path | None = None,
):
    """Issue #796 — ``study_config_dir`` (eine per ``trial_config.freeze_study_config`` eingefrorene
    Study-Config) ist optional: ``None`` (Default, z. B. wenn diese Funktion isoliert/in Tests ohne
    vorheriges Freeze aufgerufen wird) reproduziert das Alt-Verhalten bit-identisch
    (``copy_config=True``, eine Config-Kopie je Trial). Der Aufrufer (``optimize``) friert die
    Study-Config genau einmal ein und reicht den Pfad hier durch."""
    def objective(trial):
        sampled = sample_params(strategy, trial)
        trial.set_user_attr("sampled_params", sampled)

        cfg_dir = config_dir()
        optimizer_path = cfg_dir / "optimizer.json"
        seed = 42
        opt_data: dict = {}
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                opt_data = json.load(f) or {}
                seed = opt_data.get("seed", 42)

        trial_dir, manifest_path = build_trial(
            strategy_class=strategy,
            sampled=sampled,
            study_name=trial.study.study_name,
            trial_number=trial.number,
            seed=seed,
            n_folds=4,
            holdout_days=45,
            copy_config=study_config_dir is None,
            study_config_dir=study_config_dir,
        )

        _t0 = time.perf_counter()
        try:
            # Issue #797 — Subprocess-Log-Policy aus optimizer.json (Default "on_failure").
            output_path = run_backtest(
                trial_dir, manifest_path, config_dir=study_config_dir,
                subprocess_log_policy=opt_data.get("subprocess_log_policy", "on_failure"),
                subprocess_log_tail_bytes=opt_data.get("subprocess_log_tail_bytes", 32768),
            )
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")
        backtest_ms = round((time.perf_counter() - _t0) * 1000)  # Issue #415 — Wall-Clock

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        t_data: dict = {}
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                t_data = json.load(f) or {}
                risk_dd_cap = t_data.get("max_drawdown", 0.30)

        universe_path = config_dir().parent.parent / "data" / "universe" / "momentum_ls.json"
        universe_size = 70
        if universe_path.exists():
            with open(universe_path, "r", encoding="utf-8") as f:
                u_data = json.load(f)
                universe_size = len(u_data.get("universe", []))

        reward, reward_terms = compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap, return_terms=True)
        trial.set_user_attr("reward_terms", reward_terms)

        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        import logging
        # Issue #804 — jede im Subprozess gesammelte Inferenzpfad-Diagnose erneut im
        # Elternprozess-Log emittieren, BEVOR das Trial-Summary-Event geloggt wird.
        _reemit_inference_diagnostics(logging.getLogger("optimizer"), metrics, trial.number)
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "trial_number": trial.number,
            "backtest_ms": backtest_ms,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_total_trades": metrics.oos_total_trades,
            "fully_eligible_pairs": metrics.fully_eligible_pairs,
            "win_count": metrics.win_count,
            "is_total_trades": metrics.is_total_trades,
            "hit_trade_cap": metrics.hit_trade_cap,
            "outcome": outcome,
            # Issue #455 — OOS-Abdeckungs-Telemetrie auch im globalen Pfad surfacen (BEIDE Events).
            "fill_ts_max": metrics.fill_ts_max,
            "oos_window_start_ns": metrics.oos_window_start_ns,
            "oos_covered": metrics.oos_covered,
            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
            "oos_anchor_divergence": metrics.oos_anchor_divergence,
            # Issue #569 — Roh-Kennzahlen additiv (rein observational) auch im globalen Pfad.
            "oos_total_return": metrics.oos_total_return,
            "oos_sortino": metrics.oos_sortino,
            "oos_expectancy": metrics.oos_expectancy,
            "oos_win_rate": metrics.oos_win_rate,
            "oos_profit_factor": metrics.oos_profit_factor,
            "is_sortino_median": metrics.is_sortino_median,
            "per_fold_oos_sortino": list(metrics.oos_fold_sortinos),
            # Issue #665 — annualisierungs-invariante Fassung (kanonisch für fold-übergreifende
            # Vergleiche); "per_fold_oos_sortino" (oben) bleibt nur forensische Anzeige.
            "per_fold_oos_sortino_period": list(metrics.oos_fold_sortino_periods),
            # Issue #620 — #589-Kohärenz-Verletzung (sign(oos_sortino)≠sign(oos_total_return)) sichtbar.
            "oos_coherence_violation": bool(metrics.oos_coherence_violation),
            "reward_terms": reward_terms,
        })


        reward_mode = "auto"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                reward_mode = (json.load(f) or {}).get("reward_mode", "auto")
        if reward_mode == "pareto":
            min_trades = 20
            if tournament_path.exists():
                with open(tournament_path, "r", encoding="utf-8") as f:
                    min_trades = (json.load(f) or {}).get("min_trades", 20)
            trades_constraint = min_trades - metrics.oos_total_trades
            dd_constraint = metrics.oos_max_drawdown - risk_dd_cap
            trial.set_user_attr("constraints", (float(trades_constraint), float(dd_constraint)))
        # Issue #612 — Feasibility-Constraint(s) für den TPE-Sampler auch im globalen Pfad.
        # Issue #635 — dimensionslos normiert (t_data = dieselbe tournament.json-Config, oben geladen).
        trial.set_user_attr("oos_constraint_violations", _compute_oos_constraints(metrics, t_data))
        return reward
    return objective


# Issue #1067/#1217 (P1, Katalog #1196-1221) — Default fuer ``tpe_history_window`` (siehe
# ``_WindowedTPESampler``-Docstring); optimizer.json['tpe_history_window'] ueberschreibt.
_TPE_HISTORY_WINDOW_DEFAULT = 600


class _WindowedTPESampler(optuna.samplers.TPESampler):
    """Issue #1067/#1217 — begrenzt den TPE-Surrogat-Fit auf ein GLEITENDES FENSTER der letzten
    ``history_window`` Trials, statt auf die vollstaendige, monoton wachsende Trial-Historie eines
    warm-gestarteten Stores.

    Symptom (B-12): TSLA bei konstant 1940 NEUEN Trials — 0,95h / 1,26h / 1,78h / 2,23h Wallclock
    bei 3863 / 5803 / 7743 / 9683 VORLAUF-Trials im selben Store (``cpu_utilisation_backtest``
    9,8% → 4,8%, LINEAR fallend). Frische Laeufe: 0,46-0,54h bei 18,2-22,8%. Root-Cause: die Zeit
    geht NICHT in Backtests — TPE fittet sein Surrogat-Modell bei JEDEM ``sample_relative``-Aufruf
    ueber die GESAMTE, warm-gestartet wachsende Trial-Historie (``TPESampler._sample`` liest
    ``study._get_trials(...)`` unbedingt, ohne Fenster).

    Fix: ``_sample`` (Optunas privater, aber stabiler Sampling-Kern) wird hier NEU implementiert,
    identisch zu Optuna 4.9's Fassung, MIT EINER ZUSAETZLICHEN ZEILE: ``trials`` wird auf die
    LETZTEN ``history_window`` Eintraege (nach ``.number`` sortiert) beschraenkt, BEVOR der
    Below/Above-Split und der Parzen-Estimator-Fit stattfinden. AELTERE Trials bleiben
    UNVERAENDERT im Store (kein Datenverlust, keine Wirkung auf ``best_trial``/Reporting/
    Family-N-Zaehlung — ausschliesslich der SURROGAT-FIT dieses einen Sampler-Aufrufs wird
    verkleinert).

    ACHTUNG (Wartungshinweis fuer kuenftige Agenten): ``_sample`` ist eine PRIVATE Optuna-Methode
    ohne API-Stabilitaetsgarantie ueber Versionsgrenzen hinweg — dieser Override ist an Optuna
    4.9.0 gebunden (siehe ``requirements.txt``/Pin). Ein Optuna-Upgrade MUSS ``_sample``s
    Quelltext gegen diese Kopie diffen, bevor es uebernommen wird (sonst driftet dieser Override
    stillschweigend von Optunas tatsaechlichem Sampling-Verhalten ab)."""

    def __init__(self, *args, history_window: int = _TPE_HISTORY_WINDOW_DEFAULT, **kwargs):
        super().__init__(*args, **kwargs)
        self._history_window = int(history_window)
        # Issue #1067/#1217 Fix Punkt 3 — kumulierte Surrogat-Fit-Zeit dieser Sampler-Instanz
        # (eine Instanz je Study), gelesen vom Aufrufer NACH study.optimize() und dort als
        # study-user_attr gestempelt (siehe Aufrufstellen).
        self._fit_seconds_total = 0.0
        # Issue #1089/#1237 (P1, Katalog #1247+) — die Trial-Zahl des LETZTEN Fit-Aufrufs (vor UND
        # nach dem Fenster): am Studienende ist die Trial-Zahl maximal, das ist deshalb der
        # aussagekraeftigste Messpunkt fuer "wie viele Trials gingen tatsaechlich in den Fit ein"
        # (``tpe_fit_trials_used``/``_available``, gestempelt vom Aufrufer nach study.optimize()).
        self._last_trials_used = 0
        self._last_trials_available = 0

    def _sample(self, study, trial, search_space):
        _fit_t0 = time.perf_counter()
        try:
            return self._sample_windowed(study, trial, search_space)
        finally:
            self._fit_seconds_total += time.perf_counter() - _fit_t0

    def _sample_windowed(self, study, trial, search_space):
        from optuna.trial import TrialState as _TrialState
        from optuna.samplers._tpe.sampler import _split_trials

        if self._constant_liar:
            states = [_TrialState.COMPLETE, _TrialState.PRUNED, _TrialState.RUNNING]
        else:
            states = [_TrialState.COMPLETE, _TrialState.PRUNED]
        use_cache = not self._constant_liar
        trials = study._get_trials(deepcopy=False, states=states, use_cache=use_cache)
        if self._constant_liar:
            trials = [t for t in trials if trial.number != t.number]
        self._last_trials_available = len(trials)

        # Issue #1067/#1217 Fix — DAS gleitende Fenster: nur die zuletzt angelegten Trials gehen
        # in den Surrogat-Fit ein (aeltere bleiben Teil des Stores, werden hier nur nicht mehr
        # gefittet). ``sorted(..., key=.number)`` statt Store-Reihenfolge zu unterstellen.
        if self._history_window > 0 and len(trials) > self._history_window:
            trials = sorted(trials, key=lambda t: t.number)[-self._history_window:]
        self._last_trials_used = len(trials)

        n = sum(t.state != _TrialState.RUNNING for t in trials)
        below_trials, above_trials = _split_trials(
            study, trials, self._gamma(n), self._constraints_func is not None)

        mpe_below = self._build_parzen_estimator(study, search_space, below_trials, handle_below=True)
        mpe_above = self._build_parzen_estimator(study, search_space, above_trials, handle_below=False)

        samples_below = mpe_below.sample(self._rng.rng, self._n_ei_candidates)
        acq_func_vals = self._compute_acquisition_func(samples_below, mpe_below, mpe_above)
        ret = optuna.samplers.TPESampler._compare(samples_below, acq_func_vals)

        for param_name, dist in search_space.items():
            ret[param_name] = dist.to_external_repr(ret[param_name])
        return ret


def _resolve_tpe_history_window(opt_data: dict | None) -> int:
    """Issue #1067/#1217 — ``optimizer.json['tpe_history_window']`` mit Default 600. Fail-open bei
    fehlendem/ungueltigem Wert (kein Abbruch der Study-Erstellung fuer eine reine Performance-
    Konfiguration)."""
    try:
        value = (opt_data or {}).get("tpe_history_window", _TPE_HISTORY_WINDOW_DEFAULT)
        return int(value) if int(value) > 0 else _TPE_HISTORY_WINDOW_DEFAULT
    except (TypeError, ValueError):
        return _TPE_HISTORY_WINDOW_DEFAULT


# Issue #1089/#1237 (P1, Katalog #1247+) — Symptom: bei k=3 warm-gestarteten Vorlauf-Trials lag
# ``Σ tpe_fit_seconds`` bei 228s (O(n^1,9) in der Store-Groesse), 8,6% der Wallclock im Surrogat-
# Fit, ``cpu_utilisation_backtest`` bei 12,9%. ``tpe_history_window`` (#1067/#1217, Default 600)
# deckelt bereits denselben Fit — ``tpe_fit_max_trials`` ist eine ZWEITE, unabhaengig konfigurierbare
# Obergrenze mit eigenem Default (2000) und eigener Telemetrie (``tpe_fit_trials_used``/
# ``_available``), NICHT als Ersatz fuer ``tpe_history_window`` gedacht: der EFFEKTIVE Fensterwert
# ist das Minimum beider Werte (siehe Aufrufstellen), sodass eine grosszuegigere Konfiguration des
# einen Wertes nicht die vom anderen garantierte Obergrenze aufheben kann.
_TPE_FIT_MAX_TRIALS_DEFAULT = 2000


def _resolve_tpe_fit_max_trials(opt_data: dict | None) -> int:
    """Issue #1089/#1237 — ``optimizer.json['tpe_fit_max_trials']`` mit Default 2000. Fail-open bei
    fehlendem/ungueltigem Wert (dieselbe Konvention wie ``_resolve_tpe_history_window``)."""
    try:
        value = (opt_data or {}).get("tpe_fit_max_trials", _TPE_FIT_MAX_TRIALS_DEFAULT)
        return int(value) if int(value) > 0 else _TPE_FIT_MAX_TRIALS_DEFAULT
    except (TypeError, ValueError):
        return _TPE_FIT_MAX_TRIALS_DEFAULT


def optimize(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    WORK.mkdir(parents=True, exist_ok=True)
    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"

    # Default values
    conf_n_trials = 100
    n_startup_trials = 16
    seed = 42
    opt_data: dict = {}

    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f) or {}
            conf_n_trials = opt_data.get("n_trials", conf_n_trials)
            n_startup_trials = opt_data.get("n_startup_trials", n_startup_trials)
            seed = opt_data.get("seed", seed)

    # Issue #631 — Fail-loud beim Config-Load: die additiven Strafterme dürfen die Base-Streuung auf
    # dem deklarativen Kalibrier-Fixture nicht strukturell überstimmen (PENALTY_SCALE_MISCALIBRATED).
    assert_penalty_scale_calibrated(opt_data)
    # Issue #633 — warnt beim Config-Load, wenn eine eligible_requires_any-Schwelle strukturell über
    # dem p99 der dokumentierten Kalibrier-Fixture-Verteilung liegt (unerreichbarer OR-Arm).
    tournament_path_check = cfg_dir / "tournament.json"
    if tournament_path_check.exists():
        with open(tournament_path_check, "r", encoding="utf-8") as f:
            _any_arm_unreachable = check_any_arm_reachability(json.load(f) or {})
        _emit_any_arm_reachability_result(
            logging.getLogger("optimizer"), _any_arm_unreachable,
            check_name="check_any_arm_reachability", scope=strategy)

    if n_trials is None:
        n_trials = conf_n_trials
        # Issue #622 — NUR den Config-Default an die Dimensionalität koppeln (>= k·dim). Ein EXPLIZIT
        # übergebenes n_trials (Test/CLI --n-trials) ist eine bewusste Wahl und wird exakt respektiert.
        n_trials = derive_n_trials(strategy, n_trials, opt_data)
    # Issue #568 — n_startup_trials dokumentiert an die Parameterzahl koppeln (Legacy ohne den Key).
    n_startup_trials = derive_n_startup_trials(strategy, n_startup_trials, opt_data)

    study_name = f"study_{strategy}"

    reward_mode = opt_data.get("reward_mode", "auto")
    directions = None
    if reward_mode == "pareto":
        directions = ["maximize", "maximize", "maximize", "maximize", "minimize", "minimize"]
        # Issue #950 (Katalog C) — vorher ein rohes (trades_constraint, dd_constraint)-Tupel aus
        # UN-normierten Deltas (bzw. der Default (0.0, 0.0), der bei fehlendem User-Attr eine
        # KONSTANTE Distanz-0 vortaeuscht — genau das #950-Symptom exakter Nullen ohne Gradienten).
        # Derselbe kontinuierliche, normierte Constraint-Pfad wie der Default-Modus (_oos_
        # constraints_func, #635) — undefinierte Metriken zaehlen als MAXIMAL verletzt (1.0), nicht
        # als 0.0.
        sampler = optuna.samplers.NSGAIISampler(constraints_func=_oos_constraints_func, seed=seed)
    else:
        # Issue #612 — FEASIBILITY GEHÖRT IN DEN SAMPLER, nicht in eine 12-Einheiten-Reward-Klippe.
        # ``constraints_func`` liest die gestempelten, normierten OOS-Gate-Verletzungen
        # (``oos_constraint_violations``, ≤ 0 = feasible). Optuna 4.9 behandelt Feasibility NATIV:
        # ``study.best_trial`` UND das TPE-Sampling bevorzugen feasible strikt vor infeasible; unter den
        # infeasiblen wird nach Gesamtverletzung sortiert. Damit optimiert der Sampler EINE stetige
        # Grösse (die risikoadjustierte OOS-Performance, nach #614 die PSR) statt einer Stufenfunktion.
        sampler = _WindowedTPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed,
            constraints_func=_oos_constraints_func,
            # Issue #1089/#1237 — die EFFEKTIVE Obergrenze ist das Minimum aus tpe_history_window
            # (#1067/#1217) und tpe_fit_max_trials: keiner der beiden Werte kann die vom jeweils
            # anderen garantierte Obergrenze aufheben.
            history_window=min(
                _resolve_tpe_history_window(opt_data), _resolve_tpe_fit_max_trials(opt_data)),
        )

    study = _create_study_with_retry(
        study_name=study_name,
        storage=STORAGE,
        direction="maximize" if not directions else None,
        directions=directions,
        sampler=sampler
    )

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)
    # Issue #854 — orthogonale Simulations-Semantik-Version (WAS gemessen wurde, siehe dortigen
    # Docstring), unabhaengig geprueft/gestempelt.
    _check_simulation_semantics_version(study, opt_data)
    # Issue #968 — dritte orthogonale Achse: welches URTEIL (Selektionsstatistik definiert/Guard
    # getrippt) eine bereits simulierte Trade-Serie erhaelt, unabhaengig geprueft/gestempelt.
    _check_inference_semantics_version(study, opt_data)

    # Issue #409 — Fail-Loud-Guard auch im globalen Pfad (gleicher Floor-Kollaps moeglich).
    # Issue #456 — Produktion bindet stop_on_plateau=True: aussichtslose Study früh beenden.
    floor_guard = partial(floor_plateau_callback, weights=opt_data,
                          n_startup_trials=n_startup_trials, stop_on_plateau=True)
    # Issue #796 — EINE eingefrorene Config je Study statt einer Kopie je Trial. n_folds=4/
    # holdout_days=45 sind exakt die Werte, die die Objective-Closure unten pro Trial an
    # build_trial uebergibt (siehe make_objective) — muessen hier identisch sein, sonst wuerde
    # jeder Trial gegen das FALSCHE eingefrorene walk_forward laufen.
    study_config_dir = freeze_study_config(
        study_name, resolve_wf_settings(cfg_dir, holdout_days=45, n_folds=4), base_cfg=cfg_dir)
    disk_guard_cb = partial(disk_budget_callback, opt_data=opt_data)
    # Issue #803 — periodischer Fruehabbruch bei systematischer Kohaerenz-Verletzung (statt erst
    # nach dem vollen Budget zu urteilen).
    coherence_guard_cb = partial(coherence_violation_early_abort_callback, opt_data=opt_data)
    study.optimize(
        make_objective(strategy, study_config_dir=study_config_dir),
        n_trials=n_trials,
        n_jobs=n_jobs,
        catch=(json.JSONDecodeError, OSError),
        callbacks=[floor_guard, retention_callback, disk_guard_cb, coherence_guard_cb]
    )
    # Issue #1067/#1217 Fix Punkt 3 — TPE-Surrogat-Fit-Zeit dieses Laufs, konsumiert von
    # invariants.check_search_overhead_share (nur gesetzt, wenn der Sampler _WindowedTPESampler
    # ist — NSGAIISampler im 'pareto'-Reward-Modus traegt diese Telemetrie nicht).
    if hasattr(sampler, "_fit_seconds_total"):
        study.set_user_attr("tpe_fit_seconds", round(sampler._fit_seconds_total, 4))
        # Issue #1089/#1237 (P1) — je Study gestempelt, Rohmaterial fuer
        # invariants.check_tpe_fit_cost_share und das Akzeptanzkriterium
        # "tpe_fit_trials_used <= tpe_fit_max_trials".
        study.set_user_attr("tpe_fit_trials_used", sampler._last_trials_used)
        study.set_user_attr("tpe_fit_trials_available", sampler._last_trials_available)
    return study

def _sanitize(symbol: str) -> str:
    """'TSLA.ETORO' → 'TSLA_ETORO' (dateinamenstauglicher Study-/DB-Name)."""
    return symbol.replace(".", "_")


def resolve_storage(*, study_name: str, base_cfg: Path | None = None) -> str:
    """Storage-URL-Auflösung (A4.7, optional). Priorität:
       ENV `ETORO_OPTUNA_STORAGE` > optimizer.json['storage_url'] (falls nicht null)
       > f'sqlite:///{WORK}/sweep/{study_name}.db' (Default).

    **SQLite bleibt strikter Default**; Postgres o. ä. ist reines Opt-In für echte
    Multi-Maschinen-Parallelität (mehrere Hosts gegen *eine* Study) und weicht die
    „ausschließlich SQLite"-Leitplanke (Pitfall #53) bewusst, dokumentiert und begrenzt auf.
    Bei einer non-SQLite-URL wird eine Warnung geloggt (Determinismus pro Study nur bei
    n_jobs=1). Eine via ENV übergebene URL wird **verbatim** genutzt (Fail-Fast: ungültige
    URIs scheitern beim `create_study`-Connect, statt still auf SQLite zurückzufallen)."""
    env_url = os.environ.get("ETORO_OPTUNA_STORAGE")
    if env_url:
        url = env_url
    else:
        if base_cfg is None:
            base_cfg = config_dir()
        url = None
        optimizer_path = base_cfg / "optimizer.json"
        if optimizer_path.exists():
            try:
                with open(optimizer_path, "r", encoding="utf-8") as f:
                    url = (json.load(f) or {}).get("storage_url")
            except (OSError, ValueError):
                url = None
        if not url:
            url = f"sqlite:///{WORK / 'sweep' / (study_name + '.db')}"

    if not url.startswith("sqlite"):
        logging.getLogger("optimizer").warning(
            "Non-SQLite Optuna-Storage '%s' — Determinismus pro Study nur bei n_jobs=1 "
            "garantiert; parallele Writes lockern die SQLite-Leitplanke (Pitfall #53) bewusst auf.",
            url,
        )
    return url


def _tunable_params_only(strategy: str, params: dict) -> dict:
    """Issue #820 Fix Punkt 4 — filtert ``params`` auf Schlüssel, die ``bounds.
    extract_numeric_bounds`` als TATSÄCHLICH tunbar (numerischer ``suggest_*``-Aufruf in
    ``spaces.sample_params``) ausweist. Root-Cause #820: ``strategies.json[strategy].params``
    kann ein nicht-tunbares Boolean-Flag tragen (z. B. ComboTrendVwapStrategy: ``{"allow_short":
    true}``) — als Dict ist das wahr (truthy), ``load_global_best`` kehrte damit zurück, OBWOHL
    kein einziger Optimierungs-Parameter enthalten war, und verdeckte die Champion-Stufe
    (``resolve_symbol_shrinkage_seed``) für die GESAMTE Strategie dauerhaft und lautlos. Ein Dict,
    das nach dieser Filterung leer ist, ist ``{}`` (kein globales Optimum) — ``strategy`` unbekannt
    (``ValueError`` aus ``extract_numeric_bounds``) lässt ``params`` unangetastet (kein
    Suchraum-Wissen verfügbar, fail-open statt eines False-Negatives)."""
    if not params:
        return {}
    from automation.optimizer import bounds
    try:
        tunable_keys = bounds.extract_numeric_bounds(strategy)
    except ValueError:
        return dict(params)
    return {k: v for k, v in params.items() if k in tunable_keys}


def load_global_best(strategy: str, base_cfg: Path) -> dict:
    """Quelle des globalen Optimums (Warm-Start-Samen, Gate 2):
       proposal_{strategy}.json['proposed_params_override'] falls vorhanden UND status
       'READY_FOR_PR', sonst strategies.json[strategy].params, sonst {} (None-safe).

    Bewusste Entscheidung (A4.5a Rückfrage): Ein Proposal mit status != READY_FOR_PR
    (z. B. REJECTED_ON_HOLDOUT) wird NICHT als Samen genutzt — Fallback auf strategies.json.

    Issue #820 Fix Punkt 4 — BEIDE Quellen werden auf tatsächlich tunbare Parameter gefiltert
    (``_tunable_params_only``), bevor sie als "globales Optimum" zurückgegeben werden: ein Dict,
    das nur nicht-tunbare Flags (z. B. ``allow_short``) trägt, ist wahrheitswertlich ``True``,
    aber KEIN Optimierungsergebnis — ``resolve_symbol_shrinkage_seed`` würde die Champion-/
    Defaults-Stufe sonst nie erreichen (siehe dortiger Docstring, #705 §9).
    """
    proposal_path = WORK / f"proposal_{strategy}.json"
    if proposal_path.exists():
        try:
            with open(proposal_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if data.get("status") == "READY_FOR_PR":
                override = _tunable_params_only(strategy, data.get("proposed_params_override") or {})
                if override:
                    return override
        except (OSError, ValueError):
            pass

    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        try:
            with open(strats_path, "r", encoding="utf-8") as f:
                strats = json.load(f) or {}
            for s in strats.get("strategies", []):
                if s.get("strategy_class") == strategy:
                    params = _tunable_params_only(strategy, s.get("params") or {})
                    if params:
                        return params
        except (OSError, ValueError):
            pass

    return {}


def load_strategy_defaults_params(strategy: str, base_cfg: Path) -> dict:
    """Issue #565 — die deklarierten Default-Parameter einer Strategie aus strategy_defaults.json
    (ohne den ``_schema``-Block). None-safe ⇒ ``{}``, wenn Datei/Strategie fehlt. Das ist der
    ökonomisch begründete Prior (genau der Vektor, der laut Holdout-Evidenz besser generalisiert
    als der ungezügelt symbol-getunte)."""
    defaults_path = base_cfg / "strategy_defaults.json"
    if defaults_path.exists():
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            params = data.get(strategy)
            if isinstance(params, dict):
                return {k: v for k, v in params.items() if not k.startswith("_")}
        except (OSError, ValueError):
            pass
    return {}


def resolve_symbol_shrinkage_seed(strategy: str, base_cfg: Path, *,
                                  symbol: str | None = None,
                                  opt_data: dict | None = None,
                                  catalog_newest_ns: int | None = None) -> tuple[dict, str]:
    """Issue #565 — Shrinkage-/Warm-Start-Referenz für den Per-Symbol-Pfad, mit definiertem
    Fallback statt Silent-Zero. Issue #704 — erweitert um die Champion-Store-Stufe (Ebene 1 des
    Epics #702) ZWISCHEN ``global_best`` und ``strategy_defaults``.

    Reihenfolge: ``load_global_best`` (Proposal READY_FOR_PR / strategies.json) →
    ``champions.load_champion_entry`` (bester ERREICHTER, aber noch nicht promoteter Holdout-
    Kandidat, #703/#704) → ``strategy_defaults`` → ``{}``. Gibt ``(seed_params, source)`` zurück,
    ``source ∈ {'global_best', 'champion', 'champion_quality_stale', 'strategy_defaults', 'none'}``
    (Issue #853 — 'champion_quality_stale' seit diesem Fix von 'champion' unterschieden, siehe
    unten). Fehlt das echte globale Optimum, ist ``strategy_defaults`` (bzw. der Champion, falls
    vorhanden) der Prior, gegen den die A4.3-Shrinkage (``param_pen``) zieht — so wird ``param_pen``
    NIE still 0 (der Kollaps, bei dem der symbol-getunte Vektor völlig ungezügelt Richtung
    IS/OOS-CV-Rausch tunt, #565).

    ``symbol``/``opt_data`` sind ADDITIV optional (HI-2): fehlen sie (Legacy-Aufrufer, z. B. das
    globale ``optimize()`` ohne Symbol-Kontext, oder bestehende Tests), ist das Verhalten
    bit-identisch zum Pre-#704-Zustand (zwei-stufige Kette ``global_best → strategy_defaults →
    none``, keine Champion-Stufe) — der Champion-Store ist strikt Per-Symbol-skopiert und ohne
    ``symbol`` gibt es keinen eindeutigen Store-Pfad."""
    global_best = load_global_best(strategy, base_cfg)
    if global_best:
        return global_best, "global_best"
    if symbol is not None and opt_data is not None:
        # Issue #853 Fix Punkt 3 — ``seed_source`` unterscheidet jetzt 'champion' von
        # 'champion_quality_stale', statt beide unter 'champion' zu verstecken: der volle Eintrag
        # (nicht nur ``load_champion_seed``s Parameter-Extrakt) traegt die Information, ob die
        # QUALITY-Bewertung unter einem aelteren reward_semantics_version gemessen wurde
        # (champions.champion_quality_stale, #819) — der Parametervektor bleibt in BEIDEN Faellen
        # gleichwertig seed-faehig (er durchlaeuft beim Enqueue ohnehin erneut alle Gates), aber
        # die Unterscheidung macht sichtbar, ob ein Champion FEHLTE oder ob er vorhanden war und
        # nur seine Quality-Telemetrie veraltet ist (Root-Cause #853: ohne sie war "kein
        # global_best/Champion" die EINZIGE Negativ-Warnung, es gab keine positive Telemetrie).
        from automation.optimizer import champions
        champion_entry = champions.load_champion_entry(strategy, symbol, opt_data=opt_data)
        if champion_entry:
            champion_params = dict(champion_entry.get("params") or {})
            if champion_params:
                source = ("champion_quality_stale"
                          if champions.champion_quality_stale(champion_entry, opt_data)
                          else "champion")
                return champion_params, source
    defaults = load_strategy_defaults_params(strategy, base_cfg)
    if defaults:
        return defaults, "strategy_defaults"
    return {}, "none"


def _classify_trial_rejection(metrics, *, timebox_violated: bool = False) -> str:
    """Issue #408 — kategorisiert, WARUM ein Per-Symbol-Trial nicht promotebar ist, fuer die modale
    Aggregation im Proposal (confirm._dominant_rejection). Trennt den IS-Drop ('oos_not_evaluated':
    das Symbol erzeugte nie evaluierbare OOS-Trades — die Pitfall-#75-Signatur) vom OOS-Drop
    ('oos_gate_rejected': OOS evaluiert, aber durchs Eligibility-Gate gefallen) und vom Pass
    ('none'). Bewusst grob & stabil, damit die Reasons ueber Trials hinweg aggregierbar bleiben.

    Issue #971 (Pitfall #303/#304 in AGENTS.md, Katalog B) — ``timebox_violated`` (dieselbe Grösse
    wie ``run_optimization``s ``_timebox_violated_this_trial``) MUSS vor dem generischen
    ``oos_not_evaluated``-Zweig geprüft werden: der #857-Fix stempelt ``metrics.oos_evaluated =
    False`` NACHTRÄGLICH auf einen Trial, der tatsächlich OOS gehandelt hat (Gegenbeweis zum
    IS-Gate-Drop, siehe ``_classify_is_rejection_detail``). Ohne diese Unterscheidung sammelte
    ``oos_not_evaluated`` zwei kausal verschiedene Populationen (echter IS-Gate-Drop UND
    Zeitbox-Invalidierung) unter einem Namen ein — die Selbstverschleierung, die
    ``check_holding_time_cap`` (#971) und den Zero-Eligible-Plateau-Zähler (#972) unbrauchbar
    machte."""
    if timebox_violated:
        return "oos_timebox_violation"
    if metrics.oos_evaluated and metrics.oos_eligible:
        return "none"
    if not metrics.oos_evaluated:
        return "oos_not_evaluated"
    return "oos_gate_rejected"


# Issue #453/#615 — die kanonische »kein Drop«-Kategorie (``is_rejection_detail``-User-Attr eines
# eligiblen Trials). BENANNTE Konstante statt verstreuter "NONE"-String-Literale in zwei Modulen
# (run_optimization + confirm) — genau die Divergenz, die #615 verursachte: confirm filterte auf
# ``is_rejection_detail is None`` (Python-``None``), gestempelt wurde aber der STRING "NONE" ⇒
# eligible_trials war IMMER leer ⇒ Top-k-Holdout (#576) lief nie (k=1 statt k=holdout_top_k).
IS_REJECTION_NONE = "NONE"


def _compute_oos_constraints(metrics, tournament_cfg: dict | None = None) -> tuple:
    """Issue #612 — Feasibility-Constraint(s) für den Sampler (Optuna-Konvention: ``<= 0`` = feasible).

    Ein eligibler (feasibler) Trial ⇒ ``(0.0,)``. Sonst die MITTLERE, dimensionslos normierte
    OOS-Gate-Verletzung — ``> 0``, sodass der Sampler unter den infeasiblen nach vergleichbarem
    Rest-Gap sortiert.

    Issue #635 — VORHER wurden rohe, un-normierte Deltas (``oos_gate_deltas``, ``actual − threshold``)
    summiert: ein grosskaliges Gate wie PSR (∈[0,1]) verschluckte ein kleinskaliges wie Excess-Return
    (~[0,0.05]) um den Faktor ~19 — der Sampler steuerte infeasible Trials faktisch nur nach PSR-Nähe.
    Jetzt: dieselbe GETEILTE Scale-Auflösung wie der Reward-Near-Miss-Pfad
    (``reward._normalized_gate_distances``, Single Source of Truth mit ``_constraint_distance_penalty``)
    — jede Verletzung auf Target/Scale normiert, dann gemittelt über die AKTIVEN Dimensionen (analog
    #534). Erfassen die Distanzen die Ineligibilität nicht (nicht-evaluiert, Micro-Sizing, fehlende
    ``tournament_cfg``, unzureichende Metrik-Daten) ⇒ konstante Verletzung ``1.0``. KONSTANTE Länge
    (1) über alle Trials — Optuna verlangt einen fixen Constraint-Vektor."""
    if getattr(metrics, "oos_eligible", False):
        return (0.0,)
    if not getattr(metrics, "oos_evaluated", False) or not tournament_cfg:
        return (1.0,)
    try:
        from automation.optimizer.reward import _normalized_gate_distances
        risk_dd_cap = tournament_cfg.get("max_drawdown")
        distances = _normalized_gate_distances(metrics, {}, risk_dd_cap, tournament_cfg)
    except (ValueError, KeyError, TypeError, AttributeError):
        distances = {}
    active = [d for d in distances.values() if d > 0.0]
    violation = (sum(active) / len(active)) if active else 1.0
    return (float(violation),)


def _oos_constraints_func(trial):
    """Issue #612 — von TPESampler/NSGAIISampler aufgerufen: liest die im Objective gestempelten
    ``oos_constraint_violations``. Fehlt der Stempel (Pruned/Legacy) ⇒ feasible ``(0.0,)``."""
    return trial.user_attrs.get("oos_constraint_violations", (0.0,))


# Issue #453 — Prefix → dezidierte Enum-Kategorie. Die Reason-Strings stammen aus
# backtest_runner._evaluate_oos_eligibility (z. B. "oos_max_drawdown: 0.5 > 0.3"); längere Prefixe
# zuerst, damit "oos_min_total_return" nicht fälschlich von "oos_min_t..." geschluckt wird.
_OOS_REASON_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("oos_min_total_return", "REJECT_OOS_MIN_TOTAL_RETURN"),
    ("oos_min_trades", "REJECT_OOS_MIN_TRADES"),
    ("oos_min_expectancy", "REJECT_OOS_MIN_EXPECTANCY"),
    ("oos_max_drawdown", "REJECT_OOS_MAX_DRAWDOWN"),
    ("oos_min_win_rate", "REJECT_OOS_MIN_WIN_RATE"),
    ("oos_min_sortino", "REJECT_OOS_MIN_SORTINO"),
    ("oos_min_profit_factor", "REJECT_OOS_MIN_PROFIT_FACTOR"),
    # Issue #917 — fehlte bislang vollständig: JEDE oos_min_psr-Ablehnung (definierter Miss UND
    # undefinierter PSR) fiel dadurch auf den Catch-All REJECT_OOS_OTHER (1692/2187 Trials eines
    # Referenzlaufs). Ein echter numerischer Miss (nicht 'None (insufficient/guard)', siehe
    # _OOS_UNDEFINED_STATISTIC_MARKER unten) landet jetzt hier; der undefinierte Fall wird VOR
    # dieser Zuordnung in _classify_is_rejection_detail abgefangen.
    ("oos_min_psr", "REJECT_OOS_MIN_PSR"),
    ("oos_min_excess_return", "REJECT_OOS_MIN_EXCESS_RETURN"),
    # Issue #1115 — dieselbe #917-Fehlerklasse: das mit #1093/#1241 eingefuehrte
    # oos_min_alpha_tstat-Gate fehlte hier vollstaendig, JEDE Ablehnung (definierter Miss UND
    # undefinierter t(alpha), backtest_runner._evaluate_oos_eligibility) fiel auf den Catch-All
    # REJECT_OOS_OTHER statt REJECT_OOS_MIN_ALPHA_TSTAT. Dadurch blieb
    # is_rejection_detail_counts['REJECT_OOS_MIN_ALPHA_TSTAT'] bei 0, waehrend
    # invariants.gate_inventory_table dieselben Trials unabhaengig ueber oos_rejection_reasons
    # korrekt als n_solo_rejections > 0 fuer dieses Gate zaehlte -- die Ordnungs-Invariante
    # 0 <= n_solo_rejections <= n_rejections (#1003/#1155) griff genau diese Divergenz ab und
    # brach den Report-Schreibvorgang fail-loud ab (0 <= 43 <= 0 verletzt).
    ("oos_min_alpha_tstat", "REJECT_OOS_MIN_ALPHA_TSTAT"),
    ("Micro-Sizing", "REJECT_OOS_MICRO_SIZING"),
    ("oos_not_evaluable", "REJECT_OOS_NOT_EVALUABLE"),
)

# Issue #917 — Marker-Substring, den backtest_runner._evaluate_oos_eligibility in JEDE Rejection-
# Reason schreibt, deren zugrunde liegende Kennzahl UNDEFINIERT ist (None), statt eines regulär
# berechneten Werts, der die Schwelle verfehlt (z. B. "oos_min_psr: None (insufficient/guard) <
# 0.75"). "None < threshold" ist kein Vergleichsergebnis, sondern eine nicht durchgeführte Messung
# — dieselbe Missing-Data-Klasse wie #759/#788, hier auf Gate- statt Metrik-Ebene.
_OOS_UNDEFINED_STATISTIC_MARKER = "None (insufficient"


def _map_oos_reason(reason: str) -> str:
    """Issue #453 — eine konkrete OOS-Reason-Zeile auf ihre dezidierte Enum-Kategorie abbilden."""
    for prefix, code in _OOS_REASON_PREFIX_MAP:
        if reason.startswith(prefix):
            return code
    return "REJECT_OOS_OTHER"


def _extract_undefined_gate_terms(reasons) -> list[str]:
    """Issue #917 Fix 2 — welche ``eligible_requires_all``-Gates in DIESEM Trial auf einer
    undefinierten Grösse liefen (``oos_gate_undefined_terms``). Reine Textextraktion aus den
    bereits vorhandenen ``oos_rejection_reasons``-Zeilen — kein zweiter Auswertungspfad."""
    terms: list[str] = []
    for r in reasons or ():
        if _OOS_UNDEFINED_STATISTIC_MARKER in r:
            label = r.split(":", 1)[0].split(" (", 1)[0].strip()
            if label and label not in terms:
                terms.append(label)
    return terms


def _classify_is_rejection_detail(metrics, *, timebox_violated: bool = False) -> str:
    """Issue #453 — granulare, aggregierbare Ablehnungs-Kategorie (feiner als _classify_trial_rejection).

    Löst den Catch-All ``oos_not_evaluated`` in die TATSÄCHLICHE Ursache auf, sodass systematisches
    Auto-Tuning (Bezug #403/#408) den DATENseitigen Drop (OOS-Fenster nicht abgedeckt, #455) vom
    STRATEGIEseitigen (abgedeckt, aber inaktiv) und vom konkreten OOS-Gate-Drop (Drawdown, Win-Rate,
    Trades …) unterscheiden kann. Rein klassifizierend — ändert KEINE Reward-/Promotion-Entscheidung.

    Kategorien:
      * ``NONE``                          — evaluiert & eligible (kein Drop).
      * ``REJECT_OOS_TIMEBOX_VIOLATION``  — Issue #971: der Trial hat tatsächlich OOS gehandelt
                                            (``oos_covered``/``oos_total_trades`` bezeugen es), wurde
                                            aber NACHTRÄGLICH (#857) auf ``oos_evaluated=False``
                                            umgestempelt, weil mindestens ein Round-Trip die Zeitbox
                                            verletzt hat. MUSS vor der IS-Gate-Heuristik unten
                                            geprüft werden — sonst ist dieser Trial vom echten
                                            ``REJECT_OOS_DISCARDED_BY_IS_GATE`` (das Symbol wurde nie
                                            OOS gehandelt) nicht mehr unterscheidbar (Pitfall #303).
      * ``REJECT_OOS_WINDOW_UNREACHABLE`` — OOS=0 + ``oos_covered is False`` (H2-Katalog, #455).
      * ``REJECT_OOS_INACTIVE``           — OOS=0, aber ``oos_covered is True`` (Strategie handelt
                                            im OOS-Fenster nicht — strategieseitig, separat zu lösen).
      * ``REJECT_OOS_NOT_EVALUATED``      — OOS=0, Abdeckung unbekannt (Legacy/keine #455-Telemetrie).
      * ``REJECT_OOS_<GATE>``             — evaluiert, aber durchs konkrete Eligibility-Gate gefallen.
      * ``REJECT_OOS_STATISTIC_UNAVAILABLE`` — Issue #917: mindestens EIN requires_all-Gate
                                            operierte auf einer UNDEFINIERTEN Grösse (None) statt
                                            einem berechneten Wert unter der Schwelle — der Trial
                                            wurde nicht GEMESSEN, nicht am Gate abgelehnt.
    """
    if timebox_violated:
        return "REJECT_OOS_TIMEBOX_VIOLATION"
    if metrics.oos_evaluated and metrics.oos_eligible:
        return IS_REJECTION_NONE
    if not metrics.oos_evaluated:
        covered = getattr(metrics, "oos_covered", None)
        if covered is False:
            return "REJECT_OOS_WINDOW_UNREACHABLE"
        if covered is True:
            if getattr(metrics, "oos_total_trades", 0) > 0:
                return "REJECT_OOS_DISCARDED_BY_IS_GATE"
            return "REJECT_OOS_INACTIVE"
        return "REJECT_OOS_NOT_EVALUATED"
    reasons = getattr(metrics, "oos_rejection_reasons", ()) or ()
    if reasons:
        # Issue #917 — Vorrang vor JEDER anderen Klassifikation, unabhängig davon, ob die
        # undefinierte Grösse an erster Stelle der Liste steht: 'nicht messbar' ist eine andere
        # Aussage als 'am Gate gescheitert', selbst wenn ein anderes Gate ZUSÄTZLICH numerisch
        # verfehlt wurde (die numerische Verletzung ist auf einer nicht bewerteten Kohorte ohnehin
        # nicht aussagekräftig).
        if any(_OOS_UNDEFINED_STATISTIC_MARKER in r for r in reasons):
            return "REJECT_OOS_STATISTIC_UNAVAILABLE"
        return _map_oos_reason(reasons[0])
    return "REJECT_OOS_GATE"


def derive_n_startup_trials(strategy: str, base_n_startup: int, opt_data: dict) -> int:
    """Issue #568/#762 — ``n_startup_trials`` an die effektive Dimensionalität koppeln.

    Bei ``multivariate=True, group=True`` sollte ``n_startup_trials ≳ k·dim`` sein (``dim`` = Anzahl
    numerischer Suchraum-Parameter), damit der TPE die Kovarianzstruktur überhaupt schätzen kann;
    für Strategien mit vielen Parametern (ComboTrendVwap ~14) sind fixe 16 knapp. Deklarativ über
    ``n_startup_trials_per_dim`` (k): ``n_startup_trials = max(base, ceil(k·dim))``. Fehlt der Key
    (oder <= 0) ⇒ ``base`` (Legacy, bit-identisch, Zero-Hardcoding).

    Issue #762 — für ``dim >= n_startup_trials_high_dim_threshold`` ist ``k=2`` knapp für die
    Kovarianzschätzung (Squeeze dim=9 blieb bei k=2 über 124 Symbole ohne einen einzigen eligiblen
    Trial). Ist ``n_startup_trials_high_dim_threshold``/``n_startup_trials_per_dim_high_dim`` gültig
    UND ``dim`` erreicht die Schwelle, ersetzt der höhere Satz ``k`` (Squeeze: 18 → 27, ComboTrendVwap:
    28 → 42). Fehlt einer der beiden Keys (oder <= 0) ⇒ flaches ``k`` für alle dim (Legacy)."""
    k = opt_data.get("n_startup_trials_per_dim")
    if not k or float(k) <= 0.0:
        return int(base_n_startup)
    try:
        from automation.optimizer import bounds
        dim = len(bounds.extract_numeric_bounds(strategy))
    except Exception:
        return int(base_n_startup)
    threshold = opt_data.get("n_startup_trials_high_dim_threshold")
    k_high = opt_data.get("n_startup_trials_per_dim_high_dim")
    if (threshold and k_high and float(threshold) > 0.0 and float(k_high) > 0.0
            and dim >= float(threshold)):
        k = k_high
    return max(int(base_n_startup), math.ceil(float(k) * dim))


def derive_n_trials(strategy: str, base_n_trials: int, opt_data: dict) -> int:
    """Issue #622 — ``n_trials`` an die Dimensionalität koppeln (analog derive_n_startup_trials).

    ``n_trials = 100`` bei 14 Dimensionen ist faktisch Zufallssuche (72 TPE-Trials für 14 dim ⇒
    Spearman(trial_nr, reward) ≈ 0.04–0.23, best(51–100) oft SCHLECHTER als best(1–50)). Deklarativ
    über ``n_trials_per_dim`` (k ≥ 20): ``n_trials = max(base, ceil(k·dim))`` ⇒ ComboTrendVwap (14 dim)
    ≥ 280. Fehlt der Key (oder <= 0) ⇒ ``base`` (Legacy, bit-identisch, Zero-Hardcoding).

    Issue #931 Fix 2 — ``wallclock_budget_policy='degrade'`` multipliziert das Ergebnis GLOBAL mit
    dem in ``{WORK}/wallclock_degrade_state.json`` persistierten Faktor (siehe
    ``wallclock_guard.write_degrade_factor``/``read_degrade_factor``): der Sweep-Preflight
    (``sweep.run_per_symbol_sweep``) berechnet den Faktor VOR dem ersten Trial und schreibt ihn in
    diese von ``optimizer.json`` GETRENNTE Zustandsdatei, weil jede Study ihre Config unabhängig
    frisch von der Platte lädt (kein geteiltes In-Memory-Objekt über Study-Grenzen). Faktor 1.0
    (Datei fehlt, z. B. kein Degrade-Preflight gelaufen) ⇒ bit-identisch zum Pre-#931-Verhalten."""
    k = opt_data.get("n_trials_per_dim")
    if not k or float(k) <= 0.0:
        base = int(base_n_trials)
    else:
        try:
            from automation.optimizer import bounds
            dim = len(bounds.extract_numeric_bounds(strategy))
        except Exception:
            base = int(base_n_trials)
        else:
            base = max(int(base_n_trials), math.ceil(float(k) * dim))
    degrade_factor = wallclock_guard.read_degrade_factor(WORK)
    if degrade_factor >= 1.0:
        return base
    return max(1, math.ceil(base * degrade_factor))


def _apply_deprioritized_budget(strategy: str, symbol: str, n_trials: int, opt_data: dict) -> int:
    """Issue #830 Fix Punkt 2 — skaliert ``n_trials`` mit ``deprioritized_budget_factor`` (Default
    0.5), wenn der Auto-Diagnose-Cache (#681/#761) für ``(strategy, symbol)`` ``action ==
    'deprioritized'`` trägt (siehe ``sweep_diagnostics.recommend_diagnosis_action``: ein Paar,
    dessen einziger Ablehnungsgrund ``signal_quality`` ist, aber noch keine volle #830-Evidenz für
    ``'denylist'`` erreicht hat). Root-Cause #830: die Alternative — jeden Lauf das volle Budget
    ODER nach einer einzigen Beobachtung komplett verschwinden — ist ein Typ-II-Verstärker; ein
    reduziertes Budget hält den Suchraum erreichbar bei gesenkten Kosten.

    Mindestens 1 Trial (``math.ceil``, nie 0 — ein ``deprioritized`` Paar ist NICHT faktisch ein
    Denylist-Eintrag). Fail-open: ein Cache-Lesefehler lässt ``n_trials`` unverändert (dieselbe
    Konvention wie ``load_diagnosed_pairs_cache`` selbst)."""
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        entry = load_diagnosed_pairs_cache().get((strategy, symbol))
    except Exception:
        return n_trials
    if entry is None or entry.get("action") != "deprioritized":
        return n_trials
    factor = float(opt_data.get("deprioritized_budget_factor", 0.5))
    return max(1, math.ceil(n_trials * factor))


def derive_plateau_min_modelled_trials(strategy: str | None, base: int, opt_data: dict) -> int:
    """Issue #768 — koppelt die ZERO_ELIGIBLE-Modellierungsschwelle (``plateau_min_modelled_trials``)
    an die effektive Suchraum-Dimension, strukturgleich zu ``derive_n_trials``/``derive_n_startup_
    trials``.

    Root-Cause #768: ``n_trials`` (~20·dim) und ``n_startup_trials`` (~2-3·dim) skalieren beide mit
    der Dimension, ``plateau_min_modelled_trials`` (#753) blieb eine FLACHE Konstante (48) — der vor
    dem Zero-Eligible-Urteil ausgefuehrte Budgetanteil fiel dadurch monoton von 64% (dim=2) auf 32%
    (dim=14), exakt invers zur Anforderung (ein hoeher-dimensionaler Raum braucht MEHR modellierte
    Trials fuer ein belastbares "strukturell kein eligibler Lauf"-Urteil, nicht weniger).

    Deklarativ ueber ``plateau_min_modelled_trials_per_dim`` (k): ``max(base, ceil(k·dim))``. Fehlt
    der Key (oder <= 0) oder ist ``strategy`` unbekannt ⇒ ``base`` (Legacy, bit-identisch,
    Zero-Hardcoding) — dieselbe Fallback-Konvention wie ``derive_n_trials``."""
    k = opt_data.get("plateau_min_modelled_trials_per_dim") if opt_data else None
    if not k or float(k) <= 0.0 or not strategy:
        return int(base)
    try:
        from automation.optimizer import bounds
        dim = len(bounds.extract_numeric_bounds(strategy))
    except Exception:
        return int(base)
    return max(int(base), math.ceil(float(k) * dim))


def derive_structural_min_modelled_trials(strategy: str | None, opt_data: dict) -> int:
    """Issue #805 — ERSETZT das entfernte ``floor_plateau_k`` (dritte Wiederkehr derselben
    Fehlerklasse: #488 -> #753 -> #769 -> #805). Strukturgleich zu ``derive_plateau_min_modelled_
    trials`` (#768), aber fuer den STRUCTURAL_ALL_UNEVALUABLE-Zweig: ``min_for_structural =
    n_startup_trials + derive_structural_min_modelled_trials(...)``.

    Root-Cause #805: ``floor_plateau_k`` blieb auf dem seit #488 gesetzten Default 0 — ``min_for_
    structural`` kollabierte damit auf EXAKT ``n_startup_trials``, die Grenze, ab der ``TPESampler``
    ueberhaupt erst zu MODELLIEREN beginnt (0 modellierte Trials vor dem STRUCTURAL-Urteil).

    Deklarativ ueber ``structural_min_modelled_trials_per_dim`` (k, Default 3): ``ceil(k · dim)``.
    Ein explizit gesetzter Wert ``<= 0`` wuerde denselben degenerierten Zustand wiederherstellen und
    ist daher ein Konfigurationsfehler (siehe ``assert_structural_min_modelled_trials_valid`` — die
    Pruefung laeuft beim Sweep-Start, NICHT hier: diese Funktion faellt defensiv auf den Default 3
    zurueck, statt lautlos 0 zuzulassen, falls sie dennoch mit einem ungueltigen Wert aufgerufen
    wird, z. B. direkt aus einem Test ohne die Start-Preflight). Fehlt ``strategy`` oder scheitert
    die Dimensionsermittlung ⇒ ``k`` selbst als flache Konstante (kein Bounds-Zugriff noetig,
    Zero-Hardcoding-Fallback, analog ``derive_plateau_min_modelled_trials``)."""
    raw_k = opt_data.get("structural_min_modelled_trials_per_dim", 3) if opt_data else 3
    try:
        k = float(raw_k)
    except (TypeError, ValueError):
        k = 3.0
    if k <= 0.0:
        k = 3.0
    if not strategy:
        return max(1, math.ceil(k))
    try:
        from automation.optimizer import bounds
        dim = len(bounds.extract_numeric_bounds(strategy))
    except Exception:
        return max(1, math.ceil(k))
    return max(1, math.ceil(k * dim))


def assert_structural_min_modelled_trials_valid(opt_data: dict) -> None:
    """Issue #805 — FAIL-LOUD beim Sweep-Start: ``structural_min_modelled_trials_per_dim`` <= 0
    (EXPLIZIT gesetzt) wuerde den STRUCTURAL_ALL_UNEVALUABLE-Abbruch wieder auf NULL TPE-
    modellierten Trials urteilen lassen — dieselbe degenerierte Semantik wie das entfernte
    ``floor_plateau_k=0`` (dritte Wiederkehr derselben Fehlerklasse, #488/#753/#769/#805). Ein
    fehlender Key ist KEIN Fehler (Default 3, siehe ``derive_structural_min_modelled_trials``);
    nur ein explizit gesetzter Wert <= 0 wird abgelehnt (analog
    ``confirm.py['promotion_correction_mode']``, #659)."""
    if opt_data is None or "structural_min_modelled_trials_per_dim" not in opt_data:
        return
    raw = opt_data.get("structural_min_modelled_trials_per_dim")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"STRUCTURAL_MIN_MODELLED_TRIALS_INVALID (#805): "
            f"optimizer.json['structural_min_modelled_trials_per_dim']={raw!r} ist keine Zahl."
        )
    if val <= 0.0:
        raise ValueError(
            f"STRUCTURAL_MIN_MODELLED_TRIALS_DEGENERATE (#805): "
            f"optimizer.json['structural_min_modelled_trials_per_dim']={raw!r} <= 0 wuerde den "
            f"STRUCTURAL_ALL_UNEVALUABLE-Abbruch wieder auf NULL TPE-modellierten Trials urteilen "
            f"lassen — dieselbe Fehlerklasse wie das entfernte 'floor_plateau_k=0' (#488/#753/#769). "
            f"Entferne den Key (Default 3) oder setze einen Wert > 0."
        )


def gradient_signal_arm(rewards: list[float], evaluable_fraction: float,
                        tau: float, *, constraint_improvement_rate: float | None = None,
                        tau_c: float = 0.05, min_eligible_for_variance: int = 5) -> str:
    """Issue #568/#754/#808 — klassifiziert, WELCHER von DREI gleichrangigen Armen des Gradienten-
    Gates der Tier-Eskalation feuert: ``'discovery'``, ``'reward_variance'``, ``'constraint_progress'``
    oder ``'none'`` (kein Arm). ``study_shows_gradient_signal`` (bool-Wrapper, Bestandsschnittstelle)
    ist genau ``arm != 'none'``.

    1. **Entdeckungs-Arm** (#808, NEU, wird ZUERST geprueft): ``1 <= n_eligible <
       min_eligible_for_variance`` (Default 5) ⇒ ``'discovery'``. Root-Cause #808: ``pstdev`` einer
       ein- bis vierelementigen Menge ist NICHT „klein", sondern statistisch UNSCHAETZBAR/trivial —
       gegen ``τ`` geprueft, verwarf das GENAU DEN Fall, der den staerksten Beleg fuer mehr Budget
       liefert (die feasible Region ist NACHWEISLICH nicht leer). Der erste eligible Trial ist ein
       Eskalationsgrund, kein Ausschlussgrund.
    2. **Reward-Arm** (#568, NUR NOCH ab ``n_eligible >= min_eligible_for_variance``):
       ``evaluable_fraction > 0`` UND ``pstdev(reward) > τ`` — die Study hat eine NICHT-LEERE
       feasible Region UND streut dort MESSBAR (genug Stichprobe fuer eine Varianzschaetzung).
    3. **Constraint-Arm** (#754): ``constraint_improvement_rate > τ_c`` — der Sampler naehert sich
       der feasiblen Region an (relative Verbesserung der minimalen Gesamt-Constraint-Verletzung
       zwischen erster und zweiter Haelfte der modellierten Trials), UNABHAENGIG davon, ob die
       feasible Region je erreicht wurde.

    Root-Cause #754 (Constraint-Arm): der reine Reward-Arm ist bei LEERER feasibler Region
    (``p_eligible == 0``, nach #753 der Normalfall waehrend die Suche noch laeuft) IMMER ``False`` —
    eine Study braucht dann eligible Trials, um mehr Budget zu bekommen, UND mehr Budget, um
    eligible Trials zu finden (Selbstblockade). Reine, deterministische Funktion (separat testbar)."""
    n_eligible = len(rewards)
    if rewards and evaluable_fraction > 0.0 and 1 <= n_eligible < min_eligible_for_variance:
        return "discovery"
    if (rewards and evaluable_fraction > 0.0 and n_eligible >= min_eligible_for_variance
            and statistics.pstdev([float(r) for r in rewards]) > float(tau)):
        return "reward_variance"
    if constraint_improvement_rate is not None and float(constraint_improvement_rate) > float(tau_c):
        return "constraint_progress"
    return "none"


def study_shows_gradient_signal(rewards: list[float], evaluable_fraction: float,
                                tau: float, *, constraint_improvement_rate: float | None = None,
                                tau_c: float = 0.05, min_eligible_for_variance: int = 5) -> bool:
    """Issue #568/#754/#808 — bool-Wrapper um ``gradient_signal_arm`` (Bestandsschnittstelle,
    Rueckwaertskompatibel: alle drei Arme zaehlen gleichrangig). Siehe ``gradient_signal_arm``-
    Docstring fuer die volle Root-Cause je Arm."""
    return gradient_signal_arm(
        rewards, evaluable_fraction, tau,
        constraint_improvement_rate=constraint_improvement_rate, tau_c=tau_c,
        min_eligible_for_variance=min_eligible_for_variance,
    ) != "none"


def _boundary_hit_analysis(study, strategy: str | None) -> tuple[dict[str, dict], int] | None:
    """Issue #597/#763/#958 (Katalog #960, #1124) — gemeinsame Extraktion für
    ``_boundary_hit_fraction``/``_boundary_hit_directions``/``_boundary_veto_evidence``: liefert
    ``({param: {"direction", "sampled_value", "active_bounds", "default_bounds",
    "distance_to_edge"}}`` je Grenz-Parameter, Gesamtzahl numerischer Gewinner-Parameter)``, oder
    ``None`` wenn Strategie/Bounds/Winner nicht verfügbar sind (defensiv). EINE Quelle für ALLE
    drei Konsumenten — vorher (Root-Cause #958/#1124 Kandidat (b)) verglich die einzige öffentlich
    sichtbare "Beweis"-Grösse im Report (``report.winner_outside_default_bounds``) STRIKTE
    Bounds-Verletzung (``value < lo or value > hi``), während dieses Veto bereits auf blosser NÄHE
    (<= 2 % vom Rand) feuert — ein Gewinner bei ``norm=0.01`` löste das Veto aus, ohne je in
    ``winner_outside_default_bounds`` zu erscheinen (5 von 6 beobachteten Vetos ohne sichtbaren
    Grund im Report, B-Beweis im #1124-Issue). ``active_bounds`` ist die TATSÄCHLICHE
    Sampling-Spanne DIESES Trials (``best_trial.distributions[param].low/.high`` — reflektiert
    einen eventuell #761-geweiterten Suchraum), ``default_bounds`` bleibt die kuratierte Referenz
    (``bounds.extract_numeric_bounds``); beide können divergieren (Root-Cause-Kandidat (a))."""
    if not strategy:
        return None
    try:
        best = study.best_trial
    except Exception:
        return None
    from automation.optimizer import bounds as _bounds
    try:
        b = _bounds.extract_numeric_bounds(strategy)
    except Exception:
        return None
    params = getattr(best, "params", {}) or {}
    distributions = getattr(best, "distributions", {}) or {}
    numeric = [(k, v) for k, v in params.items()
               if k in b and isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numeric:
        return None
    evidence: dict[str, dict] = {}
    for k, v in numeric:
        lo, hi = b[k]
        span = (hi - lo) or 1.0
        norm = (float(v) - lo) / span
        if norm <= 0.02:
            direction, distance_to_edge = "low", round(norm, 6)
        elif norm >= 0.98:
            direction, distance_to_edge = "high", round(1.0 - norm, 6)
        else:
            continue
        dist = distributions.get(k)
        evidence[k] = {
            "direction": direction,
            "sampled_value": v,
            "active_bounds": [getattr(dist, "low", lo), getattr(dist, "high", hi)],
            "default_bounds": [lo, hi],
            "distance_to_edge": distance_to_edge,
        }
    return evidence, len(numeric)


def _boundary_hit_fraction(study, strategy: str | None) -> float | None:
    """Issue #597 — Anteil der numerischen Gewinner-Parameter, die innerhalb von 2 % einer
    Suchraumgrenze liegen. Ein Wert > 0.3 ist ein Alarm: entweder ist der Suchraum falsch gewählt
    oder der Reward drückt die Lösung in die Ecke (Randlösungs-Signatur, z. B. Trade-Frequenz
    maximieren). ``None``, wenn Strategie/Bounds/Winner nicht verfügbar sind (defensiv)."""
    analysis = _boundary_hit_analysis(study, strategy)
    if analysis is None:
        return None
    evidence, total = analysis
    return len(evidence) / total


def _boundary_hit_directions(study, strategy: str | None) -> dict[str, str] | None:
    """Issue #763 — WELCHE Gewinner-Parameter an WELCHER Suchraumgrenze kleben (``"low"``/
    ``"high"``, 2 %-Toleranz, identisch zu ``_boundary_hit_fraction``), statt nur der aggregierten
    Fraktion. Root-Cause #763: die reine Fraktion sagt, DASS eine Randlösung vorliegt, aber nicht,
    WELCHER Parameter in WELCHE Richtung ausgeweitet werden müsste — genau die Information, die
    ``sweep_diagnostics.propose_bounds_from_boundary_hits`` (#761-Cache-Brücke) braucht, um einen
    konkreten ``proposed_bounds``-Kandidaten zu schreiben. ``None`` unter denselben Bedingungen wie
    ``_boundary_hit_fraction``."""
    analysis = _boundary_hit_analysis(study, strategy)
    if analysis is None:
        return None
    evidence, _total = analysis
    return {k: v["direction"] for k, v in evidence.items()}


def _boundary_veto_evidence(study, strategy: str | None) -> dict[str, dict] | None:
    """Issue #958/#1124 (Katalog #960) — die VOLLE, benannte Evidenz je klemmendem
    Gewinner-Parameter (``{sampled_value, active_bounds, default_bounds, distance_to_edge}``,
    dieselbe 2 %-Toleranz/Quelle wie ``_boundary_hit_fraction``/``_boundary_hit_directions``, siehe
    ``_boundary_hit_analysis``-Docstring). Macht jede ``REJECTED_BOUNDARY_SOLUTION``/
    ``HOLD_BOUNDARY_UNRESOLVED``-Entscheidung im Artefakt selbst nachvollziehbar, statt nur über
    eine separate, enger geschnittene Telemetrie-Grösse (``winner_outside_default_bounds``)
    erschliessbar zu sein. ``None``/leer unter denselben Bedingungen wie
    ``_boundary_hit_fraction``."""
    analysis = _boundary_hit_analysis(study, strategy)
    if analysis is None:
        return None
    evidence, _total = analysis
    return evidence or None


def _emit_study_summary(study, symbol: str, study_t0: float, strategy: str | None = None,
                        n_startup_trials: int | None = None, run_id: str | None = None) -> None:
    """Issue #415 — Per-Study-Timing-/Evaluierbarkeits-Summary nach ``study.optimize``.

    Defensiv gegen Test-Doubles (``DummyStudy`` ohne ``trials``/``best_value``): jeder Zugriff ist
    ``getattr``-/try-gekapselt, sodass die Summary nie den Lauf crasht. Aggregiert die per-Trial
    ``backtest_ms`` (User-Attr) zu Total/Median und zaehlt evaluable Trials (``oos_evaluated``).

    Issue #568/#640/#754 — zusätzlich das Gradienten-Signal (``feasible_reward_pstdev``,
    ``feasible_p_eligible``, ``constraint_improvement_rate``, ``gradient_signal``) ausweisen, damit
    eine (aussichtslose) Study NICHT in höhere Tiers eskaliert wird und der Eskalations-Entscheid aus
    dem Log nachvollziehbar ist."""
    # Issue #1086/#1234 (Katalog #1247+, P1) — Root-Cause: ``study.trials`` ist STORE-SCOPED (alle
    # Trials aller Laeufe auf demselben Optuna-Store, ueber Warm-Start-Wiederholungen hinweg
    # akkumuliert), waehrend diese Funktion ``run_id`` bereits als Parameter erhaelt. Die fuenf
    # unten gestempelten Zaehler (``n_trials_total`` etc.) blieben bislang UNGEFILTERT — in
    # Warm-Start-Laeufen z. B. ``n_trials_total = 403`` (= 123+140+140 ueber drei Laeufe) neben
    # dem bereits korrekt run-scopeden ``n_trials_total_study``/``n_evaluable`` aus
    # ``report._study_record`` (Issue #1198, ``trials_override``). Zwei verschiedene
    # Grundgesamtheiten unter aehnlichen Namen (``check_denominator_coherence``/``check_counter_
    # partition_consistency`` failten deshalb NUR in Warm-Start-Laeufen). ``store_trials`` haelt die
    # VOLLE (store-weite) Population fuer die neuen ``_store``-Zaehler unten; ``trials`` wird ab hier
    # auf ``run_id`` gefiltert — dieselbe Filterformulierung wie ``sweep.py``s
    # ``deflation_family_floor``-Zaehlung (dortiger Kommentar).
    store_trials = list(getattr(study, "trials", None) or [])
    trials = store_trials
    if run_id is not None:
        trials = [t for t in store_trials
                  if (getattr(t, "user_attrs", {}) or {}).get("run_id") == run_id]
    durs = [v for t in trials
            if (v := getattr(t, "user_attrs", {}).get("backtest_ms")) is not None]
    evaluable = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_evaluated") is True)
    # Issue #620 — Zähler der #589-Kohärenz-Verletzungen (sichtbar statt still im Subprozess). > 1 %
    # einer Study ⇒ WARNING mit Study-Name.
    coherence_violations = sum(
        1 for t in trials if getattr(t, "user_attrs", {}).get("oos_coherence_violation") is True)
    if trials and coherence_violations / len(trials) > 0.01:
        logging.getLogger("optimizer").warning(
            "[#620] %s: coherence_violations=%d/%d (> 1 %%) — sign(oos_sortino)≠sign(oos_total_return) "
            "häuft sich; Aggregationspfad prüfen.", getattr(study, "study_name", "?"),
            coherence_violations, len(trials),
        )

    # Issue #823 Fix Punkt 4 — Guard-Trips sind eine Diagnose, kein stilles Verwerfen: übersteigt
    # der Anteil zensierter Trips (an ALLEN informativen Trials) eine konfigurierte Schwelle, ist
    # die Suche faktisch zensiert (der Guard löscht systematisch das obere Ende der Zielverteilung)
    # — das Ergebnis dieser Study ist dann NICHT als reguläres 0-/N-eligible-Resultat
    # interpretierbar. STUDY_GUARD_DOMINATED macht diesen Zustand sichtbar, statt ihn als
    # gewöhnliches Suchergebnis zu berichten (ändert KEINE Gate-/Reward-Entscheidung). Der Zähler
    # selbst ist weiter unten definiert (Issue #1063/#1213 — ``_inv._censored_trial_share``).
    guard_trip_fraction_warn = 0.10
    try:
        opt_path_guard = config_dir() / "optimizer.json"
        if opt_path_guard.exists():
            guard_trip_fraction_warn = float(
                (json.loads(opt_path_guard.read_text("utf-8")) or {}).get(
                    "sortino_guard_trip_fraction_warn", guard_trip_fraction_warn))
    except Exception:
        pass
    # Issue #885 (Pitfall #283) — der Nenner fuer STUDY_GUARD_DOMINATED misst absichtlich die
    # tatsaechlich VERWERTBARE ("informative") Trial-Menge, NICHT ``evaluable`` (dieses zaehlt
    # unter ``inference_failure_policy='prune'`` weiterhin geprunte Trials mit: ein geprunter
    # Trial (#864) behaelt sein ``oos_evaluated=True``-User-Attr — es WURDE evaluiert, BEVOR die
    # Pruning-Entscheidung fiel — sein ``TrialState`` ist aber ``PRUNED``, er hat keine informative
    # Beobachtung mehr geliefert. ``evaluable``/``evaluable_fraction`` (unten) bleiben UNVERAENDERT
    # — sie speisen das #568/#640/#754-Gradienten-Gate, dessen Semantik dieser Fix nicht aendert.
    n_trials_pruned = sum(
        1 for t in trials if getattr(t, "state", None) == optuna.trial.TrialState.PRUNED)
    n_trials_failed = sum(
        1 for t in trials if getattr(t, "state", None) == optuna.trial.TrialState.FAIL)
    n_trials_informative = sum(
        1 for t in trials
        if getattr(t, "state", None) == optuna.trial.TrialState.COMPLETE
        and getattr(t, "user_attrs", {}).get("oos_evaluated") is True
    )
    n_trials_unevaluable = max(0, len(trials) - n_trials_pruned - n_trials_failed - n_trials_informative)
    try:
        study.set_user_attr("n_trials_total", len(trials))
        study.set_user_attr("n_trials_informative", n_trials_informative)
        study.set_user_attr("n_trials_pruned", n_trials_pruned)
        study.set_user_attr("n_trials_failed", n_trials_failed)
        study.set_user_attr("n_trials_unevaluable", n_trials_unevaluable)
    except Exception:
        pass
    # Issue #1086/#1234 (Katalog #1247+, P1) Fix Punkt 2 — die STORE-weiten (ungefilterten)
    # Zaehlungen bleiben zusaetzlich unter eindeutigem ``_store``-Suffix erhalten, damit die
    # Store-Groesse (z. B. fuer Betriebs-/Kapazitaetsfragen) weiter sichtbar ist — aber kein
    # Konsument sie mehr mit dem run-scopeden Zaehler oben verwechseln kann (siehe #1235-Folgefix
    # fuer die Invarianten-Konsumenten). Wird ``run_id`` nicht uebergeben, ist ``store_trials ==
    # trials`` (dieselbe Population) und die ``_store``-Werte sind bit-identisch zu den
    # run-scopeden — kein neues Verhalten fuer Aufrufer ohne ``run_id``.
    n_trials_pruned_store = sum(
        1 for t in store_trials if getattr(t, "state", None) == optuna.trial.TrialState.PRUNED)
    n_trials_failed_store = sum(
        1 for t in store_trials if getattr(t, "state", None) == optuna.trial.TrialState.FAIL)
    n_trials_informative_store = sum(
        1 for t in store_trials
        if getattr(t, "state", None) == optuna.trial.TrialState.COMPLETE
        and getattr(t, "user_attrs", {}).get("oos_evaluated") is True
    )
    n_trials_unevaluable_store = max(
        0, len(store_trials) - n_trials_pruned_store - n_trials_failed_store
        - n_trials_informative_store)
    try:
        study.set_user_attr("n_trials_total_store", len(store_trials))
        study.set_user_attr("n_trials_informative_store", n_trials_informative_store)
        study.set_user_attr("n_trials_pruned_store", n_trials_pruned_store)
        study.set_user_attr("n_trials_failed_store", n_trials_failed_store)
        study.set_user_attr("n_trials_unevaluable_store", n_trials_unevaluable_store)
    except Exception:
        pass
    # Issue #1063/#1213 (P1, Katalog #1196-1221) — Root-Cause B-9: Squeeze/NVDA 153/180 (85,0%) bzw.
    # 152/180 (84,4%) zensierte Trials, summary_de.py §5.3 meldete in BEIDEN Laeufen "0". Zwei
    # unabhaengige Defekte: (a) dieser Zaehler zaehlte vormals NUR SORTINO_GUARD_TRIPPED, waehrend
    # ``check_inference_diagnostics_concentration`` einen breiteren Code-Satz zaehlte — behoben,
    # indem beide dieselbe Zaehl-Funktion (``_inv._censored_trial_share``) mit den vom Issue
    # EXPLIZIT benannten Kategorien (SORTINO_GUARD_TRIPPED ∪ SORTINO_INSUFFICIENT_DOWNSIDE)
    # aufrufen; (b) ``report._study_record`` kopierte das hier gestempelte ``study_guard_dominated``-
    # User-Attr NIE in den Study-Record (die eigentliche Ursache des "0"-Symptoms bei 84-85%
    # Zensur, weit ueber JEDER sinnvollen Schwelle — dasselbe Bruecken-Fehlermuster wie #1022/#1171,
    # Pitfall #421, siehe report.py-Fix). Die konfigurierbare Schwelle (``sortino_guard_trip_
    # fraction_warn``, Default 0,10, Issue #823) bleibt UNVERAENDERT — sie ist bereits weit unter
    # dem 50%-Bereich des Symptoms, eine Aenderung der Schwelle selbst war fuer B-9 nicht ursaechlich
    # (siehe test_issue_823_study_guard_dominated.py fuer die bestehende Konfigurierbarkeits-Abdeckung).
    guard_tripped = _inv._censored_trial_share(
        [dict(getattr(t, "user_attrs", {}) or {}) for t in trials])
    # Issue #1291 (GH #1164, Katalog #1272-1297, P2) — UNBEDINGT gestempelt (nicht nur bei
    # study_guard_dominated), damit invariants.check_ineligible_cohort_partition_identity dieselbe
    # Zahl wie check_inference_diagnostics_concentration als vierte Kohorten-Klasse konsumieren
    # kann (Akzeptanzkriterium: beide Werte muessen uebereinstimmen — EINE Zaehl-Funktion,
    # _inv._censored_trial_share, statt einer zweiten, unabhaengig gepflegten Zaehlung).
    try:
        study.set_user_attr("n_guard_censored", guard_tripped)
    except Exception:
        pass
    study_guard_dominated = bool(
        n_trials_informative > 0
        and (guard_tripped / n_trials_informative) > guard_trip_fraction_warn)
    if study_guard_dominated:
        try:
            study.set_user_attr("study_guard_dominated", True)
        except Exception:
            pass
        logging.getLogger("optimizer").warning(
            "[#823/#885/#1213] %s: STUDY_GUARD_DOMINATED — %d/%d informative Trials (%.1f%%) mit "
            "SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE (> %.0f%%) — die Suche ist "
            "faktisch zensiert (der Guard löscht systematisch das obere Ende der Zielverteilung); "
            "dieses Ergebnis ist NICHT als reguläres Eligibility-Resultat interpretierbar.",
            getattr(study, "study_name", "?"), guard_tripped, n_trials_informative,
            100.0 * guard_tripped / n_trials_informative,
            100.0 * guard_trip_fraction_warn,
        )
    # Issue #929 — best_value aus ALLEN abgeschlossenen Trials (Optuna-Semantik), nicht aus
    # Optunas eigenem constraint-gefilterten study.best_value (das unter oos_eligible=False für
    # JEDEN Trial null/undefiniert zurückgibt, siehe _best_completed_value-Docstring).
    try:
        _direction = study.direction.name.lower()
    except Exception:
        _direction = "maximize"
    best_value = _best_completed_value(trials, direction=_direction)
    best_eligible_value = _best_completed_value(
        [t for t in trials if getattr(t, "user_attrs", {}).get("oos_eligible") is True],
        direction=_direction)

    # Issue #611 — p_eligible (Gate-Passrate) EINMALIG bestimmen (wiederverwendet vom #640-
    # Gradienten-Gate UND von der #618-DSR-Kohorte weiter unten).
    n_eligible = sum(1 for t in trials if getattr(t, "user_attrs", {}).get("oos_eligible") is True)
    p_eligible = (n_eligible / len(trials)) if trials else 0.0

    # Issue #568 — Gradienten-Signal. tau deklarativ.
    rewards = [getattr(t, "value", None) for t in trials]
    rewards = [float(r) for r in rewards if isinstance(r, (int, float))]
    evaluable_fraction = (evaluable / len(trials)) if trials else 0.0
    tau = 1e-3
    tau_c = 0.05
    min_eligible_for_variance = 5
    try:
        opt_path = config_dir() / "optimizer.json"
        if opt_path.exists():
            _opt_data_gs = json.loads(opt_path.read_text("utf-8")) or {}
            tau = float(_opt_data_gs.get("tier_escalation_min_signal", tau))
            tau_c = float(_opt_data_gs.get("tier_escalation_min_constraint_progress", tau_c))
            # Issue #808 — Schwelle des Entdeckungs-Arms (siehe gradient_signal_arm-Docstring).
            min_eligible_for_variance = int(_opt_data_gs.get(
                "tier_escalation_min_eligible_for_variance", min_eligible_for_variance))
    except Exception:
        pass
    # Issue #640 — gradient_signal (und die feasible-Diagnose) NICHT mehr auf dem globalen,
    # populations-gemischten reward_pstdev messen: selbst nach #629 (kein Reward-Band mehr) spannt
    # die globale Reward-Verteilung weiterhin die Unevaluable-Shaping-Spanne, die potenziell
    # katastrophale Failure-Region UND das enge Eligible-Band gemeinsam auf — ihr pstdev misst primär
    # die Populations-Mischung (nahe an der Bernoulli-Streuung der Gate-Passrate p_eligible·(1−p_eligible)),
    # nicht die tatsächlich für TPE INNERHALB des feasiblen Bereichs erkletterbare Streuung. Die
    # Eskalations-Entscheidung (mehr Budget lohnt sich nur bei echtem Signal) muss daher auf der
    # FEASIBLE-REGION-Reward-Varianz laufen: nur eligible Trials, mit p_eligible als Usability-Gate
    # (kein Signal ohne mindestens 2 eligible Trials).
    feasible_rewards = [
        float(t.value) for t in trials
        if getattr(t, "user_attrs", {}).get("oos_eligible") is True
        and isinstance(getattr(t, "value", None), (int, float))
    ]
    feasible_reward_pstdev = statistics.pstdev(feasible_rewards) if len(feasible_rewards) >= 2 else 0.0
    # reward_pstdev bleibt als ROHE, globale Diagnose-Telemetrie erhalten (Populations-Streuung über
    # ALLE Trials) — ist aber seit #640 NICHT mehr die Grundlage von gradient_signal.
    reward_pstdev = statistics.pstdev(rewards) if len(rewards) >= 2 else 0.0

    # Issue #754 — Constraint-Fortschritt ueber die MODELLIERTEN Trials (Index >= n_startup_trials,
    # dieselbe Restriktion wie #753): auch bei LEERER feasibler Region (p_eligible == 0, waehrend die
    # Suche noch laeuft der Normalfall) definiert, im Gegensatz zur reward-basierten Streuung.
    _n_startup_for_gs = int(n_startup_trials) if n_startup_trials is not None else 0
    _modelled_for_gs = _modelled_trials(trials, _n_startup_for_gs)
    (min_constraint_violation_first, min_constraint_violation_last,
     constraint_improvement_rate) = _constraint_violation_progress(_modelled_for_gs)

    # Issue #754 — Root-Cause: das Gradienten-Gate war ZIRKULAER — eine Study braucht eligible
    # Trials, um mehr Budget zu bekommen, UND mehr Budget, um eligible Trials zu finden. Eine per
    # #753 VORZEITIG gestoppte Study (Basisbudget nicht ausgeschoepft) liefert daher NICHT `False`
    # (widerlegt), sondern `None` (unbekannt) — die Eskalationsfrage ist schlicht unbeantwortet,
    # nicht mit "kein Signal gefunden" zu verwechseln.
    _study_user_attrs = getattr(study, "user_attrs", None) or {}
    _early_stopped = bool(_study_user_attrs.get("floor_plateau_warned")) or bool(
        _study_user_attrs.get("zero_eligible_plateau_warned"))
    if _early_stopped:
        gradient_signal = None
        gradient_signal_arm_value = None
    else:
        # Issue #808 — EINE Klassifikation (gradient_signal_arm), gradient_signal ist deren
        # bool-Projektion (arm != 'none') statt einer zweiten, separat berechneten Grösse.
        gradient_signal_arm_value = gradient_signal_arm(
            feasible_rewards, p_eligible, tau,
            constraint_improvement_rate=constraint_improvement_rate, tau_c=tau_c,
            min_eligible_for_variance=min_eligible_for_variance)
        gradient_signal = gradient_signal_arm_value != "none"

    # Issue #930 — Budget-Ausfuehrungsgrad VORGEZOGEN (statt erst weiter unten berechnet, siehe
    # #770-Block): die [#640]-Meldung braucht ihn jetzt direkt als Ausloesebedingung.
    budget_execution = compute_budget_execution(
        trials, n_trials_budget=_study_user_attrs.get("n_trials_budget"),
        n_startup_trials=n_startup_trials, study_user_attrs=_study_user_attrs, run_id=run_id)

    # Issue #1264 (GH #1134) Fix Punkt 1 — Root-Cause: der #681/#829/#830/#831-Rückschriebpfad
    # (weiter oben in dieser Datei, in den STRUCTURAL_ALL_UNEVALUABLE-/ZERO_ELIGIBLE_PLATEAU-
    # Fruehstopp-Zweigen) ist auf das strenge sequentielle Fruehstopp-Kriterium ANGEWIESEN — eine
    # Study, die ihr VOLLES Budget durchlaeuft, OHNE dass die Fruehstopp-Statistik je feuert, erreicht
    # diesen Code NIE, obwohl ``stop_reason`` (oben berechnet, IMMER fuer jede abgeschlossene Study
    # bekannt) sie unzweideutig als STRUCTURAL_ZERO_ELIGIBLE/STRUCTURAL_ALL_UNEVALUABLE klassifiziert
    # (Symptom: 10 Studies ohne diagnosed_pairs-Eintrag, obwohl ``stop_reason`` sie auswies). Dieser
    # Block laeuft UNBEDINGT fuer JEDE abgeschlossene Study mit einem der beiden ``stop_reason``-Werte
    # — unabhaengig davon, ob der obige Fruehstopp-Pfad BEREITS geschrieben hat: ``record_diagnosed_
    # pair``s eigener ``study_fingerprint``-Dedup (#1090) verhindert eine doppelte ``n_runs_confirmed``-
    # Zaehlung fuer dieselbe Study-Beobachtung, ein zweiter Aufruf ist daher sicher.
    if strategy is not None and symbol is not None and (
            budget_execution["stop_reason"] in ("STRUCTURAL_ZERO_ELIGIBLE", "STRUCTURAL_ALL_UNEVALUABLE")):
        try:
            from automation.optimizer.sweep_diagnostics import (
                diagnose_structural_zero_eligible_gate, record_diagnosed_pair, study_fingerprint,
            )
            _all_rejection_details_for_writeback = [
                getattr(t, "user_attrs", {}).get("is_rejection_detail") for t in trials
            ]
            _rejection_detail_counts_for_writeback: dict[str, int] = {}
            for _d in _all_rejection_details_for_writeback:
                if _d:
                    _rejection_detail_counts_for_writeback[_d] = (
                        _rejection_detail_counts_for_writeback.get(_d, 0) + 1)
            # Issue #1303 (GH #1180) Fix Punkt 1/2 — dieselbe IS-Aktivitaets-/worker_error-
            # Grundgesamtheit wie report._study_record, hier direkt aus den Trial-User-Attrs
            # gehoben (dieser Block laeuft VOR jedem Report-Lauf, siehe Docstring oben).
            _is_trade_counts_for_writeback = [
                int(v) for v in (getattr(t, "user_attrs", {}).get("is_total_trades") for t in trials)
                if v is not None
            ]
            _median_is_trades_for_writeback = (
                statistics.median(_is_trade_counts_for_writeback)
                if _is_trade_counts_for_writeback else None)
            _max_is_trades_for_writeback = (
                max(_is_trade_counts_for_writeback) if _is_trade_counts_for_writeback else None)
            _worker_error_counts_for_writeback: dict[str, int] = {}
            for _t in trials:
                _werr = getattr(_t, "user_attrs", {}).get("worker_error")
                if _werr:
                    _worker_error_counts_for_writeback[_werr] = (
                        _worker_error_counts_for_writeback.get(_werr, 0) + 1)
            _structural_diagnosis = diagnose_structural_zero_eligible_gate(
                _rejection_detail_counts_for_writeback, stop_reason=budget_execution["stop_reason"],
                max_is_trades=_max_is_trades_for_writeback,
                median_is_trades=_median_is_trades_for_writeback,
                worker_error_counts=_worker_error_counts_for_writeback)
            if _structural_diagnosis["binding_cause"] not in (None, "none"):
                _structural_rec = {
                    "strategy": strategy, "symbol": symbol,
                    "action": _structural_diagnosis["proposed_action"],
                    "binding_cause": _structural_diagnosis["binding_cause"],
                    "dominant_rejection_detail": _structural_diagnosis["dominant_rejection_detail"],
                    "dominant_fraction": _structural_diagnosis["dominant_fraction"],
                    "budget_executed_fraction": budget_execution["budget_executed_fraction"],
                    "study_fingerprint": study_fingerprint(
                        getattr(study, "study_name", None),
                        _study_user_attrs.get("study_started_at_utc"),
                        budget_execution["n_trials_completed"]),
                }
                record_diagnosed_pair(_structural_rec, run_id=run_id)
        except Exception:
            logging.getLogger("optimizer").debug(
                "Issue #1264: unbedingter Struktur-Diagnose-Rueckschrieb fehlgeschlagen (non-fatal).",
                exc_info=True)

    # Issue #930 (Pitfall #303) — die Ausloesebedingung war `gradient_signal is None`, ein PROXY
    # fuer "Basisbudget nicht ausgeschoepft" aus der Zeit, als der Fruehstopp bei
    # `n_startup + 3*dim` griff (#805/#806). Seit #925 kann `budget_executed_fraction` bei einem
    # STRUCTURAL_ZERO_ELIGIBLE-Stopp bei 0,99 liegen (praktisch voll ausgefuehrt), waehrend der
    # Proxy trotzdem `None` liefert (early_stopped=True) — die Meldung feuerte dann auf einem
    # Basisbudget, das TATSAECHLICH erschoepft war. Die direkte Groesse (budget_executed_fraction)
    # liegt seit #770 vor und ersetzt den Proxy jetzt.
    _min_median_budget_execution = 0.5
    try:
        _opt_path_640 = config_dir() / "optimizer.json"
        if _opt_path_640.exists():
            _min_median_budget_execution = float(
                (json.loads(_opt_path_640.read_text("utf-8")) or {}).get(
                    "min_median_budget_execution", 0.5))
    except Exception:
        _min_median_budget_execution = 0.5
    _budget_left = (budget_execution["budget_executed_fraction"] is None
                    or budget_execution["budget_executed_fraction"] < _min_median_budget_execution)

    if gradient_signal is None and _budget_left:
        logging.getLogger("optimizer").info(
            "[#640] %s: Study vorzeitig beendet — Eskalationsfrage unbeantwortet "
            "(budget_executed_fraction=%s < %.2f, gradient_signal=None). feasible_p_eligible=%.2f, "
            "feasible_reward_pstdev=%.4f, constraint_improvement_rate=%s.",
            symbol, budget_execution["budget_executed_fraction"], _min_median_budget_execution,
            p_eligible, feasible_reward_pstdev, constraint_improvement_rate,
        )
    elif not gradient_signal and gradient_signal is not None:
        logging.getLogger("optimizer").warning(
            "[#640] %s: kein Gradienten-Signal (weder feasibler Reward-Streuung noch Constraint-"
            "Annaeherung) — feasible_p_eligible=%.2f, feasible_reward_pstdev=%.4f ≤ τ=%.4f, "
            "constraint_improvement_rate=%s ≤ τ_c=%.4f ⇒ KEINE Tier-Eskalation gerechtfertigt "
            "(zusätzliches Budget auf flacher Feasible-Region-Landschaft ist wirkungslos).",
            symbol, p_eligible, feasible_reward_pstdev, tau, constraint_improvement_rate, tau_c,
        )

    # Issue #592 — Deflations-Telemetrie auf der REWARD-Skala (je Study sichtbar, nicht nur im
    # Holdout-Pfad). Nutzt die evaluable Trial-Rewards (das tatsächliche argmax-Selektionskriterium).
    # Issue #611 — DSR-Kohorten-Telemetrie auf der ELIGIBLEN PER-PERIODEN-Sortino-Skala. Die alte
    # Reward-Skalen-Deflation über ALLE oos_evaluated-Trials schätzte σ auf einer Zwei-Punkt-Mischung
    # ⇒ Bernoulli-Standardabweichung der Passrate (anti-monoton). Jetzt: die Streuung ÜBER die
    # eligiblen Sortinos (die tatsächlichen argmax-Konkurrenten) ⇒ SR₀ (#618).
    deflated_selection, deflation_confidence = _read_deflation_config()
    cohort_sr = [getattr(t, "user_attrs", {}).get("oos_sortino_period") for t in trials
                 if getattr(t, "user_attrs", {}).get("oos_eligible") is True
                 and getattr(t, "user_attrs", {}).get("oos_sortino_period") is not None]
    deflation_n = len(cohort_sr)
    deflation_sr0 = deflation_var = None
    deflation_used_var_floor = False
    deflation_lambda = None
    deflation_theoretical_var_source = None
    if deflated_selection and deflation_n >= 2:
        from automation.optimizer.deflation import sr0_multiple_testing_robust
        deflation_var = statistics.pvariance([float(s) for s in cohort_sr])
        # Issue #636/#653 — dieselbe Small-Cohort-Robustifizierung wie confirm.py (Single Source of
        # Truth, Pitfall #131): der theoretisch begründete, T-bewusste Varianz-Floor (Lo 2002) wird
        # STETIG in N gegen die empirische Kohorten-Varianz geshrinkt — dieselbe Funktion, dieselben
        # Parameter wie in der PROMOTIONS-Entscheidung (confirm.py), sonst würde die Study-Summary-
        # Telemetrie erneut vom promotion-entscheidenden SR₀ abweichen (exakt der #651-Fehler).
        opt_path_dsr = config_dir() / "tournament.json"
        min_cohort = 10
        try:
            if opt_path_dsr.exists():
                _tcfg = json.loads(opt_path_dsr.read_text("utf-8")) or {}
                min_cohort = int(_tcfg.get("deflation_min_cohort", min_cohort))
        except Exception:
            pass
        cohort_n_periods = [getattr(t, "user_attrs", {}).get("oos_n_periods") for t in trials
                            if getattr(t, "user_attrs", {}).get("oos_eligible") is True
                            and getattr(t, "user_attrs", {}).get("oos_sortino_period") is not None
                            and getattr(t, "user_attrs", {}).get("oos_n_periods")]
        deflation_t_periods = int(statistics.median(cohort_n_periods)) if cohort_n_periods else None
        # Issue #670 — dieselbe (Rückwärtskompat-)Signatur wie confirm.py: ``deflation_used_var_floor``
        # bedeutet nur "λ ≥ 0.5"; ``deflation_lambda``/``deflation_theoretical_var_source`` sind die
        # präzisen Grössen für die Study-Summary-Telemetrie.
        # Issue #701 — ``deflation_t_periods`` ist NIE ``None``, wenn ``deflation_n >= 2`` (siehe
        # confirm.py-Kommentar/deflation.py-Docstring für die Invarianten-Herleitung); der var_floor-
        # Fallback wurde nach Verifikation entfernt. Defense-in-Depth statt Crash, falls die
        # Invariante durch eine künftige Datenanomalie doch einmal bricht.
        if deflation_t_periods is None:
            logging.getLogger("optimizer").warning(
                "[DSR #701] %s: deflation_t_periods fehlt trotz deflation_n=%d >= 2 — sollte laut "
                "Invariante unerreichbar sein. SR₀ bleibt fuer diese Study-Summary unberechnet.",
                symbol, deflation_n,
            )
        else:
            (deflation_sr0, deflation_used_var_floor, deflation_lambda,
             deflation_theoretical_var_source) = sr0_multiple_testing_robust(
                deflation_var, deflation_n, min_cohort=min_cohort,
                n_periods=deflation_t_periods)

    # Issue #597/#763 — Randlösungs-Telemetrie (Anteil der Gewinner-Parameter an der
    # Suchraumgrenze, PLUS welcher Parameter an welcher Grenze klebt — #763 Akzeptanzkriterium #1).
    boundary_hit_fraction = _boundary_hit_fraction(study, strategy)
    boundary_hit_directions = _boundary_hit_directions(study, strategy)
    if boundary_hit_fraction is not None and boundary_hit_fraction > 0.3:
        directions_str = ", ".join(
            f"{p}={d}" for p, d in sorted((boundary_hit_directions or {}).items()))
        logging.getLogger("optimizer").warning(
            "[#597] %s: boundary_hit_fraction=%.2f > 0.3 — der Gewinner klebt an den Suchraumgrenzen "
            "(Randlösung: %s). Suchraum prüfen ODER Reward-Konditionierung (Turnover/Drawdown).",
            symbol, boundary_hit_fraction, directions_str,
        )
        # Issue #831 Fix Punkt 1 — confirm.py schreibt seit #777 denselben Bounds-Vorschlag/Cache-
        # Eintrag (binding_cause='boundary_solution'), aber NUR für Studies, die confirm_per_symbol_
        # promotion tatsächlich erreichen (>= 1 eligibler Trial, siehe dortiges `if not eligible_
        # trials: return`). Eine Study, die VOLLSTÄNDIG durchläuft, aber NIE einen eligiblen Trial
        # produziert (STRUCTURAL_ALL_UNEVALUABLE/ZERO_ELIGIBLE_PLATEAU), erreicht diesen Code in
        # confirm.py NIE — das war die #831-Lücke. n_eligible==0 hier UND confirm.py's eigenes Gate
        # sind exklusiv (genau einer der beiden Pfade feuert je Study/Lauf) — kein doppeltes
        # record_diagnosed_pair für dasselbe Paar im selben Lauf (das würde n_runs_confirmed
        # fälschlich zweimal statt einmal pro Lauf inkrementieren).
        n_eligible_for_boundary = sum(
            1 for t in trials if getattr(t, "user_attrs", {}).get("oos_eligible") is True)
        if n_eligible_for_boundary == 0 and strategy is not None:
            try:
                from automation.optimizer.sweep_diagnostics import (
                    propose_bounds_from_boundary_hits, record_diagnosed_pair,
                )
                _widen_fraction_boundary = 0.3
                try:
                    _opt_path_boundary = config_dir() / "optimizer.json"
                    if _opt_path_boundary.exists():
                        _widen_fraction_boundary = float(
                            (json.loads(_opt_path_boundary.read_text("utf-8")) or {})
                            .get("bounds_widening_factor", 0.3))
                except Exception:
                    pass
                proposed_bounds_boundary = propose_bounds_from_boundary_hits(
                    boundary_hit_directions or {}, strategy, widen_fraction=_widen_fraction_boundary)
                if proposed_bounds_boundary:
                    try:
                        _boundary_params = dict(getattr(study.best_trial, "params", {}) or {})
                    except Exception:
                        _boundary_params = {}
                    record_diagnosed_pair({
                        "strategy": strategy, "symbol": symbol,
                        "action": "search_space_override",
                        "binding_cause": "boundary_solution",
                        "proposed_bounds": proposed_bounds_boundary,
                        "boundary_hit_fraction": boundary_hit_fraction,
                        "boundary_params": _boundary_params,
                    })
            except Exception:
                logging.getLogger("optimizer").debug(
                    "[#831] %s/%s: Boundary-Bounds-Vorschlag konnte nicht in den #761-Cache "
                    "geschrieben werden (non-fatal).", strategy, symbol, exc_info=True,
                )

    # Issue #660 — LIVE-Reachability-Check der eligible_requires_any-Klauseln GEGEN DIE TATSÄCHLICH
    # in DIESER Study beobachtete empirische Verteilung (nicht nur das statische #633-Cross-Strategy-
    # Fixture, das bereits beim Config-Load lief, BEVOR irgendein Trial existierte). Ein Symbol/eine
    # Strategie kann eine Schwelle strukturell nie erreichen, obwohl das globale Fixture sie als
    # 'erreichbar' einstuft (#660-Root-Cause: oos_min_win_rate=0.15 < Fixture-p99=0.197, aber die
    # für TSLA.ETORO Hourly tatsächlich beobachtete OOS-Win-Rate blieb unter ~0.11).
    live_win_rates = [getattr(t, "user_attrs", {}).get("oos_win_rate") for t in trials
                      if getattr(t, "user_attrs", {}).get("oos_win_rate") is not None]
    # Issue #1093/#1241 (P1) — dieselbe Live-Kohorte fuer das neue MANDATORY oos_min_alpha_tstat-
    # Gate (siehe reward.check_mandatory_gate_reachability_live-Docstring).
    # Issue #1255 (GH #1125), Pitfall #454-Klasse — der Handler selbst
    # (backtest_runner._evaluate_oos_eligibility) konsumiert seit diesem Fix oos_alpha_tstat_hc3
    # statt der klassischen Statistik; die Live-Kohorte MUSS auf DERSELBEN Groesse sitzen, sonst
    # diagnostiziert sie die Erreichbarkeit einer Statistik, die gar nicht mehr entscheidet. Fallback
    # auf die klassische Statistik je Trial nur fuer Legacy-Trials ohne das neue User-Attr.
    live_alpha_tstats = [
        (getattr(t, "user_attrs", {}).get("oos_alpha_tstat_hc3")
         if getattr(t, "user_attrs", {}).get("oos_alpha_tstat_hc3") is not None
         else getattr(t, "user_attrs", {}).get("oos_alpha_tstat"))
        for t in trials
    ]
    live_alpha_tstats = [v for v in live_alpha_tstats if v is not None]
    any_arm_live_unreachable = []
    # Issue #1280/#1281 (GH #1153/#1154, Katalog #1272-1297, P0) — getrennt von
    # ``any_arm_live_unreachable`` gehalten: eine ``requires_all``-Klausel (jeder Trial wird
    # abgelehnt) ist ein STRUKTURELL anderer Befund als eine ``requires_any``-Klausel (kollabiert
    # auf die uebrigen Arme) — siehe ``_emit_mandatory_gate_reachability_result``-Docstring.
    mandatory_gate_live_unreachable: list[str] = []
    # Issue #668 — hebt die reine #660-Warnung auf eine KONFIGURIERTE Policy (warn/drop_arm/
    # recalibrate). Default 'warn' liefert eine leere Entscheidung (bit-identisch zu #660).
    any_arm_policy_decision = {"policy": "warn", "dropped_clauses": [], "recalibrated_thresholds": {},
                               "any_arm_decision": None}
    _tcfg_arm: dict = {}
    try:
        opt_path_arm = config_dir() / "tournament.json"
        if opt_path_arm.exists():
            _tcfg_arm = json.loads(opt_path_arm.read_text("utf-8")) or {}
            # Issue #759 — n_evaluated durchreichen: eine Reachability-Aussage ohne einen einzigen
            # ausgewerteten Trial ist inhaltsleer (siehe check_any_arm_reachability_live-Docstring).
            any_arm_live_unreachable = check_any_arm_reachability_live(
                _tcfg_arm, {"min_win_rate": live_win_rates}, n_evaluated=evaluable)
            any_arm_policy_decision = resolve_any_arm_policy(
                _tcfg_arm, {"min_win_rate": live_win_rates}, n_evaluated=evaluable)
            _emit_any_arm_reachability_result(
                logging.getLogger("optimizer"), any_arm_live_unreachable,
                check_name="check_any_arm_reachability_live",
                scope=getattr(study, "study_name", None))
            # Issue #1093/#1241 — die READ-ONLY Diagnose fuer das neue MANDATORY-Gate.
            # Issue #1280/#1281 (GH #1153/#1154) — NICHT mehr in any_arm_live_unreachable gemergt
            # und NICHT mehr unter check_any_arm_reachability_live emittiert (Root-Cause #1281:
            # ein requires_all-Gate erschien unter Namen/Text/Severity der requires_any-Pruefung,
            # obwohl die reale Konsequenz gegensaetzlich ist — "jeder Trial wird abgelehnt" statt
            # "kollabiert auf die uebrigen Arme"). Eigener Emit-Aufruf, eigener Check-Name.
            # Issue #1247 (GH #1117) — der Schlüssel MUSS die normalisierte (unpräfigierte) Form
            # tragen, unabhängig davon, ob tournament.json['eligible_requires_all'] die Klausel
            # mit oder ohne 'oos_'-Präfix listet (Pitfall #448): reward._normalize_clause ist die
            # EINE Stelle, die diese Form definiert.
            mandatory_gate_live_unreachable = check_mandatory_gate_reachability_live(
                _tcfg_arm,
                {_reward_normalize_clause("oos_min_alpha_tstat"): live_alpha_tstats},
                n_evaluated=evaluable)
            _emit_mandatory_gate_reachability_result(
                logging.getLogger("optimizer"), mandatory_gate_live_unreachable,
                scope=getattr(study, "study_name", None))
    except Exception:
        any_arm_live_unreachable = []
        mandatory_gate_live_unreachable = []

    # Issue #1250 (GH #1120), Pitfall #451 — die EFFEKTIVE oos_min_alpha_tstat-Schwelle DIESER
    # Study (reward.resolve_alpha_tstat_gate_threshold-Docstring). n_family_stage1/
    # oos_n_periods_median sind FAMILIEN-Groessen, die erst nach Abschluss ALLER Studies eines
    # Symbols bekannt sind (sweep._family_n_stage1_from_studies/_study_oos_n_periods_median,
    # confirm.py/report.py) — zur Laufzeit DIESER einzelnen Study liegt weder eine Familiengroesse
    # noch ein Kalibrier-Fixture vor, daher bleiben beide hier unbesetzt (None). Der Call bleibt
    # dennoch verdrahtet statt zu entfallen: mode='static' (Default, siehe tournament.json
    # ['oos_min_alpha_tstat_mode']) ist ohnehin bit-identisch zur rohen Config-Konstante, und
    # resolve_alpha_tstat_gate_threshold faellt selbst bei mode='multiplicity_adjusted' ohne
    # Fixture FAIL-OPEN auf 'static' zurueck (siehe dortiger Docstring) — ein zukuenftiger
    # Kalibrierlauf kann calibration_fixture/n_family_stage1/oos_n_periods_median hier ergaenzen,
    # ohne diese Call-Site selbst nochmal aendern zu muessen.
    alpha_tstat_gate_threshold_effective, alpha_tstat_gate_threshold_source = (
        resolve_alpha_tstat_gate_threshold(_tcfg_arm))
    try:
        study.set_user_attr("alpha_tstat_gate_threshold_effective", alpha_tstat_gate_threshold_effective)
        study.set_user_attr("alpha_tstat_gate_threshold_source", alpha_tstat_gate_threshold_source)
    except Exception:
        pass

    # Issue #812 — Fingerabdruck der EFFEKTIV wirksamen Gate-Konfiguration DIESER Study (Schwellen
    # inkl. aller #668-Policy-Anpassungen, siehe reward.selection_rule_fingerprint-Docstring) — als
    # study.user_attr gestempelt, damit report._study_record ihn ohne erneute Live-Kohorten-
    # Berechnung auslesen kann (Single Source of Truth: die tatsaechlich wirksame Policy-
    # Entscheidung entsteht HIER, aus der vollen Trial-Kohorte). Voraussetzung fuer eine gueltige
    # familienweite DSR-Multiplizitaetskorrektur (sweep._family_n_from_studies, Pitfall #248).
    # Issue #1250 (GH #1120) — alpha_tstat_gate_threshold_effective fliesst mit ein (siehe oben).
    selection_fingerprint = selection_rule_fingerprint(
        _tcfg_arm, any_arm_policy_decision,
        alpha_tstat_gate_threshold_effective=alpha_tstat_gate_threshold_effective,
    )
    try:
        study.set_user_attr("selection_rule_fingerprint", selection_fingerprint)
    except Exception:
        pass

    # Issue #667/#760 — Rang-Korrelationsmatrix der AKTIVEN eligible-Gates (aus tournament.json
    # abgeleitet, keine eingefrorene Code-Konstante mehr) über die gestempelten Per-Trial
    # oos_gate_deltas (#554/#668). Reine Telemetrie/Warnung — ändert NIE eine Gate-/Reward-
    # Entscheidung; welches Gate ggf. konsolidiert wird, ist eine bewusste PR-Wahl.
    gate_deltas_cohort = [getattr(t, "user_attrs", {}).get("oos_gate_deltas") for t in trials]
    _tcfg_gate: dict = {}
    try:
        opt_path_gate = config_dir() / "tournament.json"
        if opt_path_gate.exists():
            _tcfg_gate = json.loads(opt_path_gate.read_text("utf-8")) or {}
    except Exception:
        _tcfg_gate = {}
    try:
        gate_collinearity = assert_gate_collinearity_guard(gate_deltas_cohort, _tcfg_gate)
    except Exception:
        gate_collinearity = {"n_samples": 0, "keys": [], "correlations": {}, "non_correlable_keys": []}
    # Issue #679 — dieselbe Kohorte, aber als STRUKTURIERTER Redundanz-ALARM statt nur eines
    # WARNING-Logs: welches Gate-Paar kollinear ist UND welches (niedriger priorisierte, siehe
    # reward._GATE_CONSOLIDATION_PRIORITY) Gate der Konsolidierungs-Kandidat waere.
    try:
        gate_collinearity_alarm = gate_collinearity_redundancy_alarm(gate_deltas_cohort, _tcfg_gate)
    except Exception:
        gate_collinearity_alarm = {"n_samples": 0, "alarms": [], "redundant_candidates": {}}

    # Issue #621 — Reward-Term-Dekomposition
    # Issue #980 (Katalog C, P1, Pitfall #302 in AGENTS.md) — VORHER auf den branch in ('eligible',
    # 'per_symbol', 'pareto') gefiltert: dieselbe Selection-on-the-dependent-variable-Falle wie
    # invariants._evaluated_reward_terms bereits dokumentiert (das ist die 2.15%-Kohorte aus #979,
    # in 27 von 28 Studies des Referenzlaufs 46cf5070 LEER, weil p_eligible == 0). Root-Cause von
    # #980: reward_terms_aggregates.terms war dadurch in 27/28 Studies {} — trotz identischer
    # verfügbarer Trial-Population wie invariants.check_reward_term_variance/reward_term_variance_
    # table (die BEIDE bereits auf oos_evaluated=True ohne Branch-Filter rechnen und daher NICHT
    # leer waren). Konsistent auf dieselbe Population (oos_evaluated=True, jeder Branch) umgestellt.
    eligible_terms = []
    for t in trials:
        if getattr(t, "user_attrs", {}).get("oos_evaluated") is True:
            terms = getattr(t, "user_attrs", {}).get("reward_terms")
            if terms:
                eligible_terms.append(terms)

    term_aggregates = {
        "divergence_at_cap": 0.0,
        "floor_clamped": 0.0,
        "terms": {}
    }

    if eligible_terms:
        n_el = len(eligible_terms)
        term_aggregates["divergence_at_cap"] = sum(1 for t in eligible_terms if t.get("divergence_at_cap")) / n_el
        term_aggregates["floor_clamped"] = sum(1 for t in eligible_terms if t.get("floor_clamped")) / n_el

        numeric_keys = ["base", "divergence", "dd_penalty", "param_pen", "turnover", "fold_dispersion", "tie_breaker"]

        rew_vals = []
        for t in eligible_terms:
            r = (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
                 - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
                 + t.get("tie_breaker", 0.0))
            rew_vals.append(r)

        rew_std = statistics.pstdev(rew_vals) if n_el >= 2 else 0.0
        rew_var = rew_std ** 2

        for k in numeric_keys:
            vals = [float(t.get(k, 0.0)) for t in eligible_terms]
            std_k = statistics.pstdev(vals) if n_el >= 2 else 0.0
            med_k = statistics.median(vals) if vals else 0.0

            var_contrib = 0.0
            if n_el >= 2 and rew_var > 0.0:
                mean_k = sum(vals) / n_el
                mean_r = sum(rew_vals) / n_el
                cov = sum((vals[i] - mean_k) * (rew_vals[i] - mean_r) for i in range(n_el)) / n_el
                var_contrib = cov / rew_var

            term_aggregates["terms"][k] = {
                "median": med_k,
                "std": std_k,
                "var_contrib": var_contrib
            }

            if std_k < 0.01 * rew_std:
                logging.getLogger("optimizer").warning("REWARD_TERM_INERT: %s", k)

    # Issue #770/#930 — budget_execution wurde bereits weiter oben (vor der [#640]-Meldung)
    # berechnet; hier nur noch die #770-Konsequenz (dieselbe Berechnung wie
    # ``report._study_record``, kein zweiter Aufruf mehr).
    if (budget_execution["budget_executed_fraction"] is not None
            and budget_execution["budget_executed_fraction"] < 0.5):
        logging.getLogger("optimizer").warning(
            "[#770] %s: budget_executed_fraction=%.2f (< 50%%) — stop_reason=%s. Ein grosser Teil "
            "des konfigurierten Suchbudgets (%s/%s Trials) wurde nicht ausgefuehrt.",
            symbol, budget_execution["budget_executed_fraction"], budget_execution["stop_reason"],
            budget_execution["n_trials_completed"], budget_execution["n_trials_budgeted"],
        )

    _study_wallclock_s = round(time.perf_counter() - study_t0)
    # Issue #932 (Pitfall #305) — als Study-User-Attr gestempelt (nicht nur im Log-Event), damit
    # report._study_record sie in einen künftigen #742-Report übernimmt: die EINE Quelle, aus der
    # sweep._read_last_study_wallclock_by_strategy den LPT-Dispatch (Longest-Processing-Time) des
    # NÄCHSTEN Laufs speist.
    try:
        study.set_user_attr("wallclock_s", _study_wallclock_s)
    except Exception:
        pass
    # Issue #983 (Katalog D, P0 HEADLINE) — der Wallclock-Preflight rechnete bislang mit dem MEDIAN
    # von backtest_ms, obwohl die Verteilung rechtsschief ist (Referenzlauf 46cf5070: Median 7.575s
    # vs. Mittelwert 9.690s, Faktor 1.279 — einer von drei multiplikativen Fehlerquellen, die den
    # Preflight um Faktor ~1.90 unterschätzen liessen). ``backtest_ms_mean`` ist das Rohmaterial für
    # ``sweep._read_last_backtest_ms_mean`` des NÄCHSTEN Laufs (analog #931s Median-Ledger).
    try:
        study.set_user_attr("backtest_ms_mean", (sum(durs) / len(durs)) if durs else None)
    except Exception:
        pass

    emit_execution_event(logging.getLogger("optimizer"), "optimizer_study_completed", {
        "study_name": getattr(study, "study_name", None),
        "symbol": symbol,
        "n_trials": len(trials),
        "evaluable_trials": evaluable,
        # Issue #770 — Budget-Ausfuehrungsgrad als erstklassige Study-Kennzahl (siehe
        # compute_budget_execution-Docstring).
        "n_trials_budgeted": budget_execution["n_trials_budgeted"],
        "n_trials_completed": budget_execution["n_trials_completed"],
        # Issue #1015 (Katalog #858, Fix Punkt 1) — die volle Study-SQLite-Historie (alle Läufe,
        # gepurged oder nicht) als separate Telemetrie neben ``n_trials_completed`` (nur DIESER
        # Lauf, sofern ``run_id`` gesetzt war); eine grosse Lücke zwischen beiden Zahlen macht eine
        # ungepurgte Study sichtbar.
        "n_trials_total_study": budget_execution["n_trials_total_study"],
        "budget_executed_fraction": budget_execution["budget_executed_fraction"],
        # Issue #983 Fix Punkt 3 Akzeptanzkriterium — die wallclock_budget_policy='degrade'-Kürzung
        # (#931) darf niemals stillschweigend geschehen: IMMER vorhanden (1.0 = kein Degrade), damit
        # jede Study nachvollziehbar bleibt, ohne den Ereignis-Log durchsuchen zu müssen.
        "budget_degradation_factor": getattr(study, "user_attrs", {}).get(
            "budget_degradation_factor", 1.0),
        "stop_reason": budget_execution["stop_reason"],
        "n_modelled_trials_completed": budget_execution["n_modelled_trials_completed"],
        "best_value": best_value,
        # Issue #929 — getrenntes Feld: der beste Reward NUR über die eligible Kohorte (None, wenn
        # p_eligible == 0 — hier ist die Leermenge inhaltlich korrekt, kein Constraint-Artefakt).
        "best_eligible_value": best_eligible_value,
        # Issue #929 Fix 2 — Trial-Nummer des besten abgeschlossenen Trials, unabhängig von der
        # Constraint-Feasibility (siehe _best_completed_trial_number-Docstring).
        "best_trial_number": _best_completed_trial_number(trials, direction=_direction),
        "backtest_ms_total": sum(durs) if durs else 0,
        "backtest_ms_median": int(statistics.median(durs)) if durs else None,
        "wallclock_s": _study_wallclock_s,
        # Issue #568 — globale Roh-Diagnose (Populations-Streuung über ALLE Trials, NICHT die
        # Eskalations-Grundlage seit #640 — siehe feasible_reward_pstdev/gradient_signal unten).
        "reward_pstdev": reward_pstdev,
        "evaluable_fraction": evaluable_fraction,
        # Issue #640 — die tatsaechliche Eskalations-Grundlage: Reward-Varianz NUR über die eligible
        # Kohorte (feasible_reward_pstdev) + deren Passrate (feasible_p_eligible), getrennt von der
        # globalen Populations-Streuung emittiert, damit "echtes Signal" nicht mit "Passrate > 0"
        # verwechselt wird. gradient_signal ist jetzt auf feasible_reward_pstdev/feasible_p_eligible
        # begründet, nicht mehr auf reward_pstdev/evaluable_fraction.
        "feasible_reward_pstdev": feasible_reward_pstdev,
        "feasible_p_eligible": p_eligible,
        # Issue #754 — gradient_signal ist jetzt TRI-STATE (true/false/null): null bedeutet "Study
        # vorzeitig gestoppt, Eskalationsfrage unbeantwortet" (NICHT "kein Signal gefunden").
        "gradient_signal": gradient_signal,
        # Issue #808 — WELCHER der drei Arme (discovery/reward_variance/constraint_progress/none)
        # das obige gradient_signal traegt. None ⇒ wie gradient_signal selbst unbeantwortet
        # (Early-Stop).
        "gradient_signal_arm": gradient_signal_arm_value,
        "constraint_improvement_rate": constraint_improvement_rate,
        "min_constraint_violation_first": min_constraint_violation_first,
        "min_constraint_violation_last": min_constraint_violation_last,
        # Issue #611/#618 — DSR-Telemetrie (per-Perioden-Sortino-Skala) + p_eligible (Gate-Passrate,
        # identisch zu feasible_p_eligible — hier unter dem historischen Namen für die DSR-Kohorte).
        # Monotonie-Invariante (#611): SR₀ steigt NICHT mit p_eligible (kein Bernoulli-Artefakt mehr).
        "p_eligible": p_eligible,
        "deflation_n_eligible": deflation_n,
        "deflation_sr0": deflation_sr0,
        "deflation_var_sr": deflation_var,
        # Issue #636 — sichtbar, ob das Shrinkage-Gewicht λ ≥ 0.5 ist (die theoretische Referenz
        # dominiert die Blend-Gewichtung — NICHT "die var_floor-Konstante wurde verwendet", #670).
        "deflation_used_var_floor": deflation_used_var_floor,
        # Issue #670/#701 — die präzisen Grössen: das tatsächliche Shrinkage-Gewicht λ und die
        # theoretische Referenz-Quelle (seit #701 IMMER 'lo2002' — der var_floor-Fallback ist entfernt).
        "deflation_lambda": deflation_lambda,
        "deflation_theoretical_var_source": deflation_theoretical_var_source,
        # Issue #620 — #589-Kohärenz-Verletzungen je Study (beobachtbar).
        "coherence_violations": coherence_violations,
        # Issue #597 — Randlösungs-Signatur.
        "boundary_hit_fraction": boundary_hit_fraction,
        # Issue #763 — je Grenz-Parameter, ob "low" oder "high" (Richtungsinformation für den
        # #761-Bounds-Vorschlag; leeres Dict, wenn boundary_hit_fraction None/0 ist).
        "boundary_hit_directions": boundary_hit_directions or {},
        # Issue #660 — live (studien-eigene) OR-Arm-Reachability, ergänzend zum #633-Config-Load-
        # Fixture-Check: Klauseln, deren konfigurierte Schwelle über dem beobachteten p99 DIESER
        # Study liegt.
        "any_arm_live_unreachable": any_arm_live_unreachable,
        # Issue #1280/#1281 (GH #1153/#1154, Katalog #1272-1297) — GETRENNT von
        # any_arm_live_unreachable (siehe dortiger Kommentar): eine requires_all-Klausel, keine
        # requires_any-Klausel.
        "mandatory_gate_live_unreachable": mandatory_gate_live_unreachable,
        # Issue #668 — die EXPLIZITE Policy-Entscheidung (statt der blossen #660-Warnung): welche
        # Klauseln gedroppt (any_arm_reduced) bzw. auf welche Schwellen symbol-spezifisch
        # rekalibriert wurden (any_arm_recalibrated_thresholds). Beide leer bei Policy='warn'.
        "any_arm_unreachable_policy": any_arm_policy_decision.get("policy"),
        "any_arm_reduced": any_arm_policy_decision.get("dropped_clauses"),
        "any_arm_recalibrated_thresholds": any_arm_policy_decision.get("recalibrated_thresholds"),
        # Issue #812 — SHA-256 ueber die effektiv wirksame Gate-Konfiguration dieser Study (siehe
        # reward.selection_rule_fingerprint). Studies mit unterschiedlichem Fingerprint duerfen
        # NICHT in derselben DSR-Multiplizitaets-Familie gezaehlt werden (Pitfall #248).
        "selection_rule_fingerprint": selection_fingerprint,
        # Issue #667 — Gate-Kollinearitäts-Diagnose. Tupel-Schlüssel sind nicht JSON-serialisierbar
        # ⇒ "gate_a|gate_b"-Stringform für das strukturierte Event.
        "gate_collinearity_n_samples": gate_collinearity.get("n_samples"),
        "gate_collinearity": {
            f"{k1}|{k2}": rho for (k1, k2), rho in gate_collinearity.get("correlations", {}).items()
        },
        # Issue #679/#811 — strukturierter Redundanz-Alarm (nicht nur geloggt): pro Paar mit
        # praktisch identischer PASS-Menge (Jaccard) UND vernachlässigbarem marginalem Eigenbeitrag
        # welches Gate behalten werden soll (prioritätsbasiert) und welches der Konsolidierungs-
        # Kandidat ist. ``redundant_candidates`` fasst je Kandidat-Gate die stärkste beobachtete
        # Jaccard-Übereinstimmung zusammen — leer, solange kein Paar beide Schwellen überschreitet.
        "gate_collinearity_alarm": gate_collinearity_alarm.get("alarms", []),
        "gate_collinearity_redundant_candidates": gate_collinearity_alarm.get("redundant_candidates", {}),
        "reward_terms_aggregates": term_aggregates,
    })


def _read_deflation_config() -> tuple[bool, float]:
    """Issue #592 — (deflated_selection, deflation_confidence) aus tournament.json (Zero-Hardcoding)."""
    deflated_selection, confidence = False, 0.95
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            deflated_selection = bool(data.get("deflated_selection", False))
            confidence = float(data.get("deflation_confidence", 0.95))
    except Exception:
        pass
    return deflated_selection, confidence


def _read_sortino_guard_family_median_min_siblings() -> int:
    """Issue #913 Fix 2 — Mindestzahl abgeschlossener Sibling-Trials MIT definiertem
    ``oos_n_periods``, bevor ``_resolve_family_median_n_periods`` einen Familien-Median liefert
    (Kaltstart-Untergrenze, unter der eine Median-Schätzung nicht belastbar wäre).
    ``tournament.json['sortino_guard_family_median_min_siblings']``, Default 32."""
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            return int(data.get("sortino_guard_family_median_min_siblings", 32))
    except Exception:
        pass
    return 32


def _read_sortino_guard_family_scope() -> str:
    """Issue #916 — ``tournament.json['sortino_guard_family_scope']`` ∈ {'symbol_strategy'}
    (Default 'symbol_strategy'). Bei einer study-internen Streuung von ``n_periods`` bis Faktor
    11,3 zwischen Strategien DESSELBEN Symbols (#916-Befund) ist der study-lokale (=
    symbol_strategy) Median die sachlich richtige Referenz — ein symbolweiter Median wäre eine
    Aggregation über heterogene Entitäten (Pitfall #291). 'symbol' (Familie über ALLE Strategien
    eines Symbols hinweg) verlangt Koordination über nebenläufig laufende Studies hinweg und ist
    hier bewusst NICHT implementiert (dokumentierter Scope-Cut, analog #843/#845) — ein
    konfigurierter Wert 'symbol' bricht daher fail-loud ab, statt still auf 'symbol_strategy'
    zurückzufallen."""
    scope = "symbol_strategy"
    try:
        cfg_path = config_dir() / "tournament.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            scope = str(data.get("sortino_guard_family_scope", "symbol_strategy"))
    except Exception:
        scope = "symbol_strategy"
    if scope != "symbol_strategy":
        raise ValueError(
            f"tournament.json['sortino_guard_family_scope']={scope!r} nicht unterstützt — "
            "nur 'symbol_strategy' ist implementiert (Issue #916, dokumentierter Scope-Cut).")
    return scope


def _resolve_family_median_n_periods(trial: "optuna.trial.Trial") -> float | None:
    """Issue #913 — der Injektionspunkt für ``backtest_runner._effective_sortino_numeric_guard``s
    ``family_median_n_periods``: Median von ``oos_n_periods`` über die bereits ABGESCHLOSSENEN
    Sibling-Trials DERSELBEN Study (``sortino_guard_family_scope='symbol_strategy'`` — #916).

    ``run_optimization`` (dieser Prozess) kennt die Historie der Study VOR dem Start eines neuen
    Trials — anders als der isolierte Backtest-Subprozess (siehe Docstring von
    ``_effective_sortino_numeric_guard``). Liefert ``None``, solange weniger als
    ``sortino_guard_family_median_min_siblings`` Sibling-Trials ein definiertes ``oos_n_periods``
    tragen (Kaltstart) — der Aufrufer (``build_trial``) schreibt dann keinen Manifest-Schlüssel,
    und der Subprozess behandelt den Trial nach der konfigurierten Bootstrap-Policy (Issue #913
    Fix 2)."""
    min_siblings = _read_sortino_guard_family_median_min_siblings()
    _read_sortino_guard_family_scope()  # fail-loud bei nicht unterstütztem Scope
    try:
        sibling_trials = trial.study.get_trials(
            deepcopy=False,
            states=(optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED),
        )
    except Exception:
        return None
    values = [
        v for t in sibling_trials
        if t.number != trial.number
        for v in [t.user_attrs.get("oos_n_periods")]
        if v
    ]
    if len(values) < min_siblings:
        return None
    return float(statistics.median(values))


def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
                          *, run_backtest=run_backtest, build_trial=build_trial,
                          catalog_newest_ns: int | None = None,
                          catalog_span_days: float | None = None,
                          study_config_dir: Path | None = None,
                          run_id: str | None = None):
    """Wie make_objective, aber single-symbol: build_trial(instruments=[symbol]) und
       compute_reward(universe_size=1, sampled, global_params, strategy) (Per-Symbol-Reward
       mit param_pen Richtung global_params, A4.3).

       Issue #531: ``catalog_span_days`` (reale Bar-Spanne in Tagen) wird an ``build_trial``
       durchgereicht, damit die Manifest-Konstruktion fail-loud gegen die tatsächliche Datenlage
       prüft (REJECT_DATA_INSUFFICIENT_GEOMETRY statt stiller .loc-Klemmung).

       Issue #796 — ``study_config_dir`` (siehe ``make_objective``-Docstring): ``None`` (Default)
       reproduziert das Alt-Verhalten bit-identisch (Config-Kopie je Trial); ``_optimize_symbol_impl``
       friert die Study-Config einmal ein und reicht den Pfad hier durch.

       Issue #1015 (Katalog #858, Fix Punkt 1) — ``run_id`` (Default ``None``, bit-identisch zum
       Pre-Fix-Verhalten): wird bei GESETZTEM Wert als ``trial.user_attrs['run_id']`` gestempelt,
       BEVOR irgendein anderer Codepfad diesen Trial ablehnen/pruning kann — jeder Trial dieser
       Study trägt damit nachvollziehbar, aus welchem ``sweep.run_per_symbol_sweep``-Lauf er
       stammt. ``run_optimization.compute_budget_execution`` liest dieses Feld, um
       ``budget_executed_fraction`` auf die TATSÄCHLICH dieses Laufs zugehörigen Trials zu
       beschränken, statt die Study-SQLite-Historie mehrerer Läufe zu vermischen (#858 Katalog-
       Symptom: budget_executed_fraction=362 % durch Trials VORANGEGANGENER Läufe derselben
       Study)."""
    def objective(trial):
        if run_id is not None:
            trial.set_user_attr("run_id", run_id)
        # Issue #669 — symbol-spezifische Suchraum-Bounds-Überschreibungen (opt-in, leer per
        # Default) NUR im Per-Symbol-Pfad; der globale Multi-Symbol-Pfad (make_objective) bleibt
        # ohne Symbol-Kontext bei den universellen Default-Bounds.
        sampled = sample_params(strategy, trial, symbol=symbol)
        trial.set_user_attr("sampled_params", sampled)

        cfg_dir = config_dir()
        seed = 42
        opt_data: dict = {}
        optimizer_path = cfg_dir / "optimizer.json"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                opt_data = json.load(f) or {}
                seed = opt_data.get("seed", 42)

        trial_dir, manifest_path = build_trial(
            strategy_class=strategy,
            sampled=sampled,
            study_name=trial.study.study_name,
            trial_number=trial.number,
            seed=seed,
            n_folds=4,
            holdout_days=45,
            instruments=[symbol],
            catalog_newest_ns=catalog_newest_ns,
            catalog_span_days=catalog_span_days,
            copy_config=study_config_dir is None,
            study_config_dir=study_config_dir,
            # Issue #913 — Injektionspfad: Familien-Median von oos_n_periods über die bereits
            # abgeschlossenen Sibling-Trials dieser Study, VOR dem Start dieses Trials berechnet.
            family_median_n_periods=_resolve_family_median_n_periods(trial),
        )

        # Issue #415 — Per-Trial-Wall-Clock. perf_counter UM den run_backtest-Aufruf herum (statt via
        # timings-Out-Param), damit ALLE bestehenden run_backtest-Mocks (Signatur
        # ``(trial_dir, manifest_path)``) unveraendert funktionieren (Signatur-Kompat, Pitfall #33).
        _t0 = time.perf_counter()
        try:
            # Issue #797 — Subprocess-Log-Policy aus optimizer.json (Default "on_failure").
            output_path = run_backtest(
                trial_dir, manifest_path, config_dir=study_config_dir,
                subprocess_log_policy=opt_data.get("subprocess_log_policy", "on_failure"),
                subprocess_log_tail_bytes=opt_data.get("subprocess_log_tail_bytes", 32768),
            )
            metrics = parse_tournament(output_path)
        except BacktestRunError as e:
            raise optuna.TrialPruned(f"Subprocess failed: {e}")
        backtest_ms = round((time.perf_counter() - _t0) * 1000)
        # Issue #804 — jede im Subprozess gesammelte Inferenzpfad-Diagnose erneut im
        # Elternprozess-Log emittieren (strategy/symbol/study_name via bind_study_context, #780).
        _reemit_inference_diagnostics(logging.getLogger("optimizer"), metrics, trial.number)

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        t_data: dict = {}
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                t_data = json.load(f) or {}
                risk_dd_cap = t_data.get("max_drawdown", 0.30)

        # Issue #857 (Pitfall #272) — Konsequenz auf der Aggregationsebene der Messung: die
        # Zeitbox-Messung (``oos_max_holding_time_s``) liegt JE TRIAL vor, die Verwerfung darf
        # daher NICHT je Study erfolgen (vorher AUSSCHLIESSLICH in confirm.py: EIN verletzender
        # Trial verwarf 159 saubere Geschwister). Ein zeitbox-verletzender Trial wird hier VOR
        # ``compute_reward`` auf den Unevaluable-Pfad umgestempelt (``metrics.oos_evaluated=False``)
        # — er zählt damit weder in Eligibility noch in Reward noch (via #822) in ``n_family``.
        # Referenz-Deckel über #861 ``resolve_effective_bar_cap`` (gesampelt → strategy_defaults →
        # globaler Deckel), Toleranz über #858 ``timebox_execution_slack_bars`` (Watchdog-Fenster
        # ``exit_close_max_bars`` + 1 Bar unvermeidliche Fill-Latenz, statt der vorherigen 0.01-Bar-
        # Toleranz, die genau diese vom Exit-Mechanismus selbst vorgesehene Verzögerung als
        # Vertragsbruch wertete).
        # Issue #832 Fix Punkt 1 — Haltedauer in Sekunden je Trial persistiert (Rohmaterial fuer
        # report._study_record's je-Study-Aggregat UND fuer die #857-Zeitbox-Neuberechnung weiter
        # unten im Stack, siehe invariants.compute_trial_timebox_violations). UNBEDINGT gestempelt
        # (nicht hinter der ``oos_evaluated``-Ueberschreibung unten) — sonst wuerde ein gerade
        # zeitbox-invalidierter Trial genau die Evidenz verlieren, die confirm.py/report.py
        # brauchen, um dieselbe Verletzung studienweit nachzuvollziehen (Selbstverschleierung).
        # Issue #899 — Exit-Telemetrie (aus Order-Tags, siehe backtest_runner._build_order_exit_meta)
        # als Trial-User-Attrs, unabhängig vom oos_evaluated-Zweig unten (rein additive Telemetrie,
        # analog oos_max_holding_time_s/oos_p95_holding_time_s).
        if metrics.oos_exit_reason_histogram:
            trial.set_user_attr("oos_exit_reason_histogram", metrics.oos_exit_reason_histogram)
        if metrics.oos_max_holding_bars is not None:
            trial.set_user_attr("oos_max_holding_bars", metrics.oos_max_holding_bars)
        # Issue #919 — bislang nur in TournamentMetrics geparst, nie als Trial-User-Attr
        # gestempelt: report._study_record's Study-Aggregat (median_bars_held) hatte dadurch keine
        # Eingangsgrösse.
        if metrics.oos_median_bars_held is not None:
            trial.set_user_attr("oos_median_bars_held", metrics.oos_median_bars_held)
        if metrics.oos_gross_loss_mean_bps is not None:
            trial.set_user_attr("oos_gross_loss_mean_bps", metrics.oos_gross_loss_mean_bps)
        # Issue #1024/#1173 — siehe TournamentMetrics-Docstring.
        if metrics.oos_gross_loss_median_bps is not None:
            trial.set_user_attr("oos_gross_loss_median_bps", metrics.oos_gross_loss_median_bps)
        # Issue #1035 (Katalog #866) — siehe TournamentMetrics-Docstring.
        if metrics.oos_gross_loss_mean_bps_trailing_stop is not None:
            trial.set_user_attr(
                "oos_gross_loss_mean_bps_trailing_stop", metrics.oos_gross_loss_mean_bps_trailing_stop)
        trial.set_user_attr("oos_n_trailing_stop_losses", metrics.oos_n_trailing_stop_losses)
        if metrics.oos_gross_win_mean_bps is not None:
            trial.set_user_attr("oos_gross_win_mean_bps", metrics.oos_gross_win_mean_bps)
        if metrics.oos_atr_median_bps is not None:
            trial.set_user_attr("oos_atr_median_bps", metrics.oos_atr_median_bps)
        if metrics.oos_atr_min_bps is not None:
            trial.set_user_attr("oos_atr_min_bps", metrics.oos_atr_min_bps)
        # Issue #1095 (Katalog #928) — siehe TournamentMetrics-Docstring.
        if metrics.oos_stop_exit_lag_bars_median is not None:
            trial.set_user_attr(
                "oos_stop_exit_lag_bars_median", metrics.oos_stop_exit_lag_bars_median)
        # Issue #953/#1119 (Katalog #960) — siehe TournamentMetrics-Docstring.
        if metrics.oos_bar_range_median_bps is not None:
            trial.set_user_attr("oos_bar_range_median_bps", metrics.oos_bar_range_median_bps)
        # Issue #1079/#1227 (Katalog #1247+, P0) — siehe TournamentMetrics-Docstring.
        if metrics.oos_bar_range_p75_bps is not None:
            trial.set_user_attr("oos_bar_range_p75_bps", metrics.oos_bar_range_p75_bps)
        # Issue #1259 (GH #1129) — siehe TournamentMetrics-Docstring. 0 ist ein GUELTIGER Wert
        # (DEGENERATE_ZERO_RANGE), daher explizit "is not None", nicht Wahrheitswert.
        if metrics.oos_bar_range_population_n is not None:
            trial.set_user_attr(
                "oos_bar_range_population_n", metrics.oos_bar_range_population_n)
        if metrics.oos_zero_range_bar_fraction is not None:
            trial.set_user_attr(
                "oos_zero_range_bar_fraction", metrics.oos_zero_range_bar_fraction)
        # Issue #1054/#1203 (Katalog #1196-1221) — siehe TournamentMetrics-Docstring.
        if metrics.oos_stop_distance_bps_median is not None:
            trial.set_user_attr(
                "oos_stop_distance_bps_median", metrics.oos_stop_distance_bps_median)
        if metrics.oos_trigger_to_fill_gap_bps_median is not None:
            trial.set_user_attr(
                "oos_trigger_to_fill_gap_bps_median", metrics.oos_trigger_to_fill_gap_bps_median)
        if metrics.oos_realized_loss_bps_median is not None:
            trial.set_user_attr(
                "oos_realized_loss_bps_median", metrics.oos_realized_loss_bps_median)
        trial.set_user_attr(
            "oos_n_stop_loss_identity_checked", metrics.oos_n_stop_loss_identity_checked)
        trial.set_user_attr(
            "oos_n_stop_loss_identity_violations", metrics.oos_n_stop_loss_identity_violations)
        # Issue #1259 (GH #1129), Pitfall #442 — bislang berechnet (backtest_runner.
        # _aggregate_exit_telemetry), geparst (parsing.TournamentMetrics), aber nie gestempelt.
        trial.set_user_attr(
            "oos_n_trailing_stop_exits_with_lag_telemetry",
            metrics.oos_n_trailing_stop_exits_with_lag_telemetry)
        if metrics.oos_stop_ratchet_between_trigger_and_submit_bps_median is not None:
            trial.set_user_attr(
                "oos_stop_ratchet_between_trigger_and_submit_bps_median",
                metrics.oos_stop_ratchet_between_trigger_and_submit_bps_median)
        trial.set_user_attr(
            "oos_n_trailing_stop_exits_with_ratchet_telemetry",
            metrics.oos_n_trailing_stop_exits_with_ratchet_telemetry)
        # Issue #1082/#1230 (P1, Katalog #1247+) — siehe TournamentMetrics-Docstring.
        if metrics.oos_stop_distance_share_median is not None:
            trial.set_user_attr(
                "oos_stop_distance_share_median", metrics.oos_stop_distance_share_median)
        if metrics.oos_trigger_to_fill_gap_share_median is not None:
            trial.set_user_attr(
                "oos_trigger_to_fill_gap_share_median",
                metrics.oos_trigger_to_fill_gap_share_median)
        # Issue #1097 (Katalog #930) — siehe TournamentMetrics-Docstring.
        trial.set_user_attr("oos_n_losses", metrics.oos_n_losses)
        # Issue #1085 (Katalog #866-2) — bislang nur in TournamentMetrics geparst, nie als
        # Trial-User-Attr gestempelt: report._study_record hatte dadurch keine Eingangsgrösse für
        # eine study-weite Dust-Round-Trip-Quote (Rundungsartefakte mit Notional ~1e-13, siehe
        # invariants.check_dust_round_trip_share-Docstring).
        if metrics.oos_expectancy_notional_degenerate_count:
            trial.set_user_attr(
                "oos_expectancy_notional_degenerate_count",
                metrics.oos_expectancy_notional_degenerate_count)
        # Issue #946/#1112 (Katalog #960) — Dust-Round-Trips, jetzt AN DER QUELLE verworfen
        # (``backtest_runner._filter_dust_round_trips``), statt nur an der Expectancy-Konsumstelle
        # (Feld oben, seit diesem Fix strukturell 0). Ersetzt das Feld oben als Rohmaterial fuer
        # report._study_record/invariants.check_dust_round_trip_share.
        if metrics.oos_dust_round_trips_filtered_count:
            trial.set_user_attr(
                "oos_dust_round_trips_filtered_count",
                metrics.oos_dust_round_trips_filtered_count)
        # Issue #994/#1146 (Katalog #1170) — der #1126/#1130-Feldblock war in ``TournamentMetrics``
        # geparst, aber NIE gestempelt: ``report._median_of_trial_field`` liest ausschliesslich
        # ``trial.user_attrs``, nicht ``TournamentMetrics`` direkt, also blieben 14 Report-Felder in
        # 28/28 Studies ``None``, obwohl der Backtest-Runner sie berechnete. Derselbe
        # ``if … is not None``-Stempel-Stil wie die benachbarten Felder oben (#1035/#1097).
        if metrics.oos_gross_loss_median_bps_trailing_stop is not None:
            trial.set_user_attr(
                "oos_gross_loss_median_bps_trailing_stop",
                metrics.oos_gross_loss_median_bps_trailing_stop)
        if metrics.oos_gross_loss_winsorized_mean_bps_trailing_stop is not None:
            trial.set_user_attr(
                "oos_gross_loss_winsorized_mean_bps_trailing_stop",
                metrics.oos_gross_loss_winsorized_mean_bps_trailing_stop)
        if metrics.oos_n_trailing_stop_losses_dust_filtered is not None:
            trial.set_user_attr(
                "oos_n_trailing_stop_losses_dust_filtered",
                metrics.oos_n_trailing_stop_losses_dust_filtered)
        if metrics.oos_rt_notional_p05 is not None:
            trial.set_user_attr("oos_rt_notional_p05", metrics.oos_rt_notional_p05)
        if metrics.oos_rt_notional_p50 is not None:
            trial.set_user_attr("oos_rt_notional_p50", metrics.oos_rt_notional_p50)
        if metrics.oos_rt_notional_p95 is not None:
            trial.set_user_attr("oos_rt_notional_p95", metrics.oos_rt_notional_p95)
        if metrics.oos_atr_raw_median_bps is not None:
            trial.set_user_attr("oos_atr_raw_median_bps", metrics.oos_atr_raw_median_bps)
        if metrics.oos_stop_exit_fill_lag_bars_median is not None:
            trial.set_user_attr(
                "oos_stop_exit_fill_lag_bars_median", metrics.oos_stop_exit_fill_lag_bars_median)
        if metrics.oos_stop_exit_slippage_bps_median is not None:
            trial.set_user_attr(
                "oos_stop_exit_slippage_bps_median", metrics.oos_stop_exit_slippage_bps_median)
        # Issue #1023/#1172 (Katalog #866-2) — exakt dieselbe Bruchstelle wie #994/#1146, eine
        # Ebene tiefer: die beiden Nachbarzeilen oben stempeln ihre Groesse, aber der dazugehoerige
        # STICHPROBENZAEHLER (wie viele TRAILING_STOP-Exits ueberhaupt Fill-Lag-Telemetrie tragen)
        # blieb ungestempelt. Ohne ihn ist "0,0 Bars Latenz" von "nie gemessen" nicht
        # unterscheidbar (die vormalige _INTENTIONALLY_UNSTAMPED_METRIC_FIELDS-Begruendung
        # "holdout-only" war falsch — report._study_record summiert dieses Feld nachweislich aus
        # trial_attrs, nicht aus dem Holdout-Re-Evaluation-Pfad).
        if metrics.oos_n_trailing_stop_exits_with_fill_lag_telemetry is not None:
            trial.set_user_attr(
                "oos_n_trailing_stop_exits_with_fill_lag_telemetry",
                metrics.oos_n_trailing_stop_exits_with_fill_lag_telemetry)

        _timebox_violated_this_trial = False
        if metrics.oos_evaluated and metrics.oos_max_holding_time_s is not None:
            if metrics.oos_max_holding_time_s is not None:
                trial.set_user_attr("oos_max_holding_time_s", metrics.oos_max_holding_time_s)
            if metrics.oos_p95_holding_time_s is not None:
                trial.set_user_attr("oos_p95_holding_time_s", metrics.oos_p95_holding_time_s)
            # Issue #903 — rohe Round-Trip-Haltedauern (siehe compute_trial_timebox_violations
            # Round-Trip-Ebene) statt nur des Trial-Maximums oben.
            if metrics.oos_holding_times_s:
                trial.set_user_attr("oos_holding_times_s", list(metrics.oos_holding_times_s))
            _cap_bars, _cap_source = _inv.resolve_effective_bar_cap(sampled, strategy=strategy)
            _slack_bars = float(t_data.get("timebox_execution_slack_bars", 3.0))
            # Issue #902 — Single Source of Truth statt eines eigenen 3600.0-Literals (Pitfall #271,
            # dritte Instanz): dieselbe Konstante wie invariants.compute_trial_timebox_violations.
            # Ein echter per-Symbol bar_seconds-Wert (#900 median_delta_t_s) ist an dieser Stelle
            # nicht verdrahtet (Sweep-Preflight-Ergebnis erreicht den Trial-Objective-Prozess derzeit
            # nicht) — dokumentierter Fallback, jetzt aus der EINEN Quelle statt einer Kopie.
            _bar_seconds = _contracts.BAR_SECONDS_DEFAULT
            _cap_s = (_cap_bars + _slack_bars) * _bar_seconds
            _timebox_violated_this_trial = metrics.oos_max_holding_time_s > _cap_s
            trial.set_user_attr("oos_timebox_violated", _timebox_violated_this_trial)
            trial.set_user_attr(
                "oos_holding_bars_max", round(metrics.oos_max_holding_time_s / _bar_seconds, 4))
            trial.set_user_attr("timebox_cap_source", _cap_source)
            if _timebox_violated_this_trial:
                trial.set_user_attr("oos_invalid_reason", "TIMEBOX_VIOLATION")
                metrics.oos_evaluated = False
                metrics.oos_eligible = False

        reward, reward_terms = compute_reward(metrics, universe_size=1, risk_dd_cap=risk_dd_cap,
                                sampled=sampled, global_params=global_params, strategy=strategy, return_terms=True)
        trial.set_user_attr("reward_terms", reward_terms)

        # Issue #404 (P0) — Per-Symbol-Telemetrie. Der Sweep emittierte bislang KEIN strukturiertes
        # Per-Trial-Event (nur Optunas native INFO-Zeile, vgl. #402), wodurch der Unevaluable-Floor-
        # Kollaps (Pitfall #75) forensisch unsichtbar blieb. `oos_eligible` trennt den IS-Drop
        # (Symbol nie IS-eligible ⇒ kein OOS evaluiert) vom OOS-Drop (OOS evaluiert, aber durchs
        # Gate gefallen); `is_total_trades`/`hit_trade_cap` machen die Shaping-Saettigung sichtbar.
        outcome = "evaluable" if metrics.oos_evaluated else "unevaluable"
        # Issue #408 — modale Gate-Drop-Reason: pro Trial die kategorisierte Rejection-Reason
        # persistieren, damit confirm._dominant_rejection sie ueber die Study aggregieren kann.
        # Issue #971 — ``timebox_violated`` durchgereicht, damit ein nachträglich (#857) auf
        # ``oos_evaluated=False`` umgestempelter Trial NICHT als IS-Gate-Drop fehlklassifiziert wird.
        rejection_reason = _classify_trial_rejection(metrics, timebox_violated=_timebox_violated_this_trial)
        trial.set_user_attr("rejection_reason", rejection_reason)
        # Issue #453 — granulare, dezidierte Rejection-Kategorie (löst 'oos_not_evaluated' in die
        # tatsächliche Ursache auf) zusätzlich persistieren — für die modale Proposal-Aggregation.
        is_rejection_detail = _classify_is_rejection_detail(
            metrics, timebox_violated=_timebox_violated_this_trial)
        trial.set_user_attr("is_rejection_detail", is_rejection_detail)
        # Issue #1032/#1181 (Katalog #866-2) — Root-Cause: invariants.gate_inventory_table's
        # #1003/#1155-Fix ("n_solo_rejections wird PRIMAER aus oos_rejection_reasons gebildet")
        # liest exakt dieses Feld aus trial_attrs — es wurde aber nie gestempelt
        # (_INTENTIONALLY_UNSTAMPED_METRIC_FIELDS führte es fälschlich als "synchron verbraucht,
        # kein trial_attrs-Konsument"). has_reasons_field war dadurch am Report-Zeitpunkt IMMER
        # False, sodass n_solo_rejections lautlos auf die alte, delta-basierte Näherung zurückfiel
        # — genau die #1155-Fehlerklasse, obwohl der Fix-Code bereits existierte.
        if metrics.oos_rejection_reasons:
            trial.set_user_attr("oos_rejection_reasons", list(metrics.oos_rejection_reasons))
        trial.set_user_attr("oos_timebox_invalidated", bool(_timebox_violated_this_trial))
        # Issue #917 Fix 2 — welche Gates konkret auf einer undefinierten Grösse liefen (leer im
        # Regelfall). Additiv, unabhängig von is_rejection_detail selbst gestempelt, damit auch ein
        # Trial mit einem ANDEREN dominanten Ablehnungsgrund die Information nicht verliert.
        _undefined_terms = _extract_undefined_gate_terms(
            getattr(metrics, "oos_rejection_reasons", ()))
        if _undefined_terms:
            trial.set_user_attr("oos_gate_undefined_terms", _undefined_terms)
        # Issue #415 — backtest_ms als User-Attr fuer die Per-Study-Aggregation (optimize_symbol).
        trial.set_user_attr("backtest_ms", backtest_ms)
        # Issue #413 — oos_evaluated als User-Attr: Grundlage des evaluable-basierten Floor-Guards
        # (floor_plateau_callback) UND der `evaluable_trials`-Zaehlung im Per-Study-Summary (#415).
        trial.set_user_attr("oos_evaluated", bool(metrics.oos_evaluated))
        # Issue #615 — oos_eligible EXPLIZIT stempeln (Single Source of Truth für die Top-k-Holdout-
        # Selektion in confirm.confirm_per_symbol_promotion, #576). Vorher filterte confirm auf
        # ``is_rejection_detail is None`` — der gestempelte Wert war aber der STRING "NONE", nie
        # Python-``None`` ⇒ eligible_trials IMMER leer ⇒ Top-k lief nie. Der gestempelte Bool ist die
        # kohärente, direkt filterbare Grösse (identisch zu ``is_rejection_detail == IS_REJECTION_NONE``).
        trial.set_user_attr("oos_eligible", bool(metrics.oos_eligible))
        # Issue #612 — Feasibility-Constraint(s) für den Sampler (≤ 0 = feasible). Damit optimiert der
        # TPE EINE stetige Grösse (die risikoadjustierte OOS-Performance) statt einer Stufenfunktion;
        # die Feasibility-Rangordnung übernimmt Optuna nativ (feasible ≻ infeasible in best_trial).
        # Issue #635 — dimensionslos normiert (t_data = dieselbe tournament.json-Config, oben geladen).
        trial.set_user_attr("oos_constraint_violations", _compute_oos_constraints(metrics, t_data))
        # Issue #618 — der PER-PERIODEN-Sortino + PSR je Trial (für die DSR-Kohorten-Varianz V[ŜR_trials]
        # in confirm; Multiple-Testing-Korrektur auf der per-Perioden-Skala, NICHT der Reward-Skala #611).
        # Issue #788 — NUR gesetzt, wenn der Trial tatsächlich evaluiert wurde (dieselbe Sentinel-
        # Frage wie #759/oos_win_rate: ein nicht evaluierter Trial darf für KEINE OOS-Metrik eine
        # Beobachtung tragen, weder None noch ein impliziter 0.0-Fallback — der Key entfällt ganz).
        if metrics.oos_evaluated:
            if metrics.oos_sortino_period is not None:
                trial.set_user_attr("oos_sortino_period", metrics.oos_sortino_period)
            if metrics.oos_psr is not None:
                trial.set_user_attr("oos_psr", metrics.oos_psr)
        # Issue #822 — ``oos_selection_statistic_available``: True GENAU DANN, wenn der Trial eine
        # verwertbare Selektions-Teststatistik trägt (``oos_psr`` — die Grösse, auf der die
        # Selektion seit #697 rankt). Ein Trial mit ``SORTINO_GUARD_TRIPPED`` (#823) oder
        # ``EQUITY_NONPOSITIVE`` (#825) ist ``oos_evaluated=True`` (er hat gehandelt), aber trägt
        # KEINEN Sortino/PSR — er hat das Maximum unter H₀ nicht beeinflusst und darf die
        # familienweite Multiplizität (``sweep._family_n_from_studies``) nicht mitzählen (dieselbe
        # Argumentationslogik wie #814 für nie gezogene Trials, hier eine Ebene tiefer: ein Trial,
        # dessen Statistik VERWORFEN wurde, hat ebenso keinen Schätzer). ``oos_evaluated`` bleibt
        # UNVERÄNDERT als Aktivitäts-Telemetrie erhalten (#770-Budgetbilanz, #769-Diagnose).
        trial.set_user_attr("oos_selection_statistic_available", bool(
            metrics.oos_evaluated and metrics.oos_psr is not None))
        # Issue #653 — T (Anzahl OOS-Perioden) je Trial, damit confirm.py den theoretischen
        # Lo-2002-Varianz-Floor (T-bewusst) für die Kohorte bilden kann, statt einer T-blinden
        # Konstante (siehe deflation.lo2002_sharpe_variance/sr0_multiple_testing_robust).
        trial.set_user_attr("oos_n_periods", metrics.oos_n_periods)
        # Issue #1011/#1163 (Katalog #1170) — Bar-Achsen-Dichte je Trial persistiert (None-safe,
        # siehe parsing.TournamentMetrics.oos_bars_per_calendar_day-Feldkommentar), damit
        # report.py/invariants.check_session_calendar_coherence den Study-Median bilden kann.
        if metrics.oos_bars_per_calendar_day is not None:
            trial.set_user_attr("oos_bars_per_calendar_day", metrics.oos_bars_per_calendar_day)
        if metrics.oos_session_coverage_fraction is not None:
            trial.set_user_attr(
                "oos_session_coverage_fraction", metrics.oos_session_coverage_fraction)
        # Issue #1298 (GH #1175, P0) Fix Punkt 3 — Länge der VOLLEN mtm_series-Bar-Achse je Trial
        # persistiert, Rohmaterial für report._study_record's n_bars_delivered_median.
        if metrics.oos_n_bars_delivered is not None:
            trial.set_user_attr("oos_n_bars_delivered", metrics.oos_n_bars_delivered)
        # Issue #845 — Downside-Beobachtungs-Nenner je Trial persistiert (None-safe, siehe
        # parsing.TournamentMetrics.oos_downside_obs-Feldkommentar), damit confirm.py/invariants.py
        # n_periods-Heterogenität einer Familie gegen die tatsaechlich downside-tragende
        # Teilmenge prüfen können, nicht nur gegen die volle informative Periodenzahl.
        if metrics.oos_downside_obs is not None:
            trial.set_user_attr("oos_downside_obs", metrics.oos_downside_obs)
        # Issue #620 — Kohärenz-Verletzung je Trial persistieren (Study-Zähler coherence_violations).
        trial.set_user_attr("oos_coherence_violation", bool(metrics.oos_coherence_violation))
        # Issue #804 — die strukturierten Inferenzpfad-Diagnosen je Trial persistieren, damit der
        # #742-Report sie je Study zu inference_diagnostics_by_code aggregieren kann (report.py).
        trial.set_user_attr("inference_diagnostics", list(metrics.inference_diagnostics))
        # Issue #656 — Trade-Count-Telemetrie je Trial (bereits im Log-Event vorhanden, hier
        # zusätzlich als User-Attr persistiert), damit der Zero-Eligible-Plateau-Guard
        # (floor_plateau_callback) eine Trade-Count-Diagnose bilden kann, ohne die volle Metrics
        # erneut laden zu müssen (Suchraum-Diagnose für strukturell 0-eligible Strategien).
        trial.set_user_attr("oos_total_trades", int(metrics.oos_total_trades))
        trial.set_user_attr("is_total_trades", int(metrics.is_total_trades))
        trial.set_user_attr("hit_trade_cap", bool(metrics.hit_trade_cap))
        # Issue #1299 (GH #1176) Fix Punkt 3 — der backtest_runner._empty_result-Fehlergrund dieses
        # Trials (None bei echter Ausfuehrung), Rohmaterial fuer sweep_diagnostics.diagnose_trade_
        # frequency/diagnose_structural_zero_eligible_gate's data_unavailable-Unterscheidung (#1303).
        if metrics.worker_error is not None:
            trial.set_user_attr("worker_error", metrics.worker_error)
        # Issue #1298 (GH #1175, P0) Fix Punkt 3 — Tick-Populations-Zähler je Trial persistiert,
        # Rohmaterial für report._study_record (n_ticks_raw_median/n_ticks_after_session_filter_
        # median) und invariants.check_tick_population.
        if metrics.n_ticks_raw is not None:
            trial.set_user_attr("n_ticks_raw", metrics.n_ticks_raw)
        if metrics.n_ticks_after_session_filter is not None:
            trial.set_user_attr(
                "n_ticks_after_session_filter", metrics.n_ticks_after_session_filter)
        # Issue #660 — die per-Trial OOS-Win-Rate persistiert, damit die Study-Summary
        # (_emit_study_summary) die tatsächlich BEOBACHTETE Symbol-/Strategie-spezifische
        # Win-Rate-Verteilung gegen die konfigurierte oos_min_win_rate-Schwelle prüfen kann (LIVE,
        # nicht nur das statische Cross-Strategy-Kalibrier-Fixture aus #633).
        # Issue #788 — dieselbe oos_evaluated-Torwaechter-Bedingung gilt ab hier fuer JEDE OOS-
        # Metrik (profit_factor, expectancy, total_return, sortino) — ein 0.0-Fallback fuer einen
        # NIE evaluierten Trial waere ununterscheidbar von einer echten Null-Beobachtung (dieselbe
        # #759-Fehlerklasse, hier an vier weiteren Metriken).
        # Issue #966 (Katalog A, P0, Pitfall #305 in AGENTS.md) — ``oos_expectancy`` fiel bislang in
        # der Parsing-Schicht (parsing.TournamentMetrics) auf 0.0 statt None zurueck, wenn die
        # zugrundeliegende oos_metrics.json keinen Wert trug — ein Sentinel, der die Signatur eines
        # Messwerts trug und von JEDEM nachgelagerten Konsumenten (Gate, Constraint-Distanz,
        # TPE-Sampler-Grundlage) als echte Messung behandelt wurde. ``parsing.py`` liefert None jetzt
        # korrekt durch (analog #759 fuer oos_win_rate/oos_profit_factor); dieselbe
        # "nur gesetzt, wenn vorhanden"-Konvention gilt jetzt auch hier.
        if metrics.oos_evaluated:
            if metrics.oos_win_rate is not None:
                trial.set_user_attr("oos_win_rate", metrics.oos_win_rate)
            # Issue #1093/#1241 (P1) — dieselbe "nur gesetzt, wenn vorhanden"-Konvention wie
            # oos_win_rate: LIVE-Reachability-Grundlage fuer das neue oos_min_alpha_tstat-Gate
            # (siehe check_mandatory_gate_reachability_live unten).
            if metrics.oos_alpha_tstat is not None:
                trial.set_user_attr("oos_alpha_tstat", metrics.oos_alpha_tstat)
            # Issue #1255 (GH #1125), Pitfall #454-Klasse — DIESELBE "nur gesetzt, wenn vorhanden"-
            # Konvention. Das oos_min_alpha_tstat-Gate konsumiert seit diesem Fix
            # oos_alpha_tstat_hc3 (backtest_runner._evaluate_oos_eligibility); die LIVE-
            # Reachability-Kohorte (unten) MUSS auf DERSELBEN Statistik sitzen wie das tatsaechliche
            # Gate — sonst diagnostiziert sie die Erreichbarkeit einer anderen Groesse als der, die
            # tatsaechlich entscheidet (exakt die Fehlerklasse aus Pitfall #454/#1257).
            if metrics.oos_alpha_tstat_hc3 is not None:
                trial.set_user_attr("oos_alpha_tstat_hc3", metrics.oos_alpha_tstat_hc3)
            if metrics.oos_profit_factor is not None:
                trial.set_user_attr("oos_profit_factor", metrics.oos_profit_factor)
            if metrics.oos_expectancy is not None:
                trial.set_user_attr("oos_expectancy", metrics.oos_expectancy)
            trial.set_user_attr("oos_total_return", metrics.oos_total_return)
            if metrics.oos_sortino is not None:
                trial.set_user_attr("oos_sortino", metrics.oos_sortino)
            # Issue #1100 (Katalog #933) — dieselbe oos_evaluated-Torwaechter-Konvention wie die
            # fuenf Metriken oben (#788/#966): ein nie evaluierter Trial darf keine Buy&Hold-
            # Benchmark-Beobachtung tragen. ``oos_buyhold_return`` ist bereits None-safe bis hierher
            # durchgereicht (parsing.TournamentMetrics, siehe dortiger Feldkommentar) — dieser Guard
            # verhindert, dass ein zukuenftiger Aufrufer den Key unconditional stempelt und damit
            # #759/#788/#966s Sentinel-Kollaps-Fehlerklasse fuer dieses Feld reproduziert.
            if metrics.oos_buyhold_return is not None:
                trial.set_user_attr("oos_buyhold_return", metrics.oos_buyhold_return)
        # Issue #668 — die maschinenlesbaren Gate-Deltas je Trial (bereits im JSON_EVENT emittiert,
        # #554) zusätzlich als User-Attr persistiert: erlaubt confirm.py, die eligible_requires_any-
        # Klausel für BEREITS ABGESCHLOSSENE Trials retroaktiv unter einer angepassten Policy
        # (drop_arm/recalibrate) neu zu bewerten, OHNE einen Re-Backtest zu benötigen.
        trial.set_user_attr("oos_gate_deltas", dict(metrics.oos_gate_deltas or {}))
        # Issue #619 — per-Fold-OOS-Sortinos je Trial (DEPRECATED für PBO/CSCV seit #663/#665 —
        # annualisiert, fold-spezifisch inkommensurabel; bleibt forensische Telemetrie).
        trial.set_user_attr("oos_fold_sortinos", list(metrics.oos_fold_sortinos))
        # Issue #665 — die kanonische, annualisierungs-invariante Parallelgrösse.
        trial.set_user_attr("oos_fold_sortino_periods", list(metrics.oos_fold_sortino_periods))
        # Issue #663/#665/#813 — die gepoolte OOS-Per-Perioden-Return-Serie für JEDEN oos_evaluated
        # Trial gestempelt (bis #813 NUR für eligible Trials — Root-Cause #813: die familienweite
        # DSR-Multiplizitätszahl zählt seit #784 ALLE oos_evaluated-Trials, die Declusterung
        # [cpcv.cluster_effective_configs] fand ihre Renditeserien aber nur in der viel kleineren
        # eligiblen Teilmenge — deflation_n_effective stieg um Faktor ~7,7, die tatsächlich
        # declusterte Config-Zahl blieb konstant ⇒ systematische Über-Deflation). Storage-Kosten
        # sind seit #798 [period_returns_cap, gekürzte Serie] und #794 [kontinuierliche Retention]
        # tragbar; die Serie liegt in user_attrs, nicht auf der Platte.
        if metrics.oos_evaluated:
            trial.set_user_attr("oos_period_returns", list(metrics.oos_period_returns))
        # Issue #832/#857 — oos_max_holding_time_s/oos_p95_holding_time_s werden bereits weiter
        # oben (VOR der moeglichen #857-oos_evaluated-Ueberschreibung) unbedingt gestempelt.
        emit_execution_event(logging.getLogger("optimizer"), "optimizer_trial_completed", {
            "symbol": symbol,
            "trial_number": trial.number,
            "backtest_ms": backtest_ms,
            "reward": reward,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
            "oos_total_trades": metrics.oos_total_trades,
            "oos_total_return": metrics.oos_total_return,
            "is_total_trades": metrics.is_total_trades,
            "hit_trade_cap": metrics.hit_trade_cap,
            "outcome": outcome,
            # Issue #416 — Schluessel-Kennzahlen fuer die Per-Trial-Fehleranalyse zusaetzlich ins
            # strukturierte Event heben: Daten-Zeitfenster (None, falls die JSON keinen Block traegt)
            # und die kategorisierte Gate-Drop-Reason (#408).
            "rejection_reason": rejection_reason,
            "data_window_start": metrics.data_window_start,
            "data_window_end": metrics.data_window_end,
            "data_window_days": metrics.data_window_days,
            # Issue #455 (Pitfall #82) — OOS-Abdeckungs-Telemetrie an die Operator-Konsole heben.
            # oos_covered=False ⇒ der Floor-Grund ist auf einen Blick DATENseitig (H2-Katalog zu
            # dünn/stale), nicht parameterseitig — die einzige diagnostisch relevante Zahl, die
            # bislang vor der Konsole verworfen wurde.
            "fill_ts_max": metrics.fill_ts_max,
            "oos_window_start_ns": metrics.oos_window_start_ns,
            "oos_covered": metrics.oos_covered,
            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
            # Issue #453 — granulare Rejection-Kategorie + die konkreten OOS-Gate-Verletzungen
            # ins strukturierte Event heben (Observability; ändert keine Entscheidung).
            "is_rejection_detail": is_rejection_detail,
            "oos_rejection_reasons": list(metrics.oos_rejection_reasons),
            # Issue #554 — maschinenlesbare Gate-Deltas (metric → actual − threshold) für die
            # forensische Near-Miss-Analyse ohne String-Parsing der Reject-Gründe.
            "oos_gate_deltas": metrics.oos_gate_deltas or {},
            # Issue #569 — Roh-Kennzahlen additiv ins Event heben (rein observational, kein
            # Entscheidungseinfluss), damit #559/#560/#563/#565 direkt aus dem Log verifizierbar sind
            # (statt aus oos_gate_deltas + reward rückgerechnet). null bei mathematisch undefiniertem
            # Sortino/PF (Zero-Loss/Sub-Threshold). per_fold_oos_sortino für die #565-Dispersion.
            "oos_sortino": metrics.oos_sortino,
            "oos_expectancy": metrics.oos_expectancy,
            "oos_win_rate": metrics.oos_win_rate,
            "oos_profit_factor": metrics.oos_profit_factor,
            "is_sortino_median": metrics.is_sortino_median,
            "per_fold_oos_sortino": list(metrics.oos_fold_sortinos),
            # Issue #665 — annualisierungs-invariante Fassung (kanonisch für fold-übergreifende
            # Vergleiche); "per_fold_oos_sortino" (oben) bleibt nur forensische Anzeige.
            "per_fold_oos_sortino_period": list(metrics.oos_fold_sortino_periods),
            # Issue #620 — #589-Kohärenz-Verletzung (sign(oos_sortino)≠sign(oos_total_return)) sichtbar.
            "oos_coherence_violation": bool(metrics.oos_coherence_violation),
            "reward_terms": reward_terms,
        })

        reward_mode = "auto"
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                reward_mode = (json.load(f) or {}).get("reward_mode", "auto")
        if reward_mode == "pareto":
            min_trades = 20
            if tournament_path.exists():
                with open(tournament_path, "r", encoding="utf-8") as f:
                    min_trades = (json.load(f) or {}).get("min_trades", 20)
            trades_constraint = min_trades - metrics.oos_total_trades
            dd_constraint = metrics.oos_max_drawdown - risk_dd_cap
            trial.set_user_attr("constraints", (float(trades_constraint), float(dd_constraint)))

        # Issue #864 (Pitfall #276) — "nicht messbar" ist nicht "schlecht". Ein Trial mit
        # SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE/EQUITY_NONPOSITIVE erhält bislang
        # denselben Reward-Floor wie ein Trial, der NIE gehandelt hat — der TPE-Surrogat lernt
        # daraus "Region X ist maximal schlecht", obwohl die korrekte Aussage "Region X ist mit den
        # aktuellen Schätzern nicht messbar" lautet (FlashCrashReversal/SqueezeBreakout: 1172 von
        # ~33000 Trials eines Referenzlaufs betroffen, davon 627 auf eine einzige Strategie
        # konzentriert). ``inference_failure_policy='prune'`` (Default) nutzt stattdessen Optunas
        # nativen dritten Ausgang (``TrialPruned`` ⇒ ``TrialState.PRUNED``) — TPE ignoriert geprunte
        # Trials bei der Posterior-Bildung korrekt, statt sie als negative Beobachtung zu werten.
        # ``'floor'`` bleibt für Reproduktionsläufe bit-identisch zum Pre-#864-Verhalten.
        _inference_failure_policy = opt_data.get("inference_failure_policy", "prune")
        if _inference_failure_policy not in ("floor", "prune"):
            raise ValueError(
                f"optimizer.json['inference_failure_policy']={_inference_failure_policy!r} "
                "unbekannt — erwartet 'floor' oder 'prune'.")
        # Issue #918 (Verallgemeinerung von #914) — die Menge wird nicht mehr als Literal gepflegt,
        # sondern aus der zentralen Registry (_contracts.INFERENCE_DIAGNOSTIC_CODES) abgeleitet:
        # jeder Code mit failure_policy in {'prune', 'floor'} ist ein Kandidat für die GLOBALE
        # inference_failure_policy-Behandlung unten; 'telemetry_only'-Codes werden nie gepruned.
        # Issue #914 — SORTINO_GUARD_REFERENCE_UNAVAILABLE (#901 eingeführt) fehlte hier bislang;
        # ohne Registrierung konnte inference_failure_policy='prune' diesen Code nie erreichen und
        # ~1600 nicht-messbare Trials liefen als reguläre REJECT_OOS_OTHER-Failures durch den
        # Reward-Pfad, was den TPE-Sampler auf eine uniform degenerierte Region trainierte.
        _inference_failure_codes = {
            code for code, entry in _contracts.INFERENCE_DIAGNOSTIC_CODES.items()
            if entry.failure_policy in ("prune", "floor")
        }
        _triggered_codes = sorted({
            d.get("code") for d in (metrics.inference_diagnostics or ())
        } & _inference_failure_codes)
        if _inference_failure_policy == "prune" and _triggered_codes:
            trial.set_user_attr("trial_pruned_inference_codes", _triggered_codes)
            raise optuna.TrialPruned(
                f"[#864] inference_failure_policy='prune': {_triggered_codes}")
        return reward
    return objective


def optimize_symbol(strategy: str, symbol: str, n_trials: int | None = None,
                    *, storage: str | None = None, catalog_newest_ns: int | None = None,
                    catalog_span_days: float | None = None, run_id: str | None = None):
    """Single-Symbol-Variante von `optimize`: eigene benannte SQLite-Study unter
       {WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db, Manifest mit instruments=[symbol]
       (universe_size==1 ⇒ Per-Symbol-Reward), Warm-Start am globalen Optimum (Gate 2 via
       study.enqueue_trial). n_jobs=1 wird erzwungen (SQLite-Reproduzierbarkeit, Pitfall #68).
       Das globale `optimize`/`make_objective` bleibt unverändert.

       Issue #800 — dünner Wrapper: der ``with bind_study_context(...)``-Block deckt den
       GESAMTEN Körper von ``_optimize_symbol_impl`` ab (Storage-Aufloesung, Sampler-Konstruktion,
       ``create_study``, Warm-Start-Seeding UND ``study.optimize``). Vorher stand das manuelle
       ``__enter__()`` VOR und das ``__exit__()`` NACH ~150 Zeilen Setup, sodass jede Exception in
       diesem Fenster (z. B. ``ENOSPC`` waehrend ``create_study``) das Exit uebersprang: der
       ContextVar blieb im Worker-Thread gesetzt und der naechste, andere Study im selben Thread
       erbte das ``[strategy/symbol]``-Log-Praefix der abgestuerzten Study.

       Issue #1015 (Katalog #858, Fix Punkt 1) — ``run_id`` (Default ``None``, bit-identisch zum
       Pre-Fix-Verhalten) wird an jeden in dieser Study neu erzeugten Trial durchgereicht (siehe
       ``make_symbol_objective``-Docstring); ``sweep.run_per_symbol_sweep`` übergibt seinen
       eigenen ``run_id`` (denselben, der bereits den #799-Checkpoint treibt)."""
    study_name = f"study_{strategy}_{_sanitize(symbol)}"
    with bind_study_context(strategy=strategy, symbol=symbol, study_name=study_name):
        return _optimize_symbol_impl(
            strategy, symbol, n_trials,
            storage=storage, catalog_newest_ns=catalog_newest_ns,
            catalog_span_days=catalog_span_days, study_name=study_name, run_id=run_id,
        )


def _optimize_symbol_impl(strategy: str, symbol: str, n_trials: int | None = None,
                    *, storage: str | None = None, catalog_newest_ns: int | None = None,
                    catalog_span_days: float | None = None, study_name: str,
                    run_id: str | None = None):
    """Issue #800 — unveraenderter Koerper von ``optimize_symbol`` (eine Einrueckungsebene, keine
       Logikaenderung ausser der jetzt von aussen uebergebenen ``study_name`` und dem entfernten
       manuellen Context-Enter/Exit, siehe ``optimize_symbol``-Docstring).

       Issue #1025 (Katalog #866) — Root-Cause: ``make_symbol_objective`` stempelt
       ``trial.user_attrs['run_id']`` nur, wenn ``run_id is not None``; erreichte der Parameter
       diese Funktion NICHT (Direktaufruf ausserhalb von ``sweep.run_per_symbol_sweep``, das einzige
       Alt-Verhalten, das der ``None``-Default hier zulassen wollte), blieb JEDER Trial dieser Study
       ungestempelt — ``compute_budget_execution``s ``run_id``-Filter fand dann fuer die GESAMTE
       Study keinen Treffer (``n_trials_completed == 0``), obwohl die Study ihr Budget exakt
       ausgefuehrt hatte. Ein hier selbst erzeugter Fallback (``default_run_id()``, dieselbe Quelle
       wie ``sweep.main()``) schliesst die Luecke am Study-Start selbst, statt sie stillschweigend
       bis zum Report durchzureichen — bit-identisch fuer JEDEN Aufrufer, der bereits einen
       ``run_id`` uebergibt (der Normalfall seit #1015)."""
    if run_id is None:
        run_id = default_run_id()
    study_t0 = time.perf_counter()  # Issue #415 — Per-Study-Wall-Clock
    cfg_dir = config_dir()
    conf_n_trials, n_startup_trials, seed = 100, 16, 42
    opt_data: dict = {}
    optimizer_path = cfg_dir / "optimizer.json"
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f) or {}
            conf_n_trials = opt_data.get("n_trials", conf_n_trials)
            n_startup_trials = opt_data.get("n_startup_trials", n_startup_trials)
            seed = opt_data.get("seed", seed)

    # Issue #631 — Fail-loud beim Config-Load (siehe optimize()): additive Strafterme dürfen die
    # Base-Streuung auf dem Kalibrier-Fixture nicht strukturell überstimmen.
    assert_penalty_scale_calibrated(opt_data)
    # Issue #633 — warnt beim Config-Load, wenn eine eligible_requires_any-Schwelle strukturell über
    # dem p99 der dokumentierten Kalibrier-Fixture-Verteilung liegt (unerreichbarer OR-Arm).
    tournament_path_check = cfg_dir / "tournament.json"
    if tournament_path_check.exists():
        with open(tournament_path_check, "r", encoding="utf-8") as f:
            _any_arm_unreachable = check_any_arm_reachability(json.load(f) or {})
        _emit_any_arm_reachability_result(
            logging.getLogger("optimizer"), _any_arm_unreachable,
            check_name="check_any_arm_reachability", scope=strategy)

    if n_trials is None:
        n_trials = conf_n_trials
        # Issue #622 — NUR den Config-Default an die Dimensionalität koppeln (>= k·dim, k>=20), sonst ist
        # die Suche bei 14 Dimensionen faktisch Zufall. Der Sweep ruft ohne n_trials auf ⇒ skaliert.
        # Ein EXPLIZIT übergebenes n_trials (Test/CLI --n-trials) ist bewusst gewählt und wird exakt
        # respektiert. Legacy ohne den Key.
        n_trials = derive_n_trials(strategy, n_trials, opt_data)
        # Issue #830 Fix Punkt 2 — ein als 'deprioritized' diagnostiziertes Paar (signal_quality mit
        # mindestens einer, aber noch keiner vollen #830-Bestätigung) erhält ein reduziertes statt
        # volles Budget, statt entweder jeden Lauf voll zu suchen oder komplett zu verschwinden.
        n_trials = _apply_deprioritized_budget(strategy, symbol, n_trials, opt_data)
    # Issue #568 — n_startup_trials an die Parameterzahl der Strategie koppeln (>= k·dim), damit der
    # TPE bei multivariate=True,group=True genügend Startpunkte hat. Legacy, wenn der Key fehlt.
    n_startup_trials = derive_n_startup_trials(strategy, n_startup_trials, opt_data)

    # Issue #780/#800 — strategy/symbol/study_name sind fuer die GESAMTE Lebensdauer dieser Study
    # (jede Log-Zeile/jedes Event, ueber alle verschachtelten Aufrufe: Objective, floor_plateau_
    # callback, _emit_study_summary, ...) an diesen Thread gebunden. Der Bind selbst passiert jetzt
    # im ``with``-Block des ``optimize_symbol``-Wrappers (deckt DIESEN gesamten Koerper ab, siehe
    # dessen Docstring) — hier kein manuelles Enter/Exit mehr.
    sweep_dir = WORK / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    if storage is None:
        storage = resolve_storage(study_name=study_name)   # A4.7: SQLite-Default, ENV/JSON-Opt-in
    # Issue #747 — explizit konstruierte RDBStorage-Instanz statt Optuna die Engine intern bauen zu
    # lassen (siehe _resolve_rdb_storage-Docstring). ``rdb_storage`` bleibt hier referenziert, damit
    # sie nach Studien-Nutzung disposed werden kann.
    rdb_storage = _resolve_rdb_storage(storage)

    # Issue #755 — PER-STUDY-Seed statt eines sweep-weit IDENTISCHEN Seeds (der die Sweep-Level-
    # Serialisierung ``n_jobs=1`` in sweep.main erzwungen hatte). Konstant fuer diesen study_name,
    # unabhaengig von Ausfuehrungsreihenfolge/Parallelitaet.
    # Issue #1253 (GH #1123) — der Salt kommt ausschliesslich aus der Umgebungsvariable, DEMSELBEN
    # Mechanismus wie ``OPTIMIZER_WORK_DIR`` (siehe manifest.py-Docstring): jeder Prozess/Worker
    # setzt sie VOR dem Start, ``sweep.py``s ``--seed-salt``-CLI-Flag setzt sie fuer den
    # Eltern-Sweep-Prozess (von dem Worker-Subprozesse sie erben). ``None``/leerer String ⇒
    # bit-identisch zum Pre-#1253-Verhalten (kein Lauf-Anteil im Seed).
    run_salt = os.environ.get("OPTIMIZER_SEED_SALT") or None
    seed_eff = seed_effective(seed, study_name, run_salt)

    reward_mode = opt_data.get("reward_mode", "auto")
    directions = None
    if reward_mode == "pareto":
        directions = ["maximize", "maximize", "maximize", "maximize", "minimize", "minimize"]
        # Issue #950 (Katalog C) — siehe optimize_symbol/make_objective-Kommentar: derselbe
        # kontinuierliche, normierte Constraint-Pfad wie der Default-Modus statt eines rohen
        # (0.0, 0.0)-Defaults, der eine konstante Distanz-0 vortaeuschte.
        sampler = optuna.samplers.NSGAIISampler(constraints_func=_oos_constraints_func, seed=seed_eff)
    else:
        # Issue #612 — Feasibility in den Sampler (siehe optimize_symbol): constraints_func liest die
        # gestempelten OOS-Gate-Verletzungen; Optuna 4.9 bevorzugt feasible nativ vor infeasible.
        sampler = _WindowedTPESampler(
            multivariate=True,
            group=True,
            n_startup_trials=n_startup_trials,
            seed=seed_eff,
            constraints_func=_oos_constraints_func,
            # Issue #1089/#1237 — die EFFEKTIVE Obergrenze ist das Minimum aus tpe_history_window
            # (#1067/#1217) und tpe_fit_max_trials: keiner der beiden Werte kann die vom jeweils
            # anderen garantierte Obergrenze aufheben.
            history_window=min(
                _resolve_tpe_history_window(opt_data), _resolve_tpe_fit_max_trials(opt_data)),
        )

    # Issue #411 — serialisiert + DDL-Race-fest (table studies already exists). Ersetzt das nackte
    # optuna.create_study, das bei zwei Workern auf derselben frischen SQLite-Datei crasht.
    study = _create_study_with_retry(
        study_name=study_name,
        storage=rdb_storage,
        sampler=sampler,
        direction="maximize" if not directions else None,
        directions=directions
    )
    # Issue #747 — eigene Referenz auf die RDBStorage-Instanz am Study-Objekt halten (KEIN Zugriff
    # auf Optunas privates ``study._storage``/``_CachedStorage._backend``): der Per-Symbol-Sweep
    # (sweep.py) braucht sie, um NACH Phase 2 (Confirm/Export/Champion-Store — liest study.trials
    # erneut und reconnected die Engine damit lazy) ein zweites Mal zu disposen.
    study._etoro_rdb_storage = rdb_storage

    # Issue #851 — Study-Zeitstempel-Telemetrie (Root-Cause: der #742-Report fuehrte bislang KEINE
    # Wallclock-Zeit je einzelner Study, nur die Sweep-Gesamtzeit aus #742's Top-Level — eine
    # Aufschluesselung je Symbol/Strategie (Barriere-Wartezeit #828, worker_utilisation) war aus dem
    # Artefakt NICHT ableitbar und musste aus Log-Zeitstempeln rekonstruiert werden, was bei einem
    # stilleren Lauf (kein #740/#780-Log-Praefix je Zeile) nicht funktioniert). ``study_started_at_
    # utc``/``worker_id`` JETZT gesetzt (vor ``study.optimize``), ``study_ended_at_utc``/
    # ``study_wallclock_s`` im ``finally:``-Block unten (#833-Stil: auch bei einem vorzeitigen
    # Abbruch persistiert, nicht nur im Erfolgsfall).
    import datetime as _dt851
    study.set_user_attr("study_started_at_utc", _dt851.datetime.now(_dt851.timezone.utc).isoformat())
    study.set_user_attr("worker_id", threading.get_ident())
    # Issue #1104 (Katalog #937) — der Commit, auf dem die SIMULATION tatsaechlich lief, gestempelt
    # VOR dem ersten Trial (derselbe Zeitpunkt wie study_started_at_utc) — im Gegensatz zum
    # REPORT-Commit (report.py's git_commit_report, zur Berichtszeit gelesen), der bei einer
    # nachtraeglichen Report-Regenerierung (--report-only, generate_report_for_run) auf einem
    # NEUEREN Checkout laufen kann. Root-Cause #1104: eine EINZIGE git_commit()-Lesung zur
    # Berichtszeit vermischte beide Zeitpunkte unter demselben Feldnamen — ein Report ueber einen
    # aelteren Lauf trug dadurch stillschweigend den REPORT-, nicht den SIMULATIONS-Commit.
    study.set_user_attr("git_commit_simulation", git_commit())

    # Issue #410 — Reward-Semantik-Version pruefen/stempeln (Study-Hygiene gegen alte Floor-Trials).
    _check_reward_semantics_version(study, opt_data)
    # Issue #854 — orthogonale Simulations-Semantik-Version (WAS gemessen wurde, siehe dortigen
    # Docstring), unabhaengig geprueft/gestempelt.
    _check_simulation_semantics_version(study, opt_data)
    # Issue #968 — dritte orthogonale Achse: welches URTEIL (Selektionsstatistik definiert/Guard
    # getrippt) eine bereits simulierte Trade-Serie erhaelt, unabhaengig geprueft/gestempelt.
    _check_inference_semantics_version(study, opt_data)

    # Gate 2 — Warm-Start + Shrinkage-Referenz. Issue #565: definierter Fallback statt Silent-Zero.
    # Issue #704 — die Tier-Reihenfolge ist jetzt global_best → champion → strategy_defaults → none
    # (siehe resolve_symbol_shrinkage_seed-Docstring). Fehlt das echte globale Optimum, wird der
    # Champion (falls vorhanden) bzw. strategy_defaults der Warm-Start-Seed UND die
    # Shrinkage-Referenz (param_pen zieht Richtung Anker statt ins Leere).
    # ``shrinkage_inactive`` (Study-User-Attr) markiert LAUT & forensisch sichtbar (analog
    # Floor-Guard #409/#456), dass KEIN validierter Anker (global_best ODER champion) vorliegt —
    # unabhängig vom Defaults-Fallback, damit der fehlende Anker im Standalone-Sweep nie still bleibt.
    global_best, seed_source = resolve_symbol_shrinkage_seed(
        strategy, cfg_dir, symbol=symbol, opt_data=opt_data, catalog_newest_ns=catalog_newest_ns)
    # Issue #704/#853 — ein Champion (ODER ein Champion mit veralteter Quality-Telemetrie,
    # 'champion_quality_stale', #819) ist wie global_best ein ECHTER Anker (der param_pen zieht
    # Richtung eines real erreichten Holdout-Kandidaten statt ins Leere) — shrinkage_inactive
    # bleibt daher False für ALLE DREI Quellen.
    shrinkage_inactive = seed_source not in ("global_best", "champion", "champion_quality_stale")
    study.set_user_attr("shrinkage_seed_source", seed_source)
    study.set_user_attr("shrinkage_inactive", shrinkage_inactive)
    if seed_source in ("champion", "champion_quality_stale"):
        # Issue #709 — Study-User-Attrs & Log-Parität mit #565: jeder Warm-Start-Effekt eines
        # Champions ist forensisch nachvollziehbar (analog shrinkage_*-Telemetrie).
        from automation.optimizer import champions as _champions
        champion_entry = _champions.load_champion_entry(strategy, symbol, opt_data=opt_data)
        if champion_entry:
            lifecycle = champion_entry.get("lifecycle") or {}
            integrity = champion_entry.get("integrity") or {}
            corroboration_count = lifecycle.get("corroboration_count")
            r_symbol_at_store = (champion_entry.get("quality") or {}).get("R_symbol")
            first_seen_run = lifecycle.get("first_seen_run")
            # Issue #853 — traegt jetzt den PRAEZISEN seed_source-Wert (statt hartcodiert
            # 'champion'), damit die Quality-Stale-Unterscheidung auch in diesem Study-Attr sichtbar
            # ist, nicht nur im uebergeordneten shrinkage_seed_source.
            study.set_user_attr("champion_seed_source", seed_source)
            study.set_user_attr("champion_R_symbol_at_store", r_symbol_at_store)
            study.set_user_attr("champion_corroboration_count", corroboration_count)
            study.set_user_attr("champion_writeback_applied", bool(lifecycle.get("writeback_applied")))
            # Issue #709 — ``champion_age_runs``: TROTZ des Namens (Issue-Formel "now - first_seen_
            # run") KEIN Lauf-Zähler — der Champion-Eintrag führt keinen separaten Zähler "Anzahl
            # Läufe seit Erstsichtung" (nur ``corroboration_count``, der ausschliesslich bei
            # Regionsgleichheit erhöht wird). Hier daher ehrlich als verstrichene Kalendertage seit
            # ``first_seen_run`` berechnet (Wall-Clock-Alter), nicht als Lauf-Anzahl.
            try:
                import datetime as _dt
                _first_dt = _dt.datetime.strptime(first_seen_run, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=_dt.timezone.utc)
                champion_age_days = (_dt.datetime.now(_dt.timezone.utc) - _first_dt).total_seconds() / 86400.0
            except (TypeError, ValueError):
                champion_age_days = None
            study.set_user_attr("champion_age_days", champion_age_days)
            # Issue #709 — ``champion_window_advanced``: dieselbe Snooping-Schutz-Grösse wie
            # ``maybe_write_back`` (#706), hier rein informativ (KEINE Gate-Wirkung im Enqueue-Pfad).
            current_ns = integrity.get("catalog_newest_ns")
            first_ns = lifecycle.get("first_seen_catalog_newest_ns")
            window_advanced = None
            if current_ns is not None and first_ns is not None:
                min_advance_days = opt_data.get("champion_min_advance_days")
                if min_advance_days is None:
                    min_advance_days = _champions._default_min_advance_days(cfg_dir)
                advance_days = (current_ns - first_ns) / 1_000_000_000.0 / 86400.0
                window_advanced = advance_days >= float(min_advance_days or 0)
            study.set_user_attr("champion_window_advanced", window_advanced)
            logging.getLogger("optimizer").info(
                "[#704] %s/%s: Champion-Seed aktiv (R_symbol=%s, corroboration=%s, source_run=%s) "
                "— enqueue + param_pen.",
                strategy, symbol, r_symbol_at_store, corroboration_count, first_seen_run,
            )
    elif seed_source == "strategy_defaults":
        logging.getLogger("optimizer").warning(
            "[#565] %s/%s: kein global_best/Champion im Per-Symbol-Sweep (shrinkage_inactive) — "
            "Fallback auf strategy_defaults als Shrinkage-Referenz & Warm-Start-Seed (param_pen "
            "zieht Richtung Default statt ins Leere).",
            strategy, symbol,
        )
    elif seed_source == "none":
        logging.getLogger("optimizer").warning(
            "[#565] %s/%s: WEDER global_best NOCH Champion NOCH strategy_defaults auflösbar "
            "(shrinkage_inactive) ⇒ param_pen ≡ 0, der Per-Symbol-Vektor tunt ungezügelt Richtung "
            "CV-Rausch (Overfit-Risiko).",
            strategy, symbol,
        )
    if global_best:
        study.enqueue_trial(global_best)

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())
    # Issue #755 — Sweep-Report-Telemetrie: der tatsaechlich verwendete Per-Study-Seed + Budget,
    # damit der #742-Report je Study nachvollziehbar macht, WELCHER Seed/WELCHES Budget lief (Voraus-
    # setzung fuer den Determinismus-Nachweis bei n_jobs>1).
    study.set_user_attr("seed_effective", seed_eff)
    # Issue #1253 (GH #1123) Fix Punkt 2 — der wirksame Salt-Wert (oder None), je Study gestempelt
    # (macht sichtbar, OB dieser Lauf gesalzen war, ohne seed_effective selbst zu deanonymisieren)
    # und Eingang des run_fingerprint (report.compute_run_fingerprint).
    study.set_user_attr("seed_salt", run_salt)
    study.set_user_attr("n_trials_budget", n_trials)
    study.set_user_attr("n_startup_trials", n_startup_trials)
    # Issue #931 Fix 2 — der wallclock_budget_policy='degrade'-Faktor, der bereits in n_trials
    # eingerechnet ist (derive_n_trials), zusätzlich ROH telemetriert: damit bleibt
    # budget_executed_fraction (n_trials_completed / n_trials_budget) auch bei einer degradierten
    # Study korrekt interpretierbar (das Budget selbst war kleiner, nicht die Ausführung schwächer).
    _degrade_factor = wallclock_guard.read_degrade_factor(WORK)
    if _degrade_factor < 1.0:
        study.set_user_attr("n_trials_budget_degrade_factor", round(_degrade_factor, 4))
    # Issue #983 (Katalog D) Fix Punkt 3 Akzeptanzkriterium — IMMER gestempelt (auch 1.0 = kein
    # Degrade), damit "budget_degradation_factor erscheint in ... optimizer_study_completed"
    # verlaesslich erfuellt ist statt nur bei aktivem Degrade sichtbar zu sein. Die Kuerzung durfte
    # laut #983 "niemals stillschweigend geschehen" — ein Feld, das nur bei Aktivierung existiert,
    # ist fuer denselben Zweck nicht besser als ein WARNING-Logeintrag in einem 6-MB-Log.
    study.set_user_attr("budget_degradation_factor", round(_degrade_factor, 4))

    # Issue #796 — EINE eingefrorene Config je Study statt einer Kopie je Trial. n_folds=4/
    # holdout_days=45 sind exakt die Werte, die die Objective-Closure unten pro Trial an
    # build_trial uebergibt (siehe make_symbol_objective) — muessen hier identisch sein.
    study_config_dir = freeze_study_config(
        study_name, resolve_wf_settings(cfg_dir, holdout_days=45, n_folds=4), base_cfg=cfg_dir)
    objective = make_symbol_objective(
        strategy, symbol, global_best,
        run_backtest=run_backtest, build_trial=build_trial,
        catalog_newest_ns=catalog_newest_ns,
        catalog_span_days=catalog_span_days,
        study_config_dir=study_config_dir,
        run_id=run_id,
    )
    # Issue #409 — Fail-Loud-Guard: warnt, sobald nach n_startup_trials alle Trials am
    # Unevaluable-Floor kleben (Pitfall #75). Config einmalig gebunden (kein Per-Trial-IO).
    # Issue #456 — Produktion bindet stop_on_plateau=True: die als aussichtslos erkannte
    # Per-Symbol-Study früh beenden (spart ~84 nutzlose Trials, ~30 min pro Floor-Symbol).
    floor_guard = partial(floor_plateau_callback, weights=opt_data,
                          n_startup_trials=n_startup_trials, stop_on_plateau=True,
                          strategy=strategy, symbol=symbol, run_id=run_id)
    disk_guard_cb = partial(disk_budget_callback, opt_data=opt_data)
    # Issue #803 — periodischer Fruehabbruch bei systematischer Kohaerenz-Verletzung (statt erst
    # nach dem vollen Budget zu urteilen, siehe check_study_coherence_violation_rate-Docstring).
    coherence_guard_cb = partial(coherence_violation_early_abort_callback, opt_data=opt_data)
    # Issue #1026 (Katalog #866) — Root-Cause: ``compute_budget_execution`` leitete
    # ``stop_reason == 'EXCEPTION'`` bislang als ``else``-Zweig ab (weder Plateau-Flag noch volles
    # Budget), OHNE dass irgendein Codepfad eine tatsaechlich von ``study.optimize(..., catch=...)``
    # gefangene Exception zaehlte — jede Study, die aus einem ANDEREN Grund (z. B. dem #1025-
    # Stempel-Defekt) unter ihrem Budget blieb, wurde faelschlich als abgestuerzt gemeldet. Dieser
    # Wrapper zaehlt NUR echte, von ``catch`` abgefangene Exceptions (er lässt sie unveraendert
    # weiter propagieren, damit Optuna den Trial wie zuvor als FAIL markiert).
    _catch_types = (json.JSONDecodeError, OSError)
    _exception_counts: Counter = Counter()

    def _objective_with_exception_tracking(trial):
        try:
            return objective(trial)
        except _catch_types as _exc:
            _exception_counts[type(_exc).__name__] += 1
            raise

    try:
        study.optimize(_objective_with_exception_tracking, n_trials=n_trials, n_jobs=1,
                       catch=_catch_types,
                       callbacks=[floor_guard, retention_callback, disk_guard_cb, coherence_guard_cb])
        study.set_user_attr("n_trials_exception", sum(_exception_counts.values()))
        study.set_user_attr("exception_types", dict(_exception_counts))
        # Issue #415 — Per-Study-Summary (Timing + Evaluierbarkeit) als strukturiertes Event.
        _emit_study_summary(study, symbol, study_t0, strategy=strategy,
                           n_startup_trials=n_startup_trials, run_id=run_id)
        # Issue #773 — Kohaerenz-Invariante fail-loud statt eines reinen Report-Nachtrags.
        # Issue #803 — Sicherheitsnetz fuer den Fall, dass der periodische Callback (alle 32 Trials)
        # keine exakte Intervallgrenze traf, BEVOR das Budget erschoepft war; hat der Callback die
        # Study bereits als ueberschritten markiert, wuerde diese erneute Pruefung dasselbe
        # STUDY_ABORTED_ON_INVARIANT-Ereignis ein zweites Mal emittieren — daher uebersprungen.
        if not (getattr(study, "user_attrs", None) or {}).get("coherence_violation_rate_exceeded"):
            check_study_coherence_violation_rate(study, opt_data)
    finally:
        # Issue #851 — im finally-Block (analog #833s Abbruchresilienz): auch eine vorzeitig
        # abgebrochene Study (Disk-/Wallclock-Guard, Kohaerenz-Abbruch, Exception) traegt eine
        # ended_at_utc/wallclock_s-Telemetrie, statt nur eine erfolgreich durchgelaufene. Fail-open:
        # ein Fehler hier darf einen sonst erfolgreichen Optimize-Lauf nicht crashen lassen.
        try:
            study.set_user_attr(
                "study_ended_at_utc", _dt851.datetime.now(_dt851.timezone.utc).isoformat())
            study.set_user_attr("study_wallclock_s", round(time.perf_counter() - study_t0, 3))
            # Issue #1067/#1217 Fix Punkt 3 — TPE-Surrogat-Fit-Zeit, konsumiert von
            # invariants.check_search_overhead_share zusammen mit study_wallclock_s oben.
            if hasattr(sampler, "_fit_seconds_total"):
                study.set_user_attr("tpe_fit_seconds", round(sampler._fit_seconds_total, 4))
                # Issue #1089/#1237 (P1) — je Study gestempelt, Rohmaterial fuer
                # invariants.check_tpe_fit_cost_share und das Akzeptanzkriterium
                # "tpe_fit_trials_used <= tpe_fit_max_trials".
                study.set_user_attr("tpe_fit_trials_used", sampler._last_trials_used)
                study.set_user_attr("tpe_fit_trials_available", sampler._last_trials_available)
        except Exception:
            logging.getLogger("optimizer").warning(
                "[#851] Study-Zeitstempel-Telemetrie für '%s' fehlgeschlagen (non-fatal).",
                study_name, exc_info=True,
            )
        # Issue #794 — Study-Ebene als Sicherheitsnetz (die #794-Trial-Ebene oben laeuft je Trial;
        # dieser Aufruf raeumt zusaetzlich auf, falls study.optimize() vorzeitig abgebrochen wurde,
        # BEVOR der Retention-Callback den letzten Trial noch sehen konnte). Fail-open: ein
        # Retention-Fehler darf einen erfolgreichen Optimize-Lauf nie im Nachhinein als
        # gescheitert erscheinen lassen (analog #703/#733).
        try:
            retention.prune_completed_trial_dirs(
                study_name, retention.collect_referenced_trial_dirs(), work_dir=WORK)
        except Exception:
            logging.getLogger("optimizer").warning(
                "[#794] Study-Retention für '%s' fehlgeschlagen (non-fatal).",
                study_name, exc_info=True,
            )
        # Issue #747 — Engine NACH Phase-1-Nutzung disposen (auch bei einer hier propagierenden
        # Exception): deckelt die Spitzenlast gleichzeitig offener SQLAlchemy-Engines im Per-Symbol-
        # Sweep auf ~n_jobs statt auf die Gesamtzahl der Paare. Nachfolgende Lesezugriffe (sweep.py:
        # familienweite Aggregation, Confirm/Export) reconnecten lazy und disposen dort erneut.
        _dispose_storage(rdb_storage)
        # Issue #780 — der Study-Log-Kontext wird jetzt vom ``with``-Block in ``optimize_symbol``
        # symmetrisch geschlossen (siehe dort); kein manuelles Exit mehr hier.
    return study


def run(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    log_active_config(f"global optimize · {strategy}", extra={"n_jobs": n_jobs, "n_trials_override": n_trials})
    study = optimize(strategy, n_trials=n_trials, n_jobs=n_jobs)
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if not completed:
        export_no_viable_proposal(study, strategy)
        return
    holdout_res = confirm_on_holdout(study, strategy)
    export_proposal(study, strategy, holdout_res)
    # Issue #733 — Normalfall-Retention: die Study ist jetzt abgeschlossen (Confirm + Export
    # gelaufen); ihr IS-Trial-Baum wird ab hier nicht mehr gebraucht. Fail-open: ein Retention-
    # Fehler darf einen erfolgreichen Optimize-Lauf nie im Nachhinein als gescheitert erscheinen
    # lassen.
    try:
        retention.prune_completed_trial_dirs(
            study.study_name, retention.collect_referenced_trial_dirs())
    except Exception:
        logging.getLogger("optimizer").warning(
            "[#733] Trial-Verzeichnis-Retention für Study '%s' fehlgeschlagen (non-fatal).",
            study.study_name, exc_info=True,
        )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Hyperparameter Optimization")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy class name to optimize")
    parser.add_argument("--n-trials", type=int, default=None, help="Number of trials (overrides config)")
    parser.add_argument("--n-jobs", type=int, default=1, help="Number of parallel worker jobs")

    args = parser.parse_args()
    run(strategy=args.strategy, n_trials=args.n_trials, n_jobs=args.n_jobs)
