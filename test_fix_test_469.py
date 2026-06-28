import sys
import unittest.mock as mock

sys.modules["optuna"] = mock.MagicMock()
sys.modules["sqlalchemy"] = mock.MagicMock()
sys.modules["sqlalchemy.exc"] = mock.MagicMock()

try:
    import automation.optimizer.run_optimization as ro
    print("Successfully imported ro")
except Exception as e:
    print(f"Error importing ro: {e}")
