from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import json
import groq
from datetime import datetime
import time
import threading
from collections import defaultdict

app = Flask(__name__)
TELEGRAM_BOT_TOKEN = "7792519316:AAFup8FwxZugLA0-JhHAKzzi0j8Gvyg-9UI"
GROQ_API_KEY = "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"
DEX_SCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens"


def validate_address(address):
    """
    Validates XRP/Proton address format
    Returns True if address is valid, False otherwise
    """
    
    if not address:
        return False

    
    if len(address) < 25 or len(address) > 35:
        return False

    
    valid_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    return all(char in valid_chars for char in address)



token_cache = {}
wallet_cache = {}
CACHE_DURATION = 60  


price_alerts = defaultdict(list)  # token -> [(chat_id, price_target, above/below), ...]
watched_wallets = defaultdict(list)  # wallet -> [chat_id, ...]

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
client = groq.Client(api_key=GROQ_API_KEY)

def create_main_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔍 Track Token", callback_data="track_token"),
        InlineKeyboardButton("👛 Track Wallet", callback_data="track_wallet")
    )
    markup.row(
        InlineKeyboardButton("📈 Chart", callback_data="chart"),
        InlineKeyboardButton("🔔 Alert", callback_data="alert")
    )
    markup.row(
        InlineKeyboardButton("🤖 AI", callback_data="ai"),
        InlineKeyboardButton("👥 Holders", callback_data="holders")
    )
    markup.row(
        InlineKeyboardButton("📊 Menu", callback_data="main_menu")
    )
    return markup

def analyze_with_groq(token_info):
    try:
       
        token_address = token_info.split('\n')[0].split()[-1]  
        response = requests.get(f"{DEX_SCREENER_API_URL}/{token_address}")
        data = response.json()

        if not data.get("pairs"):
            return ("🔍 Token Analysis\n\n"
                   "❌ Insufficient trading data available for detailed analysis.\n\n"
                   "Possible reasons:\n"
                   "• New or recently listed token\n"
                   "• Low liquidity or trading volume\n"
                   "• Token not yet tracked by DEX Screener\n\n"
                   "Recommendations:\n"
                   "• Monitor liquidity development\n"
                   "• Check token contract verification\n"
                   "• Research token utility and team")

        pair = data["pairs"][0]
        price = float(pair.get('priceUsd', 0))
        volume_24h = float(pair.get('volume', {}).get('h24', 0))
        liquidity = float(pair.get('liquidity', {}).get('usd', 0))
        price_change = float(pair.get('priceChange', {}).get('h24', 0))

        analysis_prompt = f"""Analyze this cryptocurrency token data:
        Price: ${price}
        24h Volume: ${volume_24h}
        Liquidity: ${liquidity}
        24h Price Change: {price_change}%
        Chart: https://dexscreener.com/xrpl/{token_address}

        Provide a professional analysis focusing on:
        1. Current market performance
        2. Risk assessment based on liquidity/volume
        3. Short-term potential based on metrics
        4. Key risks and recommendations
        """

        prompt = f"""Analyze this cryptocurrency token data and provide insights:
        {token_info}

        Focus on:
        1. Market performance and momentum
        2. Risk assessment based on liquidity and volume
        3. Short-term price potential
        4. Key risks and recommendations

        Please provide a thorough analysis covering:
        1. Risk assessment (liquidity depth, volume/market cap ratio)
        2. Market sentiment (price action, buy/sell pressure)
        3. On-chain metrics analysis
        4. Trading recommendation with specific entry/exit points
        5. Key risk factors to consider
        """

        completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert cryptocurrency market analyst providing detailed, data-driven insights with specific actionable recommendations."
                },
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            max_tokens=750
        )

        return completion.choices[0].message.content
    except Exception as e:
        print(f"Error in AI analysis: {str(e)}")
        return "Unable to generate AI analysis at this time. Please try again later."

def check_price_alerts():
    while True:
        try:
            for token, alerts in price_alerts.items():
                current_price = get_token_price(token)
                if current_price:
                    for alert in alerts[:]: 
                        chat_id, target_price, condition = alert
                        if (condition == "above" and current_price > target_price) or \
                           (condition == "below" and current_price < target_price):
                            bot.send_message(
                                chat_id,
                                f"🚨 Price Alert! {token}\n"
                                f"Current price: ${current_price}\n"
                                f"Target price: ${target_price}"
                            )
                            alerts.remove(alert)
        except Exception as e:
            print(f"Error in price alert checker: {e}")
        time.sleep(60)  

def get_token_price(token_address):
    try:
        if token_address in token_cache and \
           time.time() - token_cache[token_address]['timestamp'] < CACHE_DURATION:
            return token_cache[token_address]['price']

        response = requests.get(f"{DEX_SCREENER_API_URL}/{token_address}")
        data = response.json()
        if data.get("pairs"):
            price = float(data["pairs"][0]["priceUsd"])
            token_cache[token_address] = {
                'price': price,
                'timestamp': time.time()
            }
            return price
    except Exception as e:
        print(f"Error fetching token price: {e}")
    return None

def create_token_menu(token_address):
    markup = InlineKeyboardMarkup()
    
    short_addr = token_address[:30]
    markup.row(
        InlineKeyboardButton("💱 Trade", url="https://t.me/firstledger_bot"),
        InlineKeyboardButton("🔔 Alert", callback_data=f"a_{short_addr}")
    )
    markup.row(
        InlineKeyboardButton("🤖 AI", callback_data=f"i_{short_addr}"),
        InlineKeyboardButton("👥 Holders", callback_data=f"h_{short_addr}")
    )
    markup.row(
        InlineKeyboardButton("🏠 Menu", callback_data="main_menu")
    )
    return markup

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        if message.text == '/start':
            send_welcome(message)
        return

    
    process_token_tracking(message)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "👋 Welcome to XTRACKER - Your Advanced XRP Token Analytics Bot!\n\n"
        "🔥 Features:\n"
        "• Real-time token tracking\n"
        "• Wallet analytics\n"
        "• Price alerts\n"
        "• AI-powered insights\n"
        "• Whale tracking\n"
        "• Market analysis\n\n"
        "🚀 Get started by selecting an option below!"
    )
    bot.reply_to(message, welcome_text, reply_markup=create_main_menu())

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        if call.data == "main_menu":
            bot.edit_message_text(
                "Select an option:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_main_menu()
            )
        elif call.data.startswith("track_"):
            handle_tracking_request(call)
        elif call.data.startswith("i_"):  
            token_address = call.data.split('_')[1]
            bot.edit_message_text(
                "🤖 AI Market Analysis feature coming soon!",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_token_menu(token_address)
            )


        elif call.data.startswith("a_"):  
            token_address = call.data.split('_')[1]
            setup_price_alert(call)
        elif call.data.startswith("h_"): 
            token_address = call.data.split('_')[1]
            bot.answer_callback_query(call.id, "Holders analysis coming soon!")
        elif call.data == "trending":
            show_trending_tokens(call)
        elif call.data == "whales":
            track_whale_activity(call)
        elif call.data == "chart":
            bot.answer_callback_query(call.id, "📊 Chart feature coming soon!")
        elif call.data == "alert":
            bot.answer_callback_query(call.id, "🔔 Price alerts feature coming soon!")
        elif call.data == "ai":
            bot.answer_callback_query(call.id, "🤖 AI analysis feature coming soon!")
        elif call.data == "holders":
            bot.answer_callback_query(call.id, "👥 Holders analysis feature coming soon!")
        elif call.data.startswith("setalert_"):
            setup_price_alert(call)
        elif call.data.startswith("txns_"):  
            wallet_address = call.data.split('_')[1]
            _, recent_txns = get_xrpscan_info(wallet_address)

            message = "📊 Recent Transactions\n\n"
            if recent_txns:
                for txn in recent_txns[:5]:
                    message += (
                        f"💸 Type: {txn.get('type', 'Unknown')}\n"
                        f"💰 Amount: {txn.get('amount', 'N/A')} XRP\n"
                        f"📅 Date: {txn.get('date', 'N/A')}\n\n"
                    )
            else:
                message = "No recent transactions found."

            bot.edit_message_text(
                message,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_wallet_menu(wallet_address)
            )

        elif call.data.startswith("walert_"):  
            wallet_address = call.data.split('_')[1]
            msg = bot.edit_message_text(
                "Enter XRP amount threshold for alert (e.g., '1000 above' or '500 below'):",
                call.message.chat.id,
                call.message.message_id
            )
            bot.register_next_step_handler(msg, process_wallet_alert, wallet_address)

        elif call.data.startswith("hold_"): 
            wallet_address = call.data.split('_')[1]
            account_data, _ = get_xrpscan_info(wallet_address)

            message = "💰 Wallet Holdings\n\n"
            if account_data:
                message += (
                    f"XRP Balance: {account_data.get('xrpBalance', 'N/A')} XRP\n"
                    f"Tokens: {len(account_data.get('tokens', []))} different assets\n"
                    f"Reserved: {account_data.get('ownerCount', 0)} objects\n"
                )
            else:
                message = "No holdings data available."

            bot.edit_message_text(
                message,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_wallet_menu(wallet_address)
            )

        elif call.data.startswith("wstats_"):  
            wallet_address = call.data.split('_')[1]
            account_data, recent_txns = get_xrpscan_info(wallet_address)

            message = "📈 Wallet Analytics\n\n"
            if account_data and recent_txns:
                tx_count = len(recent_txns)
                total_volume = sum(float(tx.get('amount', 0)) for tx in recent_txns if tx.get('amount'))
                message += (
                    f"Recent Transaction Count: {tx_count}\n"
                    f"Total Volume: {total_volume:.2f} XRP\n"
                    f"Account Age: {account_data.get('age', 'N/A')} days\n"
                    f"Activity Score: {account_data.get('score', 'N/A')}\n"
                )
            else:
                message = "No analytics data available."

            bot.edit_message_text(
                message,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_wallet_menu(wallet_address)
            )
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ An error occurred")

def handle_tracking_request(call):
    if call.data == "track_token":
        msg = bot.edit_message_text(
            "📝 Please send the token contract address you want to track:",
            call.message.chat.id,
            call.message.message_id
        )
        bot.register_next_step_handler(msg, process_token_tracking)
    elif call.data == "track_wallet":
        msg = bot.edit_message_text(
            "📝 Please send the wallet address you want to track:",
            call.message.chat.id,
            call.message.message_id
        )
        bot.register_next_step_handler(msg, process_wallet_tracking)

def get_xrpscan_info(wallet_address):
    try:

        account_response = requests.get(f"https://api.xrpscan.com/api/v1/account/{wallet_address}")
        if account_response.status_code != 200:
            return None, None

        account_data = account_response.json()


        txn_response = requests.get(f"https://api.xrpscan.com/api/v1/account/{wallet_address}/transactions")
        recent_txns = txn_response.json() if txn_response.status_code == 200 else []

        return account_data, recent_txns
    except Exception as e:
        print(f"Error fetching XRPScan info: {e}")
        return None, None

def get_token_info(token_address):
    try:
        
        if len(token_address) == 40 and token_address.isalnum():
            
            response = requests.get(f"https://api.xrpscan.com/api/v1/token/{token_address}")
            if response.status_code == 200:
                data = response.json()
                
                chart_url = f"https://firstledger.net/token/{data.get('issuer', '')}/{token_address}"
                return (
                    f"🔍 XRP Token Analysis\n\n"
                    f"💰 Currency Code: {data.get('currency', 'N/A')}\n"
                    f"🏢 Issuer: {data.get('issuer', 'N/A')}\n"
                    f"📊 Total Supply: {data.get('amount', 'N/A')}\n"
                    f"📈 Chart: {chart_url}\n"
                ), create_token_menu(token_address)
            return "❌ XRP Token not found or no data available.", None

        
        if token_address in token_cache and time.time() - token_cache[token_address]['timestamp'] < CACHE_DURATION:
            data = token_cache[token_address]['data']
        else:
            
            response = requests.get(f"{DEX_SCREENER_API_URL}/{token_address}")
            data = response.json()

            
            token_cache[token_address] = {
                'data': data,
                'timestamp': time.time()
            }

        if data.get("pairs"):
            pair = data["pairs"][0]

            
            response = (
                f"🔍 Token Analysis for {pair['baseToken']['symbol']}\n\n"
                f"💰 Price: ${pair.get('priceUsd', 'N/A')}\n"
                f"📊 24h Volume: ${pair.get('volume', {}).get('h24', 'N/A')}\n"
                f"💧 Liquidity: ${pair.get('liquidity', {}).get('usd', 'N/A')}\n"
                f"📈 24h Change: {pair.get('priceChange', {}).get('h24', 'N/A')}%\n"
                f"🌐 Market Cap: ${pair.get('marketCap', 'N/A')}\n"
                f"💎 FDV: ${pair.get('fdv', 'N/A')}\n"
            )

            markup = create_token_menu(token_address)
            return response, markup
        else:
            return "❌ Token not found or no trading data available.", None

    except Exception as e:
        print(f"Error fetching token info: {e}")
        return "❌ Error fetching token data. Please try again later.", None

async def get_currency(address):
    """Get currency information for a specific XRP Ledger address."""
    try:
        body = {
            "method": "account_currencies",
            "params": [
                {
                    "account": address,
                    "ledger_index": "validated"
                }
            ]
        }
        
        response = requests.post(
            'https://s1.ripple.com:51234/',
            json=body,
            headers={'Content-Type': 'application/json'}
        )
        return response.json()
    except Exception:
        return 0

async def check_issuer_currency(issuer_address):
    """Check the currency for an issuer address."""
    currency_object = await get_currency(issuer_address)
    
    if (isinstance(currency_object, dict) and 
        'result' in currency_object and 
        'receive_currencies' in currency_object['result'] and 
        len(currency_object['result']['receive_currencies']) > 0):
        
        currency = currency_object['result']['receive_currencies'][0]
        return currency
    return None

def process_token_tracking(message):
    try:
        token_address = message.text.strip()
        if not token_address:
            bot.reply_to(message, "❌ Please provide a valid token address.")
            return

       
        parts = token_address.split('.')
        if len(parts) == 1:  
            import asyncio
            currency = asyncio.run(check_issuer_currency(token_address))
            if currency:
                token_address = f"{currency}.{token_address}"
            else:
                bot.reply_to(message, "❌ Invalid token address format.\nExample: 4A55474745524E41555400000000000000000000.rHFE5b7dqkBSxSWiCKqAbUHTb1Yp59GirV")
                return
        
        base_address = parts[0].upper()  

        
        if not (20 <= len(base_address) <= 40):
            bot.reply_to(message, "❌ Invalid token address format.\nExample: 4A55474745524E41555400000000000000000000.rHFE5b7dqkBSxSWiCKqAbUHTb1Yp59GirV")
            return

        
        response, markup = get_token_info(token_address)

        if markup:
            bot.reply_to(message, response, reply_markup=markup, parse_mode="Markdown")
        else:
            
            try:
                xrp_response = requests.get(f"https://api.xrpscan.com/api/v1/token/{base_address}")
                if xrp_response.status_code == 200:
                    data = xrp_response.json()
                    response = (
                        f"🔍 Token Information\n\n"
                        f"💎 Token: {data.get('currency', 'Unknown')}\n"
                        f"🏢 Issuer: {data.get('issuer', 'N/A')}\n"
                        f"💰 Supply: {data.get('amount', 'N/A')}\n"
                    )
                    bot.reply_to(message, response, reply_markup=create_token_menu(token_address))
                else:
                    bot.reply_to(message, "❌ Token not found. Please verify the address and try again.")
            except:
                bot.reply_to(message, "❌ Could not fetch token information. Please verify the token address.")
    except requests.exceptions.RequestException:
        bot.reply_to(message, "❌ Network error while fetching token data. Please try again.")
    except Exception as e:
        bot.reply_to(message, "❌ An unexpected error occurred. Please try again with a valid token address.")
        print(f"Error in process_token_tracking: {str(e)}")

def get_xrpscan_info(wallet_address):
    try:
        
        account_response = requests.get(f"https://api.xrpscan.com/api/v1/account/{wallet_address}")
        if account_response.status_code != 200:
            return None, None

        account_data = account_response.json()

        
        txn_response = requests.get(f"https://api.xrpscan.com/api/v1/account/{wallet_address}/transactions")
        recent_txns = txn_response.json() if txn_response.status_code == 200 else []

        return account_data, recent_txns
    except Exception as e:
        print(f"Error fetching XRPScan info: {e}")
        return None, None

def process_wallet_tracking(message):
    try:
        wallet_address = message.text.strip()
        account_data, recent_txns = get_xrpscan_info(wallet_address)

        if account_data:
            response = format_wallet_info(account_data, recent_txns)
            markup = create_wallet_menu(wallet_address)
            bot.reply_to(message, response, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Invalid wallet address or no data found.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error processing request: {str(e)}")

def format_wallet_info(account_data, recent_txns):
    
    balance = float(account_data.get('xrpBalance', 0))
    tx_count = int(account_data.get('txnCount', 0))

    
    total_volume = 0
    for txn in (recent_txns if isinstance(recent_txns, list) else []):
        if isinstance(txn, dict) and txn.get('amount'):
            total_volume += float(txn.get('amount', 0))

    
    account_type = account_data.get('accountType', 'Standard')
    domain = account_data.get('domain', 'None')
    flags = account_data.get('flags', [])

    return (
        f"👛 Wallet Analysis\n\n"
        f"💰 Balance: {balance:,.2f} XRP\n"
        f"📊 Transaction Count: {tx_count:,}\n"
        f"💎 Total Volume: {total_volume:,.2f} XRP\n"
        f"🔑 Account Type: {account_type}\n"
        f"🌐 Domain: {domain}\n"
        f"🚩 Flags: {', '.join(flags) if flags else 'None'}\n"
    )

def format_transactions_info(recent_txns):
    if not recent_txns:
        return "No recent transactions found."

    tx_list = []
    for txn in recent_txns[:10]:  
        if isinstance(txn, dict):
            tx_type = txn.get('type', 'Unknown')
            tx_amount = float(txn.get('amount', 0))
            tx_time = txn.get('date', 'N/A')
            tx_list.append(
                f"📎 Type: {tx_type}\n"
                f"💰 Amount: {tx_amount:,.2f} XRP\n"
                f"⏰ Time: {tx_time}\n"
            )

    return "📋 Recent Transactions\n\n" + "\n".join(tx_list)

def format_holdings_info(account_data):
    holdings = []
    tokens = account_data.get('tokens', [])

    if not tokens:
        return "No token holdings found."

    for token in tokens:
        symbol = token.get('currency', 'Unknown')
        balance = float(token.get('value', 0))
        issuer = token.get('issuer', 'Unknown')[:8] + "..."  
        holdings.append(
            f"🪙 {symbol}\n"
            f"💎 Balance: {balance:,.2f}\n"
            f"🏢 Issuer: {issuer}\n"
        )

    return "💰 Token Holdings\n\n" + "\n".join(holdings)

def format_analytics_info(account_data, recent_txns):
    if not account_data or not recent_txns:
        return "Insufficient data for analytics."

    
    balance = float(account_data.get('xrpBalance', 0))
    tx_count = len(recent_txns)
    total_volume = sum(float(tx.get('amount', 0)) for tx in recent_txns if tx.get('amount'))
    avg_tx_size = total_volume / tx_count if tx_count > 0 else 0

    return (
        f"📊 Wallet Analytics\n\n"
        f"💰 Current Balance: {balance:,.2f} XRP\n"
        f"📈 Transaction Count: {tx_count:,}\n"
        f"💎 Total Volume: {total_volume:,.2f} XRP\n"
        f"📊 Avg Transaction: {avg_tx_size:,.2f} XRP\n"
        f"🏆 Account Age: {account_data.get('age', 'N/A')} days\n"
        f"⭐ Activity Score: {account_data.get('score', 'N/A')}\n"
    )

def create_wallet_menu(wallet_address):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Transactions", callback_data=f"txns_{wallet_address}"),
        InlineKeyboardButton("🔔 Set Alert", callback_data=f"walert_{wallet_address}")
    )
    markup.row(
        InlineKeyboardButton("💰 Holdings", callback_data=f"hold_{wallet_address}"),
        InlineKeyboardButton("📈 Analytics", callback_data=f"wstats_{wallet_address}")
    )
    markup.row(
        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
    )
    return markup

def show_trending_tokens(call):
    try:
        
        response = requests.get(f"{DEX_SCREENER_API_URL}/search?q=xrp")
        data = response.json()

        if data.get("pairs"):
            message = "🔥 Trending XRP Tokens\n\n"
            sorted_pairs = sorted(data["pairs"], key=lambda x: float(x.get('volume', {}).get('h24', 0)), reverse=True)

            for pair in sorted_pairs[:5]:
                price_change = pair.get('priceChange', {}).get('h24', '0')
                volume = float(pair.get('volume', {}).get('h24', 0))
                token_name = pair.get('baseToken', {}).get('symbol', 'Unknown')

                message += (
                    f"🪙 {token_name}\n"
                    f"💵 ${pair.get('priceUsd', 'N/A')}\n"
                    f"📈 {price_change}%\n"
                    f"💎 Vol: ${volume:,.2f}\n\n"
                )
        else:
            message = "❌ No trending tokens found"

        bot.edit_message_text(
            message,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_menu()
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error fetching trending tokens")

def track_whale_activity(call):
    try:
        
        response = requests.get("https://api.xrpscan.com/api/v1/account/transactions/large")
        data = response.json()

        message = "🐋 Recent Whale Activity\n\n"
        for tx in data[:5]:
            message += (
                f"💰 Amount: {tx['amount']} XRP\n"
                f"📅 Time: {tx['timestamp']}\n"
                f"🏷️ Type: {tx['type']}\n\n"
            )

        bot.edit_message_text(
            message,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=create_main_menu()
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error tracking whale activity")

def setup_price_alert(call):
    token_address = call.data.split('_')[1]
    msg = bot.edit_message_text(
        "Enter price target for alert (e.g., '1.5 above' or '1.2 below'):",
        call.message.chat.id,
        call.message.message_id
    )
    bot.register_next_step_handler(msg, process_price_alert, token_address)

def process_price_alert(message, token_address):
    try:
        parts = message.text.lower().split()
        if len(parts) != 2 or parts[1] not in ['above', 'below']:
            bot.reply_to(message, "❌ Invalid format. Please use: [price] [above/below]")
            return

        price = float(parts[0])
        condition = parts[1]

        price_alerts[token_address].append((message.chat.id, price, condition))
        bot.reply_to(
            message,
            f"✅ Alert set! You'll be notified when price goes {condition} ${price}",
            reply_markup=create_main_menu()
        )
    except ValueError:
        bot.reply_to(message, "❌ Invalid price format")
    except Exception as e:
        bot.reply_to(message, f"❌ Error setting alert: {str(e)}")

def process_wallet_alert(message, wallet_address):
    try:
        parts = message.text.lower().split()
        if len(parts) != 2 or parts[1] not in ['above', 'below']:
            bot.reply_to(message, "❌ Invalid format. Please use: [amount] [above/below]")
            return

        amount = float(parts[0])
        condition = parts[1]

        watched_wallets[wallet_address].append((message.chat.id, amount, condition))
        bot.reply_to(
            message,
            f"✅ Wallet alert set! You'll be notified when balance goes {condition} {amount} XRP",
            reply_markup=create_wallet_menu(wallet_address)
        )
    except ValueError:
        bot.reply_to(message, "❌ Invalid amount format")
    except Exception as e:
        bot.reply_to(message, f"❌ Error setting alert: {str(e)}")



@app.route('/' + TELEGRAM_BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.get_json(force=True)
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "", 200

@app.route("/")
def webhook():
    bot.remove_webhook()
    webhook_url = f'https://{request.host}/{TELEGRAM_BOT_TOKEN}'
    bot.set_webhook(url=webhook_url)
    return "Webhook set", 200

if __name__ == "__main__":

    alert_thread = threading.Thread(target=check_price_alerts, daemon=True)
    alert_thread.start()


    try:
        print("Bot server is running...")
        bot.remove_webhook()
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        print(f"Server error: {str(e)}")