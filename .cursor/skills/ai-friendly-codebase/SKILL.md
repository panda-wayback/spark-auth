---
name: ai-friendly-codebase
description: >-
  用递归 spec.yaml + AI.md 索引代码，减少 Agent 读代码量。触发：写/审 spec.yaml 或 AI.md、按索引找代码、判上层是否过厚。
---

# AI-Friendly Codebase

## 必须

1. 根目录与每个模块目录各有一份 `spec.yaml` + `AI.md`：
   - `spec.yaml`：结构化接口，随契约更新。模板 [templates/spec.yaml](templates/spec.yaml)，细则 [spec-yaml.md](spec-yaml.md)。
   - `AI.md`：概括 + 为什么这么实现，低频更新。模板 [templates/AI.md](templates/AI.md)，细则 [ai-md.md](ai-md.md)。
2. 产品方案在 `docs/`，禁止写入 `spec.yaml` / `AI.md`。
3. 找/改代码路径：
   ```text
   根 spec.yaml → 子模块 spec.yaml → …逐层 → 目标模块 spec.yaml → 实现文件
   ```
   需理解设计意图或改设计时，再读对应 `AI.md`。禁止无目标全量扫代码树。
4. 同步：
   - 伪软件裁定后 / 新建模块（实现前）→ 生成 `spec.yaml` + `AI.md`，并登记到父级 `spec.yaml` 的 `modules`。
   - 改契约 → 裁定后先改该模块 `spec.yaml`，再改代码；职责变化时同步父级 `modules` 一句话。
   - 设计方向或实现理由变化 → 更新 `AI.md`。
   - 只改实现不改契约与设计 → 不更新。
5. 改完 `spec.yaml` / `AI.md` / 模块目录后运行 `python3 .cursor/skills/ai-friendly-codebase/scripts/check_index.py`；失败须修复。
6. 上层代码结构 → [thin-upper.md](thin-upper.md)。
7. 项目专属分层与业务禁令写在该仓库 `docs/` 或专属 rule；本 Skill 保持通用，禁止写死业务清单。
