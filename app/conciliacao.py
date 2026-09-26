"""
Conciliação bancária: extratos OFX enviados pela web x contas pagas do ACADE.

Fatos que guiam o módulo (verificados em 2026-09-26):
- O CLI (src/consilia_extrato.py) lê `*.ofx` de PASTA_OFX em ordem de nome
  (linha 125) e os auxiliares de PASTA_AUX; grava um xlsx por conta em
  PASTA_RELATORIOS (linha 808). Ele roda sem alterações, em processo próprio,
  por app/conciliacao_execucao.py.
- Decisões do usuário: o relatório de contas pagas é sempre capturado do ACADE
  para o período mín–máx dos OFX; pessoas_cadastradas.csv com mais de 5 dias
  bloqueia a execução (mesma regra do CLI, linhas 283-297); o painel é só leitura.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

TAMANHO_MAXIMO = 5 * 1024 * 1024
MAX_ARQUIVOS = 60
IDADE_MAXIMA_PESSOAS = timedelta(days=5)
NOME_PAGAS = "relatorio_contas_pagas.csv"

_NOME_EXECUCAO = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")
_CARACTERE_INVALIDO = re.compile(r"[^A-Za-z0-9._-]+")


class ErroConciliacao(RuntimeError):
    pass


def nome_seguro(nome: str) -> str:
    """Só o nome-base, com caracteres seguros e extensão .ofx minúscula (o CLI busca '*.ofx')."""
    base = Path((nome or "").replace("\\", "/")).name
    if not base.lower().endswith(".ofx"):
        raise ErroConciliacao(f"{base or 'Arquivo sem nome'}: envie apenas arquivos .ofx.")
    base = _CARACTERE_INVALIDO.sub("_", base[:-4]).strip("._") or "extrato"
    return base + ".ofx"


def validar_envio(arquivos: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Checagem rápida no envio; a leitura completa é feita pelo CLI na tarefa."""
    if not arquivos:
        raise ErroConciliacao("Selecione ao menos um arquivo OFX.")
    if len(arquivos) > MAX_ARQUIVOS:
        raise ErroConciliacao(f"Envie no máximo {MAX_ARQUIVOS} arquivos por conciliação.")
    vistos, saida = set(), []
    for nome, conteudo in arquivos:
        seguro = nome_seguro(nome)
        if len(conteudo) > TAMANHO_MAXIMO:
            raise ErroConciliacao(f"{seguro}: maior que {TAMANHO_MAXIMO // (1024 * 1024)} MB.")
        if b"<OFX>" not in conteudo.upper():
            raise ErroConciliacao(f"{seguro}: não parece um extrato OFX.")
        if seguro.lower() in vistos:
            raise ErroConciliacao(f"{seguro}: arquivo com o mesmo nome enviado duas vezes.")
        vistos.add(seguro.lower())
        saida.append((seguro, conteudo))
    return saida


def checar_pessoas(caminho: Path, agora: datetime | None = None) -> None:
    """Mesma regra do CLI: arquivo ausente ou modificado há mais de 5 dias bloqueia."""
    if not caminho.exists():
        raise ErroConciliacao("Cadastro de pessoas ainda não capturado. Atualize as pessoas antes de conciliar.")
    modificado = datetime.fromtimestamp(caminho.stat().st_mtime)
    agora = agora or datetime.now()
    if modificado < agora - IDADE_MAXIMA_PESSOAS:
        raise ErroConciliacao(f"Cadastro de pessoas desatualizado (há {(agora - modificado).days} dias; máximo 5). "
                              "Atualize as pessoas antes de conciliar.")


class ExecucoesConciliacao:
    """Cada conciliação fica em DATA_DIR/conciliacoes/<AAAAMMDD_HHMMSS_job>/."""

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)

    def nova(self, job_id: str, usuario: str, arquivos: list[tuple[str, bytes]]) -> Path:
        destino = self.pasta / f"{datetime.now():%Y%m%d_%H%M%S}_{job_id}"
        ofx = destino / "entradas" / "ofx"
        ofx.mkdir(parents=True)
        (destino / "entradas" / "arquivos_auxiliares").mkdir()
        for nome, conteudo in arquivos:
            (ofx / nome).write_bytes(conteudo)
        self.gravar_meta(destino, {"job_id": job_id, "usuario": usuario, "criado_em": datetime.now().isoformat(timespec="seconds"),
                                   "arquivos": [n for n, _ in arquivos]})
        return destino

    @staticmethod
    def gravar_meta(pasta: Path, extra: dict) -> None:
        caminho = pasta / "meta.json"
        meta = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
        meta.update(extra)
        caminho.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def pasta_execucao(self, nome: str) -> Path | None:
        if not _NOME_EXECUCAO.match(nome or ""):
            return None
        p = self.pasta / nome
        return p if p.is_dir() else None

    def da_tarefa(self, job_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
            return None
        return next(iter(sorted(self.pasta.glob(f"*_{job_id}"))), None) if self.pasta.is_dir() else None

    @staticmethod
    def _json(caminho: Path) -> dict | None:
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def meta(self, nome: str) -> dict:
        p = self.pasta_execucao(nome)
        return (self._json(p / "meta.json") or {}) if p else {}

    def resultado(self, nome: str) -> dict | None:
        p = self.pasta_execucao(nome)
        return self._json(p / "resultado.json") if p else None

    def listar(self, limite: int = 20) -> list[dict]:
        if not self.pasta.is_dir():
            return []
        nomes = sorted((p.name for p in self.pasta.iterdir() if _NOME_EXECUCAO.match(p.name)), reverse=True)[:limite]
        return [{"nome": n, **self.meta(n), "concluida": (self.pasta / n / "resultado.json").exists()} for n in nomes]

    def relatorios(self, nome: str) -> list[Path]:
        p = self.pasta_execucao(nome)
        if not p or not (p / "relatorios").is_dir():
            return []
        return sorted(a for a in (p / "relatorios").iterdir() if a.is_file() and a.suffix == ".xlsx")

    def arquivo_relatorio(self, nome: str, arquivo: str) -> Path | None:
        """Só arquivos listados na execução (sem montar caminho a partir da URL)."""
        return next((a for a in self.relatorios(nome) if a.name == arquivo), None)
