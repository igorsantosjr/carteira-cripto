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


def buscar_historico_btc(data_inicio, data_fim):
    """Busca precos do BTC (USD) para comparacao semanal.
    Fontes (em ordem):
      0. Arquivo local data/btc_semanal.json (pre-gerado pela tarefa agendada via MCP)
      1. Crypto.com REST candlestick semanal (publica, sem chave)
      2. CoinGecko market_chart/range (fallback)
    Devolve dict {data_iso: preco} ou {} se falhar."""
    from datetime import datetime as _dt

    # O historico fica guardado em disco e so cresce: a API as vezes devolve
    # poucas semanas, e sem isso a linha do BTC sumia do grafico.
    arq_btc = os.path.join(RAIZ, "data", "btc_semanal.json")
    acumulado = {}
    try:
        if os.path.exists(arq_btc):
            with open(arq_btc, encoding="utf-8") as f:
                acumulado = {k: float(v) for k, v in json.load(f).items()}
            print(f"  BTC historico em disco: {len(acumulado)} semanas")
    except Exception as erro:
        print(f"  [aviso] arquivo BTC local invalido: {erro}")
        acumulado = {}

    novos = {}

    # --- Fonte 1: Crypto.com candlestick semanal (publica, sem chave) ---
    try:
        # timeframe valido da Crypto.com para semana e "7D" (candle abre na segunda 00:00 UTC)
        url = "https://api.crypto.com/exchange/v1/public/get-candlestick?instrument_name=BTC_USD&timeframe=7D&count=60"
        dados = _buscar_json(url, timeout=30)
        candles = dados.get("result", {}).get("data", [])
        if candles:
            resultado = {}
            # inclui a semana que contem data_inicio (candle abre ate 6 dias antes)
            limite_ini = (date.fromisoformat(data_inicio) - timedelta(days=6)).isoformat()
            for c in candles:
                if "t" in c:  # formato REST: t em ms, c = fechamento
                    d = _dt.utcfromtimestamp(int(c["t"]) / 1000).strftime("%Y-%m-%d")
                    fechamento = float(c["c"])
                else:  # formato normalizado: timestamp ISO, close
                    d = str(c["timestamp"])[:10]
                    fechamento = float(c["close"])
                if limite_ini <= d <= data_fim:
                    resultado[d] = fechamento  # chave = segunda-feira da semana
            if resultado:
                novos = resultado
                print(f"  BTC: {len(resultado)} semanas via Crypto.com")
    except Exception as erro:
        print(f"  [aviso] BTC via Crypto.com falhou: {erro}")

    # --- Fonte 2: CoinGecko market_chart/range (fallback) ---
    try:
        if novos:
            raise StopIteration
        import calendar
        t0 = int(calendar.timegm(_dt.strptime(data_inicio, "%Y-%m-%d").timetuple()))
        t1 = int(calendar.timegm(_dt.strptime(data_fim, "%Y-%m-%d").timetuple())) + 86400
        url = (f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"
               f"?vs_currency=usd&from={t0}&to={t1}")
        dados = _buscar_json(url, timeout=30)
        precos = dados.get("prices", [])
        resultado = {}
        for ts_ms, preco in precos:
            d = _dt.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            resultado[d] = preco
        if resultado:
            novos = resultado
            print(f"  BTC: {len(resultado)} dias via CoinGecko")
    except StopIteration:
        pass
    except Exception as erro:
        print(f"  [aviso] BTC via CoinGecko falhou: {erro}")

    # Mescla o que veio agora com o que ja estava em disco e regrava, para que
    # o historico nunca encolha quando a API devolver poucas semanas.
    if novos:
        acumulado.update(novos)
        try:
            os.makedirs(os.path.dirname(arq_btc), exist_ok=True)
            with open(arq_btc, "w", encoding="utf-8") as f:
                json.dump(dict(sorted(acumulado.items())), f, indent=2)
                f.write("\n")
        except Exception as erro:
            print(f"  [aviso] nao consegui gravar {arq_btc}: {erro}")

    if not acumulado:
        print("  [aviso] nenhuma fonte de historico BTC disponivel — grafico sem benchmark")
        return {}

    limite_ini = (date.fromisoformat(data_inicio) - timedelta(days=6)).isoformat()
    janela = {k: v for k, v in acumulado.items() if limite_ini <= k <= data_fim}
    print(f"  BTC historico: {len(janela)} semanas na janela do grafico")
    return janela


def serie_semanal_btc(historico_btc, serie_carteira):
    """Alinha precos BTC as mesmas semanas da serie da carteira.
    Para cada semana, pega o preco BTC mais proximo (ultimo disponivel <= data da semana + 6 dias)."""
    if not historico_btc or not serie_carteira:
        return []
    datas_btc = sorted(historico_btc.keys())
    resultado = []
    ultimo = None
    faltaram = 0
    for item in serie_carteira:
        segunda = date.fromisoformat(item["semana"])
        domingo = segunda + timedelta(days=6)
        preco = None
        for d_str in reversed(datas_btc):
            if d_str <= domingo.isoformat():
                preco = historico_btc[d_str]
                break
        if preco is None:
            # Semana sem cotacao (buraco no meio da serie): repete a ultima
            # conhecida em vez de descartar o grafico inteiro.
            if ultimo is None:
                print(f"  [aviso] BTC nao cobre {item['semana']} (antes do inicio da serie) "
                      "— grafico sem benchmark")
                return []
            preco = ultimo
            faltaram += 1
        ultimo = preco
        resultado.append({"semana": item["semana"], "preco": preco})
    if faltaram:
        print(f"  [aviso] {faltaram} semana(s) sem cotacao BTC — valor anterior repetido")
    return resultado


def carregar_precos_locais():
    """Le data/precos_locais.json ({"data": "YYYY-MM-DD", "cambio": 5.0,
    "precos": {"SOL": 100.0, ...}}), gravado pela tarefa agendada quando a rede
    do container bloqueia as APIs. So vale se a data for a de hoje (Brasilia),
    para nunca reaproveitar preco velho."""
    arq = os.path.join(RAIZ, "data", "precos_locais.json")
    try:
        if not os.path.exists(arq):
            return {}
        with open(arq, encoding="utf-8") as f:
            dados = json.load(f)
        hoje = datetime.now(FUSO_BR).date().isoformat()
        if dados.get("data") != hoje:
            print(f"  [aviso] precos_locais.json e de {dados.get('data')}, nao de {hoje} — ignorado")
            return {}
        return dados
    except Exception as erro:
        print(f"  [aviso] precos_locais.json invalido: {erro}")
        return {}


def coletar_precos(slots, simular=False):
    """Devolve {ticker: preco_usd} e o cambio USD/BRL."""
    if simular:
        print("  [modo simulacao] usando precos fixos")
        fixos = {"USDC": 0.9997, "HYPE": 58.50, "AAVE": 159.00}
        return {s["ticker"]: fixos.get(s["ticker"], 1.0) for s in slots}, 5.2134, "simulado"

    local = carregar_precos_locais()
    local_precos = local.get("precos", {})

    if local.get("cambio"):
        cambio, fonte_cambio = float(local["cambio"]), "local"
        print(f"  cambio USD/BRL = {cambio:.4f}  (fonte: arquivo local)")
    else:
        cambio, fonte_cambio = cotacao_usd_brl()

    faltam = [s for s in slots if s["ticker"] not in local_precos]
    ids = [s["coingecko_id"] for s in faltam if s.get("coingecko_id")]
    por_id = precos_coingecko(ids) if ids else {}

    precos = {}
    for slot in slots:
        ticker = slot["ticker"]
        if ticker in local_precos:
            precos[ticker] = float(local_precos[ticker])
            print(f"  {ticker} = US$ {precos[ticker]:.4f}  (arquivo local)")
            continue
        preco = por_id.get(slot.get("coingecko_id"))
        if preco is None and slot.get("cryptocom_par"):
            preco = preco_cryptocom(slot["cryptocom_par"])
        if preco is None and slot.get("stablecoin"):
            # A Crypto.com nao lista USDC_USD. Sem cotacao, assume a paridade:
            # e melhor que derrubar o painel inteiro por causa do caixa. Quando
            # a cotacao real vem, o checklist continua avisando se houver depeg.
            preco = 1.0
            print(f"  [aviso] sem cotacao de {ticker}; assumindo paridade US$ 1,0000")
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
        medio = float(slot["preco_medio_usd"])
        preco_usd = precos_usd[slot["ticker"]]
        emprestimo = slot.get("emprestimo", False)

        # Base de calculo em dolar: preco medio e valor atual ja em USD,
        # sem depender do cambio do dia (evita distorcer o custo de aquisicao).
        aportado = qtd * medio
        valor = qtd * preco_usd

        posicoes.append({
            "ticker": slot["ticker"],
            "quantidade": qtd,
            "preco_medio": medio,
            "preco_usd": preco_usd,
            "preco_atual": preco_usd,
            "aportado": aportado,
            "valor": valor,
            "lucro": valor - aportado,
            "rent_pct": ((valor - aportado) / aportado * 100) if aportado else 0.0,
            "emprestimo": emprestimo,
        })
        # Posicoes compradas com emprestimo nao entram no total aportado
        # (e capital proprio, nao capital emprestado).
        if not emprestimo:
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


def serie_semanal(historico, maximo=104):
    """Ultimo registro de cada semana (segunda a domingo)."""
    por_semana = {}
    for item in historico:
        d = date.fromisoformat(item["data"])
        segunda = d - timedelta(days=d.weekday())
        por_semana[segunda.isoformat()] = item["patrimonio"]
    chaves = sorted(por_semana)[-maximo:]
    return [{"semana": k, "patrimonio": por_semana[k]} for k in chaves]


def calcular_derivativos(derivativos, precos_usd):
    """Posicoes alavancadas (perpetuos).

    REGRA CENTRAL: o patrimonio recebe apenas margem + resultado nao realizado,
    NUNCA o valor nocional. Os US$ 501 de uma posicao 5x com US$ 100 de margem
    sao exposicao emprestada da corretora; somar isso ao patrimonio inflaria a
    carteira e esconderia o risco real, que e perder a margem na liquidacao.
    """
    saida = []
    for dv in derivativos or []:
        mercado = dv["mercado"]
        preco = precos_usd.get(mercado)
        if preco is None:
            print(f"  [aviso] sem cotacao de {mercado} para o derivativo; posicao omitida")
            continue
        tam = float(dv["tamanho"])
        entrada = float(dv["preco_entrada"])
        margem = float(dv["margem_usd"])
        curto = str(dv.get("direcao", "long")).lower() == "short"
        sinal = -1.0 if curto else 1.0
        pnl = (preco - entrada) * tam * sinal
        liq = float(dv.get("preco_liquidacao") or 0.0)
        # Distancia ate a liquidacao: quanto o preco ainda pode andar contra
        dist = ((liq / preco - 1) * 100) if (liq and preco) else None
        saida.append({
            "mercado": mercado,
            "direcao": "short" if curto else "long",
            "plataforma": dv.get("plataforma", "—"),
            "tipo": dv.get("tipo", "perp"),
            "tamanho": tam,
            "preco_entrada": entrada,
            "preco_atual": preco,
            "nocional": tam * preco,
            "margem": margem,
            # Quanto do capital proprio esta imobilizado aqui (custo de aquisicao
            # dos USDC que viraram margem) — entra no total aportado.
            "custo_base": float(dv.get("custo_base_usd", margem)),
            "alavancagem": dv.get("alavancagem"),
            "modo_margem": dv.get("modo_margem", ""),
            "pnl": pnl,
            "roe": (pnl / margem * 100) if margem else 0.0,
            "equity": margem + pnl,
            "preco_liquidacao": liq,
            "dist_liq_pct": dist,
        })
    return saida


def calcular_saude_emprestimos(dividas, precos_usd, posicoes):
    """Saude de cada emprestimo com garantia (ex.: Jupiter Lend).

    LTV = divida / valor da garantia. O emprestimo e liquidado quando o LTV
    chega ao limite de liquidacao do mercado, ou seja, quando o preco do ativo
    da garantia cai ate: divida / (qtd_garantia * limite).

    Campos lidos de cada item de `dividas`:
      garantia_ticker      ativo da garantia, precificado como um slot (ex.: "SOL")
      garantia_qtd         quantidade depositada como garantia
      garantia_qtd_estimada  true se a quantidade foi deduzida, nao lida da plataforma
      ltv_liquidacao_pct   limite de liquidacao do mercado (lido na plataforma)
      ltv_max_pct          LTV maximo para novos emprestimos (opcional)
      destino              ticker do ativo comprado com o dinheiro (opcional)
    Sem garantia_qtd ou sem ltv_liquidacao_pct, o que nao puder ser calculado
    volta como None — nada e inventado.
    """
    saida = []
    for dv in dividas or []:
        divida = float(dv.get("valor_usd", 0.0))
        tick = dv.get("garantia_ticker") or ("SOL" if "SOL" in str(dv.get("garantia", "")).upper() else None)
        preco = precos_usd.get(tick) if tick else None
        qtd = dv.get("garantia_qtd")
        qtd = float(qtd) if qtd not in (None, "") else None
        lim = dv.get("ltv_liquidacao_pct")
        lim = float(lim) if lim not in (None, "") else None

        gar_valor = (qtd * preco) if (qtd and preco) else None
        ltv = (divida / gar_valor * 100) if gar_valor else None
        preco_liq = (divida / (qtd * lim / 100)) if (qtd and lim) else None
        dist = ((preco_liq / preco - 1) * 100) if (preco_liq and preco) else None
        fator = (lim / ltv) if (lim and ltv) else None

        if dist is None:
            status = "incompleto"
        elif abs(dist) <= 15:
            status = "critico"
        elif abs(dist) <= 30:
            status = "atencao"
        else:
            status = "saudavel"

        # O que foi comprado com o dinheiro emprestado, e quanto vale hoje
        destino = dv.get("destino_ticker")
        if not destino:
            for p in posicoes:
                if p.get("emprestimo"):
                    destino = p["ticker"]
                    break
        val_destino = sum(p["valor"] for p in posicoes if p["ticker"] == destino) if destino else None

        saida.append({
            "plataforma": dv.get("plataforma", "—"),
            "ticker_divida": dv.get("ticker", "USDC"),
            "divida": divida,
            "apy": float(dv.get("apy_divida_pct", 0.0)),
            "juros_ano": divida * float(dv.get("apy_divida_pct", 0.0)) / 100,
            "garantia_ticker": tick,
            "garantia_nome": dv.get("garantia", tick or "—"),
            "garantia_qtd": qtd,
            "qtd_estimada": bool(dv.get("garantia_qtd_estimada")),
            "garantia_valor": gar_valor,
            "preco_garantia": preco,
            "ltv": ltv,
            "ltv_liq": lim,
            "ltv_max": float(dv["ltv_max_pct"]) if dv.get("ltv_max_pct") else None,
            "preco_liq": preco_liq,
            "dist_liq_pct": dist,
            "fator_saude": fator,
            "status": status,
            "destino": destino,
            "valor_destino": val_destino,
            "pnl_destino": (val_destino - divida) if val_destino is not None else None,
        })
    return saida


def aportes_no_periodo(aportes, inicio_exclusivo, fim_inclusivo):
    """Soma os aportes com data em (inicio, fim]. Serve para descontar dinheiro
    novo de qualquer comparacao de patrimonio: aporte nao e rentabilidade."""
    total = 0.0
    for ap in aportes or []:
        d = str(ap.get("data", ""))[:10]
        if d and inicio_exclusivo < d <= fim_inclusivo:
            total += float(ap.get("valor_usd", 0.0))
    return total


def indice_twr(serie, aportes):
    """Retorno ponderado no tempo (TWR) da carteira, em % acumulado desde a
    primeira semana. Remove o efeito dos aportes: dinheiro novo entrando nao
    conta como valorizacao. Devolve lista de % (mesmo tamanho de `serie`).

    Para cada semana: retorno = (patrimonio_fim - aportes_da_semana) / patrimonio_inicio - 1
    e o indice acumula esses retornos multiplicativamente.
    """
    if not serie:
        return []
    if not aportes:
        base = serie[0]["patrimonio"]
        return [(p["patrimonio"] / base - 1) * 100 if base else 0.0 for p in serie]

    # Soma os aportes que caem dentro de cada semana (segunda a domingo).
    # Aportes anteriores ou iguais ao inicio da primeira semana sao capital
    # inicial: ja estao embutidos no primeiro patrimonio, nao sao entrada nova.
    inicio = serie[0]["semana"]
    por_semana = {p["semana"]: 0.0 for p in serie}
    semanas = [p["semana"] for p in serie]
    for ap in aportes:
        d = str(ap.get("data", ""))[:10]
        if not d or d <= inicio:
            continue
        alvo = None
        for s in semanas:
            if s <= d:
                alvo = s
            else:
                break
        if alvo:
            por_semana[alvo] += float(ap.get("valor_usd", 0.0))

    indices = [0.0]
    acumulado = 1.0
    for i in range(1, len(serie)):
        anterior = serie[i - 1]["patrimonio"]
        atual = serie[i]["patrimonio"]
        entrada = por_semana.get(serie[i]["semana"], 0.0)
        if anterior:
            acumulado *= (atual - entrada) / anterior
        indices.append((acumulado - 1) * 100)
    return indices


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


def usd(valor, casas=2):
    txt = f"{abs(valor):,.{casas}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if valor < 0 else "") + "US$ " + txt


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


def grafico_semanal(serie, serie_btc=None, aportes=None, largura=640, legenda=True):
    """SVG estatico de linha + area, com linha opcional do BTC como benchmark.
    Quando serie_btc e fornecida, ambas as linhas sao normalizadas (base 100%)
    para comparacao direta de performance."""
    if len(serie) < 2:
        if not serie:
            return '<p class="vazio">Ainda sem histórico suficiente para o gráfico.</p>'
        unico = serie[0]
        return (f'<p class="vazio">Apenas uma semana registrada '
                f'({usd(unico["patrimonio"])}). O gráfico aparece a partir da segunda.</p>')

    tem_btc = serie_btc and len(serie_btc) >= 2

    if tem_btc:
        # Modo normalizado: ambas as series como % da primeira semana
        # Curva da carteira ajustada pelos aportes (TWR): dinheiro novo nao
        # conta como valorizacao, entao a comparacao com o BTC fica justa.
        valores_pct = indice_twr(serie, aportes)
        base_btc = serie_btc[0]["preco"]
        valores_btc_pct = [(p["preco"] / base_btc - 1) * 100 for p in serie_btc]

        todos = valores_pct + valores_btc_pct
        menor, maior = min(todos), max(todos)
        span = max(maior - menor, 1.0)
        dominio_min = menor - span * 0.35
        dominio_max = maior + span * 0.25
    else:
        valores = [p["patrimonio"] for p in serie]
        menor, maior = min(valores), max(valores)
        span = max(maior - menor, 1.0)
        dominio_min = max(0.0, menor - span * 0.35)
        dominio_max = maior + span * 0.25

    if dominio_max - dominio_min < 1:
        dominio_max = dominio_min + 1

    if not tem_btc:
        passo = 10 ** max(0, len(str(int(dominio_max - dominio_min))) - 1)
        dominio_min = (dominio_min // passo) * passo
        dominio_max = ((dominio_max // passo) + 1) * passo

    # largura menor = desenho pensado para a tela do celular (letras maiores na pratica)
    if largura < 640:
        X0, X1, YTOP, YBOT = 42.0, float(largura) - 24.0, 16.0, 150.0
    else:
        X0, X1, YTOP, YBOT = 80.0, 580.0, 28.0, 150.0
    ALTURA_SVG = (210 if tem_btc else 190) if legenda else 178

    def cy(v):
        return round(YBOT - (v - dominio_min) / (dominio_max - dominio_min) * (YBOT - YTOP), 2)

    n = len(serie)
    xs = [round(X0 + (X1 - X0) * i / (n - 1), 2) for i in range(n)]

    if tem_btc:
        ys = [cy(v) for v in valores_pct]
        ys_btc = [cy(v) for v in valores_btc_pct]
    else:
        valores = [p["patrimonio"] for p in serie]
        ys = [cy(v) for v in valores]

    pontos = list(zip(xs, ys))
    linha = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(pontos))
    area = f"M{xs[0]},{YBOT} " + " ".join(f"L{x},{y}" for x, y in pontos) + f" L{xs[-1]},{YBOT} Z"

    grades = []
    if tem_btc:
        # Eixo em porcentagem
        for i in range(3):
            v = dominio_min + (dominio_max - dominio_min) * i / 2
            y = cy(v)
            grades.append(f'<line class="grade" x1="{X0}" y1="{y}" x2="{X1}" y2="{y}"/>')
            grades.append(f'<text class="eixo" x="{X0 - 8}" y="{y + 3}" text-anchor="end">{v:+.0f}%</text>')
        # Linha de zero se estiver no range
        if dominio_min < 0 < dominio_max:
            y0 = cy(0)
            grades.append(f'<line x1="{X0}" y1="{y0}" x2="{X1}" y2="{y0}" stroke="var(--suave)" stroke-width="0.5" stroke-dasharray="4,3" opacity="0.5"/>')
    else:
        for i in range(3):
            v = dominio_min + (dominio_max - dominio_min) * i / 2
            y = cy(v)
            grades.append(f'<line class="grade" x1="{X0}" y1="{y}" x2="{X1}" y2="{y}"/>')
            grades.append(f'<text class="eixo" x="{X0 - 8}" y="{y + 3}" text-anchor="end">{usd(v, 0)}</text>')

    marcas = []
    for i, (x, y) in enumerate(pontos):
        ultimo = i == n - 1
        raio = 5 if ultimo else 4
        opacidade = "" if ultimo else 'opacity="0.75" '
        marcas.append(
            f'<circle cx="{x}" cy="{y}" r="{raio}" fill="#4a9dff" '
            f'{opacidade}stroke="#0F1420" stroke-width="2"/>'
        )

    # Linha e marcas do BTC
    svg_btc = ""
    if tem_btc:
        pontos_btc = list(zip(xs[:len(ys_btc)], ys_btc))
        linha_btc = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(pontos_btc))
        svg_btc = f'<path d="{linha_btc}" fill="none" stroke="#F7931A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="6,3" opacity="0.8"/>'
        for i, (x, y) in enumerate(pontos_btc):
            ultimo = i == len(pontos_btc) - 1
            raio = 4 if ultimo else 3
            opacidade = "" if ultimo else 'opacity="0.6" '
            svg_btc += f'\n  <circle cx="{x}" cy="{y}" r="{raio}" fill="#F7931A" {opacidade}stroke="#0F1420" stroke-width="1.5"/>'

    passo_rotulo = 1 if n <= 6 else max(2, (n - 1) // 4)
    rotulos = []
    for i, ponto in enumerate(serie):
        if i == 0 or i == n - 1 or i % passo_rotulo == 0:
            d = date.fromisoformat(ponto["semana"])
            rotulos.append(
                f'<text class="eixo" x="{xs[i]}" y="{YBOT + 22}" text-anchor="middle">'
                f'{d.day:02d}/{d.month:02d}</text>'
            )

    if tem_btc:
        var_cart = valores_pct[-1]
        var_btc = valores_btc_pct[-1]
        cls_cart = "sobe" if var_cart >= 0 else "cai"
        cls_btc = "sobe" if var_btc >= 0 else "cai"

        # Legenda com bolinhas coloridas. O espacamento e calculado a partir do
        # tamanho do texto (fonte monoespacada, ~0.62em por caractere) para os
        # rotulos nunca se sobreporem quando o texto cresce.
        legenda_y = YBOT + 40
        CHAR = 11 * 0.62

        rot_cart = "Carteira (líq. aportes)" if aportes else "Carteira"
        txt_cart = f"{rot_cart} {var_cart:+.1f}%"
        txt_btc = f"Bitcoin {var_btc:+.1f}%"
        diff = var_cart - var_btc
        txt_diff = f'{"+" if diff >= 0 else ""}{diff:.1f}pp vs BTC'

        cor_cart = "var(--verde)" if var_cart >= 0 else "var(--vermelho)"
        cor_btc = "var(--verde)" if var_btc >= 0 else "var(--vermelho)"
        cor_diff = "var(--verde)" if diff >= 0 else "var(--vermelho)"

        x_btc = X0 + 8 + len(txt_cart) * CHAR + 22
        fim_btc = x_btc + 8 + len(txt_btc) * CHAR
        ini_diff = X1 - len(txt_diff) * CHAR

        # Se a diferenca nao couber na mesma linha, desce uma linha.
        y_diff = legenda_y if ini_diff > fim_btc + 12 else legenda_y + 16

        legenda_svg = (
            f'<circle cx="{X0}" cy="{legenda_y - 3}" r="4" fill="#4a9dff"/>'
            f'<text class="eixo" x="{X0 + 8}" y="{legenda_y}" style="font-size:11px;fill:var(--texto)">{rot_cart} <tspan style="fill:{cor_cart}">{var_cart:+.1f}%</tspan></text>'
            f'<circle cx="{x_btc:.1f}" cy="{legenda_y - 3}" r="4" fill="#F7931A"/>'
            f'<text class="eixo" x="{x_btc + 8:.1f}" y="{legenda_y}" style="font-size:11px;fill:var(--texto)">Bitcoin <tspan style="fill:{cor_btc}">{var_btc:+.1f}%</tspan></text>'
            f'<text class="eixo" x="{X1}" y="{y_diff}" text-anchor="end" style="font-size:11px;fill:{cor_diff}">{txt_diff}</text>'
        )
        if y_diff > legenda_y:
            ALTURA_SVG += 16
        if not legenda:
            # A legenda e desenhada em HTML pelo painel (legivel no celular)
            legenda_svg = ""
    else:
        valores = [p["patrimonio"] for p in serie]
        var = (valores[-1] - valores[-2]) / valores[-2] * 100 if valores[-2] else 0.0
        classe = "sobe" if var >= 0 else "cai"
        legenda_svg = (
            f'<text class="rotulo-valor" x="{X1}" y="{max(YTOP - 6, ys[-1] - 22)}" text-anchor="end">{usd(valores[-1])}</text>'
            f'<text class="rotulo-delta {classe}" x="{X1}" y="{max(YTOP + 8, ys[-1] - 8)}" text-anchor="end">{pct(var)} na semana</text>'
        )

    aria_label = "Evolução semanal do patrimônio"
    if tem_btc:
        aria_label += f": carteira {valores_pct[-1]:+.1f}% vs BTC {valores_btc_pct[-1]:+.1f}%"

    return f"""<svg class="grafico" width="{largura}" height="{ALTURA_SVG}" viewBox="0 0 {largura} {ALTURA_SVG}" preserveAspectRatio="xMidYMid meet" role="img"
     aria-label="{aria_label}">
  <defs>
    <linearGradient id="areaSemanal" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#4a9dff" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#4a9dff" stop-opacity="0"/>
    </linearGradient>
    <filter id="brilhoSemanal" x="-20%" y="-40%" width="140%" height="180%">
      <feGaussianBlur stdDeviation="4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  {chr(10).join('  ' + g for g in grades)}
  <path d="{area}" fill="url(#areaSemanal)"/>
  {svg_btc}
  <path d="{linha}" fill="none" stroke="#4a9dff" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round" filter="url(#brilhoSemanal)"/>
  {chr(10).join('  ' + m for m in marcas)}
  {legenda_svg}
  {chr(10).join('  ' + r for r in rotulos)}
</svg>"""


def montar_html(ctx):
    """Layout A — resumo em cartoes, pensado primeiro para o celular."""
    p = ctx["calc"]
    agora = ctx["agora"]
    var_dia = ctx["var_dia"]
    total_dividas = ctx.get("total_dividas", 0.0)
    patrimonio_bruto = ctx.get("patrimonio_bruto", p["patrimonio"])
    patrimonio_liquido = ctx.get("patrimonio_liquido", p["patrimonio"])
    lucro_liquido = ctx.get("lucro_liquido", p["lucro"])
    rent_liquido = ctx.get("rent_liquido", p["rent_pct"])
    total_aportado = ctx.get("total_aportado", p["total_aportado"])
    derivativos = ctx.get("derivativos") or []
    emprestimos = ctx.get("emprestimos") or []
    vs_btc = ctx.get("vs_btc")  # (carteira_twr_pct, btc_pct) ou None

    def c(v):
        return "up" if v >= 0 else "dn"

    # ---------------------------------------------------------------- trio
    if var_dia is None:
        hoje_html = '<b class="mu">—</b>'
    else:
        hoje_html = f'<b class="{c(var_dia)}">{pct(var_dia)}</b>'
    if vs_btc:
        dif = vs_btc[0] - vs_btc[1]
        btc_html = f'<b class="{c(dif)}">{num(dif, 1).replace("-", "−")} pp</b>'
    else:
        btc_html = '<b class="mu">—</b>'

    # ---------------------------------------------------------------- perp
    cards_risco = []
    for dv in derivativos:
        dist = dv.get("dist_liq_pct")
        q = abs(dist) if dist is not None else None
        cls_dist = "dn" if (q is not None and q <= 15) else ("am" if (q is not None and q <= 30) else "up")
        largura = min(q / 40 * 100, 100) if q is not None else 0
        alav = f"{dv['alavancagem']}x" if dv.get("alavancagem") else ""
        cards_risco.append(f"""
  <section class="card risk {'r-' + cls_dist}">
    <div class="row">
      <b>{dv['mercado']} {dv['direcao']} {alav}</b>
      <span class="mono {c(dv['pnl'])}">{usd(dv['pnl'])} · {pct(dv['roe'], 1)}</span>
    </div>
    <div class="track"><span style="width:{largura:.0f}%"></span></div>
    <div class="row mu sm">
      <span>liquida em US$ {num(dv['preco_liquidacao'], 2)}</span>
      <span class="{cls_dist}">{num(q, 1) if q is not None else '—'}% de folga</span>
    </div>
    <dl class="mini">
      <div><dt>Entrada</dt><dd>{num(dv['preco_entrada'], 2)}</dd></div>
      <div><dt>Atual</dt><dd>{num(dv['preco_atual'], 2)}</dd></div>
      <div><dt>Margem</dt><dd>{usd(dv['margem'])}</dd></div>
      <div><dt>Nocional</dt><dd>{usd(dv['nocional'], 0)}</dd></div>
    </dl>
    <p class="foot">{dv['plataforma']} · no patrimônio entra só margem + resultado, <span class="nw">{usd(dv['equity'])}</span></p>
  </section>""")

    # ------------------------------------------------- saude do emprestimo
    rotulo_status = {"saudavel": ("Saudável", "up"), "atencao": ("Atenção", "am"),
                     "critico": ("Crítico", "dn"), "incompleto": ("Dados incompletos", "mu")}
    for em in emprestimos:
        st, cls_st = rotulo_status[em["status"]]
        ltv = em["ltv"]
        lim = em["ltv_liq"]
        # Regua de LTV: 0 a 100%, com marca no limite de liquidacao
        marca_lim = f'<i class="lim" style="left:{lim:.1f}%"></i>' if lim else ""
        preench = f'<span style="width:{min(ltv, 100):.1f}%"></span>' if ltv else ""
        avisos = []
        if em["qtd_estimada"]:
            avisos.append("Quantidade de garantia estimada a partir do print de 21/10 — confirme no Jupiter.")
        if em["garantia_qtd"] is None:
            avisos.append("Falta a quantidade de garantia depositada.")
        if lim is None:
            avisos.append("Falta o LTV de liquidação do Jupiter: sem ele não dá para calcular o preço que liquida.")
        avisos_html = "".join(f'<p class="foot am">{a}</p>' for a in avisos)
        destino_html = ""
        if em["destino"] and em["valor_destino"] is not None:
            destino_html = f"""
    <div class="sep">
      <div class="mu sm">Comprado com o dinheiro emprestado</div>
      <div class="row sm" style="margin-top:3px"><span class="mono">{em['destino']} {usd(em['valor_destino'])}</span>
        <span class="mono nw {c(em['pnl_destino'])}">{usd(em['pnl_destino'])} vs dívida</span></div>
    </div>"""
        liq_txt = (f"US$ {num(em['preco_liq'], 2)}" if em["preco_liq"] else "—")
        dist_txt = (f"{num(abs(em['dist_liq_pct']), 1)}% de folga" if em["dist_liq_pct"] is not None else "folga desconhecida")
        gar_txt = (f"{num(em['garantia_qtd'], 3)} {em['garantia_nome']}" if em["garantia_qtd"] else "—")
        cards_risco.append(f"""
  <section class="card loan l-{cls_st}">
    <div class="row">
      <b>Saúde do empréstimo</b>
      <span class="pill {cls_st}">{st}</span>
    </div>
    <div class="row big2">
      <span class="mono">{num(ltv, 1) + '%' if ltv else '—'}</span>
      <span class="mu sm">LTV{' · liquida em ' + num(lim, 0) + '%' if lim else ''}</span>
    </div>
    <div class="ltvbar">{preench}{marca_lim}</div>
    <dl class="mini">
      <div><dt>Dívida</dt><dd>{usd(em['divida'])}</dd></div>
      <div><dt>Garantia</dt><dd>{gar_txt}</dd></div>
      <div><dt>Valor garantia</dt><dd>{usd(em['garantia_valor']) if em['garantia_valor'] else '—'}</dd></div>
      <div><dt>Juros/ano</dt><dd>≈ {usd(em['juros_ano'])} ({num(em['apy'], 2)}%)</dd></div>
      <div><dt>{em['garantia_ticker'] or 'Garantia'} liquida em</dt><dd>{liq_txt}</dd></div>
      <div><dt>Folga</dt><dd class="{'dn' if em['status']=='critico' else ('am' if em['status']=='atencao' else '')}">{dist_txt}</dd></div>
    </dl>{destino_html}
    <p class="foot">{em['plataforma']} · {em['ticker_divida']} emprestado contra {em['garantia_nome']}</p>{avisos_html}
  </section>""")

    # ---------------------------------------------------------------- posicoes
    cards_pos = []
    for i, pos in enumerate(p["posicoes"]):
        tags = ""
        if pos.get("emprestimo"):
            tags += '<i class="tag">empréstimo</i>'
        if pos["ticker"] == "USDC":
            tags += '<i class="tag">caixa</i>'
        cards_pos.append(f"""
    <div class="pos">
      <div class="row"><span class="nm"><span class="forma {FORMAS[i]}"></span><b>{pos['ticker']}</b><span class="px">{usd(pos['preco_atual'], 2 if pos['preco_atual'] >= 1 else 4)}</span>{tags}</span>
        <span class="mono">{usd(pos['valor'])}</span></div>
      <div class="row mu sm mono"><span>{num(pos['quantidade'], 4)} · PM {num(pos['preco_medio'], 2)} · {num(pos['peso'], 1)}%</span>
        <span class="{c(pos['rent_pct'])}">{pct(pos['rent_pct'], 1)}</span></div>
      <div class="pbar"><span style="width:{pos['peso']:.1f}%;background:{CORES[i]}"></span></div>
    </div>""")

    # ---------------------------------------------------------------- mensal
    linhas_mes = []
    for idx, item in enumerate(ctx["mensal"]):
        ano, mes = item["mes"].split("-")
        rotulo = f"{MESES_CURTO[int(mes) - 1]}/{ano[2:]}"
        if idx > 0:
            inicio = ctx["mensal"][idx - 1]["patrimonio"]
            desde = ctx["mensal"][idx - 1]["mes"] + "-31"
        else:
            inicio = ctx.get("abertura") or item["patrimonio"]
            desde = ctx.get("abertura_data") or (item["mes"] + "-00")
        entrada = aportes_no_periodo(ctx.get("aportes"), desde, item["mes"] + "-31")
        ganho = item["patrimonio"] - inicio - entrada
        rent = (ganho / inicio * 100) if inicio else 0.0
        atual = item["mes"] == ctx["hoje"].strftime("%Y-%m")
        largura = min(abs(rent) * 2.5, 100)
        linhas_mes.append(f"""
    <div class="mes">
      <span class="ml">{rotulo}{' <i class="tag">em curso</i>' if atual else ''}</span>
      <span class="mbar"><span class="{c(ganho)}" style="width:{largura:.0f}%"></span></span>
      <span class="mono {c(ganho)} mv">{pct(rent, 1)}</span>
      <span class="mono mu mg">{usd(ganho, 0)}</span>
    </div>""")

    # ---------------------------------------------------------------- alertas
    alertas = []
    maior = p["posicoes"][0] if p["posicoes"] else None
    if maior and maior["peso"] > 70:
        alertas.append(("am", f"Concentração: {maior['ticker']} é {num(maior['peso'], 1)}% da carteira."))
    if var_dia is not None and var_dia <= -10:
        alertas.append(("dn", f"Queda de {pct(var_dia)} desde o registro anterior."))
    for pos in p["posicoes"]:
        if pos["ticker"] == "USDC" and abs(pos["preco_usd"] - 1.0) * 100 > 1:
            alertas.append(("am", f"USDC fora do peg: US$ {pos['preco_usd']:.4f}."))
    for dv in derivativos:
        d = dv.get("dist_liq_pct")
        if d is not None and abs(d) <= 30:
            nivel = "dn" if abs(d) <= 15 else "am"
            alertas.append((nivel, f"{dv['mercado']} {dv['direcao']} {dv.get('alavancagem', '')}x a "
                                   f"{num(abs(d), 1)}% da liquidação (US$ {num(dv['preco_liquidacao'], 2)})."))
    for em in emprestimos:
        if em["status"] in ("atencao", "critico"):
            nivel = "dn" if em["status"] == "critico" else "am"
            alertas.append((nivel, f"Empréstimo {em['plataforma']}: LTV {num(em['ltv'], 1)}%, "
                                   f"{em['garantia_ticker']} liquida em US$ {num(em['preco_liq'], 2)} "
                                   f"({num(abs(em['dist_liq_pct']), 1)}% de folga)."))
        elif em["status"] == "incompleto":
            alertas.append(("mu", f"Empréstimo {em['plataforma']}: faltam dados para calcular a liquidação."))
    if not alertas:
        alertas.append(("up", "Nenhum alerta: concentração, queda diária, peg e liquidações dentro do normal."))
    html_alertas = "".join(f'<li><i class="dot {k}"></i><span>{t}</span></li>' for k, t in alertas)

    legenda_btc = ""
    if vs_btc:
        dif = vs_btc[0] - vs_btc[1]
        legenda_btc = (f'<div class="leg"><span><i style="background:#4a9dff"></i>Carteira '
                       f'<b class="{c(vs_btc[0])}">{pct(vs_btc[0], 1)}</b></span>'
                       f'<span><i style="background:#F7931A"></i>Bitcoin '
                       f'<b class="{c(vs_btc[1])}">{pct(vs_btc[1], 1)}</b></span>'
                       f'<span class="{c(dif)}">{num(dif, 1).replace("-", "−")} pp</span></div>')

    mercado = " · ".join(f'{pos["ticker"]} <b>{num(pos["preco_usd"], 2)}</b>' for pos in p["posicoes"])

    data_ant = ctx.get("data_anterior", "—")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Painel de Investimentos</title>
<meta name="painel-gerado" content="{agora.isoformat()}">
<meta name="theme-color" content="#05070A">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Painel Cripto">
<meta name="robots" content="noindex, nofollow">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icons/apple-touch-icon-v3.png">
<link rel="icon" href="icons/icon-192-v3.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#05070A;--p:#0F1420;--p2:#141A28;--b:#232B3D;--t:#EAEEF6;--s:#8B95AB;
    --az:#4a9dff;--up:#3FCB8C;--dn:#F26B6B;--am:#E8B84B;
    --sans:'Inter',system-ui,-apple-system,sans-serif;--disp:'Space Grotesk',system-ui,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;}}
  *{{box-sizing:border-box}}
  html{{background:var(--bg)}}
  body{{margin:0;background:radial-gradient(ellipse at top,#0b0f18 0%,var(--bg) 520px) no-repeat,var(--bg);min-height:100vh;color:var(--t);
    font-family:var(--sans);font-size:14px;line-height:1.4;
    padding:calc(14px + env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom))}}
  .wrap{{max-width:520px;margin:0 auto}}
  .mono{{font-family:var(--mono);font-variant-numeric:tabular-nums}}
  .up{{color:var(--up)}} .dn{{color:var(--dn)}} .am{{color:var(--am)}} .mu{{color:var(--s)}}
  .sm{{font-size:12px}}
  .row{{display:flex;justify-content:space-between;align-items:center;gap:10px}}
  .top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
  .top b{{font-family:var(--disp);font-size:16px;font-weight:600}}
  .top span{{font-family:var(--mono);font-size:11px;color:var(--s)}}
  .card{{background:linear-gradient(155deg,var(--p) 0%,var(--p2) 55%,var(--p) 100%);border:1px solid var(--b);
    border-radius:14px;padding:13px 14px;margin-bottom:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
  h2{{font-family:var(--disp);font-size:14px;font-weight:600;margin:22px 2px 10px}}
  h2 small{{font-family:var(--sans);font-weight:400;color:var(--s);font-size:11.5px;margin-left:6px}}

  /* destaque do patrimonio */
  .hero{{background:linear-gradient(155deg,#13203a 0%,var(--p2) 60%,var(--p) 100%);border-color:#27406b;padding:16px}}
  .lbl{{font-size:11px;color:var(--s);text-transform:uppercase;letter-spacing:.06em}}
  .big{{font-family:var(--mono);font-size:32px;font-weight:700;letter-spacing:-.02em;margin:6px 0 2px;line-height:1.1}}
  .brl{{font-family:var(--mono);font-size:12px;color:var(--s)}}
  .duo{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,.07)}}
  .duo dt,.trio dt,.mini dt{{font-size:10.5px;color:var(--s);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}}
  .duo dd,.trio dd{{margin:0;font-family:var(--mono);font-size:14px;font-weight:500;font-variant-numeric:tabular-nums}}
  .duo dd small{{display:block;font-size:11.5px;font-weight:400;margin-top:1px}}
  dl{{margin:0}}

  .trio{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}}
  .trio>div{{background:var(--p);border:1px solid var(--b);border-radius:12px;padding:10px 11px}}
  .trio dd{{font-size:14.5px}} .trio dd small{{display:block;font-size:10px;color:var(--s);font-weight:400;font-family:var(--sans);margin-top:2px}}

  /* risco: perp e emprestimo */
  .risk.r-am,.loan.l-am{{border-color:rgba(232,184,75,.38)}}
  .risk.r-dn,.loan.l-dn{{border-color:rgba(242,107,107,.5)}}
  .risk b,.loan b{{font-size:13.5px}}
  .track{{height:6px;background:#0a0e16;border-radius:3px;margin:10px 0 7px;overflow:hidden}}
  .track span{{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,var(--dn),var(--am) 45%,var(--up))}}
  .mini{{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;margin-top:11px}}
  .mini dd{{margin:0;font-family:var(--mono);font-size:12.5px;font-variant-numeric:tabular-nums}}
  .foot{{font-size:11px;color:var(--s);margin:10px 0 0;line-height:1.45}}
  .foot.am{{color:var(--am);margin-top:6px}}
  .pill{{font-size:10px;font-weight:600;padding:3px 8px;border-radius:10px;text-transform:uppercase;letter-spacing:.04em;background:rgba(139,149,171,.15)}}
  .pill.up{{background:rgba(63,203,140,.14)}} .pill.am{{background:rgba(232,184,75,.16)}} .pill.dn{{background:rgba(242,107,107,.16)}}
  .big2{{margin-top:8px;align-items:baseline;justify-content:flex-start;gap:10px}}
  .big2 .mono{{font-size:24px;font-weight:700}}
  .ltvbar{{position:relative;height:8px;background:#0a0e16;border-radius:4px;margin:8px 0 2px}}
  .ltvbar span{{position:absolute;left:0;top:0;height:100%;border-radius:4px;background:linear-gradient(90deg,var(--up),var(--am) 70%,var(--dn))}}
  .ltvbar .lim{{position:absolute;top:-4px;width:3px;height:16px;background:var(--dn);border-radius:2px;transform:translateX(-50%)}}
  .sep{{margin-top:10px;padding-top:10px;border-top:1px solid var(--b)}}

  /* posicoes */
  .pos{{background:var(--p);border:1px solid var(--b);border-radius:12px;padding:11px 13px;margin-bottom:7px}}
  .pos .nm{{display:flex;align-items:center;gap:8px;font-size:14px}}
  .pos .row+.row{{margin:5px 0 8px}}
  .pos .px{{font-family:var(--mono);font-size:12px;color:var(--s);font-variant-numeric:tabular-nums}}
  .pos .nm{{flex-wrap:wrap;row-gap:4px}}
  .tag{{font-style:normal;font-size:9.5px;color:var(--am);background:rgba(232,184,75,.13);padding:2px 6px;border-radius:4px;font-family:var(--sans)}}
  .pbar{{height:3px;background:var(--p2);border-radius:2px;overflow:hidden}} .pbar span{{display:block;height:100%}}
  .forma{{width:12px;height:12px;flex-shrink:0;display:inline-block}}
  .forma.circle{{border-radius:50%;background:{CORES[0]}}}
  .forma.diamond{{background:{CORES[1]};border:1.5px solid #6badf0;transform:rotate(45deg) scale(.85)}}
  .forma.square{{background:{CORES[2]};border-radius:2px}}
  .forma.triangle{{width:0;height:0;border-left:6.5px solid transparent;border-right:6.5px solid transparent;border-bottom:12px solid {CORES[3]}}}
  .forma.ring{{border-radius:50%;border:2.5px solid {CORES[4]}}}

  /* grafico */
  .chart{{padding:10px 8px 6px}}
  .grafico{{width:100%;height:auto;display:block}}
  .grafico text{{font-family:var(--mono)}}
  .rotulo-valor{{font-size:13px;font-weight:700;fill:var(--t)}}
  .rotulo-delta{{font-size:11px}} .rotulo-delta.sobe{{fill:var(--up)}} .rotulo-delta.cai{{fill:var(--dn)}}
  .eixo{{font-size:10px;fill:var(--s)}} .grade{{stroke:var(--b);stroke-width:1}}
  .vazio{{color:var(--s);font-size:13px;margin:4px}}
  .leg{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;margin:6px 6px 2px;font-family:var(--mono)}}
  .leg i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}}
  .leg span:last-child{{margin-left:auto}}
  .nw{{white-space:nowrap}}

  /* mensal */
  .mes{{display:grid;grid-template-columns:104px 1fr 58px 66px;align-items:center;gap:8px;padding:9px 2px;border-bottom:1px solid var(--b);font-size:12.5px}}
  .mes:last-child{{border-bottom:none}}
  .ml{{display:flex;align-items:center;gap:5px;white-space:nowrap}} .ml .tag{{font-size:8.5px;padding:1px 4px}}
  .mbar{{height:5px;background:var(--p2);border-radius:3px;overflow:hidden}}
  .mbar span{{display:block;height:100%;border-radius:3px}} .mbar .up{{background:var(--up)}} .mbar .dn{{background:var(--dn)}}
  .mv,.mg{{text-align:right;font-size:12px}}

  /* alertas */
  ul.al{{list-style:none;margin:0;padding:0}}
  ul.al li{{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--b);font-size:12.5px;line-height:1.45}}
  ul.al li:last-child{{border-bottom:none}}
  .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:5px}}
  .dot.up{{background:var(--up)}} .dot.am{{background:var(--am)}} .dot.dn{{background:var(--dn)}} .dot.mu{{background:var(--s)}}

  .mkt{{font-family:var(--mono);font-size:11px;color:var(--s);line-height:1.8}} .mkt b{{color:var(--az);font-weight:500}}
  footer{{text-align:center;color:var(--s);font-size:11px;margin-top:22px;line-height:1.6}}
  .ghost-permissao{{margin-top:10px;background:transparent;border:1px solid var(--b);color:var(--s);
    padding:6px 12px;border-radius:7px;font-size:11px;cursor:pointer;font-family:var(--sans)}}
</style>
</head>
<body>
<div class="wrap">

  <div class="top"><b>Painel Cripto</b><span>{agora.strftime('%d/%m · %H:%M')}</span></div>

  <section class="card hero">
    <div class="lbl">Patrimônio líquido</div>
    <div class="big">{usd(patrimonio_liquido)}</div>
    <div class="brl">≈ {brl(patrimonio_liquido * ctx['cambio'])}</div>
    <dl class="duo">
      <div><dt>Resultado</dt><dd class="{c(lucro_liquido)}">{usd(lucro_liquido)}<small>{pct(rent_liquido)}</small></dd></div>
      <div><dt>Aportado</dt><dd>{usd(total_aportado)}<small class="mu">ativos {usd(patrimonio_bruto, 0)}</small></dd></div>
    </dl>
  </section>

  <dl class="trio">
    <div><dt>Hoje</dt><dd>{hoje_html}<small>vs {data_ant}</small></dd></div>
    <div><dt>vs BTC</dt><dd>{btc_html}<small>desde 10/08</small></dd></div>
    <div><dt>Dívida</dt><dd>{usd(total_dividas, 0)}<small>{len(emprestimos)} empréstimo{'s' if len(emprestimos) != 1 else ''}</small></dd></div>
  </dl>
{''.join(cards_risco)}
  <h2>Posições</h2>
{''.join(cards_pos)}

  <h2>Carteira vs Bitcoin <small>sem efeito dos aportes</small></h2>
  <section class="card chart">{ctx['grafico']}{legenda_btc}</section>

  <h2>Mês a mês <small>ganho sem contar aportes</small></h2>
  <section class="card">{''.join(linhas_mes)}
  </section>

  <h2>Alertas</h2>
  <section class="card"><ul class="al">{html_alertas}</ul></section>

  <h2>Mercado</h2>
  <section class="card mkt">{mercado} · USD/BRL <b>{num(ctx['cambio'], 4)}</b></section>

  <footer>
    Gerado em {agora.strftime('%d/%m/%Y às %H:%M')} (Brasília).<br>
    Não constitui recomendação de investimento.
  </footer>
</div>
<script>
  if ('serviceWorker' in navigator) {{
    window.addEventListener('load', () => {{ navigator.serviceWorker.register('sw.js').catch(() => {{}}); }});
  }}
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

    # Mercados de derivativo que nao existem como slot tambem precisam de cotacao
    tickers_slots = {s["ticker"] for s in slots}
    extras = []
    for dv in dados.get("derivativos", []):
        if dv["mercado"] not in tickers_slots:
            extras.append({"ticker": dv["mercado"],
                           "coingecko_id": dv.get("coingecko_id"),
                           "cryptocom_par": dv.get("cryptocom_par")})
            tickers_slots.add(dv["mercado"])

    precos_usd, cambio, fonte_cambio = coletar_precos(slots + extras, simular=simular)
    calc = calcular(slots, precos_usd, cambio)

    # Posicoes alavancadas: entram pela margem + PnL, nunca pelo nocional
    derivativos = calcular_derivativos(dados.get("derivativos"), precos_usd)
    equity_deriv = sum(d["equity"] for d in derivativos)
    custo_deriv = sum(d["custo_base"] for d in derivativos)

    # Dividas (passivos) — emprestimos DeFi com garantia
    dividas = dados.get("dividas", [])
    total_dividas = sum(d["valor_usd"] for d in dividas)
    patrimonio_bruto = round(calc["patrimonio"] + equity_deriv, 2)
    patrimonio_liquido = round(patrimonio_bruto - total_dividas, 2)

    # O capital que virou margem ja era do investidor (saiu do caixa), entao
    # continua contando como aportado — so mudou de lugar.
    total_aportado = round(calc["total_aportado"] + custo_deriv, 2)

    # Lucro e rentabilidade sobre o patrimonio liquido
    lucro_liquido = round(patrimonio_liquido - total_aportado, 2)
    rent_liquido = ((patrimonio_liquido - total_aportado) / total_aportado * 100) if total_aportado else 0.0

    agora = datetime.now(FUSO_BR)
    hoje = agora.date()
    # Historico registra patrimonio liquido (ativos - dividas)
    historico, anterior = atualizar_historico(
        dados.get("historico_diario", []), hoje.isoformat(), patrimonio_liquido
    )
    dados["historico_diario"] = historico

    anteriores = [h for h in historico if h["data"] < hoje.isoformat()]
    data_anterior = "—"
    # Desconta aportes feitos depois do registro anterior: dinheiro novo entrando
    # nao e variacao de mercado. Sem isso um aporte grande vira "rentabilidade".
    desde = anteriores[-1]["data"] if anteriores else ""
    entrada = aportes_no_periodo(dados.get("aportes"), desde, hoje.isoformat())
    var_dia = ((patrimonio_liquido - entrada - anterior) / anterior * 100) if anterior else None
    if anteriores:
        d = date.fromisoformat(anteriores[-1]["data"])
        data_anterior = f"{d.day:02d}/{d.month:02d}"
    if entrada:
        print(f"  [info] var. do dia desconta US$ {entrada:.2f} de aporte")

    semanal = serie_semanal(historico)

    # Benchmark BTC: busca historico para o mesmo periodo da carteira
    btc_semanal = []
    if historico and not simular:
        data_inicio = historico[0]["data"]
        data_fim = hoje.isoformat()
        historico_btc = buscar_historico_btc(data_inicio, data_fim)
        if historico_btc:
            btc_semanal = serie_semanal_btc(historico_btc, semanal)

    # Carteira (sem efeito dos aportes) vs BTC no mesmo periodo
    vs_btc = None
    if btc_semanal and len(semanal) >= 2:
        twr = indice_twr(semanal, dados.get("aportes"))[-1]
        btc = (btc_semanal[-1]["preco"] / btc_semanal[0]["preco"] - 1) * 100
        vs_btc = (twr, btc)

    # Saude dos emprestimos com garantia
    emprestimos = calcular_saude_emprestimos(dividas, precos_usd, calc["posicoes"])

    ctx = {
        "vs_btc": vs_btc,
        "emprestimos": emprestimos,
        "calc": calc,
        "cambio": cambio,
        "fonte_cambio": fonte_cambio,
        "agora": agora,
        "hoje": hoje,
        "var_dia": var_dia,
        "data_anterior": data_anterior,
        "grafico": grafico_semanal(semanal, btc_semanal if btc_semanal else None,
                                   dados.get("aportes"), largura=360, legenda=False),
        "btc_semanal": btc_semanal,
        "aportes": dados.get("aportes", []),
        "mensal": serie_mensal(historico),
        "abertura": historico[0]["patrimonio"] if historico else None,
        "abertura_data": historico[0]["data"] if historico else None,
        "derivativos": derivativos,
        "total_aportado": total_aportado,
        "dividas": dividas,
        "total_dividas": total_dividas,
        "patrimonio_bruto": patrimonio_bruto,
        "patrimonio_liquido": patrimonio_liquido,
        "lucro_liquido": lucro_liquido,
        "rent_liquido": rent_liquido,
    }

    with open(ARQ_SAIDA, "w", encoding="utf-8") as f:
        f.write(montar_html(ctx))

    if not simular:
        with open(ARQ_DADOS, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
            f.write("\n")

    print(f"\nPatrimonio bruto:   {usd(patrimonio_bruto)}")
    print(f"Dividas:            {usd(total_dividas)}")
    print(f"Patrimonio liquido: {usd(patrimonio_liquido)}")
    print(f"Aportado:           {usd(total_aportado)}")
    print(f"Lucro liquido:      {usd(lucro_liquido)} ({pct(rent_liquido)})")
    print(f"Variacao dia:       {pct(var_dia) if var_dia is not None else 'sem base anterior'}")

    # Resumo das posicoes alavancadas na saida padrao: quem monta o relatorio
    # diario le daqui, entao sem isto a posicao nao aparece no aviso do celular.
    for dv in derivativos:
        print(f"\nDERIVATIVO {dv['mercado']} {dv['direcao']} "
              f"{dv.get('alavancagem', '?')}x ({dv['plataforma']})")
        print(f"  Entrada:          US$ {num(dv['preco_entrada'], 2)}")
        print(f"  Preco atual:      US$ {num(dv['preco_atual'], 2)}")
        print(f"  Tamanho:          {num(dv['tamanho'], 4)} {dv['mercado']}")
        print(f"  Nocional:         {usd(dv['nocional'])}  (NAO entra no patrimonio)")
        print(f"  Margem:           {usd(dv['margem'])}")
        print(f"  PnL:              {usd(dv['pnl'])} ({pct(dv['roe'])} ROE)")
        print(f"  Entra no patrim.: {usd(dv['equity'])}  (margem + PnL)")
        if dv.get("dist_liq_pct") is not None:
            q = abs(dv["dist_liq_pct"])
            risco = "CRITICO" if q <= 15 else ("atencao" if q <= 30 else "ok")
            print(f"  Liquidacao:       US$ {num(dv['preco_liquidacao'], 2)} "
                  f"— a {num(q, 1)}% do preco de hoje [{risco}]")

    for em in emprestimos:
        print(f"\nEMPRESTIMO {em['plataforma']} ({em['ticker_divida']} contra {em['garantia_nome']})")
        print(f"  Divida:           {usd(em['divida'])}  (juros ~{usd(em['juros_ano'])}/ano)")
        if em["garantia_qtd"]:
            est = "  (ESTIMADA)" if em["qtd_estimada"] else ""
            print(f"  Garantia:         {num(em['garantia_qtd'], 4)} {em['garantia_nome']}{est}"
                  f" = {usd(em['garantia_valor'])}")
            print(f"  LTV:              {num(em['ltv'], 1)}%")
        if em["preco_liq"]:
            print(f"  Liquidacao:       {em['garantia_ticker']} a US$ {num(em['preco_liq'], 2)} "
                  f"— a {num(abs(em['dist_liq_pct']), 1)}% do preco de hoje [{em['status']}]")
        else:
            print("  Liquidacao:       desconhecida (falta ltv_liquidacao_pct ou garantia_qtd)")

    # Exposicao total ao ativo: a vista + alavancado. Uma queda atinge os dois
    # ao mesmo tempo, entao o numero isolado da tabela subestima o risco.
    for dv in derivativos:
        a_vista = sum(p["quantidade"] for p in calc["posicoes"]
                      if p["ticker"] == dv["mercado"])
        if a_vista:
            print(f"\nEXPOSICAO {dv['mercado']}: {num(a_vista, 4)} a vista "
                  f"+ {num(dv['tamanho'], 4)} no perpetuo "
                  f"= {num(a_vista + dv['tamanho'], 4)} {dv['mercado']}")

    print(f"\nGerado: {ARQ_SAIDA}")


if __name__ == "__main__":
    main()
