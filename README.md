# CS2 Round Analytics

An analytics project built around professional Counter-Strike 2 rounds. It combines SQL and Python analysis, a Tableau dashboard, and a web app that predicts round winners from the opening duel.

![CS2 Dashboard](images/dashboard.png)

[View the interactive Tableau dashboard](https://public.tableau.com/app/profile/nicholas.hinkel/viz/CS2-Analytics/CS2RoundAnalytics?publish=yes)

## Round predictor

The new prediction app has a React + TypeScript frontend and a FastAPI service that loads the trained scikit-learn model. The frontend is set up for Cloudflare Pages; `render.yaml` describes the free Render web service for the API. The existing Streamlit deployment remains available while the new app is being connected to the domain:

[Open the current Streamlit predictor](https://cs-analytics-4fqredurpvknehdkr3svw4.streamlit.app/)

### Run locally

Start the API in one terminal:

```sh
cd services/prediction_api
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Start the frontend in another terminal:

```sh
cd apps/web
npm install
npm run dev
```

The local frontend calls the API through the Vite development proxy. API documentation is available at `http://localhost:8000/docs`.

### Deploy

1. Create the API service in Render from the repository's `render.yaml` blueprint. The service uses the repository root so it can load `model.pkl`.
2. Attach `api.csgooners.com` to the Render service and add the DNS record Render specifies in Cloudflare.
3. Create a Cloudflare Pages project with `apps/web` as its root directory, `npm run build` as its build command, and `dist` as its output directory.
4. Set the Pages build environment variable `VITE_API_BASE_URL` to `https://api.csgooners.com`.
5. Add `csgooners.com` and `www.csgooners.com` as Pages custom domains. Verify that the app can load options and return a prediction.

The Render free service sleeps after 15 minutes without traffic, so the first API request after idle may take about a minute to wake it.

## Project overview

The analysis uses 16,527 professional CS2 rounds to study round outcomes and train a model that estimates whether CT or T wins.

### Tech stack

- PostgreSQL, SQL, Python, pandas, and scikit-learn
- React, TypeScript, and Vite
- FastAPI and Render for the prediction service
- Cloudflare Pages for frontend hosting
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
