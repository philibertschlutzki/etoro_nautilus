import re
with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

old_matching = """        # Chronologisches FIFO-Matching pro Instrument
        for iid, f_list in instrument_fills.items():
            sorted_fills = sorted(f_list, key=_fill_ts_ns)
            buy_queue: deque[tuple[float, float, int]] = deque()  # (Stückzahl, Preis, Timestamp)
            sell_queue: deque[tuple[float, float, int]] = deque() # (Stückzahl, Preis, Timestamp)

            for f in sorted_fills:
                try:
                    qty = float(getattr(f, 'last_qty', getattr(f, 'filled_qty', getattr(f, 'quantity', 0.0))))
                    price = float(getattr(f, 'last_px', getattr(f, 'avg_px', getattr(f, 'price', 0.0))))
                    side_str = str(getattr(f, 'order_side', getattr(f, 'side', ''))).upper()
                except Exception:
                    continue

                if math.isnan(qty) or qty <= 0:
                    continue

                is_buy = "BUY" in side_str

                if is_buy:
                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        entry_notional = match_qty * s_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - s_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price, s_ts)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()
                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        buy_queue.append((qty, price, ts_entry))
                else:
                    while qty > 0 and buy_queue:
                        b_qty, b_price, b_ts = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        entry_notional = match_qty * b_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - b_ts
                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))
                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price, b_ts)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()
                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        sell_queue.append((qty, price, ts_entry))"""

new_matching = """        position_records = []

        # Chronologisches FIFO-Matching pro Instrument
        for iid, f_list in instrument_fills.items():
            sorted_fills = sorted(f_list, key=_fill_ts_ns)
            buy_queue: deque[tuple[float, float, int]] = deque()  # (Stückzahl, Preis, Timestamp)
            sell_queue: deque[tuple[float, float, int]] = deque() # (Stückzahl, Preis, Timestamp)

            current_pos_side = None
            current_pos_pnl = 0.0
            current_pos_notional = 0.0
            current_pos_start_ts = None
            current_pos_qty = 0.0

            for f in sorted_fills:
                try:
                    qty = float(getattr(f, 'last_qty', getattr(f, 'filled_qty', getattr(f, 'quantity', 0.0))))
                    price = float(getattr(f, 'last_px', getattr(f, 'avg_px', getattr(f, 'price', 0.0))))
                    side_str = str(getattr(f, 'order_side', getattr(f, 'side', ''))).upper()
                except Exception:
                    continue

                if math.isnan(qty) or qty <= 0:
                    continue

                is_buy = "BUY" in side_str

                if is_buy:
                    while qty > 0 and sell_queue:
                        s_qty, s_price, s_ts = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        entry_notional = match_qty * s_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - s_ts

                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))

                        current_pos_pnl += pnl
                        current_pos_notional += entry_notional

                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price, s_ts)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()

                        # If flat, finalize position
                        if not sell_queue:
                            if current_pos_start_ts is not None:
                                position_records.append((current_pos_pnl, ts, ts - current_pos_start_ts, current_pos_qty, current_pos_notional))
                            current_pos_side = None
                            current_pos_pnl = 0.0
                            current_pos_notional = 0.0
                            current_pos_start_ts = None
                            current_pos_qty = 0.0

                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        if current_pos_side is None:
                            current_pos_side = "LONG"
                            current_pos_start_ts = ts_entry
                        current_pos_qty += qty
                        buy_queue.append((qty, price, ts_entry))
                else:
                    while qty > 0 and buy_queue:
                        b_qty, b_price, b_ts = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        entry_notional = match_qty * b_price
                        if commission_bps > 0:
                            # Notional Value = Menge * Preis for both legs (entry and exit)
                            exit_value = match_qty * price
                            pnl -= (entry_notional + exit_value) * (commission_bps / 10000.0)
                        ts = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        holding_time_ns = ts - b_ts

                        pnls_with_ts.append((pnl, ts, holding_time_ns, match_qty))
                        notionals_with_ts.append((entry_notional, ts))

                        current_pos_pnl += pnl
                        current_pos_notional += entry_notional

                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price, b_ts)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()

                        if not buy_queue:
                            if current_pos_start_ts is not None:
                                position_records.append((current_pos_pnl, ts, ts - current_pos_start_ts, current_pos_qty, current_pos_notional))
                            current_pos_side = None
                            current_pos_pnl = 0.0
                            current_pos_notional = 0.0
                            current_pos_start_ts = None
                            current_pos_qty = 0.0

                    if qty > 0:
                        ts_entry = _fill_ts_ns(f)  # Issue #448 — fail-loud statt stillem 0-Default
                        if current_pos_side is None:
                            current_pos_side = "SHORT"
                            current_pos_start_ts = ts_entry
                        current_pos_qty += qty
                        sell_queue.append((qty, price, ts_entry))"""

content = content.replace(old_matching, new_matching)

new_block = """        is_pnls = []
        oos_pnls = []
        is_holding_times = []
        oos_holding_times = []
        is_notionals = []
        oos_notionals = []

        is_pnls_fill = []
        oos_pnls_fill = []
        is_holds_fill = []
        oos_holds_fill = []
        is_nots_fill = []
        oos_nots_fill = []

        if walk_forward_dict and start_ns is not None:
            is_window_ns = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
            oos_window_ns = walk_forward_dict.get("oos_window_days", 30) * 86400 * 1_000_000_000
            splits = walk_forward_dict.get("splits", 2)
            embargo_period_ns = walk_forward_dict.get("embargo_period_days", 0) * 86400 * 1_000_000_000
            # Issue #466/#463 — Fold-Geometrie aus der Single Source of Truth (kein Inline-Nachbau).
            fold_boundaries = compute_fold_boundaries(start_ns, walk_forward_dict)

            # IS Window boundaries are deterministic and identical for all folds
            _is_start_ns = start_ns
            is_end_ns = _is_start_ns + is_window_ns

            for (pnl, ts, ht, m_qty, notional) in position_records:
                is_oos = False

                # Check for OOS inclusion across all folds
                for _, split_oos_start_ns, split_oos_end_ns in fold_boundaries:
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        is_oos = True
                        break

                is_in_sample = _is_start_ns <= ts < is_end_ns

                if is_oos:
                    oos_pnls.append(pnl)
                    oos_holding_times.append((ht, m_qty))
                    oos_notionals.append(notional)
                elif is_in_sample:
                    is_pnls.append(pnl)
                    is_holding_times.append((ht, m_qty))
                    is_notionals.append(notional)

            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_oos = False
                for _, split_oos_start_ns, split_oos_end_ns in fold_boundaries:
                    if split_oos_start_ns <= ts < split_oos_end_ns:
                        is_oos = True
                        break
                is_in_sample = _is_start_ns <= ts < is_end_ns
                if is_oos:
                    oos_pnls_fill.append(pnl)
                    oos_holds_fill.append((ht, m_qty))
                    oos_nots_fill.append(notional)
                elif is_in_sample:
                    is_pnls_fill.append(pnl)
                    is_holds_fill.append((ht, m_qty))
                    is_nots_fill.append(notional)
        else:
            for (pnl, ts, ht, m_qty, notional) in position_records:
                is_pnls.append(pnl)
                is_holding_times.append((ht, m_qty))
                is_notionals.append(notional)
            for i, (pnl, ts, ht, m_qty) in enumerate(pnls_with_ts):
                notional, _ts = notionals_with_ts[i]
                is_pnls_fill.append(pnl)
                is_holds_fill.append((ht, m_qty))
                is_nots_fill.append(notional)"""

start_idx = content.find("        is_pnls = []")
end_idx = content.find("        import statistics", start_idx)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_block + "\n\n" + content[end_idx:]

target_return = """        is_metrics = _calculate_stats(is_pnls, is_holding_times, starting_capital, med_notional=is_med_notional, mtm_series=is_mtm)
        oos_metrics = _calculate_stats(oos_pnls, oos_holding_times, starting_capital, med_notional=oos_med_notional, mtm_series=oos_mtm) if oos_pnls else {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown": 0.0, "total_return": 0.0,
            "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
            "losses_count": 0,
            "median_position_notional": 0.0,
        }"""

new_return = """        is_metrics = _calculate_stats(is_pnls, is_holding_times, starting_capital, med_notional=is_med_notional, mtm_series=is_mtm)
        oos_metrics = _calculate_stats(oos_pnls, oos_holding_times, starting_capital, med_notional=oos_med_notional, mtm_series=oos_mtm) if oos_pnls else {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown": 0.0, "total_return": 0.0,
            "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0,
            "losses_count": 0,
            "median_position_notional": 0.0,
        }

        is_fill_med_n = statistics.median(is_nots_fill) if is_nots_fill else 0.0
        oos_fill_med_n = statistics.median(oos_nots_fill) if oos_nots_fill else 0.0
        is_metrics["fill_matches"] = _calculate_stats(is_pnls_fill, is_holds_fill, starting_capital, med_notional=is_fill_med_n, mtm_series=is_mtm)
        if oos_pnls_fill:
            oos_metrics["fill_matches"] = _calculate_stats(oos_pnls_fill, oos_holds_fill, starting_capital, med_notional=oos_fill_med_n, mtm_series=oos_mtm)
        else:
            oos_metrics["fill_matches"] = {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0, "max_drawdown": 0.0, "total_return": 0.0, "avg_holding_time_s": 0.0, "median_holding_time_s": 0.0, "losses_count": 0, "median_position_notional": 0.0,
            }"""

if target_return in content:
    content = content.replace(target_return, new_return)

with open("automation/backtest_runner.py", "w") as f:
    f.write(content)
