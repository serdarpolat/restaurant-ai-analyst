"""Settings, read from environment variables with safe defaults."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.getenv("ANALYST_DATA_DIR", str(Path.home() / ".restaurant-ai-analyst")))
DB_PATH = Path(os.getenv("ANALYST_DB", str(DATA_DIR / "analyst.db")))
RESTAURANT_TZ = ZoneInfo(os.getenv("RESTAURANT_TZ", "Europe/Istanbul"))

MAX_ROWS = int(os.getenv("ANALYST_MAX_ROWS", "1000"))
QUERY_TIMEOUT_S = float(os.getenv("ANALYST_QUERY_TIMEOUT", "3"))

# The only tables the model may read. `auditlog` (AI write log of project 1) and our own
# `analyst_meta` are deliberately left out, as is everything named sqlite_*.
ALLOWED_TABLES = frozenset(
    {
        "category",
        "dining_table",
        "ingredient",
        "menuitem",
        "menuitemmodifiergrouplink",
        "modifier",
        "modifiergroup",
        "orderitem",
        "orders",
        "payment",
        "recipeitem",
        "staff",
        "stockmovement",
    }
)

# Functions that are never allowed, even inside an otherwise valid SELECT.
DENIED_FUNCTIONS = frozenset(
    {
        "load_extension",
        "randomblob",
        "zeroblob",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
        "sqlite_offset",
    }
)

# Source of the demo data (must match the pin in pyproject.toml).
DATA_SOURCE = "restaurant-mcp-server@a8f44be"
