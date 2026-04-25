import os
import yfinance as yf
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import logging

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

def resolve_symbol(user_input):
    cleaned = user_input.strip().upper().replace(" ", "")
    
    # List of suffixes to try
    attempts = [
        cleaned,           # ZOMATO
        cleaned + "LTD",   # ZOMATOLTD  
        cleaned[:-3] if len(cleaned) > 3 else cleaned,  # trim last 3
    ]
    
    for attempt in attempts:
        try:
            test = yf.Ticker(attempt + ".NS")
            info = test.info
            if info.get("currentPrice"):
                print(f"Found: {attempt}")
                return attempt
        except:
            continue
    
    # Try yfinance search as fallback
    try:
        search_results = yf.Search(user_input, max_results=10)
        quotes = search_results.quotes
        if quotes:
            for quote in quotes:
                symbol = quote.get("symbol", "")
                if ".NS" in symbol:
                    symbol = symbol.replace(".NS", "")
                    print(f"Search found: {symbol}")
                    return symbol
    except Exception as e:
        print(f"Search error: {e}")
    
    return cleaned

def get_stock_data(user_input):
    try:
        resolved = resolve_symbol(user_input)
        stock = yf.Ticker(resolved + ".NS")
        info = stock.info

        data = {
            "symbol": resolved,
            "name": info.get("longName", resolved),
            "price": info.get("currentPrice", "N/A"),
            "pe_ratio": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "market_cap": info.get("marketCap", "N/A"),
            "revenue_growth": info.get("revenueGrowth", "N/A"),
            "profit_margins": info.get("profitMargins", "N/A"),
            "debt_to_equity": info.get("debtToEquity", "N/A"),
            "roe": info.get("returnOnEquity", "N/A"),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "summary": info.get("longBusinessSummary", "N/A")
        }
        print(f"Data fetched: {data['name']} at {data['price']}")
        return data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None
# Sector peer mapping
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
    "Insurance": ["HDFCLIFE.NS", "SBILIFE.NS", "ICICIlombard.NS"],
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
            comparison = f"cheaper than sector average"
        elif current_pe > avg_pe:
            comparison = f"more expensive than sector average"
        else:
            comparison = f"in line with sector average"
        
        return f"Sector average PE is {avg_pe}. This stock is {comparison} (stock PE: {current_pe}, sector avg: {avg_pe})."
    
    except Exception as e:
        print(f"Sector context error: {e}")
        return None
    
def analyze_stock(data):
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
(Choose exactly one: LOOKS INTERESTING / WAIT AND WATCH / AVOID FOR NOW)
(Then write 2-3 lines explaining the verdict in simple Hinglish that a beginner will understand)

Rules:
- Never use financial jargon without explaining it
- Write like you are explaining to a friend who knows nothing about stocks
- Be decisive, give a clear opinion
- No emojis
- Never say consult a financial advisor
"""
        
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Groq error: {e}")
        return f"Analysis failed: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    print(f"Received: {user_input}")

    await update.message.reply_text(f"Analyzing {user_input}... please wait")

    data = get_stock_data(user_input)

    if not data or data['price'] == 'N/A':
        await update.message.reply_text(
            "Stock not found. Try these:\n\nSymbols: TCS, RELIANCE, HDFCBANK, INFY\nNames: Zomato, Infosys, Tata Motors\n\nMake sure it is listed on NSE."
        )
        return

    analysis = analyze_stock(data)
    await update.message.reply_text(analysis)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to StockLens\n\nI analyze Indian stocks using real market data and AI.\n\nYou can send:\n- NSE symbol: TCS, RELIANCE, HDFCBANK\n- Company name: Zomato, Infosys, Tata Motors\n\nJust type any stock name or symbol to get started."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How to use StockLens:\n\n1. Send any stock name or NSE symbol\n2. Wait 10-15 seconds\n3. Get full AI analysis\n\nExamples:\nTCS\nZomato\nTata Motors\nHDFC Bank\nReliance\n\n/start - Welcome message\n/help - This message"
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
