"""沿用 Nexus BaseTool 的策略分离思想，精简适配，不复制原平台依赖。

原始设计参考相邻 nexus-ai-command/nexus_backend/app/tools/base_tool.py。
此适配器没有绑定 LLM 或 LangGraph；它只将固定规则暴露为可调用建议步骤。
"""

from dataclasses import dataclass
from enum import Enum

from ..accounting.service import AccountingService, Actor


class ToolActionType(str, Enum):
    PROPOSE = "propose"


@dataclass(frozen=True)
class ToolPolicy:
    action_type: ToolActionType = ToolActionType.PROPOSE
    requires_book_access: bool = True
    can_approve: bool = False
    can_export: bool = False


class AccountingSuggestionTool:
    name = "accounting_suggest_drafts"
    description = "对获授权账套运行确定性分类规则，生成待人工审核草稿"
    policy = ToolPolicy()

    def __init__(self, service: AccountingService):
        self.service = service

    def execute(self, actor: Actor, book_id: str):
        # 服务再次校验成员、账套授权及期间，调用工具不能绕过领域边界。
        return self.service.suggestions(actor, book_id)
