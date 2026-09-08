from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.fno_v7_holdout_c_admission_pause import (
    ENV_FNO_V7_HOLDOUT_C_PAUSED,
    HOLDOUT_C_START_PATH,
    architecture_contract,
    holdout_c_admission_paused,
    register_fno_v7_holdout_c_admission_pause,
)


def _client() -> TestClient:
    app = FastAPI()
    register_fno_v7_holdout_c_admission_pause(app)

    @app.post(HOLDOUT_C_START_PATH)
    async def start():
        return {"status": "STARTED"}

    @app.get("/v1/internal/fno/v7-holdout-c-dataset/status")
    async def status():
        return {"status": "RUNNING", "worker_active": False}

    return TestClient(app)


def test_pause_is_opt_in_and_boolean_parser_is_explicit():
    assert holdout_c_admission_paused({}) is False
    assert holdout_c_admission_paused({ENV_FNO_V7_HOLDOUT_C_PAUSED: "true"}) is True
    assert holdout_c_admission_paused({ENV_FNO_V7_HOLDOUT_C_PAUSED: "0"}) is False


def test_paused_gate_blocks_only_holdout_start(monkeypatch):
    monkeypatch.setenv(ENV_FNO_V7_HOLDOUT_C_PAUSED, "true")
    client = _client()

    blocked = client.post(HOLDOUT_C_START_PATH)
    assert blocked.status_code == 423
    body = blocked.json()
    assert body["admission_paused"] is True
    assert body["protocol_changed"] is False
    assert body["cached_progress_preserved"] is True

    status = client.get("/v1/internal/fno/v7-holdout-c-dataset/status")
    assert status.status_code == 200
    assert status.json()["status"] == "RUNNING"


def test_unpaused_gate_allows_existing_start_route(monkeypatch):
    monkeypatch.setenv(ENV_FNO_V7_HOLDOUT_C_PAUSED, "false")
    response = _client().post(HOLDOUT_C_START_PATH)
    assert response.status_code == 200
    assert response.json() == {"status": "STARTED"}


def test_contract_preserves_frozen_protocol_and_cache():
    contract = architecture_contract()
    assert contract["default_paused"] is False
    assert contract["blocks_only_holdout_c_start"] is True
    assert contract["status_and_result_readable_while_paused"] is True
    assert contract["frozen_protocol_changed"] is False
    assert contract["transport_cache_changed"] is False
    assert contract["cached_progress_preserved"] is True
    assert contract["live_execution"] is False
