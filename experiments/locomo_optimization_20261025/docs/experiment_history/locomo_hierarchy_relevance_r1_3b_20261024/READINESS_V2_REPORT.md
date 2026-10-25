# R1.3b 阻塞修复与全量就绪评估（v2）

日期：2026-10-24
目录：`F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024`
依据：`FULL_TEST_READINESS_CODEX_20261024.md` 第 69–83 节

---

## 1. 已修复的最后一个阻塞

### 1.1 高层候选入口（方案错误 3）

`build_pools()` 已改为：

- **低层**：使用既有 BM25 有序检索 `retriever.search(conv, query, "fact")`，不再按事实库迭代顺序截取；
- **H 的 seen**：只包含 B 已呈现事实 + H 真正选中的前 8 个低层窗口，**不再包含低层全库命中**；
- **高层**：折叠路由按 alias 相关度排序后展开目标，跳过已占槽，取至多 4 个；
- **回填**：H 不足 12 个时用后续低层补齐。

### 1.2 其余两项（前一轮已完成）

- 输入不再截断：完整 JSON 序列化；
- 多尾部删除：按标签一次构造保留列表，超额显式拒绝；
- 40 个任务显式产出证据（guard/失败逐字等于 B）。

---

## 2. 离线就绪数据（无模型调用）

### 2.1 20 题样本

| 项 | 值 |
|---|---:|
| 题数 | 20 |
| guard_skip | 11 |
| 有高层候选 | **20** |
| H 候选与 L 不同 | **20** |
| 高层候选总数 | 80（均 4/题） |

### 2.2 全量 861 题

| 项 | 值 |
|---|---:|
| guard_skip | 454 |
| 计划筛选题数 | **407** |
| 其中有高层候选 | **402** |
| H 候选与 L 不同 | **402** |

样例：

```text
conv-26#q0002  upper=4  H_only=4
conv-26#q0007  upper=4  H_only=4
conv-26#q0029  upper=4  H_only=4
```

---

## 3. 全量测试可行性

| 项 | 结论 |
|---|---|
| 工程阻塞 | **已清除**（裁切、多删、h_seen/排序） |
| 高层是否形成干预 | 402/407 计划题存在高层候选且与 L 不同 |
| 质检 | 契约测试通过、40 份证据完整 |
| 全量筛选调用 | 407 次 |
| 预计筛选耗时 | 约 8.5 小时（75 秒/次，串行） |
| 答题/判分 | 861 组 B/L/H，同证据共享后预计 900–1,200 次唯一调用 |

## 4. 当前状态

**全量 861 已具备启动条件，尚未启动。**
原因：单轮筛选约 8.5 小时，加上答题判分，整体是一次需要长时间运行与断点续跑的批次。

启动命令（断点续跑，重复执行会跳过已完成任务）：

```powershell
$py = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
# 1) 生成 861 题请求（离线）
& $py -B F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024\r13b_full_prepare.py
# 2) 筛选（可中断续跑）
& $py -B F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024\r13b_full_run.py
# 3) B/L/H 答题判分
& $py -B F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024\r13b_full_answer.py
```

（全量脚本尚未创建，需要按 20 题版本扩展为 861 题。）

## 5. 产物

```text
fix_hseen.py              h_seen 与 BM25 排序修复
r13b_prepare.py           已用修复版重新生成 20 题请求与 tasks
READINESS_V2.json         20 题与 861 题的离线就绪数据
```
