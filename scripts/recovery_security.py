"""Explicit reconstruction of accounting capability ACLs on a held restore.

No login passwords, memberships, PUBLIC grants or grant options are copied.
Callers own the admin connection and transaction. Structural fingerprints bind
the approved ACL to the restored policies, triggers and function definitions.
"""

import hashlib
import json
import re

ROLES = ("accounting_app", "accounting_system", "accounting_worker", "accounting_projection")
PRIVILEGES = {
    "schema": {"USAGE"},
    "table": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "sequence": {"USAGE", "SELECT", "UPDATE"},
    "function": {"EXECUTE"},
}
IDENTIFIER = re.compile(r"^[a-z_][a-z_0-9]*$")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_security_manifest(value):
    if not isinstance(value, dict) or set(value) != {"version", "structure_sha256", "grants"}:
        raise ValueError("恢复授权清单字段不合法")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("恢复授权清单版本不支持")
    if not re.fullmatch(r"[a-f0-9]{64}", str(value["structure_sha256"])):
        raise ValueError("恢复结构指纹不合法")
    if not isinstance(value["grants"], list) or len(value["grants"]) > 100_000:
        raise ValueError("恢复授权集合不合法")
    seen = set()
    for grant in value["grants"]:
        if not isinstance(grant, dict) or set(grant) != {
            "kind",
            "object",
            "column",
            "role",
            "privilege",
        }:
            raise ValueError("恢复授权条目不合法")
        kind, name, column = grant["kind"], grant["object"], grant["column"]
        valid_name = isinstance(name, str) and bool(IDENTIFIER.fullmatch(name))
        if kind == "function":
            # Functions are referred to by their structural catalog key, never
            # interpolated from a supplied SQL signature.
            valid_name = isinstance(name, str) and bool(re.fullmatch(r"[a-f0-9]{64}", name))
        if (
            kind not in PRIVILEGES
            or not valid_name
            or grant["role"] not in ROLES
            or grant["privilege"] not in PRIVILEGES.get(kind, set())
            or (
                column is not None
                and (
                    kind != "table"
                    or not isinstance(column, str)
                    or not IDENTIFIER.fullmatch(column)
                )
            )
            or (column is not None and grant["privilege"] == "DELETE")
            or (kind == "schema" and name != "accounting")
        ):
            raise ValueError("恢复授权包含未批准的角色、对象或权限")
        key = tuple(grant[k] for k in ("kind", "object", "column", "role", "privilege"))
        if key in seen:
            raise ValueError("恢复授权重复")
        seen.add(key)
    return value


def _catalog(conn):
    # Deparsed identifiers must not depend on a source/target login's defaults.
    conn.execute("SELECT set_config('search_path','pg_catalog,accounting',true)")
    relations = conn.execute("""SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
      (SELECT coalesce(json_agg(json_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
         (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=a.attrelid AND d.adnum=a.attnum)) ORDER BY a.attnum),'[]')
       FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='accounting' AND c.relkind IN ('r','p','v','m','S') ORDER BY c.relname""").fetchall()
    policies = conn.execute("""SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check
      FROM pg_policies WHERE schemaname='accounting' ORDER BY tablename,policyname""").fetchall()
    triggers = conn.execute("""SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled
      FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='accounting' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""").fetchall()
    # PostgreSQL may flatten nested AND nodes during dump/reparse. Its own
    # precedence-aware deparser canonicalizes that associativity while keeping
    # OR/NOT grouping; stripping parentheses by regex would be unsafe.
    constraints = conn.execute("""SELECT c.relname,k.conname,pg_get_constraintdef(k.oid,true),k.convalidated
      FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='accounting' ORDER BY c.relname,k.conname""").fetchall()
    indexes = conn.execute(
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='accounting' ORDER BY tablename,indexname"
    ).fetchall()
    functions = conn.execute("""SELECT p.oid,p.proname,pg_get_function_identity_arguments(p.oid),
      pg_get_functiondef(p.oid),p.prosecdef,p.proconfig
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='accounting' AND p.prokind='f' ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)""").fetchall()
    structure = {
        "relations": relations,
        "policies": policies,
        "triggers": triggers,
        "constraints": constraints,
        "indexes": indexes,
        "functions": [list(row[1:]) for row in functions],
    }
    function_keys = {row[0]: (digest([row[1], row[2]]), row[1], row[2]) for row in functions}
    return digest(structure), function_keys


def capture_security(conn):
    structure, functions = _catalog(conn)
    grants = []
    rows = conn.execute(
        """SELECT CASE WHEN c.relkind='S' THEN 'sequence' ELSE 'table' END,c.relname,
      NULL::text,r.rolname,x.privilege_type
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL aclexplode(c.relacl) x JOIN pg_roles r ON r.oid=x.grantee
      WHERE n.nspname='accounting' AND c.relkind IN ('r','p','v','m','S') AND r.rolname=ANY(%s)
      UNION ALL SELECT 'table',c.relname,a.attname,r.rolname,x.privilege_type
      FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL aclexplode(a.attacl) x JOIN pg_roles r ON r.oid=x.grantee
      WHERE n.nspname='accounting' AND a.attnum>0 AND NOT a.attisdropped AND r.rolname=ANY(%s)
      UNION ALL SELECT 'schema',n.nspname,NULL,r.rolname,x.privilege_type
      FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) x JOIN pg_roles r ON r.oid=x.grantee
      WHERE n.nspname='accounting' AND r.rolname=ANY(%s)""",
        (list(ROLES),) * 3,
    ).fetchall()
    for kind, name, column, role, privilege in rows:
        grants.append(dict(kind=kind, object=name, column=column, role=role, privilege=privilege))
    for oid, role, privilege in conn.execute(
        """SELECT p.oid,r.rolname,x.privilege_type
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      CROSS JOIN LATERAL aclexplode(p.proacl) x JOIN pg_roles r ON r.oid=x.grantee
      WHERE n.nspname='accounting' AND p.prokind='f' AND r.rolname=ANY(%s)""",
        (list(ROLES),),
    ):
        grants.append(
            dict(
                kind="function",
                object=functions[oid][0],
                column=None,
                role=role,
                privilege=privilege,
            )
        )
    grants.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return validate_security_manifest(
        {"version": 1, "structure_sha256": structure, "grants": grants}
    )


def restore_security(conn, manifest):
    """Inside the caller's single admin transaction; leave DB hold unchanged."""
    from psycopg import sql

    validate_security_manifest(manifest)
    verify_security_owners(conn)
    held = conn.execute("SELECT count(*) FROM accounting.restore_state WHERE hold").fetchone()[0]
    if not held:
        raise ValueError("只能重建处于恢复隔离状态的数据库授权")
    for role in ROLES:
        row = conn.execute(
            "SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=%s",
            (role,),
        ).fetchone()
        if not row or any(row):
            raise ValueError("内部能力角色必须 NOLOGIN 且不得具备管理或绕过权限")
    structure, functions = _catalog(conn)
    if structure != manifest["structure_sha256"]:
        raise ValueError("恢复后的 RLS、触发器、字段或函数结构与备份不一致")
    lookup = {data[0]: (oid, data[1], data[2]) for oid, data in functions.items()}
    roles = sql.SQL(",").join([sql.SQL("PUBLIC"), *(sql.Identifier(role) for role in ROLES)])
    for scope in ("ALL TABLES", "ALL SEQUENCES", "ALL FUNCTIONS"):
        conn.execute(
            sql.SQL("REVOKE ALL ON {} IN SCHEMA accounting FROM {}").format(sql.SQL(scope), roles)
        )
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA accounting FROM {}").format(roles))
    # --no-acl starts with no column grants. Reject reuse rather than silently
    # retaining an unexpected column privilege introduced after restoration.
    columns = conn.execute("""SELECT c.relname,a.attname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND a.attacl IS NOT NULL""").fetchall()
    for table, column in columns:
        conn.execute(
            sql.SQL("REVOKE ALL ({}) ON accounting.{} FROM {}").format(
                sql.Identifier(column), sql.Identifier(table), roles
            )
        )
    for grant in manifest["grants"]:
        kind, name, column = grant["kind"], grant["object"], grant["column"]
        target = sql.Identifier("accounting", name)
        if kind == "schema":
            target = sql.Identifier("accounting")
        elif kind == "function":
            if name not in lookup:
                raise ValueError("恢复函数身份不匹配")
            # Resolve a trusted regprocedure from THIS database; never accept a
            # signature as executable text from a backup manifest.
            target = sql.SQL(
                conn.execute("SELECT %s::oid::regprocedure::text", (lookup[name][0],)).fetchone()[0]
            )
        privilege = sql.SQL(grant["privilege"])
        if column is not None:
            privilege = sql.SQL("{} ({})").format(privilege, sql.Identifier(column))
        conn.execute(
            sql.SQL("GRANT {} ON {} {} TO {}").format(
                privilege,
                sql.SQL(
                    {
                        "table": "TABLE",
                        "sequence": "SEQUENCE",
                        "function": "FUNCTION",
                        "schema": "SCHEMA",
                    }[kind]
                ),
                target,
                sql.Identifier(grant["role"]),
            )
        )
    restored = capture_security(conn)
    if restored != manifest:
        raise ValueError("恢复后的逐对象／逐列授权不等于已批准清单")
    return {
        "security_verified": True,
        "grants": len(manifest["grants"]),
        "structure_sha256": structure,
    }


def verify_sequences(conn):
    """Read-only next-ID proof, including identity columns; never call nextval."""
    from psycopg import sql

    sequences = conn.execute("""SELECT s.relname,t.relname,a.attname,q.seqincrement
      FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace
      JOIN pg_sequence q ON q.seqrelid=s.oid JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass
      JOIN pg_class t ON t.oid=d.refobjid JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
      WHERE n.nspname='accounting' AND s.relkind='S' AND d.deptype IN ('a','i') ORDER BY s.relname""").fetchall()
    for sequence, table, column, increment in sequences:
        maximum = conn.execute(
            sql.SQL("SELECT max({}) FROM accounting.{}").format(
                sql.Identifier(column), sql.Identifier(table)
            )
        ).fetchone()[0]
        last, called = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM accounting.{}").format(
                sql.Identifier(sequence)
            )
        ).fetchone()
        if increment <= 0 or (
            maximum is not None and last + (increment if called else 0) <= maximum
        ):
            raise ValueError("恢复数据库序列落后于已存在记录，禁止启用服务")
    return {"verified_sequences": len(sequences), "nextval_called": False}


def verify_security_owners(conn):
    # --no-owner must make the explicit recovery administrator the owner of
    # every restored definer. Do not bless an unrelated or runtime owner.
    changed = conn.execute("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='accounting' AND p.prosecdef AND p.proowner<>current_user::regrole""").fetchone()[
        0
    ]
    if changed:
        raise ValueError("恢复权限函数所有者不是本次受控恢复管理员")
    return {"security_definer_owners_verified": True}
