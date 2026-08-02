"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from dataclasses import dataclass
from typing import Iterator, Literal, NotRequired, Protocol, TypedDict


class ImageAttachment(TypedDict):
    """单条图片附件（0340 方案 B 计划 D3：4c 混合载体，path 即全部事实）。

    path：粘贴型为 IDE 落盘 PNG 路径；文件型为用户原文件路径（不复制）。
    发送时才由 provider 层读盘 base64——附件存续期不持字节，内存零占用。
    pasted=True 表示 IDE 落盘产物（附件行删除×时连文件删）；False 为
    用户文件（绝不删）。
    """
    path: str
    mime_type: str   # 由后缀映射（png/jpg/jpeg/gif/webp/bmp）
    pasted: bool


class Message(TypedDict):
    """OpenAI 格式对话消息。

    images（0340 方案 B 计划 D8）：仅 user 消息可携带的图片附件；
    四家 ACP provider 历史由 agent 会话自管、只发末条 user 消息，
    历史图片天然不回传。
    """

    role: Literal["system", "user", "assistant"]
    content: str
    images: NotRequired[list[ImageAttachment]]


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
    """tool_call Chunk 的结构化载荷（路由层直递 output.append_tool_call）。"""
    tool_call_id: str   # 状态更新的锚点键（1425 封存款 F3）
    title: str          # 工具标题（shell 工具 title 即命令本身）
    tool_kind: str      # execute/edit/read/search/fetch/think/other
    summary: str        # 参数摘要（协议层预格式化单行，GUI 不解析 rawInput）
    is_subagent: bool   # tool_kind="think"（task 子代理）时 True（D5 标记）
    command: str        # execute 工具的完整命令（1836 计划 L2-3 输出卡 `$ ` 头）
    #: edit 类 diff 预格式化计数 `+N −M`（0645 融合计划 T1，difflib 生成）
    diff_stat: str
    #: edit 类预解析 hunk 列表 [{head, lines:[(前缀 +/-//空格, 文本)]}]，
    #: 只留 hunk 不留全文，软上限 1000 行 + truncated 标记（0645 计划 D4）
    diff_hunks: list[dict]
    #: diff_hunks 被软上限截断时 True（卡 body 尾注用）
    diff_truncated: bool
    #: 通用入参区（0645 计划 D3）：rawInput 关键字段按 kind 预格式化多行
    #: 文本（已知键 + JSON pretty 兜底），GUI 直渲不解析 rawInput
    input_detail: str


class ToolUpdatePayload(TypedDict, total=False):
    """tool_call_update Chunk 的结构化载荷（路由层补 title 后递 append_tool_update）。"""
    tool_call_id: str
    status: str         # in_progress / completed / failed
    title: str          # 路由层自簿记补入（协议层不携带，total=False）
    error: str          # failed 时的错误首行（预截断 80；旧轨行内尾注保留）
    #: failed 完整错误文本（0645 计划 §2.3-5：净化 + 软上限 1000 行保尾；
    #: 新轨 failed 卡 body 数据源，旧轨不消费）
    error_detail: str
    output: str         # 输出正文（0645 计划 §2.3-1 升级：净化 + 软上限
                        # 1000 行，方向按 kind——execute 保尾、其余保头；
                        # in_progress 帧为尾窗规格截尾末 5 行；
                        # 旧轨路由层仅 execute 工具放行上屏，其余丢弃；
                        # 新轨全工具卡 body 承接）
    output_total_lines: int  # 输出原始行数（截断前；GUI 超限行尾注用）
    command: str        # 路由层自簿记补入（execute 命令，输出卡 `$ ` 头）
    #: think（task 子代理）completed 帧成果摘要（0645 计划 D5：`<task_result>`
    #: 提取、无标记取全文、全量不截 10 行——软上限 1000 行保头由协议层执行）
    result_summary: str


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

    def poll_usage(self) -> "UsageStats | None":
        """轮次内主动取用量快照（可选能力，默认 None 表示不支持）。

        背景（0117 计划）：推送型后端（kilocode/opencode/reasonix）的
        `usage_update` 由 agent 决定时机（轮末一条），IDE 无中间数据可挖；
        kimi 的用量落在本地 wire.jsonl 且轮次内每次 API 调用后增量写盘
        （0117 T0 实证），可由 provider 主动读取。GUI 侧 QTimer 在轮次进行
        中周期调用本方法，非 None 即刷新徽章。

        约束：实现须廉价（只读小数据/文件尾部）、幂等、线程安全（从 GUI
        线程调用，与 chat 所在 worker 并发），任何失败静默返回 None，
        不得抛出、不得阻塞、不得臆造估算值。
        """
        return None
