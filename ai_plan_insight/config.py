from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Configuration for a single provider."""
    api_key: str


class Config(BaseModel):
    """Top-level configuration."""
    providers: dict[str, ProviderConfig]
