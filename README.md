# Gerador de Bilhetes - apifootball.com

Ferramenta de linha de comando (uso pessoal) que busca **jogos + previsoes
matematicas + odds** na [apifootball.com](https://apifootball.com) e monta
**bilhetes de aposta** automaticamente, priorizando confianca e/ou *value bet*.

> Aviso: previsoes e value bets sao estimativas estatisticas, nao garantias.
> Use por sua conta e risco e aposte com responsabilidade.

## Como funciona

1. Busca as previsoes (`get_predictions`) -> probabilidade do modelo para cada mercado.
2. Busca as odds (`get_odds`) de varios bookmakers e pega a **melhor odd** por selecao.
3. Calcula a **probabilidade implicita** da odd (`100 / odd`) e o **valor (EV)**:
   `EV = (prob_modelo / 100) * odd - 1`. EV positivo = aposta de valor.
4. Filtra por confianca minima e/ou valor minimo e **monta os bilhetes**
   (simples ou combinadas), uma selecao por jogo.

Mercados suportados: **1X2** (Casa/Empate/Fora), **Dupla chance** (1X/X2/12),
**Over/Under 1.5, 2.5 e 3.5 gols** e **Ambas Marcam (BTTS)** — todos vindos do
endpoint `get_predictions`.

> Dica: o modo `--sem-odds` gera bilhetes **so com as previsoes** (ranqueado por
> confianca, mostrando a "odd justa" = 100/probabilidade). Funciona de imediato,
> sem depender dos nomes dos campos de odds.

## Instalacao

Precisa de Python 3.10+.

```bash
cd apostas
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

Configure a chave da API:

```bash
# copie o exemplo e edite o arquivo .env colocando sua chave
cp .env.example .env
```

`.env`:
```
APIFOOTBALL_KEY=sua_chave_aqui
APIFOOTBALL_TIMEZONE=America/Sao_Paulo
```

> O arquivo `.env` ja esta no `.gitignore` e nunca deve ser enviado ao GitHub.

## Uso

```bash
python main.py                              # bilhetes de hoje (equilibrado)
python main.py --dias 2                      # hoje + amanha
python main.py --data 2026-06-07             # uma data especifica
python main.py --estrategia valor            # prioriza value bet (EV)
python main.py --estrategia seguro           # prioriza maior probabilidade
python main.py --simples                     # bilhetes simples (1 aposta cada)
python main.py --selecoes 4 --bilhetes 3     # combinadas de 4 selecoes, ate 3 bilhetes
python main.py --odd-alvo 2.0                 # combinada(s) misturando jogos ate odd ~2.0
python main.py --odd-alvo 2.0 --sem-odds      # mesma ideia, so com a previsao (odd justa)
python main.py --liga 152                     # filtra por uma liga (league_id)
python main.py --min-prob 60 --min-valor 0.1 # filtros mais rigorosos
python main.py --sem-valor                   # ignora EV, considera so a confianca
python main.py --sem-odds                    # bilhetes so com a previsao (sem odds)
python main.py --stake 20 --salvar saida.txt # define stake e salva (.txt ou .json)
```

### Bilhete com odd-alvo (misturando jogos)

Para montar uma **combinada que mistura jogos** mirando uma **odd total** (ex.: 2.0):

```bash
python main.py --odd-alvo 2.0                          # com odds dos bookmakers
python main.py --odd-alvo 2.0 --sem-odds               # so com a previsao (odd justa)
python main.py --odd-alvo 2.0 --tolerancia 0.2         # aceita odd entre 1.6 e 2.4
python main.py --odd-alvo 2.0 --min-selecoes 3         # pelo menos 3 jogos no bilhete
python main.py --odd-alvo 2.0 --bilhetes 3 --salvar b.json
```

Como funciona: o gerador escolhe, de forma gulosa, uma selecao por jogo cujo
**produto das odds** chegue o mais perto possivel da `--odd-alvo`, sem repetir
jogos entre os bilhetes. Cada bilhete tem no minimo `--min-selecoes` jogos
(padrao 2, para garantir que "mistura" jogos) e no maximo `--max-selecoes`.

### Principais opcoes

| Opcao         | Descricao                                              | Padrao        |
|---------------|--------------------------------------------------------|---------------|
| `--data`      | Data inicial `YYYY-MM-DD`                               | hoje          |
| `--ate`       | Data final `YYYY-MM-DD`                                 | = data        |
| `--dias`      | Qtd de dias a partir da data inicial                    | 1             |
| `--liga`      | Filtra por `league_id`                                  | todas         |
| `--estrategia`| `seguro` \| `valor` \| `equilibrado`                    | equilibrado   |
| `--selecoes`  | Selecoes por bilhete (combinada)                        | 3             |
| `--simples`   | Bilhetes simples (1 aposta cada)                        | desligado     |
| `--bilhetes`  | Maximo de bilhetes                                      | 5             |
| `--odd-alvo`  | Monta combinada(s) misturando jogos ate esta odd total  | desligado     |
| `--tolerancia`| Faixa aceitavel em torno da `--odd-alvo` (0.15 = +-15%) | 0.15          |
| `--min-selecoes` | Minimo de jogos por bilhete (modo `--odd-alvo`)      | 2             |
| `--max-selecoes` | Maximo de jogos por bilhete (modo `--odd-alvo`)      | 6             |
| `--min-prob`  | Probabilidade minima do modelo (%)                      | 50            |
| `--min-valor` | Value bet (EV) minimo                                   | 0.05          |
| `--sem-valor` | Ignora o filtro de EV                                   | desligado     |
| `--sem-odds`  | Bilhetes so com a previsao (odd justa = 100/prob)       | desligado     |
| `--stake`     | Valor apostado por bilhete (para calcular retorno)      | 10            |
| `--salvar`    | Salva em arquivo `.txt` ou `.json`                      | nao salva     |

## Conferindo acertos dos dias anteriores (backtest)

Da para checar **se as previsoes da API acertaram** em datas ja jogadas. O
script `conferir.py` cruza o `get_predictions` (palpite = maior probabilidade)
com os resultados reais do `get_events` (jogos finalizados) e calcula a taxa de
acerto por mercado:

- **1X2** — palpite = maior entre `prob_HW` / `prob_D` / `prob_AW`.
- **Over/Under 2.5** — palpite = maior entre `prob_O` (over) e `prob_U` (under).
- **Ambas Marcam (BTTS)** — palpite = maior entre `prob_bts` (sim) e `prob_ots` (nao).

```bash
python conferir.py                              # ultimos 7 dias ate ontem
python conferir.py --dias 14                     # ultimos 14 dias ate ontem
python conferir.py --data 2026-06-03 --ate 2026-06-04
python conferir.py --liga 152                     # so uma liga
python conferir.py --min-prob 60                  # so palpites "confiantes" (>=60%)
python conferir.py --detalhe                      # mostra a conferencia jogo a jogo
python conferir.py --salvar relatorio.json        # salva o relatorio completo
```

Saida: uma tabela-resumo com **acertos / total / taxa de acerto** por mercado,
e (com `--detalhe`) a conferencia jogo a jogo com o placar e um `OK`/`X` por
mercado. Com `--salvar relatorio.json` voce recebe tudo estruturado.

### Opcoes do `conferir.py`

| Opcao        | Descricao                                                   | Padrao |
|--------------|-------------------------------------------------------------|--------|
| `--data`     | Data inicial `YYYY-MM-DD`                                   | —      |
| `--ate`      | Data final `YYYY-MM-DD`                                     | = data |
| `--dias`     | Qtd de dias ate ontem (se `--data` nao for informada)        | 7      |
| `--liga`     | Filtra por `league_id`                                      | todas  |
| `--min-prob` | So avalia palpites com probabilidade escolhida >= valor (%)  | 0      |
| `--detalhe`  | Mostra a conferencia jogo a jogo                            | desligado |
| `--roi`      | Backtest de ROI com odds reais: simples vs combinadas        | desligado |
| `--estrategia` | No `--roi`: `seguro`/`valor`/`equilibrado` p/ escolher o palpite | valor |
| `--min-valor`  | No `--roi`: exige value bet (EV) >= valor (ex.: 0.1)       | sem filtro |
| `--stake`    | No `--roi`: valor por aposta/bilhete                        | 10     |
| `--combo-max`| No `--roi`: maior tamanho de combinada testado              | 5      |
| `--salvar`   | Salva o relatorio em arquivo `.json`                        | nao salva |

> Intervalos de datas sao quebrados automaticamente em janelas de 5 dias (limite
> da API), entao voce pode pedir periodos longos sem se preocupar.

### Backtest de ROI: aposta simples vs combinada (com dinheiro real)

A taxa de acerto nao diz se da lucro — quem decide isso e a **odd paga** contra a
probabilidade real. O modo `--roi` simula apostar com as **odds reais** do
`get_odds`, usando os **mesmos palpites** (a melhor selecao por jogo) tanto em
**apostas simples** quanto em **combinadas** de 2..N jogos, e mostra o **ROI**
(lucro / total apostado) de cada abordagem.

```bash
python conferir.py --data 2026-05-15 --ate 2026-06-04 --roi --estrategia valor
python conferir.py --dias 30 --roi --min-valor 0.1          # so apostas de valor (EV>=0.1)
python conferir.py --dias 30 --roi --combo-max 4 --salvar roi.json
```

Use isso para comparar, com numeros reais, se "combinar jogos" realmente compensa
no longo prazo (spoiler: combinar **multiplica a margem da casa**; quanto mais
pernas, pior o valor esperado — combinadas so parecem boas por causa da variancia).

## Busca de nichos com valor (EV+)

De milhares de jogos por dia nao queremos 50 bilhetes — queremos o **pequeno
nicho** (mercado + faixa de probabilidade + liga) onde a API tem vantagem real.
O `nichos.py` trata cada mercado de cada jogo como uma aposta simples (com odd
real e resultado real), segmenta tudo, calcula ROI + significancia (t-stat) e,
o mais importante, faz **validacao fora da amostra** (treino/teste por data):
acha nichos positivos no treino e mede se continuam positivos no teste. So
sobrevive o que da lucro nos dois periodos — e isso separa vantagem de sorte.

```bash
python nichos.py --dias 40                       # ultimos 40 dias ate ontem
python nichos.py --data 2026-04-26 --ate 2026-06-04 --min-n 60
python nichos.py --dias 40 --liga 152 --salvar nichos.json
```

| Opcao        | Descricao                                              | Padrao |
|--------------|--------------------------------------------------------|--------|
| `--data`/`--ate` | Intervalo `YYYY-MM-DD` (quebrado em janelas de 5 dias) | —  |
| `--dias`     | Dias ate ontem se `--data` ausente                     | 40     |
| `--liga`     | Filtra por `league_id`                                 | todas  |
| `--min-prob` | Probabilidade minima da selecao (%)                    | 0      |
| `--min-n`    | Amostra minima por segmento                            | 50     |
| `--min-roi`  | ROI%% minimo no treino para virar candidato            | 3      |
| `--salvar`   | Salva o relatorio em `.json`                           | nao salva |

> Leitura: um nicho com `t-stat` alto (|t|>2), amostra grande e que passa no
> treino **e** no teste e um candidato serio. Mesmo assim, valide ao vivo com
> stake pequeno: ROI passado nao garante futuro.

## Estrategia "banker do dia" (1 aposta/dia)

A ideia de "1 aposta segura por dia pra lucrar pouco" e tentadora. O `diario.py`
pega a selecao mais provavel do dia (banker), lista para voce apostar e faz o
backtest honesto dia a dia (com odds e resultados reais): mostra em quantos dias
houve vitoria, o lucro/prejuizo acumulado e — o mais importante — a pior
sequencia de derrotas e o maior rombo (drawdown).

```bash
python diario.py --data 2026-06-06 --listar               # lista os bankers do dia
python diario.py --dias 40 --min-prob 90 --max-odd 1.6     # backtest da estrategia
python diario.py --dias 40 --picks 1 --stake 10 --alvo 10 --salvar diario.json
```

| Opcao        | Descricao                                              | Padrao |
|--------------|--------------------------------------------------------|--------|
| `--min-prob` | Probabilidade minima do banker (%)                     | 85     |
| `--min-odd` / `--max-odd` | Faixa de odd aceitavel                    | 1.2 / 2.0 |
| `--picks`    | Quantos bankers por dia                                | 1      |
| `--stake`    | Stake fixo por aposta                                  | 10     |
| `--alvo`     | Lucro-alvo por win (dimensiona o stake = alvo/(odd-1)) | 10     |
| `--listar`   | So lista os bankers da data (sem backtest)             | desligado |
| `--salvar`   | Salva o relatorio em `.json`                           | nao salva |

> Realidade medida: da pra vencer 65-78% dos DIAS, mas isso **nao significa
> lucro**. A mesma config deu +3.8% num periodo e -13.3% em outro — variancia,
> nao vantagem. E no "modo alvo" voce arrisca muito pra ganhar pouco: uma
> sequencia curta de derrotas em odd baixa abre um rombo enorme. Use com stake
> pequeno e sem ilusao de ganho garantido.

## Arbitragem (surebet): lucro independente do resultado

Esta e a UNICA abordagem que ganha qualquer que seja o placar — e ela **nao usa
previsao**. Arbitragem e apostar em todos os resultados de um jogo, cada um na
casa que paga a melhor odd, quando a soma das probabilidades implicitas fica
abaixo de 100%. O `arbitragem.py` varre as odds de varias casas (a apifootball
traz ate ~13 bookmakers por jogo) e lista as oportunidades, ja com a divisao da
banca por perna.

```bash
python arbitragem.py --data 2026-06-06                     # surebets do dia
python arbitragem.py --dias 1 --banca 100 --min-lucro 1.5  # so margens >= 1.5%
python arbitragem.py --data 2026-06-06 --salvar arbs.json
```

Mercados verificados: 1X2, Over/Under 2.5 e Ambas Marcam. Margens realistas
ficam entre ~1% e 5%; valores muito altos (>8%) quase sempre sao **odd
desatualizada** (o script sinaliza).

> Por que NAO e dinheiro facil:
> - As odds da API sao um retrato e podem estar atrasadas/de horarios diferentes;
>   a surebet pode sumir na hora de apostar (confirme AO VIVO na casa).
> - Exige contas e capital em VARIAS casas, e execucao rapida (odds mudam em segundos).
> - As casas limitam/banem quem faz arbitragem.
> - A margem e pequena: R$100 distribuidos rendem ~R$1-5 por surebet.
>
> Ou seja: o lucro e garantido **por aposta SE voce conseguir executar as pernas
> nas odds mostradas**. O risco nao e o resultado do jogo — e a execucao.

## Modelo proprio (Poisson) - "fazer como as casas fazem"

Em vez de confiar na probabilidade pronta da API, o `modelo.py` constroi NOSSO
modelo a partir do historico de resultados (`get_events`). Ele usa as tecnicas
que os modeladores profissionais usam:

- Forca de ataque/defesa por time + vantagem de jogar em casa.
- Peso por recencia (forma): jogos recentes pesam mais (meia-vida ~40 dias).
- Correcao de Dixon-Coles: ajusta placares baixos (0-0,1-0,1-1) que o Poisson
  puro erra por assumir independencia.
- Ensemble com taxa empirica: mistura o Poisson com a frequencia real de Over/
  BTTS de cada time (corrige vies residual).
- Ajuste por desfalques (opcional, `--desfalques`).

Resultado medido (Argentina Primera Nacional, 215 jogos de teste): apos essas
melhorias, TODOS os mercados ficaram calibrados dentro de +-2.5pp (o vies de
BTTS caiu de -8.6pp para -2.4pp). Ou seja: quando o modelo diz 65%, acontece
~65%. Calibracao boa nao garante lucro — mas torna o 'value' confiavel e evita
apostar em ilusao.

```bash
# palpites do dia (rode SEMPRE com --liga; ex.: 99 = Brasileirao Serie A)
python modelo.py --liga 99 --treino-dias 150 --data 2026-06-07
# backtest: treina com o historico e mede o acerto do melhor palpite por dia
python modelo.py --backtest --liga 99 --treino-dias 150 --teste-dias 40
```

| Opcao         | Descricao                                          | Padrao |
|---------------|----------------------------------------------------|--------|
| `--liga`      | league_id (MUITO recomendado)                      | —      |
| `--data`      | Data alvo dos palpites                             | hoje   |
| `--treino-dias` | Dias de historico para estimar as forcas         | 120    |
| `--min-prob`  | Probabilidade minima do modelo para listar (%)     | 65     |
| `--backtest`  | Avalia o acerto do modelo em vez de prever         | desligado |
| `--teste-dias`| No backtest: dias de teste apos o treino           | 30     |

> Realidade: no teste do Brasileirao o palpite mais confiavel do modelo acertou
> ~58% dos dias — longe de "impossivel errar". O valor do modelo nao e cravar o
> dia, e gerar uma probabilidade PROPRIA para comparar com a odd e achar value
> (quando a casa paga mais que a nossa "odd justa" = 100/prob).

## Perfil das ligas (de qual liga tirar Over/Under/BTTS)

Cada liga tem uma tendencia. O `perfil_ligas.py` calcula, a partir do historico
real (`get_events`), a taxa de Over 2.5, Under 2.5, BTTS, vitoria do mandante e
media de gols por liga — e ranqueia. Assim voce escolhe o LADO certo conforme a
liga em vez de remar contra a mare.

```bash
python perfil_ligas.py --dias 21 --min-jogos 30            # ranking de todas as ligas
python perfil_ligas.py --dias 21 --ordenar btts            # ligas com mais BTTS
python perfil_ligas.py --liga 99 --dias 70                 # perfil do Brasileirao
```

Exemplo medido (Brasileirao Serie A, ~70 dias): Over 53%, Under 47%, BTTS 59%,
mandante 50%, 2.68 gols/jogo — leve tendencia a BTTS. (Ligas nordicas/holandesa
costumam puxar mais p/ Over/BTTS; varias sul-americanas, p/ Under.)

> ATENCAO: taxa-base alta NAO e lucro. Uma liga com 59% de BTTS tem a odd de BTTS
> precificada perto de 1.6-1.7 — ou seja, ~59% de acerto com EV ainda negativo
> apos a margem. O perfil serve para escolher o lado e, junto ao modelo e a odd,
> caçar value. Nao existe taxa-base que garanta lucro diario.

## Value betting (nosso modelo x odds) - o metodo profissional

`value.py` une tudo: treina o modelo Poisson (nossa probabilidade) e compara com
a melhor odd do mercado. So aponta aposta quando `(nossa_prob x odd) - 1 >= min-edge`
(a casa paga mais que o nosso preco justo). Tem backtest para provar se lucra.

```bash
python value.py --liga 99 --data 2026-06-07 --min-edge 0.05   # apostas de valor do dia
python value.py --liga 99 --backtest --teste-dias 40          # o value realmente lucrou?
```

> Resultado medido (Brasileirao, 100 apostas de valor, teste de 40 dias):
> acerto 50%, **ROI -1.7%**. Ou seja: o "value" que o nosso modelo enxergou era
> em boa parte ilusao — o mercado e mais afiado que um modelo feito so com placar.
> E a prova honesta de que nem o metodo profissional, com dados publicos, garante
> lucro. Use como filtro de disciplina (nao apostar contra o preco), nao como
> maquina de dinheiro.

## Calibracao do nosso modelo (promete x acontece)

`calibragem.py` treina o NOSSO modelo (Poisson, por liga, varias ligas) e mede,
no teste, se ele e calibrado: agrupa por faixa de probabilidade e compara com o
acerto real. Tambem mede o acerto do pick mais confiavel do dia entre todas as
ligas.

```bash
python calibragem.py --treino-dias 150 --teste-dias 40
```

> Resultado medido (~14.880 previsoes, todas as ligas): faixa 50-60% acertou
> 51.8%; 60-70% -> 57.1%; 70-80% -> 64.5%; 80-90% -> 70.8%; 90-100% -> 78.7%.
> O pick mais confiavel do dia acertou 62.5% dos dias.
>
> Conclusao: o nosso modelo e razoavelmente calibrado (quanto mais alto ele diz,
> mais acerta) — mas mesmo no topo (90-100%) a realidade e ~79%, nao 95%. A
> incerteza esta no FUTEBOL, nao no modelo. Nenhum modelo (nosso ou das casas)
> da 95% confiavel para um jogo unico, exceto apostas triviais que nao pagam nada.

## Ajuste por desfalques (artilheiro/jogador fora)

`desfalques.py` usa `get_teams` (que traz `player_goals` e `player_injured`) para
medir quanto do ataque de cada time esta de fora por lesao, e gera um
multiplicador de ataque que alimenta o `modelo.py`.

```bash
python desfalques.py --liga 99                       # tabela de desfalques por time
python modelo.py --liga 99 --data 2026-06-08 --desfalques   # previsao ja ajustada
```

Exemplo real (Brasileirao): Santos com Neymar e outros lesionados = 52% dos gols
fora -> ataque cai p/ 0.55; Sao Paulo (Luciano fora) -> 0.62; Palmeiras -> 0.78.

> DUAS RESSALVAS HONESTAS:
> 1. So serve para previsao do DIA (pra frente). NAO da para backtestar: a API
>    informa quem esta lesionado AGORA, nao numa data passada — usar isso em jogo
>    antigo seria look-ahead (trapaca com o futuro).
> 2. Qualidade do dado: alguns times vem com gols zerados (dado incompleto) e o
>    ajuste fica neutro neles. E as casas ja precificam lesao na hora — entao isso
>    ajuda a nao ser pego de surpresa, mas raramente cria vantagem sobre o mercado.

## PICK DO DIA (comando final que junta tudo)

`pick_do_dia.py` e o fechamento: treina o modelo Poisson por liga, aplica o ajuste
por desfalques (com --liga), calcula a tendencia da liga, pega as odds do dia e
lista as apostas com VALUE (nossa_prob x odd > 1), ranqueadas.

```bash
python pick_do_dia.py --liga 99 --data 2026-06-08 --desfalques --perfil
python pick_do_dia.py --data 2026-06-08 --min-edge 0.05 --top 5
python pick_do_dia.py --liga 99 --data 2026-06-08 --salvar pick.json
```

| Opcao | Descricao | Padrao |
|-------|-----------|--------|
| `--liga` | league_id (recomendado; libera desfalques) | — |
| `--data` | data alvo | hoje |
| `--treino-dias` | historico p/ treinar o modelo | 150 |
| `--min-prob` | prob minima do nosso modelo (%) | 55 |
| `--min-edge` | value minimo (0.05 = 5%) | 0 |
| `--min-odd`/`--max-odd` | faixa de odd | 1.3 / 4.0 |
| `--desfalques` | ajusta ataque por lesionados (so com --liga) | desligado |
| `--perfil` | so aposta a favor da tendencia da liga | desligado |
| `--top` | quantas apostas listar | 5 |

> CUIDADO com value alto demais: um value de +50% nao e mina de ouro — quase
> sempre e o NOSSO modelo errando (liga pequena, treino raso). O backtest de value
> (`value.py`) deu ROI -1.7%, lembrando que value medido com modelo simples e em
> boa parte ilusao. Use em ligas grandes e com bons dados, edges pequenos (3-10%),
> stake pequeno e SEMPRE valide ao vivo. Isto organiza a decisao; nao garante lucro.

## Depuracao do formato dos dados

Os nomes de alguns campos de odds podem variar conforme o plano. Para inspecionar
o JSON cru e ajustar o mapeamento em `analysis.py` (dicionario `MARKETS`), use:

```bash
python main.py --raw predictions     # mostra previsoes cruas
python main.py --raw odds            # mostra odds cruas
python main.py --raw events          # mostra jogos crus
```

Se algum mercado nao aparecer nos bilhetes, rode `--raw odds`, veja o nome real
da chave (ex: `o+2.5`, `over_25`, etc.) e adicione em `odd_keys` do mercado
correspondente em `analysis.py`.

## Enviar picks automaticamente no Discord (gratis)

Da para o projeto rodar sozinho todo dia e mandar os picks num canal do Discord,
sem servidor e sem custo, usando Discord Webhook + GitHub Actions.

### 1. Criar o webhook do Discord (1 min)
No Discord: Config do canal -> Integracoes -> Webhooks -> Novo webhook ->
Copiar URL. Guarde essa URL (e um segredo).

### 2. Testar localmente
```bash
# no .env: DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/....
python enviar_discord.py --liga 41 --min-edge 0.05 --so-alta --top 5
```
`--so-alta` envia so os picks de confianca Alta (recomendado). Sem picks bons no
dia, ele avisa "o disciplinado e nao apostar".

### 3. Rodar automatico e gratis (GitHub Actions)
1. Suba o projeto para um repositorio no GitHub.
2. No repo: Settings -> Secrets and variables -> Actions -> New repository secret,
   crie dois segredos:
   - `APIFOOTBALL_KEY` = sua chave da apifootball.com
   - `DISCORD_WEBHOOK_URL` = a URL do webhook
3. O arquivo `.github/workflows/picks.yml` ja agenda o envio diario (12:00 UTC =
   09:00 BRT). Edite o `cron` para mudar o horario, ou rode na mao em
   Actions -> "Picks do dia no Discord" -> Run workflow.

Pronto: todo dia ele treina o modelo, gera os picks de valor com nota de
confianca e posta no seu canal.

> Alternativas de hospedagem gratis: Railway/Render (free tier) com um cron, ou
> deixar `enviar_discord.py` num agendador local (Agendador de Tarefas do Windows).
> O GitHub Actions e o mais simples por nao exigir servidor.
>
> Limite: o backtest e os palpites continuam sem garantia de lucro. Automatizar
> o envio nao muda a matematica — so te poupa de rodar na mao. Aposte com
> responsabilidade.

## Estrutura

```
apostas/
  config.py        # carrega .env e parametros padrao
  api_client.py    # cliente HTTP da apifootball.com
  analysis.py      # parsing + calculo de value bet (Selection, MARKETS)
  bilhetes.py      # montagem dos bilhetes por estrategia e por odd-alvo (Bilhete)
  main.py          # CLI principal: gera bilhetes (argparse + saida com rich)
  conferir.py      # CLI de backtest: confere acertos das previsoes em datas passadas
  nichos.py        # CLI de busca de nichos EV+ com validacao fora da amostra
  diario.py        # CLI 'banker do dia': lista a aposta mais provavel e faz backtest
  arbitragem.py    # CLI scanner de arbitragem (surebets) nas odds de varias casas
  modelo.py        # CLI modelo proprio (Poisson) a partir do historico de resultados
  perfil_ligas.py  # CLI perfil das ligas (Over/Under/BTTS/Casa) por dados reais
  value.py         # CLI value betting: nosso modelo x odds reais (+ backtest de ROI)
  calibragem.py    # CLI calibracao do nosso modelo (promete x acontece) em varias ligas
  desfalques.py    # CLI ajuste por lesionados (gols fora) que alimenta o modelo
  pick_do_dia.py   # CLI FINAL: junta modelo + desfalques + perfil + value num comando
  enviar_discord.py # envia os picks do dia para o Discord (webhook)
  .github/workflows/picks.yml  # agendamento gratis (GitHub Actions) p/ envio diario
  requirements.txt
  .env.example
```
