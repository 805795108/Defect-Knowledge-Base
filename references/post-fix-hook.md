# Post-Fix Hook：Bug 修复后自动触发经验沉淀

> 本文件定义了 Bug 修复完成后自动触发缺陷卡片沉淀的完整逻辑。
> 由上游 Skill / Rule 在修复验证通过后引用执行，无需用户手动触发。

## 支持的上游入口

| 上游 Skill / Rule | 平台 | 引用时机 |
|---|---|---|
| `ios-fix-bug-ui` | iOS | Step 4 验证与回归通过后 |
| `web-fix-bug-ui` | Web | Step 4 验证与回归通过后 |
| `backend-dev-lifecycle` | Backend | BugFix Shortcut 第 4 步 Run tests 通过后 |
| `backend-workflow.mdc` (Judge) | Backend | Judge 验收通过、修复轮结束后 |

## 执行流程

### Step 1：前置检查

1. 检查项目根目录是否存在 `defect-kb.yaml`
   - 不存在 → 跳过，不提示，不影响上游 Skill 正常结束
2. 读取 `defect-kb.yaml` 的 `integrations.fix_bug_skills`
3. 检查当前上游 Skill 名称是否在列表中
   - 不在 → 跳过

### Step 2：沉淀必要性评估

满足以下**任一条件**即触发提示（否则静默跳过）：

| 条件 | 适用端 | 说明 |
|------|--------|------|
| 排查经历 2+ 个失败假设 | 全端 | 对话中有 2 个以上被排除的根因方向 |
| 修复涉及跨模块改动 | 全端 | 改动文件跨越 2 个以上功能模块 |
| Bug 严重度为 P0/P1 | 全端 | 核心流程阻断或重要功能降级 |
| 对话中出现发现性语句 | 全端 | "踩坑"、"原来是"、"没想到"、"居然是"等 |
| Judge 循环 >= 2 轮 | Backend | Worker 修复被 Judge 打回 2 次以上 |
| 修复涉及并发/缓存/安全 | 全端 | 高价值经验，值得沉淀 |

### Step 3：弹出确认提示

向用户展示以下提示（从对话上下文自动预填）：

```
Bug 已修复并验证通过。

本次修复经验建议沉淀到缺陷知识库：
  问题: {从对话提取的一句话问题摘要}
  平台: {从上游 Skill 推断: ios / web / backend}
  模块: {从改动文件路径匹配 defect-kb.yaml 的 modules.examples}
  根因: {从对话中的根因结论提取}

○ 记录缺陷（调用 defect-knowledge-base 沉淀经验卡片）
○ 跳过
```

### Step 3.5：记录修复会话事件

无论用户是否选择沉淀，都记录本次修复会话的上下文指标：

```bash
python {SKILL_DIR}/defect-kb/bootstrap.py log-event \
  --action-type fix_session \
  --data '{"platform": "{platform}", "module": "{module}", "kb_searched": {true|false}, "kb_card_applied": {true|false}, "hypotheses_tried": {N}, "severity": "{P0|P1|P2}"}'
```

| 字段 | 说明 |
|------|------|
| `kb_searched` | 本次修复流程中是否执行了 `search-defect-kb` |
| `kb_card_applied` | 是否采纳了知识库卡片中的修复策略 |
| `hypotheses_tried` | 排查过程中尝试的假设总数（含最终成功的） |
| `severity` | 从上游 Skill 或 Issue 标签推断 |

> 此事件为"知识库价值"报告的核心数据源（Section 8 修复参考率 & 假设减少率）。

### Step 4：根据用户选择执行

**用户选择「记录缺陷」：**

调用 `defect-knowledge-base` Skill，传递预填上下文：

- `phenomenon`：问题现象（从上游 Step 1 / Locate 提取）
- `root_cause`：根因（从上游 Step 2 / 根因定位提取）
- `fix_strategy`：修复方案（从上游 Step 3 / Fix 提取）
- `abandoned_approaches`：失败尝试（从对话中排除的假设）
- `platform`：ios / web / backend
- `module`：从改动文件路径推断
- `source`：agent-transcript

defect-knowledge-base 收到预填数据后，Step 1 直接使用（用户只需确认或补充），无需重新提取。

**用户选择「跳过」：**

结束，不影响上游 Skill 输出。

## 预填字段提取规则

| 字段 | 提取来源 | 回退策略 |
|------|---------|---------|
| 问题摘要 | 上游 Step 0/1 中的问题重述 | 从用户首条消息提取 |
| 平台 | 上游 Skill 名称推断 | 从改动文件路径推断 |
| 模块 | 改动文件路径 vs `modules.examples` | 让用户指定 |
| 根因 | 上游 Step 2 的根因结论 | 从对话最后确认的假设提取 |
| 修复方案 | 上游 Step 3 的改动描述 | 从 git diff 摘要 |
| 失败尝试 | 对话中被排除的假设 | 留空 |
| 严重度 | 上游判定或 Issue 标签 | 默认 P2，用户可改 |

## 上游引用方式

上游 Skill / Rule 只需在修复验证通过的位置加一行：

```markdown
修复验证通过后，执行 [post-fix-hook](../defect-knowledge-base/references/post-fix-hook.md) 经验沉淀检查。
```

对于 `.cursor/rules/*.mdc`（非 Skill），引用为：

```markdown
修复验证通过后，读取并执行 `.cursor/skills/defect-knowledge-base/references/post-fix-hook.md` 经验沉淀检查。
```
