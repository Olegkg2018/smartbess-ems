import datetime
from fastapi import APIRouter, Depends, HTTPException

from src.database.session import SessionLocal
from src.database.models import Asset, BessTelemetry
from src.core.security import RoleChecker
from src.modules.scada_service.scada_service import load_bess_connection_settings

router = APIRouter()

# Скільки часу останній запис BessTelemetry вважається "свіжим". Цикл
# поллінгу в scada_service.py пише запис раз на хвилину (округлено до
# секунди=0) — 120с дає запас на один пропущений цикл, перш ніж чесно
# показати "немає зв'язку" замість застарілих даних.
SCADA_TELEMETRY_FRESHNESS_SECONDS = 120


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


@router.get("/{asset_id}/scada-status", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_scada_status(asset_id: str):
    """
    Реальний стан SCADA-телеметрії — раніше фронтенд показував захардкоджені
    числа (20.0%, -150.0 кВт, 24.8°C, 99.85%) і статичний бейдж "ONLINE"
    незалежно від того, чи реально живий симулятор. Тепер віддає останній
    запис BessTelemetry і честно каже, застарілий він чи ні.
    """
    connection_type = load_bess_connection_settings()['connection_type']
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        tel = (
            db.query(BessTelemetry)
            .filter(BessTelemetry.asset_id == asset_id)
            .order_by(BessTelemetry.timestamp.desc())
            .first()
        )

        if tel is None:
            return {
                "connected": False,
                "connection_type": connection_type,
                "timestamp": None,
                "soc_pct": None,
                "soc_mwh": None,
                "power_mw": None,
                "battery_temp_c": None,
                "soh_pct": None,
                "system_status": None,
            }

        age_seconds = (datetime.datetime.utcnow() - tel.timestamp).total_seconds()
        soc_pct = (tel.current_soc_mwh / asset.capacity_mwh * 100.0) if asset.capacity_mwh > 0 else None

        return {
            "connected": age_seconds < SCADA_TELEMETRY_FRESHNESS_SECONDS,
            "connection_type": connection_type,
            "timestamp": tel.timestamp.isoformat() + "Z",
            "soc_pct": soc_pct,
            "soc_mwh": tel.current_soc_mwh,
            "power_mw": tel.current_power_mw,
            "battery_temp_c": tel.battery_temp_c,
            "soh_pct": tel.soh_pct,
            "system_status": tel.system_status,
        }
    finally:
        db.close()
