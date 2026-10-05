# Tableau

Two kinds of data live here:

| Files | What | Made by |
| --- | --- | --- |
| `rounds.csv`, `opening_trade.csv`, `model_accuracy.csv`, `CS2-Analytics.twb` | The original dashboard over the professional OpenCS2 dataset | `python/export_tableau.py` (cs2_analytics PostgreSQL) |
| `app/app_rounds.csv`, `app/app_matches.csv` (git-ignored, personal) | Your own matches from the cs-analytics app (Steam sync + uploaded demos) | the app's **Export for Tableau** buttons, or `python/export_app_tableau.py` |

## Export your matches

**From the web app:** sign in, open *Your matches*, and under *Across your matches* press
**Export for Tableau → Rounds CSV** / **Matches CSV**. The files are `cs2-rounds-YYYYMMDD.csv` and
`cs2-matches-YYYYMMDD.csv` (API: `GET /matches/export/rounds.csv`, `GET /matches/export/matches.csv`).

**Offline from the app database** (same columns; read-only; SQLite or PostgreSQL):

```bash
pip install -r services/prediction_api/requirements.txt
python python/export_app_tableau.py --database-url postgresql://user:pass@host:5432/db --steam-id 7656119XXXXXXXXXX
# -> tableau/app/app_rounds.csv, tableau/app/app_matches.csv   (--out-dir / --prefix / --no-personal)
```

`--steam-id` picks whose match list is exported and fills the `you_*` columns (that user must have
signed in to the app once). `python/export_tableau.py` (the pro dataset) is unchanged.

## Connect Tableau

1. Tableau Desktop → **Connect → To a File → Text file** → pick the rounds CSV.
2. Drag the matches CSV next to it and relate them on **`match_id`** (Relationship, many rounds to one match).
3. Check the types on the Data Source page: `match_day` is read as a **Date**. For a date-time use
   `match_date` with a calculated field `DATEPARSE("yyyy-MM-dd'T'HH:mm:ss'Z'", [match_date])` (UTC).
   Flags (`ct_won`, `you_won`, `model_correct`, ...) are 1/0 numbers, so `AVG([ct_won])` is the CT
   round win rate and `AVG([you_won])` your round win rate. Empty cells are nulls.
4. Re-export and **Data → Refresh** (or *Edit connection*) to pick up new matches. Column names are
   stable (`X-Export-Version: 1`); new columns are only added at the end.

Useful starts: `AVG([you_won])` by `you_side` and `map_name`; `AVG([model_correct])` vs
`AVG([opening_kill_side_won])` (model vs "side with the opening kill wins"); `SUM([you_kills]) /
SUM([you_deaths])` by `match_day`; `you_result` count by `map_name` (matches table).

## Columns

Dates are ISO 8601 UTC to the second (`2026-10-05T04:12:00Z`). Sides are `ct` / `t`. Flags are `1` / `0`.
Probabilities are 0..1. Empty = unknown / not applicable. Text starting with `= + - @` gets a leading `'`.

Both files start with:

| Column | Meaning |
| --- | --- |
| `match_id` | The app's match id (same match shared by several players has one id) |
| `map_name` | e.g. `de_mirage` |
| `match_date` | When it was played if known (Steam sync, from Valve), else when it was added to your list |
| `match_day` | `match_date` as `YYYY-MM-DD` (UTC) |
| `date_source` | `played` or `imported` (uploaded demos have no date in them) |
| `played_at` | When it was played (empty if unknown) |
| `imported_at` | When it was added to your list |
| `source` | `upload` or `steam_sync` (how it reached your list) |

`rounds.csv` (one row per round; imported matches only; warmup / knife rounds are not stored, so not here):

| Column | Meaning |
| --- | --- |
| `round_number` | 1.. from the start of the match |
| `winner_side` | Side that won the round |
| `ct_won` | 1 CT won, 0 T won |
| `opening_kill_side`, `opening_kill_seconds`, `opening_weapon` | The round's first kill: side, seconds after freeze time, weapon |
| `opening_kill_side_won` | 1 the side with the first kill won the round |
| `model_ct_win_probability` | The round-win model's P(CT wins) given the map and the opening kill (retrospective; trained on pro matches; empty if not scored) |
| `model_predicted_winner`, `model_correct` | The model's favourite and whether it won |
| `unscored_reason` | Why the model didn't score the round (e.g. `weapon_not_in_model`) |
| `match_score_ct`, `match_score_t` | Final score (team on CT / T at the end), on every row |
| `needs_reupload` | 1 parsed by an older version (upload the demo again to refresh) |
| `you_side` | Your side this round |
| `you_won` | 1 your side won the round |
| `you_kills`, `you_deaths`, `you_survived` | Your round |
| `you_opening_kill`, `you_opening_death` | 1 you got / were the first kill |
| `you_win_probability` | The model's probability for your side |
| `you_score_after`, `opponent_score_after` | Scoreboard after this round from your side (empty after a round you weren't tracked in) |

`you_*` columns are empty when you are not in that match or round (e.g. an uploaded pro demo) or the match
was parsed before per-player stats existed.

`matches.csv` (one row per match in your list, also ones that couldn't be imported):

| Column | Meaning |
| --- | --- |
| `status`, `status_reason` | `imported`, `unavailable` or `parse_failed`, and why |
| `needs_reupload`, `outdated_reason` | Parsed by an older version: `players_not_recorded` or `parser_updated` |
| `rounds` | Rounds stored |
| `match_score_ct`, `match_score_t` | Final score |
| `rounds_with_winner`, `ct_rounds_won`, `t_rounds_won`, `ct_round_win_rate` | By map side, all players |
| `opening_kill_rounds`, `opening_kill_converted`, `opening_kill_conversion_rate` | Rounds won by the side with the first kill |
| `model_scored_rounds`, `model_correct`, `model_hit_rate`, `model_brier_score` | The model over this match |
| `you_status` | `in_match`, `not_in_match`, or `unknown` (parsed before per-player stats) |
| `you_first_side`, `you_last_side` | Your side at the start / end |
| `you_rounds`, `you_rounds_won`, `you_round_win_rate` | Your rounds |
| `you_kills`, `you_deaths`, `you_kd` | Your K/D |
| `you_opening_kills`, `you_opening_deaths`, `you_survived` | Opening duels and rounds survived |
| `you_score`, `opponent_score`, `you_result` | Final score from your side, `won` / `lost` / `tied` |
