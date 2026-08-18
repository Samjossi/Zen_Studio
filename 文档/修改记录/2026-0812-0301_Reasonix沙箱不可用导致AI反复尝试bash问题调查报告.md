> ⚠️ **归档声明**：本文档为历史快照，记录了特定时间点的决策与实施状态。
> 当前代码状态以对应 `.py` 源文件为准，本文档内容可能已过时，仅供参考。

# Reasonix 沙箱不可用导致 AI 反复尝试 bash 问题调查报告

> 调查日期：2026-08-12
> 状态：**已审阅，方案 A 已实施并实测通过**（2026-08-12，见《2026-0812-0311_Reasonix沙箱关闭改造计划.md》）
> 触发材料：用户截图 `pasted-20260812-025358.png`（Reasonix 会话中 AI 连续两次 bash 调用撞沙箱报错，第三次回退 web_fetch，最终被手动停止）
> 参考入口：《文档/理论依据/后端CLI工具维护说明手册.md》§4 Reasonix

---

## 1. 问题现象（截图还原）

Reasonix 会话中，AI 为获取当前时间：

1. 调用 bash：`TZ=Asia/Taipei date '+%Y-%m-%d %H:%M'` → 报错：
   > `bash sandbox requested but unavailable on this host; refusing to run unconfined. Install bubblewrap (bwrap) or set [sandbox] bash = "off" in config.toml / Settings → Sandbox to restore pre-1.16 unconfined shell execution.`
2. **换一条命令再次调用 bash**：`TZ=Asia/Taipei .venv/bin/python -c "..."` → **同样的沙箱报错**。
3. 回退到 web_fetch 走网络时间 API → 用户手动停止。

用户观感：AI「总是试图突破沙箱」。同一台主机上 Kimi Code、OpenCode、Kilo Code 均无此问题。

---

## 2. 事实清单（均附来源）

### 2.1 报错确实出自 Reasonix 本体，且 1.16 起行为改变

- 报错字符串位于仓库 `internal/sandbox/sandbox.go:75/83` 的 `UnavailableMessage()`。
  来源：<https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/internal/sandbox/sandbox.go>
- **1.16 之前** bash 无沙箱直接执行；**1.16 起改为 fail-closed**：沙箱探测失败时**拒绝执行**，而不是退回裸跑。相关合并 PR：#5830「fail safe when bash sandbox is unavailable」、#5839「prevent bash sandbox bypass on platforms without OS sandboxing」，收录于 v1.16.0 发布日志。
  来源：<https://github.com/esengine/DeepSeek-Reasonix/releases/tag/v1.16.0>
- 沙箱后端：Linux = bubblewrap (bwrap)；macOS = Seatbelt；Windows 无 OS 级沙箱（强制 off）。沙箱内根文件系统只读，仅工作区可写，默认断网（`--unshare-net`）。
  来源：<https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/GUIDE.md>（L724–789）

### 2.2 沙箱默认开启，探测方式是「真实启动一次 bwrap」

- `[sandbox] bash` 缺省或任何非 `off` 值均解析为 `enforce`（`internal/config/config.go:1238–1252`）。
- 可用性探测**不是查 PATH**，而是实际执行 `bwrap --ro-bind / / --dev /dev --proc /proc -- true`（`usableBwrap()`，`seatbelt_other.go:21–35`）。因此「装了 bwrap 但跑不起来」同样判定为不可用。
- 已知对应 issue：#8370「bwrap 在 PATH 中却报不可用（实为 AppArmor 问题）」——Ubuntu 24.04 场景，已按现设计关闭。
  来源：<https://github.com/esengine/DeepSeek-Reasonix/issues/8370>

### 2.3 本机实测复现了「装了但不可用」

- 本机已安装 `bwrap 0.9.0`（`/usr/bin/bwrap`），但实测报 `bwrap: setting up uid map: Permission denied`。
- 原因：Ubuntu 24.04 默认 `kernel.apparmor_restrict_unprivileged_userns = 1`，禁止非特权用户命名空间，bwrap 无法建立 uid map。
- **即：本机不是缺 bwrap 包，而是内核/AppArmor 加固策略拦住了 bwrap。**

### 2.4 代理循环为什么「死循环换命令重试」

- Reasonix 设计明确：**工具执行错误只作为文本回喂给模型，不是致命错误**（SPEC §6：「Tool execution errors are fed back to the model, not fatal.」）。宿主不会自动关沙箱、不会弹窗问用户，修复提示写在错误文本里，由模型自行决定。
  来源：<https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/SPEC.md>
- 有「重复失败熔断」机制，但**按精确同一条命令计数**：Auto 模式下同一操作连续失败 3 次才阻断（`internal/recovery/decision.go:147`）。**盲点：模型每次换一条命令就算「不同操作」，熔断永远不触发**——这正是截图中「date 不行换 python 再试」死循环的直接机制原因。
- 最接近的开放 issue：#6132（Bash 卡住）、#8213（无限恢复循环，根因不同）。没有完全对应的「沙箱不可用导致反复试 bash」issue。

### 2.5 为什么其他 CLI 没事

- Kimi Code CLI **没有 OS 级 bash 沙箱**，只有权限审批体系（Bash 需审批 / YOLO 跳过），「沙箱不可用」这一失败模式根本不存在。
  来源：<https://www.kimi.com/code/docs/kimi-code-cli/reference/tools.html>
- 所以同主机上只有 Reasonix 复现——不是模型更「野」，而是只有它有这道会失败的闸门。

---

## 3. 根因结论（三层叠加）

1. **环境层**：本机 bwrap 已装但被 AppArmor 策略（`apparmor_restrict_unprivileged_userns=1`）拦截 → 沙箱后端不可用。
2. **产品层**：Reasonix ≥1.16 默认 enforce + fail-closed → 所有 bash 调用一律被拒绝。
3. **循环层**：错误仅文本回喂模型，防重复熔断按「同一条命令」计数，模型换命令即绕过 → 表现为反复撞墙、疑似「试图突破沙箱」。

**澄清用户观感**：AI 并非在「突破沙箱」，恰恰相反——它每次都在走沙箱闸门并被 fail-closed 拦下；问题是没有「同一失败原因」级别的熔断，模型又倾向于换命令重试，造成死循环观感。

---

## 4. 解决方案（二选一，待用户定夺）

### 方案 A：修好 bwrap，保留沙箱（推荐）

放行非特权用户命名空间（Reasonix 官方 CI 同款做法，见 `.github/workflows/ci.yml:97–101`）：

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
# 持久化：
echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-bwrap.conf
```

- 优点：保留沙箱隔离，符合 1.16 后的安全设计。
- 代价：放宽 Ubuntu 24.04 默认安全加固，需用户自行权衡（影响全系统，不止 Reasonix）。
- 验证：会话内 `/sandbox` 或 `reasonix doctor` 查看沙箱后端状态。

### 方案 B：显式关闭沙箱，恢复 1.16 前行为

编辑 `~/.reasonix/config.toml`（或项目级 `./reasonix.toml`），或桌面端 Settings → Sandbox：

```toml
[sandbox]
bash = "off"
```

- 优点：零系统改动，立即生效，行为与 Kimi Code 等无沙箱工具一致。
- 代价：shell 命令不再隔离（写文件工具仍受 `workspace_root` 约束）。

---

## 5. 遗留事项

- GitHub 匿名 API 额度耗尽，未能读 #8370 评论区的完整处理讨论；如需要可带 token 复查。
- 调研浅克隆留存于 `.temp/DeepSeek-Reasonix`，确认报告后可删除。
- 若后续要根治「换命令绕过熔断」的循环问题，可考虑向 Reasonix 上游提 issue（建议方向：熔断按「错误类别/工具」而非「精确命令」计数）。

---

*本报告基于官方仓库代码、文档与 issue 的一手来源，并在本机实测复现了 bwrap 不可用场景。*
