"""Git 数据层：subprocess 调用系统 git CLI，为零 Qt 依赖的纯 Python 包。

选型依据：文档/选型记录/2026-0720-0135_Git文件装饰与简易差异指示方案选型.md（方案 A）。
实施计划：文档/修改记录/2026-0720-0131_Git文件装饰与差异统计实施计划.md 阶段一。
"""
from core.git.service import GitStatusService

__all__ = ["GitStatusService"]
