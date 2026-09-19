"""
StreamVault v2 - Pure Domain Layer
Zero external dependencies, encapsulating core business entities and rules.
"""
from domain.entities import Client, Account, Payment, CatalogItem
from domain.rules import (
    resolve_http_custom_price,
    classify_client_type,
    preserve_client_type,
    get_client_type_label,
    clean_phone_number,
    parse_date_to_iso,
    is_valid_hwid,
    parse_http_custom_message,
)
from domain.exceptions import (
    DomainException,
    ClientNotFoundException,
    InsufficientStockException,
    PaymentAlreadyProcessedException,
    InvalidHWIDException,
)

__all__ = [
    "Client",
    "Account",
    "Payment",
    "CatalogItem",
    "resolve_http_custom_price",
    "classify_client_type",
    "preserve_client_type",
    "get_client_type_label",
    "clean_phone_number",
    "parse_date_to_iso",
    "is_valid_hwid",
    "parse_http_custom_message",
    "DomainException",
    "ClientNotFoundException",
    "InsufficientStockException",
    "PaymentAlreadyProcessedException",
    "InvalidHWIDException",
]
