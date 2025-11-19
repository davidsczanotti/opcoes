import argparse
import asyncio
import datetime as dt
from pathlib import Path
from typing import List, Optional

from .scraper.run import scrape_all
from .enrich import enrich_csv
from .portfolio import add_position, list_positions, close_position
from .report import generate_report
from .snapshot_export import export_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="opcoes",
        description="Coletor diário de opções CALLs Europeias do opcoes.net.br",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scrape", help="Executa a coleta")
    sc.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Lista de papéis separados por vírgula (ex.: ABEV3,BBAS3). Padrão: todos.",
    )
    sc.add_argument(
        "--max-symbols",
        type=int,
        default=None,
        help="Limita a quantidade de papéis processados (para testes).",
    )
    sc.add_argument(
        "--output",
        type=Path,
        default=Path("data/opcoes_calls_eu.csv"),
        help="Arquivo CSV de saída (default: data/opcoes_calls_eu.csv)",
    )
    sc.add_argument(
        "--headful",
        action="store_true",
        help="Abre o navegador visível (debug).",
    )
    sc.add_argument(
        "--throttle",
        type=float,
        default=1.0,
        help="Atraso (segundos) entre ações para simular ritmo humano.",
    )
    sc.add_argument(
        "--goto-timeout",
        type=int,
        default=60000,
        help="Timeout do page.goto em milissegundos (default: 60000).",
    )
    sc.add_argument(
        "--proxy-server",
        type=str,
        default=None,
        help="Proxy HTTP/HTTPS, ex.: http://host:3128 ou socks5://host:1080",
    )
    sc.add_argument(
        "--proxy-username",
        type=str,
        default=None,
        help="Usuário para autenticação no proxy (opcional).",
    )
    sc.add_argument(
        "--proxy-password",
        type=str,
        default=None,
        help="Senha para autenticação no proxy (opcional).",
    )
    sc.add_argument(
        "--fundamentals",
        type=Path,
        default=None,
        help=(
            "CSV opcional com fundamentos por ticker para calcular earnings_yield/PE. "
            "Colunas aceitas: ticker e (earnings_yield_ttm | pe_ttm | lpa_ttm + preco | "
            "lucro_liquido_ttm + acoes_total + preco)."
        ),
    )
    sc.add_argument(
        "--statusinvest",
        action="store_true",
        help=(
            "Obtém P/L e E/P automaticamente do Status Invest para os papéis processados. "
            "Equivale a fornecer fundamentos externos, porém baixados online."
        ),
    )

    ec = sub.add_parser("enrich", help="Enriquece um CSV existente com E/P e P/L")
    ec.add_argument(
        "--input",
        type=Path,
        default=Path("data/opcoes_calls_eu.csv"),
        help="Arquivo CSV de entrada a enriquecer (default: data/opcoes_calls_eu.csv)",
    )
    ec.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Arquivo CSV de saída (default: sobrescreve o de entrada)",
    )
    ec.add_argument(
        "--fundamentals",
        type=Path,
        default=None,
        help=(
            "CSV opcional com fundamentos (ticker + earnings_yield_ttm | pe_ttm | lpa_ttm + preco | "
            "lucro_liquido_ttm + acoes_total + preco). Se não informado, pode usar --statusinvest."
        ),
    )
    ec.add_argument(
        "--statusinvest",
        action="store_true",
        help=(
            "Baixa P/L do Status Invest e calcula E/P. Se usado, ignora --fundamentals."
        ),
    )
    ec.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Timeout por requisição ao Status Invest (s).",
    )
    ec.add_argument(
        "--throttle",
        type=float,
        default=0.8,
        help="Atraso entre requisições ao Status Invest (s).",
    )
    ec.add_argument(
        "--only-units",
        action="store_true",
        help="Ao usar Status Invest, preencher apenas para Units (ignora demais).",
    )

    pc = sub.add_parser("position", help="Gerencia posições compradas")
    pcs = pc.add_subparsers(dest="subcmd", required=True)

    pa = pcs.add_parser("add", help="Registra uma nova posição (compra)")
    pa.add_argument("--ticker", required=True, help="Ticker da opção (ex.: USIMJ605)")
    pa.add_argument("--underlying", required=True, help="Ticker do ativo base (ex.: USIM5)")
    pa.add_argument("--trade-date", required=True, help="Data da compra (YYYY-MM-DD)")
    pa.add_argument("--qty", type=int, required=True, help="Quantidade de contratos")
    pa.add_argument("--price", type=float, required=True, help="Preço pago por contrato")
    pa.add_argument("--fees", type=float, default=0.0, help="Custos/Taxas adicionais (opcional)")
    pa.add_argument("--notes", default=None, help="Observações (opcional)")

    pl = pcs.add_parser("list", help="Lista posições registradas")
    group = pl.add_mutually_exclusive_group()
    group.add_argument("--include-closed", action="store_true", help="Inclui posições fechadas")
    group.add_argument("--only-closed", action="store_true", help="Mostra apenas fechadas")
    pl.add_argument("--ticker", type=str, default=None, help="Filtra por ticker exato")

    pc_close = pcs.add_parser("close", help="Fecha uma posição aberta")
    pc_close.add_argument("--id", type=int, required=True, help="ID da posição (veja em position list)")
    pc_close.add_argument("--exit-date", required=True, help="Data de saída (YYYY-MM-DD)")
    pc_close.add_argument("--price", type=float, required=True, help="Preço de saída por contrato")

    rc = sub.add_parser("report", help="Gera relatório diário pós-scrape")
    rc.add_argument("--min-score", type=int, default=8, help="Score mínimo para oportunidades (default: 8)")
    rc.add_argument("--limit", type=int, default=20, help="Quantidade máxima de oportunidades listadas")

    sn = sub.add_parser("snapshot", help="Opera sobre snapshots diários")
    sns = sn.add_subparsers(dest="subcmd", required=True)
    se = sns.add_parser("export", help="Exporta snapshot para CSV")
    se.add_argument(
        "--date",
        type=str,
        default=None,
        help="Data do snapshot (YYYY-MM-DD). Default: última disponível.",
    )
    se.add_argument(
        "--output",
        type=Path,
        default=Path("data/opcoes_latest.csv"),
        help="Arquivo CSV de saída (default: data/opcoes_latest.csv)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.cmd == "scrape":
        symbols: Optional[List[str]] = None
        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

        proxy_settings = None
        if args.proxy_server:
            proxy_settings = {"server": args.proxy_server}
            if args.proxy_username:
                proxy_settings["username"] = args.proxy_username
            if args.proxy_password:
                proxy_settings["password"] = args.proxy_password

        # Interpreta alias: --fundamentals statusinvest
        use_status_invest = bool(args.statusinvest)
        if args.fundamentals and str(args.fundamentals).lower() == "statusinvest":
            use_status_invest = True
            args.fundamentals = None

        # Executa loop assíncrono do Playwright
        asyncio.run(
            scrape_all(
                symbols=symbols,
                output_csv=args.output,
                max_symbols=args.max_symbols,
                headless=not args.headful,
                throttle_sec=args.throttle,
                goto_timeout_ms=args.goto_timeout,
                proxy_settings=proxy_settings,
                fundamentals_csv=args.fundamentals,
                use_status_invest=use_status_invest,
            )
        )
    elif args.cmd == "enrich":
        use_status_invest = bool(args.statusinvest)
        fundamentals_csv = args.fundamentals
        # Alias: --fundamentals statusinvest
        if fundamentals_csv and str(fundamentals_csv).lower() == "statusinvest":
            use_status_invest = True
            fundamentals_csv = None

        output = enrich_csv(
            input_csv=args.input,
            output_csv=args.output,
            use_status_invest=use_status_invest,
            fundamentals_csv=fundamentals_csv,
            timeout=args.timeout,
            throttle=args.throttle,
            only_units=bool(getattr(args, "only_units", False)),
        )
        print(f"CSV enriquecido em: {output}")
    elif args.cmd == "position":
        if args.subcmd == "add":
            trade_date = _parse_trade_date(args.trade_date)
            pos_id = add_position(
                ticker=args.ticker,
                underlying=args.underlying,
                trade_date=trade_date,
                qty=args.qty,
                entry_price=args.price,
                fees=args.fees,
                notes=args.notes,
            )
            print(f"Posição registrada com ID {pos_id}.")
        elif args.subcmd == "list":
            positions = list_positions(
                include_closed=args.include_closed, only_closed=args.only_closed, ticker=args.ticker
            )
            if not positions:
                print("Nenhuma posição encontrada.")
            else:
                _print_positions(positions)
        elif args.subcmd == "close":
            exit_date = _parse_trade_date(args.exit_date)
            try:
                close_position(position_id=args.id, exit_date=exit_date, exit_price=args.price)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            print(f"Posição {args.id} fechada em {exit_date} a {args.price:.2f}.")
    elif args.cmd == "report":
        data = generate_report(min_score=args.min_score, limit=args.limit)
        _print_report(data)
    elif args.cmd == "snapshot":
        if args.subcmd == "export":
            try:
                date = _parse_trade_date(args.date) if args.date else None
            except SystemExit:
                raise SystemExit("Data inválida em --date. Use o formato YYYY-MM-DD.") from None
            try:
                out = export_snapshot(output_csv=args.output, snapshot_date=date)
            except RuntimeError as exc:
                raise SystemExit(str(exc)) from exc
            print(f"Snapshot exportado para: {out}")
    else:
        raise SystemExit(f"Comando desconhecido: {args.cmd}")


def _parse_trade_date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:  # noqa: F841
        raise SystemExit("Data inválida. Use o formato YYYY-MM-DD.") from exc
    return parsed.isoformat()


def _format_currency(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _format_percent(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _print_positions(positions: List[dict]) -> None:
    header = (
        f"{'ID':>4} {'Ticker':<10} {'Data':<10} {'Qtd':>5} "
        f"{'Preço':>10} {'Último':>10} {'P/L':>12} {'P/L%':>8} {'Score':>5} {'Trend':>5}"
    )
    print(header)
    print("-" * len(header))
    for pos in positions:
        print(
            f"{pos['id']:>4} "
            f"{pos['ticker']:<10} "
            f"{pos['trade_date']:<10} "
            f"{pos['qty']:>5d} "
            f"{_format_currency(pos['entry_price']):>10} "
            f"{_format_currency(pos['last_price']):>10} "
            f"{_format_currency(pos['pl']):>12} "
            f"{_format_percent(pos['pl_pct']):>8} "
            f"{(pos['score_total'] or '-'):>5} "
            f"{(pos['trend_flag'] or '-'):>5}"
        )


def _format_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _print_report(data) -> None:
    print(f"Snapshot mais recente: {data.snapshot_date}")
    print("\nTop oportunidades:")
    if not data.opportunities:
        print("  Nenhuma opção com score dentro do filtro.")
    else:
        header = (
            f"{'Ticker':<10} {'Und':<6} {'Score':>5} {'Último':>10} "
            f"{'%2x':>8} {'Custo%':>8} {'IV':>4} {'EM2x':>5} {'Fluxo':>8}"
        )
        print(header)
        print("-" * len(header))
        for opp in data.opportunities:
            print(
                f"{opp['ticker']:<10} {opp['underlying']:<6} "
                f"{(opp['score_total'] or '-'):>5} "
                f"{_format_currency(opp['ultimo']):>10} "
                f"{_format_number(opp.get('%_Alta_p_2x')):>8} "
                f"{_format_number(opp.get('custo_pct')):>8} "
                f"{(opp.get('iv_score') if opp.get('iv_score') is not None else '-'):>4} "
                f"{(opp.get('em2x_score') if opp.get('em2x_score') is not None else '-'):>5} "
                f"{_format_number(opp.get('vol_fluxo_5d')):>8}"
            )
    print("\nPosições abertas:")
    positions = data.positions
    if not positions:
        print("  Nenhuma posição aberta.")
    else:
        _print_positions(positions)
    if data.alerts:
        print("\nAlertas:")
        for alert in data.alerts:
            pos = alert["position"]
            reasons = "; ".join(alert["reasons"])
            print(f"  - {pos['ticker']} ({pos['trade_date']}): {reasons}")
    else:
        print("\nAlertas: nenhum.")


if __name__ == "__main__":
    main()
