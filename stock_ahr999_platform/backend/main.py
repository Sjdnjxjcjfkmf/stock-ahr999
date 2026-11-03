from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime, timezone
import sqlite3, json, math, os, re, time
import numpy as np
import pandas as pd
import yfinance as yf

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "stock_ahr999.db"
app = FastAPI(title="Stock AHR999 API", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE/"frontend"), name="static")

def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, created_at TEXT,
        payload TEXT NOT NULL)""")
    con.commit()
    return con

def safe_float(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def harmonic_mean_positive(values):
    vals = [float(x) for x in values if safe_float(x, 0) and float(x) > 0]
    return len(vals) / sum(1.0/x for x in vals) if vals else None

def annual_fcf_cagr(cashflow):
    # yfinance cashflow columns are usually fiscal periods, newest first.
    try:
        row_ocf = next((i for i in cashflow.index if str(i).lower() in
                        ["operatingcashflow", "total cash from operating activities"]), None)
        row_capex = next((i for i in cashflow.index if "capital expenditure" in str(i).lower()
                          or str(i).lower() in ["capitalexpenditures", "capital expenditure"]), None)
        if row_ocf is None or row_capex is None:
            return None, []
        vals = []
        for col in list(cashflow.columns)[:4]:
            ocf = safe_float(cashflow.loc[row_ocf, col])
            capex = safe_float(cashflow.loc[row_capex, col])
            if ocf is not None and capex is not None:
                vals.append({"period": str(col.date()) if hasattr(col, "date") else str(col),
                             "fcf": ocf - abs(capex)})
        vals = list(reversed(vals))
        positives = [x["fcf"] for x in vals if x["fcf"] > 0]
        if len(positives) >= 2 and vals[0]["fcf"] > 0 and vals[-1]["fcf"] > 0:
            years = max(1, len(vals)-1)
            cagr = (vals[-1]["fcf"] / vals[0]["fcf"]) ** (1/years) - 1
            return float(np.clip(cagr, -0.20, 0.30)), vals
        return None, vals
    except Exception:
        return None, []

def estimate_quality(info, fcf_history):
    # Transparent heuristic, not a credit rating.
    score = 0.70
    debt_equity = safe_float(info.get("debtToEquity"))
    if debt_equity is not None:
        score += 0.12 if debt_equity < 50 else (0.04 if debt_equity < 120 else -0.12)
    margin = safe_float(info.get("profitMargins"))
    if margin is not None:
        score += 0.08 if margin > 0.15 else (0.02 if margin > 0 else -0.10)
    if len(fcf_history) >= 3:
        fcf_vals = [x["fcf"] for x in fcf_history]
        if all(x > 0 for x in fcf_vals):
            score += 0.10
        elif sum(x > 0 for x in fcf_vals) <= 1:
            score -= 0.15
    return round(float(np.clip(score, 0.35, 1.0)), 2)

def dcf_per_share(fcf_per_share, growth, discount=0.10, terminal=0.03, years=10):
    if fcf_per_share is None or fcf_per_share <= 0:
        return None, None
    if discount <= terminal or discount <= -1:
        return None, "折现率必须高于永续增长率"
    pv, cf = 0.0, fcf_per_share
    # Growth fades linearly from estimated near-term growth to terminal growth.
    for year in range(1, years+1):
        g = growth + (terminal-growth) * ((year-1)/(years-1))
        cf *= (1+g)
        pv += cf / ((1+discount)**year)
    tv = cf*(1+terminal)/(discount-terminal)
    value = pv + tv/((1+discount)**years)
    return value, None

def make_demo(ticker):
    # Explicitly synthetic sample so UI can be tested without a working data provider.
    rng = np.random.default_rng(sum(map(ord, ticker)))
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=260)
    returns = rng.normal(0.00045, 0.018, len(dates))
    close = 100*np.exp(np.cumsum(returns))
    close[-1] = close[-1] * 0.91
    hist = pd.DataFrame({"Close": close}, index=dates)
    c = harmonic_mean_positive(hist["Close"].tail(200))
    p = float(hist["Close"].iloc[-1])
    v = p*1.25
    q = 0.82
    index = (p/c)*(p/v)/q
    return {
        "ticker":ticker,"company_name":f"{ticker} · 模拟数据","currency":"USD",
        "price":round(p,2),"cost_200d":round(c,2),"intrinsic_value":round(v,2),
        "fcf_per_share":None,"growth_assumption":0.08,"discount_rate":0.10,
        "terminal_growth":0.03,"quality_score":q,"quality_method":"模拟值",
        "pc":p/c,"pv":p/v,"ahr999":index,"fcf_history":[],
        "price_history":[{"date":d.strftime("%Y-%m-%d"),"close":round(float(x),4)}
                         for d,x in zip(hist.index, hist["Close"])],
        "valuation_status":"DEMO","warnings":["这是合成模拟数据，不是真实行情或财报。"],
        "updated_at":datetime.now(timezone.utc).isoformat()
    }

@app.get("/")
def home():
    return FileResponse(BASE/"frontend"/"index.html")

@app.get("/api/health")
def health():
    return {"ok":True,"version":"0.1.0","data_mode":"live-provider-with-demo-fallback"}

@app.get("/api/analyze/{ticker}")
def analyze(ticker: str, demo: bool=False):
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-^=]{1,15}", ticker):
        raise HTTPException(400, "股票代码格式不正确")
    if demo:
        payload = make_demo(ticker)
    else:
        warnings = []
        try:
            obj = yf.Ticker(ticker)
            hist = obj.history(period="2y", interval="1d", auto_adjust=True)
            if hist is None or hist.empty or "Close" not in hist:
                raise ValueError("行情供应商没有返回价格数据")
            hist = hist.dropna(subset=["Close"])
            prices = hist["Close"].astype(float)
            price = float(prices.iloc[-1])
            cost = harmonic_mean_positive(prices.tail(200))
            info = obj.get_info() or {}
            cf = obj.cashflow
            growth, fcf_history = annual_fcf_cagr(cf)
            fcf_ttm = None
            # Prefer latest annual FCF for the first prototype.
            if fcf_history:
                fcf_ttm = fcf_history[-1]["fcf"]
            shares = safe_float(info.get("sharesOutstanding"))
            if not shares:
                shares = safe_float(info.get("impliedSharesOutstanding"))
            fcf_per_share = fcf_ttm/shares if fcf_ttm and shares and shares > 0 else None
            if growth is None:
                growth = 0.06
                warnings.append("历史自由现金流不足以估算增长率，暂用6%保守默认假设。")
            growth = float(np.clip(growth, -0.10, 0.20))
            q = estimate_quality(info, fcf_history)
            value, err = dcf_per_share(fcf_per_share, growth)
            if value is None:
                warnings.append(err or "缺少可用的正自由现金流/股数，无法可靠计算DCF。")
                # Conservative fallback: show no DCF, never fabricate a valuation.
                pv = None
                ahr = None
            else:
                pv = price/value
                ahr = (price/cost)*pv/q if cost else None
            payload = {
                "ticker":ticker,"company_name":info.get("longName") or info.get("shortName") or ticker,
                "currency":info.get("currency") or "USD","price":price,"cost_200d":cost,
                "intrinsic_value":value,"fcf_per_share":fcf_per_share,
                "growth_assumption":growth,"discount_rate":0.10,"terminal_growth":0.03,
                "quality_score":q,"quality_method":"规则评分原型",
                "pc":price/cost if cost else None,"pv":pv,"ahr999":ahr,
                "fcf_history":fcf_history,
                "price_history":[{"date":d.strftime("%Y-%m-%d"),"close":round(float(x),4)}
                                 for d,x in prices.tail(260).items()],
                "valuation_status":"LIVE","warnings":warnings,
                "updated_at":datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            # Do not silently substitute invented live data.
            raise HTTPException(502, f"自动数据获取失败：{str(e)[:220]}。可切换演示模式验证界面。")
    con = db()
    con.execute("INSERT INTO snapshots(ticker,created_at,payload) VALUES(?,?,?)",
                (ticker,payload["updated_at"],json.dumps(payload,ensure_ascii=False)))
    con.commit(); con.close()
    return payload

@app.get("/api/history/{ticker}")
def history(ticker: str):
    con = db()
    rows = con.execute("SELECT created_at,payload FROM snapshots WHERE ticker=? ORDER BY id DESC LIMIT 30",
                       (ticker.upper(),)).fetchall()
    con.close()
    return [{"created_at":r[0],"data":json.loads(r[1])} for r in rows]

@app.get("/api/universe")
def universe():
    return {"suggested":["AAPL","MSFT","NVDA","GOOGL","AMZN","META","BRK-B","TSLA"]}
