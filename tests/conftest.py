"""Test isolation from the local .env.

The dev machine's .env may carry AIVIDEO_PG_DSN / AIVIDEO_REQUIRE_AUTH for the
running server. Tests must not inherit them: the db facade would delegate to
the real dev PG and RPC tests would demand sessions. Account/auth tests opt
back in via patch.dict in their own setUp.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _account_env_off():
    old_dsn = os.environ.get("AIVIDEO_PG_DSN")
    old_auth = os.environ.get("AIVIDEO_REQUIRE_AUTH")
    os.environ["AIVIDEO_PG_DSN"] = ""  # present-but-empty: .env loaders skip it
    os.environ["AIVIDEO_REQUIRE_AUTH"] = "off"
    try:
        yield
    finally:
        if old_dsn is None:
            os.environ.pop("AIVIDEO_PG_DSN", None)
        else:
            os.environ["AIVIDEO_PG_DSN"] = old_dsn
        if old_auth is None:
            os.environ.pop("AIVIDEO_REQUIRE_AUTH", None)
        else:
            os.environ["AIVIDEO_REQUIRE_AUTH"] = old_auth
        from end import pg_store

        pg_store.reset_pool()
        pg_store._TOMBSTONES.clear()