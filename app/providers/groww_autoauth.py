import hashlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Prefer Groww API key+secret and generate the daily access token automatically.

    Groww's approval flow expires at 06:00 IST. The user still needs to approve the
    API key on Groww once per trading day, but AlphaPilot no longer requires manual
    copying of a fresh access token into Render after that approval.

    GROWW_ACCESS_TOKEN is retained only as a fallback when API key+secret are not
    configured.
    """

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

    @staticmethod
    def _auth_session_key():
        # Groww access authorization resets daily at 06:00 Asia/Kolkata.
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        return (now - timedelta(hours=6)).date().isoformat()

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
        # Preferred path: API key + secret. This deliberately ignores a stale
        # GROWW_ACCESS_TOKEN when the renewable credentials are available.
        if self.api_key and self.api_secret:
            session_key = self._auth_session_key()
            if self._cached_token and self._cached_auth_session == session_key:
                return self._cached_token

            token = await self._generate_access_token()
            self._cached_token = token
            self._cached_auth_session = session_key
            return token

        # Fallback for installations that only use a manually generated token.
        return self.access_token
