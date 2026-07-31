"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, TypedDict


class Message(TypedDict):
    """OpenAI 格式对话消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class UsageStats:
    """上下文用量载荷定型（1454 计划 T3：扩 source 来源字段）。

    口径照单接受 agent 产出端语义：used = input + cache.read（不含
    output/reasoning，偏保守但不失真）；size 为模型上下文窗口上限；
    cost 为会话累计成本（agent 未给则 None，本期解析留存不上屏）。

    source 数据来源分级（GUI 按级标注口径，D3）：
    - "push"：ACP `usage_update` 推送（kilocode/opencode，最准，同帧 size）；
    - "transcript"：agent 会话落盘记录读出的真值（kimi wire.jsonl
      usage.record，精度等同 push 但非协议推送、轮次后异步可得）；
    - "estimate"：IDE 侧对 transcript 消息文本的 chars/4 粗估
      （reasonix；不含 agent 运行时注入开销的精确值，仅量级参考）。
    """

    used: int            # 已用 token（input + cache.read，agent 口径）
    size: int            # 上下文窗口上限
    cost: float | None   # 会话累计成本（USD；agent 未给则 None）
    source: Literal["push", "transcript", "estimate"] = "push"


@dataclass(frozen=True)
class Chunk:
    """流式块：kind 区分正文、思维链、上下文用量通知与 AI 活动信息。

    kind="usage" 时 text 为空串、载荷经 usage 字段携带（用量不上屏进
    输出区文本流，由 GUI 路由到徽章控件）。

    kind="tool_call" / "tool_call_update" / "todo"（1602 计划 T1：
    对话区 AI 活动信息充分展示）时载荷经 payload 字段携带，schema
    见下方三个 TypedDict；text 字段为协议层预填的兜底显示单行——
    GUI 对未识别 kind 按 text 渲染，新协议层配旧 GUI 不崩
    （多标签/多开版本错配防御）。新三 kind 与 reasoning 同约束：
    仅上屏，不入历史、不回传。
    """

    kind: Literal["text", "reasoning", "usage", "tool_call", "tool_call_update", "todo"]
    text: str
    usage: UsageStats | None = None
    payload: dict | None = None


class ToolCallPayload(TypedDict, total=False):
    """kind="tool_call" 的载荷（协议层 map_session_update 产出，1602 计划 T1）。"""
    tool_call_id: str   # 状态更新的锚点键（1425 封存款 F3）
    title: str          # 工具标题（shell 工具 title 即命令本身）
    tool_kind: str      # execute/edit/read/search/fetch/think/other
    summary: str        # 参数摘要（协议层预格式化单行，GUI 不解析 rawInput）
    is_subagent: bool   # tool_kind="think"（task 子代理）时 True（D5 标记）


class ToolUpdatePayload(TypedDict, total=False):
    """kind="tool_call_update" 的载荷。"""
    tool_call_id: str
    status: str         # in_progress / completed / failed
    title: str          # 路由层自簿记补入（协议层不携带，total=False）
    error: str          # failed 时的错误首行（预截断；尽力而为，可缺省）


class TodoEntry(TypedDict, total=False):
    """todo 清单条目（plan 快照与 todowrite rawInput 两通道同构，1425 封存款 F1）。"""
    content: str
    status: str         # pending / in_progress / completed / cancelled
    priority: str       # high / medium / low（渲染层本期不消费，载荷留存）


class TodoPayload(TypedDict):
    """kind="todo" 的载荷：entries 为全量快照。"""
    entries: list[TodoEntry]


class LanguageModel(Protocol):
    """统一 LLM 接口：流式 chat，与 UI 解耦。"""

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        """发送多轮消息，流式产出文本/思维链块。

        :param messages: OpenAI 格式的消息列表
        :yield: Chunk（kind="text" 为正文增量，kind="reasoning" 为思维链增量，
            思维链仅当次显示，调用方不得回传入请求历史；kind="usage" 为上下文
            用量通知，路由徽章不上屏；kind="tool_call"/"tool_call_update"/"todo"
            为 AI 活动信息（1602 计划），与 reasoning 同约束——仅上屏，
            不入历史、不回传）
        """
        ...

    def cancel(self) -> None:
        """取消当前进行中的 chat 调用（协议级，立即打断阻塞等待）。

        无进行中调用时须为无害 no-op；须可从非 chat 所在线程安全调用
        （典型场景：GUI 线程点击停止，chat 在 worker 线程阻塞）。
        取消后 chat 生成器应尽快结束（正常耗尽或抛异常均可，
        残余资源由生成器 finally 兜底清理）。
        """
        ...
