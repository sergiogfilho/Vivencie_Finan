"""
Executa o main() do CLI (src/processar_contas_pagar_cnab.py) sem alterá-lo e,
ao final de processar(), grava em JSON os pagamentos não incluídos — a mesma
lista que o CLI imprime como tabela de texto.

Uso (diretório corrente = pasta da execução, onde o gerador cria ./remessas):
  python -m app.cnab_execucao SAIDA_JSON CONTAS BANCOS PESSOAS
"""
import json
import sys


def main() -> None:
    saida_json = sys.argv.pop(1)

    import processar_contas_pagar_cnab as cli

    original = cli.ProcessadorContasPagar.processar

    def processar(self):
        resultados = original(self)
        with open(saida_json, "w", encoding="utf-8") as f:
            json.dump({"nao_incluidos": self.pagamentos_nao_incluidos,
                       "resultados": {k: [str(a) for a in v] for k, v in resultados.items()}},
                      f, ensure_ascii=False)
        return resultados

    cli.ProcessadorContasPagar.processar = processar
    cli.main()


if __name__ == "__main__":
    main()
