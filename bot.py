import os
import re
import difflib
import yfinance as yf
import urllib.request
import json
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from supabase import create_client
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler


logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_missing_env = [
    key for key, value in {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "GROQ_API_KEY": GROQ_API_KEY,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
    }.items() if not value
]
if _missing_env:
    raise RuntimeError("Missing Railway environment variables: " + ", ".join(_missing_env))

groq_client = Groq(api_key=GROQ_API_KEY, timeout=45.0, max_retries=1)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def http_read(url, timeout=15, headers=None):
    request_headers = {"User-Agent": "Mozilla/5.0 (compatible; StockLensBot/1.0)"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()

def yahoo_symbol(symbol):
    symbol = str(symbol or "").strip().upper().replace(" ", "")
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return symbol + ".NS"

def groq_generate(prompt, language="hinglish", max_tokens=1000):
    primary = "openai/gpt-oss-20b" if language in ("hindi", "marathi") else "openai/gpt-oss-120b"
    fallback = "openai/gpt-oss-120b" if primary.endswith("20b") else "openai/gpt-oss-20b"
    errors = []
    for model in (primary, fallback):
        for attempt in range(2):
            try:
                response = groq_client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.35,
                )
                if response and response.choices:
                    choice = response.choices[0]
                    content = getattr(choice.message, "content", None)
                    if isinstance(content, str) and content.strip():
                        logging.info("AI response OK model=%s chars=%s finish_reason=%s",
                                     model, len(content), getattr(choice, "finish_reason", None))
                        return content.strip()
                    errors.append(f"{model}: empty content")
                    logging.warning("AI returned empty content model=%s attempt=%s", model, attempt + 1)
                else:
                    errors.append(f"{model}: no choices")
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                logging.exception("AI request failed model=%s attempt=%s", model, attempt + 1)
    raise RuntimeError("AI generation failed after retries: " + " | ".join(errors[-4:]))

def create_stock_keyboard(matches):
    keyboard = []
    for symbol, name in matches:
        keyboard.append([InlineKeyboardButton(
            f"{name} ({symbol})",
            callback_data=f"analyze_{symbol}"
        )])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_user_language(chat_id):
    try:
        result = supabase.table("user_preferences").select("language").eq("chat_id", chat_id).execute()
        if result.data:
            return result.data[0]['language']
        return 'hinglish'
    except:
        return 'hinglish'

def set_user_language(chat_id, language):
    try:
        result = supabase.table("user_preferences").upsert({
            "chat_id": chat_id,
            "language": language,
            "updated_at": "now()"
        }).execute()
        print(f"Language saved: {language} for {chat_id}")
        return True
    except Exception as e:
        print(f"Language set error: {e}")
        return False

def get_language_prompt(language):
    prompts = {
        'english': "CRITICAL INSTRUCTION: You MUST write the ENTIRE response in simple English only. Do not use any Hindi, Marathi or any other language. Every single word must be English.",
        'hinglish': "CRITICAL INSTRUCTION: Write in Hinglish - naturally mix Hindi and English words together. Hindi words in Roman script. Example: 'Yeh stock accha hai because company ki growth strong hai aur profit margins bhi achhe hain.'",
        'hindi': "CRITICAL INSTRUCTION: You MUST write the ENTIRE response in pure Hindi using Devanagari script. Every single word must be in Hindi Devanagari script. Do not write even a single word in English or Roman script except company names, numbers, and terms like PE, NAV, ROE. Example: 'यह कंपनी बहुत अच्छी तरह से काम करती है। इसकी आमदनी में लगातार बढ़ोतरी हो रही है। निवेशकों के लिए यह एक अच्छा विकल्प हो सकता है।'",
        'marathi': "CRITICAL INSTRUCTION: You MUST write the ENTIRE response in pure Marathi using Devanagari script. Every single word must be in Marathi Devanagari script. Do not write even a single word in English or Roman script except company names, numbers, and terms like PE, NAV, ROE. Example: 'ही कंपनी खूप चांगली आहे. तिचे उत्पन्न सतत वाढत आहे. गुंतवणूकदारांसाठी हा एक चांगला पर्याय असू शकतो.'"
    }
    return prompts.get(language, prompts['hinglish'])

STOCK_ALIAS_LOOKUP = {'tata consultancy': 'TCS', 'tcs': 'TCS', 'tata consultancy services': 'TCS', 'reliance': 'RELIANCE', 'reliance industries': 'RELIANCE', 'hdfc bank': 'HDFCBANK', 'hdfc': 'HDFCBANK', 'infosys': 'INFY', 'infy': 'INFY', 'wipro': 'WIPRO', 'tata motors': 'TATAMOTORS', 'tata motor': 'TATAMOTORS', 'bajaj finance': 'BAJFINANCE', 'bajaj fin': 'BAJFINANCE', 'adani ports': 'ADANIPORTS', 'adani port': 'ADANIPORTS', 'adani enterprises': 'ADANIENT', 'adani': 'ADANIENT', 'sun pharma': 'SUNPHARMA', 'sunpharma': 'SUNPHARMA', 'sun pharmaceutical': 'SUNPHARMA', 'icici bank': 'ICICIBANK', 'icici': 'ICICIBANK', 'axis bank': 'AXISBANK', 'axis': 'AXISBANK', 'kotak bank': 'KOTAKBANK', 'kotak': 'KOTAKBANK', 'kotak mahindra': 'KOTAKBANK', 'larsen': 'LT', 'larsen toubro': 'LT', 'l&t': 'LT', 'lt': 'LT', 'hindustan unilever': 'HINDUNILVR', 'hul': 'HINDUNILVR', 'hindustan lever': 'HINDUNILVR', 'asian paints': 'ASIANPAINT', 'asian paint': 'ASIANPAINT', 'maruti': 'MARUTI', 'maruti suzuki': 'MARUTI', 'nestle': 'NESTLEIND', 'nestle india': 'NESTLEIND', 'titan': 'TITAN', 'titan company': 'TITAN', 'ultratech': 'ULTRACEMCO', 'ultratech cement': 'ULTRACEMCO', 'ongc': 'ONGC', 'oil natural gas': 'ONGC', 'ntpc': 'NTPC', 'power grid': 'POWERGRID', 'powergrid': 'POWERGRID', 'sbi': 'SBIN', 'state bank': 'SBIN', 'state bank india': 'SBIN', 'bajaj auto': 'BAJAJ-AUTO', 'hero motocorp': 'HEROMOTOCO', 'hero': 'HEROMOTOCO', 'hero honda': 'HEROMOTOCO', 'dr reddy': 'DRREDDY', 'dr reddys': 'DRREDDY', 'doctor reddy': 'DRREDDY', 'cipla': 'CIPLA', 'hindalco': 'HINDALCO', 'tata steel': 'TATASTEEL', 'tata steel ltd': 'TATASTEEL', 'jsw steel': 'JSWSTEEL', 'jsw': 'JSWSTEEL', 'coal india': 'COALINDIA', 'bharti airtel': 'BHARTIARTL', 'airtel': 'BHARTIARTL', 'bharti': 'BHARTIARTL', 'hcl': 'HCLTECH', 'hcl tech': 'HCLTECH', 'hcl technologies': 'HCLTECH', 'tech mahindra': 'TECHM', 'tech m': 'TECHM', 'indusind bank': 'INDUSINDBK', 'indusind': 'INDUSINDBK', 'divis labs': 'DIVISLAB', 'divis': 'DIVISLAB', 'divi': 'DIVISLAB', 'grasim': 'GRASIM', 'tata consumer': 'TATACONSUM', 'tata consumer products': 'TATACONSUM', 'britannia': 'BRITANNIA', 'eicher motors': 'EICHERMOT', 'eicher': 'EICHERMOT', 'royal enfield': 'EICHERMOT', 'itc': 'ITC', 'itc ltd': 'ITC', 'bajaj finserv': 'BAJAJFINSV', 'hdfc life': 'HDFCLIFE', 'sbi life': 'SBILIFE', 'paytm': 'PAYTM', 'one97': 'PAYTM', 'nykaa': 'NYKAA', 'fss': 'NYKAA', 'dmart': 'DMART', 'avenue supermarts': 'DMART', 'pidilite': 'PIDILITIND', 'fevicol': 'PIDILITIND', 'berger paints': 'BERGEPAINT', 'berger': 'BERGEPAINT', 'havells': 'HAVELLS', 'voltas': 'VOLTAS', 'godrej consumer': 'GODREJCP', 'godrej': 'GODREJCP', 'dabur': 'DABUR', 'marico': 'MARICO', 'colgate': 'COLPAL', 'colgate palmolive': 'COLPAL', 'siemens': 'SIEMENS', 'abbott india': 'ABBOTINDIA', 'abbott': 'ABBOTINDIA', 'page industries': 'PAGEIND', 'jockey': 'PAGEIND', 'mrf': 'MRF', 'mrf tyres': 'MRF', 'balkrishna': 'BALKRISIND', 'bkt': 'BALKRISIND', 'info edge': 'NAUKRI', 'naukri': 'NAUKRI', 'jeevansathi': 'NAUKRI', 'indiamart': 'INDIAMART', 'zomato': 'ZOMATO', 'swiggy': 'SWIGGY', 'irctc': 'IRCTC', 'indian railway': 'IRCTC', 'interglobe': 'INDIGO', 'indigo': 'INDIGO', 'indigo airlines': 'INDIGO', 'spicejet': 'SPICEJET', 'sula wines': 'SULA', 'metro brands': 'METROBRAND', 'metro shoes': 'METROBRAND', 'campus activewear': 'CAMPUS', 'trent': 'TRENT', 'westside': 'TRENT', 'zudio': 'TRENT', 'vedanta': 'VEDL', 'vedl': 'VEDL', 'hindzinc': 'HINDZINC', 'hindustan zinc': 'HINDZINC', 'nmdc': 'NMDC', 'sail': 'SAIL', 'steel authority': 'SAIL', 'bhel': 'BHEL', 'bharat heavy': 'BHEL', 'bpcl': 'BPCL', 'bharat petroleum': 'BPCL', 'ioc': 'IOC', 'indian oil': 'IOC', 'hpcl': 'HPCL', 'hindustan petroleum': 'HPCL', 'gail': 'GAIL', 'gail india': 'GAIL', 'torrent pharma': 'TORNTPHARM', 'torrent': 'TORNTPHARM', 'lupin': 'LUPIN', 'aurobindo': 'AUROPHARMA', 'aurobindo pharma': 'AUROPHARMA', 'mankind pharma': 'MANKIND', 'max healthcare': 'MAXHEALTH', 'apollo hospitals': 'APOLLOHOSP', 'apollo': 'APOLLOHOSP', 'fortis': 'FORTIS', 'fortis healthcare': 'FORTIS', 'muthoot finance': 'MUTHOOTFIN', 'muthoot': 'MUTHOOTFIN', 'shriram finance': 'SHRIRAMFIN', 'shriram': 'SHRIRAMFIN', 'cholamandalam': 'CHOLAFIN', 'chola': 'CHOLAFIN', 'pnb': 'PNB', 'punjab national bank': 'PNB', 'bank of baroda': 'BANKBARODA', 'bob': 'BANKBARODA', 'canara bank': 'CANBK', 'canara': 'CANBK', 'union bank': 'UNIONBANK', 'indian bank': 'INDIANB', 'federal bank': 'FEDERALBNK', 'bandhan bank': 'BANDHANBNK', 'bandhan': 'BANDHANBNK', 'au small finance': 'AUBANK', 'au bank': 'AUBANK', 'ujjivan': 'UJJIVANSFB', 'polycab': 'POLYCAB', 'dixon technologies': 'DIXON', 'dixon': 'DIXON', 'amber enterprises': 'AMBER', 'finolex': 'FINPIPE', 'finolex cables': 'FINPIPE', 'whirlpool': 'WHIRLPOOL', 'crompton': 'CROMPTON', 'crompton greaves': 'CROMPTON', 'blue star': 'BLUESTAR', 'symphony': 'SYMPHONY', 'prestige estates': 'PRESTIGE', 'prestige': 'PRESTIGE', 'godrej properties': 'GODREJPROP', 'dlf': 'DLF', 'oberoi realty': 'OBEROIRLTY', 'oberoi': 'OBEROIRLTY', 'macrotech': 'LODHA', 'lodha': 'LODHA'}
STOCK_SYMBOLS = set(STOCK_ALIAS_LOOKUP.values())

def clean_company_query(user_input):
    value = str(user_input or "").strip()
    value = re.sub(r"^(?:hey\\s+)?(?:please\\s+)?(?:analyze|analyse|search|compare|tell me about|what about|show me|check)\\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[?!.,]+$", "", value).strip()
    return re.sub(r"\\s+", " ", value)

def resolve_symbol(user_input):
    return clean_company_query(user_input).upper().replace(" ", "")

def search_stocks_by_name_exact(query):
    query_lower = clean_company_query(query).lower()
    if query_lower in STOCK_ALIAS_LOOKUP:
        symbol = STOCK_ALIAS_LOOKUP[query_lower]
        return [(symbol, get_full_name(symbol))]
    ticker = query_lower.upper()
    if ticker.endswith((".NS", ".BO")):
        ticker = ticker.rsplit(".", 1)[0]
    if ticker in STOCK_SYMBOLS:
        return [(ticker, get_full_name(ticker))]
    return []

def get_stock_data(user_input):
    try:
        clean_input = clean_company_query(user_input)
        resolved = resolve_symbol(clean_input)
        if not resolved.endswith((".NS", ".BO")):
            exact_matches = search_stocks_by_name_exact(clean_input)
            if exact_matches:
                resolved = exact_matches[0][0]
        stock = yf.Ticker(yahoo_symbol(resolved))
        info = stock.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            hist = stock.history(period="1d")
            if hist.empty:
                print(f"No price data for {resolved}")
                return None
            price = round(hist['Close'].iloc[-1], 2)
        data = {
            "symbol": resolved,
            "name": info.get("longName", resolved),
            "price": price,
            "pe_ratio": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "market_cap": info.get("marketCap", "N/A"),
            "revenue_growth": f"{round(info.get('revenueGrowth', 0) * 100, 1)}%" if info.get('revenueGrowth') else "N/A",
            "profit_margins": f"{round(info.get('profitMargins', 0) * 100, 1)}%" if info.get('profitMargins') else "N/A",
            "debt_to_equity": round(info.get('debtToEquity', 0), 1) if info.get('debtToEquity') else "N/A",
            "roe": f"{round(info.get('returnOnEquity', 0) * 100, 1)}%" if info.get('returnOnEquity') else "N/A",
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "summary": info.get("longBusinessSummary", "N/A")
        }
        print(f"Data fetched: {data['name']} at {data['price']}")
        return data
    except Exception as e:
        print(f"Error fetching data for {user_input}: {e}")
        return None

def get_morning_brief_data(symbol):
    try:
        stock = yf.Ticker(yahoo_symbol(symbol))
        info = stock.info
        hist = stock.history(period="2d")
        if hist.empty or len(hist) < 2:
            return None
        prev_close = round(hist['Close'].iloc[-2], 2)
        current_price = round(hist['Close'].iloc[-1], 2)
        change = round(current_price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2)
        change_str = f"+{change} (+{change_pct}%)" if change >= 0 else f"{change} ({change_pct}%)"
        return {
            "symbol": symbol,
            "name": info.get("longName", symbol),
            "price": current_price,
            "change": change_str,
            "positive": change >= 0,
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "pe_ratio": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
        }
    except Exception as e:
        print(f"Morning brief error for {symbol}: {e}")
        return None

SECTOR_PEERS = {
    "Information Technology Services": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    "Banks": ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS"],
    "Software": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    "Oil & Gas": ["RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS"],
    "Automobiles": ["TATAMOTORS.NS", "MARUTI.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "EICHERMOT.NS"],
    "Pharmaceuticals": ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS"],
    "Cement": ["ULTRACEMCO.NS", "GRASIM.NS", "AMBUJACEM.NS", "ACC.NS"],
    "FMCG": ["HINDUNILVR.NS", "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS"],
    "Telecom": ["BHARTIARTL.NS", "IDEA.NS"],
    "Steel": ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS"],
    "Power": ["NTPC.NS", "POWERGRID.NS", "ADANIGREEN.NS"],
    "Insurance": ["HDFCLIFE.NS", "SBILIFE.NS", "ICICIGI.NS"],
    "Consumer Cyclical": ["TATAMOTORS.NS", "MARUTI.NS", "TITAN.NS", "ASIANPAINT.NS"],
}

def get_sector_context(industry, current_pe, current_symbol):
    try:
        peers = SECTOR_PEERS.get(industry, [])
        if not peers:
            return None
        pe_values = []
        for peer in peers:
            if peer.replace(".NS", "") == current_symbol:
                continue
            try:
                info = yf.Ticker(peer).info
                pe = info.get("trailingPE", 0)
                if pe and pe > 0:
                    pe_values.append(pe)
            except:
                continue
        if not pe_values:
            return None
        avg_pe = round(sum(pe_values) / len(pe_values), 2)
        if current_pe == "N/A" or not current_pe:
            return f"Sector average PE is {avg_pe}."
        if current_pe < avg_pe:
            comparison = "cheaper than sector average"
        elif current_pe > avg_pe:
            comparison = "more expensive than sector average"
        else:
            comparison = "in line with sector average"
        return f"Sector average PE is {avg_pe}. This stock is {comparison} (stock PE: {current_pe}, sector avg: {avg_pe})."
    except Exception as e:
        print(f"Sector context error: {e}")
        return None

def generate_chart(symbol, company_name):
    try:
        stock = yf.Ticker(yahoo_symbol(symbol))
        hist = stock.history(period="6mo")
        if hist.empty:
            return None
        fig, ax = plt.subplots(figsize=(10, 5))
        start_price = hist['Close'].iloc[0]
        end_price = hist['Close'].iloc[-1]
        color = '#2ecc71' if end_price >= start_price else '#e74c3c'
        ax.plot(hist.index, hist['Close'], color=color, linewidth=2)
        ax.fill_between(hist.index, hist['Close'], alpha=0.1, color=color)
        ax.set_title(f"{company_name} - 6 Month Price Chart", fontsize=14, fontweight='bold', pad=15)
        ax.set_xlabel("")
        ax.set_ylabel("Price (INR)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.xticks(rotation=45)
        ax.annotate(f'Current: {end_price:.1f}',
                   xy=(hist.index[-1], end_price),
                   xytext=(-80, 15),
                   textcoords='offset points',
                   fontsize=10,
                   fontweight='bold',
                   color=color)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        return buf
    except Exception as e:
        print(f"Chart error: {e}")
        return None

def format_with_bold_headings(text):
    headings = {
        "STOCK SNAPSHOT", "IN SIMPLE WORDS", "WHAT LOOKS GOOD",
        "RISKS TO WATCH", "IS IT EXPENSIVE OR CHEAP?", "WHAT TO CHECK NEXT",
        "BOTTOM LINE", "TL;DR", "WHAT THIS COMPANY DOES", "WHAT IS ACTUALLY HAPPENING",
        "WHAT SMART INVESTORS ARE NOTICING", "BULL VS BEAR", "WHERE IT CAN GO WRONG",
        "FINAL TAKE", "VERDICT", "CONFIDENCE",
    }
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        plain = stripped.lstrip("📊🧠✅⚠️💰🔎🧾 ").strip()
        head = plain.split(":", 1)[0].strip().upper()
        if head in headings or head.startswith("STOCK ANALYSIS"):
            output.append(f"*{stripped}*")
        else:
            output.append(stripped)
    return "\n".join(output)

def format_comparison_headings(text):
    headings = {
        "WHAT THEY DO", "NUMBERS AT A GLANCE", "STRENGTHS", "RISKS",
        "SIMPLE TAKEAWAY", "STOCK COMPARISON",
    }
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        head = stripped.split(":", 1)[0].strip().upper()
        if head in headings or head.startswith("STOCK COMPARISON"):
            output.append(f"*{stripped}*")
        else:
            output.append(stripped)
    return "\n".join(output)

def format_portfolio_headings(text):
    headings = [
        "PORTFOLIO ANALYSIS",
        "WHAT YOU OWN",
        "SECTOR CONCENTRATION",
        "STRONGEST STOCK",
        "WEAKEST STOCK",
        "OVERALL PORTFOLIO HEALTH",
        "STRONG",
        "MODERATE",
        "NEEDS ATTENTION",
    ]
    lines = text.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        for heading in headings:
            if stripped.upper() == heading or stripped.upper().startswith(heading + ":"):
                line = f"*{stripped}*"
                break
        new_lines.append(line)
    return '\n'.join(new_lines)

def analyze_stock(data, language="hinglish"):
    try:
        sector_info = get_sector_context(data.get("industry", "N/A"), data.get("pe_ratio", "N/A"), data.get("symbol", ""))
        prompt = f"""
You are StockLens, a friendly Indian stock-market explainer for beginners.
Write a short, very readable report. Use short sentences, bullet points and blank lines. Avoid jargon or explain it simply. Never invent missing facts or promise returns.
Company: {data.get('name')} ({data.get('symbol')})
Current price: INR {data.get('price')}
P/E ratio: {data.get('pe_ratio')}
52-week high/low: {data.get('52w_high')} / {data.get('52w_low')}
Market cap: {data.get('market_cap')}
Revenue growth: {data.get('revenue_growth')}
Profit margin: {data.get('profit_margins')}
Debt/equity: {data.get('debt_to_equity')}
ROE: {data.get('roe')}
Sector/industry: {data.get('sector')} / {data.get('industry')}
Sector valuation context: {sector_info or 'Not available'}
Business description: {str(data.get('summary', 'N/A'))[:400]}

Follow this exact structure, with blank lines between sections:
STOCK SNAPSHOT
• Company:
• Current price:
• 52-week range:

IN SIMPLE WORDS
Explain what the company does in 1-2 simple sentences.

WHAT LOOKS GOOD
• Give 2 short points supported by the data.

RISKS TO WATCH
• Give 2 short points supported by the data.

IS IT EXPENSIVE OR CHEAP?
Explain P/E simply. Compare only if comparable data exists. Share price alone does not show whether a stock is cheap.

WHAT TO CHECK NEXT
• Give 2 practical checks.

BOTTOM LINE
Give a balanced 2-sentence summary. No guaranteed buy/sell call.
If a metric is N/A, say data is unavailable. Keep under 220 words.
{get_language_prompt(language)}
"""
        return format_with_bold_headings(groq_generate(prompt, language, 900))
    except Exception:
        logging.exception("Stock analysis failed symbol=%s", data.get("symbol"))
        return f"Stock data for {data.get('name', data.get('symbol', 'this company'))} was fetched, but the written analysis is temporarily unavailable. Please try again shortly."

def analyze_comparison(data1, data2, language="hinglish"):
    try:
        prompt = f"""
You are StockLens, explaining two Indian stocks to a beginner. Use short bullets and simple language, not a long essay. Use only supplied facts. Never invent values or promise returns.
Company A: {data1.get('name')} ({data1.get('symbol')})
Price: {data1.get('price')} | P/E: {data1.get('pe_ratio')} | Growth: {data1.get('revenue_growth')} | Profit margin: {data1.get('profit_margins')} | Debt/equity: {data1.get('debt_to_equity')} | ROE: {data1.get('roe')} | Sector: {data1.get('sector')}
Company B: {data2.get('name')} ({data2.get('symbol')})
Price: {data2.get('price')} | P/E: {data2.get('pe_ratio')} | Growth: {data2.get('revenue_growth')} | Profit margin: {data2.get('profit_margins')} | Debt/equity: {data2.get('debt_to_equity')} | ROE: {data2.get('roe')} | Sector: {data2.get('sector')}

Use this exact structure, with blank lines:
STOCK COMPARISON: {data1.get('name')} vs {data2.get('name')}

WHAT THEY DO
• Company A: one simple sentence.
• Company B: one simple sentence.

NUMBERS AT A GLANCE
• Valuation (P/E): compare only if the data supports it.
• Growth and profit: compare available figures.
• Debt and returns: compare available figures.

STRENGTHS
• One supported point for each company.

RISKS
• One supported point for each company.

SIMPLE TAKEAWAY
Explain the key difference in 2 sentences. Do not name a winner if data is insufficient. A lower share price alone does not mean cheaper.
Keep under 260 words. Explain technical terms briefly.
{get_language_prompt(language)}
"""
        return format_comparison_headings(groq_generate(prompt, language, 1000))
    except Exception:
        logging.exception("Comparison analysis failed symbols=%s,%s", data1.get("symbol"), data2.get("symbol"))
        return f"Stock data was fetched for {data1.get('name')} and {data2.get('name')}, but the comparison text is temporarily unavailable. Please try again shortly."

def analyze_portfolio(stocks_data, language="hinglish"):
    try:
        summary = "\\n".join(
            f"- {d.get('name')} ({d.get('symbol')}): price={d.get('price')}, P/E={d.get('pe_ratio')}, "
            f"sector={d.get('sector')}, growth={d.get('revenue_growth')}, margin={d.get('profit_margins')}, "
            f"debt/equity={d.get('debt_to_equity')}, ROE={d.get('roe')}"
            for d in stocks_data
        )
        prompt = f"""
Review this Indian stock portfolio using only these metrics. Do not invent facts or promise returns.
Holdings:
{summary}
Use headings: PORTFOLIO ANALYSIS, WHAT YOU OWN, SECTOR CONCENTRATION, RELATIVE STRENGTHS, MAIN RISKS, OVERALL PORTFOLIO REVIEW.
Explain limitations and missing metrics.
{get_language_prompt(language)}
"""
        return format_portfolio_headings(groq_generate(prompt, language, 1000))
    except Exception:
        logging.exception("Portfolio analysis failed")
        return "Portfolio analysis is temporarily unavailable. Please try again shortly."

def analyze_mutual_fund(fund, returns, meta, language="hinglish"):
    try:
        returns_text = ", ".join(f"{period}: {value}%" for period, value in (returns or {}).items()) or "Unavailable"
        prompt = f"""
Explain this Indian mutual fund to a beginner. Past returns do not guarantee future performance.
Fund: {fund.get('name')}
Fund house: {meta.get('fund_house', 'N/A')}
Category: {meta.get('scheme_category', 'N/A')}
Type: {meta.get('scheme_type', 'N/A')}
Latest NAV: {fund.get('nav', 'N/A')} as of {fund.get('date', 'N/A')}
Calculated historical returns: {returns_text}
Use headings: MUTUAL FUND ANALYSIS, WHAT THIS FUND INVESTS IN, PERFORMANCE, RISK AND LIMITATIONS, WHO MAY FIND IT SUITABLE, VERDICT.
State clearly when information is unavailable. 7% is only an illustrative comparison, not a guaranteed or equivalent benchmark.
{get_language_prompt(language)}
"""
        return format_fund_headings(groq_generate(prompt, language, 900))
    except Exception:
        logging.exception("Mutual fund analysis failed scheme=%s", fund.get("code"))
        return "Mutual fund analysis is temporarily unavailable. NAV data may still be available above."

def format_fund_headings(text):
    headings = [
        "MUTUAL FUND ANALYSIS",
        "WHAT IS THIS FUND",
        "HOW HAS IT PERFORMED",
        "WHO SHOULD INVEST",
        "RISK LEVEL",
        "FINAL VERDICT",
        "LOOKS GOOD",
        "AVERAGE",
        "NOT RECOMMENDED",
    ]
    lines = text.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        for heading in headings:
            if heading in stripped.upper():
                line = f"*{stripped}*"
                break
        new_lines.append(line)
    return '\n'.join(new_lines)

async def send_morning_briefs(app):
    print("Sending morning briefs...")
    try:
        result = supabase.table("subscriptions").select("*").execute()
        subscriptions = result.data
        user_stocks = {}
        for sub in subscriptions:
            chat_id = sub['chat_id']
            if chat_id not in user_stocks:
                user_stocks[chat_id] = []
            user_stocks[chat_id].append(sub['symbol'])
        for chat_id, symbols in user_stocks.items():
            try:
                message = "Good morning! Here is your daily stock update:\n\n"
                for symbol in symbols:
                    data = get_morning_brief_data(symbol)
                    if data:
                        direction = "up" if data['positive'] else "down"
                        message += f"{data['name']}\n"
                        message += f"Price: {data['price']} ({data['change']})\n"
                        message += f"Stock is {direction} today.\n\n"
                message += "Send any stock name for full analysis."
                await app.bot.send_message(chat_id=chat_id, text=message)
            except Exception as e:
                print(f"Error sending to {chat_id}: {e}")
    except Exception as e:
        print(f"Morning brief error: {e}")

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("Cancelled. Send any stock name to try again.")
        return
    
    if query.data.startswith("analyze_"):
        symbol = query.data.replace("analyze_", "")
        await query.edit_message_text(f"Analyzing {symbol}... please wait")
        
        language = get_user_language(query.message.chat_id)
        data = get_stock_data(symbol)
        
        if not data or data['price'] == 'N/A':
            await query.message.reply_text("Could not fetch data. Please try again.")
            return
        
        chart = generate_chart(data['symbol'], data['name'])
        if chart:
            await query.message.reply_photo(photo=chart)
        
        analysis = analyze_stock(data, language)
        try:
            await query.message.reply_text(analysis, parse_mode='Markdown')
        except Exception:
            await query.message.reply_text(analysis)

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    full_text = update.message.text.replace('/subscribe', '').strip()
    if not full_text:
        await update.message.reply_text(
            "Please send stock symbols after /subscribe\n\nExample: /subscribe TCS RELIANCE INFY"
        )
        return
    symbols = [s.strip().upper() for s in full_text.replace(',', ' ').split() if s.strip()]
    added = []
    failed = []
    for symbol in symbols:
        data = get_stock_data(symbol)
        if data and data['price'] != 'N/A':
            try:
                supabase.table("subscriptions").upsert({
                    "chat_id": chat_id,
                    "symbol": data['symbol']
                }).execute()
                added.append(data['symbol'])
            except Exception as e:
                print(f"Supabase error: {e}")
                failed.append(symbol)
        else:
            failed.append(symbol)
    reply = ""
    if added:
        reply += f"Subscribed to: {', '.join(added)}\nYou will get daily updates at 9am IST.\n\n"
    if failed:
        reply += f"Could not find: {', '.join(failed)}"
    await update.message.reply_text(reply)

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    full_text = update.message.text.replace('/unsubscribe', '').strip()
    if not full_text:
        await update.message.reply_text(
            "Please send stock symbols after /unsubscribe\n\nExample: /unsubscribe TCS RELIANCE"
        )
        return
    symbols = [s.strip().upper() for s in full_text.replace(',', ' ').split() if s.strip()]
    removed = []
    for symbol in symbols:
        try:
            supabase.table("subscriptions").delete().eq("chat_id", chat_id).eq("symbol", symbol).execute()
            removed.append(symbol)
        except Exception as e:
            print(f"Unsubscribe error: {e}")
    await update.message.reply_text(f"Unsubscribed from: {', '.join(removed)}")

async def my_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        result = supabase.table("subscriptions").select("*").eq("chat_id", chat_id).execute()
        subs = result.data
        if not subs:
            await update.message.reply_text(
                "You have no subscriptions.\n\nUse /subscribe TCS RELIANCE to subscribe."
            )
            return
        symbols = [s['symbol'] for s in subs]
        await update.message.reply_text(
            f"Your subscriptions:\n{', '.join(symbols)}\n\nUse /unsubscribe SYMBOL to remove."
        )
    except Exception as e:
        await update.message.reply_text("Error fetching subscriptions.")

NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK",
    "INFOSYS", "SBIN", "HINDUNILVR", "ITC", "KOTAKBANK",
    "LT", "HCLTECH", "MARUTI", "AXISBANK", "TITAN",
    "SUNPHARMA", "ULTRACEMCO", "NESTLEIND", "WIPRO", "NTPC",
    "POWERGRID", "TATAMOTORS", "BAJFINANCE", "ASIANPAINT", "TECHM",
    "ONGC", "TATASTEEL", "ADANIENT", "COALINDIA", "DIVISLAB"
]

def get_trending_stocks():
    try:
        movers = []
        for symbol in NIFTY50:
            try:
                stock = yf.Ticker(yahoo_symbol(symbol))
                hist = stock.history(period="2d")
                if len(hist) < 2:
                    continue
                prev = hist['Close'].iloc[-2]
                curr = hist['Close'].iloc[-1]
                volume = hist['Volume'].iloc[-1]
                change_pct = round(((curr - prev) / prev) * 100, 2)
                movers.append({
                    "symbol": symbol,
                    "price": round(curr, 2),
                    "change_pct": change_pct,
                    "volume": volume
                })
            except:
                continue
        if not movers:
            return None
        movers.sort(key=lambda x: abs(x['change_pct']), reverse=True)
        return movers[:5]
    except Exception as e:
        print(f"Trending error: {e}")
        return None

def get_stock_news(symbol, company_name):
    articles = []
    try:
        raw_news = yf.Ticker(yahoo_symbol(symbol)).news or []
        for item in raw_news[:10]:
            content = item.get("content") if isinstance(item.get("content"), dict) else item
            title = content.get("title") or item.get("title") or ""
            summary = content.get("summary") or item.get("summary") or ""
            provider = content.get("provider") or item.get("publisher") or {}
            source = provider.get("displayName") if isinstance(provider, dict) else str(provider)
            pub_date = content.get("pubDate") or ""
            if not pub_date and item.get("providerPublishTime"):
                try:
                    pub_date = datetime.fromtimestamp(int(item["providerPublishTime"]), pytz.UTC).strftime("%Y-%m-%d")
                except Exception:
                    pub_date = ""
            if title:
                articles.append({"title": title, "summary": str(summary)[:250],
                                 "source": source or "Yahoo Finance", "date": str(pub_date)[:16]})
    except Exception:
        logging.exception("Yahoo news failed symbol=%s", symbol)
    if articles:
        return articles[:5]
    try:
        query = urllib.parse.quote(f'"{company_name}" stock India')
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        root = ET.fromstring(http_read(rss_url, timeout=15))
        for item in root.findall("./channel/item")[:8]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            description = (item.findtext("description") or "").strip()
            articles.append({
                "title": title,
                "summary": re.sub(r"<[^>]+>", " ", description)[:250],
                "source": item.findtext("source") or "Google News",
                "date": (item.findtext("pubDate") or "")[:16],
                "url": (item.findtext("link") or "").strip(),
            })
        return articles[:5] or None
    except Exception:
        logging.exception("Google News fallback failed company=%s", company_name)
        return None

async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching today's top movers... please wait")
    movers = get_trending_stocks()
    if not movers:
        await update.message.reply_text("Could not fetch trending stocks right now. Try again later.")
        return
    message = "TOP MOVERS TODAY\n\n"
    for i, stock in enumerate(movers, 1):
        direction = "up" if stock['change_pct'] >= 0 else "down"
        sign = "+" if stock['change_pct'] >= 0 else ""
        message += f"{i}. {stock['symbol']}\n"
        message += f"   Price: {stock['price']} | {sign}{stock['change_pct']}% {direction}\n\n"
    message += "Send any stock name for full analysis."
    await update.message.reply_text(message)

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.partition(" ")[2].strip()
    if not query:
        await update.message.reply_text("Please send a stock symbol after /news\\n\\nExample: /news TCS")
        return
    await update.message.reply_text(f"Fetching news for {query}... please wait")
    data = get_stock_data(query)
    if not data or data.get("price") in (None, "N/A"):
        matches = search_stocks_by_name(query)
        if matches:
            data = get_stock_data(matches[0][0])
    if not data or data.get("price") in (None, "N/A"):
        await update.message.reply_text("Stock not found. Please check the company name or NSE symbol.")
        return
    articles = get_stock_news(data["symbol"], data["name"])
    if not articles:
        await update.message.reply_text(
            f"I couldn't retrieve news for {data['name']} right now. This does not necessarily mean there is no recent news. Please try again shortly."
        )
        return
    lines = [f"LATEST NEWS: {data['name']}", ""]
    for i, article in enumerate(articles, 1):
        lines.append(f"{i}. {article['title']}")
        if article.get("summary"):
            lines.append(article["summary"])
        source_line = "Source: " + str(article.get("source") or "Unknown")
        if article.get("date"):
            source_line += " | " + str(article["date"])
        lines.append(source_line)
        if article.get("url"):
            lines.append(article["url"])
        lines.append("")
    await update.message.reply_text("\\n".join(lines)[:4000])

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = clean_company_query(update.message.text.replace('/search', '').strip())
    
    if not query:
        await update.message.reply_text(
            "Type a company name after /search\n\nExamples:\n/search tata\n/search hdfc\n/search bajaj\n/search adani"
        )
        return
    
    matches = search_stocks_by_name(query)
    
    if not matches:
        await update.message.reply_text(
            f"No stocks found for '{query}'.\n\nTry:\n/search tata\n/search hdfc\n/search reliance"
        )
        return
    
    if len(matches) == 1:
        symbol = matches[0][0]
        language = get_user_language(update.message.chat_id)
        await update.message.reply_text(f"Analyzing {matches[0][1]}... please wait")
        data = get_stock_data(symbol)
        if not data or data['price'] == 'N/A':
            await update.message.reply_text("Could not fetch data. Please try again.")
            return
        chart = generate_chart(data['symbol'], data['name'])
        if chart:
            await update.message.reply_photo(photo=chart)
        analysis = analyze_stock(data, language)
        try:
            await update.message.reply_text(analysis, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(analysis)
        return
    
    keyboard = create_stock_keyboard(matches)
    await update.message.reply_text(
        f"Found {len(matches)} stocks matching '{query}'.\nWhich one do you want?",
        reply_markup=keyboard
    )

def load_amfi_funds():
    try:
        raw = http_read("https://www.amfiindia.com/spages/NAVAll.txt", timeout=18).decode("utf-8-sig", errors="replace")
        funds = []
        for line in raw.splitlines():
            parts = [part.strip() for part in line.split(";")]
            if len(parts) >= 6 and parts[0].isdigit() and parts[3]:
                funds.append({"code": parts[0], "name": parts[3], "nav": parts[4], "date": parts[5]})
        if funds:
            logging.info("Loaded %s schemes from AMFI", len(funds))
            return funds
        logging.warning("AMFI returned no parseable scheme rows")
    except Exception:
        logging.exception("AMFI NAV source failed; trying mfapi fallback")
    try:
        schemes = json.loads(http_read("https://api.mfapi.in/mf", timeout=25).decode("utf-8"))
        funds = []
        for item in schemes if isinstance(schemes, list) else []:
            code_value = str(item.get("schemeCode", "")).strip()
            name = str(item.get("schemeName", "")).strip()
            if code_value.isdigit() and name:
                funds.append({"code": code_value, "name": name, "nav": "", "date": ""})
        logging.info("Loaded %s schemes from mfapi fallback", len(funds))
        return funds
    except Exception:
        logging.exception("mfapi scheme list failed")
        return []

def fetch_fund_details(scheme_code):
    try:
        raw = http_read(f"https://api.mfapi.in/mf/{urllib.parse.quote(str(scheme_code))}", timeout=18)
        payload = json.loads(raw.decode("utf-8"))
        meta = payload.get("meta") or {}
        nav_data = payload.get("data") or []
        latest = nav_data[0] if nav_data else {}
        fund = {
            "code": str(scheme_code),
            "name": meta.get("scheme_name") or meta.get("schemeName") or "",
            "nav": latest.get("nav", "N/A"),
            "date": latest.get("date", "N/A"),
        }
        return fund, meta, nav_data
    except Exception:
        logging.exception("Fund details fetch failed scheme=%s", scheme_code)
        return None, {}, []

def search_fund(query, funds):
    query_lower = query.lower()
    words = query_lower.split()
    scored = []
    for fund in funds:
        name_lower = fund['name'].lower()
        score = sum(1 for word in words if word in name_lower)
        if score > 0:
            scored.append((score, fund))
    scored.sort(key=lambda x: x[0], reverse=True)
    growth_first = [f for _, f in scored if 'growth' in f['name'].lower()]
    others = [f for _, f in scored if 'growth' not in f['name'].lower()]
    return (growth_first + others)[:1]

def get_fund_returns(scheme_code):
    try:
        _, _, nav_data = fetch_fund_details(scheme_code)
        if len(nav_data) < 2:
            return {}
        current_nav = float(nav_data[0]["nav"])
        returns = {}
        for key, days in (("1m", 30), ("6m", 180), ("1y", 365), ("3y", 1095)):
            if len(nav_data) > days:
                past_nav = float(nav_data[days]["nav"])
                if past_nav:
                    returns[key] = round((current_nav - past_nav) / past_nav * 100, 2)
        return returns
    except Exception:
        logging.exception("Fund returns calculation failed scheme=%s", scheme_code)
        return {}

def generate_fund_chart(scheme_code, fund_name):
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        data = json.loads(http_read(url, timeout=18).decode('utf-8'))
        nav_data = (data.get('data') or [])[:365]
        if len(nav_data) < 2:
            return None
        nav_data.reverse()
        dates = [item['date'] for item in nav_data]
        navs = [float(item['nav']) for item in nav_data]
        date_objects = [datetime.strptime(d, '%d-%m-%Y') for d in dates]
        fig, ax = plt.subplots(figsize=(10, 5))
        start_nav = navs[0]
        end_nav = navs[-1]
        color = '#2ecc71' if end_nav >= start_nav else '#e74c3c'
        ax.plot(date_objects, navs, color=color, linewidth=2)
        ax.fill_between(date_objects, navs, alpha=0.1, color=color)
        ax.set_title(f"{fund_name[:50]} - 1 Year NAV Chart", fontsize=12, fontweight='bold', pad=15)
        ax.set_ylabel("NAV (INR)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.xticks(rotation=45)
        ax.annotate(f'NAV: {end_nav:.2f}',
                   xy=(date_objects[-1], end_nav),
                   xytext=(-80, 15),
                   textcoords='offset points',
                   fontsize=10,
                   fontweight='bold',
                   color=color)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        return buf
    except Exception as e:
        print(f"Fund chart error: {e}")
        return None

async def mf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.partition(" ")[2].strip()
    if not query:
        await update.message.reply_text(
            "Please send a fund name after /mf\\n\\nExamples:\\n"
            "/mf Parag Parikh Flexi Cap\\n/mf Mirae Asset Large Cap\\n"
            "/mf SBI Small Cap\\n/mf Axis Bluechip"
        )
        return
    await update.message.reply_text(f"Searching for {query}... please wait")
    funds = load_amfi_funds()
    if not funds:
        await update.message.reply_text("Fund data sources are temporarily unavailable. Please try again shortly.")
        return
    results = search_fund(query, funds)
    if not results:
        await update.message.reply_text(f"No fund found for '{query}'. Try a more specific name.")
        return
    fund = results[0]
    await update.message.reply_text(f"Found: {fund['name']}\\nFetching NAV history...")
    detailed, meta, nav_data = fetch_fund_details(fund["code"])
    if detailed:
        fund.update({k: v for k, v in detailed.items() if v not in (None, "")})
        if not fund.get("name"):
            fund["name"] = results[0]["name"]
    returns = get_fund_returns(fund["code"]) or {}
    chart = generate_fund_chart(fund["code"], fund["name"])
    if chart:
        await update.message.reply_photo(photo=chart)
    else:
        await update.message.reply_text(
            f"Latest NAV: {fund.get('nav', 'N/A')} | Date: {fund.get('date', 'N/A')}"
        )
    language = get_user_language(update.message.chat_id)
    analysis = analyze_mutual_fund(fund, returns, meta, language)
    try:
        await update.message.reply_text(analysis, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(analysis)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    current = get_user_language(chat_id)
    await update.message.reply_text(
        f"Current language: {current.upper()}\n\n"
        "Choose your preferred language:\n\n"
        "/lang_english - English\n"
        "/lang_hinglish - Hinglish (default)\n"
        "/lang_hindi - Hindi\n"
        "/lang_marathi - Marathi"
    )

async def set_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'english')
    await update.message.reply_text("Language set to English. All analysis will now be in English.")

async def set_hinglish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'hinglish')
    await update.message.reply_text("Language set to Hinglish. Bilkul sahi choice hai!")

async def set_hindi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'hindi')
    await update.message.reply_text("Language set to Hindi.")

async def set_marathi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'marathi')
    await update.message.reply_text("Language set to Marathi.")

def search_stocks_by_name(query):
    query_lower = query.lower().strip()
    
    # Large database of NSE stocks with common names
    stock_database = {
        "tata consultancy": "TCS", "tcs": "TCS",
        "tata consultancy services": "TCS",
        "reliance": "RELIANCE", "reliance industries": "RELIANCE",
        "hdfc bank": "HDFCBANK", "hdfc": "HDFCBANK",
        "infosys": "INFY", "infy": "INFY",
        "wipro": "WIPRO",
        "tata motors": "TATAMOTORS", "tata motor": "TATAMOTORS",
        "bajaj finance": "BAJFINANCE", "bajaj fin": "BAJFINANCE",
        "adani ports": "ADANIPORTS", "adani port": "ADANIPORTS",
        "adani enterprises": "ADANIENT", "adani": "ADANIENT",
        "sun pharma": "SUNPHARMA", "sunpharma": "SUNPHARMA", "sun pharmaceutical": "SUNPHARMA",
        "icici bank": "ICICIBANK", "icici": "ICICIBANK",
        "axis bank": "AXISBANK", "axis": "AXISBANK",
        "kotak bank": "KOTAKBANK", "kotak": "KOTAKBANK", "kotak mahindra": "KOTAKBANK",
        "larsen": "LT", "larsen toubro": "LT", "l&t": "LT", "lt": "LT",
        "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR", "hindustan lever": "HINDUNILVR",
        "asian paints": "ASIANPAINT", "asian paint": "ASIANPAINT",
        "maruti": "MARUTI", "maruti suzuki": "MARUTI",
        "nestle": "NESTLEIND", "nestle india": "NESTLEIND",
        "titan": "TITAN", "titan company": "TITAN",
        "ultratech": "ULTRACEMCO", "ultratech cement": "ULTRACEMCO",
        "ongc": "ONGC", "oil natural gas": "ONGC",
        "ntpc": "NTPC",
        "power grid": "POWERGRID", "powergrid": "POWERGRID",
        "sbi": "SBIN", "state bank": "SBIN", "state bank india": "SBIN",
        "bajaj auto": "BAJAJ-AUTO",
        "hero motocorp": "HEROMOTOCO", "hero": "HEROMOTOCO", "hero honda": "HEROMOTOCO",
        "dr reddy": "DRREDDY", "dr reddys": "DRREDDY", "doctor reddy": "DRREDDY",
        "cipla": "CIPLA",
        "hindalco": "HINDALCO",
        "tata steel": "TATASTEEL", "tata steel ltd": "TATASTEEL",
        "jsw steel": "JSWSTEEL", "jsw": "JSWSTEEL",
        "coal india": "COALINDIA",
        "bharti airtel": "BHARTIARTL", "airtel": "BHARTIARTL", "bharti": "BHARTIARTL",
        "hcl": "HCLTECH", "hcl tech": "HCLTECH", "hcl technologies": "HCLTECH",
        "tech mahindra": "TECHM", "tech m": "TECHM",
        "indusind bank": "INDUSINDBK", "indusind": "INDUSINDBK",
        "divis labs": "DIVISLAB", "divis": "DIVISLAB", "divi": "DIVISLAB",
        "grasim": "GRASIM",
        "tata consumer": "TATACONSUM", "tata consumer products": "TATACONSUM",
        "britannia": "BRITANNIA",
        "eicher motors": "EICHERMOT", "eicher": "EICHERMOT", "royal enfield": "EICHERMOT",
        "itc": "ITC", "itc ltd": "ITC",
        "bajaj finserv": "BAJAJFINSV",
        "hdfc life": "HDFCLIFE",
        "sbi life": "SBILIFE",
        "paytm": "PAYTM", "one97": "PAYTM",
        "nykaa": "NYKAA", "fss": "NYKAA",
        "dmart": "DMART", "avenue supermarts": "DMART",
        "pidilite": "PIDILITIND", "fevicol": "PIDILITIND",
        "berger paints": "BERGEPAINT", "berger": "BERGEPAINT",
        "havells": "HAVELLS",
        "voltas": "VOLTAS",
        "godrej consumer": "GODREJCP", "godrej": "GODREJCP",
        "dabur": "DABUR",
        "marico": "MARICO",
        "colgate": "COLPAL", "colgate palmolive": "COLPAL",
        "siemens": "SIEMENS",
        "abbott india": "ABBOTINDIA", "abbott": "ABBOTINDIA",
        "page industries": "PAGEIND", "jockey": "PAGEIND",
        "mrf": "MRF", "mrf tyres": "MRF",
        "balkrishna": "BALKRISIND", "bkt": "BALKRISIND",
        "info edge": "NAUKRI", "naukri": "NAUKRI", "jeevansathi": "NAUKRI",
        "indiamart": "INDIAMART",
        "zomato": "ZOMATO",
        "swiggy": "SWIGGY",
        "irctc": "IRCTC",
        "indian railway": "IRCTC",
        "interglobe": "INDIGO", "indigo": "INDIGO", "indigo airlines": "INDIGO",
        "spicejet": "SPICEJET",
        "sula wines": "SULA",
        "metro brands": "METROBRAND", "metro shoes": "METROBRAND",
        "campus activewear": "CAMPUS",
        "trent": "TRENT", "westside": "TRENT", "zudio": "TRENT",
        "vedanta": "VEDL", "vedl": "VEDL",
        "hindzinc": "HINDZINC", "hindustan zinc": "HINDZINC",
        "nmdc": "NMDC",
        "sail": "SAIL", "steel authority": "SAIL",
        "bhel": "BHEL", "bharat heavy": "BHEL",
        "bpcl": "BPCL", "bharat petroleum": "BPCL",
        "ioc": "IOC", "indian oil": "IOC",
        "hpcl": "HPCL", "hindustan petroleum": "HPCL",
        "gail": "GAIL", "gail india": "GAIL",
        "torrent pharma": "TORNTPHARM", "torrent": "TORNTPHARM",
        "lupin": "LUPIN",
        "aurobindo": "AUROPHARMA", "aurobindo pharma": "AUROPHARMA",
        "mankind pharma": "MANKIND",
        "max healthcare": "MAXHEALTH",
        "apollo hospitals": "APOLLOHOSP", "apollo": "APOLLOHOSP",
        "fortis": "FORTIS", "fortis healthcare": "FORTIS",
        "muthoot finance": "MUTHOOTFIN", "muthoot": "MUTHOOTFIN",
        "shriram finance": "SHRIRAMFIN", "shriram": "SHRIRAMFIN",
        "cholamandalam": "CHOLAFIN", "chola": "CHOLAFIN",
        "pnb": "PNB", "punjab national bank": "PNB",
        "bank of baroda": "BANKBARODA", "bob": "BANKBARODA",
        "canara bank": "CANBK", "canara": "CANBK",
        "union bank": "UNIONBANK",
        "indian bank": "INDIANB",
        "federal bank": "FEDERALBNK",
        "bandhan bank": "BANDHANBNK", "bandhan": "BANDHANBNK",
        "au small finance": "AUBANK", "au bank": "AUBANK",
        "ujjivan": "UJJIVANSFB",
        "polycab": "POLYCAB",
        "dixon technologies": "DIXON", "dixon": "DIXON",
        "amber enterprises": "AMBER",
        "finolex": "FINPIPE", "finolex cables": "FINPIPE",
        "whirlpool": "WHIRLPOOL",
        "crompton": "CROMPTON", "crompton greaves": "CROMPTON",
        "blue star": "BLUESTAR",
        "symphony": "SYMPHONY",
        "prestige estates": "PRESTIGE", "prestige": "PRESTIGE",
        "godrej properties": "GODREJPROP",
        "dlf": "DLF",
        "oberoi realty": "OBEROIRLTY", "oberoi": "OBEROIRLTY",
        "macrotech": "LODHA", "lodha": "LODHA",
    }
    query_lower = clean_company_query(query).lower().strip()
    if not query_lower:
        return []
    if query_lower in stock_database:
        symbol = stock_database[query_lower]
        return [(symbol, get_full_name(symbol))]
    ticker = query_lower.upper()
    if ticker.endswith((".NS", ".BO")):
        ticker = ticker.rsplit(".", 1)[0]
    if ticker in set(stock_database.values()):
        return [(ticker, get_full_name(ticker))]

    matches = []
    seen = set()
    for name, symbol in stock_database.items():
        if query_lower in name and symbol not in seen:
            matches.append((symbol, get_full_name(symbol)))
            seen.add(symbol)
    if matches:
        return matches[:5]

    # Typo-tolerant suggestions; these are shown to the user for confirmation.
    close_names = difflib.get_close_matches(query_lower, list(stock_database.keys()), n=5, cutoff=0.45)
    if not close_names and len(query_lower.split()) > 1:
        scores = []
        qtokens = set(query_lower.split())
        for name in stock_database:
            ntokens = set(name.split())
            overlap = len(qtokens & ntokens) / max(1, len(qtokens | ntokens))
            ratio = difflib.SequenceMatcher(None, query_lower, name).ratio()
            score = max(ratio, overlap * 0.9)
            if score >= 0.34:
                scores.append((score, name))
        close_names = [name for score, name in sorted(scores, reverse=True)[:5]]

    suggestions = []
    for name in close_names:
        symbol = stock_database[name]
        if all(item[0] != symbol for item in suggestions):
            suggestions.append((symbol, get_full_name(symbol)))
    return suggestions[:5]

def get_full_name(symbol):
    name_map = {
        "TCS": "Tata Consultancy Services",
        "RELIANCE": "Reliance Industries",
        "HDFCBANK": "HDFC Bank",
        "INFY": "Infosys",
        "WIPRO": "Wipro",
        "TATAMOTORS": "Tata Motors",
        "BAJFINANCE": "Bajaj Finance",
        "ADANIPORTS": "Adani Ports",
        "ADANIENT": "Adani Enterprises",
        "SUNPHARMA": "Sun Pharma",
        "ICICIBANK": "ICICI Bank",
        "AXISBANK": "Axis Bank",
        "KOTAKBANK": "Kotak Mahindra Bank",
        "LT": "Larsen & Toubro",
        "HINDUNILVR": "Hindustan Unilever",
        "ASIANPAINT": "Asian Paints",
        "MARUTI": "Maruti Suzuki",
        "NESTLEIND": "Nestle India",
        "TITAN": "Titan Company",
        "ULTRACEMCO": "UltraTech Cement",
        "ONGC": "ONGC",
        "NTPC": "NTPC",
        "POWERGRID": "Power Grid",
        "SBIN": "State Bank of India",
        "BAJAJ-AUTO": "Bajaj Auto",
        "HEROMOTOCO": "Hero MotoCorp",
        "DRREDDY": "Dr Reddy's",
        "CIPLA": "Cipla",
        "HINDALCO": "Hindalco",
        "TATASTEEL": "Tata Steel",
        "JSWSTEEL": "JSW Steel",
        "COALINDIA": "Coal India",
        "BHARTIARTL": "Bharti Airtel",
        "HCLTECH": "HCL Technologies",
        "TECHM": "Tech Mahindra",
        "INDUSINDBK": "IndusInd Bank",
        "DIVISLAB": "Divi's Laboratories",
        "GRASIM": "Grasim Industries",
        "TATACONSUM": "Tata Consumer Products",
        "BRITANNIA": "Britannia Industries",
        "EICHERMOT": "Eicher Motors",
        "ITC": "ITC",
        "BAJAJFINSV": "Bajaj Finserv",
        "HDFCLIFE": "HDFC Life",
        "SBILIFE": "SBI Life",
        "PAYTM": "Paytm",
        "DMART": "DMart",
        "IRCTC": "IRCTC",
        "INDIGO": "IndiGo Airlines",
        "ZOMATO": "Zomato",
        "TRENT": "Trent",
        "DLF": "DLF",
        "LODHA": "Lodha",
    }
    return name_map.get(symbol, symbol)

VOICE_MAP = {
    'english': 'en-IN-NeerjaNeural',
    'hinglish': 'hi-IN-SwaraNeural',
    'hindi': 'hi-IN-SwaraNeural',
    'marathi': 'mr-IN-AarohiNeural',
}

async def text_to_speech(text, language):
    try:
        import edge_tts
        from io import BytesIO
        
        # Clean text for TTS — remove markdown and special chars
        clean = text.replace('*', '').replace('_', '').replace('#', '')
        clean = clean.replace('👍', '').replace('👎', '').replace('📊', '')
        clean = clean.replace('📉', '').replace('📈', '').replace('⭐', '')
        clean = clean.replace('-', '').strip()
        
        # Limit length for TTS
        if len(clean) > 1000:
            clean = clean[:1000] + '...'
        
        voice = VOICE_MAP.get(language, 'en-IN-NeerjaNeural')
        
        tts = edge_tts.Communicate(clean, voice=voice)
        
        buf = BytesIO()
        async for chunk in tts.stream():
            if chunk['type'] == 'audio':
                buf.write(chunk['data'])
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"TTS error: {e}")
        return None

def get_user_voice(chat_id):
    try:
        result = supabase.table("user_preferences").select("voice_enabled").eq("chat_id", chat_id).execute()
        if result.data and result.data[0].get('voice_enabled') is not None:
            return result.data[0]['voice_enabled']
        return False
    except:
        return False

def set_user_voice(chat_id, enabled):
    try:
        supabase.table("user_preferences").upsert({
            "chat_id": chat_id,
            "voice_enabled": enabled,
            "updated_at": "now()"
        }).execute()
        return True
    except Exception as e:
        print(f"Voice set error: {e}")
        return False
    
def create_suggestion_keyboard(matches):
    rows = []
    for symbol, name in matches[:5]:
        rows.append([InlineKeyboardButton(f"{name} ({symbol})"[:60], callback_data=f"analyze_{symbol}")])
    rows.append([InlineKeyboardButton("None of these", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    language = get_user_language(update.message.chat_id)
    print(f"User language: {language} for chat_id: {update.message.chat_id}")
    user_input = update.message.text.strip()
    print(f"Received: {user_input}")

    # Portfolio mode
    is_portfolio = ',' in user_input and ' vs ' not in user_input.lower()
    if is_portfolio:
        symbols = [s.strip() for s in user_input.split(',')]
        if len(symbols) < 2:
            await update.message.reply_text("Please send at least 2 stocks separated by commas.\n\nExample: TCS, INFY, HDFCBANK")
            return
        if len(symbols) > 6:
            await update.message.reply_text("Maximum 6 stocks at a time for portfolio analysis.")
            return
        await update.message.reply_text(f"Analyzing your portfolio of {len(symbols)} stocks... please wait")
        stocks_data = []
        failed = []
        for symbol in symbols:
            data = get_stock_data(symbol)
            if data and data['price'] != 'N/A':
                stocks_data.append(data)
            else:
                failed.append(symbol)
        if len(stocks_data) < 2:
            await update.message.reply_text("Could not fetch data for enough stocks. Please check the symbols.")
            return
        if failed:
            await update.message.reply_text(f"Could not find: {', '.join(failed)}. Analyzing the rest.")
        analysis = analyze_portfolio(stocks_data, language)
        formatted = format_portfolio_headings(analysis)
        try:
            await update.message.reply_text(formatted, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(analysis)
        return

    # Comparison mode
    parts = re.split(r"\s+(?:vs\.?|versus)\s+", user_input, maxsplit=1, flags=re.IGNORECASE)
    is_comparison = len(parts) == 2
    if is_comparison:
        stock1 = parts[0].strip()
        stock2 = parts[1].strip()
        await update.message.reply_text(f"Comparing {stock1} vs {stock2}... please wait")
        data1 = get_stock_data(stock1)
        data2 = get_stock_data(stock2)
        if not data1 or data1['price'] == 'N/A':
            suggestions = search_stocks_by_name(stock1)
            if suggestions:
                await update.message.reply_text(
                    f'I couldn\'t identify "{clean_company_query(stock1)}" confidently. Did you mean one of these?',
                    reply_markup=create_suggestion_keyboard(suggestions)
                )
            else:
                await update.message.reply_text(
                    f'I couldn\'t find "{clean_company_query(stock1)}". Try a company name or ticker such as TCS, Tata Motors, HDFC Bank or ITC.'
                )
            return
        if not data2 or data2['price'] == 'N/A':
            suggestions = search_stocks_by_name(stock2)
            if suggestions:
                await update.message.reply_text(
                    f'I couldn\'t identify "{clean_company_query(stock2)}" confidently. Did you mean one of these?',
                    reply_markup=create_suggestion_keyboard(suggestions)
                )
            else:
                await update.message.reply_text(
                    f'I couldn\'t find "{clean_company_query(stock2)}". Try a company name or ticker such as TCS, Tata Motors, HDFC Bank or ITC.'
                )
            return
        chart1 = generate_chart(data1['symbol'], data1['name'])
        chart2 = generate_chart(data2['symbol'], data2['name'])
        if chart1:
            await update.message.reply_photo(photo=chart1, caption=data1['name'])
        if chart2:
            await update.message.reply_photo(photo=chart2, caption=data2['name'])
        comparison = analyze_comparison(data1, data2, language)
        formatted = format_comparison_headings(comparison)
        try:
            await update.message.reply_text(formatted, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(comparison)
        return

# Single stock — try direct first
    data = get_stock_data(user_input)
    
    if not data or data['price'] == 'N/A':
        # Try searching by name
        matches = search_stocks_by_name(user_input)
        
        if not matches:
            await update.message.reply_text(
                "Stock not found.\n\nTry typing the company name like:\nTata Motors\nHDFC Bank\nReliance\nInfosys"
            )
            return
        
        exact_matches = search_stocks_by_name_exact(user_input)
        if not exact_matches:
            await update.message.reply_text(
                f'Did you mean one of these for "{clean_company_query(user_input)}"? Please choose:',
                reply_markup=create_suggestion_keyboard(matches)
            )
            return

        if len(matches) == 1:
            # Exact company/ticker match — analyze directly
            symbol = matches[0][0]
            await update.message.reply_text(f"Analyzing {matches[0][1]}... please wait")
            data = get_stock_data(symbol)
            if not data or data['price'] == 'N/A':
                await update.message.reply_text("Could not fetch data. Please try again.")
                return
            chart = generate_chart(data['symbol'], data['name'])
            if chart:
                await update.message.reply_photo(photo=chart)
            analysis = analyze_stock(data, language)
            try:
                await update.message.reply_text(analysis, parse_mode='Markdown')
            except Exception:
                await update.message.reply_text(analysis)
        else:
            # Multiple matches — show buttons
            keyboard = create_stock_keyboard(matches)
            await update.message.reply_text(
                f"Found {len(matches)} stocks matching '{user_input}'.\nWhich one do you want?",
                reply_markup=keyboard
            )
        return
    
    # Direct match found
    await update.message.reply_text(f"Analyzing {user_input}... please wait")
    chart = generate_chart(data['symbol'], data['name'])
    if chart:
        await update.message.reply_photo(photo=chart)
    analysis = analyze_stock(data, language)
    try:
        await update.message.reply_text(analysis, parse_mode='Markdown')
    except Exception:
        await update.message.reply_text(analysis)
    
    if get_user_voice(update.message.chat_id):
        voice_buf = await text_to_speech(analysis, language)
        if voice_buf:
            await update.message.reply_voice(voice=voice_buf)
async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    current = get_user_voice(chat_id)
    new_state = not current
    set_user_voice(chat_id, new_state)
    
    if new_state:
        await update.message.reply_text(
            "Voice is now ON. I will send a voice note with every analysis.\n\nSend /voice again to turn it off."
        )
    else:
        await update.message.reply_text(
            "Voice is now OFF.\n\nSend /voice to turn it back on."
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to StockLens\n"
        "Your AI-powered Indian Stock Market Assistant\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "WHAT I CAN DO:\n\n"
        "1. STOCK ANALYSIS\n"
        "Just type a company name or symbol — no ticker needed\n"
        "Examples: TCS, Tata Motors, HDFC Bank, ITC\nIf unclear, I will suggest matching companies.\n\n"
        "2. COMPARE TWO STOCKS\n"
        "Example: TCS vs INFY\n\n"
        "3. PORTFOLIO ANALYSIS\n"
        "Send multiple stocks with commas\n"
        "Example: TCS, INFY, HDFCBANK, RELIANCE\n\n"
        "4. MUTUAL FUND ANALYSIS\n"
        "/mf Parag Parikh Flexi Cap\n"
        "/mf Mirae Asset Large Cap\n"
        "/mf SBI Small Cap\n\n"
        "5. TRENDING STOCKS\n"
        "/trending\n\n"
        "6. STOCK NEWS\n"
        "/news TCS\n\n"
        "7. DAILY MORNING ALERTS\n"
        "/subscribe TCS RELIANCE\n"
        "/unsubscribe TCS\n"
        "/mysubs\n\n"
        "8. LANGUAGE SETTINGS\n"
        "/language\n"
        "Options: English, Hinglish, Hindi, Marathi\n\n"
        "9. SEARCH STOCKS\n"
        "/search tata — find any stock by name\n\n"
        "10. VOICE MODE\n"
        "/voice - toggle voice on or off\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "/help - See all commands anytime\n\n"
        "Type any stock name to get started."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How to use StockLens:\n\n"
        "1. SINGLE STOCK\nExample: TCS or Tata Motors\n\n"
        "2. COMPARISON\nExample: TCS vs INFY\n\n"
        "3. PORTFOLIO\nExample: TCS, INFY, HDFCBANK\n\n"
        "4. MUTUAL FUND\n/mf Parag Parikh Flexi Cap\n\n"
        "5. DAILY UPDATES\n/subscribe TCS RELIANCE\n/unsubscribe TCS\n/mysubs\n\n"
        "6. TRENDING\n/trending\n\n"
        "7. NEWS\n/news TCS\n\n"
        "8. LANGUAGE\n/language\n\n"
        "10. VOICE MODE\n"
        "/voice - toggle voice on or off\n\n"
        "/start - Welcome\n/help - This message"
    )

def main():
    async def post_init(application):
        scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Kolkata"))
        scheduler.add_job(
            send_morning_briefs,
            trigger="cron",
            hour=9,
            minute=0,
            args=[application],
            id="daily_morning_briefs",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        application.bot_data["scheduler"] = scheduler
        logging.info("Daily morning brief scheduler started at 09:00 Asia/Kolkata")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("voice", voice_command))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("mysubs", my_subscriptions))
    app.add_handler(CommandHandler("trending", trending_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("mf", mf_command))
    app.add_handler(CommandHandler("lang_english", set_english))
    app.add_handler(CommandHandler("lang_hinglish", set_hinglish))
    app.add_handler(CommandHandler("lang_hindi", set_hindi))
    app.add_handler(CommandHandler("lang_marathi", set_marathi))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
