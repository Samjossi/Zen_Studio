"""根占用登记与唤活协议（一窗一根，work plans/2026-0831-2350 计划 D1/D2）。

同一工作区根同时只允许一个窗口：QLocalServer 按根 listen（套接字落
`USER_CONFIG_DIR/sockets/<hash8>.sock`，绝对路径 listen——放配置目录内
遵守 AGENTS.md「禁用系统临时目录」纪律，QLocalServer 默认会落 /tmp 或
XDG_RUNTIME_DIR）。套接字名与 window_state/sessions 分文件同哈希来源
（core.paths.workspace_digest）。

判定三步（main.py 单一收口调用）：
1. listen 成功 → 返回 server（本进程拥有该根）；
2. 失败 → QLocalSocket connect 探测：连上 = 已有活窗口占用，发唤活消息
   （内容无关，收到即激活）后返回 None，调用方按 EXIT_ROOT_OCCUPIED 退出；
3. 连不上 = 崩溃残留的陈旧套接字 → removeServer 清理后重试 listen
   （自愈，无需心跳/pid 文件）。

空窗（workspace_root=None）不登记：无根可占，多空窗共存本无状态文件冲突。
"""
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from core.paths import USER_CONFIG_DIR, workspace_digest

#: 占用退出码（D2）：避开正常 0 与 argparse 的 2；spawn 探活链
#: （_close_after_spawn / _report_spawn_outcome）与本常量同步消费，
#: 漏一处即「唤活被误判为启动失败」（计划 §4 红线）
EXIT_ROOT_OCCUPIED = 3

#: 套接字目录（开发态 config/sockets/；打包态 XDG 配置目录下——
#: 两态 CONFIG_DIR 不同，sockets 目录天然分离互不感知，可接受）
SOCKET_DIR = USER_CONFIG_DIR / "sockets"

#: connect 探测超时（ms）：本机 Unix domain socket 瞬时可达，300ms 足够
#: 区分「活窗口监听中」与「陈旧套接字无接收方」
_PROBE_TIMEOUT_MS = 300


def acquire_root_ownership(workspace_root: str) -> QLocalServer | None:
    """尝试拥有指定工作区根：成功返回 QLocalServer（调用方须持有并接
    newConnection 做唤活）；已被活窗口占用返回 None（唤活消息已发出）。

    须在 QApplication 创建之后调用（QLocalServer 依赖事件循环投递
    newConnection）。
    """
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    sock_path = str(SOCKET_DIR / f"{workspace_digest(workspace_root)}.sock")
    server = QLocalServer()
    if server.listen(sock_path):
        return server
    # listen 失败：探测是否已有活窗口占用（区别于崩溃残留的陈旧套接字）
    probe = QLocalSocket()
    probe.connectToServer(sock_path)
    if probe.waitForConnected(_PROBE_TIMEOUT_MS):
        # 已被活窗口占用：发唤活消息（单字节，内容无关，收到即激活）
        probe.write(b"\x01")
        probe.flush()
        probe.waitForBytesWritten(_PROBE_TIMEOUT_MS)
        probe.disconnectFromServer()
        return None
    # 连不上 = 陈旧套接字（崩溃残留）：清理后重试 listen。
    # 两进程同时 probe-fail 后竞争 listen 时败方走到此返回 None，
    # 按占用退出（竞态窗口极小且后果可接受，计划 §4）
    QLocalServer.removeServer(sock_path)
    if server.listen(sock_path):
        return server
    return None
