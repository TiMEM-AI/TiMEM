# R1.1 P1 共享选择器与五组审计

- 开发题：80；选择器：确定性规则（无模型调用）

| 组 | 证据改变 | 窗口总数 | 接受 | 替换 | 主要拒绝原因 |
|---|---:|---:|---:|---:|---|
| B | 0 | 1897 | 0 | 0 | -=0 |
| H_raw | 0 | 1897 | 0 | 0 | no_new_slot=38616 |
| H_fold | 0 | 1897 | 0 | 0 | no_new_slot=40382 |
| L_rel | 0 | 1897 | 0 | 0 | no_new_slot=29332 |
| H_rel | 0 | 1897 | 0 | 0 | no_new_slot=40382 |

## 审核抽样（前 40 组决策）

| qid | 组 | 决策 | 中心/原因 | 新增槽位 |
|---|---|---|---|---|
| conv-48#q0033 | H_raw | 拒绝 | no_new_slot | - |
| conv-48#q0033 | H_raw | 拒绝 | irrelevant | - |
| conv-48#q0033 | H_fold | 拒绝 | no_new_slot | - |
| conv-48#q0033 | H_fold | 拒绝 | irrelevant | - |
| conv-48#q0033 | L_rel | 拒绝 | no_new_slot | - |
| conv-48#q0033 | L_rel | 拒绝 | irrelevant | - |
| conv-48#q0033 | H_rel | 拒绝 | no_new_slot | - |
| conv-48#q0033 | H_rel | 拒绝 | irrelevant | - |
| conv-47#q0037 | H_raw | 拒绝 | no_new_slot | - |
| conv-47#q0037 | H_raw | 拒绝 | irrelevant | - |
| conv-47#q0037 | H_fold | 拒绝 | no_new_slot | - |
| conv-47#q0037 | H_fold | 拒绝 | irrelevant | - |
| conv-47#q0037 | L_rel | 拒绝 | no_new_slot | - |
| conv-47#q0037 | L_rel | 拒绝 | irrelevant | - |
| conv-47#q0037 | H_rel | 拒绝 | no_new_slot | - |
| conv-47#q0037 | H_rel | 拒绝 | irrelevant | - |
| conv-42#q0033 | H_raw | 拒绝 | no_new_slot | - |
| conv-42#q0033 | H_raw | 拒绝 | no_replaceable_tail | - |
| conv-42#q0033 | H_fold | 拒绝 | no_new_slot | - |
| conv-42#q0033 | H_fold | 拒绝 | no_replaceable_tail | - |
| conv-42#q0033 | L_rel | 拒绝 | no_new_slot | - |
| conv-42#q0033 | L_rel | 拒绝 | no_replaceable_tail | - |
| conv-42#q0033 | H_rel | 拒绝 | no_new_slot | - |
| conv-42#q0033 | H_rel | 拒绝 | no_replaceable_tail | - |
| conv-42#q0017 | H_raw | 拒绝 | no_new_slot | - |
| conv-42#q0017 | H_raw | 拒绝 | no_replaceable_tail | - |
| conv-42#q0017 | H_raw | 拒绝 | irrelevant | - |
| conv-42#q0017 | H_fold | 拒绝 | no_new_slot | - |
| conv-42#q0017 | H_fold | 拒绝 | no_replaceable_tail | - |
| conv-42#q0017 | H_fold | 拒绝 | irrelevant | - |
| conv-42#q0017 | L_rel | 拒绝 | no_new_slot | - |
| conv-42#q0017 | L_rel | 拒绝 | no_replaceable_tail | - |
| conv-42#q0017 | L_rel | 拒绝 | irrelevant | - |
| conv-42#q0017 | H_rel | 拒绝 | no_new_slot | - |
| conv-42#q0017 | H_rel | 拒绝 | no_replaceable_tail | - |
| conv-42#q0017 | H_rel | 拒绝 | irrelevant | - |
| conv-48#q0049 | H_raw | 拒绝 | no_new_slot | - |
| conv-48#q0049 | H_raw | 拒绝 | irrelevant | - |
| conv-48#q0049 | H_fold | 拒绝 | no_new_slot | - |
| conv-48#q0049 | H_fold | 拒绝 | irrelevant | - |