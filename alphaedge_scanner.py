#!/usr/bin/env python3
"""
AlphaEdge V3 Scanner — Phase 1
Replicates AlphaEdge V3 logic in Python
Scans all F&O stocks every 15 minutes
Sends alerts to Telegram

Author: AlphaEdge Analytics (Anish Multani)
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, time
import pytz
import time as time_module
import json
import logging

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = "8804627902:AAEvKrGkJjo3CFx4xWnrglBAyGl2tFnuDjQ"
TELEGRAM_CHAT_ID   = "922553974"

# Timeframes (in minutes)
HTF_MINUTES = 15
MTF_MINUTES = 3

# EMA Length (same as AlphaEdge V3)
EMA_LENGTH = 21

# Volume Shocker threshold
VOLUME_SHOCK_MULTIPLIER = 2.5

# Market breadth thresholds
BULLISH_THRESHOLD = 0.60   # 60% stocks up = BULLISH
BEARISH_THRESHOLD = 0.40   # 40% stocks up = BEARISH

# Confluence score threshold to alert
MIN_SCORE_TO_ALERT = 4

# Top N gainers/losers to consider
TOP_N = 5

# IST timezone
IST = pytz.timezone("Asia/Kolkata")

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/root/StockBot/alphaedge_scanner.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# SECTION 1 — TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_telegram(message):
    """Send message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            log.info("Telegram message sent successfully")
        else:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# ═══════════════════════════════════════════════════════════════
# SECTION 2 — F&O STOCK LIST (NSE)
# ═══════════════════════════════════════════════════════════════

def get_fo_stocks():
    """Fetch current F&O stock list from NSE"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        }
        session = requests.Session()
        # First hit NSE homepage to get cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        
        # Fetch F&O stock list
        url = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        r = session.get(url, headers=headers, timeout=15)
        data = r.json()
        
        stocks = []
        for item in data.get("data", []):
            symbol = item.get("symbol", "")
            if symbol and symbol not in ["NIFTY 50", "Nifty 50"]:
                stocks.append(symbol)
        
        log.info(f"F&O stocks fetched: {len(stocks)}")
        return stocks
    except Exception as e:
        log.error(f"F&O stock list fetch failed: {e}")
        # Fallback list — Nifty 50 stocks
        return [
            "RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS",
            "AXISBANK","KOTAKBANK","SBIN","BAJFINANCE","TATAMOTORS",
            "MARUTI","WIPRO","ADANIENT","ULTRACEMCO","TITAN",
            "SUNPHARMA","ONGC","NTPC","POWERGRID","COALINDIA",
            "BAJAJFINSV","HCLTECH","TECHM","NESTLEIND","BRITANNIA",
            "DIVISLAB","DRREDDY","CIPLA","EICHERMOT","HEROMOTOCO",
            "TATACONSUM","HINDUNILVR","ASIANPAINT","INDUSINDBK",
            "JSWSTEEL","TATASTEEL","HINDALCO","BPCL","IOC",
            "GRASIM","SHREECEM","LTIM","LT","M&M",
            "APOLLOHOSP","ADANIPORTS","BAJAJ-AUTO","SBILIFE","HDFCLIFE"
        ]

# ═══════════════════════════════════════════════════════════════
# SECTION 3 — MARKET BREADTH
# ═══════════════════════════════════════════════════════════════

def get_market_breadth():
    """
    Fetch market breadth from NSE
    Returns: advances, declines, unchanged, bias
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        
        url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
        r = session.get(url, headers=headers, timeout=15)
        data = r.json()
        
        advances  = data.get("advance", {}).get("advances", 0)
        declines  = data.get("advance", {}).get("declines", 0)
        unchanged = data.get("advance", {}).get("unchanged", 0)
        
        advances  = int(advances)  if advances  else 0
        declines  = int(declines)  if declines  else 0
        unchanged = int(unchanged) if unchanged else 0
        total     = advances + declines + unchanged

        if total == 0:
            return 0, 0, 0, "NEUTRAL"

        ratio = advances / total

        if ratio >= BULLISH_THRESHOLD:
            bias = "BULLISH"
        elif ratio <= BEARISH_THRESHOLD:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        log.info(f"Breadth — Advances: {advances}, Declines: {declines}, Bias: {bias}")
        return advances, declines, unchanged, bias

    except Exception as e:
        log.error(f"Market breadth fetch failed: {e}")
        return 0, 0, 0, "NEUTRAL"

# ═══════════════════════════════════════════════════════════════
# SECTION 4 — TOP GAINERS / LOSERS / VOLUME SHOCKERS
# ═══════════════════════════════════════════════════════════════

def get_gainers_losers_volume(fo_stocks):
    """
    Fetch top gainers, losers and volume shockers
    from F&O stock list
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        url = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        r = session.get(url, headers=headers, timeout=15)
        data = r.json()

        stocks_data = []
        for item in data.get("data", []):
            symbol = item.get("symbol", "")
            if symbol not in fo_stocks:
                continue
            try:
                pct_change  = float(item.get("pChange", 0))
                last_price  = float(item.get("lastPrice", 0))
                total_vol   = float(item.get("totalTradedVolume", 0))
                avg_vol     = float(item.get("averageTradedVolume", total_vol))  
                vol_ratio   = (total_vol / avg_vol) if avg_vol > 0 else 1.0

                stocks_data.append({
                    "symbol"    : symbol,
                    "price"     : last_price,
                    "pct_change": pct_change,
                    "volume"    : total_vol,
                    "avg_volume": avg_vol,
                    "vol_ratio" : vol_ratio
                })
            except:
                continue

        df = pd.DataFrame(stocks_data)
        if df.empty:
            return [], [], []

        # Top gainers
        gainers = df.nlargest(TOP_N, "pct_change").to_dict("records")

        # Top losers
        losers = df.nsmallest(TOP_N, "pct_change").to_dict("records")

        # Volume shockers — vol ratio > threshold
        shockers = df[df["vol_ratio"] >= VOLUME_SHOCK_MULTIPLIER]\
                     .nlargest(TOP_N, "vol_ratio").to_dict("records")

        log.info(f"Gainers: {len(gainers)}, Losers: {len(losers)}, Shockers: {len(shockers)}")
        return gainers, losers, shockers

    except Exception as e:
        log.error(f"Gainers/Losers fetch failed: {e}")
        return [], [], []

# ═══════════════════════════════════════════════════════════════
# SECTION 5 — SECTOR INDICES + STOCK→SECTOR MAPPING
# ═══════════════════════════════════════════════════════════════

# NSE Sectoral Indices — Yahoo Finance symbols
SECTOR_INDICES = {
    "NIFTY BANK"       : "^NSEBANK",
    "NIFTY IT"         : "^CNXIT",
    "NIFTY PHARMA"     : "^CNXPHARMA",
    "NIFTY AUTO"       : "^CNXAUTO",
    "NIFTY FMCG"       : "^CNXFMCG",
    "NIFTY METAL"      : "^CNXMETAL",
    "NIFTY REALTY"     : "^CNXREALTY",
    "NIFTY ENERGY"     : "^CNXENERGY",
    "NIFTY PSU BANK"   : "^CNXPSUBANK",
    "NIFTY FIN SERVICE": "^CNXFINANCE",
    "NIFTY INFRA"      : "^CNXINFRA",
    "NIFTY OIL & GAS"  : "^CNXOILGAS",
    "NIFTY HEALTHCARE" : "^CNXHEALTH",
    "NIFTY MEDIA"      : "^CNXMEDIA",
}

# Stock → Sector mapping (primary sector per stock)
STOCK_SECTOR_MAP = {
    # Banking & Finance
    "HDFCBANK"   : "NIFTY BANK",
    "ICICIBANK"  : "NIFTY BANK",
    "AXISBANK"   : "NIFTY BANK",
    "KOTAKBANK"  : "NIFTY BANK",
    "SBIN"       : "NIFTY PSU BANK",
    "BANKBARODA" : "NIFTY PSU BANK",
    "PNB"        : "NIFTY PSU BANK",
    "CANBK"      : "NIFTY PSU BANK",
    "BAJFINANCE" : "NIFTY FIN SERVICE",
    "BAJAJFINSV" : "NIFTY FIN SERVICE",
    "HDFCLIFE"   : "NIFTY FIN SERVICE",
    "SBILIFE"    : "NIFTY FIN SERVICE",
    "INDUSINDBK" : "NIFTY BANK",
    # IT
    "INFY"       : "NIFTY IT",
    "TCS"        : "NIFTY IT",
    "WIPRO"      : "NIFTY IT",
    "HCLTECH"    : "NIFTY IT",
    "TECHM"      : "NIFTY IT",
    "LTIM"       : "NIFTY IT",
    "MPHASIS"    : "NIFTY IT",
    "PERSISTENT" : "NIFTY IT",
    # Pharma & Healthcare
    "SUNPHARMA"  : "NIFTY PHARMA",
    "DRREDDY"    : "NIFTY PHARMA",
    "CIPLA"      : "NIFTY PHARMA",
    "DIVISLAB"   : "NIFTY PHARMA",
    "APOLLOHOSP" : "NIFTY HEALTHCARE",
    "MAXHEALTH"  : "NIFTY HEALTHCARE",
    # Auto
    "TATAMOTORS" : "NIFTY AUTO",
    "MARUTI"     : "NIFTY AUTO",
    "EICHERMOT"  : "NIFTY AUTO",
    "HEROMOTOCO" : "NIFTY AUTO",
    "BAJAJ-AUTO" : "NIFTY AUTO",
    "M&M"        : "NIFTY AUTO",
    # FMCG
    "HINDUNILVR" : "NIFTY FMCG",
    "NESTLEIND"  : "NIFTY FMCG",
    "BRITANNIA"  : "NIFTY FMCG",
    "TATACONSUM" : "NIFTY FMCG",
    "GODREJCP"   : "NIFTY FMCG",
    # Metals
    "TATASTEEL"  : "NIFTY METAL",
    "JSWSTEEL"   : "NIFTY METAL",
    "HINDALCO"   : "NIFTY METAL",
    "COALINDIA"  : "NIFTY METAL",
    "NMDC"       : "NIFTY METAL",
    "VEDL"       : "NIFTY METAL",
    # Energy & Oil
    "RELIANCE"   : "NIFTY OIL & GAS",
    "ONGC"       : "NIFTY OIL & GAS",
    "BPCL"       : "NIFTY OIL & GAS",
    "IOC"        : "NIFTY OIL & GAS",
    "GAIL"       : "NIFTY OIL & GAS",
    "NTPC"       : "NIFTY ENERGY",
    "POWERGRID"  : "NIFTY ENERGY",
    "ADANIGREEN" : "NIFTY ENERGY",
    # Infra & Real Estate
    "LT"         : "NIFTY INFRA",
    "ADANIPORTS" : "NIFTY INFRA",
    "ADANIENT"   : "NIFTY INFRA",
    "DLF"        : "NIFTY REALTY",
    "GODREJPROP" : "NIFTY REALTY",
    # Consumer / Others
    "ASIANPAINT" : "NIFTY FMCG",
    "TITAN"      : "NIFTY FMCG",
    "ULTRACEMCO" : "NIFTY INFRA",
    "SHREECEM"   : "NIFTY INFRA",
    "GRASIM"     : "NIFTY INFRA",
    # Media
    "ZEEL"       : "NIFTY MEDIA",
    "SUNTV"      : "NIFTY MEDIA",
}

def get_sector_bias():
    """
    Fetch all sectoral indices and determine BULLISH/BEARISH/NEUTRAL
    Returns: dict {sector_name: {bias, pct_change, ema_bias}}
    """
    import yfinance as yf
    sector_data = {}

    for sector_name, yf_symbol in SECTOR_INDICES.items():
        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period="5d", interval="15m")

            if df is None or df.empty:
                sector_data[sector_name] = {
                    "bias": "NEUTRAL", "pct_change": 0.0, "ema_bias": "NEUTRAL"
                }
                continue

            df = df.rename(columns={"Close": "close"})
            current_price = df["close"].iloc[-1]
            prev_close    = df["close"].iloc[0]
            pct_change    = ((current_price - prev_close) / prev_close) * 100

            # EMA 21 on 15min
            ema = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
            ema_bias = "BUY" if current_price > ema else "SELL"

            # Bias by % change
            if pct_change >= 0.5:
                bias = "BULLISH"
            elif pct_change <= -0.5:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            sector_data[sector_name] = {
                "bias"      : bias,
                "pct_change": round(pct_change, 2),
                "ema_bias"  : ema_bias
            }
            log.info(f"Sector {sector_name}: {bias} ({pct_change:.2f}%)")
            time_module.sleep(0.3)

        except Exception as e:
            log.error(f"Sector fetch failed for {sector_name}: {e}")
            sector_data[sector_name] = {
                "bias": "NEUTRAL", "pct_change": 0.0, "ema_bias": "NEUTRAL"
            }

    return sector_data

def get_stock_sector(symbol):
    """Get primary sector for a stock"""
    return STOCK_SECTOR_MAP.get(symbol, None)

# ═══════════════════════════════════════════════════════════════
# SECTION 6 — OHLCV DATA FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_ohlcv(symbol, interval_minutes, period_days=10):
    """
    Fetch OHLCV data for a symbol
    interval_minutes: 3 or 15
    """
    try:
        import yfinance as yf

        # Convert NSE symbol to Yahoo Finance format
        yf_symbol = symbol + ".NS"

        interval_map = {
            1: "1m", 2: "2m", 3: "3m", 5: "5m",
            15: "15m", 30: "30m", 60: "1h"
        }
        interval = interval_map.get(interval_minutes, "15m")

        # yfinance limits: 7 days for 1-5m, 60 days for 15-30m
        period = "5d" if interval_minutes <= 5 else "30d"

        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            return None

        df = df.rename(columns={
            "Open": "open", "High": "high",
            "Low": "low", "Close": "close",
            "Volume": "volume"
        })
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        return df

    except Exception as e:
        log.error(f"OHLCV fetch failed for {symbol}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# SECTION 6 — EMA CALCULATION (AlphaEdge V3 — EMA 21)
# ═══════════════════════════════════════════════════════════════

def calculate_ema(series, length=EMA_LENGTH):
    """Calculate EMA — same as Pine Script ta.ema()"""
    return series.ewm(span=length, adjust=False).mean()

def get_ema_bias(symbol):
    """
    Replicate AlphaEdge V3 EMA bias:
    HTF = 15min EMA 21
    MTF = 3min EMA 21
    Returns: ema_htf_bias, ema_mtf_bias, current_price
    """
    try:
        # Fetch HTF data (15 min)
        htf_df = fetch_ohlcv(symbol, HTF_MINUTES)
        # Fetch MTF data (3 min)
        mtf_df = fetch_ohlcv(symbol, MTF_MINUTES)

        if htf_df is None or mtf_df is None:
            return "NEUTRAL", "NEUTRAL", 0

        # Current price = latest close
        current_price = htf_df["close"].iloc[-1]

        # EMA 21 on HTF
        htf_ema = calculate_ema(htf_df["close"]).iloc[-1]
        # EMA 21 on MTF
        mtf_ema = calculate_ema(mtf_df["close"]).iloc[-1]

        # Bias: BUY if price above EMA, SELL if below
        # Same logic as AlphaEdge V3
        ema_htf_bias = "BUY"  if current_price > htf_ema else "SELL"
        ema_mtf_bias = "BUY"  if current_price > mtf_ema else "SELL"

        return ema_htf_bias, ema_mtf_bias, current_price

    except Exception as e:
        log.error(f"EMA bias failed for {symbol}: {e}")
        return "NEUTRAL", "NEUTRAL", 0

# ═══════════════════════════════════════════════════════════════
# SECTION 7 — SWING HIGH / LOW DETECTION
# (Basis for Support/Resistance zones — Phase 1)
# Full OB Zone logic added in Session 2
# ═══════════════════════════════════════════════════════════════

def get_key_levels(symbol):
    """
    Detect recent Swing Highs (Resistance)
    and Swing Lows (Support) on HTF
    Phase 1: Simple swing detection
    Phase 2: Full OB Zone + PRZ logic
    """
    try:
        htf_df = fetch_ohlcv(symbol, HTF_MINUTES)
        if htf_df is None or len(htf_df) < 10:
            return None, None

        highs = htf_df["high"]
        lows  = htf_df["low"]

        # Simple swing high: bar whose high > 2 bars each side
        resistance = None
        support    = None

        for i in range(2, len(highs) - 2):
            if highs.iloc[i] > highs.iloc[i-1] and \
               highs.iloc[i] > highs.iloc[i-2] and \
               highs.iloc[i] > highs.iloc[i+1] and \
               highs.iloc[i] > highs.iloc[i+2]:
                resistance = highs.iloc[i]

        for i in range(2, len(lows) - 2):
            if lows.iloc[i] < lows.iloc[i-1] and \
               lows.iloc[i] < lows.iloc[i-2] and \
               lows.iloc[i] < lows.iloc[i+1] and \
               lows.iloc[i] < lows.iloc[i+2]:
                support = lows.iloc[i]

        return support, resistance

    except Exception as e:
        log.error(f"Key levels failed for {symbol}: {e}")
        return None, None

# ═══════════════════════════════════════════════════════════════
# SECTION 8 — STRENGTH CALCULATION
# Replicates AlphaEdge V3 Strength logic exactly
# ═══════════════════════════════════════════════════════════════

def calculate_strength(ema_htf_bias, ema_mtf_bias):
    """
    Replicate AlphaEdge V3 Strength:
    STRONG BUY  = HTF BUY + MTF BUY + EMA HTF BUY + EMA MTF BUY
    BUY         = HTF BUY + MTF BUY + (EMA HTF OR EMA MTF BUY)
    STRONG SELL = HTF SELL + MTF SELL + EMA HTF SELL + EMA MTF SELL
    SELL        = HTF SELL + MTF SELL + (EMA HTF OR EMA MTF SELL)
    NEUTRAL     = anything else

    Note: In Phase 1 HTF bias = EMA HTF bias (zone bias added in Session 2)
    """
    htf_buy  = ema_htf_bias == "BUY"
    mtf_buy  = ema_mtf_bias == "BUY"
    htf_sell = ema_htf_bias == "SELL"
    mtf_sell = ema_mtf_bias == "SELL"

    if htf_buy and mtf_buy:
        return "STRONG BUY"
    elif htf_sell and mtf_sell:
        return "STRONG SELL"
    elif htf_buy or mtf_buy:
        return "BUY"
    elif htf_sell or mtf_sell:
        return "SELL"
    else:
        return "NEUTRAL"

# ═══════════════════════════════════════════════════════════════
# SECTION 9 — CONFLUENCE SCORING
# ═══════════════════════════════════════════════════════════════

def calculate_score(symbol, market_bias, gainers, losers,
                    shockers, ema_htf_bias, ema_mtf_bias,
                    current_price, support, resistance, sector_data):
    """
    Calculate confluence score 0-8:
    +1 Market bias matches direction
    +1 Sector bias matches direction
    +1 Sector EMA confirms direction
    +1 Stock in Top 5 Gainers/Losers
    +1 Volume Shocker
    +1 EMA HTF confirms
    +1 EMA MTF confirms
    +1 Price near Key Zone
    """
    score     = 0
    direction = "NEUTRAL"
    reasons   = []

    # Determine direction from EMA
    strength = calculate_strength(ema_htf_bias, ema_mtf_bias)

    if "BUY" in strength:
        direction = "BUY"
    elif "SELL" in strength:
        direction = "SELL"
    else:
        return 0, "NEUTRAL", strength, [], "UNKNOWN", "NEUTRAL"

    # +1 Market bias matches
    if (direction == "BUY"  and market_bias == "BULLISH") or \
       (direction == "SELL" and market_bias == "BEARISH"):
        score += 1
        reasons.append("Market Bias ✅")

    # +1 Sector bias matches + +1 Sector EMA confirms
    sector_name = get_stock_sector(symbol)
    sector_bias_str  = "UNKNOWN"
    sector_pct_str   = ""

    if sector_name and sector_name in sector_data:
        s = sector_data[sector_name]
        sector_bias_str = s["bias"]
        sector_pct_str  = f"{s['pct_change']:+.2f}%"

        # +1 Sector % change bias matches direction
        if (direction == "BUY"  and s["bias"] == "BULLISH") or \
           (direction == "SELL" and s["bias"] == "BEARISH"):
            score += 1
            reasons.append(f"Sector {sector_name} {s['bias']} ({sector_pct_str}) ✅")

        # +1 Sector EMA confirms direction
        if (direction == "BUY"  and s["ema_bias"] == "BUY") or \
           (direction == "SELL" and s["ema_bias"] == "SELL"):
            score += 1
            reasons.append(f"Sector EMA {s['ema_bias']} ✅")
    else:
        sector_bias_str = "N/A"

    # +1 In top gainers/losers
    gainer_symbols  = [g["symbol"] for g in gainers]
    loser_symbols   = [l["symbol"] for l in losers]
    shocker_symbols = [s["symbol"] for s in shockers]

    if direction == "BUY"  and symbol in gainer_symbols:
        score += 1
        rank = gainer_symbols.index(symbol) + 1
        reasons.append(f"Top Gainer #{rank} ✅")
    elif direction == "SELL" and symbol in loser_symbols:
        score += 1
        rank = loser_symbols.index(symbol) + 1
        reasons.append(f"Top Loser #{rank} ✅")

    # +1 Volume shocker
    if symbol in shocker_symbols:
        vol_ratio = next(s["vol_ratio"] for s in shockers
                         if s["symbol"] == symbol)
        score += 1
        reasons.append(f"Vol Shocker {vol_ratio:.1f}x")

    # +1 EMA HTF confirms
    if (direction == "BUY"  and ema_htf_bias == "BUY") or \
       (direction == "SELL" and ema_htf_bias == "SELL"):
        score += 1
        reasons.append(f"EMA HTF {ema_htf_bias} ✅")

    # +1 EMA MTF confirms
    if (direction == "BUY"  and ema_mtf_bias == "BUY") or \
       (direction == "SELL" and ema_mtf_bias == "SELL"):
        score += 1
        reasons.append(f"EMA MTF {ema_mtf_bias} ✅")

    # +1 Price near key zone (within 0.5%)
    # Phase 2: replaced with full OB Zone logic
    if current_price and support and resistance:
        zone_pct = 0.005
        if direction == "BUY" and support:
            if abs(current_price - support) / current_price <= zone_pct:
                score += 1
                reasons.append("Near Support Zone ✅")
        elif direction == "SELL" and resistance:
            if abs(current_price - resistance) / current_price <= zone_pct:
                score += 1
                reasons.append("Near Resistance Zone ✅")

    return score, direction, strength, reasons, sector_name or "N/A", sector_bias_str

# ═══════════════════════════════════════════════════════════════
# SECTION 10 — MAIN SCANNER
# ═══════════════════════════════════════════════════════════════

def run_scan():
    """
    Main scan function — runs every 15 minutes
    """
    now_ist = datetime.now(IST)
    log.info(f"=== AlphaEdge Scan started: {now_ist.strftime('%H:%M')} ===")

    # ── Fetch market data ──
    advances, declines, unchanged, market_bias = get_market_breadth()
    fo_stocks = get_fo_stocks()
    gainers, losers, shockers = get_gainers_losers_volume(fo_stocks)

    # ── Fetch sector data ──
    log.info("Fetching sector indices...")
    sector_data = get_sector_bias()

    # ── Fallback: if breadth API failed use sector data for market bias ──
    if market_bias == "NEUTRAL" and advances == 0 and declines == 0:
        bullish_sectors = sum(1 for s in sector_data.values() if s["bias"] == "BULLISH")
        bearish_sectors = sum(1 for s in sector_data.values() if s["bias"] == "BEARISH")
        total_sectors   = len(sector_data)
        if total_sectors > 0:
            if bullish_sectors / total_sectors >= BULLISH_THRESHOLD:
                market_bias = "BULLISH (Sector)"
            elif bearish_sectors / total_sectors >= BEARISH_THRESHOLD:
                market_bias = "BEARISH (Sector)"
        log.info(f"Breadth API failed — using sector fallback: {market_bias}")

    # ── Candidate stocks to check ──
    candidates = set()
    for g in gainers:  candidates.add(g["symbol"])
    for l in losers:   candidates.add(l["symbol"])
    for s in shockers: candidates.add(s["symbol"])

    log.info(f"Candidates to scan: {len(candidates)}")

    # ── Scan each candidate ──
    results = []
    for symbol in candidates:
        log.info(f"Scanning {symbol}...")
        ema_htf, ema_mtf, price = get_ema_bias(symbol)
        support, resistance      = get_key_levels(symbol)

        score, direction, strength, reasons, sector_name, sector_bias = calculate_score(
            symbol, market_bias, gainers, losers,
            shockers, ema_htf, ema_mtf, price,
            support, resistance, sector_data
        )

        if score >= MIN_SCORE_TO_ALERT:
            results.append({
                "symbol"      : symbol,
                "price"       : price,
                "direction"   : direction,
                "strength"    : strength,
                "ema_htf"     : ema_htf,
                "ema_mtf"     : ema_mtf,
                "score"       : score,
                "reasons"     : reasons,
                "sector_name" : sector_name,
                "sector_bias" : sector_bias
            })
        time_module.sleep(0.5)

    # ── Sort by score ──
    results.sort(key=lambda x: x["score"], reverse=True)

    # ── Build Telegram message ──
    bias_emoji = "🟢" if market_bias == "BULLISH" else \
                 "🔴" if market_bias == "BEARISH" else "🟡"

    msg  = f"⚡ <b>AlphaEdge V3 Scan</b> — {now_ist.strftime('%I:%M %p')}\n"
    msg += f"📅 {now_ist.strftime('%d %B %Y')}\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📊 <b>MARKET BREADTH:</b> {bias_emoji} {market_bias}\n"
    msg += f"⬆️ Advances: {advances}  "
    msg += f"⬇️ Declines: {declines}  "
    msg += f"➡️ Unchanged: {unchanged}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    # ── Sector Snapshot ──
    msg += f"🏭 <b>SECTOR SNAPSHOT:</b>\n"
    for sname, sinfo in sector_data.items():
        s_emoji = "🟢" if sinfo["bias"] == "BULLISH" else \
                  "🔴" if sinfo["bias"] == "BEARISH" else "🟡"
        pct = sinfo["pct_change"]
        msg += f"{s_emoji} {sname:<20} {pct:+.2f}%\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    # ── Results ──
    if not results:
        msg += "🔍 No high confluence setups found.\n"
        msg += "Waiting for next scan...\n"
    else:
        high   = [r for r in results if r["score"] >= 6]
        medium = [r for r in results if 4 <= r["score"] <= 5]

        if high:
            msg += "🔥 <b>HIGH PRIORITY SETUPS:</b>\n\n"
            for i, r in enumerate(high, 1):
                d_emoji = "🟢" if r["direction"] == "BUY" else "🔴"
                s_emoji = "🟢" if r["sector_bias"] == "BULLISH" else \
                          "🔴" if r["sector_bias"] == "BEARISH" else "🟡"
                msg += f"{i}. {d_emoji} <b>{r['symbol']}</b>  Rs.{r['price']:.2f}\n"
                msg += f"   Strength: <b>{r['strength']}</b>\n"
                msg += f"   Sector: {s_emoji} {r['sector_name']} ({r['sector_bias']})\n"
                msg += f"   EMA HTF: {r['ema_htf']} | MTF: {r['ema_mtf']}\n"
                msg += f"   Score: {r['score']}/8\n"
                for reason in r["reasons"]:
                    msg += f"   - {reason}\n"
                msg += "\n"

        if medium:
            msg += "✅ <b>WATCH LIST:</b>\n"
            for r in medium:
                d_emoji = "🟢" if r["direction"] == "BUY" else "🔴"
                s_emoji = "🟢" if r["sector_bias"] == "BULLISH" else \
                          "🔴" if r["sector_bias"] == "BEARISH" else "🟡"
                msg += f"- {d_emoji} {r['symbol']} Rs.{r['price']:.2f} "
                msg += f"| {r['strength']} "
                msg += f"| {s_emoji} {r['sector_name']} "
                msg += f"| Score {r['score']}/8\n"

    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"Verify zones on AlphaEdge V3 before entering any trade!"

    send_telegram(msg)
    log.info("Scan complete.")

# ═══════════════════════════════════════════════════════════════
# SECTION 11 — MARKET HOURS CHECK + SCHEDULER
# ═══════════════════════════════════════════════════════════════

def is_market_open():
    """Check if NSE market is open"""
    now = datetime.now(IST)
    # Skip weekends
    if now.weekday() >= 5:
        return False
    # Market hours 9:15 AM to 3:30 PM IST
    market_open  = time(9, 15)
    market_close = time(15, 30)
    return market_open <= now.time() <= market_close

def main():
    """Main loop — runs every 15 minutes during market hours"""
    log.info("AlphaEdge V3 Scanner started!")
    send_telegram(
        "🚀 <b>AlphaEdge V3 Scanner Started!</b>\n"
        "Scanning all F&O stocks every 15 minutes\n"
        "Market hours: 9:15 AM — 3:30 PM IST"
    )

    while True:
        if is_market_open():
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
                send_telegram(f"⚠️ Scanner error: {e}")
        else:
            log.info("Market closed — waiting...")

        # Wait 15 minutes
        time_module.sleep(15 * 60)

if __name__ == "__main__":
    main()
