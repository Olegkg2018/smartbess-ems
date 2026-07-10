import os
from typing import Dict, Any

class TariffService:
    # Distribution tariffs (Тарифи на розподіл) for 2026 (in UAH/MWh, without VAT)
    # Source: NERC (НКРЕКП) resolutions
    DISTRIBUTION_TARIFFS: Dict[str, Dict[int, float]] = {
        "DTEK_Kyiv_Grids": {
            1: 180.00,  # Class 1 (>= 27.5 kV)
            2: 780.00   # Class 2 (< 27.5 kV)
        },
        "DTEK_Kyiv_Region_Grids": {
            1: 320.00,
            2: 1250.00
        },
        "DTEK_Dnipro_Grids": {
            1: 240.00,
            2: 1050.00
        },
        "Lvivoblenergo": {
            1: 290.00,
            2: 1180.00
        },
        "Kharkivoblenergo": {
            1: 350.00,
            2: 1420.00
        },
        "Default_UA": {
            1: 280.00,
            2: 1100.00
        }
    }

    # Transmission tariff (Тариф на передачу НЕК Укренерго) for 2026 (UAH/MWh, without VAT)
    TRANSMISSION_TARIFF: float = 528.57

    # Dispatch tariff (Тариф на диспетчеризацію НЕК Укренерго) for 2026 (UAH/MWh, without VAT)
    DISPATCH_TARIFF: float = 104.57

    # Ціна РДН/ВДР емпірично ніколи не опускається нижче ~10 грн/МВт·год —
    # підтверджено реальними історичними даними, використовується як floor
    # при клипінгу прогнозу.
    #
    # ПРИМІТКА: раніше тут була таблиця погодинних СТЕЛЬ ціни (PRICE_CAP_BY_HOUR),
    # нібито взята з постанови НКРЕКП. Перевірка на реальних історичних цінах
    # показала, що мапінг година→стеля був СКЛАДЕНИЙ НЕПРАВИЛЬНО (для годин
    # 0-10 реальна ціна сягає 7000-11800 грн, а не 5600/6900, як передбачала
    # таблиця) — і навіть після емпіричного виправлення ефект на WAPE був
    # статистично незначущий (24.893 проти 24.891, у межах шуму). Таблицю
    # прибрано; клипінг знову плоский PRICE_FLOOR..16000. Не повертати без
    # нового walk-forward бектесту з підтвердженим джерелом.
    PRICE_FLOOR_UAH_MWH: float = 10.0

    @classmethod
    def get_distribution_tariff(cls, oblenergo_name: str, voltage_class: int) -> float:
        """
        Returns distribution tariff in UAH/MWh.
        """
        oblenergo = cls.DISTRIBUTION_TARIFFS.get(oblenergo_name, cls.DISTRIBUTION_TARIFFS["Default_UA"])
        return oblenergo.get(voltage_class, oblenergo[2])

    @classmethod
    def calculate_total_tariff_kwh(
        cls,
        oblenergo_name: str,
        voltage_class: int,
        supplier_margin_uah_mwh: float = 150.0,
        include_vat: bool = False
    ) -> float:
        """
        Calculates the sum of all non-energy tariff components (transmission, distribution, dispatch, supplier margin)
        in UAH/kWh.
        """
        dist_tariff = cls.get_distribution_tariff(oblenergo_name, voltage_class)
        total_mwh = cls.TRANSMISSION_TARIFF + cls.DISPATCH_TARIFF + dist_tariff + supplier_margin_uah_mwh
        
        # Convert to UAH/kWh
        total_kwh = total_mwh / 1000.0
        
        if include_vat:
            total_kwh *= 1.20
            
        return float(total_kwh)

    @classmethod
    def get_all_utility_names(cls) -> list:
        return list(cls.DISTRIBUTION_TARIFFS.keys())
