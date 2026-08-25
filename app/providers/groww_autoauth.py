import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_amount import AmountAwareGrowwProvider


class AutoAuthAmountAwareGrowwProvider(AmountAwareGrowwProvider):
    """Prefer session-aware dynamic auth, with a manual-token-only fallback.

    When API key + secret are configured, generate one shared token per Groww
    session. ``GROWW_ACCESS_TOKEN`` remains supported for deployments that do not
    have the key pair, but it must not override dynamic auth: a token left in
    Render after Groww's 06:00 IST reset would otherwise force every data request
    to fail with HTTP 401 until the environment variable was manually replaced.
    """

    _shared_token = None
    _shared_auth_session = None
    _shared_auth_lock = None

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
        # A manual token is the fallback only when a complete dynamic-auth key
        # pair is unavailable. This prevents a stale Render environment value
        # from shadowing credentials that can generate the current session token.
        if not (self.api_key and self.api_secret):
            if not self.access_token:
                raise RuntimeError(
                    "No Groww authentication credentials are configured"
                )
            return self.access_token

        session_key = self._auth_session_key()
        cls = self.__class__

        if cls._shared_token and cls._shared_auth_session == session_key:
            return cls._shared_token

        async with cls._auth_lock():
            if cls._shared_token and cls._shared_auth_session == session_key:
                return cls._shared_token

            token = await self._generate_access_token()
            cls._shared_token = token
            cls._shared_auth_session = session_key
            self._cached_token = token
            self._cached_auth_session = session_key
            return token
