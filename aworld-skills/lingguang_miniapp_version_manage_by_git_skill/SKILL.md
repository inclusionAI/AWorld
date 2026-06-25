---
description: 在制作灵光小程序时，利用 Git 进行版本控制，以实现代码与多媒体附件的同步保存、回滚、分支切换与合并。避免重复调用 AI 生成已存在的图片/音频，实现“代码+资产”的完美状态快照。
---

## 1. 核心原则与作用域 (Scope)

- **严格限制范围**：Git 版本控制的范围**仅限且必须仅限于 `workspace/` 目录及其内部文件**（包括代码和多媒体附件）。
- **禁止越界**：绝不能将外层的日志文件、临时 Python 脚本、或其他无关目录纳入版本控制。
- **强制约束手段**：在执行 Git 初始化前，**必须由智能体动态创建 `.gitignore` 文件**来限制追踪范围。该文件必须保存在当前的工作目录中，绝对不可以超出当前工作目录的范围。

## 2. 初始化与配置 (Initialization)

在项目初期（如完成 v1 版本时），智能体必须**首先**动态写入 `.gitignore` 文件，然后再初始化 Git 仓库：

```bash
# 1. 在当前工作目录动态创建 .gitignore，严格限制只追踪 workspace/
cat << 'IGNORE_EOF' > .gitignore
# 忽略所有文件
*
# 但不忽略 workspace 目录及其内部文件
!workspace/
!workspace/**
# 不忽略 .gitignore 本身
!.gitignore
IGNORE_EOF

# 2. 初始化 Git 并提交
git config --global user.email "ai@aworld.com"
git config --global user.name "AWorld AI"
git init
git add workspace/ .gitignore
git commit -m "v1: 初始版本描述"
```

## 3. 版本保存 (Commit)

- **触发时机**：每次完成一个具有独立意义的版本（如：完成单分叉点剧情、新增一个剧情分支、替换了一套UI风格）后，必须进行 Commit。
- **关键点**：必须同时提交代码文件（`App.tsx`, `storyData.ts` 等）和**多媒体附件**（`assets/image/`, `assets/audio/`）。
- **提交规范**：提交信息 (Commit Message) 必须清晰，例如："v2: 新增分叉点2及对应图片"。

## 4. 版本切换与回滚 (Checkout & Rollback)

- **无损切换**：通过 `git checkout <commit-hash>` 或 `git checkout <branch-name>` 切换版本时，Git 会自动替换代码并**恢复对应的多媒体附件**。这极大地节省了重新生成图片/音频的时间和 API 成本。
- **Check点（极其重要）**：在切换版本前，务必通过 `git status` 检查当前 `workspace/` 是否有未提交的修改。如果有，必须先 `commit` 或 `stash`，否则可能会覆盖并永久丢失新生成的附件。

## 5. 分支与合并 (Branch & Merge)

- **探索性开发**：当用户提出“尝试一种全新的剧情走向”或“换一种画风看看”时，应创建新分支（`git checkout -b feature-new-style`）。
- **合并与废弃**：如果用户对新分支满意，可以合并回主分支（`git merge`）；如果不满意，可以直接废弃该分支，切回主分支，瞬间恢复到探索前的状态。

## 6. 注意细节与避坑指南

1. **附件重名与内容变更**：不同版本中，如果同名附件（如 `page_3.png`）内容发生了变化（例如剧情走向改变导致画面不同），Git 会完美记录其二进制差异。切换版本时会自动替换为对应版本的图片，无需担心覆盖错乱。
2. **慎用 `rm -rf`**：在有 Git 保护的情况下，不要轻易手动 `rm -rf workspace/assets/image/*`。如果需要清理，请确保当前状态已 Commit，或者直接通过 Git 切换分支来改变文件状态。
3. **大文件追踪**：虽然小程序附件通常不大，但如果音频/图片极多，Git 仓库体积会增加。但在当前 AI 智能体工作流中，这是实现“状态快照”的最优解，利大于弊。
4. **忽略构建产物**：如果后续引入了本地构建流程，务必在 `workspace/.gitignore` 中忽略 `dist/` 或 `node_modules/` 等构建产物，只追踪源码和原始素材。