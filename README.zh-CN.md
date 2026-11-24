# spec2cad — 一份 spec，确定性出件

[English](README.md) | 简体中文

把**人写/LLM 写的规格（spec）**编译成 **STEP**，并当场用两个独立来源交叉验证（L1 闸门）。

```
spec.toml ──compile──▶ solidcraft 命令脚本 ──run──▶ part.step
             │                        │
             │ 解析式期望              └── 内核自检 ──┐
             │ （体积/bbox/孔位                      │
             │  由公式推导，不抄答案）                 ├── L1 断言（一致性闸门）
             └──────────────────────  FreeCAD/OCCT 独立读回 ┘
                                                      ▼
                                        report.json（逐项 PASS/FAIL + 偏差）
```

LLM（如果参与）只负责"人话 → spec"这一步；几何由内核算，不由模型猜。

## 为什么是这条路

| 路径 | 确定性 | 耗时 | 可复现 | 可测试 | 结论 |
|---|---|---|---|---|---|
| **spec → 命令脚本 → `solidcraft-cli run`** | 完全确定 | **0.1–0.4 s/件** | 字节级可复现 | CI 可跑 | **主路径** |
| LLM 直接吐 STEP 文本 | — | — | — | 语法/拓扑必崩（实测被双内核拒收） | 不采用 |

关键分工：**LLM 只写 spec（参数化文本），几何由内核算**。spec 里的期望值由解析式
推导（`V = L*W*T − n·π·(D/2)²·T`），不手抄实测值——否则闸门就退化成"抄答案"。

## 快速开始

```bash
# 1) 装内核（一次性；Linux x86_64 / macOS universal 预编译）
./tools/install-solidcraft.sh

# 2) 出件 + 双源验证
PYTHONPATH=src python3 -m spec2cad build spec/examples/plate3h.toml -o out/

# 产物
out/plate3h.step           # 几何事实源（AP242）
out/plate3h.commands.json  # 编译出的内核脚本（可 diff / 可回放）
out/plate3h.report.json    # L1 逐项断言报告
```

本机实测（macOS arm64，solidcraft-cli 0.47.0 + FreeCAD 1.1.3 oracle）：

```
spec   : spec/examples/plate3h.toml  (7 条内核命令)
STEP   : out/plate3h.step  (18190 bytes, 0.187s)
L1 闸门：24/24 通过
```

24 项断言 = 主内核 13 项（体积 / bbox 六面 / 孔数 / 闭合 / 实体数）+ oracle 8 项
（体积 / 三向尺寸 / 孔数 / 有效 / 实体数）+ 孔位 L1.5 每孔一条 + 跨源 1 项。

## L1 闸门

三类来源必须一致，缺证据不算通过：

| 来源 | 是什么 | 容差 |
|---|---|---|
| expected | spec 解析式推导（不抄答案） | — |
| kernel | solidcraft 自己量自己 | 体积相对 1e-6；bbox 绝对 1e-6 mm；圆柱面数精确相等 |
| oracle | FreeCAD/OCCT 独立读回同一个 STEP 再量 | 同上；孔位轴心绝对 1e-6 mm（L1.5） |

断言清单（逐项写进 report.json）：

| 断言 | 内容 |
|---|---|
| `volume@kernel` / `volume@oracle` | 体积 vs 解析式（相对 1e-6） |
| `size_x/y/z@*`、`bbox_min/max_*@kernel` | 包围盒逐面（绝对 1e-6 mm） |
| `cylinders@kernel` / `cylinders@oracle` | 圆柱面数 == 期望孔数（+外圆），精确相等 |
| `closed@kernel` / `valid@oracle` | 实体闭合 / 有效 |
| `bodies@kernel` / `solids@oracle` | 恰好 1 个实体 |
| `hole@(x,y)`（L1.5） | 每个孔：OCCT 圆柱面轴心与 spec 声明重合（1e-6 mm） |
| `volume@kernel==oracle` | 两个来源互相一致 |

**L1.5 为什么单独立一条**：孔挪了位置但还在实体内部时，体积和孔数都不变——
只有孔位断言能抓住"体积对、孔不对"的错件。

### 负例（闸门的牙齿）

`spec/examples/plate3h-broken.toml`：孔心写到板边界外，体积只差 1/4 个孔——
肉眼看着正常，闸门拦得住。本机实测：

| 断言 | 期望 | 实测 | 结果 |
|---|---|---|---|
| volume@kernel | 10821.902755 | 11607.300918 | FAIL |
| cylinders@kernel | 3 | 2 | FAIL |
| volume@oracle | 10821.902755 | 11607.300918 | FAIL |
| hole@(-15.000,0.000) | 轴心重合 | 没有对应圆柱面 | FAIL |

退出码 1。**闸门不吃素**：几何看着正常、数值错，一样过不去。

## 基准件集（11 件 / 10 正例 + 1 负例）

```bash
PYTHONPATH=src python3 -m spec2cad bench spec/examples -o out/bench
```

本机实测（2026-11，全部三源一致；负例被拦算符合预期）：

| spec | 负例 | 期望体积 | 主内核 | OCCT | 圆柱面 | 耗时 | 结果 |
|---|---|---|---|---|---|---|---|
| blind-plate | 否 | 23660.708 | 23660.708 | 23660.708 | 1 | 98 ms | PASS |
| boss-plate | 否 | 17428.672 | 17428.672 | 17428.672 | 1 | 140 ms | PASS |
| bracket-l | 否 | 21600.000 | 21600.000 | 21600.000 | 0 | 79 ms | PASS |
| fillet-plate | 否 | 11845.487 | 11845.487 | 11845.487 | 4 | 337 ms | PASS |
| flange | 否 | 45553.093 | 45553.093 | 45553.093 | 7 | 385 ms | PASS |
| plate-boss-holes | 否 | 18251.593 | 18251.593 | 18251.593 | 5 | 332 ms | PASS |
| plate-grid | 否 | 22190.443 | 22190.443 | 22190.443 | 6 | 309 ms | PASS |
| plate3h-broken | 是 | 10821.903 | 11607.301 | 11607.301 | 2 | 130 ms | 拦住了（符合预期） |
| plate3h | 否 | 10821.903 | 10821.903 | 10821.903 | 3 | 179 ms | PASS |
| stepped-shaft | 否 | 17954.202 | 17954.202 | 17954.202 | 2 | 160 ms | PASS |
| washer | 否 | 12723.450 | 12723.450 | 12723.450 | 2 | 144 ms | PASS |

**11/11 符合预期**：10 件三源一致（解析式 = 主内核 = OCCT，偏差在 1e-6 闸门内），
1 件负例被拦。输出 STEP 为 AP242（`FILE_SCHEMA AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`）。

## 解析式模型（实测校准）

| op | 体积 | 包围盒 | 圆柱面 |
|---|---|---|---|
| `box` | ±L·W·H | 角点 `at..at+(L,W,H)` / 居中 `at±(L/2,W/2,H/2)` | - |
| `cylinder` | ±π·r²·h | **base 是底面圆心**：xy 各 ±r，z 为 `base_z..+h` | +1 |
| `holes` | −n·π·r²·d（d = 板厚 or 盲孔 depth） | 不变 | +n |
| `fillet`（竖边） | −4·r²·(1−π/4)·H | 不变 | +4 |

## spec 形态（v1）

```toml
[part]
name = "plate3h"
units = "mm"

[params]                 # 尺寸参数：spec 里唯一允许出现数字的地方
L = 60.0
W = 40.0
T = 5.0
D = 10.0

[[build]]
op = "box"               # 长方体，anchor="corner" 角点锚在 at / "center" 居中
length = "L"
width = "W"
height = "T"

[[build]]
op = "holes"             # 孔：显式坐标 / 圆周均布 / 网格，通孔 through 或盲孔 depth
face = "XY"
diameter = "D"
through = "T"            # 通孔必须等于该方向总高；盲孔用 depth
at = [[15.0, 20.0], [45.0, 20.0], [30.0, 10.0]]

# 其他可用的 op
#   [[build]] op = "cylinder"  base = [x,y,z]  diameter/radius  height  operation = "join"|"cut"
#   [[build]] op = "fillet"    edges = "vertical"  radius = "R"
#   [build.pattern]            type = "circular"|"grid"  ...

[checks]                 # 期望值由 spec 解析式算出，不手抄
volume = "auto"
bbox = "auto"
cylinders = "auto"
```

设计铁律：

1. **spec 是唯一事实源**。编译器不读环境、不读上一轮产物、不猜默认值。
   歧义（锚点、朝向、通孔/盲孔）必须显式写，缺了报错，不许自动补。
2. **期望值必须解析式推导**，不许把实测值抄进 spec 当期望。
   发现自己在改期望值去凑实测，先怀疑几何而不是改期望。
3. **孔位单独立断言**（L1.5）：体积对、孔数对，孔挪了位置照样是错的。
4. **oracle 缺失记 FAIL 而不是跳过通过**——缺证据不算通过（`--no-oracle` 只出件不出证据）。
5. **零运行时依赖**：纯 Python 标准库 + 外挂二进制（solidcraft-cli / FreeCAD 只当 oracle）。
6. **负例必须留**。`spec/examples/*-broken.toml` 是闸门的牙齿，改动 verify 后要重跑确认它仍被拦。

## 测试

```bash
python3 -m unittest discover -s tests -t . -v   # 30 项；缺内核时端到端自动 skip，不假绿
```

覆盖：表达式求值 / spec 校验（锚点、通孔深度一致性、fillet 前置约束、负例语义）/
解析式期望值 / 命令脚本编译产物 / solidcraft 端到端 / L1.5 孔位断言 / 负例拦截。

## 已知坑（实测踩出来的）

- `solid.box` 的**角在原点，不居中**：要居中必须显式 `anchor = "center"`（编译器补平移）。
  这是 L1 要抓的第一类歧义。
- `solid.cylinder` 的 `base` 是**底面圆心**，不是角点：bbox 要 xy 各 ±r。
  按角点算会出现"体积全对、bbox 全错"——体积对不代表模型对。
- 孔落在板边界/外面时，体积可能只差 1/4 个孔——肉眼正常、数值错，L1 靠解析式才抓得住。
- conda 环境的 FreeCAD 不在默认 `sys.path` 上：oracle 探针会自动把 `<env>/lib` 插进去再试；
  也可用 `FREECAD_PYTHON` 显式指定。

## 目录

```
spec/examples/     基准件 spec（10 正例 + 1 负例，负例必须被闸门拦下）
src/spec2cad/      spec 解析 / 编译 / 执行 / L1 验证
  spec.py            spec 解析 + 解析式期望推导（不抄答案）
  emit.py            spec -> solidcraft 命令脚本（JSON，可 diff / 可回放）
  kernel.py          solidcraft-cli 出件 + FreeCAD/OCCT oracle 读回
  verify.py          L1 / L1.5 断言
  cli.py             build / bench / report
tests/             管线测试（缺内核时端到端自动 skip，不假绿）
tools/             内核安装脚本
out/               产物（gitignore）
```

## License

MIT（见 LICENSE）。内核 solidcraft-cli 为 MIT OR Apache-2.0，oracle 用 FreeCAD（LGPL-2.1），
仅作为独立验证进程调用，不随产物分发。

---

关键词：solidcraft、solidcraft-cli、CAD 内核、STEP 导出、AP242、参数化 CAD、spec 驱动建模、FreeCAD/OCCT 交叉验证、确定性出件。
