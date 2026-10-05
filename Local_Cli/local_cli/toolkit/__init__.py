"""工具内核：schema/解析/沙盒执行的纯逻辑层。

刻意不 import acp/ 与 model/——工具格式约定（§3.2）是模型↔服务端的
线约定，与 ACP 帧层正交，保持单向依赖使 parser/executor 可脱离协议
进程单测（verify_extras 即此用法）。
"""
