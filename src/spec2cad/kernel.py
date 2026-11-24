"""内核调用层：solidcraft-cli 出件 + FreeCAD/OCCT 独立复核。

两个内核的角色
--------------
- **solidcraft**：主内核（出件）。确定性、毫秒级、自带体积/包围盒/面型统计。
- **FreeCAD/OCCT**：独立 oracle。只负责"读回 STEP 并量一遍"，
  与出件内核无共享代码，避免同源性误判。

任一内核缺失时优雅降级（记 `skipped`），不假装通过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = [
    "KernelError",
    "find_cli",
    "find_freecad_python",
    "run_script",
    "measure_freecad",
    "normalise_kernel_result",
]


class KernelError(RuntimeError):
    """内核调用失败（不是 spec 的问题）。"""


# ---------------------------------------------------------------- 发现可执行文件


def find_cli() -> str:
    """按 环境变量 -> PATH -> 常见安装位置 找 solidcraft-cli。"""
    env = os.environ.get("SOLIDCRAFT_CLI")
    if env and Path(env).is_file():
        return env
    found = shutil.which("solidcraft-cli")
    if found:
        return found
    for candidate in (
        Path.home() / "bin" / "solidcraft-cli",
        Path("tools/bin/solidcraft-cli").resolve(),
        Path("/usr/local/bin/solidcraft-cli"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise KernelError(
        "找不到 solidcraft-cli；设 SOLIDCRAFT_CLI，或运行 tools/install-solidcraft.sh"
    )


def find_freecad_python() -> str | None:
    """找能 `import FreeCAD` 的 python；没有就返回 None（跳过 oracle）。"""
    env = os.environ.get("FREECAD_PYTHON")
    candidates = [env] if env else []
    candidates += [
        str(Path.home() / "mamba/envs/fcprobe/bin/python"),
        "/usr/lib/freecad/bin/python",
        "/usr/bin/python3",
    ]
    probe = (
        "import sys\n"
        "sys.path.insert(0, {lib!r})\n"
        "try:\n"
        "    import FreeCAD, Part  # noqa: F401\n"
        "except Exception:\n"
        "    sys.exit(1)\n"
    )
    for cand in candidates:
        if not cand or not Path(cand).is_file():
            continue
        # conda 装的 FreeCAD 不在默认 sys.path 上，探针要把 <env>/lib 插进去再试。
        code = probe.format(lib=str(Path(cand).parent.parent / "lib"))
        try:
            done = subprocess.run([cand, "-c", code], capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            return cand
    return None


# ---------------------------------------------------------------- 出件


def run_script(
    commands: dict[str, Any] | list[dict[str, Any]],
    out_step: Path,
    *,
    workdir: Path | None = None,
    timeout: int = 300,
    validation: str = "each_feature",
    param_check: str = "strict",
) -> dict[str, Any]:
    """把命令脚本交给 solidcraft-cli 执行，返回 {result, elapsed_s, command}。"""
    cli = find_cli()
    payload = commands if isinstance(commands, dict) else {"commands": commands}
    workdir = workdir or out_step.parent
    workdir.mkdir(parents=True, exist_ok=True)
    out_step.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        script_path = Path(tmp) / "script.json"
        result_path = Path(tmp) / "result.json"
        script_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        before = time.perf_counter()
        done = subprocess.run(
            [
                cli,
                "run",
                str(script_path),
                "--out",
                str(out_step),
                "--json-out",
                str(result_path),
                "--validation",
                validation,
                "--param-check",
                param_check,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - before

        result: dict[str, Any] | None = None
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                result = None

    if done.returncode != 0:
        raise KernelError(
            "solidcraft-cli 执行失败\n"
            f"  exit={done.returncode}\n"
            f"  stderr={done.stderr.strip()[:2000]}\n"
            f"  stdout={done.stdout.strip()[:2000]}"
        )
    if not out_step.is_file():
        raise KernelError(f"内核返回 0 但没有生成 {out_step}")

    return {
        "command": [cli, "run", "script.json", "--out", out_step.name],
        "elapsed_s": round(elapsed, 4),
        "result": result,
        "stdout": done.stdout,
        "stderr": done.stderr,
    }


def normalise_kernel_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """把内核 --json-out 归一成 {volume_mm3, bbox, cylinders, closed, faces}。"""
    if not result:
        return None
    bodies = result.get("bodies") or []
    if not bodies:
        return None
    body = bodies[0]
    face_types = body.get("face_types") or {}
    bbox = body.get("bbox") or {}
    return {
        "volume_mm3": body.get("volume_mm3"),
        "bbox": {"min": bbox.get("min"), "max": bbox.get("max")},
        "cylinders": int(face_types.get("cylinder", 0)),
        "faces": body.get("faces"),
        "closed": bool(body.get("closed")),
        "body_count": result.get("body_count", len(bodies)),
    }


# ---------------------------------------------------------------- 独立 oracle

# 注意顺序：FreeCAD 会重建 __main__ 的命名空间，先 import 的模块会被解绑
# （mlu370 的 FreeCAD 26.3 实测：json 在 import FreeCAD 之后会 NameError）。
# 所以 FreeCAD 必须最先 import，其余模块放在它之后。
_FREECAD_SCRIPT = r"""
import sys
sys.path.insert(0, {lib!r})
import FreeCAD, Part  # noqa: F401  ← 必须最先
import json

shape = Part.Shape()
shape.read({path!r})
cyl_faces = [f for f in shape.Faces if hasattr(f.Surface, "Radius")]
axes = [
    {{
        "radius": round(float(f.Surface.Radius), 6),
        "x": round(float(f.Surface.Center.x), 6),
        "y": round(float(f.Surface.Center.y), 6),
        "z": round(float(f.Surface.Center.z), 6),
        # 该圆柱面的 z 跨度：用来区分通孔与盲孔（L3 判隐藏线用）
        "zmin": round(float(f.BoundBox.ZMin), 6),
        "zmax": round(float(f.BoundBox.ZMax), 6),
    }}
    for f in cyl_faces
]
radii = sorted({{round(float(f.Surface.Radius), 6) for f in cyl_faces}})
bb = shape.BoundBox
payload = {{
    "volume_mm3": float(shape.Volume),
    "cylinders": len(cyl_faces),
    "hole_axes": axes,
    "radii": radii,
    "bbox": {{
        "min": [bb.XMin, bb.YMin, bb.ZMin],
        "max": [bb.XMax, bb.YMax, bb.ZMax],
    }},
    "solids": len(shape.Solids),
    "valid": bool(shape.isValid()),
}}
print("SPEC2CAD_ORACLE " + json.dumps(payload))
"""


def measure_freecad(step: Path, python: str | None = None) -> dict[str, Any] | None:
    """用 FreeCAD/OCCT 独立读回 STEP 并测量；不可用时返回 None。"""
    python = python or find_freecad_python()
    if not python:
        return None
    lib = str(Path(python).parent.parent / "lib")
    code = _FREECAD_SCRIPT.format(lib=lib, path=str(step))
    try:
        done = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None

    for line in reversed(done.stdout.splitlines()):
        if line.startswith("SPEC2CAD_ORACLE "):
            return json.loads(line[len("SPEC2CAD_ORACLE ") :])
    return None
