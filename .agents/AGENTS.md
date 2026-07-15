# Agent Guidelines for etoro_nautilus

Dieses Dokument enthält projektweite Guidelines und Architektur-Constraints. **Agenten MÜSSEN diese Regeln bei jeder Code-Generierung und Analyse befolgen.**

## Reward-Term-Dekomposition (Issue #621)

Die Reward-Metriken im Optimierer (Optuna) werden nach Issue #621 systematisch nach Termen dekomponiert, um die "Black Box" des TPE-Samplers aufzubrechen und absolute Transparenz in die Telemetrie zu bringen. Jeder Agent, der an `compute_reward` oder der Optimierer-Struktur arbeitet, MUSS sicherstellen:

1. **Dekompositions-Pflicht**: Jede Modifikation der Reward-Gleichung muss zwingend in das `return_terms=True` Dictionary-Format einfließen. Der Rückgabewert von `compute_reward` bei `return_terms=True` ist ein Tupel `(reward, dictionary)`.
2. **Branch Tracking**: Der `branch`-Key im Dictionary (`"eligible"`, `"unevaluable"`, `"failure"`, `"per_symbol"`, `"pareto"`) definiert den exakten Evaluierungszweig der Strategie und ist strikt beizubehalten. Es dürfen keine "silenced branches" entstehen.
3. **Inertness Guard (`REWARD_TERM_INERT`)**: Wir überwachen kontinuierlich die Standardabweichung (`std`) jedes mathematischen Terms (`base`, `divergence`, `dd_penalty`, `param_pen`, `turnover`, `fold_dispersion`, `tie_breaker`) im Verhältnis zur gesamten `std(reward)`. 
   - Fällt die Varianz eines Terms unter `0.01 * std(reward)`, erzeugt das System ein automatisches Warning (`REWARD_TERM_INERT: <name>`).
   - **CRITICAL**: Agenten DÜRFEN diesen Mechanismus NIEMALS stummschalten oder auskommentieren.
   - Tritt dieses Warning auf, bedeutet das, dass der Term wirkungslos (Inert) ist. Agenten müssen in diesem Fall die Normierungsskalen (`penalty_relative_cap`, `sortino_soft_scale` etc.) überprüfen, anstatt den Check oder das Logging zu entfernen.
4. **Varianzbeitrag & Aggregation**: In `_emit_study_summary` werden diese Metriken am Ende einer Study gesammelt und als Event `optimizer_study_completed` persistiert. Dazu gehören auch `divergence_at_cap` sowie `floor_clamped`. Diese Metriken müssen ohne Rekonstruktion aus dem Log direkt ablesbar sein.
5. **Keine verdeckten Konstanten**: Alle Bestandteile des Reward-Signals müssen exakt und vollständig im Terms-Dictionary auftauchen. Es darf keinen mathematischen Faktor geben, der nicht explizit isoliert über Telemetrie aufgezeichnet wird.

## Trennung von PSR (Gate) und PSR_Z (Reward-Base) (Issue #630)

Die Probabilistic Sharpe/Sortino Ratio (PSR) ist skalenfrei, in [0, 1] beschränkt und annualisierungs-invariant.
Die PSR setzt sich mathematisch aus der kumulativen Verteilungsfunktion (CDF, Φ) angewandt auf eine standardisierte Effektstärke `z` zusammen.
- **`oos_psr` (CDF)**: Dient AUSSCHLIESSLICH als statistisches Signifikanz-Gate (ist der Edge signifikant > 0?).
- **`oos_psr_z` (Effektstärke)**: Dient AUSSCHLIESSLICH als **Ranking-Base** im Reward-Signal (unbeschränkt, sättigt nicht an der 1.0-Decke).

**CRITICAL**: Agenten DÜRFEN NIEMALS die CDF (`oos_psr`) als Reward-Base zurück in `compute_reward` einführen. Das presst die Base an die 1,0-Decke und zerstört den Ranking-Gradienten des Optimierers. Gate- und Ranking-Signale müssen auf diesen unterschiedlichen Skalen strikt getrennt bleiben.

## Feasibility ausschliesslich als Sampler-Constraint (Issue #629) & Straf-Term-Kalibrierung (Issue #631)

Nach Issue #629 ist Feasibility (eligible vs. ineligible) **ausschliesslich** über `constraints_func`
(`_oos_constraints_func`, Optuna 4.9 nativ) geregelt. Es gibt **kein** separates Reward-Band mehr
(`evaluable_reward_floor`/`failure_ceiling`/`unevaluable_ceiling` sind entfallen).

1. **Kein Reward-Floor/-Ceiling-Comeback**: Agenten DÜRFEN NIEMALS eine Feasibility-Grenze wieder als
   Reward-Klippe (Band-Clamp, `max(reward, floor)`) implementieren — auch nicht "nur als zusätzliche
   Sicherung". Jeder evaluierte Trial (eligible ODER evaluated-aber-ineligible) durchläuft denselben
   stetigen Qualitäts-Kern in `compute_reward`; die einzige Zusatzstrafe für ineligible Trials ist die
   kontinuierliche `gate_distance_penalty` (additiv, kein Floor).
2. **Jeder neue additive Strafterm MUSS mit `_penalty_scale_vs_base(weights)` multipliziert werden**
   (Issue #631). Die Base ist seit #614/#630 `psr_z` mit einer sehr engen realisierten Streuung
   (σ ≈ 0,05–0,11) — ein unskalierter neuer Strafterm dominiert das Ranking strukturell, wie es
   `dd_penalty`/`turnover`/`fold_dispersion`/`param_pen` vor #631 taten. Nach dem Hinzufügen eines
   neuen Strafterms MUSS `assert_penalty_scale_calibrated` weiterhin grün bleiben (fail-loud beim
   Config-Load, `PENALTY_SCALE_MISCALIBRATED` sonst).
3. **Jede Reward-relevante Änderung bumpt `reward_semantics_version`** (`optimizer.json`) — auch wenn
   sie klein erscheint oder Teil einer bereits gemergten Migration nachzieht (Issue #637).

## Geteilte Gate-Distanz-Normierung: Reward-Pfad und Sampler-Constraint (Issue #635)

`reward._normalized_gate_distances(m, weights, risk_dd_cap, tournament_cfg)` ist die **einzige**
Quelle für OOS-Gate-Distanzen — genutzt sowohl von `_constraint_distance_penalty` (Reward-Near-Miss-
Shaping) als auch von `run_optimization._compute_oos_constraints` (#612-Sampler-Constraint).

**CRITICAL**: Agenten DÜRFEN NIEMALS rohe, un-normierte Gate-Deltas (`actual − threshold`) direkt
summieren oder vergleichen — kleinskalige Gates (z. B. `excess_return ~[0; 0,04]`) werden von
grossskaligen (z. B. `PSR ~[0; 0,75]`) sonst um Grössenordnungen dominiert (#635-Root-Cause). Ein
neues Gate braucht einen Eintrag in `_normalized_gate_distances` mit einer dokumentierten Scale
(`*_penalty_scale`-Key oder Target-Normierung via `_shortfall_distance`/`_excess_distance`).

## DSR-Wert vs. DSR-Drop-Effekt entkoppelt (Issue #636)

In `confirm.confirm_per_symbol_promotion` wird die Deflated-Sharpe-Ratio (`deflation_dsr`,
`deflation_dsr_z`) **immer** berechnet, sobald `deflation_n ≥ 2` und ein definierter promoteter
per-Perioden-Sortino vorliegen — **unabhängig** davon, ob `holdout_passed` durch ein früheres Gate
bereits `False` ist. Nur der **Drop-Effekt** (`holdout_passed = False` bei `DSR < deflation_confidence`)
bleibt an `holdout_passed` gekoppelt. Agenten DÜRFEN diese Entkopplung NICHT rückgängig machen (z. B.
durch `if holdout_passed and deflation_n >= 2:` vor dem Berechnungsblock) — sonst bleibt die
DSR-Telemetrie in jedem Proposal `None`, sobald irgendein früheres Gate zuerst greift (der
ursprüngliche #636-Defekt). Unterhalb `deflation_min_cohort` (Default 10) ersetzt der dokumentierte
`deflation_var_floor` (0,0018) die 2-3-Punkte-Stichproben-Varianz — niemals eine kleinere, zufällige
Roh-Varianz verwenden.
