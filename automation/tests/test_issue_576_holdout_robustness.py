import pytest
import optuna
import json
from unittest.mock import MagicMock, patch
from automation.optimizer.confirm import confirm_per_symbol_promotion

def test_deflated_holdout_gate_rejection(tmp_path):
    study = optuna.create_study()
    for i in range(100):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_sortino", 1.0 + i * 0.01)
        study.tell(t, float(i))

    t = study.ask()
    study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
    study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
    study._storage.set_trial_user_attr(t._trial_id, "oos_sortino", 2.0)
    study.tell(t, 1000.0)

    t = study.ask()
    study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
    study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
    study._storage.set_trial_user_attr(t._trial_id, "oos_sortino", 10.0)
    study.tell(t, 2000.0)

    mock_m_symbol = MagicMock()
    mock_m_symbol.oos_evaluated = True
    mock_m_symbol.oos_eligible = True
    mock_m_symbol.oos_sortino = 1.0  # Median sortino on holdout will be 1.0
    mock_m_symbol.oos_max_drawdown = 0.1
    mock_m_symbol.oos_total_return = 0.5
    mock_m_symbol.oos_total_trades = 10

    mock_m_global = MagicMock()
    mock_m_global.oos_sortino = 0.5
    mock_m_global.oos_max_drawdown = 0.1
    mock_m_global.oos_total_return = 0.1
    mock_m_global.oos_total_trades = 10

    with patch("automation.optimizer.confirm._holdout_metrics_for_params", side_effect=[mock_m_global] + [mock_m_symbol]*5) as mock_holdout:
        with patch("automation.optimizer.confirm.compute_reward", side_effect=[1.0, 2.0]) as mock_reward:
            with patch("automation.optimizer.confirm.config_dir") as mock_cfg_dir:
                t_cfg = {"deflated_selection": True, "deflation_confidence": 0.95, "holdout_top_k": 5}
                p_t = tmp_path / "tournament.json"
                p_t.write_text(json.dumps(t_cfg))

                b_cfg = {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10, "splits": 1, "embargo_period_days": 5}}
                p_b = tmp_path / "backtest.json"
                p_b.write_text(json.dumps(b_cfg))

                def _join(self, name):
                    if name == "tournament.json": return p_t
                    elif name == "backtest.json": return p_b
                    return tmp_path / name

                mock_cfg_dir.return_value.joinpath = _join
                mock_cfg_dir.return_value.__truediv__ = _join
                res = confirm_per_symbol_promotion(
                    study, "strategy", "sym", {}, catalog_newest_ns=0
                )

            assert res is not None
            assert res["status"] == "REJECTED_ON_HOLDOUT"
            assert res["holdout_passed"] == False
            assert "deflated_min_sortino" in res["metrics_symbol"]
            assert res["metrics_symbol"]["deflated_min_sortino"] > 1.0
