import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldsignal.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.report_path = str(tmp_path / "report.html")
    s.events_csv = str(tmp_path / "events.csv")
    s.random_runs = 100
    s.news_enabled = s.cot_enabled = s.calendar_enabled = False
    return s
