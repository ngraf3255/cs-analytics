"""HTTP API for the trained CS2 round winner model."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from steamlink.config import load_settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = Path(os.environ.get("MODEL_PATH", REPOSITORY_ROOT / "model.pkl"))
if not MODEL_PATH.is_absolute():
    MODEL_PATH = REPOSITORY_ROOT / MODEL_PATH

if not MODEL_PATH.is_file():
    raise RuntimeError(f"Model artifact was not found at {MODEL_PATH}")

saved_model = joblib.load(MODEL_PATH)
model = saved_model["model"]
map_options = [str(value) for value in saved_model["map_options"]]
weapon_options = [str(value) for value in saved_model["weapon_options"]]

settings = load_settings()
allowed_origins = settings.allowed_origins

app = FastAPI(
    title="CS2 Round Prediction API",
    description="Predicts a round winner from the opening kill and map conditions.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    # Credentials are needed for the Steam session cookie. Origins come from
    # ALLOWED_ORIGINS and may never be "*" (enforced in load_settings).
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


class PredictionInput(BaseModel):
    map_name: str = Field(min_length=1, max_length=80)
    opening_kill_side: Literal["ct", "t"]
    opening_kill_seconds: float = Field(ge=0, le=120)
    opening_weapon: str = Field(min_length=1, max_length=80)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/options")
def options() -> dict[str, list[str]]:
    return {
        "maps": map_options,
        "weapons": weapon_options,
        "opening_kill_sides": ["ct", "t"],
    }


@app.post("/predict")
def predict(payload: PredictionInput) -> dict[str, object]:
    if payload.map_name not in map_options:
        raise HTTPException(status_code=422, detail="Choose a map from the available options.")
    if payload.opening_weapon not in weapon_options:
        raise HTTPException(status_code=422, detail="Choose a weapon from the available options.")

    frame = pd.DataFrame(
        [
            {
                "map_name": payload.map_name,
                "opening_kill_side": payload.opening_kill_side,
                "opening_kill_seconds": payload.opening_kill_seconds,
                "opening_weapon": payload.opening_weapon,
            }
        ]
    )

    prediction = int(model.predict(frame)[0])
    probabilities = model.predict_proba(frame)[0]
    class_probabilities = {
        int(label): float(probability)
        for label, probability in zip(model.classes_, probabilities)
    }
    if 0 not in class_probabilities or 1 not in class_probabilities:
        raise HTTPException(status_code=500, detail="The model has an unexpected class mapping.")

    return {
        "predicted_winner": "t" if prediction == 1 else "ct",
        "probabilities": {
            "ct": class_probabilities[0],
            "t": class_probabilities[1],
        },
    }
