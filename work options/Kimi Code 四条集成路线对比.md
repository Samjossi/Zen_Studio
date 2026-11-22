# Kimi Code 四条集成路线对比

> 用途：个人存档与决策参考。评估「自研 IDE 作为前端接入 Kimi Code 后端」的四条技术路线。
> 整理日期：2026-11-22
> 背景：本机已装 Kimi Code CLI（当时最新正式版 2.7.2），自研 IDE 需要一个可选、可控、健康的接入方式；同时正在跟踪上游 ACP 适配层缺陷（MoonshotAI/kimi-code issue #5095 等，详见 ACP 缺陷跟踪任务文档）。
> 用户量级说明：无官方遥测数字，下表「用户量级」一栏基于公开信号推断——协议发布时间、GitHub issue 报告者分布、官方文档定位、npm 发布状态。属估计值，非精确统计。

## 一、总览对比表

| 路线 | 协议形态 | 交互能力 | ACP 缺陷（#5095 同签名） | 用户量级（估计） | 稳定性承诺 | 接入成本 |
|---|---|---|---|---|---|---|
| ① ACP（现状） | 官方第三方协议（`packages/acp-server`，Zed 系 Agent Client Protocol） | 完整（session/prompt/update/cancel） | **带病**——忙态提前 end_turn、通知轮不可见、通知轮无法 cancel | **小众**：第三方编辑器客户端用户（Orca、Zed、t3code、BB 等），从 issue 报告者分布看全网活跃用户极少 | 名义上有（对第三方客户端的兼容义务），实际修复停滞，#1485 自 7 月挂起 | 低：协议公开、语义简单 |
| ② KAP（`kimi web`） | 官方内部协议（`packages/kap-server`，HTTP REST + WebSocket，Bearer token） | 完整（sessions/:wait/WS 推送/显式 40901 session busy） | **无**——缺陷在 acp-server 适配层，KAP 路径不经过；忙态是显式错误码而非静默吞 prompt | **极少（外部）/ 大（内部）**：协议要求 CLI ≥ 2.7.0、Desktop ≥ 1.4.0，均为 2026-11-19/20 发布——`kimi web` 对外开放仅数日；官方 Desktop 是其最大内部用户 | 无第三方兼容承诺，但版本号 1.0.0、文档与 OpenAPI/AsyncAPI schema 运行时自描述，规格质量高 | 中：要消化 workspace（managed/implicit）、handoff 等领域模型 |
| ③ TUI（PTY 包装） | 无协议——渲染终端字符画面 | 仅终端仿真（IDE 退化为 xterm.js + node-pty 终端模拟器） | 无 | **最大**：TUI 交互界面是 CLI 用户的主入口，官方修复与迭代也集中在这一层 | 不适用（不是集成接口） | 低，但没有集成价值 |
| ④ node-sdk（`@moonshot-ai/kimi-code-sdk`） | 进程内库调用（harness API：prompt/steer/cancel/permission/set-model…） | 最全——含 steer 中途插话、权限回调等深层控制 | 无（直接调 core，无 ACP 适配层） | **零外部用户**：package.json `private: true`，未发布 npm，仅官方 Desktop 内部使用 | 无，上游可随时 breaking change | 高：需 vendor 源码自行构建锁 commit，IDE 后端须用 Node/TS 或起 Node sidecar |

## 二、各路线评价

### ① ACP（当前在用）

- **定位**：官方给第三方编辑器准备的协议，语义干净（session/prompt/update/cancel 四件套），是这个场景下「名分最正」的路线。
- **现状**：带病。正在跟踪的主缺陷（忙态提前 end_turn、通知轮零 update、通知轮 cancel 不到）全部长在 acp-server 的结算边界上；2.5.0–2.7.2 全复现；上游核心 issue 全部 open 无官方排期。我方客户端防线（turn_state.py 四态终态确认 + drain + session/close 分流）已兜住，是当下唯一确定有效的保障。
- **用户量级**：小众。从 issue 报告者看，全网在用 ACP 的客户端屈指可数（Orca、Zed、t3code、BB），这与官方修复动力不足直接相关。
- **结论**：维持现状 + 只读跟踪，不把交付押在官方修复上。

### ② KAP（`kimi web`）

- **定位**：官方内部协议（Kimi Agent Protocol 1.0.0），由 CLI 的 `kimi web` 模式或 Desktop 应用在本机起 HTTP/WS 服务对外提供。文档原话：「Served by the running Kimi Code local server」，来源可以是「a running Desktop app **or** `kimi web` server」。
- **接入方式**：后台起 `kimi web --port 0 --json`，stdout 第一行即返回 origin/ws_url/token；运行时 `GET /api/v1/kap.md`、`GET /openapi.json`（REST）、`GET /asyncapi.json`（WS）自带完整规范，可现场生成客户端代码。用完 SIGTERM。
- **优点**：
  - **物理绕开 ACP 缺陷**——不经过 acp-server 适配层；忙态是显式 `40901 session busy` 错误码，不是 ACP 那种静默吞 prompt 后回空 end_turn；
  - schema 自描述、规格完整；跨进程、语言无关（任何能发 HTTP/WS 的语言都能接）。
- **代价**：无第三方兼容承诺（内部协议）；workspace（managed `wm_*` / implicit `wd_*`）、session lock、handoff 等领域模型偏重；进程守护、崩溃重启、token 轮换要自己管。
- **用户量级**：分两层。内部——官方 Desktop ≥1.4.0 全量用户都在用（Desktop 是 KAP 最大用户），11-22 canary 的 changeset 里还有 kap-agent-collaboration、kap-session-continue 等迭代，说明官方正在持续投入。外部——`kimi web` 随 CLI 2.7.0（2026-11-19）才开放，至今数日，社区未见第三方接入案例，**基本可以断定当前外部使用者极少（很可能为零）**。既是风险（踩坑无人同行）也是机会（协议新、设计干净、还没有历史包袱）。
- **结论**：首选 spike 路线。成本最低、最干净，跑通后 ACP 缺陷对 IDE 侧彻底无关。

### ③ TUI（PTY 包装）

- **定位**：`packages/pi-tui` 全交互终端应用，接口就是字符画面。
- **两种用法**：PTY + xterm.js 把 TUI 嵌进 IDE（IDE 退化为终端模拟器，拿不到结构化事件，截屏解析不可取）；或 `kimi -p` 无头批处理（单向、非交互）。
- **结论**：只留一个「内置终端面板」功能位给想要原生 TUI 的用户，**不作为集成通道**。

### ④ node-sdk（`@moonshot-ai/kimi-code-sdk`）

- **定位**：TypeScript harness API，库级集成，agent core 直接当库调，连「起进程 + 协议」层都省了。examples 覆盖 prompt、steer、cancel、permission、set-model、session 管理等全套程序化驱动。
- **约束**：`private: true` 未发 npm，须 vendor 源码自行构建并锁 commit；进程内 API，IDE 后端限 Node/TS 或需 Node sidecar；无兼容性承诺。
- **用户量级**：零外部用户，纯内部包（官方 Desktop 自用）。 issues/讨论约等于无，遇到坑无社区可查。
- **结论**：KAP spike 若发现领域模型太重、或需要 steer/权限回调级深层控制时的备选，不作为首选。

## 三、决策建议（当前）

1. **短期**：ACP 维持现状（防线已实施、396 项测试全绿），继续只读跟踪上游。
2. **近期**：做 KAP spike——起 `kimi web`，用 openapi.json 生成客户端，把 IDE 最痛的一条链路（发 prompt → 流式更新 → 停止）在 KAP 上复现。
3. **中期**：KAP 跑通后，IDE 默认走 KAP；ACP 降级为兼容模式；防线决策（去留/简化）待官方修复到位后按验证数据拍板。
4. **长期观察**：node-sdk 若未来公开发布（去掉 private），可作为深层控制路线的升级选项。

## 四、证据出处

- KAP 协议文档：`packages/kap-server/docs/kap.md`（KAP 1.0.0；要求 CLI ≥ 2.7.0 / Desktop ≥ 1.4.0；实例发现与 `kimi web --json` 启动方式）
- node-sdk 包状态：`packages/node-sdk/package.json`（`@moonshot-ai/kimi-code-sdk` 0.23.2，`private: true`，MIT，exports `.` / `./exec-server` / `./ssh-askpass`）
- 仓库结构：`apps/desktop`、`packages/core`、`packages/kap-server`、`packages/acp-server`、`packages/node-sdk`、`packages/pi-tui`（单一 monorepo，CLI 与 Desktop 共享 core，是兄弟产品而非前后端）
- 版本时间：CLI 2.7.0（2026-11-19）、2.7.1/2.7.2（2026-11-20）、Desktop 1.4.0（2026-11-19）——KAP 对外开放的起点
- ACP 缺陷跟踪：本仓库《Kimi 上游 ACP 缺陷跟踪任务》文档（六次跟踪快照，截至 2026-11-22，判定「仍无进展」）
- Kimi Code 帮助中心：开始使用 / 常见问题 / 联系与反馈（kimi.com/help/kimi-code/）
