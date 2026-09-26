"""
Harness de caracterização (golden) para provar que a migração não altera
o comportamento observável do núcleo de processamento.

Roda uma árvore de código (`--src`, ex.: o `src/` do commit de referência ou o
atual) sobre um diretório de dados reais (`--data`, com `arquivos_auxiliares/`
e `ofx_a_processar/`) e grava saídas normalizadas em `--out`. Depois `compare`
confronta duas saídas byte a byte.

Normalizações (únicos campos que variam entre execuções idênticas, verificado
em 2026-09-26 rodando o gerador duas vezes sobre os mesmos dados):
  - CNAB: data/hora de geração no header de arquivo (posições 144-157,
    1-based) e o prefixo AAAAMMDD do nome do arquivo.
  - stdout: linhas iniciadas por timestamp de log.

Os dados reais NUNCA entram no repositório; ficam fora da árvore versionada.

Uso:
  python tests/golden/golden_runner.py cnab        --src SRC --data DIR --out OUT
  python tests/golden/golden_runner.py conciliacao --src SRC --data DIR --out OUT
  python tests/golden/golden_runner.py conciliacao-web --src SRC --data DIR --out OUT
  python tests/golden/golden_runner.py compare A B

`conciliacao-web` roda o mesmo main() pelo caminho da web
(app/conciliacao_execucao.py, em processo próprio) e grava no mesmo formato.
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HEADER_TS = slice(143, 157)
LOG_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _normalizar_stdout(texto: str) -> str:
    return "\n".join(l for l in texto.splitlines() if not LOG_TS.match(l)) + "\n"


def run_cnab(src: Path, data: Path, out: Path) -> None:
    aux = data / "arquivos_auxiliares"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(
            [sys.executable, str(src / "processar_contas_pagar_cnab.py"),
             str(aux / "relatorio_contas_pagar.csv"),
             str(aux / "bancos.csv"),
             str(aux / "pessoas_cadastradas.csv")],
            cwd=cwd, capture_output=True, text=True,
        )
        (out / "exit_code.txt").write_text(f"{proc.returncode}\n")
        (out / "stdout.txt").write_text(_normalizar_stdout(proc.stdout))
        remessas = Path(cwd) / "remessas"
        destino = out / "remessas"
        destino.mkdir(exist_ok=True)
        for arq in sorted(remessas.glob("*")) if remessas.exists() else []:
            linhas = arq.read_bytes().split(b"\n")
            if linhas and len(linhas[0]) >= HEADER_TS.stop:
                h = bytearray(linhas[0])
                h[HEADER_TS] = b"#" * (HEADER_TS.stop - HEADER_TS.start)
                linhas[0] = bytes(h)
            nome = re.sub(r"^\d{8}_", "DATA_", arq.name)
            (destino / nome).write_bytes(b"\n".join(linhas))


def run_conciliacao(src: Path, data: Path, out: Path) -> None:
    import pandas as pd

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raiz:
        raiz = Path(raiz)
        shutil.copytree(data / "arquivos_auxiliares", raiz / "arquivos_auxiliares")
        shutil.copytree(data / "ofx_a_processar", raiz / "ofx_a_processar")

        sys.path.insert(0, str(src))
        spec = importlib.util.spec_from_file_location("consilia_extrato_golden", src / "consilia_extrato.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.PASTA_OFX = raiz / "ofx_a_processar"
        mod.PASTA_AUX = raiz / "arquivos_auxiliares"
        mod.PASTA_RELATORIOS = raiz / "relatorios"
        # Única etapa neutralizada: ela pode disparar captura no ACADE.
        mod.preparar_arquivos_auxiliares = lambda *a, **k: None

        cwd = os.getcwd()
        os.chdir(raiz)
        try:
            from contextlib import redirect_stdout
            import io
            buf = io.StringIO()
            with redirect_stdout(buf):
                mod.main()
        finally:
            os.chdir(cwd)
            sys.path.remove(str(src))
        texto = buf.getvalue()
        for variante in {str(raiz), str(raiz.resolve())}:
            texto = texto.replace(variante, "<RAIZ>")
        (out / "stdout.txt").write_text(_normalizar_stdout(texto))

        for xlsx in sorted((raiz / "relatorios").glob("*.xlsx")):
            for aba, df in pd.read_excel(xlsx, sheet_name=None, header=None, dtype=str).items():
                df.to_csv(out / f"{xlsx.stem}__{aba}.csv", index=False)


def _dump_xlsx(relatorios: Path, out: Path) -> None:
    import pandas as pd

    for xlsx in sorted(relatorios.glob("*.xlsx")):
        for aba, df in pd.read_excel(xlsx, sheet_name=None, header=None, dtype=str).items():
            df.to_csv(out / f"{xlsx.stem}__{aba}.csv", index=False)


def run_conciliacao_web(src: Path, data: Path, out: Path) -> None:
    raiz_repo = Path(__file__).resolve().parent.parent.parent
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        shutil.copytree(data / "arquivos_auxiliares", pasta / "entradas" / "arquivos_auxiliares")
        shutil.copytree(data / "ofx_a_processar", pasta / "entradas" / "ofx")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(src), str(raiz_repo)]), PYTHONUTF8="1")
        proc = subprocess.run([sys.executable, "-m", "app.conciliacao_execucao", "conciliar", str(pasta)],
                              cwd=pasta, env=env, capture_output=True, text=True)
        texto = proc.stdout
        for variante in {str(pasta), str(pasta.resolve())}:
            # Única diferença de layout: os OFX ficam em entradas/ofx em vez de ofx_a_processar.
            texto = texto.replace(f"{variante}/entradas/ofx", "<RAIZ>/ofx_a_processar").replace(variante, "<RAIZ>")
        (out / "stdout.txt").write_text(_normalizar_stdout(texto))
        if proc.returncode != 0:
            (out / "stderr.txt").write_text(proc.stderr)
        _dump_xlsx(pasta / "relatorios", out)


def compare(a: Path, b: Path) -> int:
    arqs_a = {p.relative_to(a) for p in a.rglob("*") if p.is_file()}
    arqs_b = {p.relative_to(b) for p in b.rglob("*") if p.is_file()}
    diferencas = [f"só em A: {n}" for n in sorted(arqs_a - arqs_b)]
    diferencas += [f"só em B: {n}" for n in sorted(arqs_b - arqs_a)]
    diferencas += [f"conteúdo difere: {n}" for n in sorted(arqs_a & arqs_b)
                   if (a / n).read_bytes() != (b / n).read_bytes()]
    if diferencas:
        print("\n".join(diferencas))
        return 1
    print(f"IDÊNTICO ({len(arqs_a)} arquivos): {a} == {b}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for nome in ("cnab", "conciliacao", "conciliacao-web"):
        s = sp.add_parser(nome)
        s.add_argument("--src", type=Path, required=True)
        s.add_argument("--data", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
    c = sp.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    args = p.parse_args()

    if args.cmd == "cnab":
        run_cnab(args.src.resolve(), args.data.resolve(), args.out.resolve())
    elif args.cmd == "conciliacao":
        run_conciliacao(args.src.resolve(), args.data.resolve(), args.out.resolve())
    elif args.cmd == "conciliacao-web":
        run_conciliacao_web(args.src.resolve(), args.data.resolve(), args.out.resolve())
    else:
        return compare(args.a, args.b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
