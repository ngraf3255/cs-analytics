# Prediction API

FastAPI service that loads the repository's `model.pkl` artifact and exposes
`/health`, `/options`, and `/predict` endpoints. The model was serialized with
scikit-learn 1.9.1, so that version is pinned in `requirements.txt`.

Run it from this directory after installing the requirements:

```sh
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Interactive API docs are available at `http://localhost:8000/docs`.

## Steam match sync (feature-flagged)

Steam sign-in, match-history linking and sync are **disabled** (routes return
`503 steam_sync_disabled`, `GET /steam/status` returns `{"enabled": false}`)
unless both `DATABASE_URL` and `TOKEN_ENCRYPTION_KEYS` are set. When enabled,
these are also required: `SESSION_SECRET` (32+ chars), `PUBLIC_API_URL`,
`FRONTEND_URL`, `STEAM_WEB_API_KEY`.

Optional: `SESSION_COOKIE_SAMESITE` (lax), `SESSION_COOKIE_SECURE` (true),
`SESSION_COOKIE_DOMAIN`, `SYNC_MAX_MATCHES_PER_REQUEST` (1),
`SYNC_MIN_INTERVAL_SECONDS` (30), `DEMO_MAX_DOWNLOAD_BYTES`, `DEMO_MAX_DECOMPRESSED_BYTES`.

Generate an encryption key (comma-separate several, newest first, to rotate):

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply migrations: `DATABASE_URL=... python -m steamlink.migrate`.

Demo URL resolution needs a CS2 Game Coordinator integration
(`UnconfiguredDemoLocator`, see TODO in `steamlink/valve.py`); until then sync
stops with `demo_retrieval_not_configured` and does not advance the cursor.

Tests: `pip install -r requirements-dev.txt && pytest`
