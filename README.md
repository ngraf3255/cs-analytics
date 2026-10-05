# CS2 Round Analytics

An analytics project built around professional Counter-Strike 2 rounds. It combines SQL and Python analysis, a Tableau dashboard, and a web app that predicts round winners from the opening duel.

![CS2 Dashboard](images/dashboard.png)

[View the interactive Tableau dashboard](https://public.tableau.com/app/profile/nicholas.hinkel/viz/CS2-Analytics/CS2RoundAnalytics?publish=yes)

## Round predictor

The new prediction app has a React + TypeScript frontend and a FastAPI service that loads the trained scikit-learn model. The frontend deploys as static assets to the Cloudflare Worker `cs-analytics`; `render.yaml` describes the free Render web service for the API. The existing Streamlit deployment remains available while the new app is being connected to the domain:

[Open the current Streamlit predictor](https://cs-analytics-4fqredurpvknehdkr3svw4.streamlit.app/)

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

The local frontend calls the API through the Vite development proxy. API documentation is available at `http://localhost:8000/docs`. Frontend commands (`dev`, `build`, and `preview`) are available from the root through pnpm. The devcontainer installs Node 20 and pnpm 10.30.3.

### Deploy

1. Create the API service in Render from the repository's `render.yaml` blueprint. The service uses the repository root so it can load `model.pkl`.
2. Attach `api.csgooners.com` to the Render service and add the DNS record Render specifies in Cloudflare.
3. In Cloudflare, open the `cs-analytics` Worker and add `csgooners.com` and `www.csgooners.com` under **Settings → Domains & Routes → Add → Custom Domain**. Cloudflare creates the DNS records and certificates. Remove any conflicting CNAME records first if Cloudflare reports a conflict.
4. In GitHub repository **Settings → Secrets and variables → Actions**, add secrets `CLOUDFLARE_API_TOKEN` (with the **Edit Cloudflare Workers** permission scoped to your account) and `CLOUDFLARE_ACCOUNT_ID`. Optionally set the repository variable `VITE_API_BASE_URL`; it defaults to `https://api.csgooners.com`.
5. Push frontend changes to `main` to build and deploy automatically, or run **Deploy frontend to Cloudflare Worker** manually from the Actions tab. If Cloudflare Workers Builds is also connected to this repository, disable its automatic production deployment to avoid duplicate deploys.

The Render free service sleeps after 15 minutes without traffic, so the first API request after idle may take about a minute to wake it.

## Project overview

The analysis uses 16,527 professional CS2 rounds to study round outcomes and train a model that estimates whether CT or T wins.

### Tech stack

- PostgreSQL, SQL, Python, pandas, and scikit-learn
- React, TypeScript, and Vite
- FastAPI and Render for the prediction service
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
└── render.yaml              # Render API deployment blueprint
```

## Data source

[OpenCS2 Dataset](https://huggingface.co/datasets/blanchon/opencs2_dataset).
