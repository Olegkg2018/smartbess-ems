"""Recon: подтверждает, что t.me/s/<channel> отдаёт реальные посты без Telegram API
ключей, и что keyword-сигнал извлекается корректно. Запуск: python scratch/recon_telegram.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.external_data_service.telegram_public import sync_channel, daily_grid_stress_signal

n = sync_channel("ukrenergo", max_pages=2)
print("new posts fetched:", n)

signal = daily_grid_stress_signal()
for date_str in sorted(signal.keys())[-5:]:
    print(date_str, signal[date_str])
