import unittest

from backend.services.ws_state_service import deck_signature, global_signature


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

    def test_deck_signature_changes_when_table_report_changes(self):
        raffle_list = [{
            "deck_id": 1,
            "deckOwner": "Alice",
            "received_confirmed": True,
            "pairing_round": 2,
            "pairing_table": 4,
            "pairing_phase": "playing",
        }]
        pairings = {
            "round_reports": {
                "2": {
                    "4": {"resolved_places": {"Alice": 1}},
                }
            },
            "voting_results": {"published": False},
        }

        before = deck_signature(
            deck_id=1,
            start_file_exists=True,
            raffle_list=raffle_list,
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        pairings["round_reports"]["2"]["4"] = {"resolved_places": {"Alice": 2}}

        after = deck_signature(
            deck_id=1,
            start_file_exists=True,
            raffle_list=raffle_list,
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        self.assertNotEqual(before, after)

    def test_deck_signature_changes_when_voting_results_published(self):
        raffle_list = [{"deck_id": 1, "pairing_phase": "voting"}]
        pairings = {"voting_results": {"published": False}}

        before = deck_signature(
            deck_id=1,
            start_file_exists=True,
            raffle_list=raffle_list,
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        pairings["voting_results"] = {
            "published": True,
            "rows": [{"player": "Alice", "game_points": 9}],
        }

        after = deck_signature(
            deck_id=1,
            start_file_exists=True,
            raffle_list=raffle_list,
            pairings_loader=lambda: pairings,
            settings_loader=lambda: {},
        )

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
