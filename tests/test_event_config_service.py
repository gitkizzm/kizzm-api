import unittest
from pathlib import Path
import tempfile

from backend.services.event_config_service import (
    EventSettings,
    EventState,
    SettingsUpdateError,
    apply_settings_patch,
    reset_settings_with_locks,
    get_default_settings,
    load_event_settings,
)


class EventConfigServiceTests(unittest.TestCase):
    def test_apply_patch_rejects_locked_key(self):
        cur = EventSettings()
        with self.assertRaises(SettingsUpdateError):
            apply_settings_patch(cur, {"participants": ["Alice"]}, EventState.REGISTRATION_OPEN)

    def test_apply_patch_allows_editable_nested(self):
        cur = EventSettings()
        updated, changed = apply_settings_patch(
            cur,
            {"scryfall": {"default_background_query": "type:land"}},
            EventState.VOTING,
        )
        self.assertEqual(updated.scryfall.default_background_query, "type:land")
        self.assertEqual(changed, ["scryfall.default_background_query"])

    def test_reset_with_locks_only_changes_editable(self):
        cur = EventSettings(
            participants=["Alice"],
            min_decks_to_start=7,
            scryfall={"default_background_query": "type:land"},
        )

        updated, changed, skipped = reset_settings_with_locks(cur, EventState.REGISTRATION_OPEN)

        self.assertEqual(updated.participants, ["Alice"])  # locked -> unchanged
        self.assertEqual(updated.min_decks_to_start, 3)  # editable in registration_open
        self.assertEqual(updated.scryfall.default_background_query, "t:basic t:snow e:SLD")
        self.assertIn("participants", skipped)
        self.assertIn("min_decks_to_start", changed)

    def test_card_preview_queries_are_editable_in_voting(self):
        cur = EventSettings()
        updated, changed = apply_settings_patch(
            cur,
            {
                "scryfall": {
                    "card_preview_query_template": 'game:paper !"{name}"',
                    "card_preview_fallback_query_template": 'game:paper name:{name}',
                }
            },
            EventState.VOTING,
        )
        self.assertEqual(updated.scryfall.card_preview_query_template, 'game:paper !"{name}"')
        self.assertEqual(updated.scryfall.card_preview_fallback_query_template, 'game:paper name:{name}')
        self.assertIn("scryfall.card_preview_query_template", changed)
        self.assertIn("scryfall.card_preview_fallback_query_template", changed)

    def test_default_settings_reads_participants_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "teilnehmer.txt"
            p.write_text("Alice\nBob\n\n", encoding="utf-8")
            defaults = get_default_settings(participants_path=p)
            self.assertEqual(defaults.participants, ["Alice", "Bob"])


    def test_default_voting_points_scheme_contains_all_categories(self):
        cur = EventSettings()

        self.assertEqual(cur.voting.points_scheme["play_phase"], {"1": 4, "2": 3, "3": 2, "4": 1})
        self.assertEqual(cur.voting.points_scheme["best_deck_voting"], {"1": 3, "2": 2, "3": 1})
        self.assertEqual(cur.voting.points_scheme["best_deck_overall"], {"1": 8, "2": 5, "3": 3, "4": 2, "5": 1, "6": 0, "7": 0, "8": 0})
        self.assertEqual(cur.voting.points_scheme["deck_creator_guess"], {"correct_guess": 1})
    def test_load_event_settings_uses_participant_defaults_when_file_has_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            participants_file = Path(tmp) / "teilnehmer.txt"
            participants_file.write_text("Alice\nBob\n", encoding="utf-8")

            config_file = Path(tmp) / "event_config.json"
            config_file.write_text('{"participants": []}', encoding="utf-8")

            loaded, meta = load_event_settings(path=config_file, participants_path=participants_file)
            self.assertEqual(meta["source"], "file")
            self.assertEqual(loaded.participants, ["Alice", "Bob"])


if __name__ == "__main__":
    unittest.main()
