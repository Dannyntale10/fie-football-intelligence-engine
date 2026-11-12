"""API routers for the restructured platform."""
from app.api.combos import router as combos_router
from app.api.markets import router as markets_router
from app.api.performance import router as performance_router
from app.api.predictions import router as predictions_router
from app.api.sources import router as sources_router
from app.api.system import router as system_router

ALL_ROUTERS = (
    combos_router,
    markets_router,
    predictions_router,
    performance_router,
    sources_router,
    system_router,
)

__all__ = ["ALL_ROUTERS"]
