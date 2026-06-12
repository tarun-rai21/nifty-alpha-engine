from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):

    # Database
    DATABASE_URL: str

    # Application
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "dev-secret-key"
    DEBUG: bool = True

    # CORS — covers both Docker web container and Vite dev server
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()