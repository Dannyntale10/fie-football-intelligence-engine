from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Football Intelligence Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./fie.db"
    min_prediction_probability: float = 0.85
    min_data_quality_score: int = 70
    # Comma-separated origins allowed to call the API from a browser.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # When set (FIE_API_KEY), every route except /health requires this header.
    # Outside development an unset key is a misconfiguration, not an open door.
    fie_api_key: str = ""
    # Requests per client per minute. 0 disables that class of limit.
    rate_limit_read: int = 240
    rate_limit_write: int = 60
    # Backtests, ingestion and training replay the whole database.
    rate_limit_expensive: int = 10
    max_body_bytes: int = 2 * 1024 * 1024
    max_upload_bytes: int = 25 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
