import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app import config
from app.bancos import RepositorioBancos
from app.conciliacao import ErroConciliacao, ExecucoesConciliacao, checar_pessoas, nome_seguro, validar_envio
from app.conciliacao_execucao import (MOTIVO_CENTRO_SEM_CONTA, MOTIVO_CONTA_SEM_EXTRATO, MOTIVO_DATA_INVALIDA,
                                      MOTIVO_FORMATO, MOTIVO_SEM_CADASTRO, MOTIVO_SEM_PAGAMENTOS)
from app.db import Banco
from app.main import criar_app
from app.pessoas import ArquivoPessoas
from app.security import Credenciais
from app.tarefas import conciliar

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
_spec = importlib.util.spec_from_file_location("golden_runner", RAIZ / "tests" / "golden" / "golden_runner.py")
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

# Dados fictícios (CPF/CNPJ com dígitos verificadores válidos, sem pessoas reais).
BANCOS_CSV = """codigo,empreendimentos,nome_cedente,banco,agencia,conta,convenio,status,cnpj_cedente
756,LOTEAMENTO ALFA,ALFA LTDA,756,3357,1111-1,100001,,11222333000181
756,LOTEAMENTO ALFA II,ALFA LTDA,756,3357,1111-1,100001,,11222333000181
756,CONDOMINIO BETA,BETA LTDA,756,4480,2222-2,200002,,11222333000181
756,OUTROS GAMA,GAMA LTDA,756,3357-0,3333-3,300003,,11222333000181
756,LOTEAMENTO DELTA,DELTA LTDA,756,3357,4444-4,400004,,11222333000181
"""

PESSOAS_CSV = """Código,Nome,CPF/CNPJ,Tipo
000001,MARIA DE TESTE,123.456.789-09,Pessoa Física
000002,FORNECEDOR TESTE LTDA,11.222.333/0001-81,Pessoa Jurídica
"""

COLUNAS_PAGAS = ["Centro Custo", "Vencto", "Pagto", "Comp.", "Lancto", "Conta", "Doc", "Beneficiado", "Parc", "Valor",
                 "Conta Movimento", "Obs."]
PAGAS = [
    ("LOTEAMENTO ALFA", "05/01/2026", "05/01/2026", "01/2026", "1", "SERVIÇOS", "NF 10", "FORNECEDOR TESTE LTDA", "1/1", "150,00", "", "NF 10"),
    ("LOTEAMENTO ALFA II", "06/01/2026", "06/01/2026", "01/2026", "2", "SERVIÇOS", "", "MARIA DE TESTE", "1/1", "1.200,00", "", ""),
    ("LOTEAMENTO ALFA", "07/01/2026", "07/01/2026", "01/2026", "3", "SERVIÇOS", "", "MARIA DE TESTE", "1/1", "999,99", "", "sem débito"),
    ("LOTEAMENTO ALFA", "07/01/2026", "", "01/2026", "4", "SERVIÇOS", "", "MARIA DE TESTE", "1/1", "10,00", "", "sem data"),
    ("CENTRO SEM CADASTRO", "05/01/2026", "05/01/2026", "01/2026", "5", "SERVIÇOS", "", "MARIA DE TESTE", "1/1", "11,00", "", ""),
    ("LOTEAMENTO DELTA", "05/01/2026", "05/01/2026", "01/2026", "6", "SERVIÇOS", "", "MARIA DE TESTE", "1/1", "12,00", "", ""),
]


def ofx(agencia: str, conta: str, transacoes: list[tuple[str, str, str, str]]) -> str:
    trn = "".join(f"<STMTTRN>\n<TRNTYPE>{'DEBIT' if v.startswith('-') else 'CREDIT'}</TRNTYPE>\n"
                  f"<DTPOSTED>{d}120000[-3:BRT]</DTPOSTED>\n<TRNAMT>{v}</TRNAMT>\n<FITID>{d}{i}</FITID>\n"
                  f"<MEMO>{memo}</MEMO>\n{f'<NAME>{nome}</NAME>' if nome else ''}\n</STMTTRN>\n"
                  for i, (d, v, nome, memo) in enumerate(transacoes))
    return ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
            "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n<OFX>\n<SIGNONMSGSRSV1>\n<SONRS>\n<STATUS>\n"
            "<CODE>0</CODE>\n<SEVERITY>INFO</SEVERITY>\n</STATUS>\n<DTSERVER>20260110120000[-3:BRT]</DTSERVER>\n"
            "<LANGUAGE>POR</LANGUAGE>\n</SONRS>\n</SIGNONMSGSRSV1>\n<BANKMSGSRSV1>\n<STMTTRNRS>\n<TRNUID>1</TRNUID>\n"
            "<STATUS>\n<CODE>0</CODE>\n<SEVERITY>INFO</SEVERITY>\n</STATUS>\n<STMTRS>\n<CURDEF>BRL</CURDEF>\n"
            f"<BANKACCTFROM>\n<BANKID>756</BANKID>\n<BRANCHID>{agencia}</BRANCHID>\n<ACCTID>{conta}</ACCTID>\n"
            "<ACCTTYPE>CHECKING</ACCTTYPE>\n</BANKACCTFROM>\n<BANKTRANLIST>\n<DTSTART>20260101120000[-3:BRT]</DTSTART>\n"
            f"<DTEND>20260109120000[-3:BRT]</DTEND>\n{trn}</BANKTRANLIST>\n<LEDGERBAL>\n<BALAMT>0.00</BALAMT>\n"
            "<DTASOF>20260109120000[-3:BRT]</DTASOF>\n</LEDGERBAL>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n")


OFX = {
    # Conta com pagamentos: um OK, um só com data diferente, um débito sem pagamento e um crédito.
    "a_alfa.ofx": ofx("3357-0", "1111-1", [("20260105", "-150.00", "FORNECEDOR TESTE LTDA", "PIX"),
                                            ("20260108", "-1200.00", "MARIA DE TESTE", "PIX"),
                                            ("20260102", "-47.30", "", "TARIFA"),
                                            ("20260103", "500.00", "CLIENTE", "CREDITO")]),
    "b_sem_cadastro.ofx": ofx("3357-0", "9999-9", [("20260104", "-20.00", "X", "TARIFA")]),
    "c_beta_sem_pagamentos.ofx": ofx("4480-6", "2222-2", [("20260106", "-30.00", "Y", "TARIFA")]),
    "d_gama_formato.ofx": ofx("3357-0", "3333-3", [("20260107", "-40.00", "Z", "TARIFA")]),
}


def gravar_dados(raiz: Path) -> None:
    aux, pasta_ofx = raiz / "arquivos_auxiliares", raiz / "ofx_a_processar"
    aux.mkdir(parents=True)
    pasta_ofx.mkdir()
    (aux / "bancos.csv").write_text(BANCOS_CSV, encoding="utf-8")
    (aux / "pessoas_cadastradas.csv").write_text(PESSOAS_CSV, encoding="utf-8-sig")
    pd.DataFrame(PAGAS, columns=COLUNAS_PAGAS).to_csv(aux / "relatorio_contas_pagas.csv", index=False, encoding="utf-8-sig")
    for nome, texto in OFX.items():
        (pasta_ofx / nome).write_text(texto, encoding="latin-1")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class EquivalenciaCliTest(Base):
    def test_caminho_web_gera_as_mesmas_planilhas_e_saida_do_cli(self):
        gravar_dados(self.dir / "dados")
        golden.run_conciliacao(SRC, self.dir / "dados", self.dir / "cli")
        golden.run_conciliacao_web(SRC, self.dir / "dados", self.dir / "web")
        self.assertFalse((self.dir / "web" / "stderr.txt").exists())
        self.assertTrue(list((self.dir / "cli").glob("*__Conciliados.csv")))
        self.assertEqual(golden.compare(self.dir / "cli", self.dir / "web"), 0)


class ResultadoPainelTest(Base):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        raiz = Path(cls._tmp.name)
        gravar_dados(raiz / "dados")
        pasta = raiz / "exec"
        (pasta / "entradas").mkdir(parents=True)
        os.rename(raiz / "dados" / "arquivos_auxiliares", pasta / "entradas" / "arquivos_auxiliares")
        os.rename(raiz / "dados" / "ofx_a_processar", pasta / "entradas" / "ofx")
        from app.tarefas import executar_conciliacao
        cls.linhas = []
        assert executar_conciliacao(pasta, "analisar", cls.linhas.append) == 0
        assert executar_conciliacao(pasta, "conciliar", cls.linhas.append) == 0
        cls.analise = json.loads((pasta / "analise.json").read_text(encoding="utf-8"))
        cls.r = json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))
        cls.xlsx = sorted(p.name for p in (pasta / "relatorios").glob("*.xlsx"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_analise_usa_periodo_e_contas_dos_ofx(self):
        self.assertEqual((self.analise["data_min"], self.analise["data_max"]), ("02/01/2026", "08/01/2026"))
        self.assertEqual(len(self.analise["contas"]), 4)
        self.assertTrue(all(a["valido"] for a in self.analise["arquivos"]))

    def test_contas_puladas_pelo_cli_sao_explicadas(self):
        puladas = {p["conta"]: p for p in self.r["puladas"]}
        self.assertEqual(puladas["9999-9"]["motivo"], MOTIVO_SEM_CADASTRO)
        self.assertEqual(puladas["2222-2"]["motivo"], MOTIVO_SEM_PAGAMENTOS)
        self.assertEqual(puladas["2222-2"]["empreendimentos"], ["CONDOMINIO BETA"])
        self.assertEqual(puladas["3333-3"]["motivo"], MOTIVO_FORMATO)
        self.assertEqual(puladas["3333-3"]["cadastro"], ["3357-0 / 3333-3"])
        # O CLI imprime "Pulando" para as mesmas três contas e grava xlsx só para a outra.
        self.assertEqual(sum("Pulando este grupo" in l for l in self.linhas), 3)
        self.assertEqual(self.xlsx, ["conciliacao_756_3357_1111-1.xlsx"])

    def test_conta_conciliada(self):
        (c,) = self.r["contas"]
        self.assertEqual((c["conta"], c["agencia"], c["arquivo"]), ("1111-1", "3357", "conciliacao_756_3357_1111-1.xlsx"))
        self.assertEqual((c["transacoes"], c["debitos"]), (4, 3))
        self.assertAlmostEqual(c["valor_debitos"], 1397.30)
        self.assertEqual(c["conciliados"] + c["debitos_nao_encontrados"], c["debitos"])
        self.assertEqual(c["conciliados"], 2)
        self.assertAlmostEqual(c["valor_conciliado"], 1350.00)
        self.assertEqual(c["atencao"], {"OK": 1, "DATA": 1})
        self.assertEqual(c["pagtos_nao_encontrados"], 1)
        self.assertEqual(c["linhas"]["pagtos_nao_encontrados"][0]["Obs."], "sem débito")
        self.assertEqual(c["linhas"]["debitos_nao_encontrados"][0]["memo"], "TARIFA")
        self.assertEqual(c["empreendimentos"], ["LOTEAMENTO ALFA", "LOTEAMENTO ALFA II"])

    def test_pagamentos_fora_e_sem_data(self):
        fora = {f["Centro Custo"]: f["motivo"] for f in self.r["pagamentos_fora"]}
        self.assertEqual(fora, {"CENTRO SEM CADASTRO": MOTIVO_CENTRO_SEM_CONTA, "LOTEAMENTO DELTA": MOTIVO_CONTA_SEM_EXTRATO})
        (inv,) = self.r["pagamentos_invalidos"]
        self.assertEqual((inv["Obs."], inv["motivo"], inv["conta"]), ("sem data", MOTIVO_DATA_INVALIDA, "1111-1"))
        t = self.r["totais"]
        self.assertEqual(t["pagamentos_relatorio"], len(PAGAS))
        self.assertEqual(t["conciliados"] + t["pagtos_nao_encontrados"] + len(self.r["pagamentos_fora"]) +
                         len(self.r["pagamentos_invalidos"]), len(PAGAS))


class ValidacaoEnvioTest(unittest.TestCase):
    def test_nome_seguro(self):
        self.assertEqual(nome_seguro("../../etc/Extrato Jan.OFX"), "Extrato_Jan.ofx")
        self.assertEqual(nome_seguro("C:\\x\\a.ofx"), "a.ofx")
        with self.assertRaises(ErroConciliacao):
            nome_seguro("extrato.csv")

    def test_validar_envio(self):
        ok = OFX["a_alfa.ofx"].encode()
        self.assertEqual(validar_envio([("a.ofx", ok)]), [("a.ofx", ok)])
        for arquivos in ([], [("a.ofx", b"nada")], [("a.ofx", ok), ("A.OFX", ok)], [("a.ofx", b"<OFX>" + b"x" * 6_000_000)]):
            with self.assertRaises(ErroConciliacao):
                validar_envio(arquivos)

    def test_pessoas_com_mais_de_5_dias_bloqueia(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pessoas_cadastradas.csv"
            with self.assertRaises(ErroConciliacao):
                checar_pessoas(p)
            p.write_text(PESSOAS_CSV)
            checar_pessoas(p)
            checar_pessoas(p, agora=datetime.now() + timedelta(days=4, hours=23))
            with self.assertRaisesRegex(ErroConciliacao, "desatualizado"):
                checar_pessoas(p, agora=datetime.now() + timedelta(days=5, minutes=1))


class ContextoFalso:
    job_id = "abcdef012345"

    def __init__(self):
        self.logs, self.etapas, self.progressos = [], [], []

    def progresso(self, atual=None, total=None, etapa=None):
        if etapa:
            self.etapas.append(etapa)
        if atual is not None:
            self.progressos.append((atual, total))

    def log(self, msg, nivel="INFO"):
        self.logs.append((nivel, msg))

    def capturar_logs(self, *a, **k):
        import contextlib
        return contextlib.nullcontext()


class AutomatizadorFalso:
    df = None
    chamadas = []

    def __init__(self, headless):
        AutomatizadorFalso.chamadas = [("init", headless)]

    def fazer_login(self, u, s):
        return True

    def navegar_para_menu_relatorio(self):
        return True

    def clicar_contas_a_pagar(self):
        return True

    def configurar_formulario(self, di, df, tipo):
        self.chamadas.append(("form", di, df, tipo))
        return True

    def gerar_relatorio(self):
        return True

    def extrair_dados_tabela(self):
        return self.df.copy()

    def fechar(self):
        self.chamadas.append(("fechar",))


class TarefaConciliarTest(Base):
    def setUp(self):
        super().setUp()
        gravar_dados(self.dir / "dados")
        self.aux = self.dir / "dados" / "arquivos_auxiliares"
        self.pessoas = ArquivoPessoas(self.aux)
        self.repo = RepositorioBancos(Banco(self.dir / "t.db"))
        self.repo.importar_csv(pd.read_csv(io.StringIO(BANCOS_CSV), dtype=str, keep_default_na=False), "teste")
        self.execucoes = ExecucoesConciliacao(self.dir / "conciliacoes")
        arquivos = [(n, t.encode("latin-1")) for n, t in OFX.items()]
        self.pasta = self.execucoes.nova("abcdef012345", "usuario.teste", arquivos)
        self.cred = Credenciais("usuario.teste", "senha-teste", time.time())
        AutomatizadorFalso.df = pd.DataFrame(PAGAS, columns=COLUNAS_PAGAS)

    def rodar(self, **kw):
        ctx = ContextoFalso()
        return conciliar(ctx, self.cred, self.pasta, self.repo, self.pessoas,
                         automatizador_cls=AutomatizadorFalso, **kw), ctx

    def test_captura_pagas_do_periodo_dos_ofx_e_concilia(self):
        r, ctx = self.rodar()
        self.assertIn(("form", "02/01/2026", "08/01/2026", "P"), AutomatizadorFalso.chamadas)
        self.assertIn(("init", True), [AutomatizadorFalso.chamadas[0]])
        self.assertEqual((r["execucao"], r["contas"], r["puladas"], r["pagamentos_fora"]), (self.pasta.name, 1, 3, 2))
        self.assertEqual(r["totais"]["conciliados"], 2)
        aux = self.pasta / "entradas" / "arquivos_auxiliares"
        # Gravado com os parâmetros de salvar_relatorio: utf-8-sig, sem índice.
        self.assertTrue((aux / "relatorio_contas_pagas.csv").read_bytes().startswith(b"\xef\xbb\xbfCentro Custo,"))
        self.assertEqual((aux / "pessoas_cadastradas.csv").read_bytes(), self.pessoas.caminho.read_bytes())
        self.assertEqual(len(pd.read_csv(aux / "bancos.csv")), 5)
        self.assertTrue((self.pasta / "relatorios" / "conciliacao_756_3357_1111-1.xlsx").exists())
        self.assertEqual(ctx.progressos[-1], (4, 4))
        self.assertTrue(any(n == "WARNING" and "9999-9" in m for n, m in ctx.logs))
        meta = self.execucoes.meta(self.pasta.name)
        self.assertEqual((meta["data_inicial"], meta["data_final"], meta["usuario"]), ("02/01/2026", "08/01/2026", "usuario.teste"))
        self.assertIsNotNone(self.execucoes.resultado(self.pasta.name))

    def test_pessoas_desatualizado_bloqueia_antes_do_acade(self):
        velho = time.time() - 6 * 86400
        os.utime(self.pessoas.caminho, (velho, velho))
        with self.assertRaisesRegex(ErroConciliacao, "desatualizado"):
            self.rodar()
        self.assertEqual(AutomatizadorFalso.chamadas, [])

    def test_bancos_vazio_bloqueia(self):
        self.repo = RepositorioBancos(Banco(self.dir / "vazio.db"))
        with self.assertRaisesRegex(ErroConciliacao, "bancos vazio"):
            self.rodar()

    def test_captura_vazia_nao_concilia(self):
        AutomatizadorFalso.df = pd.DataFrame(columns=COLUNAS_PAGAS)
        with self.assertRaisesRegex(ErroConciliacao, "não retornou contas pagas"):
            self.rodar()
        self.assertFalse((self.pasta / "relatorios").exists())

    def test_ofx_invalido_falha_sem_acessar_o_acade(self):
        pasta_ofx = self.pasta / "entradas" / "ofx"
        for a in pasta_ofx.iterdir():
            a.write_text("<OFX></OFX>")
        AutomatizadorFalso.chamadas = []
        with self.assertRaisesRegex(ErroConciliacao, "Nenhum arquivo OFX válido"):
            self.rodar()
        self.assertEqual(AutomatizadorFalso.chamadas, [])


class RotasConciliacaoTest(Base):
    def setUp(self):
        super().setUp()
        gravar_dados(self.dir / "dados")
        self.chamadas = []
        teste = self

        def tarefa_falsa(ctx, cred, pasta, repo, pessoas):
            teste.chamadas.append(sorted(p.name for p in (pasta / "entradas" / "ofx").iterdir()))
            return conciliar(ctx, cred, pasta, repo, pessoas, automatizador_cls=AutomatizadorFalso)

        AutomatizadorFalso.df = pd.DataFrame(PAGAS, columns=COLUNAS_PAGAS)
        settings = config.Settings(secret_key=b"k" * 32, cookie_secure=False, cred_ttl_dias=15,
                                   data_dir=self.dir, acade_base_url="https://acade.invalid")
        self.app = criar_app(settings, validador=lambda u, s: True, tarefa_conciliar=tarefa_falsa)
        aux = self.dir / "arquivos_auxiliares"
        aux.mkdir()
        (aux / "pessoas_cadastradas.csv").write_text(PESSOAS_CSV, encoding="utf-8-sig")
        self.app.state.repo_bancos.importar_csv(pd.read_csv(io.StringIO(BANCOS_CSV), dtype=str, keep_default_na=False), "t")
        self.c = TestClient(self.app)
        self.c.post("/login", data={"usuario": "u", "senha": "s"})

    def esperar(self, job_id):
        for _ in range(600):
            job = self.app.state.jobs.obter(job_id)
            if job["status"] not in ("na_fila", "executando"):
                return job
            time.sleep(0.05)
        self.fail("tarefa não terminou")

    def enviar(self, arquivos):
        return self.c.post("/conciliacao", files=[("arquivos", (n, c, "application/octet-stream")) for n, c in arquivos],
                           follow_redirects=False)

    def test_envio_concilia_e_mostra_painel(self):
        r = self.enviar([(n, t.encode("latin-1")) for n, t in OFX.items()])
        self.assertEqual(r.status_code, 303)
        job = self.esperar(r.headers["location"].rsplit("/", 1)[1])
        self.assertEqual(job["status"], "concluido", job.get("erro"))
        self.assertEqual(self.chamadas, [sorted(OFX)])
        execucao = job["resultado"]["execucao"]

        tela = self.c.get("/conciliacao")
        self.assertIn(f"/conciliacao/{execucao}", tela.text)
        self.assertIn("02/01/2026 a 08/01/2026", tela.text)
        painel = self.c.get(f"/conciliacao/{execucao}")
        self.assertEqual(painel.status_code, 200)
        for trecho in ("9999-9", MOTIVO_SEM_CADASTRO, "3357-0 / 3333-3", "2 pagamento(s) do período",
                       "1 pagamento(s) sem data", "conciliacao_756_3357_1111-1.xlsx"):
            self.assertIn(trecho, painel.text)
        self.assertIn("Conciliação bancária", self.c.get(f"/tarefas/{job['id']}").text)

        xlsx = self.c.get(f"/conciliacao/{execucao}/conciliacao_756_3357_1111-1.xlsx")
        self.assertEqual(xlsx.status_code, 200)
        self.assertEqual(xlsx.content, (self.dir / "conciliacoes" / execucao / "relatorios" / "conciliacao_756_3357_1111-1.xlsx").read_bytes())
        z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/conciliacao/{execucao}/relatorios.zip").content))
        self.assertEqual(z.namelist(), ["conciliacao_756_3357_1111-1.xlsx"])

        for url in (f"/conciliacao/{execucao}/..%2Fmeta.json", f"/conciliacao/{execucao}/meta.json",
                    "/conciliacao/../vivencie.db", "/conciliacao/20260101_000000_abcdef012345"):
            self.assertEqual(self.c.get(url, follow_redirects=False).status_code in (303, 404), True, url)

    def test_envio_recusado_mostra_erro(self):
        r = self.enviar([("extrato.csv", b"a;b")])
        self.assertEqual(r.status_code, 400)
        self.assertIn("apenas arquivos .ofx", r.text)

    def test_pessoas_desatualizado_recusa_envio(self):
        velho = time.time() - 6 * 86400
        os.utime(self.dir / "arquivos_auxiliares" / "pessoas_cadastradas.csv", (velho, velho))
        r = self.enviar([("a.ofx", OFX["a_alfa.ofx"].encode())])
        self.assertEqual(r.status_code, 400)
        self.assertIn("desatualizado", r.text)
        self.assertIsNone(self.app.state.jobs.ultimo("conciliar"))

    def test_exige_login(self):
        c = TestClient(self.app)
        self.assertEqual(c.get("/conciliacao", follow_redirects=False).headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
