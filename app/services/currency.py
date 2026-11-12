from __future__ import annotations
"""
currency.py – Multi-currency bankroll and staking support.
Supports UGX (Ugandan Shilling), USD, KES, TZS, EUR, GBP.
"""
from typing import Any, Dict, List

CURRENCIES: Dict[str, Dict[str, Any]] = {
    "USD": {"symbol": "$", "rate_from_usd": 1.0, "decimals": 2, "name": "US Dollar (USD)"},
    "UGX": {"symbol": "UGX ", "rate_from_usd": 3700.0, "decimals": 0, "name": "Ugandan Shilling (UGX)"},
    "KES": {"symbol": "KSh ", "rate_from_usd": 130.0, "decimals": 0, "name": "Kenyan Shilling (KES)"},
    "TZS": {"symbol": "TSh ", "rate_from_usd": 2550.0, "decimals": 0, "name": "Tanzanian Shilling (TZS)"},
    "EUR": {"symbol": "€", "rate_from_usd": 0.92, "decimals": 2, "name": "Euro (EUR)"},
    "GBP": {"symbol": "£", "rate_from_usd": 0.79, "decimals": 2, "name": "British Pound (GBP)"},
}

def format_stake(usd_amount: float, currency: str = "USD") -> str:
    """Format a USD stake in target currency."""
    cfg = CURRENCIES.get(currency, CURRENCIES["USD"])
    converted = usd_amount * cfg["rate_from_usd"]
    if cfg["decimals"] == 0:
        return f"{cfg['symbol']}{converted:,.0f}"
    return f"{cfg['symbol']}{converted:,.{cfg['decimals']}f}"

def format_stake_dual(usd_amount: float, currency: str = "USD") -> str:
    """Return display string showing USD and local currency if different."""
    if currency == "USD":
        return f"${usd_amount:.2f}"
    local_str = format_stake(usd_amount, currency)
    return f"${usd_amount:.2f} ({local_str})"

def get_currency_options() -> List[str]:
    return list(CURRENCIES.keys())


def convert_amount(amount: float, from_curr: str = "USD", to_curr: str = "UGX") -> float:
    """Convert an amount between supported currencies using USD pivot rate."""
    from_cfg = CURRENCIES.get(from_curr.upper(), CURRENCIES["USD"])
    to_cfg = CURRENCIES.get(to_curr.upper(), CURRENCIES["UGX"])
    usd = amount / from_cfg["rate_from_usd"]
    return usd * to_cfg["rate_from_usd"]

