from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GROQ_API_KEY: str
    MODEL_NAME: str
    EMBEDDING_MODEL: str
    CHROMA_PATH: str
    COLLECTION_NAME: str
    TOP_K: int

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()