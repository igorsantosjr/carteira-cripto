# Painel de investimentos

Painel da minha carteira de criptoativos, atualizado sozinho todo dia às 10h
(horário de Brasília) e publicado via GitHub Pages.

## Como funciona

```
data/carteira.json  →  scripts/gerar_painel.py  →  index.html  →  GitHub Pages
   (posições +            (busca cotações e         (o painel)
    histórico)             recalcula tudo)
```

O agendamento fica em `.github/workflows/painel.yml`. A cada execução o script:

1. lê as posições e o histórico de `data/carteira.json`;
2. busca as cotações em fontes públicas (CoinGecko, com Crypto.com de reserva) e
   o câmbio USD/BRL (AwesomeAPI, com duas fontes de reserva);
3. recalcula valor atual, rentabilidade, composição e alertas;
4. grava o patrimônio de hoje no histórico e regenera o `index.html`;
5. faz commit das mudanças — é isso que mantém a comparação "desde ontem" viva.

Se nenhuma fonte de preço responder, o script falha de propósito e **não**
publica nada, para nunca mostrar um número inventado.

## Registrar uma compra ou venda

Fale com o Claude no chat contando o que mudou (ex.: "comprei 200 USDC a
R$5,18"). Ele recalcula a posição e devolve o `data/carteira.json` pronto —
é só colar por cima do arquivo aqui no GitHub (ícone do lápis em
`data/carteira.json` → colar → Commit changes).

Para adicionar um ativo novo, o Claude cria um bloco assim dentro de `slots`:

```json
{
  "ticker": "SOL",
  "nome": "Solana",
  "coingecko_id": "solana",
  "cryptocom_par": "SOL_USD",
  "quantidade": 1.5,
  "preco_medio_brl": 820.00
}
```

O `coingecko_id` é o identificador que aparece na URL da moeda no CoinGecko
(`coingecko.com/en/coins/solana` → `solana`). O painel comporta até 5 ativos —
os que sobram aparecem como "slot vago".

## Rodar na mão

Pela aba **Actions** → *Atualizar painel de investimentos* → **Run workflow**.

Localmente:

```bash
python3 scripts/gerar_painel.py            # busca cotações de verdade
python3 scripts/gerar_painel.py --simular  # preços fixos, não grava histórico
```

Não precisa instalar nada — usa só a biblioteca padrão do Python 3.

## Publicação

Em **Settings → Pages**, a fonte está como *Deploy from a branch*, branch `main`,
pasta `/ (root)`. O `index.html` na raiz é o painel.
