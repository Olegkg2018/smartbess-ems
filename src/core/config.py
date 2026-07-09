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
    
    # Database
    DATABASE_URL: str = f"sqlite:///{os.path.join(DATA_DIR, 'smartbess.db')}"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Keycloak / OIDC Config
    KEYCLOAK_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "smartbess"
    KEYCLOAK_CLIENT_ID: str = "smartbess-platform"
    JWT_ALGORITHM: str = "RS256"
    OIDC_MOCK_MODE: bool = True
    BESS_LAUNCH_DATE: str = "2026-01-01"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

# Ensure directories exist
os.makedirs(settings.DATA_DIR, exist_ok=True)
