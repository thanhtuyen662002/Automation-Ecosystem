from uuid import uuid4

import pytest
from pydantic import ValidationError

from automation_engine.models import EnqueueRequest, JobEnvelope


def test_enqueue_request_strips_task_name() -> None:
    request = EnqueueRequest(task_name="  example  ", payload={"value": 1})

    assert request.task_name == "example"


def test_enqueue_request_rejects_empty_task_name() -> None:
    with pytest.raises(ValidationError):
        EnqueueRequest(task_name=" ", payload={})


def test_job_envelope_validates_uuid() -> None:
    job_id = uuid4()

    envelope = JobEnvelope(job_id=job_id)

    assert envelope.job_id == job_id

