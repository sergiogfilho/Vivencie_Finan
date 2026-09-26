"""
Retornos CNAB 240 (.RET) do banco e baixa dos títulos pagos no ACADE.

Fatos que guiam o módulo (verificados em 2026-09-26):
- A leitura é a do CLI, sem alteração: ParserCNAB240Retorno
  (automatizador_final.py:58-288). Só pagamentos com ocorrência '00' vão para
  a baixa; os demais são "não confirmados". Segmentos A, J (exceto J-52) e O.
- A baixa usa, de cada pagamento, seu_numero (Lancto), data_pagamento
  (vencimento da parcela), data_real (data do pagamento/débito) e
  agência/conta do header de lote (automatizador_final.py:2141-2147); a conta
  movimento é procurada no ACADE como "agência conta" sem zeros à esquerda
  (automatizador_final.py:2019-2021).
- Header de arquivo, posição 143: '1' = remessa, '2' = retorno
  (manual_cnab240.txt:598-600 e 8189-8196).
- Decisões do usuário: o envio só mostra a prévia (a baixa começa por ação
  explícita); arquivo com o mesmo conteúdo de um já enviado é recusado;
  a simulação não preenche nem salva o modal de pagamento; "reprocessar"
  executa só os pagamentos que ainda não tiveram baixa com sucesso.
"""
import hashlib
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.bancos import chave_agencia, chave_conta
from app.db import Banco, agora

TAMANHO_MAXIMO = 10 * 1024 * 1024
MODOS = {"simulacao": "Simulação da baixa", "baixa": "Baixa no ACADE", "reprocessar": "Reprocessar pendências"}
MODOS_REAIS = ("baixa", "reprocessar")

# Mesma tradução de salvar_nao_processados (baixar_contas_pagas.py:73-80); a igualdade é coberta por teste.
OCORRENCIAS = {
    "AJ": "Conflito Informes",
    "BD": "Inclusão Efetuada com Sucesso",
    "PD": "Transação Pendente de Assinatura",
    "BF": "Transação Rejeitada",
    "PG": "PIX chave # favorecido",
    "PJ": "Chave não cadastrada",
}

_ID = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")
_JOB = re.compile(r"^[0-9a-f]{12}$")


class ErroRetorno(RuntimeError):
    pass


class ArquivoRepetido(ErroRetorno):
    def __init__(self, nome: str, retorno_id: str, nome_anterior: str):
        super().__init__(f"O arquivo {nome} já foi enviado (mesmo conteúdo de {nome_anterior}).")
        self.nome, self.retorno_id = nome, retorno_id


def valor_cnab(v) -> float:
    """Como salvar_nao_processados converte (baixar_contas_pagas.py:106-114): inteiro com 2 decimais implícitos."""
    try:
        texto = str(v or "").strip()
        return float(texto) / 100.0 if texto else 0.0
    except ValueError:
        return 0.0


def data_br(ddmmaaaa: str) -> str:
    d = (ddmmaaaa or "").strip()
    return f"{d[0:2]}/{d[2:4]}/{d[4:8]}" if len(d) == 8 and d.isdigit() else "—"


def termo_conta_movimento(agencia: str, conta: str) -> str:
    """O que a baixa digita no campo Conta Movimento (automatizador_final.py:2019-2021)."""
    return f"{agencia.strip().lstrip('0')} {conta.strip().lstrip('0')}"


def nome_seguro(nome: str) -> str:
    base = Path(nome or "").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120] or "retorno.RET"


def validar_conteudo(nome: str, conteudo: bytes) -> None:
    """Recusa o que claramente não é retorno CNAB 240; a leitura em si é a do parser do CLI."""
    if not conteudo:
        raise ErroRetorno(f"{nome}: arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroRetorno(f"{nome}: maior que {TAMANHO_MAXIMO // (1024 * 1024)} MB.")
    primeira = conteudo.decode("latin-1").splitlines()[0].rstrip("\r\n")
    if len(primeira) < 240 or primeira[7:8] != "0":
        raise ErroRetorno(f"{nome}: não parece um arquivo CNAB 240 (a primeira linha deve ser o header de arquivo, "
                          "com 240 posições).")
    if primeira[142:143] == "1":
        raise ErroRetorno(f"{nome}: é um arquivo de REMESSA (posição 143 = 1). Envie o arquivo de retorno do banco.")


def ler_arquivo(caminho: Path, parser_cls=None) -> dict:
    """Lê um .RET com o parser do CLI e acrescenta a cada pagamento a chave usada no acompanhamento."""
    if parser_cls is None:
        from automatizador_final import ParserCNAB240Retorno as parser_cls

    parser = parser_cls(str(caminho))
    confirmados, nao_confirmados = parser.processar_arquivo()
    linhas = parser.ler_arquivo()
    header = parser.parse_header_arquivo(linhas[0]) if linhas else None
    lotes = [l for l in (parser.parse_header_lote(x) for x in linhas if len(x) > 8 and x[7:8] == "1") if l]
    primeira = linhas[0] if linhas else ""
    for p in confirmados + nao_confirmados:
        p["arquivo"] = caminho.name
        p["chave"] = f"{caminho.name}#{p.get('lote', '')}#{p.get('sequencial', '')}"
    return {
        "arquivo": caminho.name,
        "banco": (header or {}).get("codigo_banco", ""),
        "nome_banco": (header or {}).get("nome_banco", ""),
        "codigo_remessa_retorno": primeira[142:143],
        "gerado_em": data_br(primeira[143:151]),
        "lotes": lotes,
        "confirmados": confirmados,
        "nao_confirmados": nao_confirmados,
    }


def item_exibicao(p: dict) -> dict:
    ocorrencias = p.get("ocorrencias", [])
    return {
        "chave": p["chave"], "arquivo": p["arquivo"], "segmento": p.get("segmento", ""),
        "documento": (p.get("seu_numero") or "").strip(), "nome": p.get("nome_favorecido", ""),
        "vencimento": data_br(p.get("data_pagamento", "")), "data_real": data_br(p.get("data_real", "")),
        "valor": valor_cnab(p.get("valor_pagamento")),
        "agencia": p.get("agencia", ""), "conta": p.get("conta", ""),
        "conta_movimento": termo_conta_movimento(p.get("agencia", ""), p.get("conta", "")),
        "ocorrencias": [{"codigo": c, "texto": OCORRENCIAS.get(c, c)} for c in ocorrencias],
    }


class RepositorioRetornos:
    """Arquivos em DATA_DIR/retornos/<id>/entradas; execuções e resultados por pagamento no SQLite."""

    def __init__(self, db: Banco, pasta: Path, parser_cls=None):
        self.db, self.pasta, self.parser_cls = db, Path(pasta), parser_cls

    # ------------------------------------------------------------ arquivos
    def registrar(self, usuario: str, arquivos: list[tuple[str, bytes]]) -> str:
        if not arquivos:
            raise ErroRetorno("Selecione ao menos um arquivo .RET.")
        vistos, preparados = {}, []
        for nome, conteudo in arquivos:
            validar_conteudo(nome, conteudo)
            sha = hashlib.sha256(conteudo).hexdigest()
            if sha in vistos:
                raise ErroRetorno(f"{nome} e {vistos[sha]} têm o mesmo conteúdo; envie cada arquivo uma vez.")
            vistos[sha] = nome
            preparados.append((nome, conteudo, sha))

        retorno_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        entradas = self.pasta / retorno_id / "entradas"
        with self.db.conexao() as con:
            for nome, _, sha in preparados:
                ant = con.execute("SELECT retorno_id, nome FROM retorno_arquivos WHERE sha256=?", (sha,)).fetchone()
                if ant:
                    raise ArquivoRepetido(nome, ant["retorno_id"], ant["nome"])
            con.execute("INSERT INTO retornos (id, criado_em, usuario) VALUES (?,?,?)", (retorno_id, agora(), usuario))
            entradas.mkdir(parents=True)
            for i, (nome, conteudo, sha) in enumerate(preparados, 1):
                destino = entradas / f"{i:02d}_{nome_seguro(nome)}"
                destino.write_bytes(conteudo)
                con.execute("INSERT INTO retorno_arquivos (sha256, retorno_id, nome, tamanho) VALUES (?,?,?,?)",
                            (sha, retorno_id, destino.name, len(conteudo)))
        try:
            previa = self.ler(retorno_id)
        except Exception as exc:
            self.remover(retorno_id)
            raise ErroRetorno(f"Não foi possível ler o arquivo: {exc}") from exc
        if not previa["total"]:
            self.remover(retorno_id)
            raise ErroRetorno("Nenhum pagamento (segmento A, J ou O) encontrado no arquivo.")
        return retorno_id

    def remover(self, retorno_id: str) -> None:
        """Só para desfazer um envio recusado na validação (nenhuma execução associada)."""
        with self.db.conexao() as con:
            con.execute("DELETE FROM retornos WHERE id=?", (retorno_id,))
        shutil.rmtree(self.pasta / retorno_id, ignore_errors=True)

    def obter(self, retorno_id: str) -> dict | None:
        if not _ID.match(retorno_id or ""):
            return None
        with self.db.conexao() as con:
            r = con.execute("SELECT * FROM retornos WHERE id=?", (retorno_id,)).fetchone()
            if not r:
                return None
            arqs = [dict(a) for a in con.execute(
                "SELECT nome, tamanho, sha256 FROM retorno_arquivos WHERE retorno_id=? ORDER BY nome", (retorno_id,))]
        return {**dict(r), "arquivos": arqs}

    def caminhos(self, retorno_id: str) -> list[Path]:
        r = self.obter(retorno_id)
        return [self.pasta / retorno_id / "entradas" / a["nome"] for a in r["arquivos"]] if r else []

    def arquivo_entrada(self, retorno_id: str, nome: str) -> Path | None:
        return next((c for c in self.caminhos(retorno_id) if c.name == nome), None)

    def ler(self, retorno_id: str) -> dict:
        arquivos = [ler_arquivo(c, self.parser_cls) for c in self.caminhos(retorno_id)]
        confirmados = [p for a in arquivos for p in a["confirmados"]]
        nao_confirmados = [p for a in arquivos for p in a["nao_confirmados"]]
        return {
            "arquivos": arquivos,
            "confirmados": confirmados,
            "nao_confirmados": nao_confirmados,
            "total": len(confirmados) + len(nao_confirmados),
            "valor_confirmados": sum(valor_cnab(p.get("valor_pagamento")) for p in confirmados),
            "valor_nao_confirmados": sum(valor_cnab(p.get("valor_pagamento")) for p in nao_confirmados),
        }

    def listar(self, limite: int = 30) -> list[dict]:
        with self.db.conexao() as con:
            ids = [r["id"] for r in con.execute("SELECT id FROM retornos ORDER BY criado_em DESC, id DESC LIMIT ?",
                                                (limite,))]
        return [self.obter(i) for i in ids]

    # ----------------------------------------------------------- execuções
    def execucoes(self, retorno_id: str) -> list[dict]:
        with self.db.conexao() as con:
            return [dict(e) for e in con.execute(
                "SELECT job_id, modo, criado_em FROM retorno_execucoes WHERE retorno_id=? ORDER BY criado_em, job_id",
                (retorno_id,))]

    def retorno_da_execucao(self, job_id: str) -> dict | None:
        with self.db.conexao() as con:
            e = con.execute("SELECT retorno_id, modo FROM retorno_execucoes WHERE job_id=?", (job_id,)).fetchone()
        return dict(e) if e else None

    def teve_baixa(self, retorno_id: str, exceto: str | None = None) -> bool:
        with self.db.conexao() as con:
            return con.execute(
                f"SELECT 1 FROM retorno_execucoes WHERE retorno_id=? AND modo IN {MODOS_REAIS} AND job_id<>?",
                (retorno_id, exceto or "")).fetchone() is not None

    def resolvidos(self, retorno_id: str) -> set[str]:
        """
        Pagamentos que o reprocessamento não repete: baixa confirmada, ou resultado desconhecido
        (o navegador parou no meio; a baixa pode ter sido salva). Simulações não contam.
        """
        with self.db.conexao() as con:
            return {r["chave"] for r in con.execute(
                f"""SELECT r.chave FROM retorno_resultados r JOIN retorno_execucoes e ON e.job_id = r.job_id
                    WHERE e.retorno_id=? AND e.modo IN {MODOS_REAIS} AND r.status IN ('sucesso', 'desconhecido')""",
                (retorno_id,))}

    def ultimo_estado(self, retorno_id: str, reais: bool) -> dict[str, dict]:
        """Último resultado de cada pagamento nas execuções reais (ou nas simulações)."""
        modos = MODOS_REAIS if reais else ("simulacao",)
        with self.db.conexao() as con:
            return {r["chave"]: dict(r) for r in con.execute(
                f"""SELECT r.chave, r.status, r.detalhe, r.quando, r.job_id FROM retorno_resultados r
                    JOIN retorno_execucoes e ON e.job_id = r.job_id
                    WHERE e.retorno_id=? AND e.modo IN ({",".join("?" * len(modos))}) ORDER BY r.id""",
                (retorno_id, *modos))}

    def resultados(self, job_id: str) -> dict[str, dict]:
        with self.db.conexao() as con:
            return {r["chave"]: dict(r) for r in con.execute(
                "SELECT chave, status, detalhe, quando FROM retorno_resultados WHERE job_id=? ORDER BY id", (job_id,))}

    def iniciar_execucao(self, job_id: str, retorno_id: str, modo: str) -> None:
        """
        Registra a execução se o modo for permitido agora. A rota registra ao enfileirar (fila única de
        baixas); a tarefa chama de novo ao começar, o que só confere o registro já feito.
        """
        with self.db.conexao() as con:
            feito = con.execute("SELECT retorno_id, modo FROM retorno_execucoes WHERE job_id=?", (job_id,)).fetchone()
        if feito:
            if (feito["retorno_id"], feito["modo"]) != (retorno_id, modo):
                raise ErroRetorno("Execução registrada com outro retorno ou modo.")
            return
        if modo not in MODOS:
            raise ErroRetorno("Modo de execução inválido.")
        if not self.obter(retorno_id):
            raise ErroRetorno("Retorno não encontrado.")
        teve_baixa = self.teve_baixa(retorno_id, exceto=job_id)
        if modo in ("simulacao", "baixa") and teve_baixa:
            raise ErroRetorno("Este retorno já teve baixa no ACADE; só é possível reprocessar as pendências.")
        if modo == "reprocessar" and not teve_baixa:
            raise ErroRetorno("Ainda não houve baixa deste retorno; use Executar baixa.")
        with self.db.conexao() as con:
            con.execute("INSERT INTO retorno_execucoes (job_id, retorno_id, modo, criado_em) VALUES (?,?,?,?)",
                        (job_id, retorno_id, modo, agora()))

    def registrar_resultado(self, job_id: str, chave: str, status: str, detalhe: str = "") -> None:
        with self.db.conexao() as con:
            con.execute("INSERT INTO retorno_resultados (job_id, chave, status, detalhe, quando) VALUES (?,?,?,?,?)",
                        (job_id, chave, status, (detalhe or "")[:500], agora()))

    def pasta_execucao(self, retorno_id: str, job_id: str) -> Path:
        p = self.pasta / retorno_id / "execucoes" / job_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def relatorios(self, retorno_id: str, job_id: str) -> list[Path]:
        if not _ID.match(retorno_id or "") or not _JOB.match(job_id or ""):
            return []
        p = self.pasta / retorno_id / "execucoes" / job_id
        return sorted(a for a in p.iterdir() if a.is_file() and a.suffix == ".txt") if p.is_dir() else []

    def arquivo_relatorio(self, retorno_id: str, job_id: str, nome: str) -> Path | None:
        return next((a for a in self.relatorios(retorno_id, job_id) if a.name == nome), None)
