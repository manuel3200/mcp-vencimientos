from presentation.api.accounts_api import router as accounts_router
from presentation.api.catalog_api import router as catalog_router
from presentation.api.suppliers_api import router as suppliers_router
from presentation.api.tools_api import router as tools_router
from presentation.api.webhooks_api import router as webhooks_router

__all__ = [
    "accounts_router",
    "catalog_router",
    "suppliers_router",
    "tools_router",
    "webhooks_router",
]
