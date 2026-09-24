# Automatizador Final – Contas à Pagar (Acade One)

Este repositório contém o script definitivo para gerar o relatório de **Contas à Pagar** no Acade One usando Selenium. O código atual já inclui:

- Login automático e navegação completa até o relatório.
- Manipulação confiável dos campos Select2 e das datas.
- Ajuste automático do Empreendimento para **Todos** antes de gerar.
- Tratamento do alerta de “senha comprometida” exibido pelo Chrome.
- Extração da tabela e salvamento em CSV ou Excel.

## Pré-requisitos

- Python 3.9 ou superior.
- Google Chrome instalado (mesma versão usada pelo servidor).
- Credenciais válidas de acesso ao Acade One.

## Instalação rápida

```bash
git clone <repositorio>
cd Vivencie
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

As dependências principais são: `selenium`, `webdriver-manager`, `pandas` e `python-dotenv`.

## Configuração do ambiente

1. Copie o arquivo de exemplo:

   ```bash
   cp .env.exemplo .env
   ```

2. Preencha suas credenciais e demais parâmetros em `.env`.

   ```env
   ACADE_USUARIO=seu_usuario
   ACADE_SENHA=sua_senha
   ACADE_BASE_URL=https://martins.acadeone.com.br
   ```

3. Opcionalmente ajuste `OUTPUT_DIR`, `DEFAULT_FORMAT`, `HEADLESS_MODE` ou `TIMEOUT_DEFAULT`.

## Execução

O script principal é `automatizador_final.py`. Ele possui uma interface interativa no terminal e também aceita parâmetros via `.env`.

```bash
python automatizador_final.py
```

Durante a execução você poderá:

- Confirmar (ou ajustar) as datas de início/fim.
- Escolher o formato de saída (CSV ou Excel).
- Definir se deseja rodar em modo visível ou headless.

Ao final, o arquivo será salvo no diretório configurado (padrão: `./relatorios`).

## Logs e depuração

- Logs são gravados em `automatizador_final.log` na raiz do projeto.
- Mensagens de nível `DEBUG` incluem detalhes de interação com Select2 e do bloqueio do alerta de senha.
- Para depurar visualmente basta executar com `HEADLESS_MODE=False` no `.env` ou responder `s` quando solicitado pelo script.

## Estrutura mínima do pacote

```
Vivencie/
├── automatizador_final.py       # Script principal (CLI + libs)
├── .env.exemplo                 # Modelo de configuração
├── requirements.txt             # Dependências para instalação
├── README.md                    # Esta documentação
├── ELEMENTOS_DESCOBERTOS.md     # Referência rápida de seletores
├── teste_ambiente.py            # (opcional) Verificação de ambiente
└── relatorios/                  # Saída dos relatórios
```

Arquivos auxiliares, versões antigas e logs históricos foram removidos para facilitar o porte para produção.

## Dicas para produção

- Ajuste o `.env` com credenciais específicas para o ambiente alvo.
- Configure um agendador (cron, Windows Task Scheduler, etc.) chamando `python automatizador_final.py` no horário desejado.
- Se o ambiente for headless (servidor), mantenha `HEADLESS_MODE=True` no `.env`.
- Monitore o arquivo `automatizador_final.log` para verificar status e possíveis falhas.

## Suporte

Em caso de mudanças na interface do Acade One, consulte `ELEMENTOS_DESCOBERTOS.md` para localizar rapidamente os seletores atuais. Para problemas adicionais registre o log e ajuste o tempo de espera (`TIMEOUT_DEFAULT`).

Bom uso! 🚀