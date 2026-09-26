"""
Arquivo pessoas_cadastradas.csv (cadastro de pessoas capturado do ACADE).

Fatos que guiam o módulo (verificados em 2026-09-26):
- É consumido como arquivo pelo gerador CNAB (processar_contas_pagar_cnab.py:113,
  caminho recebido por argumento) e pela conciliação
  (consilia_extrato.py:241, em arquivos_auxiliares/).
- A conciliação regenera o arquivo com mais de 4 dias e recusa com mais de 5
  (consilia_extrato.py:244-300), pela data de modificação.
- A gravação replica capturador_pessoas.main() (linhas 1285-1313); a
  equivalência byte a byte é coberta por teste.
- Decisão do usuário: se Física ou Jurídica não trouxer dados, o arquivo
  anterior é mantido (o CLI gravaria só o tipo que veio).
"""
import os
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

NOME_ARQUIVO = "pessoas_cadastradas.csv"
COLUNAS = ["Código", "Nome", "CPF/CNPJ", "Tipo"]
TIPOS = ("Física", "Jurídica")


def rotulo_tipo(tipo: str) -> str:
    """Valor da coluna Tipo gravado pelo capturador (capturador_pessoas.py:874)."""
    return f"Pessoa {tipo}"


def consolidar(dataframes: list[pd.DataFrame]) -> pd.DataFrame:
    """Mesmo tratamento de capturador_pessoas.main() antes de salvar."""
    df = pd.concat([d for d in dataframes if d is not None and not d.empty], ignore_index=True)
    if "Perfil" in df.columns:
        df = df.drop(columns=["Perfil"])
    return df


def _digitos(v: str) -> str:
    return "".join(c for c in str(v) if c.isdigit())


class ArquivoPessoas:
    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        self.caminho = self.pasta / NOME_ARQUIVO
        self.caminho_anterior = self.pasta / "pessoas_cadastradas.anterior.csv"

    def existe(self) -> bool:
        return self.caminho.exists()

    def ler(self) -> pd.DataFrame | None:
        """Lê como texto (preserva zeros à esquerda de Código e CPF/CNPJ)."""
        if not self.existe():
            return None
        return pd.read_csv(self.caminho, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    def resumo(self) -> dict | None:
        df = self.ler()
        if df is None:
            return None
        modificado = datetime.fromtimestamp(os.path.getmtime(self.caminho))
        por_tipo = df["Tipo"].value_counts().to_dict() if "Tipo" in df.columns else {}
        return {
            "total": len(df),
            "por_tipo": {t: int(por_tipo.get(rotulo_tipo(t), 0)) for t in TIPOS},
            "modificado_em": modificado,
            "idade_dias": (datetime.now() - modificado).days,
            "tamanho_kb": round(self.caminho.stat().st_size / 1024),
        }

    def gravar(self, df: pd.DataFrame) -> None:
        """Grava como o CLI (utf-8-sig, sem índice), de forma atômica, guardando a versão anterior."""
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        if self.existe():
            shutil.copy2(self.caminho, self.caminho_anterior)
        os.replace(tmp, self.caminho)

    def buscar(self, termo: str, limite: int = 100) -> tuple[list[dict], int]:
        df = self.ler()
        termo = (termo or "").strip()
        if df is None or not termo:
            return [], 0
        filtro = df["Nome"].str.contains(termo, case=False, regex=False) | (df["Código"] == termo)
        dig = _digitos(termo)
        if len(dig) >= 3:
            filtro |= df["CPF/CNPJ"].map(_digitos).str.contains(dig, regex=False)
        achados = df[filtro]
        return achados.head(limite).to_dict("records"), len(achados)


def comparar(antes: pd.DataFrame | None, depois: pd.DataFrame, exemplos: int = 25) -> dict:
    """Diferenças por (Tipo, Código) entre duas capturas."""
    if antes is None:
        return {"base": False}

    def indice(df):
        df = df.astype(str)
        return {(r["Tipo"], r["Código"]): (r["Nome"], r["CPF/CNPJ"]) for r in df[COLUNAS].to_dict("records")}

    a, d = indice(antes), indice(depois)
    novas = [k for k in d if k not in a]
    removidas = [k for k in a if k not in d]
    alteradas = [k for k in d if k in a and d[k] != a[k]]

    def item(k, origem, anterior=None):
        nome, doc = origem[k]
        r = {"tipo": k[0], "codigo": k[1], "nome": nome, "documento": doc}
        if anterior:
            r["nome_antes"], r["documento_antes"] = anterior[k]
        return r

    return {
        "base": True,
        "novas": len(novas), "removidas": len(removidas), "alteradas": len(alteradas),
        "ex_novas": [item(k, d) for k in novas[:exemplos]],
        "ex_removidas": [item(k, a) for k in removidas[:exemplos]],
        "ex_alteradas": [item(k, d, a) for k in alteradas[:exemplos]],
    }
