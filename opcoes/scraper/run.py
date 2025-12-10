from __future__ import annotations

import contextlib
import math
import re
import statistics
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yfinance as yf
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
    Locator,
    Page,
)

from .selectors import (
    BASE_URL,
    SELECT_ALL_TYPES_LABEL,
    SELECT_ALL_TYPES_RADIO,
    SELECT_ID_ACAO,
    SELECT_ID_LISTA,
    SELECT_MOD_FILTER,
    SLIDER_STRIKE_HANDLES,
    SLIDER_STRIKE_TRACK,
    TABELA_LENGTH,
    TABELA_TBODY_ROWS,
    VENCIMENTOS_CHECKBOXES,
    VENCIMENTOS_CONTAINER,
)
from .storage import append_rows_dedup, load_existing_tickers
from .fundamentals import load_earnings_yield_map
from .statusinvest import fetch_fundamentals_map
from .prices import PriceIndicators, fetch_price_indicators
from .ivrank import IVRankStore
from .activity import FlowStore
from .snapshots import SnapshotDB
from .far_expirations import fetch_far_expiration_quotes
from ..utils import infer_option_type
from .. import quant
from .health import check_health


# Número de vencimentos a selecionar no filtro da tela.
# None -> seleciona todos os vencimentos disponíveis.
MAX_VENCIMENTOS: Optional[int] = None
PROCESSING_OVERLAY = "#tblListaOpc_processing"


async def scrape_all(
    *,
    symbols: Optional[Sequence[str]] = None,
    output_csv: Path,
    max_symbols: Optional[int] = None,
    headless: bool = True,
    throttle_sec: float = 1.0,
    goto_timeout_ms: int = 60000,
    proxy_settings: Optional[Dict[str, str]] = None,
    fundamentals_csv: Optional[Path] = None,
    use_status_invest: bool = False,
) -> None:
    output_csv = Path(output_csv)
    existing_tickers = load_existing_tickers(output_csv)
    fundamentals_map: Dict[str, tuple] = {}
    async with async_playwright() as p:
        launch_kwargs = {"headless": headless}
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
        # WSL/containers costumam ter /dev/shm pequeno; evita crash prematuro do Chromium.
        launch_kwargs["args"] = launch_kwargs.get("args", []) + ["--disable-dev-shm-usage"]
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=max(goto_timeout_ms, 1000),
        )
        await _wait_idle(page)

        # Health Check antes de prosseguir
        await check_health(page)

        await _select_all_underlyings(page)
        await _wait_idle(page)

        available = await _collect_symbols(page)
        target_symbols = _filter_symbols(available, symbols)
        if max_symbols is not None:
            target_symbols = target_symbols[:max_symbols]

        if not target_symbols:
            print("Nenhum papel selecionado.")
            await browser.close()
            return

        total_written = 0
        total_symbols = len(target_symbols)

        unique_symbols = list(dict.fromkeys(target_symbols))
        # Carrega fundamentos por fonte escolhida (uma vez, antes do loop)
        if use_status_invest:
            try:
                fundamentals_map = fetch_fundamentals_map(unique_symbols)
            except Exception as exc:  # noqa: BLE001
                print(f"Aviso: falhou Status Invest: {exc}")
                fundamentals_map = {}
        elif fundamentals_csv:
            fundamentals_map = load_earnings_yield_map(Path(fundamentals_csv))
        price_map: Dict[str, PriceIndicators] = {}
        try:
            price_map = fetch_price_indicators(unique_symbols)
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: falhou preço subjacente: {exc}")
            price_map = {}
        snapshot_date = dt.date.today().isoformat()
        iv_store: Optional[IVRankStore] = None
        try:
            iv_store = IVRankStore(Path("data/iv_history.db"))
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: falhou inicializar histórico de IV: {exc}")
            iv_store = None
        flow_store: Optional[FlowStore] = None
        try:
            flow_store = FlowStore(Path("data/flow_history.db"))
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: falhou histórico de fluxo: {exc}")
            flow_store = None
        snapshot_db: Optional[SnapshotDB] = None
        try:
            snapshot_db = SnapshotDB(Path("data/opcoes_snapshots.db"))
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: falhou snapshots DB: {exc}")
            snapshot_db = None
        snapshot_rows: List[Dict[str, str]] = []
        far_quotes: Dict[str, dict] = {}
        try:
            far_quotes = fetch_far_expiration_quotes()
            if far_quotes:
                print(f"Livro vencimentos longos carregado ({len(far_quotes)} tickers).")
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: não foi possível carregar book de vencimentos longos: {exc}")

        for idx, symbol in enumerate(target_symbols, start=1):
            print(f"[{idx}/{total_symbols}] Processando {symbol}…")
            if page.is_closed():
                page = await context.new_page()
                await page.goto(
                    BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=max(goto_timeout_ms, 1000),
                )
                await _wait_idle(page)
                await _select_all_underlyings(page)
            try:
                rows = await _scrape_symbol(
                    page,
                    symbol,
                    throttle_sec=throttle_sec,
                    goto_timeout_ms=goto_timeout_ms,
                    far_quotes=far_quotes,
                )
            except Exception as exc:  # noqa: BLE001 – queremos continuar
                print(f"  -> erro ao processar {symbol}: {exc}")
                continue

            if not rows:
                print("  -> sem resultados.")
                continue

            # Fallback/auditoria: tenta completar bid/ask via Yahoo Finance
            # apenas para contratos que ainda não têm ask válido vindo do opcoes.net.
            try:
                _enrich_rows_with_yahoo_options(symbol, rows)
            except Exception as exc:  # noqa: BLE001
                print(f"Aviso: falhou enriquecimento via Yahoo para {symbol}: {exc}")

            site_price, site_price_date = await _extract_site_price(page)

            # Anota indicadores por papel subjacente se disponíveis
            if fundamentals_map:
                ey, pe = fundamentals_map.get(symbol, (None, None))
                ey_str = f"{ey:.6f}" if (ey is not None) else ""
                pe_str = f"{pe:.6f}" if (pe is not None) else ""
                for r in rows:
                    r["earnings_yield_ttm"] = ey_str
                    r["pe_ttm"] = pe_str
            price_info = price_map.get(symbol)
            if price_info:
                # Mescla preço do site apenas se ele for mais recente
                # que o do Yahoo Finance (ou se não houver dado do Yahoo).
                yf_date = None
                site_date = None
                if price_info.price_date:
                    with contextlib.suppress(ValueError):
                        yf_date = dt.date.fromisoformat(str(price_info.price_date))
                if site_price_date:
                    with contextlib.suppress(ValueError):
                        site_date = dt.date.fromisoformat(str(site_price_date))
                use_site_price = False
                if site_date and (yf_date is None or site_date >= yf_date):
                    use_site_price = True
                elif site_price is not None and price_info.price is None:
                    # Sem data, mas temos preço e o Yahoo não retornou preço.
                    use_site_price = True
                if use_site_price:
                    if site_price is not None:
                        price_info.price = site_price
                    if site_price_date:
                        price_info.price_date = site_price_date
            elif site_price is not None or site_price_date:
                price_info = PriceIndicators(
                    price=site_price,
                    price_date=site_price_date,
                    mm200=None,
                    return_3m=None,
                    trend_flag=None,
                    trend_reason="",
                )
                price_map[symbol] = price_info
            if price_info:
                for r in rows:
                    r["underlying_price"] = _format_decimal(price_info.price, decimals=2, signed=False)
                    r["underlying_price_date"] = price_info.price_date or ""
                    r["underlying_mm200"] = _format_decimal(price_info.mm200, decimals=2, signed=False)
                    r["underlying_return_3m"] = _format_decimal(price_info.return_3m, decimals=2, signed=False)
                    r["trend_flag"] = str(price_info.trend_flag) if price_info.trend_flag is not None else ""
                    r["trend_reason"] = price_info.trend_reason
                    # Preenche preços adicionais (teórico, spread, ask)
                    theo_price = _compute_theoretical_price(r, spot_price=price_info.price)
                    if theo_price is not None:
                        r["preco_teorico"] = _format_decimal(theo_price, decimals=2, signed=False)
                    spread_pct = _compute_spread_pct(r)
                    if spread_pct is not None:
                        r["spread_pct"] = _format_decimal(spread_pct, decimals=2, signed=False)
                    price_buy = _price_for_buy(r, spot_price=price_info.price)
                    
                    distorcao_pct = None
                    if price_buy is not None and theo_price is not None:
                        distorcao_pct = quant.calculate_price_distortion(price_buy, theo_price)
                    if distorcao_pct is not None:
                        r["distorcao_preco_pct"] = _format_decimal(distorcao_pct, decimals=2, signed=False)
                        if abs(distorcao_pct) > 10.0:
                            r["distorcao_flag"] = "1"
                    
                    vol = _parse_float(r.get("vol_impl_perc"))
                    days = _parse_float(r.get("dias_uteis"))
                    strike = _parse_float(r.get("strike"))
                    
                    prob_itm = None
                    if strike and vol and days:
                        prob_itm = quant.calculate_probability_itm(price_info.price, strike, vol, days)
                    if prob_itm is not None:
                        r["prob_itm_pct"] = _format_decimal(prob_itm * 100.0, decimals=1, signed=False)
                    
                    pct_to_double = _parse_float(r.get("%_Alta_p_2x"))
                    prob_2x = None
                    if pct_to_double and vol and days:
                        prob_2x = quant.calculate_probability_move(price_info.price, pct_to_double, vol, days)
                    if prob_2x is not None:
                        r["prob_2x_pct"] = _format_decimal(prob_2x * 100.0, decimals=1, signed=False)
                    
                    cost_pct = None
                    if price_buy is not None:
                        cost_pct = quant.calculate_cost_pct(price_buy, price_info.price)
                    r["custo_pct"] = _format_decimal(cost_pct, decimals=2, signed=False) if cost_pct is not None else ""
                    
                    intrinsic, extrinsic = None, None
                    if price_buy is not None and strike is not None:
                        intrinsic, extrinsic = quant.calculate_intrinsic_extrinsic(price_buy, strike, price_info.price)
                    r["intrinsic_value"] = _format_decimal(intrinsic, decimals=2, signed=False) if intrinsic is not None else ""
                    r["extrinsic_value"] = _format_decimal(extrinsic, decimals=2, signed=False) if extrinsic is not None else ""
                    
                    extrinsic_pct = None
                    if extrinsic is not None:
                        extrinsic_pct = quant.calculate_extrinsic_pct(extrinsic, price_info.price)
                    r["extrinsic_pct_spot"] = _format_decimal(extrinsic_pct, decimals=2, signed=False) if extrinsic_pct is not None else ""
                    
                    be_price, be_dist = None, None
                    if strike is not None and price_buy is not None:
                        be_price, be_dist = quant.calculate_breakeven(price_info.price, strike, price_buy)
                    if be_price is not None:
                        r["breakeven_price"] = _format_decimal(be_price, decimals=2, signed=False)
                    if be_dist is not None:
                        r["breakeven_dist_pct"] = _format_decimal(be_dist, decimals=2, signed=False)
                    
                    status_remoto = quant.classify_remote_bet(prob_itm * 100.0 if prob_itm is not None else None, extrinsic_pct, days)
                    r["Status_Remoto"] = status_remoto
            else:
                            for r in rows:
                                r["custo_pct"] = ""
                                r["intrinsic_value"] = ""
                                r["extrinsic_value"] = ""
                                r["extrinsic_pct_spot"] = ""
                                r["breakeven_price"] = ""
                                r["breakeven_dist_pct"] = ""
                                r["prob_itm_pct"] = ""
                                r["prob_2x_pct"] = ""
                                r["Status_Remoto"] = ""
                
                            iv_summary = _summarize_iv(rows)
                            iv_ranks: Dict[Tuple[str, str], Optional[float]] = {}
                            if iv_store and iv_summary:
                                entries = [
                                    (key_underlying, key_venc, snapshot_date, value)
                                    for (key_underlying, key_venc), value in iv_summary.items()
                                    if value is not None
                                ]
                                iv_store.record_many(entries)
                                for (key_underlying, key_venc), value in iv_summary.items():
                                    if value is None:
                                        continue
                                    rank = iv_store.rank_for(key_underlying, key_venc, snapshot_date, value)
                                    iv_ranks[(key_underlying, key_venc)] = rank
                
                            flow_records: List[Tuple[str, str, Optional[float], Optional[float]]] = []
                            flow_ratios: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
                            if flow_store:
                                for r in rows:
                                    ticker = _normalize_ticker(r.get("ticker"))
                                    if not ticker:
                                        continue
                                    vol = _parse_float(r.get("vol_financeiro"))
                                    num = _parse_float(r.get("num_neg"))
                                    if vol is None and num is None:
                                        continue
                                    avg_vol, avg_num = flow_store.averages(ticker, snapshot_date)
                                    ratio_vol = vol / avg_vol if avg_vol and vol is not None and avg_vol > 0 else None
                                    ratio_num = num / avg_num if avg_num and num is not None and avg_num > 0 else None
                                    flow_ratios[ticker] = (ratio_vol, ratio_num)
                                    flow_records.append((ticker, snapshot_date, vol, num))
                                flow_store.record_many(flow_records)
                
                            for r in rows:
                                ticker_key = _normalize_ticker(r.get("ticker"))
                                ratios = flow_ratios.get(ticker_key)
                                if ratios:
                                    vol_ratio, num_ratio = ratios
                                    r["vol_fluxo_5d"] = _format_decimal(vol_ratio, decimals=2, signed=False) if vol_ratio is not None else ""
                                    r["num_fluxo_5d"] = _format_decimal(num_ratio, decimals=2, signed=False) if num_ratio is not None else ""
                                else:
                                    r["vol_fluxo_5d"] = ""
                                    r["num_fluxo_5d"] = ""
                
                            for r in rows:
                                key = (_normalize_underlying(r.get("underlying")), r.get("vencimento", ""))
                                rank = iv_ranks.get(key)
                                iv_pts = 0.0
                                if rank is not None:
                                    rank_str = _format_decimal(rank, decimals=1, signed=False)
                                    iv_pts = quant.score_iv_rank(rank, _parse_float(r.get("vol_impl_perc")))
                                    r["iv_rank_180d"] = rank_str
                                    r["iv_score"] = _format_decimal(iv_pts, decimals=2, signed=False)
                                else:
                                    r["iv_rank_180d"] = ""
                                    r["iv_score"] = ""
                                
                                final_score = quant.calculate_weighted_score(
                                    moneyness_score=_parse_float(r.get("moneyness_score")) or 0.0,
                                    prob_itm_pct=_parse_float(r.get("prob_itm_pct")),
                                    prob_itm_delta_pct=_parse_float(r.get("prob_itm_delta_pct")),
                                    extrinsic_pct_spot=_parse_float(r.get("extrinsic_pct_spot")),
                                    liquidity_score=_parse_float(r.get("liquidez_score")) or 0.0,
                                    iv_score=iv_pts,
                                    theta_score=_parse_float(r.get("theta_score")) or 0.0,
                                    em2x_score=_parse_float(r.get("em2x_score")) or 0.0,
                                    double_score=_parse_float(r.get("dobro_score")) or 0.0,
                                    status_remote=r.get("Status_Remoto") or ""
                                )
                                r["score_total"] = _format_decimal(final_score, decimals=2, signed=False)
                                _apply_penalties(r)
                
            snapshot_rows.extend(rows)
            written = append_rows_dedup(output_csv, rows, existing_tickers)
            total_written += written
            print(f"  -> {len(rows)} linhas coletadas (novas: {written}).")

        await browser.close()
        if iv_store:
            iv_store.close()
        if flow_store:
            flow_store.close()
        detected_snapshot_date = _infer_snapshot_date(snapshot_rows)
        if detected_snapshot_date:
            snapshot_date = detected_snapshot_date
        if snapshot_db:
            snapshot_db.record_underlyings(snapshot_date, price_map, target_symbols)
            snapshot_db.record_options(snapshot_date, snapshot_rows)
            snapshot_db.close()
        print(f"Concluído. Novos registros gravados: {total_written}. Arquivo: {output_csv}")


async def _scrape_symbol(
    page: Page,
    symbol: str,
    *,
    throttle_sec: float,
    goto_timeout_ms: int,
    far_quotes: Optional[Dict[str, dict]] = None,
) -> List[Dict[str, str]]:
    await page.select_option(SELECT_ID_ACAO, value=symbol)
    await _wait_table_update(page, throttle_sec)

    await _ensure_all_option_types(page)
    await _wait_table_update(page, throttle_sec)

    await _select_last_vencimentos(page, MAX_VENCIMENTOS)
    await _wait_table_update(page, throttle_sec)

    await _stretch_strike_slider(page)
    await _wait_table_update(page, throttle_sec)

    await _clear_modalidade_filter(page)
    await _wait_table_update(page, throttle_sec)

    await _show_all_rows(page)
    await _wait_table_update(page, throttle_sec)

    rows = await _collect_table_rows(page, symbol, far_quotes=far_quotes or {})
    return rows


async def _select_all_underlyings(page: Page) -> None:
    """Seleciona a lista completa de ativos antes de coletar os tickers."""

    select = page.locator(SELECT_ID_LISTA)
    if not await select.count():
        return

    current = await select.input_value()
    has_all_option = await page.locator(f'{SELECT_ID_LISTA} option[value="TA"]').count() > 0
    if current == "TA" and has_all_option:
        return

    if has_all_option:
        await select.select_option(value="TA")
    else:
        options = select.locator("option")
        total = await options.count()
        for idx in range(total):
            opt = options.nth(idx)
            text = (await opt.inner_text()).strip().lower()
            if "todos" in text and "ativo" in text:
                value = await opt.get_attribute("value")
                if value:
                    await select.select_option(value=value)
                else:
                    await opt.click()
                break
    await _wait_idle(page)
    await page.wait_for_timeout(400)


async def _collect_symbols(page: Page) -> List[str]:
    return await page.eval_on_selector_all(
        f"{SELECT_ID_ACAO} option",
        "options => options.map(o => o.value).filter(v => v)",
    )


def _filter_symbols(available: Sequence[str], requested: Optional[Sequence[str]]) -> List[str]:
    if not requested:
        return list(available)
    requested_list: List[str] = []
    missing: List[str] = []
    available_set = set(available)
    for sym in requested:
        if sym in available_set:
            requested_list.append(sym)
        else:
            missing.append(sym)
    if missing:
        print(f"Aviso: papéis não encontrados e serão ignorados: {', '.join(missing)}")
    return requested_list


async def _ensure_all_option_types(page: Page) -> None:
    radio = page.locator(SELECT_ALL_TYPES_RADIO)
    if await radio.count():
        if not await radio.is_checked():
            await radio.check()
        return
    label = page.locator(SELECT_ALL_TYPES_LABEL)
    if await label.count():
        await label.click()


async def _select_last_vencimentos(page: Page, total: Optional[int]) -> None:
    container = page.locator(VENCIMENTOS_CONTAINER)
    await container.scroll_into_view_if_needed()
    checkboxes = page.locator(VENCIMENTOS_CHECKBOXES)
    count = await checkboxes.count()
    if count == 0:
        return
    for idx in range(count):
        await checkboxes.nth(idx).set_checked(False, force=True)

    indices = list(range(count))
    if total is not None:
        indices = await checkboxes.evaluate_all(
            """
            (nodes, total) => {
                const parsed = nodes.map((node, index) => {
                    const raw = node.value || node.getAttribute('value') || (node.id || '').replace(/^v/, '');
                    const time = raw ? Date.parse(raw) : Number.NaN;
                    return { index, time: Number.isNaN(time) ? null : time };
                });
                const withDate = parsed.filter(item => item.time !== null)
                    .sort((a, b) => a.time - b.time)
                    .map(item => item.index);
                const fallback = parsed.map(item => item.index);
                const order = withDate.length ? withDate : fallback;
                if (!order.length) {
                    return [];
                }
                const n = Math.max(1, Math.min(total, order.length));
                // Seleciona os vencimentos mais próximos (datas menores primeiro)
                return order.slice(0, n);
            }
            """,
            total,
        )

    for idx in indices:
        await checkboxes.nth(idx).set_checked(True, force=True)


async def _stretch_strike_slider(page: Page) -> None:
    track = page.locator(SLIDER_STRIKE_TRACK)
    handles = page.locator(SLIDER_STRIKE_HANDLES)
    if await handles.count() < 2:
        return
    box = await track.bounding_box()
    if not box:
        return
    center_y = box["y"] + box["height"] / 2

    async def drag_handle(handle: Locator, target_x: float) -> None:
        hb = await handle.bounding_box()
        if not hb:
            return
        start_x = hb["x"] + hb["width"] / 2
        start_y = hb["y"] + hb["height"] / 2
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(target_x, center_y, steps=6)
        await page.mouse.up()

    await drag_handle(handles.nth(0), box["x"])
    await drag_handle(handles.nth(1), box["x"] + box["width"])


async def _clear_modalidade_filter(page: Page) -> None:
    """Tenta limpar o filtro de modalidade para trazer A e E."""

    select = page.locator(SELECT_MOD_FILTER)
    count = await select.count()
    if not count:
        return
    for idx in range(count):
        # Valor vazio representa "todas" na DataTable; se falhar, ignoramos.
        await select.nth(idx).select_option(value="")


async def _show_all_rows(page: Page) -> None:
    select = page.locator(TABELA_LENGTH)
    if not await select.count():
        return

    value = await select.evaluate("el => el.options.length ? el.options[el.options.length - 1].value : null")
    if value:
        await select.select_option(value)


async def _collect_table_rows(page: Page, underlying: str, far_quotes: Dict[str, dict]) -> List[Dict[str, str]]:
    rows_locator = page.locator(TABELA_TBODY_ROWS)
    rows_count = await rows_locator.count()
    if rows_count == 0:
        return []
    records: List[Dict[str, str]] = []
    for idx in range(rows_count):
        row = rows_locator.nth(idx)
        classes = await row.get_attribute("class") or ""
        if "dataTables_empty" in classes:
            continue
        cells = await row.locator("td").all_inner_texts()
        if len(cells) < 25:
            continue
        cells = [c.strip() for c in cells]
        record = _build_row_dict(underlying, cells)
        _merge_far_quote(record, far_quotes)
        records.append(record)
    return records


def _build_row_dict(underlying: str, cells: Sequence[str]) -> Dict[str, str]:
    """Mapeia células da tabela para o dict interno.

    Quando habilitamos CALLs e PUTs, a tabela ganha uma coluna extra (Tipo)
    após Dias Úteis. Detectamos essa coluna e ajustamos o deslocamento.
    """

    option_type_cell = ""
    shift = 0
    if len(cells) >= 6:
        maybe_type = (cells[3] or "").strip().upper()
        if maybe_type in {"CALL", "PUT", "CALLS", "PUTS"}:
            option_type_cell = maybe_type
            shift = 1

    def col(idx: int) -> str:
        return cells[idx + shift] if idx + shift < len(cells) else ""

    record = {
        "underlying": underlying,
        "ticker": cells[0],
        "option_type": option_type_cell or infer_option_type(cells[0]) or "",
        "vencimento": cells[1],
        "dias_uteis": cells[2],
        "fm": col(3),
        "mod": col(4),
        "strike": col(5),
        "ai_otm": col(6),
        "dist_perc_strike": col(7),
        "ultimo": col(8),
        "var_perc": col(9),
        "data_hora": col(10),
        "num_neg": col(11),
        "vol_financeiro": col(12),
        "vol_impl_perc": col(13),
        "delta": col(14),
        "gamma": col(15),
        "theta_dolar": col(16),
        "theta_perc": col(17),
        "vega": col(18),
        "iq": col(19),
        "coberto": col(20),
        "travado": col(21),
        "descoberto": col(22),
        "titulares": col(23),
        "lancadores": col(24),
        # Reservados para best bid/ask; ficarão vazios se a tabela não expor
        "best_bid": "",
        "best_ask": "",
        "spread_pct": "",
        "preco_teorico": "",
    }
    _apply_status_indicators(record)
    return record


def _merge_far_quote(record: Dict[str, str], far_quotes: Dict[str, dict]) -> None:
    if not far_quotes:
        return
    ticker = _normalize_ticker(record.get("ticker"))
    if not ticker:
        return
    quote = far_quotes.get(ticker)
    if not quote:
        return
    bid = quote.get("best_bid")
    ask = quote.get("best_ask")
    if bid is not None:
        record["best_bid"] = _format_decimal(float(bid), decimals=2, signed=False)
    if ask is not None:
        record["best_ask"] = _format_decimal(float(ask), decimals=2, signed=False)
    if not record.get("vol_impl_perc"):
        vol = quote.get("vol_impl_ask") or quote.get("vol_impl_bid")
        if vol is not None:
            record["vol_impl_perc"] = _format_decimal(float(vol) * 100.0, decimals=1, signed=False)
    if not record.get("ultimo"):
        last = quote.get("ultimo")
        if last is not None:
            record["ultimo"] = _format_decimal(float(last), decimals=2, signed=False)


def _to_yahoo_symbol(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    s = symbol.strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    return f"{s}.SA"


def _enrich_rows_with_yahoo_options(underlying: str, rows: List[Dict[str, str]]) -> None:
    """Usa yfinance.option_chain como fallback/auditoria para bid/ask.

    - Não mexe em linhas que já tenham best_ask > 0.
    - Só busca expirações/strikes necessários para as linhas sem ask.
    - Não falha o scraper em caso de erro na API do Yahoo.
    """

    yahoo_symbol = _to_yahoo_symbol(underlying)
    if not yahoo_symbol:
        return

    # Mapa expiração ISO -> strikes (aprox. 2 casas) que precisam de ask.
    needed_by_exp: Dict[str, set[float]] = {}
    for row in rows:
        existing_ask = _parse_float(row.get("best_ask"))
        if existing_ask is not None and existing_ask > 0:
            continue
        venc = (row.get("vencimento") or "").strip()
        strike = _parse_float(row.get("strike"))
        if not venc or strike is None:
            continue
        try:
            exp_date = dt.datetime.strptime(venc, "%d/%m/%Y").date()
        except ValueError:
            continue
        exp_str = exp_date.isoformat()
        strikes = needed_by_exp.setdefault(exp_str, set())
        strikes.add(round(strike, 2))

    if not needed_by_exp:
        return

    try:
        ticker = yf.Ticker(yahoo_symbol)
        available_exps = set(ticker.options or [])
    except Exception as exc:  # noqa: BLE001
        print(f"Aviso: falhou option_chain para {underlying}: {exc}")
        return

    quote_map: Dict[Tuple[str, float], Tuple[Optional[float], Optional[float]]] = {}

    for exp_str, strikes in needed_by_exp.items():
        if exp_str not in available_exps:
            continue
        try:
            chain = ticker.option_chain(exp_str)
        except Exception:
            continue
        calls = getattr(chain, "calls", None)
        if calls is None or getattr(calls, "empty", False):
            continue

        for _, opt in calls.iterrows():
            strike_val = opt.get("strike")
            if strike_val is None:
                continue
            try:
                strike_f = float(strike_val)
            except (TypeError, ValueError):
                continue
            strike_key = round(strike_f, 2)
            if strike_key not in strikes:
                continue

            bid_raw = opt.get("bid")
            ask_raw = opt.get("ask")
            bid: Optional[float] = None
            ask: Optional[float] = None

            try:
                if bid_raw is not None:
                    bid_f = float(bid_raw)
                    if not math.isnan(bid_f) and bid_f > 0:
                        bid = bid_f
            except (TypeError, ValueError):
                bid = None

            try:
                if ask_raw is not None:
                    ask_f = float(ask_raw)
                    if not math.isnan(ask_f) and ask_f > 0:
                        ask = ask_f
            except (TypeError, ValueError):
                ask = None

            if bid is None and ask is None:
                continue

            prev = quote_map.get((exp_str, strike_key))
            prev_bid = prev[0] if prev else None
            prev_ask = prev[1] if prev else None
            quote_map[(exp_str, strike_key)] = (
                bid if bid is not None else prev_bid,
                ask if ask is not None else prev_ask,
            )

    if not quote_map:
        return

    for row in rows:
        venc = (row.get("vencimento") or "").strip()
        strike = _parse_float(row.get("strike"))
        if not venc or strike is None:
            continue
        try:
            exp_date = dt.datetime.strptime(venc, "%d/%m/%Y").date()
        except ValueError:
            continue
        exp_str = exp_date.isoformat()
        strike_key = round(strike, 2)
        bid, ask = quote_map.get((exp_str, strike_key), (None, None))
        if bid is None and ask is None:
            continue

        current_bid = _parse_float(row.get("best_bid"))
        current_ask = _parse_float(row.get("best_ask"))

        if bid is not None and (current_bid is None or current_bid <= 0):
            row["best_bid"] = _format_decimal(bid, decimals=2, signed=False)
        if ask is not None and (current_ask is None or current_ask <= 0):
            row["best_ask"] = _format_decimal(ask, decimals=2, signed=False)


async def _wait_table_update(page: Page, throttle_sec: float) -> None:
    await _wait_processing_overlay(page)
    await page.wait_for_timeout(max(throttle_sec, 0.2) * 1000)


async def _wait_processing_overlay(page: Page) -> None:
    overlay = page.locator(PROCESSING_OVERLAY)
    try:
        await overlay.wait_for(state="visible", timeout=1500)
    except PlaywrightTimeoutError:
        pass
    try:
        await overlay.wait_for(state="hidden", timeout=10000)
    except PlaywrightTimeoutError:
        pass


async def _wait_idle(page: Page) -> None:
    with contextlib.suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=10000)


def _apply_status_indicators(row: Dict[str, str]) -> None:
    dist = _parse_float(row.get("dist_perc_strike"))
    status_m = quant.determine_moneyness_status(row.get("ai_otm") or "", dist)
    row["Status_Moneyness"] = status_m

    pct_alta, status_2x = _double_scenario(row)
    row["%_Alta_p_2x"] = _format_decimal(pct_alta, decimals=1, signed=False)
    row["Status_2x"] = status_2x
    
    status_liq = _status_liquidez(row)
    row["Status_Liquidez"] = status_liq
    
    status_theta = _status_theta(row)
    row["Status_Theta"] = status_theta
    
    m_score = quant.score_moneyness(dist)
    l_score = quant.score_liquidity(_parse_float(row.get("num_neg")) or 0.0, _parse_float(row.get("vol_financeiro")) or 0.0, status_liq)
    d_score = quant.score_double_scenario(status_2x)
    t_score = quant.score_theta(_parse_float(row.get("theta_perc")))
    
    vol = _parse_float(row.get("vol_impl_perc")) or 0.0
    days = _parse_float(row.get("dias_uteis")) or 0.0
    em_sigma, em_ratio = quant.calculate_em_movement(vol, days, pct_alta)
    row["em_1sigma_pct"] = _format_decimal(em_sigma, decimals=1, signed=False)
    row["relacao_em_2x"] = _format_decimal(em_ratio, decimals=2, signed=False)
    em_score = quant.score_em_ratio(em_ratio)

    row["em2x_score"] = str(em_score)
    row["moneyness_score"] = _format_decimal(m_score, decimals=2, signed=False)
    row["liquidez_score"] = _format_decimal(l_score, decimals=2, signed=False)
    row["dobro_score"] = str(d_score)
    row["theta_score"] = _format_decimal(t_score, decimals=2, signed=False)
    
    delta_val = _parse_float(row.get("delta"))
    if delta_val is not None:
        prob_delta = abs(delta_val) * 100.0
        row["prob_itm_delta_pct"] = _format_decimal(prob_delta, decimals=1, signed=False)
    else:
        row["prob_itm_delta_pct"] = ""

    base_score = quant.calculate_weighted_score(
        moneyness_score=m_score,
        prob_itm_pct=_parse_float(row.get("prob_itm_pct")),
        prob_itm_delta_pct=prob_delta if delta_val is not None else None,
        extrinsic_pct_spot=_parse_float(row.get("extrinsic_pct_spot")),
        liquidity_score=l_score,
        iv_score=0.0,
        theta_score=t_score,
        em2x_score=float(em_score),
        double_score=float(d_score),
        status_remote=row.get("Status_Remoto") or ""
    )

    num_neg = _parse_float(row.get("num_neg")) or 0.0
    vol_fin = _parse_float(row.get("vol_financeiro")) or 0.0
    illiquid = num_neg < 2 and vol_fin < 1000
    row["illiquidez_flag"] = "1" if illiquid else ""
    if illiquid:
        row["score_total"] = _format_decimal(0.0, decimals=2, signed=False)
    else:
        row["score_total"] = _format_decimal(base_score, decimals=2, signed=False)


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = (
        value.strip()
        .replace("\xa0", "")
        .replace("\u2212", "-")
        .replace("−", "-")
        .replace("%", "")
        .replace("+", "")
    )
    if not cleaned:
        return None
    cleaned = (
        cleaned.replace('"', "")
        .replace("'", "")
        .replace(".", "")
        .replace(",", ".")
        .replace(" ", "")
    )
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _double_scenario(row: Dict[str, str]) -> Tuple[Optional[float], str]:
    delta = _parse_float(row.get("delta"))
    strike = _parse_float(row.get("strike"))
    dist = _parse_float(row.get("dist_perc_strike"))
    if delta is None or strike is None or dist is None:
        return None, ""
    
    spot = quant.spot_from_strike_dist(strike, dist)
    if spot is None: return None, ""
    
    option_price = _price_for_buy(row, spot_price=spot)
    if option_price is None: return None, ""
    
    pct = quant.calculate_double_upside(option_price, delta, spot)
    if pct is None: return None, ""
    
    return pct, quant.get_double_status(pct)


def _status_liquidez(row: Dict[str, str]) -> str:
    return quant.get_liquidity_status(
        _parse_float(row.get("num_neg")) or 0.0,
        _parse_float(row.get("vol_financeiro")) or 0.0
    )


def _status_theta(row: Dict[str, str]) -> str:
    return quant.get_theta_status(_parse_float(row.get("theta_perc")))


def _summarize_iv(rows: Sequence[Dict[str, str]]) -> Dict[Tuple[str, str], Optional[float]]:
    per_key: Dict[Tuple[str, str], List[float]] = {}
    for r in rows:
        underlying = _normalize_underlying(r.get("underlying"))
        venc = (r.get("vencimento") or "").strip()
        if not underlying or not venc:
            continue
        vol = _parse_float(r.get("vol_impl_perc"))
        if vol is None:
            continue
        key = (underlying, venc)
        per_key.setdefault(key, []).append(vol)
    summary: Dict[Tuple[str, str], Optional[float]] = {}
    for key, values in per_key.items():
        if not values:
            continue
        summary[key] = statistics.median(values)
    return summary


def _score_iv(rank: Optional[float], vol_impl: Optional[float] = None) -> float:
    """IV contínuo com bônus em ranks baixos e penalidade para IV cara."""

    if rank is None:
        return 0.0
    rank = max(0.0, min(100.0, rank))
    # Base trapezoide (pico em 10-60, cai depois de 60)
    if rank < 5.0:
        core = 0.0
    elif rank < 10.0:
        core = (rank - 5.0) / 5.0
    elif rank <= 60.0:
        core = 1.0
    elif rank < 90.0:
        core = (90.0 - rank) / 10.0
    else:
        core = 0.0
    core = max(0.0, min(core, 1.0)) * 2.0

    # Bônus para IV historicamente barata
    bonus = 0.0
    if rank < 20.0:
        bonus = (20.0 - rank) / 20.0 * 0.5  # até +0.5

    # Penalidade para IV esticada
    penalty = 0.0
    if rank > 80.0:
        penalty += (rank - 80.0) / 20.0 * 1.0  # até -1
    if vol_impl is not None and vol_impl > 120.0:
        penalty += min(1.0, (vol_impl - 120.0) / 80.0)  # até -1 se vol_impl > 200%

    score = core + bonus - penalty
    return max(-1.0, min(3.0, score))


def _parse_int(value: Optional[str]) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _normalize_underlying(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _normalize_ticker(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _apply_penalties(row: Dict[str, str]) -> None:
    score = _parse_float(row.get("score_total")) or 0.0
    spread = _parse_float(row.get("spread_pct"))
    if spread is not None and spread > 20.0:
        score = max(0.0, score / 2.0)
    be_dist = _parse_float(row.get("breakeven_dist_pct"))
    if be_dist is not None and be_dist > 15.0:
        score = max(0.0, score - 2.0)
    row["score_total"] = _format_decimal(score, decimals=2, signed=False)


def _price_for_buy(row: Dict[str, str], spot_price: Optional[float] = None) -> Optional[float]:
    ask = _parse_float(row.get("best_ask"))
    if ask is not None and ask > 0:
        return ask
    # Sem ask: tenta preço teórico se tivermos spot
    if spot_price is not None and spot_price > 0:
        theoretical = _compute_theoretical_price(row, spot_price=spot_price)
        if theoretical is not None and theoretical > 0:
            return theoretical
    # Último negócio só como último fallback
    last = _parse_float(row.get("ultimo"))
    if last is not None and last > 0:
        return last
    return None


def _compute_spread_pct(row: Dict[str, str]) -> Optional[float]:
    bid = _parse_float(row.get("best_bid"))
    ask = _parse_float(row.get("best_ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def _compute_theoretical_price(row: Dict[str, str], spot_price: Optional[float]) -> Optional[float]:

    if spot_price is None or spot_price <= 0:
        return None
    vol = _parse_float(row.get("vol_impl_perc"))
    strike = _parse_float(row.get("strike"))
    days = _parse_float(row.get("dias_uteis"))
    if vol is None or strike is None or days is None or days <= 0 or vol <= 0:
        return None
    try:
        return quant.calculate_black_scholes_call(spot_price, strike, vol / 100.0, days / 252.0)
    except Exception:
        return None



def _infer_snapshot_date(rows: Sequence[Dict[str, str]]) -> Optional[str]:
    dates: List[dt.date] = []
    for row in rows:
        raw = (row.get("data_hora") or "").strip()
        if len(raw) != 10 or "/" not in raw:
            continue
        day, month, year = raw.split("/")
        try:
            parsed = dt.date(int(year), int(month), int(day))
        except ValueError:
            continue
        dates.append(parsed)
    if not dates:
        return None
    latest = max(dates)
    return latest.isoformat()


async def _extract_site_price(page: Page) -> Tuple[Optional[float], Optional[str]]:
    price = None
    date_str = None
    price_locator = page.locator("#divCotacaoAtual span[data-mkt-prop='p']")
    if await price_locator.count():
        with contextlib.suppress(Exception):
            text = (await price_locator.inner_text()).strip()
            price = _parse_site_currency(text)
    date_locator = page.locator("#divCotacaoAtual span[data-mkt-prop='h']")
    if await date_locator.count():
        with contextlib.suppress(Exception):
            raw = (await date_locator.inner_text()).strip()
            date_str = _parse_site_date(raw)
    return price, date_str


def _parse_site_currency(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_site_date(text: str) -> Optional[str]:
    text = text.strip()
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        parsed = dt.date(int(year), int(month), int(day))
    except ValueError:
        return None
    return parsed.isoformat()


__all__ = ["scrape_all"]
