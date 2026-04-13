---
name: defect-kb-sop
description: >
  AI 缺陷知识库全流程 SOP，覆盖初始化、存量治理、日常使用、主动发现、维护分析五个阶段。
  Use when onboarding a new project to the defect knowledge base, planning data governance,
  or needing the complete lifecycle reference.
---

# AI 缺陷知识库 SOP

> v2.6 | 基于 MemGovern 经验治理理念 | 跨项目可复用

## 适用场景

- 新项目接入缺陷知识库
- 批量迁移存量踩坑记录
- 修复 Bug 后沉淀经验卡片
- 诊断问题时检索历史踩坑
- 主动发现潜在缺陷
- 质量统计（`stats`）与报告（`report`）

## 前置条件

- Python 3.10+
- 所有命令通过 `bootstrap.py` 调用，自动在 `defect-kb-data/.venv/` 创建虚拟环境并安装依赖
- **默认路径零 API Key**：Agent LLM 标准化 + 质量评估，CLI 校验写入，本地 embedding 索引
- Issue Tracker 认证、高级 LLM Provider 配置详见 [architecture.md](architecture.md)

> `{SKILL_DIR}` = `.cursor/skills/defect-knowledge-base` 或 `.claude/skills/defect-knowledge-base`

## 全流程总览

```
Phase 1           Phase 2            Phase 3            Phase 4            Phase 5
项目初始化         存量数据治理        日常使用            主动发现            维护与分析
→ init 模板       → Content Sources   → 写卡片(质量门禁)  → D0 静态工具      → stats 统计
→ --install-skills→ Issue Trackers    → 修 Bug 后沉淀     → D1-3 AI 兜底     → report 报告
                  → Git History/注释  → 开发前先查                            → 定期巡检
                        ↓                   ↓                  ↓                  ↓
                  ┌──────────────────────────────────────────────────────────────────┐
                  │  defect-kb-data/cards.jsonl · events.jsonl · chroma_db/          │
                  └──────────────────────────────────────────────────────────────────┘
```

---

## Phase 1：项目初始化

为项目生成 `defect-kb.yaml`，创建数据目录，可选安装 Skill 文件。

```bash
# 推荐：自动扫描 + 预览
python {SKILL_DIR}/defect-kb/bootstrap.py init --install-skills
# → 生成 defect-kb-init-preview.md → 用户编辑 → 确认：
python {SKILL_DIR}/defect-kb/bootstrap.py init --confirm --install-skills

# 快速：跳过预览
python {SKILL_DIR}/defect-kb/bootstrap.py init --template mobile --no-preview --install-skills
```

**可用模板**：

| 模板 | 预填平台 | 关注领域 |
|------|---------|---------|
| `mobile` | ios, android | state-lifecycle, concurrency, input-validation, error-handling |
| `web` | web | input-validation, error-handling, cache-consistency |
| `backend` | backend | concurrency, cache-consistency, input-validation, error-handling |
| `fullstack` | 全部 | 全部 5 个领域 |
| `legacy` | 自动扫描 | 默认开启 Mode E + Mode F；需 commit >= 50 |

**产出物**：`defect-kb.yaml`（项目配置）、`defect-kb-data/`（本地数据目录，不提交 git）、Skill 文件（`--install-skills` 时复制到 `.cursor/skills/` 和 `.claude/skills/`）。

**自动扫描检测**：平台目录、已有 fix-bug Skill、pitfalls 文件、Git remote、功能模块文档、业务规则/API 合约文档、静态分析工具。

---

## Phase 2：存量数据治理

> 详细工作流见 [govern-defect-data.md](govern-defect-data.md)

触发：说 **"治理缺陷数据"**

### 数据源

| Mode | 数据源 | source 标记 | confidence |
|------|--------|-----------|-----------|
| AB | Content Sources（交互式选择） | 按 source_tag 配置 | 按 source 配置 |
| C1 | GitHub Issues | github-issue | confirmed |
| C2 | 云效 Yunxiao | yunxiao-issue | confirmed |
| C3 | GitLab Issues | gitlab-issue | confirmed |
| E | Git History（双层分支过滤） | git-history | likely |
| F | Code Comments（TODO/FIXME/HACK） | code-comment | hypothesis |

### 标准化 + 质量门禁

每条记录经标准化后通过 6 维度质量检查（信号清晰度 / 根因深度 / 修复可迁移性 / 补丁摘要 / 验证方案 / 信息安全），均分 >= 3.5 且各维度 >= 3 方可入库。未通过时阻断（`--auto-retry` 自动改进 / `--force` 强制写入）。所有待写入卡片须经用户人工确认。

> 质量门禁详细评分标准见 [architecture.md](architecture.md) Section 5

### Experience Card 结构

```
┌─ Index Layer ──────────────────────────────────────┐
│  problem_summary: 泛化的问题描述                      │
│  signals: [错误类型, 症状, 触发条件, 受影响组件, ...]   │
├─ Resolution Layer ─────────────────────────────────┤
│  root_cause / fix_strategy / patch_digest          │
│  verification_plan / abandoned_approaches          │
├─ Metadata ─────────────────────────────────────────┤
│  id / platform / module / source / severity        │
│  confidence / quality / defect_category            │
└────────────────────────────────────────────────────┘
```

> 完整字段定义见 [architecture.md](architecture.md) Section 3

---

## Phase 3：日常使用

### 场景 A：修 Bug 后沉淀经验

| 触发方式 | 说明 |
|---------|------|
| 自动触发 | fix-bug Skill 验证通过后通过 [post-fix-hook](references/post-fix-hook.md) 弹出提示 |
| 手动触发 | 说 "记录缺陷" 或 "写缺陷卡片" |

**自动触发条件**（满足任一）：排查经历 2+ 个失败假设 / 修复涉及跨模块改动 / 出现"踩坑""原来是"等发现性语句 / Bug 严重度 P0/P1

**黄金规则**：排查超 30 分钟 → 必须沉淀 | 跨模块/跨端 → 必须沉淀 | 同类问题第二次出现 → 必须沉淀

> 完整写入工作流见 [SKILL.md](SKILL.md)

### 场景 B：开发前查知识库

触发：说 **"查缺陷库"** 或 **"搜踩坑"**

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py search \
  --query "..." --platform ios --top-k 5
# 支持 --hybrid（混合检索）、--rerank（精排）
```

结果按置信度标记：`[confirmed]` 可直接参考 | `[likely]` AI 发现 | `[hypothesis]` 需验证

> 完整检索工作流见 [search-defect-kb.md](search-defect-kb.md)

### 场景 C：被其他 Skill 联动

| 上游 Skill | 联动时机 | 联动动作 |
|-----------|---------|---------|
| ios-fix-bug-ui / web-fix-bug-ui | 修复前 | 搜索知识库 |
| ios-fix-bug-ui / web-fix-bug-ui | 验证通过后 | post-fix-hook 提示沉淀 |
| backend-dev-lifecycle / backend-workflow (Judge) | 测试/验收通过后 | post-fix-hook 提示沉淀 |
| code-review | 发现问题时 | 查知识库判断是否已知模式 |

---

## Phase 4：主动发现（Mode D）

> 详细工作流见 [govern-defect-data.md](govern-defect-data.md) Step 4

触发：`proactive_discovery.enabled = true` 且被动数据源产出为零（`when_zero`）或总是执行（`always`）。也可直接说 **"主动发现缺陷"**。

### 分层策略：工具优先，AI 兜底

```
D0 静态工具 → 质量门禁 → 产出足够? → 是 → 入库
                                   → 否 → D1/D2/D3 AI 兜底 → 质量门禁 → 入库
```

| 阶段 | 方法 | confidence |
|------|------|-----------|
| D0 | 静态分析工具（PMD / CheckStyle / SpotBugs / SwiftLint / ESLint / ktlint） | likely |
| D1 | Code Review（+ .mdc 规则增强） | likely |
| D2 | Business Rule Audit | likely |
| D3 | Brainstorm Edge Cases | hypothesis |

Mode D 质量门禁在标准 6 维度之上增加可验证性检查和语义去重（相似度 > 0.9 丢弃）。

---

## Phase 5：维护与分析

### 质量统计与报告

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py stats
python {SKILL_DIR}/defect-kb/bootstrap.py report                    # Markdown
python {SKILL_DIR}/defect-kb/bootstrap.py report --format html       # HTML Dashboard
python {SKILL_DIR}/defect-kb/bootstrap.py report --period month      # 最近 30 天
```

报告 9 维度：概览 / 分布 / 质量评分 / 检索效果 / 沉淀效率 / 覆盖缺口 / 改进建议 / 知识库价值 / ROI 摘要

### 定期巡检

- `stats` 查看规模，`browse --id DEF-xxx` 抽样检查
- `[likely]`/`[hypothesis]` 卡片定期 review，验证后升级为 `confirmed`
- `quality_overridden` 过多时检查覆写卡片的 `issues` 字段

### 跨项目迁移

```bash
python /path/to/defect-knowledge-base/defect-kb/bootstrap.py init --template backend --install-skills
```

每个项目独立的 `defect-kb.yaml` + `defect-kb-data/`，互不干扰。

---

## CLI 命令速查

| 命令 | 用途 | 关键参数 |
|------|------|---------|
| `init` | 初始化 | `--template`, `--install-skills`, `--no-preview`, `--confirm` |
| `govern` | 写入卡片 | `--json` + `--quality-json`（零 Key）, `--platform`, `--force`, `--auto-retry` |
| `index` | 向量索引 | `--embedding-provider {local\|openai}` |
| `search` | 语义检索 | `--query`, `--hybrid`, `--rerank`, `--min-similarity`, `--output-format {text\|json\|compact}` |
| `browse` | 查看卡片 | `--id` |
| `stats` | 质量统计 | `--output-format json` |
| `report` | 质量报告 | `--format {md\|html}`, `--period {all\|month\|quarter}` |
| `log-event` | 记录事件 | `--action-type`, `--data` |

> 前缀：`python {SKILL_DIR}/defect-kb/bootstrap.py`

## Skill 触发词

| Skill | 触发词 |
|-------|--------|
| defect-knowledge-base | "记录缺陷"、"写缺陷卡片"、"沉淀踩坑" |
| search-defect-kb | "查缺陷库"、"搜踩坑"、"有没有类似的坑" |
| govern-defect-data | "治理缺陷数据"、"迁移踩坑记录"、"主动发现缺陷" |

## 参考文档

- 系统架构与数据模型：[architecture.md](architecture.md)
- 写入 Skill：[SKILL.md](SKILL.md)
- 搜索 Skill：[search-defect-kb.md](search-defect-kb.md)
- 治理 Skill：[govern-defect-data.md](govern-defect-data.md)
- 自动触发：[references/post-fix-hook.md](references/post-fix-hook.md)
- CLI 入口：`defect-kb/bootstrap.py`
- Experience Card Schema：`defect-kb/schema.py`
- MemGovern 论文：[QuantaAlpha/MemGovern](https://github.com/QuantaAlpha/MemGovern)
