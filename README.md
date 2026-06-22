# 飞书流式卡片增强插件

[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A54.15-blue)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

将 AstrBot 的 LLM 流式输出渲染为持续更新的飞书卡片，提供更好的用户体验。

## 功能特性

- **流式卡片更新**：将 LLM 流式输出渲染为持续更新的飞书卡片
- **思考过程可视化**：展示 LLM 的推理过程（可选）
- **工具调用跟踪**：实时显示工具调用状态和结果
- **统计信息展示**：显示耗时、模型、token 消耗等元数据
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

1. **AstrBot 版本**：>= 4.15
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
  "update_interval": 0.2,       // 卡片更新间隔（秒）
  "show_thinking": true,        // 是否显示思考过程
  "show_tools": true,           // 是否显示工具调用
  "show_footer": true,          // 是否显示统计信息
  "max_sessions": 100,          // 最大会话数
  "session_ttl": 3600,          // 会话超时时间（秒）
  "force_patch": false,         // 强制安装 Patch（慎用）
  "debug_mode": false           // 调试模式
}
```

### 配置项详解

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | boolean | `true` | 是否启用飞书流式卡片功能 |
| `update_interval` | number | `0.2` | 卡片更新的最小时间间隔（秒），避免频繁调用飞书 API |
| `show_thinking` | boolean | `true` | 是否在卡片中显示 LLM 的思考过程 |
| `show_tools` | boolean | `true` | 是否在卡片中显示工具调用历史 |
| `show_footer` | boolean | `true` | 是否显示耗时、模型、token 等统计信息 |
| `max_sessions` | integer | `100` | 内存中保持的最大会话数，超过后自动清理最旧的会话 |
| `session_ttl` | integer | `3600` | 会话在内存中保持的最长时间（秒），超时后自动清理 |
| `force_patch` | boolean | `false` | 即使检测到冲突也强制安装 Monkey Patch（慎用） |
| `debug_mode` | boolean | `false` | 开启后会输出详细的调试日志 |

## 使用说明

### 基本使用

插件安装并启用后，在飞书中与 AstrBot 对话时，LLM 的回复会自动渲染为流式更新的卡片。

### 卡片结构

卡片包含以下部分：

```
┌──────────────────────────────────────┐
│ AstrBot                    思考中... │  ← Header（动态状态）
├──────────────────────────────────────┤
│ 思考过程：                           │  ← 思考过程（可选）
│ 分析用户问题...                      │
├──────────────────────────────────────┤
│ LLM 回答内容...                      │  ← 主内容（流式更新）
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
       ├─> SessionManager (会话管理)
       ├─> RateLimiter (节流控制)
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
| 主插件 | `main.py` | 插件入口，整合所有模块 |

### Monkey Patch 机制

插件通过 Monkey Patch 拦截 `LarkMessageEvent.send_streaming` 方法：

```python
# 拦截点
LarkMessageEvent.send_streaming
    └─> patched_send_streaming (插件实现)
        ├─> 创建飞书卡片
        ├─> 流式更新卡片内容
        ├─> 完成标记
        └─> 异常时降级到原方法
```

### 冲突检测

插件使用 `_patch_token` 机制避免与其他插件冲突：

- 启动时检查是否已有其他 patch
- 如果检测到冲突，拒绝安装（除非 `force_patch: true`）
- 卸载时验证 token，确保不会误删其他插件的 patch

## 故障排查

### 卡片不显示

**可能原因**：
1. 飞书应用缺少 `cardkit:card:write` 权限
2. 与其他流式插件冲突
3. AstrBot 版本低于 4.15

**解决方法**：
1. 检查飞书开发者后台，确保应用有卡片权限
2. 检查日志是否有 "already patched" 警告
3. 升级 AstrBot 到最新版本

### 卡片更新卡顿

**可能原因**：
1. `update_interval` 设置过大
2. 飞书 API 限流

**解决方法**：
1. 调整 `update_interval` 到 0.1-0.3 秒
2. 检查飞书 API 配额使用情况

### 内存占用过高

**可能原因**：
1. `max_sessions` 设置过大
2. `session_ttl` 设置过长

**解决方法**：
1. 调整 `max_sessions` 到 50-100
2. 调整 `session_ttl` 到 1800-3600 秒

### 查看调试日志

启用 `debug_mode: true`，查看详细日志：

```
[飞书流式卡片] 创建会话: session_123:msg_456
[飞书流式卡片] 发送卡片成功: om_xxx
[飞书流式卡片] 更新卡片成功: om_xxx
[飞书流式卡片] 会话完成: session_123:msg_456
```

## 兼容性

### 已测试的环境

- AstrBot 4.15+
- Python 3.9+
- 飞书（Lark）国内版
- 飞书（Lark）国际版

### 已知兼容插件

- `astrbot_plugin_openai` - OpenAI 接口插件
- `astrbot_plugin_claude` - Claude 接口插件

### 已知冲突插件

- `astrbot_lark_enhance` - 如果启用了其流式卡片功能
- `astrbot_lark_richpost` - 如果修改了 send_streaming

**解决方法**：禁用冲突插件的流式功能，或设置 `force_patch: true`（慎用）

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
│   └── patch.py              # Monkey Patch
├── docs/                      # 文档
│   ├── feasibility-study.md  # 可行性研究
│   └── assets/               # 资源文件
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
