import argparse
import asyncio
from pathlib import Path
from typing import List, Optional

from .scraper.run import scrape_all
from .enrich import enrich_csv


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


if __name__ == "__main__":
    main()
