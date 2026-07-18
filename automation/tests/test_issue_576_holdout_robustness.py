import pytest
import optuna
import json
from unittest.mock import MagicMock, patch
from automation.optimizer.confirm import confirm_per_symbol_promotion

def test_deflated_holdout_gate_rejection(tmp_path):
    # Issue #611/#618 — die Deflation ist jetzt die DSR auf der PER-PERIODEN-Sortino-Skala über die
    # ELIGIBLE Kohorte (nicht die bimodale Reward-Verteilung, #611). Reine Rausch-Sortinos (kein echter
    # Edge): der promotete per-Perioden-Sortino schlägt die Multiple-Testing-Schwelle SR₀ nicht mit 95 %
    # (DSR < 0.95) ⇒ HOLD. Referenz-nah (#618): ŜR≈0.114, T=202, N=100, V≈1.8e-3 ⇒ DSR≈0.54 < 0.95.
    import numpy as np
    study = optuna.create_study()
    rng = np.random.default_rng(0)
    noise_sr = rng.normal(0.0, 0.0425, 100)   # V ≈ 1.8e-3 ⇒ SR₀ ≈ 0.107
    for i in range(100):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_evaluated", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_sortino_period", float(noise_sr[i]))
        # Issue #701 — n_periods ist seit #701 ein Pflicht-Parameter von sr0_multiple_testing_robust
        # (der var_floor-Fallback ohne T wurde entfernt); T=202 matcht die im Docstring dokumentierte
        # Referenz-Grössenordnung (ŜR≈0.114, T=202) und die des promoteten mock_m_symbol unten.
        study._storage.set_trial_user_attr(t._trial_id, "oos_n_periods", 202)
        study.tell(t, float((i % 7) - 3))

    mock_m_symbol = MagicMock()
    mock_m_symbol.oos_evaluated = True
    mock_m_symbol.oos_eligible = True
    mock_m_symbol.oos_sortino = 1.0
    mock_m_symbol.oos_sortino_period = 0.114   # per-Perioden-Sortino des promoteten Holdout-Laufs
    mock_m_symbol.oos_n_periods = 202
    mock_m_symbol.oos_ret_skew = 0.0
    mock_m_symbol.oos_ret_kurtosis = 3.0
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
            # Issue #618 — DSR-Telemetrie (Sortino-Skala) statt der alten Reward-Schwelle.
            assert "deflated_dsr" in res["metrics_symbol"]
            assert res["metrics_symbol"]["deflated_dsr"] < 0.95   # Rausch ⇒ HOLD
            assert 0.05 < res["metrics_symbol"]["deflated_sr0"] < 0.2
