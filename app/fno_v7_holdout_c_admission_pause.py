"""Environment-gated admission pause for the expensive F&O V7 Holdout C builder.

This gate is operational only: it never mutates the frozen Holdout C protocol,
cache, decisions, or results.  It exists so another serialized heavyweight
research workload can temporarily take the shared Render worker without the
Holdout C GitHub poller immediately restarting the builder after a deployment.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from starlette.responses import JSONResponse

ENV_FNO_V7_HOLDOUT_C_PAUSED = "ALPHAPILOT_FNO_V7_HOLDOUT_C_PAUSED"
HOLDOUT_C_START_PATH = "/v1/internal/fno/v7-holdout-c-dataset/start"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def holdout_c_admission_paused(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return _bool(source.get(ENV_FNO_V7_HOLDOUT_C_PAUSED), False)


def register_fno_v7_holdout_c_admission_pause(app) -> None:
    """Block only new/resume Holdout C POST admission while the env gate is on."""
    if getattr(app.state, "fno_v7_holdout_c_admission_pause_registered", False):
        return
    app.state.fno_v7_holdout_c_admission_pause_registered = True

    @app.middleware("http")
    async def fno_v7_holdout_c_admission_pause(request, call_next):
        if (
            request.method.upper() == "POST"
            and request.url.path == HOLDOUT_C_START_PATH
            and holdout_c_admission_paused()
        ):
            return JSONResponse(
                status_code=423,
                content={
                    "detail": "F&O V7 Holdout C admission is temporarily paused for the serialized heavy-job lane.",
                    "admission_paused": True,
                    "protocol_changed": False,
                    "cached_progress_preserved": True,
                },
            )
        return await call_next(request)


def architecture_contract() -> dict[str, object]:
    return {
        "version": "FNO_V7_HOLDOUT_C_ADMISSION_PAUSE_V1",
        "default_paused": False,
        "blocks_only_holdout_c_start": True,
        "status_and_result_readable_while_paused": True,
        "frozen_protocol_changed": False,
        "transport_cache_changed": False,
        "cached_progress_preserved": True,
        "live_execution": False,
    }
