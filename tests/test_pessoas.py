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
    login_ok = {"Física": True, "Jurídica": True}
    atraso_pagina = 0.0
    barreira = None          # threading.Barrier: prova que os dois tipos rodam ao mesmo tempo
    eventos = []
    instancias = []

    def __init__(self, headless, tipo_pessoa, output_dir):
        self.headless, self.tipo_pessoa, self.output_dir = headless, tipo_pessoa, output_dir
        self.timeout, self.max_repeated_pages = 60, 2
        self.logger = logging.getLogger("capturador_pessoas")
        self._pags, self._i, self.fechado, self.cred = list(self.paginas.get(tipo_pessoa, [])), 0, False, None
        CapturadorFalso.instancias.append(self)

    def fazer_login(self, usuario, senha):
        self.cred = (usuario, senha)
        CapturadorFalso.eventos.append(("login", self.tipo_pessoa))
        if self.barreira:
            self.barreira.wait()
        return self.login_ok[self.tipo_pessoa]

    def navegar_menu_cadastro(self):
        return True

    def clicar_tipo_pessoa(self):
        return True

    def configurar_50_registros(self):
        return True

    def extrair_dados_tabela(self, timeout=None):
        time.sleep(self.atraso_pagina)
        if self.fechado:        # navegador fechado por outra thread
            return []
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
        CapturadorFalso.eventos.append(("fechar", self.tipo_pessoa))


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
        self.assertEqual([p["codigo"] for p in self.arq.listar("pessoa 2")["itens"]], ["000002"])
        self.assertEqual([p["codigo"] for p in self.arq.listar("00000000000003")["itens"]], ["000003"])

    def test_lista_paginada_mistura_naturezas_por_nome(self):
        pessoas = [{**_pessoa(n, "Física" if n % 2 else "Jurídica"), "Nome": f"Nome {n:03d}"} for n in range(1, 121)]
        pessoas.append({**_pessoa(500, "Física"), "Nome": "Ágata Sá"})
        self.arq.gravar(pd.DataFrame(pessoas))
        p1 = self.arq.listar(pagina=1, por_pagina=50)
        self.assertEqual((p1["total"], p1["paginas"], p1["inicio"], p1["fim"]), (121, 3, 1, 50))
        self.assertEqual(p1["itens"][0], {"codigo": "000500", "nome": "Ágata Sá", "documento": "00000000500",
                                          "natureza": "Física"})           # acento não altera a ordem
        self.assertEqual([i["natureza"] for i in p1["itens"][1:3]], ["Física", "Jurídica"])
        p3 = self.arq.listar(pagina=3, por_pagina=50)
        self.assertEqual((len(p3["itens"]), p3["inicio"], p3["fim"]), (21, 101, 121))
        self.assertEqual(self.arq.listar(pagina=99, por_pagina=50)["pagina"], 3)   # fora do limite
        self.assertEqual(self.arq.listar(pagina=0, por_pagina=50)["pagina"], 1)
        todos = [i["codigo"] for n in (1, 2, 3) for i in self.arq.listar(pagina=n, por_pagina=50)["itens"]]
        self.assertEqual(sorted(todos), sorted(p["Código"] for p in pessoas))
        self.assertEqual(self.arq.listar("agata")["total"], 1)

    def test_lista_sem_arquivo(self):
        self.assertEqual(self.arq.listar()["total"], 0)

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
        CapturadorFalso.eventos = []
        CapturadorFalso.login_ok = {"Física": True, "Jurídica": True}
        CapturadorFalso.atraso_pagina = 0.0
        CapturadorFalso.barreira = None
        CapturadorFalso.paginas = {"Física": _paginas(1, 120, "Física"), "Jurídica": _paginas(1000, 60, "Jurídica")}
        CapturadorFalso.total_acade = {"Física": 120, "Jurídica": 60}

    def tearDown(self):
        self.tmp.cleanup()

    def _rodar(self, **kw):
        job_id = self.jobs.iniciar("atualizar_pessoas", "ana",
                                   lambda ctx: atualizar_pessoas(ctx, self.cred, self.arq, capturador_cls=CapturadorFalso, **kw))
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
        self.assertEqual(sorted((c.tipo_pessoa, c.headless, c.cred, c.fechado) for c in CapturadorFalso.instancias),
                         [("Física", True, ("ana", "segredo"), True), ("Jurídica", True, ("ana", "segredo"), True)])
        self.assertTrue(r["paralelo"])

    def test_padrao_captura_os_dois_tipos_ao_mesmo_tempo(self):
        import threading
        # Cada login espera o outro; em sequência a barreira estouraria o tempo.
        CapturadorFalso.barreira = threading.Barrier(2, timeout=3)
        j = self._rodar()
        self.assertEqual(j["status"], "concluido", j["erro"])
        self.assertEqual(len(self.arq.ler()), 180)

    def test_modo_sequencial_opcional(self):
        j = self._rodar(paralelo=False)
        self.assertEqual(j["status"], "concluido", j["erro"])
        self.assertEqual(CapturadorFalso.eventos,
                         [("login", "Física"), ("fechar", "Física"), ("login", "Jurídica"), ("fechar", "Jurídica")])
        self.assertFalse(j["resultado"]["paralelo"])

    def test_mesmo_arquivo_em_paralelo_e_em_sequencia(self):
        self._rodar()
        paralelo = self.arq.caminho.read_bytes()
        self._rodar(paralelo=False)
        self.assertEqual(self.arq.caminho.read_bytes(), paralelo)

    def test_falha_em_um_tipo_encerra_o_outro(self):
        CapturadorFalso.paginas["Física"] = _paginas(1, 2000, "Física")   # 40 páginas
        CapturadorFalso.total_acade["Física"] = 2000
        CapturadorFalso.atraso_pagina = 0.05                              # ~2 s se não for interrompida
        CapturadorFalso.login_ok["Jurídica"] = False
        inicio = time.time()
        j = self._rodar()
        self.assertLess(time.time() - inicio, 1.8)
        self.assertEqual(j["status"], "erro")
        self.assertIn("Jurídica: falha no login", j["erro"])
        self.assertIn("arquivo anterior foi mantido", j["erro"])
        self.assertFalse(self.arq.existe())
        self.assertTrue(all(c.fechado for c in CapturadorFalso.instancias))

    def test_log_identifica_o_tipo(self):
        j = self._rodar()
        msgs = [l["msg"] for l in self.jobs.logs(j["id"])]
        self.assertIn("[Física] Página 1: 50 registros capturados (Total: 50)", msgs)
        self.assertIn("[Jurídica] Página 2: 10 registros capturados (Total: 60)", msgs)

    def test_pontos_do_grafico_vem_das_paginas(self):
        j = self._rodar()
        pontos = self.jobs.pontos(j["id"])
        fisica = [(p["valor"], p["total"], p["fim"]) for p in pontos if p["serie"] == "Física"]
        self.assertEqual(fisica, [(0, 120, 0), (50, 120, 0), (100, 120, 0), (120, 120, 0), (120, 120, 1)])
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
        self.assertTrue(all(c.fechado for c in CapturadorFalso.instancias))

    def test_falha_de_login_nao_grava(self):
        CapturadorFalso.login_ok = {"Física": False, "Jurídica": False}
        j = self._rodar()
        self.assertEqual(j["status"], "erro")
        self.assertIn("login", j["erro"])
        self.assertFalse(self.arq.existe())
        self.assertTrue(all(c.fechado for c in CapturadorFalso.instancias))

    def test_sequencial_para_no_primeiro_erro(self):
        CapturadorFalso.login_ok["Física"] = False
        j = self._rodar(paralelo=False)
        self.assertEqual(j["status"], "erro")
        self.assertEqual([c.tipo_pessoa for c in CapturadorFalso.instancias], ["Física"])

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
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 3)
            con.execute("SELECT id, job_id, serie, t, valor, total, fim FROM job_pontos")
            con.close()


class RotasPessoasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = config.Settings(secret_key=os.urandom(32), cookie_secure=False, cred_ttl_dias=15,
                                   data_dir=Path(self.tmp.name), acade_base_url="https://x")
        self.chamadas = []

        def tarefa_falsa(ctx, cred, arquivo, paralelo):
            self.chamadas.append((cred.usuario, paralelo))
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
        self.assertEqual(self.chamadas, [("ana", True)])
        pagina = self.c.get(f"/tarefas/{job_id}").text
        self.assertIn("Atualizar pessoas do ACADE", pagina)
        self.assertIn('id="grafico"', pagina)
        csv = self.c.get("/pessoas/pessoas_cadastradas.csv")
        self.assertEqual(csv.content, self.app.state.arquivo_pessoas.caminho.read_bytes())
        self.assertIn("Pessoas cadastradas", self.c.get("/pessoas").text)
        html = self.c.get("/pessoas").text
        self.assertIn("Pessoa 1", html)
        self.assertIn("Jurídica</span>", html)
        self.assertIn("1–2 de 2", html)
        self.assertIn("Pessoa 2", self.c.get("/pessoas", params={"q": "pessoa 2"}).text)
        self.assertIn("Nenhuma pessoa encontrada", self.c.get("/pessoas", params={"q": "zzz"}).text)

    def test_paginacao_preserva_busca(self):
        from app.rotas_pessoas import _janela
        self.assertEqual(_janela(1, 1), [1])
        self.assertEqual(_janela(6, 48), [1, None, 4, 5, 6, 7, 8, None, 48])
        self.assertEqual(_janela(2, 5), [1, 2, 3, 4, 5])
        self.app.state.arquivo_pessoas.gravar(pd.DataFrame([_pessoa(n, "Física") for n in range(1, 131)]))
        html = self.c.get("/pessoas", params={"q": "pessoa", "p": 2}).text
        self.assertIn("51–100 de 130", html)
        self.assertIn('href="/pessoas?q=pessoa&amp;p=3"', html)
        self.assertIn('aria-current="page">2<', html)
        self.assertIn("101–130 de 130", self.c.get("/pessoas", params={"p": 3}).text)

    def test_arquivo_no_volume_de_dados(self):
        self.assertEqual(self.app.state.arquivo_pessoas.caminho,
                         Path(self.tmp.name) / "arquivos_auxiliares" / "pessoas_cadastradas.csv")

    def test_painel_mostra_cartao_de_pessoas(self):
        self.assertIn("Ainda não capturado", self.c.get("/").text)


class ConfigParaleloTest(unittest.TestCase):
    def _carregar(self, **env):
        with unittest.mock.patch.dict(os.environ, {"APP_SECRET_KEY": "A" * 43, **env}):
            if "PESSOAS_PARALELO" not in env:
                os.environ.pop("PESSOAS_PARALELO", None)
            return config.carregar()

    def test_padrao_paralelo(self):
        self.assertTrue(self._carregar().pessoas_paralelo)

    def test_desligar_por_variavel(self):
        self.assertFalse(self._carregar(PESSOAS_PARALELO="false").pessoas_paralelo)


if __name__ == "__main__":
    unittest.main()
