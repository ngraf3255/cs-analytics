# Live E2E checklist (Steam → demo → parse → model → report)

The code for the live path is done and tested offline. These are the only
things left before `python -m steamlink.live_check` can pass against real Steam,
and then before it works on csgooner.com. Do them in order. Each step says who
does it, where, and what to run afterwards.

Status (Oct 5, 2026, 6:30 AM CT): none of these steps is done yet. Step 1 is stuck: Steam's sign-up page showed an hCaptcha, so a person has to create the bot account. Everything else is built and passes offline: all tests on SQLite and PostgreSQL, and `live_check --fake` (7/7 PASS). Deploy baseline: homelab API + Postgres (`docs/deploy-homelab.md`), not Render Postgres.

| # | What | Who / where | Then run |
| --- | --- | --- | --- |
| 1 | **Bot Steam account.** Create a new account for the bot (not your main) at https://store.steampowered.com/join. Steam shows an **hCaptcha**, so a human has to do this in a browser. Then verify the email and keep Steam Guard by email on. Reserved address: `noah+csabot@grafhome.net` (the password is in `/home/box/secrets/csa-steam-bot.env` on the box, never in the repo). Add **Counter-Strike 2** to the bot's library (free: store page → *Play Game*). | Noah, in a browser | nothing yet |
| 2 | **Refresh token.** Sign the bot in once. It asks for the Steam Guard code from the bot's email and writes a token file (0600). | Noah, on a trusted machine: `cd services/prediction_api && pip install -r requirements.txt && python -m steamlink.bot_login --out ~/.config/csa/steam-bot-refresh-token` | `export STEAM_BOT_REFRESH_TOKEN_FILE=~/.config/csa/steam-bot-refresh-token` |
| 3 | **Steam Web API key.** Create it at https://steamcommunity.com/dev/apikey, domain `csgooner.com` (step-by-step: `docs/deploy-homelab.md#steam-web-api-key`). Steam only gives keys to non-limited accounts (at least $5 spent), so use **Noah's main account**, not the bot. | Noah, in a browser | `echo '<key>' > ~/.config/csa/steam-web-api-key && chmod 600 ~/.config/csa/steam-web-api-key` |
| 4 | **Player codes.** (a) Game authentication code: https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128 → *Create authentication code* (`XXXX-XXXXX-XXXX`). (b) A share code of a **recent** match (under about 2 weeks old, so the demo still exists): CS2 → *Watch* → *Your Matches* → copy the share link, or the same help page (`CSGO-xxxxx-…`). | Noah, as the player | `echo '<auth code>' > ~/.config/csa/auth-code && chmod 600 ~/.config/csa/auth-code` |
| 5 | **Run the live check.** Use any fresh Fernet key. Storage is a throwaway SQLite file. | Noah or a bot, on the machine with the files from steps 2-4 | the command below. All 7 steps should say PASS |
| 6 | **Homelab API + Postgres.** Baseline: Proxmox VM, 4 vCPU (R5 5600X host), ~6 GB RAM (5.7 GiB usable) — FastAPI and Postgres on the same VM (Postgres not public). VM `counterstrike` (`192.168.4.54`, Debian 13); public API `api-site.csgooner.com` via host `cloudflared` → `http://localhost:8000`. Set `TOKEN_ENCRYPTION_KEYS`, `STEAM_WEB_API_KEY`, `STEAM_BOT_REFRESH_TOKEN`, and GitHub `VITE_API_BASE_URL=https://api-site.csgooner.com` (or unset). Details: `docs/deploy-homelab.md`. | Noah, home + Cloudflare DNS + GitHub | `curl https://api-site.csgooner.com/health` → `{"status":"ok"}` and `curl https://api-site.csgooner.com/steam/status` → `"enabled":true` and `"upload":true,"guest":false` (uploads need Steam sign-in; guest uploads off by default) |
| 7 | **Site check.** PR #1 is already on `main`. After step 6: on csgooner.com, *Sign in with Steam* → link the auth code + share code (step 4) → *Sync matches*. The match appears with a round report. Optional: live check against the homelab DB with `--database-url` (below). | Noah | browser smoke on csgooner.com |

Live check command (step 5):

```sh
cd services/prediction_api
export STEAM_WEB_API_KEY="$(cat ~/.config/csa/steam-web-api-key)"
export STEAM_BOT_REFRESH_TOKEN_FILE=~/.config/csa/steam-bot-refresh-token
export TOKEN_ENCRYPTION_KEYS="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
python -m steamlink.live_check \
  --steam-id <your SteamID64, 17 digits> \
  --known-code <CSGO-... from step 4> \
  --auth-code-file ~/.config/csa/auth-code \
  --report-json /tmp/live-report.json
```

If a step fails, the message names the cause and the fix. The usual ones:

- **Step 1:** something listed is missing.
- **Step 2:** the key was rejected, or the Steam ID is wrong.
- **Step 3:** HTTP 403 means a wrong or revoked auth code. HTTP 412 means the share code isn't this player's or is too old.
- **Step 4:** the bot login was rejected (run step 2 again), the bot never reached the GC (no CS2 on the account, or Steam is down), or the match has expired.
- **Step 5:** the replay host answered 404 (the demo expired).

Against the homelab database instead of SQLite (optional, after step 6), use a
URL reachable from the machine running the check (LAN or tunnel — do not expose
`5432` publicly). The check links that Steam ID for the run, then restores the
previous link:

```sh
python -m steamlink.live_check ... --database-url 'postgresql://...'
```

Offline (no Steam, runs anywhere; CI runs it in `tests/test_live_check.py`):

```sh
python -m steamlink.live_check --fake --demo /path/to/match.dem.bz2
```

Not covered by the check:

- the Steam OpenID sign-in round trip: test it in the browser at step 7
- the web UI against the deployed API: step 7
- multiple API processes: the job worker assumes one
