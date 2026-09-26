import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app import config
from app.bancos import RepositorioBancos
from app.db import Banco
from app.jobs import GerenciadorJobs
from app.main import criar_app
from app.pessoas import ArquivoPessoas
from app.remessas import (MOTIVO_SEM_CONTA, MOTIVO_SEM_CONVENIO, MOTIVO_SEM_LAYOUT, ArquivoContasPagar, Execucoes,
                          analisar_remessa, bancos_para_gerador, diagnosticar, ler_bancos_csv)
from app.security import Credenciais
from app.tarefas import ErroRemessas, gerar_remessas

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
HEADER_TS = slice(143, 157)      # data/hora de geração no header de arquivo (tests/golden/golden_runner.py)

# Dados fictícios (CPF/CNPJ com dígitos verificadores válidos, sem pessoas reais).
BANCOS_CSV = """codigo,empreendimentos,nome_cedente,banco,agencia,conta,convenio,status,cnpj_cedente
756,LOTEAMENTO ALFA,ALFA EMPREENDIMENTOS,756,3357,1111-1,100001,,11222333000181
756,LOTEAMENTO ALFA II,ALFA EMPREENDIMENTOS,756,3357,1111-1,100001,,11222333000181
756,CONDOMINIO BETA,BETA SPE LTDA,756,4480,22222-2,200002,,11222333000181
756,OUTROS GAMA,GAMA LTDA,756,3357,3333-3,,,11222333000181
237,DELTA URBANISMO,DELTA LTDA,237,2214,40913-8,400004,,11222333000181
"""

PESSOAS_CSV = """Código,Nome,CPF/CNPJ,Tipo
000001,MARIA DE TESTE,123.456.789-09,Pessoa Física
000002,JOSE DE TESTE,987.654.321-00,Pessoa Física
000003,FORNECEDOR TESTE LTDA,11.222.333/0001-81,Pessoa Jurídica
"""

BOLETO = "34191.79001 01043.510047 91020.150008 1 84770000002000"
CONTAS = [
    # Centro Custo, Vencto, Comp., Lancto, Conta, Doc, Beneficiado, Parc, Valor, Obs.
    ("LOTEAMENTO ALFA", "26/09/2026", "09/2026", "1001", "SERVIÇOS", "", "FORNECEDOR TESTE LTDA", "1/1", "20,00", f"bto: {BOLETO}"),
    ("LOTEAMENTO ALFA II", "26/09/2026", "09/2026", "1002", "RESTITUIÇÃO", "", "MARIA DE TESTE", "1/1", "1.234,56", "PIX: maria@example.com"),
    ("LOTEAMENTO ALFA", "26/09/2026", "09/2026", "1003", "RESTITUIÇÃO", "", "JOSE DE TESTE", "1/1", "50,00", ""),
    ("CONDOMINIO BETA", "26/09/2026", "09/2026", "1004", "RESTITUIÇÃO", "", "JOSE DE TESTE", "1/2", "300,00", "PIX: 98765432100"),
    ("OUTROS GAMA", "26/09/2026", "09/2026", "1005", "RESTITUIÇÃO", "", "MARIA DE TESTE", "1/1", "10,00", "PIX: maria@example.com"),
    ("DELTA URBANISMO", "26/09/2026", "09/2026", "1006", "RESTITUIÇÃO", "", "MARIA DE TESTE", "1/1", "11,00", "PIX: maria@example.com"),
    ("CENTRO SEM CADASTRO", "26/09/2026", "09/2026", "1007", "RESTITUIÇÃO", "", "MARIA DE TESTE", "1/1", "12,00", "PIX: maria@example.com"),
]
COLUNAS_CONTAS = ["Centro Custo", "Vencto", "Comp.", "Lancto", "Conta", "Doc", "Beneficiado", "Parc", "Valor", "Obs."]


def df_contas(linhas=CONTAS) -> pd.DataFrame:
    return pd.DataFrame(linhas, columns=COLUNAS_CONTAS)


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(RAIZ)])
    return env


def rodar_cli(pasta: Path, contas: Path, bancos: Path, pessoas: Path) -> tuple[str, dict]:
    """O CLI original, como run_processar_cnab.ps1 o chama."""
    pasta.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([sys.executable, str(SRC / "processar_contas_pagar_cnab.py"), str(contas), str(bancos),
                           str(pessoas)], cwd=pasta, capture_output=True, text=True, env=_env())
    assert proc.returncode == 0, proc.stderr
    return proc.stdout, _remessas(pasta)


def _remessas(pasta: Path) -> dict:
    out = {}
    for a in sorted((pasta / "remessas").glob("*.txt")) if (pasta / "remessas").exists() else []:
        linhas = a.read_bytes().split(b"\n")
        h = bytearray(linhas[0])
        h[HEADER_TS] = b"#" * (HEADER_TS.stop - HEADER_TS.start)
        out[a.name] = b"\n".join([bytes(h)] + linhas[1:])
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.aux = self.dir / "arquivos_auxiliares"
        self.aux.mkdir()
        (self.aux / "pessoas_cadastradas.csv").write_text(PESSOAS_CSV, encoding="utf-8-sig")
        self.bancos_csv = self.dir / "bancos_curado.csv"
        self.bancos_csv.write_text(BANCOS_CSV, encoding="utf-8")
        self.relatorio = ArquivoContasPagar(self.aux)
        self.relatorio.gravar(df_contas(), {"data_inicial": "26/09/2026", "data_final": "26/09/2026"})

    def tearDown(self):
        self.tmp.cleanup()


class GravacaoRelatorioTest(Base):
    def test_gravacao_identica_ao_salvar_relatorio_do_cli(self):
        from automatizador_final import AutomatizadorAcadeOneFINAL

        cli_dir = self.dir / "cli"
        cli_dir.mkdir()
        falso = type("A", (), {"logger": logging.getLogger("teste")})()
        cwd = os.getcwd()
        os.chdir(cli_dir)
        try:
            AutomatizadorAcadeOneFINAL.salvar_relatorio(falso, df_contas(), "A", "csv")
        finally:
            os.chdir(cwd)
        self.assertEqual(self.relatorio.caminho.read_bytes(),
                         (cli_dir / "arquivos_auxiliares" / "relatorio_contas_pagar.csv").read_bytes())

    def test_gravar_guarda_anterior_e_periodo(self):
        antes = self.relatorio.caminho.read_bytes()
        self.relatorio.gravar(df_contas(CONTAS[:1]), {"data_inicial": "01/10/2026", "data_final": "02/10/2026"})
        self.assertEqual(self.relatorio.caminho_anterior.read_bytes(), antes)
        self.assertEqual(self.relatorio.resumo()["titulos"], 1)
        self.assertEqual(self.relatorio.meta()["data_final"], "02/10/2026")


class ExecucaoGeradorTest(Base):
    def test_runner_web_produz_a_mesma_saida_do_cli(self):
        entradas = (self.relatorio.caminho, self.bancos_csv, self.aux / "pessoas_cadastradas.csv")
        stdout_cli, rem_cli = rodar_cli(self.dir / "a", *entradas)

        b = self.dir / "b"
        b.mkdir()
        proc = subprocess.run([sys.executable, "-m", "app.cnab_execucao", str(b / "saida.json"), *map(str, entradas)],
                              cwd=b, capture_output=True, text=True, env=_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, stdout_cli)
        self.assertEqual(_remessas(b), rem_cli)
        self.assertTrue(rem_cli)

        saida = json.loads((b / "saida.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(saida["resultados"]), ["1111-1_3357", "22222-2_4480", "3333-3_3357", "40913-8_2214"])
        # Título 1003 (sem dados de pagamento na observação) aparece na tabela de alerta do CLI e no JSON.
        self.assertEqual([n["Lancto"] for n in saida["nao_incluidos"]], ["1003"])
        self.assertIn("ALERTA: Pagamentos não incluídos", stdout_cli)


class DiagnosticoTest(Base):
    def test_classifica_como_o_merge_do_gerador(self):
        d = diagnosticar(self.relatorio.ler(), ler_bancos_csv(self.bancos_csv))
        motivos = {e["lancto"]: e["motivo"] for e in d["excluidos"]}
        self.assertEqual(motivos, {"1005": MOTIVO_SEM_CONVENIO, "1006": MOTIVO_SEM_LAYOUT, "1007": MOTIVO_SEM_CONTA})
        self.assertEqual(d["aptos"], 4)
        self.assertEqual([(c["conta"], c["agencia"], c["titulos"]) for c in d["contas"]],
                         [("1111-1", "3357", 3), ("22222-2", "4480", 1)])
        self.assertAlmostEqual(d["contas"][0]["valor"], 20 + 1234.56 + 50)
        self.assertEqual(d["por_motivo"][MOTIVO_SEM_CONTA]["centros"], ["CENTRO SEM CADASTRO"])

    def test_bancos_para_gerador_retira_so_contas_inaptas(self):
        filtrado = bancos_para_gerador(ler_bancos_csv(self.bancos_csv))
        self.assertEqual(filtrado["empreendimentos"].tolist(), ["LOTEAMENTO ALFA", "LOTEAMENTO ALFA II", "CONDOMINIO BETA"])

    def test_centro_vazio_no_relatorio_fica_sem_conta(self):
        linhas = [("",) + CONTAS[0][1:]]
        df = df_contas(linhas)
        df.to_csv(self.relatorio.caminho, index=False, encoding="utf-8-sig")
        d = diagnosticar(self.relatorio.ler(), ler_bancos_csv(self.bancos_csv))
        self.assertEqual([e["motivo"] for e in d["excluidos"]], [MOTIVO_SEM_CONTA])


class ContextoFalso:
    job_id = "abcdef012345"

    def __init__(self):
        self.logs, self.etapas = [], []

    def progresso(self, atual=None, total=None, etapa=None):
        if etapa:
            self.etapas.append(etapa)

    def log(self, msg, nivel="INFO"):
        self.logs.append((nivel, msg))

    def ponto(self, *a, **k):
        pass

    def capturar_logs(self, *a, **k):
        import contextlib
        return contextlib.nullcontext()


class AutomatizadorFalso:
    df = None
    chamadas = []

    def __init__(self, headless):
        AutomatizadorFalso.chamadas = [("init", headless)]

    def fazer_login(self, u, s):
        self.chamadas.append(("login", u, s))
        return True

    def navegar_para_menu_relatorio(self):
        self.chamadas.append(("menu",))
        return True

    def clicar_contas_a_pagar(self):
        self.chamadas.append(("contas",))
        return True

    def configurar_formulario(self, di, df, tipo):
        self.chamadas.append(("form", di, df, tipo))
        return True

    def gerar_relatorio(self):
        self.chamadas.append(("gerar",))
        return True

    def extrair_dados_tabela(self):
        return self.df.copy()

    def fechar(self):
        self.chamadas.append(("fechar",))


class TarefaGerarRemessasTest(Base):
    def setUp(self):
        super().setUp()
        self.repo = RepositorioBancos(Banco(self.dir / "t.db"))
        self.repo.importar_csv(pd.read_csv(self.bancos_csv, dtype=str, keep_default_na=False), "teste")
        self.pessoas = ArquivoPessoas(self.aux)
        self.execucoes = Execucoes(self.dir / "remessas")
        self.cred = Credenciais("usuario.teste", "senha-teste", time.time())

    def gerar(self, **kw):
        ctx = ContextoFalso()
        return gerar_remessas(ctx, self.cred, self.relatorio, self.repo, self.pessoas, self.execucoes, **kw), ctx

    def test_exclui_so_titulos_afetados_e_mantem_arquivos_iguais_ao_cli(self):
        r, ctx = self.gerar()
        self.assertEqual({e["lancto"]: e["motivo"] for e in r["excluidos"]},
                         {"1005": MOTIVO_SEM_CONVENIO, "1006": MOTIVO_SEM_LAYOUT, "1007": MOTIVO_SEM_CONTA})
        self.assertEqual([n["lancto"] for n in r["nao_incluidos"]], ["1003"])
        self.assertEqual([(a["conta"], a["pagamentos"]) for a in r["arquivos"]], [("1111-1", 2), ("22222-2", 1)])
        self.assertEqual(r["incluidos"], 3)
        self.assertAlmostEqual(r["valor_incluido"], 20 + 1234.56 + 300)
        self.assertEqual(r["sem_remessa"], [])
        self.assertEqual(r["divergencias"], [])

        # As remessas das contas aptas são byte a byte as do CLI rodando sobre o bancos.csv completo.
        pasta = self.execucoes.pasta_execucao(r["execucao"])
        _, rem_cli = rodar_cli(self.dir / "cli", self.relatorio.caminho, self.bancos_csv,
                               self.aux / "pessoas_cadastradas.csv")
        web = _remessas(pasta)
        self.assertEqual(sorted(web), sorted(n for n in rem_cli if "_11111_" in n or "_222222_" in n))
        for nome, conteudo in web.items():
            self.assertEqual(conteudo, rem_cli[nome])
        self.assertTrue((pasta / "resultado.txt").read_text(encoding="utf-8").startswith("=" * 80))
        self.assertTrue(any(n == "WARNING" and "OUTROS GAMA" in m for n, m in ctx.logs))

    def test_captura_do_acade_grava_relatorio_e_periodo(self):
        AutomatizadorFalso.df = df_contas(CONTAS[:2])
        r, ctx = self.gerar(periodo=("25/09/2026", "26/09/2026"), automatizador_cls=AutomatizadorFalso)
        self.assertEqual(AutomatizadorFalso.chamadas, [
            ("init", True), ("login", "usuario.teste", "senha-teste"), ("menu",), ("contas",),
            ("form", "25/09/2026", "26/09/2026", "A"), ("gerar",), ("fechar",)])
        self.assertEqual(r["relatorio"]["titulos"], 2)
        self.assertTrue(r["relatorio"]["capturado_agora"])
        self.assertEqual(self.relatorio.meta()["usuario"], "usuario.teste")
        self.assertEqual(r["incluidos"], 2)

    def test_captura_vazia_mantem_relatorio_anterior(self):
        antes = self.relatorio.caminho.read_bytes()
        AutomatizadorFalso.df = pd.DataFrame()
        with self.assertRaises(ErroRemessas) as e:
            self.gerar(periodo=("25/09/2026", "26/09/2026"), automatizador_cls=AutomatizadorFalso)
        self.assertIn("relatório anterior foi mantido", str(e.exception))
        self.assertEqual(self.relatorio.caminho.read_bytes(), antes)
        self.assertEqual(AutomatizadorFalso.chamadas[-1], ("fechar",))
        self.assertFalse(self.execucoes.pasta.exists())

    def test_exige_pessoas(self):
        (self.aux / "pessoas_cadastradas.csv").unlink()
        with self.assertRaises(ErroRemessas):
            self.gerar()

    def test_gerador_com_erro_falha_a_tarefa(self):
        with self.assertRaises(ErroRemessas):
            self.gerar(gerador=lambda *a: 1)

    def test_conta_sem_remessa_e_divergencia_sao_apontadas(self):
        def gerador_parcial(pasta, contas, bancos, pessoas, ao_linha):
            ao_linha("2026-09-26 10:00:00,000 - ERROR - Erro ao inicializar motor para 22222-2_4480: teste")
            (pasta / "remessas").mkdir()
            (pasta / "remessas" / "x.txt").write_text("")
            (pasta / "nao_incluidos.json").write_text(json.dumps(
                {"nao_incluidos": [], "resultados": {"1111-1_3357": [str(pasta / "remessas" / "x.txt")]}}))
            return 0
        r, ctx = self.gerar(gerador=gerador_parcial)
        self.assertEqual([(s["conta"], s["esperados"]) for s in r["sem_remessa"]], [("22222-2", 1)])
        self.assertEqual(r["divergencias"], [{"arquivo": "x.txt", "esperados": 3, "no_arquivo": 0}])
        self.assertIn(("ERROR", "Erro ao inicializar motor para 22222-2_4480: teste"), ctx.logs)

    def test_analisar_remessa(self):
        r, _ = self.gerar()
        pasta = self.execucoes.pasta_execucao(r["execucao"])
        info = analisar_remessa(pasta / "remessas" / r["arquivos"][0]["arquivo"])
        self.assertEqual(info, {"pagamentos": 2, "valor": 1254.56, "lotes": 2})


class RotasRemessasTest(Base):
    def setUp(self):
        super().setUp()
        self.chamadas = []

        def tarefa_falsa(ctx, cred, relatorio, repo, pessoas, execucoes, periodo=None):
            self.chamadas.append(periodo)
            pasta = execucoes.nova(ctx.job_id)
            (pasta / "remessas").mkdir()
            (pasta / "remessas" / "20260926_11111_3357.txt").write_bytes(b"CNAB")
            return {"execucao": pasta.name}

        settings = config.Settings(secret_key=b"k" * 32, cookie_secure=False, cred_ttl_dias=15,
                                   data_dir=self.dir, acade_base_url="https://acade.invalid")
        self.app = criar_app(settings, validador=lambda u, s: True, tarefa_gerar_remessas=tarefa_falsa)
        self.app.state.repo_bancos.importar_csv(pd.read_csv(self.bancos_csv, dtype=str, keep_default_na=False), "t")
        self.c = TestClient(self.app)
        self.c.post("/login", data={"usuario": "u", "senha": "s"})

    def esperar(self, job_id):
        for _ in range(100):
            job = self.app.state.jobs.obter(job_id)
            if job["status"] not in ("na_fila", "executando"):
                return job
            time.sleep(0.05)
        self.fail("tarefa não terminou")

    def test_pagina_mostra_validacao_do_relatorio_atual(self):
        r = self.c.get("/remessas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CENTRO SEM CADASTRO", r.text)
        self.assertIn(MOTIVO_SEM_CONVENIO, r.text)
        self.assertIn("R$ 1.304,56", r.text)       # conta 1111-1: 20,00 + 1.234,56 + 50,00

    def test_datas_invertidas_sao_recusadas(self):
        r = self.c.post("/remessas/gerar", data={"modo": "capturar", "data_inicial": "2026-09-27",
                                                 "data_final": "2026-09-26"}, follow_redirects=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.chamadas, [])

    def test_gerar_captura_com_periodo_e_baixa_arquivos(self):
        r = self.c.post("/remessas/gerar", data={"modo": "capturar", "data_inicial": "2026-09-25",
                                                 "data_final": "2026-09-26"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        job = self.esperar(r.headers["location"].rsplit("/", 1)[1])
        self.assertEqual(self.chamadas, [("25/09/2026", "26/09/2026")])
        exe = job["resultado"]["execucao"]

        a = self.c.get(f"/remessas/{exe}/20260926_11111_3357.txt")
        self.assertEqual(a.content, b"CNAB")
        z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/remessas/{exe}/remessas.zip").content))
        self.assertEqual(z.namelist(), ["20260926_11111_3357.txt"])

        for url in (f"/remessas/{exe}/..%2F..%2Ft.db", f"/remessas/..%2Farquivos_auxiliares/pessoas_cadastradas.csv",
                    f"/remessas/{exe}/inexistente.txt"):
            resp = self.c.get(url, follow_redirects=False)
            self.assertIn(resp.status_code, (303, 404), url)
            self.assertNotIn(b"SQLite", resp.content)

    def test_gerar_com_relatorio_atual_nao_captura(self):
        r = self.c.post("/remessas/gerar", data={"modo": "atual"}, follow_redirects=False)
        self.esperar(r.headers["location"].rsplit("/", 1)[1])
        self.assertEqual(self.chamadas, [None])

    def test_exige_login(self):
        c = TestClient(self.app)
        self.assertEqual(c.get("/remessas", follow_redirects=False).headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
