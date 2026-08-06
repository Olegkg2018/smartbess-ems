import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import Asset, MarketBid, BidMarginOverride, MarketPrice
from src.core.security import RoleChecker
import src.modules.market_data_service.data_manager as dm
from src.modules.bidding_service.services import (
    generate_bids_for_date, settle_bids_for_date, get_margin_pct, DEFAULT_MARGIN_PCT, _bid_to_dict,
    build_daily_action_summary,
)

router = APIRouter()


class MarginOverrideModel(BaseModel):
    asset_id: str
    date: str
    margin_pct: float


class GenerateBidsRequest(BaseModel):
    asset_id: str
    date: str
    margin_pct: Optional[float] = None


class SettleBidsRequest(BaseModel):
    asset_id: str
    date: str


@router.get("/margin", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_margin(asset_id: str, date: str):
    """Ручна маржа диспетчера на добу (bid_margin_overrides), або дефолт, якщо не збережено."""
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        override = db.query(BidMarginOverride).filter(
            BidMarginOverride.asset_id == asset_id, BidMarginOverride.date == target_dt,
        ).first()
        return {
            "date": date, "margin_pct": override.margin_pct if override else DEFAULT_MARGIN_PCT,
            "source": "manual" if override else "default",
        }
    finally:
        db.close()


@router.post("/margin", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_margin(req: MarginOverrideModel):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')
        row = db.query(BidMarginOverride).filter(
            BidMarginOverride.asset_id == req.asset_id, BidMarginOverride.date == target_dt,
        ).first()
        if not row:
            row = BidMarginOverride(asset_id=req.asset_id, date=target_dt)
            db.add(row)
        row.margin_pct = req.margin_pct
        db.commit()
        return {"status": "success", "message": f"Маржу заявки на {req.date} збережено: {req.margin_pct}%."}
    finally:
        db.close()


@router.delete("/margin", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def clear_margin(asset_id: str, date: str):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        db.query(BidMarginOverride).filter(
            BidMarginOverride.asset_id == asset_id, BidMarginOverride.date == target_dt,
        ).delete()
        db.commit()
        return {"status": "success", "message": f"Ручну маржу на {date} прибрано, знову дефолт {DEFAULT_MARGIN_PCT}%."}
    finally:
        db.close()


@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def list_bids(asset_id: str, date: str):
    """Список заявок (поданих і, якщо вже звірені, з фактом виконання) на добу."""
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        bids = db.query(MarketBid).filter(
            MarketBid.asset_id == asset_id,
            MarketBid.timestamp >= target_dt,
            MarketBid.timestamp < target_dt + datetime.timedelta(days=1),
        ).order_by(MarketBid.timestamp).all()
        if not bids:
            raise HTTPException(status_code=404, detail="Заявок на цю дату ще не згенеровано")
        return {"date": date, "asset_id": asset_id, "bids": [_bid_to_dict(b) for b in bids]}
    finally:
        db.close()


@router.get("/action-summary", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_action_summary(asset_id: str, date: str):
    """Синтезований список дій диспетчеру на конкретну дату — тільки читання існуючих MarketBid."""
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        return build_daily_action_summary(db, asset, target_dt)
    finally:
        db.close()


@router.post("/generate", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def generate_bids(req: GenerateBidsRequest):
    """
    Формує заявки РДН на req.date з уже порахованого MILP-графіка + прогнозу,
    зсунутих на маржу (ручну, якщо збережена, інакше дефолт). Диспетчер сам
    вносить отримані ціни/обсяги в кабінет oree.com.ua до 12:00 — автоматичної
    подачі через API поки немає (майбутня робота).
    """
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == req.asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')
        result = generate_bids_for_date(db, asset, target_dt, margin_pct=req.margin_pct)
        if result['status'] != 'ok':
            raise HTTPException(status_code=400, detail=result['message'])
        return result
    finally:
        db.close()


@router.post("/settle", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def settle_bids(req: SettleBidsRequest):
    """
    Звіряє подані заявки з РЕАЛЬНОЮ ціною РДН (спочатку локальна БД
    MarketPrice, як і /forecast/actual, інакше живий запит до oree.com.ua) —
    визначає, які заявки зіграли, рахує реальний P&L і пропонує ВДР для тих,
    що не зіграли.
    """
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == req.asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')

        rows = db.query(MarketPrice).filter(
            MarketPrice.timestamp >= target_dt,
            MarketPrice.timestamp < target_dt + datetime.timedelta(days=1),
        ).order_by(MarketPrice.timestamp).all()
        actual_by_hour = {r.timestamp.hour: r.price_uah for r in rows}

        if len(actual_by_hour) != 24:
            df_month = dm.fetch_oree_prices_for_month(target_dt.month, target_dt.year)
            if not df_month.empty:
                import pandas as pd
                df_month['Datetime'] = pd.to_datetime(df_month['Datetime'])
                df_day = df_month[
                    (df_month['Datetime'] >= target_dt) & (df_month['Datetime'] < target_dt + datetime.timedelta(days=1))
                ]
                actual_by_hour = {dt.hour: price for dt, price in zip(df_day['Datetime'], df_day['Price'])}

        if len(actual_by_hour) != 24:
            raise HTTPException(status_code=404, detail=f"Реальна ціна РДН на {req.date} ще не опублікована (є {len(actual_by_hour)}/24 годин)")

        result = settle_bids_for_date(db, asset, target_dt, actual_by_hour)
        if result['status'] != 'ok':
            raise HTTPException(status_code=400, detail=result['message'])
        return result
    finally:
        db.close()
