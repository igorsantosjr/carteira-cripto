#!/usr/bin/env python3
"""
Guarda o carteira.json cifrado dentro do repositorio publico.

O repositorio pode ser publico porque os numeros (quantidades, precos medios,
patrimonio, dividas, margem) ficam num blob AES-256-GCM. O que e publico e
apenas a estrutura: scripts, workflow e o historico de precos do BTC, que e
dado de mercado e nao diz nada sobre a carteira.

Uso:
    SENHA_PAINEL="..." python3 scripts/cofre.py abrir  data/estado.enc data/carteira.json
    SENHA_PAINEL="..." python3 scripts/cofre.py fechar data/carteira.json data/estado.enc

Para editar a carteira na sua maquina:
    export SENHA_PAINEL="sua frase"
    python3 scripts/cofre.py abrir data/estado.enc data/carteira.json
    # edite data/carteira.json normalmente
    python3 scripts/cofre.py fechar data/carteira.json data/estado.enc
    rm data/carteira.json          # nunca commite o arquivo aberto
    git add data/estado.enc && git commit -m "carteira atualizada" && git push
"""

import base64
import json
import os
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERACOES = 600_000


def _chave(senha: str, sal: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                      salt=sal, iterations=ITERACOES).derive(senha.encode("utf-8"))


def fechar(texto: str, senha: str) -> str:
    sal = os.urandom(16)
    iv = os.urandom(12)
    blob = AESGCM(_chave(senha, sal)).encrypt(iv, texto.encode("utf-8"), None)
    return json.dumps({
        "formato": "AESGCM-PBKDF2",
        "iteracoes": ITERACOES,
        "sal": base64.b64encode(sal).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "dados": base64.b64encode(blob).decode("ascii"),
    }, indent=2) + "\n"


def abrir(cofre_json: str, senha: str) -> str:
    c = json.loads(cofre_json)
    sal = base64.b64decode(c["sal"])
    iv = base64.b64decode(c["iv"])
    blob = base64.b64decode(c["dados"])
    chave = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                       salt=sal, iterations=int(c.get("iteracoes", ITERACOES))).derive(
        senha.encode("utf-8"))
    # AES-GCM e autenticado: senha errada ou arquivo adulterado lanca excecao.
    return AESGCM(chave).decrypt(iv, blob, None).decode("utf-8")


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("abrir", "fechar"):
        print(__doc__, file=sys.stderr)
        return 1

    acao, entrada, saida = sys.argv[1], sys.argv[2], sys.argv[3]
    senha = os.environ.get("SENHA_PAINEL", "")
    if len(senha) < 16:
        print("ERRO: defina SENHA_PAINEL com pelo menos 16 caracteres.\n"
              "O arquivo cifrado fica num repositorio publico e pode ser atacado "
              "offline sem limite de tentativas, entao senha curta nao serve.",
              file=sys.stderr)
        return 1

    with open(entrada, encoding="utf-8") as f:
        conteudo = f.read()

    if acao == "abrir":
        try:
            resultado = abrir(conteudo, senha)
        except Exception:
            print("ERRO: nao consegui decifrar. Senha errada ou arquivo corrompido.",
                  file=sys.stderr)
            return 1
        json.loads(resultado)  # falha cedo se o conteudo nao for JSON valido
    else:
        json.loads(conteudo)   # nao cifra lixo
        resultado = fechar(conteudo, senha)

    with open(saida, "w", encoding="utf-8") as f:
        f.write(resultado)
    print(f"{acao}: {entrada} -> {saida} ({len(resultado):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
