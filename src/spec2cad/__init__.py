"""spec2cad — 一份 spec，确定性出件（L1）。

模块划分：
    spec     解析 / 校验 spec，解析式推导期望值（不抄答案）
    emit     spec -> solidcraft 命令脚本（JSON，可 diff / 可回放）
    kernel   调用 solidcraft-cli 出件；调用 FreeCAD/OCCT 做独立 oracle
    verify   L1 断言：解析式 vs 内核自报 vs OCCT 读回
    cli      命令行入口（build / bench / report）
"""

__version__ = "0.1.0"
