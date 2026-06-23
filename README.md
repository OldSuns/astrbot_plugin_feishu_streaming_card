# 飞书流式卡片增强插件

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E4.23.1-blue)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

<img src="https://count.getloli.com/get/@astrbotpluginfeishustreamingcard?theme=moebooru" alt="Moe Counter">

将 AstrBot 的 LLM 流式输出渲染为持续更新的飞书卡片，提供更好的用户体验。

当前版本：`v0.2.2`

<img width="400" alt="sample" src="https://github.com/user-attachments/assets/9e0ac584-c266-411b-99e9-78784cfe3c50" />

## 功能特性

- **流式卡片更新**：将 LLM 流式输出渲染为持续更新的飞书卡片
- **思考过程可视化**：展示 LLM 的推理过程（可选）
- **工具调用跟踪**：通过 AstrBot LLM/tool 生命周期 hook 显示工具名称
- **统计信息展示**：显示耗时、模型、token 消耗等元数据，兼容 AstrBot `TokenUsage`
- **并发控制**：per-session 锁机制，避免并发更新导致的内容冲突
- **安全降级**：异常时自动回退到原生流式输出
- **高度可配置**：丰富的配置选项满足不同需求

## 安装

### 通过 AstrBot 插件市场安装（推荐）

1. 在 AstrBot 控制台中打开插件市场
2. 搜索 "飞书流式卡片"
3. 点击安装并启用

### 手动安装

```bash
cd /path/to/astrbot/plugins
git clone https://github.com/astrbot/astrbot_plugin_feishu_streaming_card.git
cd astrbot_plugin_feishu_streaming_card
pip install -r requirements.txt
```

重启 AstrBot 后插件会自动加载。

## 快速开始

### 前置要求

1. **AstrBot 版本**：> 4.23.1
2. **飞书平台配置**：
   - 已在 AstrBot 中配置飞书平台
   - 飞书应用需要以下权限：
     - `im:message` - 发送消息
     - `im:message:send_as_bot` - 以机器人身份发送
     - `cardkit:card:write` - 创建和更新卡片（**必需**）

### 配置说明

插件安装后会使用默认配置，你可以通过 AstrBot 控制台修改配置：

```json
{
  "enabled": true,              // 是否启用插件
  "show_thinking": true,        // 是否显示思考过程
  "show_tools": true,           // 是否显示工具调用
  "tool_limit": 5,              // 最多显示几个工具调用
  "tool_range": "latest",       // 工具调用范围：latest/earliest
  "show_footer": true,          // 是否显示统计信息
  "footer_style": "small",      // 统计信息样式：small/md
  "max_sessions": 25,           // 最大会话数
  "session_ttl": 3600,          // 会话超时时间（秒）
  "uninstall_patch": false,     // 卸载 Monkey Patch
  "debug_mode": false           // 调试模式
}
```

### 配置项详解

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | boolean | `true` | 是否启用飞书流式卡片功能 |
| `show_thinking` | boolean | `true` | 是否在卡片中显示 LLM 的思考过程 |
| `show_tools` | boolean | `true` | 是否在卡片中显示工具调用历史 |
| `tool_limit` | integer | `5` | 最多显示几个工具调用，超出显示 `+n` |
| `tool_range` | string | `latest` | 工具调用显示范围：`latest` 最新，`earliest` 最早 |
| `show_footer` | boolean | `true` | 是否显示耗时、模型、token 等统计信息 |
| `footer_style` | string | `small` | 底部统计信息样式：`small` 使用 V2 普通文本 notation 小字号，`md` 使用 lark_md 灰字 |
| `max_sessions` | integer | `25` | 内存中保持的最大会话数，超过后自动清理最旧的会话 |
| `session_ttl` | integer | `3600` | 会话在内存中保持的最长时间（秒），超时后自动清理 |
| `uninstall_patch` | boolean | `false` | 保存配置后卸载 Monkey Patch，用于不重启恢复飞书原生流式输出 |
| `debug_mode` | boolean | `false` | 开启后会输出详细的调试日志 |

## 使用说明

### 基本使用

插件安装并启用后，在飞书中与 AstrBot 对话时，LLM 正文仍使用 AstrBot/Lark 原生 CardKit 流式输出；插件只在创建卡片时补充状态 header，并在流式结束后更新完成状态、工具摘要和统计信息。飞书聊天框外部摘要优先使用实际回答正文。

插件不会把自身标记为 AstrBot 保留插件，仍可在 WebUI 中正常禁用、卸载和删除。

### 卡片结构

卡片包含以下部分：

```
┌──────────────────────────────────────┐
│ 思考中...                            │  ← Header（动态状态）
├──────────────────────────────────────┤
│ 思考过程：                           │  ← 思考过程（可选）
│ 分析用户问题...                      │
├──────────────────────────────────────┤
│ LLM 回答内容...                      │  ← 主内容（原生流式更新）
│                                      │
├──────────────────────────────────────┤
│ 🔧 工具调用：                        │  ← 工具调用历史（可选）
│ 1. ✓ search_web                     │
│ 2. ✓ get_weather                    │
├──────────────────────────────────────┤
│ 6.2s · gpt-4 · ↑1234 ↓567           │  ← Footer 统计信息
└──────────────────────────────────────┘
```

### 状态颜色

- **思考中**（indigo）：正在生成回复
- **完成**（green）：回复生成完成
- **失败**（red）：处理失败（会自动降级到原生流式）

## 技术实现

### 架构设计

```
AstrBot 主进程
   └─> FeishuStreamingCardPlugin
       ├─> Monkey Patch send_streaming
       ├─> Lifecycle Hook Passthrough (模型/token/工具观测)
       ├─> SessionManager (会话管理)
       ├─> Per-Session 锁 (并发控制)
       └─> CardSession (状态机)
           └─> render_card() (卡片渲染)
               └─> 飞书 API (send/patch)
```

### 核心模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 状态管理 | `core/session.py` | CardSession 状态机，管理会话状态 |
| 卡片渲染 | `core/render.py` | 飞书卡片 JSON 构建和渲染 |
| 文本归一化 | `core/normalizer.py` | 处理增量 Markdown 文本，确保可渲染 |
| Monkey Patch | `core/patch.py` | 拦截 send_streaming，实现流式卡片 |
| 飞书卡片辅助 | `core/lark_card.py` | CardKit JSON、chunk 文本和 ID 提取 |
| 原生飞书包装 | `core/native_lark.py` | 包装原生 Lark 方法，捕获 card/message ID |
| 生命周期 hook | `core/lifecycle_hooks.py` | 不修改 reserved metadata 的 hook 透传 |
| 观测数据 | `core/observability.py` | 提取模型名、token 用量和工具参数 |
| 主插件 | `main.py` | 插件入口，整合所有模块 |

### Monkey Patch 机制

插件通过 Monkey Patch 拦截 `LarkMessageEvent.send_streaming` 方法：

```python
# 拦截点
LarkMessageEvent.send_streaming
    └─> patched_send_streaming (插件实现)
        ├─> 调用原生 send_streaming
        ├─> 注入初始 header
        ├─> 旁路捕获文本/工具/统计
        ├─> 结束后 patch 完成状态/footer
        └─> 异常时降级到原方法
```

## 故障排查

### 卡片不显示

**可能原因**：
1. 飞书应用缺少 `cardkit:card:write` 权限
2. 与其他流式插件冲突
3. AstrBot 版本低于 4.23.1

**解决方法**：
1. 检查飞书开发者后台，确保应用有卡片权限
2. 检查日志是否有 "already patched" 警告
3. 升级 AstrBot 到最新版本

### 内存占用过高

**可能原因**：
1. `max_sessions` 设置过大
2. `session_ttl` 设置过长

**解决方法**：
1. 调整 `max_sessions` 到 50-100
2. 调整 `session_ttl` 到 1800-3600 秒

## 兼容性

### 已测试的环境

- AstrBot > 4.23.1
- Python 3.9+
- 飞书（Lark）国内版

## 开发说明

### 项目结构

```
astrbot_plugin_feishu_streaming_card/
├── main.py                    # 插件入口
├── metadata.yaml              # 插件元数据
├── _conf_schema.json          # 配置 Schema
├── requirements.txt           # 依赖
├── core/                      # 核心模块
│   ├── __init__.py
│   ├── session.py            # 状态管理
│   ├── render.py             # 卡片渲染
│   ├── normalizer.py         # 文本归一化
│   ├── patch.py              # Monkey Patch
│   ├── lark_card.py          # CardKit 辅助
│   ├── native_lark.py        # 原生飞书方法包装
│   ├── lifecycle_hooks.py    # 生命周期 hook 透传
│   └── observability.py      # 模型/token/工具观测数据
├── tests/                     # 回归测试
├── LICENSE
└── README.md
```

### 本地开发

```bash
# 克隆项目
git clone https://github.com/astrbot/astrbot_plugin_feishu_streaming_card.git
cd astrbot_plugin_feishu_streaming_card

# 安装依赖
pip install -r requirements.txt

# 链接到 AstrBot 插件目录
ln -s $(pwd) /path/to/astrbot/plugins/astrbot_plugin_feishu_streaming_card

# 重启 AstrBot
```

### 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 许可证

本项目基于 MIT 许可证开源 - 详见 [LICENSE](LICENSE) 文件

## 致谢

- [hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card) - 核心灵感来源
- [AstrBot](https://github.com/Soulter/AstrBot) - 优秀的机器人框架

---

**注意**: 本插件仅在飞书平台生效，其他平台会自动使用原生流式输出。
