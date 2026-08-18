> ⚠️ **归档声明**：本文档为历史快照，记录了特定时间点的决策与实施状态。
> 当前代码状态以对应 `.py` 源文件为准，本文档内容可能已过时，仅供参考。

# IDE 侧对接更改说明 —— 模型收敛为 dream-creator、档位收敛为 auto

> **状态**：已实施（IDE 侧由同日 2026-0812-1752 计划承接落地，联调全过）
> **范围**：IDE 侧 agent 接入配置（面向 IDE 工程师的对接文档）
> **时间**：2026-08-12 17:28（设计，UTC+8）
> **优先级**：高（需与 Dream CLI 服务端同日发布）

---

## 1. 一句话说明

IDE 界面上服务商 **DREAM** 下的模型与推理强度选项，由「两款模型 + 多档位」
收敛为**各只有一个固定值**：模型 `dream-creator`，推理强度 `auto`。
用户不再做选择；实际用哪个模型、哪档强度，全部由 Dream CLI 中转站
的服务端配置决定，IDE 端今后无需再为此改动。

## 2. IDE 侧需要改什么

| 项 | 原值 | 新值（唯一合法值） |
|:---|:---|:---|
| 服务商显示名 | DREAM | **不变** |
| 模型列表 | `dream/demo-fast`、`dream/demo-smart` | 仅保留 `dream-creator` |
| 推理强度列表 | 多档位（或此前约定的档位名） | 仅保留 `auto` |

- 模型 value 与档位 value 都是 ACP `session/set_config_option` 的
  **不透明字符串**：IDE 照旧原样下发，不做解析、不拼接、不校验格式。
- 显示名（UI 文案）IDE 侧自定，建议与 value 同名 `dream-creator` / `auto`，
  减少歧义；显示名不参与协议，改成别的也不影响对接。

## 3. 协议帧示例（不变的部分）

协议机制零改动，仅 value 取值收敛：

```json
# 模型（新会话建立后补发预选值，或会话内切换）
{"jsonrpc":"2.0","id":3,"method":"session/set_config_option","params":{
  "sessionId": "...",
  "configId": "model",
  "value": "dream-creator"
}}

# 推理强度
{"jsonrpc":"2.0","id":4,"method":"session/set_config_option","params":{
  "sessionId": "...",
  "configId": "effort",
  "value": "auto"
}}
```

握手（`initialize`）、建会话（`session/new`）、对话（`session/prompt`）、
流式更新（`session/update`）、取消（`session/cancel`）等全部环节
**无任何变化**。

## 4. 服务端新行为：白名单硬校验

本期起，Dream CLI 服务端对外部客户端启用白名单校验：

- `configId="model"`：**只接受 `dream-creator`**；任何其他值返回
  `-32602 invalid params`，报错文案会指明唯一合法值，例如：
  `invalid params: 未知模型别名 'xxx'（本服务端仅接受 'dream-creator'）`。
- `configId="effort"`：**只接受 `auto`**；其他值同样 `-32602`。
- 报错不会导致连接断开或会话失效，客户端修正 value 后重发即可。

⚠️ 这意味着：**旧值 `dream/demo-fast` / `dream/demo-smart` 在服务端新版
发布后会被拒绝**。因此双方需**同日发布**（或 IDE 侧先行、服务端紧随其后，
间隔越短越好）。若 IDE 侧希望利用报错文案做自检/引导，可直接展示
error 帧的 `message`，文案是可读中文。

## 5. 设计意图（为什么这样改）

- ACP 协议本身没有「模型目录上报」机制，IDE 的模型列表本来就是前端
  硬编码，与服务端无自动同步。既然怎么选最终都由中转站映射决定，
  前端提供多选项只会制造「选了也不生效/生效了也看不见原因」的困惑。
- 收敛后，Dream CLI 服务端改一行配置即可切换底层真实模型与推理档位，
  IDE 端零改动、用户无感知。

## 6. 联调检查清单

IDE 侧改完后，可按以下顺序自检：

1. ✅ 握手：DREAM 服务商正常出现，agent 信息正常。
2. ✅ 新建会话后补发 `model=dream-creator`、`effort=auto`，均收到成功回执
   （无 error 帧）。
3. ✅ 发一条普通消息，流式回复正常，轮次正常结束。
4. ✅ （可选）故意发一次旧值 `dream/demo-fast`，确认收到 -32602 且
   会话可继续复用——验证白名单报错路径与错误恢复。
5. ✅ `session/cancel` 取消后继续对话，会话可复用（既有行为，回归确认）。

## 7. 联系与对齐

- 服务端对应计划：`2026-0812-1659_IDE前端写死对接计划_单模型dream-creator与auto档位.md`（Dream CLI 仓库 `work plans/` 下）。
- 协议真值文档：`dream-acp-v1.md` §1.5（`set_config_option` 机制说明；
  本文档的取值约束是对该节的客户端侧补充）。
- 发布时间、灰度安排（是否需要短暂双值并存期）请与 Dream CLI 侧
  对接人确认后再定。

---

*撰写于 2026-08-12 17:28 (UTC+8) · 状态：已实施（IDE 侧由同日 2026-0812-1752 计划承接落地，联调全过）*
