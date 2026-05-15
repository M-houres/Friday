import asyncio


def test_go_back_does_not_raise_on_first_step(monkeypatch):
    from src.orchestration.workflow_engine import WorkflowEngine

    engine = WorkflowEngine()
    workflow = engine.create_workflow(
        "demo",
        [
            {"id": "step-1", "name": "Step 1", "type": "user_input"},
            {"id": "step-2", "name": "Step 2", "type": "user_input"},
        ],
    )
    workflow.current_step_index = 0

    events = []

    async def fake_navigate(*, step_from, step_to, workflow_id):
        events.append((step_from, step_to, workflow_id))

    async def fake_start_step(wf_id):
        events.append(("start", wf_id))

    monkeypatch.setattr("src.api.stream.friday_stream.workflow_navigate", fake_navigate)
    monkeypatch.setattr(engine, "start_step", fake_start_step)

    asyncio.run(engine.go_back(workflow.id))

    assert workflow.current_step_index == 0
    assert events[0] == ("step-1", "step-1", workflow.id)
