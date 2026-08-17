# ACP terminal/* 反向能力五 agent 实测报告

> **状态**：实测完成（kimi / reasonix / kilocode 结论确定；opencode 结论确定；dream 未触发工具调用，结论为「未使用」）
> **范围**：五个 ACP 后端（kimi / opencode / kilocode / reasonix / dream）对 `clientCapabilities.terminal: true` 的行为矩阵
> **时间**：2026-08-17 15:22（UTC+8，编写）
> **工具**：`scripts/spike_terminal_capability.py`（本报告全部结论可由该脚本复跑复核）
> **关联**：会话计划「ACP terminal/* 反向能力通用接口实施计划」阶段 0（本报告即其产出与裁决依据）

---

## 1. 实测方法

裸 ACP 握手时声明 `terminal: true`，下发必触发 Bash 的 prompt，观察 agent 是否发
`terminal/create` 反向请求。spike 对 terminal/* 请求做**忠实客户端模拟**（真实执行命令、
回真实输出与退出码），agent 走完全程且轮次正常收尾（`stopReason=end_turn`）才判「支持」。

复跑命令：

```bash
.venv/bin/python scripts/spike_terminal_capability.py --bin <agent二进制> -v
# opencode 需指定已配置模型（默认 big-pickle 在本机挂起 300s 零帧，实证）：
.venv/bin/python scripts/spike_terminal_capability.py --bin ~/.opencode/bin/opencode --model kimi-for-coding/k3 -v
```

## 2. 行为矩阵（实测值）

| agent | 版本/路径 | terminal/create | 全程走向 | 结论 |
|:---|:---|:---|:---|:---|
| **kimi** | `~/.kimi-code/bin/kimi` | ✅ | create → wait_for_exit → output → release；argv 形态（`/bin/bash -c <串>`） | **支持，开箱即用** |
| **reasonix** | `~/.local/bin/reasonix` | ✅ | create → wait_for_exit → output → release；**command 为整行 shell 串、args 为空** | **支持**（实现差异点见 §3） |
| **kilocode** | `~/.local/bin/kilocode` | ❌ | 触发 `bash` 工具调用但全程零 terminal/* 帧，命令内部执行 | 不支持 |
| **opencode** | `~/.opencode/bin/opencode` v1.18.9 | ❌ | 指定模型后触发 `bash` 工具调用，零 terminal/* 帧，命令内部执行 | 不支持 |
| **dream** | `~/.dream/bin/dream` | ❌（未触发） | 两种 prompt 措辞下均未发起任何工具调用即 end_turn | 未使用（其工具触发条件未命中，非能力否定实证） |

## 3. 对实施计划的三点实证输入

1. **裁决点通过**：kimi 支持 terminal/*——全量实施（连接层通用分发 + GUI 桥 + AI tab）有真实收益面，kimi 与 reasonix 两家直接受益。
2. **create 载荷两套形态**：kimi 传 argv（command=`/bin/bash`，args=[`-c`, 整串]）；reasonix 传整行 shell 串（args 空）。GUI 桥 `create` 实现须兼容——args 为空时经 shell 执行整串（spike 的 `FakeTerminalBank` 已验证该处理）。
3. **不支持方零回归保证有效**：kilocode/opencode 在 `terminal: true` 声明下行为与现状完全一致（内部执行、正常收尾）——证实"能力声明由支持矩阵逐 provider 控制"的设计是必要的：对这两家必须继续声明 `terminal: false`，否则无收益（但也无害）。

## 4. spike 过程踩坑记录（脚本已修，留档）

- **并发目录竞争**：四路并发首跑共享同一 spike 工作目录，各进程 `rmtree` 互删导致 opencode/kilocode 首跑假死、reasonix 命令执行失败。修复：工作目录按 `agent名+pid` 唯一化（`.temp/spike_terminal_<agent>_<pid>/`）。
- **shell 串执行**：reasonix 的 create 不带 args，直连 exec 会 ENOENT；args 为空时改 `shell=True` 执行整串后通过。
- **opencode 默认模型挂起**：默认 `opencode/big-pickle` 在本机 300s 零帧无响应；`--model kimi-for-coding/k3` 指定已配置模型后正常。spike 已加 `--model`/`--prompt` 参数。

## 5. 后续

- 阶段 1-3（连接层 `terminal/*` 分发 + `AgentTerminalBridge` + AI tab）按已批准计划实施，支持矩阵初值：kimi=true、reasonix=true、kilocode/opencode/dream=false。
- kilocode/opencode 若未来版本接入 terminal/*，改矩阵常量重跑本 spike 验证即可启用。
