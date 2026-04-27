# Defect Knowledge Base

AI 缺陷知识库（Defect Knowledge Base）是一套嵌入 AI 编程助手（Cursor / Claude Code）工作流的**经验治理系统**。它将团队在开发过程中遇到的 Bug、踩坑、失败尝试等隐性知识，结构化为可检索、可复用的 **Experience Card**，形成组织级缺陷经验资产。

> 架构设计借鉴 [MemGovern](https://github.com/QuantaAlpha/MemGovern) 论文提出的"经验治理"方法论。

## 解决什么问题

| 痛点 | 传统方式 | 本系统方案 |
|------|---------|-----------|
| 踩坑经验散落 | 存在于个人记忆、聊天记录中，无法复用 | 统一结构化为 Experience Card |
| 同类 Bug 反复踩 | 缺乏经验回流机制 | 修复后自动触发沉淀，开发前主动检索 |
| Bug 诊断效率低 | 每次从零排查 | 语义检索历史经验，直接参考修复策略 |
| AI 辅助缺乏上下文 | AI 无项目历史踩坑记忆 | 修复前自动查询知识库（Auto-RAG） |

## 核心特性

- **零 API Key 即可使用** — Agent 自身 LLM 做标准化 + 质量评估，本地 Embedding 做向量索引
- **6 维质量门禁** — 信号清晰度、根因深度、修复可移植性、补丁摘要、验证计划、信息安全，低于阈值严格阻断
- **多平台 Issue Tracker** — 支持 GitHub / 云效 Yunxiao / GitLab 导入
- **三级检索流水线** — 语义检索 → 混合检索（关键词+语义）→ Reranker 精排
- **热度感知排序** — `usage_count` / `last_hit_at` 自动累计，频繁命中的卡片轻微靠前（log 饱和，不喧宾夺主）
- **可见的知识资产** — `defect-kb-data/INDEX.md` 在每次 `govern`/`quick`/`upgrade` 后自动重生，作为人类可读的卡片目录随仓库一起 review
- **冷启动种子卡片** — `init --import-seeds` 一键导入 15 张 AI 编程通病示例，新仓库装完立即可 search
- **5 秒记一笔（quick）** — `cli.py quick "<一句话>"` 跳过质量门禁快速沉淀，事后用 `cli.py upgrade --id` 升级为完整卡片
- **ASCII Dashboard** — `stats` 命令直接在终端渲染分布柱图、Top Hot Cards、Cold Candidates，像 htop 看 KB 状态
- **Auto-RAG 自动注入** — Agent 编码时自动检索并注入相关缺陷警告，零用户操作
- **多 LLM Provider** — 支持 OpenAI / Claude / DeepSeek / Qwen / 豆包
- **主动发现** — 静态分析、Code Review、业务规则审计、边界假设探测

## 快速开始

### 前置条件

- Python 3.10+
- Cursor 或 Claude Code 编辑器

### 1. 初始化项目

```bash
# 使用模板快速初始化（可选: mobile / web / backend / fullstack）
python .cursor/skills/defect-knowledge-base/defect-kb/bootstrap.py init --template mobile --install-skills

# 推荐：同时导入种子卡片，让仓库装完即可 search
python .cursor/skills/defect-knowledge-base/defect-kb/bootstrap.py init \
  --template mobile --install-skills --import-seeds

# 或交互式自定义初始化
python .cursor/skills/defect-knowledge-base/defect-kb/bootstrap.py init --install-skills
```

> `bootstrap.py` 会自动在 `defect-kb-data/.venv/` 创建虚拟环境并安装依赖，无需手动 `pip install`。

`--import-seeds` 取值：
- 不带值（推荐） → 自动按检测到的 `platforms` 选择，并始终包含 `common` 通用卡
- `all` → 全部 15 张
- `ios,backend` 等 → 显式平台列表 + `common`
- `--skip-seeds` → 显式跳过

### 2. 使用方式

在 Cursor / Claude Code 对话中直接说：

| 操作 | 触发词 |
|------|--------|
| 记录缺陷 | "记录缺陷"、"写缺陷卡片"、"沉淀踩坑"、"write defect card" |
| 搜索经验 | "查缺陷库"、"搜踩坑"、"search defect" |
| 批量治理 | "治理缺陷数据"、"govern defect data" |

## 项目结构

```
defect-knowledge-base/
├── SKILL.md                  # Skill 定义入口（写缺陷卡片）
├── search-defect-kb.md       # 搜索 Skill
├── govern-defect-data.md     # 批量治理 Skill
├── defect-kb-sop.md          # 完整 SOP 操作手册
├── architecture.md           # 系统架构设计文档
├── defect-kb/                # CLI 工具
│   ├── bootstrap.py          # 入口（自动管理 venv + 依赖）
│   ├── cli.py                # 命令行实现
│   ├── schema.py             # Experience Card 数据模型
│   ├── llm.py                # LLM 抽象层（多 Provider）
│   ├── parser.py             # JSON 解析
│   ├── config.py             # 配置管理
│   └── prompts/              # LLM Prompt 模板
│       ├── standardize.txt
│       ├── quality_check.txt
│       └── proactive_*.txt
├── references/
│   └── post-fix-hook.md      # Bug 修复后自动触发逻辑
└── templates/
    └── defect-kb.mdc         # Cursor 规则模板
```

## CLI 命令参考

所有命令通过 `bootstrap.py` 调用：

```bash
SKILL_DIR=".cursor/skills/defect-knowledge-base"
```

| 命令 | 功能 | 示例 |
|------|------|------|
| `init` | 初始化项目配置（`--import-seeds` 一键导入示例卡片） | `python $SKILL_DIR/defect-kb/bootstrap.py init --template mobile --import-seeds` |
| `govern` | 原始文本 → Experience Card（写入后自动重生 INDEX.md） | `python $SKILL_DIR/defect-kb/bootstrap.py govern --json '{...}'` |
| `quick` | 一句话 5 秒落卡（跳过质量门禁，标记 `quick=true`） | `python $SKILL_DIR/defect-kb/bootstrap.py quick "Redis 击穿没加 single-flight"` |
| `upgrade` | 把 quick 卡升级为完整卡（重跑标准化 + 质量门禁） | `python $SKILL_DIR/defect-kb/bootstrap.py upgrade --id DEF-20260427-003` |
| `index` | 构建向量索引（`--rebuild-md` 仅重建 INDEX.md） | `python $SKILL_DIR/defect-kb/bootstrap.py index` |
| `search` | 语义检索（命中后累计 usage_count，`--no-record` 只读） | `python $SKILL_DIR/defect-kb/bootstrap.py search --query "内存泄漏"` |
| `browse` | 按 ID 查看卡片 | `python $SKILL_DIR/defect-kb/bootstrap.py browse --id DEF-001` |
| `stats` | ASCII Dashboard（默认排除 seed/quick；`--include-seeds` `--include-quick` 可纳入） | `python $SKILL_DIR/defect-kb/bootstrap.py stats` |
| `report` | 生成质量报告（Markdown 或 HTML Dashboard） | `python $SKILL_DIR/defect-kb/bootstrap.py report` |

## Experience Card 结构

每张卡片包含三层信息：

- **Index Layer** — `problem_summary`（泛化问题描述）+ `signals`（5-12 个高信号关键词）
- **Resolution Layer** — `root_cause`（根因）+ `fix_strategy`（抽象修复策略）+ `patch_digest`（代码变更摘要）+ `verification_plan`（验证方案）
- **Metadata** — `severity`（P0/P1/P2）+ `confidence`（confirmed/likely/hypothesis）+ `platform` + `module` + `quality_scores` + `usage_count` / `last_hit_at`（热度，CLI 自动维护）+ `seed` / `quick` / `upgraded_at`（卡片来源标记）

## 工作流

```
init --import-seeds → 导入 15 张种子卡（seed=true，立即可 search）
    ↓
Bug 修复完成
    ↓
自动/手动触发 → 提取缺陷信息 → LLM 标准化 → 质量门禁(6维评分)
    ↓                                              ↓
  通过 → 用户确认 → 写入 cards.jsonl → 重生 INDEX.md → 更新向量索引
    ↓
  未通过 → 改进建议 → 自动重试/手动修改 → 重新评估

(快速通道)
快想到一个坑 → cli.py quick "<一句话>" → 写入 cards.jsonl（quick=true，跳过门禁）→ 重生 INDEX.md
    ↓
事后有时间 → cli.py upgrade --id DEF-... → 重新标准化 + 质量门禁 → quick=false, upgraded_at 时间戳

(检索反馈环)
开发中 → 检索知识库 (search) → 命中 → usage_count += 1, last_hit_at = now
    ↓
随时 → stats Dashboard 看 Top Hot / Cold Candidates / 质量分布（默认排除 seed/quick）
```

## 依赖

核心依赖（由 `bootstrap.py` 自动安装）：

- `chromadb` — 向量数据库
- `pydantic` — 数据模型校验
- `pyyaml` — 配置文件解析
- `sentence-transformers` — 本地 Embedding

可选依赖（高级路径）：

- `openai` — OpenAI API
- `anthropic` — Claude API

## License

MIT
