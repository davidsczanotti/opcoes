opcoes – Coletor diário de opções (CALLs/PUTs)

Este projeto automatiza a coleta diária de opções no site `https://opcoes.net.br/opcoes/bovespa` usando Playwright, aplicando filtros:
- Tipo: Todas (CALLs e PUTs)
- Lista: Todos os ativos
- Vencimentos: todos disponíveis
- Strike: faixa completa (min–max)
- Modalidade: todas (A/E)
- CSVs saem com delimitador `;` (compatível com locale pt-BR) e números normalizados usando vírgula decimal.

Os resultados são salvos em CSV, sem duplicar tickers já coletados.

Como rodar
- Requisitos: Python 3.12+, Poetry instalado
- Instalar dependências e o navegador do Playwright:
  - `poetry install`
  - `poetry run playwright install chromium`

Executar coleta
- Coletar todos os papéis (padrão), saída no `data/opcoes_latest.csv` (pode usar como `base_atualizada.csv`):
  - `poetry run python -m opcoes.cli scrape`
  - Após a coleta, roda automaticamente o backfill de preços via yfinance para os underlyings (default: 90 dias) para que HV/IV Rank fiquem com histórico rápido.
    - Para desabilitar: `--no-backfill`
    - Para ajustar a janela: `--backfill-days 120` (exemplo)

- Opcional: enriquecer com indicador de preço x lucro (earnings_yield_ttm e pe_ttm) a partir de um CSV de fundamentos:
  - `poetry run python -m opcoes.cli scrape --fundamentals data/fundamentals.csv`
  - O CSV deve ter a coluna `ticker` e quaisquer das combinações abaixo:
    - `earnings_yield_ttm` (E/P) ou `pe_ttm` (P/L)
    - `lpa_ttm` e `preco`
    - `lucro_liquido_ttm`, `acoes_total` e `preco`
  - Os valores calculados são repetidos em cada linha do respectivo `underlying`.

- Alternativa automática (Status Invest):
  - `poetry run python -m opcoes.cli scrape --statusinvest`
  - Ou `--fundamentals statusinvest`
  - O coletor tenta extrair P/L da página da ação em `statusinvest.com.br` e deriva `earnings_yield_ttm = 1 / P/L`.

- Enriquecer CSV existente
- Se você já tem `data/opcoes_latest.csv` (ou `base_atualizada.csv`) e só quer adicionar as colunas de fundamentos usando os tickers da coluna `underlying`:
  - Usando Status Invest: `poetry run python -m opcoes.cli enrich --statusinvest --input data/opcoes_latest.csv`
  - Usando CSV de fundamentos: `poetry run python -m opcoes.cli enrich --fundamentals data/fundamentals.csv --input data/opcoes_latest.csv`
  - Por padrão sobrescreve o arquivo de entrada; para escrever em outro caminho, use `--output caminho.csv`.
  - Para focar apenas em Units (e ignorar ETFs/índices), inclua `--only-units`.

- Opções úteis:
  - `--symbols ABEV3,BBAS3` (limita por papéis)
  - `--max-symbols 20` (testes rápidos)
  - `--output caminho.csv` (define outro arquivo)
  - `--headful` (abre o navegador visível para depurar)
  - `--goto-timeout 90000` (aumenta timeout de carregamento)
  - `--proxy-server http://usuario:senha@proxy:3128` (caso precise autenticar)

Testes
- Instale as dependências de desenvolvimento: `poetry install --with dev`
- Exporte a variável para habilitar os testes e2e (precisam de rede liberada):
  - `RUN_E2E_TESTS=1 poetry run pytest tests/test_scraper_e2e.py`
- Sem essa variável os testes são automaticamente ignorados.

Notas
- A coleta é sequencial (ritmo humano) e destinada a uma execução diária.
- O CSV mantém unicidade por `ticker` (sem duplicatas entre execuções).
 - Quando `--fundamentals` é usado, duas novas colunas são adicionadas ao CSV: `earnings_yield_ttm` e `pe_ttm`.
- Cada linha também traz um checklist derivado (Status_Moneyness, %_Alta_p_2x, Status_2x, Status_Liquidez e Status_Theta) para ajudar a filtrar rapidamente oportunidades com base em moneyness, liquidez, cenário para dobrar e risco de theta.
- O scraper captura um snapshot diário do ativo-objeto via Yahoo Finance (preço, data do último fechamento, MM200 e retorno de 3 meses) e deriva `trend_flag`/`trend_reason`. Use `trend_flag=1` como gate para descartar calls cujos subjacentes estejam abaixo da MM200 e com retorno 3m negativo.
- Cada vencimento ganha um histórico diário de IV (via SQLite) para calcular `iv_rank_180d` (0–100) e `iv_score`. Assim fica fácil evitar calls caras: priorize `iv_score >= 1` ou `iv_rank` intermediário (aprox. 10–60).
- A planilha também traz `em_1sigma_pct`, `relacao_em_2x` e `em2x_score`, comparando o movimento implícito (1σ até o vencimento) com o movimento que você precisa para dobrar a opção. Use `relacao_em_2x >= 1` ou `em2x_score >= 1` para focar nas estruturas com cenário de 2x compatível com o que a curva de vol já precifica.
- Indicadores adicionais: `custo_pct` (custo da call sobre o ativo), `intrinsic_value`/`extrinsic_value` (quanto do prêmio é intrínseco vs. tempo/vol), `vol_fluxo_5d` e `num_fluxo_5d` (ratio vs. média móvel 5 dias de volume financeiro e negócios, usando `data/flow_history.db`). Eles ajudam a avaliar payoff vs. spot e detectar fluxo anômalo.
- Cada execução também persiste snapshots diários em `data/opcoes_snapshots.db`, permitindo comparar rankings dia a dia e alimentar um front-end futuramente.
- Use `poetry run python -m opcoes.cli position add ...` para registrar compras, `... position list` para acompanhar P/L atual (os valores usam o último snapshot da opção) e `... position close --id X --exit-date ... --price ...` para encerrar posições. O mesmo `opcoes_snapshots.db` guarda os trades em uma tabela `positions`.
- Após cada coleta, rode `poetry run python -m opcoes.cli report` para ver um resumo do snapshot mais recente (top oportunidades filtradas por score/trend) e o status das posições abertas com alertas automáticos.
- Para trabalhar no Excel sempre com dados atualizados do último snapshot (e não com um CSV congelado), use `poetry run python -m opcoes.cli snapshot export --output data/opcoes_latest.csv`. Opcionalmente, informe `--date YYYY-MM-DD` para exportar um dia específico.
- Histórico e limpeza:
  - `poetry run python -m opcoes.cli report` agora persiste automaticamente os rankings do dia em `ranking_entries` (top, racionais, loterias, teóricas). Use `--no-persist` para pular.
  - Registre uma decisão (guardar a linha completa do snapshot): `poetry run python -m opcoes.cli decision add --ticker B3SAB150 [--snapshot-date YYYY-MM-DD] --notes "..."`
  - Liste decisões registradas: `poetry run python -m opcoes.cli decision list --limit 20`
  - Limpeza de históricos vencidos/antigos: `poetry run python -m opcoes.cli cleanup --retention-days 180 --purge-snapshots` (sem `--purge-snapshots` limpa apenas rankings; com a flag também remove snapshots antigos/vencidos). O arquivo legado `opcoes_calls_eu.csv` não é mais usado.
- Para priorização rápida, o CSV calcula `moneyness_score`, `liquidez_score`, `dobro_score`, `theta_score`, `iv_score` e `score_total` (soma). As regras são:
  - Moneyness: 2 pts se está em `0-5% OTM (colada)`, 1 pt se `5-15% OTM (aposta)`.
  - Liquidez: 2 pts para Status_Liquidez = Alta, 1 pt para Média.
  - Dobro (`Status_2x`): 2 pts até 20% no ativo, 1 pt se 20–40%.
  - Theta: 1 pt se `Theta baixo`.
  - IV Rank: 2 pts se `iv_rank_180d` está entre 10–60, 1 pt se 0–10 ou 60–80.
  - Movimento implícito x 2x: 2 pts se `relacao_em_2x >= 1`, 1 pt se entre 0,5 e 1.
  - `score_total` varia de 0 a 11 para ordenar rapidamente os melhores trades dentro do checklist completo.
