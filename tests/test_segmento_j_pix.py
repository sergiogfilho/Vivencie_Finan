import unittest

from gerador_cnab240 import (
    BankProfile,
    DadosPix,
    Empresa,
    Favorecido,
    Modalidade,
    ModalidadeConfig,
    Pagamento,
    TipoChavePix,
)
from gerador_cnab240_segmentos import GeradorSegmentos


class SegmentoJPixTest(unittest.TestCase):
    def setUp(self):
        pix_config = ModalidadeConfig(
            forma_lancamento="45",
            camara_centralizadora="009",
            tipo_compromisso="01",
        )
        self.bank_profile = BankProfile(
            codigo_banco_compensacao="756",
            nome_banco="BANCO SICOOB S.A.",
            versao_layout_arquivo="087",
            versao_layout_lote="045",
            tipo_servico_pagamento="20",
            modalidades={Modalidade.PIX_CHAVE: pix_config},
        )
        self.empresa = Empresa(
            tipo_inscricao=2,
            numero_inscricao="40725515000173",
            convenio="204226",
            agencia_mantenedora="3357",
            dv_agencia=" ",
            numero_conta="40240",
            dv_conta="0",
            dv_agencia_conta=" ",
            nome_empresa="LOTEAMENTO Vivencie Camara Aquiraz",
        )
        self.gerador = GeradorSegmentos(self.bank_profile, self.empresa)

    def _pagamento_pix(self) -> Pagamento:
        favorecido = Favorecido(
            tipo_inscricao=1,
            numero_inscricao="43195149800",
            nome_favorecido="Luis Alberto",
            banco="756",
            agencia="0001",
            dv_agencia=" ",
            conta="000000000000",
            dv_conta="0",
            dv_agencia_conta=" ",
        )
        dados_pix = DadosPix(
            tipo_chave=TipoChavePix.EMAIL,
            chave="visualservice8@gmail.com",
            descricao="10/2025 45712 Distratos 7179",
        )
        return Pagamento(
            modalidade=Modalidade.PIX_CHAVE,
            valor_pagamento=17888,
            data_pagamento="20102025",
            data_vencimento="20102025",
            favorecido=favorecido,
            numero_documento="7179",
            dados_pix=dados_pix,
        )

    def test_segmento_j_pix_usa_fallback(self):
        pagamento = self._pagamento_pix()
        linha = self.gerador.gerar_segmento_j(pagamento, lote=1, sequencial=1)

        self.assertEqual(240, len(linha))
        self.assertEqual("J", linha[13])
        self.assertTrue(linha[17:61].isdigit(), "Código de barras sintético deve ser numérico")
        self.assertEqual(pagamento.data_vencimento, linha[91:99])
        self.assertEqual(
            f"{pagamento.valor_pagamento:015d}",
            linha[99:114],
            "Valor do documento precisa refletir o pagamento em centavos",
        )
        self.assertEqual(pagamento.data_pagamento, linha[144:152])
        self.assertEqual(
            f"{pagamento.valor_pagamento:015d}",
            linha[152:167],
            "Valor do pagamento deve ocupar 15 dígitos",
        )


if __name__ == "__main__":
    unittest.main()
