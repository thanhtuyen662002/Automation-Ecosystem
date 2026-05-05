import json
import logging

from automation_engine.logging import configure_json_logging


def test_json_logging_shape(capsys) -> None:
    configure_json_logging("INFO")
    logging.getLogger("test").info(
        "hello",
        extra={
            "event": "enqueue",
            "job_id": "job-1",
            "worker_id": "worker-1",
            "status": "pending",
        },
    )

    captured = capsys.readouterr()
    record = json.loads(captured.out)

    assert record["event"] == "enqueue"
    assert record["job_id"] == "job-1"
    assert record["worker_id"] == "worker-1"
    assert record["status"] == "pending"

