"""FastAPI surface for the Yanxu private team service."""

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from .service import (AuthenticationError, AuthorizationError, ConflictError,
                      NotFoundError, PostgresStore, Principal, ServiceError,
                      ValidationError)


def create_app(database_url: str | None = None, bootstrap: dict | None = None,
               github_webhook_secret: str | None = None):
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:
        raise ServiceError("Install the private service dependencies with: pip install '.[server]'") from exc

    database_url = database_url or os.environ.get("YANXU_DATABASE_URL", "")
    store = PostgresStore(database_url)
    from .github_events import GitHubEventStore
    github_store = GitHubEventStore(database_url)
    github_webhook_secret = github_webhook_secret or os.environ.get("YANXU_GITHUB_WEBHOOK_SECRET")
    if bootstrap is None:
        values = {
            "slug": os.environ.get("YANXU_BOOTSTRAP_ORG_SLUG"),
            "name": os.environ.get("YANXU_BOOTSTRAP_ORG_NAME"),
            "token": os.environ.get("YANXU_BOOTSTRAP_TOKEN"),
        }
        bootstrap = values if all(values.values()) else None

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        github_store.initialize()
        if bootstrap:
            store.bootstrap(bootstrap["slug"], bootstrap["name"], bootstrap["token"])
        app.state.store = store
        yield

    app = FastAPI(
        title="Yanxu Dev Private Service",
        version="0.16.1",
        description="Organization-scoped storage for normalized Yanxu delivery evidence.",
        lifespan=lifespan,
    )
    bearer = HTTPBearer(auto_error=False)

    class WorkspaceInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=1, max_length=120)

    class TokenInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        label: str = Field(min_length=1, max_length=120)
        role: str

    class InstallationInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        github_installation_id: int = Field(gt=0)
        account_login: str = Field(min_length=1, max_length=120)

    def principal(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token is required")
        try:
            return store.authenticate(credentials.credentials)
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    def owner(current: Annotated[Principal, Depends(principal)]) -> Principal:
        if current.role != "owner":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role is required")
        return current

    def call(function, *args):
        try:
            return function(*args)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/healthz")
    def healthz():
        try:
            healthy = store.health()
        except Exception:
            raise HTTPException(status_code=503, detail="Database is unavailable")
        return {"status": "ok" if healthy else "unavailable", "database": healthy}

    @app.get("/v1/me")
    def me(current: Annotated[Principal, Depends(principal)]):
        return {"organization_id": current.organization_id, "organization_slug": current.organization_slug,
                "token_id": current.token_id, "role": current.role}

    @app.post("/v1/tokens", status_code=201)
    def create_token(body: TokenInput, current: Annotated[Principal, Depends(owner)]):
        return call(store.issue_token, current, body.label, body.role)

    @app.get("/v1/tokens")
    def tokens(current: Annotated[Principal, Depends(owner)]):
        return {"items": store.list_tokens(current)}

    @app.delete("/v1/tokens/{token_id}")
    def revoke_token(token_id: str, current: Annotated[Principal, Depends(owner)]):
        return call(store.revoke_token, current, token_id)

    @app.get("/v1/workspaces")
    def workspaces(current: Annotated[Principal, Depends(principal)]):
        return {"items": store.list_workspaces(current)}

    @app.post("/v1/workspaces", status_code=201)
    def create_workspace(body: WorkspaceInput, current: Annotated[Principal, Depends(owner)]):
        return call(store.create_workspace, current, body.name)

    @app.post("/v1/workspaces/{workspace_id}/snapshots", status_code=201)
    def save_snapshot(workspace_id: str, body: dict[str, Any],
                      current: Annotated[Principal, Depends(owner)]):
        return call(store.save_snapshot, current, workspace_id, body)

    @app.get("/v1/workspaces/{workspace_id}/snapshots/latest")
    def latest_snapshot(workspace_id: str, current: Annotated[Principal, Depends(principal)]):
        return call(store.latest_snapshot, current, workspace_id)

    @app.get("/v1/audit")
    def audit(current: Annotated[Principal, Depends(principal)], limit: int = Query(100, ge=1, le=200)):
        return {"items": store.list_audit(current, limit)}

    @app.post("/v1/github/installations", status_code=201)
    def register_installation(body: InstallationInput,
                              current: Annotated[Principal, Depends(owner)]):
        return call(github_store.register_installation, current, body.github_installation_id,
                    body.account_login)

    @app.get("/v1/github/installations")
    def installations(current: Annotated[Principal, Depends(principal)]):
        return {"items": github_store.list_installations(current)}

    @app.get("/v1/github/deliveries")
    def deliveries(current: Annotated[Principal, Depends(principal)],
                   limit: int = Query(100, ge=1, le=200)):
        return {"items": github_store.list_deliveries(current, limit)}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(
        request: Request,
        x_hub_signature_256: Annotated[str | None, Header()] = None,
        x_github_delivery: Annotated[str | None, Header()] = None,
        x_github_event: Annotated[str | None, Header()] = None,
    ):
        if not github_webhook_secret:
            raise HTTPException(status_code=503, detail="GitHub webhook is not configured")
        if not x_github_delivery or not x_github_event:
            raise HTTPException(status_code=422, detail="GitHub delivery headers are required")
        body = await request.body()
        return call(github_store.receive, github_webhook_secret, x_hub_signature_256,
                    x_github_delivery, x_github_event, body)

    return app


def run_server(host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ServiceError("Install the private service dependencies with: pip install '.[server]'") from exc
    uvicorn.run(create_app(), host=host, port=port)
