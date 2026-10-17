"""Compatibility gate for explicitly reviewed company write domains.

Legacy writes remain exclusive. Company writes share that gate and serialize
within a company. PostgreSQL triggers reject crossing the declared write scope;
SQLite deliberately retains BEGIN IMMEDIATE and never pretends to be parallel.
"""


def write_lock(conn, *, scope="global", firm_id=None, company_id=None, system=False):
    if scope == "global":
        conn.execute("SELECT accounting.lock_legacy_write()")
    elif scope == "company":
        if not firm_id or not company_id:
            raise ValueError("企业写事务必须提供事务所与企业范围")
        try:
            if system:
                # 系统身份（API 进程）的企业写事务：共享全局门 + 企业锁，
                # 仅用于投影刷新等已评审的系统任务，不参与普通附件/账务写入。
                conn.execute(
                    "SELECT accounting.begin_system_company_write(?,?)", (firm_id, company_id)
                )
            else:
                conn.execute("SELECT accounting.begin_company_write(?,?)", (firm_id, company_id))
        except Exception as error:
            if getattr(error, "sqlstate", None) == "42501":
                from .domain import AccountingError

                raise AccountingError("企业授权已改变，后台结果未发布", 403) from None
            raise
    else:
        raise ValueError("写锁范围仅支持 global 或 company")
