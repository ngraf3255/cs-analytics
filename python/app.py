import streamlit as st
import pandas as pd
import joblib
from pathlib import Path

saved = joblib.load(Path(__file__).resolve().parents[1] / "model.pkl")

model = saved["model"]
weapon_options = saved["weapon_options"]
map_options = saved["map_options"]

st.title("CS2 Round Winner Predictor")

st.write(
    "Enter the round conditions to predict whether CT or T will win."
)

map_name = st.selectbox(
    "Map",
    map_options
)

opening_kill_side = st.selectbox(
    "Opening Kill Side",
    ["ct", "t"]
)

opening_kill_seconds = st.number_input(
    "Opening Kill Time (seconds)",
    min_value=0.0,
    value=15.0,
    step=0.1
)

opening_weapon = st.selectbox(
    "Opening Kill Weapon",
    weapon_options
)

if st.button("Predict Winner"):

    input_data = pd.DataFrame({
        "map_name": [map_name],
        "opening_kill_side": [opening_kill_side],
        "opening_kill_seconds": [opening_kill_seconds],
        "opening_weapon": [opening_weapon]
    })

    prediction = model.predict(input_data)[0]
    probabilities = model.predict_proba(input_data)[0]

    ct_probability = probabilities[0]
    t_probability = probabilities[1]

    if prediction == 1:
        st.success("Predicted Winner: T")
    else:
        st.success("Predicted Winner: CT")

    st.write(f"CT Win Probability: {ct_probability:.1%}")
    st.write(f"T Win Probability: {t_probability:.1%}")