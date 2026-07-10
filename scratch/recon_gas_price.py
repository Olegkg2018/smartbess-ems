"""Recon: index.minfin.com.ua дает РЕАЛЬНОЕ значение только "на сейчас" — URL с
датой в пути игнорируется сайтом (всегда возвращает текущий снимок). Проверено
ниже: /2026-07-09/ и /2021-03-15/ дают одинаковый "останнє оновлення: 09.07.2026".
Поэтому исторический бэкфилл с этого источника невозможен — используем только
для ежедневного накопительного лога вперёд (append_daily_snapshot).
Запуск: python scratch/recon_gas_price.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.external_data_service.gas_price import fetch_gas_price_now, append_daily_snapshot, load_daily_log

print("now:", fetch_gas_price_now())
print("append:", append_daily_snapshot())
print(load_daily_log())
