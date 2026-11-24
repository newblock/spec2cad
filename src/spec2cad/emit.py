"""spec -> solidcraft 命令脚本（v1）。

编译目标：solidcraft-cli `run <script.json>` 的输入格式（纯 JSON、可 diff、可回放）。

几何位置全部来自 `Spec.expect()` 产出的 trace，不在这里二次推导——
期望值和出件命令必须同源，否则闸门失去意义。
"""

from __future__ import annotations

from typing import Any

from .spec import Spec, SpecError

__all__ = ["CommandScript", "compile_spec"]


def _mm(value: float) -> str:
    """浮点 -> 内核表达式，避免 5.000000000000001 这类噪声。"""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{text or '0'} mm"


def _pt(values: list[float]) -> list[float]:
    return [round(float(v), 6) for v in values]


class CommandScript:
    """编译产物：命令列表 + 溯源信息。"""

    def __init__(self, spec: Spec, expect: dict[str, Any] | None = None):
        self.spec = spec
        self.expect = expect or spec.expect()
        trace = self.expect["trace"]
        self.body = spec.name
        self.commands: list[dict[str, Any]] = []

        for index, f in enumerate(spec.features):
            step = trace[index]
            if f.op == "box":
                self._box(f, step)
            elif f.op == "cylinder":
                self._cylinder(f, step)
            elif f.op == "holes":
                self._holes(f, step)
            elif f.op == "fillet":
                self._fillet(f, step)
            else:  # pragma: no cover - spec 已拦截
                raise SpecError(f"未实现的 op {f.op!r}")

    # ---- 各 op 的编译 --------------------------------------------------

    def _box(self, f, step) -> None:
        params: dict[str, Any] = {
            "length": _mm(step["hi"][0] - step["lo"][0]),
            "width": _mm(step["hi"][1] - step["lo"][1]),
            "height": _mm(step["hi"][2] - step["lo"][2]),
            "corner": _pt(step["lo"]),
            "body_name": self.body,
        }
        if f.operation != "new":
            params["operation"] = f.operation
        self.commands.append({"command": "solid.box", "params": params})

    def _cylinder(self, f, step) -> None:
        params: dict[str, Any] = {
            "base": _pt(step["base"]),
            "diameter": _mm(step["r"] * 2),
            "height": _mm(step["h"]),
        }
        if f.operation == "new":
            params["body_name"] = self.body
        else:
            params["operation"] = f.operation
        self.commands.append({"command": "solid.cylinder", "params": params})

    def _holes(self, f, step) -> None:
        name = f"Holes_{len(self.commands)}"
        sketch: dict[str, Any] = {"plane": "XY", "name": name}
        if step["direction"] == "negative":
            # 盲孔：草图落在顶面上，朝负方向切
            sketch["offset"] = _mm(step["start_z"])
        self.commands.append({"command": "sketch.create", "params": sketch})
        for i, (x, y) in enumerate(step["positions"]):
            self.commands.append(
                {
                    "command": "sketch.circle.center",
                    "params": {
                        "center": [round(float(x), 6), round(float(y), 6)],
                        "diameter": _mm(step["diameter"]),
                        "id": f"h{i}",
                    },
                }
            )
        self.commands.append({"command": "sketch.finish", "params": {}})
        extrude: dict[str, Any] = {
            "distance": _mm(step["depth"]),
            "operation": "cut",
        }
        if step["direction"] == "negative":
            extrude["direction"] = "negative"
        self.commands.append({"command": "solid.extrude", "params": extrude})

    def _fillet(self, f, step) -> None:
        self.commands.append(
            {
                "command": "solid.fillet",
                "params": {
                    "edges": [_pt(p) for p in step["edges"]],
                    "radius": _mm(step["radius"]),
                },
            }
        )

    # ---- 输出 ---------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {"commands": self.commands}

    def __len__(self) -> int:
        return len(self.commands)


def compile_spec(spec: Spec, expect: dict[str, Any] | None = None) -> CommandScript:
    return CommandScript(spec, expect)
