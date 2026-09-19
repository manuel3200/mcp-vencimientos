from presentation.web.dashboard_routes import router as dashboard_router
from presentation.web.auth_routes import router as auth_router
from presentation.web.oauth_routes import router as oauth_router

__all__ = ["dashboard_router", "auth_router", "oauth_router"]
