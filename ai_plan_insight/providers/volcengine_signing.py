"""Volcengine HMAC-SHA256 (Signature V4) request signing.

Algorithm distilled from volcenginesdkcore.SignerV4, verified to produce
identical signatures against the official SDK. Used by the Ark control-plane
API (GetCodingPlanUsage), which only accepts AK/SK signing — not Ark API Keys.
"""
import datetime
import hashlib
import hmac
from urllib.parse import quote


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(sk: str, short_date: str, region: str, service: str) -> bytes:
    k = _hmac_sha256(sk.encode("utf-8"), short_date)
    k = _hmac_sha256(k, region)
    k = _hmac_sha256(k, service)
    return _hmac_sha256(k, "request")


def _canonical_query(query: dict[str, str]) -> str:
    pairs = sorted(
        (quote(k, safe="-_.~"), quote(str(v), safe="-_.~")) for k, v in query.items()
    )
    return "&".join(f"{k}={v}" for k, v in pairs)


def sign_request(
    *,
    host: str,
    method: str,
    path: str,
    query: dict[str, str],
    body: str,
    ak: str,
    sk: str,
    region: str,
    service: str,
) -> dict[str, str]:
    """Return the full set of headers needed for a signed Volcengine request.

    The returned headers include Host, X-Date, X-Content-Sha256, Authorization
    and Content-Type. Merge/override these onto the caller's headers.
    """
    now = datetime.datetime.now(datetime.UTC)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]

    content_type = "application/json; charset=UTF-8"
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    signed_str = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-content-sha256:{body_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"

    canonical_request = "\n".join(
        [method, path, _canonical_query(query), signed_str, signed_headers, body_hash]
    )

    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    skey = _signing_key(sk, short_date, region, service)
    signature = hmac.new(skey, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "Content-Type": content_type,
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": body_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
