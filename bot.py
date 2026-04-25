import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import os
import re
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
    
    attempts = [
        cleaned,
        cleaned + "LTD",
        cleaned[:-3] if len(cleaned) > 3 else cleaned,
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
        print(f"Error fetching data: {e}")
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

def escape_markdown(text):
    # Escape special characters for Markdown but preserve * for bold
    escape_chars = ['_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_with_bold_headings(text):
    lines = text.split('\n')
    new_lines = []
    
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
    
    for line in lines:
        stripped = line.strip()
        
        # Handle STOCK ANALYSIS line
        if stripped.upper().startswith("STOCK ANALYSIS"):
            line = f"*STOCK ANALYSIS*{stripped[14:]}"
        else:
            # Check if line contains any heading (case insensitive)
            matched = False
            for heading in headings:
                if heading in stripped.upper():
                    # Replace preserving original case but wrapping in bold
                    line = f"*{stripped}*"
                    matched = True
                    break
        
        new_lines.append(line)
    
    return '\n'.join(new_lines)

def generate_chart(symbol, company_name):
    try:
        stock = yf.Ticker(symbol + ".NS")
        hist = stock.history(period="6mo")
        
        if hist.empty:
            return None
        
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # Color line green if price went up, red if down
        start_price = hist['Close'].iloc[0]
        end_price = hist['Close'].iloc[-1]
        color = '#2ecc71' if end_price >= start_price else '#e74c3c'
        
        ax.plot(hist.index, hist['Close'], color=color, linewidth=2)
        ax.fill_between(hist.index, hist['Close'], alpha=0.1, color=color)
        
        # Styling
        ax.set_title(f"{company_name} - 6 Month Price Chart", fontsize=14, fontweight='bold', pad=15)
        ax.set_xlabel("")
        ax.set_ylabel("Price (INR)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        # Format x-axis dates
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.xticks(rotation=45)
        
        # Add current price annotation
        ax.annotate(f'Current: ₹{end_price:.1f}',
                   xy=(hist.index[-1], end_price),
                   xytext=(-80, 15),
                   textcoords='offset points',
                   fontsize=10,
                   fontweight='bold',
                   color=color)
        
        plt.tight_layout()
        
        # Save to bytes instead of file
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        return buf
    except Exception as e:
        print(f"Chart error: {e}")
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

Give analysis in this EXACT format. Do not add any extra text outside this format:

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
- Always leave a blank line between the verdict word and the Hinglish explanation
"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        raw = response.choices[0].message.content
        formatted = format_with_bold_headings(raw)
        return formatted
    except Exception as e:
        print(f"Groq error: {e}")
        return f"Analysis failed: {e}"

def analyze_comparison(data1, data2):
    try:
        prompt = f"""
You are a stock market expert comparing two Indian stocks for a complete beginner.
Explain everything in simple language, no jargon.

Stock 1: {data1['name']}
- Price: {data1['price']}
- PE Ratio: {data1['pe_ratio']}
- Revenue Growth: {data1['revenue_growth']}
- Profit Margins: {data1['profit_margins']}
- Debt to Equity: {data1['debt_to_equity']}
- Return on Equity: {data1['roe']}
- 52 Week High: {data1['52w_high']}
- 52 Week Low: {data1['52w_low']}

Stock 2: {data2['name']}
- Price: {data2['price']}
- PE Ratio: {data2['pe_ratio']}
- Revenue Growth: {data2['revenue_growth']}
- Profit Margins: {data2['profit_margins']}
- Debt to Equity: {data2['debt_to_equity']}
- Return on Equity: {data2['roe']}
- 52 Week High: {data2['52w_high']}
- 52 Week Low: {data2['52w_low']}

Give comparison in this EXACT format:

STOCK COMPARISON: {data1['name']} vs {data2['name']}

WHAT DO THESE COMPANIES DO?
{data1['name']}: (1 line simple explanation)
{data2['name']}: (1 line simple explanation)

PRICE & VALUE:
(Compare current prices and PE ratios in simple words. Which one is cheaper relative to its earnings?)

GROWTH & PROFITABILITY:
(Compare revenue growth and profit margins simply. Which one is growing faster and keeping more money?)

RISK LEVEL:
(Compare debt to equity. Which one has borrowed more money and is therefore riskier?)

OVERALL WINNER:
(Choose one stock as the better option right now and explain why in 2-3 lines in simple Hinglish)

Rules:
- No emojis
- No jargon without explanation
- Be decisive, pick a winner
- Never say consult a financial advisor
"""
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Comparison error: {e}")
        return f"Comparison failed: {e}"

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
        matched = False
        for heading in headings:
            if heading in stripped.upper():
                line = f"*{stripped}*"
                matched = True
                break
        new_lines.append(line)
    return '\n'.join(new_lines)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    print(f"Received: {user_input}")

    # Detect comparison mode
    comparison_keywords = [' vs ', ' VS ', ' Vs ', ' versus ']
    is_comparison = any(kw in user_input for kw in comparison_keywords)

    if is_comparison:
        # Split into two stocks
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
            await update.message.reply_text(f"Could not find data for {stock1}. Please check the symbol.")
            return
        
        if not data2 or data2['price'] == 'N/A':
            await update.message.reply_text(f"Could not find data for {stock2}. Please check the symbol.")
            return

        # Generate comparison charts side by side
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

    # Normal single stock analysis
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
        "Welcome to StockLens\n\nI analyze Indian stocks using real market data and AI.\n\nYou can send:\n- NSE symbol: TCS, RELIANCE, HDFCBANK\n- Company name: Infosys, Tata Motors, HDFC Bank\n\nJust type any stock name or symbol to get started."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How to use StockLens:\n\n1. Send any stock name or NSE symbol\n2. Wait 10-15 seconds\n3. Get full AI analysis\n\nExamples:\nTCS\nInfosys\nTata Motors\nHDFC Bank\nReliance\n\n/start - Welcome message\n/help - This message"
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
