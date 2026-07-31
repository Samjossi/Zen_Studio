"""ACP 工具审批决策纯逻辑（权限四态；危险命令黑名单为智能放行档兜底）。

决策链：`decide_permission(params, mode)` 判定 allow/ask → allow 时
`select_option_id()` 按 kind 优先序挑 optionId 原样回传（不臆造，optionId
纪律同 1555 计划 C1）。模块零 Qt、零 IO、零阻塞——agent reader 线程直接
调用安全（纯逻辑分层同 SelectionController 先例，可脱离 GUI 单测）。

权限四态（2026-07-22，文档/修改记录/2026-0722-1240 计划，二态开关升级）：
confirm_all 逐次确认 / confirm_execute 仅命令确认 / auto_guarded 智能放行
（默认，黑名单兜底）/ auto_all 全部放行（护栏关闭，高危）。四态是单选枚举
而非规则引擎——不做工具级/命令级白名单（选型 §5 裁决不突破）。

黑名单为归一化后的正则粗匹配兜底，失败方向安全：
漏匹配 → 不弹（放手语义接受的残余风险，已裁决）；误匹配 → 弹一次窗（人看一眼）。
实施依据：文档/修改记录/2026-0722-0757_AI工具权限方案F默认放手双护栏实施计划.md。
"""
import re
from collections.abc import Sequence
from typing import Literal

from llm.providers.kimi_acp import PermissionOption, PermissionParams, ToolCallInfo

#: 决策结果常量
DECISION_ALLOW = "allow"
DECISION_ASK = "ask"

#: 决策返回：(判定, 黑名单命中原因)；allow 时原因恒为 None
Decision = tuple[Literal["allow", "ask"], str | None]

# ----------------------------------------------------------------------
# 权限模式常量（settings 默认值、设置中心单选组共用此单一来源）
# ----------------------------------------------------------------------
MODE_CONFIRM_ALL = "confirm_all"        #: 逐次确认：全部弹窗（逃生舱）
MODE_CONFIRM_EXECUTE = "confirm_execute"  #: 仅命令需确认：非 Bash 放行，Bash 全弹
MODE_AUTO_GUARDED = "auto_guarded"      #: 智能放行：仅黑名单命中弹窗（默认）
MODE_AUTO_ALL = "auto_all"              #: 全部放行：黑名单护栏关闭（高危）

#: 合法模式值（未知值回退默认）
PERMISSION_MODES = (MODE_CONFIRM_ALL, MODE_CONFIRM_EXECUTE, MODE_AUTO_GUARDED, MODE_AUTO_ALL)

DEFAULT_PERMISSION_MODE = MODE_AUTO_GUARDED

#: 模式值 → (显示名, 说明)（设置中心 AI 权限页单选组文案单一来源）
PERMISSION_MODE_LABELS: dict[str, tuple[str, str]] = {
    MODE_CONFIRM_ALL: ("逐次确认", "所有工具调用都弹窗确认，最保守"),
    MODE_CONFIRM_EXECUTE: ("仅命令需确认", "读写文件自动放行，运行命令一律弹窗"),
    MODE_AUTO_GUARDED: ("智能放行（推荐）", "普通调用自动放行，仅危险命令黑名单命中时弹窗"),
    MODE_AUTO_ALL: ("全部放行", "危险命令（rm -rf /、git push -f 等）也不再弹窗，高危"),
}

#: 自动放行的 optionId 优先序（同 Multi_Cli_Studio gemini 侧先例：
#: allow_always 优于 allow_once——等价"始终允许"语义，避免每轮重复决策）
ALLOW_KIND_PREFERENCE = ("allow_always", "allow_once")

#: 危险命令黑名单（编译后正则, 命中原因）；命令文本归一化（压缩空白 + 小写）后粗匹配。
#: 每条注明"为什么危险"（AFCP 3.4 常量化；条目按使用反馈增补，见计划 §7 备案 4）
DANGEROUS_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), reason)
    for pattern, reason in (
        # 1. 递归强删根/家目录/版本库，不可逆
        (r"\brm\s+-[a-z]*(?:rf|fr)[a-z]*(?:\s+\S+)*\s(?:/|/\*|~|\$home|\.git)(?:[\s/]|$)",
         "递归强删根目录/家目录/版本库（rm -rf），不可逆"),
        # 2. 格式化/重分区，毁盘
        (r"\bmkfs[.\w]*\b|\b(?:fdisk|parted)\b",
         "格式化/重分区操作（mkfs/fdisk/parted），可毁盘"),
        # 3. 覆写块设备
        (r"\bdd\b[^|;]*\bof=/dev/|>{1,2}\s*/dev/sd[a-z]",
         "覆写块设备（dd of=/dev 或重定向 /dev/sdX）"),
        # 4. 关机重启，工作中断
        (r"\b(?:shutdown|reboot|poweroff|halt)\b|\binit\s+[06]\b",
         "关机/重启操作，工作中断"),
        # 5. fork 炸弹，资源耗尽系统瘫痪
        (r"\(\)\s*\{.*:\|:&.*\}\s*;\s*:",
         "fork 炸弹（:(){ :|:& };:），资源耗尽系统瘫痪"),
        # 6. 杀 init/全部进程
        (r"\bkill\s+-9\s+(?:-1|1)\b|\bpkill\s+-9\b|\bkillall\b",
         "杀 init 或全量进程（kill -9 -1 / pkill -9 / killall）"),
        # 7. 递归改根权限，系统损坏
        (r"\bchmod\s+-[a-z]*r[a-z]*\s+\d*777\d*\s+/(?:\s|$)"
         r"|\bchown\s+-[a-z]*r[a-z]*\s+\S+\s+/(?:\s|$)",
         "递归改根目录权限/属主（chmod -R 777 / 等），系统损坏"),
        # 8. 不可逆覆盖远端历史（本项目仓库）
        (r"\bgit\s+push\b[^|;]*(?:--force\b|-f\b)",
         "git push --force 不可逆覆盖远端历史"),
        # 9. 系统账户变更
        (r"\b(?:passwd|useradd|userdel|visudo)\b",
         "系统账户/sudo 权限变更（passwd/useradd/userdel/visudo）"),
        # 10. 断服务/清防火墙
        (r"\bsystemctl\s+(?:--\S+\s+)*(?:stop|disable)\b|\biptables\s+-f\b|\bufw\s+disable\b",
         "停止/禁用系统服务或清空防火墙"),
        # 11. 管道执行远程脚本，供应链风险
        (r"\b(?:curl|wget)\b[^|;]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
         "管道执行远程脚本（curl/wget ... | sh），供应链风险"),
        # 12. 写入系统/SSH 敏感目录（越出项目目录，与护栏一呼应，best-effort）
        (r"(?:>{1,2}|\btee\s+(?:-\S+\s+)*)\s*(?:/etc/|/usr/|/boot/|~/\.ssh|\$home/\.ssh)",
         "写入系统/SSH 敏感目录（/etc、/usr、/boot、~/.ssh），越出项目目录"),
    )
)


def decide_permission(params: PermissionParams, mode: str = DEFAULT_PERMISSION_MODE) -> Decision:
    """审批决策：(DECISION_ALLOW, None) 自动放行 / (DECISION_ASK, 原因) 走弹窗。

    四态语义（未知 mode 回退 auto_guarded）：
    - confirm_all：全部弹窗（逃生舱；Bash 命中黑名单时附原因）
    - confirm_execute：非 Bash 放行，Bash 一律弹窗（命中黑名单附原因）
    - auto_guarded：非 Bash 放行；Bash 仅黑名单命中弹窗（附原因）；
      提取不到命令文本按放手语义放行（风险已记录在案，见 0757 计划 §6）
    - auto_all：一律放行（护栏关闭，高危档）
    """
    if mode not in PERMISSION_MODES:
        mode = DEFAULT_PERMISSION_MODE
    if mode == MODE_AUTO_ALL:
        return DECISION_ALLOW, None
    tool_call = params.get("toolCall") or {}
    if mode == MODE_CONFIRM_ALL:
        return DECISION_ASK, _danger_reason(tool_call)
    if (tool_call.get("kind") or "") != "execute":
        return DECISION_ALLOW, None
    if mode == MODE_CONFIRM_EXECUTE:
        return DECISION_ASK, _danger_reason(tool_call)
    reason = _danger_reason(tool_call)
    if reason is not None:
        return DECISION_ASK, reason
    return DECISION_ALLOW, None


def _danger_reason(tool_call: ToolCallInfo) -> str | None:
    """Bash 类工具的黑名单命中原因；非危险/提取不到文本返回 None。"""
    command_text = extract_command_text(tool_call)
    if command_text is None:
        return None
    return match_dangerous_command(command_text)


def select_option_id(
    options: Sequence[PermissionOption],
    kinds: Sequence[str] = ALLOW_KIND_PREFERENCE,
) -> str | None:
    """按 kind 优先序选 optionId（原样回传 agent 提供值）；无匹配/空列表返回 None。"""
    for kind in kinds:
        option = next((o for o in options if o.get("kind") == kind), None)
        if option and option.get("optionId"):
            return option["optionId"]
    return None


def extract_command_text(tool_call: ToolCallInfo) -> str | None:
    """从审批载荷提取命令文本：rawInput 结构化字段优先，content 描述文本兜底。

    kimi 当前 toolCall.content 携带可读描述（如 "Requesting approval to
    Running: ls *.md"），rawInput 通常未携带；kimi 未来携带 rawInput 后
    本提取自动升级为结构化路径（计划 §7 备案 1）。
    """
    raw = tool_call.get("rawInput")
    if isinstance(raw, dict):
        cmd = raw.get("command") or raw.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            return cmd
    texts: list[str] = []
    for block in tool_call.get("content") or []:
        text = ((block or {}).get("content") or {}).get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts) or None


def match_dangerous_command(command_text: str) -> str | None:
    """归一化（压缩空白 + 小写）后按黑名单粗匹配；命中返回原因，未命中返回 None。"""
    normalized = " ".join(command_text.lower().split())
    for pattern, reason in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None
