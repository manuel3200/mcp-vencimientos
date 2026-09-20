from fastapi import FastAPI

from presentation.web.dashboard_routes import router as dashboard_router
from presentation.web.auth_routes import router as auth_router
from presentation.web.oauth_routes import router as oauth_router
from presentation.api.accounts_api import router as accounts_router
from presentation.api.catalog_api import router as catalog_router
from presentation.api.suppliers_api import router as suppliers_router
from presentation.api.tools_api import router as tools_router
from presentation.api.webhooks_api import router as integrations_router
from presentation.api.groups_api import router as groups_router
from presentation.api.referrals_api import router as referrals_router
from presentation.web.ephemeral_routes import router as ephemeral_router

__all__ = [
    "accounts_router",
    "auth_router",
    "catalog_router",
    "dashboard_router",
    "ephemeral_router",
    "groups_router",
    "integrations_router",
    "oauth_router",
    "referrals_router",
    "suppliers_router",
    "tools_router",
    "register_routers",
]

def register_routers(app: FastAPI) -> None:
    """Registra todos los APIRouters modulares en la aplicación FastAPI."""
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(dashboard_router)
    app.include_router(ephemeral_router)
    app.include_router(accounts_router)
    app.include_router(catalog_router)
    app.include_router(suppliers_router)
    app.include_router(integrations_router)
    app.include_router(tools_router)
    app.include_router(groups_router)
    app.include_router(referrals_router)

