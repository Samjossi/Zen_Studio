"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from collections.abc import Mapping
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
    各 ACP provider 的历史由 agent 会话自管、只发末条 user 消息，
    历史图片天然不回传。
    """

    role: Literal["system", "user", "assistant"]
    content: str
    images: NotRequired[list[ImageAttachment]]


@dataclass(frozen=True)
class UsageStats:
    """上下文窗口用量载荷；used/size 均采用 agent 或其记录的上报口径。

    source 区分数据来源而非计费方式：
    - "push"：ACP `usage_update`，agent 对外报告的当前上下文占用与窗口；
      可包含 agent 自己的未实测尾部估算，不代表会员额度或纯 API 实测值；
    - "transcript"：后台自身落盘记录中的实测上下文；具体水位字段与
      兜底口径归各后台 provider，用于轮内刷新与 ACP 缺失兜底，窗口另取
      该后台模型目录的上报值；
    - "estimate"：IDE 对 transcript 消息文本的 chars/4 粗估，仅作量级参考。

    cost 为会话累计成本（agent 未给则 None），与上下文占用分开理解。
    """

    used: int            # 已用上下文 token（agent/记录口径）
    size: int            # 上下文窗口上限
    cost: float | None   # 会话累计成本（USD；agent 未给则 None）
    source: Literal["push", "transcript", "estimate"] = "push"


@dataclass(frozen=True)
class TurnSpeed:
    """一轮对话的平均输出速度载荷（2026-1023-1632 计划）。

    口径：output_tokens 为轮内各次 API 调用的输出 token 求和（transcript
    源的单次 output 是单次调用值，非累计，直取不差分）；gen_seconds 仅计
    模型生成时间（各次调用按请求与响应的记录时间配对求和），工具执行与
    审批等待落在两次请求之间的缝隙里，天然排除在外——分母掺入非生成
    时间会失真，这是本定型的核心约束。

    目前仅支持 transcript 源的后台产出（逐次 output 才有真值；push 帧与
    estimate 均无 output 计量）。
    """

    output_tokens: int   # 轮内输出 token 合计
    gen_seconds: float   # 轮内模型生成耗时合计（秒）


@dataclass(frozen=True)
class TurnConsumption:
    """一轮对话的真实 token 消耗载荷（2026-1114-1452 计划）。

    口径：轮内各次 API 调用的实测消耗求和，与 UsageStats 的上下文
    窗口占用是两套语义，互不复用数值。四项拆分保留缓存读写（计费
    单价不同）；turn_id 供落盘去重（同轮重复收尾不双记）。
    目前仅具备逐次真值通道的后台产出，其余后端恒 None。
    """

    input_other: int          # 非缓存输入 token 合计
    input_cache_read: int     # 缓存命中输入 token 合计
    input_cache_creation: int # 缓存写入输入 token 合计
    output: int               # 输出 token 合计
    turn_id: str              # 去重键（会话 id + 轮次序号）

    @property
    def total(self) -> int:
        return self.input_other + self.input_cache_read + self.input_cache_creation + self.output


@dataclass(frozen=True)
class BackendCapabilities:
    """后端能力快照：通用 GUI 的唯一能力读取面。

    快照只是**当前时刻**的只读状态，不承担事件通知，也不替代动作接口
    （set_model/set_permission_handler 等归各 provider 自持）。provider
    每次调用都按现状重建，不缓存跨轮次/跨会话结果——DSH 图片能力要等
    initialize 实测、Kimi 恢复态随连接变化，缓存即显示滞后。

    image_input 用三态而非 bool：None = 尚未实测，GUI 回退
    `BackendSpec.supports_images` 静态位；True/False = 运行时实测真值。
    """

    #: 图片输入能力；None = 未实测（回退注册表静态位）
    image_input: bool | None = None
    #: 轮次收尾后继续接收迟到用量帧的宽限秒数（0 = 收尾即停轮询）
    post_turn_usage_grace_s: float = 0.0
    #: 本后台是否适用轮次停滞看门狗
    stall_watchdog: bool = True
    #: 当前是否处于待恢复态（GUI 据此决定是否取消自动待发等）
    recovery_pending: bool = False


#: 未覆写 capabilities() 的对象（含不继承 Protocol 的测试桩）统一回退值
DEFAULT_CAPABILITIES = BackendCapabilities()


def capabilities_of(model: object) -> BackendCapabilities:
    """中立能力读取：`model.capabilities()`；未实现时返回默认快照。

    通用 GUI 必须经本 helper 读取能力，不得 `getattr` provider 的私有
    能力属性——测试桩、旧 provider 与未来实现都不保证继承 Protocol。
    """
    getter = getattr(model, "capabilities", None)
    if callable(getter):
        return getter()
    return DEFAULT_CAPABILITIES


def apply_vendor_settings_to(
        model: object, settings: Mapping[str, object]) -> None:
    """中立厂商设置注入：`model.apply_vendor_settings(settings)`；未实现即 no-op。

    settings 已由调用方按 `BackendSpec.vendor_settings` 过滤，provider 只
    解释本厂商声明过的键；通用层不出现任何厂商键名。
    """
    applier = getattr(model, "apply_vendor_settings", None)
    if callable(applier):
        applier(settings)


@dataclass(frozen=True)
class Chunk:
    """流式块：kind 区分正文、思维链、上下文用量通知与 AI 活动信息。

    kind="usage" 时 text 为空串、载荷经 usage 字段携带（用量不上屏进
    输出区文本流，由 GUI 路由到徽章控件）。

    kind="tool_call" / "tool_call_update" / "todo" 时载荷经 payload
    字段携带，对通用层不透明——只按事件类型与调用 ID 路由，不读取卡片
    字段；具体 schema 由各厂商 `models.py` 定义。text 字段为协议层预填的
    兜底显示单行：GUI 对未识别 kind 按 text 渲染，新协议层配旧 GUI 不崩
    （多标签/多开版本错配防御）。新三 kind 与 reasoning 同约束：
    仅上屏，不入历史、不回传。kind="status" 为后台轮次同步状态提示
    （终态确认期间由后台产出），同样只显示、不入历史、不回传。
    """

    kind: Literal[
        "text", "reasoning", "usage", "tool_call", "tool_call_update", "todo",
        "status",
    ]
    text: str
    usage: UsageStats | None = None
    #: 工具/计划活动的厂商私有载荷：通用层只按 kind 路由并原样转交，不读取
    #: 其中字段；具体 schema 归各厂商 `models.py`，卡片与 presenter 自行解释。
    payload: dict | None = None
    #: 轮次输出速度（kind="usage" 时可同帧携带；仅部分后台产出，
    #: 其余后端恒 None，GUI 无数据不显示对应行）
    speed: "TurnSpeed | None" = None
    #: 轮次真实 token 消耗（kind="usage" 时可同帧携带；仅部分后台产出，
    #: 2026-1114-1452 计划；GUI 轮末落盘进按日统计与滚动汇总）
    consumption: "TurnConsumption | None" = None


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

        背景（0117 计划）：推送型后端的 `usage_update` 由 agent 决定时机，
        IDE 无中间数据可挖；部分后台的用量落在本地会话记录且轮次内增量
        写盘，可由该后台 provider 主动读取。GUI 侧 QTimer 在轮次进行中
        周期调用本方法，非 None 即刷新徽章。

        约束：实现须廉价（首次有界尾部建立游标，之后只读新增字节与缓存）、
        幂等、线程安全（从 GUI 线程调用，与 chat 所在 worker 并发），任何
        失败静默返回 None，不得抛出、不得阻塞、不得臆造估算值。实现不得
        为徽章读取、排空或清理 ACP 队列，也不得在持轮询锁时执行目录子进程
        或等待 ACP。
        """
        return None

    def capabilities(self) -> BackendCapabilities:
        """返回当前能力快照（可选能力，默认值见 BackendCapabilities）。

        实现须按当前实例状态即时构造，不得缓存到跨轮次失效；GUI 只在
        provider 创建后、initialize 完成后、轮次收尾后与切换后台后刷新。
        """
        return DEFAULT_CAPABILITIES

    def apply_vendor_settings(self, settings: Mapping[str, object]) -> None:
        """接收本后台声明的厂商设置（可选能力，默认 no-op）。

        settings 已由调用方按 `BackendSpec.vendor_settings` 过滤；provider
        自行解释值的含义，未知键静默忽略。厂商设置不进入能力快照。
        """
        return None
