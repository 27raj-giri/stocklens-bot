import os
import yfinance as yf
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import logging

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

def get_stock_data(symbol):
    try:
        # Add .NS for Indian stocks (NSE)
        stock = yf.Ticker(symbol + ".NS")
        info = stock.info

        data = {
            "name": info.get("longName", symbol),
            "price": info.get("currentPrice", "N/A"),
            "pe_ratio": info.get("trailingPE", "N/A"),
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "market_cap": info.get("marketCap", "N/A"),
            "revenue_growth": info.get("revenueGrowth", "N/A"),
            "profit_margins": info.get("profitMargins", "N/A"),
            "debt_to_equity": info.get("debtToEquity", "N/A"),
            "roe": info.get("returnOnEquity", "N/A"),
            "sector": info.get("sector", "N/A"),
            "summary": info.get("longBusinessSummary", "N/A")
        }
        return data
    except Exception as e:
        return None

def analyze_stock(data):
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
- Business: {data['summary'][:300]}

Give analysis in this EXACT format:

STOCK ANALYSIS - {data['name']}

WHAT DOES THIS COMPANY DO?
(Explain in 1-2 lines like you are telling a friend. No jargon.)

WHERE IS THE STOCK RIGHT NOW?
(Explain current price vs 52 week high and low in simple words. Example: "Stock is near its lowest point this year" or "Stock is close to its peak". Also explain if it is cheap or expensive based on PE ratio in simple words.)

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip().upper()
    
    await update.message.reply_text(f"🔍 Analyzing {user_input}... please wait")
    
    data = get_stock_data(user_input)
    
    if not data or data['price'] == 'N/A':
        await update.message.reply_text(
            "❌ Stock not found. Please enter NSE symbol only.\n\nExamples:\nTCS\nHDFCBANK\nRELIANCE\nINFY"
        )
        return
    
    analysis = analyze_stock(data)
    await update.message.reply_text(analysis)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to *StockLens*\n\nI analyze Indian stocks using real market data and AI.\n\nJust send any NSE symbol to get started.\n\n*Popular Symbols:*\nTCS, RELIANCE, HDFCBANK\nINFY, WIPRO, BAJFINANCE\nADANIPORTS, SUNPHARMA\nTATAMOTORS, ICICIBANK\nLT, AXISBANK, KOTAKBANK\n\n*Tip:* Always use NSE symbol, not company name.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*How to use StockLens:*\n\n1. Send NSE stock symbol\n   Example: TCS, RELIANCE, INFY\n\n2. Wait 10-15 seconds\n\n3. Get full AI analysis\n\n*Common mistakes:*\nTATA MOTORS should be TATAMOTORS\nHDFC should be HDFCBANK\n\n/start - Welcome message\n/help - This message",
        parse_mode='Markdown'
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running...")
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.run_polling()

if __name__ == "__main__":
    main()
