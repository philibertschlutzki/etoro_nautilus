import re

with open("automation/daily_orchestrator.py", "r", encoding="utf-8") as f:
    content = f.read()

# Remove safety interlocks
content = re.sub(r'def _ensure_safety_interlocks\(log: logging\.Logger\) -> None:.*?# ═══════════════════════════════════════════════════════════════════════════════', '# ═══════════════════════════════════════════════════════════════════════════════', content, flags=re.DOTALL)
content = content.replace("    _ensure_safety_interlocks(log)\n", "")

# Change Phase 3 path
content = content.replace('str(PROJECT_ROOT / "backtesting" / "run_backtest.py")', 'str(_THIS_DIR / "backtest_runner.py")')

# Inject Universe Fetcher in Phase 1
universe_fetch_code = """
    # Stale-Check: universe_fetcher als Subprocess wenn > 24h alt
    try:
        from automation.universe_fetcher import is_universe_stale
        if is_universe_stale(UNIVERSE_PATH, max_age_hours=24.0):
            log.info("[Phase 1] Universe ist veraltet — starte universe_fetcher ...")
            cmd = [sys.executable, str(_THIS_DIR / "universe_fetcher.py"),
                   "--output", str(UNIVERSE_PATH)]
            proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=60, capture_output=True, text=True)
            for line in (proc.stdout or "").splitlines():
                log.info(f"  [SUBPROCESS] {line}")
            for line in (proc.stderr or "").splitlines():
                log.error(f"  [SUBPROCESS] {line}")
            if proc.returncode != 0:
                log.error("[Phase 1] universe_fetcher fehlgeschlagen.")
    except Exception as e:
        log.error(f"Error checking universe staleness: {e}")
"""

content = content.replace('log.info("[Phase 1] Lese Instrumenten-Map...")', universe_fetch_code + '\n    log.info("[Phase 1] Lese Instrumenten-Map...")')

with open("automation/daily_orchestrator.py", "w", encoding="utf-8") as f:
    f.write(content)
