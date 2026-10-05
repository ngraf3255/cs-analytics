# Steam-Linked CS2 Match Analysis: Implementation Plan

## Goal

Let a user link their Steam identity, explicitly authorize CS2 match-history access, import eligible match demos, and see round-by-round actual outcomes alongside retrospective predictions from this project's model.

The existing model predicts a **round** from `map_name`, `opening_kill_side`, `opening_kill_seconds`, and `opening_weapon`. It was trained on professional CS2 data. Reports must distinguish observed outcomes from model estimates and must not imply that the estimates were generated during the match or are calibrated for every matchmaking level.

## Current project pieces

- Frontend: React + TypeScript + Vite in `apps/web`.
- API: FastAPI in `services/prediction_api/main.py`.
- Model: root `model.pkl`; current reported held-out accuracy is 71.8%.
- Existing endpoints: `/health`, `/options`, `/predict`.
- API deployment: Render Python 3.11. No production user database or account system exists yet.
- Frontend deployment: Cloudflare Worker at `csgooner.com`.

## Product and Valve API constraints

- Steam OpenID can verify a SteamID; it does not grant access to CS2 match data and must never ask the user for their Steam password.
- Match history requires a separate user-created CS2 Game Authentication Code (`steamidkey`) and a known match sharing code (`knowncode`). Treat the auth code as a secret.
- This follows the onboarding pattern Leetify documents: Steam sign-in identifies the player, then the player supplies a Game Authentication Code once and a recent match share code to start tracking. Leetify says share codes discover newer matches only, expire 30 days after the match, and the CS2 client exposes codes for the last eight matchmaking games while they remain available. See [Leetify's share-code guide](https://leetify.com/blog/share-codes/) and [Steam sign-in explanation](https://leetify.com/blog/phishing/).
- Valve's 2023 notes say third-party match-history access covers Competitive, Wingman, and Premier, and that the known share code used to request the next code must be no more than one month old. This makes forward sync from a recent starting code plausible; it does not promise a full historical backfill.
- The match sharing code identifies a match; the demo must still be retrieved and parsed. Match/demo availability and parser compatibility can change with CS2 updates.
- Current model inputs and categorical vocabularies must be compared with parsed demo data. Unsupported map/weapon values should be reported as unscored, not silently coerced.

## MVP scope

1. Authenticate the visitor with Steam OpenID and bind the verified SteamID to an app user.
2. Explain and request the separate Game Authentication Code plus a recent match sharing code. Never request Steam credentials or Steam Guard codes.
3. Store the Game Authentication Code encrypted at rest and keep the last processed sharing code as an import cursor.
4. On explicit **Sync matches** action, request newer share codes, retrieve eligible demos, parse them, and save only parsed match/round data. Delete temporary compressed and decompressed demo files after parsing.
5. Display imported match summaries and round analyses. Make actual outcomes, model predictions, and unscored rounds visually distinct.
6. Provide disconnect, credential revocation guidance, and delete-my-data controls.

### Out of scope for the first release

- Passive high-frequency polling; use user-triggered sync first and add a scheduled worker only after rate and hosting behavior are understood.
- Claiming all old match history can be fetched from a Steam account automatically.
- Faceit/community-server match imports.
- Public profiles, friend lookups, social features, or sharing another player's match history.
- Retraining the model in this slice.

## Implementation steps

### 1. Validate Steam and demo retrieval end to end

- Implement a small local proof of concept for Steam OpenID verification, `GetNextMatchSharingCode`, share-code decoding, demo retrieval/decompression, and parsing before committing to the persistent schema.
- Confirm the exact Valve request parameters, response statuses, demo URL construction, compression, and error behavior against current Valve behavior. Do not rely on undocumented guesses or accept a client-supplied SteamID as proof of ownership.
- Verify a current supported CS2 demo with a parser that supports Python 3.11/Linux on Render. Initial parser candidate: [`demoparser2`](https://github.com/LaihoE/demoparser); pin the specific validated version.
- Check the training pipeline's opening-kill definition and normalize map, side, weapon, and kill-time features consistently.
- Stop or revise the approach if current CS2 demos cannot be retrieved or parsed reliably.

### 2. Build storage-independent code; connect the homelab database last

- Keep repository work moving with a storage interface, schema/migrations, and local development configuration. Do not store production credentials in source control or use Render's ephemeral filesystem for persistent account data.
- The final infrastructure step is to connect the API to the user's homelab PostgreSQL through `DATABASE_URL`, apply migrations, and verify backups, firewall/TLS, and connectivity from Render.
- Add tables for users, encrypted Steam match-history auth code, latest processed share code, sync status, match metadata, and parsed rounds.
- Keep encryption keys and Steam API credentials (if required) in Render environment variables, never in source control or the frontend. Use authenticated encryption; support key rotation planning.
- Do not store raw `.dem` files after parsing. Define data deletion for imported rows, account disconnect, and failed/partial sync.
- Do not enable production account linking or match sync until the homelab DB is connected and credential/cursor persistence is verified.

### 3. Implement account and sync API

- Add server-side Steam OpenID login with a checked `state`/nonce, exact return URL validation, and verified claimed SteamID.
- Use secure server-issued sessions. Configure CORS and cookies deliberately for `csgooner.com` and the API domain; do not expose Steam secrets to JavaScript.
- Add link/update/revoke credential endpoints. Validate credentials by making a server-side Valve request without logging request query strings or auth codes.
- Add `POST /steam/sync` with per-user locking, bounded imports per request, idempotency, timeout handling, and sync progress/status.
- Fetch new share codes incrementally from the stored cursor. Handle no-new-matches, invalid/revoked code, stale cursor, demo not ready, Valve rate/error response, and parser failure with safe user-facing states.
- Download each demo to a randomly named temporary file, enforce download and decompressed-size limits, parse, delete temp files in `finally`, and persist normalized match/round data.
- Rate-limit sync and protect against SSRF: never accept arbitrary demo URLs or arbitrary hosts from the client.

### 4. Build Steam link and match report UI

- Add **Connect Steam** using the official Steam sign-in flow, then show the verified account identity.
- Present a clear explanation/link for generating the separate match-history auth code and where to find a recent match sharing code in CS2.
- Provide explicit consent before storing access; mask any code after submission and offer disconnect/delete controls.
- Add **Sync matches**, sync progress/errors, imported match list, and per-match report.
- For each round show opening kill details, actual winner, model probability/prediction when scorable, and why any row could not be scored.
- Show that prediction accuracy on the user's demos is not yet calibrated to their matchmaking population.

### 5. Connect the homelab database and deploy safely (final infrastructure step)

- Configure `DATABASE_URL` to the homelab PostgreSQL only after the application flow and migrations are ready. Ensure the database is reachable securely from Render, with backups and restricted inbound access.
- Configure the encryption key, public API URL, HTTPS session behavior, allowed origins, and Steam/OpenID return URL.
- Pin parser and related dependencies and verify Linux/Python 3.11 deployment.
- Measure sync CPU, memory, disk, archive size, and request duration with representative demos before allowing multiple matches per sync.
- Start with a user-triggered, small-batch sync. Move parsing to a background worker/queue before adding scheduled imports or larger batches.

## Acceptance criteria

- Steam login proves account ownership through a verified OpenID response; no Steam password or Steam Guard code is requested or stored.
- The user must explicitly provide and authorize the separate CS2 match-history code and starting share code.
- A successful sync imports only newer available match codes, updates the cursor transactionally, and can be safely retried without duplicate matches.
- Imported rounds show the actual winner and separate retrospective probability estimate; unsupported rounds are clearly marked.
- Raw demo files and credentials never enter logs. Raw demo files are removed after parse; auth codes are encrypted at rest.
- Users can disconnect and delete stored credentials and parsed match data.
- The UI explains that older match history may need individual share codes and that this connection does not guarantee an entire lifetime history import.

## Follow-up options

- ~~Offer manual `.dem` upload as an alternative for demos not available via Steam match history.~~ Implemented (`POST /matches/upload`).
- Add scheduled incremental sync with conservative polling after user-triggered sync works reliably.
- Calibrate or retrain a separate model on consented matchmaking data only after collecting enough representative rounds and defining retention/consent rules.

## Status

- [x] Steam integration replaces the original manual-upload-first direction.
- [x] Plan recorded before implementation.
- [x] Defer the homelab PostgreSQL connection to the final infrastructure step.
- [ ] Validate Steam auth-code/share-code/demo flow with a test account and demo. *(Not done: GetNextMatchSharingCode status handling is only tested with mocks. The demoparser2 column mapping is now verified on real CS2 demos (see below), including a real Valve matchmaking replay file, but that file came from a public test-data set, not from our own GC/replay-server download.)*
- [x] Implement Steam identity and secure sessions. *(Server-side OpenID verification, signed server-side sessions, CSRF header + origin check; tested with mocked Steam responses.)*
- [ ] Implement durable data and incremental import. *(Partly done: storage interface, migrations, encrypted auth codes, bounded idempotent sync with transactional cursor. **Sync now downloads and parses in the background** (migration `0004_sync_jobs`, same worker as uploads): `POST /steam/sync` only walks the share-code history (up to `SYNC_MAX_MATCHES_PER_REQUEST`, now 3) and queues one job per new match, advancing the cursor in the same transaction; known matches are skipped without a download (share code / Valve match id, and the demo SHA-256 once downloaded); the queue cap and parse slot apply; transient failures (`demo_not_ready`, bot sign-in) are retried by the next sync up to `SYNC_JOB_MAX_ATTEMPTS`, permanent ones become stubs; interrupted sync jobs re-download after a restart; the Sync button shows per-match progress and resumes after a reload. Tested with a faked Valve fetcher serving real local demos (SQLite + PostgreSQL). At 0.1 CPU / 512 MB (Valve faked with a local file): the sync request answers in 0.2-0.3 s; a Valve-style 72 MB `.dem.bz2` imports in ~2 minutes, a 441 MB `.dem` in ~53 s. Not yet tested against Valve itself (no bot account). Game Coordinator demo-URL lookup is implemented (`steamlink/gc.py`, `gc_steamio.py`, steam.py `steamio==1.1.3`) behind bot-credential env vars. It still needs a dedicated bot account; then the one-command live check `python -m steamlink.live_check` (`docs/live-e2e-checklist.md`) proves the live chain. Database for now: Render managed PostgreSQL (`docs/deploy-render.md`). The homelab has no reachable Postgres yet.)*
- [ ] Implement parsed match analysis and UI. *(Mostly done. Manual `.dem`/`.dem.bz2` upload (`POST /matches/upload` + upload button with progress next to Sync) is the interim path while the demo bot has no account. A match that arrives by upload and by Steam sync (either order) is stored once: dedupe on share code, Valve match id, or demo SHA-256 (migration `0002`, `tests/test_dedupe.py`; the sync side of that is tested with fakes only). **Real-tested:** demoparser2 0.42.0 parsing + feature mapping + model scoring + storage + report on a real CS2 SourceTV demo (demoparser2's public `test_demo.dem`, de_mirage, 10 rounds, 9 scored), on **SQLite and PostgreSQL 17** (`tests/test_real_demo.py`; full suite runs on both, also in CI), plus a local uvicorn run of the exact Render start command against PostgreSQL (migrations, upload, `.bz2` dedupe, report, restart). This found and fixed a mapping bug: post-round exit frags were counted as the next round's opening kill. **Full-length demos (2026-10-05):** a 108 MB Valve matchmaking replay (de_ancient, 8 rounds), a 372 MB FACEIT demo (de_mirage, 25 rounds, also as `.dem.bz2`) and a 441 MB HLTV demo (de_nuke, 18 rounds), all public awpy test demos, go through the real-demo E2E and parse identically in the old and new parser code. **Still mocked / untested:** downloading a demo ourselves from Valve's replay servers (needs the demo bot), the Steam share-code sync path end to end, and whether Valve's served demo is byte-identical to the client's saved copy (hash dedupe assumes so; share-code dedupe doesn't).)*
- [x] Analytics across a user's previous matches and rounds. *(2026-10-05: `GET /matches/summary` (`steamlink/analytics.py`) aggregates every match in the user's list, shared matches included: totals, the round-win model's hit rate, Brier score and calibration bins over previous scored rounds next to an always-back-the-opening-kill baseline, CT/T round wins overall and per map, opening-kill conversion by side and weapon, unscored reasons, and recent form (last N matches vs the ones before). The web shows it as an "Across your matches" panel above the match list. Tested on SQLite and PostgreSQL, including real demos; `python -m steamlink.live_check` prints a one-line summary. **Personal (2026-10-05):** the parse now stores every player's side each round by SteamID (`player_rounds`, migration `0007`; halftime swap handled; per player, so each owner of a shared match sees their own team) plus kills, deaths, opening kill / death and survival, verified on four real demos against the demo's own player state and scoreboard. `GET /matches/summary` has a `you` section (own round win rate overall, as CT / T and per map, match results, K/D, opening duels, recent form) and the report highlights the player's team and rounds; the all-player numbers stay, labelled, and cover demos the player isn't in (e.g. pro uploads). Steam-synced matches store when they were played (Game Coordinator match time); uploads show the import date, labelled "Added". Matches parsed before `0007` show as "not tracked" until their demo is uploaded again. **Data quality (2026-10-05):** warmup / knife rounds before the last `begin_new_match` are left out of all numbers, not only per-player ones (FACEIT test demo 25 -> 24 rounds, `rounds_count` = final score sum on all four test demos); a round recorded without any spawn takes its players from the next round; matches parsed by an older parser carry `outdated` (migration `0008_parse_version`, flags only) and re-uploading the same demo replaces their rounds (no server-side re-parse: demos and replay URLs aren't kept); uploads keep the "Added" date because no CS2 demo field holds a match date.)*
- [x] Steam link UI, Leetify-style. *(2026-10-05: step-by-step guide with Valve's exact pages (Access to Your Match History, which also shows "Your most recently completed match token"; Valve's match-history docs; share-code guide); codes accepted as pasted (lowercase / no-dash auth code, CS2 `steam://` share link) on both web and API; format hints per box and swapped-code detection before Valve is asked; Valve's rejections shown under the box they belong to; after a sync that only new codes fix (`/me` `match_access.needs_relink`) a re-link banner opens the form: an expired share code needs only a new share code (stored auth code kept), a revoked auth code needs a new auth code; Sync is paused meanwhile; disconnect / relink / delete controls; the empty match list offers both Sync and upload. Tested with fakes (pytest + vitest); not yet against Valve.)*
- [ ] Validate Render resource limits and deployment configuration. *(Config ready, not deployed. `render.yaml` now defines a managed Postgres (`csgooners-db`, free plan: expires after 30 days, no backups) wired to `DATABASE_URL`, migrations on start (no pre-deploy on free), all env vars (secrets `sync: false` / `generateValue`), and `api.csgooner.com`; it validates against Render's schema. Render has no request-body cap and allows 100-minute responses, so the 1 GiB upload cap is ours (`UPLOAD_MAX_BYTES`). **Memory on 512 MB, measured on full-length demos:** parsing now runs in a short-lived child process (one demoparser2 pass, only the needed columns), and upload and sync share one parse slot. Peak anonymous memory (API + parse child) for upload -> parse -> store -> score is ~228 MB on the 372 MB / 25-round demo and ~232 MB on the 441 MB / 18-round demo (before: ~212-216 MB, but the API then kept ~80 MB of parser heap; now it drops back to ~141 MB). The memory-mapped demo pushes total memory past 512 MB, but it's reclaimable file cache: the Render start command in a 512 MB, no-swap cgroup took real HTTP uploads of all four demos with no OOM kill, and in a 200 MB cgroup only the parse child is killed (`demo_parse_failed`) while the API stays up. Gate: `scripts/measure_demo_memory.py` and `tests/test_demo_memory.py` (opt-in, 300 MB anon budget). **CPU on the free plan (0.1 CPU), measured 2026-10-05** in a 512 MB + `cpu.max` 0.1 CPU cgroup with real HTTP uploads: a full-length `.dem` takes 15-54 s end to end (441 MB: ~4.7 CPU-s), a 260 MB `.dem.bz2` ~7 minutes (~40 CPU-s, mostly bzip2); in one synchronous request that was 374 s for the `.bz2`. So **upload parsing now runs as a background job** (migration `0003_upload_jobs`, `steamlink/jobs.py`): `POST /matches/upload` answers `202` with a job in 1-15 s, one in-process worker parses jobs in order (sharing the parse slot with sync), `GET /matches/upload/{job_id}` reports stage/progress/error, the UI polls it and resumes after a reload, a queue cap (`UPLOAD_QUEUE_MAX`, 3) protects the disk, and on restart interrupted jobs are resumed if their file survived or failed with `server_restarted` (Render's disk is ephemeral). `/health` stayed responsive (worst ~2 s) and the 600 s parse timeout never fired. Still not measured on Render itself. `api.csgooner.com` already CNAMEs to the live service but isn't attached on Render (Cloudflare error 1000). Remaining steps: `docs/deploy-render.md`. Steam sync now runs on the same job worker (see the import item above).)*
