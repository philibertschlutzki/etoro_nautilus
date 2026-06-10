from automation.backtest_runner import collect_oos_fold_sortinos

def test_collect_skips_none_and_preserves_order():
    folds = [{"sortino_ratio": 1.2}, {"sortino_ratio": None}, {"sortino_ratio": 0.8}]
    assert collect_oos_fold_sortinos(folds) == [1.2, 0.8]

def test_collect_empty():
    assert collect_oos_fold_sortinos([]) == []
