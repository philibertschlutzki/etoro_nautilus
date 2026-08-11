from automation.optimizer.sweep import create_bayesian_tpe_study, objective_ev_chf


def test_issue_796_create_bayesian_tpe_study(tmp_path):
    db_file = tmp_path / "optuna_test.db"
    url = f"sqlite:///{db_file}"
    study = create_bayesian_tpe_study("test_study", storage_url=url)
    
    assert study.study_name == "test_study"
    assert study.direction.name == "MAXIMIZE"


def test_issue_796_objective_ev_chf_evaluated():
    trial_attrs = {
        "oos_evaluated": True,
        "oos_win_rate": 0.60,
        "oos_avg_win_chf": 100.0,
        "oos_avg_loss_chf": 50.0,
        "oos_turnover_cost_chf": 5.0
    }
    
    # EV_CHF = (0.60 * 100) - (0.40 * 50) - 5 = 60 - 20 - 5 = 35.0
    ev = objective_ev_chf(trial_attrs)
    assert abs(ev - 35.0) < 1e-4


def test_issue_796_objective_ev_chf_not_evaluated():
    trial_attrs = {"oos_evaluated": False}
    assert objective_ev_chf(trial_attrs) <= -10.0

