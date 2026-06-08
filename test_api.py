import requests

base = 'http://localhost:8000'

# Add AAPL
r = requests.post(f'{base}/api/stocks', json={'ticker': 'AAPL', 'period': '1mo'})
print('ADD:', r.status_code, r.json())

# List stocks
r2 = requests.get(f'{base}/api/stocks')
data = r2.json()
count = data['count']
print('LIST:', r2.status_code, count, 'stocks')
if data['stocks']:
    s = data['stocks'][0]
    print(f"  -> {s['ticker']} | {s['name']} | Price: {s['current_price']} | Change: {s['change_percent']}%")

# Get 1mo history
r3 = requests.get(f'{base}/api/stocks/AAPL/history?period=1mo')
h = r3.json()
first_date = h['history'][0]['date'] if h['history'] else None
print(f"HISTORY: {h['count']} records, first={first_date}")

# Analysis / signals
r4 = requests.get(f'{base}/api/analysis/AAPL?period=6mo')
if r4.status_code == 200:
    sig = r4.json()['signals']
    rsi = sig['rsi']
    overall = sig['overall_signal']
    print(f"SIGNALS: RSI={rsi:.1f} | Overall: {overall}")
    for name, s in sig['strategies'].items():
        print(f"  {name}: {s['signal']}")
else:
    print('SIGNALS:', r4.status_code, r4.json())
