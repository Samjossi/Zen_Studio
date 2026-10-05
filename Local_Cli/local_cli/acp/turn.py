"""轮次层：流式轮次、工具审批回环、stopReason 收尾。

在独立守护线程内执行（主读循环持续收帧，cancel 与审批回执才能及时
到达，§1.6/§3）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from local_cli.acp.session import AcpSession
from local_cli.acp.tool_loop import ToolLoopRunner
from local_cli.acp.transport import ReverseRequestBroker, StdioTransport
from local_cli.model.base import LanguageModel
from local_cli.model.mock import (
    MockIntent,
    MockLanguageModel,
)
from local_cli.toolkit.executor import ToolExecutor
from local_cli.toolkit.parser import ToolCallParser


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


#: 演示工具的唯一 toolCallId（本期只做单演示工具回环，无并发工具）
_DEMO_TOOL_CALL_ID = "demo-tool-1"
#: 演示工具落盘文件名与内容（§3 审批通过后实建，便于 spike/用户验证副作用）
_DEMO_FILE_NAME = "local_demo.txt"
_DEMO_FILE_CONTENT = "Local 示例 agent 演示文件\n"

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
_TOOL_KIND_EDIT = "edit"
_OUTCOME_SELECTED = "selected"
_ALLOW_OPTION_KIND_PREFIX = "allow"
_CONTENT_TYPE_TEXT = "text"
_CONTENT_TYPE_WRAPPED = "content"


class ToolCallStatus:
    """tool_call_update 的 status 取值（§3）。"""
    COMPLETED = "completed"
    FAILED = "failed"


def extract_prompt_text(raw_blocks: list[Any]) -> str:
    """从原始 prompt 内容块提取首个 text 块的文本。

    §2.1：prompt 恒 text 块打头；A.4：容忍空 text 块与非 dict 块，
    不因此崩轮次。线格式解析收敛在本函数，TurnRunner 只接收纯文本，
    原始 params 字典不再传入轮次层（AFCP 3.1）。
    """
    for block in raw_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text") or ""
    return ""


class TurnRunner:
    """单轮 prompt 的执行器：构造时注入全部协作者（AFCP 2.3 显式依赖）。"""

    def __init__(
        self,
        transport: StdioTransport,
        broker: ReverseRequestBroker,
        model: LanguageModel,
        session: AcpSession,
        prompt_text: str,
        tools_enabled: bool,
        tool_max_iterations: int,
    ) -> None:
        self._transport = transport
        self._broker = broker
        self._model = model
        self._session = session
        self._prompt_text = prompt_text
        self._tools_enabled = tools_enabled
        self._tool_max_iterations = tool_max_iterations

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def execute(self, request_id: int | str) -> None:
        """跑完一轮并以 prompt 响应帧收尾（stopReason 三态之一）。"""
        # §1.6：新轮次开始时清掉可能残留的取消标志，保证同会话可续
        self._session.cancel_event.clear()
        # 意图分类是 Mock 的私有演示语义；真实模型（GGUF）一律走常规轮次
        intent = (self._model.classify(self._prompt_text)
                  if isinstance(self._model, MockLanguageModel)
                  else MockIntent.NORMAL)

        if intent is MockIntent.ERROR_DEMO:
            self._run_error_demo(request_id)
            return
        if intent is MockIntent.TOOL_DEMO:
            self._run_tool_demo(request_id)
            return
        if self._tools_enabled:
            # 工具总开关下非常规演示意图一律进工具循环（含 Mock 常规回复：
            # 无围栏块时单迭代收尾，帧序列与纯文本路径逐字节一致——计划 §3.5
            # 的 Mock 关键词借此驱动真实循环做管道级回归）
            self._run_tool_loop(request_id)
            return
        self._run_normal(request_id)

    def _run_tool_loop(self, request_id: int | str) -> None:
        """委托 ToolLoopRunner：执行器绑定会话 cwd 沙盒，协作者显式接线。"""
        runner = ToolLoopRunner(
            transport=self._transport,
            broker=self._broker,
            model=self._model,
            session=self._session,
            prompt_text=self._prompt_text,
            parser=ToolCallParser(),
            executor=ToolExecutor(Path(self._session.cwd)),
            max_iterations=self._tool_max_iterations,
        )
        runner.execute(request_id)

    # ------------------------------------------------------------------
    # 三条轮次路径
    # ------------------------------------------------------------------
    def _run_normal(self, request_id: int | str) -> None:
        """常规轮次：思维链 → 逐 chunk 正文 → usage_update → end_turn（§2）。"""
        try:
            stream = self._model.stream_reply(
                self._prompt_text, self._session.model_alias)
            for chunk in stream:
                if self._is_cancelled():
                    # 显式关流：GGUF 实现借此断开 HTTP，llama-server 即刻停止生成
                    stream.close()
                    self._respond_stop(request_id, StopReason.CANCELLED)
                    self._transport.log("轮次被 cancel，已即停")
                    return
                self._send_stream_chunk(chunk.is_thought, chunk.text)
        except Exception as stream_error:  # noqa: BLE001 — 模型侧任何故障都不许崩连接，显式 error 收尾（A.3）
            # 模型侧故障（加载超时/连接断开等）不许崩连接：显式 error 收尾（A.3）
            self._transport.log(f"模型流式故障：{stream_error}")
            self._send_stream_chunk(False, f"\n（模型错误：{stream_error}）\n")
            self._respond_stop(request_id, StopReason.ERROR)
            return
        # §2.3：模型给了真实统计才发 usage_update（used/size 同帧；不臆造）
        usage = self._model.take_usage()
        if usage is not None:
            used, size = usage
            self._notify_update({
                "sessionUpdate": SessionUpdateType.USAGE_UPDATE,
                "used": used,
                "size": size,
            })
        self._respond_stop(request_id, StopReason.END_TURN)

    def _run_error_demo(self, request_id: int | str) -> None:
        """错误路径演示：先出说明 chunk，再显式 stopReason="error"。

        A.3：轮次失败必须显式标记，严禁「正常响应 + end_turn + 零 update」
        ——客户端会把零输出的成功轮次显示为空回复零提示。
        """
        self._send_stream_chunk(False, "（演示：本轮将以 error 收尾）")
        self._respond_stop(request_id, StopReason.ERROR)
        self._transport.log("演示错误路径：stopReason=error")

    def _run_tool_demo(self, request_id: int | str) -> None:
        """演示工具全链路（§3）：tool_call → 审批 → 执行/放弃 → tool_call_update。"""
        demo_path = os.path.join(self._session.cwd, _DEMO_FILE_NAME)
        tool_call = {
            "toolCallId": _DEMO_TOOL_CALL_ID,
            "title": f"创建文件 {_DEMO_FILE_NAME}",
            "kind": _TOOL_KIND_EDIT,
            "rawInput": {"path": demo_path, "content": _DEMO_FILE_CONTENT},
            "locations": [{"path": demo_path}],
        }
        self._notify_update({"sessionUpdate": SessionUpdateType.TOOL_CALL, **tool_call})

        option_id = self._request_permission(tool_call)
        if self._is_cancelled():
            self._respond_stop(request_id, StopReason.CANCELLED)
            return
        if option_id is not None and option_id.startswith(_ALLOW_OPTION_KIND_PREFIX):
            self._execute_demo_file_write(demo_path)
        else:
            # §3：被拒绝后放弃执行并向用户追问，不静默跳过
            self._notify_update({
                "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
                "toolCallId": _DEMO_TOOL_CALL_ID,
                "status": ToolCallStatus.FAILED,
                "rawOutput": {"error": {"message": "用户拒绝了权限请求"}},
            })
            self._send_stream_chunk(
                False, "\n已放弃创建文件。需要我换个路径或换个方式再试吗？\n")
        self._respond_stop(request_id, StopReason.END_TURN)

    def _execute_demo_file_write(self, demo_path: str) -> None:
        """审批通过后的真实副作用：写演示文件并回报结果。"""
        try:
            with open(demo_path, "w", encoding="utf-8") as demo_file:
                demo_file.write(_DEMO_FILE_CONTENT)
        except OSError as write_error:
            self._notify_update({
                "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
                "toolCallId": _DEMO_TOOL_CALL_ID,
                "status": ToolCallStatus.FAILED,
                "rawOutput": {"error": {"message": str(write_error)}},
            })
            self._send_stream_chunk(False, f"\n创建文件失败：{write_error}\n")
            return
        self._notify_update({
            "sessionUpdate": SessionUpdateType.TOOL_CALL_UPDATE,
            "toolCallId": _DEMO_TOOL_CALL_ID,
            "status": ToolCallStatus.COMPLETED,
            "content": [{"type": _CONTENT_TYPE_WRAPPED,
                         "content": {"type": _CONTENT_TYPE_TEXT,
                                     "text": f"已创建 {demo_path}"}}],
        })
        self._send_stream_chunk(False, f"\n已按你的允许创建演示文件：{demo_path}\n")

    # ------------------------------------------------------------------
    # 审批与取消
    # ------------------------------------------------------------------
    def _request_permission(self, tool_call: dict[str, Any]) -> str | None:
        """发 session/request_permission 并阻塞等回执，返回选中的 optionId。

        客户端回 outcome=cancelled 视为拒绝（None）。
        """
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
        """chunk 间的取消检查；消费标志后清除（§1.6：停后同会话可续）。"""
        if self._session.cancel_event.is_set():
            self._session.cancel_event.clear()
            return True
        return False

    # ------------------------------------------------------------------
    # 出站帧助手
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
