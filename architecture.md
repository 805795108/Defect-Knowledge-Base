# AI 缺陷知识库 — 系统架构设计文档

> **版本**: v2.5 | **日期**: 2026-04-10 | **状态**: 已落地运行

---

## 1. 概述

### 1.1 定位

AI 缺陷知识库（Defect Knowledge Base）是一套嵌入 AI 编程助手（Cursor / Claude Code）工作流的**经验治理系统**。它将团队在开发过程中遇到的 Bug、踩坑、失败尝试等隐性知识，结构化为可检索、可复用的 **Experience Card**，形成组织级缺陷经验资产。

### 1.2 解决的核心问题

| 问题 | 现状 | 知识库方案 |
|------|------|-----------|
| 踩坑经验散落 | 存在于个人记忆、聊天记录、零散文档中，无法被团队复用 | 统一结构化沉淀为 Experience Card |
| 同类问题反复踩 | 没有 Bug 修复后的经验回流机制，新人/AI 重复踏入已知陷阱 | 修复后自动触发沉淀，开发前主动检索 |
| Bug 诊断效率低 | 每次从零排查，不能利用历史根因分析 | 语义检索最相似历史经验，直接参考修复策略 |
| AI 辅助修复缺乏上下文 | AI 助手无项目历史踩坑记忆 | AI 修复 Bug 前自动查询知识库获取上下文 |

### 1.3 设计理念

架构设计借鉴 [MemGovern](https://github.com/QuantaAlpha/MemGovern) 论文提出的"经验治理"方法论——将非结构化的经验记忆通过 LLM 标准化、质量评估、向量索引，转化为可检索的结构化知识资产。核心理念包括：

- **治理优于堆积**：原始踩坑记录经 LLM 泛化处理后入库，去除项目特定细节，提取可迁移的抽象修复模式
- **置信度分级**：区分人工确认（confirmed）、AI 静态分析发现（likely）、AI 推测（hypothesis）三级置信度
- **质量门禁**：所有卡片经 6 维度自动评分，低于阈值**严格阻断入库**（exit code 2），支持 `--force` 强制覆写和 `--auto-retry` 自动改进

### 1.4 v2.0 核心变更

| 变更项 | v1.0 | v2.0 |
|--------|------|------|
| 质量门禁 | 仅打印评分、不拦截 | 严格阻断（exit 2），支持 --force / --auto-retry |
| 标准化路径 | 仅 `--input`（CLI 调 LLM） | 新增 `--json` 快速路径（Agent LLM 标准化 + CLI 校验写入） |
| CLI 输出 | 人类可读文本 | 所有命令支持 `--output-format json` 结构化输出 |
| Embedding | 仅 OpenAI API | 默认本地 sentence-transformers（零 Key），可选 `--embedding-provider openai` |
| 项目初始化 | 纯交互式 | 新增 `--template` 预设（mobile/web/backend/fullstack）+ `--install-skills` |
| 代码架构 | 单文件 cli.py | 拆分 llm.py（LLM 抽象）+ parser.py（JSON 解析）+ setup.py（pip 准备） |
| 质量持久化 | 无 | 每张卡片 metadata 含 QualityScore（6 维 + 均分 + issues） |
| 统计分析 | 无 | 新增 `stats` 命令，分析整体质量分布 |
| 事件追踪 | 无 | 新增 `events.jsonl` 记录每次 govern/search/index 操作 |
| 质量报告 | 无 | 新增 `report` 命令，从卡片 + 事件数据生成 Markdown 报告 |
| 多 LLM Provider | 仅 OpenAI（硬编码） | 支持 OpenAI/Claude/DeepSeek/Qwen/豆包 5 个 Provider，`providers` 字典配置 |

### 1.5 v2.1 核心变更

| 变更项 | v2.0 | v2.1 |
|--------|------|------|
| Issue Tracker | 仅 GitHub（`github_bug_label` 单字段） | 多平台 `issue_trackers` 列表：GitHub / 云效 Yunxiao / GitLab |
| source 枚举 | 5 个值 | 新增 `yunxiao-issue`、`gitlab-issue`、`git-history`、`code-comment`（共 9 个） |
| Metadata | `github_refs` 仅 GitHub | 新增 `issue_refs` 平台无关字段（`github_refs` 保留兼容） |
| 配置格式 | `data_sources.github_bug_label` | `data_sources.issue_trackers[]`，向后兼容旧字段 |
| 冷启动数据源 | 无 | 新增 Mode E（Git History Mining）和 Mode F（Code Comment Mining），`legacy` 项目模板 |

### 1.6 v2.2 核心变更

| 变更项 | v2.1 | v2.2 |
|--------|------|------|
| 检索流水线 | 纯语义向量检索（ChromaDB cosine Top-K） | 三级流水线：语义 → 可选混合检索（关键词+语义）→ 可选 Reranker 精排 |
| Reranker | 无 | `llm.py` 新增 `rerank()` 函数（cross-encoder），`search --rerank` 启用 |
| 混合检索 | 无 | `_keyword_search` 利用 `signals` 关键词匹配，`search --hybrid` 启用 |
| 增量同步 | Mode C 全量拉取 | 新增 `sync-state.json` 记录 `last_imported`，API 请求带 `since`/`updated_after` |
| 配置扩展 | 无 `search` 段 | `defect-kb.yaml` 新增 `search` 段：`hybrid`、`rerank`、权重、模型配置 |
| 事件追踪扩展 | 仅 govern/search/index 3 类事件 | 新增 `search_outcome`、`fix_session`、`confidence_change` 3 类价值度量事件 + `log-event` CLI 命令 |
| 报告增强 | 7 维度 Markdown 报告 | 新增 Section 8（知识库价值）和 Section 9（ROI 摘要），支持 `--format html` 生成 Chart.js 可视化 Dashboard |

### 1.7 v2.3 核心变更（Auto-RAG）

| 变更项 | v2.2 | v2.3 |
|--------|------|------|
| 检索触发方式 | 手动触发（用户需说"查缺陷库"） | 自动注入：Agent 在编码任务中自动检索并注入相关警告（零用户操作） |
| 输出格式 | `text` / `json` | 新增 `compact` 格式：`[ID\|严重度] 问题摘要 → 修复策略`，专为上下文注入设计（~100 tokens/条） |
| 相似度过滤 | 无（返回 Top-K 全部结果） | 新增 `--min-similarity` 参数，过滤低相关度结果 |
| 规则触发 | `defect-kb.mdc` `alwaysApply: false`，仅建议性提示 | `alwaysApply: true`，Agent 自动检索 + 条件注入 |
| 配置扩展 | 无 `auto_injection` 段 | `defect-kb.yaml` 新增 `auto_injection` 段：启用开关、阈值、注入量、任务类型过滤 |
| 缺陷分类 | 无分类维度 | `metadata.defect_category`：7 类（ai-hallucination / ai-antipattern / ai-security / ai-edge-case / framework-pitfall / framework-deprecation / team-pattern） |
| 框架信息 | 无 | `metadata.framework_info`：记录关联的框架名、版本约束、废弃 API |
| 种子内容 | 无预置知识 | 6 张 AI 反模式种子卡片（幻觉 API、过度工程化、SQL 注入、时区遗漏、竞态条件、废弃 API） |

### 1.8 v2.4 核心变更（Universal Content Scanner）

| 变更项 | v2.3 | v2.4 |
|--------|------|------|
| 被动数据源扫描 | Mode A（单文件 pitfalls）+ Mode B（单 glob feature context），固定 2 个路径 | Mode AB：`content_sources` 配置数组提供菜单，用户交互选择后处理（不再自动全量扫描） |
| 提取模式 | Mode A 固定按 `###` 分割，Mode B 固定关键词搜索 | `split_by_heading`（可配标题级别）+ `heading_keyword`（可配标题关键词） |
| 数据源选择 | 无选择，固定扫描 | 用户通过 AskQuestion 交互选择 content_source 或手动指定文件路径 |
| 自动发现 | `init` 只检查 2 个候选路径 | `init` 扫描 10 个候选 glob 路径模式，自动发现并生成 `content_sources` 预览 |
| 向后兼容 | 仅 `pitfalls_file` + `feature_context_glob` | `content_sources` 存在时优先；不存在时从旧字段自动构建 |
| 覆盖范围 | 仅单一 pitfalls 文件 + 单一 feature context glob | 可覆盖 evolution、architecture、features、playbooks、design docs、fix plans、system-opt、testing 等全项目目录 |
| 写入确认 | 无 | 所有被动数据源（Mode AB/C/E/F）写入前需经用户人工确认 |

### 1.9 v2.5 核心变更（Static Analysis D0）

| 变更项 | v2.4 | v2.5 |
|--------|------|------|
| Mode D 主动发现 | 仅 AI 方法（D1 Code Review / D2 Business Rule Audit / D3 Brainstorm） | 新增 D0 静态工具分析（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint），AI 方法降为兜底 |
| 静态工具集成 | 无 | `static_analysis` 配置段：`auto_detect` + 7 种工具注册表 + 6 种报告解析器 + 规则聚合 |
| 报告解析 | 无 | 支持 `pmd-xml`、`checkstyle-xml`、`spotbugs-xml`、`eslint-json`、`swiftlint-json`、`ktlint-text` 6 种格式 |
| AI 兜底策略 | `methods` 列表直接执行 | `methods.ai_fallback`：`when_d0_insufficient` / `always` / `never` 三种模式 |
| 规则增强 | 无 | `rule_context` 配置段：将 `.mdc` 编码规则注入 D1 Code Review prompt |
| source 枚举 | 9 个值 | 新增 `static-analysis`（共 10 个） |
| discovery_method | 3 个值 | 新增 `pmd` / `checkstyle` / `spotbugs` / `swiftlint` / `eslint` / `ktlint`（共 9 个） |
| init 自动发现 | 检测内容源（10 种 glob 模式） | 额外检测项目中已配置的静态分析工具 + 平台级规则文件 |

---

## 2. 系统架构

### 2.1 架构总览

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          AI 编程助手 (Cursor / Claude Code)                  │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────────┐   │
│  │  write-defect-   │  │  search-defect-  │  │  govern-defect-data       │   │
│  │  card (写入)     │  │  kb (检索)       │  │  (批量治理+主动发现)       │   │
│  │                  │  │                  │  │                           │   │
│  │  手动/自动触发    │  │  开发前/诊断时    │  │  Mode AB: Content Sources │   │
│  │  ↓               │  │  ↓               │  │   (用户交互选择)          │   │
│  │  提取→标准化      │  │  构造Query→检索   │  │  Mode C: Issue Trackers   │   │
│  │  →确认→入库       │  │  →展示→应用       │  │  Mode E: Git History      │   │
│  │                  │  │                  │  │  Mode F: Code Comments    │   │
│  │                  │  │                  │  │  → 人工确认 → 入库        │   │
│  │                  │  │                  │  │  Mode D0: 静态工具分析    │   │
│  │                  │  │                  │  │  Mode D1-3: AI 兜底扫描   │   │
│  └───────┬──────────┘  └────────┬─────────┘  └────────────┬──────────────┘   │
│          │                      │                          │                  │
│          │        ┌─────────────┼──────────────────────────┘                  │
│          │        │             │                                             │
│  ┌───────▼────────▼─────────────▼──────────────────────────────────────────┐  │
│  │                    CLI 工具层 (defect-kb/)                               │  │
│  │                                                                          │  │
│  │   init    govern      index       search    browse  stats  report │  │
│  │   初始化  文本/JSON    Card→       语义检索   按ID    质量   Markdown│  │
│  │   (模板)  →Card       向量索引                查看   统计   质量报告 │  │
│  │          (质量门禁)   (OpenAI/                (含                  │  │
│  │                      本地)                   质量)                 │  │
│  └─────┬──────────┬──────────────┬──────────────┬──────────────────┘   │
│        │          │              │              │                       │
└────────┼──────────┼──────────────┼──────────────┼───────────────────────┘
         │          │              │              │
         ▼          ▼              ▼              ▼
┌────────────┐  ┌───────────┐  ┌──────────┐  ┌──────────────────────────────┐
│ defect-kb  │  │  LLM API  │  │ ChromaDB │  │  defect-kb-data/             │
│ .yaml      │  │  (llm.py) │  │ (向量DB) │  │                              │
│ 项目配置    │  │           │  │          │  │  cards.jsonl (卡片数据)        │
│            │  │ - 标准化   │  │ - Embed  │  │  events.jsonl (操作事件日志)   │
│            │  │ - 质量评估 │  │ - cosine │  │  reports/ (质量报告输出)       │
└────────────┘  └───────────┘  └──────────┘  └──────────────────────────────┘
```

### 2.2 分层设计

| 层 | 组成 | 职责 |
|----|------|------|
| **Skill 编排层** | 3 个 Skill Markdown 文件 | 定义 AI 助手的交互流程和触发逻辑；默认路径（Agent 自身 LLM 标准化 + 质量评估 + CLI 校验写入，零 Key）和高级路径（CLI 内部 LLM） |
| **CLI 工具层** | `cli.py` + `config.py` + `schema.py` + `llm.py` + `parser.py` | 提供 8 个命令（init/govern/index/search/browse/stats/report/log-event），处理数据读写、质量门禁、LLM 调用、事件追踪、报告生成 |
| **Prompt 工程层** | `prompts/` 目录下 6 个模板 | 控制 LLM 的标准化、质量评估、静态分析泛化和主动发现行为 |
| **存储层** | `cards.jsonl` + `events.jsonl` + ChromaDB | 结构化数据持久化 + 操作事件日志 + 向量语义索引 |
| **配置层** | `defect-kb.yaml` | 项目级配置：平台、模块、数据源、LLM、集成 |

---

## 3. 核心数据模型：Experience Card

Experience Card 是知识库的原子单位，采用三层结构设计：

### 3.1 Index Layer（检索层）

| 字段 | 类型 | 说明 |
|------|------|------|
| `problem_summary` | string | 泛化的问题描述，去除项目特定名称（如变量名、仓库名），用于人类快速理解 |
| `signals` | string[] (5-12) | 高信号关键词，覆盖 4 个维度：错误类型、症状、触发条件、受影响组件。用于 Embedding 语义检索 |

### 3.2 Resolution Layer（解决层）

| 字段 | 类型 | 说明 |
|------|------|------|
| `root_cause` | string | 真正的根因（非表面症状） |
| `fix_strategy` | string | 可迁移的抽象修复方法，与具体代码路径解耦 |
| `patch_digest` | string | 关键代码变更摘要 |
| `verification_plan` | string | 可执行的验证步骤 |
| `abandoned_approaches` | string[] | 尝试过但失败的方案 + 失败原因 |

### 3.3 Metadata（元数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 格式 `DEF-YYYYMMDD-NNN`（如 DEF-20260318-001），CLI 自动生成 |
| `date` | string | 创建日期 |
| `project` | string | 所属项目名 |
| `platform` | string | ios / android / backend / web / harmony 等 |
| `module` | string | 模块编号（如 M014-playback-control） |
| `source` | enum | 数据来源：`pitfalls` / `github-issue` / `yunxiao-issue` / `gitlab-issue` / `git-history` / `code-comment` / `agent-transcript` / `manual` / `ai-proactive` / `static-analysis` |
| `severity` | enum | P0（核心阻断）/ P1（重要降级）/ P2（边缘场景） |
| `confidence` | enum? | `confirmed` / `likely` / `hypothesis`（可选，AI 发现时填充） |
| `discovery_method` | enum? | `code-review` / `business-rule-audit` / `brainstorm-edge-case` / `pmd` / `checkstyle` / `spotbugs` / `swiftlint` / `eslint` / `ktlint`（可选） |
| `defect_category` | enum? | 缺陷分类（v2.3）：`ai-hallucination` / `ai-antipattern` / `ai-security` / `ai-edge-case` / `framework-pitfall` / `framework-deprecation` / `team-pattern` |
| `framework_info` | dict? | 关联框架信息（v2.3）：`{name, version_constraint, deprecated_api}` |
| `related_files` | string[] | 关联代码文件路径 |
| `github_refs` | string[] | 关联 GitHub Issue/PR 链接（保留向后兼容） |
| `issue_refs` | string[] | 平台无关的 Issue/工作项引用（URL 或 ID），适用于所有 Issue Tracker 平台 |
| `quality` | QualityScore? | 质量门禁评分（可选，见下文） |
| `usage_count` | int (≥0) | 命中 search top-K 的累计次数，CLI 自动维护（默认 0，不参与人工填写） |
| `last_hit_at` | string? | 最近一次 search 命中的 ISO 时间戳，CLI 自动维护 |
| `seed` | bool | `init --import-seeds` 导入的内置示例卡片（默认 false）。stats Dashboard 默认排除，避免示例稀释项目质量信号；search 仍参与，让冷仓库立即可用 |
| `quick` | bool | 通过 `cli.py quick` 5 秒落卡的占位卡（默认 false），跳过质量门禁；`cli.py upgrade` 成功后翻为 false |
| `upgraded_at` | string? | quick 卡被 `cli.py upgrade` 升级为完整卡时的 ISO 时间戳（搭配 `quick=false` 使用，留下"曾经是 quick 起家"的痕迹） |

### 3.4 QualityScore（质量评分子模型）

每张卡片入库时经过 LLM 6 维度质量评分，结果持久化在 `metadata.quality` 中。

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_clarity` | float (1-5) | 关键词覆盖度 |
| `root_cause_depth` | float (1-5) | 根因可信度 |
| `fix_portability` | float (1-5) | 修复策略可迁移性 |
| `patch_digest_quality` | float (1-5) | 补丁摘要完整性 |
| `verification_plan` | float (1-5) | 验证步骤可执行性 |
| `infosec` | float (1-5) | 信息安全（无泄露密钥/凭据/内部 URL） |
| `average` | float (1-5) | 加权平均分 |
| `passed` | bool | 是否通过门禁（任一维度 < 3 或均分 < 3.5 则为 false） |
| `quality_override` | bool | 用户通过 `--force` 强制写入的标记 |
| `issues` | string[] | 具体改进建议（门禁未通过的维度附带） |

### 3.5 设计考量

| 设计决策 | 理由 |
|---------|------|
| problem_summary 要求泛化 | 去除 `self.viewModel.xxx` 等项目细节后，同一模式可匹配不同项目的类似问题 |
| signals 限制 5~12 个 | 太少则检索召回不足，太多则引入噪声降低精度 |
| fix_strategy 与代码解耦 | 使修复策略可跨项目、跨语言迁移（如"将实例提升为外部持有的状态"适用于所有 UI 框架） |
| abandoned_approaches 记录失败 | 避免后来者重复尝试已证伪的方案，是最高价值的隐性知识 |
| 三级置信度 | 区分经验来源可靠性，让使用者对 AI 推测保持审慎 |
| QualityScore 持久化 | 支持后续统计分析（`stats` 命令）、质量趋势监控和卡片治理 |

### 3.6 数据校验

所有 Card 入库前经过双重校验：

**Pydantic 模型校验**：
- `id` 必须匹配正则 `^DEF-\d{8}-\d{3}$`
- `signals` 长度必须在 5~12 之间
- `source` 必须为 10 个枚举值之一（含 `yunxiao-issue`、`gitlab-issue`、`git-history`、`code-comment`、`static-analysis`）
- `severity` 必须为 P0/P1/P2
- `QualityScore` 各维度必须在 1~5 之间

**LLM 质量门禁**：
- 6 维度评分均分 >= 3.5
- 任一维度 < 3 则整体不通过
- 不通过时 exit code 2，不写入（除非 `--force`）
- 支持 `--auto-retry`（最多 2 轮自动改进重试）

---

## 4. 数据流水线

### 4.1 写入流水线（Govern Pipeline）

支持两条输入路径 × 两种质量评估方式：

- **默认路径（零 API Key）**：Agent 自身 LLM 标准化 + 质量评估 → `govern --json --quality-json`，无需任何外部 API Key
- **高级：半快速路径**：Agent 标准化（`--json`）+ CLI 内部 LLM 质量评估（需 API Key）
- **高级：完整路径**：`govern --input` → CLI 内部 LLM 标准化 + 质量评估（需 API Key）

```
原始文本 ──▶ LLM 标准化 ──┐
  (高级 --input)          │
                          ▼
预标准化 JSON ──────────▶ 质量评估 ──▶ 门禁判定 ──▶ Pydantic 校验 ──▶ 写入
  (默认 --json)            │  │          │                          cards.jsonl
                           │  │     ┌────┴────┐                    (含 QualityScore)
                  ┌────────┘  │     │         │
                  │           │ PASS       FAIL ──▶ exit code 2
      默认: Agent 预评估  高级: CLI 调 LLM │              │
        (--quality-json)   (需 Key)      │    ┌─────────┴─────────┐
          零 API Key                     │    │                    │
                                         │  --auto-retry        --force
                                         │  (高级,改进重试)     (quality_override=true)
                                         │    │                    │
                                         ▼    ▼                    ▼
                                            写入 JSONL
```

**质量门禁行为**：

| 场景 | 行为 | exit code |
|------|------|-----------|
| 评分通过 | 写入 cards.jsonl（含 quality） | 0 |
| 评分未通过（默认） | 打印失败报告，**不写入** | 2 |
| 评分未通过 + `--force` | 写入，标记 `quality_override=true` | 0 |
| 评分未通过 + `--auto-retry` | LLM 自动改进卡片并重新评分（最多 2 轮），全部失败则不写入 | 0 或 2 |
| `--quality-json` | Agent 预评估的质量分数，CLI 只做门禁判定（不调外部 LLM） | 0 或 2 |
| `--skip-quality` | 跳过质量检查，直接写入（quality 字段为 null） | 0 |

**质量评估 6 维度**：

| 维度 | 评分标准（1-5 分） |
|------|-------------------|
| Signal Clarity | 关键词是否覆盖错误类型/症状/触发条件/组件 4 个维度 |
| Root Cause Credibility | 是否指向真正根因而非表面症状 |
| Fix Strategy Portability | 修复策略是否与具体代码解耦、可复用 |
| Patch Digest Completeness | 关键代码变更是否描述清晰 |
| Verification Plan Actionability | 验证步骤是否可执行 |
| Information Security | 是否泄露密钥/凭据/内部 URL |

**路径可观测性（`pipeline_path`）**：

每次 `govern` 执行时，CLI 根据传入参数组合自动判定所走路径，并写入 JSON 输出、text 日志和 `events.jsonl`：

| `pipeline_path` 值 | 参数组合 | LLM 使用 | 需要 API Key |
|---|---|---|---|
| `agent-preeval` | `--json` + `--quality-json` | Agent 完成标准化 + 质量评估，CLI 零 LLM | 否 |
| `agent-std-cli-quality` | `--json`（无 `--quality-json`） | Agent 标准化，CLI 调 LLM 质量评估 | 是 |
| `cli-full` | `--input` | CLI 调 LLM 标准化 + 质量评估 | 是 |

路径由**传入的参数组合**决定，不由 API Key 的有无决定。默认路径始终为 `agent-preeval`（零 Key）。

**写入后副作用**：

`govern` 在 Pydantic 校验通过、`cards.jsonl` 追加成功后，会同步重生 `defect-kb-data/INDEX.md`（人类可读的卡片目录）。该步骤纯本地、无网络依赖，失败也不会回滚卡片写入。手动重建可执行 `cli.py index --rebuild-md`。

### 4.2 索引流水线（Index Pipeline）

```
cards.jsonl                    ChromaDB
     │                              ▲
     ▼                              │
┌─────────────┐    ┌─────────────┐  │
│ 增量检测     │───▶│  Embedding  │──┘
│             │    │  (llm.py)   │
│ 跳过已索引   │    │             │
│ Card ID     │    │ OpenAI 或   │
│             │    │ 本地模型     │
└─────────────┘    └─────────────┘
```

- 向量化文本 = `problem_summary` + 所有 `signals` 拼接
- Embedding 提供者通过 `llm.py` 抽象层选择：
  - `local`（默认）：sentence-transformers，384 维，零 API 依赖
  - `openai`（高级，`--embedding-provider openai`）：OpenAI `text-embedding-3-small`，1536 维，需 API Key
- ChromaDB 使用余弦相似度（cosine）索引
- 元数据过滤支持 platform / module / source / severity / confidence
- `index` 命令会自动将实际使用的 `embedding_provider` 回写到 `defect-kb.yaml`，后续 `search` 自动读取，无需重复指定

**Local Embedding 语言限制**：

| 模型 | 语言 | 适用场景 |
|------|------|---------|
| `all-MiniLM-L6-v2`（默认） | 仅英文 | 英文卡片/英文查询 |
| `paraphrase-multilingual-MiniLM-L12-v2` | 中英双语 | 推荐：中文项目 |
| `BAAI/bge-small-zh-v1.5` | 中文专用 | 纯中文场景，体积小 |

在 `defect-kb.yaml` 的 `llm.providers.local.embedding_model` 中可配置模型名。建议卡片的 `problem_summary` 和 `signals` 使用英文编写以获得最佳跨语言检索效果。

### 4.2 快速通道：quick + upgrade

`govern` 是高质量但重的入口（要求 LLM 标准化 + 6 维质量门禁）。在小型项目里，开发者经常想"现在记一笔免得忘了，回头再补"——`govern` 的门禁会让这个动机直接消失。`quick` / `upgrade` 提供一条折中通道：

```
开发中突然想到一个坑
    ↓
cli.py quick "<一句话>"
    ↓
{LLM 可用}? → standardize_quick.txt → 最小合法卡
{LLM 不可用 / --no-llm} → _local_quick_card：summary=原文首行，signals 从 token 抽取，resolution_layer=TODO 占位
    ↓
schema 验证（signals ≥ 5）→ append cards.jsonl
    ↓
metadata.quick=true, severity=P2(默认), confidence=hypothesis
    ↓
重生 INDEX.md（quick 卡进 "✏️ Quick notes" 区，与正式卡分开展示）
log_event(action="quick")
```

后续升级：

```
cli.py upgrade --id DEF-XXXXXXXX-NNN  [--input "新原文"] [--json '{...}'] [--force] [--skip-quality]
    ↓
读 cards.jsonl 找到目标卡
    ↓
{有 --json} → 直接用 Agent 预填的 JSON
{有 --input} → standardize.txt 喂 LLM
{都没有} → 把现有卡内容拼成 raw_text 喂给 LLM 再标准化一遍
    ↓
质量门禁（除非 --skip-quality）：未过且无 --force → 拒绝
    ↓
preserved metadata：保留 id / date / project / usage_count / last_hit_at / 卡片来源
覆写：severity / defect_category / framework_info / related_files 等可由 LLM 改进
flip：quick=false, upgraded_at=now
    ↓
原子重写整个 cards.jsonl（_rewrite_cards_jsonl 用 .tmp + os.replace）
    ↓
重生 INDEX.md（卡现在归入 "Own cards by platform"）
log_event(action="upgrade", ran_llm=..., quality_avg=...)
```

设计要点：
- **quick 不调质量门禁** —— 这是它的核心价值；用户接受"先有再好"的代价
- **LLM 不可用时降级为本地最小卡** —— `_local_quick_card` 保证 schema 永远 valid（signals 不足时填 `todo-N`）
- **upgrade 默认调 LLM** —— 因为目的是"把占位变完整"；但 `--json` 路径让没有 API Key 的环境也能升级
- **upgraded_at 不擦 quick 标记的来源** —— 已加但 `quick=false` 翻转，事后还能在 INDEX.md / events.jsonl 里看出"这张卡曾是 quick 起家"
- **原子重写** —— upgrade 涉及覆写已有卡，必须读全 + tmp 写 + replace，避免崩溃中途留半张卡

### 4.3 检索流水线（Search Pipeline）

v2.2 升级为三级检索流水线：语义召回 → 可选混合检索 → 可选 Reranker 精排。

```
用户查询 / 报错信息 / 任务描述
          │
          ▼
    ┌───────────┐
    │  Embedding│
    │  (llm.py) │
    └─────┬─────┘
          │ query_embedding
          ▼
    ┌───────────────────────────────────────────────────────┐
    │ 路径 A: ChromaDB 语义检索 → Top-N (similarity 分数)   │
    │                                                       │
    │ 路径 B [--hybrid]: cards.jsonl signals 关键词匹配      │
    │         → Top-M (keyword_score)                       │
    └─────────────────────┬─────────────────────────────────┘
                          │
                          ▼
    ┌───────────────────────────────────────────────────────┐
    │ 合并去重 (by card_id) → 综合分排序                     │
    │ final_score = semantic_weight × similarity            │
    │             + keyword_weight × keyword_score          │
    │ (默认 0.7 / 0.3)                                      │
    └─────────────────────┬─────────────────────────────────┘
                          │
                          ▼
    ┌───────────────────────────────────────────────────────┐
    │ 热度加权 (usage_boost)                                 │
    │ final_score *= 1 + log1p(usage_count) × 0.1           │
    │ 0→×1.00, 3→×1.14, 10→×1.24, 100→×1.46 (饱和)          │
    └─────────────────────┬─────────────────────────────────┘
                          │
                          ▼
    ┌───────────────────────────────────────────────────────┐
    │ [--rerank] Cross-Encoder 精排 (llm.py rerank())      │
    │ 模型: cross-encoder/ms-marco-MiniLM-L-6-v2           │
    │ Top-N → Top-K (默认 5)                                │
    └─────────────────────┬─────────────────────────────────┘
                          │
                          ▼
    ┌───────────────────────┐         ┌──────────────────────┐
    │ Top-K 结果             │ ──────▶ │ 反馈环               │
    │                       │  hit    │ 1. cards.jsonl 中    │
    │ [confirmed]  可直接参考 │         │    usage_count += 1  │
    │ [likely]     AI 发现   │         │    last_hit_at = now │
    │ [hypothesis] 需验证    │         │ 2. events.jsonl 追加 │
    └───────────────────────┘         │    search_hit 事件   │
                                       └──────────────────────┘
```

**反馈环设计要点**：

- 仅当 `top_similarity > hit_threshold` 时触发（local: 0.3，openai: 0.7），低质量召回不计 hit
- 写回采用 `cards.jsonl.tmp + os.replace` 原子操作，崩溃不会半写
- `usage_count` 取自 `cards.jsonl`（最新值），不依赖 ChromaDB 中的快照（避免索引漂移）
- `--no-record` flag 关闭副作用，用于盘点 / 测试 / 自动化探查
- `usage_boost` 用 `log1p` 饱和曲线，避免热卡片永久压制新卡片

**检索模式**：

| 模式 | CLI 参数 | YAML 配置 | 说明 |
|------|---------|-----------|------|
| 纯语义（默认） | (无) | (无) | ChromaDB cosine Top-K，向后兼容 |
| 混合检索 | `--hybrid` | `search.hybrid: true` | 语义 + 关键词双路召回，综合分排序 |
| Reranker | `--rerank` | `search.rerank: true` | Cross-Encoder 二次精排 |
| 混合 + Reranker | `--hybrid --rerank` | 两者均 true | 最高精度，延迟稍高 |

**输出格式**（v2.3 新增 compact）：

| 格式 | CLI 参数 | 用途 |
|------|---------|------|
| `text`（默认） | `--output-format text` | 人类可读的详细展示 |
| `json` | `--output-format json` | Skill 层消费和自动化集成 |
| `compact` | `--output-format compact` | 自动注入用：`[ID\|严重度] 摘要 → 策略`，~100 tokens/条 |

**相似度过滤**（v2.3 新增）：`--min-similarity 0.3` 过滤低于阈值的结果，避免注入不相关警告。

**YAML 配置**：

```yaml
search:
  hybrid: false                                     # 开启混合检索
  semantic_weight: 0.7                               # 语义检索权重
  keyword_weight: 0.3                                # 关键词匹配权重
  rerank: false                                      # 开启 Reranker
  rerank_model: cross-encoder/ms-marco-MiniLM-L-6-v2 # Cross-Encoder 模型
```

- 置信度字段为空时显示为 `[unknown]`（v2.0 修复，不再默认为 confirmed）

### 4.4 冷启动种子卡片（Seed Bootstrap）

新仓库装完 KB 后会面临一个尴尬时刻：`cards.jsonl` 是空的，`search` 永远返 0，开发者会觉得"这东西没用"于是放弃。种子卡片解决这个问题：

```
cli.py init [--import-seeds [PLATFORMS|all]] [--skip-seeds]
    ↓
_resolve_seed_filter(args)
    ↓
{None}  → 跳过（默认行为，向后兼容）
{[]}    → 自动从 config.project.platforms 推断
{["all"]} → 全部 15 张
{["ios","backend",...]} → 显式平台 + "common"（始终包含）
    ↓
_finalize_init 末尾调 _import_seeds:
  - 读 defect-kb/seeds/built-in.jsonl
  - 按 platform 过滤
  - 重新分配项目本地 ID（DEF-{today}-NNN，接续现有 cards.jsonl 序号）
  - 改 date=今天，project=本项目
  - 标记 metadata.seed=True
  - schema 校验失败的种子直接跳过
  - append 到 cards.jsonl
    ↓
重生 INDEX.md（种子卡进 "🌱 Seed cards" 区）
```

设计要点：
- **种子源文件结构上就是普通 Experience Card** —— `built-in.jsonl` 每行都是 schema-valid 的卡，便于演化（编辑就是直接改 JSON）
- **导入时重新分配 ID** —— 避免不同项目导入同样的种子撞 ID
- **seed=true 的双面性** ——
  - `search` 仍然命中种子（让冷仓库立即可用）
  - `stats` 默认排除（避免示例稀释项目质量信号；可 `--include-seeds` 看全量）
  - `INDEX.md` 单独分区展示（与项目卡分隔，让 review 时一眼看出"这是示例"）
- **"common" 始终包含** —— `common` 平台收录跨语言/跨栈的 AI 编程通病（幻觉 API / 时区 / catch-all），任何项目都受用
- **不强制启用** —— 不传 flag 时静默跳过，保持向后兼容（已有项目升级 CLI 不会突然多卡）

种子卡片的"AI 编程通病"主题（每张都在 `_local_quick_card` 之上的高质量级别）：

| Platform | 卡片主题 |
|----------|---------|
| `common` | AI 幻觉 API / 时区混用 / catch-all 吞异常 |
| `ios` | SwiftUI @State 跨实例泄漏 / UIKit 主线程 / deeplink 双重解码 |
| `android` | Handler Activity 泄漏 / RecyclerView 复用脏状态 / 异步回调遇 destroy |
| `web` | useEffect 闭包陷阱 / 表单 preventDefault 缺失 / CORS+CSP 配置 |
| `backend` | N+1 查询 / 并发竞态丢更新 / 缓存击穿 |

种子卡片的演化策略：
- 通过实际项目的 stats 反馈（哪些 seed 经常被命中 / 哪些从来不命中）来 prune 或新增
- 维护时直接编辑 `defect-kb/seeds/built-in.jsonl`，CLI 升级后老仓库不会自动更新已导入的种子（避免覆盖用户改动）

---

## 5. 知识入库判定

本节定义"什么样的经验值得入库"和"如何阻止低质量知识进入"——这是知识库价值密度的根本保障。低质量或冗余的卡片不仅浪费存储，更会在自动注入时产生噪声，稀释真正有用的警告。

### 5.1 核心原则：治理优于堆积

知识库不是 Bug Tracker 的镜像，不是所有 Bug 都值得入库。一张 Experience Card 的价值在于**可迁移性**——它能否帮助未来遇到类似问题的人（或 AI）更快定位和解决问题。

```
原始经验                  入库标准                    知识库卡片
                                                    
"登录页 crash"      ──▶  有根因 + 有修复策略？  ──▶  "SwiftUI sheet closure
                          ↓                        inline-creates ViewModel
                          可迁移到其他项目？          causing state reset"
                          ↓
                          不含敏感信息？              抽象的、可检索的、
                          ↓                        可跨项目复用的经验
                          不与已有卡片重复？
```

### 5.2 入库信号：什么场景应该沉淀

以下信号表明一次经验值得入库。**满足任一**即建议沉淀：

| 信号 | 说明 | 典型场景 |
|------|------|---------|
| **排查代价高** | 经历 2+ 个失败假设才定位到根因 | 以为是网络问题，其实是 CodingKeys 命名不匹配 |
| **跨模块影响** | 修复涉及 2+ 个功能模块的改动 | 修改了播放页的状态管理，同时需要改首页 feed 的刷新逻辑 |
| **高严重度** | P0（核心流程阻断）或 P1（重要降级） | 登录流程无法完成、视频无法播放 |
| **隐性知识** | 发现性语句——"踩坑"、"原来是"、"没想到"、"居然是" | "原来是 @ObservedObject 不持有生命周期" |
| **高价值领域** | 涉及并发/缓存/安全/数据一致性 | 竞态条件导致计数不准确 |
| **AI 特征性错误** | AI 生成的代码存在系统性缺陷模式 | AI 总是在 SQL 查询中使用字符串拼接 |
| **框架陷阱** | 框架特定的 gotcha，文档中不显眼 | SwiftUI fullScreenCover 的 closure 每次都重建 |
| **团队反复踩** | 同类问题在团队中出现 2 次以上 | 每次 API 合约变更都忘记同步客户端 CodingKeys |

### 5.3 反入库信号：什么不该入库

以下情况**不应该**入库，或应该在质量门禁中被拦截：

| 反信号 | 说明 | 处理方式 |
|--------|------|---------|
| **表面症状无根因** | 只描述了"页面白屏"但不知道为什么 | 等定位到根因后再入库 |
| **项目强耦合** | 修复策略离不开具体代码路径（如"修改 line 42 的 if 条件"） | 标准化时要求泛化，质量门禁 fix_portability 维度会拦截 |
| **一次性配置错误** | 环境变量没设对、端口被占用等运维问题 | 不具备可迁移性 |
| **已有高度相似卡片** | ChromaDB 相似度 > 0.9 的卡片已存在 | Mode D 自动去重；手动入库时需确认不重复 |
| **敏感信息** | 包含 API Key、内部 URL、用户数据 | 质量门禁 infosec 维度会拦截 |
| **过于笼统** | "注意代码质量"、"记得写测试" | 无具体根因和修复策略，质量门禁 root_cause_depth 会拦截 |

### 5.4 入库判定决策树

```
经验/Bug 修复完成
  │
  ▼
┌──────────────────────────────┐
│ 沉淀必要性评估                │
│ （满足任一入库信号？）         │──否──▶ 不入库（静默跳过）
└──────────┬───────────────────┘
           │ 是
           ▼
┌──────────────────────────────┐
│ 信息完整性检查                │
│                              │
│ · 有明确的根因？              │──否──▶ 等定位根因后再入库
│ · 有可操作的修复策略？         │
│ · 有可执行的验证步骤？         │
└──────────┬───────────────────┘
           │ 是
           ▼
┌──────────────────────────────┐
│ 标准化（泛化处理）            │
│                              │
│ · problem_summary 去项目特定名│
│ · fix_strategy 与代码路径解耦  │
│ · signals 覆盖 4 维度         │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│ 质量门禁（6 维度 × 1-5 分）   │
│                              │
│ · 任一维度 < 3 → 不通过       │
│ · 均分 < 3.5 → 不通过         │──不通过──▶ 改进后重试
│                              │           或 --force 强制
│ 信号清晰度 ≥ 3               │
│ 根因深度   ≥ 3               │
│ 修复可迁移 ≥ 3               │
│ 补丁摘要   ≥ 3               │
│ 验证方案   ≥ 3               │
│ 信息安全   ≥ 3               │
│ 均分       ≥ 3.5             │
└──────────┬───────────────────┘
           │ 通过
           ▼
┌──────────────────────────────┐
│ 去重检查                      │
│                              │
│ ChromaDB 余弦相似度 > 0.9？   │──是──▶ 丢弃重复
└──────────┬───────────────────┘
           │ 否
           ▼
    写入 cards.jsonl + 索引

```

### 5.5 质量门禁 6 维度评分标准

每张卡片入库前必须通过 6 个维度的 LLM 自动评分。以下是每个维度的评分含义和典型分数段：

#### Signal Clarity（信号清晰度）

衡量 `signals` 关键词是否覆盖"错误类型 / 症状 / 触发条件 / 受影响组件"四个维度，以及关键词是否具有高区分度。

| 分数 | 含义 | 示例 |
|------|------|------|
| 5 | 四个维度全覆盖，关键词精准且无噪声 | `["fullScreenCover", "ObservableObject", "state-reset", "inline-init", "body-reevaluation", "SwiftUI", "closure-capture", "lifecycle"]` |
| 3 | 覆盖 2-3 个维度，部分关键词过于笼统 | `["error", "crash", "iOS", "view", "state"]`（缺少触发条件和具体组件） |
| 1 | 关键词与问题无关或缺失 | `["bug", "fix", "code"]` |

#### Root Cause Depth（根因深度）

衡量 `root_cause` 是否指向真正的技术原因，而非停留在表面症状。

| 分数 | 含义 | 示例 |
|------|------|------|
| 5 | 指向底层机制原因，解释了"为什么" | "fullScreenCover closure 在每次 parent body 求值时重新创建 ViewModel 实例，@ObservedObject 不持有生命周期导致状态丢失" |
| 3 | 指出了直接原因但未深入解释机制 | "ViewModel 被重新创建了" |
| 1 | 仅描述症状 | "输入框的内容消失了" |

#### Fix Strategy Portability（修复可迁移性）

衡量 `fix_strategy` 是否与具体代码路径解耦，能否跨项目、跨语言迁移。

| 分数 | 含义 | 示例 |
|------|------|------|
| 5 | 完全抽象，适用于同类技术栈的所有项目 | "将 ViewModel 创建移到 closure 外部，子视图用 @StateObject 持有" |
| 3 | 策略正确但带有项目特定细节 | "把 PhoneInputView 的 @ObservedObject 改成 @StateObject" |
| 1 | 纯代码级描述 | "把第 42 行的 ObservedObject 改成 StateObject" |

#### Patch Digest Quality（补丁摘要质量）

衡量 `patch_digest` 是否清晰描述了关键代码变更，既不过于详细（贴全量 diff）也不过于简略。

| 分数 | 含义 |
|------|------|
| 5 | 清晰描述了改动的本质（改了什么、从什么改到什么），不含全量 diff |
| 3 | 描述了改动但不够具体或过于冗长 |
| 1 | 缺失或仅写"修复了 bug" |

#### Verification Plan（验证方案可执行性）

衡量 `verification_plan` 是否包含具体、可操作的验证步骤，他人能否按步骤复现验证。

| 分数 | 含义 |
|------|------|
| 5 | 包含具体步骤（操作 → 预期结果），可直接执行 |
| 3 | 有验证方向但步骤不够具体 |
| 1 | 缺失或仅写"测试通过" |

#### Information Security（信息安全）

衡量卡片内容是否泄露敏感信息。**任何泄露都应得 1 分并阻断入库。**

| 分数 | 含义 |
|------|------|
| 5 | 无任何敏感信息 |
| 3 | 包含可能敏感的内部路径但无凭据 |
| 1 | 包含 API Key、密码、内部 URL、用户数据 |

### 5.6 缺陷分类体系（v2.3）

`defect_category` 字段将卡片按成因分为 7 类，支持分类过滤和统计分析：

| 分类 | 含义 | 典型入库内容 | 自动注入价值 |
|------|------|-------------|-------------|
| `ai-hallucination` | AI 生成不存在的 API/方法 | AI 建议调用 `UIView.setCornerRadius()` 但该方法不存在 | 极高：直接阻止编译错误 |
| `ai-antipattern` | AI 编码反模式 | AI 在 10 行能解决的问题上引入 3 层抽象 | 高：提升代码可维护性 |
| `ai-security` | AI 生成安全隐患 | AI 用字符串拼接 SQL，未参数化 | 极高：阻止安全漏洞 |
| `ai-edge-case` | AI 遗漏边界处理 | AI 忽略时区、并发、空值处理 | 高：减少生产环境故障 |
| `framework-pitfall` | 框架特定陷阱 | SwiftUI closure 重建 ViewModel | 高：避免重复踩坑 |
| `framework-deprecation` | 废弃 API 使用 | AI 使用旧版 API，新版已移除 | 中：避免升级时阻塞 |
| `team-pattern` | 团队反复出现的模式 | 每次合约变更都忘记同步 CodingKeys | 高：根治团队级问题 |

**分类在标准化阶段由 LLM 自动推断**（`standardize.txt` prompt 中包含分类指引），Agent 和 CLI 均不强制要求此字段（Optional），旧卡片自动兼容为 `null`。

### 5.7 不同数据源的入库判定差异

不同来源的经验进入知识库时，适用不同的置信度和门禁策略：

| 来源 | source 标记 | 默认 confidence | 质量门禁 | 去重检查 | 用户确认 |
|------|-----------|----------------|---------|---------|---------|
| 人工手写 / 对话提取 | `manual` / `agent-transcript` | `confirmed` | 标准 6 维度 | 否（用户自行判断） | 是（Step 3 写入前确认） |
| Pitfalls / Feature Context | `pitfalls` | `confirmed` | 标准 6 维度 | 否 | 是（写入前逐条/批量确认） |
| Issue Tracker | `github-issue` / `yunxiao-issue` / `gitlab-issue` | `confirmed` | 标准 6 维度 | 否 | 是（写入前逐条/批量确认） |
| Git History Mining | `git-history` | `likely` | 标准 6 维度 | 否 | 是（写入前逐条/批量确认） |
| Code Comment Mining | `code-comment` | `hypothesis` | 标准 6 维度 | 否 | 是（写入前逐条/批量确认） |
| 静态工具分析 | `static-analysis` | `likely` | 标准 + 可验证性 + 语义去重（> 0.9） | 是（自动） | 可配置（`require_user_confirm`） |
| AI 主动发现 | `ai-proactive` | `likely` / `hypothesis` | 标准 + 可验证性 + 语义去重（> 0.9） | 是（自动） | 可配置（`require_user_confirm`） |

**关键差异**：
- **所有数据源**均需用户确认后才写入，包括被动数据源（Mode AB/C/E/F）
- **静态工具分析**（Mode D0）和 **AI 主动发现**（Mode D1-3）有最严格的门禁——在标准 6 维度之上增加可验证性检查和语义去重
- D0 使用确定性工具发现，噪声率低于 AI 推测；D1-3 为 AI 推测，噪声率最高
- **人工手写/对话提取**有用户交互确认步骤，质量由人和门禁双重保障
- **被动数据源**批量处理时支持批量确认（>= 5 条时可一次性确认）或逐条审核

---

## 6. 数据采集

### 6.1 被动采集（Mode AB/C/E/F）

| 模式 | 数据源 | 采集方式 | source 标记 | confidence |
|------|--------|---------|------------|------------|
| **Mode AB** | Content Sources（用户交互选择） | `content_sources` 配置数组提供菜单，用户交互选择后处理。支持 `split_by_heading`（整文件分条）和 `heading_keyword`（按标题关键词提取段落）两种提取模式 | `pitfalls` | 按 source 配置 |
| **Mode C1** | GitHub Issues | REST API（优先）或 `gh issue list`，增量 `since` 参数，拼接 title+body | `github-issue` | `confirmed` |
| **Mode C2** | 云效 Yunxiao | REST API `listWorkitems?category=Bug` + 详情接口，增量 `gmtModifiedAfter`，拼接 subject+description | `yunxiao-issue` | `confirmed` |
| **Mode C3** | GitLab Issues | REST API `/api/v4/projects/:id/issues`，增量 `updated_after`，拼接 title+description | `gitlab-issue` | `confirmed` |
| **Mode E** | Git History | 双层过滤（分支名模式 + `git log --grep`）筛选 bug-fix commit，提取 message+diff | `git-history` | `likely` |
| **Mode F** | Code Comments | `rg` 扫描 TODO/FIXME/HACK/WORKAROUND/XXX 注释 + 上下文 | `code-comment` | `hypothesis` |

#### Mode AB: 内容源交互式选择（v2.4 → v2.6）

v2.4 将原 Mode A 和 Mode B 合并为统一的 **Mode AB 内容源处理器**。`data_sources.content_sources` 配置数组作为交互菜单来源，用户通过 AskQuestion 选择要处理的 content_source 或手动指定文件路径，不再自动全量扫描。每个 source 指定 glob 路径、提取模式和段落匹配规则。

**两种提取模式**：

| 模式 | 用途 | 分割策略 |
|------|------|---------|
| `split_by_heading` | 整文件都是踩坑记录（如 `pitfalls.md`、`FIX_PLAN*.md`） | 按 `heading_levels` 指定的标题级别分割为独立条目 |
| `heading_keyword` | 大文档中只有部分段落包含踩坑内容 | 搜索标题匹配 `heading_patterns` 的段落，提取到下一个同级标题 |

**配置字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 内容源名称（用于统计报告） |
| `globs` | string[] | glob 路径列表，支持 `**` 递归匹配 |
| `exclude_globs` | string[]? | 排除的 glob 路径（避免跨 source 重复） |
| `extract_mode` | enum | `split_by_heading` 或 `heading_keyword` |
| `heading_levels` | int[]? | `split_by_heading` 时指定分割的标题级别（如 `[3, 4]` 表示 `###`/`####`） |
| `heading_patterns` | string[]? | `heading_keyword` 时搜索的标题关键词（如 `["踩坑", "教训"]`） |
| `source_tag` | string | 写入 card 的 `source` 字段（默认 `pitfalls`） |
| `confidence` | enum | `confirmed` / `likely` / `hypothesis` |

**去噪策略**：提取的段落少于 50 字符则跳过，所有条目仍经过质量门禁。

**配置示例**（通用多端项目示例，`init` 自动发现若干内容源，作为交互菜单来源）：

```yaml
data_sources:
  content_sources:
    - name: pitfalls
      globs: ["docs/pitfalls.md"]
      extract_mode: split_by_heading
      heading_levels: [3, 4]
      source_tag: pitfalls
      confidence: confirmed

    - name: evolution-lessons
      globs: ["docs/evolution/**/*.md"]
      exclude_globs: ["docs/pitfalls.md"]
      extract_mode: heading_keyword
      heading_patterns: ["踩坑", "教训", "遗留问题", "放弃方案"]
      source_tag: pitfalls
      confidence: confirmed

    - name: feature-context
      globs: ["docs/features/**/*.md", "docs/architecture/**/*.md"]
      extract_mode: heading_keyword
      heading_patterns: ["踩坑", "教训", "遗留问题", "根因", "失败原因"]
      source_tag: pitfalls
      confidence: confirmed

    - name: design-risks
      globs: ["docs/design/**/*.md"]
      extract_mode: heading_keyword
      heading_patterns: ["风险", "注意事项", "已知问题"]
      source_tag: pitfalls
      confidence: likely

    - name: ios-fix-plans
      globs: ["docs/fix-plans/**/*.md", "docs/ui-reports/**/*.md"]
      extract_mode: split_by_heading
      heading_levels: [2, 3]
      source_tag: pitfalls
      confidence: confirmed

    - name: playbook-lessons
      globs: ["docs/playbooks/**/*.md"]
      extract_mode: heading_keyword
      heading_patterns: ["经验沉淀", "经验教训", "Lessons Learned", "注意事项"]
      source_tag: pitfalls
      confidence: confirmed
```

**向后兼容**：当 `content_sources` 不存在时，从旧字段 `pitfalls_file` + `feature_context_glob` 自动构建等效配置。当 `content_sources` 存在时，忽略旧字段。

**`init` 自动发现**：`init` 命令扫描项目目录中 10 个候选 glob 路径模式，有匹配文件的自动加入 `content_sources`，生成预览 Markdown 供用户勾选。`content_sources` 列表作为 govern 治理时的交互选择菜单，不再自动全量扫描。

Mode E/F 默认关闭，通过 `data_sources.git_history.enabled` 和 `data_sources.code_comments.enabled` 开启，`legacy` 项目模板默认开启。

**Mode E 分支选择策略**：

Mode E 采用双层过滤避免 `--all` 扫全量分支带来的噪声和重复：

1. **第一层：分支名过滤** — 默认分支（自动检测 main/master）+ 名称匹配 `branches.patterns` 的 bug-fix 分支
2. **第二层：commit message 过滤** — `--grep` 按 `keywords` 关键词匹配

```yaml
git_history:
  enabled: true
  branches:
    default: true              # 始终包含默认分支
    patterns:                  # 分支名匹配模式
      - "bf-*"                 # bugfix 分支
      - "hf-*"                 # hotfix 分支
      - "*bugfix*"             # 含 bugfix 的分支
      - "*fix*"                # 含 fix 的分支（覆盖 hotfix、fix/、patch-fix 等）
    include_all: false         # true 退化为 --all
  keywords: [fix, bug, hotfix, patch, 修复, 缺陷]
  limit: 100
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `branches.default` | `true` | 始终包含默认分支（自动检测） |
| `branches.patterns` | `["bf-*", "hf-*", "*bugfix*", "*fix*"]` | 分支名 glob 匹配模式 |
| `branches.include_all` | `false` | 为 true 时忽略 patterns，使用 `--all` |
| `keywords` | `[fix, bug, hotfix, patch, 修复, 缺陷]` | commit message grep 关键词 |
| `limit` | `100` | 最大提取 commit 数 |

### 6.2 主动发现（Mode D）：工具优先，AI 兜底

v2.5 将 Mode D 重构为"工具优先，AI 兜底"的分层策略。先利用项目已配置的确定性静态分析工具（D0），产出不足时再 fallback 到 AI 方法（D1/D2/D3）。

```
Step 3: 判断进入 Mode D
     │
     ▼
D0: 静态工具报告采集 ──────────────────────────────────
     │                                                │
     ├── D0a: 自动检测可用工具                          │
     ├── D0b: 运行工具 / 读取已有报告                    │
     ├── D0c: 解析报告 → 归一化 Finding                  │
     ├── D0d: 按规则聚合 + 趋势分析                      │
     └── D0e: LLM 泛化为 Experience Card                │
     │                                                │
     ▼                                                │
D-Gate: 质量门禁 ──────────────────────────             │
     │                                                │
     ▼                                                │
D0 产出 >= min_static_findings?                         │
     │                                                │
     ├─ 是 ──▶ Step 5 索引                              │
     │                                                │
     └─ 否 ──▶ D1/D2/D3 AI 兜底 ──▶ D-Gate ──▶ Step 5  │
```

#### 6.2.1 D0: 静态分析工具

利用项目已配置的确定性代码分析工具发现系统性代码质量问题。**零 LLM token 成本，确定性、可重复。**

| 平台 | 工具 | 检测依据 | 报告格式 | source | confidence |
|------|------|---------|---------|--------|-----------|
| backend | PMD | `pom.xml` 含 `maven-pmd-plugin` | `pmd-xml` | `static-analysis` | `likely` |
| backend | CheckStyle | `pom.xml` 含 `maven-checkstyle-plugin` | `checkstyle-xml` | `static-analysis` | `likely` |
| backend | SpotBugs | `pom.xml` 含 `spotbugs-maven-plugin` | `spotbugs-xml` | `static-analysis` | `likely` |
| ios | SwiftLint | `.swiftlint.yml` 存在 | `swiftlint-json` | `static-analysis` | `likely` |
| web | ESLint | `.eslintrc.json` 存在 | `eslint-json` | `static-analysis` | `likely` |
| harmony | ESLint | `.eslintrc.json` 存在 | `eslint-json` | `static-analysis` | `likely` |
| android | ktlint | `build.gradle.kts` 含 `org.jlleitschuh.gradle.ktlint` | `ktlint-text` | `static-analysis` | `likely` |

**聚合策略**：按 `(tool, rule_id)` 分组，同一规则出现 >= `aggregate_threshold`（默认 3）次才视为系统性问题值得建卡。单次出现的个案不建卡。热度排名 = `count × severity_weight`。

**运行模式**：

| mode | 行为 |
|------|------|
| `auto`（默认） | 检查报告文件是否存在且 < `report_max_age_hours`，有则读取，无则运行工具 |
| `run` | 总是执行 `command` 生成新报告 |
| `report` | 仅读取已有报告文件（适用于 CI 产出） |

**配置示例**：

```yaml
proactive_discovery:
  static_analysis:
    enabled: true
    auto_detect: true
    mode: auto
    report_max_age_hours: 24
    aggregate_threshold: 3
    min_severity: warning
    tools:
      - platform: backend
        name: pmd
        command: "mvn pmd:pmd -Dformat=xml -q"
        report_glob: "target/pmd.xml"
        format: pmd-xml
        working_dir: backend
      - platform: ios
        name: swiftlint
        command: "swiftlint lint --reporter json 2>/dev/null || true"
        format: swiftlint-json
        working_dir: ios
      # ... 更多工具
```

#### 6.2.2 Rule Context 增强

`rule_context` 将 Cursor/Claude 的 `.mdc` 编码规则文件注入 D1 Code Review 的 prompt，让 AI 按项目特定编码标准审查：

```yaml
proactive_discovery:
  rule_context:
    enabled: true
    sources:
      - ".cursor/rules/backend.mdc"
      - ".cursor/rules/ios.mdc"
      - ".cursor/rules/web.mdc"
      - ".cursor/rules/android.mdc"
```

#### 6.2.3 D1/D2/D3 AI 兜底

当 D0 静态工具产出不足时，fallback 到 AI 方法：

| 子阶段 | 方法 | 适用场景 | confidence |
|--------|------|---------|-----------|
| **D1** | Code Review（+ rule_context 增强） | 审查最近 20 次提交，按模块并行分发 | `likely` |
| **D2** | Business Rule Audit | 逐条比对业务规则文档与实现代码 | `likely` |
| **D3** | Brainstorm Edge Cases | 按 (module, focus_area) 组合推测潜在缺陷 | `hypothesis` |

**AI 兜底触发策略**：

| `ai_fallback` | 行为 |
|----------------|------|
| `when_d0_insufficient` | D0 产出 < `min_static_findings` 时执行 D1/D2/D3 |
| `always` | D0 之后始终追加 D1/D2/D3 |
| `never` | 完全禁用 AI 方法（纯工具模式） |

**Focus Areas（关注领域）**：

| 领域 | 关注方向 |
|------|---------|
| concurrency | 竞态条件、非原子操作、死锁 |
| input-validation | 缺失校验、类型转换、边界值 |
| error-handling | 未捕获异常、降级策略、超时 |
| cache-consistency | 缓存失效、键冲突、非原子读写 |
| state-lifecycle | 状态泄露、生命周期不匹配、内存未释放 |

### 6.3 Mode D 质量门禁

D0（静态工具）和 D1/D2/D3（AI）产出的所有卡片在 v2.0 标准质量门禁之上增加额外门禁：

```
D0/D1/D2/D3 产出
     │
     ▼
 标准质量检查（6 维度 >= 3.5）──▶ 低分淘汰 (exit 2)
     │
     ▼
 可验证性检查（verification_plan 可执行）──▶ 不可验证淘汰
     │
     ▼
 语义去重（ChromaDB 相似度 > 0.9）──▶ 重复淘汰
     │
     ▼
 用户确认（require_user_confirm=true 时）
     │
     ├─ 确认 → confidence 升级为 confirmed
     ├─ 修改 → 编辑后入库
     └─ 丢弃 → 不入库
```

---

## 7. 工作流集成

### 7.1 与 Bug 修复流程的集成

```
Bug 报告
  │
  ▼
┌─────────────────────────────────────┐
│  ios-fix-bug-ui / web-fix-bug-ui   │
│                                     │
│  Step 0: 问题重述                    │
│  Step 1: 查询缺陷知识库              │◀── search-defect-kb
│  Step 2: 定位根因                    │     (找历史类似经验)
│  Step 3: 修复                        │
│  Step 4: 验证                        │
│  Step 5: 经验沉淀触发                │──▶ post-fix-hook
│                                     │     → defect-knowledge-base
└─────────────────────────────────────┘
```

**自动触发条件**（满足任一即弹出沉淀提示）：

- 排查经历 2+ 个失败假设
- 修复涉及跨模块改动
- Bug 严重度为 P0/P1
- 对话中出现"踩坑""原来是""没想到"等发现性语句
- Judge 修复循环 >= 2 轮
- 涉及并发/缓存/安全

### 7.2 集成的上游 Skill

| 上游 Skill | 平台 | 集成点 |
|-----------|------|--------|
| `ios-fix-bug-ui` | iOS | 修复前查库 + 修复后沉淀 |
| `web-fix-bug-ui` | Web | 修复前查库 + 修复后沉淀 |
| `backend-dev-lifecycle` | Backend | 修复后沉淀 |
| `backend-workflow.mdc` (Judge) | Backend | 验收通过后沉淀 |
| `code-review` | 全端 | 发现问题时查库判断是否已知模式 |

### 7.3 Auto-RAG 自动注入机制（v2.3）

v2.3 将知识库从"被动检索"升级为"主动注入"——Agent 在编码任务中自动检索知识库并将相关警告注入到响应上下文，开发者无需手动触发。

#### 7.3.1 决策树总览

```
用户发送消息
  │
  ▼
┌────────────────────────────────┐
│ Guard 1: defect-kb.yaml 存在?  │──否──▶ 全部跳过（静默）
└───────────┬────────────────────┘
            │ 是
            ▼
┌────────────────────────────────┐
│ Guard 2: chroma_db/ 有索引?    │──否──▶ 仅保留沉淀提醒
└───────────┬────────────────────┘
            │ 是
            ▼
┌────────────────────────────────┐
│ Guard 3: auto_injection.enabled│──否──▶ 退回手动模式（v2.2 行为）
└───────────┬────────────────────┘
            │ 是
            ▼
┌────────────────────────────────┐
│ 跳过条件检查（任一命中则跳过）  │
│                                │
│ · 非编码任务（纯聊天/文档/git）  │──命中──▶ 正常响应，零开销
│ · 任务类型 ∉ trigger_on 列表    │
│ · 同一 query 已检索过           │
│ · 会话检索次数 >= max_searches  │
└───────────┬────────────────────┘
            │ 全部未命中
            ▼
┌────────────────────────────────┐
│ 触发条件检查（任一命中即检索）  │
│                                │
│ · bug_fix: 报错/crash/异常      │
│ · feature_dev: 实现/开发功能    │──无命中──▶ 正常响应
│ · code_review: 审查代码         │
│ · 正在执行对应 Skill            │
└───────────┬────────────────────┘
            │ 命中
            ▼
┌────────────────────────────────┐
│ 提取 query（2-5 个技术关键词）  │
│ 推断 platform                  │
│ 执行 search --output-format    │
│   compact --min-similarity ... │
└───────────┬────────────────────┘
            │
      ┌─────┴──────┐
      │            │
   有结果       无结果
      │            │
      ▼            ▼
   注入警告     正常响应
   然后执行     零开销
   用户任务
```

#### 7.3.2 触发条件详解

Agent 在收到每条用户消息时，根据消息内容和对话上下文判定当前任务类型。每种类型有明确的信号词和上下文线索：

| 任务类型 | 配置键 | 信号词示例 | 上下文线索 |
|---------|--------|-----------|-----------|
| `bug_fix` | `trigger_on: [bug_fix]` | 报错、bug、修复、排查、crash、异常、失败、not working | 用户粘贴了错误日志或堆栈；正在执行 `fix-bug` 类 Skill |
| `feature_dev` | `trigger_on: [feature_dev]` | 实现、开发、新增、添加、重构、迭代 | 用户提到模块名（如 M009-home-feed）；正在执行 `feature-dev` 类 Skill |
| `code_review` | `trigger_on: [code_review]` | review、审查、检查、看下代码 | 用户提供了 diff/PR 链接；正在执行 `code-review` Skill |

**只有当识别出的任务类型存在于 `trigger_on` 列表中时才触发检索**。默认配置包含全部三种类型，可按需裁剪。

#### 7.3.3 跳过条件详解

以下任一条件成立，即跳过自动检索：

| 跳过条件 | 判定方式 | 设计理由 |
|---------|---------|---------|
| 非编码任务 | 消息不涉及代码生成或修改（如纯问答、讨论方案、写文档、配置变更、git 操作） | 避免在无关场景浪费检索资源和上下文空间 |
| 任务类型不在 trigger_on | 识别到的任务类型不在 `auto_injection.trigger_on` 列表中 | 允许项目按需关闭某类任务的自动注入 |
| 重复 query | 本次会话中已对相同或高度相似的关键词组合检索过 | 防止同一话题反复注入相同警告 |
| 检索配额耗尽 | 本次会话自动检索次数已达 `max_searches_per_session`（默认 3） | 限制单次会话的上下文累积开销 |

#### 7.3.4 执行步骤

**Step 1: 提取 Query**

从用户消息中提取 2-5 个技术关键词作为检索 query。提取策略：
- 错误信息中的关键字段（如 `Cannot find type in scope`、`NullPointerException`）
- 涉及的技术概念（如 `fullScreenCover`、`ObservableObject`、`CodingKeys`）
- 涉及的模块名（如 `M009-home-feed`）
- 操作动作（如 `并发访问`、`缓存失效`、`数据库查询`）

**Step 2: 推断 Platform**

从上下文推断 `platform` 参数：
- 当前正在编辑的文件路径（`ios/` → ios，`backend/` → backend）
- 正在执行的 Skill（`ios-fix-bug-ui` → ios）
- 用户消息中明确提到的平台
- 无法推断时省略 `--platform`，不做平台过滤

**Step 3: 执行检索**

静默执行 CLI 命令（不向用户展示命令本身）：

```bash
python3 {SKILL_DIR}/defect-kb/bootstrap.py search \
  --query "{query}" \
  --platform {platform} \
  --top-k {auto_injection.max_results} \
  --min-similarity {auto_injection.min_similarity} \
  --output-format compact
```

**Step 4: 注入或跳过**

- **有结果**：在 Agent 响应开头以引用块展示，然后正常执行用户请求的任务。

  ```
  > **已知陷阱提醒**（缺陷知识库）：
  > - [DEF-20260410-001|P1] fullScreenCover 内联创建 ViewModel 导致子视图状态重置 → 将 ViewModel 创建移到 closure 外部，子视图用 @StateObject 持有
  ```

- **无结果**：正常执行任务，不展示任何提示，用户无感知。

用户可说 **"展开 DEF-xxx"** 查看完整 Resolution Layer（根因、验证方案、失败尝试）。

#### 7.3.5 上下文开销预算

| 开销来源 | Token 量 | 时机 | 频率 |
|---------|---------|------|------|
| `defect-kb.mdc` 规则本身 | ~1.5k | alwaysApply，每次会话加载 | 固定 |
| compact 检索结果（每条） | 50-150 | 触发时注入 | 0-3 条/次 |
| 单次注入总计 | 0-450 | 仅编码任务 | ≤ max_results |
| 单会话累积注入 | 0-1350 | 全会话 | ≤ max_searches × max_results |

**总开销**：在 128k 上下文窗口中，单次注入占 0-0.35%，全会话累积占 0-1.05%。绝大多数非编码对话零额外开销。

**与注意力稀释的权衡**：compact 格式刻意精简到一行（`[ID|严重度] 摘要 → 策略`），仅传达"有什么坑"和"怎么避"两个核心信息。过度的上下文注入反而会分散模型注意力，因此通过 `min_similarity` 阈值和 `max_results` 上限双重控制注入量。

#### 7.3.6 配置参考

```yaml
auto_injection:
  enabled: true                  # 总开关
  min_similarity: 0.3            # 最低相似度阈值
  max_results: 3                 # 每次最多注入条数
  max_searches_per_session: 3    # 每会话最多自动检索次数
  format: compact                # 注入格式（compact | full）
  trigger_on:                    # 启用自动注入的任务类型
    - bug_fix
    - feature_dev
    - code_review
```

| 参数 | 默认值 | 调优建议 |
|------|--------|---------|
| `enabled` | `true` | 不想要自动注入时设为 `false`，退回手动模式 |
| `min_similarity` | `0.3` | 本地 embedding 建议 0.3；OpenAI embedding 建议 0.7（距离分布不同） |
| `max_results` | `3` | 知识库卡片 < 20 张时可降为 2；> 100 张时可升为 5 |
| `max_searches_per_session` | `3` | 长会话（多轮修复/开发）可升为 5 |
| `format` | `compact` | 需要完整 Resolution Layer 时改为 `full`（但上下文开销大幅增加） |
| `trigger_on` | 全部 3 类 | 如项目不需要 code review 注入可移除 `code_review` |

#### 7.3.7 降级与兼容

| 场景 | 行为 |
|------|------|
| `auto_injection` 段不存在 | 等同 `enabled: false`，退回 v2.2 手动触发模式 |
| `enabled: false` | 自动注入关闭，手动触发词（"查缺陷库"、"搜踩坑"）仍然可用 |
| 知识库为空（chroma_db 无数据） | 跳过自动注入，仅展示首次使用引导 |
| 检索返回空结果 | 静默跳过，用户无感知 |
| CLI 执行失败（venv 损坏等） | 吞掉错误，不阻断用户任务 |

### 7.4 集成配置

所有集成关系通过 `defect-kb.yaml` 的 `integrations` 字段声明式管理：

```yaml
integrations:
  write_context_skill: write-dev-context   # 联动决策记录
  read_context_skill: read-dev-context     # 联动历史上下文
  fix_bug_skills:                          # 自动触发沉淀的上游 Skill
    - ios-fix-bug-ui
    - web-fix-bug-ui
    - backend-dev-lifecycle
```

留空则不联动，系统降级为独立工作模式。

---

## 8. 技术栈

| 组件 | 技术选型 | 选型理由 |
|------|---------|---------|
| 运行环境 | Python 3.10+ | 生态成熟，与 AI 工具链天然适配 |
| 数据校验 | Pydantic v2 | 类型安全，自动生成 JSON Schema |
| 结构化存储 | JSONL (JSON Lines) | 增量追加友好，每行一张 Card，便于 git diff |
| 向量数据库 | ChromaDB (PersistentClient) | 本地嵌入式，零运维，支持元数据过滤 |
| Embedding | 默认 sentence-transformers（本地，零 Key）；高级可选 OpenAI / DeepSeek / Qwen / 豆包 API | 默认零依赖本地模型 |
| LLM 标准化 | 默认 Agent 自身 LLM（零 Key）；高级可选 OpenAI GPT / Claude / DeepSeek / Qwen / 豆包 | 默认路径无需 API Key |
| LLM 调用抽象 | `llm.py` — OpenAI 兼容 Provider 统一走 `openai` SDK + `base_url`，Claude 走 `anthropic` SDK | `get_provider_config()` 路由到正确的 SDK |
| JSON 解析 | `parser.py` | 健壮解析 LLM 输出（直接 JSON / markdown fence / 大括号匹配） |
| 配置管理 | YAML (PyYAML) | 可读性强，适合项目级配置 |
| 项目配置发现 | 向上遍历目录树 | 类似 `.git` 发现机制，支持 monorepo |

### 8.1 依赖清单

通过 `bootstrap.py` 自动管理：首次调用任何命令时，在 `defect-kb-data/.venv/` 自动创建虚拟环境并安装所有依赖。

```
chromadb>=0.5.0
pydantic>=2.7.0
pyyaml>=6.0
sentence-transformers>=2.7.0

# Advanced: only needed for --input / --auto-retry / openai embedding
# openai>=1.30.0
# anthropic>=0.40.0
```

### 8.2 高级配置：多 Provider LLM 支持

> 默认路径（`--json` + `--quality-json` + local embedding）无需配置任何 Provider。以下为高级路径（`--input` / `--auto-retry`）所需。

支持 5 个云端 Provider + 本地 Embedding：

| Provider | API 协议 | SDK | 环境变量 | 默认模型 |
|----------|---------|-----|---------|---------|
| OpenAI GPT | 原生 OpenAI | `openai` | `OPENAI_API_KEY` | gpt-4o-mini |
| Anthropic Claude | Anthropic Messages API | `anthropic`（可选依赖） | `ANTHROPIC_API_KEY` | claude-sonnet-4-20250514 |
| DeepSeek | OpenAI 兼容 | `openai` | `DEEPSEEK_API_KEY` | deepseek-chat |
| Qwen/通义千问 | OpenAI 兼容 | `openai` | `DASHSCOPE_API_KEY` | qwen-plus |
| 豆包/Doubao | OpenAI 兼容 | `openai` | `ARK_API_KEY` | doubao-1-5-pro-32k |
| local | N/A | `sentence-transformers` | (无) | all-MiniLM-L6-v2 (仅 Embedding) |

**配置方式**：在 `defect-kb.yaml` 的 `llm.providers` 字典中声明启用的 Provider，`llm.provider` 指定 LLM 调用用哪个，`llm.embedding_provider` 指定 Embedding 用哪个（可不同）。

**限制**：Claude 不提供 Embedding API，若将 `embedding_provider` 设为 `claude` 会报错。

### 8.3 Issue Tracker 多平台认证

Mode C 支持三个 Issue Tracker 平台，各自独立配置认证方式：

| 平台 | 认证方式 | 环境变量 | 请求头 | CLI/API |
|------|---------|---------|--------|---------|
| GitHub | Token（优先）+ `gh` CLI（fallback） | `GITHUB_TOKEN`（可通过 `env_token` 自定义） | `Authorization: Bearer {token}` | REST API `curl`；token 不存在时 fallback `gh issue list` |
| 云效 Yunxiao | 个人访问令牌 | `YUNXIAO_TOKEN`（可通过 `env_token` 自定义） | `x-yunxiao-token: {token}` | REST API `curl` |
| GitLab | Private Token | `GITLAB_TOKEN`（可通过 `env_token` 自定义） | `PRIVATE-TOKEN: {token}` | REST API `curl` |

**配置方式**：在 `defect-kb.yaml` 的 `data_sources.issue_trackers` 列表中声明启用的 Issue Tracker：

```yaml
data_sources:
  issue_trackers:
    - type: github
      repo: org/repo
      bug_label: bug
      state: closed
      env_token: GITHUB_TOKEN
      limit: 50
    - type: yunxiao
      organization_id: "5ebbc022xxx"
      project_id: "proj-xxx"
      category: Bug
      env_token: YUNXIAO_TOKEN
      base_url: https://devops.aliyun.com
      limit: 50
    - type: gitlab
      project: "group/project"
      bug_label: bug
      state: closed
      env_token: GITLAB_TOKEN
      base_url: https://gitlab.com
      limit: 50
```

**向后兼容**：旧配置中的 `data_sources.github_bug_label` 仍然可用，系统自动转换为 `type: github` 的 tracker 条目。

---

## 9. 文件结构

### 9.1 可移植工具包（随项目 `.cursor/skills/` 分发）

```
defect-knowledge-base/
├── SKILL.md                          # Skill 入口：写入缺陷卡片
├── search-defect-kb.md               # Skill：语义检索知识库
├── govern-defect-data.md             # Skill：批量治理 + 主动发现
├── defect-kb-sop.md                  # 全流程 SOP 文档
├── architecture.md                   # ← 本文档
├── references/
│   └── post-fix-hook.md              # Bug 修复后自动沉淀的触发逻辑
└── defect-kb/                        # CLI 工具包
    ├── __init__.py                   # 版本号 (v2.0)
    ├── bootstrap.py                  # 零依赖入口：自动创建 venv + 安装依赖 + exec cli.py
    ├── cli.py                        # 命令实现：init/govern/quick/upgrade/index/search/browse/stats/report/log-event（含 INDEX.md 渲染、usage 反馈环、种子卡片导入）
    ├── config.py                     # 配置加载与路径解析
    ├── schema.py                     # Experience Card + QualityScore Pydantic 模型
    ├── llm.py                        # LLM 调用抽象层（OpenAI/Claude/DeepSeek/Qwen/豆包 + local embedding）
    ├── parser.py                     # LLM 输出健壮解析（JSON + markdown fence）
    ├── requirements.txt              # Python 依赖
    ├── setup.py                      # pip 包元信息（暂不发布，为抽包做准备）
    ├── prompts/                      # LLM Prompt 模板
    │   ├── standardize.txt           # 原始文本 → Experience Card（govern 主路径）
    │   ├── standardize_quick.txt     # 一句话 → 最小合法卡（quick 命令使用，约束更宽松）
    │   ├── quality_check.txt         # 6 维度质量评估
    │   ├── proactive_static.txt      # D0: 静态工具 Finding → Card
    │   ├── proactive_review.txt      # D1: Code Review 发现 → Card
    │   ├── proactive_audit.txt       # D2: 业务规则审计 → Card
    │   └── proactive_brainstorm.txt  # D3: 边界假设推测 → Card
    └── seeds/                        # 内置种子卡片（init --import-seeds 导入）
        └── built-in.jsonl            # 15 张 AI 编程通病示例（common / ios / android / web / backend）
```

### 9.2 项目级产出（不随工具包分发，每个项目独立）

```
项目根目录/
├── defect-kb.yaml                    # 项目配置（init 生成，提交 git）
└── defect-kb-data/                   # 数据目录（部分追踪，部分忽略）
    ├── .gitignore                    # 自动生成（仅忽略 chroma_db/events/reports/.venv/.tmp）
    ├── .venv/                        # Python 虚拟环境（bootstrap.py 自动创建，git 忽略）
    ├── cards.jsonl                   # 所有 Experience Card 原始数据（含 QualityScore + usage_count，**纳入 git**）
    ├── INDEX.md                      # 人类可读的卡片目录，govern 后自动重生（**纳入 git**）
    ├── events.jsonl                  # 操作事件日志（govern/search/search_hit/index/search_outcome/fix_session/confidence_change，git 忽略）
    ├── sync-state.json               # Issue Tracker 增量同步时间戳（v2.2，git 忽略）
    ├── chroma_db/                    # ChromaDB 向量索引（git 忽略）
    └── reports/                      # report 命令生成的 Markdown/HTML 报告（git 忽略）
        └── report-YYYYMMDD.md
```

> **追踪 vs 忽略的设计**：`cards.jsonl` 和 `INDEX.md` 是知识资产本身，纳入 git 后 PR 可以同时 review 代码改动 + 沉淀的经验；`events.jsonl` / `chroma_db/` / `reports/` 是衍生数据，可由 `cards.jsonl` 重建。`init` 命令对老项目 `.gitignore` 做幂等迁移（`cards.jsonl` → `cards.jsonl.tmp`）。

---

## 10. 跨项目可移植性

### 10.1 设计原则

系统严格分离"工具"与"数据"：

| 维度 | 可移植（工具） | 不可移植（数据） |
|------|--------------|----------------|
| 文件 | `defect-knowledge-base/` 整个目录 | `defect-kb.yaml` + `defect-kb-data/` |
| 内容 | Skill 定义、CLI 代码、Prompt 模板 | 项目平台列表、模块定义、知识库数据 |
| 管理 | 可跨项目复制、版本同步 | 每个项目独立 init、独立积累 |

### 10.2 新项目接入

```bash
# 方式 1：自动扫描 + 预览（推荐）
# bootstrap.py 自动创建 venv 并安装依赖，无需手动 pip install
python /path/to/defect-knowledge-base/defect-kb/bootstrap.py init --install-skills
# → 生成 defect-kb-init-preview.md，用户检查/编辑后确认
python /path/to/defect-knowledge-base/defect-kb/bootstrap.py init --confirm --install-skills

# 方式 2：跳过预览，直接写入 YAML
python /path/to/defect-knowledge-base/defect-kb/bootstrap.py init --template mobile --no-preview --install-skills
```

**可用模板**：

| 模板 | 平台 | 模块规范 | 关注领域 | fix_bug_skills 预填 |
|------|------|---------|---------|-------------------|
| `mobile` | ios, android | M{NNN}-{name} | state-lifecycle, concurrency, input-validation, error-handling | ios-fix-bug-ui, android-fix-bug-ui |
| `web` | web | 自由文本 | input-validation, error-handling, cache-consistency | web-fix-bug-ui |
| `backend` | backend | 自由文本 | concurrency, cache-consistency, input-validation, error-handling | backend-dev-lifecycle |
| `fullstack` | ios, android, web, backend | M{NNN}-{name} | concurrency, input-validation, error-handling, cache-consistency, state-lifecycle | ios-fix-bug-ui, web-fix-bug-ui, backend-dev-lifecycle |

### 10.3 跨项目经验复用

虽然每个项目数据独立，但 Experience Card 的泛化设计使**手动跨项目迁移**成为可能：

- `problem_summary` 不含项目特定名称 → 可被其他项目语义匹配
- `fix_strategy` 与具体代码解耦 → 可直接应用到类似技术栈
- 未来规划支持多项目联合索引

---

## 11. CLI 命令参考

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `init` | 初始化项目配置（自动扫描项目名/repo/平台/模块/Skill/规则文件 + Markdown 预览） + 可选导入种子卡片 | `--template`、`--install-skills`、`--force`、`--no-preview`、`--confirm`、`--import-seeds [PLATFORMS\|all]`、`--skip-seeds` |
| `govern` | 原始文本/JSON → Experience Card；写入成功后自动重生 `INDEX.md` | `--json`（默认）、`--quality-json`（默认）、`--platform`、`--force`、`--output-format`；高级：`--input`（需 Key）、`--auto-retry`（需 Key）、`--skip-quality` |
| `quick` | 一句话 5 秒落卡：跳过质量门禁，标记 `metadata.quick=true`；写入后重生 `INDEX.md` 并 log `quick` 事件 | `note`（位置参数）、`--platform`、`--module`、`--severity {P0\|P1\|P2}`（默认 P2）、`--no-llm`（强制本地最小卡）、`--json`（Agent 预填）、`--output-format`；LLM 不可用时自动降级为本地最小卡 |
| `upgrade` | 把 quick 卡升级为完整卡：重新走 standardize + 6 维质量门禁；保留 id/date/usage_count，翻 `quick=false` 并写 `upgraded_at` | `--id`（必填）、`--input`（新原文）、`--json`（Agent 预填）、`--force`（覆写质量失败）、`--skip-quality`、`--output-format`；默认用现有卡内容作 raw_text 喂给 LLM 改写 |
| `index` | 未索引 Card → ChromaDB 向量索引；`--rebuild-md` 仅重建 `INDEX.md` 并退出 | `--embedding-provider {openai\|local}`、`--rebuild-md`、`--output-format` |
| `search` | 检索知识库（语义/混合/精排）；命中后累计 `usage_count` 并 log `search_hit` 事件 | `--query`、`--platform`、`--top-k`、`--min-similarity`、`--hybrid`、`--rerank`、`--embedding-provider`、`--no-record`（只读模式，跳过反馈环）、`--output-format {text\|json\|compact}` |
| `browse` | 按 ID 查看完整 Card（含质量评分详情） | `--id`、`--output-format` |
| `stats` | ASCII Dashboard：分布柱图、Top Hot Cards、Cold Candidates、质量门禁；默认排除 seed/quick 卡，避免示例/占位稀释项目质量信号 | `--include-seeds`、`--include-quick`、`--output-format {text\|json}` |
| `report` | 生成质量报告（Markdown 或 HTML Dashboard） | `--period {all\|month\|quarter}`、`--format {md\|html}`、`--output` |
| `log-event` | 记录自定义事件到 events.jsonl | `--action-type {search_outcome\|fix_session\|confidence_change}`、`--data` |

所有命令支持 `--output-format json` 结构化输出，方便 Skill 层消费和自动化集成。

### 11.1 事件日志（events.jsonl）

`govern`、`search`、`index` 三个命令在执行完成后自动追加事件到 `defect-kb-data/events.jsonl`，每行一个 JSON 事件。
`log-event` 命令允许 Skill 层写入自定义价值度量事件。

| action | 记录字段 | 写入方式 |
|--------|---------|---------|
| `govern` | card_id, platform, module, source, quality_passed, quality_avg, quality_override, rejected | CLI 自动 |
| `quick` | card_id, platform, module, severity | CLI 自动（5 秒落卡路径） |
| `upgrade` | card_id, ran_llm, quality_avg, quality_passed | CLI 自动 |
| `search` | query, platform, results_count, top_similarity, hit, search_mode | CLI 自动 |
| `search_hit` | card_id, query, position, similarity, final_score, usage_count_after | CLI 自动（每张命中卡片一条） |
| `index` | indexed_count, total_count | CLI 自动 |
| `search_outcome` | query, top_card_id, outcome (applied/viewed/ignored/no_results), platform | Skill 调 `log-event` |
| `fix_session` | platform, module, kb_searched, kb_card_applied, hypotheses_tried, severity | Skill 调 `log-event` |
| `confidence_change` | card_id, from_level, to_level, reason | Skill 调 `log-event` |

所有事件均包含 `ts`（ISO 格式时间戳）字段。

### 11.2 报告命令（report）

`report` 命令从 `cards.jsonl` + `events.jsonl` 聚合数据，支持两种输出格式：

- `--format md`（默认）：生成 Markdown 质量报告
- `--format html`：生成自包含 HTML 可视化 Dashboard（Chart.js 图表）

报告包含 9 个维度：

1. **知识库概览** — 总卡片数、质量通过率、覆写率
2. **卡片分布** — 按平台 / 严重度 / 来源 / 模块
3. **质量评分分析** — 6 维度平均分、最低维度、月度趋势
4. **检索效果** — 总检索次数、命中率、月度趋势
5. **沉淀效率** — govern 调用次数、门禁拒绝率、月度趋势
6. **覆盖缺口分析** — 有卡片但无检索的模块 / 有检索但无卡片的领域
7. **改进建议** — 基于数据自动生成 2-3 条建议
8. **知识库价值** — 检索应用率、修复参考率、假设减少率、置信度升级数
9. **ROI 摘要** — 综合投入产出分析

---

## 12. 安全与隐私

| 关注点 | 措施 |
|--------|------|
| API Key | 默认路径（`--json` + `--quality-json` + local embedding）无需任何 API Key；高级路径通过环境变量读取（每个 Provider 独立 env_key），不写入配置文件或代码 |
| 知识库数据 | `defect-kb-data/` 自动生成 `.gitignore`，不提交到远程仓库 |
| 信息泄露 | 质量评估第 6 维度（Information Security）专门检测是否泄露密钥/凭据/内部 URL |
| LLM 数据传输 | 默认路径 Agent 自身 LLM 处理，不额外外传；高级路径通过所选 Provider API 传输 |
| 本地模式 | `--embedding-provider local` 使用本地模型，数据不离开本机 |

---

## 13. 项目落地示例

以下为一个多端项目的参考落地形态：

| 配置项 | 值 |
|--------|-----|
| 平台覆盖 | ios, android, backend, web 等多端项目 |
| 模块数量 | 多个核心模块（如 M001 ~ M0XX） |
| LLM | 默认路径：Agent LLM（标准化 + 质量评估）+ 本地 sentence-transformers（向量化），零 API Key；高级可切换至 OpenAI/Claude/DeepSeek/Qwen/豆包 |
| 被动数据源 | pitfalls.md + feature context glob + Issue Trackers（GitHub / 云效 / GitLab 多平台） |
| 主动发现 | 启用，触发模式 when_zero。D0 静态工具（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint 7 工具），AI 兜底 when_d0_insufficient |
| 上游集成 | ios-fix-bug-ui、web-fix-bug-ui、backend-dev-lifecycle |
| 联动 Skill | write-dev-context（决策记录）、read-dev-context（历史上下文） |

---

## 14. 未来演进方向

| 方向 | 描述 | 优先级 | 状态 |
|------|------|--------|------|
| 质量门禁强制执行 | 6 维评分低于阈值阻断写入，支持强制覆写和自动改进重试 | P0 | v2.0 已完成 |
| CLI+Skill 混合架构 | CLI 专注数据操作，Skill 专注智能编排；`--json` 快速路径无需 API Key | P0 | v2.0 已完成 |
| 本地 Embedding | `--embedding-provider local` 支持 sentence-transformers，零 API 依赖 | P1 | v2.0 已完成 |
| 项目模板化 | `init --template` 支持 mobile/web/backend/fullstack 预设 | P1 | v2.0 已完成 |
| 事件追踪日志 | govern/search/index 操作自动记录到 events.jsonl | P1 | v2.0 已完成 |
| 质量报告生成 | `report` 命令从 cards+events 聚合生成 Markdown 报告 | P1 | v2.0 已完成 |
| 卡片生命周期管理 | 支持更新、废弃、合并已有卡片 | P1 | 规划中 |
| Mode AB-D 批量流水线 | CLI 层原生支持批量采集和主动扫描 | P1 | 规划中 |
| 多项目联合索引 | 支持跨项目 ChromaDB 联合检索，复用通用踩坑经验 | P1 | 规划中 |
| 知识库价值度量 | `search_outcome`/`fix_session`/`confidence_change` 事件追踪 + 报告 Section 8-9 | P1 | v2.2 已完成 |
| HTML 可视化报告 | `report --format html` 生成 Chart.js Dashboard（指标卡、柱状图、雷达图、仪表盘） | P1 | v2.2 已完成 |
| Web Dashboard | 提供知识库浏览、统计、趋势分析的可视化界面 | P2 | 规划中 |
| 自动置信度升级 | 当 `[hypothesis]` 卡片被实际 Bug 修复引用时自动升级为 `confirmed` | P2 | 规划中 |
| 团队协作 | 支持 cards.jsonl 的 git 协作（冲突合并策略） | P2 | 规划中 |
| 多 LLM Provider（高级） | `llm.py` + `config.py` 支持 OpenAI/Claude/DeepSeek/Qwen/豆包 5 个 Provider + 本地 Embedding（默认零 Key 路径无需配置） | P1 | v2.0 已完成 |
| 多平台 Issue Tracker | Mode C 支持 GitHub / 云效 Yunxiao / GitLab 三个平台，`issue_trackers` 列表配置，向后兼容 `github_bug_label` | P1 | v2.1 已完成 |
| 老项目冷启动 | Mode E（Git History Mining）+ Mode F（Code Comment Mining）+ `legacy` 项目模板 | P1 | v2.1 已完成 |
| Reranker 精排 | `search --rerank` 启用 cross-encoder 二次排序，提升 Top-K 精度 | P1 | v2.2 已完成 |
| 混合检索 | `search --hybrid` 关键词 + 语义双路召回，综合分排序 | P1 | v2.2 已完成 |
| Issue Tracker 增量同步 | `sync-state.json` 记录 `last_imported`，Mode C API 带 `since`/`updated_after` | P1 | v2.2 已完成 |
| Auto-RAG 自动注入 | Agent 在编码任务中自动检索知识库并注入 compact 格式警告，`auto_injection` 配置段 | P0 | v2.3 已完成 |
| 缺陷分类体系 | `defect_category` 7 类标签（ai-hallucination/ai-antipattern/ai-security/ai-edge-case/framework-pitfall/framework-deprecation/team-pattern） | P1 | v2.3 已完成 |
| 框架信息追踪 | `framework_info` 记录框架名/版本约束/废弃 API | P1 | v2.3 已完成 |
| AI 反模式种子库 | 6 张通用 AI 反模式卡片作为初始知识 | P1 | v2.3 已完成 |
| 通用内容扫描器 | Mode A/B 合并为 Mode AB，`content_sources` 配置数组支持多 glob + 两种提取模式，`init` 自动发现 10 种候选路径 | P0 | v2.4 已完成 |
| 交互式治理 + 人工确认 | Mode AB 从自动全量扫描改为用户交互选择；所有数据源写入前增加人工确认步骤（单条/批量） | P0 | v2.6 已完成 |
| 静态分析工具集成 | Mode D 新增 D0 阶段：自动检测项目已有工具（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint），解析报告、聚合建卡；AI 方法降为兜底 | P0 | v2.5 已完成 |
| 规则上下文增强 | `.mdc` 编码规则注入 D1 Code Review prompt，提升 AI 审查精度 | P1 | v2.5 已完成 |
| CI 集成 | PR 检查时自动查询知识库，若改动区域有已知踩坑则警告 | P3 | 规划中 |
