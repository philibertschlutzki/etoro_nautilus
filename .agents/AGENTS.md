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
4. **Keine Reward-Bänder mehr:** (Issue #629) Die Feasibility wird **ausschließlich** über den `#612`-Constraint von Optuna getragen. Die Reward-Gleichung ist ein einziges, stetiges, nicht-gesättigtes Qualitätsziel über ALLE Trials (feasible wie infeasible). `evaluable_reward_floor`, `failure_ceiling`, `unevaluable_ceiling` und das Branching im `compute_reward`-Pfad existieren nicht mehr.
5. **Varianzbeitrag & Aggregation**: In `_emit_study_summary` werden diese Metriken am Ende einer Study gesammelt und als Event `optimizer_study_completed` persistiert. Dazu gehören auch `divergence_at_cap` sowie `floor_clamped`. Diese Metriken müssen ohne Rekonstruktion aus dem Log direkt ablesbar sein.
6. **Keine verdeckten Konstanten**: Alle Bestandteile des Reward-Signals müssen exakt und vollständig im Terms-Dictionary auftauchen. Es darf keinen mathematischen Faktor geben, der nicht explizit isoliert über Telemetrie aufgezeichnet wird.
