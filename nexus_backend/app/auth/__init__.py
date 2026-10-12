"""认证只建立用户身份；会计授权由数据库中的成员与账套关系决定。"""

from .supabase import AuthError, AuthSettings, Identity, SupabaseAuthenticator

__all__ = ["AuthError", "AuthSettings", "Identity", "SupabaseAuthenticator"]
