def print_portfolio_report(self, prices):
    """
    Prints a detailed report of the portfolio, including ticker, group, adjust flag, shares, price, value, target %, and drift %.
    Colors drift by magnitude for visual cues.
    """
    print(f"\nPortfolio Report (Base Currency: {self.base_currency})")
    print(
        "{:<10} {:<8} {:<6} {:>12} {:>12} {:>12} {:>12} {:>10}".format(
            "Ticker",
            "Group",
            "Adjst",
            "Shares",
            "Price",
            "Value",
            "Target %",
            "Drift %",
        )
    )
    total_value = 0
    asset_data = []
    # Calculate value for each asset and accumulate total
    for asset in self.get_assets():
        price = prices.get(asset.ticker)
        value = price * asset.shares if price is not None else 0
        total_value += value
        asset_data.append((asset, price, value))
    # Print each asset's details with colored drift
    for asset, price, value in asset_data:
        target_pct = asset.target_allocation
        current_pct = (value / total_value * 100) if total_value > 0 else 0
        drift = current_pct - target_pct
        group = getattr(asset, "group", "")
        adjust = getattr(asset, "adjust", "")
        # Color drift: yellow if abs(drift) > 5%, green if drift <= 0, red if drift > 0 and <= 5%
        if abs(drift) > 5:
            drift_str = f"\033[93m{drift:.2f}\033[0m"  # yellow
        elif drift > 0:
            drift_str = f"\033[91m{drift:.2f}\033[0m"  # red
        else:
            drift_str = f"\033[92m{drift:.2f}\033[0m"  # green
        print(
            "{:<10} {:<8} {:<6} {:>12.4f} {:>12.2f} {:>12.2f} {:>12.2f} {:>10}".format(
                asset.ticker,
                group,
                adjust,
                asset.shares,
                price if price else 0,
                value,
                target_pct,
                drift_str,
            )
        )
    print(f"\nTotal Portfolio Value: {total_value:.2f} {self.base_currency}")


def print_rebalance_suggestions(suggestions, prices=None):
    """
    Prints suggested trades for rebalancing, including the cash value of each trade if prices are provided.
    Shows group column if portfolio is provided.
    """
    if not suggestions:
        print("\nNo trades needed. Portfolio is within target allocations.")
        return
    print("\nSuggested Trades:")
    # Print header depending on available info
    if self:
        print(
            "{:<10} {:<8} {:<6} {:>8} {:>12} {:>12} {:>12}".format(
                "Ticker", "Group", "Action", "Shares", "From", "To", "Cash"
            )
        )
    elif prices:
        print(
            "{:<10} {:<6} {:>8} {:>12} {:>12} {:>12}".format(
                "Ticker", "Action", "Shares", "From", "To", "Cash"
            )
        )
    else:
        print(
            "{:<10} {:<6} {:>8} {:>12} {:>12}".format(
                "Ticker", "Action", "Shares", "From", "To"
            )
        )
    # Print each suggestion, including group if available
    for s in suggestions:
        cash = None
        group = ""
        if self:
            asset = next(
                (a for a in self.get_assets() if a.ticker == s["ticker"]), None
            )
            group = getattr(asset, "group", "") if asset else ""
        if prices and s["ticker"] in prices and prices[s["ticker"]] is not None:
            cash = s["shares"] * prices[s["ticker"]]
        if self:
            print(
                "{:<10} {:<8} {:<6} {:>8.2f} {:>12.2f} {:>12.2f} {:>12.2f}".format(
                    s["ticker"],
                    group,
                    s["action"],
                    s["shares"],
                    s["from"],
                    s["to"],
                    cash if cash is not None else 0,
                )
            )
        elif prices:
            print(
                "{:<10} {:<6} {:>8.2f} {:>12.2f} {:>12.2f} {:>12.2f}".format(
                    s["ticker"],
                    s["action"],
                    s["shares"],
                    s["from"],
                    s["to"],
                    cash if cash is not None else 0,
                )
            )
        else:
            print(
                "{:<10} {:<6} {:>8.2f} {:>12.2f} {:>12.2f}".format(
                    s["ticker"], s["action"], s["shares"], s["from"], s["to"]
                )
            )


def print_group_weights_report(self, prices):
    """
    Prints a table showing the current and target weights of each group in the portfolio, including group drift and cash value per group.
    Warns if total target != 100%.
    Colors drift by magnitude for visual cues.
    """
    from collections import defaultdict
    import math

    print(f"\nGroup Weights Report (Base Currency: {self.base_currency})")
    print(
        "{:<10} {:>15} {:>15} {:>15} {:>10}".format(
            "Group", "Current %", "Target %", "Cash", "Drift %"
        )
    )
    total_value = 0
    group_values = defaultdict(float)
    group_targets = defaultdict(float)
    # Aggregate values and targets by group
    for asset in self.get_assets():
        price = prices.get(asset.ticker)
        value = price * asset.shares if price is not None else 0
        group = getattr(asset, "group", "") or "UNGROUPED"
        group_values[group] += value
        group_targets[group] += asset.target_allocation
        total_value += value
    total_target = sum(group_targets.values())
    # Print each group's allocation and drift
    for group in sorted(group_values.keys()):
        current_pct = (
            (group_values[group] / total_value * 100) if total_value > 0 else 0
        )
        target_pct = group_targets[group]
        cash = group_values[group]
        drift = current_pct - target_pct
        # Color drift: yellow if abs(drift) > 5%, green if drift <= 0, red if drift > 0 and <= 5%
        if abs(drift) > 5:
            drift_str = f"\033[93m{drift:.2f}\033[0m"  # yellow
        elif drift > 0:
            drift_str = f"\033[91m{drift:.2f}\033[0m"  # red
        else:
            drift_str = f"\033[92m{drift:.2f}\033[0m"  # green
        print(
            "{:<10} {:>15.2f} {:>15.2f} {:>15.2f} {:>10}".format(
                group, current_pct, target_pct, cash, drift_str
            )
        )
    # Warn if total target allocation is not close to 100%
    if not math.isclose(total_target, 100.0, rel_tol=1e-4):
        print(f"\n[Warning] Total target allocation = {total_target:.2f}% (not 100%)")


def apply_trades(self, suggestions):
    """
    Returns a new Portfolio with trades applied (deepcopy), updating shares for each suggestion.
    Prevents negative shares.
    """
    from copy import deepcopy

    new_portfolio = deepcopy(self)
    ticker_to_asset = {a.ticker: a for a in new_portfolio.get_assets()}
    for s in suggestions:
        ticker = s["ticker"]
        if ticker in ticker_to_asset:
            asset = ticker_to_asset[ticker]
            if s["action"] == "BUY":
                asset.shares += s["shares"]
            elif s["action"] == "SELL":
                asset.shares -= s["shares"]
                if asset.shares < 0:
                    asset.shares = 0
    return new_portfolio


def print_new_portfolio_report(self, prices, suggestions):
    """
    Prints the portfolio report after applying the suggested trades, and the group weights report.
    Also prints the old and new total portfolio values for comparison, and the delta.
    """
    old_total = sum(prices.get(a.ticker, 0) * a.shares for a in self.get_assets())
    new_portfolio = apply_trades(self, suggestions)
    new_total = sum(
        prices.get(a.ticker, 0) * a.shares for a in new_portfolio.get_assets()
    )
    print("\nNew Portfolio Allocation After Suggested Trades:")
    print_portfolio_report(new_portfolio, prices)
    print_group_weights_report(new_portfolio, prices)
    delta = new_total - old_total
    pct = (delta / old_total * 100) if old_total else 0
    print(f"Δ Portfolio Value: {delta:.2f} {self.base_currency} ({pct:.2f}%)")