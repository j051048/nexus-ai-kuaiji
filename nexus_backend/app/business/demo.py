"""Explicit fake local identities, unavailable in real authentication mode."""
from ..accounting.domain import Actor
from ..accounting.seed import LOCAL_ACTOR, USER_NAME

PERSONAS = {
    "accountant": {"id": "accountant", "label": "会计复核", "user_id": LOCAL_ACTOR.user_id, "name": USER_NAME},
    "employee": {"id": "employee", "label": "企业员工", "user_id": "local-business-employee", "name": "示例员工 小陈"},
    "department": {"id": "department", "label": "部门审批人", "user_id": "local-business-department", "name": "示例部门负责人 小林"},
    "finance": {"id": "finance", "label": "企业财务", "user_id": "local-business-finance", "name": "示例企业财务 小周"},
}


def bootstrap_business_demo(database, enabled=True):
    if not enabled or database.is_postgres:
        return
    # Only the explicitly fictional companies get demonstration assignments.
    with database.transaction(write=True) as conn:
        companies = conn.execute(
            "SELECT DISTINCT c.id FROM companies c JOIN books b ON b.company_id=c.id "
            "WHERE b.firm_id=? AND c.name IN (?,?,?)",
            (LOCAL_ACTOR.firm_id, "澄光科技（虚构示例）", "禾田商贸（虚构示例）", "青屿设计（虚构示例）"),
        ).fetchall()
        if not companies:
            return
        for persona in PERSONAS.values():
            if persona["id"] == "accountant":
                continue
            conn.execute("INSERT INTO users(id,name,email) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING",
                         (persona["user_id"], persona["name"], persona["id"] + "@demo.example.invalid"))
        for company in companies:
            department_id = "demo-department-" + company["id"]
            conn.execute("INSERT INTO departments(id,company_id,name) VALUES(?,?,'综合业务部') ON CONFLICT(id) DO NOTHING", (department_id, company["id"]))
            for key, role in (("employee", "employee"), ("department", "employee"), ("finance", "finance")):
                conn.execute("INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,?,?) ON CONFLICT(company_id,user_id) DO NOTHING",
                             (company["id"], PERSONAS[key]["user_id"], role, department_id))
            conn.execute("INSERT INTO department_approvers(firm_id,company_id,department_id,user_id) VALUES(?,?,?,?) ON CONFLICT(firm_id,department_id,user_id) DO NOTHING",
                         (LOCAL_ACTOR.firm_id, company["id"], department_id, PERSONAS["department"]["user_id"]))
            for book in conn.execute("SELECT id FROM books WHERE company_id=? AND firm_id=?", (company["id"], LOCAL_ACTOR.firm_id)):
                conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'editor') ON CONFLICT(book_id,user_id) DO NOTHING",
                             (book["id"], LOCAL_ACTOR.firm_id, PERSONAS["finance"]["user_id"]))
