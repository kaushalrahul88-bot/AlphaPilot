"""AlphaPilot application package bootstrap.

Render can override the Dockerfile command and import ``app.main:app`` directly.
The supplemental operational routes live in ``app.asgi`` and decorate that same
FastAPI application object.  On Render, load the supplemental module once at
package import time so either entrypoint exposes the identical route set.

The bootstrap is deliberately Render-only to avoid turning ordinary library/test
imports of ``app.*`` into full API application imports.
"""

from __future__ import annotations

import os


if os.getenv("RENDER_GIT_COMMIT"):
    from . import asgi as _supplemental_asgi_routes  # noqa: F401,E402
