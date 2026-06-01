with open('automation/backtest_runner.py', 'r') as f:
    code = f.read()

# 1. Update matching logic to compute a qty-weighted holding time:
#   holding_time_ns = (ts - s_ts) * match_qty (we will sum these up in _calculate_stats instead of simple mean)
# And we also need to keep track of match_qty to divide later.
# So pnls_with_ts.append((pnl, ts, holding_time_ns)) -> pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))

match_buy_old = """                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns))"""
match_buy_new = """                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))"""
code = code.replace(match_buy_old, match_buy_new)

match_sell_old = """                    while qty > 0 and buy_queue:
                        b_qty, b_price, b_ts = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns))"""
match_sell_new = """                    while qty > 0 and buy_queue:
                        b_qty, b_price, b_ts = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        ts = getattr(f, 'ts_event', getattr(f, 'ts_init', 0))
                        if isinstance(ts, pd.Timestamp):
                            ts = ts.value
                        ts = int(ts)
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))"""
code = code.replace(match_sell_old, match_sell_new)

split_old = """        for pnl, ts, holding_time_ns in pnls_with_ts:
            if oos_start_ns is not None and ts >= oos_start_ns:
                oos_pnls.append(pnl)
                oos_holds.append(holding_time_ns)
            else:
                is_pnls.append(pnl)
                is_holds.append(holding_time_ns)"""
split_new = """        for pnl, ts, holding_time_ns, match_qty in pnls_with_ts:
            if oos_start_ns is not None and ts >= oos_start_ns:
                oos_pnls.append(pnl)
                oos_holds.append((holding_time_ns, match_qty))
            else:
                is_pnls.append(pnl)
                is_holds.append((holding_time_ns, match_qty))"""
code = code.replace(split_old, split_new)

# Modify _calculate_stats signature to accept list of tuples instead of list of ints
# def _calculate_stats(pnl_list: list[float], hold_list: list[tuple[int, float]], starting_capital: float) -> dict:

calc_stats_sig_old = "def _calculate_stats(pnl_list: list[float], hold_list: list[int], starting_capital: float) -> dict:"
calc_stats_sig_new = "def _calculate_stats(pnl_list: list[float], hold_list: list[tuple[int, float]], starting_capital: float) -> dict:"
code = code.replace(calc_stats_sig_old, calc_stats_sig_new)

calc_stats_logic_old = """    import statistics
    if hold_list:
        holds_s = [h / 1e9 for h in hold_list]
        avg_hold = sum(holds_s) / len(holds_s)
        med_hold = statistics.median(holds_s)
    else:
        avg_hold = 0.0
        med_hold = 0.0"""
calc_stats_logic_new = """    import statistics
    if hold_list:
        holds_s = [h / 1e9 for h, _ in hold_list]
        med_hold = statistics.median(holds_s)

        total_qty = sum(qty for _, qty in hold_list)
        if total_qty > 1e-9:
            avg_hold = sum((h / 1e9) * qty for h, qty in hold_list) / total_qty
        else:
            avg_hold = sum(holds_s) / len(holds_s)
    else:
        avg_hold = 0.0
        med_hold = 0.0"""
code = code.replace(calc_stats_logic_old, calc_stats_logic_new)

with open('automation/backtest_runner.py', 'w') as f:
    f.write(code)
