from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.copper_direction_v2_startup import (
    register_copper_direction_v2_prospective_startup,
)


class _FakeApp:
    def __init__(self):
        self.startup = None

    def on_event(self, event_name: str):
        if event_name != "startup":
            raise AssertionError(f"unexpected event: {event_name}")

        def decorator(func):
            self.startup = func
            return func

        return decorator


class _Settings:
    def __init__(self, database_url: str):
        self.database_url = database_url


class CopperDirectionV2StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_database_url_is_noop(self):
        app = _FakeApp()
        register_copper_direction_v2_prospective_startup(app, _Settings(""))
        self.assertIsNotNone(app.startup)

        with patch(
            "app.copper_direction_v2_startup.initialize_store",
            new_callable=AsyncMock,
        ) as initialize:
            await app.startup()
            initialize.assert_not_awaited()

    async def test_configured_database_initializes_schema_only(self):
        app = _FakeApp()
        register_copper_direction_v2_prospective_startup(
            app,
            _Settings("postgresql://example.invalid/alphapilot"),
        )
        self.assertIsNotNone(app.startup)

        with patch(
            "app.copper_direction_v2_startup.initialize_store",
            new_callable=AsyncMock,
        ) as initialize:
            await app.startup()
            initialize.assert_awaited_once_with(
                "postgresql://example.invalid/alphapilot"
            )


if __name__ == "__main__":
    unittest.main()
