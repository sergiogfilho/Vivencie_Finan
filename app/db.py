"""SQLite da aplicação (arquivo no volume persistente)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Cada item é aplicado uma única vez, em ordem (PRAGMA user_version).
MIGRACOES = [
    """
    CREATE TABLE contas_bancarias (
        id                   INTEGER PRIMARY KEY,
        banco_codigo         TEXT NOT NULL,
        banco_nome           TEXT,
        agencia              TEXT NOT NULL,
        conta                TEXT NOT NULL,
        agencia_chave        TEXT NOT NULL,
        conta_chave          TEXT NOT NULL,
        convenio             TEXT,
        nome_cedente         TEXT,
        cnpj_cedente         TEXT,
        status               TEXT,
        acade_empreendimento TEXT,
        no_acade             INTEGER,          -- 1 presente / 0 ausente na última sincronização / NULL nunca sincronizada
        acade_visto_em       TEXT,
        ordem_importacao     INTEGER,
        criado_em            TEXT NOT NULL,
        atualizado_em        TEXT NOT NULL,
        UNIQUE (banco_codigo, agencia_chave, conta_chave)
    );
    CREATE TABLE centros_custo (
        id               INTEGER PRIMARY KEY,
        nome             TEXT NOT NULL UNIQUE,
        conta_id         INTEGER REFERENCES contas_bancarias(id) ON DELETE SET NULL,
        ordem_importacao INTEGER,
        criado_em        TEXT NOT NULL,
        atualizado_em    TEXT NOT NULL
    );
    CREATE TABLE auditoria (
        id         INTEGER PRIMARY KEY,
        quando     TEXT NOT NULL,
        usuario    TEXT NOT NULL,
        entidade   TEXT NOT NULL,
        entidade_id INTEGER,
        acao       TEXT NOT NULL,
        antes      TEXT,
        depois     TEXT
    );
    CREATE TABLE jobs (
        id           TEXT PRIMARY KEY,
        tipo         TEXT NOT NULL,
        usuario      TEXT NOT NULL,
        status       TEXT NOT NULL,
        criado_em    TEXT NOT NULL,
        iniciado_em  TEXT,
        finalizado_em TEXT,
        progresso    INTEGER,
        total        INTEGER,
        etapa        TEXT,
        resultado    TEXT,
        erro         TEXT
    );
    CREATE TABLE job_logs (
        id     INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        quando TEXT NOT NULL,
        nivel  TEXT NOT NULL,
        msg    TEXT NOT NULL
    );
    CREATE INDEX ix_job_logs ON job_logs(job_id, id);
    """,
    """
    CREATE TABLE job_pontos (
        id     INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        serie  TEXT NOT NULL,
        t      REAL NOT NULL,              -- segundos desde o início da execução
        valor  INTEGER NOT NULL,
        total  INTEGER
    );
    CREATE INDEX ix_job_pontos ON job_pontos(job_id, id);
    """,
    """
    ALTER TABLE job_pontos ADD COLUMN fim INTEGER NOT NULL DEFAULT 0;
    """,
    """
    CREATE TABLE retornos (
        id        TEXT PRIMARY KEY,               -- também é o nome da pasta em DATA_DIR/retornos
        criado_em TEXT NOT NULL,
        usuario   TEXT NOT NULL
    );
    CREATE TABLE retorno_arquivos (
        sha256     TEXT PRIMARY KEY,               -- o mesmo conteúdo não entra duas vezes
        retorno_id TEXT NOT NULL REFERENCES retornos(id) ON DELETE CASCADE,
        nome       TEXT NOT NULL,
        tamanho    INTEGER NOT NULL
    );
    CREATE TABLE retorno_execucoes (
        job_id     TEXT PRIMARY KEY,
        retorno_id TEXT NOT NULL REFERENCES retornos(id) ON DELETE CASCADE,
        modo       TEXT NOT NULL,                  -- simulacao | baixa | reprocessar
        criado_em  TEXT NOT NULL
    );
    CREATE INDEX ix_retorno_execucoes ON retorno_execucoes(retorno_id, criado_em);
    -- Um registro por pagamento concluído, gravado na hora: sobrevive a reinício no meio da baixa.
    CREATE TABLE retorno_resultados (
        id      INTEGER PRIMARY KEY,
        job_id  TEXT NOT NULL REFERENCES retorno_execucoes(job_id) ON DELETE CASCADE,
        chave   TEXT NOT NULL,
        status  TEXT NOT NULL,                     -- sucesso | erro
        detalhe TEXT,
        quando  TEXT NOT NULL
    );
    CREATE INDEX ix_retorno_resultados ON retorno_resultados(job_id, chave);
    """,
]


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.conexao() as con:
            versao = con.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRACOES[versao:], start=versao + 1):
                con.executescript(sql)
                con.execute(f"PRAGMA user_version = {i}")

    @contextmanager
    def conexao(self):
        con = sqlite3.connect(self.caminho, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def auditar(con, usuario: str, entidade: str, entidade_id, acao: str, antes=None, depois=None):
        con.execute(
            "INSERT INTO auditoria (quando, usuario, entidade, entidade_id, acao, antes, depois) VALUES (?,?,?,?,?,?,?)",
            (agora(), usuario, entidade, entidade_id, acao,
             json.dumps(antes, ensure_ascii=False) if antes is not None else None,
             json.dumps(depois, ensure_ascii=False) if depois is not None else None),
        )
