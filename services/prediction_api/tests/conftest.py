import os
import sys
from pathlib import Path

# Make the service directory importable and make sure tests never pick up a
# real database or encryption key from the developer's environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("DATABASE_URL", "TOKEN_ENCRYPTION_KEYS", "SESSION_SECRET", "STEAM_WEB_API_KEY"):
    os.environ.pop(name, None)
