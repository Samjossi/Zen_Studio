"""provider 子包：稳定 ACP 公共层与各厂商后台目录。

本包刻意不 re-export 任何具体 provider：生产代码经 `ide.llm.registry`
的 `BackendSpec.factory` 装配实例，只有注册表可以从厂商包导入具体类。
具体类的公开入口留在各厂商包（`providers/<vendor>/__init__.py`），
跨厂商导入由 `tests/test_provider_boundaries.py` 守卫。
"""
