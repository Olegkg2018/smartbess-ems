import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "SmartBESS Energy Arbitrage Platform"
    API_V1_STR: str = "/api/v1"
    
    # Coordinates for IPS Ukraine Central Region
    LAT: float = 49.53
    LON: float = 30.40
    
    # Path settings
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATA_DIR: str = os.path.join(BASE_DIR, "data")
    
    # Keys
    OPENWEATHER_API_KEY: str = ""
    ENTSOE_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    
    # Database
    DATABASE_URL: str = f"sqlite:///{os.path.join(DATA_DIR, 'smartbess.db')}"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Keycloak / OIDC Config
    KEYCLOAK_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "smartbess"
    KEYCLOAK_CLIENT_ID: str = "smartbess-platform"
    JWT_ALGORITHM: str = "RS256"
    OIDC_MOCK_MODE: bool = True
    # Секрет, яким backend підписує mock-JWT у /auth/mock-login. Ніколи не
    # передається на фронтенд — саме це відрізняє поточну версію від старої
    # схеми, де клієнт сам збирав alg:none токен (будь-хто міг підробити
    # роль через curl). Перевизначити через .env для розгорнутих інстансів.
    MOCK_JWT_SECRET: str = "dev-mock-secret-change-me"
    BESS_LAUNCH_DATE: str = "2026-01-01"
    # Підключення батареї (симулятор/tcp/serial, host/port/COM-порт тощо)
    # перенесено з env-var Settings у system_settings.json (2026-08-26) —
    # редаговане в UI (Settings.tsx), не потребує доступу до сервера/.env.
    # Див. scada_service.py::load_bess_connection_settings.
    # "mock" (дефолт, єдиний реально готовий режим) — див.
    # bidding_service/oree_client.py. "live" поки не реалізовано (немає
    # публічного API OREE ні облікових даних, MEMORY.md §8).
    OREE_CLIENT_MODE: str = "mock"

    class Config:
        env_file = ".env"
        case_sensitive = True
        # .env копіюється в образ (Dockerfile: COPY . .) і містить змінні
        # для docker-compose variable substitution (POSTGRES_PASSWORD,
        # REDIS_PASSWORD), які самим додатком напряму не читаються — лише
        # DATABASE_URL/REDIS_URL, вже зібрані compose. За замовчуванням
        # pydantic-settings падає на "зайвих" змінних .env; ignore — щоб
        # такі суто-інфраструктурні змінні не ламали старт застосунку.
        extra = "ignore"

settings = Settings()

# Ensure directories exist
os.makedirs(settings.DATA_DIR, exist_ok=True)
