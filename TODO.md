# 飞书流式卡片插件 - 功能任务列表

本列表基于 Hermes `hermes-feishu-streaming-card` 能力调研和当前 AstrBot 最小版本代码现状整理。后续进入实现阶段时必须按 TDD 执行：先写失败测试，确认失败原因正确，再做最小实现。

## 已有基础

- [x] 基础 `send_streaming` monkey patch 骨架
- [x] `CardSession` / `SessionManager` 会话状态骨架
- [x] 飞书卡片 JSON 基础渲染
- [x] `RateLimiter` 节流器
- [x] per-session 锁机制
- [x] 基础并发控制测试

## P0 - 必补基础功能

- [x] 修正流式更新双重节流问题
  - 现状：`_stream_updates()` 和 `_update_card()` 连续调用 `RateLimiter.should_update()`，正常流式 PATCH 可能被第二次检查挡掉
  - TDD 验收：连续流式 chunk 在满足 `update_interval` 后应触发真实卡片 PATCH
  - 已完成：节流统一保留在 `_update_card()`，`_stream_updates()` 只负责累积文本并请求更新

- [ ] 原生消息抑制
  - 卡片成功完成后不再额外发送灰色原生文本消息
  - 卡片创建或主流程失败时才降级到原生流式输出
  - TDD 验收：成功卡片路径不调用原始 `send_streaming`；失败路径完整消费并返回原生流式文本

- [ ] 真实 AstrBot/Lark API 适配测试
  - 校验 `create` / `patch` 请求的 `receive_id_type`、`receive_id`、`msg_type=interactive`、`content` 结构
  - 确认当前 `event.bot.im.v1.message.create/patch` 调用方式与 AstrBot 飞书平台对象兼容
  - TDD 验收：fake Lark client 捕获到符合飞书接口预期的 create/patch 请求

- [ ] 长 Markdown 结构化切分
  - 超长 fenced code block 拆成多个完整 fenced block
  - 超长 Markdown 表格按结构边界切分并重复表头
  - 保护飞书卡片元素长度和表格数量限制
  - TDD 验收：分块后不存在半截代码围栏，表格块保留表头，单元素不超过配置限制

- [ ] 工具调用实时更新闭环
  - 当前 `render_card()` 仅在终态显示工具，无法体现实时工具状态
  - `update_tool_result()` 修改状态后还需要触发卡片更新
  - TDD 验收：工具开始显示 running，工具完成显示 completed 和结果摘要，工具失败显示 failed

- [ ] 思考过程抽取与渲染
  - 当前 `thinking_text` 字段存在，但流式 chunk 全部进入 `answer_text`
  - 需要适配 AstrBot/模型响应中的 reasoning/thinking 字段；若平台无法提供，需要文档明确仅支持答案流
  - TDD 验收：thinking 增量进入 `thinking_text`，答案增量进入 `answer_text`，`show_thinking=false` 时不渲染思考内容

- [x] 失败路径资源清理
  - 异常路径应清理 session 和 session lock，避免长期运行泄漏
  - TDD 验收：发送失败、PATCH 失败、generator 抛错后锁和 session 均按预期清理
  - 已完成：`_handle_streaming()` 使用 `finally` 统一清理 session 和 per-session lock

- [ ] Patch 生命周期与冲突处理
  - `force_patch` 配置目前没有真正传入 `StreamingPatch.install()`
  - 插件卸载/热重载时需要只恢复自己的 patch
  - TDD 验收：已有其他 patch 时默认拒绝；force 行为明确；卸载时不误删其他插件 patch

## P1 - 基础体验增强

- [ ] 卡片内容安全过滤
  - 过滤 `<think>`、`</think>`、`<thinking>`、`</thinking>` 等内部控制标记
  - TDD 验收：终态卡片不泄露 thinking 标签或零宽控制字符

- [ ] 统计信息与最终卡片时序
  - 确认 `on_llm_response` 与 `_handle_streaming()` 的实际生命周期顺序
  - 避免 session 被过早移除导致模型、token、耗时等 footer 缺失
  - TDD 验收：最终卡片包含模型名和 token 统计；没有统计时显示可接受默认值

- [ ] 插件级端到端测试
  - 使用 fake event、fake Lark client、async generator 跑完整 `_handle_streaming()`
  - TDD 验收：create/patch 次数、最终卡片内容、fallback 行为均可断言

- [ ] 配置项补齐
  - 建议新增：`card_title`、`max_card_element_chars`、`max_tables`、`suppress_native_message`、`filter_thinking_tags`、`show_tools_realtime`
  - TDD 验收：配置开关能影响对应渲染或流程行为

## P2 - 高级特性（暂缓）

- [ ] 卡片内交互按钮（approval/clarify）
  - 需要先确认 AstrBot 是否有等价交互生命周期

- [ ] 多 Bot / 群聊绑定 / 多 Profile
  - AstrBot 通常已负责平台和 bot 路由，不建议直接照搬 Hermes sidecar 配置

- [ ] 健康检查与诊断系统
  - 可做插件级 health/debug 命令，但不必复制 Hermes 的 sidecar doctor/install/repair 体系

- [ ] 附件与媒体摘要
  - 展示图片、文件、音频、视频等结构化摘要，同时不阻断原生媒体投递

## 测试覆盖缺口

- [x] 并发控制单元测试
- [x] 锁机制测试
- [x] 流式 PATCH 双重节流回归测试
- [ ] 原生消息抑制测试
- [ ] 真实插件路径端到端测试
- [ ] 长内容切分测试
- [ ] 工具实时状态测试
- [ ] 思考内容抽取测试
- [x] 失败清理测试
- [ ] Patch 冲突与卸载测试
