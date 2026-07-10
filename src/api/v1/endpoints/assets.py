from fastapi import APIRouter, Depends

from src.database.session import SessionLocal
from src.database.models import Asset
from src.core.security import RoleChecker

router = APIRouter()


@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def list_assets():
    """Реальний список активів з БД — фронтенд більше не хардкодить asset_id."""
    db = SessionLocal()
    try:
        assets = db.query(Asset).all()
        return {
            "assets": [
                {
                    "id": a.id,
                    "name": a.name,
                    "capacity_mwh": a.capacity_mwh,
                    "power_mw": a.power_mw,
                }
                for a in assets
            ]
        }
    finally:
        db.close()
