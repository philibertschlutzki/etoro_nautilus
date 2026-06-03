import sys
import unittest
from unittest.mock import patch

class TestLiveExecutionDefaults(unittest.TestCase):
    @patch('os.getenv')
    def test_default_execution_parameters_are_safe(self, mock_getenv):
        """Ensures that missing .env parameters strictly default to safe demo mode."""
        # Setup mock to simulate missing env vars
        mock_getenv.side_effect = lambda key, default=None: default

        if 'automation.momentum_ls_run' in sys.modules:
            del sys.modules['automation.momentum_ls_run']

        from automation.momentum_ls_run import ETORO_EXECUTION

        self.assertEqual(ETORO_EXECUTION["environment"], "demo")
        self.assertTrue(ETORO_EXECUTION["dry_run"])
        self.assertFalse(ETORO_EXECUTION["enable_trailing_stop"])

if __name__ == '__main__':
    unittest.main()
