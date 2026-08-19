#!/usr/bin/env python3
"""
Gera o painel de investimentos (index.html) a partir de data/carteira.json.

Busca as cotacoes em fontes publicas (sem chave de API), atualiza o historico
diario e escreve o HTML estatico que o GitHub Pages publica.

Roda com Python 3 puro - sem dependencias externas.

Uso:
    python3 scripts/gerar_painel.py            # busca cotacoes reais
    python3 scripts/gerar_painel.py --simular  # usa precos fixos (teste local)
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta, date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQ_DADOS = os.path.join(RAIZ, "data", "carteira.json")
ARQ_SAIDA = os.path.join(RAIZ, "index.html")

FUSO_BR = timezone(timedelta(hours=-3))

# ----------------------------------------------------------------------------
# Coleta de cotacoes
# ----------------------------------------------------------------------------


def _buscar_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "painel-investimentos/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def cotacao_usd_brl():
    """Cambio USD->BRL. Tenta tres fontes independentes."""
    fontes = [
        ("awesomeapi", "https://economia.awesomeapi.com.br/last/USD-BRL",
         lambda d: float(d["USDBRL"]["bid"])),
        ("open.er-api", "https://open.er-api.com/v6/latest/USD",
         lambda d: float(d["rates"]["BRL"])),
        ("coingecko", "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=brl",
         lambda d: float(d["tether"]["brl"])),
    ]
    for nome, url, extrair in fontes:
        try:
            valor = extrair(_buscar_json(url))
            if valor and valor > 0:
                print(f"  cambio USD/BRL = {valor:.4f}  (fonte: {nome})")
                return valor, nome
        except Exception as erro:
            print(f"  [aviso] cambio via {nome} falhou: {erro}")
    raise RuntimeError("Nao consegui obter o cambio USD/BRL em nenhuma fonte.")


def precos_coingecko(ids):
    """Precos em USD de varios ativos de uma vez."""
    if not ids:
        return {}
    url = ("https://api.coingecko.com/api/v3/simple/price"
           f"?ids={','.join(ids)}&vs_currencies=usd")
    try:
        dados = _buscar_json(url)
        return {k: float(v["usd"]) for k, v in dados.items() if "usd" in v}
    except Exception as erro:
        print(f"  [aviso] CoinGecko falhou: {erro}")
        return {}


def preco_cryptocom(par):
    """Preco em USD de um par especifico na Crypto.com (fallback)."""
    url = f"https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name={par}"
    try:
        dados = _buscar_json(url)
        lista = dados.get("result", {}).get("data", [])
        if lista:
            return float(lista[0]["a"] or lista[0]["b"])
    except Exception as erro:
        print(f"  [aviso] Crypto.com ({par}) falhou: {erro}")
    return None


def coletar_precos(slots, simular=False):
    """Devolve {ticker: preco_usd} e o cambio USD/BRL."""
    if simular:
        print("  [modo simulacao] usando precos fixos")
        fixos = {"USDC": 0.9997, "HYPE": 58.50}
        return {s["ticker"]: fixos.get(s["ticker"], 1.0) for s in slots}, 5.2134, "simulado"

    cambio, fonte_cambio = cotacao_usd_brl()

    ids = [s["coingecko_id"] for s in slots if s.get("coingecko_id")]
    por_id = precos_coingecko(ids)

    precos = {}
    for slot in slots:
        ticker = slot["ticker"]
        preco = por_id.get(slot.get("coingecko_id"))
        if preco is None and slot.get("cryptocom_par"):
            preco = preco_cryptocom(slot["cryptocom_par"])
        if preco is None:
            raise RuntimeError(
                f"Nao consegui o preco de {ticker} em nenhuma fonte. "
                "O painel nao foi atualizado para evitar publicar dado errado."
            )
        precos[ticker] = preco
        print(f"  {ticker} = US$ {preco:.4f}")
    return precos, cambio, fonte_cambio


# ----------------------------------------------------------------------------
# Calculos
# ----------------------------------------------------------------------------


def calcular(slots, precos_usd, cambio):
    posicoes = []
    total_aportado = 0.0
    patrimonio = 0.0

    for slot in slots:
        qtd = float(slot["quantidade"])
        medio = float(slot["preco_medio_brl"])
        preco_usd = precos_usd[slot["ticker"]]
        preco_brl = preco_usd * cambio

        aportado = qtd * medio
        valor = qtd * preco_brl

        posicoes.append({
            "ticker": slot["ticker"],
            "quantidade": qtd,
            "preco_medio": medio,
            "preco_usd": preco_usd,
            "preco_atual": preco_brl,
            "aportado": aportado,
            "valor": valor,
            "lucro": valor - aportado,
            "rent_pct": ((valor - aportado) / aportado * 100) if aportado else 0.0,
        })
        total_aportado += aportado
        patrimonio += valor

    for p in posicoes:
        p["peso"] = (p["valor"] / patrimonio * 100) if patrimonio else 0.0

    posicoes.sort(key=lambda p: p["valor"], reverse=True)

    total_aportado = round(total_aportado, 2)
    patrimonio = round(patrimonio, 2)
    return {
        "posicoes": posicoes,
        "total_aportado": total_aportado,
        "patrimonio": patrimonio,
        "lucro": round(patrimonio - total_aportado, 2),
        "rent_pct": ((patrimonio - total_aportado) / total_aportado * 100) if total_aportado else 0.0,
    }


def atualizar_historico(historico, hoje_iso, patrimonio):
    """Insere ou substitui a linha de hoje. Devolve (historico, patrimonio_anterior)."""
    historico = [h for h in historico if h.get("data")]
    anteriores = [h for h in historico if h["data"] < hoje_iso]
    anterior = anteriores[-1]["patrimonio"] if anteriores else None

    historico = [h for h in historico if h["data"] != hoje_iso]
    historico.append({"data": hoje_iso, "patrimonio": round(patrimonio, 2)})
    historico.sort(key=lambda h: h["data"])
    return historico, anterior


def serie_semanal(historico, maximo=12):
    """Ultimo registro de cada semana (segunda a domingo)."""
    por_semana = {}
    for item in historico:
        d = date.fromisoformat(item["data"])
        segunda = d - timedelta(days=d.weekday())
        por_semana[segunda.isoformat()] = item["patrimonio"]
    chaves = sorted(por_semana)[-maximo:]
    return [{"semana": k, "patrimonio": por_semana[k]} for k in chaves]


def serie_mensal(historico):
    por_mes = {}
    for item in historico:
        por_mes[item["data"][:7]] = item["patrimonio"]
    return [{"mes": k, "patrimonio": por_mes[k]} for k in sorted(por_mes)]


# ----------------------------------------------------------------------------
# Formatacao
# ----------------------------------------------------------------------------

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_CURTO = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set",
               "Out", "Nov", "Dez"]
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo"]


def brl(valor, casas=2):
    txt = f"{abs(valor):,.{casas}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if valor < 0 else "") + "R$ " + txt


def num(valor, casas=2):
    txt = f"{abs(valor):,.{casas}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if valor < 0 else "") + txt


def pct(valor, casas=2):
    return f"{'+' if valor >= 0 else '-'}{abs(valor):.{casas}f}%".replace(".", ",")


def data_extenso(d):
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"


# ----------------------------------------------------------------------------
# Geracao do HTML
# ----------------------------------------------------------------------------

FORMAS = ["circle", "diamond", "square", "triangle", "ring"]
CORES = ["#4a9dff", "#1f4e82", "#12a5b8", "#6f7bd9", "#4568b0"]
TOTAL_SLOTS = 5


def grafico_semanal(serie):
    """SVG estatico de linha + area. Devolve o markup."""
    if len(serie) < 2:
        if not serie:
            return '<p class="vazio">Ainda sem histórico suficiente para o gráfico.</p>'
        unico = serie[0]
        return (f'<p class="vazio">Apenas uma semana registrada '
                f'({brl(unico["patrimonio"])}). O gráfico aparece a partir da segunda.</p>')

    valores = [p["patrimonio"] for p in serie]
    menor, maior = min(valores), max(valores)
    span = max(maior - menor, 1.0)
    dominio_min = max(0.0, menor - span * 0.35)
    dominio_max = maior + span * 0.25
    if dominio_max - dominio_min < 1:
        dominio_max = dominio_min + 1

    passo = 10 ** max(0, len(str(int(dominio_max - dominio_min))) - 1)
    dominio_min = (dominio_min // passo) * passo
    dominio_max = ((dominio_max // passo) + 1) * passo

    X0, X1, YTOP, YBOT = 80.0, 580.0, 28.0, 150.0

    def cy(v):
        return round(YBOT - (v - dominio_min) / (dominio_max - dominio_min) * (YBOT - YTOP), 2)

    n = len(serie)
    xs = [round(X0 + (X1 - X0) * i / (n - 1), 2) for i in range(n)]
    ys = [cy(v) for v in valores]
    pontos = list(zip(xs, ys))

    linha = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(pontos))
    area = f"M{xs[0]},{YBOT} " + " ".join(f"L{x},{y}" for x, y in pontos) + f" L{xs[-1]},{YBOT} Z"

    grades = []
    for i in range(3):
        v = dominio_min + (dominio_max - dominio_min) * i / 2
        y = cy(v)
        grades.append(f'<line class="grade" x1="{X0}" y1="{y}" x2="{X1}" y2="{y}"/>')
        grades.append(f'<text class="eixo" x="{X0 - 8}" y="{y + 3}" text-anchor="end">{brl(v, 0)}</text>')

    marcas = []
    for i, (x, y) in enumerate(pontos):
        ultimo = i == n - 1
        raio = 5 if ultimo else 4
        opacidade = "" if ultimo else 'opacity="0.75" '
        marcas.append(
            f'<circle cx="{x}" cy="{y}" r="{raio}" fill="#4a9dff" '
            f'{opacidade}stroke="#0F1420" stroke-width="2"/>'
        )

    passo_rotulo = 1 if n <= 6 else max(2, (n - 1) // 4)
    rotulos = []
    for i, ponto in enumerate(serie):
        if i == 0 or i == n - 1 or i % passo_rotulo == 0:
            d = date.fromisoformat(ponto["semana"])
            rotulos.append(
                f'<text class="eixo" x="{xs[i]}" y="172" text-anchor="middle">'
                f'{d.day:02d}/{d.month:02d}</text>'
            )

    var = (valores[-1] - valores[-2]) / valores[-2] * 100 if valores[-2] else 0.0
    classe = "sobe" if var >= 0 else "cai"

    return f"""<svg class="grafico" width="640" height="190" viewBox="0 0 640 190" preserveAspectRatio="xMidYMid meet" role="img"
     aria-label="Evolução semanal do patrimônio: {brl(valores[-1])} na última semana registrada, variação de {pct(var)}">
  <defs>
    <linearGradient id="areaSemanal" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#4a9dff" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#4a9dff" stop-opacity="0"/>
    </linearGradient>
    <filter id="brilhoSemanal" x="-20%" y="-40%" width="140%" height="180%">
      <feGaussianBlur stdDeviation="4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  {chr(10).join('  ' + g for g in grades)}
  <path d="{area}" fill="url(#areaSemanal)"/>
  <path d="{linha}" fill="none" stroke="#4a9dff" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round" filter="url(#brilhoSemanal)"/>
  {chr(10).join('  ' + m for m in marcas)}
  <text class="rotulo-valor" x="{X1}" y="{max(YTOP - 6, ys[-1] - 22)}" text-anchor="end">{brl(valores[-1])}</text>
  <text class="rotulo-delta {classe}" x="{X1}" y="{max(YTOP + 8, ys[-1] - 8)}" text-anchor="end">{pct(var)} na semana</text>
  {chr(10).join('  ' + r for r in rotulos)}
</svg>"""


def montar_html(ctx):
    p = ctx["calc"]
    agora = ctx["agora"]
    var_dia = ctx["var_dia"]

    linhas_posicoes = []
    for i, pos in enumerate(p["posicoes"]):
        classe_rent = "sobe" if pos["rent_pct"] >= 0 else "cai"
        largura = min(abs(pos["rent_pct"]) * 3, 100)
        cor_barra = "var(--verde)" if pos["rent_pct"] >= 0 else "var(--vermelho)"
        linhas_posicoes.append(f"""          <tr>
            <td><span class="ativo"><span class="forma {FORMAS[i]}"></span>{pos['ticker']}</span><span class="peso">Slot {i + 1} &middot; {num(pos['peso'], 1)}% da carteira</span></td>
            <td class="n">{num(pos['quantidade'], 2)}</td>
            <td class="n">{num(pos['preco_medio'], 4)}</td>
            <td class="n">{num(pos['preco_atual'], 4)}</td>
            <td class="n">{brl(pos['aportado'])}</td>
            <td class="n">{brl(pos['valor'])}</td>
            <td>
              <div class="{classe_rent} mono s12">{pct(pos['rent_pct'])}</div>
              <div class="{classe_rent} mono s11">{brl(pos['lucro'])}</div>
              <div class="barra-fundo"><span class="barra" style="width:{largura:.0f}%;background:{cor_barra}"></span></div>
            </td>
          </tr>""")

    for i in range(len(p["posicoes"]), TOTAL_SLOTS):
        linhas_posicoes.append(f"""          <tr class="vago">
            <td><span class="ativo"><span class="forma {FORMAS[i]}"></span>Slot {i + 1} — vago</span></td>
            <td class="n">—</td><td class="n">—</td><td class="n">—</td>
            <td class="n">—</td><td class="n">—</td><td>—</td>
          </tr>""")

    segmentos, legendas = [], []
    for i, pos in enumerate(p["posicoes"]):
        segmentos.append(f'<span class="fatia" style="width:{pos["peso"]:.2f}%;background:{CORES[i]}"></span>')
        legendas.append(
            f'<div class="item-legenda"><span class="forma {FORMAS[i]}"></span>{pos["ticker"]}'
            f'<span class="valor-legenda">{brl(pos["valor"])} &middot; {num(pos["peso"], 1)}%</span></div>'
        )
    for i in range(len(p["posicoes"]), TOTAL_SLOTS):
        legendas.append(
            f'<div class="item-legenda apagado"><span class="forma {FORMAS[i]}"></span>'
            f'Slot {i + 1} — vago<span class="valor-legenda">—</span></div>'
        )

    alertas = []
    maior = p["posicoes"][0] if p["posicoes"] else None
    if maior and maior["peso"] > 70:
        alertas.append(("atencao", "ATENÇÃO",
                        f"Concentração acima de 70%: {maior['ticker']} representa "
                        f"{num(maior['peso'], 1)}% da carteira."))
    else:
        alertas.append(("ok", "OK", "Nenhum ativo passa de 70% da carteira."))

    if var_dia is None:
        alertas.append(("info", "INFO",
                        "Sem registro anterior para comparar — esta é a primeira medição do histórico."))
    elif var_dia <= -10:
        alertas.append(("atencao", "ATENÇÃO",
                        f"Queda relevante: o patrimônio caiu {pct(var_dia)} desde o registro anterior."))
    else:
        alertas.append(("ok", "OK",
                        f"Sem queda superior a 10% desde o registro anterior ({pct(var_dia)})."))

    for pos in p["posicoes"]:
        if pos["ticker"] == "USDC":
            desvio = abs(pos["preco_usd"] - 1.0) * 100
            if desvio > 1:
                alertas.append(("atencao", "ATENÇÃO",
                                f"USDC fora do peg: US$ {pos['preco_usd']:.4f} "
                                f"({num(desvio, 2)}% de desvio)."))
            else:
                alertas.append(("ok", "OK",
                                f"USDC dentro do peg (US$ {pos['preco_usd']:.4f})."))

    html_alertas = "\n".join(
        f'      <li><span class="tag {c}">{t}</span><span>{txt}</span></li>'
        for c, t, txt in alertas
    )

    linhas_mes = []
    for item in ctx["mensal"]:
        ano, mes = item["mes"].split("-")
        rotulo = f"{MESES_CURTO[int(mes) - 1]}/{ano}"
        lucro_mes = item["patrimonio"] - p["total_aportado"]
        rent_mes = (lucro_mes / p["total_aportado"] * 100) if p["total_aportado"] else 0.0
        cls = "sobe" if lucro_mes >= 0 else "cai"
        atual = item["mes"] == ctx["hoje"].strftime("%Y-%m")
        badge = '<span class="badge">em andamento</span>' if atual else ""
        largura = min(abs(rent_mes) * 3, 100)
        cor = "var(--verde)" if lucro_mes >= 0 else "var(--vermelho)"
        linhas_mes.append(f"""          <tr>
            <td><div class="mes-cel"><span>{rotulo}</span>{badge}</div></td>
            <td class="n">{brl(item['patrimonio'])}</td>
            <td class="n {cls}">{brl(lucro_mes)}</td>
            <td class="n {cls}">{pct(rent_mes)}</td>
            <td><div class="barra-fundo"><span class="barra" style="width:{largura:.0f}%;background:{cor}"></span></div></td>
          </tr>""")

    cls_lucro = "sobe" if p["lucro"] >= 0 else "cai"
    if var_dia is None:
        card_var = '<div class="valor neutro">—</div><div class="delta neutro">primeira medição</div>'
    else:
        cls_var = "sobe" if var_dia >= 0 else "cai"
        card_var = (f'<div class="valor {cls_var}">{pct(var_dia)}</div>'
                    f'<div class="delta neutro">vs. {ctx["data_anterior"]}</div>')

    faixa = " ".join(
        f'<span>{pos["ticker"]} <b>${pos["preco_usd"]:,.4f}</b></span>'.replace(",", ".")
        for pos in p["posicoes"]
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Painel de Investimentos</title>
<meta name="painel-gerado" content="{agora.isoformat()}">
<meta name="theme-color" content="#0A0E14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Painel Cripto">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<link rel="icon" href="icons/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --bg:#05070A; --painel:#0F1420; --painel2:#141A28; --borda:#232B3D;
    --texto:#EAEEF6; --suave:#8B95AB;
    --azul:#4a9dff; --verde:#3FCB8C; --vermelho:#F26B6B; --ambar:#E8B84B;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:radial-gradient(ellipse at top,#0b0f18 0%,var(--bg) 60%);
    color:var(--texto);font-family:'Inter',sans-serif;padding:32px 20px 60px;}}
  .wrap{{max-width:960px;margin:0 auto;}}
  .mono{{font-family:'JetBrains Mono',monospace;}} .s12{{font-size:12px;}} .s11{{font-size:11px;}}
  .faixa{{display:flex;gap:14px;flex-wrap:wrap;padding-bottom:14px;margin-bottom:22px;
    border-bottom:1px solid var(--borda);}}
  .faixa span{{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--suave);white-space:nowrap;}}
  .faixa b{{color:var(--azul);font-weight:500;}}
  h1{{font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;margin:0 0 4px;letter-spacing:-.01em;}}
  .sub{{color:var(--suave);font-size:13px;}}
  header{{margin-bottom:28px;}}
  .cards{{display:grid;grid-template-columns:1fr 1.6fr 1fr;gap:12px;margin-bottom:24px;}}
  @media(max-width:760px){{.cards{{grid-template-columns:1fr;}}}}
  .card{{background:linear-gradient(155deg,var(--painel) 0%,var(--painel2) 55%,var(--painel) 100%);
    border:1px solid var(--borda);border-radius:12px;padding:16px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);}}
  .card .rotulo{{font-size:11px;color:var(--suave);text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;}}
  .card .valor{{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;}}
  .card .delta{{font-family:'JetBrains Mono',monospace;font-size:12px;margin-top:4px;}}
  .sobe{{color:var(--verde);}} .cai{{color:var(--vermelho);}} .neutro{{color:var(--suave);}}
  .trio{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}}
  .trio .r{{font-size:10px;color:var(--suave);text-transform:uppercase;letter-spacing:.03em;margin-bottom:6px;}}
  .trio .v{{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;white-space:nowrap;}}
  .secao{{background:linear-gradient(155deg,var(--painel) 0%,var(--painel2) 60%,var(--painel) 100%);
    border:1px solid var(--borda);border-radius:12px;padding:18px 18px 20px;margin-bottom:20px;}}
  .secao h2{{font-family:'Space Grotesk',sans-serif;font-size:15px;margin:0 0 14px;}}
  .legenda-secao{{font-size:12px;color:var(--suave);margin:-8px 0 12px;}}
  .badge{{font-family:'JetBrains Mono',monospace;font-size:11px;padding:3px 8px;border-radius:5px;
    background:rgba(232,184,75,.14);color:var(--ambar);white-space:nowrap;flex-shrink:0;}}
  .mes-cel{{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}}
  .rolagem{{overflow-x:auto;}}
  table{{border-collapse:collapse;margin-top:8px;width:100%;}}
  #posicoes{{min-width:700px;}} #mensal{{min-width:480px;}}
  th{{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--suave);
    padding:8px 6px;border-bottom:1px solid var(--borda);}}
  td{{padding:11px 6px;border-bottom:1px solid var(--borda);font-size:13px;vertical-align:middle;}}
  td.n{{font-family:'JetBrains Mono',monospace;}}
  tfoot td{{border-bottom:none;font-weight:600;padding-top:14px;}}
  tr.vago td{{opacity:.38;}}
  .barra-fundo{{width:80px;height:6px;background:var(--painel2);border-radius:3px;overflow:hidden;margin-top:4px;}}
  .barra{{height:100%;border-radius:3px;display:block;}}
  .ativo{{display:inline-flex;align-items:center;gap:8px;font-weight:600;}}
  .peso{{font-size:11px;color:var(--suave);display:block;margin-top:2px;}}
  .forma{{width:14px;height:14px;flex-shrink:0;display:inline-block;}}
  .forma.circle{{border-radius:50%;background:{CORES[0]};}}
  .forma.diamond{{background:{CORES[1]};border:1.5px solid #6badf0;transform:rotate(45deg);}}
  .forma.square{{background:{CORES[2]};border-radius:3px;}}
  .forma.triangle{{width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;
    border-bottom:13px solid {CORES[3]};background:none;}}
  .forma.ring{{border-radius:50%;border:3px solid {CORES[4]};background:none;}}
  .empilhada{{display:flex;width:100%;height:14px;border-radius:7px;overflow:hidden;
    background:var(--painel2);margin-bottom:14px;}}
  .fatia{{height:100%;}}
  .legendas{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;}}
  .item-legenda{{display:flex;align-items:center;gap:8px;font-size:12px;}}
  .item-legenda.apagado{{opacity:.4;}}
  .valor-legenda{{margin-left:auto;font-family:'JetBrains Mono',monospace;color:var(--suave);font-size:11px;}}
  ul.checklist{{list-style:none;padding:0;margin:0;}}
  ul.checklist li{{display:flex;align-items:flex-start;gap:10px;padding:10px 0;
    border-bottom:1px solid var(--borda);font-size:13px;line-height:1.45;}}
  ul.checklist li:last-child{{border-bottom:none;}}
  .tag{{font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 7px;border-radius:4px;
    flex-shrink:0;margin-top:1px;letter-spacing:.03em;}}
  .tag.ok{{background:rgba(63,203,140,.14);color:var(--verde);}}
  .tag.atencao{{background:rgba(232,184,75,.14);color:var(--ambar);}}
  .tag.info{{background:var(--painel2);color:var(--suave);}}
  .kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;}}
  .kv div span{{display:block;font-size:11px;color:var(--suave);text-transform:uppercase;
    letter-spacing:.04em;margin-bottom:5px;}}
  .kv div b{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:500;}}
  .grafico{{width:100%;height:auto;max-height:280px;display:block;}}
  .grafico text{{font-family:'JetBrains Mono',monospace;}}
  .rotulo-valor{{font-size:13px;font-weight:700;fill:var(--texto);}}
  .rotulo-delta{{font-size:11px;}} .rotulo-delta.sobe{{fill:var(--verde);}} .rotulo-delta.cai{{fill:var(--vermelho);}}
  .eixo{{font-size:10px;fill:var(--suave);}}
  .grade{{stroke:var(--borda);stroke-width:1;}}
  .vazio{{color:var(--suave);font-size:13px;margin:0;}}
  footer{{text-align:center;color:var(--suave);font-size:11px;margin-top:24px;line-height:1.6;}}
  .ghost-permissao{{margin-top:10px;background:transparent;border:1px solid var(--borda);color:var(--suave);
    padding:6px 12px;border-radius:7px;font-size:11px;cursor:pointer;font-family:'Inter',sans-serif;}}
  .ghost-permissao:hover{{border-color:var(--azul);color:var(--azul);}}
</style>
</head>
<body>
<div class="wrap">

  <div class="faixa">
    {faixa}
    <span>USD/BRL <b>{num(ctx['cambio'], 4)}</b></span>
    <span>atualizado {agora.strftime('%d/%m/%Y %H:%M')}</span>
  </div>

  <header>
    <h1>Painel de Investimentos</h1>
    <div class="sub">{data_extenso(ctx['hoje'])} &middot; atualizado automaticamente</div>
  </header>

  <div class="cards">
    <div class="card">
      <div class="rotulo">Patrimônio total</div>
      <div class="valor">{brl(p['patrimonio'])}</div>
    </div>
    <div class="card">
      <div class="rotulo">Rentabilidade total</div>
      <div class="trio">
        <div><div class="r">Total aportado</div><div class="v">{brl(p['total_aportado'])}</div></div>
        <div><div class="r">Lucro (ganho de capital)</div><div class="v {cls_lucro}">{brl(p['lucro'])}</div></div>
        <div><div class="r">Rentabilidade</div><div class="v {cls_lucro}">{pct(p['rent_pct'])}</div></div>
      </div>
    </div>
    <div class="card">
      <div class="rotulo">Variação desde o registro anterior</div>
      {card_var}
    </div>
  </div>

  <div class="secao">
    <h2>Dados de mercado</h2>
    <div class="kv">
      {"".join(f'<div><span>{pos["ticker"]} (USD)</span><b>$ {num(pos["preco_usd"], 4)}</b></div>' for pos in p["posicoes"])}
      <div><span>Câmbio USD/BRL</span><b>{brl(ctx['cambio'], 4)}</b></div>
      <div><span>Coleta</span><b>{agora.strftime('%d/%m/%Y %H:%M')}</b></div>
    </div>
  </div>

  <div class="secao">
    <h2>Posições</h2>
    <div class="rolagem">
      <table id="posicoes">
        <thead>
          <tr><th>Ativo</th><th>Qtd.</th><th>Preço médio R$</th><th>Preço atual R$</th>
              <th>Aportado</th><th>Valor atual</th><th>Rentab.</th></tr>
        </thead>
        <tbody>
{chr(10).join(linhas_posicoes)}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td><td class="n">—</td><td class="n">—</td><td class="n">—</td>
            <td class="n">{brl(p['total_aportado'])}</td>
            <td class="n">{brl(p['patrimonio'])}</td>
            <td>
              <div class="{cls_lucro} mono s12">{pct(p['rent_pct'])}</div>
              <div class="{cls_lucro} mono s11">{brl(p['lucro'])}</div>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>

  <div class="secao">
    <h2>Composição da carteira</h2>
    <div class="empilhada">{"".join(segmentos)}</div>
    <div class="legendas">{"".join(legendas)}</div>
  </div>

  <div class="secao">
    <h2>Checklist de alertas</h2>
    <ul class="checklist">
{html_alertas}
    </ul>
  </div>

  <div class="secao">
    <h2>Evolução semanal</h2>
    <div class="legenda-secao">Patrimônio total ao fim de cada semana registrada</div>
    {ctx['grafico']}
  </div>

  <div class="secao">
    <h2>Evolução mensal</h2>
    <div class="rolagem">
      <table id="mensal">
        <thead><tr><th>Mês</th><th>Patrimônio</th><th>Ganho de capital</th><th>Rentab.</th><th></th></tr></thead>
        <tbody>
{chr(10).join(linhas_mes)}
        </tbody>
      </table>
    </div>
  </div>

  <footer>
    Gerado automaticamente em {agora.strftime('%d/%m/%Y às %H:%M')} (horário de Brasília).<br>
    Não constitui recomendação de investimento.
  </footer>
</div>
<script>
  // Registra o service worker (deixa o painel instalável e funcionando offline).
  if ('serviceWorker' in navigator) {{
    window.addEventListener('load', () => {{
      navigator.serviceWorker.register('sw.js');
    }});
  }}

  // Avisa quando o painel foi atualizado desde a última visita neste aparelho.
  (function avisarAtualizacao() {{
    const geradoEm = document.querySelector('meta[name="painel-gerado"]').content;
    const anterior = localStorage.getItem('painel_gerado_em');
    if (anterior && anterior !== geradoEm) {{
      if ('Notification' in window && Notification.permission === 'granted') {{
        new Notification('Painel de investimentos atualizado', {{
          body: 'Novos dados de hoje já estão disponíveis.',
          icon: 'icons/icon-192.png',
        }});
      }}
    }}
    localStorage.setItem('painel_gerado_em', geradoEm);

    if ('Notification' in window && Notification.permission === 'default') {{
      const botao = document.createElement('button');
      botao.textContent = 'Ativar aviso de atualização';
      botao.className = 'ghost-permissao';
      botao.onclick = () => {{
        Notification.requestPermission().then(() => botao.remove());
      }};
      document.querySelector('footer').appendChild(document.createElement('br'));
      document.querySelector('footer').appendChild(botao);
    }}
  }})();
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------


def main():
    simular = "--simular" in sys.argv

    with open(ARQ_DADOS, encoding="utf-8") as f:
        dados = json.load(f)

    slots = dados["slots"]
    print(f"Carteira: {len(slots)} ativo(s). Buscando cotacoes...")

    precos_usd, cambio, fonte_cambio = coletar_precos(slots, simular=simular)
    calc = calcular(slots, precos_usd, cambio)

    agora = datetime.now(FUSO_BR)
    hoje = agora.date()
    historico, anterior = atualizar_historico(
        dados.get("historico_diario", []), hoje.isoformat(), calc["patrimonio"]
    )
    dados["historico_diario"] = historico

    var_dia = ((calc["patrimonio"] - anterior) / anterior * 100) if anterior else None
    anteriores = [h for h in historico if h["data"] < hoje.isoformat()]
    data_anterior = "—"
    if anteriores:
        d = date.fromisoformat(anteriores[-1]["data"])
        data_anterior = f"{d.day:02d}/{d.month:02d}"

    semanal = serie_semanal(historico)
    ctx = {
        "calc": calc,
        "cambio": cambio,
        "fonte_cambio": fonte_cambio,
        "agora": agora,
        "hoje": hoje,
        "var_dia": var_dia,
        "data_anterior": data_anterior,
        "grafico": grafico_semanal(semanal),
        "mensal": serie_mensal(historico),
    }

    with open(ARQ_SAIDA, "w", encoding="utf-8") as f:
        f.write(montar_html(ctx))

    if not simular:
        with open(ARQ_DADOS, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
            f.write("\n")

    print(f"\nPatrimonio:   {brl(calc['patrimonio'])}")
    print(f"Aportado:     {brl(calc['total_aportado'])}")
    print(f"Lucro:        {brl(calc['lucro'])} ({pct(calc['rent_pct'])})")
    print(f"Variacao dia: {pct(var_dia) if var_dia is not None else 'sem base anterior'}")
    print(f"\nGerado: {ARQ_SAIDA}")


if __name__ == "__main__":
    main()
