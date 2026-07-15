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
