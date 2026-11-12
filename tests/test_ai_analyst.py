from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ai import ai_analyst, ai_insights

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache():
    ai_analyst._CACHE.clear()
    yield
    ai_analyst._CACHE.clear()


def _slip(validated: bool = True, include_learning: bool = True) -> dict:
    return {
        "matchday_label": "Thursday, 12 Nov 2026",
        "params": {"selection_mode": "ev"},
        "best_combo": {
            "rank": 1,
            "score": 86.2,
            "grade": "A",
            "legs_count": 2,
            "total_odds": 3.24,
            "combined_probability": 0.6312,
            "avg_leg_probability": 0.794,
            "min_leg_probability": 0.712,
            "expected_value_percent": 104.5,
            "edge_percent": 32.3,
            "kelly_stake_percent": 2.4,
            "legs": [
                {
                    "match": "Spain vs Cyprus",
                    "selection": "Spain to Win",
                    "odds": 1.20,
                    "probability": 0.905,
                    "edge_percent": 8.0,
                    "expected_value_percent": 8.6,
                    "odds_source": "market",
                    "calibrated": True,
                    "model_source": "team_ratings",
                    "kickoff_eat": "Thu 12 Nov, 19:00 EAT",
                },
                {
                    "match": "England vs Albania",
                    "selection": "England -0.5 Handicap",
                    "odds": 2.70,
                    "probability": 0.697,
                    "edge_percent": 12.0,
                    "expected_value_percent": 88.2,
                    "odds_source": "market",
                    "calibrated": True,
                    "model_source": "team_ratings",
                    "kickoff_eat": "Thu 12 Nov, 21:45 EAT",
                },
            ],
        },
        "learning": {
            "active": include_learning,
            "version": "learn-1" if include_learning else None,
            "trained_at": "2026-11-12T15:09:21+00:00",
            "applied_markets": ["home_win", "away_win"] if include_learning else [],
            "calibrated_legs": 2 if include_learning else 0,
            "edge_premiums": {},
        },
        "calibration": {
            "validated": validated,
            "verdict": "model_beats_market" if validated else "market_beats_model",
            "n_matches": 3576,
            "model_brier": 0.5600,
            "market_brier": 0.5663,
        },
        "ranking": {"total_combos_ranked": 466, "total_legs_ranked": 17},
        "diagnostics": {
            "fixtures_in_scope": 8,
            "fixtures_with_priced_markets": 8,
            "rated_fixtures": 8,
            "candidate_legs": 17,
            "synthetic_legs_excluded": 0,
        },
        "blocked_reason": None,
    }


def test_facts_from_slip_capture_engine_numbers():
    facts = ai_analyst.facts_from_slip(_slip())

    assert facts["legs_count"] == 2
    assert facts["joint_probability_percent"] == 63.1
    assert facts["rank"] == 1
    assert facts["rank_score"] == 86.2
    assert facts["calibration"]["validated"] is True
    assert facts["learning"]["version"] == "learn-1"
    assert len(facts["legs"]) == 2
    assert facts["legs"][0]["model_probability_percent"] == 90.5
    assert any("learn-1" in note for note in facts["notes"])


def test_rules_review_is_grounded_and_advisory():
    facts = ai_analyst.facts_from_slip(_slip(validated=False, include_learning=False))

    result = ai_analyst.review(facts, use_llm=False)

    assert result["mode"] == "rules"
    assert result["model"] is None
    assert result["advisory"] is True
    assert result["headline"]
    assert result["strengths"] and result["risks"] and result["checks"]
    assert result["confidence"] == "low"
    assert result["grounding"]["grounded"] is True
    assert any("not validated" in risk for risk in result["risks"])
    assert any("No learned calibration" in risk for risk in result["risks"])


def test_rules_review_confidence_raised_when_validated_and_learned():
    facts = ai_analyst.facts_from_slip(_slip())

    result = ai_analyst.review(facts, use_llm=False)

    assert result["confidence"] == "medium"


def test_grounding_flags_a_number_that_is_not_in_the_data():
    facts = ai_analyst.facts_from_slip(_slip())

    check = ai_analyst.check_grounding("This slip wins 99.9% of the time.", facts)

    assert check["grounded"] is False
    assert check["unmatched"] == [99.9]
    assert check["ratio"] < 1.0


def test_grounding_accepts_restated_engine_numbers():
    facts = ai_analyst.facts_from_slip(_slip())

    check = ai_analyst.check_grounding(
        "2 legs at 3.24 odds, joint probability 63.1%, expected value +104.5%.", facts
    )

    assert check["grounded"] is True
    assert check["cited_numbers"] == check["matched_numbers"]


def test_llm_review_is_used_and_grounded(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_insights,
        "call_llm",
        lambda *a, **k: (
            '{"headline": "2-leg slip at 3.24 odds with 63.1% joint probability.",'
            ' "strengths": ["Every leg uses a real price."],'
            ' "risks": ["Calibration covers 3576 matches."],'
            ' "checks": ["Confirm lineups."], "confidence": "medium"}',
            None,
        ),
    )

    result = ai_analyst.review(ai_analyst.facts_from_slip(_slip()))

    assert result["mode"] == "llm"
    assert result["model"]
    assert result["confidence"] == "medium"
    assert result["grounding"]["grounded"] is True
    assert result["llm_error"] is None


def test_llm_hallucination_is_flagged(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_insights,
        "call_llm",
        lambda *a, **k: (
            '{"headline": "A guaranteed 99.9% winner.", "strengths": [], '
            '"risks": [], "checks": [], "confidence": "high"}',
            None,
        ),
    )

    result = ai_analyst.review(ai_analyst.facts_from_slip(_slip()))

    assert result["mode"] == "llm"
    assert result["grounding"]["grounded"] is False
    assert 99.9 in result["grounding"]["unmatched"]


def test_broken_llm_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai_insights, "call_llm", lambda *a, **k: (None, "RuntimeError: boom"))

    result = ai_analyst.review(ai_analyst.facts_from_slip(_slip()))

    assert result["mode"] == "rules"
    assert result["llm_error"] == "RuntimeError: boom"
    assert result["grounding"]["grounded"] is True


def test_rules_ask_answers_risk_question():
    facts = ai_analyst.facts_from_slip(_slip(validated=False, include_learning=False))

    answer = ai_analyst.ask("What are the risks here?", facts, use_llm=False)

    assert answer["mode"] == "rules"
    assert "Risks" in answer["answer"]
    assert answer["grounding"]["grounded"] is True


def test_ask_uses_llm_when_available(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_insights,
        "call_llm",
        lambda *a, **k: ("The joint probability is 63.1% and rank is #1.", None),
    )

    answer = ai_analyst.ask("How strong is this slip?", ai_analyst.facts_from_slip(_slip()))

    assert answer["mode"] == "llm"
    assert answer["grounding"]["grounded"] is True


def test_ask_rejects_empty_or_oversized_question():
    facts = ai_analyst.facts_from_slip(_slip())

    assert "error" in ai_analyst.ask("   ", facts)
    assert "error" in ai_analyst.ask("x" * (ai_analyst.MAX_QUESTION + 1), facts)


# ── API ──────────────────────────────────────────────────────────────────────

def test_ai_status_endpoint():
    res = client.get("/ai/status")
    assert res.status_code == 200
    data = res.json()
    assert "llm_enabled" in data
    assert data["advisory"] is True
    assert data["mode"] in {"llm", "rules"}


def test_ai_review_endpoint_rejects_missing_slip():
    res = client.post("/ai/review", json={})
    assert res.status_code == 400


def test_ai_review_endpoint_returns_grounded_review():
    res = client.post("/ai/review", json={"slip": _slip()})
    assert res.status_code == 200
    data = res.json()
    assert data["advisory"] is True
    assert data["headline"]
    assert "grounding" in data
    assert data["facts"]["legs_count"] == 2


def test_ai_ask_endpoint_validates_and_answers():
    empty = client.post("/ai/ask", json={"question": "  ", "slip": _slip()})
    assert empty.status_code == 400

    res = client.post("/ai/ask", json={"question": "How much does Kelly size?", "slip": _slip()})
    assert res.status_code == 200
    data = res.json()
    assert data["question"] == "How much does Kelly size?"
    assert data["answer"]
    assert "grounding" in data
