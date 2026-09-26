import io
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app import config
from app.bancos import ErroBancos, RepositorioBancos, chave_agencia, chave_conta
from app.db import Banco
from app.jobs import GerenciadorJobs, JobEmAndamento
from app.main import criar_app

# Formato do bancos.csv curado (dados fictícios).
CSV_CURADO = """codigo,empreendimentos,nome_cedente,banco,agencia,conta,convenio,status,cnpj_cedente
756,LOTEAMENTO ALFA,ALFA EMPREENDIMENTOS,756,3357,1111-1,100001,,11111111000111
756,LOTEAMENTO ALFA II,ALFA EMPREENDIMENTOS,756,3357,1111-1,100001,,11111111000111
756,CONDOMINIO BETA,BETA SPE LTDA,756,4480,22222-2,200002,,22222222000122
756,OUTROS GAMA,GAMA LTDA,756,3357,3333-3,300003,,33333333000133
"""

# Formato da saída do CapturadorBancos (dados fictícios).
ACADE = [
    {"codigo": "756", "empreendimentos": "Loteamento Alfa", "banco": "Sicoob", "nome_cedente": "Alfa Empreend Ltda",
     "agencia": "3357-0", "conta": "1111-1", "status": "Ativo", "cnpj_cedente": "11.111.111/0001-11"},
    {"codigo": "756", "empreendimentos": "Beta", "banco": "Sicoob", "nome_cedente": "BETA SPE LTDA",
     "agencia": "4480", "conta": "22222-2", "status": "Ativo", "cnpj_cedente": "22.222.222/0001-22"},
    {"codigo": "237", "empreendimentos": "Delta", "banco": "Bradesco", "nome_cedente": "DELTA LTDA",
     "agencia": "2214", "conta": "0040913-8", "status": "Ativo", "cnpj_cedente": "44.444.444/0001-44"},
]


def _df(texto=CSV_CURADO):
    return pd.read_csv(io.StringIO(texto), dtype=str, keep_default_na=False)


class BaseRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = RepositorioBancos(Banco(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.tmp.cleanup()


class ChavesTest(unittest.TestCase):
    def test_agencia_ignora_dv_e_zeros(self):
        self.assertEqual(chave_agencia("3357-0"), chave_agencia("3357"))
        self.assertEqual(chave_agencia("0159"), "159")

    def test_conta_mantem_dv(self):
        self.assertEqual(chave_conta("0040913-8"), "409138")
        self.assertNotEqual(chave_conta("1111-1"), chave_conta("1111-2"))


class ImportarExportarTest(BaseRepo):
    def test_ida_e_volta_byte_a_byte(self):
        self.repo.importar_csv(_df(), "u")
        self.assertEqual(self.repo.exportar_df().to_csv(index=False), CSV_CURADO)

    def test_agrupa_centros_por_conta(self):
        r = self.repo.importar_csv(_df(), "u")
        self.assertEqual(r, {"linhas": 4, "contas": 3})
        alfa = [c for c in self.repo.listar_contas() if c["conta"] == "1111-1"][0]
        self.assertEqual(alfa["qtd_centros"], 2)

    def test_recusa_conta_com_convenios_divergentes(self):
        ruim = CSV_CURADO.replace("LOTEAMENTO ALFA II,ALFA EMPREENDIMENTOS,756,3357,1111-1,100001",
                                  "LOTEAMENTO ALFA II,ALFA EMPREENDIMENTOS,756,3357,1111-1,999999")
        with self.assertRaisesRegex(ErroBancos, "convenio"):
            self.repo.importar_csv(_df(ruim), "u")
        self.assertTrue(self.repo.vazio())  # transação desfeita

    def test_recusa_reimportacao(self):
        self.repo.importar_csv(_df(), "u")
        with self.assertRaises(ErroBancos):
            self.repo.importar_csv(_df(), "u")

    def test_recusa_colunas_faltando(self):
        with self.assertRaisesRegex(ErroBancos, "convenio"):
            self.repo.importar_csv(_df().drop(columns=["convenio"]), "u")


class SincronizarTest(BaseRepo):
    def setUp(self):
        super().setUp()
        self.repo.importar_csv(_df(), "u")
        self.antes = self.repo.exportar_df()
        self.r = self.repo.sincronizar_acade(ACADE, "u")

    def test_valores_locais_vencem(self):
        # Nome do cedente e agência diferem no ACADE; nada muda no que o CNAB consome.
        self.assertTrue(self.repo.exportar_df().equals(self.antes))

    def test_contagens(self):
        self.assertEqual((self.r.lidas, self.r.existentes, len(self.r.novas), len(self.r.ausentes)), (3, 2, 1, 1))

    def test_conta_nova_fica_com_convenio_pendente(self):
        nova = self.repo.obter_conta(self.r.novas[0]["id"])
        self.assertIsNone(nova["convenio"])
        self.assertIn("Convênio pendente", nova["pendencias"])
        self.assertIn("Banco sem layout CNAB", nova["pendencias"])
        self.assertEqual(nova["cnpj_cedente"], "44444444000144")
        self.assertEqual(nova["acade_empreendimento"], "Delta")

    def test_ausente_marcada_nao_apagada(self):
        gama = [c for c in self.repo.listar_contas() if c["conta"] == "3333-3"][0]
        self.assertEqual(gama["no_acade"], 0)
        self.assertIn("Não encontrada no ACADE", gama["pendencias"])
        self.assertEqual(gama["convenio"], "300003")

    def test_segunda_sincronizacao_idempotente(self):
        r2 = self.repo.sincronizar_acade(ACADE, "u")
        self.assertEqual((r2.existentes, len(r2.novas)), (3, 0))
        self.assertTrue(self.repo.exportar_df().equals(self.antes))

    def test_convenio_preenchido_depois_sobrevive_a_nova_sincronizacao(self):
        conta_id = self.r.novas[0]["id"]
        self.repo.atualizar_conta(conta_id, {"convenio": "777777"}, "u")
        self.repo.sincronizar_acade(ACADE, "u")
        self.assertEqual(self.repo.obter_conta(conta_id)["convenio"], "777777")


class EdicaoTest(BaseRepo):
    def setUp(self):
        super().setUp()
        self.repo.importar_csv(_df(), "u")
        self.alfa = [c for c in self.repo.listar_contas() if c["conta"] == "1111-1"][0]

    def test_convenio_invalido(self):
        with self.assertRaises(ErroBancos):
            self.repo.atualizar_conta(self.alfa["id"], {"convenio": "12 34"}, "u")

    def test_conflito_de_conta(self):
        with self.assertRaisesRegex(ErroBancos, "outra conta"):
            self.repo.atualizar_conta(self.alfa["id"], {"agencia": "4480", "conta": "22222-2"}, "u")

    def test_edicao_auditada(self):
        self.repo.atualizar_conta(self.alfa["id"], {"convenio": "100009"}, "maria")
        with self.repo.db.conexao() as con:
            a = con.execute("SELECT * FROM auditoria WHERE acao='editar'").fetchone()
        self.assertEqual((a["usuario"], a["entidade_id"]), ("maria", self.alfa["id"]))
        self.assertIn("100001", a["antes"])
        self.assertIn("100009", a["depois"])

    def test_centro_duplicado(self):
        with self.assertRaises(ErroBancos):
            self.repo.salvar_centro("CONDOMINIO BETA", self.alfa["id"], "u")

    def test_novo_centro_entra_na_exportacao(self):
        self.repo.salvar_centro("LOTEAMENTO NOVO", self.alfa["id"], "u")
        exp = self.repo.exportar_df()
        self.assertEqual(exp.iloc[-1]["empreendimentos"], "LOTEAMENTO NOVO")
        self.assertEqual(exp.iloc[-1]["convenio"], "100001")


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = GerenciadorJobs(Banco(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _esperar(self, job_id):
        for _ in range(100):
            j = self.jobs.obter(job_id)
            if j["status"] not in ("na_fila", "executando"):
                return j
            time.sleep(0.02)
        self.fail("job não terminou")

    def test_sucesso_com_progresso_e_log(self):
        def f(ctx):
            ctx.progresso(1, 2, "etapa")
            ctx.log("olá")
            return {"ok": 1}
        j = self._esperar(self.jobs.iniciar("t", "u", f))
        self.assertEqual((j["status"], j["resultado"], j["progresso"], j["total"]), ("concluido", {"ok": 1}, 1, 2))
        self.assertEqual(self.jobs.logs(j["id"])[0]["msg"], "olá")

    def test_erro_registrado(self):
        def f(ctx):
            raise RuntimeError("falhou no ACADE")
        j = self._esperar(self.jobs.iniciar("t", "u", f))
        self.assertEqual((j["status"], j["erro"]), ("erro", "falhou no ACADE"))

    def test_um_por_tipo(self):
        import threading
        liberar = threading.Event()
        id1 = self.jobs.iniciar("t", "u", lambda ctx: liberar.wait(2))
        with self.assertRaises(JobEmAndamento) as cm:
            self.jobs.iniciar("t", "u", lambda ctx: None)
        self.assertEqual(cm.exception.job_id, id1)
        liberar.set()
        self._esperar(id1)


class CapturadorCredenciaisTest(unittest.TestCase):
    """A CLI (sem argumentos) continua lendo o .env; o app passa credenciais explícitas."""

    def test_sem_argumentos_usa_ambiente(self):
        from capturador_bancos import CapturadorBancos
        with unittest.mock.patch.dict(os.environ, {"ACADE_USUARIO": "env_u", "ACADE_SENHA": "env_s"}):
            c = CapturadorBancos(headless=True)
        self.assertEqual((c.usuario, c.senha), ("env_u", "env_s"))

    def test_argumentos_tem_precedencia(self):
        from capturador_bancos import CapturadorBancos
        with unittest.mock.patch.dict(os.environ, {"ACADE_USUARIO": "env_u", "ACADE_SENHA": "env_s"}):
            c = CapturadorBancos(headless=True, usuario="web_u", senha="web_s")
        self.assertEqual((c.usuario, c.senha), ("web_u", "web_s"))


class RotasBancosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = config.Settings(secret_key=os.urandom(32), cookie_secure=False, cred_ttl_dias=15,
                                   data_dir=Path(self.tmp.name), acade_base_url="https://x")
        self.chamadas = []

        def tarefa_falsa(ctx, cred, repo):
            self.chamadas.append(cred.usuario)
            return repo.sincronizar_acade(ACADE, cred.usuario).como_dict()

        self.app = criar_app(settings, validador=lambda u, s: True, tarefa_atualizar_bancos=tarefa_falsa)
        self.c = TestClient(self.app)
        self.c.post("/login", data={"usuario": "ana", "senha": "x"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_exige_login(self):
        r = TestClient(self.app).get("/bancos", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_fluxo_importar_atualizar_exportar(self):
        self.assertIn("Cadastro de bancos vazio", self.c.get("/bancos").text)
        r = self.c.post("/bancos/importar", files={"arquivo": ("bancos.csv", CSV_CURADO.encode("utf-8-sig"))})
        self.assertIn("Cadastro de bancos importado", r.text)
        r = self.c.post("/bancos/atualizar-acade", follow_redirects=False)
        job_id = r.headers["location"].rsplit("/", 1)[1]
        for _ in range(100):
            estado = self.c.get(f"/tarefas/{job_id}/estado").json()["job"]
            if estado["status"] == "concluido":
                break
            time.sleep(0.02)
        self.assertEqual(estado["resultado"]["existentes"], 2)
        self.assertEqual(self.chamadas, ["ana"])
        self.assertIn("Convênio pendente", self.c.get("/bancos").text)
        self.assertEqual(self.c.get("/bancos/exportar.csv").text, CSV_CURADO)

    def test_salvar_convenio(self):
        self.c.post("/bancos/importar", files={"arquivo": ("b.csv", CSV_CURADO.encode())})
        conta_id = self.app.state.repo_bancos.listar_contas()[0]["id"]
        r = self.c.post(f"/bancos/{conta_id}/convenio", data={"convenio": "555555"})
        self.assertIn("Convênio salvo", r.text)
        self.assertEqual(self.app.state.repo_bancos.obter_conta(conta_id)["convenio"], "555555")

    def test_importacao_invalida_mostra_erro(self):
        r = self.c.post("/bancos/importar", files={"arquivo": ("b.csv", b"a,b\n1,2\n")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Colunas ausentes", r.text)


if __name__ == "__main__":
    unittest.main()
