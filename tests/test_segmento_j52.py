import unittest

from gerador_cnab240 import (
    BankProfile,
    Empresa,
    Favorecido,
    Modalidade,
    ModalidadeConfig,
    Pagamento,
    TipoChavePix,
    DadosPix,
)
from gerador_cnab240_segmentos import GeradorSegmentos


class SegmentoJ52LengthTest(unittest.TestCase):
    def setUp(self):
        pix_config = ModalidadeConfig(
            forma_lancamento="01",
            camara_centralizadora="000",
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

    def _criar_pagamento_pix(self) -> Pagamento:
        favorecido = Favorecido(
            tipo_inscricao=1,
            numero_inscricao="43195149800",
            nome_favorecido="Luis Alberto Martins da Silva",
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
            txid=None,
            url_qr=None,
            payload_emv=None,
            descricao="10/2025 45712 Distratos 7179",
        )
        return Pagamento(
            modalidade=Modalidade.PIX_CHAVE,
            valor_pagamento=17888,
            data_pagamento="20102025",
            favorecido=favorecido,
            numero_documento="7179",
            data_vencimento="20102025",
            dados_pix=dados_pix,
            informacao_recebedor="10/2025 45712 Distratos 7179",
        )

    def test_segmento_j52_totaliza_240_colunas(self):
        pagamento = self._criar_pagamento_pix()
        linha = self.gerador.gerar_segmento_j52(pagamento, lote=1, sequencial=3)
        self.assertEqual(
            240,
            len(linha),
            "Segmento J52 deve ocupar exatamente 240 caracteres",
        )
        self.assertEqual("0", linha[14], "Tipo de movimento padrão deve ser zero")
        self.assertEqual("52", linha[17:19], "Indicador '52' precisa estar presente nos campos 18-19")


if __name__ == "__main__":
    unittest.main()
