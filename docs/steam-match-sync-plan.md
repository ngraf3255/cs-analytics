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
- [ ] Validate Steam auth-code/share-code/demo flow with a test account and demo. *(Not done: GetNextMatchSharingCode status handling and the demoparser2 column mapping are implemented but only tested with mocks.)*
- [x] Implement Steam identity and secure sessions. *(Server-side OpenID verification, signed server-side sessions, CSRF header + origin check; tested with mocked Steam responses.)*
- [ ] Implement durable data and incremental import. *(Partly done: storage interface, migrations, encrypted auth codes, bounded idempotent sync with transactional cursor. Game Coordinator demo-URL lookup is implemented (`steamlink/gc.py`, `gc_steamio.py`, steam.py `steamio==1.1.3`) behind bot-credential env vars. It still needs a dedicated bot account and the live smoke test in `docs/steam-demo-bot.md`. Database for now: Render managed PostgreSQL (`docs/deploy-render.md`). The homelab has no reachable Postgres yet.)*
- [ ] Implement parsed match analysis and UI. *(Partly done: manual `.dem`/`.dem.bz2` upload (`POST /matches/upload` + UI) as a fallback to Steam sync; round feature mapping, scoring with unscored reasons, Connect Steam onboarding, sync, match list and round report UI. Not verified end to end on a real demo.)*
- [ ] Validate Render resource limits and deployment configuration. *(Open: also needs an API domain on csgooner.com, e.g. api.csgooner.com, for first-party session cookies, plus a background worker before larger sync batches.)*
