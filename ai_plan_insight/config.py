from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Configuration for a single provider."""
    api_key: str = ""
    base_url: str = ""
    user_name: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""


class PocketBaseConfig(BaseModel):
    """Configuration for PocketBase connection."""
    url: str = ""
    email: str = ""
    password: str = ""


class Config(BaseModel):
    """Top-level configuration."""
    providers: dict[str, ProviderConfig]
    pocketbase: PocketBaseConfig | None = None
