"""KAP 传输层：实例排序/回环限制/发现复用与 CLI 版本门槛。"""
import json
from pathlib import Path

import pytest

from ide.llm.providers.kimi import catalog
from ide.llm.providers.kimi.kap import transport
from ide.llm.providers.kimi.kap.transport import (
    KapEndpoint,
    KapError,
    KapServer,
    _endpoint_from_origin,
    _iter_instances,
    discover_endpoint,
)


def _write_instance(home: Path, name: str, payload: dict) -> None:
    directory = home / "server" / "instances"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def test_instances_sort_desktop_first_then_newest(tmp_path):
    home = tmp_path / "home"
    _write_instance(home, "old-cli.json", {
        "pid": 1, "host": "127.0.0.1", "port": 1001,
        "product": "kimi-code-cli", "started_at": 100,
    })
    _write_instance(home, "new-cli.json", {
        "pid": 1, "host": "127.0.0.1", "port": 1002,
        "product": "kimi-code-cli", "started_at": 200,
    })
    _write_instance(home, "desktop.json", {
        "pid": 1, "host": "127.0.0.1", "port": 1003,
        "product": "kimi-code-desktop", "started_at": 50,
    })
    order = [item["port"] for item in _iter_instances(home)]
    assert order == [1003, 1002, 1001]


def test_instances_skip_broken_json(tmp_path):
    home = tmp_path / "home"
    directory = home / "server" / "instances"
    directory.mkdir(parents=True)
    (directory / "broken.json").write_text("{", encoding="utf-8")
    assert _iter_instances(home) == []


def test_endpoint_rejects_non_loopback_token_target():
    with pytest.raises(KapError, match="回环"):
        _endpoint_from_origin("http://203.0.113.10:3000", "token")


def test_endpoint_maps_wildcard_to_loopback():
    endpoint = _endpoint_from_origin("http://0.0.0.0:4321", "token")
    assert endpoint.origin == "http://127.0.0.1:4321"
    assert endpoint.ws_url == "ws://127.0.0.1:4321/api/v1/ws"


def test_discover_endpoint_uses_server_token_and_probe(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "server.token").write_text("secret-token\n", encoding="utf-8")
    _write_instance(home, "inst.json", {
        "pid": 1, "host": "0.0.0.0", "port": 4321,
        "product": "kimi-code-cli", "started_at": 1,
    })
    seen = []

    def fake_probe(origin, token):
        seen.append((origin, token))
        return KapEndpoint(origin=origin, ws_url="ws://127.0.0.1:4321/api/v1/ws",
                           token=token, product="kimi-code-cli")

    monkeypatch.setattr(transport, "_probe", fake_probe)
    monkeypatch.setattr(transport.os, "kill", lambda pid, sig: None)
    endpoint = discover_endpoint(home)
    assert endpoint is not None
    assert seen == [("http://127.0.0.1:4321", "secret-token")]
    assert endpoint.token == "secret-token"


def test_discover_endpoint_none_without_token(tmp_path):
    home = tmp_path / "home"
    _write_instance(home, "inst.json", {
        "pid": 1, "host": "127.0.0.1", "port": 4321,
        "product": "kimi-code-cli", "started_at": 1,
    })
    assert discover_endpoint(home) is None


def test_server_ensure_reuses_discovered_without_process(monkeypatch):
    endpoint = KapEndpoint(origin="http://127.0.0.1:1",
                           ws_url="ws://127.0.0.1:1/api/v1/ws", token="t")
    monkeypatch.setattr(transport, "discover_endpoint", lambda *args: endpoint)
    server = KapServer(".")
    assert server.ensure() is endpoint
    server.close()  # 复用实例不归本对象所有，close 无副作用


def _version_proc(text: str):
    return type("Proc", (), {"stdout": text, "stderr": ""})()


def test_kimi_kap_available_version_gate(monkeypatch):
    monkeypatch.setattr(catalog, "_find_bin", lambda: "/fake/kimi")
    monkeypatch.setattr(catalog.subprocess, "run",
                        lambda *args, **kwargs: _version_proc("2.6.9\n"))
    assert catalog.kimi_kap_available() is False
    monkeypatch.setattr(catalog.subprocess, "run",
                        lambda *args, **kwargs: _version_proc("2.7.0\n"))
    assert catalog.kimi_kap_available() is True
    monkeypatch.setattr(catalog.subprocess, "run",
                        lambda *args, **kwargs: _version_proc("2.7.2\n"))
    assert catalog.kimi_kap_available() is True
    # 解析失败 fail-open：版本格式变化不应让可用后端消失
    monkeypatch.setattr(catalog.subprocess, "run",
                        lambda *args, **kwargs: _version_proc("unknown\n"))
    assert catalog.kimi_kap_available() is True
    monkeypatch.setattr(catalog, "_find_bin", lambda: None)
    assert catalog.kimi_kap_available() is False
