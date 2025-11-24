from __future__ import annotations

import contextlib
import math
import re
import statistics
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
    Locator,
    Page,
)

from .selectors import (
    BASE_URL,
    SELECT_CALLS_CHECKBOX,
    SELECT_CALLS_LABEL,
    SELECT_ID_ACAO,
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


MAX_VENCIMENTOS = 8
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
                    distorcao_pct = _distorcao_preco(price_buy, theo_price)
                    if distorcao_pct is not None:
                        r["distorcao_preco_pct"] = _format_decimal(distorcao_pct, decimals=2, signed=False)
                        if abs(distorcao_pct) > 10.0:
                            r["distorcao_flag"] = "1"
                    cost_pct = _cost_pct(row=r, spot_price=price_info.price)
                    r["custo_pct"] = _format_decimal(cost_pct, decimals=2, signed=False) if cost_pct is not None else ""
                    intrinsic, extrinsic = _intrinsic_extrinsic(row=r, spot_price=price_info.price)
                    r["intrinsic_value"] = _format_decimal(intrinsic, decimals=2, signed=False) if intrinsic is not None else ""
                    r["extrinsic_value"] = _format_decimal(extrinsic, decimals=2, signed=False) if extrinsic is not None else ""
                    extrinsic_pct = _extrinsic_pct_spot(extrinsic, spot_price=price_info.price)
                    r["extrinsic_pct_spot"] = _format_decimal(extrinsic_pct, decimals=2, signed=False) if extrinsic_pct is not None else ""
                    be_price, be_dist = _breakeven(price_info.price, r)
                    if be_price is not None:
                        r["breakeven_price"] = _format_decimal(be_price, decimals=2, signed=False)
                    if be_dist is not None:
                        r["breakeven_dist_pct"] = _format_decimal(be_dist, decimals=2, signed=False)
                    _apply_penalties(r)
            else:
                for r in rows:
                    r["custo_pct"] = ""
                    r["intrinsic_value"] = ""
                    r["extrinsic_value"] = ""
                    r["extrinsic_pct_spot"] = ""
                    r["breakeven_price"] = ""
                    r["breakeven_dist_pct"] = ""

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
                base_total = _parse_int(r.get("score_total"))
                if rank is not None:
                    rank_str = _format_decimal(rank, decimals=1, signed=False)
                    iv_pts = _score_iv(rank)
                    r["iv_rank_180d"] = rank_str
                    r["iv_score"] = str(iv_pts)
                    r["score_total"] = str(base_total + iv_pts)
                else:
                    r["iv_rank_180d"] = ""
                    r["iv_score"] = ""
                    r["score_total"] = str(base_total)

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

    await _ensure_calls_checked(page)
    await _wait_table_update(page, throttle_sec)

    await _select_last_vencimentos(page, MAX_VENCIMENTOS)
    await _wait_table_update(page, throttle_sec)

    await _stretch_strike_slider(page)
    await _wait_table_update(page, throttle_sec)

    await _set_modalidade_e(page)
    await _wait_table_update(page, throttle_sec)

    await _show_all_rows(page)
    await _wait_table_update(page, throttle_sec)

    rows = await _collect_table_rows(page, symbol, far_quotes=far_quotes or {})
    rows = [row for row in rows if (row.get("mod") or "").strip().upper() == "E"]
    return rows


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


async def _ensure_calls_checked(page: Page) -> None:
    checkbox = page.locator(SELECT_CALLS_CHECKBOX)
    if await checkbox.count() == 0:
        # fallback: clicar no label
        await page.locator(SELECT_CALLS_LABEL).click()
        return
    if not await checkbox.is_checked():
        await checkbox.check()


async def _select_last_vencimentos(page: Page, total: int) -> None:
    container = page.locator(VENCIMENTOS_CONTAINER)
    await container.scroll_into_view_if_needed()
    checkboxes = page.locator(VENCIMENTOS_CHECKBOXES)
    count = await checkboxes.count()
    if count == 0:
        return
    for idx in range(count):
        await checkboxes.nth(idx).set_checked(False, force=True)

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
            const start = Math.max(order.length - total, 0);
            return order.slice(start);
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


async def _set_modalidade_e(page: Page) -> None:
    select = page.locator(SELECT_MOD_FILTER)
    count = await select.count()
    if not count:
        return
    for idx in range(count):
        await select.nth(idx).select_option(value="E")


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
    record = {
        "underlying": underlying,
        "ticker": cells[0],
        "vencimento": cells[1],
        "dias_uteis": cells[2],
        "fm": cells[3],
        "mod": cells[4],
        "strike": cells[5],
        "ai_otm": cells[6],
        "dist_perc_strike": cells[7],
        "ultimo": cells[8],
        "var_perc": cells[9],
        "data_hora": cells[10],
        "num_neg": cells[11],
        "vol_financeiro": cells[12],
        "vol_impl_perc": cells[13],
        "delta": cells[14],
        "gamma": cells[15],
        "theta_dolar": cells[16],
        "theta_perc": cells[17],
        "vega": cells[18],
        "iq": cells[19],
        "coberto": cells[20],
        "travado": cells[21],
        "descoberto": cells[22],
        "titulares": cells[23],
        "lancadores": cells[24],
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
    status_m = _status_moneyness(row)
    row["Status_Moneyness"] = status_m
    pct_alta, status_2x = _double_scenario(row)
    row["%_Alta_p_2x"] = _format_decimal(pct_alta, decimals=1, signed=False)
    row["Status_2x"] = status_2x
    status_liq = _status_liquidez(row)
    row["Status_Liquidez"] = status_liq
    status_theta = _status_theta(row)
    row["Status_Theta"] = status_theta

    m_score = _score_moneyness(status_m)
    l_score = _score_liquidez(status_liq)
    d_score = _score_dobro(status_2x)
    t_score = _score_theta(status_theta)
    em_sigma, em_ratio = _em_movement(row)
    row["em_1sigma_pct"] = _format_decimal(em_sigma, decimals=1, signed=False)
    row["relacao_em_2x"] = _format_decimal(em_ratio, decimals=2, signed=False)
    em_score = _score_em_ratio(em_ratio)
    row["em2x_score"] = str(em_score)
    row["moneyness_score"] = str(m_score)
    row["liquidez_score"] = str(l_score)
    row["dobro_score"] = str(d_score)
    row["theta_score"] = str(t_score)
    base_score = m_score + l_score + d_score + t_score + em_score

    num_neg = _parse_float(row.get("num_neg")) or 0.0
    vol_fin = _parse_float(row.get("vol_financeiro")) or 0.0
    illiquid = num_neg < 2 and vol_fin < 1000
    row["illiquidez_flag"] = "1" if illiquid else ""
    if illiquid:
        # Penaliza fortemente liquidez pífia
        row["score_total"] = "0"
    else:
        row["score_total"] = str(base_score)


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


def _status_moneyness(row: Dict[str, str]) -> str:
    raw_state = (row.get("ai_otm") or "").upper()
    if "ITM" in raw_state:
        return "ITM"
    dist = _parse_float(row.get("dist_perc_strike"))
    if dist is None:
        return ""
    if "ATM" in raw_state and dist <= 1.0:
        return "0-5% OTM (colada)"
    if dist < 0:
        return "ITM"
    if dist <= 5:
        return "0-5% OTM (colada)"
    if dist <= 15:
        return "5-15% OTM (aposta)"
    if dist <= 20:
        return "15-20% OTM"
    return "20%+ OTM (loteria)"


def _double_scenario(row: Dict[str, str]) -> Tuple[Optional[float], str]:
    # Usa ask quando existe; se não houver ask, tenta preço teórico
    delta = _parse_float(row.get("delta"))
    strike = _parse_float(row.get("strike"))
    dist = _parse_float(row.get("dist_perc_strike"))
    if (
        delta is None
        or abs(delta) < 1e-4
        or strike is None
        or dist is None
    ):
        return None, ""

    spot = _spot_from_strike_dist(strike, dist)
    if spot is None or spot <= 0:
        return None, ""

    option_price = _price_for_buy(row, spot_price=spot)
    if option_price is None or option_price <= 0:
        return None, ""

    move_abs = option_price / abs(delta)
    if move_abs <= 0:
        return None, ""
    pct = (move_abs / spot) * 100.0
    if not math.isfinite(pct) or pct <= 0:
        return None, ""

    if pct <= 20:
        status = "Dobra com até 20% no ativo"
    elif pct <= 40:
        status = "Dobra com 20-40% no ativo"
    else:
        status = "Precisa de 40%+ no ativo"
    return pct, status


def _spot_from_strike_dist(strike: float, dist: float) -> Optional[float]:
    denom = 1 + (dist / 100.0)
    if abs(denom) < 1e-6:
        return None
    return strike / denom


def _status_liquidez(row: Dict[str, str]) -> str:
    num_neg = _parse_float(row.get("num_neg")) or 0.0
    vol_fin = _parse_float(row.get("vol_financeiro")) or 0.0
    if num_neg >= 30 or vol_fin >= 50000:
        return "Alta"
    if num_neg >= 5 or vol_fin >= 5000:
        return "Média"
    if num_neg > 0 or vol_fin > 0:
        return "Baixa"
    return ""


def _status_theta(row: Dict[str, str]) -> str:
    theta = _parse_float(row.get("theta_perc"))
    if theta is None:
        return ""
    abs_theta = abs(theta)
    if abs_theta < 0.5:
        return "Theta baixo"
    if abs_theta < 1.0:
        return "Theta médio"
    return "Theta alto"


def _format_decimal(value: Optional[float], *, decimals: int = 2, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return ""
    fmt = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    return fmt.replace(".", ",")


def _score_moneyness(label: str) -> int:
    if label == "0-5% OTM (colada)":
        return 2
    if label == "5-15% OTM (aposta)":
        return 1
    return 0


def _score_liquidez(label: str) -> int:
    if label == "Alta":
        return 2
    if label == "Média":
        return 1
    return 0


def _score_dobro(label: str) -> int:
    if label == "Dobra com até 20% no ativo":
        return 2
    if label == "Dobra com 20-40% no ativo":
        return 1
    return 0


def _score_theta(label: str) -> int:
    return 1 if label == "Theta baixo" else 0


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


def _score_iv(rank: Optional[float]) -> int:
    if rank is None:
        return 0
    rank = max(0.0, min(100.0, rank))
    if 10 <= rank <= 60:
        return 2
    if 0 <= rank < 10 or 60 < rank <= 80:
        return 1
    return 0


def _parse_int(value: Optional[str]) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _em_movement(row: Dict[str, str]) -> Tuple[Optional[float], Optional[float]]:
    vol = _parse_float(row.get("vol_impl_perc"))
    days = _parse_float(row.get("dias_uteis"))
    pct_to_double = _parse_float(row.get("%_Alta_p_2x"))
    if vol is None or days is None or days <= 0:
        return None, None
    em_sigma = (vol / 100.0) * math.sqrt(days / 252.0) * 100.0
    ratio = None
    if pct_to_double is not None and pct_to_double > 0:
        ratio = em_sigma / pct_to_double
    return em_sigma, ratio


def _score_em_ratio(ratio: Optional[float]) -> int:
    if ratio is None:
        return 0
    if ratio >= 1.0:
        return 2
    if ratio >= 0.5:
        return 1
    return 0


def _normalize_underlying(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _normalize_ticker(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _cost_pct(row: Dict[str, str], spot_price: Optional[float]) -> Optional[float]:
    if spot_price is None or spot_price <= 0:
        return None
    option_price = _price_for_buy(row, spot_price=spot_price)
    if option_price is None:
        return None
    return (option_price / spot_price) * 100.0


def _intrinsic_extrinsic(row: Dict[str, str], spot_price: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if spot_price is None or spot_price <= 0:
        return None, None
    strike = _parse_float(row.get("strike"))
    option_price = _price_for_buy(row, spot_price=spot_price)
    if strike is None or option_price is None:
        return None, None
    intrinsic = max(spot_price - strike, 0.0)
    extrinsic = max(option_price - intrinsic, 0.0)
    return intrinsic, extrinsic


def _extrinsic_pct_spot(extrinsic: Optional[float], spot_price: Optional[float]) -> Optional[float]:
    if extrinsic is None or spot_price is None or spot_price <= 0:
        return None
    return (extrinsic / spot_price) * 100.0


def _distorcao_preco(price_buy: Optional[float], price_theoretical: Optional[float]) -> Optional[float]:
    if price_buy is None or price_theoretical is None or price_theoretical <= 0:
        return None
    return ((price_buy - price_theoretical) / price_theoretical) * 100.0


def _apply_penalties(row: Dict[str, str]) -> None:
    score = _parse_int(row.get("score_total"))
    if score is None:
        score = 0
    spread = _parse_float(row.get("spread_pct"))
    if spread is not None and spread > 20.0:
        score = max(0, int(score / 2))
    be_dist = _parse_float(row.get("breakeven_dist_pct"))
    if be_dist is not None and be_dist > 15.0:
        score = max(0, score - 2)
    row["score_total"] = str(score)


def _breakeven(spot: Optional[float], row: Dict[str, str]) -> Tuple[Optional[float], Optional[float]]:
    if spot is None or spot <= 0:
        return None, None
    strike = _parse_float(row.get("strike"))
    price = _price_for_buy(row, spot_price=spot)
    if strike is None or price is None:
        return None, None
    be_price = strike + price
    dist_pct = ((be_price - spot) / spot) * 100.0
    return be_price, dist_pct


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
        return _black_scholes_call(spot_price, strike, vol / 100.0, days / 252.0)
    except Exception:
        return None


def _black_scholes_call(spot: float, strike: float, vol: float, years: float, rate: float = 0.0, div: float = 0.0) -> float:
    if spot <= 0 or strike <= 0 or vol <= 0 or years <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    # N(x) approx via error function
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2)))
    nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2)))
    return spot * math.exp(-div * years) * nd1 - strike * math.exp(-rate * years) * nd2


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
