import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Groww provider with stable token reuse and safe dynamic-auth fallback.

    A configured GROWW_ACCESS_TOKEN is preferred because it is already approved
    for the current Groww session and survives Render process restarts. API
    key+secret generation is used only when no explicit token is configured.

    When dynamic generation is required, the generated token is cached
    process-wide for the current Groww auth session so scanner batches do not
    repeatedly authenticate.
    """

    _daily_token = None
    _daily_auth_session = None
    _daily_auth_lock = None

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
        if cls._daily_auth_lock is None:
            cls._daily_auth_lock = asyncio.Lock()
        return cls._daily_auth_lock

    @staticmethod
    def _auth_session_key():
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        return (now - timedelta(hours=6)).date().isoformat()

    async def _generate_access_token(self):
        last_error = None
        for attempt in range(3):
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
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    response = await client.post(
                        f"{self.BASE_URL}/v1/token/api/access",
                        headers=headers,
                        json=payload,
                    )
                if response.status_code == 200:
                    data = response.json()
                    token = "".join(str(data.get("token", "")).split())
                    if token:
                        return token
                    last_error = RuntimeError(f"Groww token generation failed: {data}")
                else:
                    last_error = RuntimeError(
                        f"Groww token generation failed ({response.status_code}): {response.text[:300]}"
                    )
                    if response.status_code not in (408, 429) and response.status_code < 500:
                        break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc

            if attempt < 2:
                await asyncio.sleep(0.75 * (attempt + 1))

        if last_error:
            raise last_error
        raise RuntimeError("Groww token generation failed")

    async def _get_access_token(self):
        # Prefer the already-approved token supplied to Render. This avoids a
        # fresh approval/token-generation round trip whenever Render redeploys.
        if self.access_token:
            return self.access_token

        if not (self.api_key and self.api_secret):
            raise RuntimeError("No Groww authentication credentials are configured")

        session_key = self._auth_session_key()
        cls = self.__class__

        if cls._daily_token and cls._daily_auth_session == session_key:
            return cls._daily_token

        async with cls._auth_lock():
            if cls._daily_token and cls._daily_auth_session == session_key:
                return cls._daily_token

            token = await self._generate_access_token()
            cls._daily_token = token
            cls._daily_auth_session = session_key
            self._cached_token = token
            self._cached_auth_session = session_key
            return token
