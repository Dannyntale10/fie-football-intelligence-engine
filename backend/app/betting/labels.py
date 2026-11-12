"""Human-readable market and team labels for model market keys."""
from __future__ import annotations


def selection_label(key: str, home: str, away: str) -> tuple[str, str]:
    """Human-readable (market type, selection) for a model market key."""
    fixed = {
        "home_win": ("Match Result (1)", f"{home} to Win"),
        "draw": ("Match Result (X)", "Draw"),
        "away_win": ("Match Result (2)", f"{away} to Win"),
        "double_chance_1x": ("Double Chance (1X)", f"{home} Win or Draw (1X)"),
        "double_chance_x2": ("Double Chance (X2)", f"{away} Win or Draw (X2)"),
        "double_chance_12": ("Double Chance (12)", f"{home} or {away} to Win (12)"),
        "btts_yes": ("Both Teams To Score", "Both Teams To Score - Yes"),
        "btts_no": ("Both Teams To Score", "Both Teams To Score - No"),
        "dnb_home": ("Draw No Bet (DNB)", f"{home} Draw No Bet (DNB)"),
        "dnb_away": ("Draw No Bet (DNB)", f"{away} Draw No Bet (DNB)"),
    }
    if key in fixed:
        return fixed[key]

    for prefix, under in (("over_", False), ("under_", True)):
        if key.startswith(prefix):
            line = key[len(prefix):].replace("_", ".")
            label = f"{'Under' if under else 'Over'} {line} Match Goals"
            return "Total Goals", label

    for prefix, side in (("ah_home_minus_", "home"), ("ah_away_plus_", "away")):
        if key.startswith(prefix):
            line = key[len(prefix):].replace("_", ".")
            if side == "home":
                return "Asian Handicap", f"{home} -{line} Handicap"
            return "Asian Handicap", f"{away} +{line} Handicap"

    return "Market", key.replace("_", " ").title()


def team_label(key: str, home: str, away: str) -> str:
    if key.startswith("home") or key.startswith("ah_home") or key.startswith("dnb_home"):
        return home
    if key.startswith("double_chance_1x"):
        return home
    if key.startswith("away") or key.startswith("ah_away") or key.startswith("dnb_away"):
        return away
    if key.startswith("double_chance_x2"):
        return away
    return f"{home} / {away}"
