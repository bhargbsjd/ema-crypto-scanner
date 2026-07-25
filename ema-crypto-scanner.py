import os
from flask import Flask, render_template_string, jsonify
import ccxt
import pandas as pd
import time

app = Flask(__name__)

# ==========================================
# CONFIGURATION
# ==========================================
TIMEFRAME = '1h'
EMA_FAST = 50
EMA_MED = 100
EMA_SLOW = 150
# Notice: MAX_COINS_TO_SCAN is removed to scan everything!

exchange = ccxt.binance({
    'enableRateLimit': True, # CCXT will automatically manage API limits
    'options': {'defaultType': 'future'}
})

def calculate_emas(df):
    df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
    df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    return df

# ==========================================
# HTML FRONTEND (Dashboard)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Triple EMA Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #131722; color: #d1d4dc; }
        .card { background-color: #1e222d; border: 1px solid #434651; }
    </style>
</head>
<body class="p-8">
    <div class="max-w-5xl mx-auto">
        <div class="flex justify-between items-center mb-8">
            <h1 class="text-3xl font-bold text-white">Triple EMA Crossover Scanner</h1>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded transition-colors">
                Scan All Markets
            </button>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Settings</h3>
                <p class="text-lg font-semibold text-white">{{ timeframe }} | {{ fast }}/{{ med }}/{{ slow }}</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Markets Scanned</h3>
                <p id="marketsScanned" class="text-lg font-semibold text-white">0</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Active Signals Found</h3>
                <p id="signalsFound" class="text-lg font-semibold text-white">0</p>
            </div>
        </div>

        <div class="card rounded overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-[#434651] bg-[#131722]">
                        <th class="p-3">Symbol</th>
                        <th class="p-3">Price</th>
                        <th class="p-3">Current Trend</th>
                        <th class="p-3">Signal</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr>
                        <td colspan="4" class="p-6 text-center text-gray-500">Click 'Scan All Markets' to begin. (Scanning ~250+ coins takes about 30 seconds to respect API limits)</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function runScan() {
            const btn = document.getElementById('scanBtn');
            const tbody = document.getElementById('resultsBody');
            
            btn.innerText = 'Scanning ~250+ coins... Please wait';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-blue-400 animate-pulse">Fetching 300 candles for every Binance Perpetual market...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                const data = await response.json();
                
                document.getElementById('marketsScanned').innerText = data.total_scanned;
                
                let signalsCount = 0;
                tbody.innerHTML = '';
                
                data.results.forEach(coin => {
                    if (coin.signal !== 'None') signalsCount++;
                    
                    let trendColor = coin.trend === 'Strong Bullish' ? 'text-green-500' : (coin.trend === 'Strong Bearish' ? 'text-red-500' : 'text-gray-400');
                    let signalHtml = coin.signal === 'LONG' ? '<span class="bg-green-600/20 text-green-500 px-2 py-1 rounded font-bold">LONG SIGNAL</span>' : 
                                    (coin.signal === 'SHORT' ? '<span class="bg-red-600/20 text-red-500 px-2 py-1 rounded font-bold">SHORT SIGNAL</span>' : '<span class="text-gray-600">-</span>');

                    tbody.innerHTML += `
                        <tr class="border-b border-[#434651] hover:bg-[#2a2e39] transition-colors">
                            <td class="p-3 font-semibold text-white">${coin.symbol}</td>
                            <td class="p-3">${coin.price}</td>
                            <td class="p-3 ${trendColor}">${coin.trend}</td>
                            <td class="p-3">${signalHtml}</td>
                        </tr>
                    `;
                });
                
                document.getElementById('signalsFound').innerText = signalsCount;
                if(data.results.length === 0) {
                     tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-gray-500">Scan complete. No active setups found right now.</td></tr>';
                }
                
            } catch (error) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-6 text-center text-red-500">Error fetching data. Check console.</td></tr>`;
                console.error(error);
            } finally {
                btn.innerText = 'Scan All Markets';
                btn.disabled = false;
                btn.classList.remove('opacity-50');
            }
        }
    </script>
</body>
</html>
"""

# ==========================================
# ROUTES
# ==========================================
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE, 
                                  timeframe=TIMEFRAME, 
                                  fast=EMA_FAST, 
                                  med=EMA_MED, 
                                  slow=EMA_SLOW)

@app.route('/api/scan')
def api_scan():
    tickers = exchange.fetch_tickers()
    # Get all USDT perpetuals
    perpetuals = [k for k in tickers.keys() if k.endswith(':USDT')]
    
    results = []
    
    for symbol in perpetuals:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=300)
            if len(ohlcv) < EMA_SLOW + 5:
                continue
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = calculate_emas(df)
            
            curr = df.iloc[-2]
            prev = df.iloc[-3]
            
            # Trend Logic
            curr_bullish = (curr['EMA_50'] > curr['EMA_100']) and (curr['EMA_100'] > curr['EMA_150'])
            curr_bearish = (curr['EMA_50'] < curr['EMA_100']) and (curr['EMA_100'] < curr['EMA_150'])
            
            prev_bullish = (prev['EMA_50'] > prev['EMA_100']) and (prev['EMA_100'] > prev['EMA_150'])
            prev_bearish = (prev['EMA_50'] < prev['EMA_100']) and (prev['EMA_100'] < prev['EMA_150'])
            
            trend_text = "Strong Bullish" if curr_bullish else ("Strong Bearish" if curr_bearish else "Neutral")
            
            signal = "None"
            if curr_bullish and not prev_bullish:
                signal = "LONG"
            elif curr_bearish and not prev_bearish:
                signal = "SHORT"
                
            if trend_text != "Neutral" or signal != "None":
                results.append({
                    "symbol": symbol.replace(":USDT", ""),
                    "price": curr['close'],
                    "trend": trend_text,
                    "signal": signal
                })
                
        except Exception as e:
            # Skip coins that throw errors (delisted, new, etc.)
            pass

    # Sort results to show new Signals at the top
    results.sort(key=lambda x: 0 if x['signal'] != "None" else 1)

    return jsonify({
        "total_scanned": len(perpetuals),
        "results": results
    })

# Required for hosting platforms
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)