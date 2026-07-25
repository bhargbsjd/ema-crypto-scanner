import os
import time
import requests
import pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# User-Agent header prevents Cloudflare/403 blocking on Delta Exchange
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

BASE_URLS = [
    'https://api.india.delta.exchange',
    'https://api.delta.exchange'
]

EMA_FAST = 50
EMA_MED = 100
EMA_SLOW = 150

def get_delta_products():
    """Fetch live perpetual futures symbols from Delta Exchange."""
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
    
    # Fallback list if product discovery fails
    return BASE_URLS[0], ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'AVAXUSD', 'DOGEUSD', 'BNBUSD', 'LINKUSD', 'NEARUSD']

def fetch_candles(base_url, symbol):
    """Fetch 1h OHLCV candles from Delta Exchange."""
    end_time = int(time.time())
    start_time = end_time - (250 * 3600)  # Fetch 250 candles to accurately calculate EMA 150
    
    url = f"{base_url}/v2/history/candles"
    params = {
        'resolution': '1h',
        'symbol': symbol,
        'start': start_time,
        'end': end_time
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            res = resp.json()
            candles = res.get('result', [])
            if isinstance(candles, list) and len(candles) > 0:
                return sorted(candles, key=lambda x: x.get('time', 0))
    except Exception as e:
        print(f"Error fetching candles for {symbol}: {e}")
    return []

def calculate_emas_and_signals(candles):
    """Calculates EMA 50, 100, 150 and applies crossover rules."""
    if len(candles) < EMA_SLOW + 5:
        return None

    df = pd.DataFrame(candles)
    df['close'] = df['close'].astype(float)

    # Triple EMA Calculations
    df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
    df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

    curr = df.iloc[-2]  # Most recently closed candle
    prev = df.iloc[-3]  # Previous candle

    # Alignment Rules
    curr_bullish = (curr['EMA_50'] > curr['EMA_100']) and (curr['EMA_100'] > curr['EMA_150'])
    curr_bearish = (curr['EMA_50'] < curr['EMA_100']) and (curr['EMA_100'] < curr['EMA_150'])

    prev_bullish = (prev['EMA_50'] > prev['EMA_100']) and (prev['EMA_100'] > prev['EMA_150'])
    prev_bearish = (prev['EMA_50'] < prev['EMA_100']) and (prev['EMA_100'] < prev['EMA_150'])

    trend_text = "Strong Bullish" if curr_bullish else ("Strong Bearish" if curr_bearish else "Neutral")

    # Crossover Signals
    signal = "None"
    if curr_bullish and not prev_bullish:
        signal = "LONG"
    elif curr_bearish and not prev_bearish:
        signal = "SHORT"

    return {
        'price': round(float(curr['close']), 4),
        'ema50': round(float(curr['EMA_50']), 4),
        'ema100': round(float(curr['EMA_100']), 4),
        'ema150': round(float(curr['EMA_150']), 4),
        'trend': trend_text,
        'signal': signal
    }

# ==========================================
# SINGLE FILE HTML FRONTEND
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
                <p class="text-sm text-gray-400 mt-1">Triple EMA (50/100/150) Crossover Strategy</p>
            </div>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors shadow-lg">
                Scan Delta Perpetuals
            </button>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Settings</h3>
                <p class="text-lg font-semibold text-white">1h | EMA 50/100/150</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Perpetuals Scanned</h3>
                <p id="marketsScanned" class="text-lg font-semibold text-white">0</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Active Signals Found</h3>
                <p id="signalsFound" class="text-lg font-semibold text-white">0</p>
            </div>
        </div>

        <!-- SEARCH BAR CONTROLS -->
        <div class="flex gap-2 mb-4">
            <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="Search contract symbol (e.g. BTC, SOL, ETH)..." 
                   class="bg-[#1e222d] border border-[#434651] text-white px-4 py-2 rounded focus:outline-none focus:border-blue-500 flex-1">
            <button onclick="filterTable()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-5 rounded transition-colors">
                Search
            </button>
            <button onclick="clearSearch()" class="bg-gray-700 hover:bg-gray-600 text-white font-semibold py-2 px-4 rounded transition-colors">
                Clear
            </button>
        </div>

        <div class="card rounded overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-[#434651] bg-[#131722] text-gray-300">
                        <th class="p-3">Contract</th>
                        <th class="p-3">Price ($)</th>
                        <th class="p-3">EMA 50 / 100 / 150</th>
                        <th class="p-3">Current Trend</th>
                        <th class="p-3">Signal</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr>
                        <td colspan="5" class="p-6 text-center text-gray-400">Click 'Scan Delta Perpetuals' to begin scanning.</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let allScannedData = [];

        function renderTable(data) {
            const tbody = document.getElementById('resultsBody');
            tbody.innerHTML = '';
            
            if (!data || data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-gray-400">No matching contracts found.</td></tr>';
                return;
            }

            data.forEach(coin => {
                let trendColor = coin.trend === 'Strong Bullish' ? 'text-green-500 font-bold' : (coin.trend === 'Strong Bearish' ? 'text-red-500 font-bold' : 'text-gray-400');
                let signalHtml = coin.signal === 'LONG' ? '<span class="bg-green-600/20 text-green-400 px-3 py-1 rounded font-bold border border-green-500/30">🟢 LONG SIGNAL</span>' : 
                                (coin.signal === 'SHORT' ? '<span class="bg-red-600/20 text-red-400 px-3 py-1 rounded font-bold border border-red-500/30">🔴 SHORT SIGNAL</span>' : '<span class="text-gray-600">-</span>');

                tbody.innerHTML += `
                    <tr class="border-b border-[#434651] hover:bg-[#2a2e39] transition-colors">
                        <td class="p-3 font-bold text-white">${coin.symbol}</td>
                        <td class="p-3 font-mono text-gray-200">${coin.price}</td>
                        <td class="p-3 text-xs font-mono text-gray-400">${coin.ema50} / ${coin.ema100} / ${coin.ema150}</td>
                        <td class="p-3 ${trendColor}">${coin.trend}</td>
                        <td class="p-3">${signalHtml}</td>
                    </tr>
                `;
            });
        }

        function filterTable() {
            const query = document.getElementById('searchInput').value.trim().toUpperCase();
            if (!query) {
                renderTable(allScannedData);
                return;
            }
            const filtered = allScannedData.filter(coin => coin.symbol.toUpperCase().includes(query));
            renderTable(filtered);
        }

        function clearSearch() {
            document.getElementById('searchInput').value = '';
            renderTable(allScannedData);
        }

        async function runScan() {
            const btn = document.getElementById('scanBtn');
            const tbody = document.getElementById('resultsBody');
            
            btn.innerText = 'Scanning Delta Exchange...';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-blue-400 animate-pulse">Fetching live candles from Delta Exchange...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                const result = await response.json();
                
                if (result.status !== 'success') {
                    throw new Error(result.message || 'Error occurred');
                }

                allScannedData = result.data || [];
                document.getElementById('marketsScanned').innerText = result.scanned_count;
                
                let signalsCount = allScannedData.filter(c => c.signal !== 'None').length;
                document.getElementById('signalsFound').innerText = signalsCount;
                
                filterTable();
                
            } catch (error) {
                tbody.innerHTML = `<tr><td colspan="5" class="p-6 text-center text-red-500">Error: ${error.message}</td></tr>`;
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
        # Top 30 perpetual contracts for fast execution
        symbols = symbols[:30]
        
        results = []
        for sym in symbols:
            candles = fetch_candles(base_url, sym)
            if not candles:
                continue
            
            analysis = calculate_emas_and_signals(candles)
            if analysis:
                results.append({
                    'symbol': sym,
                    'price': analysis['price'],
                    'ema50': analysis['ema50'],
                    'ema100': analysis['ema100'],
                    'ema150': analysis['ema150'],
                    'trend': analysis['trend'],
                    'signal': analysis['signal']
                })
        
        # Sort: Active Crossovers first, then Strong Trends, then Neutral
        def sort_key(item):
            if item['signal'] != 'None':
                return 0
            if item['trend'] != 'Neutral':
                return 1
            return 2

        results.sort(key=sort_key)
            
        return jsonify({
            'status': 'success',
            'scanned_count': len(results),
            'data': results
        })
    except Exception as e:
        print(f"Scan error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
