import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def database_path() -> str:
    return os.getenv("FRONTDOOR_DB", "data/frontdoor.db")


def service_secret() -> str:
    configured = os.getenv("SERVICE_SECRET")
    if configured:
        return configured
    path = Path(database_path()).parent / ".service-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as file:
            file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii")


def mode() -> str:
    value = os.getenv("DEMO_MODE", "mock")
    if value not in {"mock", "live"}:
        raise RuntimeError("DEMO_MODE must be mock or live")
    return value


def auth_mode() -> str:
    value = os.getenv("AUTH_MODE", "mock")
    if value not in {"mock", "entra"}:
        raise RuntimeError("AUTH_MODE must be mock or entra")
    return value


def knowledge_provider() -> str:
    value = os.getenv("KNOWLEDGE_PROVIDER", "mock")
    if value not in {"mock", "azure_search", "foundry_iq"}:
        raise RuntimeError("KNOWLEDGE_PROVIDER must be mock, azure_search or foundry_iq")
    return value


def supply_provider() -> str:
    value = os.getenv("SUPPLY_PROVIDER", "mock")
    if value not in {"mock", "foundry_a2a"}:
        raise RuntimeError("SUPPLY_PROVIDER must be mock or foundry_a2a")
    return value


def ess_provider() -> str:
    value = os.getenv("ESS_PROVIDER", "mock")
    if value not in {"mock", "foundry"}:
        raise RuntimeError("ESS_PROVIDER must be mock or foundry")
    return value


def gateway_provider() -> str:
    value = os.getenv("GATEWAY_PROVIDER", "mock")
    if value not in {"mock", "apim"}:
        raise RuntimeError("GATEWAY_PROVIDER must be mock or apim")
    return value


def catalog_provider() -> str:
    value = os.getenv("CATALOG_PROVIDER", "local")
    if value not in {"local", "apic"}:
        raise RuntimeError("CATALOG_PROVIDER must be local or apic")
    return value
