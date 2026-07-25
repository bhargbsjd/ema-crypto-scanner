import os
import time
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Headers to prevent Cloudflare/403 blocking on Delta Exchange
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
MAX_WORKERS = 15  # Scan 15 markets in parallel for high speed

def get_delta_products():
    """Fetch all active perpetual futures symbols from Delta Exchange."""
    for base_url in BASE_URLS:
        try:
            url = f"{base_url}/v2/products"
            resp = requests.get(url, headers=HEADERS, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                products = data.get('result', [])
                
                symbols = []
                for p in products:
                    if isinstance(p, dict) and p.get('contract_type') == 'perpetual_futures':
                        symbols.append(p.get('symbol'))
                if symbols:
                    return base_url, symbols
        except Exception as e:
            print(f"Error fetching products from {base_url}: {e}")
    
    return BASE_URLS[0], ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'AVAXUSD', 'DOGEUSD', 'BNBUSD', 'LINKUSD', 'NEARUSD']

def fetch_and_analyze(base_url, symbol):
    """Fetch candles for a symbol and check for exact EMA crossover."""
    end_time = int(time.time())
    start_time = end_time - (250 * 3600)  # Need 250 candles for stable 150 EMA
    
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

        # Triple EMA Calculations
        df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
        df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

        curr = df.iloc[-2]  # Most recently closed candle
        prev = df.iloc[-3]  # Previous candle

        # Alignment Checks
        curr_bullish = (curr['EMA_50'] > curr['EMA_100']) and (curr['EMA_100'] > curr['EMA_150'])
        curr_bearish = (curr['EMA_50'] < curr['EMA_100']) and (curr['EMA_100'] < curr['EMA_150'])

        prev_bullish = (prev['EMA_50'] > prev['EMA_100']) and (prev['EMA_100'] > prev['EMA_150'])
        prev_bearish = (prev['EMA_50'] < prev['EMA_100']) and (prev['EMA_100'] < prev['EMA_150'])

        # EXACT CROSSOVER CONDITIONS (Pine Script longCond / shortCond)
        signal = None
        if curr_bullish and not prev_bullish:
            signal = "LONG"
        elif curr_bearish and not prev_bearish:
            signal = "SHORT"

        # STRICT FILTER: Only return data if a crossover JUST occurred
        if signal is not None:
            return {
                'symbol': symbol,
                'price': round(float(curr['close']), 4),
                'ema50': round(float(curr['EMA_50']), 4),
                'ema100': round(float(curr['EMA_100']), 4),
                'ema150': round(float(curr['EMA_150']), 4),
                'signal': signal
            }

    except Exception as e:
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
    <title>Delta Exchange - Exact EMA Crossover Signals</title>
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
                <p class="text-sm text-gray-400 mt-1">Showing ONLY perpetuals with exact EMA (50/100/150) crossovers on the latest candle</p>
            </div>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors shadow-lg">
                Scan Delta Perpetuals
            </button>
        </div>

        <div class="grid grid-cols-2 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Timeframe & EMAs</h3>
                <p class="text-lg font-semibold text-white">1h | EMA 50/100/150</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Fresh Crossovers Found</h3>
                <p id="signalsFound" class="text-lg font-semibold text-white">0</p>
            </div>
        </div>

        <div class="card rounded overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-[#434651] bg-[#131722] text-gray-300">
                        <th class="p-3">Contract</th>
                        <th class="p-3">Trigger Price ($)</th>
                        <th class="p-3">EMA 50 / 100 / 150</th>
                        <th class="p-3">Exact Signal Triggered</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr>
                        <td colspan="4" class="p-6 text-center text-gray-400">Click 'Scan Delta Perpetuals' to find active crossovers.</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function runScan() {
            const btn = document.getElementById('scanBtn');
            const tbody = document.getElementById('resultsBody');
            
            btn.innerText = 'Scanning ~100 Perpetuals...';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-blue-400 animate-pulse">Analyzing EMA alignments across Delta Exchange...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                const result = await response.json();
                
                if (result.status !== 'success') {
                    throw new Error(result.message || 'Error occurred');
                }

                document.getElementById('signalsFound').innerText = result.data.length;
                tbody.innerHTML = '';
                
                if (!result.data || result.data.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="4" class="p-8 text-center text-gray-400">
                                ⚪ <span class="font-semibold text-gray-300">No fresh crossovers right now.</span><br>
                                <span class="text-xs text-gray-500 mt-1 block">None of the perpetuals crossed into 50/100/150 alignment on the latest closed candle.</span>
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
                            <td class="p-3 font-mono text-gray-200">$${coin.price}</td>
                            <td class="p-3 text-xs font-mono text-gray-400">${coin.ema50} / ${coin.ema100} / ${coin.ema150}</td>
                            <td class="p-3">${signalHtml}</td>
                        </tr>
                    `;
                });
                
            } catch (error) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-6 text-center text-red-500">Error: ${error.message}</td></tr>`;
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
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/scan')
def scan():
    try:
        base_url, symbols = get_delta_products()
        # Scan up to 100 perpetual symbols for complete coverage
        symbols = symbols[:100]
        
        matches = []
        
        # Fast parallel scanning across 15 threads
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_symbol = {executor.submit(fetch_and_analyze, base_url, s): s for s in symbols}
            for future in as_completed(future_to_symbol):
                res = future.result()
                if res:
                    matches.append(res)
            
        return jsonify({
            'status': 'success',
            'scanned_count': len(symbols),
            'data': matches
        })
    except Exception as e:
        print(f"Scan error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
