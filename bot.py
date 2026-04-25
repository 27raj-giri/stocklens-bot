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
You are a sharp Indian stock market analyst who explains stocks to retail investors in simple language.

Here is the stock data:
- Company: {data['name']}
- Current Price: ₹{data['price']}
- P/E Ratio: {data['pe_ratio']}
- 52 Week High: ₹{data['52w_high']}
- 52 Week Low: ₹{data['52w_low']}
- Market Cap: {data['market_cap']}
- Revenue Growth: {data['revenue_growth']}
- Profit Margins: {data['profit_margins']}
- Debt to Equity: {data['debt_to_equity']}
- Return on Equity: {data['roe']}
- Sector: {data['sector']}
- Business: {data['summary'][:300]}

Give analysis in this EXACT format:

📊 *STOCK ANALYSIS*

🏢 *Company:* (one line what the company does)

📈 *Current Situation:* (2-3 lines, where is the stock vs 52w high/low, is it expensive or cheap based on PE)

💪 *Strengths:* (2-3 bullet points based on data)

⚠️ *Risks:* (2-3 bullet points based on data)

🎯 *Verdict:* (ONE of these: LOOKS INTERESTING / WAIT AND WATCH / AVOID FOR NOW)

📝 *In Simple Words:* (2 lines in easy Hindi-English mix explaining what a beginner should understand)

Keep it sharp. No generic advice. Be decisive. Never say "consult a financial advisor".
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
    await update.message.reply_text(analysis, parse_mode='MarkdownV2')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to StockLens!\n\nSend any NSE stock symbol to get AI analysis.\n\nExamples:\nTCS\nRELIANCE\nHDFCBANK\nINFY\nWIPRO"
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running...")
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

if __name__ == "__main__":
    main()
