from fastapi import APIRouter

from src.api.v1.endpoints import forecast, optimization, scenarios, reports, jobs, assets, notifications

api_router = APIRouter()

api_router.include_router(forecast.router, prefix="/forecast", tags=["forecasts"])
api_router.include_router(optimization.router, prefix="/optimization", tags=["optimization"])
api_router.include_router(scenarios.router, prefix="/scenarios", tags=["scenarios"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
