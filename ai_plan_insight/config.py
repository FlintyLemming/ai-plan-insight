from pydantic import BaseModel, Field


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
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def alias_lookup(self) -> dict[str, str]:
        """Reverse map {raw_model_id -> canonical_label}.

        Built from `model_aliases` on each call. Unknown raw ids (not present
        here) are treated as their own canonical label at read time. If the
        same raw id appears in multiple arrays, the last definition wins
        (dict insertion order = source order).
        """
        lookup: dict[str, str] = {}
        for label, raw_ids in self.model_aliases.items():
            for raw_id in raw_ids:
                lookup[raw_id] = label
        return lookup
