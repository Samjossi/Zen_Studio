"""DeepSeek Harness（dsh-acp）后端注册与探测兜底测试（计划 2026-1018-0732 任务 8）。

覆盖：
- 注册表层：dsh-acp 注册项存在、插入序位于 kimi 之后、vendor 分组
  「DSH」、未封存、图片能力 False（spike 实测 initialize
  promptCapabilities.image=false）、静态强度档位与显示名表；
- provider 层：探测链 DSH_HOME 级与 npx 缓存级生效；dsh 二进制缺失时
  available=False；模型/显示名静态表与 spike 实测值一致；
- 序列化记忆兼容：spec_of 按接口名解析（settings `model_backend`
  持久化值语义），resolve_efforts 回退接口级静态档位。

全部经 monkeypatch 驱动，不拉真实子进程、不依赖本机安装状态。
"""
from llm.providers import dsh_acp
from llm.providers.dsh_acp import list_dsh_display_names, list_dsh_models
from llm.registry import REGISTRY, resolve_efforts, spec_of, vendor_groups

#: spike 实测模型选项值（文档/帧存档/dsh_spike_20261018_074413.json）：
#: 不透明 JSON 对串（路由 + 型号），原样透传语义
_SPIKE_MODELS = [
    '["deepseek-official","deepseek-v4-flash"]',
    '["deepseek-official","deepseek-flash"]',
    '["deepseek-official","deepseek-v4-pro"]',
]


def test_dsh_spec_registered():
    spec = spec_of("dsh-acp")
    assert spec is not None
    assert spec.vendor == "dsh"
    assert spec.vendor_label == "DSH"
    assert spec.label == "DeepSeek Harness"
    assert spec.archived is False
    assert spec.supports_images is False
    assert spec.efforts == ("off", "low", "high", "max")
    assert spec.default_effort == "high"
    assert spec.list_efforts is None  # 无模型级动态数据源，接口级静态兜底
    assert spec.dev_note  # developer preview 如实告知


def test_dsh_menu_position_after_kimi():
    # 插入序即菜单序：dsh-acp 置于 kimi 之后（计划 §3.1 任务 3）
    assert list(REGISTRY)[:2] == ["kimi-acp", "dsh-acp"]


def test_dsh_vendor_group_present():
    groups = vendor_groups()
    assert "dsh" in groups
    assert any(spec.name == "dsh-acp" for spec in groups["dsh"])


def test_dsh_models_match_spike():
    assert list_dsh_models() == _SPIKE_MODELS


def test_dsh_display_names_cover_models():
    names = list_dsh_display_names()
    assert set(names) == set(_SPIKE_MODELS)
    assert all(isinstance(v, str) and v for v in names.values())


def test_dsh_resolve_efforts_fallback():
    # list_efforts 为 None → 接口级静态兜底（resolve_efforts 第 2 级语义）
    spec = spec_of("dsh-acp")
    assert spec is not None
    assert resolve_efforts(spec, _SPIKE_MODELS[0]) == (
        ("off", "low", "high", "max"), "high")


def test_find_bin_dsh_home_fallback(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "dsh"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(dsh_acp.shutil, "which", lambda _name: None)
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    assert dsh_acp._find_bin() == str(fake)
    assert dsh_acp.dsh_available() is True


def test_find_bin_npx_cache_fallback(monkeypatch, tmp_path):
    # npx 缓存兜底级：~/.npm/_npx/*/node_modules/.bin/dsh 取可执行者
    cache_bin = tmp_path / ".npm" / "_npx" / "abc123" / "node_modules" / ".bin"
    cache_bin.mkdir(parents=True)
    fake = cache_bin / "dsh"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(dsh_acp.shutil, "which", lambda _name: None)
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.setattr(dsh_acp.Path, "home", lambda: tmp_path)
    assert dsh_acp._find_bin() == str(fake)


def test_unavailable_when_bin_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dsh_acp.shutil, "which", lambda _name: None)
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.setattr(dsh_acp.Path, "home", lambda: tmp_path)  # 空 home：无 ~/.dsh、无 npx 缓存
    assert dsh_acp._find_bin() is None
    assert dsh_acp.dsh_available() is False
