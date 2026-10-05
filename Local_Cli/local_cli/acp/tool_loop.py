"""有界工具循环：流式 → 解析 → 审批/执行 → 回灌，至多 tool_max_iterations 轮。

协议与设计依据：
- §2.2/§3：tool_call / tool_call_update / request_permission 帧格式与三态审批；
  被拒绝后放弃执行并向用户追问，不静默跳过。
- §2.3：usage 多轮迭代累加后单帧回报（used/size 同帧）。
- §1.6：每次迭代开始前、审批返回后、流式 chunk 间均检查 cancel_event，
  停后同会话可续。
- 计划 §3.2：模型以 tool_call 围栏块发起调用，结果以【工具结果】/【工具错误】
  user 消息回灌；解析失败同样回灌错误原因给模型自我修正（计划 §6）。

本模块与 turn.py 的关系：审批与出站帧助手是同一模式的平行实现（线格式常量
各自按协议小节声明）；turn.py 不 import 本模块的常量、本模块也不 import
turn.py——两边都只面向协议文档取值，避免 acp 层内循环依赖。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from local_cli.acp.session import AcpSession
from local_cli.acp.transport import ReverseRequestBroker, StdioTransport
from local_cli.model.base import ChatMessage, ChatRole, LanguageModel
from local_cli.toolkit.definitions import (
    TOOL_CALL_BLOCK_PATTERN,
    TOOL_DEFINITION_MAP,
    TOOL_ERROR_FEEDBACK_PREFIX,
    TOOL_NAME_LIST_DIR,
    TOOL_NAME_READ_FILE,
    TOOL_NAME_WRITE_FILE,
    TOOL_RESULT_FEEDBACK_PREFIX,
    build_system_prompt,
)
from local_cli.toolkit.executor import ToolExecutionResult, ToolExecutor
from local_cli.toolkit.fence_filter import ToolCallFenceFilter
from local_cli.toolkit.parser import (
    ToolCallParseFailure,
    ToolCallParser,
    ToolCallRequest,
)


class SessionUpdateType:
    """session/update 通知的 sessionUpdate 字段取值（§2.2，魔法字符串命名常量）。"""
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    TOOL_CALL = "tool_call"
    TOOL_CALL_UPDATE = "tool_call_update"
    USAGE_UPDATE = "usage_update"


class StopReason:
    """prompt 响应的 stopReason 取值（§2.4）。"""
    END_TURN = "end_turn"
    CANCELLED = "cancelled"
    ERROR = "error"


class ToolCallStatus:
    """tool_call_update 的 status 取值（§3）。"""
    COMPLETED = "completed"
    FAILED = "failed"


#: 审批三态 options（§3：optionId 原值回传，客户端按 kind 兜底选择）
_PERMISSION_OPTIONS = [
    {"optionId": "allow_once", "name": "允许一次", "kind": "allow_once"},
    {"optionId": "allow_always", "name": "总是允许", "kind": "allow_always"},
    {"optionId": "reject_once", "name": "拒绝", "kind": "reject_once"},
]

#: agent→client 出站方法名（入站方法名归口 dispatcher.RpcMethod，两侧分别命名）
_METHOD_SESSION_UPDATE = "session/update"
_METHOD_REQUEST_PERMISSION = "session/request_permission"

#: ACP 线格式枚举取值（协议 §2/§3 的固定值，魔法字符串命名常量）
_OUTCOME_SELECTED = "selected"
_ALLOW_OPTION_KIND_PREFIX = "allow"
_OPTION_ID_ALLOW_ALWAYS = "allow_always"
_CONTENT_TYPE_TEXT = "text"
_CONTENT_TYPE_WRAPPED = "content"

#: toolCallId 命名（计划 §3.3）：tool-<session 序号>-<迭代序号>，同轮多次调用不撞
_TOOL_CALL_ID_PREFIX = "tool"

#: ACP kind 取值：写操作归 edit（与 turn.py 演示工具一致），只读操作归 read
_TOOL_KIND_BY_NAME = {
    TOOL_NAME_WRITE_FILE: "edit",
    TOOL_NAME_READ_FILE: "read",
    TOOL_NAME_LIST_DIR: "read",
}

#: tool_call 帧 title 动词：IDE 卡片标题的人类可读动作描述
_TOOL_TITLE_BY_NAME = {
    TOOL_NAME_WRITE_FILE: "写入文件",
    TOOL_NAME_READ_FILE: "读取文件",
    TOOL_NAME_LIST_DIR: "列出目录",
}


@dataclass(frozen=True)
class _IterationUsage:
    """跨迭代累加的 token 账本（§2.3：无真实统计时 size 保持 None 即不上报）。"""
    used_tokens: int = 0
    context_size: int | None = None


@dataclass(frozen=True)
class _IterationScreening:
    """一次迭代的流式产物：解析用原文 + 过滤器扣留待裁决文本（计划 §3.1 状态机）。

    held_text 的语义由 is_suspended 区分：False=NORMAL 尾部余量（直接补发，
    纯文本轮次逐字节不变）；True=自起始标记起的扣留原文（交 parser 裁决：
    合法调用丢围栏块补块后正文 / 解析失败全量补发 / 不完整块视同失败补发）。
    """
    reply_text: str
    held_text: str
    is_suspended: bool


class ToolLoopRunner:
    """单轮 prompt 的工具循环执行器：构造时注入全部协作者（AFCP 2.3/2.4）。"""

    def __init__(
        self,
        transport: StdioTransport,
        broker: ReverseRequestBroker,
        model: LanguageModel,
        session: AcpSession,
        prompt_text: str,
        parser: ToolCallParser,
        executor: ToolExecutor,
        max_iterations: int,
    ) -> None:
        self._transport = transport
        self._broker = broker
        self._model = model
        self._session = session
        self._prompt_text = prompt_text
        self._parser = parser
        self._executor = executor
        self._max_iterations = max_iterations
        #: session 序号只用于 toolCallId 可读性（计划 §3.3），不参与逻辑判定
        self._session_sequence = session.session_id.rsplit("-", 1)[-1]

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def execute(self, request_id: int | str) -> None:
        """跑完一轮（含至多 N 次工具迭代）并以 prompt 响应帧收尾。"""
        # 轮次内消息列表仅本轮内存活（计划 §3.4：跨轮次记忆不在本期）
        messages = [ChatMessage(role=ChatRole.USER, content=self._prompt_text)]
        system_text = build_system_prompt()
        usage = _IterationUsage()
        for iteration in range(1, self._max_iterations + 1):
            # §1.6：迭代开始前的取消检查（流式 chunk 间与审批返回后另有检查点）
            if self._is_cancelled():
                self._respond_stop(request_id, StopReason.CANCELLED)
                return
            screening = self._stream_iteration(messages, system_text, request_id)
            if screening is None:
                return  # cancel/error 已在 _stream_iteration 内收尾（扣留弃置）
            usage = self._accumulate_usage(usage)
            parse_result = self._parser.parse(screening.reply_text)
            if parse_result is None:
                # 无完整围栏块：NORMAL 尾量或不完整块原文都补发（透明语义）
                self._emit_held_text(screening.held_text)
                self._finish_end_turn(request_id, usage)
                return
            if isinstance(parse_result, ToolCallParseFailure):
                # 解析失败：扣留原文全量补发——用户能看到模型的错误输出（计划 §5）
                self._emit_held_text(screening.held_text)
                self._append_exchange(messages, screening.reply_text,
                                      f"{TOOL_ERROR_FEEDBACK_PREFIX}"
                                      f"{parse_result.error_message}。"
                                      "请按约定格式重新输出，或用文字直接回答。")
                continue
            # 合法工具调用：围栏块丢弃（IDE 只渲染卡片），块后正文补发不吞话语
            self._emit_held_text(
                self._extract_post_block_text(screening.held_text))
            if not self._run_tool_call(parse_result, screening.reply_text, messages,
                                       iteration, usage, request_id):
                return  # 拒绝/cancel 路径已在内部收尾
        # 迭代用尽仍以说明性文本显式收尾（计划 §6：不静默吞错，A.3）
        self._send_stream_chunk(
            False,
            f"\n（工具调用已达单轮上限 {self._max_iterations} 次，"
            "本轮先收尾；如未完成请继续追问。）\n")
        self._finish_end_turn(request_id, usage)

    # ------------------------------------------------------------------
    # 单次迭代：流式
    # ------------------------------------------------------------------
    def _stream_iteration(
        self,
        messages: list[ChatMessage],
        system_text: str,
        request_id: int | str,
    ) -> _IterationScreening | None:
        """流式一轮：正文经过滤器上屏（思维链直通不过滤，计划 §6），返回迭代产物。

        None = 轮次已在本方法内收尾（cancel/error 的 stop 响应已发），扣留
        未上屏文本随过滤器弃置（轮次已终结无补发对象，计划 §3.3）；
        调用方直接返回——与 turn.py 常规轮次同一收尾语义（§1.6/A.3）。
        """
        reply_parts: list[str] = []
        fence_filter = ToolCallFenceFilter()
        try:
            stream = self._model.stream_chat(
                messages, system_text, self._session.model_alias)
            for chunk in stream:
                if self._is_cancelled():
                    # 显式关流：GGUF 实现借此断开 HTTP，llama-server 即刻停止生成
                    stream.close()
                    self._respond_stop(request_id, StopReason.CANCELLED)
                    self._transport.log("工具循环轮次被 cancel，已即停")
                    return None
                if chunk.is_thought:
                    self._send_stream_chunk(True, chunk.text)
                    continue
                reply_parts.append(chunk.text)  # 解析用原文照记，与上屏过滤解耦
                visible_text = fence_filter.feed(chunk.text)
                if visible_text:
                    self._send_stream_chunk(False, visible_text)
        except Exception as stream_error:  # noqa: BLE001 — 模型侧任何故障都不许崩连接，显式 error 收尾（A.3）
            self._transport.log(f"模型流式故障：{stream_error}")
            self._send_stream_chunk(False, f"\n（模型错误：{stream_error}）\n")
            self._respond_stop(request_id, StopReason.ERROR)
            return None
        is_suspended = fence_filter.is_suspended
        return _IterationScreening(
            reply_text="".join(reply_parts),
            held_text=fence_filter.flush(),
            is_suspended=is_suspended,
        )

    def _emit_held_text(self, text: str) -> None:
        """补发扣留文本；空串不发帧（§2.2：content.text 非空才上屏）。"""
        if text:
            self._send_stream_chunk(False, text)

    def _extract_post_block_text(self, held_text: str) -> str:
        """扣留原文中末个围栏块之后的正文（合法调用时只有它配补发上屏）。"""
        matches = list(TOOL_CALL_BLOCK_PATTERN.finditer(held_text))
        if not matches:
            return ""
        return held_text[matches[-1].end():]

    def _accumulate_usage(self, usage: _IterationUsage) -> _IterationUsage:
        """本次迭代的 token 统计并入账本（§2.3：累加后单帧回报）。"""
        iteration_usage = self._model.take_usage()
        if iteration_usage is None:
            return usage
        used, size = iteration_usage
        return _IterationUsage(
            used_tokens=usage.used_tokens + used, context_size=size)

    # ------------------------------------------------------------------
    # 单次迭代：工具调用
    # ------------------------------------------------------------------
    def _run_tool_call(
        self,
        request: ToolCallRequest,
        reply_text: str,
        messages: list[ChatMessage],
        iteration: int,
        usage: _IterationUsage,
        request_id: int | str,
    ) -> bool:
        """tool_call → （审批）→ 执行 → tool_call_update → 结果回灌。

        返回 False = 轮次已收尾（拒绝/cancel），调用方直接返回。
        """
        tool_call_id = f"{_TOOL_CALL_ID_PREFIX}-{self._session_sequence}-{iteration}"
        tool_call = self._build_tool_call_frame(tool_call_id, request)
        self._notify_update({"sessionUpdate": SessionUpdateType.TOOL_CALL, **tool_call})

        is_permitted = self._confirm_permission(request, tool_call)
        # §1.6/计划 §3.4：审批返回后（无论批准与否）先查取消再决定下一步
        if self._is_cancelled():
            self._respond_stop(request_id, StopReason.CANCELLED)
            return False
        if not is_permitted:
            # §3：被拒绝后放弃执行并向用户追问，不静默跳过
            self._notify_update({
                "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
                "toolCallId": tool_call_id,
                "status": ToolCallStatus.FAILED,
                "rawOutput": {"error": {"message": "用户拒绝了权限请求"}},
            })
            self._send_stream_chunk(
                False,
                f"\n已放弃{tool_call['title']}（你拒绝了权限请求）。"
                "需要我换个路径或方式再试吗？\n")
            self._finish_end_turn(request_id, usage)
            return False

        execution = self._executor.execute(request)
        self._notify_update(self._build_result_frame(tool_call_id, execution))
        feedback_prefix = TOOL_RESULT_FEEDBACK_PREFIX if execution.is_success \
            else TOOL_ERROR_FEEDBACK_PREFIX
        self._append_exchange(messages, reply_text,
                              f"{feedback_prefix}{execution.output_text}")
        return True

    def _confirm_permission(
        self, request: ToolCallRequest, tool_call: dict[str, Any]
    ) -> bool:
        """写操作审批回环；只读工具与 allow_always 记忆命中直接放行（§3.3）。"""
        definition = TOOL_DEFINITION_MAP[request.tool_name]
        if not definition.needs_permission:
            return True
        if request.tool_name in self._session.always_allowed_tools:
            return True
        option_id = self._request_permission(tool_call)
        if option_id == _OPTION_ID_ALLOW_ALWAYS:
            # 会话级记忆：同会话后续同工具不再重复询问（计划 §6：进程退出即失效）
            self._session.always_allowed_tools.add(request.tool_name)
        return option_id is not None and option_id.startswith(_ALLOW_OPTION_KIND_PREFIX)

    def _build_tool_call_frame(
        self, tool_call_id: str, request: ToolCallRequest
    ) -> dict[str, Any]:
        # rawInput/locations 的 path 原样下传相对路径（协议 §2.2：客户端按工作区根解析）
        raw_path = request.arguments.get("path", "")
        return {
            "toolCallId": tool_call_id,
            "title": f"{_TOOL_TITLE_BY_NAME[request.tool_name]} {raw_path}",
            "kind": _TOOL_KIND_BY_NAME[request.tool_name],
            "rawInput": dict(request.arguments),
            "locations": [{"path": raw_path}],
        }

    def _build_result_frame(
        self, tool_call_id: str, execution: ToolExecutionResult
    ) -> dict[str, Any]:
        """终态帧：completed 带 content 出参，failed 带 rawOutput 错误详情（§2.2/§3）。"""
        if execution.is_success:
            return {
                "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
                "toolCallId": tool_call_id,
                "status": ToolCallStatus.COMPLETED,
                "content": [{"type": _CONTENT_TYPE_WRAPPED,
                             "content": {"type": _CONTENT_TYPE_TEXT,
                                         "text": execution.output_text}}],
            }
        return {
            "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
            "toolCallId": tool_call_id,
            "status": ToolCallStatus.FAILED,
            "rawOutput": {"error": {"message": execution.output_text}},
        }

    def _append_exchange(
        self, messages: list[ChatMessage], reply_text: str, feedback_text: str
    ) -> None:
        """assistant 回复与工具结果成对入列：模型下一轮能看到自己的原始输出。"""
        messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=reply_text))
        messages.append(ChatMessage(role=ChatRole.USER, content=feedback_text))

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def _finish_end_turn(
        self, request_id: int | str, usage: _IterationUsage
    ) -> None:
        """正常收尾：有真实统计先发 usage_update（used/size 同帧，§2.3），再 end_turn。"""
        if usage.context_size is not None:
            self._notify_update({
                "sessionUpdate": SessionUpdateType.USAGE_UPDATE,
                "used": usage.used_tokens,
                "size": usage.context_size,
            })
        self._respond_stop(request_id, StopReason.END_TURN)

    # ------------------------------------------------------------------
    # 审批与取消（turn.py 同一模式：§3 反向请求阻塞等回执，§1.6 消费式取消检查）
    # ------------------------------------------------------------------
    def _request_permission(self, tool_call: dict[str, Any]) -> str | None:
        """发 session/request_permission 并阻塞等回执；outcome=cancelled 视为拒绝。"""
        result = self._broker.request(
            _METHOD_REQUEST_PERMISSION,
            {"sessionId": self._session.session_id,
             "toolCall": tool_call,
             "options": _PERMISSION_OPTIONS},
        )
        outcome = result.get("outcome") or {}
        if outcome.get("outcome") != _OUTCOME_SELECTED:
            return None
        return outcome.get("optionId")

    def _is_cancelled(self) -> bool:
        """取消检查；消费标志后清除（§1.6：停后同会话可续）。"""
        if self._session.cancel_event.is_set():
            self._session.cancel_event.clear()
            return True
        return False

    # ------------------------------------------------------------------
    # 出站帧助手（turn.py 同一模式：写锁串行化由 transport 保证）
    # ------------------------------------------------------------------
    def _notify_update(self, update: dict[str, Any]) -> None:
        self._transport.send_notification(_METHOD_SESSION_UPDATE, {
            "sessionId": self._session.session_id,
            "update": update,
        })

    def _send_stream_chunk(self, is_thought: bool, text: str) -> None:
        update_type = (SessionUpdateType.AGENT_THOUGHT_CHUNK if is_thought
                       else SessionUpdateType.AGENT_MESSAGE_CHUNK)
        self._notify_update({
            "sessionUpdate": update_type,
            "content": {"type": _CONTENT_TYPE_TEXT, "text": text},
        })

    def _respond_stop(self, request_id: int | str, stop_reason: str) -> None:
        self._transport.send_result(request_id, {"stopReason": stop_reason})
