import os
import time
import requests
import traceback
from flask import Flask, render_template_string, jsonify

# We put pandas import in a try-except block to catch if it's not installed
try:
    import pandas as pd
    PANDAS_INSTALLED = True
except ImportError:
    PANDAS_INSTALLED = False

app = Flask(__name__)

# =======================================================
# 🛑 CUSTOM COIN LIST 🛑
# Formatted automatically for the Delta Exchange API
# =======================================================
CUSTOM_COIN_LIST = [
    'BTCUSD',
    'ETHUSD',
    'SOLUSD',
    'ADAUSD',
    'UNIUSD',
    'SNDKBUSD',
    'HYPEUSD',
    'BCHUSD'
]
# =======================================================

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

def fetch_candles(symbol):
    """Fetch 30m OHLCV candles from Delta Exchange."""
    end_time = int(time.time())
    start_time = end_time - (300 * 1800)  # Fetch 300 candles (30m each)
    
    params = {
        'resolution': '30m',
        'symbol': symbol,
        'start': start_time,
        'end': end_time
    }
    
    # Try both base URLs in case one is blocked in your region
    for base_url in BASE_URLS:
        url = f"{base_url}/v2/history/candles"
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                res = resp.json()
                candles = res.get('result', [])
                if isinstance(candles, list) and len(candles) > 0:
                    return sorted(candles, key=lambda x: x.get('time', 0))
        except Exception as e:
            print(f"Error fetching {symbol} from {base_url}: {e}")
            continue # Try the next URL if this one fails
            
    return []

def calculate_emas_and_signals(candles, lookback=3):
    """Calculates EMA 50, 100, 150 and applies crossover rules."""
    if not PANDAS_INSTALLED:
        raise Exception("Pandas library is not installed. Please run: pip install pandas")

    if len(candles) < EMA_SLOW + 10:
        return None

    try:
        df = pd.DataFrame(candles)
        
        # Make sure the 'close' column actually exists in the API response
        if 'close' not in df.columns:
            return None
            
        df['close'] = df['close'].astype(float)

        # Triple EMA Calculations
        df['EMA_50'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df['EMA_100'] = df['close'].ewm(span=EMA_MED, adjust=False).mean()
        df['EMA_150'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

        # Full 3-EMA Stacking (Strong)
        df['full_bullish'] = (df['EMA_50'] > df['EMA_100']) & (df['EMA_100'] > df['EMA_150'])
        df['full_bearish'] = (df['EMA_50'] < df['EMA_100']) & (df['EMA_100'] < df['EMA_150'])

        # Fast/Early Alignment (Fast EMA crosses both medium and slow EMAs)
        df['fast_bullish'] = (df['EMA_50'] > df['EMA_100']) & (df['EMA_50'] > df['EMA_150'])
        df['fast_bearish'] = (df['EMA_50'] < df['EMA_100']) & (df['EMA_50'] < df['EMA_150'])

        curr = df.iloc[-2]  # Most recently closed candle

        # Trend determination
        if curr['full_bullish']:
            trend_text = "Strong Bullish"
        elif curr['full_bearish']:
            trend_text = "Strong Bearish"
        elif curr['fast_bullish']:
            trend_text = "Early Bullish"
        elif curr['fast_bearish']:
            trend_text = "Early Bearish"
        else:
            trend_text = "Neutral"

        # Signal Check with lookback window
        signal = "None"
        for i in range(2, 2 + lookback):
            c_curr = df.iloc[-i]
            c_prev = df.iloc[-(i + 1)]

            if c_curr['full_bullish'] and not c_prev['full_bullish']:
                signal = "LONG (Strong)"
                break
            elif c_curr['full_bearish'] and not c_prev['full_bearish']:
                signal = "SHORT (Strong)"
                break
            elif c_curr['fast_bullish'] and not c_prev['fast_bullish']:
                signal = "LONG (Early/Fast)"
                break
            elif c_curr['fast_bearish'] and not c_prev['fast_bearish']:
                signal = "SHORT (Early/Fast)"
                break

        return {
            'price': round(float(curr['close']), 4),
            'ema50': round(float(curr['EMA_50']), 4),
            'ema100': round(float(curr['EMA_100']), 4),
            'ema150': round(float(curr['EMA_150']), 4),
            'trend': trend_text,
            'signal': signal
        }
    except Exception as e:
        print(f"Error calculating EMA: {e}")
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
    <title>Delta Exchange - Custom Scan</title>
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
                <p class="text-sm text-gray-400 mt-1">30m Timeframe | Custom List | Fast & Strong Crossover Strategy</p>
            </div>
            <button id="scanBtn" onclick="runScan()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors shadow-lg">
                Scan Custom List
            </button>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-8">
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Settings</h3>
                <p class="text-lg font-semibold text-white">30m | EMA 50/100/150</p>
            </div>
            <div class="card p-4 rounded text-center">
                <h3 class="text-sm text-gray-400">Coins Scanned</h3>
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
                        <td colspan="5" class="p-6 text-center text-gray-400">Click 'Scan Custom List' to begin scanning.</td>
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
                let trendColor = coin.trend.includes('Bullish') ? 'text-green-500 font-bold' : (coin.trend.includes('Bearish') ? 'text-red-500 font-bold' : 'text-gray-400');
                let signalHtml = coin.signal.includes('LONG') ? `<span class="bg-green-600/20 text-green-400 px-3 py-1 rounded font-bold border border-green-500/30">🟢 ${coin.signal}</span>` : 
                                (coin.signal.includes('SHORT') ? `<span class="bg-red-600/20 text-red-400 px-3 py-1 rounded font-bold border border-red-500/30">🔴 ${coin.signal}</span>` : '<span class="text-gray-600">-</span>');

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
            
            btn.innerText = 'Scanning List...';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            tbody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-blue-400 animate-pulse">Fetching candles for your custom list...</td></tr>';

            try {
                const response = await fetch('/api/scan');
                
                const contentType = response.headers.get("content-type");
                if (!contentType || !contentType.includes("application/json")) {
                    const textResult = await response.text();
                    console.error("Server returned HTML:", textResult);
                    throw new Error("API Route did not return JSON. Check your Python terminal.");
                }

                const result = await response.json();
                
                if (result.status !== 'success') {
                    throw new Error(result.message || 'Error occurred during scan');
                }

                allScannedData = result.data || [];
                document.getElementById('marketsScanned').innerText = result.scanned_count;
                
                let signalsCount = allScannedData.filter(c => c.signal !== 'None').length;
                document.getElementById('signalsFound').innerText = signalsCount;
                
                filterTable();
                
            } catch (error) {
                // If it fails, display the EXACT Python error on screen
                tbody.innerHTML = `<tr><td colspan="5" class="p-6 text-center text-red-500 font-bold bg-red-900/20 border border-red-500/50 rounded">Python Backend Error: ${error.message}</td></tr>`;
                console.error(error);
            } finally {
                btn.innerText = 'Scan Custom List';
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
        if not PANDAS_INSTALLED:
            raise Exception("Missing 'pandas' library! Please open your terminal and run: pip install pandas")
        
        results = []
        # Use the global CUSTOM_COIN_LIST instead of fetching all products
        for sym in CUSTOM_COIN_LIST:
            candles = fetch_candles(sym)
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
        
        # Sort: Active Crossovers first, then Strong/Early Trends, then Neutral
        def sort_key(item):
            if item['signal'] != 'None':
                return 0
            if 'Bullish' in item['trend'] or 'Bearish' in item['trend']:
                return 1
            return 2

        results.sort(key=sort_key)
            
        return jsonify({
            'status': 'success',
            'scanned_count': len(results),
            'data': results
        }), 200
        
    except Exception as e:
        error_details = str(e)
        print(f"CRITICAL SCAN ERROR: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': error_details
        }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
