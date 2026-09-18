"""模型上下文窗口上限查询（1454 计划 T2：多数据源，缺项返回 None 不臆造）。

数据源按后端分派（2026-07-31 E2/E3 实证后定稿，非静态手写表）：

- **reasonix**（`provider/model` 别名）：解析 agent config.toml `[[providers]]`
  段的 `context_window` 字段（官方配置字段，「compaction triggers near this
  limit」语义即上下文窗口；provider 级，段内所有模型共享）。alias 为 None
  时回退顶层 `default_model`（对应 provider 用 agent 默认模型的场景）。
- **kimi**：不经本模块——wire.jsonl `llm.request.maxTokens` 随会话直给
  （实测 262144，与 kilocode 推送的 size 交叉验证一致），见 kimi_acp.py。
- **kilocode / opencode**：不经本模块——L0 `usage_update` 推送的 size
  同帧送达（1412 已落地）。

D4 红线：查不到上限一律返回 None（调用方隐藏徽章），不臆造数值。

依赖方向：本模块是 reasonix config.toml 路径解析的主定义点
（`reasonix_config_path`），providers/reasonix_acp 反向复用——
providers → context_limits，本模块不 import providers，无环。
"""
import os
import tomllib
from pathlib import Path


def reasonix_config_path() -> Path:
    """config.toml 路径：$REASONIX_HOME/config.toml → ~/.reasonix/config.toml。

    REASONIX_HOME 可覆盖安装根（reasonix 官方约定）；不存在时返回默认路径
    （调用方按文件缺失兜底，无需区分两候选——覆盖路径优先级已隐含）。
    主定义点：reasonix_acp._config_path 为本函数的兼容别名。
    """
    if home := os.environ.get("REASONIX_HOME"):
        return Path(home) / "config.toml"
    return Path.home() / ".reasonix" / "config.toml"


def reasonix_context_window(alias: str | None) -> int | None:
    """reasonix 模型别名（`provider/model`）→ 上下文窗口 tokens；查不到返回 None。

    解析规则：
    - alias 为 None 时取 config.toml 顶层 `default_model`（agent 默认模型场景）；
    - provider 名 = alias 第一个 `/` 前段，匹配 `[[providers]]` 段 `name`；
    - 取该段 `context_window`（正整数才有效），缺字段/非正数 → None。
    - 文件缺失 / TOML 解析失败 / 段缺失 → None（R2：配置结构随版本漂移，
      全 try 兜底，与 list_reasonix_models 同一防御策略）。
    """
    path: Path = reasonix_config_path()
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not alias:
        default = config.get("default_model")
        alias = default if isinstance(default, str) else None
    if not alias or "/" not in alias:
        return None
    provider_name = alias.split("/", 1)[0]
    for provider in config.get("providers") or []:
        if isinstance(provider, dict) and provider.get("name") == provider_name:
            window = provider.get("context_window")
            return window if isinstance(window, int) and window > 0 else None
    return None
