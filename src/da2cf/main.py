from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session, select

from .clients.cloudflare import CloudflareClient
from .clients.directadmin import DirectAdminClient
from .config import env_config
from .database import get_session, init_db
from .models import AppSettings, Domain, SyncRun
from .scheduler import SchedulerManager
from .security import get_session_secret, require_user
from .sync_service import SyncService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="DirectAdmin ➜ Cloudflare DNS Sync")

app.add_middleware(SessionMiddleware, secret_key=get_session_secret())

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

scheduler = AsyncIOScheduler(timezone="Europe/London")
sync_service: Optional[SyncService] = None
scheduler_manager: Optional[SchedulerManager] = None


@app.on_event("startup")
async def on_startup() -> None:
    global sync_service, scheduler_manager  # noqa: PLW0603
    init_db()
    if not env_config.directadmin_base_url or not env_config.directadmin_username:
        logger.warning("DirectAdmin configuration is incomplete; UI will show errors")
    if not env_config.cloudflare_email or not env_config.cloudflare_api_key:
        logger.warning("Cloudflare configuration is incomplete; UI will show errors")

    da_client = DirectAdminClient(
        base_url=env_config.directadmin_base_url or "",
        username=env_config.directadmin_username or "",
        password=env_config.directadmin_password or "",
        token=env_config.directadmin_token or None,
    )
    cf_client = CloudflareClient(
        email=env_config.cloudflare_email or "",
        api_key=env_config.cloudflare_api_key or "",
    )
    sync_service = SyncService(da_client, cf_client)
    scheduler_manager = SchedulerManager(scheduler, sync_service)
    scheduler.start()
    scheduler_manager.schedule_all()


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if (
        username != (env_config.app_admin_user or "")
        or password != (env_config.app_admin_password or "")
    ):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=400,
        )
    request.session["user"] = username
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


def current_user(user: str = Depends(require_user)) -> str:
    return user


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domains = session.exec(select(Domain)).all()
    runs = session.exec(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(10)).all()
    next_run: Optional[datetime] = None
    if scheduler_manager:
        for d in domains:
            nr = scheduler_manager.next_run_for_domain(d.id)
            if nr and (not next_run or nr < next_run):
                next_run = nr
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "domains": domains,
            "runs": runs,
            "next_run": next_run,
        },
    )


@app.get("/domains", response_class=HTMLResponse)
async def domains_page(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domains = session.exec(select(Domain)).all()
    da_domains: List[str] = []
    da_error: Optional[str] = None
    if sync_service and sync_service.da.base_url:
        try:
            da_domains = sync_service.da.list_domains()
        except Exception as exc:  # noqa: BLE001
            da_error = str(exc)
    domain_map = {d.name: d for d in domains}
    return templates.TemplateResponse(
        "domains.html",
        {
            "request": request,
            "user": user,
            "da_domains": da_domains,
            "da_error": da_error,
            "domain_map": domain_map,
        },
    )


@app.post("/domains", response_class=HTMLResponse)
async def domains_save(
    request: Request,
    selected_domains: List[str] = Form(default=[]),
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    existing = {d.name: d for d in session.exec(select(Domain)).all()}
    selected_set = set(selected_domains)
    for name in selected_set:
        if name in existing:
            existing[name].enabled = True
            session.add(existing[name])
        else:
            session.add(Domain(name=name, enabled=True))
    for name, dom in existing.items():
        if name not in selected_set:
            dom.enabled = False
            session.add(dom)
    session.commit()
    if scheduler_manager:
        scheduler_manager.schedule_all()
    return RedirectResponse(url="/domains", status_code=302)


@app.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_detail(
    domain_id: int,
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domain = session.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404)
    da_records = []
    cf_records = []
    plan_preview = None
    da_error = None
    cf_error = None
    if sync_service:
        try:
            da_records = sync_service.da.get_dns_records(domain.name)
        except Exception as exc:  # noqa: BLE001
            da_error = str(exc)
        try:
            if domain.cf_zone_id:
                cf_records = sync_service.cf.list_dns_records(domain.cf_zone_id)
        except Exception as exc:  # noqa: BLE001
            cf_error = str(exc)
    if domain.last_plan_preview_details:
        plan_preview = domain.last_plan_preview_details
    return templates.TemplateResponse(
        "domain_detail.html",
        {
            "request": request,
            "user": user,
            "domain": domain,
            "da_records": da_records,
            "cf_records": cf_records,
            "plan_preview": plan_preview,
            "da_error": da_error,
            "cf_error": cf_error,
        },
    )


@app.post("/domains/{domain_id}/preview")
async def domain_preview(
    domain_id: int,
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domain = session.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404)
    if not sync_service:
        raise HTTPException(status_code=503, detail="Sync service not ready")
    plan = sync_service.compute_plan_for_domain(session, domain)
    domain.last_plan_preview_at = datetime.utcnow()
    domain.last_plan_preview_summary = plan.summary()
    domain.last_plan_preview_details = plan.dict()
    session.add(domain)
    session.commit()
    return RedirectResponse(url=f"/domains/{domain_id}", status_code=302)


@app.post("/domains/{domain_id}/sync")
async def domain_sync(
    domain_id: int,
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domain = session.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404)
    if not sync_service:
        raise HTTPException(status_code=503, detail="Sync service not ready")
    plan = sync_service.compute_plan_for_domain(session, domain)
    sync_service.apply_plan(session, domain, plan)
    return RedirectResponse(url=f"/domains/{domain_id}", status_code=302)


@app.post("/sync-all")
async def sync_all(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    if not sync_service:
        raise HTTPException(status_code=503, detail="Sync service not ready")
    domains = session.exec(select(Domain).where(Domain.enabled == True)).all()  # noqa: E712
    for domain in domains:
        try:
            plan = sync_service.compute_plan_for_domain(session, domain)
            sync_service.apply_plan(session, domain, plan)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error syncing domain %s during sync-all: %s", domain.name, exc)
    return RedirectResponse(url="/", status_code=302)


@app.get("/settings/proxy", response_class=HTMLResponse)
async def proxy_settings(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    settings = sync_service.get_settings(session) if sync_service else None
    return templates.TemplateResponse(
        "settings_proxy.html",
        {"request": request, "user": user, "settings": settings},
    )


@app.post("/settings/proxy")
async def proxy_settings_save(
    request: Request,
    proxy_a: Optional[str] = Form(default=None),
    proxy_aaaa: Optional[str] = Form(default=None),
    proxy_cname: Optional[str] = Form(default=None),
    proxy_host: Optional[str] = Form(default=None),
    proxy_sub: Optional[str] = Form(default=None),
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    if not sync_service:
        raise HTTPException(status_code=503, detail="Sync service not ready")
    settings = sync_service.get_settings(session)
    settings.proxy_a = bool(proxy_a)
    settings.proxy_aaaa = bool(proxy_aaaa)
    settings.proxy_cname = bool(proxy_cname)
    settings.proxy_host = bool(proxy_host)
    settings.proxy_sub = bool(proxy_sub)
    session.add(settings)
    session.commit()
    return RedirectResponse(url="/settings/proxy", status_code=302)


@app.get("/settings/schedule", response_class=HTMLResponse)
async def schedule_settings(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    domains = session.exec(select(Domain)).all()
    settings = sync_service.get_settings(session) if sync_service else None
    next_runs = {}
    if scheduler_manager:
        for d in domains:
            next_runs[d.id] = scheduler_manager.next_run_for_domain(d.id)
    return templates.TemplateResponse(
        "settings_schedule.html",
        {
            "request": request,
            "user": user,
            "domains": domains,
            "settings": settings,
            "next_runs": next_runs,
        },
    )


@app.post("/settings/schedule")
async def schedule_settings_save(
    request: Request,
    default_interval_minutes: int = Form(...),
    acme_interval_minutes: Optional[int] = Form(default=None),
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    if not sync_service or not scheduler_manager:
        raise HTTPException(status_code=503, detail="Scheduler not ready")
    settings = sync_service.get_settings(session)
    settings.default_sync_interval_minutes = default_interval_minutes
    if acme_interval_minutes:
        settings.acme_sync_interval_minutes = acme_interval_minutes
    else:
        settings.acme_sync_interval_minutes = None
    session.add(settings)
    session.commit()
    domains = session.exec(select(Domain)).all()
    for domain in domains:
        domain.sync_interval_minutes = default_interval_minutes
        session.add(domain)
    session.commit()
    scheduler_manager.schedule_all()
    return RedirectResponse(url="/settings/schedule", status_code=302)


@app.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    runs = session.exec(select(SyncRun).order_by(SyncRun.started_at.desc())).all()
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "user": user, "runs": runs},
    )


@app.get("/history/{run_id}", response_class=HTMLResponse)
async def history_detail(
    run_id: int,
    request: Request,
    user: str = Depends(current_user),
    session: Session = Depends(get_session),
):
    run = session.get(SyncRun, run_id)
    if not run:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "history_detail.html",
        {"request": request, "user": user, "run": run},
    )
