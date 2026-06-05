import re
with open('automation/tests/test_backtest_runner.py', 'r') as f:
    content = f.read()

new_test = """
import collections
from unittest.mock import MagicMock, patch, mock_open

def test_run_single_backtest_worker_logging_warning_within_tolerance():
    Tick = collections.namedtuple("Tick", ["ts_event"])
    start_ts = collections.namedtuple("TS", ["value"])(value=0)
    end_ts = collections.namedtuple("TS", ["value"])(value=int(149.8 * 86400 * 1_000_000_000))
    ticks = [Tick(ts_event=start_ts), Tick(ts_event=end_ts)]

    strat = {"_walk_forward_days": 150}

    with patch("automation.backtest_runner.load_ticks_from_catalog", return_value=ticks), \\
         patch("automation.backtest_runner.pd.Timestamp", return_value=MagicMock()), \\
         patch("automation.backtest_runner._empty_result", return_value={}), \\
         patch("builtins.open", mock_open()) as m_open:

        # We will catch the Exception raised by the next step to stop the function early
        try:
            from automation.backtest_runner import run_single_backtest_worker
            run_single_backtest_worker(
                "MOCK.SYM", "1H", strat, "path", None, None, 10000.0, False, "dir", "log.txt", 1.0
            )
        except Exception:
            pass

        # Check that wlog was called with the correct warning message
        written_lines = [call.args[0] for call in m_open().write.call_args_list]
        assert any("Knappe Datenspanne, fahre fort" in line for line in written_lines)
"""

with open('automation/tests/test_backtest_runner.py', 'a') as f:
    f.write(new_test)
