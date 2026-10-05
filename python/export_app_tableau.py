"""Export your matches from the cs-analytics app database as Tableau-ready CSVs.

The same tables as the app's "Export for Tableau" buttons (GET /matches/export/rounds.csv
and /matches/export/matches.csv), read straight from the app database, so Tableau Desktop
can use them offline. Read-only: nothing is written to the database.

    python python/export_app_tableau.py --database-url postgresql://user:pass@host:5432/db --steam-id 7656119...
    python python/export_app_tableau.py --database-url sqlite:///path/to/local.db --steam-id 7656119...

Writes tableau/app/app_rounds.csv and tableau/app/app_matches.csv (see tableau/README.md for
the columns and how to connect Tableau). Needs the API's requirements
(pip install -r services/prediction_api/requirements.txt) and model.pkl at the repo root.

This does not change python/export_tableau.py, which exports the original pro-match dataset
(cs2_analytics database) to tableau/rounds.csv and tableau/opening_trade.csv.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "prediction_api"))

from steamlink.export import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
