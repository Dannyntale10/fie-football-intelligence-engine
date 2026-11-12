from __future__ import annotations
"""
ml_ensemble.py – Poisson + Gradient Boosting Machine (GBM/XGB) ensemble model.

Blends Dixon-Coles Poisson model (60%) with a scikit-learn GradientBoostingClassifier (40%)
trained on historical ratings, goal differentials, and home advantage.
"""
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture
from app.models.team_rating import TeamRating

MODEL_DIR = Path(__file__).resolve().parents[3] / "models"
MODEL_PATH = MODEL_DIR / "ensemble_model.pkl"

_cached_model = None

def _get_team_rating(db: Session, team: str) -> Tuple[float, float, float]:
    """Get attack rating, defense rating, home boost for a team."""
    rating = db.scalar(
        select(TeamRating).where(TeamRating.team_name == team)
    )
    if rating:
        return (
            float(rating.attack_rating or 1.0),
            float(rating.defense_rating or 1.0),
            float(getattr(rating, "home_attack_boost", 0.0) or 0.0),
        )
    return 1.0, 1.0, 0.0

def _build_features(home_team: str, away_team: str, competition: str, kickoff_utc, db: Session) -> Optional[list]:
    """Build feature vector for a match."""
    h_att, h_def, h_boost = _get_team_rating(db, home_team)
    a_att, a_def, a_boost = _get_team_rating(db, away_team)

    league_home_avg, league_away_avg = 1.5, 1.2
    h_lambda = league_home_avg * h_att * (1.0 / max(a_def, 0.1)) * (1.0 + h_boost * 0.25)
    a_lambda = league_away_avg * a_att * (1.0 / max(h_def, 0.1))

    att_diff = h_att - a_att
    def_diff = h_def - a_def
    xg_diff = h_lambda - a_lambda

    month = kickoff_utc.month if (kickoff_utc and hasattr(kickoff_utc, "month")) else 6
    season_phase = ((month - 8) % 12) / 11.0

    return [
        h_att, h_def, a_att, a_def, h_lambda, a_lambda,
        att_diff, def_diff, xg_diff, h_boost, season_phase
    ]

def _outcome_label(home_score: int, away_score: int) -> int:
    """0 = Home win, 1 = Draw, 2 = Away win"""
    if home_score > away_score:
        return 0
    if home_score == away_score:
        return 1
    return 2

def train_ensemble(db: Session) -> Dict[str, Any]:
    """Train gradient-boosted ensemble classifier on historical matches."""
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
        import numpy as np
    except ImportError:
        return {"status": "error", "message": "scikit-learn is not installed."}

    finished = db.scalars(
        select(Fixture).where(
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
        ).order_by(Fixture.kickoff_utc)
    ).all()

    X, y = [], []
    for m in finished:
        feats = _build_features(m.home_team, m.away_team, m.competition or "", m.kickoff_utc, db)
        if feats is None:
            continue
        label = _outcome_label(m.home_score, m.away_score)
        X.append(feats)
        y.append(label)

    if len(X) < 40:
        return {"status": "error", "message": f"Not enough training samples ({len(X)} matches found; need 40+)."}

    X_arr = np.nan_to_num(np.array(X, dtype=float), nan=1.0, posinf=3.0, neginf=0.0)
    y_arr = np.array(y)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_arr)

    model = GradientBoostingClassifier(
        n_estimators=150,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
    model.fit(X_scaled, y_arr)

    cv_scores = cross_val_score(model, X_scaled, y_arr, cv=5, scoring="accuracy")
    cv_acc = float(cv_scores.mean())

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "scaler": scaler}, f)

    global _cached_model
    _cached_model = {"model": model, "scaler": scaler}

    return {
        "status": "success",
        "training_samples": len(X),
        "cv_accuracy_percent": round(cv_acc * 100, 1),
        "model_path": str(MODEL_PATH),
        "message": f"Ensemble successfully trained on {len(X)} matches! 5-Fold Cross-Val Accuracy: {cv_acc*100:.1f}%",
    }

def _load_model() -> Optional[Dict]:
    global _cached_model
    if _cached_model:
        return _cached_model
    if MODEL_PATH.exists():
        try:
            with open(MODEL_PATH, "rb") as f:
                _cached_model = pickle.load(f)
            return _cached_model
        except Exception:
            return None
    return None

def is_model_trained() -> bool:
    return MODEL_PATH.exists() or _cached_model is not None

def ensemble_predict(
    home_team: str,
    away_team: str,
    competition: str,
    kickoff_utc,
    db: Session,
    poisson_probs: Optional[Dict[str, float]] = None,
    blend_weight: float = 0.40,
) -> Dict[str, Any]:
    """Blend Poisson probabilities with Gradient Boosting Classifier."""
    p_hw = poisson_probs.get("home_win", 0.45) if poisson_probs else 0.45
    p_dr = poisson_probs.get("draw", 0.27) if poisson_probs else 0.27
    p_aw = poisson_probs.get("away_win", 0.28) if poisson_probs else 0.28

    model_data = _load_model()
    if model_data is None:
        return {
            "home_win": round(p_hw, 4),
            "draw": round(p_dr, 4),
            "away_win": round(p_aw, 4),
            "source": "poisson_baseline",
            "ensemble_active": False,
        }

    try:
        import numpy as np
        feats = _build_features(home_team, away_team, competition, kickoff_utc, db)
        X = np.nan_to_num(np.array([feats], dtype=float), nan=1.0, posinf=3.0, neginf=0.0)
        X_scaled = model_data["scaler"].transform(X)
        proba = model_data["model"].predict_proba(X_scaled)[0]

        ml_hw, ml_dr, ml_aw = float(proba[0]), float(proba[1]), float(proba[2])

        w_p = 1.0 - blend_weight
        w_m = blend_weight

        b_hw = w_p * p_hw + w_m * ml_hw
        b_dr = w_p * p_dr + w_m * ml_dr
        b_aw = w_p * p_aw + w_m * ml_aw

        tot = b_hw + b_dr + b_aw
        if tot > 0:
            b_hw /= tot
            b_dr /= tot
            b_aw /= tot

        return {
            "home_win": round(b_hw, 4),
            "draw": round(b_dr, 4),
            "away_win": round(b_aw, 4),
            "source": "ensemble_60p_40gbm",
            "ensemble_active": True,
            "ml_raw": {"home_win": round(ml_hw, 4), "draw": round(ml_dr, 4), "away_win": round(ml_aw, 4)},
            "poisson_raw": {"home_win": round(p_hw, 4), "draw": round(p_dr, 4), "away_win": round(p_aw, 4)},
        }
    except Exception:
        return {
            "home_win": round(p_hw, 4),
            "draw": round(p_dr, 4),
            "away_win": round(p_aw, 4),
            "source": "poisson_fallback",
            "ensemble_active": False,
        }
