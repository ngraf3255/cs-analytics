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
5. Don't use the account for anything else and don't share it. Limited (no-purchase) accounts are expected to work for GC lookups. **The live check (step 4, `python -m steamlink.live_check`) verifies this.**

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

## Live check (one command; needs the bot account, not yet run live)

`python -m steamlink.live_check` runs the whole live chain with the production
code and prints PASS / FAIL / SKIP per step. It stops at the first problem and
says exactly what is missing (exit 0 = all passed, 1 = a step failed,
2 = a prerequisite is missing):

| # | Step | What it proves |
| --- | --- | --- |
| 1 | config + inputs | env vars and inputs are present and well-formed (same settings loader as the API) |
| 2 | Steam Web API key | `STEAM_WEB_API_KEY` is accepted (`ISteamUser/GetPlayerSummaries`) and the Steam ID exists |
| 3 | match history walk | the auth code + known share code work; walks up to `--walk` (3) newer codes with `GetNextMatchSharingCode` (checks the 200/202/403/412 mapping) |
| 4 | GC demo URL (bot) | the bot signs in with its refresh token, reaches the CS2 GC and gets the newest match's `replayN.valve.net` URL |
| 5 | demo download | downloads the `.dem.bz2` with the production fetcher (allowlist, size caps), decompresses it; prints size and time |
| 6 | parse + model score | demoparser2 parse (child process, as on Render) and `model.pkl` round scoring |
| 7 | store + round report | the real `POST /steam/sync` logic queues a `steam_sync` job, the job worker imports and stores it, then prints the per-round report (same JSON as `GET /matches/{id}`, `--report-json FILE` saves it) |

Run it on your own machine (not CI), from `services/prediction_api` with the
requirements installed:

```sh
cd services/prediction_api
export STEAM_WEB_API_KEY="$(cat ~/.config/csa/steam-web-api-key)"
export STEAM_BOT_REFRESH_TOKEN_FILE=~/.config/csa/steam-bot-refresh-token
export TOKEN_ENCRYPTION_KEYS="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
python -m steamlink.live_check \
  --steam-id 7656119XXXXXXXXXX \
  --known-code CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx \
  --auth-code-file ~/.config/csa/auth-code \
  --report-json /tmp/live-report.json
```

- Inputs come only from env / arguments. `CSA_LIVE_STEAM_ID`, `CSA_LIVE_AUTH_CODE`
  and `CSA_LIVE_KNOWN_CODE` work instead of the flags. Prefer `--auth-code-file`
  over `--auth-code`, because the flag ends up in shell history. Secrets are never printed, and share codes are masked.
- Storage is a throwaway SQLite file by default. `DATABASE_URL` from the
  environment is ignored on purpose. `--database-url URL` stores into a real
  database (e.g. Render's) and restores that user's previous link afterwards.
- Offline version (fake Valve HTTP + fake GC, everything else real; this is what
  CI runs): `python -m steamlink.live_check --fake --demo /path/to/match.dem.bz2`.
- What's still needed before it can pass live, and who does it:
  `docs/live-e2e-checklist.md`.

After it passes, check these by hand:

- [ ] A very old share code → step 4 reports "knows no such match" or a GC timeout (sync then records it as unavailable after 3 timeouts).
- [ ] Revoke the refresh token (change the bot password) → step 4 says the bot's login was rejected. In the app, sync shows `demo_bot_auth_failed` and the cursor doesn't move.
- [ ] Compare a couple of rounds' opening kills in the report with the in-game replay.
- [ ] The output and API logs contain no token, password, auth code or share-code query string.
- [ ] If demoparser2's columns on the Valve-served demo differ (`round_end.winner`, `player_death.attacker_team_num`, ...), step 6 fails or reports unscored rounds; fix `Demoparser2Parser`.
