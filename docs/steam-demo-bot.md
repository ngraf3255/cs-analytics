# Steam demo bot (CS2 Game Coordinator)

A match sharing code identifies a match but not where its demo lives. The API
asks the CS2 Game Coordinator (GC) for the match (`MatchListRequestFullGameInfo`
with the decoded match id, outcome id and token). The reply's last
`roundstatsall[].map` field is the replay URL
(`http://replayN.valve.net/730/<matchid>_<outcomeid>.dem.bz2`). Talking to the
GC requires a logged-in Steam account, so the API uses a **dedicated bot account**.

Code: `steamlink/gc.py` (locator logic, throttling, error mapping),
`steamlink/gc_steamio.py` (steam.py / `steamio==1.1.3` client),
`steamlink/bot_login.py` (one-time login).

## Feature flag

Demo retrieval is **off** unless bot credentials are set. Without them sync
stops with `demo_retrieval_not_configured` and does not move the cursor.

| Env var | Required | Notes |
| --- | --- | --- |
| `STEAM_BOT_REFRESH_TOKEN` | preferred | Output of `python -m steamlink.bot_login`. No password stored. |
| `STEAM_BOT_REFRESH_TOKEN_FILE` | alternative | Path to a file containing the token (e.g. a Render secret file). |
| `STEAM_BOT_USERNAME` + `STEAM_BOT_PASSWORD` + `STEAM_BOT_SHARED_SECRET` | fallback | Only if you use a mobile-authenticator `shared_secret` (e.g. from SDA). Username/password without the shared secret is rejected at startup, because the server can't type a Steam Guard code. |

Never commit any of these. Set them only in Render's environment (or secret files).

## Create the bot account (one time)

1. Create a **new** Steam account just for this (not your main): use a fresh email address you control.
2. Verify the email. Enable **Steam Guard via email** (the default) — that's enough for the refresh-token flow.
3. Add **Counter-Strike 2** to the account's library (free: store page → *Play Game* / *Add to Library*). The GC only talks to accounts that own app 730.
4. Optional but recommended: launch CS2 once on that account so it's fully provisioned with the GC. No Prime status is needed to look up match info.
5. Don't use the account for anything else and don't share it. Limited (no-purchase) accounts are expected to work for GC lookups. **Verify this during the smoke test.**

## Get a refresh token (one time, on your own machine)

```sh
cd services/prediction_api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m steamlink.bot_login --out ~/.config/csa/steam-bot-refresh-token
# enter username, password (hidden), then the Steam Guard code from email
```

Copy the file's contents into `STEAM_BOT_REFRESH_TOKEN` on Render, then delete
the local file. Steam refresh tokens last roughly 200 days. When the bot can no
longer sign in, sync returns `demo_bot_auth_failed`. Re-run the command to get a
new token. Changing the bot's password or using *Deauthorize all devices* revokes the token.

## Behaviour and limits

- The bot logs in lazily on the first demo lookup and keeps one GC session per API process.
- At most one GC request every 2 s per process. A process that just lost its connection waits 60 s before logging in again.
- GC not ready/timeout → `demo_not_ready` (cursor not advanced). The same match timing out 3 times in a row is recorded as `unavailable` so one dead match can't block sync.
- An empty match list or a match with no replay URL is recorded as `unavailable` (`demo_unavailable`). Demos expire on Valve's side after a few weeks.
- Invalid or expired credentials → `demo_bot_auth_failed`. These are sticky until the process restarts with new credentials.
- Any URL the GC returns must pass the `replayN.valve.net` allowlist before it is downloaded.
- steam.py loggers are capped at WARNING. Tokens, passwords, auth codes and share codes are never logged.

## Manual smoke test (needs the bot account; not yet run)

Run locally first, with SQLite, before touching Render:

1. `export DATABASE_URL=sqlite:///./local.db TOKEN_ENCRYPTION_KEYS=<fernet key> SESSION_SECRET=<32+ chars> PUBLIC_API_URL=http://localhost:8000 FRONTEND_URL=http://localhost:5173 STEAM_WEB_API_KEY=<key> STEAM_BOT_REFRESH_TOKEN=<token> SESSION_COOKIE_SECURE=false`
2. `python -m steamlink.migrate` then `uvicorn main:app --port 8000`.
3. GC lookup alone (no web flow):
   ```sh
   python - <<'PY'
   import os
   from steamlink.gc import GameCoordinatorDemoLocator
   from steamlink.gc_steamio import SteamioGameCoordinator
   from steamlink.sharecode import decode
   gc = SteamioGameCoordinator(refresh_token=os.environ["STEAM_BOT_REFRESH_TOKEN"])
   print(GameCoordinatorDemoLocator(gc).demo_url(decode("CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx")))
   PY
   ```
   - [ ] Bot logs in with the refresh token (no prompt).
   - [ ] GC becomes ready within ~60 s.
   - [ ] A recent (<2 weeks) share code returns a `http://replayN.valve.net/730/...dem.bz2` URL.
   - [ ] A very old share code returns `DemoUnavailable`, or times out three times and is then treated as unavailable.
4. Full flow in the browser (`pnpm dev` in `apps/web`, proxy `/api` → `:8000`):
   - [ ] Sign in through Steam, then link a Game Authentication Code and recent share code. Valve accepts them.
   - [ ] **Sync matches** imports a match. The `.dem.bz2` download stays under the size limits, the temp dir is removed afterwards, and demoparser2 parses it.
   - [ ] The round report shows plausible opening kills (compare a couple of rounds with the in-game replay) and actual winners.
   - [ ] Revoke the refresh token (change the bot password), then sync → `demo_bot_auth_failed`, and the cursor is unchanged.
   - [ ] Logs contain no token, password, auth code or share-code query strings.
5. Record the demoparser2 column names actually seen (`round_end.winner`, `player_death.attacker_team_num`, etc.). If any differ, fix `Demoparser2Parser`.
