> **状态**：已实施（2026-08-12 落地；管道级联调 12/12 + GUI 无头探针 7/7 全过，见 §7 实施记录）
> **范围**：`llm/providers/dream_acp.py`、`llm/registry.py`；GUI 零改动（数据驱动）
> **时间**：2026-08-12 17:52（设计，UTC+8）
> **优先级**：高（需与 Dream CLI 服务端同日发布，间隔越短越好）
> **依据**：`2026-0812-1728_IDE侧对接更改说明_模型收敛dream-creator与auto档位.md`
> （Dream CLI 侧发来的对接文档，下称「对接文档」）

# DREAM 模型收敛 dream-creator 与 auto 档位对接计划

## 1. 背景

Dream CLI 服务端本期起对 `session/set_config_option` 启用**白名单硬校验**：

- `configId="model"` 只接受 `dream-creator`；旧值 `dream/demo-fast` /
  `dream/demo-smart` 一律 `-32602 invalid params`；
- `configId="effort"` 只接受 `auto`；其他值同样 `-32602`。

IDE 界面上 DREAM 服务商下的模型与推理强度选项随之各收敛为唯一固定值，
用户不再做选择；实际模型与档位由服务端配置决定，IDE 今后零改动。
协议机制（握手/建会话/对话/流式/取消）零变化，仅 value 取值收敛
（对接文档 §2–§4）。

## 2. IDE 侧现状（代码实证）

| 位置 | 现状 |
|---|---|
| `llm/providers/dream_acp.py` `_DEMO_MODELS` | 静态表 `["dream/demo-fast", "dream/demo-smart"]`（示例期，与 `dream-acp/example/dream` DEMO_MODELS 同步），即 DREAM 模型菜单数据源 |
| `llm/providers/dream_acp.py` `DreamAcpLLM` | 有 `set_model`（configId="model"，别名原样透传）；**无 `set_effort`**，`_ensure_session` 新会话只补发预选模型，不补发强度 |
| `llm/registry.py` dream-acp `BackendSpec` | 无 `efforts` / `default_effort` 声明 → ModelBar 第四级「推理强度」按钮对 DREAM 禁用（「当前接口不支持」） |
| `gui/panels/chat/model_bar.py` / `tabs.py` / `panel.py` | 全部数据驱动：模型菜单来自 `spec.list_models()`，强度菜单来自 `resolve_efforts(spec, model)`，强度下发鸭子类型 `provider.set_effort`。**无需任何改动** |
| 持久化记忆（`gui/settings.py` `model_versions` / `model_efforts`） | 当前 `config/settings.json` 无 dream 条目；即便用户历史上存过旧别名，既有回退链（模型落首项、强度 `_validate_effort` 值域校验落默认档）可静默消化，不需迁移代码 |

## 3. 改动清单

### T1：`llm/providers/dream_acp.py` — 模型表收敛 + 补强度轴

1. `_DEMO_MODELS = ["dream/demo-fast", "dream/demo-smart"]`
   → `_DREAM_MODELS = ["dream-creator"]`（改名同步语义——不再是「示例期
   演示表」，而是服务端白名单唯一合法值；`list_dream_models()` 随之返回
   单元素表）。
2. 新增 `DreamAcpLLM.set_effort(value)`，与 `ReasonixAcpLLM.set_effort`
   逐行同构：`session/set_config_option(configId="effort")` 原样透传；
   失败不丢会话（强度是辅助轴，`_effort` 已记，下个新会话生效）。
   `__init__` 增加 `self._effort: str | None = None`。
3. `_ensure_session` 新会话补发链：既有「应用预选模型」之后追加「应用预选
   推理强度」块（reasonix 同款，失败静默保持 agent 默认）。
4. 文档串更新：模块 docstring 差异 ②（「示例期静态表与 DEMO_MODELS
   同步」表述作废）与 `list_dream_models` docstring 改写为「收敛期唯一
   合法值 `dream-creator`，服务端白名单硬校验（对接文档 §4）」。
5. 报错面不改：`set_config_option` 失败仍按既有纪律静默降级（不阻断
   对话）；服务端 `-32602` 文案为可读中文，若未来要上屏可直接展示 error
   帧 `message`，本期不做。

### T2：`llm/registry.py` — dream-acp 注册项回填强度声明

```python
# llm/registry.py（dream-acp BackendSpec 内追加，注释随附）
# 服务端白名单唯一合法档（对接文档 §2/§4：effort 仅接受 auto，
# 实际档位由 Dream CLI 服务端配置决定，IDE 不提供选择）
efforts=("auto",),
default_effort="auto",
```

效果：ModelBar 第四级对 DREAM 由「禁用」变为单选项 `auto`（恒勾选，
用户无可选余地，与对接文档「用户不再做选择」一致——单档菜单仅作
状态呈现）。

### T3：GUI 零改动验证（不写代码，只做核对）

- 模型菜单：DREAM 下仅 `dream-creator`（数据源即 T1 的 `list_dream_models`）；
- 强度菜单：仅 `auto`，默认勾选；
- 强度下发：`ChatPanel.set_effort` 鸭子类型命中新增的
  `DreamAcpLLM.set_effort`（T1-2）；
- 旧记忆值回退：若持久化中存在 `dream/demo-fast` / 旧档位，启动时模型落
  首项（`dream-creator`）、强度 `_validate_effort` 落默认档（`auto`），
  静默不报错。

## 4. 非目标（明确不动）

- ❌ 协议机制与 `llm/providers/acp.py` 公共连接层：零改动（对接文档 §3）；
- ❌ `dream-acp/example/dream` 示例 agent 与 `dream-acp/tools/spike_handshake.py`：
  示例 agent 是协议形态演示夹具，spike §7 断言的正是演示别名切换链路；
  真实联调走真 CLI（`~/.dream/bin/dream` 已在机），不拿示例 agent 冒充。
  `dream_acp.py` docstring 中「与 DEMO_MODELS 同步」的表述在 T1-4 一并
  清除，消除「两边仍同步」的误导；
- ❌ `supports_images=False`：对接文档未涉及视觉能力，维持现状；
- ❌ 服务商显示名 `Dream` / `Dream ACP`：不变；
- ❌ 持久化迁移代码：旧值由既有回退链消化（§2 表末行），不写一次性迁移。

## 5. 风险与对齐

- ⚠️ **同日发布约束**：IDE 改完后若连的是旧版服务端，`dream-creator` 会被
  旧服务端当未知别名拒绝；反之服务端先发布则旧 IDE 的 demo 别名被拒。
  按对接文档 §4「同日发布（或 IDE 先行、服务端紧随）」执行。用户已确认
  本机 dream CLI 为新版本（联调环境），实施后立即实测验证。
- ⚠️ spike 脚本若日后用 `--bin ~/.dream/bin/dream` 跑真 CLI，§7（demo 别名
  断言）必然失败——届时按真实白名单改写断言，本期不动脚本（§4）。

## 6. 验证方案（实施后执行）

1. **静态检查**：`.venv` Python 编译通过；`grep -rn "demo-fast\|demo-smart" llm/`
   应无残留命中（示例 agent 与 spike 目录除外）。
2. **管道级联调**（真 CLI，`~/.dream/bin/dream acp`）：写一次性验证脚本
   （放 `.temp/`，测后即弃），按对接文档 §6 清单逐条断言：
   - initialize 含 agentInfo；
   - session/new 后补发 `model=dream-creator`、`effort=auto`，均成功回执无 error；
   - 普通对话流式回复正常、end_turn 收尾；
   - 故意发 `model=dream/demo-fast`，确认 `-32602` 且文案含「仅接受
     'dream-creator'」，会话可继续复用；
   - `session/cancel` 取消后续聊，会话可复用。
3. **GUI 实测**：启动 Zen Studio，选 DREAM 后台：
   - 模型菜单仅 `dream-creator`，推理强度菜单仅 `auto`（默认勾选）；
   - 发一条消息，流式回复正常结束；强度切换路径（`set_effort`）无异常。
4. **回归**：其余后台（kimi/kilocode/reasonix 等）注册项未动，编译级确认
   即可。

## 7. 实施记录

- 2026-08-12 18:00 前后（UTC+8）：T1/T2 代码落地——
  `llm/providers/dream_acp.py`（`_DEMO_MODELS` → `_DREAM_MODELS =
  ["dream-creator"]`，新增 `set_effort`，`_ensure_session` 补发预选强度，
  文档串更新）、`llm/registry.py`（dream-acp 注册项 `efforts=("auto",)`
  + `default_effort="auto"`）。
- 静态检查通过：编译通过；`list_dream_models()` → `['dream-creator']`；
  `resolve_efforts` → `(('auto',), 'auto')`；`llm/` 下无 demo 别名残留。
- **管道级联调（服务端新版上线后重跑，12 过 / 0 挂）**：
  `.temp/verify_dream_whitelist.py` 直连 `~/.dream/bin/dream acp`——
  握手 agentInfo ✅；`model=dream-creator`、`effort=auto` 成功回执 ✅；
  普通对话流式 + end_turn ✅；旧值 `dream/demo-fast` → -32602 且文案指明
  「仅接受 'dream-creator'」、会话可复用 ✅；cancel 即停 + 续聊复用 ✅。
  （脚本留档 `.temp/` 作回归工具，未按计划弃置。）
- **GUI 无头探针（7 过 / 0 挂）**：`.temp/probe_dream_menubar.py`
  （生产 ModelBar + 生产 qss/字体，offscreen）——模型菜单仅
  `dream-creator`、强度菜单仅 `auto` 且按钮启用默认勾选 ✅；旧记忆值
  `dream/demo-fast` / `high` 静默回退 `dream-creator` / `auto` ✅；
  抓帧 `.temp/model_bar_dream.png`（四按钮链：Dream ▾ ACP ▾
  dream-creator ▾ auto ▾）✅。
- 曾踩环境坑（已随服务端新版上线消解）：本机 dream CLI 一度为旧版
  0.1.0（接受 demo 别名、拒绝 dream-creator），首轮联调 8 过 / 4 挂，
  挂项全部属「旧服务端不认新值」；新版上线后同脚本复测全过。
- 遗留：第二级闭环（用户真机自然操作终裁）——日常使用中顺手确认即可，
  无需专项测试。
