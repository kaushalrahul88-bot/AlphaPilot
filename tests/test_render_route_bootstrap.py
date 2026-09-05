from __future__ import annotations

import os
import subprocess
import sys
import unittest


REQUIRED_ROUTES = {
    "/v1/internal/copper/current-mind-forward-phase1/start",
    "/v1/internal/copper/current-mind-forward-phase1/status",
    "/v1/internal/copper/current-mind-forward-phase1/result",
    "/v1/internal/crude-oil-mini/research-framework/start",
    "/v1/internal/crude-oil-mini/research-framework/status",
    "/v1/internal/crude-oil-mini/research-framework/result",
    "/v1/internal/crypto/macro-live-availability/report",
}


class RenderRouteBootstrapTests(unittest.TestCase):
    def _assert_entrypoint(self, module: str) -> None:
        expected = repr(sorted(REQUIRED_ROUTES))
        script = (
            f"from {module} import app; "
            "paths={route.path for route in app.routes}; "
            f"required=set({expected}); "
            "missing=sorted(required-paths); "
            "assert not missing, missing"
        )
        env = os.environ.copy()
        env["RENDER_GIT_COMMIT"] = "route-bootstrap-test"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            cwd=os.getcwd(),
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"{module} failed to expose supplemental routes:\n{completed.stdout}\n{completed.stderr}",
        )

    def test_render_main_entrypoint_registers_supplemental_routes(self):
        self._assert_entrypoint("app.main")

    def test_render_asgi_entrypoint_registers_supplemental_routes(self):
        self._assert_entrypoint("app.asgi")


if __name__ == "__main__":
    unittest.main()
