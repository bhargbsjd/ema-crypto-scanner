import os
import time
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

BASE_URLS = [
    'https://api.india.delta.exchange',
    'https://api.delta.exchange'
]

TIMEFRAME = '1h'
EMA_FAST = 50
EMA_MED = 100
EMA_SLOW = 150

# Minimum Coin Price in USD ($1.00+)
MIN_PRICE = 1.0  
MAX_WORKERS = 8 # Lowered slightly to respect Delta's API rate limits

def get_high_price_perpetuals():
    """Fetch symbols and immediately filter out cheap coins BEFORE downloading heavy candle data."""
    for base_url in BASE_URLS:
        try:
            # 1. Get all Perpetual Futures products
            prod_url = f"{base_url}/v2/products"
            prod_resp = requests.get(prod_url, headers=HEADERS, timeout=5)
            if prod_resp.status_code != 200:
                continue
            
            perpetuals = set()
            for p in prod_resp.json().get('result', []):
                if p.get('contract_type') == 'perpetual_futures':
                    perpetuals.add(p.get('symbol'))

            # 2. Get current prices for ALL coins at once
            tick_url = f"{base_url}/v2/tickers"
            tick_resp = requests.get(tick_url, headers=HEADERS, timeout=5)
            if tick_resp.status_code != 200:
                continue
                
            high_price_symbols = []
            for t in tick_resp.json().get('result', []):
                sym = t.get('symbol')
                try:
                    price = float(t.get('close', 0))
                except (ValueError, TypeError):
                    price = 0
                
                # 3. Keep only perpetuals that are above MIN_PRICE
                if sym in perpetuals and price >= MIN_PRICE:
                    high_price_symbols.append(sym)
                    
            if high_price_symbols:
                return base_url, high_price_symbols

        except Exception as e:
            print(f"Error fetching symbols from {base_url}: {e}")
            
    # Fallback if both APIs fail
    return BASE_URLS[0], ['BTCUSD', 'ETHUSD', 'SOLUSD', 'BNBUSD']

def fetch_and_analyze(base_url, symbol):
    """Fetch candles for a pre-filtered symbol and check for exact EMA crossover."""
    end_time = int(time.time())
    start_time = end_time - (250 * 3600)  
    
    url = f"{base_url}/v2/history/candles"
    params = {
        'resolution': TIMEFRAME,
        'symbol': symbol,
        'start': start_time,
        'end': end_time
    }
    
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=5)
        if resp.status_code != 200:
            return None
            
        res = resp.json()
        candles = res.get('result', [])
        if not isinstance(candles, list) or len(candles) < EMA_SLOW + 5:
            return None
            
        candles = sorted(candles, key=lambda x: x.get('time', 0))
        df = pd.DataFrame(candles)
        df['close'] = df['close'].astype(float)

        curr_price = float(df.iloc[-2]['close'])

        # Triple EMA Calculations
        df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
        df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

        curr = df.iloc[-2]  # Last closed candle
        prev = df.iloc[-3]  # Previous candle

        # Alignment Checks
        curr_bullish = (curr['EMA_50'] > curr['EMA_100']) and (curr['EMA_100'] > curr['EMA_150'])
        curr_bearish = (curr['EMA_50'] < curr['EMA_100']) and (curr['EMA_100'] < curr['EMA_150'])

        prev_bullish = (prev['EMA_50'] > prev['EMA_100']) and (prev['EMA_100'] > prev['EMA_150'])
        prev_bearish = (prev['EMA_50'] < prev['EMA_100']) and (prev['EMA_100'] < prev['EMA_150'])

        # EXACT CROSSOVER CONDITION
        signal = None
        if curr_bullish and not prev_bullish:
            signal = "LONG"
        elif curr_bearish and not prev_bearish:
            signal = "SHORT"

        if signal is not None:
            return {
                'symbol': symbol,
                'price': round(curr_price, 4),
                'ema50': round(float(curr['EMA_50']), 4),
                'ema100': round(float(curr['EMA_100']), 4),
                'ema150': round(float(curr['EMA_150']), 4),
                'signal': signal
            }

    except Exception:
        pass
        
    return None

# ==========================================
# SINGLE FILE HTML FRONTEND
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Delta Exchange - High Price Crossover Signals</title>
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
                <h1 class="text-3xl font-bold text-white">Delta Exchange Crossover Scanner</h1>
                <p class="text-sm text-gray-400 mt-1">High-value coins ($1.00+) with fresh EMA (50/100/150) crossovers</p>
            </div>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors shadow-lg">
                Scan High Price Perpetuals
            </button>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Timeframe & EMAs</h3>
                <p class="text-lg font-semibold text-white">1h | EMA 50/100/150</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Min Price Filter</h3>
                <p class="text-lg font-semibold text-green-400">≥ $1.00 USD</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Fresh Signals Found</h3>
                <p id="signalsFound" class="text-lg font-semibold text-white">0</p>
            </div>
        </div>

        <div class="card rounded overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-[#434651] bg-[#131722] text-gray-300">
                        <th class="p-3">Contract</th>
                        <th class="p-3">Price ($) ↓</th>
                        <th class="p-3">EMA 50 / 100 / 150</th>
                        <th class="p-3">Exact Signal Triggered</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr>
                        <td colspan="4" class="p-6 text-center text-gray-400">Click 'Scan High Price Perpetuals' to search active crossovers.</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function runScan() {
            const btn = document.getElementById('scanBtn');
            const tbody = document.getElementById('resultsBody');
            
            btn.innerText = 'Scanning High Price Coins...';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-blue-400 animate-pulse">Filtering out cheap coins and scanning EMA data...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                
                // Better error handling to catch non-JSON (like timeout pages)
                if (!response.ok) {
                    throw new Error(`Server returned status: ${response.status}. The server might have timed out.`);
                }
                
                const textData = await response.text();
                let result;
                try {
                    result = JSON.parse(textData);
                } catch (e) {
                    throw new Error("Server did not return valid data. It likely took too long and timed out.");
                }
                
                if (result.status !== 'success') {
                    throw new Error(result.message || 'Unknown backend error occurred');
                }

                document.getElementById('signalsFound').innerText = result.data.length;
                tbody.innerHTML = '';
                
                if (!result.data || result.data.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="4" class="p-8 text-center text-gray-400">
                                ⚪ <span class="font-semibold text-gray-300">No fresh crossovers on high-price coins ($1.00+) right now.</span><br>
                                <span class="text-xs text-gray-500 mt-1 block">Check back when a new hourly candle closes.</span>
                            </td>
                        </tr>`;
                    return;
                }

                result.data.forEach(coin => {
                    let signalHtml = coin.signal === 'LONG' 
                        ? '<span class="bg-green-600/20 text-green-400 px-3 py-1 rounded font-bold border border-green-500/30">🟢 BUY / LONG SIGNAL</span>' 
                        : '<span class="bg-red-600/20 text-red-400 px-3 py-1 rounded font-bold border border-red-500/30">🔴 SELL / SHORT SIGNAL</span>';

                    tbody.innerHTML += `
                        <tr class="border-b border-[#434651] hover:bg-[#2a2e39] transition-colors">
                            <td class="p-3 font-bold text-white">${coin.symbol}</td>
                            <td class="p-3 font-mono font-semibold text-green-300">$${coin.price}</td>
                            <td class="p-3 text-xs font-mono text-gray-400">${coin.ema50} / ${coin.ema100} / ${coin.ema150}</td>
                            <td class="p-3">${signalHtml}</td>
                        </tr>
                    `;
                });
                
            } catch (error) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-6 text-center text-red-500 font-bold">Error: ${error.message}</td></tr>`;
                console.error("Scan Failed:", error);
            } finally {
                btn.innerText = 'Scan High Price Perpetuals';
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
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/scan')
def scan():
    try:
        # Pre-filter: only get symbols that are ALREADY >= $1.00
        base_url, symbols = get_high_price_perpetuals()
        
        matches = []
        
        # Parallel processing for only the expensive coins
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_symbol = {executor.submit(fetch_and_analyze, base_url, s): s for s in symbols}
            for future in as_completed(future_to_symbol):
                res = future.result()
                if res:
                    matches.append(res)
            
        # Sort by Price Descending
        matches.sort(key=lambda x: x['price'], reverse=True)

        return jsonify({
            'status': 'success',
            'scanned_count': len(symbols),
            'data': matches
        })
    except Exception as e:
        print(f"Server Route Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
