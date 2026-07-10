"""Recon: подтверждает, что oree.com.ua market=IDM отдаёт реальные почасовые цены
внутрішньодобового ринку той же структуры, что и DAM. Запуск: python scratch/recon_idm.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.external_data_service.intraday_market import fetch_idm_prices_for_month

df = fetch_idm_prices_for_month(6, 2026)
print(df.shape)
print(df.head())
