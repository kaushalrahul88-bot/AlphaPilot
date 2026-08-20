# AlphaPilot API

Server-side market-data and scanning foundation. Keep broker secrets and API tokens here, never in the React frontend.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Endpoints:
- `GET /health`
- `GET /v1/quote/{symbol}`
- `GET /v1/options/{symbol}`
- `POST /v1/scan`

The default provider is `MOCK`. Switch to a supported live provider only after credentials and instrument mappings are configured.
