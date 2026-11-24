"""spec2cad 命令行（L1：spec -> STEP -> 双源交叉验证）。

    python3 -m spec2cad build  spec/examples/plate3h.toml -o out/
    python3 -m spec2cad build  my.toml -o out/ --no-oracle
    python3 -m spec2cad bench  spec/examples -o out/bench/
    python3 -m spec2cad bench  spec/examples -o out/bench/ --pattern plate
    python3 -m spec2cad report out/plate3h.report.json

退出码：0 = L1 全部通过；1 = 有断言失败；2 = spec/内核错误。
bench：`*-broken.toml` 是负例，**必须失败**才算通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .emit import compile_spec
from .kernel import (
    KernelError,
    find_cli,
    measure_freecad,
    normalise_kernel_result,
    run_script,
)
from .spec import SpecError, load
from .verify import check_l1

MARK = {True: "PASS", False: "FAIL"}


def _print_table(rows: list[dict], stream=sys.stdout) -> None:
    """Markdown 表格（不用框线字符）。"""
    print("| 断言 | 来源 | 期望 | 实测 | 容差 | 结果 |", file=stream)
    print("|---|---|---|---|---|---|", file=stream)
    for r in rows:
        print(
            f"| {r['name']} | {r['source']} | {r['expected']} | {r['actual']} "
            f"| {r['tolerance'] or '-'} | {MARK[r['pass']]} |",
            file=stream,
        )


def build_one(spec_path: Path, outdir: Path, *, use_oracle: bool = True, quiet: bool = False) -> dict:
    """跑一件：spec -> 命令脚本 -> STEP -> L1。返回报告（含 exit_code）。"""
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        spec = load(spec_path)
    except SpecError as exc:
        return {"exit_code": 2, "error": f"spec 错误：{exc}", "spec_path": str(spec_path)}

    try:
        find_cli()
    except KernelError as exc:
        return {"exit_code": 2, "error": f"内核错误：{exc}", "spec_path": str(spec_path)}

    expected = spec.expect()
    script_path = outdir / f"{spec.name}.commands.json"
    step_path = outdir / f"{spec.name}.step"

    script = compile_spec(spec, expected)
    text = json.dumps(script.to_json(), ensure_ascii=False, indent=2)
    script_path.write_text(text, encoding="utf-8")
    try:
        run = run_script(script.to_json(), step_path, workdir=outdir)
    except KernelError as exc:
        return {"exit_code": 2, "error": f"内核错误：{exc}", "spec_path": str(spec_path)}
    measured = normalise_kernel_result(run["result"])

    oracle = measure_freecad(step_path) if use_oracle else None
    l1 = check_l1(expected, measured, oracle)

    report = {
        "spec": {
            "name": spec.name,
            "source": str(spec_path),
            "units": spec.units,
            "params": spec.params,
            "features": [f.raw for f in spec.features],
        },
        "artifacts": {
            "step": str(step_path),
            "commands": str(script_path),
            "step_bytes": step_path.stat().st_size,
        },
        "kernel": {
            "name": "solidcraft",
            "elapsed_s": run["elapsed_s"],
            "command": run["command"],
            "measured": measured,
        },
        "oracle": oracle,
        "expected": {k: v for k, v in expected.items() if k != "trace"},
        "l1": l1.to_dict(),
        "version": __version__,
        "exit_code": 0 if l1.passed else 1,
    }
    (outdir / f"{spec.name}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not quiet:
        print(f"spec   : {spec_path}  ({len(script)} 条内核命令)")
        print(
            f"STEP   : {step_path}  ({report['artifacts']['step_bytes']} bytes, "
            f"{run['elapsed_s']:.3f}s)"
        )
        print(f"报告   : {outdir / (spec.name + '.report.json')}")
        print()
        _print_table(l1.checks)
        print()
    return report


def cmd_build(args: argparse.Namespace) -> int:
    report = build_one(
        Path(args.spec), Path(args.out), use_oracle=not args.no_oracle
    )
    if "error" in report:
        print(report["error"], file=sys.stderr)
    total = report.get("l1", {}).get("total", 0)
    failed = report.get("l1", {}).get("failed", [])
    if total:
        print(f"L1 闸门：{total - len(failed)}/{total} 通过" + (f"，失败 {failed}" if failed else ""))
    return int(report["exit_code"])


def cmd_bench(args: argparse.Namespace) -> int:
    root = Path(args.specs)
    specs = sorted(root.glob("*.toml"))
    if args.pattern:
        specs = [p for p in specs if args.pattern in p.name]
    if not specs:
        print(f"{root} 下没有 *.toml", file=sys.stderr)
        return 2

    use_oracle = not args.no_oracle
    rows: list[dict] = []
    for spec_path in specs:
        negative = spec_path.stem.endswith("-broken")
        report = build_one(spec_path, Path(args.out), use_oracle=use_oracle, quiet=True)
        row: dict = {"name": spec_path.stem, "negative": negative, "ok": False}
        if "error" in report:
            spec_level = report["error"].startswith("spec 错误")
            row["ok"] = negative and spec_level  # 负例在 spec 层被拦也算拦住；内核错误不算
            row["error"] = report["error"]
            rows.append(row)
            continue
        l1 = report["l1"]
        got_pass = bool(l1["pass"])
        measured = report["kernel"]["measured"] or {}
        row["ok"] = (not got_pass) if negative else got_pass
        row.update(
            {
                "exp_vol": report["expected"]["volume_mm3"],
                "vol": measured.get("volume_mm3"),
                "orc": (report["oracle"] or {}).get("volume_mm3"),
                "cyl": measured.get("cylinders"),
                "elapsed": report["kernel"]["elapsed_s"],
                "failed": l1["failed"],
            }
        )
        rows.append(row)

    print("| spec | 负例 | 期望体积 | 主内核 | OCCT | 圆柱面 | 耗时 | 结果 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        neg = "是" if r["negative"] else "否"
        if "error" in r:
            verdict = "拦住了（符合预期）" if r["ok"] else f"FAIL（{r['error'].splitlines()[0][:48]}）"
            print(f"| {r['name']} | {neg} | - | - | - | - | - | {verdict} |")
            continue
        if r["negative"] and r["ok"]:
            verdict = "拦住了（符合预期）"
        elif r["ok"]:
            verdict = "PASS"
        else:
            brief = ",".join(r["failed"][:3]) + (f" 等{len(r['failed'])}项" if len(r["failed"]) > 3 else "")
            verdict = f"FAIL（{brief}）"

        def _fmt(v, spec="{:.3f}"):
            return spec.format(v) if isinstance(v, (int, float)) else "-"

        print(
            f"| {r['name']} | {neg} | {_fmt(r['exp_vol'])} | {_fmt(r['vol'])} "
            f"| {_fmt(r['orc'])} | {_fmt(r['cyl'], '{}')} "
            f"| {r['elapsed'] * 1000:.0f} ms | {verdict} |"
        )

    bad = [r["name"] for r in rows if not r["ok"]]
    print()
    summary = f"基准件：{len(rows) - len(bad)}/{len(rows)} 符合预期"
    if bad:
        summary += f"，问题件 {bad}"
    print(summary)
    return 0 if not bad else 1


def cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.report)
    if not path.is_file():
        print(f"报告不存在：{path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    _print_table(data["l1"]["checks"])
    print()
    print(f"L1: {'PASS' if data['l1']['pass'] else 'FAIL'}"
          f"  ({data['l1']['total']} 项, 失败 {len(data['l1']['failed'])})")
    return 0 if data["l1"]["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spec2cad", description=__doc__)
    parser.add_argument("--version", action="version", version=f"spec2cad {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="把 spec 编译成 STEP 并跑 L1 闸门")
    build.add_argument("spec", help="spec 文件（.toml / .json / .yaml）")
    build.add_argument("-o", "--out", default="out", help="产物目录（默认 out/）")
    build.add_argument("--no-oracle", action="store_true", help="跳过 FreeCAD 独立复核（只出件，报告会记 oracle 缺失）")
    build.set_defaults(func=cmd_build)

    bench = sub.add_parser("bench", help="跑整组基准件（含负例）")
    bench.add_argument("specs", nargs="?", default="spec/examples", help="spec 目录")
    bench.add_argument("-o", "--out", default="out/bench", help="产物目录")
    bench.add_argument("--pattern", default="", help="只跑名字含该串的 spec")
    bench.add_argument("--no-oracle", action="store_true", help="跳过 FreeCAD 独立复核")
    bench.set_defaults(func=cmd_bench)

    report = sub.add_parser("report", help="重打已有报告")
    report.add_argument("report", help="*.report.json")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
