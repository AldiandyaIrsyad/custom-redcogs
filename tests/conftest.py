import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redbot.core import Config, data_manager

from schedule import Schedule


_ids = itertools.count(1)


@pytest.fixture
def cog(tmp_path, monkeypatch):
    monkeypatch.setattr(data_manager, "basic_config", {
        "DATA_PATH": str(tmp_path), "COG_PATH_APPEND": "cogs",
        "CORE_PATH_APPEND": "core", "STORAGE_TYPE": "JSON", "STORAGE_DETAILS": {},
    })
    config = Config.get_conf(None, identifier=next(_ids), cog_name=f"TestSchedule{next(_ids)}", force_registration=True)
    monkeypatch.setattr(Config, "get_conf", lambda *args, **kwargs: config)
    bot = SimpleNamespace(get_guild=lambda _: None, fetch_channel=AsyncMock())
    return Schedule(bot)
