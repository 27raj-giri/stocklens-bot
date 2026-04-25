import os
import re
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
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def resolve_symbol(user_input):
    cleaned = user_input.strip().upper().replace(" ", "")
    
    # Just return the cleaned symbol directly
    # We'll validate in get_stock_data using history instead
    return cleaned

def get_stock_data(user_input):
    try:
        resolved = resolve_symbol(user_input)
        stock = yf.Ticker(resolved + ".NS")
        info = stock.info
        
        # Use regularMarketPrice or currentPrice or fall back to history
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
        stock = yf.Ticker(symbol + ".NS")
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
        stock = yf.Ticker(symbol + ".NS")
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
    headings = [
        "WHAT DOES THIS COMPANY DO",
        "WHERE IS THE STOCK RIGHT NOW",
        "WHY THIS STOCK LOOKS GOOD",
        "WHAT COULD GO WRONG",
        "FINAL VERDICT",
        "LOOKS INTERESTING",
        "WAIT AND WATCH",
        "AVOID FOR NOW",
    ]
    lines = text.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("STOCK ANALYSIS"):
            line = f"*STOCK ANALYSIS*{stripped[14:]}"
        else:
            for heading in headings:
                if heading in stripped.upper():
                    line = f"*{stripped}*"
                    break
        new_lines.append(line)
    return '\n'.join(new_lines)

def format_comparison_headings(text):
    headings = [
        "WHAT DO THESE COMPANIES DO",
        "PRICE & VALUE",
        "GROWTH & PROFITABILITY",
        "RISK LEVEL",
        "OVERALL WINNER",
        "STOCK COMPARISON",
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

def analyze_stock(data, language='hinglish'):
    try:
        sector_info = get_sector_context(data['industry'], data['pe_ratio'], data['symbol'])
        sector_line = f"- Sector Context: {sector_info}" if sector_info else ""
        prompt = f"""
You are a stock market expert who explains stocks to complete beginners in India.
Your job is to make complex financial data easy to understand for someone who has never studied finance.

Here is the stock data:
- Company: {data['name']}
- Current Price: {data['price']}
- P/E Ratio: {data['pe_ratio']}
- 52 Week High: {data['52w_high']}
- 52 Week Low: {data['52w_low']}
- Market Cap: {data['market_cap']}
- Revenue Growth: {data['revenue_growth']}
- Profit Margins: {data['profit_margins']}
- Debt to Equity: {data['debt_to_equity']}
- Return on Equity: {data['roe']}
- Sector: {data['sector']}
{sector_line}
- Business: {data['summary'][:300]}

Give analysis in this EXACT format:

STOCK ANALYSIS - {data['name']}

WHAT DOES THIS COMPANY DO?
(Explain in 1-2 lines like you are telling a friend. No jargon.)

WHERE IS THE STOCK RIGHT NOW?
(Explain current price vs 52 week high and low in simple words. Also explain if it is cheap or expensive compared to its sector peers using the sector context data provided.)

WHY THIS STOCK LOOKS GOOD:
- (Point 1 in very simple language, explain what the number means, not just the number)
- (Point 2 in very simple language)
- (Point 3 in very simple language)

WHAT COULD GO WRONG:
- (Risk 1 explained simply)
- (Risk 2 explained simply)
- (Risk 3 explained simply)

FINAL VERDICT:
LOOKS INTERESTING

(Then write 2-3 lines explaining the verdict in simple Hinglish that a beginner will understand. This explanation must be on a new line after a blank line.)

Rules:
- Never use financial jargon without explaining it
- Write like you are explaining to a friend who knows nothing about stocks
- Be decisive, give a clear opinion
- No emojis
- Never say consult a financial advisor
- Always leave a blank line between the verdict word and the explanation
- {get_language_prompt(language)}
"""
        model_to_use = "gemma2-9b-it" if language in ['hindi', 'marathi'] else "llama-3.3-70b-versatile"
        response = groq_client.chat.completions.create(
        model=model_to_use,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        raw = response.choices[0].message.content
        formatted = format_with_bold_headings(raw)
        return formatted
    except Exception as e:
        print(f"Groq error: {e}")
        return f"Analysis failed: {e}"

def analyze_comparison(data1, data2, language='hinglish'):
    try:
        prompt = f"""
You are a stock market expert comparing two Indian stocks for a complete beginner.
Explain everything in simple language, no jargon.

Stock 1: {data1['name']}
- Price: {data1['price']}, PE: {data1['pe_ratio']}, Sector: {data1['sector']}
- Revenue Growth: {data1['revenue_growth']}, Profit Margins: {data1['profit_margins']}
- Debt to Equity: {data1['debt_to_equity']}, ROE: {data1['roe']}
- 52W High: {data1['52w_high']}, 52W Low: {data1['52w_low']}

Stock 2: {data2['name']}
- Price: {data2['price']}, PE: {data2['pe_ratio']}, Sector: {data2['sector']}
- Revenue Growth: {data2['revenue_growth']}, Profit Margins: {data2['profit_margins']}
- Debt to Equity: {data2['debt_to_equity']}, ROE: {data2['roe']}
- 52W High: {data2['52w_high']}, 52W Low: {data2['52w_low']}

Give comparison in this EXACT format:

STOCK COMPARISON: {data1['name']} vs {data2['name']}

WHAT DO THESE COMPANIES DO?
{data1['name']}: (1 line simple explanation)
{data2['name']}: (1 line simple explanation)

PRICE & VALUE:
(Compare PE ratios simply. Which one is cheaper relative to earnings?)

GROWTH & PROFITABILITY:
(Compare revenue growth and margins simply. Which is growing faster?)

RISK LEVEL:
(Compare debt. Which one has borrowed more and is riskier?)

OVERALL WINNER:
(Pick one stock. Explain why in 2-3 lines.)

Rules:
- No emojis
- Simple language only
- Be decisive, pick a winner
- Never say consult a financial advisor
- {get_language_prompt(language)}
"""
        model_to_use = "gemma2-9b-it" if language in ['hindi', 'marathi'] else "llama-3.3-70b-versatile"
        response = groq_client.chat.completions.create(
        model=model_to_use,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Comparison error: {e}")
        return f"Comparison failed: {e}"

def analyze_portfolio(stocks_data, language='hinglish'):
    try:
        stocks_summary = ""
        for d in stocks_data:
            stocks_summary += f"""
- {d['name']} ({d['symbol']}):
  Price: {d['price']}, PE: {d['pe_ratio']}, Sector: {d['sector']}
  Revenue Growth: {d['revenue_growth']}, Profit Margins: {d['profit_margins']}
  Debt to Equity: {d['debt_to_equity']}, ROE: {d['roe']}
"""
        sectors = [d['sector'] for d in stocks_data if d['sector'] != 'N/A']
        sector_counts = {}
        for s in sectors:
            sector_counts[s] = sector_counts.get(s, 0) + 1
        sector_summary = ", ".join([f"{k}: {v} stock(s)" for k, v in sector_counts.items()])

        prompt = f"""
You are a stock market expert reviewing a beginner investor's portfolio in India.
Explain everything in very simple language. No jargon.

The investor holds these stocks:
{stocks_summary}

Sector breakdown: {sector_summary}

Give portfolio analysis in this EXACT format:

PORTFOLIO ANALYSIS

WHAT YOU OWN:
(List each stock in 1 line - what the company does in simplest words)

SECTOR CONCENTRATION:
(Are they too focused in one sector? Explain diversification simply.)

STRONGEST STOCK:
(Which stock looks best? Say why simply.)

WEAKEST STOCK:
(Which stock looks worst or riskiest? Say why simply.)

OVERALL PORTFOLIO HEALTH:
(Rate as STRONG / MODERATE / NEEDS ATTENTION)
(Explain in 3-4 lines.)

Rules:
- No emojis
- Simple language only
- Be decisive
- Never say consult a financial advisor
- {get_language_prompt(language)}
"""
        model_to_use = "gemma2-9b-it" if language in ['hindi', 'marathi'] else "llama-3.3-70b-versatile"
        response = groq_client.chat.completions.create(
        model=model_to_use,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Portfolio error: {e}")
        return f"Portfolio analysis failed: {e}"

async def send_morning_briefs(app):
    print("Sending morning briefs...")
    try:
        result = supabase.table("subscriptions").select("*").execute()
        subscriptions = result.data
        
        # Group by chat_id
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

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    # Get full text after /subscribe
    full_text = update.message.text.replace('/subscribe', '').strip()
    
    if not full_text:
        await update.message.reply_text(
            "Please send stock symbols after /subscribe\n\nExample: /subscribe TCS RELIANCE INFY"
        )
        return
    
    # Split by space or comma
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
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "Please send stock symbols after /unsubscribe\n\nExample: /unsubscribe TCS RELIANCE"
        )
        return
    
    removed = []
    for symbol in args:
        symbol = symbol.upper().strip()
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

# Nifty 50 stocks for trending
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
                stock = yf.Ticker(symbol + ".NS")
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
        
        # Sort by absolute % change
        movers.sort(key=lambda x: abs(x['change_pct']), reverse=True)
        return movers[:5]
    except Exception as e:
        print(f"Trending error: {e}")
        return None

def get_stock_news(symbol, company_name):
    try:
        stock = yf.Ticker(symbol + ".NS")
        news = stock.news
        
        if not news:
            return None
        
        articles = []
        for item in news[:5]:
            content = item.get('content', {})
            title = content.get('title', '')
            summary = content.get('summary', '')
            source = content.get('provider', {}).get('displayName', '')
            pub_date = content.get('pubDate', '')
            
            if title:
                articles.append({
                    "title": title,
                    "summary": summary[:200] if summary else '',
                    "source": source,
                    "date": pub_date[:10] if pub_date else ''
                })
        
        return articles if articles else None
    except Exception as e:
        print(f"News error: {e}")
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
    args = update.message.text.replace('/news', '').strip()
    
    if not args:
        await update.message.reply_text(
            "Please send a stock symbol after /news\n\nExample: /news TCS"
        )
        return
    
    await update.message.reply_text(f"Fetching news for {args}... please wait")
    
    data = get_stock_data(args)
    if not data or data['price'] == 'N/A':
        await update.message.reply_text("Stock not found. Please check the symbol.")
        return
    
    articles = get_stock_news(data['symbol'], data['name'])
    
    if not articles:
        await update.message.reply_text(f"No recent news found for {data['name']}.")
        return
    
    message = f"LATEST NEWS: {data['name']}\n\n"
    for i, article in enumerate(articles, 1):
        message += f"{i}. {article['title']}\n"
        if article['summary']:
            message += f"{article['summary'][:150]}...\n"
        if article['source']:
            message += f"Source: {article['source']}"
        if article['date']:
            message += f" | {article['date']}"
        message += "\n\n"
    
    await update.message.reply_text(message)

def load_amfi_funds():
    try:
        data = urllib.request.urlopen('https://www.amfiindia.com/spages/NAVAll.txt').read().decode('utf-8')
        lines = data.split('\n')
        funds = []
        for line in lines:
            parts = line.strip().split(';')
            if len(parts) == 6:
                funds.append({
                    'code': parts[0].strip(),
                    'name': parts[3].strip(),
                    'nav': parts[4].strip(),
                    'date': parts[5].strip()
                })
        return funds
    except Exception as e:
        print(f"AMFI load error: {e}")
        return []

def search_fund(query, funds):
    query_lower = query.lower()
    words = query_lower.split()
    
    # Score each fund by how many words match
    scored = []
    for fund in funds:
        name_lower = fund['name'].lower()
        score = sum(1 for word in words if word in name_lower)
        if score > 0:
            scored.append((score, fund))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Prefer Growth plans
    growth_first = [f for _, f in scored if 'growth' in f['name'].lower()]
    others = [f for _, f in scored if 'growth' not in f['name'].lower()]
    
    return (growth_first + others)[:1]

def get_fund_returns(scheme_code):
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        data = json.loads(urllib.request.urlopen(url).read())
        nav_data = data['data']
        
        if len(nav_data) < 2:
            return None
        
        current_nav = float(nav_data[0]['nav'])
        
        returns = {}
        
        # 1 month return
        if len(nav_data) >= 30:
            nav_1m = float(nav_data[29]['nav'])
            returns['1m'] = round(((current_nav - nav_1m) / nav_1m) * 100, 2)
        
        # 6 month return
        if len(nav_data) >= 180:
            nav_6m = float(nav_data[179]['nav'])
            returns['6m'] = round(((current_nav - nav_6m) / nav_6m) * 100, 2)
        
        # 1 year return
        if len(nav_data) >= 365:
            nav_1y = float(nav_data[364]['nav'])
            returns['1y'] = round(((current_nav - nav_1y) / nav_1y) * 100, 2)
        
        # 3 year return
        if len(nav_data) >= 1095:
            nav_3y = float(nav_data[1094]['nav'])
            returns['3y'] = round(((current_nav - nav_3y) / nav_3y) * 100, 2)
        
        return returns
    except Exception as e:
        print(f"Fund returns error: {e}")
        return None

def generate_fund_chart(scheme_code, fund_name):
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        data = json.loads(urllib.request.urlopen(url).read())
        nav_data = data['data'][:365]  # Last 1 year
        nav_data.reverse()  # Oldest first
        
        dates = [item['date'] for item in nav_data]
        navs = [float(item['nav']) for item in nav_data]
        
        from datetime import datetime
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

def analyze_mutual_fund(fund, returns, meta, language='hinglish'):
    try:
        returns_text = ""
        if '1m' in returns:
            returns_text += f"- 1 Month Return: {returns['1m']}%\n"
        if '6m' in returns:
            returns_text += f"- 6 Month Return: {returns['6m']}%\n"
        if '1y' in returns:
            returns_text += f"- 1 Year Return: {returns['1y']}%\n"
        if '3y' in returns:
            returns_text += f"- 3 Year Return: {returns['3y']}%\n"

        prompt = f"""
You are a mutual fund expert explaining funds to complete beginners in India.
Explain everything in very simple language. No jargon.

Fund Details:
- Fund Name: {fund['name']}
- Fund House: {meta.get('fund_house', 'N/A')}
- Category: {meta.get('scheme_category', 'N/A')}
- Type: {meta.get('scheme_type', 'N/A')}
- Current NAV: {fund['nav']}
- NAV Date: {fund['date']}
{returns_text}

Give analysis in this EXACT format:

MUTUAL FUND ANALYSIS - {fund['name'][:50]}

WHAT IS THIS FUND?
(Explain in 1-2 lines what kind of fund this is and what it invests in. No jargon.)

HOW HAS IT PERFORMED?
(Explain the returns in simple words. Is 1 year return good or bad? Compare to FD rate of 7% as benchmark.)

WHO SHOULD INVEST?
(What kind of person is this fund good for?)

RISK LEVEL:
(LOW / MEDIUM / HIGH - explain why simply.)

FINAL VERDICT:
(LOOKS GOOD / AVERAGE / NOT RECOMMENDED)
(Then write 2-3 lines explaining the verdict.)

Rules:
- No emojis
- Simple language only
- Compare returns to FD 7% as reference
- Never say consult a financial advisor
- {get_language_prompt(language)}
"""
        model_to_use = "gemma2-9b-it" if language in ['hindi', 'marathi'] else "llama-3.3-70b-versatile"
        response = groq_client.chat.completions.create(
        model=model_to_use,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Fund analysis error: {e}")
        return f"Analysis failed: {e}"

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

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    current = get_user_language(chat_id)
    
    await update.message.reply_text(
        f"Current language: {current.upper()}\n\n"
        "Choose your preferred language by sending the command:\n\n"
        "/lang_english - English\n"
        "Example: 'This company provides IT services globally.'\n\n"
        "/lang_hinglish - Hinglish (default)\n"
        "Example: 'Yeh stock accha hai because growth strong hai.'\n\n"
        "/lang_hindi - Hindi\n"
        "Example: 'Yeh company bahut acchi hai aur iski growth strong hai.'\n\n"
        "/lang_marathi - Marathi\n"
        "Example: 'Hi company khup changli aahe aur tichi growth strong aahe.'"
    )

async def set_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'english')
    await update.message.reply_text("Language set to English. All analysis will now be in English.")

async def set_hinglish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'hinglish')
    await update.message.reply_text("Language set to Hinglish. Bilkul sahi choice hai!")

async def set_hindi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'hindi')
    await update.message.reply_text("Language Hindi set ho gayi. Ab sab analysis Hindi mein aayega.")

async def set_marathi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_language(update.message.chat_id, 'marathi')
    await update.message.reply_text("Bhasha Marathi set zali. Aata sagli analysis Marathi madhe yeil.")

async def mf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.replace('/mf', '').strip()
    
    if not query:
        await update.message.reply_text(
            "Please send a fund name after /mf\n\nExamples:\n/mf Parag Parikh Flexi Cap\n/mf Mirae Asset Large Cap\n/mf SBI Small Cap\n/mf Axis Bluechip"
        )
        return
    
    await update.message.reply_text(f"Searching for {query}... please wait")
    
    funds = load_amfi_funds()
    if not funds:
        await update.message.reply_text("Could not load fund data. Try again later.")
        return
    
    results = search_fund(query, funds)
    if not results:
        await update.message.reply_text(
            f"No fund found for '{query}'.\n\nTry more specific names:\n/mf Parag Parikh Flexi Cap\n/mf Mirae Asset Large Cap\n/mf SBI Small Cap"
        )
        return
    
    fund = results[0]
    await update.message.reply_text(f"Found: {fund['name']}\nFetching analysis...")
    
    # Get historical data
    returns = get_fund_returns(fund['code'])
    
    # Get meta
    try:
        url = f"https://api.mfapi.in/mf/{fund['code']}"
        meta_data = json.loads(urllib.request.urlopen(url).read())
        meta = meta_data.get('meta', {})
    except:
        meta = {}
    
    # Generate chart
    chart = generate_fund_chart(fund['code'], fund['name'])
    if chart:
        await update.message.reply_photo(photo=chart)
    
    # Generate analysis
    language = get_user_language(update.message.chat_id)
    analysis = analyze_mutual_fund(fund, returns or {}, meta, language)
    formatted = format_fund_headings(analysis)
    
    try:
        await update.message.reply_text(formatted, parse_mode='Markdown')
    except Exception:
        await update.message.reply_text(analysis)

def get_user_language(chat_id):
    try:
        result = supabase.table("user_preferences").select("language").eq("chat_id", chat_id).execute()
        if result.data:
            return result.data[0]['language']
        return 'hinglish'  # default
    except:
        return 'hinglish'

def set_user_language(chat_id, language):
    try:
        result = supabase.table("user_preferences").upsert({
            "chat_id": chat_id,
            "language": language,
            "updated_at": "now()"
        }).execute()
        print(f"Language saved: {language} for {chat_id}, result: {result}")
        return True
    except Exception as e:
        print(f"Language set error: {e}")
        return False

def get_language_prompt(language):
    prompts = {
        'english': "IMPORTANT: Write the ENTIRE response in simple English only. No Hindi or regional language words at all.",
        'hinglish': "IMPORTANT: Write in Hinglish - naturally mix Hindi and English words together. Hindi words in Roman script. Example: 'Yeh stock accha hai because company ki growth strong hai aur profit margins bhi achhe hain.'",
        'hindi': "IMPORTANT: Write the ENTIRE response in pure Hindi using Devanagari script (हिंदी में लिखें). Every single word must be in Hindi Devanagari script. Only keep company names, numbers, and financial terms like PE, NAV, ROE in English. Example: 'यह कंपनी बहुत अच्छी तरह से काम करती है। इसकी आमदनी में लगातार बढ़ोतरी हो रही है। निवेशकों के लिए यह एक अच्छा विकल्प हो सकता है।'",
        'marathi': "IMPORTANT: Write the ENTIRE response in pure Marathi using Devanagari script (मराठीत लिहा). Every single word must be in Marathi Devanagari script. Only keep company names, numbers, and financial terms like PE, NAV, ROE in English. Example: 'ही कंपनी खूप चांगली आहे. तिचे उत्पन्न सतत वाढत आहे. गुंतवणूकदारांसाठी हा एक चांगला पर्याय असू शकतो.'"
    }
    return prompts.get(language, prompts['hinglish'])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    language = get_user_language(update.message.chat_id)
    print(f"User language: {language} for chat_id: {update.message.chat_id}")

    user_input = update.message.text.strip()
    print(f"Received: {user_input}")

    # Detect portfolio mode
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
        analysis = analyze_portfolio(stocks_data)
        formatted = format_portfolio_headings(analysis)
        try:
            await update.message.reply_text(formatted, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(analysis)
        return

    # Detect comparison mode
    comparison_keywords = [' vs ', ' VS ', ' Vs ', ' versus ']
    is_comparison = any(kw in user_input for kw in comparison_keywords)

    if is_comparison:
        for kw in comparison_keywords:
            if kw in user_input:
                parts = user_input.split(kw)
                break
        stock1 = parts[0].strip()
        stock2 = parts[1].strip()
        await update.message.reply_text(f"Comparing {stock1} vs {stock2}... please wait")
        data1 = get_stock_data(stock1)
        data2 = get_stock_data(stock2)
        if not data1 or data1['price'] == 'N/A':
            await update.message.reply_text(f"Could not find data for {stock1}.")
            return
        if not data2 or data2['price'] == 'N/A':
            await update.message.reply_text(f"Could not find data for {stock2}.")
            return
        chart1 = generate_chart(data1['symbol'], data1['name'])
        chart2 = generate_chart(data2['symbol'], data2['name'])
        if chart1:
            await update.message.reply_photo(photo=chart1, caption=data1['name'])
        if chart2:
            await update.message.reply_photo(photo=chart2, caption=data2['name'])
        comparison = analyze_comparison(data1, data2)
        formatted = format_comparison_headings(comparison)
        try:
            await update.message.reply_text(formatted, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(comparison)
        return

    # Single stock analysis
    await update.message.reply_text(f"Analyzing {user_input}... please wait")
    data = get_stock_data(user_input)
    if not data or data['price'] == 'N/A':
        await update.message.reply_text(
            "Stock not found. Try these:\n\nSymbols: TCS, RELIANCE, HDFCBANK, INFY\nNames: Infosys, Tata Motors, HDFC Bank\n\nMake sure it is listed on NSE."
        )
        return
    chart = generate_chart(data['symbol'], data['name'])
    if chart:
        await update.message.reply_photo(photo=chart)
    analysis = analyze_stock(data)
    try:
        await update.message.reply_text(analysis, parse_mode='Markdown')
    except Exception:
        await update.message.reply_text(analysis)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to StockLens\n\nI analyze Indian stocks using real market data and AI.\n\n"
        "What you can do:\n"
        "- Single stock: TCS or Tata Motors\n"
        "- Compare: TCS vs INFY\n"
        "- Portfolio: TCS, INFY, HDFCBANK\n"
        "- Daily updates: /subscribe TCS RELIANCE\n\n"
        "Just type any stock name or symbol to get started."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How to use StockLens:\n\n"
        "1. SINGLE STOCK ANALYSIS\n"
        "Send any stock name or symbol\n"
        "Example: TCS or Tata Motors\n\n"
        "2. STOCK COMPARISON\n"
        "Example: TCS vs INFY\n\n"
        "3. PORTFOLIO ANALYSIS\n"
        "Example: TCS, INFY, HDFCBANK, RELIANCE\n\n"
        "4. DAILY MORNING UPDATES\n"
        "/subscribe TCS RELIANCE\n"
        "/unsubscribe TCS\n"
        "/mysubs - see your subscriptions\n\n"
        "5. TRENDING STOCKS\n"
        "/trending - top movers today\n\n"
        "6. STOCK NEWS\n"
        "/news TCS - latest news for any stock\n\n"
        "/start - Welcome message\n"
        "/help - This message"
        "7. MUTUAL FUND ANALYSIS\n"
        "/mf Parag Parikh Flexi Cap"
        "/mf Mirae Asset Large Cap"
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("mysubs", my_subscriptions))
    app.add_handler(CommandHandler("trending", trending_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("mf", mf_command))

    # Start scheduler inside the app's post_init
    async def post_init(application):
        scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Kolkata'))
        scheduler.add_job(send_morning_briefs, 'cron', hour=9, minute=0, args=[application])
        scheduler.start()
        print("Scheduler started")

    app.post_init = post_init

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
