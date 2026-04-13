---
name: defect-knowledge-base
description: >
  从当前对话中提取缺陷信息，标准化为 Experience Card 并写入知识库。
  Use when the user says "记录缺陷"、"写缺陷卡片"、"沉淀踩坑"、"write defect card"、"save defect",
  or after a bug fix when post-fix-hook triggers automatic experience capture.
---

# Write Defect Card

从当前对话上下文提取缺陷信息，调用 CLI 工具标准化为 Experience Card，经质量门禁和用户确认后写入项目的缺陷知识库。

## 触发方式

### 手动触发

在 Cursor / Claude Code 中说："记录缺陷"、"写缺陷卡片"、"沉淀踩坑"、"write defect card"

### 自动触发（由上游 Skill 联动）

Bug 修复类 Skill（iOS / Web / Backend）在验证通过后，通过 [post-fix-hook](references/post-fix-hook.md) 自动评估沉淀必要性并弹出提示。用户确认后调用本 Skill，上下文已预填（问题现象、根因、修复方案、平台、模块），Step 1 直接使用预填数据，用户只需确认或补充。

完整的自动触发逻辑、判定条件、预填字段提取规则见 [references/post-fix-hook.md](references/post-fix-hook.md)。

## 前置条件

- 项目根目录存在 `defect-kb.yaml`（通过 `python {SKILL_DIR}/defect-kb/bootstrap.py init` 生成）
- Python 依赖通过 `bootstrap.py` 自动管理（自动创建 `defect-kb-data/.venv` 虚拟环境并安装依赖），无需手动 `pip install`
- **默认路径（零 API Key）**：Agent 自身 LLM 做标准化 + 质量评估，CLI 只做校验+写入，无需任何外部 API Key
- **高级路径（需 API Key）**：如需使用 CLI 内部 LLM（`--input` / `--auto-retry`），需配置对应 Provider 的 API Key（支持 OpenAI/Claude/DeepSeek/Qwen/豆包，详见 `architecture.md`）

> `{SKILL_DIR}` = 本 Skill 所在目录，即 `.cursor/skills/defect-knowledge-base` 或 `.claude/skills/defect-knowledge-base`
> 所有命令统一通过 `bootstrap.py` 调用，它会自动管理虚拟环境和依赖安装。

## 工作流程

### Step 0: 读取项目配置

读取项目根目录的 `defect-kb.yaml`，获取：
- `platforms` 列表（用于校验 platform 字段）
- `modules.pattern` 和 `modules.examples`（用于匹配 module）
- `data.cards_path`（确认数据目录存在）
- `integrations`（判断是否联动其他 Skill）

### Step 1: 从对话提取原始缺陷信息

从当前对话上下文中识别并提取：

1. **问题现象**：用户描述的 bug 表现、错误信息、截图
2. **触发条件**：什么操作 / 什么状态下触发
3. **根因分析**：如果对话中已定位根因
4. **修复方案**：如果已修复，提取修复策略
5. **失败尝试**：对话中尝试过但失败的方案
6. **平台**：从 `defect-kb.yaml` 的 `platforms` 中匹配
7. **模块**：从 `modules.examples` 中匹配，或让用户指定

如果信息不完整，使用 AskQuestion 补充：

```
提取到以下缺陷信息，请确认或补充：

问题现象：{phenomenon}
触发条件：{trigger}
根因：{root_cause or "未定位"}
修复方案：{fix or "未修复"}
平台：{platform}
模块：{module}

○ 确认
○ 补充（请说明）
```

### Step 2: 标准化（构造 JSON）

根据环境选择路径：

#### 默认路径（零 API Key）

Agent 用自身 LLM 能力完成**标准化 + 质量评估**两步，CLI 只做校验和写入。

**Step 2a: 标准化**

Agent 将提取的信息标准化为 Experience Card JSON（遵循 `schema.py` 的 `ExperienceCard` 结构）。

Agent 生成 JSON 时遵循以下规则：
1. `problem_summary` 必须泛化——不含仓库名、commit hash、具体变量名
2. `signals` 覆盖 4 个维度：错误类型、症状、触发条件、受影响组件（5-12 个关键词）
3. `fix_strategy` 与具体代码路径解耦——描述抽象修复方法
4. `severity`：P0 = 核心流程阻断，P1 = 重要降级，P2 = 边缘场景

**Step 2b: 质量评估**

Agent 读取 `{SKILL_DIR}/defect-kb/prompts/quality_check.txt` 模板，将生成的卡片 JSON 填入 `{card_json}` 占位符，用自身 LLM 评估，得到质量分数 JSON：

```json
{"scores":{"signal_clarity":4,"root_cause_depth":5,"fix_portability":4,"patch_digest_quality":4,"verification_plan":3,"infosec":5},"average":4.2,"issues":[],"pass":true}
```

#### 高级路径（需要 API Key）

将提取的信息拼接为原始文本，由 CLI 内部调用 LLM 做标准化：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --input "{extracted_text}" \
  --platform {platform} \
  --module "{module}" \
  --source {source} \
  --output-format json
```

### Step 3: 人工确认

**写入前必须经用户确认。** 向用户展示生成的 Experience Card 摘要，重点关注：

- `problem_summary` 是否足够泛化（不含项目特定名称）
- `signals` 是否覆盖 4 个维度（错误类型 / 症状 / 触发条件 / 组件）
- `fix_strategy` 是否可迁移到其他项目
- `severity` 是否准确
- 质量评分各维度

```
生成的缺陷卡片：

问题摘要: {problem_summary}
信号词: {signals}
根因: {root_cause}
修复策略: {fix_strategy}
严重度: {severity}
质量评分: {average}/5.0 ({PASSED/FAILED})

○ 确认写入
○ 修改后写入（请说明修改点）
○ 放弃
```

如果质量检查未通过（任一维度 < 3 或平均分 < 3.5），额外展示失败维度和改进建议：

```
质量检查未通过（平均分: {average}）

失败维度：
  - {dimension}: {score}/5 — {issue}

○ 修改后重新提交（Agent 根据建议改进 JSON，重新展示）
○ 强制写入（标记为 quality_override）
○ 放弃
```

### Step 4: 确认后调用 CLI 写入

用户确认后，将卡片 JSON 和质量分数 JSON 一起传给 CLI 写入：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{"index_layer":{...},"resolution_layer":{...},"metadata":{"severity":"P1",...}}' \
  --quality-json '{"scores":{...},"average":4.2,"issues":[],"pass":true}' \
  --platform {platform} \
  --module "{module}" \
  --source {source} \
  --output-format json
```

如果省略 `--quality-json`，CLI 将使用配置的 LLM Provider 做内部质量评估（高级路径，需要 API Key）。

强制写入时追加 `--force` 标记。

### Step 5: 更新索引

用户确认后，执行索引更新（默认使用本地 embedding，零 API Key）：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py index --output-format json
```

### Step 6: 联动（可选）

读取 `defect-kb.yaml` 的 `integrations` 配置：

- 如果 `write_context_skill` 非空（如 `write-dev-context`），提示用户：
  "是否同时将此踩坑记录写入 dev_context？"
- 联动是可选的，用户可以跳过。

## CLI 命令参考

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `init` | 初始化项目配置 | `--template {mobile\|web\|backend\|fullstack}`、`--install-skills`、`--force` |
| `govern` | 原始文本→Card | `--input` 或 `--json`、`--quality-json`、`--platform`、`--force`、`--auto-retry`、`--output-format` |
| `index` | Card→向量索引 | `--embedding-provider {openai\|local}`、`--output-format` |
| `search` | 语义检索 | `--query`、`--platform`、`--top-k`、`--min-similarity`、`--output-format {text\|json\|compact}` |
| `browse` | 按ID查看 | `--id`、`--output-format` |
| `stats` | 质量统计 | `--output-format` |

## 新项目接入

```bash
# 方式 1：使用模板快速初始化（推荐）
python {SKILL_DIR}/defect-kb/bootstrap.py init --template mobile --install-skills

# 方式 2：交互式自定义初始化
python {SKILL_DIR}/defect-kb/bootstrap.py init --install-skills
```

## 工作流引导（上下文感知提醒）

当项目根目录存在 `defect-kb.yaml` 时，以下场景应主动提供引导（每个场景同一会话仅提示一次，用户说"跳过"后不再提示）。

无 `defect-kb.yaml` 时所有引导静默跳过，不影响正常工作流。

### 首次使用引导

当 `defect-kb.yaml` 存在但 `defect-kb-data/cards.jsonl` 不存在或为空时，给出一次性提示：

> 检测到项目已配置缺陷知识库，但知识库当前为空。建议说 "治理缺陷数据" 批量导入，或说 "记录缺陷" 写入单条卡片。

### 场景 A：Bug 修复前 — 查知识库

触发：当前任务为 Bug 修复（用户提到报错、Bug、修复、排查、crash、异常等）。

> 建议先说 **"查缺陷库"** 或 **"搜踩坑"** 检索历史经验，可能加速定位。

### 场景 B：Bug 修复后 — 兜底沉淀提醒

触发：完成 Bug 修复且未经过已接线的 fix-bug Skill（即 post-fix-hook 未自动执行），且满足任一条件：排查经历 2+ 个失败假设 / 跨模块改动 / 出现"踩坑""原来是"等发现性语句 / 涉及并发/缓存/安全。

> 本次修复经验建议沉淀到缺陷知识库。说 **"记录缺陷"** 开始，或说 **"跳过"** 忽略。

### 场景 C：新功能开发 — 已知踩坑提示

触发：当前任务为新功能开发/模块迭代，且能识别到涉及的模块名。

> 开始 {module} 模块开发前，建议先说 **"查缺陷库"** 或 **"搜踩坑 {module}"** 了解已知踩坑。

### 场景 D：代码审查 — 检查已知模式

触发：当前正在执行 code-review 操作。

> 发现的问题可能是已知缺陷模式，可通过 **"搜踩坑 {问题关键词}"** 查找历史经验。

## 参考

- CLI 入口：`{SKILL_DIR}/defect-kb/bootstrap.py`（自动管理 venv + 依赖）
- CLI 工具：`{SKILL_DIR}/defect-kb/cli.py`
- Experience Card Schema：`{SKILL_DIR}/defect-kb/schema.py`
- 标准化 Prompt：`{SKILL_DIR}/defect-kb/prompts/standardize.txt`
- 质量检查 Prompt：`{SKILL_DIR}/defect-kb/prompts/quality_check.txt`
- 搜索 Skill：`{SKILL_DIR}/search-defect-kb.md`
- 治理 Skill：`{SKILL_DIR}/govern-defect-data.md`
- SOP：`{SKILL_DIR}/defect-kb-sop.md`
- 系统架构：`{SKILL_DIR}/architecture.md`
- 工作流引导规则（Cursor）：`templates/defect-kb.mdc`（由 `--install-skills` 自动安装到 `.cursor/rules/`）
