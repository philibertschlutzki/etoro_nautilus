import re

with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

# According to PR comments: `margin_balance` might not include floating PNL.
# "In zahlreichen quantitativen Trading-Engines repräsentiert die Margin-Balance ausschließlich Realized Capital (Cash + geschlossene Trades)."
# So we need: `total_equity = account.margin_balance().as_double() + sum(pos.unrealized_pnl.as_double() for pos in portfolio.positions())` wait, actually `account.equity()` usually does it, but we got an error earlier.
# Let's fix the PortfolioMonitor code to do exactly what's requested: `total_equity = account.margin_balance() + sum(pos.unrealized_pnl for pos in portfolio.positions())` if we must. Wait, NautilusTrader `account.margin_balance()` might be the only property available. We can just sum the unrealized PNL from `portfolio.positions()`.

search_on_bar = """        try:
            # We try to use portfolio.equity directly, which is common in NT for aggregate equity.
            # But in NautilusTrader 1.229.0 it might be a property or a method. Let's try it as a property or method
            # In our previous dir output, `equity` was listed.
            eq = self.portfolio.account.margin_balance().as_double() if hasattr(self.portfolio, 'account') and hasattr(self.portfolio.account, 'margin_balance') else self.portfolio.account.equity().as_double() if hasattr(self.portfolio, 'account') else None
            # Wait, Nautilus Trader portfolio doesn't have `.accounts()`. It has `.account` (property?) Let's fallback to `self.portfolio.equity` if it exists.
        except Exception:
            pass
        # To be completely safe and get the total portfolio equity:
        try:
            # According to our `mtm_portfolio_test.py`, `portfolio` has `account`, `equity`, `unrealized_pnl`...
            # if `equity` is a property or method:
            if callable(self.portfolio.equity):
                eq = self.portfolio.equity().as_double()
            else:
                eq = float(self.portfolio.equity)
            self.equity_curve.append((bar.ts_event, eq))
        except Exception:
            pass"""

replace_on_bar = """        try:
            eq = None
            if hasattr(self.portfolio, 'account'):
                # In NautilusTrader v1.229.0, margin_balance() might only be realized capital.
                # To guarantee we capture floating PnL (Issue #465), we explicitly add the sum of unrealized PnLs
                base_eq = self.portfolio.account.margin_balance().as_double() if hasattr(self.portfolio.account, 'margin_balance') else self.portfolio.account.balance().as_double()
                floating_pnl = sum(pos.unrealized_pnl().as_double() if callable(pos.unrealized_pnl) else float(pos.unrealized_pnl) for pos in self.portfolio.positions()) if hasattr(self.portfolio, 'positions') else 0.0
                eq = base_eq + floating_pnl
            elif hasattr(self.portfolio, 'equity'):
                if callable(self.portfolio.equity):
                    eq = self.portfolio.equity().as_double()
                else:
                    eq = float(self.portfolio.equity)
            if eq is not None:
                self.equity_curve.append((bar.ts_event, eq))
        except Exception:
            pass"""

content = content.replace(search_on_bar, replace_on_bar)

with open("automation/backtest_runner.py", "w") as f:
    f.write(content)
