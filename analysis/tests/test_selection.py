"""Tests for the final-report selection rules."""

from __future__ import annotations

import unittest

from analysis.selection import (
    consecutive_run_ids,
    latest_run_ids,
    practice_groups,
    select_canonical_groups,
)


class SelectionTests(unittest.TestCase):
    def test_latest_run_ids_are_numeric_not_insertion_order(self) -> None:
        self.assertEqual(
            latest_run_ids([1700000002, 1699999999, 1700000001, 1700000000]),
            (1700000000, 1700000001, 1700000002),
        )

    def test_consecutive_requires_exactly_three_in_order(self) -> None:
        self.assertTrue(consecutive_run_ids([1700000001, 1700000000, 1700000002]))
        self.assertFalse(consecutive_run_ids([1700000000, 1700000001]))
        self.assertFalse(consecutive_run_ids([1700000000, 1700000002, 1700000003]))

    def test_older_runs_are_removed_from_canonical_groups(self) -> None:
        groups = {
            "baseline": {
                1699999999: ["legacy"],
                1700000001: ["one"],
                1700000000: ["zero"],
                1700000002: ["two"],
            },
            "test/no_congestion": {9000: ["practice"]},
        }
        selected = select_canonical_groups(groups)
        self.assertEqual(list(selected), ["baseline"])
        self.assertEqual(list(selected["baseline"]), [1700000000, 1700000001, 1700000002])

    def test_practice_groups_keep_all_runs(self) -> None:
        groups = {
            "baseline": {1700000000: ["one"]},
            "test/no_congestion": {9000: ["one"], 9001: ["two"]},
        }
        practice = practice_groups(groups)
        self.assertEqual(list(practice), ["test/no_congestion"])
        self.assertEqual(list(practice["test/no_congestion"]), [9000, 9001])


if __name__ == "__main__":
    unittest.main()
