"""spec 解析 / 期望值推导的单元测试（不需要内核，标准库 unittest）。"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spec2cad.spec import SpecError, expr, load  # noqa: E402

EXAMPLE = ROOT / "spec/examples/plate3h.toml"


class ExprTest(unittest.TestCase):
    def test_basic(self):
        env = {"L": 60.0, "W": 40.0}
        self.assertEqual(expr("L", env), 60.0)
        self.assertEqual(expr("L/2 - 30", env), 0.0)
        self.assertEqual(expr("W * 0.75", env), 30.0)
        self.assertEqual(expr(12.5, env), 12.5)

    def test_unknown_name(self):
        with self.assertRaises(SpecError):
            expr("Q + 1", {"L": 1.0})

    def test_rejects_calls(self):
        with self.assertRaises(SpecError):
            expr("__import__('os').system('echo hi')", {})


class ExampleSpecTest(unittest.TestCase):
    def test_expectations(self):
        spec = load(EXAMPLE)
        got = spec.expect()
        want_vol = 60.0 * 40.0 * 5.0 - 3 * math.pi * 25.0 * 5.0
        self.assertAlmostEqual(got["volume_mm3"], want_vol, places=9)
        self.assertEqual(got["bbox"]["max"], [60.0, 40.0, 5.0])
        self.assertEqual(got["cylinders"], 3)

    def test_anchor_is_required(self):
        text = EXAMPLE.read_text(encoding="utf-8").replace('anchor = "corner"', "")
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
        with self.assertRaises(SpecError):
            load(handle.name)

    def test_through_must_match_thickness(self):
        text = EXAMPLE.read_text(encoding="utf-8").replace('through = "T"', 'through = "2"')
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
        with self.assertRaises(SpecError):
            load(handle.name)


if __name__ == "__main__":
    unittest.main()
