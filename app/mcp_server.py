from mcp.server.fastmcp import FastMCP

mcp = FastMCP("finance-navigator-mcp")

@mcp.tool()
def get_subscription_catalog() -> str:
    """Get a list of popular subscription services and their standard monthly plans/costs."""
    catalog = (
        "Popular Subscriptions Catalog:\n"
        "- Netflix: Standard ($15.49/mo), Premium ($22.99/mo)\n"
        "- Spotify: Individual ($10.99/mo), Family ($16.99/mo)\n"
        "- Gym Pass: Basic ($29.99/mo), Premium ($49.99/mo)\n"
        "- Adobe Creative Cloud: Single App ($20.99/mo), All Apps ($54.99/mo)\n"
        "- YouTube Premium: Individual ($13.99/mo), Family ($22.99/mo)"
    )
    return catalog

@mcp.tool()
def calculate_savings_projection(current_balance: float, monthly_deposit: float, annual_interest_rate: float, months: int) -> str:
    """Calculate projected savings growth over a period of months with monthly deposits and compound interest.
    
    Args:
        current_balance: Starting savings account balance.
        monthly_deposit: Amount deposited monthly.
        annual_interest_rate: Annual interest rate in percent (e.g. 4.5 for 4.5%).
        months: Duration in months.
    """
    rate_monthly = (annual_interest_rate / 100) / 12
    balance = current_balance
    total_deposited = 0.0
    for _ in range(months):
        balance = balance * (1 + rate_monthly) + monthly_deposit
        total_deposited += monthly_deposit
    
    interest_earned = balance - current_balance - total_deposited
    return (
        f"Savings Projection over {months} months:\n"
        f"- Starting Balance: ${current_balance:,.2f}\n"
        f"- Total Deposited: ${total_deposited:,.2f}\n"
        f"- Projected Interest Earned: ${interest_earned:,.2f}\n"
        f"- Projected Final Balance: ${balance:,.2f}"
    )

@mcp.tool()
def check_unused_subscriptions(last_login_days: int) -> str:
    """Checks and flags if a subscription is considered unused based on the number of days since last login.
    
    Args:
        last_login_days: Days since the user last accessed the service.
    """
    if last_login_days >= 90:
        return "CRITICAL: Service unused for >= 90 days. High recommendation to cancel."
    elif last_login_days >= 30:
        return "WARNING: Service unused for >= 30 days. Recommend reviewing usage."
    return "OK: Service actively used within the last 30 days."

if __name__ == "__main__":
    mcp.run()
