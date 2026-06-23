# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-06-23

### Added
- 添加 `tool_limit` 和 `tool_range` 配置，控制显示最新或最早的工具调用数量。

### Changed
- 工具调用历史只显示工具名称，不再显示参数、状态或结果摘要。
- 超出 `tool_limit` 的工具调用以 `+n` 计数显示，不展开具体内容。
- 统计信息样式从 `compact`/`normal` 重命名为更贴近真实渲染的 `small`/`md`。

### Removed
- 移除旧节流器和未使用的兼容壳，主链路直接按终态/工具状态更新卡片。
- 移除未使用的渲染导出、会话字段和开发依赖，减少维护面。

## [0.2.0] - 2026-06-23

### Fixed
- 修复工具调用不显示的问题，运行态工具开始/结束和 LLM 响应中的工具调用都会写入卡片会话。
- 修复 footer 中模型名始终为 `Unknown` 的问题，优先从 ProviderRequest、当前 Provider 和响应对象提取模型名。
- 修复输入/输出 token 始终为 `0` 的问题，兼容 AstrBot `TokenUsage(input_other, input_cached, output)`。
- 修复 AstrBot Lark 原生 `_send_card_message()` 只返回 `bool` 时无法捕获 `message_id`，导致最终 PATCH 不执行的问题。
- 修复开发版本误把插件设为 AstrBot 保留插件导致无法删除的问题；插件现在会保持 `reserved=False`。

### Changed
- 生命周期观测改为运行期 hook 透传，不再依赖修改插件 metadata。
- 将 CardKit、原生 Lark 包装、观测数据提取拆分到 `core/` 子模块，降低 `main.py` 复杂度。
- 更新 AstrBot 兼容版本为 `>4.23.1,<5`。

### Added
- 添加模型/token/工具显示链路的回归测试。
- 添加原生 Lark message_id 捕获和插件可删除状态的回归测试。

## [0.1.0] - 2026-06-22

### Added
- 初始版本发布
- 实现飞书流式卡片核心功能
- 支持思考过程可视化
- 支持工具调用跟踪
- 支持统计信息展示
- 实现 Monkey Patch 机制
- 实现冲突检测
- 实现安全降级策略
- 实现节流控制
- 实现会话管理
- 添加完整配置选项
- 添加调试模式

### Technical Details
- 使用飞书 CardKit JSON 2.0 API
- 支持 AstrBot 4.15+
