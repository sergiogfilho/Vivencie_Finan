import base64
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.main import criar_app
from app.security import COOKIE_NOME, CofreCredenciais

CHAVE = os.urandom(32)
DIA = 86400


def _settings(**kw):
    base = dict(secret_key=CHAVE, cookie_secure=False, cred_ttl_dias=15,
                data_dir=Path(tempfile.mkdtemp()), acade_base_url="https://exemplo")
    base.update(kw)
    return config.Settings(**base)


class CofreTest(unittest.TestCase):
    def setUp(self):
        self.cofre = CofreCredenciais(CHAVE, 15 * DIA)

    def test_ida_e_volta(self):
        cred = self.cofre.decifrar(self.cofre.cifrar("ana", "s3nh@", agora=1000), agora=1000 + DIA)
        self.assertEqual((cred.usuario, cred.senha), ("ana", "s3nh@"))

    def test_token_nao_contem_senha_em_claro(self):
        token = self.cofre.cifrar("ana", "senha-secreta")
        self.assertNotIn(b"senha-secreta", base64.urlsafe_b64decode(token))

    def test_expira_em_15_dias(self):
        token = self.cofre.cifrar("ana", "x", agora=0)
        self.assertIsNotNone(self.cofre.decifrar(token, agora=15 * DIA - 1))
        self.assertIsNone(self.cofre.decifrar(token, agora=15 * DIA))

    def test_emitido_no_futuro_rejeitado(self):
        token = self.cofre.cifrar("ana", "x", agora=10_000)
        self.assertIsNone(self.cofre.decifrar(token, agora=0))

    def test_adulterado_rejeitado(self):
        bruto = bytearray(base64.urlsafe_b64decode(self.cofre.cifrar("ana", "x")))
        bruto[-1] ^= 1
        self.assertIsNone(self.cofre.decifrar(base64.urlsafe_b64encode(bytes(bruto)).decode()))

    def test_outra_chave_rejeitada(self):
        outro = CofreCredenciais(os.urandom(32), 15 * DIA)
        self.assertIsNone(outro.decifrar(self.cofre.cifrar("ana", "x")))

    def test_lixo_rejeitado(self):
        for token in ("", "abc", "!!!", None):
            self.assertIsNone(self.cofre.decifrar(token))


class ConfigTest(unittest.TestCase):
    def test_ttl_acima_de_15_recusado(self):
        chave = base64.urlsafe_b64encode(CHAVE).decode()
        with unittest.mock.patch.dict(os.environ, {"APP_SECRET_KEY": chave, "CRED_TTL_DIAS": "16"}):
            with self.assertRaises(config.ConfigError):
                config.carregar()

    def test_chave_obrigatoria(self):
        with unittest.mock.patch.dict(os.environ, {"APP_SECRET_KEY": ""}):
            with self.assertRaises(config.ConfigError):
                config.carregar()


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.chamadas = []

        def validador(u, s):
            self.chamadas.append((u, s))
            return s == "certa"

        self.client = TestClient(criar_app(_settings(), validador=validador))

    def test_sem_cookie_redireciona_para_login(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))

    def test_login_ok_emite_cookie_protegido(self):
        r = self.client.post("/login", data={"usuario": "ana", "senha": "certa"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        set_cookie = r.headers["set-cookie"].lower()
        for atributo in ("httponly", "samesite=lax", f"max-age={15 * DIA}"):
            self.assertIn(atributo, set_cookie)
        self.assertNotIn("certa", r.headers["set-cookie"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_login_errado_nao_emite_cookie(self):
        r = self.client.post("/login", data={"usuario": "ana", "senha": "errada"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn(COOKIE_NOME, r.headers.get("set-cookie", ""))

    def test_bloqueio_apos_5_falhas_nao_consulta_acade(self):
        for _ in range(5):
            self.client.post("/login", data={"usuario": "ana", "senha": "errada"})
        r = self.client.post("/login", data={"usuario": "ana", "senha": "certa"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.chamadas), 5)

    def test_logout_remove_cookie(self):
        self.client.post("/login", data={"usuario": "ana", "senha": "certa"})
        self.client.post("/logout")
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_cookie_secure_por_padrao(self):
        c = TestClient(criar_app(_settings(cookie_secure=True), validador=lambda u, s: True))
        r = c.post("/login", data={"usuario": "a", "senha": "b"}, follow_redirects=False)
        self.assertIn("secure", r.headers["set-cookie"].lower())


if __name__ == "__main__":
    unittest.main()
