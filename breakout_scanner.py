import gzip, io, json, os, time, requests, pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def load_env_file():
    if os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env_file()
UPSTOX_TOKEN = os.environ["UPSTOX_ACCESS_TOKEN"]
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]

ZONE_LEN, RANGE_PCT, NEAR_EMA_PCT, EMA_GAP_PCT, VOL_MULT = 22, 1.0, 0.5, 0.3, 2.0
alerted = set()
prev_day = {}   # har stock ki pichhle din ki candles (ek baar laate hain)
HEADERS = {"Authorization": f"Bearer {UPSTOX_TOKEN}", "Accept": "application/json"}
COLS = ["ts", "o", "h", "l", "c", "v", "oi"]


def now_ist():
    return datetime.now(IST)


def get_cash_stocks():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    data = json.load(gzip.GzipFile(fileobj=io.BytesIO(requests.get(url, timeout=60).content)))
    return [(d["instrument_key"], d["trading_symbol"]) for d in data
            if d.get("segment") == "NSE_EQ" and d.get("instrument_type") == "EQ"]


def get_prev_candles(key):
    # Pichhle 7 din me se aakhri trading din ki 5-min candles
    to_d = (now_ist().date() - timedelta(days=1)).isoformat()
    from_d = (now_ist().date() - timedelta(days=7)).isoformat()
    url = f"https://api.upstox.com/v3/historical-candle/{key}/minutes/5/{to_d}/{from_d}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        c = r.json().get("data", {}).get("candles", []) if r.status_code == 200 else []
    except Exception:
        c = []
    if not c:
        return None
    df = pd.DataFrame(c, columns=COLS).sort_values("ts")
    last_day = df["ts"].str[:10].max()
    return df[df["ts"].str[:10] == last_day]


def get_candles(key):
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/5"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return None
    c = r.json().get("data", {}).get("candles", [])
    if not c:
        return None
    today = pd.DataFrame(c, columns=COLS)
    prev = prev_day.get(key)
    df = pd.concat([prev, today]) if prev is not None else today
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def check_setup(df):
    if df is None or len(df) < ZONE_LEN + 2:
        return False
    df["e9"]  = df["c"].ewm(span=9,  adjust=False).mean()
    df["e20"] = df["c"].ewm(span=20, adjust=False).mean()
    bo, zone = df.iloc[-1], df.iloc[-(ZONE_LEN + 1):-1]
    zh, zl = zone["h"].max(), zone["l"].min()
    if (zh - zl) / zl * 100 > RANGE_PCT: return False
    if ((zone["c"] - zone["e20"]).abs() / zone["e20"] * 100 > NEAR_EMA_PCT).any(): return False
    if ((zone["e9"] - zone["e20"]).abs() / zone["e20"] * 100 > EMA_GAP_PCT).any(): return False
    if bo["c"] <= zh: return False
    if bo["v"] < VOL_MULT * df["v"].iloc[-21:-1].mean(): return False
    return bo["e9"] > bo["e20"]


def send_telegram(msg):
    requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                  data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=10)


def scan(stocks):
    for key, sym in stocks:
        try:
            df = get_candles(key)
            if check_setup(df):
                tag = (sym, df.iloc[-1]["ts"])
                if tag not in alerted:
                    alerted.add(tag)
                    send_telegram(f"BREAKOUT: {sym}\nClose: {df.iloc[-1]['c']}\nTime: {df.iloc[-1]['ts']}")
        except Exception as e:
            print(sym, e)
        time.sleep(0.05)


if __name__ == "__main__":
    start = now_ist()
    if (start.hour, start.minute) < (11, 0):
        stop_at = (12, 45)
    else:
        stop_at = (15, 31)
        while (now_ist().hour, now_ist().minute) < (12, 45):
            time.sleep(30)

    stocks = get_cash_stocks()
    print("Stocks:", len(stocks))
    for key, sym in stocks:
        prev_day[key] = get_prev_candles(key)
        time.sleep(0.05)
    print("Pichhle din ki candles aa gayi:", sum(v is not None for v in prev_day.values()))
    send_telegram(f"Scanner chalu ho gaya ({now_ist():%H:%M} IST)")

    while True:
        now = now_ist()
        if (now.hour, now.minute) >= stop_at:
            print("Aaj ka kaam khatam.")
            break
        if (9, 20) <= (now.hour, now.minute) <= (15, 30):
            scan(stocks)
        now = now_ist()
        time.sleep(300 - (now.minute % 5) * 60 - now.second + 5)
