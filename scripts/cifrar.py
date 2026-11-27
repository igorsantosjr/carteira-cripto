#!/usr/bin/env python3
"""
Embrulha o painel num HTML protegido por senha.

O conteudo real vai cifrado com AES-256-GCM; a pagina publicada so contem o
blob cifrado e o codigo que pede a senha e decifra no proprio navegador, usando
a Web Crypto API. A senha nunca trafega nem fica no arquivo.

IMPORTANTE: o arquivo cifrado fica numa URL publica, entao qualquer um pode
baixa-lo e tentar quebrar a senha offline, sem limite de tentativas. Use uma
frase longa (5-6 palavras), nao uma senha curta.

Uso:
    SENHA_PAINEL="sua frase secreta" python3 scripts/cifrar.py index.html saida.html
"""

import base64
import os
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# OWASP recomenda >= 600k iteracoes para PBKDF2-HMAC-SHA256 (2023+).
# No navegador isso custa cerca de 1 segundo, o que tambem encarece
# o ataque de forca bruta de quem baixar o arquivo.
ITERACOES = 600_000

MOLDE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Painel de Investimentos</title>
<meta name="theme-color" content="#0A0E14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Painel Cripto">
<meta name="robots" content="noindex, nofollow">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icons/apple-touch-icon-v3.png">
<link rel="icon" href="icons/icon-192-v3.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{--bg:#05070A;--painel:#0F1420;--painel2:#141A28;--borda:#232B3D;
        --texto:#EAEEF6;--suave:#8B95AB;--azul:#4a9dff;--vermelho:#F26B6B;}
  *{box-sizing:border-box;}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:radial-gradient(ellipse at top,#0b0f18 0%,var(--bg) 60%);
       color:var(--texto);font-family:'Inter',sans-serif;padding:24px;}
  .caixa{background:linear-gradient(155deg,var(--painel) 0%,var(--painel2) 55%,var(--painel) 100%);
         border:1px solid var(--borda);border-radius:14px;padding:28px 24px;width:100%;max-width:380px;
         box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 18px 40px rgba(0,0,0,.45);}
  h1{font-family:'Space Grotesk',sans-serif;font-size:19px;margin:0 0 6px;letter-spacing:-.01em;}
  p.sub{color:var(--suave);font-size:13px;margin:0 0 20px;line-height:1.5;}
  label{display:block;font-size:11px;color:var(--suave);text-transform:uppercase;
        letter-spacing:.04em;margin-bottom:7px;}
  input{width:100%;padding:12px 13px;border-radius:9px;border:1px solid var(--borda);
        background:#0a0e16;color:var(--texto);font-size:15px;font-family:'Inter',sans-serif;}
  input:focus{outline:none;border-color:var(--azul);}
  button{width:100%;margin-top:14px;padding:12px;border-radius:9px;border:none;cursor:pointer;
         background:var(--azul);color:#061018;font-weight:600;font-size:15px;font-family:'Inter',sans-serif;}
  button:disabled{opacity:.55;cursor:default;}
  .lembrar{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:12px;color:var(--suave);}
  .lembrar input{width:auto;}
  .erro{color:var(--vermelho);font-size:13px;margin-top:13px;min-height:18px;}
  .nota{color:var(--suave);font-size:11px;margin-top:18px;line-height:1.5;}
</style>
</head>
<body>
<div class="caixa">
  <h1>Painel de Investimentos</h1>
  <p class="sub">Conteúdo cifrado. Informe a frase secreta para abrir.</p>
  <label for="senha">Frase secreta</label>
  <input type="password" id="senha" autocomplete="current-password" autofocus>
  <button id="abrir">Abrir painel</button>
  <div class="lembrar">
    <input type="checkbox" id="lembrar">
    <label for="lembrar" style="margin:0;text-transform:none;letter-spacing:0;font-size:12px;">Lembrar neste aparelho</label>
  </div>
  <div class="erro" id="erro"></div>
  <p class="nota">A frase não é enviada a lugar nenhum: a decifragem acontece neste aparelho.</p>
</div>
<script>
const SAL = "__SAL__";
const IV = "__IV__";
const DADOS = "__DADOS__";
const ITER = __ITER__;

const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

async function decifrar(senha) {
  const cod = new TextEncoder();
  const base = await crypto.subtle.importKey("raw", cod.encode(senha), "PBKDF2", false, ["deriveKey"]);
  const chave = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: b64(SAL), iterations: ITER, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  // AES-GCM e autenticado: senha errada faz o decrypt lancar excecao.
  const aberto = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(IV) }, chave, b64(DADOS));
  return new TextDecoder().decode(aberto);
}

// Minutos entre recargas automaticas enquanto o painel fica aberto.
// O Actions publica a cada 20 min; checar a cada 10 garante que a tela nunca
// fique mais de ~10 min atras da ultima publicacao.
const MINUTOS_RECARGA = 10;

function mostrar(html, autoAtualizar) {
  if (autoAtualizar) {
    // So faz sentido recarregar sozinho quando a frase esta guardada: senao a
    // recarga cairia na tela de senha e o painel sumiria da vista.
    // Duas regras: (1) recarrega sozinho a cada MINUTOS_RECARGA com o app aberto;
    // (2) ao voltar do segundo plano, recarrega se a tela tiver mais de 5 min.
    // A regra 2 e a que importa no celular: iOS e Android congelam timers de
    // apps em segundo plano, entao sem ela voce reabriria vendo dados velhos.
    const script = '<scr' + 'ipt>(function(){'
      + 'var t0=Date.now();'
      + 'setTimeout(function(){location.reload();},' + (MINUTOS_RECARGA * 60 * 1000) + ');'
      + 'document.addEventListener("visibilitychange",function(){'
      + 'if(document.visibilityState==="visible"&&Date.now()-t0>300000)location.reload();'
      + '});})();</scr' + 'ipt>';
    html = /<\/body>/i.test(html) ? html.replace(/<\/body>/i, script + '</body>')
                                  : html + script;
  }
  document.open();
  document.write(html);
  document.close();
}

async function tentar(senha, guardar) {
  const erro = document.getElementById("erro");
  const botao = document.getElementById("abrir");
  erro.textContent = "";
  botao.disabled = true;
  botao.textContent = "Decifrando...";
  try {
    const html = await decifrar(senha);
    let lembrado = false;
    if (guardar) {
      try { localStorage.setItem("painel_frase", senha); lembrado = true; } catch (e) {}
    } else {
      try { lembrado = !!localStorage.getItem("painel_frase"); } catch (e) {}
    }
    mostrar(html, lembrado);
  } catch (e) {
    try { localStorage.removeItem("painel_frase"); } catch (e2) {}
    erro.textContent = "Frase incorreta.";
    botao.disabled = false;
    botao.textContent = "Abrir painel";
    const campo = document.getElementById("senha");
    campo.value = "";
    campo.focus();
  }
}

document.getElementById("abrir").onclick = () =>
  tentar(document.getElementById("senha").value, document.getElementById("lembrar").checked);
document.getElementById("senha").onkeydown = e => {
  if (e.key === "Enter") document.getElementById("abrir").click();
};

// Se o aparelho ja guardou a frase, abre direto.
(function () {
  let guardada = null;
  try { guardada = localStorage.getItem("painel_frase"); } catch (e) {}
  if (guardada) {
    document.getElementById("lembrar").checked = true;
    tentar(guardada, false);
  }
})();
</script>
</body>
</html>
"""


def cifrar(conteudo: str, senha: str) -> str:
    sal = os.urandom(16)
    iv = os.urandom(12)
    chave = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                       salt=sal, iterations=ITERACOES).derive(senha.encode("utf-8"))
    blob = AESGCM(chave).encrypt(iv, conteudo.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return (MOLDE
            .replace("__SAL__", b64(sal))
            .replace("__IV__", b64(iv))
            .replace("__DADOS__", b64(blob))
            .replace("__ITER__", str(ITERACOES)))


def main():
    if len(sys.argv) != 3:
        print("uso: cifrar.py <entrada.html> <saida.html>", file=sys.stderr)
        return 1

    senha = os.environ.get("SENHA_PAINEL", "")
    if len(senha) < 16:
        print("ERRO: defina SENHA_PAINEL com pelo menos 16 caracteres.\n"
              "O arquivo cifrado fica numa URL publica e pode ser atacado "
              "offline sem limite de tentativas, entao senha curta nao serve.",
              file=sys.stderr)
        return 1

    with open(sys.argv[1], encoding="utf-8") as f:
        original = f.read()

    saida = cifrar(original, senha)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(saida)

    print(f"Cifrado: {len(original):,} bytes -> {len(saida):,} bytes "
          f"({sys.argv[2]}, PBKDF2 {ITERACOES:,} iteracoes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
