"""
selecionar_parcela_por_vencimento com Chromium real sobre páginas locais (sem rede).

As páginas seguem a estrutura que o próprio código lê (automatizador_final.py:1683-1870):
#parcelasTable, vencimento na 2ª célula da linha e botão .btnPagarParcela / [id^='btnPagar_'].
Parcela já paga = linha sem botão de pagar. Não é cópia do HTML do ACADE.

Rodar na imagem do container (tem Chromium e chromedriver), sem acesso à rede:
    docker run --rm --network none -v "$PWD":/w -w /w -e PYTHONPATH=/w/src:/w \
        railway-acade-auth-migration-4808fa-web python -m unittest tests.test_parcela_vencimento
"""
import logging
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from automatizador_final import AutomatizadorAcadeOneFINAL


def pagina(linhas) -> str:
    """linhas: [(vencimento 'DD/MM/AAAA', tem_botao)]."""
    corpo = []
    for i, (venc, botao) in enumerate(linhas, 1):
        acao = (f"<button type='button' class='btn btnPagarParcela' id='btnPagar_{i}' "
                f"onclick=\"document.body.setAttribute('data-clicado', '{venc}')\">$</button>") if botao else "Pago"
        corpo.append(f"<tr><td>{i}/{len(linhas)}</td><td>{venc}</td><td>100,00</td><td>{acao}</td></tr>")
    return ("<html><body><table id='parcelasTable'><thead><tr><th>Parc</th><th>Vencto</th><th>Valor</th>"
            "<th></th></tr></thead><tbody>" + "".join(corpo) + "</tbody></table></body></html>")


@unittest.skipUnless(shutil.which("chromium") or shutil.which("google-chrome"), "requer Chromium (rode no container)")
class SelecionarParcelaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.auto = AutomatizadorAcadeOneFINAL.__new__(AutomatizadorAcadeOneFINAL)
        cls.auto.logger, cls.auto.timeout = logging.getLogger("teste_parcela"), 10
        cls.auto.setup_driver(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.auto.driver.quit()
        cls.tmp.cleanup()

    def setUp(self):
        p = unittest.mock.patch("automatizador_final.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def selecionar(self, linhas, vencimento):
        arq = Path(self.tmp.name) / "parcelas.html"
        arq.write_text(pagina(linhas), encoding="utf-8")
        self.auto.driver.get(arq.as_uri())
        ok = self.auto.selecionar_parcela_por_vencimento(vencimento)
        return ok, self.auto.driver.find_element("tag name", "body").get_attribute("data-clicado")

    def test_parcela_ja_paga_nao_baixa_a_proxima(self):
        ok, clicado = self.selecionar([("22/04/2026", False), ("22/05/2026", True), ("22/06/2026", True)],
                                      "22042026")
        self.assertEqual((ok, clicado), (False, None))

    def test_vencimento_ausente_nao_clica_em_nada(self):
        ok, clicado = self.selecionar([("22/05/2026", True)], "22042026")
        self.assertEqual((ok, clicado), (False, None))

    def test_escolhe_a_parcela_do_vencimento_mesmo_com_anterior_em_aberto(self):
        ok, clicado = self.selecionar([("22/04/2026", True), ("22/05/2026", True), ("22/06/2026", True)],
                                      "22052026")
        self.assertEqual((ok, clicado), (True, "22/05/2026"))

    def test_parcela_unica_em_aberto(self):
        ok, clicado = self.selecionar([("22/04/2026", True)], "22042026")
        self.assertEqual((ok, clicado), (True, "22/04/2026"))

    def test_aceita_data_ja_formatada(self):
        ok, clicado = self.selecionar([("22/04/2026", False), ("22/05/2026", True)], "22/05/2026")
        self.assertEqual((ok, clicado), (True, "22/05/2026"))


if __name__ == "__main__":
    unittest.main()
