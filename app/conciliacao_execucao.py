"""
Executa o CLI de conciliação (src/consilia_extrato.py) sem alterá-lo, como
tests/golden/golden_runner.py: só os caminhos PASTA_* apontam para a execução e
preparar_arquivos_auxiliares é neutralizada (a web captura as contas pagas antes).

  analisar PASTA   grava PASTA/analise.json com o período e as contas dos OFX,
                   calculados pelas mesmas funções e passos de main() (linhas 737-742).
  conciliar PASTA  roda main() e grava PASTA/resultado.json com o que o painel mostra.
                   Os xlsx ficam em PASTA/relatorios, gravados pelo próprio CLI.

Uso: python -m app.conciliacao_execucao {analisar|conciliar} PASTA
"""
import json
import math
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.bancos import chave_agencia, chave_conta

MOTIVO_SEM_CADASTRO = "Conta do extrato não cadastrada em Bancos e convênios"
MOTIVO_FORMATO = ("Conta cadastrada com agência/conta escrita diferente do extrato; o CLI compara o texto exato "
                  "(agência sem DV, conta com DV)")
MOTIVO_SEM_PAGAMENTOS = "Nenhum pagamento dos centros de custo vinculados no período"
MOTIVO_CENTRO_SEM_CONTA = "Centro de custo sem conta bancária cadastrada"
MOTIVO_CONTA_SEM_EXTRATO = "Conta do centro de custo sem extrato OFX nesta conciliação"
MOTIVO_DATA_INVALIDA = "Data de pagamento ausente ou inválida (o CLI descarta)"

# Colunas levadas ao painel (as planilhas completas continuam nos xlsx).
COLUNAS_CONCILIADOS = ["ATENÇÃO", "Centro Custo", "Beneficiado", "Pagto", "Valor", "data", "valor", "descricao",
                       "memo", "delta_valor", "match_score_pag", "Doc", "Obs."]
COLUNAS_PAGAMENTOS = ["Centro Custo", "Beneficiado", "Pagto", "Valor", "Doc", "Lancto", "Obs."]
COLUNAS_DEBITOS = ["data", "valor", "descricao", "memo"]


def _json(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return None if pd.isna(v) else v.strftime("%d/%m/%Y")
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, (int, bool, str)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        return v.item()
    return str(v)


def _linhas(df: pd.DataFrame, colunas: list[str]) -> list[dict]:
    cols = [c for c in colunas if c in df.columns]
    return [{c: _json(r[c]) for c in cols} for r in df[cols].to_dict("records")]


def _soma(serie) -> float:
    return float(sum(Decimal(str(v)) for v in serie if not pd.isna(v)))


def _modulo(pasta: Path):
    import consilia_extrato as mod

    entradas = pasta / "entradas"
    mod.PASTA_OFX = entradas / "ofx"
    mod.PASTA_AUX = entradas / "arquivos_auxiliares"
    mod.PASTA_RELATORIOS = pasta / "relatorios"
    mod.preparar_arquivos_auxiliares = lambda *a, **k: None
    return mod


def _contas(df_ofx: pd.DataFrame) -> list[dict]:
    contas = []
    for (codigo, agencia, conta), g in df_ofx.groupby(["banco_codigo", "banco_agencia", "banco_conta"]):
        deb = g[g["valor"] < 0]
        contas.append({"banco": str(codigo), "agencia": str(agencia), "conta": str(conta), "transacoes": len(g),
                       "debitos": len(deb), "valor_debitos": abs(_soma(deb["valor"]))})
    return contas


def analisar(pasta: Path) -> int:
    mod = _modulo(pasta)
    arquivos = []
    for arq in sorted(mod.PASTA_OFX.glob("*.ofx")):
        r = mod.converter_ofx_para_dataframe(str(arq))
        arquivos.append({"nome": arq.name, "valido": r is not None, "transacoes": len(r[0]) if r else 0})
    saida = {"arquivos": arquivos}
    try:
        df_ofx = mod.carregar_arquivos_ofx()
    except SystemExit:
        saida["erro"] = "Nenhum arquivo OFX válido (com extrato e transações) foi enviado."
    else:
        df_ofx["data"] = pd.to_datetime(df_ofx["data"]).dt.tz_localize(None)
        saida.update(data_min=df_ofx["data"].min().strftime("%d/%m/%Y"),
                     data_max=df_ofx["data"].max().strftime("%d/%m/%Y"), contas=_contas(df_ofx))
    (pasta / "analise.json").write_text(json.dumps(saida, ensure_ascii=False), encoding="utf-8")
    return 0


def conciliar(pasta: Path) -> int:
    mod = _modulo(pasta)
    capturado = {"grupos": []}
    orig_ofx, orig_aux, orig_conciliar = mod.carregar_arquivos_ofx, mod.carregar_arquivos_auxiliares, mod.conciliar_grupo

    def carregar_ofx():
        capturado["ofx"] = df = orig_ofx()
        return df

    def carregar_aux():
        capturado["aux"] = r = orig_aux()
        return r

    def conciliar_grupo(df_banco_grupo, df_pagamentos_grupo, df_cadastro):
        r = orig_conciliar(df_banco_grupo, df_pagamentos_grupo, df_cadastro)
        capturado["grupos"].append((df_banco_grupo, df_pagamentos_grupo, r))
        return r

    mod.carregar_arquivos_ofx, mod.carregar_arquivos_auxiliares, mod.conciliar_grupo = carregar_ofx, carregar_aux, conciliar_grupo
    try:
        mod.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    sys.stdout.flush()

    resultado = montar_resultado(mod, capturado)
    (pasta / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    return 0


def montar_resultado(mod, capturado: dict) -> dict:
    df_ofx = capturado["ofx"]
    df_pagamentos, _cadastro, df_bancos = capturado["aux"]
    processadas, indices_usados, contas, invalidos = set(), set(), [], []

    for df_banco_grupo, df_pag_grupo, (df_conc, df_pag_nao, df_deb_nao) in capturado["grupos"]:
        l0 = df_banco_grupo.iloc[0]
        chave = (str(l0["banco_codigo"]), str(l0["banco_agencia"]), str(l0["banco_conta"]))
        processadas.add(chave)
        indices_usados.update(df_pag_grupo.index)
        deb = df_banco_grupo[df_banco_grupo["valor"] < 0]
        datas = pd.to_datetime(df_pag_grupo[mod.COL_PAGTO_DATA], errors="coerce", dayfirst=True)
        for r in df_pag_grupo[datas.isna()].to_dict("records"):
            invalidos.append({**{c: _json(r.get(c)) for c in COLUNAS_PAGAMENTOS if c in r},
                              "motivo": MOTIVO_DATA_INVALIDA, "conta": chave[2]})
        atencao = df_conc["ATENÇÃO"].value_counts().to_dict() if "ATENÇÃO" in df_conc.columns else {}
        contas.append({
            "banco": chave[0], "agencia": chave[1], "conta": chave[2],
            "arquivo": f"conciliacao_{chave[0]}_{chave[1]}_{chave[2]}.xlsx",
            "empreendimentos": sorted(str(e) for e in df_pag_grupo["Centro Custo"].unique()),
            "transacoes": len(df_banco_grupo),
            "debitos": len(deb), "valor_debitos": abs(_soma(deb["valor"])),
            "conciliados": len(df_conc), "valor_conciliado": _soma(df_conc["valor"]) if len(df_conc) else 0.0,
            "debitos_nao_encontrados": len(df_deb_nao), "valor_debitos_nao_encontrados": _soma(df_deb_nao["valor"]),
            "pagtos_nao_encontrados": len(df_pag_nao), "valor_pagtos_nao_encontrados": _soma(df_pag_nao["Valor"]),
            "atencao": {str(k): int(v) for k, v in atencao.items()},
            "linhas": {
                "conciliados": _linhas(df_conc, COLUNAS_CONCILIADOS),
                "pagtos_nao_encontrados": _linhas(df_pag_nao, COLUNAS_PAGAMENTOS),
                "debitos_nao_encontrados": _linhas(df_deb_nao, COLUNAS_DEBITOS),
            },
        })

    # O que main() pula (linhas 776-798): mesmo agrupamento e mesmos filtros.
    puladas = []
    for c in _contas(df_ofx):
        chave = (c["banco"], c["agencia"], c["conta"])
        if chave in processadas:
            continue
        cadastro = df_bancos[(df_bancos["codigo"].astype(str) == chave[0]) &
                             (df_bancos["agencia"].astype(str) == chave[1]) &
                             (df_bancos["conta"].astype(str) == chave[2])]
        if cadastro.empty:
            parecidas = df_bancos[(df_bancos["codigo"].astype(str) == chave[0]) &
                                  (df_bancos["agencia"].map(chave_agencia) == chave_agencia(chave[1])) &
                                  (df_bancos["conta"].map(chave_conta) == chave_conta(chave[2]))]
            puladas.append({**c, "motivo": MOTIVO_FORMATO if len(parecidas) else MOTIVO_SEM_CADASTRO,
                            "cadastro": sorted({f"{a} / {b}" for a, b in zip(parecidas["agencia"], parecidas["conta"])}),
                            "empreendimentos": []})
        else:
            puladas.append({**c, "motivo": MOTIVO_SEM_PAGAMENTOS,
                            "empreendimentos": sorted(str(e) for e in cadastro["empreendimentos"].unique())})

    # Pagamentos do relatório que não entraram em nenhuma conta conciliada.
    empreendimentos_cadastrados = set(df_bancos["empreendimentos"])
    fora = []
    for idx, r in df_pagamentos.iterrows():
        if idx in indices_usados:
            continue
        motivo = MOTIVO_CONTA_SEM_EXTRATO if r.get("Centro Custo") in empreendimentos_cadastrados else MOTIVO_CENTRO_SEM_CONTA
        fora.append({**{c: _json(r.get(c)) for c in COLUNAS_PAGAMENTOS if c in r}, "motivo": motivo})

    totais = {k: sum(c[k] for c in contas) for k in (
        "transacoes", "debitos", "valor_debitos", "conciliados", "valor_conciliado", "debitos_nao_encontrados",
        "valor_debitos_nao_encontrados", "pagtos_nao_encontrados", "valor_pagtos_nao_encontrados")}
    atencao = {}
    for c in contas:
        for k, v in c["atencao"].items():
            atencao[k] = atencao.get(k, 0) + v
    totais["atencao"] = atencao
    totais["pagamentos_relatorio"] = len(df_pagamentos)
    totais["valor_pagamentos_relatorio"] = sum(float(mod.limpar_valor_monetario(v)) for v in df_pagamentos["Valor"])
    return {"contas": contas, "puladas": puladas, "pagamentos_fora": fora, "pagamentos_invalidos": invalidos,
            "totais": totais}


def main() -> int:
    acao, pasta = sys.argv[1], Path(sys.argv[2]).resolve()
    return {"analisar": analisar, "conciliar": conciliar}[acao](pasta)


if __name__ == "__main__":
    sys.exit(main())
