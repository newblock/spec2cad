"""L1.5 孔位断言的单元测试（不需要内核）。

体积闸门的盲区：孔位挪了但落在实体内部时，**体积和孔数都不变**。
这里证明位置断言对此有牙。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spec2cad.verify import check_hole_positions  # noqa: E402


class HolePositionTest(unittest.TestCase):
    def test_exact_match_passes(self):
        rows = check_hole_positions(
            [{"x": 15.0, "y": 20.0, "r": 5.0}],
            {"hole_axes": [{"radius": 5.0, "x": 15.0, "y": 20.0}]},
        )
        self.assertTrue(rows[0]["pass"])

    def test_shifted_hole_fails(self):
        rows = check_hole_positions(
            [{"x": 15.0, "y": 20.0, "r": 5.0}],
            {"hole_axes": [{"radius": 5.0, "x": 15.5, "y": 20.0}]},
        )
        self.assertFalse(rows[0]["pass"])
        self.assertIn("0.5", rows[0]["actual"])

    def test_missing_hole_fails(self):
        rows = check_hole_positions(
            [{"x": 15.0, "y": 20.0, "r": 5.0}], {"hole_axes": []}
        )
        self.assertFalse(rows[0]["pass"])

    def test_radius_must_match(self):
        """同样是 3 个圆柱面，但半径不对 → 不算匹配。"""
        rows = check_hole_positions(
            [{"x": 0.0, "y": 0.0, "r": 5.0}],
            {"hole_axes": [{"radius": 4.0, "x": 0.0, "y": 0.0}]},
        )
        self.assertFalse(rows[0]["pass"])


if __name__ == "__main__":
    unittest.main()
