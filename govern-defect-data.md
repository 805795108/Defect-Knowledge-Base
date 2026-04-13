---
name: govern-defect-data
description: >
  批量治理缺陷数据：从存量文件 / Issue Tracker（GitHub、云效、GitLab） / AI 主动发现等多数据源提取缺陷，
  标准化为 Experience Card 并建索引。
  Use when the user says "治理缺陷数据"、"迁移踩坑记录"、"govern defect data"、"批量导入缺陷"、
  "主动发现缺陷"、"proactive discovery".
---

# Govern Defect Data

批量治理缺陷数据的编排 Skill。从多种数据源提取原始缺陷信息，标准化为 Experience Card，建立向量索引。当所有被动数据源均无结果时，可调用 Superpowers 工具主动发现潜在缺陷。

## 前置条件

- 项目根目录存在 `defect-kb.yaml`（通过 `python {SKILL_DIR}/defect-kb/bootstrap.py init` 生成）
- Python 依赖通过 `bootstrap.py` 自动管理（自动创建 `defect-kb-data/.venv` 虚拟环境并安装依赖），无需手动 `pip install`
- **默认路径（零 API Key）**：Agent 自身 LLM 做标准化 + 质量评估（`--json` + `--quality-json`），CLI 只做校验+写入，向量索引使用本地 embedding，无需任何外部 API Key
- **高级路径（需 API Key）**：如需使用 CLI 内部 LLM（`--input` / `--auto-retry`），需配置对应 Provider 的 API Key

> `{SKILL_DIR}` = `.cursor/skills/defect-knowledge-base` 或 `.claude/skills/defect-knowledge-base`

- （Mode C — 按平台不同）：
  - **GitHub**：环境变量 `GITHUB_TOKEN` 已设置（优先）；或 `gh` CLI 已安装且已认证（fallback）
  - **云效 Yunxiao**：环境变量 `YUNXIAO_TOKEN`（个人访问令牌）已设置
  - **GitLab**：环境变量 `GITLAB_TOKEN`（Private Token）已设置
- （Mode D — D0）项目中已配置静态分析工具（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint）
- （Mode D — D1/D2/D3 AI 兜底）Superpowers 插件已安装（用于 AI 主动发现）

## 工作流程

### Step 0: 读取配置

读取 `defect-kb.yaml`，获取：
- `data_sources`：判断哪些被动数据源可用
- `proactive_discovery`：判断主动发现是否启用
- `proactive_discovery.trigger`：判断触发模式（`when_zero` / `always`）

### Step 1: 执行被动数据源（Mode AB / C）

按用户选择的数据源执行，跳过路径为空或不存在的数据源。

---

#### Mode AB: 内容源交互式选择

**条件**: `data_sources.content_sources` 列表非空（优先），或旧字段 `pitfalls_file` / `feature_context_glob` 非空（向后兼容）

**向后兼容**：当 `content_sources` 不存在时，从旧字段自动构建：
- `pitfalls_file` 非空 → 等效 `{extract_mode: split_by_heading, globs: [pitfalls_file]}`
- `feature_context_glob` 非空 → 等效 `{extract_mode: heading_keyword, globs: [feature_context_glob]}`

当 `content_sources` 存在时，忽略旧字段。

##### Step 1a: 展示可用内容源菜单

读取 `defect-kb.yaml` → `data_sources.content_sources`，向用户展示可选列表：

```
项目已配置以下内容源：

1. pitfalls — docs/pitfalls.md (split_by_heading)
2. evolution-lessons — docs/evolution/**/*.md (heading_keyword: 踩坑/教训/遗留问题/放弃方案)
3. feature-context — docs/features/**/*.md (heading_keyword: 踩坑/教训/根因)
4. design-risks — docs/design/**/*.md (heading_keyword: 风险/注意事项)
...

○ 选择内容源（可多选，如 "1,3"）
○ 手动指定文件路径（逗号分隔）
○ 全部跳过
```

使用 AskQuestion 让用户选择。用户也可以直接说文件路径（如 `docs/pitfalls.md`），Agent 自动匹配对应的 content_source 配置或按默认 `split_by_heading` 处理。

##### Step 1b: 处理用户选定的范围

仅对用户选中的 content_source 或文件执行提取：

1. 展开选中 source 的 `globs` 列表，匹配项目中的 Markdown 文件
2. 排除 `exclude_globs` 中匹配的文件（避免跨 source 重复）
3. 对每个匹配的文件，按 `extract_mode` 提取内容条目：

**`split_by_heading` 模式**：

适用于整个文件都是踩坑记录的专用文件（如 `pitfalls.md`、`FIX_PLAN*.md`）。

按 `heading_levels` 指定的标题级别（默认 `###`/`####`）分割文件为独立条目，每个条目包含标题 + 标题下的全部内容，作为一条原始文本。

**`heading_keyword` 模式**：

适用于大文档中只有部分段落包含踩坑内容的场景。

在文件中搜索标题文本匹配 `heading_patterns` 中任一关键词的段落（如标题包含"踩坑"、"教训"、"遗留问题"等），提取该标题下的完整内容（直到下一个同级或更高级标题）。

**去噪**：提取的段落少于 50 字符则跳过（排除空的模板占位符段落）。

4. Agent 将每个提取的条目标准化为 Experience Card JSON + 质量评估 JSON（默认路径，零 API Key）

5. 记录每个 source 的提取条目数

**配置示例**（`content_sources` 仍作为交互菜单来源，不再自动全量扫描）：

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
```

`init` 命令会自动扫描项目目录发现可用的内容源并生成预配置。`content_sources` 列表作为用户交互选择的菜单来源。

---

#### Mode C: Issue Tracker 导入（多平台 · 增量同步）

**条件**: `data_sources.issue_trackers` 列表非空

遍历 `issue_trackers` 列表中每个 tracker 配置，按 `type` 分别执行。

**增量同步机制**：

Mode C 支持增量同步，避免每次 govern 都全量拉取所有历史 issues。同步状态保存在 `defect-kb-data/sync-state.json`：

```json
{
  "issue_trackers": {
    "github:owner/repo": "2026-04-09T12:00:00Z",
    "gitlab:group/project": "2026-04-08T10:00:00Z",
    "yunxiao:org_id/project_id": "2026-04-07T08:00:00Z"
  }
}
```

每个 tracker 以 `{type}:{唯一标识}` 为 key，值为 ISO 8601 时间戳。执行流程：

1. 读取 `sync-state.json`（不存在则视为首次全量同步）
2. 查找当前 tracker 的 `last_imported` 时间戳
3. API 请求带 `since` / `updated_after` 参数，只拉取 `last_imported` 之后的 issues
4. 所有 issues 处理成功后，更新该 tracker 的时间戳为当前时间并写回 `sync-state.json`
5. 如需强制全量重新导入，删除 `sync-state.json` 或移除对应 tracker 的 key 即可

**兼容旧配置**：如果 `issue_trackers` 不存在但 `data_sources.github_bug_label` 非空，自动当作 `type: github` 处理，`repo` 取 `project.repo`，`bug_label` 取 `github_bug_label`。

---

##### C1: GitHub

**条件**: tracker `type` = `github`

**认证**: 环境变量 `{tracker.env_token}`（默认 `GITHUB_TOKEN`）；若 token 不存在，fallback 到 `gh` CLI

**增量同步 key**: `github:{tracker.repo}`

**路径 A（优先）**：Token + REST API

检查环境变量 `${tracker.env_token}` 是否有值，如有则用 REST API。增量同步时附加 `since` 参数：

```bash
# 首次全量
curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{tracker.repo}/issues?labels={tracker.bug_label}&state={tracker.state}&per_page={tracker.limit}"

# 增量同步（since = sync-state.json 中的 last_imported）
curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{tracker.repo}/issues?labels={tracker.bug_label}&state={tracker.state}&per_page={tracker.limit}&since={last_imported}"
```

**路径 B（Fallback）**：gh CLI

若 token 环境变量为空，则 fallback 到 `gh` CLI（需已执行 `gh auth login`）：

```bash
gh issue list --repo {tracker.repo} \
  --label {tracker.bug_label} \
  --state {tracker.state} \
  --limit {tracker.limit} \
  --json number,title,body,labels,closedAt
```

> 注意：gh CLI 无原生 `since` 参数，增量同步时 Agent 需在结果中按 `closedAt` / `updatedAt` 过滤。

对每条 issue，拼接 title + body 作为原始文本，调用 CLI govern（source = `github-issue`）

---

##### C2: 云效 Yunxiao

**条件**: tracker `type` = `yunxiao`

**增量同步 key**: `yunxiao:{tracker.organization_id}/{tracker.project_id}`

**认证**: 环境变量 `{tracker.env_token}`（默认 `YUNXIAO_TOKEN`）中存储个人访问令牌

1. 调用云效 REST API 获取已完成的缺陷工作项列表。增量同步时附加 `gmtModifiedAfter` 参数：

```bash
# 首次全量
curl -s -H "x-yunxiao-token: ${YUNXIAO_TOKEN}" \
  "{tracker.base_url}/oapi/v1/projex/organizations/{tracker.organization_id}/listWorkitems?category=Bug&spaceType=Project&spaceIdentifier={tracker.project_id}&maxResults={tracker.limit}"

# 增量同步
curl -s -H "x-yunxiao-token: ${YUNXIAO_TOKEN}" \
  "{tracker.base_url}/oapi/v1/projex/organizations/{tracker.organization_id}/listWorkitems?category=Bug&spaceType=Project&spaceIdentifier={tracker.project_id}&maxResults={tracker.limit}&gmtModifiedAfter={last_imported}"
```

2. 从返回的 `workitems` 数组中筛选 `logicalStatus` 为已完成/已关闭的条目
3. 对每条工作项，获取详情（因列表 API 的 `content` 字段已废弃）：

```bash
curl -s -H "x-yunxiao-token: ${YUNXIAO_TOKEN}" \
  "{tracker.base_url}/oapi/v1/projex/organizations/{tracker.organization_id}/workitems/{workitem.identifier}"
```

4. 拼接 `subject`（标题）+ 详情中的 `description`（描述）作为原始文本
5. 调用 CLI govern（source = `yunxiao-issue`）

---

##### C3: GitLab

**条件**: tracker `type` = `gitlab`

**增量同步 key**: `gitlab:{tracker.project_url_encoded}`

**认证**: 环境变量 `{tracker.env_token}`（默认 `GITLAB_TOKEN`）中存储 Private Token

1. 调用 GitLab REST API 获取已关闭的 bug issues。增量同步时附加 `updated_after` 参数：

```bash
# 首次全量
curl -s -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "{tracker.base_url}/api/v4/projects/{tracker.project_url_encoded}/issues?labels={tracker.bug_label}&state={tracker.state}&per_page={tracker.limit}"

# 增量同步
curl -s -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "{tracker.base_url}/api/v4/projects/{tracker.project_url_encoded}/issues?labels={tracker.bug_label}&state={tracker.state}&per_page={tracker.limit}&updated_after={last_imported}"
```

2. 对每条 issue，拼接 `title` + `description` 作为原始文本
3. 调用 CLI govern（source = `gitlab-issue`）

---

#### Mode E: Git History Mining

**条件**: `data_sources.git_history.enabled` = `true`

从 git 历史中筛选 bug-fix 类型的 commit，提取 message + diff 作为原始文本。适用于老项目冷启动。

##### 分支选择策略（双层过滤）

Mode E 采用"先筛分支、再筛 commit"的双层过滤策略，避免 `--all` 扫描全部分支引入噪声和重复：

| 层 | 过滤方式 | 作用 |
|----|---------|------|
| **第一层：分支名过滤** | 默认分支 + 名称匹配 bug-fix 模式的分支 | 缩小搜索范围，排除无关 feature/实验分支 |
| **第二层：commit message 过滤** | `--grep` 关键词匹配 | 在选定分支中精准筛选 fix 类 commit |

**分支匹配模式**（通过 `git_history.branches.patterns` 配置）：

| 模式 | 匹配示例 |
|------|---------|
| `bf-*` | `bf-login-crash`（bugfix 分支） |
| `hf-*` | `hf-urgent-fix`（hotfix 分支） |
| `*bugfix*` | `ft-20260330-bugfix`、`release-bugfix-v2` |
| `*fix*` | `hotfix/v2.1.1`、`fix/null-pointer`、`patch-fix-xxx` |

**配置示例**：

```yaml
git_history:
  enabled: true
  branches:
    default: true              # 始终包含默认分支（自动检测 main/master）
    patterns:                  # 分支名匹配模式
      - "bf-*"
      - "hf-*"
      - "*bugfix*"
      - "*fix*"
    include_all: false         # true 则退化为 --all（全量扫描）
  keywords:
    - fix
    - bug
    - hotfix
    - patch
    - 修复
    - 缺陷
  limit: 100
```

##### 执行步骤

1. 确定扫描分支范围：

```bash
# 检测默认分支
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
  | sed 's|refs/remotes/origin/||') || DEFAULT_BRANCH="main"

# 按 patterns 匹配 bug-fix 分支（本地 + remote）
BUGFIX_BRANCHES=$(git branch -a --format='%(refname:short)' \
  | grep -E '(^bf-|^hf-|bugfix|fix)')
```

如果 `include_all: true`，则跳过上述步骤，直接使用 `--all`。

2. 在选定分支中按关键词筛选 commit：

```bash
git log $DEFAULT_BRANCH $BUGFIX_BRANCHES \
  --grep="fix\|bug\|hotfix\|patch\|修复\|缺陷" \
  --format="%H %s" | head -n {git_history.limit}
```

3. 对每条匹配的 commit，获取 message + 变更文件列表 + 简要 diff：

```bash
git show {commit_hash} --stat --format="%B"
```

4. 拼接 commit message + 变更文件列表作为原始文本
5. 从变更文件路径推断 platform 和 module
6. 调用 CLI govern（source = `git-history`，confidence 自动设为 `likely`）

---

#### Mode F: Code Comment Mining

**条件**: `data_sources.code_comments.enabled` = `true`

扫描代码库中的 TODO/FIXME/HACK/WORKAROUND/XXX 注释，提取为潜在缺陷。适用于老项目冷启动。

1. 用 ripgrep 按标记搜索代码注释：

```bash
rg "(TODO|FIXME|HACK|WORKAROUND|XXX)" \
  --glob "*.{swift,kt,ts,tsx,java,py,go}" \
  -C 3 --max-count {code_comments.limit}
```

2. 对每条匹配提取：注释文本 + 所在文件路径 + 上下文代码（前后 3 行）
3. 从文件路径推断 platform 和 module
4. 调用 CLI govern（source = `code-comment`，confidence 自动设为 `hypothesis`）

---

### Step 1.5: 人工确认待写入卡片

所有被动数据源（Mode AB / C / E / F）提取并标准化的卡片，在调用 CLI 写入前，必须经用户确认。

**单条确认**（条目 < 5 条时）：

逐条展示卡片摘要，使用 AskQuestion：

```
待写入卡片 [{n}/{total}]:

问题摘要: {problem_summary}
信号词: {signals}
严重度: {severity}
质量分: {average}/5.0

○ 确认写入
○ 修改后写入（请说明修改点）
○ 跳过此条
```

**批量确认**（条目 >= 5 条时）：

先展示全部摘要列表，用户可批量确认或逐条审核：

```
本次提取 {total} 条缺陷卡片待确认：

1. [P1] {problem_summary_1} | {platform} | {quality_avg}
2. [P2] {problem_summary_2} | {platform} | {quality_avg}
3. [P1] {problem_summary_3} | {platform} | {quality_avg}
...

○ 全部确认写入
○ 逐条审核
○ 按编号选择写入（如 "1,3,5"）
○ 全部跳过
```

用户确认后，对选中的卡片调用 CLI 写入：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{card_json}' \
  --quality-json '{quality_json}' \
  --platform {platform} \
  --module "{module}" \
  --source {source_tag} \
  --output-format json
```

### Step 2: 统计被动数据源结果

汇总 Mode AB + C + E + F 的卡片产出数量（仅统计用户确认写入的卡片）。

```
被动数据源治理结果:
  Mode AB (content sources):
    pitfalls: {pitfalls_count} 张卡片（已确认写入）
    evolution-lessons: {evo_count} 张卡片
    feature-context: {feat_count} 张卡片
    design-risks: {risk_count} 张卡片
    ...（按 content_sources 列表逐项列出）
  Mode C (issue trackers):
    C1 GitHub: {c1_count} 张卡片
    C2 Yunxiao: {c2_count} 张卡片
    C3 GitLab: {c3_count} 张卡片
  Mode E (git history): {e_count} 张卡片
  Mode F (code comments): {f_count} 张卡片
  用户跳过: {skipped_count} 张
  合计入库: {total} 张卡片
```

### Step 3: 判断是否进入 Mode D

```
IF proactive_discovery.enabled == false:
    跳过 Mode D
ELIF proactive_discovery.trigger == "when_zero" AND total > 0:
    跳过 Mode D（被动数据源已有结果）
ELIF proactive_discovery.trigger == "when_zero" AND total == 0:
    进入 Mode D（被动数据源无结果，需主动发现）
ELIF proactive_discovery.trigger == "always":
    进入 Mode D（追加执行，不论被动数据源是否有结果）
```

如果跳过 Mode D，直接进入 Step 5。

---

### Step 4: Mode D — 主动发现（工具优先，AI 兜底）

> Mode D 采用"工具优先，AI 兜底"策略：先运行项目已有的静态分析工具（D0），
> 产出不足时再 fallback 到 AI 方法（D1/D2/D3）。

---

#### D0: 静态分析工具报告采集

**条件**: `proactive_discovery.static_analysis.enabled` = true

> 利用项目已配置的确定性代码分析工具（PMD、CheckStyle、SpotBugs、SwiftLint、ESLint、ktlint）
> 发现系统性代码质量问题。零 LLM token 成本，可重复、确定性的发现。
> 所有 D0 产出的卡片标记 `source: static-analysis`，`discovery_method` 为工具名。

##### D0a: 自动检测可用工具

读取 `proactive_discovery.static_analysis.tools` 列表。如果 `auto_detect` = true 且 `tools` 为空，
扫描项目结构自动检测（同 `init` 的 `_detect_tools()` 逻辑）。

支持的工具清单：

| 平台 | 工具 | 检测依据 | 报告格式 |
|------|------|---------|---------|
| backend | PMD | `pom.xml` 含 `maven-pmd-plugin` | `pmd-xml` |
| backend | CheckStyle | `pom.xml` 含 `maven-checkstyle-plugin` | `checkstyle-xml` |
| backend | SpotBugs | `pom.xml` 含 `spotbugs-maven-plugin` | `spotbugs-xml` |
| ios | SwiftLint | `.swiftlint.yml` 存在 | `swiftlint-json` |
| web | ESLint | `.eslintrc.json` 存在 | `eslint-json` |
| harmony | ESLint | `.eslintrc.json` 存在 | `eslint-json` |
| android | ktlint | `build.gradle.kts` 含 `org.jlleitschuh.gradle.ktlint` | `ktlint-text` |

##### D0b: 运行工具 / 读取已有报告

三种 `mode` 可选：

| mode | 行为 |
|------|------|
| `run` | 总是执行 `command` 生成新报告 |
| `report` | 仅读取 `report_glob` 对应的已有文件，不运行工具 |
| `auto`（默认） | 检查 `report_glob` 文件是否存在且修改时间 < `report_max_age_hours`，有则读取，无则运行 |

对每个 tool 配置：

1. `cd {working_dir}` 进入工具所在目录
2. 检查报告文件是否存在（`auto` / `report` 模式）
3. 如需运行：执行 `command`，超时 5 分钟，失败不阻断其他工具
4. 读取报告内容

##### D0c: 解析报告 → 归一化 Finding

对每个工具的报告，调用对应的解析器（`_parse_report()`），统一输出 `NormalizedFinding`：

```
NormalizedFinding:
  tool: "pmd" / "checkstyle" / "spotbugs" / "swiftlint" / "eslint" / "ktlint"
  platform: "backend" / "ios" / "web" / "harmony" / "android"
  rule_id: 规则 ID（如 "CyclomaticComplexity"、"line_length"）
  severity: "error" / "warning" / "info"
  message: 原始描述
  file_path: 相对文件路径
  line: 行号
  category: 规则分类（如 "design"、"errorprone"、"style"）
```

##### D0d: 按规则聚合 + 趋势分析

1. 按 `(tool, rule_id)` 分组统计出现次数
2. 过滤：severity < `min_severity`（默认 warning）的 finding 丢弃
3. 过滤：同一规则出现 < `aggregate_threshold`（默认 3）次的个案不建卡
4. 计算"热度排名"：`count × severity_weight`（error=3, warning=2, info=1）
5. 按热度降序排序

##### D0e: LLM 泛化为 Experience Card

对每个聚合组（按热度排序取 Top-N）：

1. 取代表性 finding（最多 5 条不同 message + 5 个不同文件路径）
2. 构造 Prompt（使用 `defect-kb/prompts/proactive_static.txt`）
3. 让 Agent LLM 基于工具名、规则 ID、代表性 message 泛化为 Experience Card
4. 调用 CLI 标准化：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --json '{generalized_card_json}' \
  --quality-json '{quality_scores}' \
  --platform {platform} \
  --module "{module}" \
  --source static-analysis \
  --confidence likely \
  --discovery-method {tool_name}
```

##### D0 汇总

```
D0 静态分析结果:
  检测到工具: {tool_count} 个
  原始 Finding 总数: {raw_count} 条
  聚合后规则组: {group_count} 组（阈值 >= {threshold}）
  泛化为卡片: {card_count} 张 (source: static-analysis)
  
  工具明细:
    pmd(backend): {pmd_raw} 条 → {pmd_groups} 组 → {pmd_cards} 张
    checkstyle(backend): {cs_raw} 条 → {cs_groups} 组 → {cs_cards} 张
    ...
```

---

#### D0→D1 兜底判定

```
IF d0_card_count >= methods.min_static_findings:
    跳过 D1/D2/D3（静态工具已足够）
ELIF methods.ai_fallback == "always":
    继续执行 D1/D2/D3
ELIF methods.ai_fallback == "when_d0_insufficient":
    继续执行 D1/D2/D3（D0 产出不足）
ELIF methods.ai_fallback == "never":
    跳过 D1/D2/D3（纯工具模式）
```

---

#### D1: Code Review 发现（AI 兜底）

**对应 Superpowers**: `requesting-code-review` + `code-reviewer` subagent

**条件**: `methods.items` 中存在 `type: code-review` 且 D0→D1 兜底判定通过

**D1 增强**：如果 `rule_context.enabled` = true，将 `rule_context.sources` 中的 `.mdc` 规则文件摘要
注入 D1 的 code review prompt，让 AI 按项目特定编码标准审查。

1. 确定审查范围：
   - 使用配置的 `scope`（如 `git diff HEAD~20`）
   - 如果项目无 commit 历史，扫描核心目录文件

2. 读取 `rule_context.sources` 中的规则文件，提取关键编码标准作为审查增强上下文

3. 按项目模块并行分发 code review：
   - 使用 Superpowers `dispatching-parallel-agents` 并行审查
   - 或使用项目已有的 `code-review` Skill

4. 收集审查结果，过滤低严重度：
   - 只保留 >= `min_severity`（默认 Important）的发现
   - 丢弃 Minor / Nice-to-have 级别

5. 每条发现调用 CLI 标准化：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --input "{finding_text}" \
  --platform {platform} \
  --module "{module}" \
  --source ai-proactive \
  --confidence likely \
  --discovery-method code-review
```

---

#### D2: Business Rule Audit（AI 兜底）

**对应 Superpowers**: `systematic-debugging`（Phase 1 root cause tracing）

**条件**: `methods.items` 中存在 `type: business-rule-audit` 且 `rules_file` 非空，D0→D1 兜底判定通过

1. 读取业务规则文件（如 `docs/business-rules.md`）
2. 逐条业务规则在对应实现代码中搜索：
   - 规则完全无实现 → 功能缺失
   - 实现逻辑与规则矛盾 → 逻辑错误
   - 错误码不一致 → 合约偏差
   - 跨模块副作用遗漏 → 集成缺陷
3. 对照合约文件（如 `contracts/api-contract.yaml`）交叉验证
4. 每条发现调用 CLI 标准化（source = `ai-proactive`, confidence = `likely`）

---

#### D3: Brainstorm Edge Cases（AI 兜底）

**对应 Superpowers**: `brainstorming`

**条件**: `methods.items` 中存在 `type: brainstorm-edge-case`，D0→D1 兜底判定通过

1. 读取 `focus_areas` 列表和 `modules.examples`
2. 对每个 (module, focus_area) 组合：
   - 构造 Prompt（使用 `defect-kb/prompts/proactive_brainstorm.txt`）
   - 让 AI 基于该模块和焦点领域推测潜在缺陷
   - 要求：问题假设 + 触发条件 + 可能根因 + 验证方法
3. 每条假设调用 CLI 标准化：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py govern \
  --input "{hypothesis_text}" \
  --platform {platform} \
  --module "{module}" \
  --source ai-proactive \
  --confidence hypothesis \
  --discovery-method brainstorm-edge-case
```

4. 受 `max_cards` 限制（默认 10），超出则按质量评分排序截断

---

#### D-Gate: Mode D 质量门禁

Mode D 产出的所有卡片（D0 + D1/D2/D3）在入库前，必须通过额外校验：

1. **标准质量检查**：同 Mode A/B/C，6 维度评分
2. **可验证性检查**：verification_plan 是否描述了可实际执行的验证步骤
3. **去重检查**：与已有卡片语义比对（ChromaDB 余弦相似度 > 0.9 则丢弃重复）
4. **最低质量分**：6 维度平均分 >= `quality_gate.min_quality_score`（默认 3.5）

```
Mode D 质量门禁结果:
  通过: {pass_count} 张
  低分淘汰: {low_score_count} 张
  重复淘汰: {dup_count} 张
```

5. **用户确认**（如果 `quality_gate.require_user_confirm` = true）：

```
以下 {pass_count} 张主动发现的缺陷卡片待确认：

1. [likely] {problem_summary_1}
   发现方式: pmd (static-analysis) | 平台: {platform} | 质量分: {score}
   ○ 确认  ○ 修改  ○ 丢弃

2. [likely] {problem_summary_2}
   发现方式: code-review (ai-proactive) | 平台: {platform} | 质量分: {score}
   ○ 确认  ○ 修改  ○ 丢弃

3. [hypothesis] {problem_summary_3}
   发现方式: brainstorm (ai-proactive) | 平台: {platform} | 质量分: {score}
   ○ 确认  ○ 修改  ○ 丢弃
```

- 用户「确认」→ confidence 升级为 `confirmed`
- 用户「修改」→ 编辑后入库
- 用户「丢弃」→ 不入库

如果 `require_user_confirm` = false，自动入库但 confidence 保持原值不升级。

---

### Step 5: 批量索引

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py index
```

### Step 6: 汇报治理结果

```
=== 缺陷数据治理完成 ===

被动数据源:
  Mode AB (content sources):
    pitfalls: {pitfalls} 张
    evolution-lessons: {evo} 张
    feature-context: {feat} 张
    ...（按 content_sources 列表逐项列出）
  Mode C (issue trackers):
    C1 GitHub: {c1} 张
    C2 Yunxiao: {c2} 张
    C3 GitLab: {c3} 张
  Mode E (git history): {e} 张 (confidence: likely)
  Mode F (code comments): {f} 张 (confidence: hypothesis)

主动发现 (Mode D):
  D0 static-analysis:
    pmd(backend): {pmd} 张 (confidence: likely)
    checkstyle(backend): {cs} 张 (confidence: likely)
    spotbugs(backend): {sb} 张 (confidence: likely)
    swiftlint(ios): {sl} 张 (confidence: likely)
    eslint(web): {el_web} 张 (confidence: likely)
    eslint(harmony): {el_harm} 张 (confidence: likely)
    ktlint(android): {kt} 张 (confidence: likely)
  D1 code-review: {d1} 张 (confidence: likely)
  D2 business-rule-audit: {d2} 张 (confidence: likely)
  D3 brainstorm-edge-case: {d3} 张 (confidence: hypothesis)
  质量门禁淘汰: {rejected} 张

合计入库: {total_indexed} 张
索引状态: {index_count} 张已索引
同步状态: sync-state.json 已更新
```

## Superpowers 工具映射

| Mode D 阶段 | 工具/Superpowers Skill | 用法 |
|---|---|---|
| D0 Static Analysis | 项目已有工具（PMD/CheckStyle/SpotBugs/SwiftLint/ESLint/ktlint） | 运行工具 → 解析报告 → 聚合 → LLM 泛化 |
| D1 Code Review | `requesting-code-review` + `code-reviewer` + `.mdc` 规则增强 | 按模块并行分发审查，注入项目编码标准 |
| D2 Business Rule Audit | `systematic-debugging` | 逐条规则追踪实现代码 |
| D3 Brainstorm | `brainstorming` | 按 focus_area 推测潜在缺陷 |
| D-Gate 验证 | `verification-before-completion` | 验证卡片可操作性 |

## 参考

- CLI 工具：`{SKILL_DIR}/defect-kb/bootstrap.py`
- Experience Card Schema：`{SKILL_DIR}/defect-kb/schema.py`
- 标准化 Prompt：`{SKILL_DIR}/defect-kb/prompts/standardize.txt`
- Static Analysis Prompt：`{SKILL_DIR}/defect-kb/prompts/proactive_static.txt`
- Code Review Prompt：`{SKILL_DIR}/defect-kb/prompts/proactive_review.txt`
- Audit Prompt：`{SKILL_DIR}/defect-kb/prompts/proactive_audit.txt`
- Brainstorm Prompt：`{SKILL_DIR}/defect-kb/prompts/proactive_brainstorm.txt`
- 质量检查 Prompt：`{SKILL_DIR}/defect-kb/prompts/quality_check.txt`
