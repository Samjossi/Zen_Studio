"""Local CLI 入口：解析 acp / gui / models 子命令（§1.1：客户端以 [bin, "acp"] 拉起）。"""
from __future__ import annotations

import argparse
import signal
import sys

from local_cli import AGENT_NAME
from local_cli.acp.dispatcher import RpcDispatcher
from local_cli.acp.session import SessionManager
from local_cli.acp.transport import ReverseRequestBroker, StdioTransport
from local_cli.config import BACKEND_GGUF, load_config
from local_cli.model.base import LanguageModel
from local_cli.model.gguf import GgufLanguageModel, list_gguf_aliases
from local_cli.model.mock import MockLanguageModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=AGENT_NAME, description="Local CLI —— 本地语言模型服务端")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("acp", help="ACP 协议模式（stdio ndjson JSON-RPC）")
    subparsers.add_parser("gui", help="GUI 桌面模式（PySide6，阶段 3 实现）")
    subparsers.add_parser(
        "models", help="列出本地 GGUF 模型别名（扫描配置的 model_dir，每行一个）")
    return parser


def _build_model() -> LanguageModel:
    """按配置 backend 选模型实现；默认 mock（协议回归基线不破）。"""
    config = load_config()
    if config.backend == BACKEND_GGUF:
        return GgufLanguageModel(config)
    return MockLanguageModel()


def run_acp() -> int:
    """组装协议内核并进入主读循环（组合优先：协作者在此显式接线）。"""
    # SIGTERM 转成正常退出：atexit 才有机会回收 llama-server 子进程，
    # 否则 IDE/spike 的 terminate() 会让推理实例成孤儿
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
    transport = StdioTransport(agent_name=AGENT_NAME)
    broker = ReverseRequestBroker(transport)
    model = _build_model()
    aliases = model.known_aliases()
    # 别名为空时默认别名给空串：GGUF 后端此时 is_available=False，
    # session/new 会先以 -32603 拦下，空串不会被真正用于推理
    sessions = SessionManager(default_model_alias=aliases[0] if aliases else "")
    dispatcher = RpcDispatcher(
        transport=transport,
        broker=broker,
        sessions=sessions,
        model=model,
        agent_name=AGENT_NAME,
    )
    return dispatcher.serve()


def run_models() -> int:
    """扫描 model_dir 输出 GGUF 别名清单；缺目录/空目录报错并非零退出。"""
    config = load_config()
    aliases = list_gguf_aliases(config.model_dir)
    if aliases is None:
        print(f"模型目录不存在：{config.model_dir}"
              f"（在 ~/.local-cli/config.toml 的 model_dir 键配置）", file=sys.stderr)
        return 1
    if not aliases:
        print(f"模型目录为空（无 *.gguf）：{config.model_dir}", file=sys.stderr)
        return 1
    for alias in aliases:
        print(alias)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "acp":
        return run_acp()
    if args.command == "models":
        return run_models()
    # command == "gui"：占位实现，打印说明后正常退出
    from local_cli.gui import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
