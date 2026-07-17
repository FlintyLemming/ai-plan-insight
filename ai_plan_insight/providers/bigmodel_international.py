from .bigmodel import BigModelProvider


class BigModelInternationalProvider(BigModelProvider):
    """智谱 GLM Coding Plan 国际版 usage provider."""

    API_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
    MODEL_USAGE_URL = "https://api.z.ai/api/monitor/usage/model-usage"

    @property
    def name(self) -> str:
        return "GLM Coding Plan 国际版"
