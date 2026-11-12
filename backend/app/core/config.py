from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Football Intelligence Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./fie.db"
    min_prediction_probability: float = 0.85
    min_data_quality_score: int = 70
    # Comma-separated origins allowed to call the API from a browser.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
