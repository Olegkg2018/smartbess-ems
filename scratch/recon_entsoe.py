"""Recon: підтверджує, що ENTSO-E Transparency Platform з реальним токеном
користувача віддає:
- AGGREGATED_GENERATION_PER_TYPE для України (domain 10Y1001C--00003F) лише
  з 2021-01-01 по 2022-02-24 — Україна припинила публікацію одразу після
  повномасштабного вторгнення, дані НЕ відновлювались (перевірено аж до 2026).
- Cross-Border Physical Flow (documentType=A11) — реальний, безперервний з
  2021 по сьогодні, репортується сусідньою стороною (PL/RO/SK/HU/MD), тому
  не залежить від обмежень України.
Запуск: python scratch/recon_entsoe.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.external_data_service.entsoe import fetch_net_export_series

df = fetch_net_export_series(start_year=2025, end_year=2026)
print(df.shape)
print(df.tail(5))
