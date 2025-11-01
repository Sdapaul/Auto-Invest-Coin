# coin_m_bot_logic.py (★로그 날짜 자동 변경, ★HTF 필터, ★ATR SL/TP 적용됨)

import os, sys, time, json, logging
import pandas as pd
import pandas_ta as ta
from binance.client import Client
from binance.enums import *
from datetime import datetime
import math # [★신규]

# --- 1. 설정 ---
COIN_M_POSITION_FILE = "coin_m_position.json" # [★신규] 포지션 상태 파일

try:
    with open('config.json', 'r') as f: config = json.load(f)
    mode = config.get("mode", "Test")
    if mode == "Test":
        api_key = config.get("testnet_api_key"); secret_key = config.get("testnet_secret_key"); is_testnet = True
    else:
        api_key = config.get("live_api_key"); secret_key = config.get("live_secret_key"); is_testnet = False
    
    # COIN-M 설정 로드
    settings = config.get("coin_m_settings", {})
    leverage = int(settings.get("leverage", 3))
    stop_loss_pct = float(settings.get("stop_loss_pct", 2.0))
    take_profit_pct = float(settings.get("take_profit_pct", 5.0))
    quantity = int(settings.get("quantity", 1))
    timeframe = settings.get("timeframe", "1h")
    symbol = settings.get("symbol", "BTCUSD_PERP")
    margin_type = settings.get("margin_type", "ISOLATED") 
    
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

client = Client(api_key, secret_key, testnet=is_testnet)
short_sma_len, long_sma_len, rsi_len, bbands_len = 10, 50, 14, 20
macd_fast, macd_slow, macd_signal = 12, 26, 9

# --- 2. 로깅 설정 (변경 없음) ---
log_folder = "logs"
if not os.path.exists(log_folder): os.makedirs(log_folder)
LOG_FILE_BASE = "coin_m_log" 
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
        logging.info(f"[COIN-M] 로그 파일 날짜 변경. 이전 파일 닫는 중: {handler_to_remove.baseFilename}")
        handler_to_remove.close()
        logger.removeHandler(handler_to_remove)
    if not correct_handler_exists:
        logging.info(f"[COIN-M] 새 로그 파일 생성: {log_file_path}")
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
        file_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
# --- [로깅 설정 수정 완료] ---


# --- 3. 핵심 함수 (COIN-M API 기준) ---
def get_market_data(symbol, timeframe, limit=200):
    # logging.info(f"[COIN-M] {symbol} {timeframe} 데이터 가져옵니다...")
    try:
        klines = client.futures_coin_klines(symbol=symbol, interval=timeframe, limit=limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols: df[col] = pd.to_numeric(df[col])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"[COIN-M] *** {timeframe} 데이터 가져오기 실패: {e} ***")
        return None

def calculate_indicators(df):
    df.ta.sma(length=short_sma_len, append=True); df.ta.sma(length=long_sma_len, append=True)
    df.ta.rsi(length=rsi_len, append=True); df.ta.bbands(length=bbands_len, append=True)
    df.ta.macd(fast=macd_fast, slow=macd_slow, signal=macd_signal, append=True)
    df.ta.stoch(high='high', low='low', close='close', k=14, d=3, append=True)
    df.ta.sma(length=20, close='volume', append=True)
    df.ta.atr(length=atr_length, append=True) # [★신규] ATR 계산
    return df

def place_order(symbol, side, quantity, order_type=ORDER_TYPE_MARKET, stop_price=None):
    try:
        logging.info(f"[COIN-M] --- 주문 실행: {symbol}, {side}, 수량: {quantity} 계약, {order_type} ---")
        params = {'symbol': symbol, 'side': side, 'type': order_type, 'quantity': quantity}
        if order_type == 'STOP_MARKET':
            params['stopPrice'] = stop_price; params['closePosition'] = True
        order = client.futures_coin_create_order(**params)
        logging.info("[COIN-M] --- 주문 성공 ---"); logging.info(str(order))
        return order
    except Exception as e:
        logging.error(f"[COIN-M] *** 주문 실패: {e} ***"); return None

def get_position_with_pnl(symbol):
    try:
        positions = client.futures_coin_position_information()
        for p in positions:
            if p['symbol'] == symbol:
                position_amt = float(p['positionAmt']); entry_price = float(p['entryPrice']); mark_price = float(p['markPrice'])
                return position_amt, entry_price, mark_price # [★수정] PNL% 대신 현재가(mark_price) 반환
        return 0.0, 0.0, 0.0
    except Exception as e:
        logging.error(f"[COIN-M] *** 포지션(PNL) 확인 실패: {e} ***"); return 0.0, 0.0, 0.0

def cancel_all_open_orders(symbol):
    try:
        orders = client.futures_coin_get_open_orders(symbol=symbol)
        if orders:
            client.futures_coin_cancel_all_open_orders(symbol=symbol)
            logging.info(f"[COIN-M] {symbol}의 모든 대기 주문(손절 등)을 취소했습니다.")
    except Exception as e:
        logging.error(f"[COIN-M] *** 주문 취소 중 에러 발생: {e} ***")

# [★신규] 포지션 파일 관리 함수
def load_position():
    try:
        with open(COIN_M_POSITION_FILE, 'r') as f: return json.load(f)
    except FileNotFoundError: return None

def save_position(entry_price, quantity, sl_target, tp_target):
    data = {
        'entry_price': entry_price, 
        'quantity': quantity,
        'sl_target': sl_target,
        'tp_target': tp_target
    }
    with open(COIN_M_POSITION_FILE, 'w') as f:
        json.dump(data, f)
    logging.info(f"[COIN-M] 포지션 저장: 진입={entry_price}, SL={sl_target}, TP={tp_target}")

def clear_position():
    if os.path.exists(COIN_M_POSITION_FILE): 
        os.remove(COIN_M_POSITION_FILE)
        logging.info(f"[COIN-M] 포지션 파일 삭제 완료.")

# [★신규] 상위 타임프레임(HTF) 추세 확인 함수
def get_htf_trend(symbol, htf_timeframe, htf_short, htf_long):
    logging.info(f"[COIN-M] {htf_timeframe} 상위 추세 확인 중...")
    df_htf = get_market_data(symbol, htf_timeframe, limit=100) # HTF 데이터 가져오기
    if df_htf is None or len(df_htf) < htf_long:
        logging.warning(f"[COIN-M] {htf_timeframe} 데이터 부족. 추세 필터 비활성.")
        return "NEUTRAL"
        
    df_htf.ta.sma(length=htf_short, append=True)
    df_htf.ta.sma(length=htf_long, append=True)
    
    htf_latest = df_htf.iloc[-2] # 확정 캔들
    htf_sma_short_val = htf_latest.get(f'SMA_{htf_short}', 0)
    htf_sma_long_val = htf_latest.get(f'SMA_{htf_long}', 0)

    if htf_sma_short_val > htf_sma_long_val:
        return "UP"
    elif htf_sma_short_val < htf_sma_long_val:
        return "DOWN"
    else:
        return "NEUTRAL"

# [★신규] 가격 정밀도(소수점) 계산
def get_price_precision(symbol):
    try:
        filters = client.futures_coin_exchange_info()['symbols']
        symbol_info = next((s for s in filters if s['symbol'] == symbol), None)
        if symbol_info:
            return symbol_info['pricePrecision']
    except Exception as e:
        logging.error(f"[COIN-M] 가격 정밀도 조회 실패: {e}. 기본값(1) 사용")
        return 1

# --- 4. 메인 로직 (★HTF/ATR 적용으로 전면 수정됨) ---
def run_bot():
    ensure_correct_log_file(LOG_FILE_BASE)
    
    logging.info(f"COIN-M 봇을 [ {mode} ] 모드로 시작합니다...")
    logging.info(f"설정 - 심볼: {symbol}, 마진:{margin_type}, 레버리지:{leverage}, 수량:{quantity}(계약), 타임프레임: {timeframe}")
    logging.info(f"필터 - HTF: {use_htf_filter}({htf_timeframe}), ATR SL/TP: {use_atr_sl_tp}")
    if use_atr_sl_tp:
        logging.info(f"ATR 설정 - SL: {atr_sl_multiplier}x, TP: {atr_tp_multiplier}x")
    else:
        logging.info(f"고정 설정 - 손절: {stop_loss_pct}%, 익절: {take_profit_pct}%")

    # [★신규] 마진/레버리지/안전성 검사 (기존 유지)
    try: 
        client.futures_coin_change_margin_type(symbol=symbol, marginType=margin_type); logging.info(f"[COIN-M] 마진 타입 {margin_type} 설정 완료.")
    except Exception as e:
        if "No need to change" in str(e): logging.info(f"[COIN-M] 마진 타입이 이미 {margin_type} 입니다.")
        else: logging.error(f"[COIN-M] 마진 설정 실패: {e}"); return
    try: 
        client.futures_coin_change_leverage(symbol=symbol, leverage=leverage); logging.info(f"[COIN-M] 레버리지 {leverage}로 설정 완료.")
    except Exception as e: logging.error(f"[COIN-M] 레버리지 설정 실패: {e}"); return
    try:
        liquidation_pct = (1 / leverage) * 100 * 0.9
        if not use_atr_sl_tp and stop_loss_pct >= liquidation_pct: 
            logging.error(f"경고: 손절({stop_loss_pct}%) >= 청산({liquidation_pct:.2f}%) 위험!"); return
        else: logging.info(f"[안전성 검사 통과] 청산: {liquidation_pct:.2f}%")
    except Exception as e: logging.error(f"[COIN-M] 안전성 검사 오류: {e}"); return

    # [★신규] 가격 정밀도
    price_decimals = get_price_precision(symbol)
    logging.info(f"[COIN-M] {symbol} 가격 정밀도: {price_decimals} 소수점")

    check_interval = {'15m': 900, '1h': 3600, '4h': 14400}.get(timeframe, 3600)

    try:
        while True:
            try:
                ensure_correct_log_file(LOG_FILE_BASE)
                
                # [★수정] 포지션 파일과 실제 포지션 동기화
                position_data = load_position()
                current_position_amt, broker_entry_price, current_price = get_position_with_pnl(symbol)

                if current_position_amt != 0 and not position_data:
                    logging.warning("[COIN-M] 포지션 파일 불일치 감지. 브로커 정보로 파일 생성 (SL/TP 재설정 필요)")
                    save_position(broker_entry_price, abs(current_position_amt), 0, 0)
                    position_data = load_position()
                elif current_position_amt == 0 and position_data:
                    logging.warning("[COIN-M] 포지션 파일 불일치 감지 (브로커 포지션 없음). 파일 삭제.")
                    clear_position()
                    position_data = None
                
                # --- [A] 포지션 보유 중 (익절/손절/전략 종료 검사) ---
                if position_data and current_position_amt != 0:
                    entry_price = position_data['entry_price']
                    sl_target = position_data['sl_target']
                    tp_target = position_data['tp_target']
                    position_qty = position_data['quantity']
                    
                    if use_atr_sl_tp and (sl_target == 0 or tp_target == 0):
                        logging.warning("[COIN-M] ATR 타겟이 없습니다. 고정 %로 SL/TP를 설정합니다.")
                        if current_position_amt > 0: # 롱
                            sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                            tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)
                        else: # 숏
                            sl_target = round(entry_price * (1 + stop_loss_pct / 100), price_decimals)
                            tp_target = round(entry_price * (1 - take_profit_pct / 100), price_decimals)
                        save_position(entry_price, position_qty, sl_target, tp_target)
                    elif not use_atr_sl_tp: # 고정 % 모드
                        if current_position_amt > 0: # 롱
                            sl_target = round(entry_price * (1 - stop_loss_pct / 100), price_decimals)
                            tp_target = round(entry_price * (1 + take_profit_pct / 100), price_decimals)
                        else: # 숏
                            sl_target = round(entry_price * (1 + stop_loss_pct / 100), price_decimals)
                            tp_target = round(entry_price * (1 - take_profit_pct / 100), price_decimals)

                    logging.info(f"포지션: {current_position_amt} {symbol} @ {entry_price:.{price_decimals}f}")
                    logging.info(f"타겟: SL={sl_target:.{price_decimals}f}, TP={tp_target:.{price_decimals}f}, 현재가={current_price:.{price_decimals}f}")

                    df = get_market_data(symbol, timeframe); 
                    if df is None: time.sleep(check_interval); continue
                    df = calculate_indicators(df); 
                    if len(df) < 4: time.sleep(check_interval); continue
                    
                    latest = df.iloc[-2]; prev = df.iloc[-3]
                    
                    # --- 지표 값 로드 ---
                    sma_short_col=f'SMA_{short_sma_len}'; sma_long_col=f'SMA_{long_sma_len}'; rsi_col=f'RSI_{rsi_len}'; macd_col=f'MACD_{macd_fast}_{macd_slow}_{macd_signal}'; macd_signal_col=f'MACDs_{macd_fast}_{macd_slow}_{macd_signal}'; bb_cols = [col for col in df.columns if col.startswith('BB')]; bbl_col = next((c for c in bb_cols if 'BBL' in c), None); bbu_col = next((c for c in bb_cols if 'BBU' in c), None); stoch_k_col = 'STOCHk_14_3_3'; stoch_d_col = 'STOCHd_14_3_3'
                    latest_sma_short = latest.get(sma_short_col, 0); latest_sma_long = latest.get(sma_long_col, 0); latest_rsi = latest.get(rsi_col, 50); latest_macd = latest.get(macd_col, 0); latest_macd_signal_val = latest.get(macd_signal_col, 0); latest_bbl = latest.get(bbl_col, 0); latest_bbu = latest.get(bbu_col, 0); latest_stoch_k = latest.get(stoch_k_col, 50); latest_stoch_d = latest.get(stoch_d_col, 50); latest_close = latest['close']
                    prev_sma_short = prev.get(sma_short_col, 0); prev_sma_long = prev.get(sma_long_col, 0); prev_rsi = prev.get(rsi_col, 50); prev_macd = prev.get(macd_col, 0); prev_macd_signal_val = prev.get(macd_signal_col, 0); prev_bbl = prev.get(bbl_col, 0); prev_bbu = prev.get(bbu_col, 0); prev_stoch_k = prev.get(stoch_k_col, 50); prev_stoch_d = prev.get(stoch_d_col, 50); prev_close = prev['close']
                    
                    sell_reason = None
                    
                    if current_position_amt > 0: # 롱 포지션 종료 검사
                        if current_price >= tp_target: sell_reason = f"익절(TP) 도달"
                        elif current_price <= sl_target: sell_reason = f"손절(SL) 도달"
                        else:
                            long_exit_conditions_met = []; long_exit_reasons = []
                            if use_sma and (prev_sma_short >= prev_sma_long) and (latest_sma_short < latest_sma_long): long_exit_conditions_met.append(True); long_exit_reasons.append(f"데드 크로스")
                            if use_rsi and (prev_rsi >= 45) and (latest_rsi < 45): long_exit_conditions_met.append(True); long_exit_reasons.append(f"RSI 45 하회")
                            if use_macd and (prev_macd >= prev_macd_signal_val) and (latest_macd < latest_macd_signal_val): long_exit_conditions_met.append(True); long_exit_reasons.append("MACD<Signal")
                            if use_bb and (prev_close >= prev_bbl) and (latest_close < prev_bbl): long_exit_conditions_met.append(True); long_exit_reasons.append("종가<BB하단")
                            if use_stoch_cross and (prev_stoch_k >= prev_stoch_d) and (latest_stoch_k < latest_stoch_d): long_exit_conditions_met.append(True); long_exit_reasons.append("스토캐스틱 하락")
                            
                            if len(long_exit_conditions_met) >= min_exit_conditions:
                                sell_reason = f"전략 종료 신호 ({', '.join(long_exit_reasons)})"
                                
                    elif current_position_amt < 0: # 숏 포지션 종료 검사
                        if current_price <= tp_target: sell_reason = f"익절(TP) 도달"
                        elif current_price >= sl_target: sell_reason = f"손절(SL) 도달"
                        else:
                            short_exit_conditions_met = []; short_exit_reasons = []
                            if use_sma and (prev_sma_short <= prev_sma_long) and (latest_sma_short > latest_sma_long): short_exit_conditions_met.append(True); short_exit_reasons.append(f"골든 크로스")
                            if use_rsi and (prev_rsi <= 55) and (latest_rsi > 55): short_exit_conditions_met.append(True); short_exit_reasons.append(f"RSI 55 상회")
                            if use_macd and (prev_macd <= prev_macd_signal_val) and (latest_macd > latest_macd_signal_val): short_exit_conditions_met.append(True); short_exit_reasons.append("MACD>Signal")
                            if use_bb and (prev_close <= prev_bbu) and (latest_close > prev_bbu): short_exit_conditions_met.append(True); short_exit_reasons.append("종가>BB상단")
                            if use_stoch_cross and (prev_stoch_k <= prev_stoch_d) and (latest_stoch_k > latest_stoch_d): short_exit_conditions_met.append(True); short_exit_reasons.append("스토캐스틱 상승")
                            
                            if len(short_exit_conditions_met) >= min_exit_conditions:
                                sell_reason = f"전략 종료 신호 ({', '.join(short_exit_reasons)})"
                    
                    if sell_reason:
                        logging.info(f"[COIN-M] >>> [포지션 종료 신호] {sell_reason} <<<")
                        cancel_all_open_orders(symbol) # 기존 SL 주문 취소
                        side = SIDE_SELL if current_position_amt > 0 else SIDE_BUY
                        place_order(symbol, side, abs(current_position_amt))
                        clear_position()

                # --- [B] 포지션 미보유 (진입 검사) ---
                elif position_data is None and current_position_amt == 0:
                    logging.info(f"포지션 없음. 진입 신호 확인 중...")
                    
                    # [★신규] HTF 추세 확인
                    htf_trend = "NEUTRAL"
                    if use_htf_filter:
                        htf_trend = get_htf_trend(symbol, htf_timeframe, htf_sma_short_len, htf_sma_long_len)
                        logging.info(f"[COIN-M] {htf_timeframe} 상위 추세: {htf_trend}")

                    df = get_market_data(symbol, timeframe); 
                    if df is None: time.sleep(check_interval); continue
                    df = calculate_indicators(df); 
                    if len(df) < 4: time.sleep(check_interval); continue
                    
                    latest = df.iloc[-2]; prev = df.iloc[-3]
                    latest_atr = latest.get(f'ATR_{atr_length}', 0.0)
                    
                    # --- 지표 값 로드 ---
                    sma_short_col=f'SMA_{short_sma_len}'; sma_long_col=f'SMA_{long_sma_len}'; rsi_col=f'RSI_{rsi_len}'; macd_col=f'MACD_{macd_fast}_{macd_slow}_{macd_signal}'; macd_signal_col=f'MACDs_{macd_fast}_{macd_slow}_{macd_signal}'; bb_cols = [col for col in df.columns if col.startswith('BB')]; bbl_col = next((c for c in bb_cols if 'BBL' in c), None); bbu_col = next((c for c in bb_cols if 'BBU' in c), None); stoch_k_col = 'STOCHk_14_3_3'; stoch_d_col = 'STOCHd_14_3_3'; volume_sma_col = 'SMA_20_volume'
                    latest_close = latest['close']; latest_sma_short = latest.get(sma_short_col, latest_close); latest_sma_long = latest.get(sma_long_col, latest_close); latest_rsi = latest.get(rsi_col, 50); latest_macd = latest.get(macd_col, 0); latest_macd_signal_val = latest.get(macd_signal_col, 0); latest_bbl = latest.get(bbl_col, latest_close) if bbl_col else latest_close; latest_bbu = latest.get(bbu_col, latest_close) if bbu_col else latest_close; latest_stoch_k = latest.get(stoch_k_col, 50); latest_stoch_d = latest.get(stoch_d_col, 50); latest_current_volume = latest['volume']; latest_volume_sma = latest.get(volume_sma_col, latest_current_volume)
                    prev_close = prev['close']; prev_sma_short = prev.get(sma_short_col, latest_close); prev_sma_long = prev.get(sma_long_col, latest_close); prev_rsi = prev.get(rsi_col, 50); prev_macd = prev.get(macd_col, 0); prev_macd_signal_val = prev.get(macd_signal_col, 0); prev_stoch_k = prev.get(stoch_k_col, 50); prev_stoch_d = prev.get(stoch_d_col, 50); prev_bbl = prev.get(bbl_col, prev_close) if bbl_col else prev_close; prev_bbu = prev.get(bbu_col, prev_close) if bbu_col else prev_close

                    logging.info(f"지표 값 - SMA{short_sma_len}: {latest_sma_short:.2f}, SMA{long_sma_len}: {latest_sma_long:.2f}, RSI: {latest_rsi:.2f}, ATR: {latest_atr:.{price_decimals}f}")
                    logging.info(f"스토캐스틱 K: {latest_stoch_k:.2f}, D: {latest_stoch_d:.2f}")

                    # --- 롱 포지션 진입 조건 검사 ---
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
                        is_stoch_exit_oversold = (prev_stoch_k < stoch_oversold) and (latest_stoch_k > stoch_oversold)
                        if is_stoch_exit_oversold: long_conditions_met.append(True); long_entry_reasons.append(f"스토캐스틱 과매도 탈출")
                    if use_stoch_cross:
                        is_stoch_bullish_event = (prev_stoch_k <= prev_stoch_d) and (latest_stoch_k > latest_stoch_d)
                        if is_stoch_bullish_event: long_conditions_met.append(True); long_entry_reasons.append("스토캐스틱 상승교차")
                    if use_volume:
                        is_volume_high = latest_current_volume > latest_volume_sma * volume_multiplier
                        if is_volume_high: long_conditions_met.append(True); long_entry_reasons.append(f"거래량 증가")
                    long_entry = len(long_conditions_met) >= min_conditions
                    
                    # --- 숏 포지션 진입 조건 검사 ---
                    short_conditions_met = []; short_entry_reasons = []
                    if use_sma:
                        is_dc_event = (prev_sma_short >= prev_sma_long) and (latest_sma_short < latest_sma_long)
                        is_dc_state = latest_sma_short < latest_sma_long
                        if is_dc_event or is_dc_state: short_conditions_met.append(True); short_entry_reasons.append(f"SMA 하락")
                    if use_rsi:
                        is_rsi_falling = latest_rsi < prev_rsi
                        is_rsi_ok_short = latest_rsi > rsi_oversold and is_rsi_falling
                        if is_rsi_ok_short: short_conditions_met.append(True); short_entry_reasons.append(f"RSI 하락")
                    if use_macd:
                        is_macd_event = (prev_macd >= prev_macd_signal_val) and (latest_macd < latest_macd_signal_val)
                        is_macd_state = latest_macd < latest_macd_signal_val
                        if is_macd_event or is_macd_state: short_conditions_met.append(True); short_entry_reasons.append(f"MACD 하락")
                    if use_bb:
                        is_bb_cross = (prev_close >= prev_bbu) and (latest_close < latest_bbu)
                        is_bb_ok_short = latest_close < latest_bbu
                        if is_bb_cross or is_bb_ok_short: short_conditions_met.append(True); short_entry_reasons.append(f"BB상단 아래")
                    if use_stoch:
                        is_stoch_exit_overbought = (prev_stoch_k > stoch_overbought) and (latest_stoch_k < stoch_overbought)
                        if is_stoch_exit_overbought: short_conditions_met.append(True); short_entry_reasons.append(f"스토캐스틱 과매수 탈출")
                    if use_stoch_cross:
                        is_stoch_bearish_event = (prev_stoch_k >= prev_stoch_d) and (latest_stoch_k < latest_stoch_d)
                        if is_stoch_bearish_event: short_conditions_met.append(True); short_entry_reasons.append("스토캐스틱 하락교차")
                    if use_volume:
                        is_volume_high_short = latest_current_volume > latest_volume_sma * volume_multiplier
                        if is_volume_high_short: short_conditions_met.append(True); short_entry_reasons.append(f"거래량 증가")
                    short_entry = len(short_conditions_met) >= min_conditions

                    # --- 주문 로직 ---
                    if long_entry and (not use_htf_filter or (use_htf_filter and htf_trend == "UP")):
                        logging.info("[COIN-M] >>> [롱 포지션 진입 신호] <<<")
                        logging.info(f"진입 사유: {', '.join(long_entry_reasons)}")
                        order = place_order(symbol, SIDE_BUY, quantity)
                        if order:
                            time.sleep(1); _, entry, _ = get_position_with_pnl(symbol)
                            if entry == 0: entry = latest_close # 진입가 조회 실패시
                            
                            sl_target, tp_target = 0, 0
                            if use_atr_sl_tp and latest_atr > 0:
                                sl_target = round(entry - (latest_atr * atr_sl_multiplier), price_decimals)
                                tp_target = round(entry + (latest_atr * atr_tp_multiplier), price_decimals)
                            else:
                                sl_target = round(entry * (1 - stop_loss_pct / 100), price_decimals)
                                tp_target = round(entry * (1 + take_profit_pct / 100), price_decimals)
                                
                            save_position(entry, quantity, sl_target, tp_target)
                            place_order(symbol, SIDE_SELL, quantity, 'STOP_MARKET', stop_price=sl_target)

                    elif short_entry and (not use_htf_filter or (use_htf_filter and htf_trend == "DOWN")):
                        logging.info("[COIN-M] >>> [숏 포지션 진입 신호] <<<")
                        logging.info(f"진입 사유: {', '.join(short_entry_reasons)}")
                        order = place_order(symbol, SIDE_SELL, quantity)
                        if order:
                            time.sleep(1); _, entry, _ = get_position_with_pnl(symbol)
                            if entry == 0: entry = latest_close # 진입가 조회 실패시

                            sl_target, tp_target = 0, 0
                            if use_atr_sl_tp and latest_atr > 0:
                                sl_target = round(entry + (latest_atr * atr_sl_multiplier), price_decimals)
                                tp_target = round(entry - (latest_atr * atr_tp_multiplier), price_decimals)
                            else:
                                sl_target = round(entry * (1 + stop_loss_pct / 100), price_decimals)
                                tp_target = round(entry * (1 - take_profit_pct / 100), price_decimals)
                            
                            save_position(entry, quantity, sl_target, tp_target)
                            place_order(symbol, SIDE_BUY, quantity, 'STOP_MARKET', stop_price=sl_target)

            except Exception as e:
                logging.error(f"[COIN-M] *** 메인 루프 내에서 에러 발생: {e} ***")
            
            logging.info(f"다음 확인까지 {check_interval}초 대기합니다...")
            time.sleep(check_interval)
            
    except KeyboardInterrupt: logging.info("\n[COIN-M] 종료 신호 감지.")
    finally:
        logging.info("[COIN-M] 종료 전 주문 취소 시도..."); 
        # [★수정] 이 시점의 position_data가 정의되지 않았을 수 있으므로, API로 직접 확인
        current_pos, _, _ = get_position_with_pnl(symbol) 
        if current_pos != 0:
             cancel_all_open_orders(symbol)
        logging.info("[COIN-M] 안전 종료 완료.")

if __name__ == '__main__':
    run_bot()