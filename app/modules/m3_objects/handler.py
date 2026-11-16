from fastapi import APIRouter, Body, Request
from app.contracts import V2
from app.errors import DomainError
from . import core
from .service import execute
router = APIRouter(tags=["M3-对象核验"])

@router.get("/objects")
def page(request: Request):
    store = request.app.state.store
    data = execute({"store": store})
    data["migration"] = store.migration_status()
    data["contract"] = store.current_contract()
    return request.app.state.templates.TemplateResponse(request, "m3_objects.html", {"title": "对象核验", "data": data})

@router.post("/api/objects/verify")
def api(request: Request, payload: dict = Body(default={})):
    return execute({**payload, "store": request.app.state.store})

@router.post("/api/objects/retry")
def retry(request: Request, payload: dict = Body(default={})):
    store = request.app.state.store
    receipt = store.get_manifest(payload["manifest_id"]) if payload.get("manifest_id") else store.latest_manifest()
    if not receipt:
        raise DomainError("NO_MANIFEST", "尚未接收清单，无法重试")
    data = execute({**payload, "store": store, "manifest_id": receipt["manifest_id"]})
    return {"retried": True, **data}

@router.post("/api/objects/contract/upgrade")
def upgrade(request: Request):
    return request.app.state.store.upgrade_contract(V2)

@router.post("/api/objects/migrate")
def migrate(request: Request, payload: dict = Body(default={})):
    return core.migrate(request.app.state.store, payload.get("batch_size"))
