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


class SegmentoBLayoutTest(unittest.TestCase):
    def setUp(self):
        pix_config = ModalidadeConfig(
            forma_lancamento="45",
            camara_centralizadora="009",
            versao_layout="045",
            tipo_compromisso="01",
        )
        self.bank_profile = BankProfile(
            codigo_banco_compensacao="756",
            nome_banco="BANCO SICOOB S.A.",
            versao_layout_arquivo="087",
            tipo_servico_pagamento="20",
            modalidades={
                Modalidade.PIX_CHAVE: pix_config,
            },
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
            cep="60811220",
            uf="CE",
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
            logradouro="RUA UM",
            numero_endereco="123",
            bairro="BAIRRO",
            cidade="FORTALEZA",
            cep="60000000",
            uf="CE",
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
            favorecido=favorecido,
            numero_documento="7179",
            data_vencimento="20102025",
            dados_pix=dados_pix,
            informacao_recebedor="10/2025 45712 Distratos 7179",
        )

    def test_segmento_b_mantem_layout_oficial(self):
        pagamento = self._pagamento_pix()
        linha = self.gerador.gerar_segmento_b(pagamento, lote=1, sequencial=2)
        self.assertEqual(240, len(linha))
        self.assertEqual("B", linha[13], "Segmento deve permanecer identificado como 'B'")
        self.assertEqual(
            "003",
            linha[14:17],
            "Forma de iniciação deve refletir a chave PIX do favorecido (e-mail nesta simulação)",
        )
        self.assertEqual(
            "1",
            linha[17],
            "Tipo de inscrição do favorecido deve aparecer após a forma de iniciação",
        )


class SegmentoBTEDLayoutTest(unittest.TestCase):
    """Testa o layout do Segmento B para TED (não-PIX) conforme manual Sicoob seção 7.3"""

    def setUp(self):
        ted_config = ModalidadeConfig(
            forma_lancamento="41",
            camara_centralizadora="018",
            versao_layout="045",
            tipo_compromisso="01",
        )
        self.bank_profile = BankProfile(
            codigo_banco_compensacao="756",
            nome_banco="BANCO SICOOB S.A.",
            versao_layout_arquivo="087",
            tipo_servico_pagamento="20",
            modalidades={
                Modalidade.TED: ted_config,
            },
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
            nome_empresa="EMPRESA TESTE",
            cep="60811220",
            uf="CE",
        )
        self.gerador = GeradorSegmentos(self.bank_profile, self.empresa)

    def _pagamento_ted_sem_endereco(self) -> Pagamento:
        """Cria pagamento TED sem dados de endereço (campos opcionais)"""
        favorecido = Favorecido(
            tipo_inscricao=1,
            numero_inscricao="73112810325",
            nome_favorecido="Jose Mauricio da Costa",
            banco="001",
            agencia="5929",
            dv_agencia=" ",
            conta="350",
            dv_conta="0",
            dv_agencia_conta=" ",
            # Campos de endereço vazios/nulos
            logradouro=None,
            numero_endereco=None,
            complemento=None,
            bairro=None,
            cidade=None,
            cep=None,
            uf=None,
        )
        return Pagamento(
            modalidade=Modalidade.TED,
            valor_pagamento=32199,
            data_pagamento="05012026",
            favorecido=favorecido,
            numero_documento="44191",
            data_vencimento="05012026",
        )

    def test_segmento_b_ted_layout_correto(self):
        """Verifica que o Segmento B para TED segue o layout do manual 7.3"""
        pagamento = self._pagamento_ted_sem_endereco()
        linha = self.gerador.gerar_segmento_b(pagamento, lote=2, sequencial=2)

        # Verifica tamanho total
        self.assertEqual(240, len(linha), "Linha deve ter exatamente 240 caracteres")

        # Verifica campos de controle
        self.assertEqual("756", linha[0:3], "Pos 1-3: Código Banco")
        self.assertEqual("0002", linha[3:7], "Pos 4-7: Lote")
        self.assertEqual("3", linha[7], "Pos 8: Tipo Registro")
        self.assertEqual("00002", linha[8:13], "Pos 9-13: Nº Sequencial")
        self.assertEqual("B", linha[13], "Pos 14: Segmento")

        # Verifica tipo de inscrição e CPF
        self.assertEqual("1", linha[17], "Pos 18: Tipo Inscrição (1=CPF)")
        self.assertEqual("00073112810325", linha[18:32], "Pos 19-32: CPF Favorecido")

        # Verifica campos de endereço (devem estar vazios/zerados, não com lixo)
        logradouro = linha[32:62]
        self.assertEqual(30, len(logradouro), "Logradouro deve ter 30 posições")
        self.assertTrue(logradouro.strip() == "", "Logradouro deve estar em branco quando não informado")

        numero = linha[62:67]
        self.assertEqual("00000", numero, "Pos 63-67: Número deve ser zeros quando não informado")

        complemento = linha[67:82]
        self.assertEqual(15, len(complemento), "Complemento deve ter 15 posições")
        self.assertTrue(complemento.strip() == "", "Complemento deve estar em branco")

        bairro = linha[82:97]
        self.assertEqual(15, len(bairro), "Bairro deve ter 15 posições")
        self.assertTrue(bairro.strip() == "", "Bairro deve estar em branco")

        cidade = linha[97:117]
        self.assertEqual(20, len(cidade), "Cidade deve ter 20 posições")
        self.assertTrue(cidade.strip() == "", "Cidade deve estar em branco")

        # CEP dividido em duas partes conforme manual
        cep_principal = linha[117:122]
        self.assertEqual("00000", cep_principal, "Pos 118-122: CEP deve ser zeros (5 posições)")

        cep_complemento = linha[122:125]
        self.assertEqual(3, len(cep_complemento), "Complemento CEP deve ter 3 posições")
        self.assertTrue(cep_complemento.strip() == "", "Complemento CEP deve estar em branco")

        uf = linha[125:127]
        self.assertEqual(2, len(uf), "UF deve ter 2 posições")
        self.assertTrue(uf.strip() == "", "UF deve estar em branco quando não informado")

        # Verifica campos de valores (após posição 127)
        data_vencimento = linha[127:135]
        self.assertEqual("05012026", data_vencimento, "Pos 128-135: Data Vencimento")

        # Verifica que NÃO há dados de CPF repetidos após pos 127
        resto_linha = linha[135:]
        self.assertNotIn("73112810325", resto_linha, "CPF não deve aparecer repetido após pos 135")
        self.assertNotIn("0501202600000", resto_linha, "Não deve haver data+zeros concatenados")

    def test_segmento_b_ted_nao_tem_lixo(self):
        """Verifica que não há 'lixo' (dados repetidos) no final da linha"""
        pagamento = self._pagamento_ted_sem_endereco()
        linha = self.gerador.gerar_segmento_b(pagamento, lote=2, sequencial=2)

        # Após posição 210, só deve haver campos padrão, não dados repetidos
        # Pos 211-225: Código/Doc Favorecido (alfa, pode ser branco)
        # Pos 226: Aviso (1 num)
        # Pos 227-232: Código UG (6 num)
        # Pos 233-240: CNAB (8 alfa)

        cod_favorecido = linha[210:225]
        self.assertEqual(15, len(cod_favorecido), "Código Favorecido deve ter 15 posições")

        aviso = linha[225]
        self.assertTrue(aviso.isdigit(), "Aviso deve ser numérico")

        ug_centralizadora = linha[226:232]
        self.assertEqual("000000", ug_centralizadora, "UG Centralizadora deve ser zeros")

        cnab_final = linha[232:240]
        self.assertEqual(8, len(cnab_final), "CNAB final deve ter 8 posições")
        self.assertTrue(cnab_final.strip() == "", "CNAB final deve estar em branco")


if __name__ == "__main__":
    unittest.main()
