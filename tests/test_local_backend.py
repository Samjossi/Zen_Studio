"""本地模型（local-acp）后端注册与枚举兜底测试（计划 2026-1004-2124 T6）。

覆盖：
- 注册表层：local-acp 注册项存在、vendor 分组「Local」、未封存、
  纯文本能力声明与单档 auto 强度语义；
- provider 层：探测链 LOCAL_HOME 级生效；`local` 二进制缺失时
  available=False、list_local_models 兜底空列表（不崩 UI 的 R2 纪律）；
  `local models` 非零退出码/异常同样兜底空列表；正常输出逐行解析为别名。

全部经 monkeypatch 驱动，不拉真实子进程、不依赖本机安装状态。
"""
import subprocess

from llm.providers import local_acp
from llm.registry import REGISTRY, spec_of, vendor_groups


def test_local_spec_registered():
    spec = spec_of("local-acp")
    assert spec is not None
    assert spec.vendor == "local"
    assert spec.vendor_label == "Local"
    assert spec.archived is False
    assert spec.supports_images is False
    assert spec.efforts == ("auto",)
    assert spec.default_effort == "auto"


def test_local_vendor_group_present():
    groups = vendor_groups()
    assert "local" in groups
    assert any(spec.name == "local-acp" for spec in groups["local"])


def test_local_does_not_disturb_existing_backends():
    # local-acp 追加不得挤占既有接口（注册表插入序即菜单序）
    assert list(REGISTRY)[:5] == [
        "kimi-acp", "reasonix-acp", "opencode-acp", "kilocode-acp", "dream-acp"]


def test_find_bin_local_home_fallback(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "local-cli"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(local_acp.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCAL_HOME", str(tmp_path))
    assert local_acp._find_bin() == str(fake)
    assert local_acp.local_available() is True


def test_unavailable_when_bin_missing(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: None)
    assert local_acp.local_available() is False
    assert local_acp.list_local_models() == []


def test_list_models_parses_lines(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: "/fake/local")

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0,
            stdout="Qwen3.5-35B-A3B-MXFP4_MOE\n\nQwen3.5-9B-UD-Q4_K_XL\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert local_acp.list_local_models() == [
        "Qwen3.5-35B-A3B-MXFP4_MOE", "Qwen3.5-9B-UD-Q4_K_XL"]


def test_list_models_fallback_on_failure(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: "/fake/local")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="boom"))
    assert local_acp.list_local_models() == []

    def _raise(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert local_acp.list_local_models() == []
