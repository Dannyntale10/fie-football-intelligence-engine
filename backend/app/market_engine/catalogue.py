"""Canonical market catalogue.

Discovery is provider-driven: a market is discovered because a feed priced it,
never because it appears in this file. The catalogue only normalizes discovered
markets into families, records settlement rules, and states which model (if any)
can price a family. Families we cannot model are still catalogued and shown with
qualification explicitly unavailable, so nothing is silently dropped or faked.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple

ENGINE_VERSION = "market-engine-1"

# Categories mirror the sections of the platform requirements.
CATEGORIES = (
    "result",
    "goals",
    "halves",
    "corners",
    "cards",
    "players",
    "statistics",
    "combinations",
)


@dataclass(frozen=True)
class MarketDefinition:
    family: str
    label: str
    category: str
    settlement_group: str
    model: Optional[str] = None
    required_stats: Tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["required_stats"] = list(self.required_stats)
        return data


_DEFINITIONS: Tuple[MarketDefinition, ...] = (
    # ── Result markets ────────────────────────────────────────────────────
    MarketDefinition("match_result", "Full-time 1X2", "result", "1x2", "dixon_coles"),
    MarketDefinition("double_chance", "Double Chance", "result", "double_chance", "dixon_coles"),
    MarketDefinition("draw_no_bet", "Draw No Bet", "result", "dnb", "dixon_coles"),
    MarketDefinition("asian_handicap", "Asian Handicap", "result", "asian_handicap", "dixon_coles",
                     notes="Half lines only until quarter-line split rules are modelled."),
    MarketDefinition("european_handicap", "European Handicap", "result", "european_handicap", None,
                     notes="Whole-goal handicap with draw priced separately."),
    MarketDefinition("first_half_result", "First-half result", "halves", "ht_1x2", None,
                     required_stats=("first_half_goals",)),
    MarketDefinition("second_half_result", "Second-half result", "halves", "sh_1x2", None,
                     required_stats=("second_half_goals",)),
    MarketDefinition("half_time_full_time", "Half-time / full-time", "halves", "htft", None,
                     required_stats=("first_half_goals",)),
    MarketDefinition("to_qualify", "To qualify", "result", "to_qualify", None,
                     notes="Cup tie progression; depends on competition rules."),

    # ── Goal markets ─────────────────────────────────────────────────────
    MarketDefinition("total_goals", "Total goals over/under", "goals", "total_goals", "dixon_coles"),
    MarketDefinition("team_total_home", "Home team goals", "goals", "team_total_home", "dixon_coles"),
    MarketDefinition("team_total_away", "Away team goals", "goals", "team_total_away", "dixon_coles"),
    MarketDefinition("either_team_goals", "Either team goals", "goals", "either_team_goals", "dixon_coles"),
    MarketDefinition("btts", "Both teams to score", "goals", "btts", "dixon_coles"),
    MarketDefinition("btts_first_half", "BTTS first half", "halves", "btts_1h", None,
                     required_stats=("first_half_goals",)),
    MarketDefinition("btts_second_half", "BTTS second half", "halves", "btts_2h", None,
                     required_stats=("second_half_goals",)),
    MarketDefinition("clean_sheet", "Clean sheet", "goals", "clean_sheet", "dixon_coles"),
    MarketDefinition("win_to_nil", "Win to nil", "goals", "win_to_nil", "dixon_coles"),
    MarketDefinition("correct_score", "Correct score", "goals", "correct_score", "dixon_coles"),
    MarketDefinition("goal_range", "Goal ranges", "goals", "goal_range", "dixon_coles"),
    MarketDefinition("odd_even", "Odd/even total goals", "goals", "odd_even", "dixon_coles"),
    MarketDefinition("score_both_halves", "Team to score in both halves", "halves", "score_both_halves", None,
                     required_stats=("first_half_goals", "second_half_goals")),
    MarketDefinition("first_goal", "First goal", "goals", "first_goal", None),
    MarketDefinition("last_goal", "Last goal", "goals", "last_goal", None),

    # ── Half and time-based markets ──────────────────────────────────────
    MarketDefinition("first_half_goals", "First-half goal totals", "halves", "total_goals_1h", None,
                     required_stats=("first_half_goals",)),
    MarketDefinition("second_half_goals", "Second-half goal totals", "halves", "total_goals_2h", None,
                     required_stats=("second_half_goals",)),
    MarketDefinition("goal_both_halves", "Goal in both halves", "halves", "goal_both_halves", None,
                     required_stats=("first_half_goals", "second_half_goals")),
    MarketDefinition("first_goal_timing", "First goal timing", "halves", "first_goal_timing", None),

    # ── Corner markets ───────────────────────────────────────────────────
    MarketDefinition("total_corners", "Total corners", "corners", "total_corners", "corners",
                     required_stats=("corners",)),
    MarketDefinition("team_corners_home", "Home team corners", "corners", "team_corners_home", "corners",
                     required_stats=("corners",)),
    MarketDefinition("team_corners_away", "Away team corners", "corners", "team_corners_away", "corners",
                     required_stats=("corners",)),
    MarketDefinition("corner_handicap", "Corner handicap", "corners", "corner_handicap", "corners",
                     required_stats=("corners",)),
    MarketDefinition("first_half_corners", "First-half corners", "corners", "corners_1h", None,
                     required_stats=("first_half_corners",)),
    MarketDefinition("second_half_corners", "Second-half corners", "corners", "corners_2h", None,
                     required_stats=("second_half_corners",)),
    MarketDefinition("most_corners", "Most corners", "corners", "most_corners", "corners",
                     required_stats=("corners",)),
    MarketDefinition("corner_race", "Corner race", "corners", "corner_race", None,
                     required_stats=("corners",)),

    # ── Cards and disciplinary markets ───────────────────────────────────
    MarketDefinition("total_cards", "Total cards", "cards", "total_cards", "cards",
                     required_stats=("cards",)),
    MarketDefinition("yellow_cards", "Yellow cards", "cards", "yellow_cards", "cards",
                     required_stats=("cards",)),
    MarketDefinition("red_cards", "Red cards", "cards", "red_cards", None,
                     required_stats=("cards",)),
    MarketDefinition("team_cards_home", "Home team cards", "cards", "team_cards_home", "cards",
                     required_stats=("cards",)),
    MarketDefinition("team_cards_away", "Away team cards", "cards", "team_cards_away", "cards",
                     required_stats=("cards",)),
    MarketDefinition("card_handicap", "Card handicap", "cards", "card_handicap", "cards",
                     required_stats=("cards",)),
    MarketDefinition("first_half_cards", "First-half cards", "cards", "cards_1h", None,
                     required_stats=("first_half_cards",)),
    MarketDefinition("second_half_cards", "Second-half cards", "cards", "cards_2h", None,
                     required_stats=("second_half_cards",)),
    MarketDefinition("player_card", "Player to be carded", "cards", "player_card", "player",
                     required_stats=("player_cards", "lineups")),

    # ── Player markets ───────────────────────────────────────────────────
    MarketDefinition("player_goalscorer", "Anytime / first / last goalscorer", "players",
                     "player_goalscorer", "player",
                     required_stats=("player_goals", "expected_minutes", "lineups")),
    MarketDefinition("player_shots", "Player shots", "players", "player_shots", "player",
                     required_stats=("player_shots", "expected_minutes")),
    MarketDefinition("player_shots_on_target", "Player shots on target", "players",
                     "player_shots_on_target", "player",
                     required_stats=("player_shots_on_target", "expected_minutes")),
    MarketDefinition("player_assists", "Player assists", "players", "player_assists", "player",
                     required_stats=("player_assists", "expected_minutes")),
    MarketDefinition("goalkeeper_saves", "Goalkeeper saves", "players", "goalkeeper_saves", "player",
                     required_stats=("goalkeeper_saves", "lineups")),

    # ── Match statistics markets ─────────────────────────────────────────
    MarketDefinition("total_shots", "Total shots", "statistics", "total_shots", None,
                     required_stats=("shots",)),
    MarketDefinition("shots_on_target", "Shots on target", "statistics", "shots_on_target", None,
                     required_stats=("shots_on_target",)),
    MarketDefinition("team_shots", "Team shots", "statistics", "team_shots", None,
                     required_stats=("shots",)),
    MarketDefinition("offsides", "Offsides", "statistics", "offsides", None,
                     required_stats=("offsides",)),
    MarketDefinition("tackles", "Tackles", "statistics", "tackles", None,
                     required_stats=("tackles",)),
    MarketDefinition("fouls", "Fouls", "statistics", "fouls", None,
                     required_stats=("fouls",)),
    MarketDefinition("saves", "Saves", "statistics", "saves", None,
                     required_stats=("saves",)),
    MarketDefinition("possession", "Possession", "statistics", "possession", None,
                     required_stats=("possession",)),

    # ── Combination markets ──────────────────────────────────────────────
    MarketDefinition("result_btts", "Result + BTTS", "combinations", "result_btts", "dixon_coles"),
    MarketDefinition("result_goals", "Result + goals", "combinations", "result_goals", "dixon_coles"),
    MarketDefinition("double_chance_goals", "Double chance + goals", "combinations",
                     "dc_goals", "dixon_coles"),
    MarketDefinition("btts_goals", "BTTS + over/under", "combinations", "btts_goals", "dixon_coles"),
    MarketDefinition("htft_combo", "HT/FT combination", "combinations", "htft_combo", None,
                     required_stats=("first_half_goals",)),
)

MARKET_CATALOGUE: Dict[str, MarketDefinition] = {d.family: d for d in _DEFINITIONS}


def get_definition(family: str) -> Optional[MarketDefinition]:
    return MARKET_CATALOGUE.get(family)


def catalogue_payload() -> List[Dict[str, object]]:
    """Serialisable catalogue for the API and dashboard."""
    return [d.as_dict() for d in _DEFINITIONS]


def families_by_category() -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {c: [] for c in CATEGORIES}
    for definition in _DEFINITIONS:
        grouped.setdefault(definition.category, []).append(definition.family)
    return grouped


def families(iterable: Iterable[MarketDefinition]) -> List[str]:
    return [d.family for d in iterable]
