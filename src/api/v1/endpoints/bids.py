import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import Asset, MarketBid, BidMarginOverride, MarketPrice, MarketBidSocFeasibility
from src.core.security import RoleChecker
import src.modules.market_data_service.data_manager as dm
from src.modules.bidding_service.services import (
    generate_bids_for_date, settle_bids_for_date, get_margin_pct, DEFAULT_MARGIN_PCT, _bid_to_dict,
    build_daily_action_summary,
)
from src.core.time_utils import kyiv_to_utc, kyiv_day_bounds, utc_to_kyiv

router = APIRouter()


class MarginOverrideModel(BaseModel):
    asset_id: str
    date: str
    margin_pct: float


class GenerateBidsRequest(BaseModel):
    asset_id: str
    date: str
    margin_pct: Optional[float] = None
    # 2026-08-26: свідомий вихід із заморозки минулих годин — див.
    # RunOptimizationRequest.force_full_day (optimization.py).
    force_full_day: Optional[bool] = False


class SettleBidsRequest(BaseModel):
    asset_id: str
    date: str


class AcknowledgeIdmFallbackRequest(BaseModel):
    asset_id: str
    date: str
    hour: int  # реальна київська година (0-23) — той самий патерн, що GenerateBidsRequest/SettleBidsRequest


@router.get("/margin", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_margin(asset_id: str, date: str):
    """Ручна маржа диспетчера на добу (bid_margin_overrides), або дефолт, якщо не збережено."""
    db = SessionLocal()
    try:
        target_dt = kyiv_to_utc(date, 0)
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
        target_dt = kyiv_to_utc(req.date, 0)
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
        target_dt = kyiv_to_utc(date, 0)
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
        day_start, day_end = kyiv_day_bounds(date)
        bids = db.query(MarketBid).filter(
            MarketBid.asset_id == asset_id,
            MarketBid.timestamp >= day_start,
            MarketBid.timestamp < day_end,
        ).order_by(MarketBid.timestamp).all()
        if not bids:
            raise HTTPException(status_code=404, detail="Заявок на цю дату ще не згенеровано")
        soc_rows = db.query(MarketBidSocFeasibility).filter(
            MarketBidSocFeasibility.asset_id == asset_id,
            MarketBidSocFeasibility.timestamp >= day_start,
            MarketBidSocFeasibility.timestamp < day_end,
        ).all()
        soc_map = {r.timestamp: r.soc_feasible for r in soc_rows}
        return {"date": date, "asset_id": asset_id, "bids": [_bid_to_dict(b, soc_feasible=soc_map.get(b.timestamp)) for b in bids]}
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
        target_dt = kyiv_to_utc(date, 0)
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
        target_dt = kyiv_to_utc(req.date, 0)
        result = generate_bids_for_date(db, asset, target_dt, margin_pct=req.margin_pct, force_full_day=req.force_full_day or False)
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
        target_dt = kyiv_to_utc(req.date, 0)
        day_start, day_end = kyiv_day_bounds(req.date)

        # Реальна київська доба (не наївна UTC) — CLAUDE.md п.26/27: старе
        # вікно `[target_dt, target_dt+1day)` різало суміш хвоста доби D і
        # голови доби D+1, тож заявки звірялись проти ціни ІНШОЇ реальної
        # години/доби. Ключуємо за реальною київською годиною скрізь.
        rows = db.query(MarketPrice).filter(
            MarketPrice.timestamp >= day_start,
            MarketPrice.timestamp < day_end,
        ).order_by(MarketPrice.timestamp).all()
        actual_by_hour = {utc_to_kyiv(r.timestamp).hour: r.price_uah for r in rows}

        if len(actual_by_hour) != 24:
            df_month = dm.fetch_oree_prices_for_month(day_start.month, day_start.year)
            df_month_next = dm.fetch_oree_prices_for_month(day_end.month, day_end.year)
            import pandas as pd
            if not df_month_next.empty:
                df_month = pd.concat([df_month, df_month_next]).drop_duplicates(subset=['Datetime']) if not df_month.empty else df_month_next
            if not df_month.empty:
                df_month['Datetime'] = pd.to_datetime(df_month['Datetime'])
                df_day = df_month[
                    (df_month['Datetime'] >= day_start) & (df_month['Datetime'] < day_end)
                ]
                actual_by_hour = {utc_to_kyiv(dt.to_pydatetime()).hour: price for dt, price in zip(df_day['Datetime'], df_day['Price'])}

        if len(actual_by_hour) != 24:
            raise HTTPException(status_code=404, detail=f"Реальна ціна РДН на {req.date} ще не опублікована (є {len(actual_by_hour)}/24 годин)")

        result = settle_bids_for_date(db, asset, target_dt, actual_by_hour)
        if result['status'] != 'ok':
            raise HTTPException(status_code=400, detail=result['message'])
        return result
    finally:
        db.close()


@router.post("/idm-fallback/acknowledge", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def acknowledge_idm_fallback(req: AcknowledgeIdmFallbackRequest):
    """
    Диспетчер вручну підтверджує, що сам розібрався з ВДР-фолбеком для цієї
    години (подав сам через кабінет OREE, або свідомо вирішив нічого не
    робити) — "настроюваний сценарій віртуального диспетчера", 2026-08-26.
    Авто-подача (`submit_idm_fallback_bids_for_date`) після цього НЕ займає
    цю годину, навіть якщо `idm_external_order_id` ще порожній.
    """
    db = SessionLocal()
    try:
        ts = kyiv_to_utc(req.date, req.hour)
        bid = db.query(MarketBid).filter(
            MarketBid.asset_id == req.asset_id, MarketBid.timestamp == ts,
        ).first()
        if not bid:
            raise HTTPException(status_code=404, detail="Заявку на цю годину не знайдено")
        bid.idm_fallback_acknowledged = True
        db.commit()
        return {'status': 'ok', 'bid': _bid_to_dict(bid)}
    finally:
        db.close()
