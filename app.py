# app.py (종료 조건 UI 추가 및 실시간 분석 탭 연동 완료)

import streamlit as st
import subprocess, os, time, json, signal, re
import pandas as pd
import plotly.graph_objects as go
from binance.client import Client, BinanceAPIException
from datetime import datetime, date, timedelta, timezone

st.set_page_config(page_title="통합 자동매매 대시보드", layout="wide")

# --- 기본 설정 ---
CONFIG_FILE_PATH = "config.json"
USD_M_BOT_SCRIPT = "usd_m_bot_logic.py"
COIN_M_BOT_SCRIPT = "coin_m_bot_logic.py"
SPOT_BOT_SCRIPT = "spot_bot_logic.py" 

# --- 세션 상태 초기화 ---
for key in ['usd_m_process', 'coin_m_process', 'spot_process', 
            'usd_m_trades_df', 'coin_m_trades_df', 'spot_trades_df', 
            'futures_client', 'spot_client']: 
    if key not in st.session_state: st.session_state[key] = None
for key in ['usd_m_auto_refresh', 'coin_m_auto_refresh', 'spot_auto_refresh']: 
    if key not in st.session_state: st.session_state[key] = True
if 'history_date' not in st.session_state: st.session_state.history_date = date.today()

# --- 설정 파일 관리 ---
def load_config():
    try:
        with open(CONFIG_FILE_PATH, 'r') as f: return json.load(f)
    except FileNotFoundError: 
        # [수정] 기본 config 생성 시 min_exit_conditions 포함
        default_config = {
            "mode": "Test", "testnet_api_key": "", "testnet_secret_key": "",
            "live_api_key": "", "live_secret_key": "",
            "usd_m_settings": {"symbol": "BTCUSDT", "margin_type": "ISOLATED", "leverage": 10, "stop_loss_pct": 5.0, "take_profit_pct": 5.0, "quantity": 0.001, "timeframe": "1h"},
            "coin_m_settings": {"symbol": "ETHUSD_PERP", "margin_type": "ISOLATED", "leverage": 10, "stop_loss_pct": 5.0, "take_profit_pct": 5.0, "quantity": 1, "timeframe": "1h"},
            "spot_settings": {"symbol": "BTCUSDT", "quantity_usdt": 11.0, "stop_loss_pct": 5.0, "take_profit_pct": 5.0, "timeframe": "15m"},
            "indicator_settings": {
                "use_sma": True, "use_rsi": True, "use_macd": True, "use_bb": True,
                "use_stoch": True, "use_stoch_cross": True, "use_volume": True,
                "min_conditions": 4, "min_exit_conditions": 3, # <--- 기본값 추가
                "rsi_oversold": 24, "rsi_overbought": 75,
                "stoch_oversold": 20, "stoch_overbought": 80, "volume_multiplier": 1.1
            }
        }
        save_config(default_config)
        return default_config
    
def save_config(config_data):
    with open(CONFIG_FILE_PATH, 'w') as f: json.dump(config_data, f, indent=4)
    st.session_state.futures_client = None 
    st.session_state.spot_client = None
    st.toast("✅ 설정 저장 완료.")

# --- 바이낸스 클라이언트 생성 (선물 / 현물 분리) ---
def get_futures_client(config):
    if st.session_state.futures_client:
        return st.session_state.futures_client
    mode = config.get("mode", "Test")
    api_key = config.get("testnet_api_key") if mode == "Test" else config.get("live_api_key")
    secret_key = config.get("testnet_secret_key") if mode == "Test" else config.get("live_secret_key")
    if not api_key or not secret_key:
        st.error(f"💡 {mode} 모드 선물 API 키가 필요합니다."); return None
    try:
        client = Client(api_key, secret_key, testnet=(mode == "Test")) 
        client.ping()
        st.session_state.futures_client = client 
        return client
    except BinanceAPIException as e:
        st.error(f"❌ 선물 API 연결 실패: {e}"); return None
    except Exception as e:
        st.error(f"❌ 선물 클라이언트 생성 오류: {e}"); return None

def get_spot_client(config):
    if st.session_state.spot_client:
        try:
            st.session_state.spot_client.ping()
            return st.session_state.spot_client
        except:
            st.session_state.spot_client = None
    
    mode = config.get("mode", "Test")
    api_key = config.get("testnet_api_key") if mode == "Test" else config.get("live_api_key")
    secret_key = config.get("testnet_secret_key") if mode == "Test" else config.get("live_secret_key")
    
    if not api_key or not secret_key:
        st.error(f"💡 {mode} 모드 현물 API 키가 필요합니다."); return None
    
    try:
        if mode == "Test":
            client = Client(api_key, secret_key, testnet=True)
            st.info("🔗 현물 테스트넷에 연결 중...")
        else:
            client = Client(api_key, secret_key)
            st.info("🔗 현물 라이브넷에 연결 중...")
        
        try:
            server_time = client.get_server_time()
            if server_time and 'serverTime' in server_time:
                st.success(f"✅ 현물 {mode} 모드 연결 성공!")
                st.session_state.spot_client = client
                return client
            else:
                st.error("❌ 현물 서버 시간 조회 실패"); return None
        except Exception as e:
            st.error(f"❌ 현물 서버 시간 조회 실패: {e}"); return None
            
    except BinanceAPIException as e:
        error_msg = str(e)
        if "Invalid API-key" in error_msg: st.error(f"❌ 현물 API 키가 유효하지 않습니다. API 키를 확인해주세요.")
        elif "IP" in error_msg: st.error(f"❌ 현물 API IP 제한이 설정되어 있습니다. IP 화이트리스트를 확인해주세요.")
        else: st.error(f"❌ 현물 API 연결 실패: {e}")
        return None
    except Exception as e:
        error_msg = str(e)
        if "DNS" in error_msg or "network" in error_msg.lower(): st.error(f"❌ 현물 네트워크 연결 오류: 인터넷 연결을 확인해주세요.")
        else: st.error(f"❌ 현물 클라이언트 생성 오류: {e}")
        return None

# --- 실시간 차트 표시 ---
def display_chart(client, market_type, symbol, timeframe, mode):
    st.subheader(f"📊 {market_type} 실시간 가격 차트 ({symbol}, {timeframe}) - [ {mode} 모드 ]")
    try:
        if market_type == "USD-M":
            klines = client.futures_klines(symbol=symbol, interval=timeframe, limit=100)
        elif market_type == "COIN-M":
            klines = client.futures_coin_klines(symbol=symbol, interval=timeframe, limit=100)
        else: # Spot
            try:
                klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100) 
            except Exception as e:
                if "Invalid symbol" in str(e): st.error(f"'{symbol}'은(는) 현물에서 유효하지 않은 심볼입니다.")
                else: st.error(f"현물 차트 데이터 조회 오류: {e}")
                return
            
        if not klines: st.warning("차트 데이터를 가져올 수 없습니다."); return

        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close']: df[col] = pd.to_numeric(df[col])
        df['SMA10'] = df['close'].rolling(window=10).mean(); df['SMA50'] = df['close'].rolling(window=50).mean()
        fig = go.Figure(data=[go.Candlestick(x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name=symbol)])
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['SMA10'], mode='lines', name='SMA 10', line=dict(color='orange', width=1)))
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['SMA50'], mode='lines', name='SMA 50', line=dict(color='purple', width=1)))
        fig.update_layout(title=f'{symbol} Chart ({timeframe})', yaxis_title='Price', xaxis_rangeslider_visible=False, height=500)
        st.plotly_chart(fig, use_container_width=True)
    except BinanceAPIException as e:
         st.error(f"차트 데이터 조회 오류 (API): {e}")
    except Exception as e:
        if "Invalid symbol" in str(e): st.error(f"'{symbol}'은(는) 유효하지 않은 심볼입니다. 심볼을 확인해주세요.")
        else: st.error(f"차트 표시 중 오류 발생: {e}")

# --- 로그 파일 읽기 ---
def read_log_file(log_path):
    try:
        if not log_path.startswith("logs/"):
            log_path = f"logs/{log_path}"
        with open(log_path, "r", encoding='utf-8') as f: return f.read()
    except Exception: return "로그 파일 없음."

# --- 거래 내역 조회 ---
def fetch_trade_history(client, market_type, symbol, selected_date):
    st.info(f"{market_type} '{symbol}' ({selected_date.strftime('%Y-%m-%d')}) 거래 내역 조회 (UTC 기준)...")
    try:
        start_ts = int(datetime.combine(selected_date, datetime.min.time()).timestamp() * 1000)
        end_ts = int(datetime.combine(selected_date, datetime.max.time()).timestamp() * 1000)
        
        if market_type == "USD-M":
            trades = client.futures_account_trades(symbol=symbol, startTime=start_ts, endTime=end_ts)
        elif market_type == "COIN-M":
            trades = client.futures_coin_account_trades(symbol=symbol, startTime=start_ts, endTime=end_ts)
        else: # Spot
            trades = client.get_my_trades(symbol=symbol, startTime=start_ts, endTime=end_ts) 
            
        if not trades: st.warning("해당 기간 거래 내역 없음."); return pd.DataFrame()
        
        df = pd.DataFrame(trades)
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        
        if market_type == "Spot":
            df['realizedPnl'] = 0.0 
            df['qty'] = pd.to_numeric(df['qty'])
            df['quoteQty'] = pd.to_numeric(df['quoteQty'])
            df['commission'] = pd.to_numeric(df['commission'])
            df['side'] = df['isBuyer'].apply(lambda x: 'BUY' if x else 'SELL')
            df['price'] = pd.to_numeric(df['price'])
            df['time_kst'] = df['time'] + timedelta(hours=9)
            return df[['time_kst', 'symbol', 'side', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset']]
        else:
            for col in ['price', 'qty', 'realizedPnl', 'commission']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
                else: df[col] = 0
            df['time_kst'] = df['time'] + timedelta(hours=9)
            return df[['time_kst', 'symbol', 'side', 'price', 'qty', 'commission', 'realizedPnl']]

    except BinanceAPIException as e:
         st.error(f"거래 내역 조회 오류 (API): {e}"); return pd.DataFrame()
    except Exception as e: st.error(f"거래 내역 처리 중 오류: {e}"); return pd.DataFrame()

# --- 투자 보고서 생성 ---
def generate_report(futures_client, spot_client, config, selected_date, usd_m_trades_df, coin_m_trades_df, spot_trades_df):
    date_str = selected_date.strftime('%Y-%m-%d'); report_lines = [f"# {date_str} 통합 투자 보고서\n"]
    report_lines.append("## 🤖 봇 활동 요약 (로그 기반)\n")
    for market_type in ["usd_m", "coin_m", "spot"]: 
        log_file = f"logs/{market_type}_log_{date_str}.txt"; log_content = read_log_file(log_file)
        report_lines.append(f"### {market_type.upper()} 봇\n")
        if "로그 파일 없음" in log_content: 
            report_lines.append("- 로그 파일 없음")
            continue
        
        entries = len(re.findall(r">>> \[.+? 진입 신호\]", log_content))
        exits = len(re.findall(r">>> \[.+? 종료 신호\]", log_content))
        orders_succeeded = log_content.count("--- 주문 성공 ---")
        orders_failed = log_content.count("*** 주문 실패:")
        has_trades = "주문 성공" in log_content or "진입 신호" in log_content or "종료 신호" in log_content
        
        report_lines.append(f"- 진입/종료 신호: {entries}회 / {exits}회")
        report_lines.append(f"- 주문 성공/실패: {orders_succeeded}회 / {orders_failed}회")
        
        if not has_trades:
            report_lines.append("- **봇 활동 없음**: 해당 날짜에 거래 신호나 주문이 발생하지 않았습니다.")
        
        if orders_failed > 0:
            errors = re.findall(r"\*\*\* 주문 실패: (.*?) \*\*\*", log_content)
            if errors: report_lines.append(f"  - 주요 실패 원인: `{errors[0]}`")

    report_lines.append("\n## 📈 실제 거래 성과 (API 기반)\n")
    total_usd_pnl = 0.0 
    
    report_lines.append("### 🔍 거래 데이터 확인\n")
    report_lines.append(f"- USD-M 거래 데이터: {'있음' if usd_m_trades_df is not None and not usd_m_trades_df.empty else '없음'}")
    report_lines.append(f"- COIN-M 거래 데이터: {'있음' if coin_m_trades_df is not None and not coin_m_trades_df.empty else '없음'}")
    report_lines.append(f"- Spot 거래 데이터: {'있음' if spot_trades_df is not None and not spot_trades_df.empty else '없음'}")
    
    if usd_m_trades_df is not None and not usd_m_trades_df.empty: report_lines.append(f"- USD-M 거래 건수: {len(usd_m_trades_df)}건")
    if coin_m_trades_df is not None and not coin_m_trades_df.empty: report_lines.append(f"- COIN-M 거래 건수: {len(coin_m_trades_df)}건")
    if spot_trades_df is not None and not spot_trades_df.empty: report_lines.append(f"- Spot 거래 건수: {len(spot_trades_df)}건")
    
    report_lines.append("### USD-M 거래\n")
    if usd_m_trades_df is None or usd_m_trades_df.empty: 
        report_lines.append("- 조회된 실제 거래 없음")
    else:
        pnl = usd_m_trades_df['realizedPnl'].sum()
        commission = usd_m_trades_df['commission'].sum()
        total_trades = len(usd_m_trades_df)
        wins = len(usd_m_trades_df[usd_m_trades_df['realizedPnl'] > 0])
        losses = len(usd_m_trades_df[usd_m_trades_df['realizedPnl'] < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        total_usd_pnl += pnl
        
        report_lines.append(f"- **실현 손익: {pnl:.8f} USDT**")
        report_lines.append(f"- 총 수수료: {commission:.8f} USDT")
        report_lines.append(f"- 총 거래: {total_trades}회 (Win: {wins}, Loss: {losses})")
        report_lines.append(f"- **승률: {win_rate:.2f}%**")
        
        if total_trades > 0:
            report_lines.append(f"- 거래 상세:")
            for idx, trade in usd_m_trades_df.iterrows():
                side_emoji = "📈" if trade['side'] == 'BUY' else "📉"
                pnl_emoji = "💰" if trade['realizedPnl'] > 0 else "💸"
                report_lines.append(f"  {side_emoji} {trade['time_kst']} | {trade['side']} {trade['qty']} @ {trade['price']:.2f} | {pnl_emoji} PnL: {trade['realizedPnl']:.4f}")
        
    report_lines.append("\n### COIN-M 거래\n")
    coin_m_pnl_usdt = 0.0
    if coin_m_trades_df is None or coin_m_trades_df.empty: 
        report_lines.append("- 조회된 실제 거래 없음")
    else:
        pnl = coin_m_trades_df['realizedPnl'].sum()
        commission = coin_m_trades_df['commission'].sum()
        total_trades = len(coin_m_trades_df)
        wins = len(coin_m_trades_df[coin_m_trades_df['realizedPnl'] > 0])
        losses = len(coin_m_trades_df[coin_m_trades_df['realizedPnl'] < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        
        report_lines.append(f"- **실현 손익 (코인 기준): {pnl:.8f}**")
        report_lines.append(f"- 총 수수료 (코인 기준): {commission:.8f}")
        report_lines.append(f"- 총 거래: {total_trades}회 (Win: {wins}, Loss: {losses})")
        report_lines.append(f"- **승률: {win_rate:.2f}%**")
        
        if total_trades > 0:
            report_lines.append(f"- 거래 상세:")
            for idx, trade in coin_m_trades_df.iterrows():
                side_emoji = "📈" if trade['side'] == 'BUY' else "📉"
                pnl_emoji = "💰" if trade['realizedPnl'] > 0 else "💸"
                report_lines.append(f"  {side_emoji} {trade['time_kst']} | {trade['side']} {trade['qty']} @ {trade['price']:.2f} | {pnl_emoji} PnL: {trade['realizedPnl']:.4f}")
        
        try:
            if futures_client and pnl != 0: 
                coin_m_symbol = config.get("coin_m_settings", {}).get("symbol", "BTCUSD_PERP")
                ticker_symbol = coin_m_symbol.split('_')[0].replace("USD", "") + "USDT"
                ticker = futures_client.get_symbol_ticker(symbol=ticker_symbol) 
                current_price = float(ticker['price'])
                coin_m_pnl_usdt = pnl * current_price
                total_usd_pnl += coin_m_pnl_usdt
                report_lines.append(f"- **실현 손익 (USDT 환산): {coin_m_pnl_usdt:.4f} USDT** (현재가 {current_price} 기준)")
        except BinanceAPIException as e:
            report_lines.append(f"- USDT 환산 실패 (API 오류): `{e}`")
        except Exception as e:
            report_lines.append(f"- USDT 환산 실패: `{e}`")

    report_lines.append("\n### Spot (현물) 거래\n")
    if spot_trades_df is None or spot_trades_df.empty: 
        report_lines.append("- 조회된 실제 거래 없음")
    else:
        total_buy_usdt = spot_trades_df[spot_trades_df['side'] == 'BUY']['quoteQty'].sum()
        total_sell_usdt = spot_trades_df[spot_trades_df['side'] == 'SELL']['quoteQty'].sum()
        total_trades = len(spot_trades_df)
        buy_count = len(spot_trades_df[spot_trades_df['side'] == 'BUY'])
        sell_count = len(spot_trades_df[spot_trades_df['side'] == 'SELL'])
        
        report_lines.append(f"- 총 매수 금액: {total_buy_usdt:.4f} USDT")
        report_lines.append(f"- 총 매도 금액: {total_sell_usdt:.4f} USDT")
        report_lines.append(f"- 총 거래: {total_trades}회 (Buy: {buy_count}, Sell: {sell_count})")
        
        if total_trades > 0:
            report_lines.append(f"- 거래 상세:")
            for idx, trade in spot_trades_df.iterrows():
                side_emoji = "📈" if trade['side'] == 'BUY' else "📉"
                report_lines.append(f"  {side_emoji} {trade['time_kst']} | {trade['side']} {trade['qty']} @ {trade['price']:.2f} | 금액: {trade['quoteQty']:.2f}")
        
        commissions = spot_trades_df.groupby('commissionAsset')['commission'].sum()
        if not commissions.empty:
            report_lines.append("- 총 수수료:")
            for asset, total_comm in commissions.items():
                report_lines.append(f"  - {total_comm:.8f} {asset}")
        else:
            report_lines.append("- 총 수수료: 0")

    report_lines.append(f"\n## 💰 전체 요약 (선물 기준)\n")
    report_lines.append(f"- **USD-M 총 실현 손익: {total_usd_pnl - coin_m_pnl_usdt:.8f} USDT**")
    if coin_m_pnl_usdt != 0:
        report_lines.append(f"- **COIN-M 총 실현 손익 (USDT 환산): {coin_m_pnl_usdt:.4f} USDT**")
    report_lines.append(f"### **📈 통합 총 실현 손익 (선물): {total_usd_pnl:.4f} USDT**")
    report_lines.append("\n**참고:** 현물 거래는 PNL이 아닌 매수/매도 총액으로 집계됩니다.")
    return "\n".join(report_lines)


def render_log_tab(title, is_running, log_file_base, auto_refresh_key, refresh_btn_key, log_area_key):
    st.subheader(title)
    log_file = f"logs/{log_file_base}_{datetime.now().strftime('%Y-%m-%d')}.txt"
    if is_running:
        col1, col2 = st.columns([1, 3])
        auto_refresh = col1.checkbox("자동 새로고침", value=st.session_state.get(auto_refresh_key, True), key=f"{auto_refresh_key}_check")
        st.session_state[auto_refresh_key] = auto_refresh
        if col2.button("🔄 수동 새로고침", key=refresh_btn_key): st.rerun()
    log_content = read_log_file(log_file)
    if "로그 파일 없음" in log_content: st.info("💡 로그 파일 없음.") 
    elif "로그 읽기" in log_content: st.error(log_content)
    else: st.text_area("로그 출력", log_content, height=500, key=log_area_key)
    if is_running and st.session_state.get(auto_refresh_key, False): time.sleep(2); st.rerun()

# --- 사이드바 UI (현물 추가) ---
with st.sidebar:
    st.header("⚙️ 통합 봇 설정")
    config = load_config()
    mode = st.radio("거래 환경 선택", ("Test", "Live"), index=0 if config.get("mode", "Test") == "Test" else 1, key="mode_radio")
    st.markdown("---"); st.subheader("🔑 API 키")
    with st.expander("API 키 설정 (Test/Live 공용)"):
        testnet_api_key = st.text_input("Testnet API Key", value=config.get("testnet_api_key", ""), type="password", key="tn_api")
        testnet_secret_key = st.text_input("Testnet Secret Key", value=config.get("testnet_secret_key", ""), type="password", key="tn_secret")
        live_api_key = st.text_input("Live API Key", value=config.get("live_api_key", ""), type="password", key="live_api")
        live_secret_key = st.text_input("Live Secret Key", value=config.get("live_secret_key", ""), type="password", key="live_secret")
    
    st.markdown("---"); st.subheader("💵 USD-M 봇 설정")
    usd_m_settings = config.get("usd_m_settings", {})
    usd_m_symbol = st.text_input("USD-M 심볼", value=usd_m_settings.get("symbol", "BTCUSDT"), help="예: BTCUSDT...", key="usd_symbol")
    usd_m_margin_type = st.radio("USD-M 마진 타입", ("ISOLATED", "CROSSED"), index=["ISOLATED", "CROSSED"].index(usd_m_settings.get("margin_type", "ISOLATED")), key="usd_margin_radio")
    usd_m_quantity = st.number_input("USD-M 수량(코인)", value=usd_m_settings.get("quantity", 0.001), min_value=0.0, format="%.5f", step=0.001, help=f"{usd_m_symbol[:3]} 수량", key="usd_qty")
    usd_m_leverage = st.number_input("USD-M 레버리지", min_value=1, max_value=50, value=usd_m_settings.get("leverage", 3), key="usd_lev")
    usd_m_stop_loss = st.number_input("USD-M 손절매(%)", min_value=0.1, max_value=20.0, value=usd_m_settings.get("stop_loss_pct", 2.0), step=0.1, format="%.1f", key="usd_sl")
    usd_m_take_profit = st.number_input("USD-M 익절 비율(%)", min_value=0.1, value=usd_m_settings.get("take_profit_pct", 5.0), step=0.1, format="%.1f", key="usd_tp")
    usd_m_timeframe = st.selectbox("USD-M 타임프레임", ["15m", "1h", "4h"], index=["15m", "1h", "4h"].index(usd_m_settings.get("timeframe", "1h")), key="usd_tf")
    
    st.markdown("---"); st.subheader("🪙 COIN-M 봇 설정")
    coin_m_settings = config.get("coin_m_settings", {})
    coin_m_symbol = st.text_input("COIN-M 심볼", value=coin_m_settings.get("symbol", "BTCUSD_PERP"), help="예: BTCUSD_PERP...", key="coin_symbol")
    coin_m_margin_type = st.radio("COIN-M 마진 타입", ("ISOLATED", "CROSSED"), index=["ISOLATED", "CROSSED"].index(coin_m_settings.get("margin_type", "ISOLATED")), key="coin_margin_radio")
    coin_m_quantity = st.number_input("COIN-M 수량(계약)", value=coin_m_settings.get("quantity", 1), min_value=1, format="%d", step=1, help="계약 수", key="coin_qty")
    coin_m_leverage = st.number_input("COIN-M 레버리지", min_value=1, max_value=50, value=coin_m_settings.get("leverage", 3), key="coin_lev")
    coin_m_stop_loss = st.number_input("COIN-M 손절매(%)", min_value=0.1, max_value=20.0, value=coin_m_settings.get("stop_loss_pct", 2.0), step=0.1, format="%.1f", key="coin_sl")
    coin_m_take_profit = st.number_input("COIN-M 익절 비율(%)", min_value=0.1, value=coin_m_settings.get("take_profit_pct", 5.0), step=0.1, format="%.1f", key="coin_tp")
    coin_m_timeframe = st.selectbox("COIN-M 타임프레임", ["15m", "1h", "4h"], index=["15m", "1h", "4h"].index(coin_m_settings.get("timeframe", "1h")), key="coin_tf")
    
    st.markdown("---"); st.subheader("📈 Spot (현물) 봇 설정")
    spot_settings = config.get("spot_settings", {})
    spot_symbol = st.text_input("현물 심볼", value=spot_settings.get("symbol", "BTCUSDT"), key="spot_symbol")
    spot_quantity_usdt = st.number_input("현물 매수금액(USDT)", 10.0, value=spot_settings.get("quantity_usdt", 11.0), step=1.0, format="%.2f", key="spot_quantity", help="USDT로 구매할 금액 (최소 10~11 USDT 권장)")
    spot_stop_loss = st.number_input("현물 손절매 (%)", 0.1, 20.0, spot_settings.get("stop_loss_pct", 5.0), 0.1, "%.1f", key="spot_stop_loss")
    spot_take_profit = st.number_input("현물 익절 비율 (%)", 0.1, value=spot_settings.get("take_profit_pct", 5.0), step=0.1, format="%.1f", key="spot_take_profit")
    spot_timeframe = st.selectbox("현물 타임프레임", ["15m", "1h", "4h"], index=["15m", "1h", "4h"].index(spot_settings.get("timeframe", "1h")), key="spot_timeframe")
    
    
    indicator_settings = config.get("indicator_settings", {})
    quick_setup_mode = st.session_state.get('quick_setup', None)
    
    if quick_setup_mode == "conservative":
        default_use_sma = True; default_use_rsi = True; default_use_macd = True
        default_use_bb = True; default_use_stoch = True; default_use_stoch_cross = True
        default_use_volume = True; default_min_conditions = 7
    elif quick_setup_mode == "balanced":
        default_use_sma = True; default_use_rsi = True; default_use_macd = True
        default_use_bb = True; default_use_stoch = False; default_use_stoch_cross = False
        default_use_volume = False; default_min_conditions = 4
    elif quick_setup_mode == "aggressive":
        default_use_sma = True; default_use_rsi = False; default_use_macd = True
        default_use_bb = False; default_use_stoch = False; default_use_stoch_cross = False
        default_use_volume = False; default_min_conditions = 2
    else:
        default_use_sma = indicator_settings.get("use_sma", True)
        default_use_rsi = indicator_settings.get("use_rsi", True)
        default_use_macd = indicator_settings.get("use_macd", True)
        default_use_bb = indicator_settings.get("use_bb", True)
        default_use_stoch = indicator_settings.get("use_stoch", False)
        default_use_stoch_cross = indicator_settings.get("use_stoch_cross", False)
        default_use_volume = indicator_settings.get("use_volume", False)
        default_min_conditions = indicator_settings.get("min_conditions", 7)
    
    # [수정] 종료 조건 기본값 불러오기
    default_min_exit_conditions = indicator_settings.get("min_exit_conditions", 3)
    
    # [수정] 헤더 변경
    st.markdown("---"); st.subheader("🎯 지표 조건 설정")
    
    # [수정] expander 이름 변경
    with st.expander("📊 지표별 진입/종료 조건 설정", expanded=(quick_setup_mode is not None)):
        st.markdown("**각 지표를 개별적으로 활성화/비활성화할 수 있습니다.**")
        
        st.markdown("#### 📈 기본 지표")
        use_sma = st.checkbox("SMA 골든/데드 크로스 사용", value=default_use_sma, key="use_sma")
        use_rsi = st.checkbox("RSI 과매수/과매도 사용", value=default_use_rsi, key="use_rsi")
        use_macd = st.checkbox("MACD 모멘텀 사용", value=default_use_macd, key="use_macd")
        use_bb = st.checkbox("볼린저 밴드 사용", value=default_use_bb, key="use_bb")
        
        st.markdown("#### 📊 신호 지표")
        use_stoch = st.checkbox("스토캐스틱 과매수/과매도 사용", value=default_use_stoch, key="use_stoch")
        use_stoch_cross = st.checkbox("스토캐스틱 전환 사용", value=default_use_stoch_cross, key="use_stoch_cross")
        use_volume = st.checkbox("거래량 증가 사용", value=default_use_volume, key="use_volume")
        
        # [수정] 섹션 이름 변경
        st.markdown("#### ⚙️ 진입/종료 조건 설정")
        min_conditions = st.slider("최소 진입 조건 수", 1, 7, value=default_min_conditions, key="min_conditions", 
                                 help="몇 개의 '진입' 조건을 만족해야 진입할지 설정 (1-7개)")
        
        # [수정] 최소 종료 조건 슬라이더 추가
        min_exit_conditions = st.slider("최소 종료 조건 수", 1, 5, value=default_min_exit_conditions, key="min_exit_conditions",
                                        help="몇 개의 '종료' 조건을 만족해야 종료할지 설정 (1-5개)")
        
        st.markdown("#### 🔧 고급 설정")
        rsi_oversold = st.number_input("RSI 과매도 기준", 10, 40, value=indicator_settings.get("rsi_oversold", 30), key="rsi_oversold")
        rsi_overbought = st.number_input("RSI 과매수 기준", 60, 90, value=indicator_settings.get("rsi_overbought", 70), key="rsi_overbought")
        stoch_oversold = st.number_input("스토캐스틱 과매도 기준", 10, 30, value=indicator_settings.get("stoch_oversold", 20), key="stoch_oversold")
        stoch_overbought = st.number_input("스토캐스틱 과매수 기준", 70, 90, value=indicator_settings.get("stoch_overbought", 80), key="stoch_overbought")
        volume_multiplier = st.number_input("거래량 증가 배수", 1.0, 3.0, value=indicator_settings.get("volume_multiplier", 1.2), step=0.1, key="volume_multiplier")
    
    
    if st.button("모든 설정 저장 및 적용", use_container_width=True, type="primary", key="save_btn"):
        save_config({
            "mode": mode, "testnet_api_key": testnet_api_key, "testnet_secret_key": testnet_secret_key,
            "live_api_key": live_api_key, "live_secret_key": live_secret_key,
            "usd_m_settings": {"symbol": usd_m_symbol.upper(), "margin_type": usd_m_margin_type, "leverage": usd_m_leverage, "stop_loss_pct": usd_m_stop_loss, "take_profit_pct": usd_m_take_profit, "quantity": usd_m_quantity, "timeframe": usd_m_timeframe},
            "coin_m_settings": {"symbol": coin_m_symbol.upper(), "margin_type": coin_m_margin_type, "leverage": coin_m_leverage, "stop_loss_pct": coin_m_stop_loss, "take_profit_pct": coin_m_take_profit, "quantity": coin_m_quantity, "timeframe": coin_m_timeframe},
            "spot_settings": {"symbol": spot_symbol.upper(), "quantity_usdt": spot_quantity_usdt, "stop_loss_pct": spot_stop_loss, "take_profit_pct": spot_take_profit, "timeframe": spot_timeframe},
            "indicator_settings": {
                "use_sma": use_sma, "use_rsi": use_rsi, "use_macd": use_macd, "use_bb": use_bb,
                "use_stoch": use_stoch, "use_stoch_cross": use_stoch_cross, "use_volume": use_volume,
                "min_conditions": min_conditions, 
                "min_exit_conditions": min_exit_conditions, # [수정] 저장 로직에 추가
                "rsi_oversold": rsi_oversold, "rsi_overbought": rsi_overbought,
                "stoch_oversold": stoch_oversold, "stoch_overbought": stoch_overbought, "volume_multiplier": volume_multiplier
            }
        })
        
        if 'quick_setup' in st.session_state:
            del st.session_state['quick_setup']
            
        st.rerun()

# --- 메인 대시보드 UI ---
config = load_config(); mode = config.get('mode', 'Test')
st.title(f"📈 통합 자동매매 대시보드 - [ {mode} 모드 ]"); st.markdown("---")
IS_WINDOWS = os.name == 'nt'

def start_process(script_path):
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    return subprocess.Popen(["python", script_path], creationflags=creationflags)
def stop_process(process):
    if process is None: return
    try:
        if IS_WINDOWS: os.kill(process.pid, signal.CTRL_C_EVENT)
        else: process.send_signal(signal.SIGINT)
        process.wait(timeout=10); st.toast(f"✔️ PID {process.pid} 봇 안전 종료.")
    except (subprocess.TimeoutExpired, ProcessLookupError):
        process.kill(); st.warning(f"PID {process.pid} 강제 종료.")

bot_scripts = {
    "USD-M": USD_M_BOT_SCRIPT,
    "COIN-M": COIN_M_BOT_SCRIPT,
    "Spot (현물)": SPOT_BOT_SCRIPT
}

for market, script in bot_scripts.items():
    st.header(f"💵 {market} Bot Controller") 
    col1, col2 = st.columns(2) 
    process_key = f"{market.lower().split(' ')[0]}_process" 
    status_placeholder = st.empty() 
    
    process = st.session_state.get(process_key)
    is_running = process is not None and process.poll() is None

    if col1.button(f"🚀 {market} 봇 시작", use_container_width=True, key=f"start_{process_key}", disabled=is_running):
        st.toast(f"[ {mode} ] {market} 봇 시작..."); st.session_state[process_key] = start_process(script); st.rerun()
    if col2.button(f"🛑 {market} 봇 중지", use_container_width=True, key=f"stop_{process_key}", disabled=not is_running):
        current_process = st.session_state.get(process_key)
        if current_process:
            st.toast(f"{market} 봇 종료 시도..."); 
            stop_process(current_process)
            st.session_state[process_key] = None
            st.rerun()
        else:
            st.warning(f"{market} 봇이 실행 중이지 않습니다.")
            
    if is_running:
        status_placeholder.info(f"✅ **{market} 상태:** 실행 중 (PID: {st.session_state[process_key].pid})")
    else:
        status_placeholder.info(f"⚠️ **{market} 상태:** 중지됨")


st.markdown("---")
tab_list = ["📊 차트", "🔍 실시간 분석", "📝 USD-M 로그", "📝 COIN-M 로그", "📝 Spot 로그", "📜 거래 내역", "📄 보고서"]
tab_chart, tab_analysis, tab_usd_log, tab_coin_log, tab_spot_log, tab_trade_history, tab_report = st.tabs(tab_list)

with tab_chart:
    chart_market_type = st.radio("표시할 차트 선택", ("USD-M", "COIN-M", "Spot"), horizontal=True, key="chart_radio")
    
    client = None
    if chart_market_type == "USD-M":
        client = get_futures_client(config)
        current_symbol = config.get("usd_m_settings", {}).get("symbol", "BTCUSDT")
        current_timeframe = config.get("usd_m_settings", {}).get("timeframe", "1h")
    elif chart_market_type == "COIN-M":
        client = get_futures_client(config)
        current_symbol = config.get("coin_m_settings", {}).get("symbol", "BTCUSD_PERP")
        current_timeframe = config.get("coin_m_settings", {}).get("timeframe", "1h")
    else: # Spot
        client = get_spot_client(config)
        current_symbol = config.get("spot_settings", {}).get("symbol", "BTCUSDT")
        current_timeframe = config.get("spot_settings", {}).get("timeframe", "1h")

    if client: 
        if current_symbol and current_timeframe:
             display_chart(client, chart_market_type, current_symbol.upper(), current_timeframe, mode)
    else:
        st.warning(f"차트를 표시하려면 {chart_market_type} API 키를 설정하거나 네트워크를 확인하세요.")

# --- 실시간 분석 탭 [수정됨] ---
with tab_analysis:
    st.header("🔍 실시간 시장 분석")
    st.markdown("**7개 지표 기반 포지션 진입/종료 안정성 분석**")
    
    analysis_market = st.radio("분석할 시장 선택", ("USD-M", "COIN-M", "Spot"), horizontal=True, key="analysis_radio")
    
    analysis_client = None; analysis_symbol = None; analysis_timeframe = None
    
    if analysis_market == "USD-M":
        analysis_client = get_futures_client(config)
        analysis_symbol = config.get("usd_m_settings", {}).get("symbol", "BTCUSDT")
        analysis_timeframe = config.get("usd_m_settings", {}).get("timeframe", "1h")
    elif analysis_market == "COIN-M":
        analysis_client = get_futures_client(config)
        analysis_symbol = config.get("coin_m_settings", {}).get("symbol", "BTCUSD_PERP")
        analysis_timeframe = config.get("coin_m_settings", {}).get("timeframe", "1h")
    else: # Spot
        analysis_client = get_spot_client(config)
        analysis_symbol = config.get("spot_settings", {}).get("symbol", "BTCUSDT")
        analysis_timeframe = config.get("spot_settings", {}).get("timeframe", "1h")
    
    if analysis_client and analysis_symbol and analysis_timeframe:
        try:
            if analysis_market == "USD-M":
                klines = analysis_client.futures_klines(symbol=analysis_symbol, interval=analysis_timeframe, limit=200)
            elif analysis_market == "COIN-M":
                klines = analysis_client.futures_coin_klines(symbol=analysis_symbol, interval=analysis_timeframe, limit=200)
            else: # Spot
                klines = analysis_client.get_klines(symbol=analysis_symbol, interval=analysis_timeframe, limit=200)
            
            if klines:
                df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
                numeric_cols = ['open', 'high', 'low', 'close', 'volume']
                for col in numeric_cols: df[col] = pd.to_numeric(df[col])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                
                try:
                    import pandas_ta as ta
                    df.ta.sma(length=10, append=True)
                    df.ta.sma(length=50, append=True)
                    df.ta.rsi(length=14, append=True)
                    df.ta.bbands(length=20, append=True)
                    df.ta.macd(fast=12, slow=26, signal=9, append=True)
                    df.ta.stoch(high='high', low='low', close='close', k=14, d=3, append=True)
                    df.ta.sma(length=20, close='volume', append=True)
                except ImportError:
                    st.error("pandas_ta 라이브러리가 필요합니다. pip install pandas_ta")
                    st.stop() 
                
                latest = df.iloc[-2]
                current_price = df.iloc[-1]['close']
                
                sma_short = latest.get('SMA_10', current_price)
                sma_long = latest.get('SMA_50', current_price)
                rsi = latest.get('RSI_14', 50)
                macd = latest.get('MACD_12_26_9', 0)
                macd_signal = latest.get('MACDs_12_26_9', 0)
                stoch_k = latest.get('STOCHk_14_3_3', 50)
                stoch_d = latest.get('STOCHd_14_3_3', 50)
                current_volume = latest['volume']
                volume_sma = latest.get('SMA_20_volume', current_volume)
                
                bb_cols = [col for col in df.columns if col.startswith('BB')]
                bbl_col = next((c for c in bb_cols if 'BBL' in c), None)
                bbu_col = next((c for c in bb_cols if 'BBU' in c), None)
                bbl = latest.get(bbl_col, current_price) if bbl_col else current_price
                bbu = latest.get(bbu_col, current_price) if bbu_col else current_price
                
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                st.markdown(f"### 📊 {analysis_market} 시장 분석 - {analysis_symbol}")
                st.markdown(f"**분석 시간**: {current_time}")
                st.markdown(f"**현재 가격**: {current_price:,.2f}")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### 📈 기본 지표")
                    st.metric("SMA 10", f"{sma_short:,.2f}")
                    st.metric("SMA 50", f"{sma_long:,.2f}")
                    st.metric("RSI", f"{rsi:.2f}")
                    st.metric("MACD", f"{macd:.6f}")
                    st.metric("MACD Signal", f"{macd_signal:.6f}")
                with col2:
                    st.markdown("#### 📊 추가 지표")
                    st.metric("BB 상단", f"{bbu:,.2f}")
                    st.metric("BB 하단", f"{bbl:,.2f}")
                    st.metric("스토캐스틱 K", f"{stoch_k:.2f}")
                    st.metric("스토캐스틱 D", f"{stoch_d:.2f}")
                    st.metric("거래량", f"{current_volume:,.0f}")
                    st.metric("거래량 SMA20", f"{volume_sma:,.0f}")
                
                
                default_indicator_settings = config.get("indicator_settings", {})
                
                use_sma = st.session_state.get("use_sma", default_indicator_settings.get("use_sma", True))
                use_rsi = st.session_state.get("use_rsi", default_indicator_settings.get("use_rsi", True))
                use_macd = st.session_state.get("use_macd", default_indicator_settings.get("use_macd", True))
                use_bb = st.session_state.get("use_bb", default_indicator_settings.get("use_bb", True))
                use_stoch = st.session_state.get("use_stoch", default_indicator_settings.get("use_stoch", True))
                use_stoch_cross = st.session_state.get("use_stoch_cross", default_indicator_settings.get("use_stoch_cross", True))
                use_volume = st.session_state.get("use_volume", default_indicator_settings.get("use_volume", True))
                min_conditions = st.session_state.get("min_conditions", default_indicator_settings.get("min_conditions", 7))
                # [수정] 종료 조건 불러오기
                min_exit_conditions = st.session_state.get("min_exit_conditions", default_indicator_settings.get("min_exit_conditions", 3))
                
                rsi_oversold = st.session_state.get("rsi_oversold", default_indicator_settings.get("rsi_oversold", 30))
                rsi_overbought = st.session_state.get("rsi_overbought", default_indicator_settings.get("rsi_overbought", 70))
                stoch_oversold = st.session_state.get("stoch_oversold", default_indicator_settings.get("stoch_oversold", 20))
                stoch_overbought = st.session_state.get("stoch_overbought", default_indicator_settings.get("stoch_overbought", 80))
                volume_multiplier = st.session_state.get("volume_multiplier", default_indicator_settings.get("volume_multiplier", 1.2))
                
                
                st.markdown("---")
                # [수정] 헤더 변경
                st.markdown("### 🎯 포지션 조건 분석")
                # [수정] 종료 조건 표시
                st.markdown(f"**설정된 최소 진입 조건**: {min_conditions}개 | **설정된 최소 종료 조건**: {min_exit_conditions}개")
                
                st.markdown("#### 📈 롱 진입 조건")
                long_conditions = {}
                if use_sma: long_conditions["SMA 골든 크로스"] = sma_short > sma_long
                if use_rsi: long_conditions[f"RSI < {rsi_overbought}"] = rsi < rsi_overbought
                if use_macd: long_conditions["MACD > Signal"] = macd > macd_signal
                if use_bb: long_conditions["Close > BB하단"] = current_price > bbl
                if use_stoch: long_conditions[f"스토캐스틱 과매도 (K,D < {stoch_oversold})"] = stoch_k < stoch_oversold and stoch_d < stoch_oversold
                if use_stoch_cross: long_conditions["스토캐스틱 상승전환"] = stoch_k > stoch_d
                if use_volume: long_conditions[f"거래량 증가 ({volume_multiplier:,.2f}x)"] = current_volume > volume_sma * volume_multiplier
                
                long_satisfied = sum(long_conditions.values())
                long_total = len(long_conditions)
                
                if long_total > 0:
                    for condition, satisfied in long_conditions.items():
                        st.write(f"{'✅' if satisfied else '❌'} {condition}")
                    st.markdown(f"**롱 진입 조건**: {long_satisfied}/{long_total} 만족")
                else:
                    st.warning("활성화된 롱 진입 지표가 없습니다.")
                
                st.markdown("#### 📉 숏 진입 조건")
                short_conditions = {}
                if use_sma: short_conditions["SMA 데드 크로스"] = sma_short < sma_long
                if use_rsi: short_conditions[f"RSI > {rsi_oversold}"] = rsi > rsi_oversold
                if use_macd: short_conditions["MACD < Signal"] = macd < macd_signal
                if use_bb: short_conditions["Close < BB상단"] = current_price < bbu
                if use_stoch: short_conditions[f"스토캐스틱 과매수 (K,D > {stoch_overbought})"] = stoch_k > stoch_overbought and stoch_d > stoch_overbought
                if use_stoch_cross: short_conditions["스토캐스틱 하락전환"] = stoch_k < stoch_d
                if use_volume: short_conditions[f"거래량 증가 ({volume_multiplier:,.2f}x)"] = current_volume > volume_sma * volume_multiplier
                
                short_satisfied = sum(short_conditions.values())
                short_total = len(short_conditions)
                
                if short_total > 0:
                    for condition, satisfied in short_conditions.items():
                        st.write(f"{'✅' if satisfied else '❌'} {condition}")
                    st.markdown(f"**숏 진입 조건**: {short_satisfied}/{short_total} 만족")
                else:
                    st.warning("활성화된 숏 진입 지표가 없습니다.")

                # [수정] 롱 종료 조건 분석 추가
                st.markdown("#### 📉 롱 종료 조건 (매도)")
                long_exit_conditions_filtered = {}
                if use_sma: long_exit_conditions_filtered["SMA 데드 크로스"] = sma_short < sma_long
                if use_rsi: long_exit_conditions_filtered["RSI < 45 (약세)"] = rsi < 45
                if use_macd: long_exit_conditions_filtered["MACD < Signal (하락)"] = macd < macd_signal
                if use_bb: long_exit_conditions_filtered["Close < BB하단"] = current_price < bbl
                if use_stoch_cross: long_exit_conditions_filtered["스토캐스틱 하락전환"] = stoch_k < stoch_d

                long_exit_satisfied = sum(long_exit_conditions_filtered.values())
                long_exit_total = len(long_exit_conditions_filtered)
                
                if long_exit_total > 0:
                    for condition, satisfied in long_exit_conditions_filtered.items():
                        st.write(f"{'✅' if satisfied else '❌'} {condition}")
                    st.markdown(f"**롱 종료 조건**: {long_exit_satisfied}/{long_exit_total} 만족")
                else:
                    st.warning("활성화된 롱 종료 지표가 없습니다.")

                # [수정] 숏 종료 조건 분석 추가
                st.markdown("#### 📈 숏 종료 조건 (매수)")
                short_exit_conditions_filtered = {}
                if use_sma: short_exit_conditions_filtered["SMA 골든 크로스"] = sma_short > sma_long
                if use_rsi: short_exit_conditions_filtered["RSI > 55 (강세)"] = rsi > 55
                if use_macd: short_exit_conditions_filtered["MACD > Signal (상승)"] = macd > macd_signal
                if use_bb: short_exit_conditions_filtered["Close > BB상단"] = current_price > bbu
                if use_stoch_cross: short_exit_conditions_filtered["스토캐스틱 상승전환"] = stoch_k > stoch_d

                short_exit_satisfied = sum(short_exit_conditions_filtered.values())
                short_exit_total = len(short_exit_conditions_filtered)

                if short_exit_total > 0:
                    for condition, satisfied in short_exit_conditions_filtered.items():
                        st.write(f"{'✅' if satisfied else '❌'} {condition}")
                    st.markdown(f"**숏 종료 조건**: {short_exit_satisfied}/{short_exit_total} 만족")
                else:
                    st.warning("활성화된 숏 종료 지표가 없습니다.")
                
                st.markdown("---")
                st.markdown("### 🎯 종합 판단")
                
                # [수정] 종합 판단 로직에 종료 조건 추가
                if long_satisfied >= min_conditions:
                    st.success(f"🚀 **롱 진입 권장** - {long_satisfied}/{long_total} 조건 만족 (최소 {min_conditions}개 필요)")
                elif short_satisfied >= min_conditions:
                    st.warning(f"📉 **숏 진입 권장** - {short_satisfied}/{short_total} 조건 만족 (최소 {min_conditions}개 필요)")
                elif long_exit_satisfied >= min_exit_conditions:
                    st.error(f"🚨 **롱 포지션 종료 권장** - {long_exit_satisfied}/{long_exit_total} 종료 조건 만족 (최소 {min_exit_conditions}개 필요)")
                elif short_exit_satisfied >= min_exit_conditions:
                    st.error(f"🚨 **숏 포지션 종료 권장** - {short_exit_satisfied}/{short_exit_total} 종료 조건 만족 (최소 {min_exit_conditions}개 필요)")
                elif long_satisfied >= min_conditions * 0.7:  
                    st.info(f"📈 **롱 진입 고려** - {long_satisfied}/{long_total} 조건 만족 (약한 신호)")
                elif short_satisfied >= min_conditions * 0.7: 
                    st.info(f"📉 **숏 진입 고려** - {short_satisfied}/{short_total} 조건 만족 (약한 신호)")
                else:
                    st.info("⏳ **대기 권장** - 설정된 최소 조건 수 미달")
                
                if (long_total > 0 or short_total > 0):
                    max_satisfied = max(long_satisfied, short_satisfied)
                    max_total = max(long_total, short_total) if max(long_total, short_total) > 0 else 1 # 0으로 나누기 방지
                    confidence = (max_satisfied / max_total) * 100
                    
                    if confidence >= 80: st.success(f"🎯 진입 신뢰도: {confidence:.1f}% (매우 높음)")
                    elif confidence >= 60: st.success(f"🎯 진입 신뢰도: {confidence:.1f}% (높음)")
                    elif confidence >= 40: st.warning(f"🎯 진입 신뢰도: {confidence:.1f}% (중간)")
                    else: st.error(f"🎯 진입 신뢰도: {confidence:.1f}% (낮음)")
                
                else:
                    st.error("🚫 **판단 불가** - 모든 지표가 비활성화되어 있습니다.")

                
                st.markdown("---")
                st.markdown("### ⚙️ 현재 설정 정보")
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**활성화된 지표:**")
                    active_indicators = []
                    if use_sma: active_indicators.append("SMA")
                    if use_rsi: active_indicators.append("RSI")
                    if use_macd: active_indicators.append("MACD")
                    if use_bb: active_indicators.append("BB")
                    if use_stoch: active_indicators.append("스토캐스틱")
                    if use_stoch_cross: active_indicators.append("스토캐스틱 전환")
                    if use_volume: active_indicators.append("거래량")
                    
                    if active_indicators: st.write(", ".join(active_indicators))
                    else: st.write("활성화된 지표 없음")
                
                with col2:
                    st.markdown("**설정값:**")
                    st.write(f"최소 진입 조건: {min_conditions}개")
                    st.write(f"최소 종료 조건: {min_exit_conditions}개") # [수정] 종료 조건 표시
                    st.write(f"RSI 기준: {rsi_oversold}-{rsi_overbought}")
                    st.write(f"스토캐스틱: {stoch_oversold}-{stoch_overbought}")
                    st.write(f"거래량 배수: {volume_multiplier:,.2f}x")
                
                st.markdown("---")
                st.markdown("### 💡 지표 설정 수정 추천")
                
                recommendations = []
                
                if (long_total > 0 or short_total > 0) and (long_satisfied < min_conditions and short_satisfied < min_conditions):
                    if min_conditions >= 5:
                        recommendations.append({"type": "warning", "title": "🔧 최소 조건 수 조정 권장", "description": f"현재 {min_conditions}개 조건이 너무 엄격합니다. 3-4개로 줄여보세요.", "action": f"최소 조건을 {max(1, min_conditions-2)}개로 설정"})
                    else:
                        recommendations.append({"type": "info", "title": "⏳ 대기 권장", "description": "현재 시장에서 명확한 신호가 없습니다. 더 나은 기회를 기다리세요.", "action": "현재 설정 유지"})
                elif (long_total == 0 and short_total == 0):
                    recommendations.append({"type": "error", "title": "🚫 지표 비활성화됨", "description": "모든 지표가 비활성화되어 봇이 작동할 수 없습니다.", "action": "'빠른 설정'을 선택하거나 지표를 1개 이상 활성화하세요."})
                
                if recommendations:
                    for i, rec in enumerate(recommendations, 1):
                        if rec["type"] == "success": st.success(f"**{i}. {rec['title']}**\n{rec['description']}\n💡 **권장사항**: {rec['action']}")
                        elif rec["type"] == "warning": st.warning(f"**{i}. {rec['title']}**\n{rec['description']}\n💡 **권장사항**: {rec['action']}")
                        elif rec["type"] == "error": st.error(f"**{i}. {rec['title']}**\n{rec['description']}\n💡 **권장사항**: {rec['action']}")
                        else: st.info(f"**{i}. {rec['title']}**\n{rec['description']}\n💡 **권장사항**: {rec['action']}")
                else:
                    st.info("💡 **현재 설정이 적절합니다.** 특별한 수정이 필요하지 않습니다.")
                
                
                st.markdown("---")
                st.markdown("### ⚡ 빠른 설정 적용")

                def set_quick_setup(mode):
                    st.session_state.quick_setup = mode 
                    
                    if mode == "conservative":
                        st.session_state.use_sma = True; st.session_state.use_rsi = True; st.session_state.use_macd = True
                        st.session_state.use_bb = True; st.session_state.use_stoch = True; st.session_state.use_stoch_cross = True
                        st.session_state.use_volume = True; st.session_state.min_conditions = 7
                    
                    elif mode == "balanced":
                        st.session_state.use_sma = True; st.session_state.use_rsi = True; st.session_state.use_macd = True
                        st.session_state.use_bb = True; st.session_state.use_stoch = False; st.session_state.use_stoch_cross = False
                        st.session_state.use_volume = False; st.session_state.min_conditions = 4

                    elif mode == "aggressive":
                        st.session_state.use_sma = True; st.session_state.use_rsi = False; st.session_state.use_macd = True
                        st.session_state.use_bb = False; st.session_state.use_stoch = False; st.session_state.use_stoch_cross = False
                        st.session_state.use_volume = False; st.session_state.min_conditions = 2
                
                col1, col2, col3 = st.columns(3)
                with col1: st.button("🎯 보수적 설정", help="모든 지표 사용, 7개 조건", on_click=set_quick_setup, args=("conservative",))
                with col2: st.button("⚖️ 균형 설정", help="기본 지표만 사용, 4개 조건", on_click=set_quick_setup, args=("balanced",))
                with col3: st.button("🚀 적극적 설정", help="핵심 지표만 사용, 2개 조건", on_click=set_quick_setup, args=("aggressive",))
                
                if 'quick_setup' in st.session_state:
                    if st.session_state.quick_setup == "conservative": st.success("🎯 **보수적 설정 적용됨**: 모든 지표 활성화, 7개 조건")
                    elif st.session_state.quick_setup == "balanced": st.success("⚖️ **균형 설정 적용됨**: 기본 지표만 활성화, 4개 조건")
                    elif st.session_state.quick_setup == "aggressive": st.success("🚀 **적극적 설정 적용됨**: 핵심 지표만 활성화, 2개 조건")
                    st.info("사이드바에서 '모든 설정 저장 및 적용'을 클릭하세요.")
                
                if st.button("🔄 분석 새로고침", key="analysis_refresh"):
                    st.rerun()
                
            else:
                st.error("데이터를 가져올 수 없습니다.")
                
        except Exception as e:
            st.error(f"분석 중 오류 발생: {e}")
            import traceback
            st.error(traceback.format_exc()) 
    else:
        st.warning(f"분석을 위해 {analysis_market} API 키를 설정해주세요.")


with tab_usd_log:
    is_usd_m_running = 'usd_m_process' in st.session_state and st.session_state.usd_m_process and st.session_state.usd_m_process.poll() is None
    render_log_tab("📝 USD-M 실시간 로그", is_usd_m_running, "usd_m_log", 'usd_m_auto_refresh', 'usd_refresh_btn', 'usd_m_log_area')

with tab_coin_log:
    is_coin_m_running = 'coin_m_process' in st.session_state and st.session_state.coin_m_process and st.session_state.coin_m_process.poll() is None
    render_log_tab("📝 COIN-M 실시간 로그", is_coin_m_running, "coin_m_log", 'coin_m_auto_refresh', 'coin_refresh_btn', 'coin_m_log_area')

with tab_spot_log:
    is_spot_running = 'spot_process' in st.session_state and st.session_state.spot_process and st.session_state.spot_process.poll() is None
    render_log_tab("📝 Spot 실시간 로그", is_spot_running, "spot_log", 'spot_auto_refresh', 'spot_refresh_btn', 'spot_log_area')

with tab_trade_history:
    st.header("📜 거래 내역 조회 (API 기반)")
    if 'history_date' not in st.session_state: st.session_state.history_date = date.today()
    selected_hist_date = st.date_input("조회할 날짜 선택", value=st.session_state.history_date, key="history_date_input")
    st.session_state.history_date = selected_hist_date 
    
    if st.button("🔄 선택 날짜 거래 내역 불러오기", key="fetch_history_btn"):
        futures_client = get_futures_client(config)
        spot_client = get_spot_client(config)
        
        if futures_client:
            with st.spinner("선물 거래 내역을 불러오는 중..."):
                usd_m_symbol_hist = config.get("usd_m_settings", {}).get("symbol", "BTCUSDT") 
                st.session_state.usd_m_trades_df = fetch_trade_history(futures_client, "USD-M", usd_m_symbol_hist, selected_hist_date)
                coin_m_symbol_hist = config.get("coin_m_settings", {}).get("symbol", "BTCUSD_PERP")
                st.session_state.coin_m_trades_df = fetch_trade_history(futures_client, "COIN-M", coin_m_symbol_hist, selected_hist_date)
        else:
            st.error("선물 API 키가 설정되지 않아 선물 거래 내역을 조회할 수 없습니다.")
            
        if spot_client:
            with st.spinner("현물 거래 내역을 불러오는 중..."):
                spot_symbol_hist = config.get("spot_settings", {}).get("symbol", "BTCUSDT")
                st.session_state.spot_trades_df = fetch_trade_history(spot_client, "Spot", spot_symbol_hist, selected_hist_date)
        else:
            st.error("현물 API 키가 설정되지 않아 현물 거래 내역을 조회할 수 없습니다.")
        
        st.success("거래 내역 조회가 완료되었습니다.")
            
    market_dfs = {
        "USD-M": st.session_state.get("usd_m_trades_df"),
        "COIN-M": st.session_state.get("coin_m_trades_df"),
        "Spot (현물)": st.session_state.get("spot_trades_df") 
    }
    date_str = selected_hist_date.strftime('%Y-%m-%d')
    
    for market, df in market_dfs.items():
        st.markdown("---")
        if df is not None and not df.empty:
            st.subheader(f"💵 {market} 거래 내역 ({date_str})"); st.dataframe(df)
        else: st.info(f"선택한 날짜의 {market} 거래 내역이 없습니다.")

with tab_report:
    st.header("📄 통합 투자 보고서")
    report_date = st.date_input("보고서 생성 날짜 선택", value=st.session_state.history_date, key="report_gen_date")
    st.markdown("---")
    st.info("보고서를 생성하기 전에 [📜 거래 내역] 탭에서 해당 날짜의 데이터를 먼저 불러와 주세요.")
    
    futures_client = get_futures_client(config) 
    spot_client = get_spot_client(config)
    report_config = load_config() 
    
    if futures_client and spot_client:
        report_content = generate_report(
            futures_client, spot_client, report_config, report_date, 
            st.session_state.usd_m_trades_df, 
            st.session_state.coin_m_trades_df,
            st.session_state.spot_trades_df 
        )
        st.markdown("### 📝 생성된 보고서")
        st.markdown(report_content)
        st.download_button(label="💾 보고서 다운로드 (.md)", data=report_content.encode('utf-8'),
                           file_name=f"investment_report_{report_date.strftime('%Y-%m-%d')}.md", mime="text/markdown")
    else:
        st.warning("보고서를 생성하려면 선물과 현물 API 키가 모두 필요합니다.")
