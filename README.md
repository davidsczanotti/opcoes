opcoes – Monitoramento e gestão de opções (CALLs/PUTs)

Projeto para coletar dados de opções, rankear oportunidades, manter histórico e apoiar
decisões com foco didático. A plataforma separa responsabilidades (scraper, ranking,
estratégias, posições, finanças e DARF) e mantém trilha auditável no SQLite.

## Visão geral
- Scraper diário do `https://opcoes.net.br/opcoes/bovespa` via Playwright.
- Geração de ranking com critérios de moneyness, liquidez, IV, risco de theta e cenário de 2x.
- Estratégias para Cash-Covered Put e Covered Call, com gestão de posições e fluxo de caixa.
- Aba Fundamentus para filtro fundamentalista e ranking histórico de aprovadas.
- Web app para navegação didática (ranking, posições, DARF, estratégias, settings).

## Requisitos
- Python 3.12+
- Poetry

## Instalação
```bash
poetry install
poetry run playwright install chromium
```

## Uso rápido
1) Coletar opções:
```bash
poetry run python -m opcoes.cli scrape
```
2) Ver resumo e alertas:
```bash
poetry run python -m opcoes.cli report
```
3) Abrir a interface web:
```bash
poetry run python -m opcoes.web
```

## Comandos principais (CLI)
Ver ajuda geral:
```bash
poetry run python -m opcoes.cli --help
```

### Coleta e enriquecimento
- Coletar tudo (CALL/PUT, todos ativos, strikes e vencimentos):
```bash
poetry run python -m opcoes.cli scrape
```
- Limitar por símbolos ou quantidade:
```bash
poetry run python -m opcoes.cli scrape --symbols ABEV3,BBAS3 --max-symbols 20
```
- Definir output, headful, timeout e proxy:
```bash
poetry run python -m opcoes.cli scrape --output data/opcoes_latest.csv --headful --goto-timeout 90000 --proxy-server http://proxy:3128 --proxy-username usuario --proxy-password senha
```
- Retomar coleta interrompida (checkpoint automatico):
O scraper agora salva progresso automaticamente e retoma na proxima execucao, sem flag.
Opcional: `--resume-file caminho/para/checkpoint.db`. Por padrao, o scraper cria `<output>.checkpoint.db`.
Regras da retomada:
- O checkpoint so e reaproveitado se `--output` for o mesmo da execucao original.
- A validacao da lista de simbolos na retomada ignora ordem e duplicidades (usa assinatura canonica).
- Se a lista atual mudou parcialmente, o scraper reconcilia simbolos/linhas no checkpoint e continua pelos simbolos restantes, sem reset total.
- Se mudar `--output`, o scraper ignora o checkpoint e inicia do zero.
- Use `--no-resume` para desativar checkpoint nesta execucao.
- Backfill de preços (HV/IV Rank):
```bash
poetry run python -m opcoes.cli scrape --backfill-days 120
```
Para desabilitar: `--no-backfill`.

### Fundamentos (enriquecimento no CSV)
- Usando CSV de fundamentos:
```bash
poetry run python -m opcoes.cli scrape --fundamentals data/fundamentals.csv
```
O CSV deve ter `ticker` e alguma combinação:
`earnings_yield_ttm` (E/P) ou `pe_ttm` (P/L), ou `lpa_ttm + preco`, ou `lucro_liquido_ttm + acoes_total + preco`.
- Usando Status Invest:
```bash
poetry run python -m opcoes.cli scrape --statusinvest
```

### Enriquecer CSV existente
```bash
poetry run python -m opcoes.cli enrich --fundamentals data/fundamentals.csv --input data/opcoes_latest.csv
poetry run python -m opcoes.cli enrich --statusinvest --input data/opcoes_latest.csv
```
Por padrão sobrescreve o input; para salvar em outro lugar use `--output`.

### Exportar snapshot (para Excel)
```bash
poetry run python -m opcoes.cli snapshot export --output data/opcoes_latest.csv
```
Opcional: `--date YYYY-MM-DD`.

### Relatórios
- Relatório diário (ranking, alertas e posições):
```bash
poetry run python -m opcoes.cli report
```
Por padrão persiste ranking do dia; use `--no-persist` para pular.

### Posições
- Adicionar posição:
```bash
poetry run python -m opcoes.cli position add --ticker B3SAB150 --underlying B3SA3 --qty 100 --price 0.35 --trade-date 2025-01-01
```
- Listar posições:
```bash
poetry run python -m opcoes.cli position list
```
- Encerrar posição:
```bash
poetry run python -m opcoes.cli position close --id 10 --exit-date 2025-01-20 --price 0.05
```

### Decisões e histórico
```bash
poetry run python -m opcoes.cli decision add --ticker B3SAB150 --notes "Entrada por score e liquidez"
poetry run python -m opcoes.cli decision list --limit 20
```

### Limpeza de histórico
```bash
poetry run python -m opcoes.cli cleanup --retention-days 180 --purge-snapshots
```
Sem `--purge-snapshots`, remove apenas rankings.

### Fundamentus (filtro fundamentalista)
- Coletar snapshot:
```bash
poetry run python -m opcoes.cli fundamentus
```
- Aplicar filtros:
```bash
poetry run python -m opcoes.cli fundamentus-filter
```
Os parâmetros do filtro podem ser ajustados com as flags do comando.

### DARF / relatório fiscal
```bash
poetry run python -m opcoes.cli tax --year 2025 --month 1
poetry run python -m opcoes.cli tax --year 2025 --month 1 --mode simulated
poetry run python -m opcoes.cli tax --year 2025 --month 1 --mode all
```
`--mode` aceita `real` (default), `simulated` ou `all`.

## Web app
Rode:
```bash
poetry run python -m opcoes.web
```
Páginas principais:
- Ranking: oportunidades com/sem bid+ask (watchlist).
- Covered Call e Cash-Covered Put: sugestões e lotes.
- Fundamentus: filtros fundamentalistas + ranking didático de PUTs (score, perfil e alerta de execução).
- Posições: P/L e alertas.
- Auditoria: reconciliação de fluxo de caixa vs posições.
- DARF e configurações.

## Funcionamento do ranking e scores
- Checklist derivado: `Status_Moneyness`, `%_Alta_p_2x`, `Status_2x`, `Status_Liquidez`, `Status_Theta`.
- Scores contínuos (moneyness, liquidez, theta, IV, cenário 2x) formam `score_total`.
- Regras rápidas:
  - Moneyness: 2 pts em `0-5% OTM`, 1 pt em `5-15% OTM`.
  - Liquidez: 2 pts para Alta, 1 pt para Média.
  - Dobro: 2 pts se precisa <= 20% no ativo, 1 pt se 20–40%.
  - Theta: 1 pt se `Theta baixo`.
  - IV Rank: 2 pts entre 10–60; 1 pt em 0–10 ou 60–80.
  - Movimento implícito x 2x: 2 pts se `relacao_em_2x >= 1`, 1 pt se 0,5–1.

## Fundamentus (aba e cálculos)
- Filtros sequenciais: `liquidez_2m`, `div_bruta_patrim`, `cresc_rec_5a`, `div_yield`,
  `roe`, `margem_liquida` (com exceção de 0% opcional).
- Preço teto: `preco_teto = cotacao * (div_yield / target_yield_pct)`.
- Ranking histórico de aprovadas por janela de dias.

## Dados e arquivos
- `data/opcoes_latest.csv`: export atual para planilhas.
- `data/opcoes_snapshots.db`: snapshots, posições, rankings e histórico.
- `data/iv_history.db`: histórico de IV.
- `data/flow_history.db`: histórico de fluxo.
- `data/opcoes_latest.checkpoint.db`: checkpoint transacional da coleta (retomada por símbolo).

## Variáveis de ambiente
- `OPCOES_DB_PATH`: define outro caminho para o SQLite (default: `data/opcoes_snapshots.db`).

## Testes
```bash
poetry install --with dev
RUN_E2E_TESTS=1 poetry run pytest tests/test_scraper_e2e.py
```
Sem `RUN_E2E_TESTS`, os testes e2e são ignorados.

## Observações
- A coleta é sequencial (ritmo humano), pensada para execução diária.
- Se o navegador fechar durante o scrape, o coletor tenta reiniciar e continuar no mesmo símbolo.
- Bid/ask podem não estar disponíveis na fonte. Quando ausentes, o app trata como watchlist
  e usa último/preço teórico apenas como referência.
- O CSV mantém unicidade por `ticker` e normaliza números no padrão pt-BR.
- Melhorias recentes:
  - Recalculo de score/IV no scraper aplicado em todos os casos (com e sem preço do ativo).
  - Segmentação de ranking por delta usando `abs(delta)` (corrige classificação de PUTs).
  - Cálculo de prêmio/DARF centralizado e DARF sempre arredondado em centavos.
  - Relatório fiscal (`tax`) agora suporta filtro de modo `real/simulated/all`.
  - Recalculo final do snapshot consolidado para `iv_rank_180d`, `iv_score`, `vol_fluxo_5d` e `score_total` (evita divergência em retomadas/checkpoints).
  - `iv_history.db` e `flow_history.db` agora seguem o mesmo diretório do `OPCOES_DB_PATH`, evitando mistura entre contextos.
  - Retomada do scraper migrada para checkpoint SQLite transacional por símbolo (mais resiliente a quedas e reinícios).
  - Aba `Cash-Covered Put` agora filtra prêmios/movimentações por estratégia (`cash_put`) para não misturar lançamentos de `covered_call`.
  - Aba `Ranking` agora exibe alerta didático automático quando faltar `bid+ask` em massa (ou 100% dos casos), explicando por que o Top pode ficar vazio e os itens irem para Watchlist.
  - Aba `Fundamentus` agora traduz motivos de reprovação para texto didático, mostra alerta de defasagem entre snapshots e prioriza preço conservador em PUT (`best_bid` antes de `ultimo`).
  - Ranking de PUTs na aba `Fundamentus` ganhou score didático (segurança/renda/qualidade/execução), perfil (`Conservadora/Equilibrada/Agressiva`) e novos parâmetros na tela de configurações.
  - Aba `Posições` ganhou novos motivos de encerramento/parcial no dropdown (`Recompra para encerrar`, `Rolagem`, `Exercício`, `Vencimento sem valor`, `Ajuste manual`), mantendo compatibilidade com motivos antigos já salvos.
  - Encerramento manual de opção vendida na aba `Posições` agora sincroniza automaticamente a recompra no `ledger` (tipo `BUY`), sem duplicar lançamento em salvamentos repetidos.
  - Fluxos de prêmio/DARF passaram a validar se o ticker é realmente opção (`infer_option_type`), evitando tratar lote de ação como opção quando houver ticker/ativo divergente por erro de cadastro.
  - Aba `Auditoria` agora mostra também visão de caixa operacional por posição (`Prêmio + DARF + Recompra`), além da visão fiscal, para refletir melhor o efeito real no caixa do cliente.
