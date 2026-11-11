from __future__ import annotations
"""
ai_insights.py – Gemini AI-powered match previews & combo explanations.

Integrates Google Gemini API to generate natural-language commentary for:
  - Individual match previews (2-3 sentences based on Poisson probabilities)
  - Combo slip explanations (why this multi-bet was selected)
  - Agent scorecard narrative summary

Set the GEMINI_API_KEY environment variable to enable. Falls back gracefully
to rule-based text if the API key is not configured or the call fails.
"""
import os
from typing import Any, Dict, List, Optional

_GEMINI_AVAILABLE = False
_gemini_model = None

def _init_gemini():
    """Lazy-initialise the Gemini client on first use."""
    global _GEMINI_AVAILABLE, _gemini_model
    if _GEMINI_AVAILABLE:
        return True
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel("gemini-1.5-flash")
        _GEMINI_AVAILABLE = True
        return True
    except Exception:
        return False


def _call_gemini(prompt: str, max_tokens: int = 256) -> Optional[str]:
    """Call Gemini and return plain-text response, or None on failure."""
    if not _init_gemini():
        return None
    try:
        resp = _gemini_model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens, "temperature": 0.7},
        )
        return resp.text.strip() if resp.text else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def gemini_match_preview(
    home_team: str,
    away_team: str,
    competition: str,
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    home_lambda: float,
    away_lambda: float,
    kickoff_eat: str = "",
) -> str:
    """Generate a 2-3 sentence AI match preview using Poisson model outputs.

    Returns a Gemini-generated preview, or a compact rule-based fallback.
    """
    prompt = (
        f"You are a sharp football analyst. Write exactly 2–3 concise sentences previewing "
        f"the match: {home_team} vs {away_team} ({competition}). "
        f"Model says home win prob = {home_win_prob*100:.1f}%, draw = {draw_prob*100:.1f}%, "
        f"away win = {away_win_prob*100:.1f}%. Expected goals: {home_team} {home_lambda:.2f} xG, "
        f"{away_team} {away_lambda:.2f} xG. "
        f"Mention the key statistical edge clearly. Keep it punchy, data-driven, no fluff."
    )
    ai_text = _call_gemini(prompt, max_tokens=180)
    if ai_text:
        return f"🤖 **AI Preview:** {ai_text}"

    # Rule-based fallback
    fav = home_team if home_win_prob > away_win_prob else away_team
    fav_p = max(home_win_prob, away_win_prob)
    return (
        f"📊 **Model Preview:** {fav} are favourites at {fav_p*100:.0f}% win probability "
        f"({home_team} {home_lambda:.2f} xG vs {away_team} {away_lambda:.2f} xG). "
        f"Draw probability is {draw_prob*100:.0f}%. Dixon-Coles model in play."
    )


def gemini_combo_explanation(
    legs: List[Dict[str, Any]],
    total_odds: float,
    combined_prob: float,
    avg_prob: float,
    ev_percent: float,
    matchday: str,
) -> str:
    """Generate a plain-English explanation of why this combo was selected."""
    legs_text = "; ".join(
        f"{l['selection']} @ {l['odds']:.2f} ({l['probability']*100:.0f}% confidence)"
        for l in legs
    )
    prompt = (
        f"You are a sharp betting analyst. Explain in 3 sentences why this accumulator is a "
        f"strong selection for {matchday}. Legs: {legs_text}. "
        f"Combined odds: {total_odds:.2f} | Joint win probability: {combined_prob*100:.1f}% | "
        f"Avg team confidence: {avg_prob*100:.1f}% | Expected Value: +{ev_percent:.1f}%. "
        f"Focus on the mathematical edge, not hype. Be concise and professional."
    )
    ai_text = _call_gemini(prompt, max_tokens=200)
    if ai_text:
        return f"🤖 **AI Combo Analysis:** {ai_text}"

    # Rule-based fallback
    n = len(legs)
    return (
        f"📐 **Why this combo?** Each of the {n} legs carries {avg_prob*100:.0f}%+ individual "
        f"confidence from the Dixon-Coles model. At combined odds of {total_odds:.2f}, the "
        f"implied probability is only {(1/total_odds)*100:.1f}% — well below the model's "
        f"{combined_prob*100:.1f}% joint estimate, giving a +{ev_percent:.1f}% expected value edge."
    )


def gemini_agent_rating_narrative(
    total_fixtures: int,
    total_teams: int,
    historical_matches: int,
    win_rate: float,
    roi: float,
    model_name: str = "Dixon-Coles Bivariate Poisson",
) -> str:
    """Generate an AI summary of the agent's current capability rating."""
    prompt = (
        f"You are a quant betting analyst evaluating a football prediction AI engine. "
        f"Write 3 sentences summarising its strengths and weaknesses based on these stats: "
        f"Model: {model_name}. Data: {historical_matches} historical matches, {total_teams} team ratings. "
        f"Backtest win rate: {win_rate:.1f}%, backtest ROI: {roi:+.1f}%. "
        f"Give an honest, data-driven assessment. No hype."
    )
    ai_text = _call_gemini(prompt, max_tokens=200)
    if ai_text:
        return f"🤖 **AI Assessment:** {ai_text}"
    return ""


def is_gemini_enabled() -> bool:
    """Return True if Gemini API key is configured."""
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())
