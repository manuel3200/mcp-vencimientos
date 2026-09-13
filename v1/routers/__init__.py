from fastapi import FastAPI

from routers.accounts_router import router as accounts_router
from routers.auth_router import router as auth_router
from routers.catalog_router import router as catalog_router
from routers.dashboard_router import router as dashboard_router
from routers.integrations_router import router as integrations_router
from routers.oauth_router import router as oauth_router
from routers.suppliers_router import router as suppliers_router
from routers.tools_router import router as tools_router

__all__ = [
    "accounts_router",
    "auth_router",
    "catalog_router",
    "dashboard_router",
    "integrations_router",
    "oauth_router",
    "suppliers_router",
    "tools_router",
    "register_routers",
]


def register_routers(app: FastAPI) -> None:
    """Registra todos los APIRouters modulares en la aplicación FastAPI."""
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(dashboard_router)
    app.include_router(accounts_router)
    app.include_router(catalog_router)
    app.include_router(suppliers_router)
    app.include_router(integrations_router)
    app.include_router(tools_router)
