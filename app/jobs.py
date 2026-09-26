"""
Execução de tarefas longas (capturas no ACADE, baixas) em segundo plano,
com progresso e log persistidos no SQLite para a interface acompanhar.
"""
import json
import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Iterable

from app.db import Banco, agora

logger = logging.getLogger(__name__)

EM_ANDAMENTO = ("na_fila", "executando")


class JobEmAndamento(RuntimeError):
    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id


class ContextoJob:
    def __init__(self, gerenciador: "GerenciadorJobs", job_id: str):
        self._g, self.job_id = gerenciador, job_id
        self.inicio = time.time()

    def progresso(self, atual: int | None = None, total: int | None = None, etapa: str | None = None) -> None:
        campos = {k: v for k, v in (("progresso", atual), ("total", total), ("etapa", etapa)) if v is not None}
        if campos:
            self._g._atualizar(self.job_id, **campos)

    def log(self, msg: str, nivel: str = "INFO") -> None:
        with self._g.db.conexao() as con:
            con.execute("INSERT INTO job_logs (job_id, quando, nivel, msg) VALUES (?,?,?,?)",
                        (self.job_id, agora(), nivel, str(msg)[:2000]))

    def ponto(self, serie: str, valor: int, total: int | None = None, fim: bool = False) -> None:
        """Registra uma amostra de uma série de progresso (para o gráfico); `fim` marca a série concluída."""
        with self._g.db.conexao() as con:
            con.execute("INSERT INTO job_pontos (job_id, serie, t, valor, total, fim) VALUES (?,?,?,?,?,?)",
                        (self.job_id, serie, round(time.time() - self.inicio, 1), valor, total, int(fim)))

    @contextmanager
    def capturar_logs(self, nomes_loggers: Iterable[str], ao_registrar: Callable[[logging.LogRecord], None] | None = None,
                      prefixo: str = "", prefixo_threads: str | None = None):
        """
        Encaminha para o log do job os registros emitidos por esta thread nos loggers indicados
        ou, com `prefixo_threads`, pelas threads cujo nome começa com ele (workers do próprio job).
        """
        contexto, thread_id = self, threading.get_ident()

        class _Handler(logging.Handler):
            def emit(self, record):
                if prefixo_threads is not None:
                    if not (record.threadName or "").startswith(prefixo_threads):
                        return
                elif record.thread != thread_id:
                    return
                try:
                    msg = record.getMessage().strip()
                    if msg and set(msg) != {"="}:
                        contexto.log(prefixo + msg, record.levelname)
                    if ao_registrar:
                        ao_registrar(record)
                except Exception:
                    pass

        handler = _Handler(level=logging.INFO)
        alvos = [logging.getLogger(n) for n in nomes_loggers]
        for lg in alvos:
            lg.addHandler(handler)
        try:
            yield
        finally:
            for lg in alvos:
                lg.removeHandler(handler)


class GerenciadorJobs:
    def __init__(self, db: Banco, max_paralelo: int = 2):
        self.db = db
        self._pool = ThreadPoolExecutor(max_workers=max_paralelo, thread_name_prefix="job")
        self._lock = threading.Lock()
        with db.conexao() as con:
            con.execute("""UPDATE jobs SET status='interrompido', finalizado_em=?,
                           erro='Aplicação reiniciada durante a execução' WHERE status IN ('na_fila','executando')""",
                        (agora(),))

    def iniciar(self, tipo: str, usuario: str, funcao: Callable[[ContextoJob], dict | None],
                ao_criar: Callable[[str], None] | None = None) -> str:
        """
        Enfileira `funcao`. Só uma tarefa de cada tipo por vez (dados compartilhados).
        `ao_criar(job_id)` roda antes de enfileirar; se falhar, a tarefa é marcada com erro e a exceção sobe.
        """
        with self._lock:
            with self.db.conexao() as con:
                atual = con.execute(f"SELECT id FROM jobs WHERE tipo=? AND status IN {EM_ANDAMENTO}", (tipo,)).fetchone()
                if atual:
                    raise JobEmAndamento(atual["id"])
                job_id = uuid.uuid4().hex[:12]
                con.execute("INSERT INTO jobs (id, tipo, usuario, status, criado_em, progresso) VALUES (?,?,?,?,?,0)",
                            (job_id, tipo, usuario, "na_fila", agora()))
            if ao_criar:
                try:
                    ao_criar(job_id)
                except Exception as exc:
                    self._atualizar(job_id, status="erro", finalizado_em=agora(), erro=str(exc) or exc.__class__.__name__)
                    raise
        self._pool.submit(self._executar, job_id, funcao)
        return job_id

    def _executar(self, job_id: str, funcao) -> None:
        ctx = ContextoJob(self, job_id)
        self._atualizar(job_id, status="executando", iniciado_em=agora())
        try:
            resultado = funcao(ctx)
            self._atualizar(job_id, status="concluido", finalizado_em=agora(),
                            resultado=json.dumps(resultado or {}, ensure_ascii=False, default=str))
        except Exception as exc:
            logger.exception("Job %s falhou", job_id)
            ctx.log(traceback.format_exc(limit=3), "ERROR")
            self._atualizar(job_id, status="erro", finalizado_em=agora(), erro=str(exc) or exc.__class__.__name__)

    def _atualizar(self, job_id: str, **campos) -> None:
        with self.db.conexao() as con:
            con.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in campos)} WHERE id=?", (*campos.values(), job_id))

    def obter(self, job_id: str) -> dict | None:
        with self.db.conexao() as con:
            l = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not l:
            return None
        job = dict(l)
        job["resultado"] = json.loads(job["resultado"]) if job["resultado"] else None
        return job

    def logs(self, job_id: str, depois_de: int = 0, limite: int = 500) -> list[dict]:
        with self.db.conexao() as con:
            return [dict(l) for l in con.execute(
                "SELECT id, quando, nivel, msg FROM job_logs WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                (job_id, depois_de, limite)).fetchall()]

    def pontos(self, job_id: str, depois_de: int = 0, limite: int = 2000) -> list[dict]:
        with self.db.conexao() as con:
            return [dict(l) for l in con.execute(
                "SELECT id, serie, t, valor, total, fim FROM job_pontos WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                (job_id, depois_de, limite)).fetchall()]

    def listar(self, tipo: str, limite: int = 20) -> list[dict]:
        with self.db.conexao() as con:
            ids = [l["id"] for l in con.execute("SELECT id FROM jobs WHERE tipo=? ORDER BY criado_em DESC LIMIT ?",
                                                (tipo, limite)).fetchall()]
        return [self.obter(i) for i in ids]

    def ultimo(self, tipo: str) -> dict | None:
        with self.db.conexao() as con:
            l = con.execute("SELECT id FROM jobs WHERE tipo=? ORDER BY criado_em DESC LIMIT 1", (tipo,)).fetchone()
        return self.obter(l["id"]) if l else None
