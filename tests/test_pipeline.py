"""管线测试：spec -> 命令脚本编译产物 + solidcraft 端到端（L1）。

编译产物测试不需要内核，永远可跑；端到端用例需要真内核（solidcraft-cli / FreeCAD），
缺内核时自动 skip——skip 不算通过，装上内核再跑才算数。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spec2cad.emit import compile_spec  # noqa: E402
from spec2cad.kernel import find_cli, measure_freecad, normalise_kernel_result, run_script  # noqa: E402
from spec2cad.verify import check_l1  # noqa: E402

EXAMPLES = ROOT / "spec/examples"


class CompileTest(unittest.TestCase):
    """emit 编译产物：JSON 可 diff / 可回放，几何位置与期望值同源（trace）。"""

    def test_compile_is_plain_json(self):
        spec, expect = load_and_expect("plate3h")
        text = json.dumps(compile_spec(spec, expect).to_json(), ensure_ascii=False, indent=2)
        commands = json.loads(text)["commands"]
        self.assertEqual(commands[0]["command"], "solid.box")
        self.assertEqual(commands[0]["params"]["corner"], [0.0, 0.0, 0.0])
        self.assertEqual([c["command"] for c in commands[-5:]],
                         ["sketch.circle.center", "sketch.circle.center",
                          "sketch.circle.center", "sketch.finish", "solid.extrude"])

    def test_center_anchor_resolves_to_corner(self):
        """anchor=center 在 spec 层就解析掉；命令脚本只看到角点（solid.move 已消化）。"""
        text = (
            '[part]\nname = "centerbox"\nunits = "mm"\n'
            "[params]\nL = 20.0\n\n"
            '[[build]]\nop = "box"\nlength = "L"\nwidth = "10.0"\nheight = "4.0"\n'
            'anchor = "center"\nat = [100.0, 100.0, 10.0]\n'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
        spec, expect = load_and_expect(handle.name)
        commands = compile_spec(spec, expect).to_json()["commands"]
        box = commands[0]["params"]
        self.assertEqual(box["corner"], [90.0, 95.0, 8.0])

    def test_washer_cylinder_base_is_bottom_centre(self):
        """cylinder 的 base 是底面圆心：Ø60 外圆在前，Ø30 切孔在后，都在 (0,0,0)。"""
        spec, expect = load_and_expect("washer")
        commands = compile_spec(spec, expect).to_json()["commands"]
        self.assertEqual(commands[0]["command"], "solid.cylinder")
        self.assertEqual(commands[0]["params"]["base"], [0.0, 0.0, 0.0])
        self.assertEqual(float(commands[0]["params"]["diameter"].split()[0]), 60.0)
        self.assertEqual(commands[1]["params"]["operation"], "cut")
        self.assertEqual(float(commands[1]["params"]["diameter"].split()[0]), 30.0)


def _require_cli(testcase):
    try:
        find_cli()
    except Exception:
        testcase.skipTest("solidcraft-cli 不可用（tools/install-solidcraft.sh）")


class SolidcraftEndToEndTest(unittest.TestCase):
    def test_plate3h(self):
        _require_cli(self)
        spec, expect = load_and_expect("plate3h")
        with tempfile.TemporaryDirectory() as tmp:
            script = compile_spec(spec, expect)
            run = run_script(script.to_json(), Path(tmp) / "plate3h.step", workdir=Path(tmp))
        measured = normalise_kernel_result(run["result"])
        self.assertAlmostEqual(
            measured["volume_mm3"], expect["volume_mm3"],
            delta=expect["volume_mm3"] * 1e-9,
        )
        self.assertEqual(measured["cylinders"], 3)

    def test_l1_gate_passes_on_plate3h(self):
        """完整 L1：解析式 = 内核自报 = OCCT 读回（oracle 与 STEP 都在临时目录内量完）。"""
        _require_cli(self)
        spec, expect = load_and_expect("plate3h")
        with tempfile.TemporaryDirectory() as tmp:
            script = compile_spec(spec, expect)
            step = Path(tmp) / "plate3h.step"
            run = run_script(script.to_json(), step, workdir=Path(tmp))
            measured = normalise_kernel_result(run["result"])
            oracle = measure_freecad(step)  # 必须在临时目录存活时读回
        l1 = check_l1(expect, measured, oracle)
        self.assertTrue(l1.passed, f"L1 失败：{l1.failed}")


class NegativeExampleTest(unittest.TestCase):
    def test_negative_example_is_blocked(self):
        """负例：内核照常出件，但孔落边界、体积对不上解析式——闸门必须拦住。"""
        _require_cli(self)
        spec, expect = load_and_expect("plate3h-broken")
        with tempfile.TemporaryDirectory() as tmp:
            script = compile_spec(spec, expect)
            step = Path(tmp) / "plate3h-broken.step"
            run = run_script(script.to_json(), step, workdir=Path(tmp))
        measured = normalise_kernel_result(run["result"])
        got = measured["volume_mm3"]
        want = expect["volume_mm3"]
        self.assertGreater(abs(got - want) / abs(want), 1e-6, "闸门没牙了：负例体积居然对上了")
        self.assertLess(measured["cylinders"], expect["cylinders"], "负例孔数应少于期望")


def load_and_expect(name) -> tuple:
    from spec2cad.spec import load
    spec = load(EXAMPLES / f"{name}.toml") if not str(name).endswith(".toml") else load(name)
    return spec, spec.expect()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
