import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, jsonify
import ccxt
import pandas as pd

app = Flask(__name__)

# ==========================================
# CONFIGURATION
# ==========================================
TIMEFRAME = '1h'
EMA_FAST = 50
EMA_MED = 100
EMA_SLOW = 150
MAX_WORKERS = 10  # Parallel threads for fast scanning

# Initialize Delta Exchange via CCXT
exchange = ccxt.delta({
    'enableRateLimit': True,
})

def calculate_emas(df):
    df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
    df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    return df

def analyze_symbol(symbol):
    """Fetches OHLCV data for Delta Exchange perpetuals and applies Triple EMA rules."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=200)
        if len(ohlcv) < EMA_SLOW + 5:
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = calculate_emas(df)

        curr = df.iloc[-2]  # Most recently closed candle
        prev = df.iloc[-3]  # Candle before that

        # Triple EMA Alignment Rules
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

        clean_symbol = symbol.split(':')[0] # Clean up symbol formatting for display

        return {
            "symbol": clean_symbol,
            "price": round(float(curr['close']), 4),
            "trend": trend_text,
            "signal": signal
        }
    except Exception as e:
        return None

# ==========================================
# HTML FRONTEND (Dashboard)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Delta Exchange - Triple EMA Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #131722; color: #d1d4dc; }
        .card { background-color: #1e222d; border: 1px solid #434651; }
    </style>
</head>
<body class="p-8">
    <div class="max-w-6xl mx-auto">
        <div class="flex justify-between items-center mb-8">
            <div>
                <h1 class="text-3xl font-bold text-white">Delta Exchange Scanner</h1>
                <p class="text-sm text-gray-400 mt-1">Triple EMA (50/100/150) Crossover Scanner for Perpetuals</p>
            </div>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors shadow-lg">
                Scan Delta Perpetuals
            </button>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Settings</h3>
                <p class="text-lg font-semibold text-white">{{ timeframe }} | EMA {{ fast }}/{{ med }}/{{ slow }}</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Perpetuals Scanned</h3>
                <p id="marketsScanned" class="text-lg font-semibold text-white">0</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Active Crossovers Found</h3>
                <p id="signalsFound" class="text-lg font-semibold text-white">0</p>
            </div>
        </div>

        <div class="card rounded overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-[#434651] bg-[#131722] text-gray-300">
                        <th class="p-3">Contract</th>
                        <th class="p-3">Price ($)</th>
                        <th class="p-3">EMA Alignment</th>
                        <th class="p-3">Signal</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr>
                        <td colspan="4" class="p-6 text-center text-gray-400">Click 'Scan Delta Perpetuals' to begin scanning.</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function runScan() {
            const btn = document.getElementById('scanBtn');
            const tbody = document.getElementById('resultsBody');
            
            btn.innerText = 'Scanning Delta Exchange...';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-blue-400 animate-pulse">Fetching perpetual contracts from Delta Exchange...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                const data = await response.json();
                
                document.getElementById('marketsScanned').innerText = data.total_scanned;
                
                let signalsCount = 0;
                tbody.innerHTML = '';
                
                if (!data.results || data.results.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-yellow-400">No data returned. Try scanning again.</td></tr>';
                    return;
                }

                data.results.forEach(coin => {
                    if (coin.signal !== 'None') signalsCount++;
                    
                    let trendColor = coin.trend === 'Strong Bullish' ? 'text-green-500 font-bold' : (coin.trend === 'Strong Bearish' ? 'text-red-500 font-bold' : 'text-gray-400');
                    let signalHtml = coin.signal === 'LONG' ? '<span class="bg-green-600/20 text-green-400 px-3 py-1 rounded font-bold border border-green-500/30">🟢 LONG SIGNAL</span>' : 
                                    (coin.signal === 'SHORT' ? '<span class="bg-red-600/20 text-red-400 px-3 py-1 rounded font-bold border border-red-500/30">🔴 SHORT SIGNAL</span>' : '<span class="text-gray-600">-</span>');

                    tbody.innerHTML += `
                        <tr class="border-b border-[#434651] hover:bg-[#2a2e39] transition-colors">
                            <td class="p-3 font-bold text-white">${coin.symbol}</td>
                            <td class="p-3 font-mono text-gray-200">${coin.price}</td>
                            <td class="p-3 ${trendColor}">${coin.trend}</td>
                            <td class="p-3">${signalHtml}</td>
                        </tr>
                    `;
                });
                
                document.getElementById('signalsFound').innerText = signalsCount;
                
            } catch (error) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-6 text-center text-red-500">Error running scan. Check Render logs.</td></tr>`;
                console.error(error);
            } finally {
                btn.innerText = 'Scan Delta Perpetuals';
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
    try:
        markets = exchange.load_markets()
        
        # Filter strictly for Delta Exchange perpetual futures (swap contracts)
        perpetuals = [
            symbol for symbol, market in markets.items() 
            if market.get('swap') is True or market.get('type') == 'swap' or market.get('subType') == 'perpetual'
        ]
        
        results = []
        
        # Fast parallel execution
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_symbol = {executor.submit(analyze_symbol, s): s for s in perpetuals}
            for future in as_completed(future_to_symbol):
                data = future.result()
                if data:
                    results.append(data)

        # Sort: Crossovers first, then Strong Trends, then Neutral
        def sort_key(item):
            if item['signal'] != 'None':
                return 0
            if item['trend'] != 'Neutral':
                return 1
            return 2

        results.sort(key=sort_key)

        return jsonify({
            "total_scanned": len(results),
            "results": results
        })
    except Exception as e:
        print(f"Error in api_scan: {e}")
        return jsonify({"total_scanned": 0, "results": [], "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
