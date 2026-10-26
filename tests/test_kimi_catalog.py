"""kimi 模型目录别名、强度档位、显示名与上下文窗口解析测试。

字段形态以 2026-10-24 本机 CLI 2.0.0 实测载荷为准（原始载荷存档
`.temp/kimi_provider_list_20261024.json`）：K3.1 Pro 系列条目
supportEfforts/defaultEffort 齐全、无 overrides 子表（服务端目录
本即为新，无需 kimi-for-coding 当年的 overrides 过渡手段）；
kimi-for-coding 的 overrides 并入路径为既有回归保护。

纯解析断言，不触发 CLI 子进程。
"""
from llm.providers.kimi_common import (
    context_sizes_from_catalog,
    efforts_from_catalog,
    models_from_catalog,
)


def _catalog_20261024() -> dict:
    """2026-10-24 实测目录的裁剪形态（字段名与取值照抄实测载荷）。"""
    return {"models": {
        "kimi-code/k3": {
            "displayName": "K3",
            "maxContextSize": 1048576,
            "supportEfforts": ["low", "high", "max"],
            "defaultEffort": "high",
        },
        "kimi-code/k3.1-pro": {
            "displayName": "K3.1 Pro",
            "maxContextSize": 1048576,
            "supportEfforts": ["low", "high", "max"],
            "defaultEffort": "max",
        },
        "kimi-code/k3.1-pro-highspeed": {
            "displayName": "K3.1 Pro Highspeed",
            "maxContextSize": 1048576,
            "supportEfforts": ["low", "high", "max"],
            "defaultEffort": "max",
        },
        "kimi-code/kimi-for-coding-highspeed": {
            "provider": "managed:kimi-code",
            "maxContextSize": 262144,
        },
    }}


def test_context_sizes_from_catalog():
    sizes = context_sizes_from_catalog(_catalog_20261024())
    assert sizes == {
        "kimi-code/k3": 1048576,
        "kimi-code/k3.1-pro": 1048576,
        "kimi-code/k3.1-pro-highspeed": 1048576,
        "kimi-code/kimi-for-coding-highspeed": 262144,
    }


def test_context_sizes_overrides_take_precedence():
    data = {"models": {"a/model": {
        "maxContextSize": 1000,
        "overrides": {"maxContextSize": 2000},
    }}}
    assert context_sizes_from_catalog(data) == {"a/model": 2000}


def test_context_sizes_reject_invalid_values():
    data = {"models": {
        f"a/model-{index}": {"maxContextSize": value}
        for index, value in enumerate((None, "262144", 0, -1, True, 1.5))
    }}
    assert context_sizes_from_catalog(data) == {}
    assert context_sizes_from_catalog({}) == {}
    assert context_sizes_from_catalog({"models": None}) == {}


def test_k31_pro_efforts_default_max():
    efforts = efforts_from_catalog(_catalog_20261024())
    assert efforts["kimi-code/k3.1-pro"] == (["low", "high", "max"], "max")
    assert efforts["kimi-code/k3.1-pro-highspeed"] == (
        ["low", "high", "max"], "max")


def test_existing_models_efforts_unchanged():
    efforts = efforts_from_catalog(_catalog_20261024())
    assert efforts["kimi-code/k3"] == (["low", "high", "max"], "high")
    # 无 supportEfforts 的模型不产生条目（无强度轴，D1 不做静态兜底）
    assert "kimi-code/kimi-for-coding-highspeed" not in efforts


def test_models_sorted_menu_order():
    models = models_from_catalog(_catalog_20261024())
    assert models == sorted(models)
    assert "kimi-code/k3.1-pro" in models
    assert "kimi-code/k3.1-pro-highspeed" in models


def test_kimi_for_coding_overrides_regression():
    """kimi-for-coding 的 overrides 并入路径不受新模型条目影响。"""
    data = _catalog_20261024()
    data["models"]["kimi-code/kimi-for-coding"] = {
        "supportEfforts": ["low"],
        "defaultEffort": "low",
        "overrides": {
            "supportEfforts": ["low", "high", "max"],
            "defaultEffort": "max",
        },
    }
    efforts = efforts_from_catalog(data)
    assert efforts["kimi-code/kimi-for-coding"] == (["low", "high", "max"], "max")
    assert efforts["kimi-code/k3.1-pro"] == (["low", "high", "max"], "max")
