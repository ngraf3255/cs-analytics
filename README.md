# CS2 Round Analytics

An analytics project built around professional Counter-Strike 2 rounds. It combines SQL and Python analysis, a Tableau dashboard, and a web app that predicts round winners from the opening duel.

![CS2 Dashboard](images/dashboard.png)

[View the interactive Tableau dashboard](https://public.tableau.com/app/profile/nicholas.hinkel/viz/CS2-Analytics/CS2RoundAnalytics?publish=yes)

## Round predictor

The new prediction app has a React + TypeScript frontend and a FastAPI service that loads the trained scikit-learn model. The frontend deploys as static assets to the Cloudflare Worker `cs-analytics`; the API baseline is the homelab VM (`docs/deploy-homelab.md`). The existing Streamlit deployment remains available while the new app is being connected to the domain:

[Open the current Streamlit predictor](https://cs-analytics-4fqredurpvknehdkr3svw4.streamlit.app/)

The next planned feature is Steam-linked match sync; see the [implementation plan](docs/steam-match-sync-plan.md).

### Run locally

Install the frontend dependencies and start the frontend from the repository root:

```sh
pnpm install
pnpm dev
```

In a second terminal, start the API:

```sh
cd services/prediction_api
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

The local frontend calls the API through the Vite development proxy. API documentation is available at `http://localhost:8000/docs`. Frontend commands (`dev`, `build`, and `preview`) are available from the root through pnpm. The devcontainer installs Node 22 and pnpm 10.30.3.

### Deploy

**Baseline:** FastAPI + Postgres on a home Proxmox VM (2 vCPU / 8 GB); site on Cloudflare; API at `api.csgooner.com`. Compose/env/Caddy: [`deploy/homelab/`](deploy/homelab/). Steps: [`docs/deploy-homelab.md`](docs/deploy-homelab.md). `render.yaml` is optional/legacy.

1. On the homelab VM: copy `deploy/homelab/.env.example` → `.env`, set secrets, then `docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d --build` (or the systemd unit in the same folder).
2. Point Cloudflare DNS for `api.csgooner.com` at home (`REPLACE_WITH_HOME_PUBLIC_IP` or a tunnel). Prefer **DNS only** for large demo uploads. Terminate HTTPS with Caddy (`--profile edge`), nginx, or Cloudflare Tunnel.
3. The frontend production default is `https://api.csgooner.com`. Optionally set GitHub variable `VITE_API_BASE_URL` to the same value and redeploy the Worker.
4. The Wrangler config attaches `csgooner.com` and `www.csgooner.com` to the `cs-analytics` Worker. In GitHub **Settings → Secrets and variables → Actions**, keep secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
5. Push frontend changes to `main` (or run **Deploy frontend to Cloudflare Worker**) to publish the site. If Cloudflare Workers Builds is also connected, disable its automatic deploy; it does not target `apps/web`.

## Project overview

The analysis uses 16,527 professional CS2 rounds to study round outcomes and train a model that estimates whether CT or T wins.

### Tech stack

- PostgreSQL, SQL, Python, pandas, and scikit-learn
- React, TypeScript, and Vite
- FastAPI (homelab) for the prediction service; Cloudflare for the site
- Cloudflare Workers for frontend hosting
- Tableau for the analytics dashboard

### Database

The relational PostgreSQL database contains:

- 794 match/map records
- 16,527 rounds
- 111,715 kills
- 168,294 player-round records

Tables: `matches`, `rounds`, `kills`, and `round_player`.

### Key findings

- The side getting the opening kill won roughly 70–73% of rounds.
- An untraded opening kill resulted in a 78.5% round win rate.
- When the opening kill was traded within 5 seconds, that dropped to about 54%.
- T-side win rate increased from 20.3% without a bomb plant to 75.8% with a plant.
- Map side advantage varied, with Anubis leaning T and Nuke leaning CT.

### Machine learning

The predictor uses map, opening-kill side, opening-kill timing, and opening weapon. The main logistic regression reached 71.84% accuracy on the project's held-out split; weapon-class logistic regression reached 71.54%, random forest reached 60.07%, and the baseline reached 50.89%.

### SQL optimization

`EXPLAIN ANALYZE` and composite indexing reduced a high-volume kill query from 12.868 ms to 0.085 ms, about a 99.3% reduction.

## Project structure

```text
cs2-analytics/
├── apps/
│   └── web/                 # React + TypeScript prediction frontend
├── data/                    # Parquet source data
├── images/
├── python/                  # Analysis, data loading, and legacy Streamlit app
├── services/
│   └── prediction_api/       # FastAPI service and pinned model runtime
├── sql/
├── tableau/
├── model.pkl                # Trained scikit-learn pipeline and input options
├── deploy/homelab/          # Homelab compose, Dockerfile, Caddy, systemd
└── render.yaml              # Legacy Render API blueprint (not baseline)
```

## Data source

[OpenCS2 Dataset](https://huggingface.co/datasets/blanchon/opencs2_dataset).
