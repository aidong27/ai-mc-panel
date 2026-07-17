# Contributing

感谢你帮助方块管家变得更安全、更易用。项目面向不熟悉服务器运维的 Minecraft 管理员，因此正确性、安全边界和普通中文表达优先于功能数量。

## 开始之前

- 安全漏洞请使用 GitHub Private vulnerability reporting，不要创建公开 Issue。
- 新功能不要直接扩大 root helper 权限；先说明威胁模型、参数边界和回滚方法。
- 不要提交真实服务器地址、玩家 IP、密码、API key、Cookie、SSH 材料、日志或世界数据。
- 破坏性测试只能使用临时目录和 mock adapter。

## 本地环境

需要 Python 3.12、uv、Node.js 24+ 和 npm。

```bash
cp .env.example .env
set -a && source .env && set +a
uv sync --project apps/api --all-extras --locked
npm --prefix apps/web ci
```

默认配置使用 mock adapter，不会连接真实 Minecraft 服务。

## 提交前检查

```bash
cd apps/api
.venv/bin/ruff format src tests ../../helper ../../deploy/scripts
.venv/bin/ruff check src tests ../../helper ../../deploy/scripts
.venv/bin/mypy src
.venv/bin/pytest

cd ../web
npm test
npm run build
```

所有新增行为应包含测试。安全边界、共享 API 合约、数据库迁移和用户工作流需要扩大测试覆盖；纯文案修改可保持轻量。

## Pull Request

PR 应说明：

- 改了什么以及用户为什么需要它。
- 风险等级、权限变化、数据迁移和兼容性影响。
- 对 Minecraft 停服、备份与回滚的影响。
- 执行过的测试和未验证的部分。

保持改动聚焦，不顺手重构无关模块。面向用户的错误信息使用普通中文；内部代码、接口字段和提交信息使用清晰英文。

## 设计规则

- AI 只能调用 typed tools，禁止执行模型生成的 Shell 文本。
- Web/API 进程不能以 root 身份运行。
- 路径必须由 root 配置或后端对象 ID 决定，不能由浏览器直接指定。
- 中风险操作需要确认，高风险操作需要二次确认和恢复点。
- 无法确认路径、版本或结果时必须失败关闭，不得猜测或伪报成功。
