"""Normalize raw bookmaker market names into catalogue families.

Feeds label the same market in many ways ("1X2", "Match Result", "Full Time
Result"; "Over 2.5" vs "Total Goals Over/Under 2.5"). Normalization maps a raw
name to a family and canonical selection without discarding the original label,
so provenance survives all the way to the UI.

A market that cannot be mapped is still returned with ``family=None`` instead
of being dropped: discovery reports it as unmodelled, never as analysed.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.market_engine.catalogue import get_definition

_LINE_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")
_SCORE_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


@dataclass(frozen=True)
class NormalizedSelection:
    family: Optional[str]
    selection: Optional[str]
    line: Optional[float]
    settlement_group: Optional[str]
    model_key: Optional[str]
    raw_name: str
    raw_selection: str
    raw_handicap: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Existing probability-model keys, mapped to their catalogue family.
_MODEL_KEY_SELECTIONS = {
    "home_win": ("match_result", "1"),
    "draw": ("match_result", "X"),
    "away_win": ("match_result", "2"),
    "double_chance_1x": ("double_chance", "1X"),
    "double_chance_x2": ("double_chance", "X2"),
    "double_chance_12": ("double_chance", "12"),
    "btts_yes": ("btts", "YES"),
    "btts_no": ("btts", "NO"),
    "dnb_home": ("draw_no_bet", "1"),
    "dnb_away": ("draw_no_bet", "2"),
}

_OVER_UNDER_KEY_RE = re.compile(r"^(over|under)_(\d+)_(\d+)$")
_TEAM_TOTAL_KEY_RE = re.compile(r"^(home|away)_over_(\d+)_(\d+)$")
_EITHER_TOTAL_KEY_RE = re.compile(r"^either_team_over_(\d+)_(\d+)$")
_AH_KEY_RE = re.compile(r"^ah_(home_minus|away_plus)_(\d+)_(\d+)$")
_COUNT_KEY_RE = re.compile(
    r"^(corners|cards)(?:_(home|away))?_(over|under)_(\d+)_(\d+)$"
)


def _parse_line(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        line = abs(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return line if line > 0 else None


def _line_suffix(line: Optional[float]) -> Optional[str]:
    if line is None:
        return None
    text = f"{line:g}"
    return text.replace(".", "_")


def _from_key_parts(whole: str, frac: str) -> float:
    return float(f"{int(whole)}.{frac}")


def _line_from_label(label: str) -> Optional[float]:
    matches = _LINE_RE.findall(label)
    if not matches:
        return None
    return _parse_line(matches[-1])


def build_model_key(family: Optional[str], selection: Optional[str], line: Optional[float]) -> Optional[str]:
    """Return the probability-model key for a selection, or None if unpriceable."""
    if not family or not selection:
        return None
    suffix = _line_suffix(line)
    selection = selection.upper()

    if family == "match_result":
        return {"1": "home_win", "X": "draw", "2": "away_win"}.get(selection)
    if family == "double_chance":
        return {"1X": "double_chance_1x", "X2": "double_chance_x2", "12": "double_chance_12"}.get(selection)
    if family == "btts":
        return {"YES": "btts_yes", "NO": "btts_no"}.get(selection)
    if family == "draw_no_bet":
        return {"1": "dnb_home", "2": "dnb_away"}.get(selection)

    if family == "total_goals":
        if selection not in ("OVER", "UNDER") or not suffix:
            return None
        return f"{selection.lower()}_{suffix}"

    if family in ("team_total_home", "team_total_away"):
        if selection not in ("OVER", "UNDER") or not suffix:
            return None
        side = "home" if family.endswith("home") else "away"
        if selection == "OVER":
            return f"{side}_over_{suffix}"
        return None

    if family == "either_team_goals":
        if selection == "OVER" and suffix:
            return f"either_team_over_{suffix}"
        return None

    if family == "asian_handicap":
        if not suffix:
            return None
        if selection == "HOME_MINUS":
            return f"ah_home_minus_{suffix}"
        if selection == "AWAY_PLUS":
            return f"ah_away_plus_{suffix}"
        return None

    if family in ("total_corners", "team_corners_home", "team_corners_away"):
        if selection not in ("OVER", "UNDER") or not suffix:
            return None
        side = {"total_corners": "", "team_corners_home": "_home", "team_corners_away": "_away"}[family]
        return f"corners{side}_{selection.lower()}_{suffix}"

    if family in ("total_cards", "team_cards_home", "team_cards_away", "yellow_cards"):
        if selection not in ("OVER", "UNDER") or not suffix:
            return None
        prefix = "yellow_cards" if family == "yellow_cards" else "cards"
        side = {"total_cards": "", "team_cards_home": "_home", "team_cards_away": "_away"}.get(family, "")
        return f"{prefix}{side}_{selection.lower()}_{suffix}"

    if family == "clean_sheet":
        return {"HOME": "clean_sheet_home", "AWAY": "clean_sheet_away"}.get(selection)
    if family == "win_to_nil":
        return {"HOME": "win_to_nil_home", "AWAY": "win_to_nil_away"}.get(selection)
    if family == "correct_score":
        score = _SCORE_RE.match(selection)
        if not score:
            return None
        return f"correct_score_{int(score.group(1))}_{int(score.group(2))}"
    if family == "odd_even":
        return {"ODD": "odd_even_odd", "EVEN": "odd_even_even"}.get(selection)
    if family == "goal_range":
        text = selection.replace("+", "_plus").replace("-", "_").replace(" ", "")
        return f"goal_range_{text.lower()}"

    if family == "result_btts":
        return f"result_btts_{selection.lower()}"
    if family == "result_goals":
        return f"result_goals_{selection.lower()}"
    if family == "double_chance_goals":
        return f"dc_goals_{selection.lower()}"
    if family == "btts_goals":
        return f"btts_goals_{selection.lower()}"

    return None


def normalize_model_key(key: str) -> NormalizedSelection:
    """Normalize a key already stored in ``fixture.market_odds_json``."""
    text = str(key).strip()

    if text in _MODEL_KEY_SELECTIONS:
        family, selection = _MODEL_KEY_SELECTIONS[text]
        definition = get_definition(family)
        return NormalizedSelection(
            family=family,
            selection=selection,
            line=None,
            settlement_group=definition.settlement_group if definition else None,
            model_key=text,
            raw_name=text,
            raw_selection=selection,
        )

    match = _OVER_UNDER_KEY_RE.match(text)
    if match:
        return _selection_result("total_goals", match.group(1).upper(), _from_key_parts(match.group(2), match.group(3)), text)

    match = _TEAM_TOTAL_KEY_RE.match(text)
    if match:
        family = "team_total_home" if match.group(1) == "home" else "team_total_away"
        return _selection_result(family, "OVER", _from_key_parts(match.group(2), match.group(3)), text)

    match = _EITHER_TOTAL_KEY_RE.match(text)
    if match:
        return _selection_result("either_team_goals", "OVER", _from_key_parts(match.group(1), match.group(2)), text)

    match = _AH_KEY_RE.match(text)
    if match:
        selection = "HOME_MINUS" if match.group(1) == "home_minus" else "AWAY_PLUS"
        return _selection_result("asian_handicap", selection, _from_key_parts(match.group(2), match.group(3)), text)

    match = _COUNT_KEY_RE.match(text)
    if match:
        prefix, side, direction, whole, frac = match.groups()
        if prefix == "corners":
            family = "total_corners" if not side else f"team_corners_{side}"
        else:
            family = "total_cards" if not side else f"team_cards_{side}"
        return _selection_result(family, direction.upper(), _from_key_parts(whole, frac), text)

    return NormalizedSelection(None, None, None, None, None, text, text)


def _selection_result(
    family: str,
    selection: str,
    line: Optional[float],
    raw_name: str,
    raw_selection: Optional[str] = None,
    raw_handicap: Optional[str] = None,
) -> NormalizedSelection:
    definition = get_definition(family)
    return NormalizedSelection(
        family=family,
        selection=selection,
        line=line,
        settlement_group=definition.settlement_group if definition else None,
        model_key=build_model_key(family, selection, line),
        raw_name=raw_name,
        raw_selection=raw_selection or selection,
        raw_handicap=raw_handicap,
    )


def _detect_family(text: str) -> Optional[str]:
    t = text.lower()

    corners = "corner" in t
    cards = "card" in t or "booking" in t
    first_half = "first half" in t or "1st half" in t or "1h" in t
    second_half = "second half" in t or "2nd half" in t or "2h" in t
    player = "player" in t or "goalscorer" in t or "assist" in t or "goalkeeper" in t

    if corners:
        if first_half:
            return "first_half_corners"
        if second_half:
            return "second_half_corners"
        if "most" in t:
            return "most_corners"
        if "race" in t:
            return "corner_race"
        if "handicap" in t:
            return "corner_handicap"
        if "home" in t:
            return "team_corners_home"
        if "away" in t:
            return "team_corners_away"
        return "total_corners"

    if cards or player and "card" in t:
        if player and not first_half and not second_half:
            return "player_card"
        if first_half:
            return "first_half_cards"
        if second_half:
            return "second_half_cards"
        if "handicap" in t:
            return "card_handicap"
        if "red" in t:
            return "red_cards"
        if "yellow" in t:
            return "yellow_cards"
        if "home" in t:
            return "team_cards_home"
        if "away" in t:
            return "team_cards_away"
        return "total_cards"

    if player:
        if "goalscorer" in t or "to score" in t:
            return "player_goalscorer"
        if "shot on target" in t:
            return "player_shots_on_target"
        if "shot" in t:
            return "player_shots"
        if "assist" in t:
            return "player_assists"
        if "goalkeeper" in t and "save" in t:
            return "goalkeeper_saves"

    if "result" in t and ("both teams" in t or "btts" in t):
        return "result_btts"
    if ("double chance" in t or "dc" in t) and ("goal" in t or "over" in t or "under" in t):
        return "double_chance_goals"
    if ("both teams" in t or "btts" in t) and ("over" in t or "under" in t or "goal" in t) and "first half" not in t:
        return "btts_goals"
    if "both teams" in t or "btts" in t:
        return "btts_first_half" if first_half else "btts_second_half" if second_half else "btts"

    if "double chance" in t:
        return "double_chance"
    if "draw no bet" in t:
        return "draw_no_bet"
    if "correct score" in t or _SCORE_RE.match(t):
        return "correct_score"
    if "half time" in t and "full time" in t or "ht/ft" in t or "ht-ft" in t:
        return "half_time_full_time"
    if "to qualify" in t or "qualification" in t:
        return "to_qualify"
    if "clean sheet" in t:
        return "clean_sheet"
    if "win to nil" in t:
        return "win_to_nil"
    if "odd" in t and "even" in t:
        return "odd_even"
    if "first goal" in t:
        return "first_goal" if "timing" not in t else "first_goal_timing"
    if "last goal" in t:
        return "last_goal"
    if "asian" in t and "handicap" in t:
        return "asian_handicap"
    if "european handicap" in t:
        return "european_handicap"

    if first_half and ("goal" in t or "over" in t or "under" in t):
        if "result" in t or "1x2" in t:
            return "first_half_result"
        return "first_half_goals"
    if second_half and ("goal" in t or "over" in t or "under" in t):
        if "result" in t or "1x2" in t:
            return "second_half_result"
        return "second_half_goals"

    if "shot on target" in t:
        return "shots_on_target"
    if "total shots" in t or ("shot" in t and "player" not in t):
        return "total_shots"
    if "offsides" in t:
        return "offsides"
    if "tackles" in t:
        return "tackles"
    if "fouls" in t:
        return "fouls"
    if "possession" in t:
        return "possession"
    if "saves" in t and "goalkeeper" not in t:
        return "saves"

    if "1x2" in t or "match result" in t or "match winner" in t or t.strip() in ("result", "winner"):
        return "match_result"
    if "team total" in t or ("team goals" in t):
        if "home" in t:
            return "team_total_home"
        if "away" in t:
            return "team_total_away"
    if "over" in t or "under" in t or "total goals" in t or "goals" in t:
        return "total_goals"
    return None


_SELECTION_ALIASES = {
    "1": "1", "HOME": "1", "H": "1",
    "X": "X", "DRAW": "X", "D": "X",
    "2": "2", "AWAY": "2", "A": "2",
    "YES": "YES", "Y": "YES",
    "NO": "NO", "N": "NO",
    "OVER": "OVER", "O": "OVER",
    "UNDER": "UNDER", "U": "UNDER",
    "1X": "1X", "X2": "X2", "12": "12",
    "HOME_MINUS": "HOME_MINUS", "AWAY_PLUS": "AWAY_PLUS",
    "ODD": "ODD", "EVEN": "EVEN",
}


def _normalize_selection(label: str, family: Optional[str]) -> Optional[str]:
    text = str(label).strip().upper()
    if family == "match_result":
        return {"1": "1", "HOME": "1", "H": "1", "X": "X", "DRAW": "X", "D": "X",
                "2": "2", "AWAY": "2", "A": "2"}.get(text)
    if family == "draw_no_bet":
        return {"1": "1", "HOME": "1", "2": "2", "AWAY": "2"}.get(text)
    if family in ("total_goals", "team_total_home", "team_total_away", "either_team_goals",
                  "total_corners", "team_corners_home", "team_corners_away",
                  "total_cards", "team_cards_home", "team_cards_away", "yellow_cards",
                  "first_half_goals", "second_half_goals", "first_half_corners",
                  "second_half_corners", "first_half_cards", "second_half_cards"):
        return {"OVER": "OVER", "O": "OVER", "UNDER": "UNDER", "U": "UNDER"}.get(text)
    if family in ("btts", "btts_first_half", "btts_second_half"):
        return {"YES": "YES", "Y": "YES", "NO": "NO", "N": "NO"}.get(text)
    if family in ("clean_sheet", "win_to_nil"):
        return {"HOME": "HOME", "H": "HOME", "1": "HOME", "AWAY": "AWAY", "A": "AWAY", "2": "AWAY"}.get(text)
    if family == "double_chance":
        return {"1X": "1X", "X2": "X2", "12": "12", "HOME OR DRAW": "1X",
                "DRAW OR AWAY": "X2", "HOME OR AWAY": "12"}.get(text)
    if family == "odd_even":
        return {"ODD": "ODD", "EVEN": "EVEN"}.get(text)
    if family == "asian_handicap":
        if text in ("1", "HOME", "H"):
            return "HOME_MINUS"
        if text in ("2", "AWAY", "A"):
            return "AWAY_PLUS"
        return None
    if family in ("match_result", "player_goalscorer", "correct_score", "half_time_full_time"):
        return text or None
    return _SELECTION_ALIASES.get(text, text or None)


def normalize_raw_selection(
    market_name: str,
    selection_label: str,
    handicap: Any = None,
) -> NormalizedSelection:
    """Map a bookmaker market name + selection label onto the catalogue."""
    market_text = str(market_name or "").strip()
    label_text = str(selection_label or "").strip()
    combined = f"{market_text} {label_text}".strip()

    family = _detect_family(combined)
    selection = _normalize_selection(label_text, family)
    line = _parse_line(handicap) or _line_from_label(label_text) or _line_from_label(market_text)

    if family == "asian_handicap" and selection in ("HOME_MINUS", "AWAY_PLUS"):
        signed = str(handicap).strip() if handicap is not None else ""
        if selection == "HOME_MINUS" and not signed.startswith("-"):
            selection = None
        if selection == "AWAY_PLUS" and not signed.startswith("+"):
            selection = None

    definition = get_definition(family) if family else None
    model_key = None
    if definition and definition.model:
        model_key = build_model_key(family, selection, line)

    return NormalizedSelection(
        family=family,
        selection=selection,
        line=line,
        settlement_group=definition.settlement_group if definition else None,
        model_key=model_key,
        raw_name=market_text,
        raw_selection=label_text,
        raw_handicap=str(handicap) if handicap is not None else None,
    )
