opcoes – Coletor diário de opções (CALLs Europeias)

Este projeto automatiza a coleta diária de opções no site `https://opcoes.net.br/opcoes/bovespa` usando Playwright, aplicando filtros:
- Tipo: CALLs
- Vencimentos: 8 mais próximos
- Strike: faixa completa (min–max)
- Modalidade: Europeias (Mod = E)

Os resultados são salvos em CSV, sem duplicar tickers já coletados.

Como rodar
- Requisitos: Python 3.12+, Poetry instalado
- Instalar dependências e o navegador do Playwright:
  - `poetry install`
  - `poetry run playwright install chromium`

Executar coleta
- Coletar todos os papéis (padrão), saída no `data/opcoes_calls_eu.csv`:
  - `poetry run python -m opcoes.cli scrape`

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

Enriquecer CSV existente
- Se você já tem `data/opcoes_calls_eu.csv` e só quer adicionar as colunas de fundamentos usando os tickers da coluna `underlying`:
  - Usando Status Invest: `poetry run python -m opcoes.cli enrich --statusinvest --input data/opcoes_calls_eu.csv`
  - Usando CSV de fundamentos: `poetry run python -m opcoes.cli enrich --fundamentals data/fundamentals.csv --input data/opcoes_calls_eu.csv`
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
- Para priorização rápida, o CSV calcula `moneyness_score`, `liquidez_score`, `dobro_score`, `theta_score` e `score_total` (soma). As regras são:
  - Moneyness: 2 pts se está em `0-5% OTM (colada)`, 1 pt se `5-15% OTM (aposta)`.
  - Liquidez: 2 pts para Status_Liquidez = Alta, 1 pt para Média.
  - Dobro (`Status_2x`): 2 pts até 20% no ativo, 1 pt se 20–40%.
  - Theta: 1 pt se `Theta baixo`.
  - `score_total` varia de 0 a 7 para ordenar rapidamente os melhores trades dentro do checklist.



poetry run python -m opcoes.cli scrape \
  --headful \
  --goto-timeout 90000 \
  --proxy-server http://192.168.21.246:3128 \
  --proxy-username davidsc \
  --proxy-password 1981Card 



HTTPS_PROXY="http://davidsc:1981Card@192.168.21.246:3128" \
HTTP_PROXY="http://davidsc:1981Card@192.168.21.246:3128" \
poetry run python -m opcoes.cli enrich \
  --statusinvest --only-units \
  --input data/opcoes_calls_eu.csv
