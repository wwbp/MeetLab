"""Unit tests for db/url.py — how agent-runner finds its database.

v1 sets DATABASE_URL directly. v2 on ECS gets the password from the RDS-managed
secret as DB_PASSWORD, and RDS-generated passwords can contain characters that
break a hand-built URL (@ : / ? # %), so the URL is assembled with escaping.

Pure/offline. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_db_url -v
"""
import unittest

from sqlalchemy.engine import make_url

from db.url import database_url

PARTS = {
    "DB_HOST": "meetlab-v2-staging.abc.us-east-1.rds.amazonaws.com",
    "DB_NAME": "meetlab",
    "DB_USER": "meetlab",
    "DB_PASSWORD": "p@ss:w/rd?#%",
}


class DatabaseUrlTest(unittest.TestCase):
    def test_database_url_wins_when_set(self):
        url = "postgresql+asyncpg://u:p@localhost/db"
        self.assertEqual(database_url({"DATABASE_URL": url, **PARTS}), url)

    def test_parts_round_trip_a_password_full_of_url_characters(self):
        url = make_url(database_url(PARTS))
        self.assertEqual(url.password, "p@ss:w/rd?#%")
        self.assertEqual(url.username, "meetlab")
        self.assertEqual(url.host, PARTS["DB_HOST"])
        self.assertEqual(url.port, 5432)
        self.assertEqual(url.database, "meetlab")
        self.assertEqual(url.drivername, "postgresql+asyncpg")

    def test_parts_require_tls(self):
        # RDS Postgres 17 rejects plain connections (rds.force_ssl defaults on).
        self.assertEqual(make_url(database_url(PARTS)).query.get("ssl"), "require")

    def test_port_can_be_overridden(self):
        self.assertEqual(make_url(database_url({**PARTS, "DB_PORT": "6543"})).port, 6543)

    def test_missing_config_names_what_is_missing(self):
        with self.assertRaises(KeyError) as ctx:
            database_url({"DB_HOST": "h"})
        self.assertIn("DB_PASSWORD", str(ctx.exception))
        self.assertIn("DATABASE_URL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
