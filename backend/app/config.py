"""Application configuration with no filesystem side effects at import time."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Environment-backed settings for the local Mass Production Quality Validation process.

    Direct ``.env`` loading is intentionally disabled. Deployment code may inject
    environment variables or a future secret-store adapter, but importing this
    module never creates a data directory or database file.
    """

    model_config = SettingsConfigDict(
        env_prefix="MASS_PRODUCTION_QUALITY_VALIDATION_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    app_name: str = "Mass Production Quality Validation"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default="sqlite+pysqlite:///./.localdata/mass_production_quality_validation.sqlite3",
        min_length=1,
    )
    original_file_store_root: Path = Path(".localdata/original-files")
    intake_staging_root: Path = Path(".localdata/intake-staging")
    max_upload_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    intake_queue_capacity: int = Field(default=8, ge=1, le=64)
    intake_registry_capacity: int = Field(default=128, ge=8, le=4096)
    bulk_staging_root: Path = Path(".localdata/bulk-staging")
    bulk_max_files: int = Field(default=20, ge=1, le=200)
    bulk_max_batch_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    bulk_queue_capacity: int = Field(default=4, ge=1, le=32)
    frontend_dist_path: Path = Path("frontend/dist")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the process settings without performing external I/O."""

    return AppSettings()
