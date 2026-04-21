# Proposal

## Why

当前 gateway 子系统在对外叙述上混用了 `aworld_gateway` 与 `aworld-gateway` 两套命名，容易让使用者把 Python import 名、仓库/组件显示名、文档称呼混为一谈。

## What Changes

- 明确命名规范：对外显示、文档、服务标题统一使用 `aworld-gateway`。
- 明确 Python 代码导入名保持 `aworld_gateway`，因为 Python 包导入语法不能使用连字符。
- 为 gateway CLI、HTTP app 和导出的命名常量补齐这一约定。
- 在 README 中加入简短命名说明，避免后续再出现混用。
