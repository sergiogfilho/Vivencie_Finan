"""
Remessas CNAB 240: relatório de contas a pagar, validação contra o cadastro
de bancos e execuções do gerador.

Fatos que guiam o módulo (verificados em 2026-09-26):
- O relatório é gravado como em AutomatizadorAcadeOneFINAL.salvar_relatorio
  (automatizador_final.py:1255: utf-8-sig, sem índice); a equivalência byte a
  byte é coberta por teste.
- O gerador liga título → conta por igualdade exata entre `Centro Custo` e
  `empreendimentos` (merge left, processar_contas_pagar_cnab.py:273-279) e
  descarta em silêncio os títulos sem conta (dropna, linha 282). A validação
  aqui repete o mesmo merge para listar esses títulos.
- Decisões do usuário: título cujo centro de custo não tem conta, cuja conta
  não tem convênio ou é de banco sem layout CNAB (≠ 756) é excluído e
  informado em destaque; os demais seguem normalmente. A exclusão é feita
  retirando essas contas do bancos.csv entregue ao gerador, o que as leva ao
  mesmo descarte do CLI; nenhum código de src/ é alterado.
- O gerador grava as remessas em ./remessas relativo ao diretório corrente
  (gerador_cnab240_motor.py:461); por isso cada execução roda em processo
  próprio, com diretório corrente na pasta da execução.
"""
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.bancos import BANCOS_COM_CNAB

NOME_RELATORIO = "relatorio_contas_pagar.csv"
COLUNAS_BANCOS_GERADOR = ["empreendimentos", "conta", "agencia", "banco", "convenio", "cnpj_cedente", "nome_cedente"]

MOTIVO_SEM_CONTA = "Centro de custo sem conta bancária cadastrada"
MOTIVO_SEM_CONVENIO = "Conta bancária sem convênio"
MOTIVO_SEM_LAYOUT = "Banco sem layout CNAB (só Sicoob 756)"

_NOME_EXECUCAO = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")


def valor_numerico(v) -> float:
    """Mesma conversão de ProcessadorContasPagar.carregar_dados (processar_contas_pagar_cnab.py:83-96)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    texto = str(v).strip()
    if not texto or texto.lower() in {"nan", "none"}:
        return 0.0
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _vazio(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def _txt(v) -> str:
    return "" if _vazio(v) else str(v)


# --------------------------------------------------------------- relatório
class ArquivoContasPagar:
    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        self.caminho = self.pasta / NOME_RELATORIO
        self.caminho_anterior = self.pasta / "relatorio_contas_pagar.anterior.csv"
        self.caminho_meta = self.pasta / "relatorio_contas_pagar.meta.json"

    def existe(self) -> bool:
        return self.caminho.exists()

    def ler(self) -> pd.DataFrame | None:
        """Lê como o gerador lê (processar_contas_pagar_cnab.py:79-83)."""
        if not self.existe():
            return None
        return pd.read_csv(self.caminho, encoding="utf-8", dtype=str)

    def meta(self) -> dict:
        try:
            return json.loads(self.caminho_meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def resumo(self) -> dict | None:
        df = self.ler()
        if df is None:
            return None
        modificado = datetime.fromtimestamp(os.path.getmtime(self.caminho))
        total = sum(valor_numerico(v) for v in df["Valor"]) if "Valor" in df.columns else 0.0
        return {"titulos": len(df), "valor": total, "modificado_em": modificado, **self.meta()}

    def gravar(self, df: pd.DataFrame, meta: dict) -> None:
        """Grava como o CLI (utf-8-sig, sem índice), de forma atômica, guardando a versão anterior."""
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        if self.existe():
            shutil.copy2(self.caminho, self.caminho_anterior)
        os.replace(tmp, self.caminho)
        self.caminho_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------- validação
def ler_bancos_csv(caminho: Path) -> pd.DataFrame:
    """Lê como o gerador lê (processar_contas_pagar_cnab.py:101-105)."""
    return pd.read_csv(caminho, encoding="utf-8", dtype=str)


def conta_apta(linha) -> bool:
    return not _vazio(linha.get("convenio")) and _txt(linha.get("banco")).strip() in BANCOS_COM_CNAB


def bancos_para_gerador(df_bancos: pd.DataFrame) -> pd.DataFrame:
    """Só as linhas cujas contas podem gerar remessa; as demais caem no descarte do gerador."""
    return df_bancos[[conta_apta(r) for r in df_bancos.to_dict("records")]]


def diagnosticar(df_contas: pd.DataFrame, df_bancos: pd.DataFrame) -> dict:
    """
    Classifica cada título pelo mesmo merge do gerador. `df_bancos` é o cadastro
    completo (antes do filtro), lido como o gerador lê.
    """
    m = df_contas.merge(df_bancos[COLUNAS_BANCOS_GERADOR], left_on="Centro Custo",
                        right_on="empreendimentos", how="left")
    if len(m) != len(df_contas):
        raise ValueError("Centro de custo repetido no cadastro de bancos; corrija antes de gerar.")

    excluidos, contas = [], {}
    for r in m.to_dict("records"):
        if _vazio(r.get("conta")) or _vazio(r.get("agencia")):
            motivo = MOTIVO_SEM_CONTA
        elif _vazio(r.get("convenio")):
            motivo = MOTIVO_SEM_CONVENIO
        elif _txt(r.get("banco")).strip() not in BANCOS_COM_CNAB:
            motivo = MOTIVO_SEM_LAYOUT
        else:
            motivo = None
        valor = valor_numerico(r.get("Valor"))
        if motivo:
            excluidos.append({
                "centro_custo": _txt(r.get("Centro Custo")), "vencto": _txt(r.get("Vencto")),
                "lancto": _txt(r.get("Lancto")), "doc": _txt(r.get("Doc")), "beneficiado": _txt(r.get("Beneficiado")),
                "parc": _txt(r.get("Parc")), "valor": valor, "motivo": motivo,
                "banco": _txt(r.get("banco")), "agencia": _txt(r.get("agencia")), "conta": _txt(r.get("conta")),
            })
            continue
        chave = (str(r["conta"]), str(r["agencia"]))
        c = contas.setdefault(chave, {"conta": chave[0], "agencia": chave[1], "titulos": 0, "valor": 0.0,
                                      "centros": []})
        c["titulos"] += 1
        c["valor"] += valor
        if r["Centro Custo"] not in c["centros"]:
            c["centros"].append(r["Centro Custo"])

    por_motivo = {}
    for e in excluidos:
        p = por_motivo.setdefault(e["motivo"], {"titulos": 0, "valor": 0.0, "centros": []})
        p["titulos"] += 1
        p["valor"] += e["valor"]
        if e["centro_custo"] not in p["centros"]:
            p["centros"].append(e["centro_custo"])
    return {
        "titulos": len(df_contas),
        "valor": sum(valor_numerico(v) for v in df_contas["Valor"]),
        "aptos": sum(c["titulos"] for c in contas.values()),
        "contas": [contas[k] for k in sorted(contas)],
        "excluidos": excluidos,
        "por_motivo": por_motivo,
    }


# ------------------------------------------------------------ arquivo CNAB
def analisar_remessa(caminho: Path) -> dict:
    """
    Conta pagamentos e soma valores de um arquivo gerado. Pagamento = segmento
    A, O ou J (exceto o J-52, registro opcional '52' nas posições 18-19);
    valor = soma dos trailers de lote (posições 24-41, 2 decimais), como
    gravados por gerar_trailer_lote (gerador_cnab240_segmentos.py:834).
    """
    pagamentos, centavos, lotes = 0, 0, 0
    for linha in caminho.read_text(encoding="latin-1").splitlines():
        if len(linha) < 41:
            continue
        tipo = linha[7]
        if tipo == "3":
            seg = linha[13]
            if seg in "AO" or (seg == "J" and linha[17:19] != "52"):
                pagamentos += 1
        elif tipo == "5":
            lotes += 1
            centavos += int(linha[23:41])
    return {"pagamentos": pagamentos, "valor": centavos / 100, "lotes": lotes}


# --------------------------------------------------------------- execuções
class Execucoes:
    """Cada geração fica em DATA_DIR/remessas/<AAAAMMDD_HHMMSS_job>/ com entradas, remessas e saídas."""

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)

    def nova(self, job_id: str) -> Path:
        nome = f"{datetime.now():%Y%m%d_%H%M%S}_{job_id}"
        destino = self.pasta / nome
        (destino / "entradas").mkdir(parents=True)
        return destino

    def pasta_execucao(self, nome: str) -> Path | None:
        if not _NOME_EXECUCAO.match(nome or ""):
            return None
        p = self.pasta / nome
        return p if p.is_dir() else None

    def remessas(self, nome: str) -> list[Path]:
        p = self.pasta_execucao(nome)
        if not p or not (p / "remessas").is_dir():
            return []
        return sorted(a for a in (p / "remessas").iterdir() if a.is_file() and a.suffix == ".txt")

    def arquivo_remessa(self, nome: str, arquivo: str) -> Path | None:
        """Só arquivos listados na execução (sem montar caminho a partir da URL)."""
        return next((a for a in self.remessas(nome) if a.name == arquivo), None)
