import contextlib
import io
import logging
import os
import sqlite3
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

import capturador_pessoas
from capturador_pessoas import CapturadorPessoasAcadeOne
from app import config
from app.db import Banco
from app.jobs import GerenciadorJobs
from app.main import criar_app
from app.pessoas import ArquivoPessoas, comparar, consolidar
from app.security import Credenciais
from app.tarefas import atualizar_pessoas


def _pessoa(n, tipo):
    doc = f"{n:011d}" if tipo == "Física" else f"{n:014d}"
    return {"Código": f"{n:06d}", "Nome": f"Pessoa {n}", "CPF/CNPJ": doc, "Tipo": f"Pessoa {tipo}"}


def _paginas(inicio, qtd, tipo, por_pagina=50):
    regs = [_pessoa(n, tipo) for n in range(inicio, inicio + qtd)]
    return [regs[i:i + por_pagina] for i in range(0, qtd, por_pagina)]


class CapturadorFalso(CapturadorPessoasAcadeOne):
    """
    Substitui só o que toca o navegador; capturar_todas_paginas() é o código real
    de src/capturador_pessoas.py, incluindo as mensagens de log que a tarefa lê.
    """
    paginas = {}
    total_acade = {}
    login_ok = True
    instancias = []

    def __init__(self, headless, tipo_pessoa, output_dir):
        self.headless, self.tipo_pessoa, self.output_dir = headless, tipo_pessoa, output_dir
        self.timeout, self.max_repeated_pages = 60, 2
        self.logger = logging.getLogger("capturador_pessoas")
        self._pags, self._i, self.fechado, self.cred = list(self.paginas.get(tipo_pessoa, [])), 0, False, None
        CapturadorFalso.instancias.append(self)

    def fazer_login(self, usuario, senha):
        self.cred = (usuario, senha)
        return self.login_ok

    def navegar_menu_cadastro(self):
        return True

    def clicar_tipo_pessoa(self):
        return True

    def configurar_50_registros(self):
        return True

    def extrair_dados_tabela(self, timeout=None):
        return list(self._pags[self._i]) if self._i < len(self._pags) else []

    def obter_info_datatable(self):
        n = len(self._pags)
        fim = sum(len(p) for p in self._pags[:self._i + 1])
        return {"status": "ok", "page": self._i, "pages": n, "start": self._i * 50, "end": fim,
                "recordsDisplay": self.total_acade.get(self.tipo_pessoa), "recordsTotal": self.total_acade.get(self.tipo_pessoa)}

    def clicar_proxima_pagina(self):
        self._i += 1
        return self._i < len(self._pags)

    def fechar(self):
        self.fechado = True


class GravacaoIgualAoCliTest(unittest.TestCase):
    """O arquivo gravado pela web é byte a byte o que capturador_pessoas.main() grava."""

    def test_bytes_identicos_ao_main(self):
        fisica = pd.DataFrame([
            {"Código": "012103", "Nome": "Adauto Nunes de Araujo", "CPF/CNPJ": "857.173.553-00", "Tipo": "Pessoa Física"},
            {"Código": "000007", "Nome": 'Nome, com vírgula e "aspas"', "CPF/CNPJ": "", "Tipo": "Pessoa Física"},
        ])
        juridica = pd.DataFrame([
            {"Código": "004410", "Nome": "Construtora Ação Ltda", "CPF/CNPJ": "01.234.567/0001-89", "Tipo": "Pessoa Jurídica", "Perfil": "x"},
        ])
        with tempfile.TemporaryDirectory() as cli, tempfile.TemporaryDirectory() as web:
            por_tipo = {"Física": fisica, "Jurídica": juridica}
            with unittest.mock.patch.dict(os.environ, {"OUTPUT_DIR": cli, "ACADE_USUARIO": "u", "ACADE_SENHA": "s"}), \
                    unittest.mock.patch("builtins.input", side_effect=["n", "n"]), \
                    unittest.mock.patch.object(capturador_pessoas, "capturar_tipo_pessoa",
                                               side_effect=lambda tipo, *a, **k: por_tipo[tipo].copy()), \
                    contextlib.redirect_stdout(io.StringIO()):
                capturador_pessoas.main()
            arq = ArquivoPessoas(Path(web))
            arq.gravar(consolidar([fisica.copy(), juridica.copy()]))
            esperado = (Path(cli) / "pessoas_cadastradas.csv").read_bytes()
            self.assertEqual(arq.caminho.read_bytes(), esperado)
            self.assertTrue(esperado.startswith("﻿Código,Nome,CPF/CNPJ,Tipo".encode("utf-8")))


class ArquivoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arq = ArquivoPessoas(Path(self.tmp.name) / "aux")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resumo_e_busca(self):
        self.assertIsNone(self.arq.resumo())
        self.arq.gravar(pd.DataFrame([_pessoa(1, "Física"), _pessoa(2, "Jurídica"), _pessoa(3, "Jurídica")]))
        r = self.arq.resumo()
        self.assertEqual((r["total"], r["por_tipo"]), (3, {"Física": 1, "Jurídica": 2}))
        self.assertEqual(self.arq.ler()["Código"].tolist(), ["000001", "000002", "000003"])  # zeros preservados
        self.assertEqual([p["Código"] for p in self.arq.buscar("pessoa 2")[0]], ["000002"])
        self.assertEqual([p["Código"] for p in self.arq.buscar("00000000000003")[0]], ["000003"])

    def test_gravar_guarda_anterior(self):
        self.arq.gravar(pd.DataFrame([_pessoa(1, "Física")]))
        self.arq.gravar(pd.DataFrame([_pessoa(2, "Física")]))
        self.assertIn("000001", self.arq.caminho_anterior.read_text(encoding="utf-8-sig"))
        self.assertFalse(self.arq.caminho.with_suffix(".tmp").exists())

    def test_comparar(self):
        antes = pd.DataFrame([_pessoa(1, "Física"), _pessoa(2, "Física"), _pessoa(3, "Jurídica")])
        depois = pd.DataFrame([_pessoa(1, "Física"), {**_pessoa(2, "Física"), "Nome": "Outro"}, _pessoa(4, "Jurídica")])
        d = comparar(antes, depois)
        self.assertEqual((d["novas"], d["removidas"], d["alteradas"]), (1, 1, 1))
        self.assertEqual(d["ex_alteradas"][0]["nome_antes"], "Pessoa 2")
        self.assertEqual(comparar(None, depois), {"base": False})


class TarefaAtualizarPessoasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Banco(Path(self.tmp.name) / "t.db")
        self.jobs = GerenciadorJobs(self.db)
        self.arq = ArquivoPessoas(Path(self.tmp.name) / "aux")
        self.cred = Credenciais("ana", "segredo", 0)
        CapturadorFalso.instancias = []
        CapturadorFalso.login_ok = True
        CapturadorFalso.paginas = {"Física": _paginas(1, 120, "Física"), "Jurídica": _paginas(1000, 60, "Jurídica")}
        CapturadorFalso.total_acade = {"Física": 120, "Jurídica": 60}

    def tearDown(self):
        self.tmp.cleanup()

    def _rodar(self):
        job_id = self.jobs.iniciar("atualizar_pessoas", "ana",
                                   lambda ctx: atualizar_pessoas(ctx, self.cred, self.arq, capturador_cls=CapturadorFalso))
        for _ in range(250):
            j = self.jobs.obter(job_id)
            if j["status"] not in ("na_fila", "executando"):
                return j
            time.sleep(0.02)
        self.fail("tarefa não terminou")

    def test_captura_completa(self):
        j = self._rodar()
        self.assertEqual(j["status"], "concluido", j["erro"])
        r = j["resultado"]
        self.assertEqual(r["total"], 180)
        self.assertEqual(r["tipos"]["Física"], {"capturados": 120, "acade": 120, "anterior": None, "paginas": 3})
        self.assertEqual(r["tipos"]["Jurídica"]["paginas"], 2)
        self.assertEqual(r["incompletos"], [])
        self.assertEqual(r["diferencas"], {"base": False})
        self.assertEqual((j["progresso"], j["total"]), (180, 180))
        df = self.arq.ler()
        self.assertEqual(df["Tipo"].value_counts().to_dict(), {"Pessoa Física": 120, "Pessoa Jurídica": 60})
        self.assertEqual(df.iloc[0]["Código"], "000001")
        # Mesmas credenciais do usuário logado, navegador oculto, sempre fechado.
        self.assertEqual([(c.tipo_pessoa, c.headless, c.cred, c.fechado) for c in CapturadorFalso.instancias],
                         [("Física", True, ("ana", "segredo"), True), ("Jurídica", True, ("ana", "segredo"), True)])

    def test_pontos_do_grafico_vem_das_paginas(self):
        j = self._rodar()
        pontos = self.jobs.pontos(j["id"])
        fisica = [(p["valor"], p["total"]) for p in pontos if p["serie"] == "Física"]
        self.assertEqual(fisica, [(0, 120), (50, 120), (100, 120), (120, 120), (120, 120)])
        self.assertEqual([p["valor"] for p in pontos if p["serie"] == "Jurídica"], [0, 50, 60, 60])
        self.assertTrue(all(p["t"] >= 0 for p in pontos))

    def test_incompleta_grava_e_avisa(self):
        CapturadorFalso.total_acade["Jurídica"] = 75
        j = self._rodar()
        self.assertEqual(j["status"], "concluido")
        self.assertEqual(j["resultado"]["incompletos"], ["Jurídica"])
        self.assertEqual(len(self.arq.ler()), 180)
        self.assertTrue(any(l["nivel"] == "WARNING" and "60 de 75" in l["msg"] for l in self.jobs.logs(j["id"])))

    def test_tipo_sem_dados_mantem_arquivo_anterior(self):
        self.arq.gravar(pd.DataFrame([_pessoa(1, "Física"), _pessoa(9, "Jurídica")]))
        antes = self.arq.caminho.read_bytes()
        CapturadorFalso.paginas["Jurídica"] = []
        j = self._rodar()
        self.assertEqual(j["status"], "erro")
        self.assertIn("Jurídica", j["erro"])
        self.assertEqual(self.arq.caminho.read_bytes(), antes)
        self.assertTrue(CapturadorFalso.instancias[-1].fechado)

    def test_falha_de_login_nao_grava(self):
        CapturadorFalso.login_ok = False
        j = self._rodar()
        self.assertEqual(j["status"], "erro")
        self.assertIn("login", j["erro"])
        self.assertFalse(self.arq.existe())
        self.assertEqual(len(CapturadorFalso.instancias), 1)

    def test_diferencas_com_captura_anterior(self):
        self.arq.gravar(pd.DataFrame([_pessoa(1, "Física"), _pessoa(5000, "Jurídica")]))
        j = self._rodar()
        r = j["resultado"]
        self.assertEqual(r["tipos"]["Física"]["anterior"], 1)
        self.assertEqual(r["total_anterior"], 2)
        self.assertEqual((r["diferencas"]["novas"], r["diferencas"]["removidas"]), (179, 1))


class MigracaoTest(unittest.TestCase):
    def test_banco_da_fase_2_ganha_tabela_de_pontos(self):
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "v.db"
            from app.db import MIGRACOES
            con = sqlite3.connect(caminho)
            con.executescript(MIGRACOES[0])
            con.execute("PRAGMA user_version = 1")
            con.close()
            Banco(caminho)
            con = sqlite3.connect(caminho)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
            con.execute("SELECT id, job_id, serie, t, valor, total FROM job_pontos")
            con.close()


class RotasPessoasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = config.Settings(secret_key=os.urandom(32), cookie_secure=False, cred_ttl_dias=15,
                                   data_dir=Path(self.tmp.name), acade_base_url="https://x")
        self.chamadas = []

        def tarefa_falsa(ctx, cred, arquivo):
            self.chamadas.append(cred.usuario)
            ctx.ponto("Física", 10, 20)
            arquivo.gravar(pd.DataFrame([_pessoa(1, "Física"), _pessoa(2, "Jurídica")]))
            return {"total": 2}

        self.app = criar_app(settings, validador=lambda u, s: True, tarefa_atualizar_pessoas=tarefa_falsa)
        self.c = TestClient(self.app)
        self.c.post("/login", data={"usuario": "ana", "senha": "x"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_exige_login(self):
        c = TestClient(self.app)
        self.assertEqual(c.get("/pessoas", follow_redirects=False).status_code, 303)
        self.assertEqual(c.get("/pessoas/pessoas_cadastradas.csv", follow_redirects=False).status_code, 303)
        self.assertEqual(c.post("/pessoas/atualizar-acade", follow_redirects=False).status_code, 303)
        self.assertEqual(self.chamadas, [])

    def test_fluxo_atualizar_acompanhar_baixar(self):
        self.assertIn("ainda não capturado", self.c.get("/pessoas").text)
        r = self.c.post("/pessoas/atualizar-acade", follow_redirects=False)
        job_id = r.headers["location"].rsplit("/", 1)[1]
        for _ in range(100):
            estado = self.c.get(f"/tarefas/{job_id}/estado").json()
            if estado["job"]["status"] == "concluido":
                break
            time.sleep(0.02)
        self.assertEqual(estado["job"]["status"], "concluido")
        self.assertEqual([(p["serie"], p["valor"], p["total"]) for p in estado["pontos"]], [("Física", 10, 20)])
        ultimo = estado["pontos"][-1]["id"]
        self.assertEqual(self.c.get(f"/tarefas/{job_id}/estado?depois_ponto={ultimo}").json()["pontos"], [])
        self.assertEqual(self.chamadas, ["ana"])
        pagina = self.c.get(f"/tarefas/{job_id}").text
        self.assertIn("Atualizar pessoas do ACADE", pagina)
        self.assertIn('id="grafico"', pagina)
        csv = self.c.get("/pessoas/pessoas_cadastradas.csv")
        self.assertEqual(csv.content, self.app.state.arquivo_pessoas.caminho.read_bytes())
        self.assertIn("Pessoas cadastradas", self.c.get("/pessoas").text)
        self.assertIn("Pessoa 2", self.c.get("/pessoas", params={"q": "pessoa 2"}).text)
        self.assertIn("Nenhuma pessoa encontrada", self.c.get("/pessoas", params={"q": "zzz"}).text)

    def test_arquivo_no_volume_de_dados(self):
        self.assertEqual(self.app.state.arquivo_pessoas.caminho,
                         Path(self.tmp.name) / "arquivos_auxiliares" / "pessoas_cadastradas.csv")

    def test_painel_mostra_cartao_de_pessoas(self):
        self.assertIn("Ainda não capturado", self.c.get("/").text)


if __name__ == "__main__":
    unittest.main()
