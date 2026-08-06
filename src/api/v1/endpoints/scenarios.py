from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from src.modules.reporting_service.services import ReportingService
from src.modules.tariff_service.services import TariffService
from src.core.security import RoleChecker

router = APIRouter()

class PaybackRequest(BaseModel):
    capex_uah: float
    yearly_revenue_base_uah: float
    yearly_opex_uah: Optional[float] = 0.0
    yearly_traded_volume_mwh: Optional[float] = 0.0  # для реального тарифу OREE (грн/МВт·год); 0.0 = внесок не рахується поза фіксованим місячним
    discount_rate: Optional[float] = 0.12
    lifetime_years: Optional[int] = 10
    pessimistic_risk_factor: Optional[float] = 0.20 # 20% drop in revenue

@router.post("/payback", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def calculate_payback(req: PaybackRequest):
    if req.capex_uah <= 0 or req.yearly_revenue_base_uah <= 0:
        raise HTTPException(status_code=400, detail="CAPEX and revenue must be positive numbers")

    base_opex = req.yearly_opex_uah if req.yearly_opex_uah > 0 else (req.capex_uah * 0.02) # 2% Capex as opex default
    oree_fee = TariffService.calculate_oree_participation_fee_uah(req.yearly_traded_volume_mwh)

    # Calculate base economics
    base_metrics = ReportingService.calculate_project_economics(
        capex_uah=req.capex_uah,
        yearly_revenue_uah=req.yearly_revenue_base_uah,
        yearly_opex_uah=base_opex,
        oree_participation_fee_uah=oree_fee,
        discount_rate=req.discount_rate,
        lifetime_years=req.lifetime_years
    )

    # Calculate pessimistic economics
    pess_revenue = req.yearly_revenue_base_uah * (1.0 - req.pessimistic_risk_factor)
    pess_metrics = ReportingService.calculate_project_economics(
        capex_uah=req.capex_uah,
        yearly_revenue_uah=pess_revenue,
        yearly_opex_uah=base_opex,
        oree_participation_fee_uah=oree_fee,
        discount_rate=req.discount_rate,
        lifetime_years=req.lifetime_years
    )
    
    return {
        "metrics": {
            "discount_rate_pct": req.discount_rate * 100,
            "lifetime_years": req.lifetime_years,
            "base": base_metrics,
            "pessimistic": pess_metrics
        }
    }
