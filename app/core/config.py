from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Football Intelligence Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./fie.db"
    min_prediction_probability: float = 0.85
    min_data_quality_score: int = 70

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
