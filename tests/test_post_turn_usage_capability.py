"""能力快照矩阵：GUI 只消费 capabilities()，不按后台 ID 特判、不读私有属性。"""
from dataclasses import FrozenInstanceError

import pytest

from ide.llm.base import BackendCapabilities, capabilities_of
from ide.llm.providers.dsh.provider import DshAcpLLM
from ide.llm.providers.kimi.provider import KimiAcpLLM
from ide.llm.providers.local.provider import LocalAcpLLM


def test_kimi_normal_and_recovery_snapshot():
    llm = KimiAcpLLM()
    normal = llm.capabilities()
    assert normal.image_input is True
    assert normal.post_turn_usage_grace_s == 10.0
    assert normal.stall_watchdog is False
    assert normal.recovery_pending is False

    llm._session_id = "session"
    llm._needs_recovery = True
    recovering = llm.capabilities()
    assert recovering.recovery_pending is True
    assert recovering.post_turn_usage_grace_s == 0.0
    # 快照每次重建：恢复态变化不依赖缓存刷新
    assert recovering is not normal


def test_dsh_snapshot_before_and_after_initialize():
    llm = DshAcpLLM()
    before = llm.capabilities()
    assert before.image_input is None  # 未实测：GUI 回退注册表静态位
    assert before.post_turn_usage_grace_s == 0.0
    assert before.stall_watchdog is True
    assert before.recovery_pending is False

    llm.supports_images_override = True
    assert llm.capabilities().image_input is True
    llm.supports_images_override = False
    assert llm.capabilities().image_input is False


def test_local_default_snapshot():
    caps = LocalAcpLLM().capabilities()
    assert caps.image_input is False
    assert caps.post_turn_usage_grace_s == 0.0
    assert caps.stall_watchdog is True
    assert caps.recovery_pending is False


def test_helper_falls_back_for_objects_without_capabilities():
    class BareStub:
        pass

    default = BackendCapabilities()
    assert capabilities_of(BareStub()) == default
    assert capabilities_of(None) == default
    assert default.image_input is None
    assert default.post_turn_usage_grace_s == 0.0
    assert default.stall_watchdog is True
    assert default.recovery_pending is False


def test_snapshot_is_immutable():
    caps = KimiAcpLLM().capabilities()
    with pytest.raises(FrozenInstanceError):
        caps.image_input = False  # type: ignore[misc]
