from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Credential value object passed to provider constructors.

    Decoupled from any config file; built from V2InstanceConfig by
    provider_registry._to_provider_config.
    """
    api_key: str = ""
    base_url: str = ""
    user_name: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    admin_password: str = ""
    cookie: str = ""
    order: int = 999
