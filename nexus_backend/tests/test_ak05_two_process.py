"""AK05: two OS processes claim the same durable SQLite job once."""
import multiprocessing

from test_intelligence_workflow import ai_case
from test_queue_workflow import create_event, enqueue, queue_case


def _claim(path, worker_id, output):
    from app.accounting.database import Database
    from app.intelligence.service import IntelligenceService
    from app.jobs.service import QueueService

    database = Database(path)
    try:
        service = IntelligenceService(database, None)
        job = QueueService(database, service).claim(worker_id, lease_seconds=180, concurrency=2)
        output.put((worker_id, job["id"] if job else None))
    finally:
        database.close()


def test_two_processes_claim_one_job(queue_case):
    case = queue_case
    enqueue(case, [create_event(case)])
    output = multiprocessing.get_context("spawn").Queue()
    processes = [
        multiprocessing.get_context("spawn").Process(
            target=_claim, args=(case.db.path, f"ak05-proc-{index}", output)
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    results = [output.get(timeout=5) for _ in processes]
    claimed = [job_id for _worker, job_id in results if job_id]
    assert len(claimed) == 1, results
    assert len({worker for worker, _job in results}) == 2
