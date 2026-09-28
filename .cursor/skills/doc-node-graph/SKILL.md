---
name: doc-node-graph
description: >-
  写 docs（推理 + 技术）与伪软件。触发：探讨方案、写/改 docs、设计模块。
---

# Doc Node Graph

## docs 页

一个能力一页：`docs/<能力>/README.md`；根页 `docs/README.md`。

```markdown
# <能力名>

## 目标

## 推理

- 需求、约束、失败场景
- 推导过程

## 技术

- 选定方案与理由
- 未选定候选标「未选定」

## 伪软件

见下节。
```

- 未裁定内容禁止入档。
- 子能力需独立理解时才建子页，并在父页链接。

## 伪软件

docs 裁定后写在对应 docs 页：

```markdown
## 伪软件

| 模块 | 输入 | 输出 | 契约 | 依赖 | 独立测试 |
|---|---|---|---|---|---|
| … | … | … | … | … | … |

### 组合测试

### Integration Test
```

1. 一个模块 = 一个可独立测试、边界明确的责任单元。
2. 契约写行为保证、边界条件、失败场景。
3. 裁定后按 `ai-friendly-codebase` 生成各模块 `spec.yaml`，本节表格替换为 `spec.yaml` 链接。
4. 此后改契约只改 `spec.yaml`，同样须裁定。
