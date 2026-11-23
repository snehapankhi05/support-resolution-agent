"""The queue publisher sends the run id and does not open a broker in this test."""

import uuid

from app.worker import enqueue_workflow, run_workflow


def test_enqueue_publishes_the_run_id(monkeypatch):
    published: list[str] = []
    monkeypatch.setattr(run_workflow, "delay", published.append)
    run_id = uuid.uuid4()
    enqueue_workflow(run_id)
    assert published == [str(run_id)]
    assert run_workflow.name == "workflows.run"
