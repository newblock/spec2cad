"""spec 解析、校验与期望值推导（v1：box / cylinder / holes / fillet）。

设计原则
--------
1. **spec 是唯一事实源**：编译器只读 spec，不读环境、不读上一轮产物。
2. **期望值由解析式推导**：体积/包围盒/圆柱面数从 params 算出来，绝不手抄常量。
   每个模型都经过实测校准，公式写在对应分支的注释里（见 `_EXPECT_DOC`）。
3. **歧义必须显式**：锚点、朝向、通孔/盲孔都必须写，缺了直接报错。
4. **推导与出件共用一条 trace**：`expect()` 同时产出几何轨迹（每步的 lo/hi、孔位、
   圆角边中点），`emit.py` 直接用这条 trace 生成命令——避免两侧各算一遍而漂移。
"""

from __future__ import annotations

import ast
import json
import math
import operator
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = ["Spec", "SpecError", "load", "expr"]

_EXPECT_DOC = """
解析式模型（均经 solidcraft 0.47 实测校准，见 tests/test_expectations.py）
  box        V += L*W*H；bbox = at..at+(L,W,H) 或 ±(L/2,W/2,H/2)
  cylinder   V ± pi*r^2*h；bbox = base±r（xy）、base_z..+h（z）；圆柱面 += 1（轴向仅 Z）
  holes      V -= n*pi*r^2*d；d = 板厚（through）或 depth（盲孔）；圆柱面 += n
  fillet     竖边圆角：V -= 4*r^2*(1-pi/4)*H；圆柱面 += 4；bbox 不变
"""


class SpecError(ValueError):
    """spec 不合法（不是内核错误）。"""


# ---------------------------------------------------------------- 表达式求值

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise SpecError(f"表达式只允许数值常量，收到 {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise SpecError(f"表达式引用了未定义的参数 {node.id!r}（已定义：{sorted(env)}）")
        return float(env[node.id])
    if isinstance(node, ast.BinOp):
        fn = _BINOPS.get(type(node.op))
        if fn is None:
            raise SpecError(f"不支持的运算符 {type(node.op).__name__}")
        return fn(_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.UnaryOp):
        fn = _UNOPS.get(type(node.op))
        if fn is None:
            raise SpecError(f"不支持的一元运算符 {type(node.op).__name__}")
        return fn(_eval_node(node.operand, env))
    raise SpecError(f"表达式里不允许的语法：{type(node).__name__}")


def expr(value: Any, env: dict[str, float]) -> float:
    """把 spec 里的数值/表达式解析成 float（mm）。支持 + - * / ** % 与括号。"""
    if isinstance(value, bool):
        raise SpecError(f"布尔值不是尺寸：{value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise SpecError(f"表达式语法错误 {text!r}: {exc.msg}") from exc
        return _eval_node(tree, env)
    raise SpecError(f"既不是数值也不是表达式字符串：{value!r}")


def _check_point(value: Any, keys: tuple[str, str, str], where: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SpecError(f"{where} 必须是 [x, y, z]，收到 {value!r}")
    return [float(v) for v in value]


# ---------------------------------------------------------------- spec 结构


@dataclass
class Feature:
    op: str
    params: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    def number(self, key: str) -> float:
        if key not in self.params:
            raise SpecError(f"{self.op} 缺少必填字段 {key!r}")
        value = self.params[key]
        if not isinstance(value, float):
            raise SpecError(f"{self.op}.{key} 不是尺寸")
        return value

    def point(self, key: str, default: list[float] | None = None) -> list[float]:
        if key not in self.params:
            if default is None:
                raise SpecError(f"{self.op} 缺少必填字段 {key!r}")
            return list(default)
        return _check_point(self.params[key], ("x", "y", "z"), f"{self.op}.{key}")

    @property
    def operation(self) -> str:
        op = self.params.get("operation", "new")
        if op not in ("new", "join", "cut"):
            raise SpecError(f"{self.op}.operation 必须是 new/join/cut，收到 {op!r}")
        return op


@dataclass
class Spec:
    name: str
    units: str
    params: dict[str, float]
    features: list[Feature]
    checks: dict[str, Any]
    source: Path | None = None

    # ---- 主推导 -------------------------------------------------------

    def expect(self) -> dict[str, Any]:
        """解析式推导期望值 + 几何轨迹（emit 直接用 trace，不再重算）。"""
        volume = 0.0
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        cylinders = 0
        hole_positions: list[tuple[float, float]] = []
        hole_specs: list[dict[str, float]] = []
        trace: dict[int, dict[str, Any]] = {}
        solid_seen = False
        last_box: dict[str, Any] | None = None

        def union(b_lo: list[float], b_hi: list[float]) -> None:
            for i in range(3):
                lo[i] = min(lo[i], b_lo[i])
                hi[i] = max(hi[i], b_hi[i])

        for index, f in enumerate(self.features):
            if f.op == "box":
                L, W, H = f.number("length"), f.number("width"), f.number("height")
                anchor = f.params.get("anchor")
                if anchor not in ("corner", "center"):
                    raise SpecError(
                        f"box.anchor 必须是 'corner' 或 'center'，收到 {anchor!r}"
                    )
                at = f.point("at", [0.0, 0.0, 0.0])
                if anchor == "corner":
                    b_lo, b_hi = list(at), [at[0] + L, at[1] + W, at[2] + H]
                else:
                    b_lo = [at[0] - L / 2, at[1] - W / 2, at[2] - H / 2]
                    b_hi = [at[0] + L / 2, at[1] + W / 2, at[2] + H / 2]
                sign = -1 if f.operation == "cut" else 1
                volume += sign * L * W * H
                if sign > 0:
                    union(b_lo, b_hi)
                    solid_seen = True
                    last_box = {"lo": b_lo, "hi": b_hi, "op": f.operation}
                elif not solid_seen:
                    raise SpecError("box(operation=cut) 之前没有实体")
                trace[index] = {"kind": "box", "lo": b_lo, "hi": b_hi, "op": f.operation}

            elif f.op == "cylinder":
                if "radius" in f.params:
                    r = f.number("radius")
                else:
                    r = f.number("diameter") / 2
                h = f.number("height")
                base = f.point("base", [0.0, 0.0, 0.0])
                sign = -1 if f.operation == "cut" else 1
                volume += sign * math.pi * r * r * h
                cylinders += 1  # 圆柱侧面（实测：join/cut 都各产生 1 个圆柱面）
                if sign > 0:
                    # base 是底面**圆心**（实测），不是角点：xy 各向两侧各 r
                    union(
                        [base[0] - r, base[1] - r, base[2]],
                        [base[0] + r, base[1] + r, base[2] + h],
                    )
                    solid_seen = True
                    last_box = None  # 圆角只支持长方体
                elif not solid_seen:
                    raise SpecError("cylinder(operation=cut) 之前没有实体")
                trace[index] = {"kind": "cylinder", "base": base, "r": r, "h": h,
                                "op": f.operation}

            elif f.op == "holes":
                if not solid_seen:
                    raise SpecError("holes 之前没有任何实体")
                d = f.number("diameter")
                r = d / 2
                face = f.params.get("face", "XY")
                if face != "XY":
                    raise SpecError(f"v1 只支持 XY 面上的孔，收到 face={face!r}")
                positions = self._hole_positions(f)
                top_z = hi[2]
                if "through" in f.params:
                    declared = expr(f.params["through"], self.params)
                    span = top_z - lo[2]
                    if abs(declared - span) > 1e-6:
                        raise SpecError(
                            f"holes.through={declared} 与该方向总高 {span:.6f} 不一致"
                            "（through 必须等于实体总高；盲孔请用 depth）"
                        )
                    depth, start_z, direction = span, lo[2], "positive"
                elif "depth" in f.params:
                    depth = expr(f.params["depth"], self.params)
                    span = top_z - lo[2]
                    if not 0 < depth <= span + 1e-9:
                        raise SpecError(f"holes.depth={depth} 必须落在 (0, {span:.6f}]")
                    start_z, direction = top_z, "negative"
                else:
                    raise SpecError("holes 必须写 through（通孔）或 depth（盲孔深度）")
                volume -= len(positions) * math.pi * r * r * depth
                cylinders += len(positions)
                hole_positions.extend(positions)
                hole_specs.extend({"x": x, "y": y, "r": r} for x, y in positions)
                trace[index] = {
                    "kind": "holes",
                    "positions": positions,
                    "diameter": d,
                    "depth": depth,
                    "start_z": start_z,
                    "direction": direction,
                }

            elif f.op == "fillet":
                if last_box is None:
                    raise SpecError(
                        "fillet 必须紧跟一次 box（圆角只支持长方体的竖边）"
                    )
                which = f.params.get("edges", "vertical")
                if which != "vertical":
                    raise SpecError(f"v1 只支持 edges='vertical'，收到 {which!r}")
                r = f.number("radius")
                height = last_box["hi"][2] - last_box["lo"][2]
                if r * 2 > min(
                    last_box["hi"][0] - last_box["lo"][0],
                    last_box["hi"][1] - last_box["lo"][1],
                ):
                    raise SpecError(f"fillet.radius={r} 太大（超过最短边的一半）")
                # 90° 凸边圆角：每个单位长度去掉 r^2 - pi*r^2/4
                volume -= 4 * r * r * (1 - math.pi / 4) * height
                cylinders += 4
                b_lo, b_hi = last_box["lo"], last_box["hi"]
                zc = (b_lo[2] + b_hi[2]) / 2
                corners = [
                    [b_lo[0], b_lo[1], zc],
                    [b_hi[0], b_lo[1], zc],
                    [b_lo[0], b_hi[1], zc],
                    [b_hi[0], b_hi[1], zc],
                ]
                trace[index] = {"kind": "fillet", "edges": corners, "radius": r}
                last_box = None  # 一个 box 只允许一次竖边圆角

            else:
                raise SpecError(f"不支持的 op {f.op!r}（v1: box/cylinder/holes/fillet）")

        if not solid_seen:
            raise SpecError("spec 里没有任何实体")
        return {
            "volume_mm3": volume,
            "bbox": {"min": [round(v, 9) for v in lo], "max": [round(v, 9) for v in hi]},
            "cylinders": cylinders,
            "hole_positions": hole_positions,
            "holes": hole_specs,
            "units": self.units,
            "trace": trace,
        }

    # ---- 孔位 ---------------------------------------------------------

    @staticmethod
    def _hole_positions(f: Feature) -> list[tuple[float, float]]:
        p = f.params
        if "at" in p:
            pts = p["at"]
            if not isinstance(pts, list) or not pts:
                raise SpecError("holes.at 必须是非空坐标列表")
            out = []
            for pt in pts:
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    raise SpecError(f"holes.at 的每一项必须是 [x, y]，收到 {pt!r}")
                out.append((float(pt[0]), float(pt[1])))
            return out
        pat = p.get("pattern")
        if not isinstance(pat, dict):
            raise SpecError("holes 必须给 at（显式坐标）或 pattern（阵列）")
        kind = pat.get("type")
        if kind == "circular":
            cx, cy = pat["center"]
            count = int(pat["count"])
            rad = float(pat["radius"])
            start = math.radians(float(pat.get("start_deg", 0.0)))
            return [
                (cx + rad * math.cos(start + 2 * math.pi * i / count),
                 cy + rad * math.sin(start + 2 * math.pi * i / count))
                for i in range(count)
            ]
        if kind == "grid":
            (x0, y0), (dx, dy) = pat["origin"], pat["spacing"]
            nx, ny = int(pat["nx"]), int(pat["ny"])
            if nx < 1 or ny < 1:
                raise SpecError("holes.pattern(nx/ny) 必须 >= 1")
            return [
                (x0 + i * dx, y0 + j * dy) for j in range(ny) for i in range(nx)
            ]
        raise SpecError(f"v1 只支持 circular / grid 阵列，收到 {kind!r}")


# ---------------------------------------------------------------- 加载


def _read(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".toml":
        return tomllib.loads(text)
    if suffix == ".json":
        return json.loads(text)
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise SpecError("读 YAML 需要 pyyaml；改用 .toml 或 .json") from exc
        return yaml.safe_load(text)
    raise SpecError(f"不认识的 spec 后缀 {suffix!r}（用 .toml / .json / .yaml）")


def _resolve_params(raw: dict[str, Any]) -> dict[str, float]:
    """params 可以有依赖（H = T * 2），按拓扑顺序解析。"""
    resolved: dict[str, float] = {}
    pending = dict(raw)
    while pending:
        progressed = False
        for name in list(pending):
            try:
                resolved[name] = expr(pending[name], resolved)
            except SpecError:
                continue
            del pending[name]
            progressed = True
        if not progressed:
            raise SpecError(f"params 存在循环引用或非法表达式：{sorted(pending)}")
    return resolved


_DIM_KEYS = ("length", "width", "height", "diameter", "radius", "through", "depth")


def _build_features(raw: list[dict[str, Any]], env: dict[str, float]) -> list[Feature]:
    features: list[Feature] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SpecError(f"build[{i}] 必须是对象，收到 {type(item).__name__}")
        op = item.get("op")
        if not op:
            raise SpecError(f"build[{i}] 缺少 op")
        params: dict[str, Any] = {}
        for key, value in item.items():
            if key == "op":
                continue
            if key == "at" and op == "holes":
                params[key] = [(expr(pt[0], env), expr(pt[1], env)) for pt in value]
            elif key in ("at", "base"):
                params[key] = [expr(v, env) for v in value]
            elif key in _DIM_KEYS:
                params[key] = expr(value, env)
            elif key == "pattern":
                pat = dict(value)
                if pat.get("type") == "circular":
                    pat["center"] = tuple(expr(c, env) for c in pat["center"])
                    pat["radius"] = expr(pat["radius"], env)
                elif pat.get("type") == "grid":
                    pat["origin"] = tuple(expr(c, env) for c in pat["origin"])
                    pat["spacing"] = [expr(c, env) for c in pat["spacing"]]
                params[key] = pat
            else:
                params[key] = value
        features.append(Feature(op=op, params=params, raw=item))
    return features


def load(path: str | Path) -> Spec:
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"spec 文件不存在：{path}")
    raw = _read(path)

    part = raw.get("part") or {}
    name = part.get("name") or path.stem
    units = part.get("units", "mm")
    if units != "mm":
        raise SpecError(f"v1 只支持 mm，收到 {units!r}")

    params = _resolve_params(raw.get("params") or {})
    build = raw.get("build")
    if not isinstance(build, list) or not build:
        raise SpecError("spec 必须有非空的 [[build]] 段")

    spec = Spec(
        name=name,
        units=units,
        params=params,
        features=_build_features(build, params),
        checks=raw.get("checks") or {},
        source=path,
    )
    spec.expect()  # 编译前就校验
    return spec


def iter_allowed_keys() -> Iterable[str]:
    return ("part", "params", "build", "checks")
