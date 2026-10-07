"""Typed, validated configuration. Every value comes from the environment (prefix BG_REMOVE_)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Device(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BG_REMOVE_", env_file=".env", extra="ignore")

    api_key: SecretStr = Field(description="Shared secret clients send in the x-api-key header")
    require_auth: bool = Field(default=True, description="Disable only for local development")
    model: str = Field(default="birefnet-general-lite", description="rembg model name")
    device: Device = Field(default=Device.CPU)
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    models_dir: str = Field(
        default="/models", description="Where rembg stores weights (U2NET_HOME)"
    )

    @field_validator("api_key")
    @classmethod
    def _non_empty_key(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("BG_REMOVE_API_KEY must not be empty")
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # api_key is read from the environment
