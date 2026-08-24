import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Groww provider with process-wide daily authentication reuse.

    Groww approval resets at 06:00 IST. A generated access token is shared by all
    provider instances in the Render process so a 44-symbol scanner run does not
    repeatedly regenerate the same daily token for each API request/batch.
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
                async with httpx.AsyncClient(timeout=20) as client:
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
                await asyncio.sleep(1.0 * (attempt + 1))

        if last_error:
            raise last_error
        raise RuntimeError("Groww token generation failed")

    async def _get_access_token(self):
        if self.api_key and self.api_secret:
            session_key = self._auth_session_key()
            cls = self.__class__

            if cls._daily_token and cls._daily_auth_session == session_key:
                self._cached_token = cls._daily_token
                self._cached_auth_session = session_key
                return cls._daily_token

            async with cls._auth_lock():
                if cls._daily_token and cls._daily_auth_session == session_key:
                    self._cached_token = cls._daily_token
                    self._cached_auth_session = session_key
                    return cls._daily_token

                try:
                    token = await self._generate_access_token()
                except Exception:
                    if self.access_token:
                        return self.access_token
                    raise

                cls._daily_token = token
                cls._daily_auth_session = session_key
                self._cached_token = token
                self._cached_auth_session = session_key
                return token

        return self.access_token
