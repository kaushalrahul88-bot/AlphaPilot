import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class GrowwAuthRateLimitedError(RuntimeError):
    """Raised while dynamic Groww token generation is under a local cooldown."""


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Session-aware Groww authentication with a manual-token fast path.

    A configured ``GROWW_ACCESS_TOKEN`` is already a session credential. Prefer it
    instead of generating another token from every fresh worker/process. Dynamic
    API-key authentication remains the fallback when no manual token is present.

    Groww authentication 429s are guarded by a process-wide circuit breaker so
    scheduled collectors and dashboard clicks do not repeatedly hammer the token
    endpoint while it is rate-limited. A configured access token always bypasses
    the breaker.
    """

    AUTH_429_DEFAULT_COOLDOWN_SECONDS = 60 * 60

    _shared_token = None
    _shared_auth_session = None
    _shared_auth_lock = None
    _auth_blocked_until_monotonic = 0.0

    def __init__(self, settings):
        self.api_key = "".join(os.getenv("GROWW_API_KEY", "").split())
        self.api_secret = "".join(os.getenv("GROWW_API_SECRET", "").split())
        self.access_token = "".join(os.getenv("GROWW_ACCESS_TOKEN", "").split())
        self._cached_token = None
        self._cached_auth_session = None

        if not (self.api_key and self.api_secret) and not self.access_token:
            raise RuntimeError(
                "Set both GROWW_API_KEY and GROWW_API_SECRET, or GROWW_ACCESS_TOKEN"
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
            f"no token-generation request will be retried for about {int(remaining) + 1}s. "
            "Configure a current GROWW_ACCESS_TOKEN to bypass dynamic authentication."
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

    async def _generate_access_token(self):
        ts = str(int(time.time()))
        checksum = hashlib.sha256((self.api_secret + ts).encode()).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "key_type": "approval",
            "checksum": checksum,
            "timestamp": ts,
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
            raise RuntimeError(f"Groww token generation failed: {data}")
        return token

    async def _get_access_token(self):
        # A supplied session token is process-independent and therefore prevents
        # stateless workers from each consuming Groww's token-generation quota.
        if self.access_token:
            return self.access_token

        if not (self.api_key and self.api_secret):
            raise RuntimeError("No Groww authentication credentials are configured")

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
