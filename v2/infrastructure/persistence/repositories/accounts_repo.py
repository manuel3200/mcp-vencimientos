"""Fachada de compatibilidad hacia la implementación canónica en db.repositories.accounts_repo (Q06)."""
from db.repositories.accounts_repo import *  # noqa: F401,F403
from db.repositories.accounts_repo import _decrypt_account_dict  # noqa: F401
