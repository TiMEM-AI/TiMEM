# 源码快照与迁移说明

本材料保留本轮实际运行源码。没有为打包而重写模型提示、检索排序、事实库或评测结果；源码 hash 可与原始文件核对。

## 最终有效入口

`source/experiments/locomo_hierarchy_relevance_full_20261025/` 内的 `full_prepare.py`、`full_run.py`、修正后 `full_answer.py` 为当前执行入口；`repair_cached_answers.py` 只处理已知两题，`finalize_results.py` 离线汇总，`test_scoring_cache.py` 为三项缓存回归。

最终检索还依赖 `grounded.py`、`upper_w0.py`、`r13b_prepare.py` 的 `build_pools()`、`r13b_fix.py` 的 `apply_plan_fixed()`、R1.2 `screen_client.py`、W0 arms/frozen 及 bench 模块。这些已随包放入，详见 `SOURCE_MAP.md`。

## 路径需要迁移

原实验根路径是 `F:/codex数据库memory`，原评测工程路径是 `F:/worktable/research/bench`。部分文件通过绝对路径和 `sys.path` 导入相邻轮次模块；原始层级 plan 也保存了原始 source SQLite 路径。

迁入仓库时应把实验根、数据根、bench 根、输出目录改成配置或 CLI 参数，并保持父子目录关系。两个 zip 解压后提供所需事实、索引、路由与题单，但不自动覆盖这些代码路径设置。

`build_hierarchy.py::space_config()` 等历史云端工具会读取外部 Space 配置文件。该外部文件含私密配置，未附入；最终本地检索和结果复核不需要调用此云端工具。不能在没有独立凭据与授权时执行上传、导入或账户操作。

## 配置与依赖

`source/bench/.env.example` 是上游占位示例；根目录 `MODEL_CONFIG.env.example` 只包含本实验模型名、接口和空白密钥字段。实际 `.env` 和认证值没有复制。生产 TiMEM 的其余核心依赖应使用上游仓库，`source/timem_aml/` 只提供与本项目相关的接口/模块参考。

源码保留历史实现，包含未采用的 C2/C3、W1、旧窗口/筛选器及旧缓存。它们用于解释实验演进，不能作为当前默认生产路径。等量交换仅约束窗口数，不保证事实条数和语义信息完全无损。

## 本包不做的动作

校验工具只读文件与压缩包，不启动 API、不调用模型、不写数据库、不推送 GitHub。重新跑生成/筛选/答题的原脚本会产生模型调用或账户操作，需要使用相应项目环境与配置；整理材料本身未执行这些动作。
