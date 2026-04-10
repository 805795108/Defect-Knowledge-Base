# AI 缺陷知识库 SOP：从初始化到日常使用

> 基于 MemGovern 经验治理理念构建的项目级 AI 缺陷知识库，跨项目可复用。
> 所有缺陷经验以 Experience Card 结构化沉淀，支持语义检索，辅助 Bug 诊断与修复。
> **v2.6**：交互式治理（Mode AB 用户选择数据源 + 全 Mode 写入前人工确认）+ 质量门禁严格执行 + Agent LLM 快速路径（零 API Key）+ 本地 Embedding + 项目模板化 + 多平台 Issue Tracker（GitHub / 云效 / GitLab）+ 混合检索 + Reranker 精排 + 增量同步 + 知识库价值度量 + Auto-RAG 自动注入 + Mode AB 交互式内容源选择 + Mode D0 静态分析工具优先 + AI 兜底 + 规则上下文增强

---

## 适用场景

- 新项目接入缺陷知识库（1 分钟模板初始化）
- 批量迁移存量踩坑记录到结构化知识库
- 修复 Bug 后沉淀经验卡片
- 诊断问题时检索历史踩坑
- 项目代码扫描无缺陷时，主动发现潜在问题
- 分析知识库整体质量分布（`stats` 命令）
- 生成结构化质量报告（`report` 命令）

## 前置条件

- Python 3.10+
- 所有命令通过 `bootstrap.py` 调用，它会自动在 `defect-kb-data/.venv/` 创建虚拟环境并安装依赖，无需手动 `pip install`

> `{SKILL_DIR}` = `.cursor/skills/defect-knowledge-base` 或 `.claude/skills/defect-knowledge-base`

**按使用路径区分**：

| 路径 | 额外要求 | 说明 |
|------|---------|------|
| **默认路径**（`--json` + `--quality-json`） | 无 | Agent 自身 LLM 做标准化 + 质量评估，CLI 只做校验+写入，零 API Key |
| **向量索引**（`index`） | 无（local embedding 默认） | 本地 sentence-transformers，零 API Key |
| **GitHub 导入**（Mode C1） | 环境变量 `GITHUB_TOKEN` 已设置（优先）；或 `gh` CLI 已安装且认证（fallback） | 用于 GitHub Issues 导入 |
| **云效导入**（Mode C2） | 环境变量 `YUNXIAO_TOKEN` 已设置 | 云效个人访问令牌，用于 REST API |
| **GitLab 导入**（Mode C3） | 环境变量 `GITLAB_TOKEN` 已设置 | GitLab Private Token，用于 REST API |
| **高级：半快速路径**（`--json`） | 所选 Provider 的 API Key | Agent 标准化，CLI 内部 LLM 做质量评估 |
| **高级：完整路径**（`--input`） | 所选 Provider 的 API Key | CLI 内部调用 LLM 做标准化 + 质量评估 |

> 高级路径支持 5 个云端 LLM Provider（OpenAI/Claude/DeepSeek/Qwen/豆包），详见 `architecture.md` 的"高级配置"章节。

---

## 全流程总览

```
Phase 1             Phase 2              Phase 3              Phase 4              Phase 5
项目初始化           存量数据治理          日常使用              主动发现              维护与分析
→ init 模板         → Content Sources     → 写卡片(质量门禁)    → D0 静态工具分析    → stats 统计
→ --install-skills  → Issue Trackers      → 修 Bug 后沉淀       → D1 Code Review    → report 报告
                    → Git History/注释    → 开发前先查           → D2 业务规则审计    → 置信度升级
                                          → 质量门禁拦截/重试    → D3 边界假设        → 定期巡检
                          ↓                      ↓                    ↓                    ↓
                    ┌────────────────────────────────────────────────────────────────────────────┐
                    │              defect-kb-data/cards.jsonl (含 QualityScore)                   │
                    │              defect-kb-data/events.jsonl (操作事件日志)                      │
                    │              defect-kb-data/chroma_db/ (向量索引)                            │
                    │              defect-kb-data/reports/ (质量报告)                               │
                    └────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1：项目初始化

### 做什么

为项目生成配置文件 `defect-kb.yaml`，创建数据目录，可选自动安装 Skill 文件。

### 执行步骤

```bash
# 方式 1：自动扫描 + 预览（推荐）
# 自动扫描项目目录，检测平台、fix-bug Skill、pitfalls 文件，推荐模板
python {SKILL_DIR}/defect-kb/bootstrap.py init --install-skills
# → 生成 defect-kb-init-preview.md（可编辑的 Markdown 配置预览）
# → 用户查看/编辑预览文件中的 checkbox 和配置项
# → 确认后执行：
python {SKILL_DIR}/defect-kb/bootstrap.py init --confirm --install-skills

# 方式 2：跳过预览，直接写入（已知模板的快速初始化）
python {SKILL_DIR}/defect-kb/bootstrap.py init --template mobile --no-preview --install-skills

# 方式 3：手动复制 + 初始化（已有 Skill 文件的项目）
python {SKILL_DIR}/defect-kb/bootstrap.py init --no-preview
```

**自动扫描检测项**：
- 平台目录（ios / android / web / backend / harmony / flutter / recommend）
- 已有的 fix-bug Skill 文件（`.cursor/skills/` 和 `.claude/skills/` 下的 `*fix-bug*` 目录）
- pitfalls 文件（如 `docs/pitfalls.md`）
- feature context glob 路径
- Git remote → 自动推断 GitHub Repo（`org/repo` 格式）
- 功能模块文档目录（如 `docs/features/*/`）→ 自动提取模块示例（`M{NNN}-{name}` 格式）
- `write-dev-context` / `read-dev-context` Skill → 自动填充 integrations
- 业务规则文档 / API 合约文档（如 `docs/business-rules.md`、`contracts/api-contract.yaml`）→ 自动填充 business-rule-audit 路径

**可用模板**：

| 模板 | 预填平台 | 预填关注领域 | 特殊配置 |
|------|---------|-------------|---------|
| `mobile` | ios, android | state-lifecycle, concurrency, input-validation, error-handling | — |
| `web` | web | input-validation, error-handling, cache-consistency | — |
| `backend` | backend | concurrency, cache-consistency, input-validation, error-handling | — |
| `fullstack` | ios, android, web, backend | 全部 5 个领域 | — |
| `legacy` | 自动扫描 | concurrency, input-validation, error-handling, cache-consistency | 默认开启 Mode E（Git History，双层分支过滤）+ Mode F（Code Comments）；需 commit >= 50 才推荐 |

### 产出物

| 文件 | 说明 |
|------|------|
| `defect-kb.yaml` | 项目唯一配置文件，所有 Skill 和 CLI 从此读取上下文 |
| `defect-kb-data/` | 本地数据目录（cards.jsonl + events.jsonl + sync-state.json + chroma_db/ + reports/），不提交 git |
| `defect-kb-data/.gitignore` | 自动生成，忽略数据文件 |
| `.cursor/skills/defect-knowledge-base/` | 使用 `--install-skills` 时自动复制 |
| `.claude/skills/defect-knowledge-base/` | 检测到 `.claude/` 目录时同步复制 |

### 检查清单

- [ ] `defect-kb.yaml` 中 platforms 列表与项目实际技术栈一致
- [ ] `modules.examples` 涵盖核心模块
- [ ] `data_sources.pitfalls_file` 指向正确的存量文件（如有）
- [ ] `proactive_discovery.enabled` 根据需要设置（默认 true）
- [ ] Skill 文件已在 `.cursor/skills/` 中（`--install-skills` 自动完成）

---

## Phase 2：存量数据治理

### 做什么

将项目已有的踩坑记录、Bug Issue、Feature Context 中的缺陷经验批量转化为 Experience Card。

### 触发方式

在 Cursor / Claude Code 中说：

> "治理缺陷数据"

或手动执行：

```bash
# 默认路径（Agent 已标准化好 JSON + 质量评估，零 API Key）
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":{...},"resolution_layer":{...},"metadata":{"severity":"P1",...}}' \
  --quality-json '{"scores":{...},"average":4.2,"issues":[],"pass":true}' \
  --platform ios \
  --module "M014-playback-control" \
  --source pitfalls \
  --output-format json

# 批量索引（默认本地 embedding，零 API Key）
python {SKILL_DIR}/defect-kb/bootstrap.py index --output-format json

# 高级路径（CLI 调用 LLM，需 API Key）
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --input "fullScreenCover 闭包中内联创建 ViewModel 导致 TextField 输入丢失..." \
  --platform ios \
  --module "M014-playback-control" \
  --source pitfalls \
  --output-format json
```

### 数据源优先级

Skill `govern-defect-data` 按以下顺序执行（Mode AB 需用户交互选择）：

```
Mode AB: Content Sources（v2.6 交互式选择）
  └─ 展示 content_sources 配置列表作为菜单
  └─ 用户通过 AskQuestion 选择要处理的 source 或手动指定文件路径
  └─ 两种提取模式：
     · split_by_heading：按标题级别分割整文件（如 pitfalls.md）
     · heading_keyword：搜索标题匹配关键词的段落（如 "踩坑"、"教训"）
  └─ source = 按 source_tag 配置（默认 "pitfalls"）
  └─ 向后兼容：content_sources 不存在时从旧字段 pitfalls_file + feature_context_glob 自动构建

Mode C: Issue Tracker 导入（按 issue_trackers 列表遍历，支持增量同步）
  └─ 读取 defect-kb-data/sync-state.json 获取 last_imported 时间戳
  └─ 首次运行为全量拉取，后续仅拉取 last_imported 之后的新增 issues
  C1 GitHub:
    └─ REST API（优先，since 参数）或 gh issue list --label bug --state closed
    └─ 拼接 title + body → source = "github-issue"
  C2 云效 Yunxiao:
    └─ REST API listWorkitems?category=Bug（gmtModifiedAfter 参数）+ 详情接口
    └─ 拼接 subject + description → source = "yunxiao-issue"
  C3 GitLab:
    └─ REST API /api/v4/projects/:id/issues（updated_after 参数）
    └─ 拼接 title + description → source = "gitlab-issue"
  └─ 处理完成后更新 sync-state.json 时间戳

Mode E: Git History Mining（需 git_history.enabled = true）
  └─ 双层过滤：分支名模式（bf-*/hf-*/*bugfix*/*fix*）+ git log --grep 关键词
  └─ 提取 commit message + diff → source = "git-history", confidence = "likely"

Mode F: Code Comment Mining（需 code_comments.enabled = true）
  └─ rg 扫描 TODO/FIXME/HACK/WORKAROUND/XXX 注释
  └─ 提取注释 + 上下文代码 → source = "code-comment", confidence = "hypothesis"
```

### 标准化 + 质量门禁流程（每条记录）

```
原始文本 / 预标准化 JSON
    │
    ▼
标准化（LLM 或 Agent 自身）
    │  泛化 problem_summary
    │  提取 5-12 个 signals
    │  抽象 fix_strategy
    │  记录 abandoned_approaches
    ▼
质量检查（6 维度 1-5 分）
    │
    ├─ PASS (均分 >= 3.5 且各维度 >= 3)
    │   └─ 写入 cards.jsonl（含 QualityScore）
    │
    └─ FAIL
        ├─ --auto-retry → 自动改进重试（最多 2 轮）
        ├─ --force → 强制写入（标记 quality_override）
        └─ 默认 → 阻断，打印改进建议（exit code 2）
    ▼
Pydantic 校验 (schema.py)
    │  字段完整性 + 类型正确
    ▼
写入 cards.jsonl → Embedding → ChromaDB 索引
```

### Experience Card 结构

```
┌─ Index Layer（检索层）─────────────────────────────┐
│  problem_summary: 泛化的问题描述                      │
│  signals: [错误类型, 症状, 触发条件, 受影响组件, ...]   │
├─ Resolution Layer（解决层）─────────────────────────┤
│  root_cause: 真正的根因                               │
│  fix_strategy: 可迁移的抽象修复方法                     │
│  patch_digest: 关键代码变更摘要                        │
│  verification_plan: 可执行的验证步骤                   │
│  abandoned_approaches: [失败方案 + 原因]               │
├─ Metadata（元数据）────────────────────────────────┤
│  id: DEF-20260318-001                                │
│  platform / module / source / severity               │
│  confidence: confirmed | likely | hypothesis         │
│  quality: {6 维评分 + average + passed + issues}      │
└──────────────────────────────────────────────────────┘
```

**Local Embedding 语言注意事项**：

默认本地模型 `all-MiniLM-L6-v2` 仅支持英文。如果 Experience Card 使用中文编写：
- 推荐在 `defect-kb.yaml` 的 `llm.providers.local.embedding_model` 中配置多语言模型：
  - `paraphrase-multilingual-MiniLM-L12-v2`（推荐，中英双语效果好）
  - `BAAI/bge-small-zh-v1.5`（中文专用，体积小）
- 或者保持 `all-MiniLM-L6-v2` 但将卡片的 `problem_summary` 和 `signals` 用英文编写

### 检查清单

- [ ] Mode AB: 用户已选择要处理的内容源或文件
- [ ] 所有待写入卡片已经用户人工确认
- [ ] Mode AB/C 各执行完毕，汇报产出数量（仅统计已确认写入的卡片）
- [ ] 质量门禁生效：低分卡片被阻断或标记 override
- [ ] `index` 执行成功（OpenAI 或 local embedding）
- [ ] 抽样检查 2-3 张卡片：problem_summary 已泛化、signals 覆盖 4 维度
- [ ] `stats` 命令确认整体质量分布合理

---

## Phase 3：日常使用

### 场景 A：修 Bug 后沉淀经验（自动触发）

**触发方式**：

| 方式 | 说明 |
|------|------|
| **自动触发** | `ios-fix-bug-ui` / `web-fix-bug-ui` 修复验证通过后自动弹出提示 |
| 手动触发 | 在 Cursor / Claude Code 中说 "记录缺陷" 或 "写缺陷卡片" |

**自动触发条件**（满足任一即弹出提示）：
- 本次排查经历了 2 个以上失败假设
- 修复涉及跨模块改动
- 对话中出现过"踩坑"、"原来是"、"没想到"等发现性语句
- Bug 严重度为 P0/P1

**自动触发流程**：

```
ios-fix-bug-ui / web-fix-bug-ui
    │
    ├─ Step 0-4: 诊断 → 定位 → 修复 → 验证
    │
    ▼
Step 5: 经验沉淀（自动触发）
    │
    ├─ 检查 defect-kb.yaml 是否存在
    ├─ 评估沉淀必要性
    │
    ▼
┌─────────────────────────────────────────────┐
│  Bug 已修复并验证通过。                        │
│                                               │
│  本次修复经验建议沉淀到缺陷知识库：              │
│    问题: fullScreenCover 内联创建 ViewModel...  │
│    平台: ios                                   │
│    模块: M014-playback-control                 │
│    根因: 闭包中 init() 导致每次重建实例           │
│                                               │
│  ○ 记录缺陷（沉淀经验卡片）                     │
│  ○ 跳过                                       │
└─────────────────────────────────────────────┘
    │
    ▼ 用户选择「记录缺陷」
    │
Skill: defect-knowledge-base（预填上下文）
    │
    ├─ Step 1: 信息已预填，用户确认或补充
    ├─ Step 2: 调用 CLI govern（默认 --json --quality-json，高级可用 --input）
    ├─ Step 3: 质量门禁检查
    │   ├─ PASS → 展示卡片，用户确认
    │   └─ FAIL → 展示失败维度，用户选择修改/强制/放弃
    ├─ Step 4: 更新索引（index）
    └─ Step 5 (可选): 联动 write-dev-context 写决策记录
```

**配置方式**：

在 `defect-kb.yaml` 中配置哪些 Skill 会自动触发：

```yaml
integrations:
  fix_bug_skills:
    - ios-fix-bug-ui           # iOS Bug 修复后自动触发
    - web-fix-bug-ui           # Web Bug 修复后自动触发
    - backend-dev-lifecycle    # Backend Bug 修复后自动触发
```

自动触发的完整逻辑定义在 `references/post-fix-hook.md`，上游 Skill 各只需一行引用。

留空则不会自动触发，用户仍可手动说"记录缺陷"。

**黄金规则**：

1. 排查超过 30 分钟的 Bug → 必须沉淀
2. 跨模块 / 跨端的问题 → 必须沉淀
3. 同一类问题第二次出现 → 必须沉淀

### 场景 B：开发前查知识库

**时机**：开始新功能开发前、遇到报错时、诊断 Bug 时

**触发**：在 Cursor / Claude Code 中说：

> "查缺陷库" 或 "搜踩坑" 或 "有没有类似的坑"

**流程**：

```
遇到问题 / 开始新功能
    │
    ▼
Skill: search-defect-kb
    │
    ├─ Step 1: 构造检索 query（从报错 / 任务描述提取）
    │
    ├─ Step 2: 检索 top-5 结果
    │   python bootstrap.py search --query "..." --output-format json
    │   # 支持 --hybrid（混合检索）、--rerank（精排）
    │   # 支持 --embedding-provider local（无 API Key 时）
    │
    ├─ Step 3: 展示结果（含置信度标记）
    │   [confirmed]  人工确认的真实缺陷
    │   [likely]     AI 发现，高置信度
    │   [hypothesis] AI 推测，需验证
    │   [unknown]    置信度未标注
    │
    └─ Step 4: 展开高相关卡片的完整 Resolution Layer
```

**最佳实践**：

- 不确定某个 API 的边界行为？先查知识库
- 收到模糊的 Bug 报告？用关键词搜索相似经验
- 准备用 `.fullScreenCover`？查一下有没有已知的坑

### 场景 C：被其他 Skill 联动调用

| 上游 Skill / Rule | 联动时机 | 联动动作 |
|---|---|---|
| `ios-fix-bug-ui` | 修复 Bug 前 | 搜索知识库，找类似经验 |
| `web-fix-bug-ui` | 修复 Bug 前 | 搜索知识库，找类似经验 |
| `ios-fix-bug-ui` | Step 4 验证通过后 | 自动触发 post-fix-hook 提示沉淀 |
| `web-fix-bug-ui` | Step 4 验证通过后 | 自动触发 post-fix-hook 提示沉淀 |
| `backend-dev-lifecycle` | BugFix 第 4 步通过后 | 自动触发 post-fix-hook 提示沉淀 |
| `backend-workflow` (Judge) | 修复轮验收通过后 | 自动触发 post-fix-hook 提示沉淀 |
| `code-review` | 发现问题时 | 查知识库，判断是否已知模式 |

---

## Phase 4：主动发现（Mode D）— 工具优先，AI 兜底

### 做什么

当被动数据源（Mode AB / C / E / F）没有产出足够缺陷卡片时，主动扫描项目代码发现潜在缺陷。v2.5 采用**工具优先，AI 兜底**策略：先运行项目已有的确定性静态分析工具（D0），产出不足时再 fallback 到 AI 方法（D1/D2/D3）。

### 触发条件

```
proactive_discovery.enabled = true

trigger = "when_zero":  仅 Mode AB/C/E/F 合计为 0 张卡片（用户确认写入的）时触发
trigger = "always":     每次治理都追加执行
```

也可以直接说：

> "主动发现缺陷" 或 "proactive discovery"

### 分层流水线总览

```
Step 3: 判断进入 Mode D
     │
     ▼
D0: 静态工具报告采集（零 LLM token，确定性、可重复）
     │
     ├── D0a: 自动检测可用工具（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint）
     ├── D0b: 运行工具 / 读取已有报告（auto/run/report 三种模式）
     ├── D0c: 解析报告 → 归一化 NormalizedFinding
     ├── D0d: 按 (tool, rule_id) 聚合 + 热度排名（>= aggregate_threshold 才建卡）
     └── D0e: LLM 泛化为 Experience Card（proactive_static.txt prompt）
     │
     ▼
D-Gate: 质量门禁
     │
     ▼
D0 产出 >= min_static_findings?
     │
     ├─ 是 ──▶ Step 5 索引（跳过 AI 方法）
     │
     └─ 否 ──▶ D1/D2/D3 AI 兜底 ──▶ D-Gate ──▶ Step 5
```

### D0: 静态分析工具（v2.5 新增）

利用项目已配置的确定性代码分析工具发现系统性代码质量问题。

| 平台 | 工具 | 检测依据 | 报告格式 | discovery_method |
|------|------|---------|---------|-----------------|
| backend | PMD | `pom.xml` 含 `maven-pmd-plugin` | `pmd-xml` | `pmd` |
| backend | CheckStyle | `pom.xml` 含 `maven-checkstyle-plugin` | `checkstyle-xml` | `checkstyle` |
| backend | SpotBugs | `pom.xml` 含 `spotbugs-maven-plugin` | `spotbugs-xml` | `spotbugs` |
| ios | SwiftLint | `.swiftlint.yml` 存在 | `swiftlint-json` | `swiftlint` |
| web | ESLint | `.eslintrc.json` 存在 | `eslint-json` | `eslint` |
| harmony | ESLint | `.eslintrc.json` 存在 | `eslint-json` | `eslint` |
| android | ktlint | `build.gradle.kts` 含 `org.jlleitschuh.gradle.ktlint` | `ktlint-text` | `ktlint` |

**运行模式**：

| mode | 行为 |
|------|------|
| `auto`（默认） | 检查报告文件是否存在且 < `report_max_age_hours`，有则读取，无则运行工具 |
| `run` | 总是执行 `command` 生成新报告 |
| `report` | 仅读取已有报告文件（适用于 CI 产出） |

**聚合策略**：按 `(tool, rule_id)` 分组，同一规则出现 >= `aggregate_threshold`（默认 3）次才视为系统性问题值得建卡。热度排名 = `count × severity_weight`。

**D0 产出的每条聚合 Finding**：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":...}' \
  --platform {platform} \
  --source static-analysis \
  --confidence likely \
  --discovery-method {tool_name} \
  --output-format json
```

### D0 → D1 兜底判定

| `ai_fallback` 配置 | 行为 |
|---------------------|------|
| `when_d0_insufficient`（默认） | D0 产出 < `min_static_findings`（默认 5）时执行 D1/D2/D3 |
| `always` | D0 之后始终追加 D1/D2/D3 |
| `never` | 完全禁用 AI 方法（纯工具模式） |

### D1: Code Review 发现（AI 兜底）

当 `rule_context.enabled = true` 时，D1 会将 `.mdc` 编码规则注入 prompt，按项目特定标准审查代码。

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":...}' \
  --platform {platform} \
  --source ai-proactive \
  --confidence likely \
  --discovery-method code-review \
  --output-format json
```

### D2: Business Rule Audit（AI 兜底）

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":...}' \
  --platform {platform} \
  --source ai-proactive \
  --confidence likely \
  --discovery-method business-rule-audit \
  --output-format json
```

### D3: Brainstorm Edge Cases（AI 兜底）

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":...}' \
  --platform {platform} \
  --source ai-proactive \
  --confidence hypothesis \
  --discovery-method brainstorm-edge-case \
  --output-format json
```

### 质量门禁（D0 + D1/D2/D3 通用）

D0 和 D1/D2/D3 产出的所有卡片经过增强门禁：

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

**Focus Areas 参考**（D3 使用）：

| 焦点领域 | 关注什么 |
|----------|---------|
| concurrency | 竞态条件、非原子操作、死锁 |
| input-validation | 缺失校验、类型转换、边界值 |
| error-handling | 未捕获异常、降级策略、超时处理 |
| cache-consistency | 缓存失效、键冲突、非原子读写 |
| state-lifecycle | 状态泄露、生命周期不匹配、内存未释放 |

### 配置参考

```yaml
proactive_discovery:
  enabled: true
  trigger: when_zero
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
      # ... 更多工具（init 自动检测并生成）
  rule_context:
    enabled: true
    sources:
      - ".cursor/rules/backend.mdc"
      - ".cursor/rules/ios.mdc"
  methods:
    ai_fallback: when_d0_insufficient
    min_static_findings: 5
    items:
      - type: code-review
        scope: "git diff HEAD~20"
        min_severity: Important
      - type: business-rule-audit
        rules_file: docs/business-rules.md
        contract_file: contracts/api-contract.yaml
      - type: brainstorm-edge-case
        focus_areas: [concurrency, input-validation, error-handling, cache-consistency, state-lifecycle]
        max_cards: 10
  quality_gate:
    require_user_confirm: true
    min_quality_score: 3.5
```

---

## Phase 5：知识库维护与分析

### 质量统计

```bash
# 文本格式
python {SKILL_DIR}/defect-kb/bootstrap.py stats

# JSON 格式（用于自动化）
python {SKILL_DIR}/defect-kb/bootstrap.py stats --output-format json
```

输出包含：卡片总数、质量通过/未通过/覆写数量、按平台/严重度/来源的分布、各质量维度平均分。

### 质量报告

生成包含知识库各维度指标的质量报告：

```bash
# Markdown 报告（默认，输出到 defect-kb-data/reports/report-YYYYMMDD.md）
python {SKILL_DIR}/defect-kb/bootstrap.py report

# HTML 可视化 Dashboard（Chart.js 图表，输出到 defect-kb-data/reports/report-YYYYMMDD.html）
python {SKILL_DIR}/defect-kb/bootstrap.py report --format html

# 最近 30 天报告
python {SKILL_DIR}/defect-kb/bootstrap.py report --period month

# 最近 90 天报告
python {SKILL_DIR}/defect-kb/bootstrap.py report --period quarter

# 自定义输出路径
python {SKILL_DIR}/defect-kb/bootstrap.py report --output ./my-report.md
```

报告包含 9 个维度：

1. **知识库概览** — 总卡片数、质量通过率、覆写率
2. **卡片分布** — 按平台 / 严重度 / 来源 / 模块
3. **质量评分分析** — 6 维度均分 + 月度趋势
4. **检索效果** — 命中率 + 月度趋势
5. **沉淀效率** — 门禁拒绝率 + 月度趋势
6. **覆盖缺口分析** — 有卡片但无检索 / 有检索但无卡片
7. **改进建议** — 基于数据自动生成
8. **知识库价值** — 检索应用率、修复参考率、假设减少率、置信度升级数
9. **ROI 摘要** — 综合投入产出分析

> 事件来源：`govern`/`search`/`index` 命令自动追加事件到 `events.jsonl`。
> `search_outcome`/`fix_session`/`confidence_change` 事件由 Skill 层通过 `log-event` 命令写入，为 Section 8-9 提供数据。

### 定期巡检（月度）

```bash
# 查看知识库规模
python {SKILL_DIR}/defect-kb/bootstrap.py stats

# 抽样检查卡片质量（含质量评分详情）
python {SKILL_DIR}/defect-kb/bootstrap.py browse --id DEF-20260318-001

# 搜索特定领域
python {SKILL_DIR}/defect-kb/bootstrap.py search --query "并发问题" --top-k 10
```

### 置信度升级

长期未确认的 `[likely]` / `[hypothesis]` 卡片应定期 review：
- 经实际验证确认 → 手动编辑 cards.jsonl 升级为 `confirmed`
- 长期未验证且无参考价值 → 考虑移除

### 质量覆写卡片治理

通过 `stats` 命令发现 `quality_overridden` 数量过多时：
- 检查覆写卡片的 `issues` 字段，定位常见质量问题
- 考虑改进 Prompt 或调整质量阈值

### 跨项目迁移

所有工具、Skill、Prompt、SOP 全部集中在 `defect-knowledge-base/` 一个文件夹内。新项目接入：

```bash
# 一键初始化 + 自动安装（bootstrap.py 自动创建 venv 并安装依赖）
# 默认零 API Key：govern 用 --json --quality-json（Agent LLM），index 用本地 embedding
python /path/to/defect-knowledge-base/defect-kb/bootstrap.py init --template backend --install-skills
```

每个项目各自拥有独立的 `defect-kb.yaml`（项目根目录）和 `defect-kb-data/`（ChromaDB + JSONL），互不干扰。

---

## CLI 命令速查

| 命令 | 用途 | 示例 |
|------|------|------|
| `init` | 初始化（自动扫描+预览） | `bootstrap.py init --install-skills` |
| `init` | 确认预览并写入 | `bootstrap.py init --confirm --install-skills` |
| `init` | 跳过预览直接写入 | `bootstrap.py init --template mobile --no-preview --install-skills` |
| `govern` | 默认路径（零 Key） | `bootstrap.py govern --json '...' --quality-json '...' --platform ios --output-format json` |
| `govern` | 高级：半快速路径 | `bootstrap.py govern --json '...' --platform ios --output-format json` |
| `govern` | 高级：完整路径 | `bootstrap.py govern --input "..." --platform ios --auto-retry` |
| `govern` | 强制写入 | `bootstrap.py govern --json '...' --force --output-format json` |
| `index` | 向量索引 | `bootstrap.py index --output-format json` |
| `index` | 本地 embedding | `bootstrap.py index --embedding-provider local` |
| `search` | 语义检索 | `bootstrap.py search --query "输入丢失" --platform ios --top-k 5` |
| `search` | 混合检索 | `bootstrap.py search --query "输入丢失" --hybrid` |
| `search` | 混合+精排 | `bootstrap.py search --query "输入丢失" --hybrid --rerank` |
| `browse` | 查看卡片详情 | `bootstrap.py browse --id DEF-20260318-001` |
| `stats` | 质量统计 | `bootstrap.py stats --output-format json` |
| `report` | 生成 Markdown 报告 | `bootstrap.py report --period month` |
| `report` | 生成 HTML Dashboard | `bootstrap.py report --format html` |
| `report` | 自定义输出路径 | `bootstrap.py report --output ./report.md` |
| `log-event` | 记录价值度量事件 | `bootstrap.py log-event --action-type search_outcome --data '{...}'` |

> 所有命令前缀：`python {SKILL_DIR}/defect-kb/bootstrap.py`
> 全局参数 `--project-root` 放在子命令之前

## govern 关键参数速查

| 参数 | 说明 |
|------|------|
| `--input` | 原始文本（CLI 调用 LLM 标准化），与 `--json` 互斥 |
| `--json` | 预标准化 JSON（跳过 LLM 标准化），与 `--input` 互斥 |
| `--quality-json` | 预评估的质量分数 JSON（跳过 CLI 内部 LLM 质量评估，零 Key） |
| `--platform` | 平台标识 |
| `--module` | 模块名 |
| `--source` | 数据来源：pitfalls / github-issue / yunxiao-issue / gitlab-issue / git-history / code-comment / agent-transcript / manual / ai-proactive / static-analysis |
| `--force` | 质量门禁失败时仍然写入（标记 quality_override） |
| `--auto-retry` | 质量门禁失败时自动改进重试（最多 2 轮，与 `--quality-json` 同时传入时忽略） |
| `--skip-quality` | 跳过质量检查（优先级高于 `--quality-json`） |
| `--output-format` | 输出格式：text（默认）/ json |

## search 关键参数速查

| 参数 | 说明 |
|------|------|
| `--query` | 检索查询（必需） |
| `--platform` | 按平台过滤 |
| `--top-k` | 返回结果数（默认 5） |
| `--hybrid` | 开启混合检索（关键词 + 语义双路召回），也可在 `defect-kb.yaml` 设 `search.hybrid: true` |
| `--rerank` | 开启 cross-encoder 精排（提升 Top-K 精度），也可在 `defect-kb.yaml` 设 `search.rerank: true` |
| `--embedding-provider` | Embedding 提供者：local（默认）/ openai |
| `--output-format` | 输出格式：text（默认）/ json |

## Skill 触发词速查

| Skill | 触发词 |
|-------|--------|
| defect-knowledge-base | "记录缺陷"、"写缺陷卡片"、"沉淀踩坑"、"write defect card" |
| search-defect-kb | "查缺陷库"、"搜踩坑"、"search defect"、"有没有类似的坑" |
| govern-defect-data | "治理缺陷数据"、"迁移踩坑记录"、"govern defect data"、"主动发现缺陷" |

## 参考文档

- 项目配置：`defect-kb.yaml`（项目根目录）
- 项目数据：`defect-kb-data/`（项目根目录，不提交 git）
- **以下全部在 `{SKILL_DIR}/` 下：**
- CLI 入口：`defect-kb/bootstrap.py`（零依赖，自动管理 venv + 依赖安装）
- CLI 工具：`defect-kb/cli.py`（8 个命令：init/govern/index/search/browse/stats/report/log-event）
- Experience Card Schema：`defect-kb/schema.py`（含 QualityScore 模型）
- LLM 抽象层：`defect-kb/llm.py`（默认零 Key；高级支持 OpenAI/Claude/DeepSeek/Qwen/豆包 + local embedding）
- JSON 解析器：`defect-kb/parser.py`
- Prompt 模板：`defect-kb/prompts/`（6 个模板：standardize / quality_check / proactive_static / proactive_review / proactive_audit / proactive_brainstorm）
- 写入 Skill：`SKILL.md`
- 搜索 Skill：`search-defect-kb.md`
- 治理 Skill：`govern-defect-data.md`
- 自动触发：`references/post-fix-hook.md`
- 系统架构：`architecture.md`
- MemGovern 论文：[QuantaAlpha/MemGovern](https://github.com/QuantaAlpha/MemGovern)
