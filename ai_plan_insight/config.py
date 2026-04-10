from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Configuration for a single provider."""
    api_key: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""


class Config(BaseModel):
    """Top-level configuration."""
    providers: dict[str, ProviderConfig]
