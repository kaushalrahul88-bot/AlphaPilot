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
    from .copper_candle_observation_store import register_copper_candle_observation_startup  # noqa: E402
    from .copper_commodity_brain_prospective_startup import register_copper_commodity_brain_prospective_startup  # noqa: E402
    from .copper_direction_v2_startup import register_copper_direction_v2_prospective_startup  # noqa: E402
    from .copper_option_observation_store import register_copper_option_observation_startup  # noqa: E402
    from .copper_pit_api import register_copper_pit_routes  # noqa: E402
    from .crude_oil_mini_manual_api import register_crude_oil_mini_manual_routes  # noqa: E402
    from .crude_oil_mini_option_observation_store import register_crude_oil_mini_option_observation_startup  # noqa: E402
    from .crypto_btc_capture_startup import register_btc_capture_startup  # noqa: E402
    from .crypto_btc_delta_options_probe_startup import register_delta_options_probe_startup  # noqa: E402
    from .crypto_btc_live_shadow_click_startup import register_btc_live_shadow_click_startup  # noqa: E402
    from .crypto_btc_prospective_proof_api import register_btc_prospective_proof_routes  # noqa: E402
    from .crypto_btc_prospective_proof_startup import register_btc_prospective_proof_startup  # noqa: E402
    from .crypto_btc_prospective_resolution_startup import register_btc_prospective_resolution_startup  # noqa: E402
    from .crypto_macro_live_availability_api import register_crypto_macro_live_availability_routes  # noqa: E402
    from .shared_commodity_brain_dashboard_api import register_shared_commodity_brain_dashboard_routes  # noqa: E402
    from .main import _collector_store as _collector_auth, app as _app, settings as _settings  # noqa: E402

    register_crude_oil_mini_option_observation_startup(_app, _settings)
    register_copper_option_observation_startup(_app, _settings)
    register_copper_candle_observation_startup(_app, _settings)
    register_copper_direction_v2_prospective_startup(_app, _settings)
    register_copper_commodity_brain_prospective_startup(_app, _settings)
    register_btc_capture_startup(_app)
    register_delta_options_probe_startup(_app)
    register_btc_prospective_proof_startup(_app, _settings)
    register_btc_prospective_resolution_startup(_app, _settings)
    register_btc_live_shadow_click_startup(_app, _settings)
    register_copper_pit_routes(_app, _settings, _collector_auth)
    register_crude_oil_mini_manual_routes(_app, _settings)
    register_btc_prospective_proof_routes(_app, _settings, _collector_auth)
    register_crypto_macro_live_availability_routes(_app, _settings, _collector_auth)
    register_shared_commodity_brain_dashboard_routes(_app, _settings)
