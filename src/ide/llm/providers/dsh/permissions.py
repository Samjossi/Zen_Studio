"""DSH 权限请求分类：只按已实证结构解释，不借用其他厂商的提问规则。

DSH 现有实证只覆盖普通工具审批（`文档/帧存档/dsh_spike_*.json` 的
`session/request_permission` 未出现提问回环）；提问能力仍属未实证专项。
因此本模块恒返回 `permission`，即使 rawInput 偶然带有其他厂商的字段名，
也不得凭相似性把它升级为提问——否则 auto_all 或卡片桥会顺着猜测路径
激活 DSH 从未验证过的交互能力。

待真实 DSH 提问帧取证后，只在本模块按实测结构扩展，并从 DSH 卡片包补
问题文本与选项语义；不得从 Kimi/Local 导入规则。原始参数原样保留。
"""
from ide.llm.providers.acp import PermissionParams, PermissionRequest


def build_permission_request(params: PermissionParams) -> PermissionRequest:
    """DSH 审批请求恒为普通权限；提问回环未实证，不在此处猜测。"""
    return PermissionRequest(params=params, kind="permission")
