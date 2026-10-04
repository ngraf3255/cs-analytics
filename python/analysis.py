import pandas as pd
from sqlalchemy import create_engine, URL
from getpass import getpass

password = getpass("Postgres password: ")

url = URL.create(
    "postgresql+psycopg2",
    username="postgres",
    password=password,
    host="localhost",
    port=5432,
    database="cs2_analytics"
)

engine = create_engine(url)

query = """
SELECT *
FROM rounds
WHERE winner_side IN ('ct', 't');
"""

df = pd.read_sql(query, engine)

print(df.head())
print(df.shape)
print(df.columns.tolist())


model_df = df[
    [
        "map_name",
        "opening_kill_side",
        "opening_kill_seconds",
        "opening_weapon",
        "winner_side"
    ]
].copy()

model_df = model_df[
    model_df["opening_kill_side"].isin(["ct", "t"])
].dropna()

# Target: 1 = T wins, 0 = CT wins
model_df["t_win"] = (model_df["winner_side"] == "t").astype(int)

print(model_df.head())
print(model_df.shape)
print(model_df.isnull().sum())



# Given the map, opening-kill side, opening-kill timing, and opening weapon, which side will win the round: CT or T?
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

X = model_df[
    ["map_name", "opening_kill_side", "opening_kill_seconds", "opening_weapon"]
]
y = model_df["t_win"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

categorical = ["map_name", "opening_kill_side", "opening_weapon"]

preprocessor = ColumnTransformer([
    ("cat", OneHotEncoder(handle_unknown="ignore"), categorical)
], remainder="passthrough")

model = Pipeline([
    ("preprocessor", preprocessor),
    ("model", LogisticRegression(max_iter=1000))
])

model.fit(X_train, y_train)

predictions = model.predict(X_test)

print("Accuracy:", round(accuracy_score(y_test, predictions), 3))

print("Baseline accuracy:", round(y_test.value_counts(normalize=True).max(), 3))
from sklearn.metrics import classification_report, confusion_matrix

print(confusion_matrix(y_test, predictions))
print(classification_report(y_test, predictions))




from sklearn.ensemble import RandomForestClassifier

rf_model = Pipeline([
    ("preprocessor", preprocessor),
    ("model", RandomForestClassifier(
        n_estimators=200,
        random_state=42
    ))
])

rf_model.fit(X_train, y_train)

rf_predictions = rf_model.predict(X_test)

print("Random Forest Accuracy:",
      round(accuracy_score(y_test, rf_predictions), 3))

print(classification_report(y_test, rf_predictions))




#importance features
import pandas as pd

feature_names = model.named_steps["preprocessor"].get_feature_names_out()
coefficients = model.named_steps["model"].coef_[0]

importance = pd.DataFrame({
    "feature": feature_names,
    "coefficient": coefficients
})

importance["abs_coefficient"] = importance["coefficient"].abs()

print(
    importance
    .sort_values("abs_coefficient", ascending=False)
    .head(15)
    [["feature", "coefficient"]]
)





''' Weapon Class Model'''

query_weapon_class = """
SELECT
    r.*,
    k.weapon_class AS opening_weapon_class
FROM rounds r
JOIN kills k
    ON r.match_id = k.match_id
    AND r.map_id = k.map_id
    AND r.round = k.round
    AND k.is_opening_kill = TRUE
WHERE r.winner_side IN ('ct', 't');
"""

df_weapon_class = pd.read_sql(query_weapon_class, engine)

model_df_weapon_class = df_weapon_class[
    [
        "map_name",
        "opening_kill_side",
        "opening_kill_seconds",
        "opening_weapon_class",
        "winner_side"
    ]
].copy()

model_df_weapon_class = model_df_weapon_class[
    model_df_weapon_class["opening_kill_side"].isin(["ct", "t"])
].dropna()

model_df_weapon_class["t_win"] = (
    model_df_weapon_class["winner_side"] == "t"
).astype(int)

X_wc = model_df_weapon_class[
    ["map_name", "opening_kill_side", "opening_kill_seconds", "opening_weapon_class"]
]
y_wc = model_df_weapon_class["t_win"]

X_train_wc, X_test_wc, y_train_wc, y_test_wc = train_test_split(
    X_wc, y_wc, test_size=0.2, random_state=42
)

categorical_wc = [
    "map_name",
    "opening_kill_side",
    "opening_weapon_class"
]

preprocessor_wc = ColumnTransformer([
    ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_wc)
], remainder="passthrough")

model_wc = Pipeline([
    ("preprocessor", preprocessor_wc),
    ("model", LogisticRegression(max_iter=1000))
])

model_wc.fit(X_train_wc, y_train_wc)

predictions_wc = model_wc.predict(X_test_wc)

print(
    "Weapon Class Logistic Regression Accuracy:",
    round(accuracy_score(y_test_wc, predictions_wc), 3)
)


#Weapon Features
feature_names_wc = model_wc.named_steps["preprocessor"].get_feature_names_out()
coefficients_wc = model_wc.named_steps["model"].coef_[0]

importance_wc = pd.DataFrame({
    "feature": feature_names_wc,
    "coefficient": coefficients_wc
})

importance_wc["abs_coefficient"] = importance_wc["coefficient"].abs()

print("\nWeapon Class Feature Importance")
print(
    importance_wc
    .sort_values("abs_coefficient", ascending=False)
    .head(15)
    [["feature", "coefficient"]]
)


#export csv
model_results = pd.DataFrame({
    "Model": [
        "Baseline",
        "Logistic Regression",
        "Random Forest",
        "Weapon Class Logistic"
    ],
    "Accuracy": [
        y_test.value_counts(normalize=True).max(),
        accuracy_score(y_test, predictions),
        accuracy_score(y_test, rf_predictions),
        accuracy_score(y_test_wc, predictions_wc)
    ]
})

model_results.to_csv("tableau/model_accuracy.csv", index=False)

print("\nExported tableau/model_accuracy.csv")

# Save the fitted pipeline and categorical input values for the API/UI.
from pathlib import Path
import joblib

categorical_encoder = model.named_steps["preprocessor"].named_transformers_["cat"]
map_options = categorical_encoder.categories_[0].tolist()
weapon_options = categorical_encoder.categories_[2].tolist()
model_path = Path(__file__).resolve().parents[1] / "model.pkl"

joblib.dump(
    {
        "model": model,
        "weapon_options": weapon_options,
        "map_options": map_options,
    },
    model_path,
)

print(f"Saved model artifact to {model_path}")