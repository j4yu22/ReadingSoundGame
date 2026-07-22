from __future__ import annotations

import unittest

from app.services.activity_service import (
    ActivityCatalogError,
    load_activity_catalog,
    select_catalog_activity,
)


class ActivityServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_activity_catalog()

    def test_selects_an_exact_catalog_line(self) -> None:
        activity = select_catalog_activity(
            level="D",
            sublevel="1",
            section="standard",
            exercise_number="1",
            line_letter="a",
            catalog=self.catalog,
        )

        self.assertEqual(activity["word"], "birthday")
        self.assertEqual(activity["answer"], "day")
        self.assertEqual(activity["type"], "deletion")
        self.assertEqual(activity["catalog"]["sourcePage"], 129)

    def test_selects_a_level_without_a_sublevel(self) -> None:
        activity = select_catalog_activity(
            level="J",
            sublevel="none",
            section="standard",
            exercise_number="1",
            line_letter="a",
            catalog=self.catalog,
        )

        self.assertIsNone(activity["catalog"]["sublevel"])

    def test_rejects_an_unknown_line(self) -> None:
        with self.assertRaises(ActivityCatalogError):
            select_catalog_activity(
                level="D",
                sublevel="1",
                section="standard",
                exercise_number="1",
                line_letter="z",
                catalog=self.catalog,
            )


if __name__ == "__main__":
    unittest.main()
