"""Group start links (step 0): the three tables' models, read from the metadata (no database)."""
import unittest

from meetlab_core.db.models import Match, StartLink, Ticket, TICKET_STATES, LIVE_TICKET_STATES


class GroupLinkModelsTest(unittest.TestCase):
    def test_start_links_columns(self):
        self.assertEqual(set(StartLink.__table__.c.keys()), {
            "link_id", "name", "pool", "size", "min_size", "delay_s", "max_wait_s", "ready_check_s",
            "password_hash", "active", "legacy", "created_at", "stopped_at"})

    def test_matches_columns(self):
        self.assertEqual(set(Match.__table__.c.keys()), {
            "room_name", "link_id", "bot_config_scope", "size", "created_at", "ready_deadline",
            "starts_at", "closed_at", "session_id", "setup_claimed_at"})

    def test_tickets_columns(self):
        self.assertEqual(set(Ticket.__table__.c.keys()), {
            "id", "link_id", "participant_id", "state", "queued_at", "last_seen", "ready_at",
            "placed_at", "requeued_at", "room_name"})

    def test_states(self):
        self.assertEqual(TICKET_STATES, ("searching", "found", "ready", "placed", "done", "expired", "removed"))
        self.assertEqual(LIVE_TICKET_STATES, ("searching", "found", "ready", "placed"))

    def test_one_non_terminal_ticket_per_participant_and_link(self):
        index = next(i for i in Ticket.__table__.indexes if i.unique)
        self.assertEqual([c.name for c in index.columns], ["link_id", "participant_id"])
        self.assertIn("'searching', 'found', 'ready', 'placed'",
                      str(index.dialect_options["postgresql"]["where"]))


if __name__ == "__main__":
    unittest.main()
