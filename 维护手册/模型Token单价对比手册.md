# 模型 Token 单价对比手册

> **创建日期**：2026-10-04
> **最后更新**：2026-10-04
> **版本**：v1.0
> **用途**：汇总 Zen Studio 各后端当前可用模型的官方 Token 单价，供选型与成本估算对比。价格有时效性，引用前请以官方定价页为准。

---

## 1. 口径说明

- 除特别标注外，价格单位均为 **每百万 tokens（1M tokens）**，输入与输出分别计费。
- 数据查询日期：**2026-10-04**，来源为各厂商官方定价页及当日公开资料（见 §6）。
- DeepSeek 自 2026-08-17 起实行 **峰谷分时定价**：高峰时段为北京时间 **9:00–12:00、14:00–18:00（工作日）**，价格为空闲时段的 2 倍。
- 「缓存命中」指上下文前缀命中服务商缓存的输入部分，命中部分单价远低于未命中（DeepSeek 差约 50 倍，Kimi K3 差 10 倍），长 system prompt / 多轮对话场景影响极大。

---

## 2. Zen Studio 后端与模型对应

| 后端 | 当前枚举模型（2026-09-17 实测基线） | 计费方式 |
|:---|:---|:---|
| kimi-acp | `k3`、`k3-256k`、`kimi-for-coding`（K2.8 Preview）、`kimi-for-coding-highspeed` | Kimi Code **会员订阅制**（CLI 走额度，不按 token 计费）；§4 的 API 单价仅供横向参考 |
| reasonix-acp | `deepseek-v4-pro`、`deepseek-v4-flash`（新模型需自行写入 `~/.reasonix/config.toml`，如 `deepseek-flash`） | DeepSeek 官方 API 按量计费 |
| opencode-acp | `deepseek-v4-flash`、`deepseek-v4-pro`、`k3`、`k3-256k`、`kimi-for-coding*`（另有两个已停用旧别名 `deepseek-chat`/`deepseek-reasoner`） | 上游官方同价 |
| kilocode-acp | — | **已封存**（个人维护状态，不再更新） |
| dream-acp | — | 开发中，无计费 |

---

## 3. DeepSeek 价格（人民币 / 每百万 tokens）

官方同时公布人民币与美元价，峰谷价差 2 倍。

### 3.1 `deepseek-flash`（DeepSeek-V4.1-Flash，2026-09-10 起的主力模型）

| 计费项 | 空闲时段 | 高峰时段 |
|:---|:---:|:---:|
| 输入·缓存命中 | ¥0.02（$0.003） | ¥0.04（$0.006） |
| 输入·缓存未命中 | ¥1.00（$0.15） | ¥2.00（$0.30） |
| 输出 | ¥4.00（$0.60） | ¥8.00（$1.20） |

类型定位：552B MoE，全新 Causal-Encoder-Decoder 结构；**1M 上下文、最大输出 384K**；原生多模态（支持图片输入）；支持思考/非思考模式切换。官方口径：性能、速度、费用全面超越 V4 Pro。

### 3.2 `deepseek-v4-flash`（上一代 V4 Flash，2026-08-21 定价）

| 计费项 | 空闲时段 | 高峰时段 |
|:---|:---:|:---:|
| 输入·缓存命中 | ¥0.05（$0.007） | ¥0.10（$0.014） |
| 输入·缓存未命中 | ¥1.50（$0.22） | ¥3.00（$0.44） |
| 输出 | ¥4.50（$0.66） | ¥9.00（$1.32） |

⚠️ 已非最新。2026-09-10 Flash 系列调价（缓存命中降 60%、未命中降 33%、输出降 11%）即对应 V4.1 价格；旧名 `deepseek-v4-flash` 仍可调，但无价格优势，建议迁移到 `deepseek-flash`。

### 3.3 `deepseek-v4-pro`（V4 系列旗舰，2026-08-13 上线）

| 计费项 | 空闲时段 | 高峰时段 |
|:---|:---:|:---:|
| 输入·缓存命中 | ¥0.15（$0.022） | ¥0.30（$0.044） |
| 输入·缓存未命中 | ¥4.50（$0.66） | ¥9.00（$1.32） |
| 输出 | ¥13.50（$1.98） | ¥27.00（$3.96） |

⚠️ 关键备注：官方通告自 V4.1 Flash 上线起、V4.1 Pro 上线前，**对 V4 Pro 的请求全部路由至 V4.1 Flash，并按 V4.1 Flash 单价计费**。即当前选 Pro 实际跑的是 V4.1 Flash、按 Flash 价格收钱，上表仅为标价留档。

---

## 4. Kimi（Moonshot）价格

Zen Studio 经 Kimi Code CLI（会员订阅）使用时不按 token 计费；以下为 Moonshot 开放平台 API 按量价，供与 DeepSeek 横向对比。

| 模型 | 类型定位 | 输入·缓存命中 | 输入·缓存未命中 | 输出 | 上下文 |
|:---|:---|:---:|:---:|:---:|:---:|
| Kimi K3（`k3` / `k3-256k`） | 旗舰推理（2.8T MoE），始终 thinking 模式 | ¥2（$0.30） | ¥20（$3.00） | ¥100（$15.00） | 1M |
| Kimi K2.7 Code | 编程专用 | ¥1.30（$0.19） | ¥6.50（$0.95） | ¥27（$4.00） | 256K |
| Kimi K2.7 Code Highspeed | 编程专用·高速版 | ¥2.60 | ¥13.00 | ¥54 | 256K |
| `kimi-for-coding`（K2.8 Preview） | 编程订阅专用 | 未公布按量价，仅 Kimi Code 会员订阅可用 | — | — | 1M（全会员档位） |

⚠️ K3 的 reasoning tokens 按输出价（¥100/M）计费，无法关闭思考模式，只能调 low/high/max 档位——短回答前可能先烧数千 reasoning tokens，账单体感会比标价贵。

---

## 5. 横向对比速查（人民币 / 每百万 tokens）

统一基准：**高峰或标准时段、缓存未命中输入 + 输出**。

| 模型 | 输入 | 输出 | 相对档位 |
|:---|:---:|:---:|:---|
| DeepSeek V4.1 Flash（`deepseek-flash`） | ¥2 | ¥8 | 🟢 最低（空闲再减半至 ¥1/¥4） |
| DeepSeek V4 Flash（`deepseek-v4-flash`，旧） | ¥3 | ¥9 | 🟢 低，但已被 V4.1 取代 |
| Kimi K2.7 Code | ¥6.5 | ¥27 | 🟡 中 |
| DeepSeek V4 Pro（标价，实际路由到 V4.1 Flash） | ¥9 | ¥27 | 🟡 中（名义） |
| Kimi K3 | ¥20 | ¥100 | 🔴 高（约为 V4.1 Flash 的 10 倍） |

直观结论：**DeepSeek V4.1 Flash 是当前可用模型中单价最低的一档**，输出价仅为 Kimi K3 的 1/12；K3 买的是旗舰推理能力上限，K2.7 Code 居中。

---

## 6. 省钱要点

1. **错峰**：DeepSeek 空闲时段（高峰外全部时间）半价，可延迟的批量任务尽量错峰。
2. **缓存命中**：稳定的 system prompt 放最前，DeepSeek 命中价差 50 倍（¥0.02 vs ¥1）、Kimi K3 差 10 倍。
3. **K3 reasoning 档位**：账单大头常是 reasoning tokens，调低 effort 档位可减少其体量。
4. **别选旧别名**：`deepseek-v4-flash` 比 `deepseek-flash` 贵且无优势；`deepseek-chat`/`deepseek-reasoner` 已于 2026-07-24 停用。
5. **订阅 vs 按量**：日常编程走 Kimi Code 会员订阅（kimi-for-coding 系列）不按 token 计费，重度使用时通常比 K3 API 按量划算。

---

## 7. 数据来源（2026-10-04 查询）

- DeepSeek V4.1 Flash 定价与 V4 Pro 路由通告：[DeepSeek V4.1 Flash 发布（前端进阶之旅）](https://feinterview.poetries.top/ai-monitor/news/deepseek-9-10-v4-1-flash-v4-pro)、[DeepSeek 调价公告（网易）](https://www.163.com/dy/article/L6D49CK10519QIKK.html)、[Apifox 定价一览](https://apifox.com/apiskills/how-to-use-deepseek-v4-1-flash-api-cn/)
- DeepSeek 峰谷定价机制：[ChooseAI 价格表](https://www.chooseai.net/ai-news/detail/7413/)、[搜狐时间线（劳动报）](https://timeline.sohu.com/news/UrLxrfyX7f)
- DeepSeek V4 Pro 定价：[Free AI Perks](https://www.getaiperks.com/zh/ai/deepseek-v4-pro-pricing)
- Kimi K3 定价：[Moonshot 官方口径（getaiperks 转引）](https://www.getaiperks.com/zh/ai/kimi-k3-pricing-free-credits)、[K3 使用教程（AI工具宝箱）](https://www.aitoollab.cn/articles/kimi-k3-usage-guide-2026/)、[K3 reasoning 计费说明（AvenChat）](https://avenchat.com/zh/blog/kimi-k3-pricing)
- Kimi K2.7 Code 定价：[Kimi 官方资源页](https://www.kimi.com/ja/resources/kimi-k2-7-code-pricing)

---

*维护说明：价格变动频繁，修改本文档时请同步更新抬头的「最后更新」日期与版本号，并核对 §7 来源。*
