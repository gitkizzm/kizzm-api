import unittest

from backend.services.ws_state_service import global_signature


class WSStateServiceTests(unittest.TestCase):
    def test_global_signature_changes_when_voting_results_are_published(self):
        pairings = {
            "phase": "voting",
            "active_round": 3,
            "voting_results": {"published": False, "data": None},
        }

        before = global_signature(
            start_file_exists=True,
            raffle_list=[{"deck_id": 1, "received_confirmed": True}],
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        pairings["voting_results"] = {
            "published": True,
            "published_at": "2026-01-01T00:00:00+00:00",
            "data": {"rows": [{"owner": "Alice", "total_points": 8}]},
        }

        after = global_signature(
            start_file_exists=True,
            raffle_list=[{"deck_id": 1, "received_confirmed": True}],
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
