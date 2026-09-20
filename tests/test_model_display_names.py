"""模型显示名解析与注册表字段测试（计划 2026-0920-0727 T5）。

覆盖：
- display_names_from_catalog：顶层 displayName、overrides 优先并入
  （CLI effective 语义）、缺字段/非字符串/空串跳过、非 dict 载荷回退
  空 dict；
- 注册表层：list_display_names 默认 None（无数据源接口 UI 回退别名），
  kimi-acp 挂派生 callable；字段为纯 UI 呈现数据，不碰别名数据链路。

纯解析与结构断言，不触发 CLI 子进程（kimi 派生 callable 不在此调用）。
"""
from llm.providers.kimi_common import display_names_from_catalog
from llm.registry import REGISTRY, spec_of


def test_top_level_display_name():
    data = {"models": {"kimi-code/k3": {"displayName": "K3"}}}
    assert display_names_from_catalog(data) == {"kimi-code/k3": "K3"}


def test_overrides_take_precedence():
    data = {"models": {"kimi-code/kimi-for-coding": {
        "displayName": "旧名",
        "overrides": {"displayName": "K2.8 Preview"},
    }}}
    assert display_names_from_catalog(data) == {
        "kimi-code/kimi-for-coding": "K2.8 Preview"}


def test_missing_field_skipped():
    data = {"models": {
        "kimi-code/k3": {"displayName": "K3"},
        "kimi-code/kimi-for-coding-highspeed": {"provider": "managed:kimi-code"},
    }}
    assert display_names_from_catalog(data) == {"kimi-code/k3": "K3"}


def test_non_string_display_name_skipped():
    data = {"models": {
        "a/x": {"displayName": 42},
        "a/y": {"displayName": ""},
        "a/z": "not-a-dict",
    }}
    assert display_names_from_catalog(data) == {}


def test_malformed_payload_returns_empty():
    assert display_names_from_catalog({}) == {}
    assert display_names_from_catalog({"models": None}) == {}
    assert display_names_from_catalog({"models": ["not-a-dict"]}) == {}


def test_kimi_spec_has_display_names_source():
    spec = spec_of("kimi-acp")
    assert spec is not None
    assert callable(spec.list_display_names)


def test_other_backends_default_no_display_names():
    for name, spec in REGISTRY.items():
        if name != "kimi-acp":
            assert spec.list_display_names is None, f"{name} 应回退别名呈现"
