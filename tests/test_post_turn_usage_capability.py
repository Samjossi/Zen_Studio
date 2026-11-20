"""轮后用量宽限/看门狗改为 provider 可选能力，GUI 不按后台 ID 特判。"""
from ide.llm.providers.dsh.provider import DshAcpLLM
from ide.llm.providers.kimi.provider import KimiAcpLLM
from ide.llm.providers.local.provider import LocalAcpLLM


def test_kimi_normal_and_recovery_grace():
    llm = KimiAcpLLM()
    assert llm.post_turn_usage_grace_s() == 10.0
    assert llm.stall_watchdog_enabled() is False

    llm._session_id = "session"
    llm._needs_recovery = True
    assert llm.recovery_pending is True
    assert llm.post_turn_usage_grace_s() == 0.0


def test_dsh_and_local_default_to_no_grace_and_watchdog_on():
    for cls in (DshAcpLLM, LocalAcpLLM):
        llm = cls()
        assert llm.post_turn_usage_grace_s() == 0.0
        assert llm.stall_watchdog_enabled() is True
