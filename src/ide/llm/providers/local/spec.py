"""Local 后台注册声明：模型枚举缓存、静态能力、强度与工厂。

`local-cli models` 子进程枚举仍由本厂商 provider 提供；本模块只把接口级
枚举包上进程缓存，并声明 UI 可见的静态能力与单档强度。

依赖方向：本模块 → `llm.spec`/`llm.cache` 与本厂商模块；不得 import
`ide.llm.registry`、GUI 或其他厂商。import 本模块不执行 CLI。
"""
from ide.llm.cache import cached_list_models
from ide.llm.providers.local.provider import (
    LocalAcpLLM,
    list_local_models,
    local_available,
)
from ide.llm.spec import BackendSpec

SPEC = BackendSpec(
    name="local-acp",
    label="Local ACP",
    vendor="local",
    vendor_label="Local",
    available=local_available,
    # 枚举走 `local-cli models` 子进程（Local CLI 服务端扫描 model_dir
    # 的 *.gguf 产出），无静态表；兜底空列表纪律同各后台
    list_models=cached_list_models("local-acp", list_local_models),
    factory=LocalAcpLLM,
    # 当前接入的 GGUF 均为纯文本模型；日后接视觉 GGUF 实测后翻案
    supports_images=False,
    # 本地模型无强度目录，单档 auto 仅作状态呈现
    efforts=("auto",),
    default_effort="auto",
)
