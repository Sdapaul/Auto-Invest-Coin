# spot_bot_logic.py (★로그 날짜 자동 변경, ★HTF 필터, ★ATR SL/TP 적용됨)

import os, sys, time, json, logging
import pandas as pd
import pandas_ta as ta
from binance.client import Client, BinanceAPIException
from binance.enums import *
from datetime import datetime
import math # [★신규]

# --- 1. 설정 ---
POSITION_FILE = "spot_position.json"
try:
    with open('config.json', 'r') as f: config = json.load(f)
    mode = config.get("mode", "Test")
    if mode == "Test":
        api_key = config.get("testnet_api_key"); secret_key = config.get("testnet_secret_key"); is_testnet = True
    else:
        api_key = config.get("live_api_key"); secret_key = config.get("live_secret_key"); is_testnet = False
    
    # Spot 설정 로드
    settings = config.get("spot_settings", {})
    stop_loss_pct = float(settings.get("stop_loss_pct", 5.0))
    take_profit_pct = float(settings.get("take_profit_pct", 5.0))
    quantity_usdt = float(settings.get("quantity_usdt", 11.0)) # 매수할 USDT 금액
    timeframe = settings.get("timeframe", "1h")
    symbol = settings.get("symbol", "BTCUSDT")
    
    # 지표 설정 로드
    indicator_settings = config.get("indicator_settings", {})
    use_sma = indicator_settings.get("use_sma", True)
    use_rsi = indicator_settings.get("use_rsi", True)
    use_macd = indicator_settings.get("use_macd", True)
    use_bb = indicator_settings.get("use_bb", True)
    use_stoch = indicator_settings.get("use_stoch", True)
    use_stoch_cross = indicator_settings.get("use_stoch_cross", True)
    use_volume = indicator_settings.get("use_volume", True)
    
    min_conditions = indicator_settings.get("min_conditions", 7)
    min_exit_conditions = indicator_settings.get("min_exit_conditions", 3)
    
    rsi_oversold = indicator_settings.get("rsi_oversold", 30)
    rsi_overbought = indicator_settings.get("rsi_overbought", 70)
    stoch_oversold = indicator_settings.get("stoch_oversold", 20)
    stoch_overbought = indicator_settings.get("stoch_overbought", 80)
    volume_multiplier = indicator_settings.get("volume_multiplier", 1.2)

    # [★신규] HTF (상위 타임프레임) 필터 설정
    htf_settings = config.get("htf_settings", {})
    use_htf_filter = htf_settings.get("use_htf_filter", True)
    htf_timeframe = htf_settings.get("htf_timeframe", "4h")
    htf_sma_short_len = htf_settings.get("htf_sma_short", 10)
    htf_sma_long_len = htf_settings.get("htf_sma_long", 50)

    # [★신규] ATR 동적 손절/익절 설정
    atr_settings = config.get("atr_settings", {})
    use_atr_sl_tp = atr_settings.get("use_atr_sl_tp", True)
    atr_length = atr_settings.get("atr_length", 14)
    atr_sl_multiplier = atr_settings.get("atr_sl_multiplier", 2.0)
    atr_tp_multiplier = atr_settings.get("atr_tp_multiplier", 3.0)

    if not api_key or not secret_key:
        print(f"오류: [ {mode} ] API 키 필요."); exit()
except FileNotFoundError: print("오류: config.json 파일 없음."); exit()

# 현물 클라이언트 생성 (변경 없음)
try:
    if is_testnet:
        print(f"[Spot] 현물 테스트넷에 연결 중...")
        client = Client(api_key, secret_key, testnet=True)
    else:
        print(f"[Spot] 현물 라이브넷에 연결 중...")
        client = Client(api_key, secret_key)
    server_time = client.get_server_time()
    if server_time and 'serverTime' in server_time: print(f"[Spot] ✅ 현물 {mode} 모드 연결 성공!")
    else: print(f"[Spot] ❌ 현물 서버 시간 조회 실패"); exit()
except Exception as e:
    print(f"[Spot] ❌ 현물 클라이언트 생성 오류: {e}"); exit()

# 지표 설정
short_sma_len, long_sma_len, rsi_len, bbands_len = 10, 50, 14, 20
macd_fast, macd_slow, macd_signal = 12, 26, 9

# --- 2. 로깅 설정 (변경 없음) ---
log_folder = "logs"
if not os.path.exists(log_folder): os.makedirs(log_folder)
LOG_FILE_BASE = "spot_log"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger() 
def ensure_correct_log_file(log_file_base):
    today_str = datetime.now().strftime('%Y-%m-%d')
    log_file_path = os.path.join(log_folder, f"{log_file_base}_{today_str}.txt")
    correct_handler_exists = False
    handler_to_remove = None
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            if handler.baseFilename == log_file_path:
                correct_handler_exists = True
            else:
                handler_to_remove = handler
    if handler_to_remove:
        logging.info(f"[Spot] 로그 파일 날짜 변경. 이전 파일 닫는 중: {handler_to_remove.baseFilename}")
        handler_to_remove.close()
        logger.removeHandler(handler_to_remove)
    if not correct_handler_exists:
        logging.info(f"[Spot] 새 로그 파일 생성: {log_file_path}")
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
        file_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logging.info(f"\n{'='*50}\nSpot Bot New Day Start - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*50}")
# --- [로깅 설정 수정 완료] ---


# --- 3. 핵심 함수 ---
def get_market_data(symbol, timeframe, limit=200):
    # logging.info(f"[Spot] {symbol} {timeframe} 데이터 가져옵니다...") # 로그가 너무 많아짐
    try:
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=limit)
        if not klines:
            logging.error(f"[Spot] *** {symbol} 데이터를 가져올 수 없습니다 ***"); return None
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols: df[col] = pd.to_numeric(df[col])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"[Spot] *** {timeframe} 데이터 가져오기 실패: {e} ***")
        return None

def calculate_indicators(df):
    df.ta.sma(length=short_sma_len, append=True); df.ta.sma(length=long_sma_len, append=True)
    df.ta.rsi(length=rsi_len, append=True); df.ta.bbands(length=bbands_len, append=True)
    df.ta.macd(fast=macd_fast, slow=macd_slow, signal=macd_signal, append=True)
    df.ta.stoch(high='high', low='low', close='close', k=14, d=3, append=True)
    df.ta.sma(length=20, close='volume', append=True)
    df.ta.atr(length=atr_length, append=True) # [★신규] ATR 계산
    return df

# [★신규] 가격/수량 정밀도 계산 함수 추가
def get_price_precision(symbol):
    try:
        filters = client.get_symbol_info(symbol)['filters']
        price_filter = next((f for f in filters if f['filterType'] == 'PRICE_FILTER'), None)
        if price_filter:
            tick_size = float(price_filter['tickSize'])
            if tick_size == 1: return 0
            if tick_size == 0.1: return 1
            if tick_size == 0.01: return 2
            precision = int(round(-math.log(tick_size, 10), 0))
            return precision
    except Exception as e:
        logging.error(f"[Spot] 가격 정밀도 조회 실패: {e}. 기본값(2) 사용")
        return 2

def get_quantity_precision(symbol):
    try:
        filters = client.get_symbol_info(symbol)['filters']
        lot_size_filter = next((f for f in filters if f['filterType'] == 'LOT_SIZE'), None)
        if lot_size_filter:
            step_size = float(lot_size_filter['stepSize'])
            if step_size == 1: return 0
            if step_size == 0.1: return 1
            if step_size == 0.01: return 2
            precision = int(round(-math.log(step_size, 10), 0))
            return precision
    except Exception as e:
        logging.error(f"[Spot] 수량 정밀도 조회 실패: {e}. 기본값(8) 사용")
        return 8

def place_order(symbol, side, quantity=None, quote_order_qty=None, current_price=None):
    try:
        order_details = f"{symbol}, {side}"
        params = {'symbol': symbol, 'side': side, 'type': ORDER_TYPE_MARKET}
        
        if side == SIDE_BUY and quote_order_qty:
            params['quoteOrderQty'] = quote_order_qty; order_details += f", 매수금액: {quote_order_qty} USDT"
        elif side == SIDE_SELL and quantity:
            # [★수정] 수량 정밀도에 맞게 포맷팅
            qty_precision = get_quantity_precision(symbol)
            params['quantity'] = "{:0.0{}f}".format(quantity, qty_precision)
            order_details += f", 매도수량: {params['quantity']}"
        else:
            logging.error("[Spot] *** 주문 오류: 매수(quote_order_qty) 또는 매도(quantity) 필요 ***"); return None

        logging.info(f"[Spot] --- 주문 실행: {order_details} ---")
        
        try:
            order = client.create_order(**params)
            logging.info("[Spot] --- 주문 성공 ---"); logging.info(str(order))
            return order
        except BinanceAPIException as e:
            if e.code == -2015:  # 테스트 모드 시뮬레이션
                logging.warning(f"[Spot] *** 주문 실행 실패 (API 권한 부족): {e.message} ***")
                logging.warning("[Spot] 테스트 모드: 가상 주문으로 시뮬레이션합니다.")
                sim_price = current_price if current_price else 110000.0
                sim_qty = quote_order_qty / sim_price if side == SIDE_BUY else quantity
                simulated_order = {
                    'symbol': symbol, 'side': side, 'type': 'MARKET', 'status': 'FILLED',
                    'executedQty': str(sim_qty), 'price': str(sim_price),
                    'fills': [{'price': str(sim_price), 'qty': str(sim_qty)}]
                }
                logging.info("[Spot] --- 가상 주문 성공 (시뮬레이션) ---"); logging.info(f"[Spot] 가상 주문 결과: {simulated_order}")
                return simulated_order
            else:
                logging.error(f"[Spot] *** 주문 실패: {e} ***"); return None
        except Exception as e:
            logging.error(f"[Spot] *** 주문 실패: {e} ***"); return None
    except Exception as e:
        logging.error(f"[Spot] *** 주문 오류: {e} ***"); return None

def get_base_asset_balance(symbol):
    try:
        info = client.get_symbol_info(symbol)
        base_asset = info['baseAsset']
        
        try:
            balance = client.get_asset_balance(asset=base_asset)
            free_balance = float(balance['free'])
        except Exception as e:
            logging.warning(f"[Spot] 계정 정보 조회 실패 (API 권한 부족): {e}. 테스트 모드(잔고 0)로 진행.")
            free_balance = 0.0
        
        min_qty = 0.0; step_size = 0.0
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE': 
                min_qty = float(f['minQty']); 
                step_size = float(f['stepSize']); 
                break
        
        return base_asset, free_balance, min_qty
    except Exception as e:
        logging.error(f"[Spot] *** 잔고 확인 실패: {e} ***"); return None, 0.0, 0.0

def load_position():
    try:
        with open(POSITION_FILE, 'r') as f: return json.load(f)
    except FileNotFoundError: return None

# [★수정] SL/TP 타겟 저장
def save_position(entry_price, quantity, sl_target, tp_target):
    data = {
        'entry_price': entry_price, 
        'quantity': quantity,
        'sl_target': sl_target,
        'tp_target': tp_target
    }
    with open(POSITION_FILE, 'w') as f:
        json.dump(data, f)
    logging.info(f"[Spot] 포지션 저장: 진입={entry_price}, 수량={quantity}, SL={sl_target}, TP={tp_target}")


def clear_position():
    if os.path.exists(POSITION_FILE): 
        os.remove(POSITION_FILE)
        logging.info(f"[Spot] 포지션 파일 삭제 완료.")

def get_avg_fill_price(order):
    try:
        if 'fills' in order and order['fills']:
            total_cost = sum(float(f['price']) * float(f['qty']) for f in order['fills'])
            total_qty = sum(float(f['qty']) for f in order['fills'])
            if total_qty == 0: return 0.0
            return total_cost / total_qty
        else:
            return float(order.get('price', 0.0))
    except Exception as e:
        logging.error(f"[Spot] *** 평균 체결가 계산 실패: {e} ***"); return 0.0

# [★신규] 상위 타임프레임(HTF) 추세 확인 함수 (get_klines 사용)
def get_htf_trend(symbol, htf_timeframe, htf_short, htf_long):
    logging.info(f"[Spot] {htf_timeframe} 상위 추세 확인 중...")
    df_htf = get_market_data(symbol, htf_timeframe, limit=100) # HTF 데이터 가져오기
    if df_htf is None or len(df_htf) < htf_long:
        logging.warning(f"[Spot] {htf_timeframe} 데이터 부족. 추세 필터 비활성.")
        return "NEUTRAL"
        
    df_htf.ta.sma(length=htf_short, append=True)
    df_htf.ta.sma(length=htf_long, append=True)
    
    htf_latest = df_htf.iloc[-2] # 확정 캔들
    htf_sma_short_val = htf_latest.get(f'SMA_{htf_short}', 0)
    htf_sma_long_val = htf_latest.get(f'SMA_{htf_long}', 0)

    if htf_sma_short_val > htf_sma_long_val:
        return "UP"
    elif htf_sma_short_val < htf_sma_long_val:
        return "DOWN" # Spot 봇은 사용하지 않음
    else:
        return "NEUTRAL"

# --- 4. 메인 로직 (★HTF/ATR 적용으로 전면 수정됨) ---
def run_bot():
    ensure_correct_log_file(LOG_FILE_BASE)

    logging.info(f"Spot (현물) 봇을 [ {mode} ] 모드로 시작합니다...")
    logging.info(f"설정 - 심볼: {symbol}, 매수금액: {quantity_usdt} USDT, 타임프레임: {timeframe}")
    logging.info(f"필터 - HTF: {use_htf_filter}({htf_timeframe}), ATR SL/TP: {use_atr_sl_tp}")
    if use_atr_sl_tp:
        logging.info(f"ATR 설정 - SL: {atr_sl_multiplier}x, TP: {atr_tp_multiplier}x")
    else:
        logging.info(f"고정 설정 - 손절: {stop_loss_pct}%, 익절: {take_profit_pct}%")

    # [★신규] 가격 정밀도
    price_decimals = get_price_precision(symbol)
    logging.info(f"[Spot] {symbol} 가격 정밀도: {price_decimals} 소수점")

    check_interval = {'15m': 900, '1h': 3600, '4h': 14400}.get(timeframe, 3600)

    try:
        while True:
            try:
                ensure_correct_log_file(LOG_FILE_BASE)

                base_asset, current_balance, min_qty = get_base_asset_balance(symbol)
                if base_asset is None: time.sleep(60); continue

                position = load_position()

                # [★신규] 파일과 실제 잔고 동기화
                if position and current_balance < min_qty:
                    logging.warning("[Spot] 포지션 파일이 있으나 실제 잔고가 없습니다. 파일 삭제.")
                    clear_position()
                    position = None
                elif not position and current_balance > min_qty:
                    logging.warning("[Spot] 포지션 파일이 없으나 실제 잔고가 있습니다. (수동 매매로 간주)")
                    # 현물은 파일 없이도 잔고가 있을 수 있으므로, 파일 강제 생성은 안함.
                    # 단, 이 경우 봇은 매도만 검사함 (기존 로직 유지)
                    pass
                
                df = get_market_data(symbol, timeframe)
                if df is None:
                    logging.warning(f"[Spot] 데이터를 가져올 수 없어 {check_interval}초 후 재시도합니다...")
                    time.sleep(check_interval); continue
                    
                df = calculate_indicators(df); 
                if len(df) < 4: 
                    logging.warning(f"[Spot] 데이터 부족 (교차 확인 위해 {len(df)}/4 개). 대기합니다.")
                    time.sleep(check_interval); continue
                    
                latest = df.iloc[-2] # 확정 캔들 (신호 발생)
                prev = df.iloc[-3]   # 이전 캔들 (교차 확인용)
                current_price = df.iloc[-1]['close'] # 현재가 (손절/익절 확인용)
                latest_atr = latest.get(f'ATR_{atr_length}', 0.0)

                logging.info(f"\n[Spot] ========== [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ==========")
                logging.info(f"현재 보유량: {current_balance:.8f} {base_asset} (현재가: {current_price})")

                # --- 지표 값 로드 ---
                sma_short_col=f'SMA_{short_sma_len}'; sma_long_col=f'SMA_{long_sma_len}'; rsi_col=f'RSI_{rsi_len}'; macd_col=f'MACD_{macd_fast}_{macd_slow}_{macd_signal}'; macd_signal_col=f'MACDs_{macd_fast}_{macd_slow}_{macd_signal}'; bb_cols = [col for col in df.columns if col.startswith('BB')]; bbl_col = next((c for c in bb_cols if 'BBL' in c), None); bbu_col = next((c for c in bb_cols if 'BBU' in c), None); stoch_k_col = 'STOCHk_14_3_3'; stoch_d_col = 'STOCHd_14_3_3'; volume_sma_col = 'SMA_20_volume'
                latest_close = latest['close']; latest_sma_short = latest.get(sma_short_col, latest_close); latest_sma_long = latest.get(sma_long_col, latest_close); latest_rsi = latest.get(rsi_col, 50); latest_macd = latest.get(macd_col, 0); latest_macd_signal_val = latest.get(macd_signal_col, 0); latest_bbl = latest.get(bbl_col, latest_close) if bbl_col else latest_close; latest_bbu = latest.get(bbu_col, latest_close) if bbu_col else latest_close; latest_stoch_k = latest.get(stoch_k_col, 50); latest_stoch_d = latest.get(stoch_d_col, 50); latest_current_volume = latest['volume']; latest_volume_sma = latest.get(volume_sma_col, latest_current_volume)
                prev_close = prev['close']; prev_sma_short = prev.get(sma_short_col, latest_close); prev_sma_long = prev.get(sma_long_col, latest_close); prev_rsi = prev.get(rsi_col, 50); prev_macd = prev.get(macd_col, 0); prev_macd_signal_val = prev.get(macd_signal_col, 0); prev_stoch_k = prev.get(stoch_k_col, 50); prev_stoch_d = prev.get(stoch_d_col, 50); prev_bbl = prev.get(bbl_col, prev_close) if bbl_col else prev_close; prev_bbu = prev.get(bbu_col, prev_close) if bbu_col else prev_close
                
                logging.info(f"지표 값 - SMA{short_sma_len}: {latest_sma_short:.2f}, SMA{long_sma_len}: {latest_sma_long:.2f}, RSI: {latest_rsi:.2f}, ATR: {latest_atr:.{price_decimals}f}")
                logging.info(f"스토캐스틱 K: {latest_stoch_k:.2f}, D: {latest_stoch_d:.2f}")

                # --- [A] 보유 중 (매도 조건 확인) ---
                if current_balance > min_qty:
                    entry_price = current_price # 기본값
                    sl_target, tp_target = 0, 0

                    if position: # 봇이 매수한 경우
                        entry_price = position['entry_price']
                        if use_atr_sl_tp:
                            sl_target = position.get('sl_target', 0)
                            tp_target = position.get('tp_target', 0)
                            if sl_target == 0 or tp_target == 0: # 파일은 있으나 SL/TP가 없는 경우 (구 버전 파일)
                                logging.warning("[Spot] ATR 타겟이 없습니다. 고정 %로 SL/TP를 설정합니다.")
                                sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                                tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)
                                # [★신규] 구 버전 파일 업데이트
                                save_position(entry_price, position.get('quantity', current_balance), sl_target, tp_target)
                        else: # 고정 % 모드
                            sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                            tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)
                    else: # 봇이 매수하지 않았으나 잔고가 있는 경우 (수동 매매)
                        logging.info("[Spot] 수동 보유 물량 감지. 고정 %로 SL/TP 종료 로직만 적용합니다.")
                        entry_price = current_price # 현재가를 기준가로
                        sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                        tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)

                    pnl_percent = ((current_price - entry_price) / entry_price) * 100
                    logging.info(f"진입 가격: {entry_price:.{price_decimals}f} / 현재 PNL: {pnl_percent:.2f}%")
                    logging.info(f"타겟: SL={sl_target:.{price_decimals}f}, TP={tp_target:.{price_decimals}f}")

                    # --- 동적 전략 종료 조건 ('이벤트' 기반) ---
                    long_exit_conditions_met = []; long_exit_reasons = []
                    if use_sma and (prev_sma_short >= prev_sma_long) and (latest_sma_short < latest_sma_long): long_exit_conditions_met.append(True); long_exit_reasons.append(f"데드 크로스")
                    if use_rsi and (prev_rsi >= 45) and (latest_rsi < 45): long_exit_conditions_met.append(True); long_exit_reasons.append(f"RSI 45 하회")
                    if use_macd and (prev_macd >= prev_macd_signal_val) and (latest_macd < latest_macd_signal_val): long_exit_conditions_met.append(True); long_exit_reasons.append("MACD<Signal")
                    if use_bb and (prev_close >= prev_bbl) and (latest_close < prev_bbl): long_exit_conditions_met.append(True); long_exit_reasons.append("종가<BB하단")
                    if use_stoch_cross and (prev_stoch_k >= prev_stoch_d) and (latest_stoch_k < latest_stoch_d): long_exit_conditions_met.append(True); long_exit_reasons.append("스토캐스틱 하락")
                    
                    long_exit = len(long_exit_conditions_met) >= min_exit_conditions 
                    
                    sell_reason = None
                    if current_price <= sl_target:
                        sell_reason = f"손절매(SL) 도달"
                    elif current_price >= tp_target:
                        sell_reason = f"익절(TP) 도달"
                    elif long_exit:
                        sell_reason = f"전략 종료 신호 ({', '.join(long_exit_reasons)})"

                    if sell_reason:
                        logging.info(f"[Spot] >>> [매도 신호] <<<")
                        logging.info(f"매도 사유: {sell_reason}")
                        order = place_order(symbol, SIDE_SELL, quantity=current_balance, current_price=current_price)
                        if order:
                            clear_position() # 포지션 파일 삭제
                
                # --- [B] 미보유 중 (매수 조건 확인) ---
                elif position is None and current_balance < min_qty:
                    logging.info(f"진입 대기 중...")
                    
                    # [★신규] HTF 추세 확인
                    htf_trend = "NEUTRAL"
                    if use_htf_filter:
                        htf_trend = get_htf_trend(symbol, htf_timeframe, htf_sma_short_len, htf_sma_long_len)
                        logging.info(f"[Spot] {htf_timeframe} 상위 추세: {htf_trend}")

                    # --- 동적 매수(롱) 조건 ('이벤트' 기반) ---
                    long_conditions_met = []; long_entry_reasons = []
                    if use_sma:
                        is_gc_event = (prev_sma_short <= prev_sma_long) and (latest_sma_short > latest_sma_long)
                        is_gc_state = latest_sma_short > latest_sma_long
                        if is_gc_event or is_gc_state: long_conditions_met.append(True); long_entry_reasons.append(f"SMA 상승")
                    if use_rsi:
                        is_rsi_rising = latest_rsi > prev_rsi
                        is_rsi_ok_long = latest_rsi < rsi_overbought and is_rsi_rising
                        if is_rsi_ok_long: long_conditions_met.append(True); long_entry_reasons.append(f"RSI 상승")
                    if use_macd:
                        is_macd_event = (prev_macd <= prev_macd_signal_val) and (latest_macd > latest_macd_signal_val)
                        is_macd_state = latest_macd > latest_macd_signal_val
                        if is_macd_event or is_macd_state: long_conditions_met.append(True); long_entry_reasons.append(f"MACD 상승")
                    if use_bb:
                        is_bb_cross = (prev_close <= prev_bbl) and (latest_close > latest_bbl)
                        is_bb_ok_long = latest_close > latest_bbl
                        if is_bb_cross or is_bb_ok_long: long_conditions_met.append(True); long_entry_reasons.append(f"BB하단 위")
                    if use_stoch:
                        is_stoch_exit_oversold = (prev_stoch_k < stoch_oversold) and (latest_stoch_k > stoch_oversold) # 과매도 '탈출'
                        # [★오류 수정] 'Example C' 텍스트 제거
                        if is_stoch_exit_oversold: 
                            long_conditions_met.append(True); long_entry_reasons.append(f"스토캐스틱 과매도 탈출(K:{latest_stoch_k:.1f})")
                    if use_stoch_cross:
                        is_stoch_bullish_event = (prev_stoch_k <= prev_stoch_d) and (latest_stoch_k > latest_stoch_d)
                        if is_stoch_bullish_event: long_conditions_met.append(True); long_entry_reasons.append("스토캐스틱 상승교차(K>D)")
                    if use_volume:
                        is_volume_high = latest_current_volume > latest_volume_sma * volume_multiplier
                        if is_volume_high: long_conditions_met.append(True); long_entry_reasons.append(f"거래량 증가")

                    long_entry = len(long_conditions_met) >= min_conditions

                    # [★수정] HTF 필터 적용
                    if long_entry and (not use_htf_filter or (use_htf_filter and htf_trend == "UP")):
                        logging.info("[Spot] >>> [매수 신호] <<<")
                        logging.info(f"매수 사유: {', '.join(long_entry_reasons)}")
                        
                        order = place_order(symbol, SIDE_BUY, quote_order_qty=quantity_usdt, current_price=current_price)
                        if order:
                            entry_price = get_avg_fill_price(order)
                            filled_qty = float(order.get('executedQty', 0.0))
                            
                            sl_target, tp_target = 0, 0
                            if use_atr_sl_tp and latest_atr > 0:
                                sl_target = round(entry_price - (latest_atr * atr_sl_multiplier), price_decimals)
                                tp_target = round(entry_price + (latest_atr * atr_tp_multiplier), price_decimals)
                            else:
                                sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                                tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)
                            
                            if entry_price > 0:
                                save_position(entry_price, filled_qty, sl_target, tp_target) # 포지션 파일 저장
                                logging.info(f"실제 진입 가격: {entry_price:.{price_decimals}f} / 수량: {filled_qty}")
                            else:
                                logging.warning("[Spot] 체결 가격을 확인할 수 없어 포지션을 저장하지 못했습니다.")

            except Exception as e:
                logging.error(f"[Spot] *** 메인 루프 내에서 에러 발생: {e} ***")

            logging.info(f"다음 확인까지 {check_interval}초 대기합니다...")
            time.sleep(check_interval)
            
    except KeyboardInterrupt: 
        logging.info("\n[Spot] 종료 신호 감지.")
    finally:
        logging.info("[Spot] 안전 종료 완료.")

if __name__ == '__main__':
    run_bot()