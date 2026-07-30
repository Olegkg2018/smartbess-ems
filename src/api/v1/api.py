from fastapi import APIRouter

from src.api.v1.endpoints import forecast, optimization, scenarios, reports, jobs, assets, notifications, generation_adjustments, grid_stress, data_audit

api_router = APIRouter()

api_router.include_router(forecast.router, prefix="/forecast", tags=["forecasts"])
api_router.include_router(optimization.router, prefix="/optimization", tags=["optimization"])
api_router.include_router(scenarios.router, prefix="/scenarios", tags=["scenarios"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(generation_adjustments.router, prefix="/generation-adjustments", tags=["generation-adjustments"])
api_router.include_router(grid_stress.router, prefix="/grid-stress", tags=["grid-stress"])
api_router.include_router(data_audit.router, prefix="/data-audit", tags=["data-audit"])
