import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

import baixar_contas_pagas
import worker_thread
from automatizador_final import AutomatizadorAcadeOneFINAL, ParserCNAB240Retorno
from app import config, tarefas
from app.db import Banco
from app.main import criar_app
from app.retornos import (OCORRENCIAS, ArquivoRepetido, ErroRetorno, RepositorioRetornos, ler_arquivo,
                          validar_conteudo)
from app.security import Credenciais
from app.tarefas import (DETALHE_DESCONHECIDO, DETALHE_NAO_INICIADO, DETALHE_SIMULACAO, DETALHE_SIMULACAO_SEM_MODAL,
                         _classe_worker, baixar_retorno)


# ------------------------------------------------------------------ .RET sintético
# Posições (1-based) lidas por ParserCNAB240Retorno (automatizador_final.py:75-189). Dados fictícios.
def L(*campos) -> str:
    b = [" "] * 240
    for ini, txt in campos:
        b[ini - 1:ini - 1 + len(txt)] = list(txt)
    return "".join(b)


def header_arquivo(codigo="2"):
    return L((1, "756"), (4, "0000"), (8, "0"), (73, "SICOOB".ljust(30)), (143, codigo), (144, "28092026"))


def header_lote(lote, agencia, agencia_dv, conta, conta_dv):
    return L((1, "756"), (4, lote), (8, "1"), (53, agencia), (58, agencia_dv), (59, conta), (71, conta_dv))


def seg_a(lote, seq, nome, seu, venc, centavos, real, ocorr):
    return L((1, "756"), (4, lote), (8, "3"), (9, f"{seq:05d}"), (14, "A"), (16, "00"), (44, nome.ljust(30)),
             (74, seu.ljust(20)), (94, venc), (120, f"{centavos:015d}"), (155, real), (163, f"{centavos:015d}"),
             (231, ocorr.ljust(10)))


def seg_b(lote, seq):
    return L((1, "756"), (4, lote), (8, "3"), (9, f"{seq:05d}"), (14, "B"))


def seg_j(lote, seq, nome, seu, venc, centavos, real, ocorr):
    return L((1, "756"), (4, lote), (8, "3"), (9, f"{seq:05d}"), (14, "J"), (16, "00"), (18, "7569" + "1" * 40),
             (62, nome.ljust(30)), (92, venc), (100, f"{centavos:015d}"), (145, real), (153, f"{centavos:015d}"),
             (183, seu.ljust(20)), (231, ocorr.ljust(10)))


def seg_j52(lote, seq):
    return L((1, "756"), (4, lote), (8, "3"), (9, f"{seq:05d}"), (14, "J"), (18, "52"))


def seg_o(lote, seq, nome, seu, venc, pgto, centavos, ocorr):
    return L((1, "756"), (4, lote), (8, "3"), (9, f"{seq:05d}"), (14, "O"), (62, nome.ljust(30)), (92, venc),
             (100, pgto), (108, f"{centavos:015d}"), (123, seu.ljust(20)), (231, ocorr.ljust(10)))


def retorno(codigo="2", extra=0) -> bytes:
    linhas = [
        header_arquivo(codigo),
        header_lote("0001", "03357", "0", "000000055263", "1"),
        seg_a("0001", 1, "MARIA DE TESTE", "1001", "26092026", 123456, "26092026", "00"),
        seg_b("0001", 2),
        seg_a("0001", 3, "JOSE DE TESTE", "1002", "26092026", 5000, "26092026", "BDPD"),
        seg_j("0001", 4, "FORNECEDOR TESTE LTDA", "1003", "26092026", 2000, "27092026", "00"),
        seg_j52("0001", 5),
        L((1, "756"), (4, "0001"), (8, "5")),
        header_lote("0002", "04480", "0", "000000022222", "2"),
        seg_o("0002", 1, "CONCESSIONARIA TESTE", "1004", "26092026", "26092026", 5000, "00"),
        seg_a("0002", 2, "MARIA DE TESTE", "1005", "26092026", 1100, "26092026", "BFZZ"),
        seg_a("0002", 3, "JOSE DE TESTE", "1006", "26102026", 30000, "26092026", "00"),
    ] + [seg_a("0002", 10 + i, f"EXTRA {i}", f"20{i:02d}", "26092026", 100 + i, "26092026", "00") for i in range(extra)] + [
        L((1, "756"), (4, "0002"), (8, "5")),
        L((1, "756"), (4, "9999"), (8, "9")),
    ]
    return ("\r\n".join(linhas) + "\r\n").encode("latin-1")


CONFIRMADOS = ["1001", "1003", "1004", "1006"]


# --------------------------------------------------------------- ACADE falso
class AcadeFalso(AutomatizadorAcadeOneFINAL):
    """
    Herda baixar_titulo real (automatizador_final.py:2124); só troca o que toca o navegador.
    `roteiro` por documento: 'ok', 'nao_encontrado', 'sem_parcela'.
    """
    roteiro = {}
    login_ok = True
    chamadas = []
    trava = threading.Lock()

    class _Driver:
        current_url = "https://acade.invalid/acade/finan/contaPagar/"

        def get(self, url):
            pass

    def __init__(self, headless=False, timeout=60):
        self.headless, self.timeout = headless, timeout
        self.logger = logging.getLogger("automatizador_final")
        self.driver = self._Driver()
        self.base_url = "https://acade.invalid"
        self._reg("init", headless)

    def _reg(self, *c):
        with self.trava:
            AcadeFalso.chamadas.append(c)

    def fazer_login(self, usuario, senha):
        self._reg("login", usuario, senha)
        return self.login_ok

    def navegar_para_contas_pagar(self):
        self._reg("contas_pagar")
        return True

    def buscar_titulo_por_documento(self, documento, data_vencimento):
        self._reg("buscar", documento, data_vencimento)
        return self.roteiro.get(documento, "ok") != "nao_encontrado"

    def clicar_botao_pagar(self, documento):
        self._reg("pagar", documento)
        return True

    def selecionar_parcela_por_vencimento(self, data_vencimento):
        self._reg("parcela", data_vencimento)
        return self.roteiro.get(self._doc_atual(), "ok") != "sem_parcela"

    def _doc_atual(self):
        return next(c[1] for c in reversed(self.chamadas) if c[0] == "buscar")

    def preencher_modal_pagamento(self, agencia, conta, data_pagamento):
        self._reg("salvar", agencia, conta, data_pagamento)
        return True

    def fechar(self):
        self._reg("fechar")


def _sem_esperas():
    return [unittest.mock.patch("automatizador_final.time.sleep"), unittest.mock.patch("worker_thread.time.sleep")]


def _normalizar(texto: str) -> str:
    return re.sub(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", "<AGORA>", texto)


class ContextoFalso:
    def __init__(self, job_id="abcdef012345"):
        self.job_id, self.logs, self.progressos = job_id, [], []

    def progresso(self, atual=None, total=None, etapa=None):
        self.progressos.append((atual, total, etapa))

    def log(self, msg, nivel="INFO"):
        self.logs.append((nivel, msg))

    def ponto(self, *a, **k):
        pass

    def capturar_logs(self, *a, **k):
        import contextlib
        return contextlib.nullcontext()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.repo = RepositorioRetornos(Banco(self.dir / "t.db"), self.dir / "retornos")
        self.cred = Credenciais("usuario.teste", "senha-teste", time.time())
        AcadeFalso.roteiro, AcadeFalso.login_ok, AcadeFalso.chamadas = {}, True, []
        for p in _sem_esperas():
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()


# ------------------------------------------------------------------- leitura
class LeituraTest(Base):
    def test_ler_arquivo_e_o_parser_do_cli_com_chave(self):
        caminho = self.dir / "01_Retorno.RET"
        caminho.write_bytes(retorno())
        cli_conf, cli_nao = ParserCNAB240Retorno(str(caminho)).processar_arquivo()
        web = ler_arquivo(caminho)
        tirar = lambda ps: [{k: v for k, v in p.items() if k not in ("arquivo", "chave")} for p in ps]
        self.assertEqual(tirar(web["confirmados"]), cli_conf)
        self.assertEqual(tirar(web["nao_confirmados"]), cli_nao)
        self.assertEqual([p["seu_numero"] for p in web["confirmados"]], CONFIRMADOS)
        self.assertEqual([p["segmento"] for p in web["confirmados"]], ["A", "J", "O", "A"])
        self.assertEqual([p["seu_numero"] for p in web["nao_confirmados"]], ["1002", "1005"])
        self.assertEqual(len({p["chave"] for p in web["confirmados"] + web["nao_confirmados"]}), 6)
        self.assertEqual([(l["agencia"], l["conta"], l["conta_dv"]) for l in web["lotes"]],
                         [("03357", "000000055263", "1"), ("04480", "000000022222", "2")])
        self.assertEqual(web["codigo_remessa_retorno"], "2")

    def test_validacao_recusa_remessa_e_o_que_nao_e_cnab(self):
        with self.assertRaisesRegex(ErroRetorno, "REMESSA"):
            validar_conteudo("r.txt", retorno(codigo="1"))
        with self.assertRaisesRegex(ErroRetorno, "não parece"):
            validar_conteudo("x.RET", b"abc\r\n")
        with self.assertRaisesRegex(ErroRetorno, "vazio"):
            validar_conteudo("x.RET", b"")
        validar_conteudo("ok.RET", retorno())

    def test_traducao_de_ocorrencias_igual_a_do_cli(self):
        pags = [{"seu_numero": c, "nome_favorecido": "X", "ocorrencias": [c], "valor_pagamento": "1"}
                for c in list(OCORRENCIAS) + ["ZZ"]]
        arq = baixar_contas_pagas.salvar_nao_processados(pags, str(self.dir))
        linhas = {l.split()[0]: l for l in Path(arq).read_text(encoding="utf-8").splitlines() if l[:2] in OCORRENCIAS or l.startswith("ZZ")}
        for codigo, texto in OCORRENCIAS.items():
            self.assertIn(texto, linhas[codigo])
        self.assertIn(" ZZ ", linhas["ZZ"])


class RepositorioTest(Base):
    def test_registra_e_recusa_conteudo_repetido(self):
        rid = self.repo.registrar("u", [("Retorno 1.RET", retorno())])
        previa = self.repo.ler(rid)
        self.assertEqual(len(previa["confirmados"]), 4)
        self.assertAlmostEqual(previa["valor_confirmados"], 1234.56 + 20 + 50 + 300)
        with self.assertRaises(ArquivoRepetido) as e:
            self.repo.registrar("u", [("outro nome.RET", retorno())])
        self.assertEqual(e.exception.retorno_id, rid)
        with self.assertRaisesRegex(ErroRetorno, "mesmo conteúdo"):
            self.repo.registrar("u", [("a.RET", retorno(extra=1)), ("b.RET", retorno(extra=1))])
        self.assertEqual([r["id"] for r in self.repo.listar()], [rid])

    def test_envio_recusado_nao_deixa_rastro(self):
        with self.assertRaises(ErroRetorno):
            self.repo.registrar("u", [("r.RET", retorno(codigo="1"))])
        sem_pagamentos = ("\r\n".join([header_arquivo(), L((1, "756"), (4, "9999"), (8, "9"))]) + "\r\n").encode()
        with self.assertRaisesRegex(ErroRetorno, "Nenhum pagamento"):
            self.repo.registrar("u", [("vazio.RET", sem_pagamentos)])
        self.assertEqual(self.repo.listar(), [])
        self.assertEqual(list((self.dir / "retornos").iterdir()) if (self.dir / "retornos").exists() else [], [])


class MigracaoTest(unittest.TestCase):
    def test_banco_da_fase_4_ganha_tabelas_de_retorno_sem_perder_dados(self):
        import sqlite3
        from app.db import MIGRACOES
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "v.db"
            con = sqlite3.connect(caminho)
            for sql in MIGRACOES[:3]:
                con.executescript(sql)
            con.execute("PRAGMA user_version = 3")
            con.execute("INSERT INTO jobs (id, tipo, usuario, status, criado_em) VALUES ('j1','gerar_remessas','u','concluido','x')")
            con.commit()
            con.close()
            Banco(caminho)
            con = sqlite3.connect(caminho)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(con.execute("SELECT id, status FROM jobs").fetchall(), [("j1", "concluido")])
            for t in ("retornos", "retorno_arquivos", "retorno_execucoes", "retorno_resultados"):
                con.execute(f"SELECT * FROM {t}")
            con.close()


# ------------------------------------------------------------------- workers
class WorkerTest(Base):
    def _sequencia(self, worker_cls, login_ok):
        AcadeFalso.chamadas, AcadeFalso.login_ok = [], login_ok
        from queue import Queue
        w = worker_cls(worker_id=1, pagamentos_queue=Queue(), resultados={"sucessos": [], "erros": []},
                       usuario="u", senha="s", headless=True)
        ok = w.inicializar_browser()
        return ok, list(AcadeFalso.chamadas)

    def test_inicializar_browser_igual_ao_do_cli(self):
        web_cls = _classe_worker(AcadeFalso, lambda r: None)
        for login_ok in (True, False):
            with unittest.mock.patch("worker_thread.AutomatizadorAcadeOneFINAL", AcadeFalso):
                cli = self._sequencia(worker_thread.WorkerThread, login_ok)
            self.assertEqual(self._sequencia(web_cls, login_ok), cli)


# -------------------------------------------------------------------- tarefa
class TarefaBaixaTest(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.repo.registrar("u", [("Retorno.RET", retorno())])

    def baixar(self, modo, job_id="abcdef012345", **kw):
        ctx = ContextoFalso(job_id)
        kw.setdefault("workers", 1)
        return baixar_retorno(ctx, self.cred, self.repo, self.rid, modo, automatizador_cls=AcadeFalso, **kw), ctx

    def test_mesmas_chamadas_ao_acade_e_mesmos_relatorios_do_cli(self):
        AcadeFalso.roteiro = {"1003": "nao_encontrado", "1006": "sem_parcela"}

        # CLI original: main() com 1 worker, como run_baixar_contas.ps1 (exceto o número de workers).
        cli_dir = self.dir / "cli"
        cli_dir.mkdir()
        (cli_dir / "Retorno.RET").write_bytes(retorno())
        cwd = os.getcwd()
        os.chdir(cli_dir)
        try:
            with unittest.mock.patch("worker_thread.AutomatizadorAcadeOneFINAL", AcadeFalso), \
                    unittest.mock.patch.object(baixar_contas_pagas, "configurar_logging"), \
                    unittest.mock.patch.dict(os.environ, {"ACADE_USUARIO": "usuario.teste", "ACADE_SENHA": "senha-teste"}), \
                    unittest.mock.patch.object(sys, "argv", ["x", "--diretorio_retorno", str(cli_dir), "--workers", "1"]):
                baixar_contas_pagas.main()
        finally:
            os.chdir(cwd)
        chamadas_cli = AcadeFalso.chamadas

        AcadeFalso.chamadas = []
        r, ctx = self.baixar("baixa")
        self.assertEqual(AcadeFalso.chamadas, chamadas_cli)
        self.assertIn(("salvar", "03357", "000000055263", "26092026"), chamadas_cli)

        pasta = self.dir / "retornos" / self.rid / "execucoes" / "abcdef012345"
        for prefixo in ("relatorio_baixas_", "nao_processados_"):
            web = next(pasta.glob(prefixo + "*.txt")).read_text(encoding="utf-8")
            cli = next(cli_dir.glob(prefixo + "*.txt")).read_text(encoding="utf-8")
            self.assertEqual(_normalizar(web), _normalizar(cli), prefixo)

        self.assertEqual(r["por_status"], {"sucesso": 2, "erro": 2})
        status = {i["documento"]: (i["status"], i["detalhe"]) for i in r["itens"]}
        self.assertEqual(status["1003"], ("erro", "Título não encontrado"))
        self.assertEqual(status["1006"], ("erro", "Parcela não encontrada"))
        self.assertAlmostEqual(r["valor_sucesso"], 1234.56 + 50)
        self.assertEqual(sorted(r["relatorios"])[0][:17], "nao_processados_2")

    def test_simulacao_nao_preenche_nem_salva(self):
        AcadeFalso.roteiro = {"1003": "nao_encontrado"}
        with unittest.mock.patch.object(tarefas, "_modal_de_pagamento_visivel", return_value=True) as modal:
            r, _ = self.baixar("simulacao")
        self.assertFalse([c for c in AcadeFalso.chamadas if c[0] == "salvar"])
        self.assertEqual(modal.call_count, 3)                  # 1001, 1004 e 1006; 1003 não foi encontrado
        status = {i["documento"]: (i["status"], i["detalhe"]) for i in r["itens"]}
        self.assertEqual(status["1001"], ("sucesso", DETALHE_SIMULACAO))
        self.assertEqual(status["1003"], ("erro", "Título não encontrado"))
        self.assertEqual(r["relatorios"], [])
        self.assertEqual(self.repo.resolvidos(self.rid), set())
        self.assertFalse(self.repo.teve_baixa(self.rid))

    def test_simulacao_aponta_modal_que_nao_abriu(self):
        with unittest.mock.patch.object(tarefas, "_modal_de_pagamento_visivel", return_value=False):
            r, _ = self.baixar("simulacao", max_retries=1)
        self.assertEqual({i["detalhe"] for i in r["itens"]}, {DETALHE_SIMULACAO_SEM_MODAL})

    def test_sem_login_nao_trava_e_informa_nao_processados(self):
        AcadeFalso.login_ok = False
        resultado = {}
        t = threading.Thread(target=lambda: resultado.update(r=self.baixar("baixa", workers=2)[0]), daemon=True)
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive(), "a baixa travou esperando a fila")
        r = resultado["r"]
        self.assertEqual(r["por_status"], {"nao_iniciado": 4})
        self.assertEqual({i["detalhe"] for i in r["itens"]}, {DETALHE_NAO_INICIADO})
        self.assertEqual(self.repo.resolvidos(self.rid), set())

    def test_reprocessar_so_pendencias_e_bloqueios(self):
        AcadeFalso.roteiro = {"1003": "nao_encontrado"}
        self.baixar("baixa", job_id="000000000001")
        with self.assertRaisesRegex(ErroRetorno, "já teve baixa"):
            self.baixar("baixa", job_id="000000000002")
        with self.assertRaisesRegex(ErroRetorno, "já teve baixa"):
            self.baixar("simulacao", job_id="000000000003")

        AcadeFalso.roteiro, AcadeFalso.chamadas = {}, []
        r, _ = self.baixar("reprocessar", job_id="000000000004")
        self.assertEqual([c[1] for c in AcadeFalso.chamadas if c[0] == "buscar"], ["1003"])
        self.assertEqual((r["total"], r["ja_resolvidos"], r["por_status"]), (1, 3, {"sucesso": 1}))
        self.assertEqual(len(self.repo.resolvidos(self.rid)), 4)

    def test_falha_ao_gravar_resultado_e_conciliada_pela_memoria(self):
        original = self.repo.registrar_resultado
        falhou = []

        def instavel(job_id, chave, status, detalhe=""):
            if chave.endswith("#0001#00001") and not falhou:     # pagamento 1001: a gravação falha uma vez
                falhou.append(chave)
                raise RuntimeError("database is locked")
            return original(job_id, chave, status, detalhe)

        with unittest.mock.patch.object(self.repo, "registrar_resultado", side_effect=instavel):
            r, _ = self.baixar("baixa", job_id="000000000001", workers=2)
        self.assertTrue(falhou)
        self.assertEqual(r["por_status"], {"sucesso": 4})
        self.assertEqual(len(self.repo.resolvidos(self.rid)), 4)

    def test_pagamento_interrompido_fica_desconhecido_e_fora_do_reprocessamento(self):
        from exceptions import AcadeSessionExpiredException

        class AcadeQueCai(AcadeFalso):
            def baixar_titulo(self, pagamento):
                if pagamento["seu_numero"] == "1004":
                    AcadeFalso.login_ok = False                 # a reabertura do navegador também falha
                    raise AcadeSessionExpiredException("sessão expirada")
                return super().baixar_titulo(pagamento)

        ctx = ContextoFalso("000000000001")
        r = baixar_retorno(ctx, self.cred, self.repo, self.rid, "baixa", workers=1, automatizador_cls=AcadeQueCai)
        status = {i["documento"]: (i["status"], i["detalhe"]) for i in r["itens"]}
        self.assertEqual(status["1004"], ("desconhecido", DETALHE_DESCONHECIDO))
        self.assertEqual(status["1006"], ("nao_iniciado", DETALHE_NAO_INICIADO))
        self.assertEqual(status["1001"][0], "sucesso")
        self.assertIn(("ERROR", f"1004 - CONCESSIONARIA TESTE: {DETALHE_DESCONHECIDO}"), ctx.logs)

        AcadeFalso.login_ok, AcadeFalso.chamadas = True, []
        r2, _ = self.baixar("reprocessar", job_id="000000000002")
        self.assertEqual([c[1] for c in AcadeFalso.chamadas if c[0] == "buscar"], ["1006"])


# ---------------------------------------------------------------------- rotas
class RotasRetornosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.chamadas = []

        def tarefa_falsa(ctx, cred, repo, retorno_id, modo, workers=3):
            self.chamadas.append((retorno_id, modo, workers))
            repo.iniciar_execucao(ctx.job_id, retorno_id, modo)
            pasta = repo.pasta_execucao(retorno_id, ctx.job_id)
            (pasta / "relatorio_baixas_20260928_100000.txt").write_text("RELATÓRIO", encoding="utf-8")
            for p in repo.ler(retorno_id)["confirmados"]:
                repo.registrar_resultado(ctx.job_id, p["chave"], "erro" if p["seu_numero"] == "1003" else "sucesso", "x")
            return {"retorno": retorno_id, "modo": modo, "por_status": {}, "relatorios": ["relatorio_baixas_20260928_100000.txt"]}

        settings = config.Settings(secret_key=b"k" * 32, cookie_secure=False, cred_ttl_dias=15, data_dir=self.dir,
                                   acade_base_url="https://acade.invalid", baixa_workers=2)
        self.app = criar_app(settings, validador=lambda u, s: True, tarefa_baixar_retorno=tarefa_falsa)
        self.app.state.repo_bancos.importar_csv(pd.DataFrame([{
            "codigo": "756", "empreendimentos": "LOTEAMENTO ALFA", "nome_cedente": "ALFA EMPREENDIMENTOS", "banco": "756",
            "agencia": "3357", "conta": "55263-1", "convenio": "100001", "status": "", "cnpj_cedente": "11222333000181"}]), "t")
        self.c = TestClient(self.app)
        self.c.post("/login", data={"usuario": "u", "senha": "s"})

    def tearDown(self):
        self.tmp.cleanup()

    def enviar(self, conteudo, nome="Retorno.RET"):
        return self.c.post("/retornos/enviar", files=[("arquivos", (nome, conteudo, "application/octet-stream"))],
                           follow_redirects=False)

    def esperar(self, job_id):
        for _ in range(100):
            job = self.app.state.jobs.obter(job_id)
            if job["status"] not in ("na_fila", "executando"):
                return job
            time.sleep(0.05)
        self.fail("tarefa não terminou")

    def test_fluxo_previa_confirmacao_baixa_e_reprocesso(self):
        r = self.enviar(retorno())
        self.assertEqual(r.status_code, 303)
        url = r.headers["location"]
        rid = url.rsplit("/", 1)[1]

        pagina = self.c.get(url)
        self.assertEqual(pagina.status_code, 200)
        for texto in ("MARIA DE TESTE", "FORNECEDOR TESTE LTDA", "Transação Rejeitada", "ALFA EMPREENDIMENTOS",
                      '"3357 55263"', "Executar baixa", "R$ 1.604,56"):
            self.assertIn(texto, pagina.text)

        self.assertEqual(self.enviar(retorno(), "copia.RET").status_code, 409)

        sem_confirmar = self.c.post(f"{url}/executar", data={"modo": "baixa"}, follow_redirects=False)
        self.assertEqual(sem_confirmar.status_code, 400)
        self.assertEqual(self.chamadas, [])

        r = self.c.post(f"{url}/executar", data={"modo": "baixa", "confirmo": "sim"}, follow_redirects=False)
        job_id = r.headers["location"].rsplit("/", 1)[1]
        self.assertEqual(self.esperar(job_id)["status"], "concluido")
        self.assertEqual(self.chamadas, [(rid, "baixa", 2)])

        self.assertEqual(self.app.state.retornos.retorno_da_execucao(job_id), {"retorno_id": rid, "modo": "baixa"})
        tarefa = self.c.get(f"/tarefas/{job_id}")
        self.assertIn("Baixa no ACADE", tarefa.text)
        self.assertIn(f'href="/retornos/{rid}"', tarefa.text)

        depois = self.c.get(url)
        self.assertIn("Reprocessar 1 pendência", depois.text)
        self.assertNotIn("Executar baixa</button>", depois.text)
        self.assertEqual(self.c.post(f"{url}/executar", data={"modo": "baixa", "confirmo": "sim"},
                                     follow_redirects=False).status_code, 409)
        self.assertEqual(self.c.post(f"{url}/executar", data={"modo": "simulacao"},
                                     follow_redirects=False).status_code, 409)

        rel = self.c.get(f"/retornos/{rid}/execucoes/{job_id}/relatorio_baixas_20260928_100000.txt")
        self.assertEqual(rel.text, "RELATÓRIO")
        original = self.c.get(f"/retornos/{rid}/entradas/01_Retorno.RET")
        self.assertEqual(original.content, retorno())
        for u in (f"/retornos/{rid}/execucoes/{job_id}/..%2F..%2F..%2Ft.db", f"/retornos/{rid}/entradas/..%2F..%2F..%2Fvivencie.db",
                  f"/retornos/..%2F{rid}/entradas/01_Retorno.RET"):
            resp = self.c.get(u, follow_redirects=False)
            self.assertIn(resp.status_code, (303, 404), u)
            self.assertNotIn(b"SQLite", resp.content)

        r = self.c.post(f"{url}/executar", data={"modo": "reprocessar", "confirmo": "sim"}, follow_redirects=False)
        self.esperar(r.headers["location"].rsplit("/", 1)[1])
        self.assertEqual(self.chamadas[-1], (rid, "reprocessar", 2))

    def test_execucao_fica_registrada_antes_da_tarefa_comecar(self):
        rid = self.enviar(retorno()).headers["location"].rsplit("/", 1)[1]
        vistos = []
        original = self.app.state.tarefa_baixar_retorno

        def tarefa(ctx, cred, repo, retorno_id, modo, workers=3):
            vistos.append(repo.retorno_da_execucao(ctx.job_id))
            return original(ctx, cred, repo, retorno_id, modo, workers)

        self.app.state.tarefa_baixar_retorno = tarefa
        r = self.c.post(f"/retornos/{rid}/executar", data={"modo": "simulacao"}, follow_redirects=False)
        self.esperar(r.headers["location"].rsplit("/", 1)[1])
        self.assertEqual(vistos, [{"retorno_id": rid, "modo": "simulacao"}])

    def test_envio_de_remessa_e_recusado(self):
        r = self.enviar(retorno(codigo="1"), "remessa.txt")
        self.assertEqual(r.status_code, 400)
        self.assertIn("REMESSA", r.text)

    def test_menu_e_painel_ligados(self):
        self.assertIn('href="/retornos" class=""', self.c.get("/").text)
        self.assertEqual(self.c.get("/retornos").status_code, 200)

    def test_exige_login(self):
        c = TestClient(self.app)
        self.assertEqual(c.get("/retornos", follow_redirects=False).headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
