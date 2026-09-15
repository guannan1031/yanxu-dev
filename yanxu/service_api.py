"""FastAPI surface for the Yanxu private team service."""

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from .service import (AuthenticationError, AuthorizationError, ConflictError,
                      NotFoundError, PostgresStore, Principal, ServiceError,
                      ValidationError, parse_cost_amount)


def create_app(database_url: str | None = None, bootstrap: dict | None = None,
               github_webhook_secret: str | None = None, secure_cookies: bool | None = None):
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
        from fastapi.responses import HTMLResponse, RedirectResponse, Response
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:
        raise ServiceError("Install the private service dependencies with: pip install '.[server]'") from exc

    database_url = database_url or os.environ.get("YANXU_DATABASE_URL", "")
    store = PostgresStore(database_url)
    from .github_events import GitHubEventStore
    github_store = GitHubEventStore(database_url)
    github_webhook_secret = github_webhook_secret or os.environ.get("YANXU_GITHUB_WEBHOOK_SECRET")
    if secure_cookies is None and "YANXU_SECURE_COOKIES" in os.environ:
        cookie_setting = os.environ["YANXU_SECURE_COOKIES"].strip().lower()
        if cookie_setting not in {"1", "true", "yes", "0", "false", "no"}:
            raise ValidationError("YANXU_SECURE_COOKIES must be true or false")
        secure_cookies = cookie_setting in {"1", "true", "yes"}
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
        version="1.0.0",
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

    class SessionInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: str = Field(min_length=24, max_length=512)

    class CostInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        category: str
        amount: str
        currency: str
        evidence_ref: str = Field(min_length=1, max_length=500)

    class AcceptanceInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        criterion: str = Field(min_length=1, max_length=240)

    class AcceptanceUpdate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        status: str
        evidence_ref: str | None = Field(default=None, max_length=500)
        customer_confirmed: bool = False

    class SupportInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        category: str
        minutes: int = Field(ge=1, le=100000)
        evidence_ref: str = Field(min_length=1, max_length=500)

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

    def browser_principal(request: Request) -> Principal:
        session = request.cookies.get("yanxu_session", "")
        return store.authenticate_web_session(session)

    def html_response(content: str, status_code: int = 200):
        return HTMLResponse(content, status_code=status_code, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
                                       "script-src 'unsafe-inline'; img-src data:; form-action 'self'; "
                                       "base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        })

    @app.get("/healthz")
    def healthz():
        try:
            healthy = store.health()
        except Exception:
            raise HTTPException(status_code=503, detail="Database is unavailable")
        return {"status": "ok" if healthy else "unavailable", "database": healthy}

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app", status_code=303)

    @app.get("/login")
    def login_page():
        from .pilot_dashboard import render_login
        return html_response(render_login())

    @app.post("/v1/session")
    def create_session(body: SessionInput, request: Request):
        result = call(store.create_web_session, body.token)
        response = Response(content='{"status":"ok"}', media_type="application/json",
                            headers={"Cache-Control": "no-store"})
        use_secure_cookie = secure_cookies if secure_cookies is not None else request.url.scheme == "https"
        response.set_cookie("yanxu_session", result["session"], max_age=28_800,
                            httponly=True, secure=use_secure_cookie, samesite="strict", path="/")
        return response

    @app.get("/app")
    def dashboard(request: Request):
        from .pilot_dashboard import build_summary, render_dashboard
        try:
            current = browser_principal(request)
        except AuthenticationError:
            return RedirectResponse("/login", status_code=303)
        return html_response(render_dashboard(build_summary(store, github_store, current)))

    @app.get("/v1/audit/export")
    def audit_export(request: Request):
        from .pilot_dashboard import build_audit_export
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        body, fingerprint = build_audit_export(store, current)
        return Response(content=body, media_type="application/json", headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="yanxu-audit-{current.organization_slug}.json"',
            "X-Yanxu-Evidence-Fingerprint": fingerprint,
        })

    @app.get("/v1/costs")
    def costs(request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return {"items": store.list_cost_records(current), "summary": store.summarize_costs(current)}

    @app.post("/v1/costs", status_code=201)
    def create_cost(body: CostInput, request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        amount_micros = call(parse_cost_amount, body.amount)
        result = call(store.create_cost_record, current, body.category, amount_micros,
                      body.currency, body.evidence_ref)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/v1/pilot/acceptance")
    def acceptance(request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return {"items": store.list_acceptance_items(current),
                "summary": store.summarize_acceptance(current)}

    @app.post("/v1/pilot/acceptance", status_code=201)
    def create_acceptance(body: AcceptanceInput, request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        result = call(store.create_acceptance_item, current, body.criterion)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.patch("/v1/pilot/acceptance/{item_id}")
    def update_acceptance(item_id: str, body: AcceptanceUpdate, request: Request,
                          response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        result = call(store.update_acceptance_item, current, item_id, body.status,
                      body.evidence_ref, body.customer_confirmed)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/v1/pilot/support")
    def support(request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return {"items": store.list_support_records(current),
                "summary": store.summarize_support(current)}

    @app.post("/v1/pilot/support", status_code=201)
    def create_support(body: SupportInput, request: Request, response: Response):
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        result = call(store.create_support_record, current, body.category, body.minutes,
                      body.evidence_ref)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/v1/pilot/report")
    def pilot_report(request: Request):
        from .pilot_dashboard import build_acceptance_report
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        content, fingerprint = build_acceptance_report(store, github_store, current)
        response = html_response(content)
        response.headers["Content-Disposition"] = (
            f'inline; filename="yanxu-pilot-report-{current.organization_slug}.html"')
        response.headers["X-Yanxu-Evidence-Fingerprint"] = fingerprint
        return response

    @app.get("/v1/pilot/export")
    def pilot_export(request: Request):
        from .pilot_dashboard import build_pilot_bundle
        try:
            current = browser_principal(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        body, fingerprint = build_pilot_bundle(store, github_store, current)
        return Response(content=body, media_type="application/zip", headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="yanxu-pilot-{current.organization_slug}.zip"',
            "X-Yanxu-Evidence-Fingerprint": fingerprint,
        })

    @app.post("/logout")
    def logout(request: Request):
        session = request.cookies.get("yanxu_session", "")
        if session:
            try:
                store.revoke_web_session(session)
            except AuthenticationError:
                pass
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("yanxu_session", path="/")
        return response

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
