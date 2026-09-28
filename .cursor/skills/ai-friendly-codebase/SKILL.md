---
name: ai-friendly-codebase
description: >-
  spec.yaml 格式与校验：伪软件落成的模块契约与代码索引。触发：写/改 spec.yaml、按索引找代码。
---

# AI-Friendly Codebase

## 规则

1. 根目录与每个模块目录各一份 `spec.yaml`；模板 [templates/spec.yaml](templates/spec.yaml)。
2. 模块 = 父级 `modules` 登记的目录；未登记目录归属最近父模块。
3. 父级 `modules` 只写 `path` + `function`；其余字段只写在模块自己的 `spec.yaml`。
4. 禁止写实现细节、产品方案。
5. 改完运行 `python3 .cursor/skills/ai-friendly-codebase/scripts/check_index.py`；失败须修复。

## 字段

| 字段 | 必填 | 内容 |
|---|---|---|
| `name` | 是 | 模块名 |
| `function` | 是 | 一句话职责 |
| `modules` | 是 | 子模块 `path` + `function`；无则 `[]` |
| `input` | 是 | `name` / `type` / `desc`；无则 `[]` |
| `output` | 是 | `name` / `type` / `desc`；无则 `[]` |
| `contract` | 是，非空 | 行为保证、边界条件、失败场景 |
| `depends` | 是 | 仓库根相对路径；外部写 `ext:<名称>`；无则 `[]` |
| `tests` | 是，非空 | 独立测试；有子模块时另写组合测试 / Integration Test |
