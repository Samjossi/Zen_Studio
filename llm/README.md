# LLM 包说明

> **状态**：已实施
> **范围**：`llm/` 包 — LLM 调用薄层（与界面解耦的后端逻辑）
> **时间**：2026-07-18 13:01（UTC+8）

---

## 1. 概述

`llm/` 是 Zen Studio 的 LLM 调用层，与 [`gui/`](../gui/) 平级。职责单一：把"多轮消息 → 流式文本"抽象为统一接口 `LanguageModel`，经注册表按名称取用 provider。前端仅通过 `from llm import get_llm` 消费，依赖方向为"前端 → 后端"，包内不 import 任何 GUI 代码。

设计蓝本：theia-zen `LanguageModel` Protocol + 注册表模式，选型依据见 [`work options/2026-0718-1215_对话栏AI聊天面板选型报告.md`](../work%20options/2026-0718-1215_对话栏AI聊天面板选型报告.md)；实施记录见 [`文档/修改记录/2026-0718-1219_对话栏AI聊天面板实施计划.md`](../文档/修改记录/2026-0718-1219_对话栏AI聊天面板实施计划.md)。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| [`__init__.py`](__init__.py) | 包初始化：创建全局 `registry` 并注册内置 provider，对外导出 `get_llm` |
| [`base.py`](base.py) | `LanguageModel` Protocol + `Message` 类型别名 |
| [`registry.py`](registry.py) | `LLMRegistry`：名称 → provider 实例的注册表 |
| [`providers/__init__.py`](providers/__init__.py) | provider 子包标记（每家厂商一个文件） |
| [`providers/deepseek.py`](providers/deepseek.py) | `DeepSeekLLM`：openai SDK 直连 DeepSeek 端点（流式 + 版本项切换 + 思考模式参数化 + 思维链分流） |
| [`providers/kimi_cli.py`](providers/kimi_cli.py) | `KimiCliLLM`：本机 Kimi Code CLI 后端（spawn `kimi -p --output-format stream-json` 子进程 + session_id 续接） |

## 3. 接口设计

```python
# 消息格式（OpenAI 格式）
Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}

@dataclass(frozen=True)
class Chunk:
    kind: Literal["text", "reasoning"]  # 正文 / 思维链
    text: str

class LanguageModel(Protocol):
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        """发送多轮消息，流式产出文本/思维链块。"""
        ...
```

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` | 统一接口，返回 `Iterator[Chunk]` 逐块产出，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 为正文增量；`kind="reasoning"` 为思维链增量（仅当次显示，**不回传入请求历史**——无工具调用时 API 会忽略，统一不回传最安全） |
| `LLMRegistry` | `register(name, llm)` / `get(name)` / `names()`；取未注册名称时抛 `KeyError` 并列出可用项 |
| `ModelVersion` | 版本项：`api_id` + `alias` + `thinking`（思考开关）+ `send_thinking`（是否下发 thinking 参数） |
| `DeepSeekLLM` | provider：端点 `https://api.deepseek.com`，openai SDK `stream=True`；`MODELS` 定义 4 个版本项（V4 Flash/Pro × 思考/非思考）；`set_version()` 校验切换（下次请求生效），经 `extra_body` 下发思考开关；`label_for(version)` 单点维护显示格式、`current_label` 返回当前版本项显示名 |
| `KimiCliLLM` | provider：本机 Kimi Code CLI（OAuth 自管凭证，代码库零密钥）；spawn 子进程逐行解析 JSONL，assistant 正文为**消息粒度**（非 token 流式），`tool_calls` 复用 reasoning 通道灰字展示；历史由 CLI 会话管理（meta 行 `session_id` 续接），`set_model(alias)` 切换模型、`reset_session()` 开新会话；⚠️ `-p` 固定 auto 权限，agent 可在项目目录读写文件与执行命令 |

## 4. 使用方式

```python
from llm import MODELS, DeepSeekLLM, get_llm

llm = get_llm("deepseek")  # 默认即 deepseek，参数可省略
if isinstance(llm, DeepSeekLLM):
    # set_version 为 DeepSeek 专有（非 Protocol 成员），跨 provider 需 isinstance 判断
    llm.set_version(MODELS[1])  # 切到 V4 Flash · 非思考（默认 MODELS[0] V4 Flash · 思考）
for chunk in llm.chat([{"role": "user", "content": "你好"}]):
    if chunk.kind == "reasoning":
        print(chunk.text, end="", flush=True)  # 思维链：仅显示，勿入历史
    else:
        print(chunk.text, end="", flush=True)  # 正文
```

> ⚠️ `chat()` 是阻塞式 generator。GUI 中必须放后台线程消费，经信号逐块上屏，避免冻结主线程（参考实现：[`gui/panels/chat/worker.py`](../gui/panels/chat/worker.py)）。

多轮对话由调用方维护消息列表，将完整历史随请求发送；网络异常由 provider 抛出，调用方捕获后上屏，不崩溃。

## 5. 密钥安全

- 密钥仅从项目根 `api_key/deepseek` 文件读取：跳过备注行，取首个 `sk-` 开头的纯 ASCII 行
- 代码中不出现任何密钥字面量；`api_key/` 已在 `.gitignore`
- 密钥文件缺失或无有效 key 时，provider 初始化抛 `ValueError`（含文件路径提示）

## 6. 新增 provider

1. 在 [`providers/`](providers/) 下新建 `<厂商>.py`，实现 `LanguageModel` 协议（`chat()` 为流式 generator；历史策略由各实现自决——DeepSeek 全量回传、KimiCli 仅取末条 user 消息 + CLI 侧会话）
2. 在 [`llm/__init__.py`](__init__.py) 中导入并 `registry.register("<名称>", XxxLLM())`（外部 CLI 类 provider 先 `shutil.which` 检测可用性再注册）
3. 凭证管理：API key 类放项目根 `api_key/<名称>`（读取仿 `deepseek._load_api_key`）；CLI 类凭证由 CLI 自管，代码库不出现密钥
