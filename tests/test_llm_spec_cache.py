"""S5：注册表组合、BackendSpec/强度解析与三路进程缓存。

只做纯内存/临时进程验证：不启动真实后台 CLI，缓存 helper 用计数假函数；
厂商 spec 的惰性与无循环 import 用隔离子进程验证，避免污染当前进程状态。
"""
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import ide.llm as public
from ide.llm import cache, registry, spec
from ide.llm.providers.dsh import spec as dsh_spec
from ide.llm.providers.dsh.provider import DshAcpLLM
from ide.llm.providers.kimi import spec as kimi_spec
from ide.llm.providers.kimi.provider import KimiAcpLLM
from ide.llm.providers.local import spec as local_spec
from ide.llm.providers.local.provider import LocalAcpLLM

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_cache():
    """每个用例前后清空进程缓存，避免测试间串数据。"""
    cache.clear()
    yield
    cache.clear()


def _spec(**overrides) -> spec.BackendSpec:
    base = {
        "name": "unit-acp",
        "label": "Unit ACP",
        "vendor": "unit",
        "vendor_label": "Unit",
        "available": lambda: True,
        "list_models": lambda: [],
        "factory": lambda **kwargs: None,
    }
    base.update(overrides)
    return spec.BackendSpec(**base)


# ----------------------------------------------------------------------
# 注册表快照与公共导出
# ----------------------------------------------------------------------
def test_registry_order_labels_and_queries_unchanged():
    assert list(registry.REGISTRY) == ["kimi-acp", "dsh-acp", "local-acp"]
    assert registry.DEFAULT_BACKEND == "kimi-acp"
    assert registry.BACKEND_LABELS == {
        "kimi-acp": "Kimi ACP",
        "dsh-acp": "DeepSeek Harness",
        "local-acp": "Local ACP",
    }
    assert registry.VENDOR_LABELS == {
        "kimi": "Kimi", "dsh": "DSH", "local": "Local"}
    assert list(registry.vendor_groups()) == ["kimi", "dsh", "local"]
    assert registry.spec_of("kimi-acp") is kimi_spec.SPEC
    assert registry.spec_of("missing") is None
    assert registry.vendor_of("dsh-acp") == "dsh"
    assert registry.vendor_of("missing") is None


def test_public_exports_keep_backend_spec_and_resolve_efforts():
    assert public.BackendSpec is spec.BackendSpec
    assert registry.BackendSpec is spec.BackendSpec
    assert public.resolve_efforts is spec.resolve_efforts
    assert registry.resolve_efforts is spec.resolve_efforts
    assert "BackendSpec" in public.__all__
    assert "resolve_efforts" in public.__all__
    assert {"cached_list_models", "cached_raw", "clear"}.isdisjoint(public.__all__)


def test_backend_spec_is_frozen():
    with pytest.raises(FrozenInstanceError):
        kimi_spec.SPEC.name = "changed"  # type: ignore[misc]


def test_vendor_specs_snapshot_metadata_and_factories():
    assert kimi_spec.SPEC.name == "kimi-acp"
    assert kimi_spec.SPEC.vendor_label == "Kimi"
    assert kimi_spec.SPEC.supports_images is True
    assert kimi_spec.SPEC.efforts == ("low", "high", "max")
    assert kimi_spec.SPEC.default_effort == "high"
    assert kimi_spec.SPEC.vendor_settings == ()
    assert kimi_spec.SPEC.dev_note is None
    assert kimi_spec.SPEC.factory is KimiAcpLLM

    assert dsh_spec.SPEC.name == "dsh-acp"
    assert dsh_spec.SPEC.vendor_label == "DSH"
    dsh_models = dsh_spec.list_dsh_models()
    dsh_names = dsh_spec.list_dsh_display_names()
    assert dsh_models == [
        '["deepseek-official","deepseek-flash"]',
        '["deepseek-official","deepseek-v4-pro"]',
    ]
    assert set(dsh_names) == set(dsh_models)
    assert dsh_names[dsh_models[0]] == "DeepSeek-V4.1-Flash"
    assert dsh_names[dsh_models[1]] == "DeepSeek-V4-Pro"
    assert dsh_spec.SPEC.efforts == ("off", "low", "high", "max")
    assert dsh_spec.SPEC.default_effort == "high"
    assert dsh_spec.SPEC.supports_images is False
    assert "developer preview" in (dsh_spec.SPEC.dev_note or "")
    assert dsh_spec.SPEC.factory is DshAcpLLM

    assert local_spec.SPEC.name == "local-acp"
    assert local_spec.SPEC.efforts == ("auto",)
    assert local_spec.SPEC.default_effort == "auto"
    assert local_spec.SPEC.supports_images is False
    assert local_spec.SPEC.factory is LocalAcpLLM


def test_vendor_specs_have_lazy_callables():
    for backend_spec in registry.REGISTRY.values():
        assert callable(backend_spec.available)
        assert callable(backend_spec.list_models)
        assert callable(backend_spec.factory)


def test_registry_source_has_no_vendor_metadata_or_cache_logic():
    source = Path(registry.__file__).read_text(encoding="utf-8")
    for literal in ("Kimi ACP", "DeepSeek Harness", "Local ACP",
                    "kimi_wire_sidecar", "cached_list_models", "cached_raw",
                    "import threading"):
        assert literal not in source, literal


# ----------------------------------------------------------------------
# 强度解析链
# ----------------------------------------------------------------------
def test_resolve_efforts_falls_back_to_static_spec():
    backend_spec = _spec(efforts=("low", "high"), default_effort="high")
    assert spec.resolve_efforts(backend_spec, "any") == (
        ("low", "high"), "high")


def test_resolve_efforts_uses_model_level_data():
    backend_spec = _spec(
        efforts=("low", "high"), default_effort="high",
        list_efforts=lambda: {"m1": (["low", "max"], "max")})
    assert spec.resolve_efforts(backend_spec, "m1") == (
        ("low", "max"), "max")


def test_resolve_efforts_unknown_model_has_no_static_fallback():
    backend_spec = _spec(
        efforts=("low", "high"), default_effort="high",
        list_efforts=lambda: {"m1": (["low"], "low")})
    assert spec.resolve_efforts(backend_spec, "unknown") == ((), None)


def test_resolve_efforts_empty_model_data_falls_back():
    backend_spec = _spec(
        efforts=("low",), default_effort="low", list_efforts=lambda: {})
    assert spec.resolve_efforts(backend_spec, "m1") == (("low",), "low")


def test_resolve_efforts_empty_entry_and_none():
    empty_entry = _spec(
        efforts=("low",), default_effort="low",
        list_efforts=lambda: {"m1": ([], None)})
    assert spec.resolve_efforts(empty_entry, "m1") == ((), None)
    no_dynamic = _spec(efforts=(), default_effort=None)
    assert spec.resolve_efforts(no_dynamic, "m1") == ((), None)


# ----------------------------------------------------------------------
# 三路缓存与唯一失效口
# ----------------------------------------------------------------------
def test_cached_list_models_hits_once_and_returns_copy():
    calls = []

    def load() -> list[str]:
        calls.append(1)
        return ["a"]

    wrapped = cache.cached_list_models("unit-models", load)
    assert wrapped() == ["a"]
    assert wrapped() == ["a"]
    assert len(calls) == 1
    first = wrapped()
    first.append("mutated")
    assert wrapped() == ["a"], "命中必须返回副本，调用方不能改写缓存"


def test_cached_list_efforts_hits_once_and_returns_copy():
    calls = []

    def load() -> dict[str, tuple[list[str], str | None]]:
        calls.append(1)
        return {"m1": (["low"], "low")}

    wrapped = cache.cached_list_efforts("unit-efforts", load)
    result = wrapped()
    assert result == {"m1": (["low"], "low")}
    result["injected"] = (["x"], "x")
    assert wrapped() == {"m1": (["low"], "low")}
    assert len(calls) == 1


def test_cached_raw_hits_once_and_returns_same_payload():
    calls = []
    payload = {"models": {}}

    def load() -> dict:
        calls.append(1)
        return payload

    wrapped = cache.cached_raw("unit-raw", load)
    assert wrapped() is payload
    assert wrapped() is payload
    assert len(calls) == 1


def test_cache_key_is_interface_name_not_vendor():
    calls = {"a": 0, "b": 0}

    def load_a() -> list[str]:
        calls["a"] += 1
        return ["a"]

    def load_b() -> list[str]:
        calls["b"] += 1
        return ["b"]

    wrapped_a = cache.cached_list_models("vendor-a", load_a)
    wrapped_b = cache.cached_list_models("vendor-b", load_b)
    assert wrapped_a() == ["a"] and wrapped_b() == ["b"]
    assert wrapped_a() == ["a"] and wrapped_b() == ["b"]
    assert calls == {"a": 1, "b": 1}

    cache.clear("vendor-a")
    assert wrapped_a() == ["a"] and wrapped_b() == ["b"]
    assert calls == {"a": 2, "b": 1}, "按接口名清理不得影响其他接口"


def test_clear_all_clears_models_efforts_and_raw():
    counters = {"models": 0, "efforts": 0, "raw": 0}
    models = cache.cached_list_models(
        "unit-all", lambda: counters.__setitem__(
            "models", counters["models"] + 1) or ["m"])
    efforts = cache.cached_list_efforts(
        "unit-all", lambda: counters.__setitem__(
            "efforts", counters["efforts"] + 1) or {"m": (["low"], "low")})
    raw = cache.cached_raw(
        "unit-all", lambda: counters.__setitem__(
            "raw", counters["raw"] + 1) or {"ok": True})
    models(), efforts(), raw()
    assert counters == {"models": 1, "efforts": 1, "raw": 1}

    cache.clear()
    models(), efforts(), raw()
    assert counters == {"models": 2, "efforts": 2, "raw": 2}


def test_refresh_models_delegates_to_cache_clear(monkeypatch):
    calls = []
    monkeypatch.setattr(
        registry, "clear_caches", lambda name=None: calls.append(name))
    registry.refresh_models()
    registry.refresh_models("kimi-acp")
    assert calls == [None, "kimi-acp"]


# ----------------------------------------------------------------------
# 导入隔离 smoke
# ----------------------------------------------------------------------
def test_vendor_spec_imports_without_cycles_or_cli_side_effects():
    code = (
        "import subprocess\n"
        "_real_run = subprocess.run\n"
        "def _guard(args, *pargs, **kwargs):\n"
        "    cmd = args if isinstance(args, (list, tuple)) else [args]\n"
        "    text = ' '.join(str(part) for part in cmd)\n"
        "    if any(name in text for name in ('kimi', 'dsh', 'local-cli')):\n"
        "        raise AssertionError('import 期不得探测厂商 CLI')\n"
        "    return _real_run(args, *pargs, **kwargs)\n"
        "subprocess.run = _guard\n"
        "import ide.llm\n"
        "import ide.llm.providers.kimi.spec\n"
        "import ide.llm.providers.dsh.spec\n"
        "import ide.llm.providers.local.spec\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep \
        + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
