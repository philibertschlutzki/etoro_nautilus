for strat in HourlyMeanReversionStrategy SmaCrossoverStrategy ComboTrendVwapStrategy FlashCrashReversalStrategy VolatilityBreakoutPumpStrategy VwapExhaustionStrategy; do
    echo "================================================="
    echo "🚀 Starte Optimierung für $strat"
    echo "================================================="
    
    # Passe hier n-trials (Anzahl Durchläufe) und n-jobs (parallele Threads) an deine Hardware an
    python3 -m automation.optimizer.run_optimization \
        --strategy "$strat" \
        --n-trials 100 \
        --n-jobs 4
done