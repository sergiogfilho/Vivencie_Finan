"""
Cache cifrado das credenciais ACADE no navegador do usuário.

As credenciais nunca são persistidas no servidor: vão cifradas (AES-256-GCM)
num cookie HttpOnly. Só o servidor tem a chave (APP_SECRET_KEY). O instante de
emissão viaja dentro do conteúdo cifrado e autenticado, então a validade
máxima é imposta pelo servidor mesmo que o navegador ignore o Max-Age.
"""
import base64
import json
import os
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

COOKIE_NOME = "vf_cred"
_AAD = b"vivencie-finan/cred/v1"
_NONCE = 12


@dataclass(frozen=True)
class Credenciais:
    usuario: str
    senha: str
    emitido_em: float

    def expira_em(self, ttl_segundos: int) -> float:
        return self.emitido_em + ttl_segundos


class CofreCredenciais:
    def __init__(self, chave: bytes, ttl_segundos: int):
        self._aes = AESGCM(chave)
        self.ttl_segundos = ttl_segundos

    def cifrar(self, usuario: str, senha: str, agora: float | None = None) -> str:
        agora = time.time() if agora is None else agora
        conteudo = json.dumps({"u": usuario, "p": senha, "iat": int(agora)}).encode()
        nonce = os.urandom(_NONCE)
        return base64.urlsafe_b64encode(nonce + self._aes.encrypt(nonce, conteudo, _AAD)).decode()

    def decifrar(self, token: str | None, agora: float | None = None) -> Credenciais | None:
        """Retorna as credenciais, ou None se o token for inválido, adulterado ou expirado."""
        if not token:
            return None
        agora = time.time() if agora is None else agora
        try:
            bruto = base64.urlsafe_b64decode(token.encode())
            conteudo = json.loads(self._aes.decrypt(bruto[:_NONCE], bruto[_NONCE:], _AAD))
            cred = Credenciais(str(conteudo["u"]), str(conteudo["p"]), float(conteudo["iat"]))
        except (InvalidTag, ValueError, KeyError, TypeError):
            return None
        if cred.emitido_em > agora + 60 or agora >= cred.expira_em(self.ttl_segundos):
            return None
        return cred
