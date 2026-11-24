"""解析式模型的校准测试：公式必须与实测一致（不需要内核）。

每个用例都对应一个已被 solidcraft 0.47 + FreeCAD 双源验证过的几何：
公式变了、或者内核行为变了，这里先红。
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spec2cad.spec import SpecError, load  # noqa: E402

EXAMPLES = ROOT / "spec/examples"


def vol(name: str) -> float:
    return load(EXAMPLES / f"{name}.toml").expect()["volume_mm3"]


class BoxTest(unittest.TestCase):
    def test_corner_anchor(self):
        spec = load(EXAMPLES / "plate3h.toml")
        got = spec.expect()
        self.assertAlmostEqual(got["volume_mm3"], 12000 - 3 * math.pi * 25 * 5, places=9)
        self.assertEqual(got["bbox"], {"min": [0.0, 0.0, 0.0], "max": [60.0, 40.0, 5.0]})

    def test_hole_count_and_positions(self):
        got = load(EXAMPLES / "plate3h.toml").expect()
        self.assertEqual(got["cylinders"], 3)
        self.assertEqual(got["hole_positions"], [(15.0, 20.0), (45.0, 20.0), (30.0, 10.0)])


class CylinderTest(unittest.TestCase):
    def test_bbox_uses_centre_not_corner(self):
        """base 是底面圆心：Ø60 h6 的 bbox 是 -30..30，不是 0..60。"""
        got = load(EXAMPLES / "washer.toml").expect()
        self.assertEqual(got["bbox"]["min"], [-30.0, -30.0, 0.0])
        self.assertEqual(got["bbox"]["max"], [30.0, 30.0, 6.0])

    def test_cut_cylinder_keeps_outer_bbox(self):
        got = load(EXAMPLES / "washer.toml").expect()
        self.assertAlmostEqual(got["volume_mm3"], math.pi * (900 - 225) * 6, places=9)
        self.assertEqual(got["cylinders"], 2)

    def test_join_stack(self):
        got = load(EXAMPLES / "stepped-shaft.toml").expect()
        self.assertAlmostEqual(
            got["volume_mm3"], math.pi * 225 * 20 + math.pi * 81 * 15, places=9
        )
        self.assertEqual(got["bbox"]["max"], [15.0, 15.0, 35.0])
        self.assertEqual(got["cylinders"], 2)


class HolePatternTest(unittest.TestCase):
    def test_grid(self):
        got = load(EXAMPLES / "plate-grid.toml").expect()
        self.assertEqual(got["cylinders"], 6)
        self.assertAlmostEqual(got["volume_mm3"], 24000 - 6 * math.pi * 16 * 6, places=9)

    def test_circular(self):
        got = load(EXAMPLES / "flange.toml").expect()
        self.assertEqual(got["cylinders"], 7)  # 外圆 + 6 孔
        positions = got["hole_positions"]
        self.assertEqual(len(positions), 6)
        for x, y in positions:
            self.assertAlmostEqual(math.hypot(x, y), 28.0, places=9)

    def test_blind_is_shallower_than_through(self):
        blind = load(EXAMPLES / "blind-plate.toml").expect()
        self.assertAlmostEqual(blind["volume_mm3"], 24000 - math.pi * 36 * 3, places=9)
        self.assertEqual(blind["cylinders"], 1)


class FilletTest(unittest.TestCase):
    def test_vertical_edges_remove_material(self):
        """90° 凸边圆角每单位长度去掉 r^2(1-pi/4)，四条竖边。"""
        got = load(EXAMPLES / "fillet-plate.toml").expect()
        want = 12000 - 4 * 36 * (1 - math.pi / 4) * 5
        self.assertAlmostEqual(got["volume_mm3"], want, places=9)
        self.assertEqual(got["cylinders"], 4)
        self.assertEqual(got["bbox"]["max"], [60.0, 40.0, 5.0])  # 圆角不改 bbox

    def test_fillet_needs_a_box_first(self):
        text = (EXAMPLES / "fillet-plate.toml").read_text(encoding="utf-8")
        text = text.replace('[[build]]\nop = "box"', '[[build]]\nop = "cylinder"')
        tmp = Path("/tmp/spec2cad-fillet-bad.toml")
        tmp.write_text(text, encoding="utf-8")
        with self.assertRaises(SpecError):
            load(tmp)


class GuardTest(unittest.TestCase):
    def test_through_must_match_height(self):
        text = (EXAMPLES / "plate3h.toml").read_text(encoding="utf-8")
        tmp = Path("/tmp/spec2cad-through-bad.toml")
        tmp.write_text(text.replace('through = "T"', 'through = "3"'), encoding="utf-8")
        with self.assertRaises(SpecError):
            load(tmp)

    def test_depth_must_fit(self):
        text = (EXAMPLES / "blind-plate.toml").read_text(encoding="utf-8")
        tmp = Path("/tmp/spec2cad-depth-bad.toml")
        tmp.write_text(text.replace('depth = "DEP"', 'depth = "99"'), encoding="utf-8")
        with self.assertRaises(SpecError):
            load(tmp)

    def test_holes_need_solid(self):
        tmp = Path("/tmp/spec2cad-holes-first.toml")
        tmp.write_text(
            '[part]\nname = "x"\n[[build]]\nop = "holes"\ndiameter = 5\nthrough = 5\n'
            "at = [[1, 1]]\n",
            encoding="utf-8",
        )
        with self.assertRaises(SpecError):
            load(tmp)

    def test_unknown_op(self):
        tmp = Path("/tmp/spec2cad-op-bad.toml")
        tmp.write_text(
            '[part]\nname = "x"\n[[build]]\nop = "torus"\n', encoding="utf-8"
        )
        with self.assertRaises(SpecError):
            load(tmp)


if __name__ == "__main__":
    unittest.main()
