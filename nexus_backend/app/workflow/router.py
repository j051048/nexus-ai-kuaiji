from fastapi import APIRouter, Depends, Query, Request

from .models import (
    DefinitionPublish,
    DefinitionRollback,
    DefinitionWrite,
    EscalationSweep,
    InstanceAction,
    InstanceStart,
)


def build_workflow_router(actor_dependency):
    router = APIRouter(prefix="/api/workflow", tags=["流程编排中台"])

    def service(request: Request):
        return request.app.state.workflow

    @router.get("/overview")
    def overview(
        company_id: str = Query(min_length=1, max_length=120),
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.overview(current, company_id)

    @router.get("/definitions/{definition_id}")
    def definition(
        definition_id: str,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.definition(current, definition_id)

    @router.post("/definitions")
    def create_definition(
        body: DefinitionWrite,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.create_version(current, body)

    @router.post("/definitions/{definition_id}/publish")
    def publish_definition(
        definition_id: str,
        body: DefinitionPublish,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.publish(current, definition_id)

    @router.post("/definitions/{definition_id}/rollback")
    def rollback_definition(
        definition_id: str,
        body: DefinitionRollback,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.rollback(current, definition_id, body.note)

    @router.get("/diff")
    def diff_versions(
        from_id: str = Query(min_length=1, max_length=120),
        to_id: str = Query(min_length=1, max_length=120),
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.diff(current, from_id, to_id)

    @router.get("/instances")
    def instances(
        company_id: str = Query(min_length=1, max_length=120),
        status: str = Query("", max_length=20),
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.instances(current, company_id, status)

    @router.post("/instances")
    def start_instance(
        body: InstanceStart,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.start(current, body)

    @router.post("/instances/{instance_id}/actions")
    def act(
        instance_id: str,
        body: InstanceAction,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.act(current, instance_id, body)

    @router.post("/escalations")
    def sweep_escalations(
        body: EscalationSweep,
        current=Depends(actor_dependency),
        workflow=Depends(service),
    ):
        return workflow.sweep_escalations(current, body.company_id, body.now)

    return router
