import asyncio
import base64
import hashlib
import hmac
import os
import struct
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class GrowwAuthRateLimitedError(RuntimeError):
    """Raised while dynamic Groww token generation is under a local cooldown."""


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Session-aware Groww authentication.

    Authentication preference is deliberately deterministic:
    1. GROWW_ACCESS_TOKEN: explicit session credential, if configured.
    2. GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET: unattended TOTP session generation.
    3. GROWW_API_KEY + GROWW_API_SECRET: legacy daily-approval fallback.

    Generated session tokens are shared inside the process for the Groww session
    day. Authentication 429s open a process-wide circuit breaker so collectors
    and dashboard clicks cannot repeatedly hammer the token endpoint.
    """

    AUTH_429_DEFAULT_COOLDOWN_SECONDS = 60 * 60

    _shared_token = None
    _shared_auth_session = None
    _shared_auth_lock = None
    _auth_blocked_until_monotonic = 0.0

    def __init__(self, settings):
        self.api_key = "".join(os.getenv("GROWW_API_KEY", "").split())
        self.api_secret = "".join(os.getenv("GROWW_API_SECRET", "").split())
        self.totp_token = "".join(os.getenv("GROWW_TOTP_TOKEN", "").split())
        self.totp_secret = "".join(os.getenv("GROWW_TOTP_SECRET", "").split())
        self.access_token = "".join(os.getenv("GROWW_ACCESS_TOKEN", "").split())
        self._cached_token = None
        self._cached_auth_session = None

        if not (
            self.access_token
            or (self.totp_token and self.totp_secret)
            or (self.api_key and self.api_secret)
        ):
            raise RuntimeError(
                "Configure GROWW_ACCESS_TOKEN, GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET, "
                "or GROWW_API_KEY + GROWW_API_SECRET"
            )

    @classmethod
    def _auth_lock(cls):
        if cls._shared_auth_lock is None:
            cls._shared_auth_lock = asyncio.Lock()
        return cls._shared_auth_lock

    @staticmethod
    def _auth_session_key():
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        return (now - timedelta(hours=6)).date().isoformat()

    @classmethod
    def _auth_block_remaining(cls) -> float:
        return max(0.0, cls._auth_blocked_until_monotonic - time.monotonic())

    @classmethod
    def _raise_if_auth_blocked(cls) -> None:
        remaining = cls._auth_block_remaining()
        if remaining <= 0:
            return
        raise GrowwAuthRateLimitedError(
            "Groww dynamic authentication is temporarily blocked after HTTP 429; "
            f"no token-generation request will be retried for about {int(remaining) + 1}s."
        )

    @classmethod
    def _register_auth_rate_limit(cls, response: httpx.Response) -> None:
        retry_after = 0.0
        try:
            retry_after = max(0.0, float(response.headers.get("Retry-After", "0")))
        except (TypeError, ValueError):
            retry_after = 0.0
        cooldown = max(cls.AUTH_429_DEFAULT_COOLDOWN_SECONDS, retry_after)
        cls._auth_blocked_until_monotonic = max(
            cls._auth_blocked_until_monotonic,
            time.monotonic() + cooldown,
        )

    @staticmethod
    def _totp_now(secret: str, *, now: int | None = None) -> str:
        """Generate RFC 6238 SHA-1/30s/6-digit TOTP without another dependency."""
        normalized = "".join(secret.split()).upper()
        padding = "=" * ((8 - len(normalized) % 8) % 8)
        key = base64.b32decode(normalized + padding, casefold=True)
        counter = int(time.time() if now is None else now) // 30
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{binary % 1_000_000:06d}"

    async def _post_access_token(self, *, api_key: str, payload: dict) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.BASE_URL}/v1/token/api/access",
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        token = "".join(str(data.get("token", "")).split())
        if not token:
            raise RuntimeError("Groww token generation returned no access token")
        return token

    async def _generate_access_token(self) -> str:
        if self.totp_token and self.totp_secret:
            return await self._post_access_token(
                api_key=self.totp_token,
                payload={"key_type": "totp", "totp": self._totp_now(self.totp_secret)},
            )

        ts = str(int(time.time()))
        checksum = hashlib.sha256((self.api_secret + ts).encode()).hexdigest()
        return await self._post_access_token(
            api_key=self.api_key,
            payload={"key_type": "approval", "checksum": checksum, "timestamp": ts},
        )

    async def _get_access_token(self):
        if self.access_token:
            return self.access_token

        cls = self.__class__
        cls._raise_if_auth_blocked()
        session_key = self._auth_session_key()
        if cls._shared_token and cls._shared_auth_session == session_key:
            return cls._shared_token

        async with cls._auth_lock():
            cls._raise_if_auth_blocked()
            if cls._shared_token and cls._shared_auth_session == session_key:
                return cls._shared_token

            try:
                token = await self._generate_access_token()
            except httpx.HTTPStatusError as exc:
                if getattr(exc.response, "status_code", None) == 429:
                    cls._register_auth_rate_limit(exc.response)
                    cls._raise_if_auth_blocked()
                raise

            cls._auth_blocked_until_monotonic = 0.0
            cls._shared_token = token
            cls._shared_auth_session = session_key
            self._cached_token = token
            self._cached_auth_session = session_key
            return token
