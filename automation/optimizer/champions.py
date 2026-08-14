"""Champion-Store (Epic #702, Ebene 1 + Ebene 2 — Issues #703-#710; gehärtet #817-#821).

Iterativer Warm-Start & symbol-skopierte Default-Nachführung, OHNE die Selektions-Integrität zu
brechen, die die Optimizer-Härtung seit #649 aufgebaut hat: ein Champion ist immer nur ein
zusätzlicher Enqueue-Kandidat, der beim nächsten Sweep-Lauf erneut VOLLSTÄNDIG durch alle
Eligibility-/Holdout-/DSR-Gates läuft (integritätsneutral) — er drückt nie unvalidierte,
symbol-übergefittete Parameter direkt in einen Live-Pfad.

Drei Ebenen, drei Gates (siehe Issue #705 §3):
    Ebene 1 (Such-Anker, jeder Lauf, niedriges Risiko):
        ``store_champion`` (#703) persistiert den besten *erreichten* Holdout-Kandidaten je
        (Strategie, Symbol) unter ``data/optimizer/champions/``; ``load_champion_seed``/
        ``load_champion_entry`` (#704) lesen ihn als Warm-Start-Seed (zwischen ``global_best``
        und ``strategy_defaults`` in der Tier-Reihenfolge von
        ``run_optimization.resolve_symbol_shrinkage_seed``).
    Ebene 2 (Default-Nachführung, korroboriert, mittleres Risiko):
        ``maybe_write_back`` (#706) schreibt einen Champion NUR nach ``strategy_symbol_seeds.json``
        (symbol-skopiert, NIE ``strategy_defaults.json``), wenn er entweder eine echte
        READY_FOR_PR-Promotion ist ODER über ``champion_promote_after_runs`` Läufe UND ein
        fortgeschrittenes Datenfenster (Snooping-Schutz) korroboriert wurde.
    Ebene 3 (Live-Deployment) bleibt UNVERÄNDERT menschlich (HI-3): dieses Modul schreibt
        NIEMALS ``strategies.json``.

``champion_is_admissible`` (#705) ist die EINE zentrale Guard-Funktion, die sowohl beim Schreiben
(``store_champion``) als auch beim Lesen (``load_champion_entry``) entscheidet, ob ein
Champion-Kandidat/-Eintrag zulässig ist — reward-version, override-keys, Rejection-Allowlist,
R_symbol-Floor, Demotion-Schwelle. Keine Guard-Logik ist im Sweep/Resolver dupliziert.

Issue-Katalog #817-#821 (Befund: der Store war in allen drei Dimensionen unwirksam/falsch
parametrisiert — siehe GitHub-Issue #749) härtet vier Achsen nach:
    #817 — ``REJECT_HOLDOUT_GATE`` (ein am Holdout-Gate SELBST gescheiterter Kandidat, kein
        knapp verfehlter Gewinner) ist NICHT MEHR pauschal zulässig; nur noch aufgeschlüsselt
        (``holdout_binding_gate``, #786) UND innerhalb einer konfigurierbaren Marge
        (``champion_max_holdout_gate_shortfall``). Die übrige Allowlist ist deklarativ aus
        ``optimizer.json['champion_admissible_reject_details']`` ableitbar.
    #819 — ein ``reward_semantics_version``-Bump entwertet nur die QUALITY-Bewertung
        (``champion_quality_stale``), NICHT den Parametervektor selbst: ``params`` und
        ``lifecycle.corroboration_count`` überleben einen Bump (nur ein
        ``params_schema_version``-Mismatch — der SUCHRAUM selbst hat sich geändert — verwirft
        den Eintrag vollständig).
    #820 — zusätzliches relatives Qualitätskriterium ``R_symbol > R_global + champion_min_
        tuning_edge`` (ein symbol-getunter Vektor, der schlechter ist als der ungetunte globale
        Default, ist als Suchanker strikt schädlich); Cross-Snapshot-Vergleiche werden nicht mehr
        roh gegeneinander bewertet (der neuere Snapshot gewinnt immer, Korroboration bleibt
        erhalten).
    #821 — ``run_id`` ist jetzt ein Pflichtparameter (die SWEEP-run_id, kein Schreibzeitstempel);
        ``corroboration_count`` erhöht sich nur, wenn sich die ``run_id`` seit dem letzten
        Schreiben geändert hat (``lifecycle.last_seen_run``). Ein versionsfremder Alt-Eintrag,
        dessen Nachfolge-Kandidat selbst unzulässig ist, wird nach ``_stale/`` quarantäniert statt
        still auf der Platte liegen zu bleiben.

Issue #853 (P2, Befund: 826/826 Studies ``[#565] shrinkage_inactive`` — der Store bleibt
strukturell leer) — Root-Cause-Analyse ergab KEINEN neuen Code-Defekt, sondern eine Kopplung
DREIER korrekter Mechanismen zu einem Deadlock (dieselbe Klasse wie #829/#828, Pitfall #258):
#834 (v17→v18) entwertete den Vorlauf-Store bei Laufbeginn; ohne #840/#841 (Resume/Ledger)
bricht jeder Lauf bei ~59 Symbolen ab und deckt nie die volle Universe-Rotation ab;
``maybe_write_back`` verlangt kumulativ Versionsgleichheit + ``corroboration_count >= 2`` +
``champion_min_advance_days`` — bei einem Semantik-Bump je Sitzung und einer Trunkierung bei 59
Symbolen ist ``corroboration_count >= 2`` strukturell unerreichbar.

Umgesetzt in DIESEM Katalog (additiv, siehe ``run_optimization.resolve_symbol_shrinkage_seed``/
``report._seed_source_distribution``/``invariants.check_champion_seed_coverage``):
``seed_source`` unterscheidet jetzt ``'champion'`` von ``'champion_quality_stale'`` (#819) als
POSITIVE Telemetrie (vorher existierte nur die ``[#565]``-Negativ-WARNUNG), im #742-Report
aggregiert und gegen eine 90-%-``strategy_defaults``-Schwelle geprüft (bewusst auf EINEN Lauf
skopiert statt der im Issue-Text verlangten Zwei-Lauf-Persistenz — siehe
``check_champion_seed_coverage``-Docstring).

BEWUSST NICHT umgesetzt (dokumentierter Scope-Cut, dieselbe Entscheidungsklasse wie #843/#845
Punkt 2 in diesem Katalog): die vom Issue-Text verlangte Umstellung von
``corroboration_count`` (zählt SCHREIBVORGÄNGE, #821) auf ``corroborating_snapshots: list[{sha256,
first_seen_at, advance_days}]`` (zählt tatsächlich VERSCHIEDENE Daten-Snapshots mit zeitlichem
Abstand — die statistisch korrekte Korroborations-Definition). Diese Umstellung betrifft das
gespeicherte JSON-Schema JEDES Champion-Eintrags, ``maybe_write_back``s gesamte Entscheidungskette
(:737 ff.) UND ``champion_quality_stale``s Interaktion mit einer neuen, bislang ungetesteten
Datenstruktur — ohne einen realen Mehrfach-Snapshot-Sweep-Lauf zur Regressionsverifikation in
diesem Environment ist das Risiko einer stillen Korruption des einzigen persistenten
Cross-Sitzungs-Zustands (dem Champion-Store selbst) höher als der Nutzen in diesem Durchgang. Der
naheliegende, WENIGER riskante erste Schritt — #840/#841 (Resume/Ledger, bereits umgesetzt,
Katalog B) — behebt bereits die Trunkierungs-Ursache; ob die verbleibende
``corroboration_count>=2``-Hürde nach einem vollen Abdeckungszyklus noch strukturell unerreichbar
ist, sollte an ECHTEN Zwei-Zyklen-Daten neu beurteilt werden, bevor die Datenmodell-Umstellung
riskiert wird.
"""
import json
import logging
from pathlib import Path

from automation.optimizer.manifest import WORK, catalog_fingerprint
from automation.log_manager import emit_execution_event

# Issue #703 (Gate, Punkt 2) — Rejections, die bedeuten, dass der Kandidat den Holdout nie erreicht
# hat (kein Backtest-Ergebnis, aus dem ein Seed sinnvoll wäre).
_UNREACHED_HOLDOUT_REJECT_DETAILS = frozenset({
    "HOLDOUT_NO_ELIGIBLE_TRIALS",
    "REJECT_HOLDOUT_UNREACHABLE",
})
# Issue #703 (Gate, Punkt 2) — Status-Werte, die grundsätzlich keinen speicherwürdigen Kandidaten
# tragen (kein abgeschlossener Trial überhaupt).
_INADMISSIBLE_STATUS = frozenset({"NO_VIABLE_TRIAL"})

# Issue #817 — Default-Allowlist, wenn ``optimizer.json['champion_admissible_reject_details']``
# fehlt: NUR die beiden Klassen, die das Holdout-Gate SELBST bestanden haben und lediglich an der
# Multiplizitäts- bzw. Konfidenzkorrektur gescheitert sind (der Parametervektor ist real). Bewusst
# OHNE ``REJECT_HOLDOUT_GATE`` — diese Klasse hat ihr eigenes, strengeres Zulassungskriterium
# (siehe ``champion_is_admissible``/``_holdout_gate_shortfall_relative``), weil sie den Holdout
# selbst nicht bestanden hat (ein gemessener OOS-Durchfaller, kein knapp verfehlter Gewinner).
# ``REJECT_SELECTION_PBO``/``REJECT_BOUNDARY_SOLUTION`` bleiben AUSGESCHLOSSEN (würden die
# Folgesuche destabilisieren statt sie zu verankern).
_DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS = frozenset({
    "REJECT_HOLDOUT_DSR_DROP",
    "REJECT_HOLDOUT_BOOTSTRAP_CI",
})

# Issue #817 — Rückübersetzung eines ``holdout_gate_deltas``-Schlüssels (siehe ``confirm.py``/
# ``backtest_runner.py``, ``oos_``-präfigierte Delta-Spalten, Konvention "negativ = Gate verfehlt")
# auf den zugehörigen Schwellen-Key in ``tournament.json``, für die relative Shortfall-Berechnung
# (analog ``reward._shortfall_distance``). Nur Keys, die tatsächlich als ``oos_gate_deltas``-Spalte
# existieren (``backtest_runner._evaluate_oos_eligibility``).
_GATE_KEY_TO_THRESHOLD_CONFIG_KEY = {
    "oos_min_trades": "oos_min_trades",
    "oos_min_total_return": "min_total_return",
    "oos_min_expectancy": "min_expectancy",
    "oos_max_drawdown": "max_drawdown",
    "oos_min_win_rate": "oos_min_win_rate",
    "oos_min_sortino": "oos_min_sortino",
    "oos_min_psr": "oos_min_psr",
    "oos_min_profit_factor": "oos_min_profit_factor",
    "oos_min_excess_return": "oos_min_excess_return",
    "oos_min_profitable_folds_frac": "oos_min_profitable_folds_frac",
}


def _sanitize(symbol: str) -> str:
    """'TSLA.ETORO' -> 'TSLA_ETORO'. Bewusst hier dupliziert (nicht aus run_optimization
    importiert) — eine triviale, reine String-Transformation; ein Modul-Import würde einen
    Zirkelimport riskieren, da run_optimization.py umgekehrt dieses Modul importiert (#704)."""
    return symbol.replace(".", "_")


def _champions_dir() -> Path:
    d = WORK / "champions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _champion_path(strategy: str, symbol: str) -> Path:
    return _champions_dir() / f"champion_{strategy}_{_sanitize(symbol)}.json"


def _read_entry(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_entry(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)


def _quarantine_champion(strategy: str, symbol: str, path: Path, entry: dict, *, reason: str) -> None:
    """Issue #821 — statt eines stillen Liegenbleibens auf der Platte: ein versionsfremder
    (``params_schema_version``-inkompatibler) Alt-Eintrag, dessen Nachfolge-Kandidat selbst
    unzulässig ist, wird nach ``data/optimizer/champions/_stale/`` verschoben (KEIN ``unlink`` —
    die Historie bleibt forensisch verfügbar), statt weiterhin unter dem aktiven Store-Pfad zu
    liegen. Fail-open: ein Verschiebefehler darf den Sweep nie crashen."""
    import datetime as dt

    stale_dir = _champions_dir() / "_stale"
    try:
        stale_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = stale_dir / f"{path.stem}_{stamp}.json"
        if path.exists():
            path.replace(dest)
        else:
            _write_entry(dest, entry)
    except OSError:
        logging.getLogger("optimizer").warning(
            "[#821] %s/%s: Quarantäne des versionsfremden Champion-Eintrags fehlgeschlagen "
            "(non-fatal).", strategy, symbol, exc_info=True,
        )
        return
    emit_execution_event(logging.getLogger("optimizer"), "CHAMPION_STALE_QUARANTINED", {
        "strategy": strategy, "symbol": symbol, "reason": reason,
    })
    logging.getLogger("optimizer").warning(
        "[#821] %s/%s: versionsfremder Champion-Eintrag quarantäniert (%s) -> %s.",
        strategy, symbol, reason, dest,
    )


def _same_region(strategy: str, params_a: dict, params_b: dict, opt_data: dict) -> bool:
    """Issue #707 — Regionsgleichheit: dieselbe normierte, ``[0,1]``-skalierte Parameter-Distanz
    wie die A4.3-Shrinkage (``bounds.normalized_param_distance`` gegen die ``spaces.py``-Bandbreite
    jedes Parameters) — bewusst dieselbe, bereits etablierte Metrik statt einer zweiten,
    abweichenden Distanzdefinition (DRY, konsistente Skala über den gesamten Optimizer). Innerhalb
    ``champion_region_eps`` (Default 0.10) ⇒ derselbe Champion (Korroboration); sonst ein neuer."""
    if not params_a or not params_b:
        return False
    from automation.optimizer import bounds
    try:
        b = bounds.extract_numeric_bounds(strategy)
    except ValueError:
        return False
    eps = float(opt_data.get("champion_region_eps", 0.10))
    return bounds.normalized_param_distance(params_a, params_b, b) <= eps


def _params_schema_version(strategy: str) -> str | None:
    """Issue #819 — Signatur des SUCHRAUMS (sortierte Parameter-NAMEN aus
    ``bounds.extract_numeric_bounds``, KEINE Bounds-WERTE — eine reine Bounds-Verschiebung ändert
    diese Signatur nicht, nur ein hinzugefügter/entfernter Parameter macht einen gespeicherten
    ``params``-Vektor strukturell inkompatibel, weil ``store_champion``/``resolve_params`` ihn dann
    gegen einen anderen Suchraum anwenden würden). ``None`` bei unbekannter Strategie (ein reines
    Store-Housekeeping-Feld darf nie crashen)."""
    from automation.optimizer import bounds
    try:
        keys = sorted(bounds.extract_numeric_bounds(strategy).keys())
    except ValueError:
        return None
    return ",".join(keys)


def _load_tournament_cfg(base_cfg: Path | None = None) -> dict:
    """Issue #817 — lazy-geladenes ``tournament.json`` für die Schwellen-Auflösung des bindenden
    Holdout-Gates (``_holdout_gate_shortfall_relative``). Fail-open (leeres Dict), analog
    ``_default_min_advance_days`` — eine fehlende/kaputte Datei darf die Admissibility-Prüfung
    nicht crashen lassen (sie macht die REJECT_HOLDOUT_GATE-Klasse dann konservativ unzulässig,
    siehe Aufrufer)."""
    from automation.optimizer.trial_config import config_dir
    path = (base_cfg or config_dir()) / "tournament.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _configured_admissible_reject_details(opt_data: dict) -> frozenset[str]:
    """Issue #817 Fix Punkt 3 — die Rejection-Allowlist ist DEKLARATIV aus
    ``optimizer.json['champion_admissible_reject_details']`` ableitbar (dieselbe Governance wie
    ``tournament.json['gate_consolidation_priority']`` nach #810), statt einer eingefrorenen
    Modul-Konstante. ``REJECT_HOLDOUT_GATE`` wird HIER NIE zugelassen — diese Klasse hat ihr
    eigenes, strengeres Kriterium (Shortfall-Marge, siehe ``champion_is_admissible``); ein
    Operator, der sie versehentlich in diese flache Liste einträgt, bekommt NICHT die schwächere
    Prüfung. Fehlt der Key ⇒ ``_DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS``."""
    raw = opt_data.get("champion_admissible_reject_details")
    if raw is None:
        return _DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS
    # Issue #839 — REJECT_INVALID_TIMEBOX (Simulation verletzt den #714/GR-01-Zeitbox-Vertrag)
    # darf NIE zulässig sein, ebensowenig wie REJECT_HOLDOUT_GATE: der Parametervektor ist unter
    # einer bekanntermassen fehlerhaften Simulation nicht als "bewährt" belegbar.
    return frozenset(raw) - {"REJECT_HOLDOUT_GATE", "REJECT_INVALID_TIMEBOX"}


def _holdout_gate_shortfall_relative(gate_deltas: dict, binding_gate: str,
                                     tournament_cfg: dict) -> float | None:
    """Issue #817 Fix Punkt 2 — dimensionslose, auf die Gate-Schwelle normierte Unterschreitung des
    bindenden Holdout-Gates (analog ``reward._shortfall_distance``): ``|delta| / |threshold|``.
    ``gate_deltas`` folgt der ``oos_gate_deltas``-Konvention (``actual − threshold``, für
    ``oos_max_drawdown`` bereits invertiert zu ``cap − actual`` — negativ heisst in BEIDEN Fällen
    "Gate verfehlt"). ``None``, wenn Delta oder Schwelle nicht auflösbar sind (der Aufrufer
    behandelt ``None`` konservativ als nicht-zulässig, kein erfundener Wert)."""
    delta = gate_deltas.get(binding_gate)
    if not isinstance(delta, (int, float)):
        return None
    if delta >= 0:
        return 0.0
    threshold_key = _GATE_KEY_TO_THRESHOLD_CONFIG_KEY.get(binding_gate)
    threshold = tournament_cfg.get(threshold_key) if threshold_key else None
    if not threshold:
        return None
    return abs(delta) / abs(float(threshold))


def champion_quality_stale(entry: dict, opt_data: dict) -> bool:
    """Issue #819 — ``True``, wenn die QUALITY-Bewertung (``quality.R_symbol``/``R_global``/
    ``reward``) des Eintrags unter einer ANDEREN ``reward_semantics_version`` gemessen wurde als
    der aktuellen Config. Der Parametervektor bleibt trotzdem seed-fähig (siehe
    ``champion_is_admissible`` — ein reiner Versions-Mismatch schliesst NICHT mehr aus, der Seed
    durchläuft beim Enqueue ohnehin erneut ALLE Gates auf frischen Daten), verliert aber die
    WRITEBACK-Berechtigung (``maybe_write_back``), bis ``quality`` unter der aktuellen Semantik neu
    gemessen wurde (Root-Cause #819: sonst annulliert jeder Bump — im Schnitt alle 1-2 Tage —
    die Korroborationskette vollständig, strukturell unerreichbar)."""
    integrity = entry.get("integrity") or {}
    reward_version = opt_data.get("reward_semantics_version")
    if reward_version is None:
        return True
    return integrity.get("reward_semantics_version") != reward_version


def champion_simulation_stale(entry: dict, opt_data: dict) -> bool:
    """Issue #854 — ``True``, wenn die SIMULATION selbst (Exit-Pfad #836-#838, Zeitbox-Semantik
    #839, T-abhängige Guard-Schwelle #844 — siehe ``optimizer.json['simulation_semantics_version']``
    -Schema für die vollständige Abgrenzung reward/simulation/params_schema) unter einer ANDEREN
    Version gemessen wurde als der aktuellen Config.

    ANDERS als ``champion_quality_stale`` (ein reiner ``reward_semantics_version``-Mismatch
    entwertet NUR die Quality-Bewertung, der Parametervektor bleibt seed-fähig, #819): ein
    ``simulation_semantics_version``-Mismatch entwertet den EINTRAG VOLLSTÄNDIG (params UND
    quality) — die gemessenen Metriken (Haltedauern, Equity-Kurve, Trade-Zahlen), aus denen
    ``params`` als "bewährt" hervorging, wurden unter einem ANDEREN Handelsvertrag erhoben; der
    Parametervektor selbst ist unter der neuen Simulation nicht mehr belegbar (dieselbe Argumentation
    wie bei einem ``params_schema_version``-Mismatch — der SUCHRAUM hat sich geändert —, hier auf der
    MESS-Ebene statt der Suchraum-Ebene). ``None``/fehlende Version auf einer der beiden Seiten
    (Legacy-Eintrag vor #854, oder Config ohne den Key) ⇒ konservativ NICHT als Mismatch gewertet
    (kein falscher Datenverlust durch ein Feld, das der Alt-Eintrag/die Alt-Config noch nicht kannte)."""
    integrity = entry.get("integrity") or {}
    current = opt_data.get("simulation_semantics_version")
    stored = integrity.get("simulation_semantics_version")
    if current is None or stored is None:
        return False
    return stored != current


def champion_is_admissible(entry: dict, opt_data: dict,
                           tournament_cfg: dict | None = None) -> tuple[bool, str | None]:
    """Issue #705 (P0), gehärtet #817/#819/#820 — die EINE zentrale Guard-Einheit für
    Champion-Zulässigkeit. Wird sowohl beim Schreiben (``store_champion``, gegen einen frisch
    gebauten Kandidaten) als auch beim Lesen (``load_champion_entry``, gegen den gespeicherten
    Eintrag) aufgerufen — identische Prüfkette, keine duplizierte Guard-Logik in
    sweep.py/run_optimization.py.

    Reines Prädikat: keine Mutation (``tournament_cfg`` wird nur bei Bedarf, für die
    ``REJECT_HOLDOUT_GATE``-Marge, lazy von der Platte gelesen, falls nicht injiziert). Rückgabe
    ``(ok, reason)`` — ``reason`` ist ``None`` bei ``ok=True``, sonst ein stabiler Kurzcode
    (analog ``gate.is_symbol_tunable``) für Tests/Logs.

    Geprüfte Bedingungen:
      1. ``champion_enabled`` (globaler Kill-Switch, Default ``true``).
      2. ``params`` nicht-leer (override-keys > 0).
      3. ``status_at_store`` nicht in {NO_VIABLE_TRIAL}.
      4. ``holdout_reject_detail`` hat den Holdout erreicht (nicht HOLDOUT_NO_ELIGIBLE_TRIALS/
         REJECT_HOLDOUT_UNREACHABLE).
      5. Issue #817 — ``holdout_reject_detail == 'REJECT_HOLDOUT_GATE'``: NUR zulässig, wenn der
         Eintrag ``holdout_binding_gate``/``holdout_gate_deltas`` (#786) trägt UND die relative
         Unterschreitung des bindenden Gates innerhalb ``champion_max_holdout_gate_shortfall``
         liegt (Default 0.0 ⇒ effektiv geschlossen, bis ein Operator explizit eine Marge
         freigibt). Jede ANDERE ``holdout_reject_detail`` muss in der deklarativen Allowlist
         (``_configured_admissible_reject_details``) stehen.
      6. ``R_symbol >= champion_min_R_symbol`` (absoluter Qualitäts-Floor, Default 0.0).
      7. Issue #820 — ``R_symbol > R_global + champion_min_tuning_edge`` (relatives
         Qualitätskriterium, wenn ``R_global`` bekannt ist): ein symbol-getunter Vektor, der
         nicht besser als der ungetunte globale Default ist, ist als Suchanker strikt schädlich.
      8. ``reward_semantics_version`` fehlt in der Config ⇒ inadmissibel (degenerierte Config).
         Issue #819 — ein reiner VERSIONS-MISMATCH des Eintrags gegen die Config schliesst NICHT
         mehr aus (siehe ``champion_quality_stale`` für die zugehörige Writeback-Sperre).
      9. ``degrade_streak < champion_demote_after_runs`` (#708 — nicht demoted).
    """
    if not opt_data.get("champion_enabled", True):
        return False, "CHAMPION_DISABLED"

    params = entry.get("params") or {}
    if not params:
        return False, "EMPTY_PARAMS"

    provenance = entry.get("provenance") or {}
    if provenance.get("status_at_store") in _INADMISSIBLE_STATUS:
        return False, "INADMISSIBLE_STATUS"
    reject_detail = provenance.get("holdout_reject_detail")
    if reject_detail in _UNREACHED_HOLDOUT_REJECT_DETAILS:
        return False, "HOLDOUT_UNREACHED"

    if reject_detail == "REJECT_HOLDOUT_GATE":
        binding_gate = provenance.get("holdout_binding_gate")
        gate_deltas = provenance.get("holdout_gate_deltas") or {}
        if not binding_gate:
            return False, "REJECTION_NOT_ALLOWLISTED"
        cfg = tournament_cfg if tournament_cfg is not None else _load_tournament_cfg()
        shortfall = _holdout_gate_shortfall_relative(gate_deltas, binding_gate, cfg)
        if shortfall is None:
            return False, "REJECTION_NOT_ALLOWLISTED"
        max_shortfall = float(opt_data.get("champion_max_holdout_gate_shortfall", 0.0))
        if shortfall > max_shortfall:
            return False, "HOLDOUT_GATE_SHORTFALL_TOO_LARGE"
    elif reject_detail is not None and reject_detail not in _configured_admissible_reject_details(opt_data):
        return False, "REJECTION_NOT_ALLOWLISTED"

    quality = entry.get("quality") or {}
    r_symbol = quality.get("R_symbol")
    r_global = quality.get("R_global")
    min_r = float(opt_data.get("champion_min_R_symbol", 0.0))
    if r_symbol is None or r_symbol < min_r:
        return False, "BELOW_QUALITY_FLOOR"

    min_edge = float(opt_data.get("champion_min_tuning_edge", 0.0))
    if r_global is not None and r_symbol <= r_global + min_edge:
        return False, "NO_TUNING_EDGE"

    reward_version = opt_data.get("reward_semantics_version")
    if reward_version is None:
        return False, "REWARD_SEMANTICS_MISMATCH"

    # Issue #854 — ANDERS als ein reiner reward_semantics_version-Mismatch (nur die Quality-
    # Bewertung wird stale, params bleiben seed-fähig, #819): ein simulation_semantics_version-
    # Mismatch schliesst den EINTRAG VOLLSTÄNDIG aus (siehe champion_simulation_stale-Docstring).
    if champion_simulation_stale(entry, opt_data):
        return False, "SIMULATION_SEMANTICS_MISMATCH"

    lifecycle = entry.get("lifecycle") or {}
    degrade_streak = int(lifecycle.get("degrade_streak", 0) or 0)
    demote_after = int(opt_data.get("champion_demote_after_runs", 2))
    if degrade_streak >= demote_after:
        return False, "DEMOTED"

    return True, None


def _bump_corroboration(prior_lifecycle: dict, run_id: str) -> tuple[int, str]:
    """Issue #821 — ``corroboration_count`` zählt LÄUFE (verschiedene ``run_id``), nicht
    Schreibvorgänge: zwei ``store_champion``-Aufrufe mit DERSELBEN ``run_id`` (z. B. ein Confirm-
    Retry innerhalb desselben Sweeps) erhöhen den Zähler nicht. Gibt ``(neuer_count,
    last_seen_run)`` zurück — ``last_seen_run`` ist immer die aktuelle ``run_id`` (dieser
    Schreibvorgang fand offensichtlich in diesem Lauf statt, unabhängig davon, ob er zählte)."""
    prior_count = int(prior_lifecycle.get("corroboration_count", 1) or 1)
    if prior_lifecycle.get("last_seen_run") == run_id:
        return prior_count, run_id
    return prior_count + 1, run_id


def _build_entry_from_promotion(study, strategy: str, symbol: str, promotion: dict, *,
                                catalog_newest_ns: int | None, opt_data: dict,
                                tier: str = "all", run_id: str) -> dict:
    """Issue #703 (Datenmodell §4), erweitert #786/#819/#821 — baut die Champion-Store-
    Repräsentation AUSSCHLIESSLICH aus bereits im Proposal/Study vorhandenen Feldern (keine neue
    Berechnung): ``params`` == ``promotion['symbol_params']`` (== das spätere
    ``proposed_instrument_override``), ``R_symbol``/``R_global``/``holdout_reject_detail``/
    ``status`` == dieselben Felder, die ``confirm.export_symbol_proposal`` in
    ``proposal_{strategy}_{symbol}.json`` schreibt; ``holdout_binding_gate``/``holdout_gate_deltas``
    (#786) aus ``promotion['metrics_symbol']`` — Grundlage für die #817-Shortfall-Marge;
    ``reward_semantics_version``/``params_schema_version`` (#819) aus Config/Suchraum;
    ``data_snapshot_sha256`` aus ``manifest.catalog_fingerprint()``; ``run_id`` (#821) ist die
    SWEEP-run_id (Pflichtparameter, kein Schreibzeitstempel)."""
    try:
        reward = (study.best_value
                  if len(getattr(study, "directions", ["maximize"])) == 1
                  else promotion.get("R_symbol", 0.0))
    except ValueError:
        reward = None
    r_symbol = promotion.get("R_symbol")
    metrics_symbol = promotion.get("metrics_symbol") or {}
    return {
        "strategy": strategy,
        "symbol": symbol,
        "tier": tier,
        "params": dict(promotion.get("symbol_params") or {}),
        "quality": {
            "R_symbol": r_symbol,
            "R_global": promotion.get("R_global"),
            "reward": reward,
        },
        "provenance": {
            "run_id": run_id,
            "source_trial_dir": promotion.get("trial_dir"),
            "holdout_reject_detail": promotion.get("is_rejection_detail_override"),
            "status_at_store": promotion.get("status"),
            # Issue #817 — die AUFGESCHLÜSSELTE Rejection-Ursache (#786), Grundlage der
            # Shortfall-Marge für REJECT_HOLDOUT_GATE-Kandidaten.
            "holdout_binding_gate": metrics_symbol.get("holdout_binding_gate"),
            "holdout_gate_deltas": dict(metrics_symbol.get("holdout_gate_deltas") or {}),
        },
        "integrity": {
            "reward_semantics_version": opt_data.get("reward_semantics_version"),
            # Issue #819 — der SUCHRAUM-Fingerprint, unabhängig von der Reward-Semantik.
            "params_schema_version": _params_schema_version(strategy),
            # Issue #854 — die SIMULATIONS-Semantik (Exit-Pfad/Zeitbox/Guard-Schwelle, siehe
            # optimizer.json['simulation_semantics_version']-Schema), unabhängig von der
            # Reward-FORMEL: ändert sich die Simulation, ist der gemessene Parametervektor selbst
            # nicht mehr belegbar (anders als bei einem reinen reward_semantics_version-Mismatch,
            # der nur die Bewertungs-FORMEL betrifft, siehe champion_simulation_stale-Docstring).
            "simulation_semantics_version": opt_data.get("simulation_semantics_version"),
            "data_snapshot_sha256": catalog_fingerprint(),
            "catalog_newest_ns": catalog_newest_ns,
        },
        "lifecycle": {
            "first_seen_run": run_id,
            "first_seen_catalog_newest_ns": catalog_newest_ns,
            "corroboration_count": 1,
            # Issue #821 — der LETZTE Lauf, der diesen Eintrag geschrieben/korroboriert hat;
            # Grundlage der run_id-gegateten Korroborationszählung.
            "last_seen_run": run_id,
            "last_R_symbol": r_symbol,
            "degrade_streak": 0,
            "writeback_applied": False,
            "snapshot_rollover_count": 0,
            "semantics_migrated_from": None,
        },
    }


def store_champion(study, strategy: str, symbol: str, promotion: dict, *,
                   catalog_newest_ns: int | None, opt_data: dict, tier: str = "all",
                   run_id: str) -> Path | None:
    """Issue #703 (P0), gehärtet #817/#819/#820/#821 — persistiert den Ebene-1-Suchanker für
    (strategy, symbol).

    Aufgerufen von ``sweep.py`` UNMITTELBAR NACH ``export_symbol_proposal`` — jeder Sweep-Lauf,
    unabhängig vom Promotion-Status (Ebene 1 ist integritätsneutral: der Seed durchläuft beim
    nächsten Enqueue erneut ALLE Gates auf frischen Daten). Ändert NIEMALS ``strategies.json``
    (HI-3) — ausschliesslich ``data/optimizer/champions/*.json``. Ändert nie die aktuelle
    Promotion-Entscheidung selbst (rein additiv, kein Rückkanal in ``promotion``).

    ``run_id`` (Issue #821) ist ein PFLICHTPARAMETER — die SWEEP-run_id (``sweep.default_run_id()``
    bzw. die vom Aufrufer übergebene run_id), NICHT ein bei jedem Aufruf frisch geminteter
    Schreibzeitstempel (der frühere ``_run_id()`` ist entfallen). Nur so zählt
    ``corroboration_count`` nachweisbar LÄUFE, nicht Schreibvorgänge.

    Versions-Handling (Issue #819): ein ``reward_semantics_version``-Mismatch des gespeicherten
    Eintrags gegen die aktuelle Config verwirft ihn NICHT mehr vollständig — ``params``,
    ``corroboration_count`` und die übrige Lifecycle-Historie bleiben erhalten, NUR ``quality``
    wird neu gesetzt (``lifecycle.semantics_migrated_from`` dokumentiert die alte Version). Ein
    ``params_schema_version``-Mismatch (der SUCHRAUM selbst hat sich geändert) verwirft den
    Eintrag weiterhin vollständig — ist der neue Kandidat dann selbst unzulässig, wird der alte
    Eintrag nach ``_stale/`` quarantäniert statt still liegen zu bleiben (#821).

    Merge-Logik gegen einen bestehenden, versions-kompatiblen Eintrag (Issue #703 Punkt 3 +
    #707/#708/#820):
      - Region-gleich (#707) zum gespeicherten Champion, aber unter dem Qualitäts-Floor ⇒
        Regime-Degradation (#708): ``degrade_streak += 1``; erreicht sie
        ``champion_demote_after_runs`` ⇒ Demotion (Store- + Seed-Eintrag entfernt).
      - Region-gleich UND admissible ⇒ ``corroboration_count`` läuft-gegated erhöht (#821);
        Übernahme des besseren ``R_symbol``-Vektors (#707 „Erhalt vs. Ersetzung") — ODER, bei
        einem Katalog-Snapshot-Wechsel (#820), IMMER der neuere Kandidat (nicht vergleichbar).
      - Region-ungleich UND (besser ODER Snapshot-Wechsel) ⇒ neuer Champion; bei einem
        Snapshot-Wechsel bleibt ``corroboration_count`` erhalten (derselbe Parametervektor, nur
        ein neuerer Datensnapshot — kein frischer Lifecycle).
      - Region-ungleich UND nicht besser (gleicher Snapshot) ⇒ gespeicherter Champion bleibt
        unangetastet.

    Returns den Pfad der (ggf. aktualisierten) Store-Datei, oder ``None``, wenn der Kandidat
    dieses Laufs selbst nicht speicherwürdig ist (ein zuvor auf demselben Weg erkannter
    Degrade-/Demotion-Effekt auf einen BESTEHENDEN Eintrag bleibt davon unberührt persistiert)."""
    if not opt_data.get("champion_enabled", True):
        return None
    if not run_id:
        raise ValueError(
            "champions.store_champion: run_id ist seit #821 ein Pflichtparameter (die Sweep-"
            "run_id) — kein automatisch geminteter Schreibzeitstempel mehr."
        )

    path = _champion_path(strategy, symbol)
    existing = _read_entry(path)
    reward_version = opt_data.get("reward_semantics_version")
    params_schema_version = _params_schema_version(strategy)

    quality_stale = False
    semantics_migrated_from = None
    discarded_for_quarantine: dict | None = None
    quarantine_mismatch_kind = "params_schema_version_mismatch"
    if existing is not None:
        existing_integrity = existing.get("integrity") or {}
        existing_reward_version = existing_integrity.get("reward_semantics_version")
        existing_schema_version = existing_integrity.get("params_schema_version")
        version_mismatch = existing_reward_version != reward_version
        # Issue #819 — NUR ein params_schema_version-Mismatch (der SUCHRAUM selbst hat sich
        # geändert, z. B. ein Parameter wurde entfernt/hinzugefügt) verwirft den Eintrag
        # vollständig; fehlt eine der beiden Signaturen (Legacy-Eintrag vor #819), wird
        # konservativ NICHT auf Schema-Inkompatibilität geschlossen (kein falscher Datenverlust
        # durch ein Feld, das der Alt-Eintrag noch nicht kannte).
        schema_mismatch = (
            params_schema_version is not None
            and existing_schema_version is not None
            and existing_schema_version != params_schema_version
        )
        # Issue #854 — dieselbe VOLLSTÄNDIGE Verwerfung wie beim Suchraum-Mismatch, jetzt auch für
        # die SIMULATIONS-Semantik (siehe champion_simulation_stale-Docstring): die gemessenen
        # Metriken, aus denen params als "bewährt" hervorging, wurden unter einem anderen
        # Handelsvertrag erhoben.
        simulation_mismatch = champion_simulation_stale(existing, opt_data)
        if schema_mismatch or simulation_mismatch:
            discarded_for_quarantine = existing
            quarantine_mismatch_kind = (
                "params_schema_version_mismatch" if schema_mismatch
                else "simulation_semantics_version_mismatch")
            existing = None
        elif version_mismatch:
            semantics_migrated_from = existing_reward_version
            quality_stale = True

    candidate_params = dict(promotion.get("symbol_params") or {})
    candidate_r_symbol = promotion.get("R_symbol")

    # #708 — Degrade-Signal: eine ERNEUTE Evaluierung DERSELBEN Region unter dem Qualitäts-Floor
    # zählt als Regime-Degradation, UNABHÄNGIG davon, ob der neue Kandidat selbst admissible ist
    # (ein unter-dem-Floor-liegender Kandidat ist gerade das Degrade-Signal, das erkannt werden
    # soll). NUR wenn NICHT quality_stale — über eine reward_semantics_version-Bump-Grenze hinweg
    # ist R_symbol nicht vergleichbar (#819), ein Degrade-Urteil darauf wäre eine erfundene
    # Aussage.
    if existing is not None and not quality_stale and candidate_params and candidate_r_symbol is not None:
        if _same_region(strategy, candidate_params, existing.get("params") or {}, opt_data):
            min_r = float(opt_data.get("champion_min_R_symbol", 0.0))
            lifecycle = existing.setdefault("lifecycle", {})
            if candidate_r_symbol < min_r:
                lifecycle["degrade_streak"] = int(lifecycle.get("degrade_streak", 0) or 0) + 1
            else:
                lifecycle["degrade_streak"] = 0
            demote_after = int(opt_data.get("champion_demote_after_runs", 2))
            if lifecycle["degrade_streak"] >= demote_after:
                _demote_champion(strategy, symbol, path, reason="degrade_streak")
                existing = None
            else:
                _write_entry(path, existing)

    candidate_entry = _build_entry_from_promotion(
        study, strategy, symbol, promotion,
        catalog_newest_ns=catalog_newest_ns, opt_data=opt_data, tier=tier, run_id=run_id,
    )
    ok, reason = champion_is_admissible(candidate_entry, opt_data)
    if not ok:
        # Issue #821 — der Kandidat selbst ist nicht speicherwürdig. War der VORHANDENE Eintrag
        # versionsfremd (params_schema_version-Mismatch, oben verworfen), bleibt er sonst still
        # auf der Platte liegen — stattdessen Quarantäne statt stiller Persistenz.
        if discarded_for_quarantine is not None:
            _quarantine_champion(strategy, symbol, path, discarded_for_quarantine,
                                 reason=f"{quarantine_mismatch_kind}_then_{reason}")
        return None  # ein Degrade-Update oben (falls quality_stale=False) bleibt bestehen.

    if existing is None:
        merged = candidate_entry
    elif quality_stale:
        # Issue #819 — params_schema kompatibel, aber reward_semantics_version hat gebumpt: die
        # Historie (corroboration_count, first_seen_*) bleibt erhalten, params werden vom NEUEN
        # (unter der aktuellen Semantik neu bewerteten) Kandidaten übernommen — quality ist damit
        # frisch und vergleichbar, degrade_streak resettet (über die Bump-Grenze nicht mehr
        # aussagekräftig).
        merged = candidate_entry
        prior_lifecycle = existing.get("lifecycle") or {}
        new_count, last_seen = _bump_corroboration(prior_lifecycle, run_id)
        merged["lifecycle"]["corroboration_count"] = new_count
        merged["lifecycle"]["last_seen_run"] = last_seen
        merged["lifecycle"]["first_seen_run"] = prior_lifecycle.get("first_seen_run", run_id)
        merged["lifecycle"]["first_seen_catalog_newest_ns"] = prior_lifecycle.get(
            "first_seen_catalog_newest_ns", catalog_newest_ns)
        merged["lifecycle"]["degrade_streak"] = 0
        merged["lifecycle"]["semantics_migrated_from"] = semantics_migrated_from
        merged["lifecycle"]["snapshot_rollover_count"] = int(
            prior_lifecycle.get("snapshot_rollover_count", 0) or 0)
        merged["lifecycle"]["writeback_applied"] = False  # quality neu -> re-validieren
    else:
        region_equal = _same_region(strategy, candidate_entry["params"],
                                    existing.get("params") or {}, opt_data)
        existing_integrity = existing.get("integrity") or {}
        existing_snapshot = existing_integrity.get("data_snapshot_sha256")
        candidate_snapshot = candidate_entry["integrity"]["data_snapshot_sha256"]
        # Issue #820 — auf verschiedenen Katalog-Snapshots gemessene R_symbol-Werte sind NICHT
        # vergleichbar (der Amtsinhaber wurde auf einem anderen Datenfenster gemessen); der
        # NEUERE Kandidat (aktuellere Daten) gewinnt dann IMMER, unabhängig von seinem R_symbol.
        snapshot_rollover = bool(
            existing_snapshot and candidate_snapshot and existing_snapshot != candidate_snapshot)
        existing_r = (existing.get("quality") or {}).get("R_symbol")
        better = True if snapshot_rollover else (existing_r is None or candidate_r_symbol > existing_r)
        prior_lifecycle = existing.get("lifecycle") or {}
        if region_equal:
            merged = candidate_entry if better else existing
            if better:
                merged["lifecycle"]["writeback_applied"] = False  # Params geändert -> re-validieren
            merged.setdefault("lifecycle", {})
            new_count, last_seen = _bump_corroboration(prior_lifecycle, run_id)
            merged["lifecycle"]["corroboration_count"] = new_count
            merged["lifecycle"]["last_seen_run"] = last_seen
            merged["lifecycle"]["first_seen_run"] = prior_lifecycle.get(
                "first_seen_run", merged["lifecycle"].get("first_seen_run"))
            merged["lifecycle"]["first_seen_catalog_newest_ns"] = prior_lifecycle.get(
                "first_seen_catalog_newest_ns", merged["lifecycle"].get("first_seen_catalog_newest_ns"))
            merged["lifecycle"]["degrade_streak"] = prior_lifecycle.get("degrade_streak", 0)
            merged["lifecycle"]["last_R_symbol"] = candidate_r_symbol
            merged["lifecycle"]["snapshot_rollover_count"] = int(
                prior_lifecycle.get("snapshot_rollover_count", 0) or 0) + (1 if snapshot_rollover else 0)
            merged.setdefault("integrity", {})
            merged["integrity"]["catalog_newest_ns"] = catalog_newest_ns
            merged["integrity"]["data_snapshot_sha256"] = candidate_entry["integrity"]["data_snapshot_sha256"]
        elif better:
            merged = candidate_entry  # neue, bessere Region (oder Snapshot-Wechsel) -- s.u.
            if snapshot_rollover:
                # Issue #820 Punkt 3b — ein Snapshot-Wechsel ist KEIN frischer Lifecycle: derselbe
                # Parametervektor-Gewinner, nur ein neuerer Datensnapshot. Die bisherige
                # Korroboration bleibt gültig (NICHT über _bump_corroboration erhöht — der
                # Snapshot-Wechsel selbst ist kein zusätzlicher Korroborations-Lauf, er ERSETZT
                # nur die unvergleichbare Bewertung).
                merged["lifecycle"]["corroboration_count"] = int(
                    prior_lifecycle.get("corroboration_count", 1) or 1)
                merged["lifecycle"]["last_seen_run"] = run_id
                merged["lifecycle"]["first_seen_run"] = prior_lifecycle.get("first_seen_run", run_id)
                merged["lifecycle"]["first_seen_catalog_newest_ns"] = prior_lifecycle.get(
                    "first_seen_catalog_newest_ns", catalog_newest_ns)
                merged["lifecycle"]["snapshot_rollover_count"] = int(
                    prior_lifecycle.get("snapshot_rollover_count", 0) or 0) + 1
        else:
            merged = existing  # schlechter, gleicher Snapshot -- gespeicherten Champion unangetastet lassen

    _write_entry(path, merged)
    return path


def _demote_champion(strategy: str, symbol: str, path: Path, *, reason: str) -> None:
    """Issue #708 (P2) — Regime-Degradation: Store-Eintrag UND ein etwaiger
    ``strategy_symbol_seeds.json``-Eintrag werden entfernt; die Seed-Auflösung fällt auf
    ``strategy_defaults`` zurück (kein stale Optimum ankert die Suche dauerhaft am falschen Punkt)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    _remove_symbol_seed(strategy, symbol)
    emit_execution_event(logging.getLogger("optimizer"), "CHAMPION_DEMOTED", {
        "strategy": strategy, "symbol": symbol, "champion_demoted_reason": reason,
    })
    logging.getLogger("optimizer").warning(
        "[#708] %s/%s: Champion demoted (%s) — Store- + Seed-Eintrag entfernt, Fallback auf "
        "strategy_defaults.", strategy, symbol, reason,
    )


def load_champion_entry(strategy: str, symbol: str, *, opt_data: dict) -> dict | None:
    """Issue #704 (P0) — liest + validiert (``champion_is_admissible``) den gespeicherten
    Champion. Gibt den VOLLEN Eintrag zurück (für #709-Study-Attr-Telemetrie);
    ``load_champion_seed`` extrahiert daraus nur ``params`` (Issue-Signatur, s. u.)."""
    entry, _reason, _provenance = load_champion_entry_with_reason(strategy, symbol, opt_data=opt_data)
    return entry


def _champion_store_has_any_entry() -> bool:
    """Issue #1084 Fix Punkt 2 (Katalog #866-2, Kohorte E, Root-Cause c) — True, wenn WENIGSTENS
    EIN Champion-Store-Eintrag existiert, für IRGENDEIN (Strategie, Symbol)-Paar. ``_champion_path``
    ist PAAR-skopiert (eine Datei je Paar) — es gibt keinen einzigen kombinierten "Store", dessen
    Leerheit eine einzelne Existenzprüfung beantworten könnte. ``load_champion_entry_with_reason``
    nannte ``entry is None`` vorher UNBEDINGT ``'STORE_EMPTY'``, obwohl das für ein Paar ohne
    eigenen Eintrag in einem sonst gefüllten Store (12 von 14 Paaren im #866-2-Referenzlauf) eine
    Fehlbenennung ist — der Name suggeriert einen leeren Store, obwohl 2 andere Paare durchaus
    Einträge tragen. Fail-open (``False``) bei einem Lesefehler — dieselbe Konvention wie überall
    im Champion-Store."""
    try:
        return next(_champions_dir().glob("champion_*.json"), None) is not None
    except OSError:
        return False


_NO_ENTRY_PROVENANCE_MAX_KEYS = 10


def _no_entry_provenance(strategy: str, symbol: str) -> dict:
    """Issue #1103 (Katalog #936) — Rohmaterial für ``load_champion_entry_with_reason``s
    ``NO_ENTRY_FOR_PAIR``/``STORE_EMPTY``-Provenienz: WELCHER Store-Schlüssel wurde gesucht
    (``looked_up_key``, der Dateiname aus ``_champion_path``) und WELCHE Schlüssel existieren
    tatsächlich (``available_keys``, alphabetisch, auf ``_NO_ENTRY_PROVENANCE_MAX_KEYS`` gekürzt —
    ``available_keys_total`` trägt die volle Zahl, damit die Kürzung selbst sichtbar bleibt).

    Root-Cause #936: 51 von 56 Studies im NVDA-Referenzlauf tragen ``NO_ENTRY_FOR_PAIR``, obwohl
    ``champions.stored`` > 0 meldet — der Verdacht auf einen Schlüssel-Mismatch (Paar-skopierter
    Lookup gegen einen abweichend benannten/global gemeinten Store-Eintrag) ist seit #1084 offen
    und unbewiesen. Diese Provenienz macht den Verdacht das erste Mal NACHPRÜFBAR, statt eine
    Vermutung zu bleiben. Fail-open (leere ``available_keys``) bei einem Lesefehler — dieselbe
    Konvention wie überall im Champion-Store."""
    looked_up_key = _champion_path(strategy, symbol).name
    try:
        available = sorted(p.name for p in _champions_dir().glob("champion_*.json"))
    except OSError:
        available = []
    return {
        "looked_up_key": looked_up_key,
        "available_keys": available[:_NO_ENTRY_PROVENANCE_MAX_KEYS],
        "available_keys_total": len(available),
    }


def load_champion_entry_with_reason(strategy: str, symbol: str, *,
                                    opt_data: dict) -> tuple[dict | None, str | None, dict | None]:
    """Issue #910 Fix 2 — wie ``load_champion_entry``, aber unterscheidet die beiden Ursachen, die
    vorher beide als ``None`` (und stromabwärts als derselbe ``NO_ADMISSIBLE_ENTRY``-String)
    kollabierten: ein Eintrag EXISTIERT, ist aber inadmissibel (``champion_is_admissible``s
    granularer Reason-Code, z. B. ``'EMPTY_PARAMS'``/``'REJECT_HOLDOUT_GATE'``/...) vs. KEIN
    Eintrag existiert für dieses Paar. Der Report konnte vorher nicht sagen, ob die Kette nie
    startet oder ständig scheitert.

    Issue #1084 Fix Punkt 2 — der zweite Fall (kein Eintrag) trägt seither ZWEI unterscheidbare
    Reason-Codes statt des einen, pauschalen ``'STORE_EMPTY'``: ``'STORE_EMPTY'`` NUR, wenn der
    Store insgesamt leer ist (``_champion_store_has_any_entry`` False — die Kette hat noch nie
    für IRGENDEIN Paar geschrieben); ``'NO_ENTRY_FOR_PAIR'``, wenn der Store Einträge trägt, nur
    keinen für DIESES Paar (der pair-skopierte Normalfall, solange nicht jedes Paar bereits einen
    admissiblen Kandidaten hervorgebracht hat, siehe ``store_champion``-Docstring).

    Issue #1103 (Katalog #936) — DRITTES Rückgabeelement ``provenance``: ``{looked_up_key,
    available_keys, available_keys_total}`` (siehe ``_no_entry_provenance``), NUR gesetzt für
    ``STORE_EMPTY``/``NO_ENTRY_FOR_PAIR`` (die beiden Fälle, in denen "welcher Schlüssel wurde
    gesucht, welche existieren" die diagnostisch wertvolle Frage ist) — ``None`` in jedem anderen
    Fall (ein existierender, aber inadmissibler Eintrag, oder ein admissibler Treffer)."""
    entry = _read_entry(_champion_path(strategy, symbol))
    if entry is None:
        reason = "STORE_EMPTY" if not _champion_store_has_any_entry() else "NO_ENTRY_FOR_PAIR"
        return None, reason, _no_entry_provenance(strategy, symbol)
    ok, reason = champion_is_admissible(entry, opt_data)
    return (entry, None, None) if ok else (None, reason or "INADMISSIBLE", None)


def load_champion_seed(strategy: str, symbol: str, base_cfg: Path | None = None, *,
                       opt_data: dict, catalog_newest_ns: int | None = None) -> dict:
    """Issue #704 (P0) — Champion-Seed Reader für ``run_optimization.resolve_symbol_shrinkage_seed``.

    ``base_cfg``/``catalog_newest_ns`` werden für Signatur-Parität zu ``load_global_best``/
    ``load_strategy_defaults_params`` akzeptiert, aber intern nicht gebraucht: der Champion-Store
    liegt unter ``WORK`` (nicht config-relativ), und der ENQUEUE-Pfad braucht KEIN Fenster-Gate —
    der Seed wird auf den AKTUELLEN Daten voll neu durch alle Gates re-evaluiert. Das Fenster-Gate
    (``catalog_newest_ns`` vs. Erst-Sichtung) betrifft ausschliesslich den Writeback (#706,
    ``maybe_write_back``), nicht das Enqueue. Issue #819 — liefert die ``params`` AUCH für einen
    ``quality_stale`` Eintrag (ein reiner reward_semantics_version-Mismatch schliesst das Enqueue
    nicht mehr aus, siehe ``champion_is_admissible``)."""
    entry = load_champion_entry(strategy, symbol, opt_data=opt_data)
    return dict(entry.get("params") or {}) if entry else {}


def _seeds_path(base_cfg: Path | None = None) -> Path:
    from automation.optimizer.trial_config import config_dir
    return (base_cfg or config_dir()) / "strategy_symbol_seeds.json"


def _read_seeds_file(base_cfg: Path | None = None) -> dict:
    path = _seeds_path(base_cfg)
    if not path.exists():
        return {"seeds": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("seeds", {})
    return data


def _write_symbol_seed(strategy: str, symbol: str, params: dict, *,
                       base_cfg: Path | None = None) -> None:
    """Issue #706 (P1) — schreibt ``params`` nach ``strategy_symbol_seeds.json[seeds][strategy]
    [symbol]``. NIE ``strategy_defaults.json`` (der globale Cross-Symbol-Prior) — Symbol-Seeds
    sind strikt symbol-skopiert (siehe Docstring ``maybe_write_back``)."""
    path = _seeds_path(base_cfg)
    data = _read_seeds_file(base_cfg)
    data["seeds"].setdefault(strategy, {})[symbol] = dict(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _remove_symbol_seed(strategy: str, symbol: str, *, base_cfg: Path | None = None) -> None:
    path = _seeds_path(base_cfg)
    if not path.exists():
        return
    data = _read_seeds_file(base_cfg)
    strat_seeds = data.get("seeds", {}).get(strategy)
    if strat_seeds and symbol in strat_seeds:
        del strat_seeds[symbol]
        if not strat_seeds:
            del data["seeds"][strategy]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _default_min_advance_days(base_cfg: Path | None) -> float:
    """Issue #706 — Default für ``champion_min_advance_days``, wenn der Key in ``optimizer.json``
    fehlt: ``backtest.json``'s ``walk_forward.oos_window_days`` (Zero-Hardcoding, dieselbe
    Fallback-Konvention wie überall im Optimizer — fehlt der Key, ein dokumentiertes,
    config-abgeleitetes Verhalten statt einer erfundenen Konstante). Issue #819 — der Key steht
    seit dem Fix explizit (nicht mehr ``null``) in ``optimizer.json``; dieser Fallback bleibt nur
    für Legacy-/Test-Configs ohne den Key erhalten."""
    from automation.optimizer.trial_config import config_dir
    cfg_dir = base_cfg or config_dir()
    bt_path = cfg_dir / "backtest.json"
    if bt_path.exists():
        try:
            with open(bt_path, "r", encoding="utf-8") as f:
                wf = (json.load(f) or {}).get("walk_forward", {})
            return float(wf.get("oos_window_days", 0) or 0)
        except (OSError, ValueError):
            pass
    return 0.0


def maybe_write_back(entry: dict, opt_data: dict, *, base_cfg: Path | None = None) -> bool:
    """Issue #706 (P1), gehärtet #819 — Default-Nachführung: schreibt ``entry['params']`` nach
    ``strategy_symbol_seeds.json``, wenn ALLE Bedingungen erfüllt sind:
      1. Issue #819 — ``champion_quality_stale`` ist ``False``: die ``quality``-Bewertung
         (R_symbol/R_global) muss unter der AKTUELLEN ``reward_semantics_version`` gemessen sein
         — ein Writeback auf Basis einer unter einer alten Semantik gemessenen Qualität würde
         nicht mehr vergleichbare Zahlen deployen (der Parametervektor selbst darf trotzdem als
         SEED weiterleben, siehe ``load_champion_seed`` — das ist NICHT dasselbe wie ein
         Writeback).
      2. Fensterfortschritt: ``catalog_newest_ns`` liegt um >= ``champion_min_advance_days``
         (Default: ``backtest.json.walk_forward.oos_window_days``) über
         ``first_seen_catalog_newest_ns`` — Snooping-Schutz (sonst würden Parameter deployt, die
         nur auf identischen Daten „bestätigt" wurden). ENTFÄLLT bei einer echten
         READY_FOR_PR-Promotion (Punkt 3b) — die ist bereits vollständig auf dem Holdout validiert.
      3. Korroboration: ``corroboration_count >= champion_promote_after_runs`` (Default 2)
         ODER ``status_at_store == 'READY_FOR_PR'`` (sofortiger Writeback bei echter Promotion).

    Setzt ``lifecycle.writeback_applied = True`` und persistiert den Champion-Store-Eintrag
    zurück, wenn geschrieben wurde. HI-3 bleibt gewahrt: ``strategy_symbol_seeds.json`` ist ein
    Seed/Prior für den Resolver (``resolve.resolve_params``), KEIN Live-Deployment —
    ``strategies.json`` ändert weiterhin ausschliesslich ein menschlich freigegebener PR.

    **Warum niemals nach ``strategy_defaults.json``:** das ist der globale Cross-Symbol-Prior; ein
    symbol-getunter Vektor kann global toxisch sein (R_symbol positiv ↔ R_global negativ ist ein
    realer Fall, siehe Issue #705 §2), Symbol-Seeds gehören strikt symbol-skopiert."""
    if not opt_data.get("champion_enabled", True):
        return False
    if champion_quality_stale(entry, opt_data):
        return False
    params = entry.get("params") or {}
    if not params:
        return False

    provenance = entry.get("provenance") or {}
    is_ready_for_pr = provenance.get("status_at_store") == "READY_FOR_PR"

    lifecycle = entry.get("lifecycle") or {}
    corroboration_count = int(lifecycle.get("corroboration_count", 0) or 0)
    promote_after = int(opt_data.get("champion_promote_after_runs", 2))
    corroborated = corroboration_count >= promote_after

    if not (is_ready_for_pr or corroborated):
        return False

    if not is_ready_for_pr:
        # Issue #910 Fix 1 — der Korroborationsbegriff wird von advance_days ENTKOPPELT.
        # champion_corroboration_mode ∈ {'window_advance', 'independent_search', 'either'}
        # (Default 'either'):
        #   'window_advance'      — bit-identisch zum Pre-#910-Verhalten: corroborated UND das
        #                            Datenfenster ist um min_advance_days vorgerückt (Snooping-
        #                            Schutz gegen zwei Läufe auf identischen Daten).
        #   'independent_search'  — corroboration_count (verschiedene run_id, #821) allein genügt:
        #                            zwei unabhängige Läufe DERSELBEN Datenbasis korroborieren einen
        #                            Parametervektor sehr wohl, wenn sie ihn unabhängig finden
        #                            (verschiedene Sampler-Seeds) — advance_days=0.0 ist unter
        #                            diesem Modus kein Blocker mehr.
        #   'either'              — promotet, sobald EINE der beiden Routen erfüllt ist.
        # Root-Cause #910: VOR diesem Fix war 'window_advance' die EINZIGE Route — zwei Läufe
        # am selben Tag konnten die Kette per Konstruktion nie korroborieren, unabhängig davon,
        # wie stabil der Kandidat war (14/14 Writebacks NO_ADMISSIBLE_ENTRY im ea4c409d-Lauf).
        mode = opt_data.get("champion_corroboration_mode", "either")
        if mode not in ("window_advance", "independent_search", "either"):
            raise ValueError(
                f"optimizer.json['champion_corroboration_mode']={mode!r} unbekannt — erwartet "
                "'window_advance', 'independent_search' oder 'either'."
            )
        if mode == "independent_search":
            pass  # corroborated (bereits oben geprüft) genügt — kein Fenster-Gate.
        else:
            # Snooping-Schutz: gilt für 'window_advance' unbedingt, für 'either' als EINE der
            # beiden möglichen Routen.
            integrity = entry.get("integrity") or {}
            current_ns = integrity.get("catalog_newest_ns")
            first_ns = lifecycle.get("first_seen_catalog_newest_ns")
            min_advance_days = opt_data.get("champion_min_advance_days")
            if min_advance_days is None:
                min_advance_days = _default_min_advance_days(base_cfg)
            advance_days = (
                (current_ns - first_ns) / 1_000_000_000.0 / 86400.0
                if current_ns is not None and first_ns is not None else None
            )
            window_advanced = advance_days is not None and advance_days >= float(min_advance_days or 0)
            if mode == "window_advance" and not window_advanced:
                return False
            # mode == 'either': corroborated ist bereits erfüllt (oben geprüft) — die
            # independent_search-Route deckt den window_advanced=False-Fall ab, kein return False.

    strategy = entry.get("strategy")
    symbol = entry.get("symbol")
    _write_symbol_seed(strategy, symbol, params, base_cfg=base_cfg)
    entry.setdefault("lifecycle", {})["writeback_applied"] = True
    _write_entry(_champion_path(strategy, symbol), entry)
    return True
