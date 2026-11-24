"""L1 闸门：解析式期望 vs 主内核自报 vs 独立 oracle。

三类来源必须三方一致：
    expected   由 spec 解析式推导（不抄答案）
    kernel     solidcraft 自己量自己
    oracle     FreeCAD/OCCT 读回同一个 STEP 再量

容差
----
体积   相对 1e-6
包围盒 绝对 1e-6 mm
圆柱面 精确相等
闭合/ 有效 布尔
"""

from __future__ import annotations

from typing import Any

__all__ = ["L1Result", "check_l1", "check_hole_positions"]

REL_VOL = 1e-6
ABS_BBOX = 1e-6


class L1Result:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(
        self,
        name: str,
        expected: Any,
        actual: Any,
        *,
        source: str,
        passed: bool,
        tolerance: str = "",
    ) -> None:
        self.checks.append(
            {
                "name": name,
                "source": source,
                "expected": expected,
                "actual": actual,
                "tolerance": tolerance,
                "pass": bool(passed),
            }
        )

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c["pass"] for c in self.checks)

    @property
    def failed(self) -> list[str]:
        """失败断言名（与 to_dict()['failed'] 同源）。"""
        return [c["name"] for c in self.checks if not c["pass"]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "total": len(self.checks),
            "failed": [c["name"] for c in self.checks if not c["pass"]],
            "checks": self.checks,
        }


def check_hole_positions(expected_holes: list[dict], oracle: dict) -> list[dict]:
    """L1.5：孔位坐标。体积对不上号时它没用，但**孔位错、体积对**时只有它能抓。"""
    rows: list[dict] = []
    axes = oracle.get("hole_axes") or []
    for hole in expected_holes:
        want_r = float(hole["r"])
        candidates = [a for a in axes if abs(float(a["radius"]) - want_r) <= 1e-6]
        best = None
        for a in candidates:
            dist = ((float(a["x"]) - hole["x"]) ** 2 + (float(a["y"]) - hole["y"]) ** 2) ** 0.5
            if best is None or dist < best[0]:
                best = (dist, a)
        if best is None:
            rows.append({
                "name": f"hole@({hole['x']:.3f},{hole['y']:.3f})",
                "source": "oracle",
                "expected": f"半径 {want_r}",
                "actual": "没有对应圆柱面",
                "tolerance": "exact",
                "pass": False,
            })
            continue
        dist, axis = best
        rows.append({
            "name": f"hole@({hole['x']:.3f},{hole['y']:.3f})",
            "source": "oracle",
            "expected": "轴心重合",
            "actual": f"偏差 {dist:.6f} mm",
            "tolerance": f"abs {ABS_BBOX:g} mm",
            "pass": dist <= ABS_BBOX,
        })
    return rows


def _rel_ok(expected: float, actual: float, rel: float = REL_VOL) -> bool:
    if expected == 0:
        return abs(actual) <= rel
    return abs(actual - expected) / abs(expected) <= rel


def check_l1(
    expected: dict[str, Any],
    kernel: dict[str, Any] | None,
    oracle: dict[str, Any] | None,
) -> L1Result:
    res = L1Result()
    exp_vol = float(expected["volume_mm3"])
    exp_bbox = expected["bbox"]
    exp_cyl = int(expected["cylinders"])

    # --- 主内核 ---------------------------------------------------------
    if kernel is None:
        res.add("kernel.result", "有", "缺失", source="kernel", passed=False)
    else:
        res.add(
            "volume@kernel",
            round(exp_vol, 6),
            round(float(kernel["volume_mm3"]), 6),
            source="kernel",
            passed=_rel_ok(exp_vol, float(kernel["volume_mm3"])),
            tolerance=f"rel {REL_VOL:g}",
        )
        for axis, idx in (("x", 0), ("y", 1), ("z", 2)):
            got = float(kernel["bbox"]["max"][idx]) - float(kernel["bbox"]["min"][idx])
            want = float(exp_bbox["max"][idx]) - float(exp_bbox["min"][idx])
            res.add(
                f"size_{axis}@kernel",
                round(want, 6),
                round(got, 6),
                source="kernel",
                passed=abs(got - want) <= ABS_BBOX,
                tolerance=f"abs {ABS_BBOX:g} mm",
            )
            for side in ("min", "max"):
                got_s = float(kernel["bbox"][side][idx])
                want_s = float(exp_bbox[side][idx])
                res.add(
                    f"bbox_{side}_{axis}@kernel",
                    round(want_s, 6),
                    round(got_s, 6),
                    source="kernel",
                    passed=abs(got_s - want_s) <= ABS_BBOX,
                    tolerance=f"abs {ABS_BBOX:g} mm",
                )
        res.add(
            "cylinders@kernel",
            exp_cyl,
            int(kernel["cylinders"]),
            source="kernel",
            passed=int(kernel["cylinders"]) == exp_cyl,
            tolerance="exact",
        )
        res.add(
            "closed@kernel",
            True,
            bool(kernel["closed"]),
            source="kernel",
            passed=bool(kernel["closed"]),
        )
        res.add(
            "bodies@kernel",
            1,
            int(kernel["body_count"]),
            source="kernel",
            passed=int(kernel["body_count"]) == 1,
            tolerance="exact",
        )

    # --- 独立 oracle ----------------------------------------------------
    if oracle is None:
        res.add(
            "oracle.available",
            "FreeCAD/OCCT 可读回",
            "跳过（未安装 FreeCAD）",
            source="oracle",
            passed=False,
        )
    else:
        res.add(
            "volume@oracle",
            round(exp_vol, 6),
            round(float(oracle["volume_mm3"]), 6),
            source="oracle",
            passed=_rel_ok(exp_vol, float(oracle["volume_mm3"])),
            tolerance=f"rel {REL_VOL:g}",
        )
        for axis, idx in (("x", 0), ("y", 1), ("z", 2)):
            got = float(oracle["bbox"]["max"][idx]) - float(oracle["bbox"]["min"][idx])
            want = float(exp_bbox["max"][idx]) - float(exp_bbox["min"][idx])
            res.add(
                f"size_{axis}@oracle",
                round(want, 6),
                round(got, 6),
                source="oracle",
                passed=abs(got - want) <= ABS_BBOX,
                tolerance=f"abs {ABS_BBOX:g} mm",
            )
        res.add(
            "cylinders@oracle",
            exp_cyl,
            int(oracle["cylinders"]),
            source="oracle",
            passed=int(oracle["cylinders"]) == exp_cyl,
            tolerance="exact",
        )
        res.add(
            "valid@oracle",
            True,
            bool(oracle["valid"]),
            source="oracle",
            passed=bool(oracle["valid"]),
        )
        for row in check_hole_positions(expected.get("holes") or [], oracle):
            res.add(row["name"], row["expected"], row["actual"], source=row["source"],
                    passed=row["pass"], tolerance=row["tolerance"])
        res.add(
            "solids@oracle",
            1,
            int(oracle["solids"]),
            source="oracle",
            passed=int(oracle["solids"]) == 1,
            tolerance="exact",
        )

    # --- 跨内核一致性 ---------------------------------------------------
    if kernel is not None and oracle is not None:
        k_vol = float(kernel["volume_mm3"])
        o_vol = float(oracle["volume_mm3"])
        res.add(
            "volume@kernel==oracle",
            round(k_vol, 6),
            round(o_vol, 6),
            source="cross",
            passed=_rel_ok(k_vol, o_vol),
            tolerance=f"rel {REL_VOL:g}",
        )
    return res
