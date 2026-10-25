# 全量 861 题时间注释实验：结果报告（交方案调整用）

日期：2026-10-23
执行：deepseek harness（严格按 `EXECUTION_PLAN.md` 的 prepare → offline → evaluate → report）
结论一句话：**C1（原始 A0）最好（+17 题，时间类 +10.2pp）；C2/C3 的“规范化字段 + 解析修复”逐级变差，方案假设未成立。**

---

## 1. 执行范围与硬门槛

| 项 | 结果 |
|---|---|
| 题量 | 861 唯一 qid，分类 167 / 77 / 581 / 36（与方案一致） |
| 候选 | 861 唯一 qid，与题单集合完全相等 |
| 原文快照 | 5,882 turns / 299 requests，只读 |
| 非时间保护题 | **692 道，改动 0 道** |
| 变更题 | **127 道**（时间 125 + 已知单跳 2） |
| 跨度校验（`content[start:end] == raw_phrase`） | 0 失败（含 Unicode） |
| 精度纪律（value 精度 == precision） | 0 违规 |
| 预算截断（`format_memories` 丢记录） | 0 失败 |
| 跨用户来源 | 0 例 |
| 300 题 smoke 重放（C0 旧 V1 / C1 旧 A0） | 0 失败 |

**离线门禁全部通过，才进入模型调用。**

---

## 2. 四组结果（同一次运行，共享采样）

| 组 | 定义 | 总分 | 时间(167) | 多跳(77) | 单跳(581) | 推理(36) |
|---|---|---:|---:|---:|---:|---:|
| **C0** | raw_v1，无注释 | 697 (80.95%) | **89 (53.3%)** | 50 | 528 | 30 |
| **C1** | a0_frozen（原始 A0） | **714 (82.93%)** | **106 (63.5%)** | 50 | 528 | 30 |
| **C2** | 同解析，换 spec 字段格式 | 707 (82.11%) | 100 (59.9%) | 50 | **527** | 30 |
| **C3** | spec 字段 + 解析修复 + 原句关联 | 703 (81.65%) | 95 (56.9%) | 50 | 528 | 30 |

### 预定比较

| 比较 | 错转对 | 对转错 | 净 | McNemar 精确 p |
|---|---:|---:|---:|---:|
| C3 − C0（主） | 25 | 19 | **+6** | 0.4514 |
| C2 − C1 | 5 | 12 | **−7** | 0.1435 |
| C3 − C2 | 5 | 9 | **−4** | 0.4240 |

C3−C0 按对话聚类 bootstrap 95% 区间：**[-1.22, +2.69] pp**（跨 0，仅 10 段对话，区间不稳）。

### 调用与缓存

```text
逻辑评测 3,444 次
唯一答题请求 1,222（缓存命中 2,190）
唯一判分请求   996（缓存命中 2,416）
模型 deepseek-v4.1-flash @ kspmas.ksyun.com，temperature 0
```

---

## 3. 关键发现：C1 的优势来自“并列答案”

C1 在时间类上从 89 → 106（+17），而 C2/C3 分别只有 100 / 95。逐题看，差别几乎都指向答案形态：

```text
q: conv-26#q0008  gold: The week before 9 June 2023 / From May 29 to June 4, 2023
  C1: Last week (around 2023-06-02)     ✅  并列：相对 + 绝对
  C3: Last week                        ❌

q: conv-30#q0032  gold: July 2023 / the Friday before 23 July, 2023 / ...
  C1: July 21, 2023                    ✅
  C3: Last Friday                      ❌

q: conv-41#q0018  gold: March 2023
  C1: Last month (March 2023)          ✅
  C3: Last month                       ❌
```

**C1 的注释让模型倾向于补一个括号日期；C2/C3 的“更规范”字段反而把模型推回复述原句。**

C1 也有它的胜利（说明 C3 并非全面更差）：

```text
conv-47#q0027  gold 2019    C1: 2022-07  ❌   C3: 2019      ✅
conv-49#q0048  gold weekend before 17 Oct 2023
               C1: Last weekend ❌   C3: The weekend of October 14–15, 2023 ✅
```

### 注释密度

| 组 | 注释条数（127 题） | 平均/题 |
|---|---:|---:|
| C1 | 834 | 6.6 |
| C2 | 834 | 6.6 |
| C3 | 685 | 5.4 |

C3 的“全题去重”让注释密度下降约 18%，与它的分数下滑方向一致（但和字段变更混在一起，不能单独归因）。

---

## 4. 方案假设的检验结果

| 方案假设 | 实测 | 判定 |
|---|---|---|
| “补 precision 字段能提高换算准确率” | C1 的 `_annotation_line` **本来就有** `precision=month` 与 `calendar_value` | **假设前提不存在** |
| “规范字段格式（span/anchor_date/status）会更好” | C2 − C1 = **−7** | 未成立 |
| “修复合短语/引语/去重会更好” | C3 − C2 = **−4** | 未成立 |
| “修订版整体（C3）优于无注释（C0）” | +6，p=0.45，区间跨 0 | 方向为正，**未证明稳定** |
| “时间类提升、其他类不下降” | 时间 +6，其他三类持平（单跳 C3 持平） | 字面达标，但远弱于 C1 |

补充：`manual_audit.md` 记录本轮同一题同一答案的判定翻转 **0 次**（上轮 300 题曾出现 1 次，未复现），说明本轮判分一致，分差可归因于输入/生成差异。

---

## 5. 对下一版方案的建议（请据此调整）

1. **基准换成 C1**
   任何新组的主比较应该是 **C* − C1**，而不是 C* − C0。C1 = 714/861、时间 106/167，是当前要超越的对象。

2. **不要再加结构化字段**
   C2/C3 已经证明字段增多会让时间类掉分。如果还想验证“字段”，只允许**单变量**改动，并且只加一个与答案表面形式有关的字段，例如：

   ```text
   calendar_value=2023-06; precision=month; calendar_text="June 2023"
   ```

   其余一律冻结（不加 span、不加 source_sentence、不改去重、不改 max_mentions）。

3. **下一轮真正的假设：并列答案**
   建议把实验目标从“注释更规范”改成“让答案稳定输出『相对短语 + 绝对日期』”。可测的干预点：
   - 保持 C1 短格式，只在证据里给出与该行事件直接对应的日期（而非整窗多锚点）；
   - 或者对 week/weekend 类保留原句、同时给出区间（`the week of 2023-05-29 to 2023-06-04`），因为金标里 18 道就是周/周末。

4. **周/周末仍未解决**
   167 道时间题里，金标为周/周末形态的有 18 道，A0/C1/C3 都不解析它们。这是目前最大的单块剩余空间。

5. **保留本轮的方法学**
   以下必须保留，否则结果不可比：
   - 全量 861 + 692 保护断言
   - 同请求共享采样（避免未改动题重复采样制造假分差）
   - fingerprint 校验的断点续跑
   - 结构化 trace（span/source_sentence/status/render 原因）
   - 预登记主比较，不跑完再挑最好的一组

6. **成本参考**
   本轮 3,444 次逻辑评测只花了 1,222 次唯一答题 + 996 次唯一判分（缓存命中率 64%/71%）。
   仅筛 127 道变更题的单变量实验约 10–15 分钟，可先做筛选再上全量。

---

## 6. 交付物与复现

根目录：`F:\codex数据库memory\locomo_temporal_annotation_full_20261023\`

```text
REPORT.md                 四组表 + 预定比较 + 机制与建议
manual_audit.md           粒度争议、金标冲突、同答案判定、两道单跳
experiment.json           四组定义、输入/代码 hash、模型参数、缓存协议、seed
frozen/                   题单、候选、V1/A0/三臂代码快照
offline/input_diff.json   861 题逐题 hash 与变更标记
offline/temporal_trace.jsonl  C3 结构化注释（span/原句/状态）
evaluate/rep1/            四组逐题答案、判定、请求 hash、缓存统计
tools/ run_annotation_full.py（prepare/offline/evaluate/report）+ 三个模式模块
tests/ 15 个 C3 边界用例
```

复现命令：

```powershell
$py = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$runRoot = 'F:\codex数据库memory\locomo_temporal_annotation_full_20261023'
$env:PYTHONUTF8 = '1'; $env:NO_PROXY = '127.0.0.1,localhost,::1'
& $py -B "$runRoot\run_annotation_full.py" --mode prepare  --output $runRoot
& $py -B "$runRoot\run_annotation_full.py" --mode offline  --config "$runRoot\experiment.json"
& $py -B "$runRoot\run_annotation_full.py" --mode evaluate --config "$runRoot\experiment.json" --replicate 1 --concurrency 4
& $py -B "$runRoot\run_annotation_full.py" --mode report   --config "$runRoot\experiment.json" --replicate 1
```
