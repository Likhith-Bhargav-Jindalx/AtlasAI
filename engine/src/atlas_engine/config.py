from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env is shared with atlas_engine.llm.factory's own
    # dotenv loading (ATLAS_AZURE_OPENAI_*, read via plain os.environ.get,
    # not a Settings field) — pydantic-settings defaults BaseSettings to
    # extra="forbid", which would otherwise crash the whole app at import
    # time on any ATLAS_-prefixed key Settings doesn't declare a field for.
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas"
    run_root: Path = Path.home() / ".atlas" / "runs"
    session_secret: str = "dev-insecure-secret-change-me-via-ATLAS_SESSION_SECRET"

    def ensure_run_root(self) -> Path:
        self.run_root.mkdir(parents=True, exist_ok=True)
        return self.run_root


settings = Settings()
