"""Current-process transaction observations under current company manager ACL."""
from .service import OperationsService


class TelemetryService:
    def __init__(self, db, operations: OperationsService):
        self.db, self.operations = db, operations

    def overview(self, actor, company_id):
        with self.db.transaction(actor=actor, company_id=company_id) as conn:
            self.operations._company(conn, actor, company_id, manage=True)
        metrics = self.db.metrics.snapshot(firm_id=actor.firm_id, company_id=company_id)
        return {"company_id": company_id, "transactions": metrics,
                "limits": {"global_model_concurrency": 2, "company_model_concurrency": 1},
                "measurement": "当前API进程的近期有限样本；不含其它进程、未标记公司事务或历史数据库总体指标"}
