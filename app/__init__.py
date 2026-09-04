"""AlphaPilot application package bootstrap.

Render can override the Dockerfile command and import ``app.main:app`` directly.
The supplemental operational routes live in ``app.asgi`` and decorate that same
FastAPI application object. On Render, load supplemental modules once at package
import time so either entrypoint exposes the identical route set.

The bootstrap is deliberately Render-only to avoid turning ordinary library/test
imports of ``app.*`` into full API application imports.
"""

from __future__ import annotations

import os


if os.getenv("RENDER_GIT_COMMIT"):
    from . import asgi as _supplemental_asgi_routes  # noqa: F401,E402
    from .copper_option_observation_store import (  # noqa: E402
        register_copper_option_observation_startup,
    )
    from .crude_oil_mini_manual_api import register_crude_oil_mini_manual_routes  # noqa: E402
    from .crude_oil_mini_option_observation_store import (  # noqa: E402
        register_crude_oil_mini_option_observation_startup,
    )
    from .main import app as _app, settings as _settings  # noqa: E402

    register_crude_oil_mini_option_observation_startup(_app, _settings)
    register_copper_option_observation_startup(_app, _settings)
    register_crude_oil_mini_manual_routes(_app, _settings)