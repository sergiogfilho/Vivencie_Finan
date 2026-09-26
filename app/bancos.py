"""
Contas bancárias, convênios e vínculos centro de custo → conta.

Fatos que guiam o modelo (verificados em 2026-09-26):
- O convênio é atributo da conta: no bancos.csv curado, 17 pares
  (agência, conta) têm 17 convênios, sem conflito.
- O ACADE não tem o vínculo centro de custo → conta; ele é mantido aqui.
- Na sincronização com o ACADE os valores locais vencem para contas já
  existentes (decisão do usuário); o ACADE só alimenta contas novas.
- A agência do ACADE pode vir com DV ("3357-0"); a chave ignora o DV.
- O gerador CNAB lê o código do banco da coluna `banco`
  (processar_contas_pagar_cnab.py:302) e a conciliação da coluna `codigo`
  (consilia_extrato.py:777): a exportação grava o código nas duas.
"""
import re
from dataclasses import dataclass

import pandas as pd

from app.db import Banco, agora

COLUNAS_CSV = ["codigo", "empreendimentos", "nome_cedente", "banco", "agencia",
               "conta", "convenio", "status", "cnpj_cedente"]

# processar_contas_pagar_cnab.py só monta perfil Sicoob (BankProfile 756).
BANCOS_COM_CNAB = {"756"}

CAMPOS_EDITAVEIS = ("banco_codigo", "banco_nome", "agencia", "conta", "convenio",
                    "nome_cedente", "cnpj_cedente", "status")


class ErroBancos(ValueError):
    pass


def chave_agencia(agencia: str) -> str:
    return re.sub(r"\D", "", str(agencia or "").split("-")[0]).lstrip("0")


def chave_conta(conta: str) -> str:
    return re.sub(r"\D", "", str(conta or "")).lstrip("0")


def _txt(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    v = str(v).strip()
    return v or None


@dataclass
class ResumoSincronizacao:
    lidas: int
    existentes: int
    novas: list
    ausentes: list
    pendentes_convenio: list

    def como_dict(self):
        return self.__dict__


class RepositorioBancos:
    def __init__(self, db: Banco):
        self.db = db

    # ---------------------------------------------------------------- leitura
    def vazio(self) -> bool:
        with self.db.conexao() as con:
            return con.execute("SELECT COUNT(*) FROM contas_bancarias").fetchone()[0] == 0

    def listar_contas(self) -> list[dict]:
        with self.db.conexao() as con:
            linhas = con.execute("""
                SELECT c.*, (SELECT COUNT(*) FROM centros_custo cc WHERE cc.conta_id = c.id) AS qtd_centros
                FROM contas_bancarias c
                ORDER BY c.banco_codigo, c.agencia_chave, c.conta_chave
            """).fetchall()
        return [self._com_situacao(dict(l)) for l in linhas]

    def obter_conta(self, conta_id: int) -> dict | None:
        with self.db.conexao() as con:
            l = con.execute("""
                SELECT c.*, (SELECT COUNT(*) FROM centros_custo cc WHERE cc.conta_id = c.id) AS qtd_centros
                FROM contas_bancarias c WHERE c.id = ?""", (conta_id,)).fetchone()
        return self._com_situacao(dict(l)) if l else None

    def listar_centros(self, conta_id: int | None = None) -> list[dict]:
        sql = """SELECT cc.*, c.banco_codigo, c.agencia, c.conta, c.convenio
                 FROM centros_custo cc LEFT JOIN contas_bancarias c ON c.id = cc.conta_id"""
        args = ()
        if conta_id is not None:
            sql += " WHERE cc.conta_id = ?"
            args = (conta_id,)
        with self.db.conexao() as con:
            return [dict(l) for l in con.execute(sql + " ORDER BY cc.nome", args).fetchall()]

    @staticmethod
    def _com_situacao(c: dict) -> dict:
        pend = []
        if not c.get("convenio"):
            pend.append("Convênio pendente")
        if c.get("no_acade") == 0:
            pend.append("Não encontrada no ACADE")
        if not c.get("qtd_centros"):
            pend.append("Sem centro de custo vinculado")
        if c.get("banco_codigo") not in BANCOS_COM_CNAB:
            pend.append("Banco sem layout CNAB")
        c["pendencias"] = pend
        return c

    def resumo(self) -> dict:
        contas = self.listar_contas()
        centros = self.listar_centros()
        return {
            "contas": len(contas),
            "convenio_pendente": sum(1 for c in contas if not c["convenio"]),
            "ausentes_acade": sum(1 for c in contas if c["no_acade"] == 0),
            "centros": len(centros),
            "centros_sem_conta": sum(1 for cc in centros if cc["conta_id"] is None),
        }

    # ------------------------------------------------------ importar/exportar
    def importar_csv(self, df: pd.DataFrame, usuario: str) -> dict:
        """Carga inicial a partir do bancos.csv curado. Só com o banco vazio."""
        faltando = [c for c in COLUNAS_CSV if c not in df.columns]
        if faltando:
            raise ErroBancos(f"Colunas ausentes no arquivo: {', '.join(faltando)}")
        if not self.vazio():
            raise ErroBancos("Já existem contas cadastradas; a importação é só para a carga inicial.")

        ts = agora()
        contas: dict[tuple, int] = {}
        with self.db.conexao() as con:
            for ordem, r in enumerate(df.to_dict("records")):
                codigo = _txt(r["codigo"]) or _txt(r["banco"])
                if not codigo:
                    raise ErroBancos(f"Linha {ordem + 2}: código do banco vazio")
                chave = (codigo, chave_agencia(r["agencia"]), chave_conta(r["conta"]))
                campos = {k: _txt(r[k]) for k in ("nome_cedente", "cnpj_cedente", "convenio", "status")}
                if chave not in contas:
                    cur = con.execute("""
                        INSERT INTO contas_bancarias (banco_codigo, agencia, conta, agencia_chave, conta_chave,
                            convenio, nome_cedente, cnpj_cedente, status, ordem_importacao, criado_em, atualizado_em)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (codigo, _txt(r["agencia"]), _txt(r["conta"]), chave[1], chave[2], campos["convenio"],
                         campos["nome_cedente"], campos["cnpj_cedente"], campos["status"], ordem, ts, ts))
                    contas[chave] = cur.lastrowid
                else:
                    atual = dict(con.execute("SELECT * FROM contas_bancarias WHERE id=?", (contas[chave],)).fetchone())
                    divergentes = [k for k, v in campos.items() if v != atual[k]]
                    if divergentes:
                        raise ErroBancos(f"Linha {ordem + 2}: conta {r['agencia']}/{r['conta']} com valores "
                                         f"diferentes de linha anterior em: {', '.join(divergentes)}")
                nome_cc = _txt(r["empreendimentos"])
                if not nome_cc:
                    raise ErroBancos(f"Linha {ordem + 2}: empreendimento vazio")
                try:
                    con.execute("""INSERT INTO centros_custo (nome, conta_id, ordem_importacao, criado_em, atualizado_em)
                                   VALUES (?,?,?,?,?)""", (nome_cc, contas[chave], ordem, ts, ts))
                except Exception as exc:
                    raise ErroBancos(f"Linha {ordem + 2}: centro de custo '{nome_cc}' repetido") from exc
            Banco.auditar(con, usuario, "bancos", None, "importar_csv",
                          depois={"linhas": len(df), "contas": len(contas)})
        return {"linhas": len(df), "contas": len(contas)}

    def exportar_df(self) -> pd.DataFrame:
        """Mesmo formato do bancos.csv consumido pelo CNAB e pela conciliação."""
        with self.db.conexao() as con:
            linhas = con.execute("""
                SELECT c.banco_codigo, cc.nome, c.nome_cedente, c.agencia, c.conta, c.convenio, c.status, c.cnpj_cedente
                FROM centros_custo cc JOIN contas_bancarias c ON c.id = cc.conta_id
                ORDER BY cc.ordem_importacao IS NULL, cc.ordem_importacao, cc.nome
            """).fetchall()
        return pd.DataFrame(
            [{"codigo": l["banco_codigo"], "empreendimentos": l["nome"], "nome_cedente": l["nome_cedente"],
              "banco": l["banco_codigo"], "agencia": l["agencia"], "conta": l["conta"],
              "convenio": l["convenio"], "status": l["status"], "cnpj_cedente": l["cnpj_cedente"]}
             for l in linhas],
            columns=COLUNAS_CSV,
        )

    # --------------------------------------------------------------- edição
    def atualizar_conta(self, conta_id: int, campos: dict, usuario: str) -> dict:
        campos = {k: _txt(v) for k, v in campos.items() if k in CAMPOS_EDITAVEIS}
        for obrig in ("banco_codigo", "agencia", "conta"):
            if obrig in campos and not campos[obrig]:
                raise ErroBancos(f"O campo {obrig} é obrigatório.")
        if campos.get("banco_codigo") and not re.fullmatch(r"\d{3}", campos["banco_codigo"]):
            raise ErroBancos("Código do banco deve ter 3 dígitos (ex.: 756).")
        if campos.get("convenio") and not re.fullmatch(r"[0-9A-Za-z]{1,20}", campos["convenio"]):
            raise ErroBancos("Convênio deve ter até 20 caracteres alfanuméricos, sem espaços.")
        with self.db.conexao() as con:
            antes = con.execute("SELECT * FROM contas_bancarias WHERE id=?", (conta_id,)).fetchone()
            if not antes:
                raise ErroBancos("Conta não encontrada.")
            antes = dict(antes)
            novo = {**antes, **campos}
            novo["agencia_chave"] = chave_agencia(novo["agencia"])
            novo["conta_chave"] = chave_conta(novo["conta"])
            conflito = con.execute("""SELECT id FROM contas_bancarias WHERE banco_codigo=? AND agencia_chave=?
                                      AND conta_chave=? AND id<>?""",
                                   (novo["banco_codigo"], novo["agencia_chave"], novo["conta_chave"], conta_id)).fetchone()
            if conflito:
                raise ErroBancos("Já existe outra conta com este banco/agência/conta.")
            con.execute(f"""UPDATE contas_bancarias SET {', '.join(f'{k}=?' for k in (*campos, 'agencia_chave', 'conta_chave'))},
                            atualizado_em=? WHERE id=?""",
                        (*campos.values(), novo["agencia_chave"], novo["conta_chave"], agora(), conta_id))
            mudou = {k: v for k, v in campos.items() if antes.get(k) != v}
            if mudou:
                Banco.auditar(con, usuario, "conta_bancaria", conta_id, "editar",
                              antes={k: antes.get(k) for k in mudou}, depois=mudou)
        return self.obter_conta(conta_id)

    def criar_conta(self, campos: dict, usuario: str) -> int:
        campos = {k: _txt(v) for k, v in campos.items() if k in CAMPOS_EDITAVEIS}
        for obrig in ("banco_codigo", "agencia", "conta"):
            if not campos.get(obrig):
                raise ErroBancos(f"O campo {obrig} é obrigatório.")
        ts = agora()
        with self.db.conexao() as con:
            try:
                cur = con.execute("""INSERT INTO contas_bancarias (banco_codigo, banco_nome, agencia, conta, agencia_chave,
                        conta_chave, convenio, nome_cedente, cnpj_cedente, status, criado_em, atualizado_em)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (campos["banco_codigo"], campos.get("banco_nome"), campos["agencia"], campos["conta"],
                     chave_agencia(campos["agencia"]), chave_conta(campos["conta"]), campos.get("convenio"),
                     campos.get("nome_cedente"), campos.get("cnpj_cedente"), campos.get("status"), ts, ts))
            except Exception as exc:
                raise ErroBancos("Já existe uma conta com este banco/agência/conta.") from exc
            Banco.auditar(con, usuario, "conta_bancaria", cur.lastrowid, "criar", depois=campos)
            return cur.lastrowid

    def salvar_centro(self, nome: str, conta_id: int | None, usuario: str, centro_id: int | None = None) -> int:
        nome = _txt(nome)
        if not nome:
            raise ErroBancos("Informe o nome do centro de custo exatamente como aparece no relatório do ACADE.")
        ts = agora()
        with self.db.conexao() as con:
            if conta_id is not None and not con.execute("SELECT 1 FROM contas_bancarias WHERE id=?", (conta_id,)).fetchone():
                raise ErroBancos("Conta bancária inexistente.")
            try:
                if centro_id is None:
                    cur = con.execute("""INSERT INTO centros_custo (nome, conta_id, criado_em, atualizado_em)
                                         VALUES (?,?,?,?)""", (nome, conta_id, ts, ts))
                    centro_id = cur.lastrowid
                    Banco.auditar(con, usuario, "centro_custo", centro_id, "criar", depois={"nome": nome, "conta_id": conta_id})
                else:
                    antes = con.execute("SELECT nome, conta_id FROM centros_custo WHERE id=?", (centro_id,)).fetchone()
                    if not antes:
                        raise ErroBancos("Centro de custo não encontrado.")
                    con.execute("UPDATE centros_custo SET nome=?, conta_id=?, atualizado_em=? WHERE id=?",
                                (nome, conta_id, ts, centro_id))
                    Banco.auditar(con, usuario, "centro_custo", centro_id, "editar",
                                  antes=dict(antes), depois={"nome": nome, "conta_id": conta_id})
            except ErroBancos:
                raise
            except Exception as exc:
                raise ErroBancos(f"Já existe um centro de custo chamado '{nome}'.") from exc
        return centro_id

    def remover_centro(self, centro_id: int, usuario: str) -> None:
        with self.db.conexao() as con:
            antes = con.execute("SELECT nome, conta_id FROM centros_custo WHERE id=?", (centro_id,)).fetchone()
            if not antes:
                raise ErroBancos("Centro de custo não encontrado.")
            con.execute("DELETE FROM centros_custo WHERE id=?", (centro_id,))
            Banco.auditar(con, usuario, "centro_custo", centro_id, "remover", antes=dict(antes))

    # ------------------------------------------------------- sincronização
    def sincronizar_acade(self, linhas_acade: list[dict], usuario: str) -> ResumoSincronizacao:
        """
        Aplica a captura do ACADE (lista de dicts do CapturadorBancos).
        Contas existentes: nenhum campo local é alterado; só a presença no
        ACADE é registrada. Contas novas: inseridas com convênio pendente.
        Contas que sumiram do ACADE: marcadas, nunca apagadas.
        """
        ts = agora()
        vistas: set[int] = set()
        novas, existentes = [], 0
        with self.db.conexao() as con:
            for r in linhas_acade:
                codigo = _txt(r.get("codigo"))
                if not codigo:
                    continue
                ag_ch, ct_ch = chave_agencia(r.get("agencia")), chave_conta(r.get("conta"))
                atual = con.execute("""SELECT id, banco_nome FROM contas_bancarias
                                       WHERE banco_codigo=? AND agencia_chave=? AND conta_chave=?""",
                                    (codigo, ag_ch, ct_ch)).fetchone()
                if atual:
                    existentes += 1
                    vistas.add(atual["id"])
                    con.execute("""UPDATE contas_bancarias SET no_acade=1, acade_visto_em=?,
                                   acade_empreendimento=?, banco_nome=COALESCE(banco_nome, ?) WHERE id=?""",
                                (ts, _txt(r.get("empreendimentos")), _txt(r.get("banco")), atual["id"]))
                    continue
                agencia = str(r.get("agencia") or "").split("-")[0].strip()
                cnpj = re.sub(r"\D", "", str(r.get("cnpj_cedente") or "")) or None
                cur = con.execute("""
                    INSERT INTO contas_bancarias (banco_codigo, banco_nome, agencia, conta, agencia_chave, conta_chave,
                        convenio, nome_cedente, cnpj_cedente, status, acade_empreendimento, no_acade, acade_visto_em,
                        criado_em, atualizado_em)
                    VALUES (?,?,?,?,?,?,NULL,?,?,?,?,1,?,?,?)""",
                    (codigo, _txt(r.get("banco")), agencia, _txt(r.get("conta")), ag_ch, ct_ch,
                     _txt(r.get("nome_cedente")), cnpj, _txt(r.get("status")), _txt(r.get("empreendimentos")), ts, ts, ts))
                vistas.add(cur.lastrowid)
                novas.append({"id": cur.lastrowid, "banco": codigo, "agencia": agencia, "conta": _txt(r.get("conta")),
                              "cedente": _txt(r.get("nome_cedente")), "empreendimento_acade": _txt(r.get("empreendimentos"))})

            ausentes = []
            for l in con.execute("SELECT id, banco_codigo, agencia, conta, nome_cedente FROM contas_bancarias").fetchall():
                if l["id"] not in vistas:
                    con.execute("UPDATE contas_bancarias SET no_acade=0 WHERE id=?", (l["id"],))
                    ausentes.append({"id": l["id"], "banco": l["banco_codigo"], "agencia": l["agencia"],
                                     "conta": l["conta"], "cedente": l["nome_cedente"]})
            pendentes = [{"id": l["id"], "banco": l["banco_codigo"], "agencia": l["agencia"], "conta": l["conta"],
                          "cedente": l["nome_cedente"]}
                         for l in con.execute("""SELECT * FROM contas_bancarias WHERE convenio IS NULL OR convenio=''
                                                 ORDER BY banco_codigo, agencia_chave, conta_chave""").fetchall()]
            resumo = ResumoSincronizacao(len(linhas_acade), existentes, novas, ausentes, pendentes)
            Banco.auditar(con, usuario, "bancos", None, "sincronizar_acade",
                          depois={"lidas": resumo.lidas, "existentes": existentes, "novas": len(novas),
                                  "ausentes": len(ausentes)})
        return resumo
