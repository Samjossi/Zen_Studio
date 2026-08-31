"""会话记录持久化：读写 config/sessions/<hash8>.json（按工作区分文件）。

2026-0818-2350 计划 T1：除非用户主动关闭会话标签，否则程序关闭后下次
重启恢复上次各标签的文字对话记录（只读展示，不回传 provider）。

仿 gui/window_state.py 范式：
- 分文件：工作区根 sha256 前 8 位作文件名，多开窗口各自读写各自工作区
  的存档，互不覆盖；路径由 session_file_for(workspace_root) 推导，调用方
  显式传入（AFCP 2.3 依赖显式）。
- 原子写：复用 settings.write_json_atomic（临时文件 + os.replace）。
- 损坏防御：文件缺失 / JSON 损坏 / 结构非法 → 静默回退空存档，不阻断启动；
  未登记字段读取即丢弃，键演进零迁移代码。
- 保存即全量快照：存档内容 = 保存时刻存活标签的集合——用户主动关闭的
  标签天然不在快照中（「主动关即不保留」语义无需单独剔除路径）；快照为
  空（全关后退出）时删除存档文件，下次启动不恢复任何会话。

数据边界（一期定论）：只存 _history 文字对话（user/assistant 正文 + 图片
附件引用）；思维链、工具卡片、todo、用量等渲染层事件不入 _history，亦不
入存档。恢复的消息只上屏展示，不回传 provider（全部 ACP 系 provider
历史由 agent 会话自管、只发末条 user 消息，回传无意义且污染协议语义）。
"""
import json
from pathlib import Path
from typing import TypedDict

from core.paths import workspace_digest
from gui.settings import CONFIG_DIR, write_json_atomic
from llm.base import Message

#: 会话存档子目录（对齐 window_state/ 收编范式，不散落 config 根目录）
SESSIONS_DIR = CONFIG_DIR / "sessions"

#: 单工作区存档标签数上限：与 ChatTabs.MAX_TABS 同源语义（字面量独立
#: 持有——本模块被 tabs 消费，反向 import 会成环）
MAX_SESSION_RECORDS = 4

#: 顶层键（消费侧唯一合法引用方式，禁止裸字符串）
KEY_SESSIONS = "sessions"

_VALID_ROLES = frozenset({"system", "user", "assistant"})


class SessionRecord(TypedDict):
    """单标签会话存档（一标签一条）。

    backend/version 供恢复时作新建注入值（各标签异构后台，2026-0803-0112
    计划）；effort 不存——跟随 model_efforts 记忆表既有机制。标签序号
    「会话 N」不存——恢复后按存档顺序重新编号（与全关重置序号语义一致）。
    """

    backend: str | None
    version: str | None
    history: list[Message]


class SessionArchive(TypedDict):
    """sessions/<hash8>.json 全量结构（单固定键）。"""

    sessions: list[SessionRecord]


def session_file_for(workspace_root: str) -> Path:
    """工作区根 → 存档文件路径（sha256 前 8 位分文件，多开互不覆盖）。

    哈希算法收口 core.paths.workspace_digest（2026-0831-2350 计划 D1），
    与 window_state/ 分文件、root_ownership 套接字命名同一来源。
    """
    return SESSIONS_DIR / f"{workspace_digest(workspace_root)}.json"


def _clean_history(raw: object) -> list[Message]:
    """逐条校验历史消息：role 合法 + content 为 str 才保留；images 逐项
    校验（缺 path 的条目剔除，字段全缺则整条消息降级为纯文本）。非法
    条目静默丢弃，不阻断其余条目。"""
    history: list[Message] = []
    if not isinstance(raw, list):
        return history
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in _VALID_ROLES or not isinstance(content, str):
            continue
        message = Message(role=role, content=content)
        images = [
            {"path": img["path"],
             "mime_type": img.get("mime_type") or "image/png",
             "pasted": bool(img.get("pasted", True))}
            for img in item.get("images") or []
            if isinstance(img, dict) and isinstance(img.get("path"), str)
        ]
        if images:
            message["images"] = images
        history.append(message)
    return history


def load_sessions(workspace_root: str) -> list[SessionRecord]:
    """读取本工作区会话存档；文件缺失 / JSON 损坏 / 结构非法回退空列表。

    防御分级：单条记录非法丢弃该条（其余照常恢复）；清洗后 history 为空
    的记录视为空会话丢弃；总数超 MAX_SESSION_RECORDS 截断（手改存档防御）。
    """
    records: list[SessionRecord] = []
    try:
        with open(session_file_for(workspace_root), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return records
    raw_sessions = data.get(KEY_SESSIONS) if isinstance(data, dict) else None
    if not isinstance(raw_sessions, list):
        return records
    for raw in raw_sessions[:MAX_SESSION_RECORDS]:
        if not isinstance(raw, dict):
            continue
        history = _clean_history(raw.get("history"))
        if not history:
            continue  # 空会话不入恢复集（与保存侧空历史跳过同律）
        backend = raw.get("backend")
        version = raw.get("version")
        records.append(SessionRecord(
            backend=backend if isinstance(backend, str) else None,
            version=version if isinstance(version, str) else None,
            history=history,
        ))
    return records


def save_sessions(workspace_root: str, records: list[SessionRecord]) -> None:
    """全量快照写入本工作区存档（原子写）；records 为空 → 删除存档文件。

    全量覆盖语义：调用方传入保存时刻存活标签的完整集合，被用户主动关闭
    的标签自然缺席，下次启动不恢复。空快照删文件（全关后退出 → 下次零
    恢复），避免存「空数组」残留文件。
    """
    state_file = session_file_for(workspace_root)
    if not records:
        try:
            state_file.unlink()
        except FileNotFoundError:
            pass
        return
    state_file.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(state_file, SessionArchive(sessions=records))
