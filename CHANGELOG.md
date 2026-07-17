# Changelog

版本遵循 [Semantic Versioning](https://semver.org/)。项目进入 `1.0.0` 前，次版本可能包含需要人工迁移的部署配置变化。

## [Unreleased]

### Planned

- Passkey 与恢复代码。
- 备份失败、磁盘不足和崩服的外部通知。
- 更多控制台和运行方式适配器。

## [0.4.0] - 2026-07-17

### Added

- 可配置 systemd 运行 profile 和 root-owned helper profile。
- Minecraft、Forge、NeoForge、Fabric、Quilt、Vanilla 与 Java 证据化识别。
- `mc-panel-admin doctor` 和通用 fingerprint CLI。
- Apache-2.0、CI、贡献规范、安全报告、Issue/PR 模板和公开路线图。

### Changed

- 移除源码、部署模板和文档中的单台生产服务器绑定。
- 只读安装要求显式提供关键路径；写入 drop-in 从 root 配置生成。
- 未知版本在 API 和界面中明确显示为待识别，不再使用固定默认值。
- 所有维护脚本从运行 profile 读取面板端口和 Minecraft service，不再依赖默认值。

### Fixed

- 修复新安装生成带引号的 `panel.env` 后，管理员创建和密码重置脚本读取错误。
- 修复非默认面板端口下，启用写入和配置 AI 会错误判断健康检查失败。
- 修复卸载脚本固定检查 `minecraft.service`，导致自定义 service 无法安全验收。

### Security

- helper 配置拒绝软链接、错误所有者、可写模式、额外字段、相对路径、路径穿越、非法 unit、端口、账号、screen 名称和 glob。
- 部署、更新、回滚和卸载脚本仅信任 `root:mc-panel 0640` 的环境文件与 `root:root 0600` 的 helper profile。
- 发布构建排除环境文件、数据库、日志、证书、密钥、本地生产记录和历史归档。
- 生产启动拒绝未知环境名、非 systemd adapter、非回环绑定、非 Secure Cookie、相对进程路径和非法 fingerprint。
