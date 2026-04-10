---
name: search-defect-kb
description: >
  在缺陷知识库中语义检索相似踩坑经验，辅助当前问题的诊断和修复。
  触发词："查缺陷库"、"搜踩坑"、"search defect"、"有没有类似的坑"、"查知识库"
domain: qa
---

# Search Defect Knowledge Base

在项目的缺陷知识库中进行语义检索，找到与当前问题最相关的历史踩坑经验，帮助快速诊断和修复。

## 前置条件

- 项目根目录存在 `defect-kb.yaml`
- `defect-kb-data/chroma_db/` 中有已索引的数据（至少执行过一次 `govern` + `index`）
- **默认路径（零 API Key）**：使用本地 embedding（`sentence-transformers`），无需任何外部 API Key
- **高级路径**：如需使用 OpenAI embedding，需设置环境变量 `OPENAI_API_KEY`

## 工作流程

### Step 0: 读取配置，确认数据可用

读取 `defect-kb.yaml`，检查 `data.chroma_path` 对应目录存在且非空。

如果无数据：
```
缺陷知识库为空。请先执行以下操作之一：
- "治理缺陷数据"（批量导入存量踩坑记录）
- "记录缺陷"（手动写入单条卡片）
```

### Step 1: 构造检索 Query

从以下来源提取检索关键信息：

1. **用户直接输入**：如 "有没有 fullScreenCover 导致输入丢失的坑"
2. **当前任务描述**：从对话上下文提取正在处理的问题
3. **报错信息**：如果用户粘贴了错误日志，提取关键错误信息

将提取的信息拼接为检索 query。

### Step 2: 执行检索

**标准模式**（手动触发，完整展示）：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py search \
  --query "{query}" \
  --platform {platform} \
  --top-k 5
```

**Compact 模式**（自动注入用，精简输出）：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py search \
  --query "{query}" \
  --platform {platform} \
  --top-k 3 \
  --min-similarity 0.3 \
  --output-format compact
```

Compact 输出每条一行：`[{id}|{severity}] {problem_summary} → {fix_strategy}`，专为上下文注入设计（~100-150 tokens/条）。`--min-similarity` 过滤低相关度结果。

> `{SKILL_DIR}` = `.cursor/skills/defect-knowledge-base` 或 `.claude/skills/defect-knowledge-base`

`--platform` 从当前上下文推断（如正在处理 iOS 代码则为 `ios`），或省略不过滤。

### Step 3: 展示结果（含置信度标记）

对检索到的卡片，按相关度排序展示：

```
搜索结果 ({count} 条):

1. [confirmed] DEF-20260318-001 | fullScreenCover 内联创建 ViewModel 导致输入丢失
   平台: ios | 模块: M014-playback-control | 相关度: 0.92

2. [likely] DEF-20260409-003 | Toggle 操作计数非原子更新
   平台: backend | 模块: M005-comment | 相关度: 0.85
   ⚠️ 来源: AI code-review 主动发现，未经人工确认

3. [hypothesis] DEF-20260409-007 | 高并发下缓存击穿
   平台: backend | 模块: M009-home-feed | 相关度: 0.78
   ⚠️ 来源: AI brainstorm 推测，需验证
```

**置信度标记规则：**

| 标记 | 含义 | 展示方式 |
|------|------|---------|
| `[confirmed]` | 人工确认的真实缺陷 | 正常展示 |
| `[likely]` | AI 静态分析发现，高置信度 | 加 ⚠️ 提示来源 |
| `[hypothesis]` | AI brainstorm 推测 | 加 ⚠️ 提示需验证 |

### Step 4: 展开高相关卡片

对相关度 > 0.8 的卡片，自动调用 `browse` 获取完整 Resolution Layer：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py browse --id {card_id}
```

向用户展示完整的修复策略、根因分析、验证方案。

### Step 5: 应用建议

基于检索到的经验卡片，向用户提供：

1. **直接参考**：如果当前问题与某张卡片高度匹配，建议直接参考其 fix_strategy
2. **类比借鉴**：如果是类似模式但不完全相同，提取可迁移的修复思路
3. **排除已知坑**：如果 abandoned_approaches 中有用户正在尝试的方案，及时预警

### Step 6: 记录检索结果反馈

检索完成后，根据用户对结果的实际使用情况，调用 `log-event` 记录 `search_outcome` 事件：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py log-event \
  --action-type search_outcome \
  --data '{"query": "{query}", "top_card_id": "{best_match_id}", "outcome": "{applied|viewed|ignored|no_results}", "platform": "{platform}"}'
```

**outcome 取值：**

| outcome | 触发条件 |
|---------|---------|
| `applied` | 用户采纳了检索结果中的修复策略 |
| `viewed` | 用户查看了卡片详情但未直接采纳 |
| `ignored` | 返回了结果但用户未使用 |
| `no_results` | 检索无命中 |

> 此事件为"知识库价值"报告的核心数据源（Section 8 检索应用率）。

## 被其他 Skill 调用

此 Skill 可被其他 Skill 编排调用。联动方式由 `defect-kb.yaml` 的 `integrations` 驱动：

- `ios-fix-bug-ui` / `web-fix-bug-ui` 修复 bug 前，先查知识库看是否有类似经验
- `code-review` 发现问题后，查知识库看是否是已知模式

有配就联动，没配就独立工作。

## 参考

- CLI 工具：`{SKILL_DIR}/defect-kb/bootstrap.py`
- Experience Card Schema：`{SKILL_DIR}/defect-kb/schema.py`
