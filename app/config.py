"""Configuração da aplicação web, lida exclusivamente de variáveis de ambiente."""
import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path

# Limite máximo do cache de credenciais exigido pelo negócio.
CRED_TTL_MAX_DIAS = 15
# Cada navegador Chromium consome memória no contêiner.
BAIXA_WORKERS_MAX = 6


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    secret_key: bytes
    cookie_secure: bool
    cred_ttl_dias: int
    data_dir: Path
    acade_base_url: str
    # Captura Pessoa Física e Jurídica ao mesmo tempo (dois navegadores).
    pessoas_paralelo: bool = True
    # Navegadores em paralelo na baixa pelo retorno (run_baixar_contas.ps1 usa --workers 3).
    baixa_workers: int = 3


def _bool(valor: str | None, padrao: bool) -> bool:
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


def _chave(valor: str | None) -> bytes:
    if not valor:
        raise ConfigError(
            "APP_SECRET_KEY ausente. Gere com: python -c \"import secrets,base64;"
            "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        chave = base64.urlsafe_b64decode(valor.strip() + "=" * (-len(valor.strip()) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("APP_SECRET_KEY não é base64 válido") from exc
    if len(chave) != 32:
        raise ConfigError(f"APP_SECRET_KEY deve ter 32 bytes (tem {len(chave)})")
    return chave


def carregar() -> Settings:
    ttl = int(os.getenv("CRED_TTL_DIAS", str(CRED_TTL_MAX_DIAS)))
    if not 1 <= ttl <= CRED_TTL_MAX_DIAS:
        raise ConfigError(f"CRED_TTL_DIAS deve estar entre 1 e {CRED_TTL_MAX_DIAS}")
    workers = int(os.getenv("BAIXA_WORKERS", "3"))
    if not 1 <= workers <= BAIXA_WORKERS_MAX:
        raise ConfigError(f"BAIXA_WORKERS deve estar entre 1 e {BAIXA_WORKERS_MAX}")
    return Settings(
        secret_key=_chave(os.getenv("APP_SECRET_KEY")),
        cookie_secure=_bool(os.getenv("COOKIE_SECURE"), True),
        cred_ttl_dias=ttl,
        data_dir=Path(os.getenv("DATA_DIR", "/data")),
        acade_base_url=os.getenv("ACADE_BASE_URL", "https://martins.acadeone.com.br").rstrip("/"),
        pessoas_paralelo=_bool(os.getenv("PESSOAS_PARALELO"), True),
        baixa_workers=workers,
    )
