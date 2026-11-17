from datetime import datetime

import pytest

from analyst.builddb import build_database
from analyst.config import RESTAURANT_TZ

# A fixed "now" makes the data identical on every run and every machine.
FIXED_NOW = datetime(2026, 11, 17, 12, 0, tzinfo=RESTAURANT_TZ)


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("data") / "analyst.db", now=FIXED_NOW)
