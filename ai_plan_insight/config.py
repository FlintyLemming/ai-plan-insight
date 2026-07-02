from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Configuration for a single provider.

    `order` controls the card display order in the UI. Smaller values come
    first. Defaults to 999 so providers that omit the field sort last
    without forcing existing config files to be edited.
    """
    api_key: str = ""
    base_url: str = ""
    user_name: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    admin_password: str = ""
    cookie: str = ""
    order: int = 999


class Config(BaseModel):
    """Top-level configuration."""
    providers: dict[str, ProviderConfig]
