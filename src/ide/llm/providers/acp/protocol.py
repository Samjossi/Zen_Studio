"""ACP 协议定型类型与中性能力载荷。

权限、terminal 反向请求与 usage 通知都是 ACP 标准面；本模块不含厂商判断，
也不认识具体工具名。
"""
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypedDict

from ide.llm.base import UsageStats

class PermissionOption(TypedDict, total=False):
    """`session/request_permission` 的单个选项（agent 提供，optionId 回应用原值）。"""
    optionId: str
    name: str
    kind: str  # allow_once / allow_always / reject_once / reject_always


class ToolCallInfo(TypedDict, total=False):
    """审批请求携带的工具调用信息（键全为可选，agent 实发字段随工具而异）。"""
    title: str
    kind: str
    rawInput: dict
    content: list
    locations: list


class PermissionParams(TypedDict, total=False):
    """`session/request_permission` 的 params（ACP 入口定型点）。"""
    sessionId: str
    toolCall: ToolCallInfo
    options: list[PermissionOption]


#: 权限请求的中性分类：普通工具审批 / agent 提问，由各厂商自行判定
PermissionKind = Literal["permission", "question"]


@dataclass(frozen=True)
class PermissionRequest:
    """中性审批请求：原始 ACP params 原样保留，厂商分类结果只增不改。

    `params` 是卡片回填、选项回传和诊断的唯一原始真相源；`kind` 与
    `questions` 由各厂商 `permissions.py` 从自身结构提取，公共策略和
    GUI 宿主不再解析厂商工具名或 rawInput 字段。
    """
    params: PermissionParams
    kind: PermissionKind = "permission"
    questions: tuple[str, ...] = ()


#: 连接层处理器签名：session/request_permission 原始 params → optionId
#: （None 视为拒绝）。连接层不分类、不认识厂商工具名。
PermissionHandler = Callable[[PermissionParams], str | None]

#: provider/GUI 层处理器签名：接收厂商分类后的中性请求 → optionId
PermissionRequestHandler = Callable[[PermissionRequest], str | None]


class TerminalHandler(Protocol):
    """terminal/* 反向请求处理器鸭子类型契约（GUI 桥 AgentTerminalBridge 实现）。

    create/output/kill/release 在连接层 reader 线程被调，实现方自行封送
    GUI 线程同步执行后返回；wait_for_exit 为回调式（长阻塞语义，严禁
    占 reader 线程——会卡死同 session 的 session/update 流），进程退出时
    实现方回调 `{exitCode, signal}`，连接层据登记挂起的 response id 补发
    响应包。on_connection_dead 在死讯注入路径被调（agent 已死，无回包），
    实现方清理本连接全部挂起 wait 回调。
    """

    def create(self, command: str, args: list[str], cwd: str | None,
               env: dict[str, str] | None) -> str:
        """建终端会话执行命令 → terminalId（args 为空时 command 为整行 shell 串）。"""
        ...

    def output(self, terminal_id: str) -> dict:
        """→ {output, truncated, exitStatus?}（尾部 64KB 截断策略归实现方）。"""
        ...

    def wait_for_exit(self, terminal_id: str, callback: Callable[[dict], None]) -> None:
        """挂进程退出一次性回调；已退出则立即回调 {exitCode, signal}。"""
        ...

    def kill(self, terminal_id: str) -> None:
        """终止会话进程（幂等）。"""
        ...

    def release(self, terminal_id: str) -> None:
        """解除协议侧跟踪（tab 保留归实现方语义）。"""
        ...

    def on_connection_dead(self) -> None:
        """agent 进程死讯注入：清理本连接全部挂起 wait 回调（兜底 exitCode=-1）。"""
        ...


def build_client_capabilities(terminal: bool) -> dict:
    """initialize 载荷 clientCapabilities 构造收口（fs 声明恒 false，见 1554 计划 §5）。

    :param terminal: 是否声明 terminal/* 反向能力（支持矩阵常量 and 已注入 handler）
    """
    return {
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": terminal,
    }


def _parse_terminal_env(env: object) -> dict[str, str] | None:
    """terminal/create 的 env 载荷 → dict（协议为 [{name, value}] 数组，dict 容错直通）。"""
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    if isinstance(env, list):
        parsed = {str(item["name"]): str(item.get("value", ""))
                  for item in env if isinstance(item, dict) and "name" in item}
        return parsed or None
    return None

#: 轮次内消息：update/response 载荷为 JSON-RPC 帧，dead 载荷为进程退出码
_TurnMessage = tuple[Literal["update", "response"], dict] | tuple[Literal["dead"], int | None]



def map_usage_update(update: dict) -> UsageStats | None:
    """`usage_update` 通知载荷 → UsageStats；无有效上限数据返回 None。

    协议载荷：`{"sessionUpdate": "usage_update", "used": N, "size": M,
    "cost": {"amount": f, "currency": "USD"}}`——used/size 同帧送达，
    IDE 侧无需自维护模型上下文上限表。size 缺失或为 0 的通知无意义
    （算不出百分比），返回 None 不上屏（UI 须容忍收不到用量的后端，
    保持隐藏而非显示 0%）。
    """
    size = update.get("size")
    used = update.get("used")
    if not isinstance(size, int) or size <= 0 or not isinstance(used, int) or used < 0:
        return None
    cost_amount = (update.get("cost") or {}).get("amount")
    return UsageStats(
        used=used,
        size=size,
        cost=cost_amount if isinstance(cost_amount, (int, float)) else None,
    )
