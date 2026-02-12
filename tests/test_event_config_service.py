import unittest

from backend.services.event_config_service import (
    EventSettings,
    EventState,
    SettingsUpdateError,
    apply_settings_patch,
    reset_settings_with_locks,
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


if __name__ == "__main__":
    unittest.main()
