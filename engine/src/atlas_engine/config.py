from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env")

    database_url: str = "postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas"
    run_root: Path = Path.home() / ".atlas" / "runs"

    def ensure_run_root(self) -> Path:
        self.run_root.mkdir(parents=True, exist_ok=True)
        return self.run_root


settings = Settings()
