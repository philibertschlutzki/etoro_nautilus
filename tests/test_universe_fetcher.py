import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

_ENV = Path("automation/.env")
if not _ENV.exists():
    _ENV = Path(".env")
load_dotenv(str(_ENV))

class TestUniverseFetcher:

    def test_is_universe_stale_fresh(self, tmp_path):
        """Frisches Universe (< 24h) → False."""
        from automation.universe_fetcher import is_universe_stale
        universe = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "universe": []
        }
        path = tmp_path / "universe.json"
        path.write_text(json.dumps(universe))
        assert is_universe_stale(path, max_age_hours=24.0) is False

    def test_is_universe_stale_old(self, tmp_path):
        """Veraltetes Universe (> 24h) → True."""
        from automation.universe_fetcher import is_universe_stale
        old_dt = datetime.now(timezone.utc) - timedelta(hours=25)
        universe = {"fetched_at": old_dt.isoformat(), "universe": []}
        path = tmp_path / "universe.json"
        path.write_text(json.dumps(universe))
        assert is_universe_stale(path, max_age_hours=24.0) is True

    def test_is_universe_stale_missing_file(self, tmp_path):
        """Fehlende Datei → True (immer fetchen)."""
        from automation.universe_fetcher import is_universe_stale
        assert is_universe_stale(tmp_path / "missing.json") is True

    def test_load_instrument_map(self):
        """Instrument-Map aus JSON laden."""
        from automation.universe_fetcher import load_instrument_map
        path = Path("automation/config/instrument_map.json")
        result = load_instrument_map(path)
        assert isinstance(result, dict)
        assert len(result) > 0
        assert "TSLA.ETORO" in result.values()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_universe_live(self):
        """Integration: Echter API-Call mit Demo-Credentials."""
        from automation.universe_fetcher import run_fetch
        api_key  = os.getenv("ETORO_API_KEY", "")
        user_key = os.getenv("ETORO_USER_KEY", "")
        username = os.getenv("MOMENTUM_LS_USERNAME", "")
        if not all([api_key, user_key, username]):
            pytest.skip("API-Credentials fehlen in .env")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "universe.json"
            result = await run_fetch(
                api_key=api_key,
                user_key=user_key,
                username=username,
                output_path=output,
                instrument_map_path=Path("automation/config/instrument_map.json"),
            )
            assert result is True
            assert output.exists()
            data = json.loads(output.read_text())
            assert "fetched_at" in data
            assert "universe" in data
            assert len(data["universe"]) > 0
