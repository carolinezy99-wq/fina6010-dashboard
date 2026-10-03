#!/usr/bin/env python3
"""Morning Meeting Dashboard — local data proxy + static server."""

from __future__ import annotations

import email.utils
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dtime, timedelta, timezone
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
import evidence

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
CACHE = DATA / "snapshot.json"
PORT = int(os.environ.get("PORT", "6010"))
UA = "MorningMeeting-Dashboard/1.0"
HKT = ZoneInfo("Asia/Hong_Kong")
NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
LONDON = ZoneInfo("Europe/London")

UNIVERSE = [
    {"id": "spx", "cls": "equities", "symbol": "^GSPC", "label": "S&P 500", "region": "US", "kind": "index"},
    {"id": "ndx", "cls": "equities", "symbol": "^IXIC", "label": "Nasdaq Comp", "region": "US", "kind": "index"},
    {"id": "dji", "cls": "equities", "symbol": "^DJI", "label": "Dow Jones", "region": "US", "kind": "index"},
    {"id": "nky", "cls": "equities", "symbol": "^N225", "label": "Nikkei 225", "region": "JP", "kind": "index"},
    {"id": "hsi", "cls": "equities", "symbol": "^HSI", "label": "Hang Seng", "region": "HK", "kind": "index"},
    {"id": "sx5e", "cls": "equities", "symbol": "^STOXX50E", "label": "Euro Stoxx 50", "region": "EU", "kind": "index"},
    {"id": "eur", "cls": "fx", "symbol": "EURUSD=X", "label": "EUR / USD", "region": "EU", "kind": "pair"},
    {"id": "jpy", "cls": "fx", "symbol": "USDJPY=X", "label": "USD / JPY", "region": "JP", "kind": "pair"},
    {"id": "gbp", "cls": "fx", "symbol": "GBPUSD=X", "label": "GBP / USD", "region": "UK", "kind": "pair"},
    {"id": "chf", "cls": "fx", "symbol": "USDCHF=X", "label": "USD / CHF", "region": "CH", "kind": "pair"},
    {"id": "aud", "cls": "fx", "symbol": "AUDUSD=X", "label": "AUD / USD", "region": "AU", "kind": "pair"},
    {"id": "cny", "cls": "fx", "symbol": "USDCNY=X", "label": "USD / CNY", "region": "CN", "kind": "pair"},
    {"id": "wti", "cls": "commodities", "symbol": "CL=F", "label": "WTI Crude", "region": "US", "kind": "energy"},
    {"id": "ng", "cls": "commodities", "symbol": "NG=F", "label": "Natural Gas", "region": "US", "kind": "energy"},
    {"id": "au", "cls": "commodities", "symbol": "GC=F", "label": "Gold", "region": "US", "kind": "precious"},
    {"id": "ag", "cls": "commodities", "symbol": "SI=F", "label": "Silver", "region": "US", "kind": "precious"},
    {"id": "cu", "cls": "commodities", "symbol": "HG=F", "label": "Copper", "region": "US", "kind": "metal"},
    {"id": "wh", "cls": "commodities", "symbol": "ZW=F", "label": "Wheat", "region": "US", "kind": "ags"},
    # Yahoo carries no sovereign yields outside the US, so the rest of the curve
    # comes straight from each issuer's own daily fixing.
    {"id": "us10y", "cls": "bonds", "symbol": "^TNX", "label": "US 10Y Yield", "region": "US", "kind": "yield"},
    {"id": "us30y", "cls": "bonds", "symbol": "^TYX", "label": "US 30Y Yield", "region": "US", "kind": "yield"},
    {"id": "de10y", "cls": "bonds", "feed": "ecb", "label": "Euro Area 10Y", "region": "EU", "kind": "yield"},
    {"id": "jp10y", "cls": "bonds", "feed": "jgb", "label": "Japan 10Y (JGB)", "region": "JP", "kind": "yield"},
    {"id": "gb10y", "cls": "bonds", "feed": "boe", "label": "UK 10Y (Gilt)", "region": "UK", "kind": "yield"},
    {"id": "ca10y", "cls": "bonds", "feed": "boc", "label": "Canada 10Y", "region": "CA", "kind": "yield"},
]

CRYPTO = [
    {"id": "btc", "cls": "crypto", "cg": "bitcoin", "label": "Bitcoin", "region": "GL", "kind": "coin"},
    {"id": "eth", "cls": "crypto", "cg": "ethereum", "label": "Ethereum", "region": "GL", "kind": "coin"},
    {"id": "sol", "cls": "crypto", "cg": "solana", "label": "Solana", "region": "GL", "kind": "coin"},
    {"id": "xrp", "cls": "crypto", "cg": "ripple", "label": "XRP", "region": "GL", "kind": "coin"},
    {"id": "bnb", "cls": "crypto", "cg": "binancecoin", "label": "BNB", "region": "GL", "kind": "coin"},
    {"id": "doge", "cls": "crypto", "cg": "dogecoin", "label": "Dogecoin", "region": "GL", "kind": "coin"},
]

NEWS_KEYWORDS = {
    # index names only — a generic "stock falls" pattern matches any single name
    "spx": [r"s&p ?500", r"\bspx\b", r"wall st", r"u\.?s\.?\s+stocks?", r"us equities", r"\bfed\b", r"fomc"],
    "ndx": [r"nasdaq", r"tech stocks?", r"magnificent seven", r"big tech"],
    "dji": [r"\bdow\b", r"industrial average", r"dow jones"],
    "nky": [r"nikkei", r"japan", r"tokyo", r"boj", r"yen"],
    "hsi": [r"hang seng", r"\bhsi\b", r"hong kong stock", r"hong kong shares", r"china stock"],
    "sx5e": [r"stoxx", r"europe", r"eurozone", r"ecb", r"frankfurt", r"european share"],
    "chf": [r"swiss franc", r"usd/chf", r"\bfranc\b", r"\bsnb\b"],
    "eur": [r"\beuro\b", r"eur/usd", r"\becb\b", r"eurozone"],
    "jpy": [r"usd/jpy", r"\byen\b", r"bank of japan", r"\bboj\b"],
    "gbp": [r"pound sterling", r"gbp/usd", r"\bsterling\b", r"bank of england", r"\bgbp\b", r"\bpound\b"],
    # \b matters: a bare "aud" also matches "Saudi"
    "aud": [r"\baussie\b", r"australian dollar", r"\baud\b", r"\brba\b"],
    "cny": [r"\byuan\b", r"renminbi", r"\bcny\b", r"\bpboc\b", r"china currency"],
    "wti": [r"wti", r"west texas", r"crude", r"\boil\b", r"opec"],
    "wh": [r"\bwheat\b", r"\bgrain\b", r"\bcrop\b", r"black sea"],
    "au": [r"\bgold\b", r"bullion", r"precious metal"],
    "ag": [r"\bsilver\b", r"precious metal"],
    "cu": [r"copper", r"dr\.? copper", r"base metal"],
    "ng": [r"natural gas", r"henry hub", r"lng"],
    "de10y": [r"\bbund\b", r"german bond", r"euro ?zone yield", r"\becb\b", r"german yield"],
    "jp10y": [r"\bjgb\b", r"japanese government bond", r"japan yield", r"\bboj\b"],
    "us10y": [r"10-year", r"10 year", r"treasury", r"bond yield", r"\bfed\b"],
    "us30y": [r"30-year", r"long bond", r"term premium", r"treasury"],
    "gb10y": [r"\bgilt\b", r"uk bond", r"british bond", r"\bboe\b", r"bank of england"],
    "ca10y": [r"canad", r"\bboc\b", r"bank of canada"],
    "btc": [r"bitcoin", r"\bbtc\b", r"crypto"],
    "eth": [r"ethereum", r"\beth\b", r"\bether\b"],
    "sol": [r"solana", r"\bsol\b"],
    "xrp": [r"\bxrp\b", r"ripple"],
    "bnb": [r"\bbnb\b", r"binance"],
    "doge": [r"doge", r"dogecoin", r"meme coin"],
}

# Terms that name a region or institution rather than the instrument. "Japan" also
# matches a typhoon story, so these only count alongside market vocabulary.
NEWS_WEAK = {
    "spx": [r"\bfed\b", r"fomc"],
    "ndx": [r"big tech"],
    "nky": [r"japan", r"tokyo", r"boj", r"yen"],
    "hsi": [r"china stock"],
    "sx5e": [r"europe", r"eurozone", r"ecb", r"frankfurt"],
    "eur": [r"\beuro\b", r"\becb\b", r"eurozone"],
    "jpy": [r"bank of japan", r"\bboj\b"],
    "chf": [r"\bfranc\b", r"\bsnb\b"],
    "gbp": [r"bank of england", r"\bpound\b"],
    "aud": [r"\brba\b"],
    "cny": [r"\bpboc\b", r"china currency"],
    "de10y": [r"\becb\b", r"euro ?zone", r"german"],
    "jp10y": [r"\bboj\b", r"japan"],
    "us10y": [r"treasury", r"\bfed\b"],
    "us30y": [r"treasury", r"term premium"],
    "gb10y": [r"\bboe\b", r"bank of england", r"\buk\b", r"britain"],
    "ca10y": [r"canad", r"bank of canada"],
    "btc": [r"crypto"],
    "eth": [r"\beth\b"],
    "bnb": [r"binance"],
}

# Broad geography used only to order the class-level fallback, so sibling cards
# do not all land on the same global headline.
REGION_HINTS = {
    "spx": [r"wall st", r"u\.?s\.?\b", r"american"],
    "ndx": [r"tech", r"\bai\b", r"chip", r"semiconductor", r"nasdaq"],
    "dji": [r"wall st", r"industrial", r"u\.?s\.?\b"],
    "nky": [r"japan", r"tokyo", r"\byen\b", r"\bboj\b", r"asia"],
    "hsi": [r"hong kong", r"china", r"chinese", r"shanghai", r"asia", r"yuan"],
    "sx5e": [r"europe", r"german", r"france", r"\becb\b", r"\beuro\b", r"london"],
    "chf": [r"\bswiss\b", r"\bfranc\b", r"\bsnb\b", r"\bdollar\b"],
    "eur": [r"\beuro\b", r"\becb\b", r"europe", r"german"],
    "jpy": [r"\byen\b", r"japan", r"\bboj\b", r"tokyo"],
    "gbp": [r"\bpound\b", r"\bsterling\b", r"\buk\b", r"britain", r"british", r"\bboe\b"],
    "aud": [r"austral", r"\brba\b", r"\baussie\b", r"commodity currenc"],
    "cny": [r"\byuan\b", r"china", r"chinese", r"\bpboc\b", r"renminbi"],
    "us10y": [r"treasur", r"\bfed\b", r"10-year"],
    "us30y": [r"treasur", r"long bond", r"30-year"],
    "de10y": [r"\bbund\b", r"german", r"\becb\b", r"euro ?zone", r"europe"],
    "jp10y": [r"\bjgb\b", r"japan", r"\bboj\b", r"tokyo"],
    "gb10y": [r"\bgilt", r"\buk\b", r"britain", r"british", r"\bboe\b", r"bank of england"],
    "ca10y": [r"canad", r"bank of canada", r"\bboc\b"],
}

CLASS_KEYWORDS = {
    # bare "euro" matches "Europe", so these all carry word boundaries
    "equities": [r"\bstocks?\b", r"\bequit(?:y|ies)\b", r"\bshares?\b", r"\bindex(?:es)?\b", r"\bindices\b"],
    "fx": [r"\bdollar\b", r"\bfx\b", r"\bforex\b", r"\bcurrenc(?:y|ies)\b", r"\byen\b", r"\beuro\b", r"\bsterling\b"],
    "commodities": [r"\boil\b", r"\bcrude\b", r"\bgold\b", r"\bmetals?\b", r"\bgas\b", r"\bopec\b"],
    "bonds": [r"\btreasur", r"\byields?\b", r"\bbonds?\b", r"\bfed\b", r"\bduration\b", r"\bgilts?\b"],
    "crypto": [r"\bbitcoin\b", r"\bcrypto", r"\btokens?\b", r"\bether"],
}

DIR_UP = re.compile(
    r"\b(rise[sn]?|rising|rose|gain(?:s|ed)?|rall(?:y|ies|ied)|higher|surge[sd]?|jump(?:s|ed)?|"
    r"climb(?:s|ed)?|advance[sd]?|rebound(?:s|ed)?|recover(?:s|ed|y)|firm(?:s|er)?|strengthen(?:s|ed)?|"
    r"lift(?:s|ed)?|boost(?:s|ed)?|up\b|bid|top(?:s|ped)?|extend(?:s|ed)? gains)\b",
    re.I,
)
DIR_DOWN = re.compile(
    r"\b(fall(?:s|en|ing)?|fell|drop(?:s|ped)?|slide[sd]?|slid|lower|decline[sd]?|tumble[sd]?|"
    r"slip(?:s|ped)?|sink(?:s)?|sank|plunge[sd]?|slump(?:s|ed)?|retreat(?:s|ed)?|ease[sd]?|easing|"
    r"soften(?:s|ed)?|weaken(?:s|ed)?|pare[sd]?|sold|loss(?:es)?|selloff|sell-off|down\b|shed[s]?)\b",
    re.I,
)
WHY = re.compile(
    r"\b(after|amid|because|as\s+(?:fed|ecb|boj|opec|war|yields?)|on the back of|fueled|fuelled|"
    r"prompted|weighs?|fears?|war|hike|cut|opec|inventory|sanction|tariff|inflation|payrolls|"
    r"cpi|fomc|boj|ecb|pboc|geopolit|ceasefire|iran|rate\s+hike|rate\s+cut)\b",
    re.I,
)
MARKET_DRIVER_TITLE = re.compile(
    r"\b(flows?|yields?|rates?|inflation|prices?|rally|sell-?off|retreats?|rebound|outlook|forecast|"
    r"deal|talks?|demand|supply|weak yen|strong dollar|fed|central bank|opec)\b",
    re.I,
)
JUNK_TITLE = re.compile(
    r"price prediction|how to trade|live chart|xstock|convert 1 |etfs? to buy|moomoo|"
    r"cryptorank|24/7 wall|altcoin buzz|coinpedia|pluang|trading guide|inside the index|"
    r"price today\b|quotes? & news|technical analysis 20\d\d|what could .+ be worth|"
    r"school fee|net zero department|five-year plan|freewheeling hong kong|lagarde.?s successor|"
    r"print edition|editor.?s choice|editor-in-chief|watch live|podcast|photos of the|"
    r"newsletter|weekly recap|what to watch this week|"
    r"stock price & latest news|\| stock price|taking stock|latest news$|quote & chart|"
    r"profile and biography|sicav|accumulating|share class|\bisin\b|factsheet|"
    r"school ib |ib economics|roundup: market talk|futures prices and news|prices and news \||"
    # corporate press releases keep landing in the metals queries
    r"\bannounces\b|bought deal|private placement|drill (?:results|program|hole)|"
    r"notice of intent|metallurgical|appoints|files (?:for|its)|grants? (?:options|stock)|"
    r"silver lining|golden (?:age|opportunity)|climate week|"
    r"stock quote|outperforms competitors|underperforms (?:the )?market|"
    r"stock (?:rises|falls|gains|drops) (?:on )?(?:monday|tuesday|wednesday|thursday|friday)",
    re.I,
)
TIER1 = {
    "Financial Times",
    "Bloomberg",
    "WSJ",
    "Economist",
    "Nikkei",
    "AP",
}
TIER2 = {"CNBC", "BBC", "SCMP", "MarketWatch", "Guardian", "Axios", "NPR", "ABC", "CoinDesk"}
# Reuters answers 401 on a direct click, so it never earns a slot.
# The added desks are the ones a reader can actually open.
NEWSWORTHY = TIER1 | TIER2
# the board covers Monday to Friday of the current HKT week; the floor keeps a Monday morning readable
NEWS_MIN_AGE_H = 48.0


def news_max_age() -> float:
    now = datetime.now(HKT)
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), dtime(0, 0), tzinfo=HKT)
    return 168.0
SOURCE_RANK = {
    "Financial Times": 12,
    "Bloomberg": 12,
    "Reuters": 12,
    "WSJ": 11,
    "Economist": 9,
    "Nikkei": 9,
    "AP": 8,
    "BBC": 7,
    "Guardian": 7,
    "SCMP": 7,
    "CNBC": 6,
    "Axios": 6,
    "NPR": 6,
    "ABC": 6,
    "MarketWatch": 5,
    "CoinDesk": 5,
    "Yahoo Finance": 2,
    "Google News": 0,
}
# What a reader actually gets when they click. Checked against live responses:
# Reuters answers 401 to the dashboard's clicks, the majors want a subscription,
# the rest open in full.
ACCESS = {
    "Reuters": "restricted",
    "Financial Times": "sub",
    "Bloomberg": "sub",
    "WSJ": "sub",
    "Economist": "sub",
    "Nikkei": "sub",
    "CNBC": "free",
    "AP": "free",
    "BBC": "free",
    "SCMP": "free",
    "MarketWatch": "free",
    "CoinDesk": "free",
}

AUTH_SITES = (
    "site:ft.com OR site:bloomberg.com OR site:wsj.com OR site:cnbc.com "
    "OR site:apnews.com OR site:bbc.com OR site:theguardian.com OR site:axios.com "
    "OR site:npr.org OR site:abcnews.go.com OR site:marketwatch.com OR site:scmp.com"
)

NEWS_FEEDS = [
    # Publisher feeds provide direct article URLs. Google News wrappers can
    # expire or trigger a consent page, so they are not used for displayed links.
    ("Financial Times", "https://www.ft.com/rss/markets"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("WSJ", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Guardian", "https://www.theguardian.com/uk/business/rss"),
    ("Axios", "https://api.axios.com/feed/"),
    ("NPR", "https://feeds.npr.org/1006/rss.xml"),
    ("ABC", "https://abcnews.go.com/abcnews/moneyheadlines"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("SCMP", "https://www.scmp.com/rss/91/feed"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Economist", "https://www.economist.com/finance-and-economics/rss.xml"),
]

SOURCE_ALIASES = {
    "ft": "Financial Times",
    "financial times": "Financial Times",
    "bloomberg": "Bloomberg",
    "reuters": "Reuters",
    "wsj": "WSJ",
    "wall street journal": "WSJ",
    "cnbc": "CNBC",
    "bbc": "BBC",
    "associated press": "AP",
    "ap news": "AP",
    "the guardian": "Guardian",
    "axios": "Axios",
    "npr": "NPR",
    "abc news": "ABC",
    "nikkei": "Nikkei",
    "scmp": "SCMP",
    "south china morning": "SCMP",
    "marketwatch": "MarketWatch",
    "economist": "Economist",
    "coindesk": "CoinDesk",
    "barron": "Barron's",
    "caixin": "Caixin",
    "investopedia": "Investopedia",
    "yahoo": "Yahoo Finance",
    "google news": "Google News",
}

ASSET_QUERIES = {
    "spx": '"S&P 500" (Fed OR Treasury OR inflation OR stocks) when:7d',
    "ndx": "Nasdaq (Fed OR tech OR stocks) when:7d",
    "dji": '"Dow Jones" (Fed OR stocks OR yields) when:7d',
    "nky": '"Nikkei 225" OR (Nikkei (BOJ OR yen OR stocks)) when:7d',
    "hsi": '"Hang Seng" (China OR Hong Kong OR stocks) when:7d',
    "sx5e": '"Euro Stoxx" OR (Europe stocks (ECB OR yields)) when:7d',
    "chf": 'USDCHF OR (Swiss franc (SNB OR dollar)) when:7d',
    "eur": "EURUSD OR (euro dollar (ECB OR Fed)) when:7d",
    "jpy": "USDJPY OR (yen (BOJ OR intervention)) when:7d",
    "gbp": "GBPUSD OR (pound dollar (BOE OR rates)) when:7d",
    "aud": "AUDUSD OR (Australian dollar (RBA OR China)) when:7d",
    "cny": "yuan OR renminbi (PBOC OR dollar) when:7d",
    "wti": "WTI OR crude (OPEC OR supply OR Iran) when:7d",
    "wh": "wheat prices (crop OR export OR harvest OR Black Sea) when:7d",
    "au": "gold (yields OR dollar OR Fed) when:7d",
    "ag": "silver (gold OR industrial demand) when:7d",
    "cu": "copper (China OR demand OR supply) when:7d",
    "ng": '"natural gas" (inventory OR LNG OR weather) when:7d',
    "de10y": "Bund yield OR German bond yields (ECB OR eurozone) when:7d",
    "jp10y": "JGB OR Japanese government bond yield (BOJ OR Japan) when:7d",
    "us10y": '"10-year" Treasury yield (Fed OR inflation) when:7d',
    "us30y": '"30-year" Treasury (term premium OR fiscal) when:7d',
    "gb10y": "gilt yields OR UK bond yields (BOE OR gilts) when:7d",
    "ca10y": "Canada bond yield OR Canadian bonds (Bank of Canada) when:7d",
    "btc": "Bitcoin (Fed OR ETF OR crypto) when:7d",
    "eth": "Ethereum (ETF OR crypto OR Fed) when:7d",
    "sol": "Solana (crypto OR ETF) when:7d",
    "xrp": "XRP OR Ripple (SEC OR crypto) when:7d",
    "bnb": "Binance OR BNB (crypto OR regulation) when:7d",
    "doge": "Dogecoin (crypto OR meme) when:7d",
}


LOCK = threading.Lock()
SNAPSHOT: dict = {"status": "booting", "updatedAt": None, "assets": [], "news": [], "newsBySource": {}}
SPECS_BY_ID = {s["id"]: s for s in [*UNIVERSE, *CRYPTO]}
CHART_RANGES = {
    "1D": {"yahoo": ("1d", "1m"), "cg": 1},
    "5D": {"yahoo": ("5d", "5m"), "cg": 7},
    "1M": {"yahoo": ("1mo", "1d"), "cg": 30},
    "3M": {"yahoo": ("3mo", "1d"), "cg": 90},
    "YTD": {"yahoo": ("ytd", "1d"), "cg": 365},
}
CHART_MEM: dict[tuple[str, str], tuple[float, dict]] = {}
CHART_TTL = 20.0
CRYPTO_TTL = 120.0
CRYPTO_LAST_GOOD: dict[str, dict] = {}


def quote_url(spec: dict) -> str:
    if spec.get("cls") == "crypto" and spec.get("cg"):
        return f"https://www.coingecko.com/en/coins/{spec['cg']}"
    if spec.get("feed"):
        return SOV_SOURCE[spec["feed"]][1]
    symbol = spec.get("symbol") or ""
    return "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol, safe="")


def chart_url(spec: dict) -> str | None:
    """Interactive chart for the same instrument the card plots."""
    if spec.get("cls") == "crypto" and spec.get("cg"):
        return f"https://www.coingecko.com/en/coins/{spec['cg']}"
    if spec.get("feed"):
        # Yahoo has no raw Germany/Japan/UK/Canada 10Y yield ticker. The chart
        # already drawn in the card is the official history; do not make it
        # jump to a central-bank landing page that has no matching chart.
        return None
    # /chart/<sym> only resolves for index-style tickers; this form works for all
    symbol = spec.get("symbol") or ""
    return "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol, safe="") + "/chart"


def chart_site(spec: dict) -> str:
    if spec.get("cls") == "crypto":
        return "CoinGecko"
    if spec.get("feed"):
        return f"{SOV_SOURCE[spec['feed']][0]} official data"
    return "Yahoo Finance"


def cross_check(spec: dict) -> dict | None:
    """Independent raw-yield source shown beside each bond chart."""
    if spec.get("feed"):
        name, url = SOV_SOURCE[spec["feed"]]
        return {
            "label": f"Cross-check on {name}",
            "url": url,
            "note": "Yahoo Finance has no raw ticker for this yield; no ETF or fund proxy is used.",
        }
    fred = {"us10y": "DGS10", "us30y": "DGS30"}.get(spec.get("id"))
    if fred:
        return {
            "label": "Cross-check on FRED",
            "url": f"https://fred.stlouisfed.org/series/{fred}",
            "note": "Yahoo Finance chart, checked against the US Treasury daily yield series on FRED.",
        }
    return None


# Third-party charts of the same raw instrument. TradingView embeds only render
# interbank FX and exchange crypto; its index, futures and yield symbols show
# "only available on TradingView", and its CFDs are not the raw print. FRED
# covers the official daily closes it publishes. Anything not listed has no
# embeddable raw-data chart and keeps the dashboard chart only.
REF_CHART = {
    "spx": ("fred", "SP500", "S&P 500 daily close (S&P Dow Jones Indices)", "d"),
    "ndx": ("fred", "NASDAQCOM", "Nasdaq Composite daily close (Nasdaq OMX)", "d"),
    "dji": ("fred", "DJIA", "Dow Jones Industrial Average daily close (S&P Dow Jones Indices)", "d"),
    "nky": ("fred", "NIKKEI225", "Nikkei 225 daily close (Nikkei Inc.)", "d"),
    "wti": ("fred", "DCOILWTICO", "WTI Cushing spot, daily (US EIA)", "d"),
    "ng": ("fred", "DHHNGSP", "Henry Hub natural gas spot, daily (US EIA)", "d"),
    "us10y": ("fred", "DGS10", "10-year Treasury constant-maturity yield, daily (US Treasury)", "d"),
    "us30y": ("fred", "DGS30", "30-year Treasury constant-maturity yield, daily (US Treasury)", "d"),
    "jp10y": ("fred", "IRLTLT01JPM156N", "Japan 10-year government bond yield, monthly average (OECD)", "m"),
    "gb10y": ("fred", "IRLTLT01GBM156N", "UK 10-year gilt yield, monthly average (OECD)", "m"),
    "ca10y": ("fred", "IRLTLT01CAM156N", "Canada 10-year government bond yield, monthly average (OECD)", "m"),
    "eur": ("tv", "FX_IDC:EURUSD", "EUR/USD interbank spot", ""),
    "jpy": ("tv", "FX_IDC:USDJPY", "USD/JPY interbank spot", ""),
    "gbp": ("tv", "FX_IDC:GBPUSD", "GBP/USD interbank spot", ""),
    "chf": ("tv", "FX_IDC:USDCHF", "USD/CHF interbank spot", ""),
    "aud": ("tv", "FX_IDC:AUDUSD", "AUD/USD interbank spot", ""),
    "cny": ("tv", "FX_IDC:USDCNY", "USD/CNY interbank spot", ""),
    "btc": ("tv", "COINBASE:BTCUSD", "Coinbase BTC/USD spot", ""),
    "eth": ("tv", "COINBASE:ETHUSD", "Coinbase ETH/USD spot", ""),
    "sol": ("tv", "COINBASE:SOLUSD", "Coinbase SOL/USD spot", ""),
    "xrp": ("tv", "COINBASE:XRPUSD", "Coinbase XRP/USD spot", ""),
    "bnb": ("tv", "BINANCE:BNBUSDT", "Binance BNB/USDT spot", ""),
    "doge": ("tv", "COINBASE:DOGEUSD", "Coinbase DOGE/USD spot", ""),
}
REF_IMG: dict[str, tuple[float, bytes]] = {}
REF_TTL = 3 * 3600.0


def ref_chart(asset_id: str) -> dict | None:
    item = REF_CHART.get(asset_id)
    if not item:
        return None
    kind, symbol, what, freq = item
    if kind == "fred":
        return {
            "kind": "fred",
            "site": "FRED",
            "symbol": symbol,
            "what": what,
            "freq": "monthly" if freq == "m" else "daily",
            "img": f"/api/refchart?id={asset_id}",
            "url": f"https://fred.stlouisfed.org/series/{symbol}",
        }
    return {
        "kind": "tv",
        "site": "TradingView",
        "symbol": symbol,
        "what": what,
        "url": "https://www.tradingview.com/symbols/" + symbol.replace(":", "-") + "/",
    }


def fred_png(asset_id: str) -> bytes | None:
    item = REF_CHART.get(asset_id)
    if not item or item[0] != "fred":
        return None
    hit = REF_IMG.get(asset_id)
    if hit and time.time() - hit[0] < REF_TTL:
        return hit[1]
    back = timedelta(days=5 * 365) if item[3] == "m" else timedelta(days=183)
    start = (datetime.now(HKT).date() - back).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.png?id={item[1]}&cosd={start}&width=720&height=340"
    try:
        # FRED stalls requests whose User-Agent it does not recognise
        body = http_get(url, timeout=30, ua="curl/8.7.1")
    except Exception:
        return hit[1] if hit else None
    if not body.startswith(b"\x89PNG"):
        return hit[1] if hit else None
    REF_IMG[asset_id] = (time.time(), body)
    return body


REF_WARM = threading.Lock()


def warm_ref_charts() -> None:
    if not REF_WARM.acquire(blocking=False):
        return
    try:
        for asset_id, item in REF_CHART.items():
            if item[0] == "fred":
                fred_png(asset_id)
    finally:
        REF_WARM.release()


def http_get(url: str, timeout: int = 16, ua: str = UA) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
            code = getattr(exc, "code", None)
            if code in (401, 404):
                raise
            time.sleep(0.4 * (attempt + 1))
    raise last or TimeoutError("http_get failed")


def fetch_chart(symbol: str, range_: str = "ytd", interval: str = "1d") -> dict | None:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol, safe="")
        + f"?range={urllib.parse.quote(range_)}&interval={urllib.parse.quote(interval)}&includePrePost=false"
    )
    last_err: Exception | None = None
    for _ in range(3):
        try:
            raw = http_get(url, timeout=14)
            payload = json.loads(raw.decode("utf-8"))
            result = (payload.get("chart") or {}).get("result") or []
            if result:
                return result[0]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            last_err = exc
            time.sleep(0.3)
    # Yahoo rejects this server's address. CNBC's public quote is the same print.
    chart = fetch_cnbc_chart(symbol, range_, interval)
    if chart is None and last_err is not None:
        print(f"[warn] {symbol} quote failed: {last_err}", flush=True)
    return chart


def _cnbc_float(text) -> float | None:
    if text in (None, "", "UNCH"):
        return None
    cleaned = str(text).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _cnbc_epoch(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            stamp = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=NY)
        return int(stamp.timestamp())
    return None


# Yahoo symbol -> CNBC symbol. These are the cash index, spot FX, futures and yield, not an ETF.
CNBC_SYMBOL = {
    "^GSPC": ".SPX",
    "^IXIC": ".IXIC",
    "^DJI": ".DJI",
    "^N225": ".N225",
    "^HSI": ".HSI",
    "^STOXX50E": ".STOXX50E",
    "EURUSD=X": "EUR=",
    "USDJPY=X": "JPY=",
    "GBPUSD=X": "GBP=",
    "USDCHF=X": "CHF=",
    "AUDUSD=X": "AUD=",
    "USDCNY=X": "CNY=",
    "CL=F": "@CL.1",
    "NG=F": "@NG.1",
    "GC=F": "@GC.1",
    "SI=F": "@SI.1",
    "HG=F": "@HG.1",
    "ZW=F": "@W.1",
    "^TNX": "US10Y",
    "^TYX": "US30Y",
}
CNBC_PERIOD = {
    ("1d", "1m"): "1D",
    ("5d", "5m"): "5D",
    ("1mo", "1d"): "1M",
    ("3mo", "1d"): "3M",
    ("ytd", "1d"): "YTD",
}
_CNBC_BARS: dict[tuple[str, str], tuple[float, list[tuple[int, float]]]] = {}
_CNBC_QUOTE: dict[str, tuple[float, dict]] = {}


def _cnbc_bars(symbol: str, period: str) -> list[tuple[int, float]]:
    key = (symbol, period)
    hit = _CNBC_BARS.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    url = "https://ts-api.cnbc.com/harmony/app/charts/" + urllib.parse.quote(period) + ".json?symbol=" + urllib.parse.quote(symbol)
    payload = json.loads(http_get(url, timeout=12, ua="Mozilla/5.0").decode("utf-8"))
    bars = []
    for row in ((payload.get("barData") or {}).get("priceBars") or []):
        close = _cnbc_float(row.get("close"))
        stamp = row.get("tradeTimeinMills")
        if close is None or not isinstance(stamp, (int, float)):
            continue
        bars.append((int(stamp) // 1000, close))
    bars.sort()
    if bars:
        _CNBC_BARS[key] = (time.time(), bars)
    return bars


def _cnbc_quote(symbol: str) -> dict:
    hit = _CNBC_QUOTE.get(symbol)
    if hit and time.time() - hit[0] < 45:
        return hit[1]
    url = (
        "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols="
        + urllib.parse.quote(symbol)
        + "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1"
    )
    payload = json.loads(http_get(url, timeout=12, ua="Mozilla/5.0").decode("utf-8"))
    quote = (payload.get("FormattedQuoteResult") or {}).get("FormattedQuote") or {}
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    if quote.get("code") in (0, "0"):
        _CNBC_QUOTE[symbol] = (time.time(), quote)
    return quote


def fetch_cnbc_chart(symbol: str, range_: str, interval: str) -> dict | None:
    """Yahoo-shaped chart built from CNBC, so the rest of the board can stay unchanged."""
    mapped = CNBC_SYMBOL.get(symbol)
    period = CNBC_PERIOD.get((range_, interval), "YTD")
    if not mapped:
        return None
    try:
        bars = _cnbc_bars(mapped, period)
        quote = _cnbc_quote(mapped)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[warn] CNBC {symbol}: {exc}", flush=True)
        return None
    if len(bars) < 2:
        return None
    last = _cnbc_float(quote.get("last"))
    if last is None:
        last = bars[-1][1]
    change = _cnbc_float(quote.get("change_pct"))
    if quote.get("change_pct") == "UNCH":
        change = 0.0
    when = _cnbc_epoch(quote.get("last_time") or "") or bars[-1][0]
    print(f"[info] {symbol} via CNBC", flush=True)
    return {
        "timestamp": [t for t, _c in bars],
        "indicators": {"quote": [{"close": [c for _t, c in bars]}]},
        "meta": {
            "regularMarketPrice": last,
            "regularMarketChangePercent": change,
            "regularMarketTime": when,
            "currency": "USD",
            "quoteVendor": "CNBC",
        },
    }


SOV_MEM: dict[str, tuple[float, dict]] = {}
SOV_TTL = 900.0
SOV_SOURCE = {
    "ecb": ("ECB Data Portal", "https://data.ecb.europa.eu/data/datasets/YC"),
    "jgb": ("Japan MoF", "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm"),
    "boe": ("Bank of England", "https://www.bankofengland.co.uk/boeapps/database/"),
    "boc": ("Bank of Canada", "https://www.bankofcanada.ca/rates/interest-rates/lookup-bond-yields/"),
}


def _sov_csv(url: str) -> list[str]:
    return http_get(url, timeout=20).decode("utf-8", errors="replace").strip().splitlines()


def sov_ecb() -> list[tuple[str, float]]:
    """Euro area AAA government 10Y spot rate, daily."""
    url = (
        "https://data-api.ecb.europa.eu/service/data/YC/"
        "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&lastNObservations=400"
    )
    out = []
    for line in _sov_csv(url)[1:]:
        parts = line.split(",")
        if len(parts) > 9:
            try:
                out.append((parts[8], float(parts[9])))
            except ValueError:
                continue
    return out


def sov_jgb() -> list[tuple[str, float]]:
    """JGB 10Y from the MoF fixing; the year file stops last month, so add the current one."""
    base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    out: dict[str, float] = {}
    for name in ("historical/jgbcme_all.csv", "jgbcme.csv"):
        try:
            lines = _sov_csv(base + name)
        except Exception:
            continue
        for line in lines:
            parts = line.split(",")
            if len(parts) < 11 or not re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", parts[0].strip()):
                continue
            try:
                y, m, d = (int(x) for x in parts[0].strip().split("/"))
                out[f"{y:04d}-{m:02d}-{d:02d}"] = float(parts[10])
            except ValueError:
                continue
    return sorted(out.items())


def sov_boe() -> list[tuple[str, float]]:
    """IUDMNZC — 10-year nominal par gilt yield, daily."""
    today = datetime.now(LONDON)
    start = today.replace(year=today.year - 1)
    url = (
        "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp?csv.x=yes"
        f"&Datefrom={start:%d/%b/%Y}&Dateto={today:%d/%b/%Y}"
        "&SeriesCodes=IUDMNZC&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    out = []
    for line in _sov_csv(url)[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            day = datetime.strptime(parts[0].strip(), "%d %b %Y")
            out.append((day.strftime("%Y-%m-%d"), float(parts[1])))
        except ValueError:
            continue
    return out


def sov_boc() -> list[tuple[str, float]]:
    """Government of Canada benchmark 10-year bond yield, daily."""
    url = "https://www.bankofcanada.ca/valet/observations/BD.CDN.10YR.DQ.YLD/json?recent=400"
    payload = json.loads(http_get(url, timeout=20).decode("utf-8"))
    out = []
    for obs in payload.get("observations") or []:
        val = (obs.get("BD.CDN.10YR.DQ.YLD") or {}).get("v")
        try:
            out.append((obs["d"], float(val)))
        except (TypeError, ValueError, KeyError):
            continue
    return sorted(out)


SOV_FETCH = {"ecb": sov_ecb, "jgb": sov_jgb, "boe": sov_boe, "boc": sov_boc}

# CNBC's quote time is US Eastern. These are exchange yields, not the official fixing,
# so a missing session is added by applying the exchange's own move to the last fixing.
CNBC_YIELD = {
    "ecb": ("DE10Y-DE", "Europe/Berlin", "German exchange 10Y yield"),
    "boe": ("GB10Y-GB", "Europe/London", "UK exchange 10Y gilt yield"),
    "jgb": ("JP10Y-JP", "Asia/Tokyo", "Japan exchange 10Y yield"),
    "boc": ("CA10Y-CA", "America/Toronto", "Canada exchange 10Y yield"),
}
ET = ZoneInfo("America/New_York")


def cnbc_yield_closes(symbol: str, zone_name: str) -> dict[str, float]:
    """Last exchange print of each local session over the past five days."""
    url = f"https://ts-api.cnbc.com/harmony/app/charts/5D.json?symbol={urllib.parse.quote(symbol)}"
    bars = json.loads(http_get(url, timeout=12).decode("utf-8"))["barData"]["priceBars"]
    zone = ZoneInfo(zone_name)
    last: dict[str, float] = {}
    for row in bars:
        close = row.get("close")
        stamp = row.get("tradeTime")
        if not stamp or not isinstance(close, str):
            continue
        local = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=ET).astimezone(zone)
        day = local.date()
        # A print just after midnight belongs to the session that was still open.
        if local.hour < 7:
            day -= timedelta(days=1)
        if day.weekday() >= 5:
            continue
        last[day.isoformat()] = float(close)
    return last


def extend_fixing(feed: str, stamps: list[int], closes: list[float]) -> dict | None:
    """Fill sessions the official file has not published yet, from the exchange quote."""
    symbol, zone, label = CNBC_YIELD[feed]
    market = cnbc_yield_closes(symbol, zone)
    if not stamps or not market:
        return None
    official = datetime.fromtimestamp(stamps[-1], timezone.utc).date().isoformat()
    anchor = market.get(official)
    if not anchor:
        return None
    today = datetime.now(HKT).date().isoformat()
    base = closes[-1]
    filled = []
    for day in sorted(market):
        if day <= official or day > today:
            continue
        level = base * (market[day] / anchor)
        stamp = int(datetime.strptime(day, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc).timestamp())
        stamps.append(stamp)
        closes.append(round(level, 6))
        filled.append(day)
    if not filled:
        return None
    return {"through": official, "filled": filled, "source": label, "symbol": symbol}


def fetch_sovereign(spec: dict) -> dict | None:
    """Daily yield history shaped like a Yahoo chart result so build_row is unchanged."""
    feed = spec["feed"]
    now = time.time()
    hit = SOV_MEM.get(feed)
    if hit and now - hit[0] < SOV_TTL:
        return hit[1]
    try:
        series = SOV_FETCH[feed]()
    except Exception as exc:
        print(f"[warn] {feed} yield feed: {exc}", flush=True)
        return hit[1] if hit else None
    series = [(d, v) for d, v in series if isinstance(v, float)]
    if not series:
        return hit[1] if hit else None
    stamps, closes = [], []
    for day, value in series:
        try:
            dt = datetime.strptime(day, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc)
        except ValueError:
            continue
        stamps.append(int(dt.timestamp()))
        closes.append(value)
    fill = extend_fixing(feed, stamps, closes)
    result = {
        "meta": {
            "regularMarketPrice": closes[-1],
            "regularMarketTime": stamps[-1],
            "currency": "%",
            "marketState": "CLOSED",
            "exchangeFill": fill,
        },
        "timestamp": stamps,
        "indicators": {"quote": [{"close": closes}]},
    }
    SOV_MEM[feed] = (now, result)
    return result


def yahoo_points(result: dict) -> list[dict]:
    ts = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote") or [{}]
    closes = (quote[0] or {}).get("close") or []
    pts: list[dict] = []
    last_t = -1
    for t, c in zip(ts, closes):
        if t is None or not isinstance(c, (int, float)):
            continue
        t = int(t)
        if t <= last_t:
            continue
        last_t = t
        pts.append({"time": t, "value": round(float(c), 6)})
    return pts


def fetch_crypto_points(cg: str, days: int) -> list[dict]:
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        + urllib.parse.quote(cg)
        + f"/market_chart?vs_currency=usd&days={int(days)}"
    )
    try:
        payload = json.loads(http_get(url, timeout=14).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, urllib.error.HTTPError):
        return []
    pts: list[dict] = []
    last_t = -1
    for row in payload.get("prices") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        t, px = row[0], row[1]
        if not isinstance(px, (int, float)):
            continue
        t = int(t / 1000)
        if t <= last_t:
            continue
        last_t = t
        pts.append({"time": t, "value": round(float(px), 6)})
    return pts


# Same venues the browser streams ticks from, so history and live price join cleanly.
EXCHANGE = {
    "btc": ("coinbase", "BTC-USD", "Coinbase BTC/USD"),
    "eth": ("coinbase", "ETH-USD", "Coinbase ETH/USD"),
    "sol": ("coinbase", "SOL-USD", "Coinbase SOL/USD"),
    "xrp": ("coinbase", "XRP-USD", "Coinbase XRP/USD"),
    "doge": ("coinbase", "DOGE-USD", "Coinbase DOGE/USD"),
    "bnb": ("okx", "BNB-USDT", "OKX BNB/USDT"),
}
# range -> (lookback seconds, candle seconds)
EXCHANGE_BARS = {
    "1D": (86400, 300),
    "5D": (5 * 86400, 900),
    "1M": (31 * 86400, 3600),
    "3M": (92 * 86400, 21600),
    "YTD": (None, 86400),
}
OKX_BAR = {300: "5m", 900: "15m", 3600: "1H", 21600: "6Hutc", 86400: "1Dutc"}


def fetch_exchange_candles(asset_id: str, range_key: str) -> tuple[list[dict], int, str]:
    venue, product, label = EXCHANGE[asset_id]
    lookback, step = EXCHANGE_BARS[range_key]
    end = int(time.time())
    if lookback is None:
        start = int(datetime(datetime.now(HKT).year, 1, 1, tzinfo=HKT).timestamp())
    else:
        start = end - lookback
    rows: dict[int, float] = {}
    if venue == "coinbase":
        chunk = 300 * step
        t0 = start
        while t0 < end:
            t1 = min(end, t0 + chunk)
            url = (
                f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={step}"
                f"&start={datetime.fromtimestamp(t0, timezone.utc).isoformat()}"
                f"&end={datetime.fromtimestamp(t1, timezone.utc).isoformat()}"
            )
            for c in json.loads(http_get(url, timeout=12).decode("utf-8")):
                rows[int(c[0])] = float(c[4])
            t0 = t1
    else:
        after = None
        while True:
            url = f"https://www.okx.com/api/v5/market/history-candles?instId={product}&bar={OKX_BAR[step]}&limit=100"
            if after:
                url += f"&after={after}"
            data = json.loads(http_get(url, timeout=12).decode("utf-8")).get("data") or []
            if not data:
                break
            for c in data:
                rows[int(c[0]) // 1000] = float(c[4])
            oldest = min(int(c[0]) for c in data)
            if oldest // 1000 <= start:
                break
            after = oldest
        recent = f"https://www.okx.com/api/v5/market/candles?instId={product}&bar={OKX_BAR[step]}&limit=5"
        for c in json.loads(http_get(recent, timeout=12).decode("utf-8")).get("data") or []:
            rows[int(c[0]) // 1000] = float(c[4])
    pts = [{"time": t, "value": round(v, 6)} for t, v in sorted(rows.items()) if t >= start]
    return pts, step, label


def cached_snapshot_points(asset_id: str, range_key: str) -> list[dict]:
    """Visible chart fallback built from the last persisted, verified snapshot."""
    with LOCK:
        row = next((r for r in (SNAPSHOT.get("assets") or []) if r.get("id") == asset_id), None)
    if not row:
        return []
    values = [float(v) for v in (row.get("sparkPx") or []) if isinstance(v, (int, float))]
    if len(values) < 2:
        return []
    counts = {"1D": 2, "5D": 6, "1M": 23, "3M": 48, "YTD": 48}
    values = values[-counts.get(range_key, 23) :]
    end = row.get("quoteTime")
    if not isinstance(end, (int, float)):
        end = int(time.time())
    if row.get("cls") == "crypto":
        # CoinGecko's seven-day sparkline is hourly.
        step = max(300, int(7 * 86400 / max(1, len(row.get("sparkPx") or []) - 1)))
    else:
        step = 86400
    start = int(end) - step * (len(values) - 1)
    return [
        {"time": start + i * step, "value": round(value, 6)}
        for i, value in enumerate(values)
    ]


def series_for(asset_id: str, range_key: str) -> dict:
    spec = SPECS_BY_ID.get(asset_id)
    if not spec or range_key not in CHART_RANGES:
        return {"ok": False, "points": [], "error": "unknown id or range"}
    key = (asset_id, range_key)
    now = time.time()
    # crypto lines are driven by exchange ticks in the browser, so the CoinGecko
    # history behind them can be cached far longer without looking stale
    ttl = CRYPTO_TTL if spec.get("cls") == "crypto" else CHART_TTL
    hit = CHART_MEM.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    cfg = CHART_RANGES[range_key]
    as_of = None
    step = None
    if spec.get("cls") == "crypto":
        points = []
        try:
            points, step, source = fetch_exchange_candles(asset_id, range_key)
        except Exception as exc:
            print(f"[warn] {asset_id} exchange candles: {exc}", flush=True)
        if len(points) < 2:
            points = fetch_crypto_points(spec["cg"], cfg["cg"])
            source, step = "CoinGecko", None
        unit = "USD"
        if points:
            as_of = points[-1]["time"]
    elif spec.get("feed"):
        result = fetch_sovereign(spec)
        points = yahoo_points(result) if result else []
        if range_key == "YTD":
            jan1 = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp()
            points = [p for p in points if p["time"] >= jan1]
        else:
            points = points[-{"1D": 5, "5D": 10, "1M": 31, "3M": 92}.get(range_key, 31) :]
        source = SOV_SOURCE[spec["feed"]][0]
        fill = ((result or {}).get("meta") or {}).get("exchangeFill")
        if fill:
            source = f"{source} · latest from {fill['source']} (CNBC)"
        unit = "yield %"
        if points:
            as_of = points[-1]["time"]
    else:
        y_range, y_int = cfg["yahoo"]
        result = fetch_chart(spec["symbol"], y_range, y_int)
        points = yahoo_points(result) if result else []
        source = "Yahoo Finance"
        unit = "index" if spec.get("kind") == "index" else spec.get("kind") or "price"
        if spec.get("kind") == "yield":
            unit = "yield %"
        meta = (result or {}).get("meta") or {}
        tick, tick_t = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        if isinstance(tick, (int, float)) and isinstance(tick_t, (int, float)):
            tick_t = int(tick_t)
            as_of = tick_t
            if points and tick_t <= points[-1]["time"]:
                points[-1] = {"time": points[-1]["time"], "value": round(float(tick), 6)}
            elif points:
                points.append({"time": tick_t, "value": round(float(tick), 6)})
    if len(points) < 2 and hit and len(hit[1].get("points") or []) >= 2:
        # a throttled upstream must not blank a chart that already drew fine
        payload = {**hit[1], "stale": True, "fallback": "last successful chart"}
        CHART_MEM[key] = (now - ttl / 2, payload)
        return payload
    if len(points) < 2:
        points = cached_snapshot_points(asset_id, range_key)
        if points:
            as_of = points[-1]["time"]
            source = f"{source} · cached daily fallback"
    last = points[-1]["value"] if points else None
    payload = {
        "ok": bool(points),
        "id": asset_id,
        "label": spec["label"],
        "range": range_key,
        "source": source,
        "unit": unit,
        "last": last,
        "asOf": as_of,
        "points": points,
        "step": step,
        "stale": "cached daily fallback" in source,
        "fallback": "persisted snapshot" if "cached daily fallback" in source else None,
    }
    CHART_MEM[key] = (now, payload)
    return payload


def prior_session_close(last: float | None, closes: list[float]) -> float | None:
    """Previous completed session, not the chart-range start Yahoo calls chartPreviousClose."""
    if len(closes) < 2:
        return closes[-1] if closes and last is not None and abs(closes[-1] - last) > 1e-6 else None
    if last is None:
        return closes[-2]
    tol = max(0.02, abs(last) * 1e-5)
    if abs(closes[-1] - last) <= tol:
        return closes[-2]
    return closes[-1]


def slim_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def pct(new: float, old: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return (new / old - 1.0) * 100.0


def last_n_change(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return pct(closes[-1], closes[0]) if len(closes) >= 2 else None
    return pct(closes[-1], closes[-(n + 1)])


def downsample(series: list[float], max_n: int = 48) -> list[float]:
    if len(series) <= max_n:
        return series
    step = (len(series) - 1) / (max_n - 1)
    out = [series[round(i * step)] for i in range(max_n)]
    out[0], out[-1] = series[0], series[-1]
    hi_i, lo_i = series.index(max(series)), series.index(min(series))
    out[min(max_n - 1, round(hi_i / step))] = series[hi_i]
    out[min(max_n - 1, round(lo_i / step))] = series[lo_i]
    return out


def pack_spark(closes: list[float], n: int = 30) -> tuple[list[float], list[float]]:
    series = [float(c) for c in closes[-n:] if isinstance(c, (int, float))]
    series = downsample(series, 48)
    if len(series) < 2:
        return [], []
    lo, hi = min(series), max(series)
    span = hi - lo or 1.0
    norm = [round((v - lo) / span, 4) for v in series]
    raw = [round(v, 6) for v in series]
    return norm, raw


def format_px(value: float, kind: str) -> str:
    if kind == "yield":
        return f"{value:.3f}%"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 100:
        return f"{value:,.2f}"
    if abs(value) >= 10:
        return f"{value:.3f}"
    return f"{value:.4f}"


def format_cap(value: float | None) -> str | None:
    if not value:
        return None
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def empty_row(spec: dict) -> dict:
    return {
        **spec,
        "ok": False,
        "last": None,
        "lastDisplay": "—",
        "dayPct": None,
        "dayBps": None,
        "weekPct": None,
        "monthPct": None,
        "ytdPct": None,
        "spark": [],
        "sparkPx": [],
        "currency": None,
        "mcap": None,
        "volume": None,
        "explanation": "Feed delayed",
        "next": "Retry",
        "moverRank": None,
        "source": "Yahoo Finance",
        "lag": "Delayed ~15m",
        "ranges": ["1M", "3M", "YTD"] if spec.get("feed") else ["1D", "5D", "1M", "3M", "YTD"],
        "sessionCode": "closed",
        "sessionLabel": "CLOSED",
        # a failed fetch must not strip the card's link to its own data source
        "sourceUrl": quote_url(spec) if spec.get("symbol") or spec.get("feed") or spec.get("cg") else None,
        "chartUrl": chart_url(spec) if spec.get("symbol") or spec.get("feed") or spec.get("cg") else None,
        "chartSite": chart_site(spec),
        "crossCheck": cross_check(spec),
        "headlines": [],
        "summary": "",
    }


def narrative(row: dict, regime: str) -> tuple[str, str]:
    chg = row.get("dayPct") or 0.0
    kind = row["kind"]
    label = row["label"]
    m1 = row.get("monthPct")
    up = chg >= 0
    abs_chg = abs(chg)

    if kind == "yield":
        bps = row.get("dayBps")
        bps_txt = f"{bps:+.1f}bp" if bps is not None else f"{chg:+.2f}%"
        expl = (
            f"Benchmark yield {('bid higher' if up else 'marked lower')} ({bps_txt}); duration {('hurt' if up else 'helped')}"
        )
        nxt = "Further backup in yields" if up else "Yields may ease / duration bid"
        if "10Y" in label:
            expl = f"US 10Y — class benchmark — {bps_txt} today; sets discount rates for every other market"
            last_y = row.get("last") or 0
            nxt = "Watch 5% handle and Fed-speak" if last_y >= 4.9 else "Watch 4.75–5.00% range"
        if "13W" in label:
            expl = f"Front-end bill yield {bps_txt} — closest listed proxy for policy / cash rates"
            nxt = "Anchored by Fed funds path"
        if "30Y" in label:
            expl = f"Long end {bps_txt} — term premium / fiscal narrative"
            nxt = "Steepener if long end leads; flattener if front end does"
        if "5Y" in label:
            expl = f"Belly of the curve {bps_txt} — sensitive to medium-term inflation / cuts"
            nxt = "Follow 10Y; belly often leads the narrative"
        return expl, nxt

    if kind == "price" and row["cls"] == "bonds":
        expl = f"Bond PRICE {('up' if up else 'down')} {abs_chg:.2f}% — inverse of yields"
        nxt = "Further price pressure if 10Y keeps rising" if not up else "Duration rally if yields reverse"
        return expl, nxt

    if row["cls"] == "equities":
        expl = f"{'Risk-on bid' if up else 'Risk-off offer'} in {row['region']} equities"
        if abs_chg < 0.15:
            expl = f"Quiet tape in {label} — digestion, not a new story"
        if m1 is not None and (m1 > 0) != up and abs_chg >= 0.3:
            expl = f"Today fades the 1-month trend in {label}"
            return expl, "Watch for reversal vs continuation"
        nxt = "Further upward" if up else "Further downward"
        if regime == "risk-off" and up:
            nxt = "Counter-trend bounce — fade or follow?"
        return expl, nxt

    if row["cls"] == "fx":
        if "Dollar Index" in label:
            expl = f"USD vs a basket of 6 majors — {'firmer' if up else 'softer'} {abs_chg:.2f}%"
            nxt = "USD bid if yields stay high" if up else "USD offered if risk-on continues"
            return expl, nxt
        expl = f"{label} {'higher' if up else 'lower'} {abs_chg:.2f}% today"
        nxt = "Follow DXY and rate differentials"
        return expl, nxt

    if row["cls"] == "commodities":
        if "Crude" in label or "Brent" in label:
            expl = f"Energy complex {'bid' if up else 'sold'} — growth / supply narrative"
            nxt = "Watch inventory + geopolitics; beta to global growth"
        elif label == "Gold":
            expl = f"Precious-metal hedge {'in demand' if up else 'offered'} vs real yields / USD"
            nxt = "Gold follows real yields and DXY more than a single print"
        elif label == "Silver":
            expl = f"Higher-beta precious metal — {('follows gold + industrial bid' if up else 'risk-off / profit-take')}"
            nxt = "Track gold ratio and industrial demand"
        elif label == "Copper":
            expl = f"Dr. Copper — {'growth impulse' if up else 'growth scare'} in the metals pit"
            nxt = "China + global PMIs next"
        else:
            expl = f"{label} {'bid' if up else 'offered'} {abs_chg:.2f}% — weather / storage / seasonality"
            nxt = "Idiosyncratic — less macro than oil/gold"
        return expl, nxt

    if label == "Bitcoin":
        expl = f"Sentiment asset — no cash flows; {'bid' if up else 'risk-off'} {abs_chg:.2f}%"
        nxt = "Further upward with liquidity" if up else "Watch round-numbers / ETF flows"
        return expl, nxt
    expl = f"{label} {'higher' if up else 'lower'} — high-beta to Bitcoin"
    nxt = "Follows BTC more than fundamentals"
    return expl, nxt


def _minutes(t: dtime) -> int:
    return t.hour * 60 + t.minute


def _in_range(now_t: dtime, start: dtime, end: dtime) -> bool:
    n, a, b = _minutes(now_t), _minutes(start), _minutes(end)
    if a <= b:
        return a <= n < b
    return n >= a or n < b


def weekend_fx_futures_open(now: datetime) -> bool:
    ny = now.astimezone(NY)
    wd = ny.weekday()
    mins = _minutes(ny.time())
    close = 17 * 60
    open_sun = 18 * 60
    if wd == 5:
        return False
    if wd == 6:
        return mins >= open_sun
    if wd == 4:
        return mins < close
    return True


def session_status(spec: dict, meta: dict | None) -> tuple[str, str]:
    """Cash/session status for the board. Crypto is 24/7; listed markets are not."""
    if spec["cls"] == "crypto":
        return "open", "OPEN 24/7"
    if spec.get("feed"):
        # official fixings publish once a day, so there is no live session to show
        return "closed", "DAILY FIX"
    now = datetime.now(timezone.utc)
    yahoo = ((meta or {}).get("marketState") or "").upper()
    if spec["cls"] == "equities":
        mapping = {
            "REGULAR": ("open", "OPEN"),
            "PRE": ("pre", "PRE-MARKET"),
            "PREPRE": ("pre", "PRE-MARKET"),
            "POST": ("post", "AFTER HOURS"),
            "POSTPOST": ("post", "AFTER HOURS"),
            "CLOSED": ("closed", "CLOSED"),
        }
        if yahoo in mapping:
            return mapping[yahoo]
        local = {
            "US": (NY, dtime(9, 30), dtime(16, 0)),
            "JP": (TOKYO, dtime(9, 0), dtime(15, 0)),
            "HK": (HKT, dtime(9, 30), dtime(16, 0)),
            "EU": (LONDON, dtime(8, 0), dtime(16, 30)),
        }.get(spec.get("region"), (NY, dtime(9, 30), dtime(16, 0)))
        tz, start, end = local
        here = now.astimezone(tz)
        if here.weekday() >= 5:
            return "closed", "CLOSED"
        return ("open", "OPEN") if _in_range(here.time(), start, end) else ("closed", "CLOSED")
    if spec["cls"] == "fx" or spec["kind"] in ("energy", "precious", "metal", "ags"):
        if not weekend_fx_futures_open(now):
            return "closed", "CLOSED"
        if yahoo == "CLOSED":
            return "closed", "CLOSED"
        ny = now.astimezone(NY)
        if ny.weekday() < 5 and 17 * 60 <= _minutes(ny.time()) < 18 * 60:
            return "closed", "SETTLEMENT BREAK"
        return "open", "OPEN"
    # US cash bonds / TLT
    if yahoo in ("REGULAR",):
        return "open", "OPEN"
    if yahoo in ("PRE", "PREPRE"):
        return "pre", "PRE-MARKET"
    if yahoo in ("POST", "POSTPOST"):
        return "post", "AFTER HOURS"
    if yahoo == "CLOSED":
        return "closed", "CLOSED"
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return "closed", "CLOSED"
    return ("open", "OPEN") if _in_range(ny.time(), dtime(8, 0), dtime(17, 0)) else ("closed", "CLOSED")


def class_session(rows: list[dict]) -> str:
    codes = {r.get("sessionCode") for r in rows}
    if codes == {"open"}:
        return "OPEN"
    if codes <= {"closed"}:
        return "CLOSED"
    if "open" in codes:
        return "MIXED"
    return "CLOSED"


def build_row(spec: dict, chart: dict | None) -> dict:
    row = empty_row(spec)
    if not chart:
        return row
    meta = chart.get("meta") or {}
    quote = (chart.get("indicators") or {}).get("quote") or [{}]
    closes_raw = (quote[0] or {}).get("close") or []
    closes = [c for c in closes_raw if isinstance(c, (int, float))]
    last = meta.get("regularMarketPrice")
    if last is None and closes:
        last = closes[-1]
    # This is the percentage Yahoo shows beside the quote. Recomputing it from
    # daily chart bars is not equivalent: FX uses a separate reference close,
    # while futures can roll contracts between two adjacent bars.
    yahoo_chg = meta.get("regularMarketChangePercent")
    if isinstance(yahoo_chg, (int, float)) and not (
        spec["kind"] == "yield" and abs(yahoo_chg) > 20
    ):
        chg = float(yahoo_chg)
        prev = last / (1 + chg / 100) if last is not None and chg != -100 else None
        change_basis = "Yahoo Finance official session change"
    else:
        prev = prior_session_close(last, closes)
        chg = pct(last, prev)
        change_basis = "latest price versus previous daily close"
    # Keep 1W/1M anchored to the live quote, but do not add an extra session.
    # Replacing the last bar handles a quote that updated after the daily chart.
    stat_closes = list(closes)
    if last is not None:
        if stat_closes:
            stat_closes[-1] = last
        else:
            stat_closes = [last]
    week = last_n_change(stat_closes, 5)
    month = last_n_change(stat_closes, 21)
    ytd = pct(last, closes[0]) if closes else None
    spark_n, spark_px = pack_spark(stat_closes)
    bps = None
    if spec["kind"] == "yield" and last is not None and prev not in (None, 0):
        bps = (last - prev) * 100.0
        if abs(bps) > 80 and len(closes) >= 2:
            bps = (last - closes[-2]) * 100.0
            chg = pct(last, closes[-2])
    code, label = session_status(spec, meta)
    row.update(
        {
            "ok": last is not None,
            "last": last,
            "lastDisplay": format_px(last, spec["kind"]) if last is not None else "—",
            "dayPct": slim_pct(chg),
            "dayBps": round(bps, 1) if bps is not None else None,
            "changeBasis": change_basis,
            "quoteTime": meta.get("regularMarketTime"),
            "weekPct": slim_pct(week),
            "monthPct": slim_pct(month),
            "ytdPct": slim_pct(ytd),
            "spark": spark_n,
            "sparkPx": spark_px,
            "currency": meta.get("currency"),
            "source": (meta.get("quoteVendor") or chart_site(spec)),
            "lag": "CNBC quote" if meta.get("quoteVendor") else ("Daily fixing" if spec.get("feed") else "Delayed ~15m"),
            "ranges": ["1M", "3M", "YTD"] if spec.get("feed") else ["1D", "5D", "1M", "3M", "YTD"],
            "sessionCode": code,
            "sessionLabel": label,
            "marketState": (meta.get("marketState") or ""),
            "sourceUrl": quote_url(spec),
            "chartUrl": chart_url(spec),
            "chartSite": chart_site(spec),
            "crossCheck": cross_check(spec),
            "headlines": [],
        }
    )
    fill = meta.get("exchangeFill")
    if fill:
        shown = [datetime.strptime(d, "%Y-%m-%d").strftime("%-d %b") for d in fill["filled"]]
        when = " and ".join(shown)
        through = datetime.strptime(fill["through"], "%Y-%m-%d").strftime("%-d %b")
        row["exchangeFill"] = f"{fill['source']} via CNBC for {when}"
        row["lag"] = "Official fixing, plus the exchange quote"
        row["changeBasis"] = (
            f"Official fixing through {through}. {when} {'uses' if len(shown) == 1 else 'use'} the {fill['source']} on CNBC, "
            "scaled onto that fixing so the change equals the exchange move."
        )
        if row.get("crossCheck"):
            row["crossCheck"] = {**row["crossCheck"], "note": row["changeBasis"]}
    return row


def fetch_crypto() -> dict[str, dict]:
    ids = ",".join(spec["cg"] for spec in CRYPTO)
    url = (
        "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
        f"&ids={ids}&order=market_cap_desc&sparkline=true"
        "&price_change_percentage=24h,7d,30d"
    )
    try:
        payload = json.loads(http_get(url, timeout=18).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # CoinGecko's free tier throttles datacenter addresses. Use the exchange print.
        payload = []
    if not isinstance(payload, list):
        payload = []
    by_cg = {item.get("id"): item for item in payload if isinstance(item, dict)}
    history: dict[str, list] = {}
    if by_cg:
        with ThreadPoolExecutor(max_workers=len(CRYPTO)) as pool:
            history = dict(zip([s["cg"] for s in CRYPTO], pool.map(crypto_history, [s["cg"] for s in CRYPTO])))
    out: dict[str, dict] = {}
    for spec in CRYPTO:
        item = by_cg.get(spec["cg"])
        if not item:
            continue
        last = item.get("current_price")
        chg = item.get("price_change_percentage_24h")
        if chg is None:
            chg = item.get("price_change_percentage_24h_in_currency")
        week = item.get("price_change_percentage_7d_in_currency")
        month = item.get("price_change_percentage_30d_in_currency")
        spark_raw = ((item.get("sparkline_in_7d") or {}).get("price")) or []
        spark_n, spark_px = pack_spark(spark_raw, n=len(spark_raw) or 48)
        row = empty_row(spec)
        row.update(
            {
                "ok": last is not None,
                "last": last,
                "lastDisplay": format_px(last, "coin") if last is not None else "—",
                "dayPct": slim_pct(chg),
                "weekPct": slim_pct(week),
                "monthPct": slim_pct(month),
                "spark": spark_n,
                "sparkPx": spark_px,
                "dayMoves": day_moves_from_spark(spark_raw, history.get(spec["cg"])),
                "currency": "USD",
                "mcap": format_cap(item.get("market_cap")),
                "volume": format_cap(item.get("total_volume")),
                "source": "CoinGecko",
                "lag": "Near real-time",
                "sessionCode": "open",
                "sessionLabel": "OPEN 24/7",
                "sourceUrl": quote_url(spec),
                "chartUrl": chart_url(spec),
                "chartSite": chart_site(spec),
                "headlines": [],
            }
        )
        out[spec["id"]] = row
    for spec in CRYPTO:
        if (out.get(spec["id"]) or {}).get("last") is not None:
            continue
        try:
            row = exchange_crypto_row(spec)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError, IndexError) as exc:
            print(f"[warn] {spec['id']} exchange quote: {exc}", flush=True)
            row = None
        if row:
            out[spec["id"]] = row
    if out:
        CRYPTO_LAST_GOOD.clear()
        CRYPTO_LAST_GOOD.update(out)
    return out or dict(CRYPTO_LAST_GOOD)


def exchange_crypto_row(spec: dict) -> dict | None:
    """Last price and daily closes from the same venue the chart already uses."""
    venue, product, label = EXCHANGE[spec["id"]]
    if venue == "coinbase":
        stats = json.loads(http_get(f"https://api.exchange.coinbase.com/products/{product}/stats", timeout=12).decode("utf-8"))
        last = _cnbc_float(stats.get("last"))
        opened = _cnbc_float(stats.get("open"))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=21)
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400"
            f"&start={start.isoformat()}&end={end.isoformat()}"
        )
        candles = json.loads(http_get(url, timeout=12).decode("utf-8"))
        levels = sorted((int(c[0]), float(c[4])) for c in candles)
    else:
        tick = json.loads(http_get(f"https://www.okx.com/api/v5/market/ticker?instId={product}", timeout=12).decode("utf-8"))
        quote = (tick.get("data") or [{}])[0]
        last = _cnbc_float(quote.get("last"))
        opened = _cnbc_float(quote.get("open24h"))
        raw = json.loads(
            http_get(f"https://www.okx.com/api/v5/market/candles?instId={product}&bar=1Dutc&limit=21", timeout=12).decode("utf-8")
        ).get("data") or []
        levels = sorted((int(c[0]) // 1000, float(c[4])) for c in raw)
    if last is None or len(levels) < 2:
        return None
    closes = [px for _t, px in levels]
    if abs(closes[-1] - last) > max(1e-8, abs(last) * 1e-4):
        closes.append(last)
    dated = [(datetime.fromtimestamp(t, HKT).date(), px) for t, px in levels]
    spark_n, spark_px = pack_spark(closes)
    row = empty_row(spec)
    row.update(
        {
            "ok": True,
            "last": last,
            "lastDisplay": format_px(last, "coin"),
            "dayPct": slim_pct(pct(last, opened)),
            "weekPct": slim_pct(last_n_change(closes, 7)),
            "monthPct": slim_pct(last_n_change(closes, 21)),
            "spark": spark_n,
            "sparkPx": spark_px,
            "dayMoves": day_moves_from_levels(dated),
            "currency": "USD",
            "source": label.split()[0],
            "lag": "Exchange print",
            "sessionCode": "open",
            "sessionLabel": "OPEN 24/7",
            "sourceUrl": quote_url(spec),
            "chartUrl": chart_url(spec),
            "chartSite": chart_site(spec),
            "headlines": [],
        }
    )
    return row


def canon_source(raw: str) -> str | None:
    low = (raw or "").strip().lower()
    # substring matching would fold every publisher containing "ft" into the FT
    for key, name in SOURCE_ALIASES.items():
        if re.search(r"\b" + re.escape(key) + r"\b", low):
            return name
    return None


def strip_html(raw: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def article_body(desc: str) -> str:
    """Full feed text used to decide relevance. Titles alone are not enough."""
    return strip_html(desc)[:8000]


def excerpt_from(title: str, desc: str) -> str:
    text = article_body(desc)
    if not text:
        return ""
    folded = title.casefold()
    low = text.casefold()
    if low.startswith(folded[: min(48, len(folded))]):
        text = text[len(title) :].lstrip(" .:—-|")
    text = re.sub(r"^(Latest\s+\w+:\s*)", "", text, flags=re.I)
    if len(text) < 50:
        return ""
    if len(text) > 320:
        text = text[:320].rsplit(" ", 1)[0] + "…"
    return text


def rss_epoch(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def age_hours(item: dict) -> float | None:
    ts = item.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, (time.time() - ts) / 3600.0)


def parse_rss_items(xml: bytes, fallback_source: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return items
    nodes = root.findall("./channel/item")
    if not nodes:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for node in root.findall("a:entry", ns):
            title = unescape((node.findtext("a:title", default="", namespaces=ns) or "").strip())
            if not title:
                continue
            desc = node.findtext("a:summary", default="", namespaces=ns) or node.findtext(
                "a:content", default="", namespaces=ns
            )
            items.append(
                {
                    "title": title,
                    "source": fallback_source,
                    "ts": rss_epoch(node.findtext("a:updated", default="", namespaces=ns) or ""),
                    "time": node.findtext("a:updated", default="", namespaces=ns) or "",
                    "link": (node.find("a:link", ns).get("href") if node.find("a:link", ns) is not None else ""),
                    "body": article_body(desc or ""),
                    "excerpt": excerpt_from(title, desc or ""),
                }
            )
        return items
    for node in nodes:
        title = unescape((node.findtext("title") or "").strip())
        if not title:
            continue
        src_el = node.find("source")
        src = canon_source(src_el.text if src_el is not None else "") or fallback_source
        title = re.sub(
            r"\s+[-–]\s+(Reuters|Bloomberg|Financial Times|FT|WSJ|CNBC|BBC|AP|Nikkei|SCMP|MarketWatch|Economist|"
            r"Nikkei Asia|BNN Bloomberg|CNBC TV18|CNBC Africa|The Wall Street Journal|Associated Press|"
            r"Bloomberg\.com|reuters\.com|ft\.com|wsj\.com|cnbc\.com)\s*$",
            "",
            title,
            flags=re.I,
        )
        title = re.sub(r"\s+[-–]\s+[\w.-]+\.(com|co\.uk|net|org)\s*$", "", title, flags=re.I)
        items.append(
            {
                "title": title,
                "source": src,
                "ts": rss_epoch(node.findtext("pubDate") or ""),
                "time": node.findtext("pubDate") or "",
                "link": (node.findtext("link") or "").strip(),
                "body": article_body(node.findtext("description") or ""),
                "excerpt": excerpt_from(title, node.findtext("description") or ""),
            }
        )
    return items


def fetch_news() -> list[dict]:
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(http_get, url, 12): src for src, url in NEWS_FEEDS}
        for fut in as_completed(futs):
            src = futs[fut]
            try:
                items.extend(parse_rss_items(fut.result(), src))
            except Exception:
                continue
    seen: set[str] = set()
    out: list[dict] = []
    keep = re.compile(
        r"market|stock|bond|yield|fed|oil|crude|gold|bitcoin|crypto|dollar|forex|fx|treasury|inflation|rate|equit|commodit|currenc|bank|ipo",
        re.I,
    )
    skip = re.compile(
        r"tintoretto|crossword|recipe|horoscope|abduction|iowa state fair|obituary|wedding|arrests man",
        re.I,
    )
    for it in items:
        title = it["title"]
        if skip.search(title) or JUNK_TITLE.search(title):
            continue
        if ACCESS.get(it.get("source") or "") == "restricted":
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    # RFC822 strings do not sort chronologically; rank on the parsed stamp
    out.sort(key=lambda x: (x.get("ts") or 0, SOURCE_RANK.get(x.get("source") or "", 0)), reverse=True)
    return out[:600]


def google_rss(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def fetch_rss_list(query: str, fallback: str) -> list[dict]:
    try:
        return parse_rss_items(http_get(google_rss(query), timeout=10), fallback)
    except Exception:
        return []


# Short on purpose: a long site: filter makes Google drop the asset terms and return generic news.
WEEK_TERMS = {
    "spx": '"S&P 500" OR "Wall Street" stocks',
    "ndx": "Nasdaq stocks",
    "dji": '"Dow Jones" OR "the Dow" stocks',
    "nky": 'Nikkei OR "Japanese stocks" OR "Tokyo stocks"',
    "hsi": '"Hang Seng" OR "Hong Kong stocks"',
    "sx5e": '"European stocks" OR "Euro Stoxx" OR "Stoxx 600"',
    "eur": "euro dollar ECB",
    "jpy": "yen dollar",
    "gbp": "sterling OR pound dollar",
    "chf": '"Swiss franc"',
    "aud": '"Australian dollar"',
    "cny": "yuan dollar",
    "wti": "oil prices",
    "ng": '"natural gas" prices',
    "au": "gold prices",
    "ag": "silver prices",
    "cu": "copper prices",
    "wh": "wheat prices",
    "us10y": "Treasury yields",
    "us30y": '"30-year" Treasury yield',
    "de10y": 'Bund yields OR "German bonds"',
    "jp10y": 'JGB yields OR "Japanese bonds"',
    "gb10y": "gilt yields",
    "ca10y": '"Canadian bonds" OR "Canada bond yields"',
    "btc": "bitcoin",
    "eth": "ethereum OR ether",
    "sol": "solana",
    "xrp": "XRP",
    "bnb": "BNB OR Binance",
    "doge": "dogecoin",
}
WEEK_NEWS_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def week_windows() -> list[tuple]:
    """Two-day Google date windows from the Sunday before Monday up to today (HKT)."""
    today = datetime.now(HKT).date()
    start = today - timedelta(days=today.weekday() + 1)
    out = []
    while start <= today:
        out.append((start, start + timedelta(days=2)))
        start += timedelta(days=2)
    return out


def fetch_week_news(asset_id: str) -> list[dict]:
    terms = WEEK_TERMS.get(asset_id)
    if not terms:
        return []
    today = datetime.now(HKT).date()
    items: list[dict] = []
    for lo, hi in week_windows():
        key = (asset_id, lo.isoformat())
        ttl = 6 * 3600 if hi < today else 600
        hit = WEEK_NEWS_CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            items.extend(hit[1])
            continue
        got = fetch_rss_list(f"{terms} after:{lo.isoformat()} before:{hi.isoformat()}", "Google News")
        if got or not hit:
            WEEK_NEWS_CACHE[key] = (time.time(), got)
            items.extend(got)
        else:
            items.extend(hit[1])
    return items


def fetch_asset_news(spec: dict) -> tuple[str, list[dict]]:
    items: list[dict] = []
    seen: set[str] = set()
    batches = [fetch_week_news(spec["id"])]
    q = ASSET_QUERIES.get(spec["id"])
    if q:
        batches.append(fetch_rss_list(q, "Google News"))
    for batch in batches:
        for it in batch:
            if JUNK_TITLE.search(it.get("title") or ""):
                continue
            # unattributed aggregator items can never earn a slot, and keeping them
            # here would crowd the named desks out of the cut below
            if (it.get("source") or "") not in NEWSWORTHY:
                continue
            key = (it.get("title") or "").casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            it["forId"] = spec["id"]
            items.append(it)
    items.sort(key=lambda x: (x.get("ts") or 0, SOURCE_RANK.get(x.get("source") or "", 0)), reverse=True)
    return spec["id"], items[:120]


def news_score(item: dict, spec: dict, day_pct: float | None) -> int:
    title = item.get("title") or ""
    blob = f"{title} {item.get('excerpt') or ''}"
    if JUNK_TITLE.search(title):
        return 0
    if (item.get("source") or "") not in NEWSWORTHY:
        return 0
    if ACCESS.get(item.get("source") or "") == "restricted":
        return 0
    age = age_hours(item)
    if age is None or age > news_max_age():
        return 0
    if len(title) < 25:
        return 0
    weak = NEWS_WEAK.get(spec["id"], [])
    strong = [p for p in NEWS_KEYWORDS.get(spec["id"], []) if p not in weak]
    hits = lambda pats, text: sum(1 for p in pats if re.search(p, text, re.I))
    named = hits(strong, title)
    named_body = hits(strong, item.get("excerpt") or "")
    regional = hits(weak, title)
    topical = hits(CLASS_KEYWORDS.get(spec["cls"], []), blob)
    # the headline must name the instrument, or lean on a regional term while
    # still reading like market copy — "Japan" alone also matches a typhoon story
    if not named and not (regional and topical):
        return 0
    moves = DIR_UP.search(title) or DIR_DOWN.search(title)
    # A company/person story that merely contains "oil", "Treasury" or "stock"
    # is not evidence for a market move. Require the headline itself to describe
    # a move or name a plausible market driver.
    if not (moves or MARKET_DRIVER_TITLE.search(title)):
        return 0
    relevance = named * 5 + named_body * 2 + min(regional, 2) * 2 + min(topical, 3) * 2
    if item.get("forId") == spec["id"]:
        relevance += 3
    if WHY.search(blob):
        relevance += 3
    if moves:
        relevance += 2
    if relevance < 7:
        return 0
    # relevance sets the tier; recency and source only order within it
    score = relevance * 10
    score += SOURCE_RANK.get(item.get("source") or "", 0)
    if age <= 3:
        score += 12
    elif age <= 8:
        score += 9
    elif age <= 16:
        score += 5
    elif age <= 24:
        score += 2
    if item.get("excerpt"):
        score += 2
    # a wire nobody can open is worth less in the room than one they can read
    if ACCESS.get(item.get("source") or "") == "free":
        score += 8
    if day_pct is not None and abs(day_pct) >= 0.15:
        up, down = DIR_UP.search(title), DIR_DOWN.search(title)
        if (day_pct > 0 and up) or (day_pct < 0 and down):
            score += 25
        elif (day_pct > 0 and down) or (day_pct < 0 and up):
            score -= 20
    return score


THEME_RULES = [
    (r"\b(boj|bank of japan|\byen\b|nikkei)\b", "BOJ policy and the yen"),
    (r"\b(fed|fomc|powell|federal reserve)\b", "the Federal Reserve"),
    (r"\b(treasury|10-year|10 year|yields?|duration)\b", "US yields"),
    (r"\b(ecb|eurozone|euro zone|stoxx)\b", "euro-area markets"),
    (r"\b(oil|brent|wti|opec|crude)\b", "the oil complex"),
    (r"\b(gold|silver|precious metal)\b", "precious metals"),
    (r"\b(copper|wheat|grain|nat(?:ural)? gas)\b", "industrial commodities"),
    (r"\b(bitcoin|ethereum|crypto|solana)\b", "crypto risk appetite"),
    (r"\b(dollar|dxy|greenback)\b", "the US dollar"),
    (r"\b(hang seng|hong kong|china|pboc)\b", "China / Hong Kong risk"),
    (r"\b(inflation|consumer prices)\b", "inflation data"),
    (r"\b(tariff|trade deal|chip factory)\b", "trade and industrial policy"),
    (r"\b(earnings|guidance)\b", "corporate earnings"),
]


def wire_themes(heads: list[dict]) -> list[str]:
    found: list[str] = []
    for item in heads[:8]:
        title = item.get("title") or ""
        for pat, name in THEME_RULES:
            if name not in found and re.search(pat, title, re.I):
                found.append(name)
    return found[:3]


def write_summary(row: dict) -> str:
    chg = row.get("dayPct")
    horizon = "over 24 hours" if row.get("cls") == "crypto" else "today"
    if chg is None:
        move = f"{row['label']} has no clean print {horizon}"
    elif abs(chg) < 0.05:
        move = f"{row['label']} is little changed {horizon} ({chg:+.2f}%)"
    elif chg > 0:
        extra = f", {row['dayBps']:+.1f} bp" if row.get("dayBps") is not None else ""
        move = f"{row['label']} is up {abs(chg):.2f}% {horizon}{extra}"
    else:
        extra = f", {row['dayBps']:+.1f} bp" if row.get("dayBps") is not None else ""
        move = f"{row['label']} is down {abs(chg):.2f}% {horizon}{extra}"
    trend = ""
    w, m = row.get("weekPct"), row.get("monthPct")
    if w is not None and m is not None and abs(w) >= 0.2:
        if (w > 0) != (m > 0):
            trend = f"; the 1-week move ({w:+.2f}%) cuts against the 1-month trend ({m:+.2f}%)"
        elif chg is not None and abs(chg) >= 0.2 and (chg > 0) != (w > 0):
            trend = f"; today fades the 1-week trend ({w:+.2f}%)"
    heads = [h for h in (row.get("headlines") or []) if h.get("title")]
    if not heads:
        return f"{move}{trend}. No named wire on this ticker yet — treat the print as the story."
    themes = wire_themes(heads)
    sources = []
    for h in heads[:4]:
        src = h.get("source")
        if src and src not in sources:
            sources.append(src)
    src_txt = ", ".join(sources[:3]) if sources else "wires"
    if themes:
        if len(themes) == 1:
            cluster = themes[0]
        elif len(themes) == 2:
            cluster = f"{themes[0]} and {themes[1]}"
        else:
            cluster = f"{themes[0]}, {themes[1]} and {themes[2]}"
        news = f"Latest {src_txt} cluster around {cluster}."
    else:
        news = (
            f"Latest {src_txt} mention the name but do not form one clean story; "
            "use the print first and skim the wires below for colour."
        )
    return f"{move}{trend}. {news}"


GNEWS_URL: dict[str, str] = {}
NEWS_LINK_CACHE: dict[str, tuple[float, bool]] = {}
NEWS_LINK_TTL = 6 * 3600.0
NEWS_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


def resolve_gnews(link: str) -> str:
    """Google wraps every item in a JS redirect; unwrap it so the card links to the publisher."""
    if "/rss/articles/" not in link:
        return link
    token = link.split("/rss/articles/")[1].split("?")[0]
    if token in GNEWS_URL:
        return GNEWS_URL[token]
    for prefix in ("articles", "rss/articles", "read"):
        try:
            page = urllib.request.Request(
                f"https://news.google.com/{prefix}/{token}",
                headers={"User-Agent": NEWS_BROWSER_UA},
            )
            with urllib.request.urlopen(page, timeout=12) as resp:
                html = resp.read().decode("utf-8", "replace")
            sig = re.search(r'data-n-a-sg="([^"]+)"', html)
            stamp = re.search(r'data-n-a-ts="([^"]+)"', html)
            if not (sig and stamp):
                continue
            shell = [
                ["en-US", "US", ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"], None, None, 1, 1,
                 "US:en", None, 1, None, None, None, None, None, 0, 1],
                "en-US", "US", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ]
            inner = json.dumps(
                ["garturlreq", shell, token, int(stamp.group(1)), sig.group(1)],
                separators=(",", ":"),
            )
            payload = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
            req = urllib.request.Request(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
                data=urllib.parse.urlencode({"f.req": payload}).encode("ascii"),
                headers={
                    "User-Agent": NEWS_BROWSER_UA,
                    "Referer": "https://news.google.com/",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                text = resp.read().decode("utf-8", "replace")
            if text.startswith(")]}'"):
                text = text.split("\n", 1)[1]
            text = text.lstrip()
            first, sep, rest = text.partition("\n")
            if sep and first.strip().isdigit():
                text = rest
            for envelope in json.loads(text):
                if not (
                    isinstance(envelope, list)
                    and len(envelope) >= 3
                    and envelope[0] == "wrb.fr"
                    and envelope[1] == "Fbv4je"
                ):
                    continue
                decoded = json.loads(envelope[2])
                if (
                    isinstance(decoded, list)
                    and len(decoded) >= 2
                    and decoded[0] == "garturlres"
                    and isinstance(decoded[1], str)
                ):
                    GNEWS_URL[token] = decoded[1]
                    return decoded[1]
        except Exception:
            continue
    return link


SOURCE_HOST = {
    "Financial Times": "ft.com",
    "Bloomberg": "bloomberg.com",
    "WSJ": "wsj.com",
    "Nikkei": "nikkei.com",
    "AP": "apnews.com",
    "CNBC": "cnbc.com",
    "BBC": "bbc.co",
    "SCMP": "scmp.com",
    "MarketWatch": "marketwatch.com",
    "Guardian": "theguardian.com",
    "Axios": "axios.com",
    "NPR": "npr.org",
    "ABC": "abcnews.go.com",
    "Economist": "economist.com",
    "CoinDesk": "coindesk.com",
}


def _publisher_host_ok(item: dict) -> bool:
    link = item.get("link") or ""
    host = urllib.parse.urlparse(link).netloc.casefold()
    expected = SOURCE_HOST.get(item.get("source") or "")
    return bool(
        link.startswith(("http://", "https://"))
        and host
        and "news.google.com" not in host
        and (not expected or host == expected or host.endswith("." + expected))
    )


def _news_link_opens(item: dict) -> bool:
    """HTTP errors and paywalls do not establish article readability."""
    return bool(evidence.check_link(item).get("ok"))


def resolve_links(rows: list[dict]) -> None:
    """Replace Google wrappers, live-check every displayed URL, and remove failures."""
    pending = {
        h["link"]
        for row in rows
        for h in row.get("headlines") or []
        if h.get("link") and "/rss/articles/" in h["link"]
    }
    resolved = {}
    if pending:
        with ThreadPoolExecutor(max_workers=16) as pool:
            resolved = dict(zip(pending, pool.map(resolve_gnews, pending)))
    for row in rows:
        for group in (row.get("headlines") or [], row.get("_pool") or []):
            for item in group:
                if item.get("link") in resolved:
                    item["link"] = resolved[item["link"]]
    candidates: dict[str, dict] = {}
    for row in rows:
        for item in row.get("headlines") or []:
            if item.get("link"):
                candidates.setdefault(item["link"], item)
    if candidates:
        items = list(candidates.values())
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(_news_link_opens, items))
        good = {item["link"] for item, ok in zip(items, results) if ok}
    else:
        good = set()
    for row in rows:
        row["headlines"] = [h for h in row.get("headlines") or [] if h.get("link") in good]
        # Explanations may cite only stories whose displayed links passed the
        # same check; this prevents a removed 404 from still driving the brief.
        row["_pool"] = [h for h in row.get("_pool") or [] if h.get("link") in good]
        row["newsAsOf"] = max((h.get("ts") or 0 for h in row["headlines"]), default=None)
        row["summary"] = write_summary(row)


def sector_wires(row: dict, news: list[dict]) -> list[dict]:
    """Class-level colour for a name no major desk has written about today."""
    pats = CLASS_KEYWORDS.get(row["cls"], [])
    region = (
        REGION_HINTS.get(row["id"])
        or NEWS_WEAK.get(row["id"], []) + NEWS_KEYWORDS.get(row["id"], [])
    )
    cands: list[tuple[int, float, dict]] = []
    seen: set[str] = set()
    for item in news:
        if (item.get("source") or "") not in NEWSWORTHY or not item.get("link"):
            continue
        age = age_hours(item)
        if age is None or age > news_max_age():
            continue
        title = item.get("title") or ""
        if len(title) < 25 or JUNK_TITLE.search(title) or title.casefold() in seen:
            continue
        blob = f"{title} {item.get('excerpt') or ''}"
        if not any(re.search(p, blob, re.I) for p in pats):
            continue
        if not WHY.search(blob) and not DIR_UP.search(title) and not DIR_DOWN.search(title):
            continue
        seen.add(title.casefold())
        # rank on how close the story sits to this name's region so sibling
        # cards do not all fall back to the same four global headlines
        near = sum(1 for p in region if re.search(p, blob, re.I))
        cands.append((near, item.get("ts") or 0, item))
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c[2] for c in cands[:4]]


WIRES_PER_CARD = 20


def attach_headlines(rows: list[dict], news: list[dict]) -> None:
    for row in rows:
        scored = []
        for item in news:
            s = news_score(item, row, row.get("dayPct"))
            if s > 0:
                scored.append((s, item))
        scored.sort(key=lambda x: (x[0], x[1].get("ts") or 0), reverse=True)
        pool, pool_seen = [], set()
        for _, item in scored:
            key = (item.get("title") or "").casefold()
            if key not in pool_seen:
                pool_seen.add(key)
                pool.append(item)
        # the brief reads the whole week; the card only shows the top of it
        row["_pool"] = pool[:80]
        picked: list[dict] = []
        seen_titles: set[str] = set()
        for _, item in scored:
            key = (item.get("title") or "").casefold()
            if not item.get("link") or key in seen_titles:
                continue
            seen_titles.add(key)
            picked.append(item)
            if len(picked) == WIRES_PER_CARD:
                break
        row["wireScope"] = "asset" if picked else "sector"
        if not picked:
            row["wireScope"] = "none"
        # Keep only asset-relevant stories; an unrelated class-level story is
        # worse than an honest empty state.
        picked.sort(key=lambda p: p.get("ts") or 0, reverse=True)
        newest = max((p.get("ts") or 0) for p in picked) if picked else None
        picked = [{**p, "access": ACCESS.get(p.get("source") or "", "sub")} for p in picked]
        row["headlines"] = picked
        row["newsAsOf"] = newest
        row["summary"] = write_summary(row)


def news_by_source(news: list[dict]) -> dict[str, list[dict]]:
    grouped = {"Financial Times": [], "Bloomberg": [], "Reuters": []}
    for it in news:
        bucket = grouped.get(it["source"])
        if bucket is not None and len(bucket) < 6:
            bucket.append(it)
    return grouped


def mark_movers(rows: list[dict]) -> None:
    by: dict[str, list[dict]] = {}
    for row in rows:
        row["moverRank"] = None
        by.setdefault(row["cls"], []).append(row)
    for group in by.values():
        ranked = sorted(
            [r for r in group if r.get("ok") and r.get("dayPct") is not None],
            key=lambda r: abs(r["dayPct"]),
            reverse=True,
        )
        for i, row in enumerate(ranked[:2], start=1):
            row["moverRank"] = i


def regime_from(rows: list[dict]) -> str:
    by = {r["id"]: r for r in rows}
    spx = (by.get("spx") or {}).get("dayPct") or 0
    # no dollar index on the board any more; a firmer euro is a softer dollar
    dxy = -((by.get("eur") or {}).get("dayPct") or 0)
    btc = (by.get("btc") or {}).get("dayPct") or 0
    tnx_bps = (by.get("us10y") or {}).get("dayBps")
    score = (1 if spx > 0 else -1) + (1 if btc > 0 else -1) + (-1 if dxy > 0.15 else 1 if dxy < -0.15 else 0)
    if isinstance(tnx_bps, (int, float)):
        score += -1 if tnx_bps > 2 else 1 if tnx_bps < -2 else 0
    if score >= 2:
        return "risk-on"
    if score <= -2:
        return "risk-off"
    return "mixed"


# Factor, pattern, phrase when that factor is falling, phrase when it is rising.
# Equity indices usually like the first and dislike the second.
INDEX_DRIVERS = [
    ("oil", re.compile(r"\b(oil|crude|brent|wti)\b", re.I), "softer crude", "firmer crude"),
    ("yields", re.compile(r"\b(yields?|treasur)", re.I), "yields easing", "yields backing up"),
    ("policy", re.compile(r"\b(fed|fomc|rate[- ]hikes?|rate[- ]cuts?|hike bets|central bank|powell|boj|ecb|boe|rba|snb|pboc)\b", re.I), "a less hostile rate path", "tighter policy"),
    ("chips", re.compile(r"\b(chipmakers?|semiconductors?|nvidia|ai|artificial intelligence|tech stocks|big tech|magnificent seven|meta)\b", re.I), "a fade in the AI trade", "the chip and AI bid"),
    ("china", re.compile(r"\b(china|hong kong|trump-xi|tariff|trade talk)\b", re.I), "trade tension", "US-China talks"),
    ("geopolitics", re.compile(r"\b(iran|houthi|middle east|ceasefire|israel|russia|ukraine)\b", re.I), "calmer geopolitics", "fresh geopolitical risk"),
    ("data", re.compile(r"\b(gdp|payrolls?|jobs report|jobless|cpi|pce|inflation|consumer prices|pmi|retail sales|economic data|eco data|economy)\b", re.I), "softer data", "hot data"),
    ("fiscal", re.compile(r"\b(auctions?|issuance|deficit|fiscal|term premium|debt ceiling|shutdown)\b", re.I), "fiscal relief", "fiscal worry"),
    ("supply", re.compile(r"\b(supply|opec\+?|output|shortage|inventor(?:y|ies)|stockpiles?)\b", re.I), "looser supply", "tighter supply"),
    ("earnings", re.compile(r"\b(earnings|guidance|quarterly results|profit warning)\b", re.I), "weak earnings", "earnings beats"),
    (
        "intervention",
        re.compile(
            r"\b(intervention|intervene|jawbon\w*|weak yen|yen weakness|finance chiefs? discuss(?: the)? yen|concerns? (?:about|over|on) (?:the )?yen)\b",
            re.I,
        ),
        "intervention risk",
        "intervention risk",
    ),
    ("flows", re.compile(r"\b(etf (?:flows?|inflows?|outflows?)|inflows?|outflows?|liquidations?)\b", re.I), "outflows", "inflows"),
    ("weather", re.compile(r"\b(storage|inventor(?:y|ies)|weather|heat ?wave|cold snap|hurricane|lng)\b", re.I), "loose storage", "tight storage"),
    ("crops", re.compile(r"\b(harvest|crops?|black sea|usda|drought)\b", re.I), "better crops", "crop risk"),
]

def _lex(up: str, down: str) -> tuple[re.Pattern, re.Pattern]:
    return re.compile(rf"\b({up})", re.I), re.compile(rf"\b({down})", re.I)


# A failed de-escalation is escalation: "hopes fade for talks" is risk rising.
NEGATED = re.compile(r"\b(hopes? (?:fade|dim)|stall\w*|collaps\w*|breaks? down|no deal|impasse|talks fail)", re.I)

# "up" is the second phrase in INDEX_DRIVERS for that factor, "down" the first.
FACTOR_LEX = {
    "yields": _lex(
        r"yields? (?:rise|rises|climb|jump|soar|surge|hit|top|near|at|push)|highest|(?:treasury|bond) (?:sell-?off|rout|slump)|sell-?off deepens",
        r"yields? (?:fall|falls|ease|eases|drop|slip|decline)|(?:treasury|bond) rally|treasuries gain",
    ),
    "policy": _lex(
        r"hikes?\b|hiking|hawkish|not done|higher for longer|tighten|rate-?boost|raise rates|rate increase",
        r"cuts?\b|cutting|dovish|pause|easing cycle|lower rates",
    ),
    "geopolitics": _lex(
        r"attack|strikes?\b|escalat|impasse|stalemate|standoff|tension|war\b|sanction|threat|missile",
        r"talks?\b|truce|ceasefire|deal\b|de-?escalat|peace|reprieve|agreement",
    ),
    "china": _lex(
        r"talks?\b|deal\b|truce|summit|agreement|thaw",
        r"tariffs? (?:hike|threat|rise)|tension|curbs?\b|ban\b|retaliat|export controls",
    ),
    "data": _lex(
        r"hot\b|hotter|strong|beats?\b|robust|resilient|red-hot|accelerat|jumps?\b|surges?\b",
        r"weak|miss|slow|cool|soft|contract|decelerat|shrink",
    ),
    "fiscal": _lex(r"heavy|worr|deficit|downgrade|weak demand|tail\b|record", r"light|strong demand|smaller"),
    "supply": _lex(
        r"worr|concern|shortage|disrupt|output cut|cuts output|bites|draw\b|tight",
        r"light|strong demand|smaller|concerns? ease|worries ease|glut|output (?:rise|hike|boost)|more barrels",
    ),
    "flows": _lex(r"inflows?", r"outflows?|liquidat"),
    "weather": _lex(r"tight|shortage|heat|cold|draw\b|freeze", r"build\b|glut|mild|surplus"),
    "crops": _lex(r"drought|damage|ban\b|disrupt|shortage", r"bumper|record harvest|surplus"),
    "intervention": _lex(
        r"risk|concern|weak yen|yen weakness|discuss(?: the)? yen|finance chiefs",
        r"risk eases|concerns? ease|rules out|no intervention",
    ),
}

# Words that tell which way a factor moved when the ordinary up/down verbs are absent.
FACTOR_UP = re.compile(
    r"\b(hot|hotter|strong|stronger|beats?|robust|red-hot|resilient|soar\w*|highest|record|hawkish|"
    r"hikes?|hiking|not done|higher for longer|tighten\w*|sell-?off|inflows?|escalat\w*|attack\w*|strikes?)\b",
    re.I,
)
FACTOR_DOWN = re.compile(
    r"\b(weak|weaker|miss(?:es|ed)?|slow\w*|cool\w*|soft\w*|contract\w*|dovish|cuts?|cutting|pause|"
    r"reprieve|outflows?|talks?|truce|ceasefire|de-?escalat\w*|lowest)\b",
    re.I,
)

NEXT_BY_DRIVER = {
    "oil": (
        "Stock buying depends partly on oil staying lower. If oil reverses higher, that support can disappear.",
        "Firmer crude is the threat. If oil keeps rising, the pressure on equity multiples continues.",
    ),
    "yields": (
        "If the US 10-year yield rises again, the support from lower borrowing costs can disappear.",
        "Watch the US 10-year. A further rise makes future company earnings less valuable in today's terms.",
    ),
    "policy": (
        "A signal that rates may stay higher from the next central-bank speaker could reverse this relief rally.",
        "The next central-bank speaker can extend this move by signalling that rates may not need to rise further.",
    ),
    "chips": (
        "Watch whether buying returns to chip shares, or weakness spreads beyond the Nasdaq.",
        "Watch whether chip-share gains spread beyond the Nasdaq or remain concentrated in one index.",
    ),
    "china": (
        "A better follow-up on the talks is what would take the tariff overhang off Asia.",
        "Asia's next session is whether the talks produce anything past the headline. A sour follow-up hits the Hang Seng first.",
    ),
    "geopolitics": (
        "The risk is a fresh Middle East headline that puts oil back up and takes the equity relief away.",
        "If geopolitical pressure on oil fades, the inflation concern should ease with it.",
    ),
}


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]




def _driver_counts(items: list[dict], min_hits: int = 2) -> list[tuple[str, str, int]]:
    """Rank factors by how often the wires mention them. Direction is the factor's, read next to the word, not the asset's."""
    ranked = []
    for key, pat, _soft, _firm in INDEX_DRIVERS:
        hits = [it for it in items if pat.search(f"{it.get('title') or ''} {it.get('excerpt') or ''}")]
        if len(hits) < min_hits:
            continue
        votes = [d for it in hits if (d := _near_direction(pat, it.get("title") or "", key))]
        if not votes:
            continue
        down = votes.count("down")
        ranked.append((key, "down" if down >= len(votes) - down else "up", len(hits)))
    ranked.sort(key=lambda x: x[2], reverse=True)
    return ranked


def _near_direction(pat: re.Pattern, title: str, key: str = "") -> str | None:
    """Up or down for the factor itself. Event factors read their own vocabulary; price factors read the verb next to them."""
    m = pat.search(title)
    if not m:
        return None
    if NEGATED.search(title) and key in ("geopolitics", "china"):
        return "up" if key == "geopolitics" else "down"
    lex = FACTOR_LEX.get(key)
    if lex:
        up, down = lex[0].search(title), lex[1].search(title)
        if up and not down:
            return "up"
        if down and not up:
            return "down"
    window = title[max(0, m.start() - 30) : m.end() + 30]
    origin = m.start() - max(0, m.start() - 30)
    down = DIR_DOWN.search(window)
    up = DIR_UP.search(window)
    if down and up:
        return "down" if abs(down.start() - origin) <= abs(up.start() - origin) else "up"
    if down:
        return "down"
    if up:
        return "up"
    f_down = FACTOR_DOWN.search(title)
    f_up = FACTOR_UP.search(title)
    if f_down and not f_up:
        return "down"
    if f_up and not f_down:
        return "up"
    return None


# Mechanism, not a label. First sentence is the factor falling, second is it rising.
# Bond lines keep price and yield as the same fact, which is the point of that market.
CAUSE = {
    "equities": {
        "oil": (
            "Cheaper oil reduces inflation and interest-rate pressure, so investors may pay more for future company earnings.",
            "Firmer oil raises inflation concern. If rates are expected to stay higher, investors may pay less for future company earnings.",
        ),
        "yields": (
            "Treasury yields eased, which lowers the discount rate on future earnings and lets equities re-rate higher.",
            "Treasury yields backed up, so future earnings are discounted more heavily and equities cheapen.",
        ),
        "policy": (
            "Investors expect a less restrictive central-bank path, so the indices rose on rate relief rather than company earnings.",
            "Tighter policy raises borrowing costs and reduces what investors will pay for future earnings.",
        ),
        "chips": (
            "The AI and chip trade is fading, so the Nasdaq stops pulling the rest of the indices with it.",
            "Buying in chip and AI shares is doing the lifting. It starts with the Nasdaq and becomes a broad stock-market move only if it spreads.",
        ),
        "china": (
            "Trade tension is the overhang. Asia is pricing the risk of a worse tariff path, not a local earnings miss.",
            "Asian shares are responding to the tone of US-China talks. A better tariff outlook supports Hong Kong shares and exporters first.",
        ),
        "geopolitics": (
            "Calmer geopolitics supports shares mainly by lowering oil and inflation concerns.",
            "A fresh geopolitical risk is back in the price, and it arrives through oil and then through the rate path.",
        ),
    },
    "fx": {
        "oil": (
            "Softer oil helps the currencies of energy importers and reduces inflation-related support for the dollar.",
            "Firmer oil supports the dollar via the inflation channel and hurts the currencies that import energy.",
        ),
        "yields": (
            "US yields eased, so the dollar's rate advantage narrows and the pairs quoted against it adjust.",
            "Higher US yields widen the dollar's rate advantage. That is the gap these pairs are pricing, not a growth story.",
        ),
        "policy": (
            "A less hostile rate path narrows expected policy gaps, which is what moves a currency pair.",
            "Tighter policy in one country versus another is the exchange rate. The pair moves with that gap.",
        ),
        "chips": (
            "Buying in chip shares shows stronger confidence. Growth-sensitive currencies often follow, while the yen may move the other way.",
            "Stronger buying in chip and AI shares shows confidence, so funding currencies can weaken while growth-sensitive currencies strengthen.",
        ),
        "china": (
            "Trade tension hits the yuan and the currencies that trade with China, ahead of any domestic data.",
            "A better US-China tone supports the yuan and commodity currencies tied to Chinese demand.",
        ),
        "geopolitics": (
            "Calmer geopolitics reduces demand for the dollar and yen as safe currencies.",
            "Fresh geopolitical risk increases demand for the dollar and yen as safe currencies before it reaches growth-sensitive currencies.",
        ),
    },
    "commodities": {
        "oil": (
            "Crude itself is the story: supply looks less tight, so energy prices fall and inflation concerns ease.",
            "Crude supply looks tighter, so energy rises and inflation concerns can also support gold.",
        ),
        "yields": (
            "Yields eased, which lowers the opportunity cost of holding gold and silver.",
            "Yields backed up, so gold and silver have to compete with a higher real return on cash and bonds.",
        ),
        "policy": (
            "A less hostile rate path supports gold, because the metal pays nothing and lives off the real rate.",
            "Tighter policy raises real rates, which is a direct offer in gold and only an indirect one in oil.",
        ),
        "chips": (
            "Chip-share demand can signal stronger industrial demand for copper, but it does not directly explain oil.",
            "A stronger AI and chip cycle raises expected copper demand. Oil follows only if the extra power demand reaches energy markets.",
        ),
        "china": (
            "Trade tension is a demand scare for copper and the grains that move on Chinese buying.",
            "A better US-China tone improves expected demand, which often appears first in copper and crops.",
        ),
        "geopolitics": (
            "Calmer geopolitics takes the supply premium out of oil. That is a barrel story, not a growth story.",
            "A fresh Middle East supply risk supports oil. Gold follows only if the shock also raises inflation concerns.",
        ),
    },
    "bonds": {
        "oil": (
            "Softer oil cools the inflation premium. Bond prices rise, which is the same fact as yields falling.",
            "Firmer oil lifts the inflation premium. Bond prices fall, which is the same fact as yields rising.",
        ),
        "yields": (
            "The yield move is the story. A lower yield is a higher bond price, driven by less inflation or less supply fear.",
            "Yields are rising. Bond prices are falling because investors want more compensation for inflation or for holding long-term bonds.",
        ),
        "policy": (
            "A less hostile policy path pulls yields down. The bond price rises because the expected path of the policy rate is lower.",
            "Tighter policy lifts yields. The bond price falls because cash and short rates are expected to pay more.",
        ),
        "chips": (
            "Weaker chip shares can increase demand for safer government bonds.",
            "Strong chip and AI shares show confidence, so long-term government bonds may be less attractive and yields can rise.",
        ),
        "china": (
            "Trade tension can increase demand for long-term government bonds if it does not also raise inflation.",
            "A better trade outlook improves growth expectations, which usually pushes yields a little higher.",
        ),
        "geopolitics": (
            "Calmer geopolitics reduces demand for safe government bonds, so yields can edge up.",
            "A fresh geopolitical shock increases demand for bonds, unless higher oil makes the shock inflationary.",
        ),
    },
    "crypto": {
        "oil": (
            "Softer oil is a risk-on tell for bitcoin only when it also eases the rate scare. Crypto has no cash flow of its own.",
            "Firmer oil raises concern about rates, and bitcoin is currently reacting more to available money than to inflation protection.",
        ),
        "yields": (
            "Yields fell, so holding a token that pays no interest became less costly and bitcoin gained support.",
            "Yields rose, so cash pays more and demand for a token that pays no interest can fade.",
        ),
        "policy": (
            "Lower expected rates leave investors more willing to buy crypto, which pays no interest.",
            "Higher expected rates make cash more attractive and usually weigh on bitcoin first, then on smaller coins.",
        ),
        "chips": (
            "Weaker chip shares show caution, and crypto often magnifies that mood.",
            "Buying in chip and AI shares reflects the same confidence that can lift bitcoin; the link is sentiment, not cash flow.",
        ),
        "china": (
            "Trade tension hits risk appetite, and crypto sells with it.",
            "A better US-China tone improves confidence. Bitcoin often rises, and smaller coins can magnify the move.",
        ),
        "geopolitics": (
            "Calmer geopolitics can support bitcoin when it also lowers oil prices and interest-rate pressure.",
            "A fresh geopolitical shock usually hurts bitcoin here; it is not behaving like gold in this market.",
        ),
    },
}


CAUSE_EXTRA = {
    "equities": {
        "data": (
            "Softer data pulls the expected rate path lower, which supports valuations even as it clouds the earnings outlook.",
            "Hot data leaves the Fed less room to ease. A higher rate path weighs on valuations unless earnings keep pace.",
        ),
        "fiscal": (
            "Less worry about Treasury supply lowers the extra return demanded for long-term bonds, which also supports stock valuations.",
            "Fiscal and bond-supply worries make investors demand more return on long-term bonds, which weighs on stock valuations.",
        ),
        "earnings": (
            "Weak results or guidance cut the E in the P/E directly; the index follows its heaviest names.",
            "Earnings beats lift the E in the P/E; the index follows its heaviest names.",
        ),
    },
    "fx": {
        "data": (
            "Softer US data lowers the expected Fed path, narrowing the dollar's rate advantage.",
            "Hot US data lifts the expected Fed path and widens the dollar's rate advantage.",
        ),
        "intervention": (
            "Officials are signalling they will sell dollars to defend their currency, which caps the pair.",
            "Officials are signalling they will sell dollars to defend their currency, which caps the pair.",
        ),
        "fiscal": (
            "Calmer fiscal news trims the risk premium in the currency.",
            "Fiscal worries add a risk premium to the currency: investors want more to hold it.",
        ),
    },
    "commodities": {
        "supply": (
            "Supply worries eased: the market expects more barrels or tonnes, so the price has to fall to clear them.",
            "Supply is tightening: fewer barrels or tonnes are expected, so the price rises to ration demand.",
        ),
        "data": (
            "Softer data is a demand scare for oil and copper, and it lowers real rates, which helps gold.",
            "Stronger data is a demand bid for oil and copper, but higher real rates weigh on gold.",
        ),
        "weather": (
            "Comfortable storage or milder weather means less demand for gas this season.",
            "Tight storage or weather-driven demand pulls natural gas higher.",
        ),
        "crops": (
            "Better harvest and export supply weighs on grain prices.",
            "Crop damage or export disruption tightens grain supply and lifts the price.",
        ),
    },
    "bonds": {
        "data": (
            "Softer data lowers the expected policy path: yields fall, which is the bond price rising.",
            "Hot data raises the expected policy path: yields rise, which is the bond price falling.",
        ),
        "fiscal": (
            "Lighter bond supply is easier for investors to absorb, so yields fall and bond prices rise.",
            "Heavy issuance and fiscal worries make investors demand more return: long yields rise and bond prices fall.",
        ),
    },
    "crypto": {
        "flows": (
            "ETF outflows and forced sales remove buying demand, so the price falls.",
            "ETF inflows add new buying demand, so the price can follow the money higher.",
        ),
        "data": (
            "Softer data points to easier liquidity ahead, which crypto prices first.",
            "Hot data points to tighter liquidity ahead, which crypto feels first.",
        ),
    },
}
DRIVER_LABEL = {
    "oil": ("Cheaper oil", "Firmer oil"),
    "yields": ("Lower Treasury yields", "Higher Treasury yields"),
    "policy": ("Softer central-bank path", "Tighter central-bank path"),
    "chips": ("AI / chip trade fading", "AI / chip bid"),
    "china": ("US-China trade tension", "US-China talks"),
    "geopolitics": ("Geopolitics calming", "Geopolitical risk"),
    "data": ("Soft economic data", "Hot economic data"),
    "fiscal": ("Fiscal worries easing", "Fiscal / bond supply worry"),
    "supply": ("Supply worries easing", "Tighter supply"),
    "earnings": ("Weak earnings", "Earnings beats"),
    "intervention": ("Intervention risk", "Intervention risk"),
    "flows": ("ETF outflows / liquidations", "ETF inflows"),
    "weather": ("Loose gas storage / weather", "Tight gas storage / weather"),
    "crops": ("Better crop supply", "Crop / export risk"),
}


def _because(cls: str, drivers: list[tuple[str, str, int]], empty: str | None = None) -> str:
    table = {**(CAUSE_EXTRA.get(cls) or {}), **(CAUSE.get(cls) or {})}
    for key, direction, _n in drivers[:1]:
        pair = table.get(key)
        if not pair:
            continue
        return pair[0] if direction == "down" else pair[1]
    return empty or "No wire from that session is still on the board. The percentage stands on its own."


BRIEF_FILE = DATA / "briefs.json"
CLASS_BRIEF = {
    "equities": ("Daily and weekly market drivers", "The equity indices", "index"),
    "fx": ("Daily and weekly market drivers", "The dollar pairs", "pair"),
    "commodities": ("Daily and weekly market drivers", "Commodities", "contract"),
    "bonds": ("Daily and weekly market drivers", "Government yields", "yield"),
    "crypto": ("Daily and weekly market drivers", "The major coins", "coin"),
}
CLASS_NEXT = {
    "equities": "The next session follows whichever index led. If that leader reverses, the rest of the complex usually follows.",
    "fx": "The next move follows the rate differential. A sharper dollar reprices every pair on this page together.",
    "commodities": "Oil is the contract that spills into the rest. A turn in crude reprices gold and the inflation bid at the same time.",
    "bonds": "A higher yield means a lower bond price. If the US 10-year yield falls back, long-term bond prices recover.",
    "crypto": "Bitcoin leads this complex. If it gives the move back, the higher-beta coins follow.",
}
NEXT_LINE = {
    "equities": NEXT_BY_DRIVER,
    "fx": {
        "oil": "A turn in crude reprices the dollar through inflation, and every pair here moves with that.",
        "yields": "If the US 10-year gives the move back, the dollar's rate advantage narrows and the pairs reverse together.",
        "policy": "The next decisions from both central banks matter. A higher-for-longer signal in one economy increases that currency's rate advantage.",
        "chips": "If chip-share buying fades, confidence fades with it and funding currencies strengthen.",
        "china": "A sour follow-up on tariffs hits the yuan first, then the currencies that trade with China.",
        "geopolitics": "A fresh geopolitical shock usually increases demand for the dollar and yen as safer currencies.",
    },
    "commodities": {
        "oil": "Watch the next oil move. If crude reverses, gold and inflation expectations may reverse with it.",
        "yields": "If real yields give the move back, gold's opportunity cost changes and the metal follows.",
        "policy": "The next rate decision changes how attractive interest-paying cash and bonds are compared with gold.",
        "chips": "Copper follows chip-share strength only while it still points to real industrial demand.",
        "china": "A sour trade follow-up hits copper and the crops that depend on Chinese buying.",
        "geopolitics": "A fresh supply headline puts the premium back into oil.",
    },
    "bonds": {
        "oil": "If crude reverses, the inflation premium in yields reverses. Remember a higher yield is a lower bond price.",
        "yields": "If the US 10-year gives the move back, the price of the bond recovers by the same amount the yield falls.",
        "policy": "The next central-bank announcement resets expected short-term rates; bond prices then move in the opposite direction from yields.",
        "chips": "If chip-share strength fades, selling pressure on long-term government bonds may also fade.",
        "china": "A sour trade follow-up is a growth scare, which bids bond prices unless oil makes it look inflationary.",
        "geopolitics": "A fresh shock increases demand for government bonds, unless it lifts oil and raises inflation concerns.",
    },
    "crypto": {
        "oil": "Oil matters to crypto mainly when it changes inflation and interest-rate expectations.",
        "yields": "If yields fall back, interest-paying cash becomes less attractive relative to bitcoin.",
        "policy": "Easier policy usually supports bitcoin; tighter policy usually weighs on it.",
        "chips": "If the chip bid fades, the same risk appetite that bid bitcoin fades with it.",
        "china": "A sour trade follow-up is risk-off, and the smaller coins amplify whatever bitcoin does.",
        "geopolitics": "A fresh shock is risk-off here. Bitcoin does not behave like gold on this tape.",
    },
}


GENERIC_NEXT = {
    "data": "The next data release (CPI, payrolls, GDP) decides whether this extends or reverses.",
    "fiscal": "The next Treasury auction is the test: weak demand pushes long yields higher again.",
    "supply": "The next OPEC+ decision and the weekly inventory data decide whether supply stays tight.",
    "earnings": "The next big results decide whether the index keeps following its heaviest names.",
    "intervention": "Watch for official action: an actual intervention reverses the pair sharply.",
    "flows": "Daily ETF flow numbers are the tell: a flip in flows flips the price.",
    "weather": "The next storage report and the weather forecast set the direction for gas.",
    "crops": "The next USDA report and Black Sea export news set the direction for grain.",
}


ASSET_NEXT = {
    "ng": {
        "supply": "The weekly EIA storage report and LNG export flows decide whether supply stays tight.",
        "geopolitics": "Any threat to LNG shipping routes puts the premium straight back into gas.",
    },
    "wh": {"supply": "The next USDA report and Black Sea export news set the direction for grain."},
}


def _condition(cls: str, key: str | None, direction: str | None = None, asset: str = "") -> str:
    own = (ASSET_NEXT.get(asset) or {}).get(key or "")
    if own:
        return own
    if key in GENERIC_NEXT and key not in (NEXT_LINE.get(cls) or {}):
        return GENERIC_NEXT[key]
    if key:
        line = (NEXT_LINE.get(cls) or {}).get(key)
        if isinstance(line, tuple):
            return line[0] if direction == "down" else line[1]
        if isinstance(line, str):
            return line
    return CLASS_NEXT.get(cls, "")


def day_moves_from_levels(levels: list[tuple]) -> list[dict]:
    """levels: (date, price) in order. Each entry is that date's change versus the previous print."""
    out = []
    for i in range(1, len(levels)):
        prev_day, prev = levels[i - 1]
        day, cur = levels[i]
        if not prev or not isinstance(cur, (int, float)):
            continue
        out.append(
            {
                "date": day.isoformat(),
                "pct": round((cur / prev - 1.0) * 100.0, 2),
                "bp": round((cur - prev) * 100.0, 1),
            }
        )
    return out[-12:]


def _business_date(ts: int, spec: dict) -> object:
    """Session date shown in HKT, folding a weekend close back to Friday."""
    day = datetime.fromtimestamp(ts, HKT).date()
    # FX closes at 17:00 New York on Friday, which is Saturday in Hong Kong.
    # US cash and futures prints can also land after HKT midnight.
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def day_moves_from_chart(chart: dict | None, spec: dict) -> list[dict]:
    pts = yahoo_points(chart) if chart else []
    by: dict = {}
    for p in pts:
        by[_business_date(p["time"], spec)] = p["value"]
    moves = day_moves_from_levels(sorted(by.items()))
    if not chart:
        return moves
    meta = chart.get("meta") or {}
    official = meta.get("regularMarketChangePercent")
    quote_time = meta.get("regularMarketTime")
    code, _label = session_status(spec, meta)
    # Once the session is complete, use Yahoo's own displayed change for that
    # date. This fixes FX reference-close differences and futures contract rolls.
    if (
        code in ("closed", "post")
        and isinstance(official, (int, float))
        and isinstance(quote_time, (int, float))
    ):
        day = _business_date(int(quote_time), spec).isoformat()
        last = meta.get("regularMarketPrice")
        prev = last / (1 + official / 100) if last is not None and official != -100 else None
        bp = (last - prev) * 100 if spec.get("kind") == "yield" and prev is not None else None
        item = {"date": day, "pct": round(float(official), 2), "bp": round(bp, 1) if bp is not None else None}
        moves = [m for m in moves if m.get("date") != day]
        moves.append(item)
        moves.sort(key=lambda m: m["date"])
    return moves[-12:]


CRYPTO_HISTORY: dict[str, tuple[float, list[dict]]] = {}
CRYPTO_HISTORY_TTL = 1800


def crypto_history(cg: str) -> list[dict]:
    """14 days of hourly prices, cached: the 7-day sparkline alone loses Monday's base by Sunday."""
    hit = CRYPTO_HISTORY.get(cg)
    if hit and time.time() - hit[0] < CRYPTO_HISTORY_TTL:
        return hit[1]
    pts = fetch_crypto_points(cg, 14)
    if pts:
        CRYPTO_HISTORY[cg] = (time.time(), pts)
        return pts
    return hit[1] if hit else []


def day_moves_from_spark(spark: list, history: list[dict] | None = None) -> list[dict]:
    vals = [v for v in spark if isinstance(v, (int, float))]
    span = 7 * 86400
    now = time.time()
    points = [(p["time"], p["value"]) for p in history or []]
    if len(vals) >= 2:
        step = span / (len(vals) - 1)
        start = now - span
        newest = points[-1][0] if points else 0
        points += [(start + i * step, px) for i, px in enumerate(vals) if start + i * step > newest]
    if len(points) < 2:
        return []
    by: dict = {}
    for t, px in sorted(points):
        by[datetime.fromtimestamp(t, HKT).date()] = px
    return day_moves_from_levels(sorted(by.items()))


def _load_notes() -> dict:
    try:
        return json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_notes(notes: dict) -> None:
    DATA.mkdir(exist_ok=True)
    BRIEF_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def _move_for(row: dict, day, today) -> dict | None:
    for item in row.get("dayMoves") or []:
        if item.get("date") == day.isoformat():
            return item
    # Briefing uses completed daily observations only. Do not substitute a live,
    # incomplete session for a daily close.
    return None


def _signed(row: dict, move: dict) -> float | None:
    """Signed move used in the explanation. Yields use basis points; other assets use percent."""
    if row.get("kind") == "yield" and isinstance(move.get("bp"), (int, float)):
        return float(move["bp"])
    pct = move.get("pct")
    return float(pct) if isinstance(pct, (int, float)) else None


def _rank_move(move: dict) -> float:
    """Compare every asset on the percentage move required by the class template."""
    pct = move.get("pct")
    return abs(float(pct)) if isinstance(pct, (int, float)) else -1.0


def _move_words(row: dict, move: dict) -> str:
    pct = move.get("pct")
    if not isinstance(pct, (int, float)):
        return "Unchanged"
    suffix = ""
    if row.get("kind") == "yield" and isinstance(move.get("bp"), (int, float)):
        suffix = f" ({move['bp']:+.0f}bp yield)"
    if pct > 0.05:
        return f"Up {abs(pct):.1f}%{suffix}"
    if pct < -0.05:
        return f"Down {abs(pct):.1f}%{suffix}"
    return f"Flat {pct:+.1f}%{suffix}"


def _term(_row: dict) -> str:
    """All briefing comparisons use one published daily observation versus the prior one."""
    return "Daily"


def _direction(signed: float | None, yield_move: bool) -> str:
    if signed is None or abs(signed) <= (0.5 if yield_move else 0.05):
        return "Neutral bias"
    return "Upward bias" if signed > 0 else "Downward bias"


def _wire_bounds(row: dict, day):
    """US and European closes print the next Hong Kong morning, so their copy lands then too."""
    if row.get("region") in ("US", "EU", "UK", "CA", "CH") or row.get("cls") in ("fx", "commodities", "crypto"):
        start = datetime.combine(day, dtime(12, 0), tzinfo=HKT)
        end = datetime.combine(day + timedelta(days=1), dtime(8, 30), tzinfo=HKT)
    else:
        start = datetime.combine(day, dtime(0, 0), tzinfo=HKT)
        end = datetime.combine(day + timedelta(days=1), dtime(0, 0), tzinfo=HKT)
    return start.timestamp(), end.timestamp()






def _all_wires(rows: list[dict]) -> list[dict]:
    heads = []
    seen: set[str] = set()
    for row in rows:
        for item in row.get("_pool") or row.get("headlines") or []:
            title = (item.get("title") or "").casefold()
            if title and title not in seen:
                seen.add(title)
                heads.append(item)
    return heads


FACTOR_PRINT = {"oil": "wti", "yields": "us10y"}


def _print_direction(all_rows: list[dict], key: str, day, today, weekly: bool = False) -> str | None:
    """The factor's own percentage, when it is on the board, outranks a noisy verb in a headline."""
    row = next((r for r in all_rows if r.get("id") == FACTOR_PRINT.get(key)), None)
    if not row:
        return None
    if weekly:
        signed = row.get("weekPct")
        if row.get("kind") == "yield" and isinstance(signed, (int, float)) and isinstance(row.get("last"), (int, float)):
            signed = row["last"] * signed
        threshold = 0.5 if row.get("kind") == "yield" else 0.05
    else:
        mv = _move_for(row, day, today)
        signed = _signed(row, mv) if mv else None
        threshold = 0.5 if row.get("kind") == "yield" else 0.05
    if not isinstance(signed, (int, float)) or abs(signed) <= threshold:
        return None
    return "down" if signed < 0 else "up"


def _with_print(drivers, all_rows, day, today, weekly: bool = False):
    if not drivers:
        return drivers
    key, _direction, n = drivers[0]
    printed = _print_direction(all_rows, key, day, today, weekly)
    if not printed:
        return drivers
    return [(key, printed, n), *drivers[1:]]


def _subject(row: dict) -> str:
    label = row["label"]
    if row.get("kind") == "yield" and "yield" not in label.casefold():
        return f"{label} yield"
    return label


CATALYST_LEAD = re.compile(r"\b(on|after|amid|following|despite|ahead of|as)\s+(.+)$", re.I)
CATALYST_PUSH = re.compile(
    r"^(.{8,}?)\s+(?:helps?|lifts?|pushes|push|sends?|drags?|weighs? on|hits?|boosts?|sparks?|fuels?|"
    r"spurs?|drives?|knocks?|rattles?|jolts?|batters?|buoys?)\b",
    re.I,
)
KEEP_CASE = {
    "Fed", "Iran", "China", "Chinese", "Japan", "Japanese", "Trump", "Powell", "Xi", "OPEC", "ECB", "BOJ",
    "BOE", "Europe", "European", "Treasury", "Treasuries", "Wall", "Street", "U.S.", "US", "UK", "EU",
    "Russia", "Ukraine", "Israel", "Hong", "Kong", "Nvidia", "Apple", "Tesla", "Microsoft", "AI", "GDP",
    "CPI", "PCE", "SEC", "ETF", "ETFs", "Bitcoin", "Ethereum", "Congress", "Washington", "Beijing",
    "Tokyo", "Saudi", "Gaza", "Biden", "Middle", "East", "Brent", "WTI", "BoJ", "PBOC", "RBA", "SNB",
}


def _clean_title(title: str) -> str:
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", title or "").strip()
    t = re.sub(r"^(watch|live|breaking|analysis|opinion)[:\s]+", "", t, flags=re.I)
    return t.strip(" .:;'\"‘’“”")


def _sentence_case(text: str, force: bool = False) -> str:
    words = text.split()
    caps = sum(1 for w in words if w[:1].isupper())
    if not words or (not force and caps < 0.6 * len(words)):
        return text
    out = []
    for w in words:
        core = w.strip(",.:;'\"‘’“”()$")
        head = re.split(r"[-’']", core)[0]
        if core in KEEP_CASE or head in KEEP_CASE or (core.isupper() and len(core) > 1) or re.search(r"\d", core):
            out.append(w)
        else:
            out.append(w.lower())
    return " ".join(out)


def _catalyst(title: str) -> str | None:
    """The cause clause of a headline, as a fragment. Never the whole headline."""
    t = _clean_title(title)
    t = t.split(": ")[0] if re.search(r":\s+(market analysis|what to know|explained|live|markets wrap)", t, re.I) else t
    t = re.sub(r"(?i):\s*markets wrap$", "", t)
    m = CATALYST_LEAD.search(t)
    if m:
        lead, clause = m.group(1).lower(), m.group(2).strip()
        weak = lead == "as" and len(clause.split()) < 3
        if len(clause) >= 14 and not weak and not re.match(
            r"(?i)(of|a result|well|the week|investors|traders|monday|tuesday|wednesday|thursday|friday|the day|record)\b",
            clause,
        ):
            return _sentence_case(clause, force=True)
    m = CATALYST_PUSH.match(t)
    if m and not re.match(r"(?i)^(stocks?|shares|markets?|the dow|dow|s&p|nasdaq|u\.?s\.? stocks)\b", m.group(1)):
        return _sentence_case(m.group(1), force=True)
    return None


def _moved_verb(row: dict, signed: float | None) -> str:
    if signed is None:
        return "was little changed"
    if row.get("kind") == "yield":
        if signed > 0.5:
            return f"yield rose {abs(signed):.0f}bp (the bond price fell)"
        if signed < -0.5:
            return f"yield fell {abs(signed):.0f}bp (the bond price rose)"
        return "yield was little changed"
    if signed > 0.05:
        return f"rose {abs(signed):.1f}%"
    if signed < -0.05:
        return f"fell {abs(signed):.1f}%"
    return "was little changed"


def _wire_dir_ok(item: dict, signed: float | None, yield_move: bool) -> int:
    """+1 if the headline moves the same way as the print, -1 if it cuts against it."""
    if signed is None:
        return 0
    title = item.get("title") or ""
    up = bool(DIR_UP.search(title) or (yield_move and re.search(r"(?i)\b(soar|highest|sell-?off)", title)))
    down = bool(DIR_DOWN.search(title)) and not (yield_move and re.search(r"(?i)sell-?off", title))
    if up == down:
        return 0
    return 1 if (up and signed > 0) or (down and signed < 0) else -1


# Which factors can plausibly move each asset. Everything else is noise for that name.
EQUITY_DRIVERS = {"oil", "yields", "policy", "chips", "china", "geopolitics", "data", "fiscal", "earnings"}
FX_DRIVERS = {"oil", "yields", "policy", "china", "geopolitics", "data", "fiscal", "intervention"}
BOND_DRIVERS = {"yields", "policy", "data", "fiscal", "oil", "geopolitics", "china"}
CRYPTO_DRIVERS = {"flows", "policy", "yields", "data", "geopolitics", "china"}
ASSET_DRIVERS = {
    "wti": {"oil", "supply", "geopolitics", "data", "china", "policy"},
    "ng": {"weather", "supply", "geopolitics", "data"},
    "au": {"yields", "policy", "geopolitics", "data"},
    "ag": {"yields", "policy", "geopolitics", "data", "china"},
    "cu": {"china", "data", "chips", "supply"},
    "wh": {"crops", "geopolitics", "china", "data", "supply"},
}
# The asset's own price is not a cause of itself.
SELF_FACTOR = {"wti": "oil", "us10y": "yields", "us30y": "yields", "de10y": "yields", "jp10y": "yields",
               "gb10y": "yields", "ca10y": "yields"}
# When one headline names several factors, the event outranks the price it moved.
LEAD_ORDER = ["policy", "data", "geopolitics", "china", "fiscal", "supply", "earnings", "flows",
              "weather", "crops", "intervention", "oil", "yields", "chips"]


# Sign of the asset's move when the factor rises. Yields are read as the yield, not the price.
EXPECT = {
    "equities": {"oil": -1, "yields": -1, "policy": -1, "chips": 1, "china": 1, "geopolitics": -1,
                 "data": -1, "fiscal": -1, "earnings": 1},
    "bonds": {"yields": 1, "policy": 1, "data": 1, "fiscal": 1, "oil": 1, "geopolitics": -1, "china": 1},
    "crypto": {"flows": 1, "policy": -1, "yields": -1, "data": -1, "geopolitics": -1, "china": 1},
}
# FX in dollar terms, then flipped for pairs quoted as foreign currency per dollar.
DOLLAR_EXPECT = {"yields": 1, "policy": 1, "data": 1, "oil": 1, "china": -1, "geopolitics": 1, "intervention": -1}
USD_BASE = {"jpy", "chf", "cny"}
ASSET_EXPECT = {
    "wti": {"supply": 1, "geopolitics": 1, "data": 1, "china": 1, "policy": -1},
    "ng": {"weather": 1, "supply": 1, "geopolitics": 1, "data": 1},
    "au": {"yields": -1, "policy": -1, "geopolitics": 1, "data": -1},
    "ag": {"yields": -1, "policy": -1, "geopolitics": 1, "data": -1, "china": 1},
    "cu": {"china": 1, "data": 1, "chips": 1, "supply": 1},
    "wh": {"crops": 1, "geopolitics": 1, "china": 1, "supply": 1},
}


# Where the class-wide mechanism describes the wrong market for this name.
ASSET_CAUSE = {
    "ng": {
        "geopolitics": (
            "Calmer Middle East risk takes the supply premium out of energy, LNG shipping included.",
            "Middle East risk threatens LNG and energy shipping, so the market puts a supply premium into gas.",
        ),
        "supply": (
            "More gas is expected to reach the market, so the price falls to clear it.",
            "Less gas is expected to reach the market, so the price rises to ration demand.",
        ),
    },
    "nky": {
        "chips": (
            "The AI and chip trade is fading, and Japan's heavyweight chip-equipment names drag the Nikkei with it.",
            "The AI and chip bid lifts Japan's heavyweight chip-equipment names, which carry the price-weighted Nikkei.",
        ),
    },
    "wh": {
        "supply": (
            "Better export and harvest supply weighs on grain prices.",
            "Tighter export or harvest supply lifts grain prices.",
        ),
    },
}

FX_POLICY_PAIR = {
    "eur": ("ECB", "Federal Reserve", "euro", "US dollar"),
    "jpy": ("Federal Reserve", "Bank of Japan", "US dollar", "yen"),
    "gbp": ("Bank of England", "Federal Reserve", "pound", "US dollar"),
    "chf": ("Federal Reserve", "Swiss National Bank", "US dollar", "Swiss franc"),
    "aud": ("Reserve Bank of Australia", "Federal Reserve", "Australian dollar", "US dollar"),
    "cny": ("Federal Reserve", "People's Bank of China", "US dollar", "yuan"),
}


def _fx_policy_context(focus: dict, signed: float | None) -> str:
    pair = FX_POLICY_PAIR.get(focus.get("id"))
    if not pair:
        return ""
    first_bank, second_bank, first_ccy, second_ccy = pair
    if signed is None or abs(signed) <= 0.05:
        return (
            f"{focus['label']} compares {first_bank} policy with {second_bank} policy. "
            "Neither side has a clear advantage in this move, so both rate outlooks must be checked."
        )
    if signed > 0:
        winner_bank, loser_bank = first_bank, second_bank
        winner_ccy, loser_ccy = first_ccy, second_ccy
    else:
        winner_bank, loser_bank = second_bank, first_bank
        winner_ccy, loser_ccy = second_ccy, first_ccy
    return (
        f"{focus['label']} moved in favour of the {winner_ccy} over the {loser_ccy}. "
        f"This is a two-central-bank comparison: it fits a relatively tighter {winner_bank} outlook, "
        f"a relatively easier {loser_bank} outlook, or both."
    )


def _asset_mechanism(
    focus: dict, signed: float | None, cls: str, key: str, direction: str, drivers: list[tuple] | None = None
) -> str:
    own = (ASSET_CAUSE.get(focus["id"]) or {}).get(key)
    base = own[0 if direction == "down" else 1] if own else _because(
        cls, drivers or [(key, direction, 1)], empty=""
    )
    if cls == "fx":
        context = _fx_policy_context(focus, signed)
        return f"{base} {context}".strip()
    return base


def _expected_sign(focus: dict, key: str, direction: str) -> int:
    """+1 or -1 for the move this factor should cause, 0 when the link is ambiguous."""
    if focus["id"] in ASSET_EXPECT:
        base = ASSET_EXPECT[focus["id"]].get(key, 0)
    elif focus.get("cls") == "fx":
        base = DOLLAR_EXPECT.get(key, 0) * (1 if focus["id"] in USD_BASE else -1)
    else:
        base = EXPECT.get(focus.get("cls"), {}).get(key, 0)
    return base * (1 if direction == "up" else -1)


def _coherent(focus: dict, key: str, direction: str, signed: float | None) -> bool:
    if signed is None:
        return True
    threshold = 0.5 if focus.get("kind") == "yield" else 0.05
    if abs(signed) <= threshold:
        return True
    want = _expected_sign(focus, key, direction)
    return want == 0 or (want > 0) == (signed > 0)


def _allowed(focus: dict) -> set[str]:
    if focus["id"] in ASSET_DRIVERS:
        return ASSET_DRIVERS[focus["id"]]
    return {
        "equities": EQUITY_DRIVERS,
        "fx": FX_DRIVERS,
        "bonds": BOND_DRIVERS,
        "crypto": CRYPTO_DRIVERS,
    }.get(focus.get("cls"), set(LEAD_ORDER))


def _clause_driver(clause: str, focus: dict) -> tuple[str, str] | None:
    """The factor the cause clause is about, and which way it moved, read from its own words."""
    allowed = _allowed(focus)
    own = SELF_FACTOR.get(focus["id"])
    hits = []
    for key, pat, _soft, _firm in INDEX_DRIVERS:
        if key not in allowed or not pat.search(clause):
            continue
        hits.append(key)
    others = [k for k in hits if k != own]
    if not others:
        return None
    key = min(others, key=lambda k: LEAD_ORDER.index(k) if k in LEAD_ORDER else 99)
    pat = next(p for k, p, _a, _b in INDEX_DRIVERS if k == key)
    direction = _near_direction(pat, clause, key)
    if direction is None:
        return None
    return key, direction


def _collect_wires(rows: list[dict], focus: dict, day, lookback_days: int = 0) -> list[dict]:
    start, end = _wire_bounds(focus, day)
    if lookback_days:
        earlier, _ = _wire_bounds(focus, day - timedelta(days=lookback_days))
        start = earlier
    pool = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: r is not focus):
        for item in row.get("_pool") or row.get("headlines") or []:
            ts = item.get("ts")
            key = (item.get("title") or "").casefold()
            if isinstance(ts, (int, float)) and start <= ts < end and key not in seen:
                seen.add(key)
                pool.append(item)
    return pool


def _infer_from_prints(focus: dict, signed: float | None, all_rows: list[dict], day, today, weekly: bool = False):
    """If the wires are thin, read the factor that is actually on this board."""
    hits = []
    own = SELF_FACTOR.get(focus["id"])
    for key in ("oil", "yields", "policy", "data"):
        if key not in _allowed(focus) or key == own:
            continue
        printed = _print_direction(all_rows, key, day, today, weekly)
        if printed and _coherent(focus, key, printed, signed):
            hits.append((key, printed, 1))
    return hits


def _plausible(focus: dict, signed: float | None) -> tuple[str | None, str | None]:
    """A coherent factor for this sign, used only when neither the day nor the prior sessions name one."""
    own = SELF_FACTOR.get(focus["id"])
    for key in LEAD_ORDER:
        if key not in _allowed(focus) or key == own:
            continue
        for direction in ("down", "up"):
            if _coherent(focus, key, direction, signed):
                return key, direction
    return None, None


def _fill_why(focus: dict, signed: float | None, cls: str, key: str, direction: str, lead_in: str) -> dict:
    label = DRIVER_LABEL.get(key, ("", ""))[0 if direction == "down" else 1]
    mechanism = _asset_mechanism(focus, signed, cls, key, direction)
    moved = _moved_verb(focus, signed)
    if "yield" in focus["label"].casefold() and moved.startswith("yield "):
        moved = moved[len("yield ") :]
    catalyst = f"{focus['label']} {moved}. {lead_in}".strip()
    return {
        "driver": label,
        "catalyst": catalyst,
        "mechanism": mechanism,
        "explanation": f"{catalyst} {mechanism}".strip(),
        "key": key,
        "direction": direction,
    }


def _explain(focus: dict, signed: float | None, pool: list[dict], cls: str, drivers_fix, period: str = "day", carry: bool = False) -> dict:
    """Catalyst from the wires, preferring ones that name this asset, then the transmission mechanism."""
    yield_move = focus.get("kind") == "yield"
    pats = NEWS_KEYWORDS.get(focus["id"]) or []
    allowed = _allowed(focus)
    ranked = []
    for item in pool:
        blob = f"{item.get('title') or ''} {item.get('excerpt') or ''}"
        named = any(re.search(p, blob, re.I) for p in pats)
        agree = _wire_dir_ok(item, signed, yield_move)
        if agree < 0:
            continue
        clause = _catalyst(item.get("title") or "")
        found = _clause_driver(clause, focus) if clause else None
        if found and not _coherent(focus, found[0], found[1], signed):
            found = None
        if not found:
            clause = None
        score = (5 if named else 0) + (4 if found else 0) + agree * 2 + SOURCE_RANK.get(item.get("source") or "", 0) / 10
        ranked.append((score, named, clause, found, item))
    ranked.sort(key=lambda x: x[0], reverse=True)
    lead = next((r for r in ranked if r[2]), None)
    named_items = [r[4] for r in ranked if r[1]]
    name = focus["label"]
    moved = _moved_verb(focus, signed)
    if "yield" in name.casefold() and moved.startswith("yield "):
        moved = moved[len("yield ") :]

    def sources(items):
        out = []
        for it in items:
            src = it.get("source")
            if src and src not in out:
                out.append(src)
        return _join(out[:3])

    if lead:
        _s, lead_named, clause, (key, direction), item = lead
        if key == "intervention":
            clause = _clean_title(item.get("title") or clause)
        agreeing = [r[4] for r in ranked if r[3] and r[3][0] == key][:3]
        src = sources([item, *agreeing])
        clause = clause[:1].upper() + clause[1:]
        if carry:
            catalyst = f"{name} {moved}. Earlier reports this week pointed to: {clause} ({src})."
        elif lead_named:
            catalyst = f"{name} {moved} because {clause[0].lower() + clause[1:]} ({src})."
        else:
            catalyst = f"{name} {moved}. Other markets and reports that {period} showed: {clause} ({src})."
        drivers = [(key, direction, 1)]
    else:
        drivers = [d for d in drivers_fix(_driver_counts(named_items[:6], min_hits=1)) if d[0] in allowed]
        drivers = [
            d for d in drivers
            if d[0] != SELF_FACTOR.get(focus["id"]) and _coherent(focus, d[0], d[1], signed)
        ]
        key, direction = (drivers[0][0], drivers[0][1]) if drivers else (None, None)
        if key:
            label = DRIVER_LABEL[key][0 if direction == "down" else 1]
            when = "earlier-week" if carry else period
            catalyst = f"{name} {moved}. {sources(named_items)} {when} coverage ties it to {label.lower()}."
        else:
            return {
                "driver": "",
                "catalyst": "",
                "mechanism": "",
                "explanation": "",
                "key": None,
                "direction": None,
            }
    label = DRIVER_LABEL.get(key, ("", ""))[0 if direction == "down" else 1] if key else ""
    mechanism = _asset_mechanism(focus, signed, cls, key, direction, drivers) if key and direction else ""
    return {
        "driver": label,
        "catalyst": catalyst,
        "mechanism": mechanism,
        "explanation": f"{catalyst} {mechanism}".strip(),
        "key": key,
        "direction": direction,
    }


def _outlook(focus: dict, signed: float | None, cls: str, why: dict, period: str = "daily") -> str:
    """A conditional trend, not a mechanical extrapolation of the latest move."""
    subject = _subject(focus)
    key, direction = why.get("key"), why.get("direction")
    if not key:
        bias = _direction(signed, focus.get("kind") == "yield")
        return f"{bias} for {subject} unless the next print reverses the daily move."
    bias = _direction(signed, focus.get("kind") == "yield")
    driver = (why.get("driver") or "the identified catalyst").lower()
    condition = _condition(cls, key, direction, focus["id"])
    line = f"{bias} for {subject} if {driver} persists."
    answer = f"{line} {condition}".strip() if condition else line
    pair = FX_POLICY_PAIR.get(focus.get("id"))
    if pair:
        answer += f" Compare the next {pair[0]} and {pair[1]} decisions and data; do not read only one side."
    return answer


def _day_note(rows: list[dict], day, today, cls: str, all_rows: list[dict]) -> dict:
    moves = [(row, mv) for row in rows if (mv := _move_for(row, day, today))]
    if not moves:
        return {"explanation": "No session yet." if day > today else "No print posted on this date."}
    focus, mv = max(moves, key=lambda p: _rank_move(p[1]))
    why = evidence.analysis(focus, mv.get("pct"), day, "daily")
    if cls == "crypto":
        term = "Today so far (since 00:00 HKT)" if day == today else "Daily (00:00–24:00 HKT)"
    else:
        term = "Daily (close-to-close)"
    return {**why, "version": 12, "focus": focus["label"], "move": _move_words(focus, mv), "term": term}


def _week_signed(row: dict) -> float | None:
    p = row.get("weekPct")
    if not isinstance(p, (int,float)):
        return None
    if row.get("kind") == "yield" and isinstance(row.get("last"), (int,float)) and p != -100:
        return (row["last"] - row["last"] / (1 + p / 100)) * 100
    return p


def _weekly_reading(rows: list[dict], cls: str, all_rows: list[dict]) -> dict:
    ranked = [r for r in rows if _week_signed(r) is not None]
    if not ranked:
        return {"explanation": "The weekly path is not available yet."}
    focus = max(ranked, key=lambda r: abs(float(r.get("weekPct") or 0)))
    why = evidence.analysis(focus, focus.get("weekPct"), period="weekly")
    fake = {"pct": focus.get("weekPct"), "bp": _week_signed(focus) if focus.get("kind") == "yield" else None}
    term = "Week-to-date (Mon 00:00 HKT to now, 7-day market)" if cls == "crypto" else "Week-to-date (from daily closes)"
    return {**why, "version": 12, "focus": focus["label"], "move": _move_words(focus, fake), "term": term}


def _week_to_date(row: dict, week_days: list, today) -> float | None:
    """Monday-to-latest change, compounded from this week's daily closes only."""
    growth, seen = 1.0, False
    for d in week_days:
        if d > today:
            break
        mv = _move_for(row, d, today)
        if mv and isinstance(mv.get("pct"), (int, float)):
            growth *= 1 + mv["pct"] / 100
            seen = True
    return round((growth - 1) * 100, 2) if seen else None


def _bar(row: dict, pct: float, bp, focus: str | None) -> dict:
    return {
        "label": row["label"],
        "pct": round(float(pct), 2),
        "bp": bp if row.get("kind") == "yield" else None,
        "focus": row["label"] == focus,
    }


def class_briefs(rows: list[dict]) -> dict:
    """Mon–Fri notes (Mon–Sun for 24/7 crypto) for the current week, plus a separate weekly reading."""
    today = datetime.now(HKT).date()
    monday = today - timedelta(days=today.weekday())
    week_key = today.strftime("%G-W%V")
    notes = _load_notes()
    bucket = notes.setdefault(week_key, {})
    notes = {week_key: bucket}
    briefs = {}
    changed = False
    for cls, (title, _noun, _one) in CLASS_BRIEF.items():
        group = [r for r in rows if r.get("cls") == cls]
        week_days = [monday + timedelta(days=i) for i in range(7 if cls == "crypto" else 5)]
        saved = bucket.setdefault(cls, {})
        days = []
        sources: list[str] = []
        for day in week_days:
            key = day.isoformat()
            kept = saved.get(key)
            # Rewrite notes created under an older horizon/ranking schema.
            thin = not (isinstance(kept, dict) and kept.get("version") == 12)
            if day > today:
                note = {"explanation": "No session yet."}
            else:
                note = _day_note(group, day, today, cls, rows)
                if note.get("move") and kept != note:
                    saved[key] = note
                    changed = True
            for row in group:
                for item in row.get("headlines") or []:
                    src = item.get("source")
                    if src and src not in sources:
                        sources.append(src)
            bars, gaps = [], []
            if day <= today:
                for row in group:
                    mv = _move_for(row, day, today)
                    if mv and isinstance(mv.get("pct"), (int, float)):
                        bars.append(_bar(row, mv["pct"], mv.get("bp"), note.get("focus")))
                    else:
                        seen = [m.get("date") for m in row.get("dayMoves") or [] if m.get("date")]
                        later = bool(seen) and max(seen) > key
                        gaps.append({"label": row["label"], "pct": None, "bp": None, "focus": False,
                                     "note": "Market closed (holiday)" if later else "Not published yet"})
            days.append(
                {
                    "date": key,
                    "label": day.strftime("%a %-d %b"),
                    "state": "ahead" if day > today else "today" if day == today else "done",
                    "focus": note.get("focus") or "",
                    "move": note.get("move") or "",
                    "term": note.get("term") or "",
                    "driver": note.get("driver") or "",
                    "catalyst": note.get("catalyst") or "",
                    "mechanism": note.get("mechanism") or "",
                    "explanation": note.get("explanation") or "",
                    "next": note.get("next") or "",
                    "text": note.get("explanation") or "No session yet.",
                    **{k: note.get(k) for k in ("observed", "confidence", "confidenceMeaning", "evidence", "banks", "evidenceThrough", "points", "nextPoints")},
                    "bars": sorted(bars, key=lambda b: b["pct"], reverse=True) + gaps,
                }
            )
        wtd = [{**r, "weekPct": _week_to_date(r, week_days, today)} for r in group]
        week = _weekly_reading(wtd, cls, rows)
        week["bars"] = sorted(
            [
                _bar(r, r["weekPct"], None, week.get("focus"))
                for r in wtd
                if isinstance(r.get("weekPct"), (int, float))
            ],
            key=lambda b: b["pct"],
            reverse=True,
        )
        focus_row = next((r for r in group if r["label"] == week.get("focus")), None)
        week["path"] = []
        for d in week_days:
            if not focus_row:
                continue
            if d > today:
                week["path"].append({"date": d.isoformat(), "label": d.strftime("%a"), "pct": None, "ahead": True})
                continue
            mv = _move_for(focus_row, d, today)
            week["path"].append({"date": d.isoformat(), "label": d.strftime("%a"), "pct": mv["pct"] if mv else None, "today": d == today})
        briefs[cls] = {
            "title": title,
            "days": days,
            "week": week,
            "note": "Morning meeting: the name, the percentage move, the term, then the explanation and what comes next.",
        }
    if changed:
        _save_notes(notes)
    return briefs


CLASS_WORD = {"equities": "Stocks", "fx": "FX", "commodities": "Commodities", "bonds": "Bonds", "crypto": "Crypto"}


def market_pulse(rows: list[dict], regime: str) -> dict:
    return evidence.pulse(rows)


PIN_FILE = DATA / "day-moves-pin.json"


def pinned_day_moves() -> dict:
    """Daily moves recorded where Yahoo is reachable, so the hosted board shows the same week."""
    try:
        return json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def assemble() -> dict:
    charts: dict[str, dict | None] = {}
    crypto_rows: dict[str, dict] = {}
    news: list[dict] = []

    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {
            (
                pool.submit(fetch_sovereign, spec)
                if spec.get("feed")
                else pool.submit(fetch_chart, spec["symbol"])
            ): spec["id"]
            for spec in UNIVERSE
        }
        crypto_fut = pool.submit(fetch_crypto)
        news_fut = pool.submit(fetch_news)
        for fut in as_completed(futs):
            charts[futs[fut]] = fut.result()
        try:
            crypto_rows = crypto_fut.result()
        except Exception:
            crypto_rows = {}
        try:
            news = news_fut.result() or []
        except Exception:
            news = []

    rows = [build_row(spec, charts.get(spec["id"])) for spec in UNIVERSE]
    pins = pinned_day_moves()
    for spec in CRYPTO:
        row = crypto_rows.get(spec["id"]) or empty_row(spec)
        row["source"] = row.get("source") or "CoinGecko"
        row["lag"] = row.get("lag") or "Near real-time"
        row["sourceUrl"] = quote_url(spec)
        row["chartUrl"] = chart_url(spec)
        row["chartSite"] = chart_site(spec)
        row["sessionCode"] = "open"
        row["sessionLabel"] = "OPEN 24/7"
        if pins.get(spec["id"]):
            by = {m["date"]: m for m in row.get("dayMoves") or []}
            by.update({m["date"]: m for m in pins[spec["id"]]})
            row["dayMoves"] = sorted(by.values(), key=lambda m: m["date"])[-12:]
        rows.append(row)

    specs = {s["id"]: s for s in [*UNIVERSE, *CRYPTO]}
    for row in rows:
        if row.get("cls") != "crypto":
            row["dayMoves"] = day_moves_from_chart(charts.get(row["id"]), specs[row["id"]])
            if pins.get(row["id"]):
                by = {m["date"]: m for m in row["dayMoves"]}
                by.update({m["date"]: m for m in pins[row["id"]]})
                row["dayMoves"] = sorted(by.values(), key=lambda m: m["date"])[-12:]
    regime = regime_from(rows)
    for row in rows:
        expl, nxt = narrative(row, regime)
        row["explanation"] = expl
        row["next"] = nxt
    mark_movers(rows)
    attach_headlines(rows, news)
    evidence.enrich(rows, news)
    briefs = class_briefs(rows)
    for row in rows:
        row.pop("_pool", None)
    by_cls: dict[str, list[dict]] = {}
    for row in rows:
        by_cls.setdefault(row["cls"], []).append(row)

    snapshot = {
        "status": "ok",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "updatedAtHkt": datetime.now(HKT).strftime("%a %d %b %Y  %H:%M:%S HKT"),
        "regime": regime,
        "pulse": market_pulse(rows, regime),
        "briefs": briefs,
        "autoRefreshSec": 15,
        "rebuildSec": 60,
        "realtime": False,
        "sources": [
            {
                "id": "yahoo",
                "name": "Yahoo Finance",
                "covers": "Cash index levels, FX pairs, commodity futures and the two US Treasury yield indices (^TNX, ^TYX) — the actual prints, no ETF proxies",
                "lag": "Quote and official session-change fields are polled every 30s; venue delay runs from near-live on FX and cash indices to ~10-20 minutes on futures",
            },
            {
                "id": "sovereign",
                "name": "ECB / Japan MoF / Bank of England / Bank of Canada",
                "covers": "Euro area, Japan, UK and Canada 10-year government yields, taken from each issuer's own published curve rather than a bond ETF",
                "lag": "Official daily fixing — one print per business day, re-read every 15 minutes",
            },
            {
                "id": "crypto-live",
                "name": "Coinbase / OKX",
                "covers": "Crypto last price and 24h change, streamed trade by trade (BNB via OKX, the rest in true USD on Coinbase)",
                "lag": "Real-time — direct exchange WebSocket, no polling",
            },
            {
                "id": "coingecko",
                "name": "CoinGecko",
                "covers": "Crypto chart history, market cap and volume",
                "lag": "Near real-time, polled every 30s",
            },
            {
                "id": "wires",
                "name": "Wires",
                "covers": "Reviewed central-bank releases, Bloomberg on SWI, Reuters syndication, AP and specialist commodity research, alongside direct publisher feeds. Each link states its source, relevance and check date. Related headlines are not automatically causal evidence",
                "lag": "Prices and feeds refresh automatically. News covers the latest seven HKT calendar days. Reviewed analysis is dated and requires an editorial update when new evidence arrives. HTTP checks are cached 30 minutes; browser readability checks expire after seven days. Earlier policy decisions are labelled separately",
            },
            {
                "id": "charting",
                "name": "Charting",
                "covers": "Lightweight Charts (Apache 2.0) renders the dashboard panels; every point plotted comes from the feeds above",
                "lag": "Rendering only — no third-party data",
            },
        ],
        "assets": rows,
        "news": news,
        "newsBySource": news_by_source(news),
        "classes": [
            {
                **item,
                "session": class_session(by_cls.get(item["id"], [])),
            }
            for item in [
                {"id": "equities", "title": "Stocks", "sub": "Shares / Equity Markets", "source": "Yahoo Finance"},
                {"id": "fx", "title": "Foreign Exchange", "sub": "FX / Currency Markets", "source": "Yahoo Finance"},
                {"id": "commodities", "title": "Commodities", "sub": "Precious metals, oil, wheat", "source": "Yahoo Finance"},
                {"id": "bonds", "title": "Bonds", "sub": "Fixed Income / Debt Markets", "source": "Yahoo Finance"},
                {"id": "crypto", "title": "Cryptocurrencies", "sub": "24h change", "source": "CoinGecko"},
            ]
        ],
    }
    return snapshot


def live_prints(snap: dict) -> int:
    return sum(1 for a in (snap.get("assets") or []) if a.get("ok") and a.get("last") is not None)


def carry_last_good(snap: dict, prev: dict) -> None:
    """One flaky upstream call should not blank a card that printed a minute ago."""
    old = {r["id"]: r for r in (prev or {}).get("assets") or [] if r.get("ok")}
    for i, row in enumerate(snap.get("assets") or []):
        if row.get("ok"):
            continue
        was = old.get(row["id"])
        if not was:
            continue
        snap["assets"][i] = {
            **was,
            # the wires refreshed fine even if the price did not
            "headlines": row.get("headlines") or was.get("headlines") or [],
            "newsAsOf": row.get("newsAsOf") or was.get("newsAsOf"),
            "wireScope": row.get("wireScope") or was.get("wireScope"),
            "stale": True,
        }


def rebuild_snapshot_views(snap: dict) -> None:
    """Recompute every derived panel after stale quote rows have been restored."""
    snap["realtime"] = False
    snap["sources"] = [
            {
                "id": "yahoo",
                "name": "Yahoo Finance",
                "covers": "Cash index levels, FX pairs, commodity futures and the two US Treasury yield indices (^TNX, ^TYX) — the actual prints, no ETF proxies",
                "lag": "Quote and official session-change fields are polled every 30s; venue delay runs from near-live on FX and cash indices to ~10-20 minutes on futures",
            },
            {
                "id": "sovereign",
                "name": "ECB / Japan MoF / Bank of England / Bank of Canada",
                "covers": "Euro area, Japan, UK and Canada 10-year government yields, taken from each issuer's own published curve rather than a bond ETF",
                "lag": "Official daily fixing — one print per business day, re-read every 15 minutes",
            },
            {
                "id": "crypto-live",
                "name": "Coinbase / OKX",
                "covers": "Crypto last price and 24h change, streamed trade by trade (BNB via OKX, the rest in true USD on Coinbase)",
                "lag": "Real-time — direct exchange WebSocket, no polling",
            },
            {
                "id": "coingecko",
                "name": "CoinGecko",
                "covers": "Crypto chart history, market cap and volume",
                "lag": "Near real-time, polled every 30s",
            },
            {
                "id": "wires",
                "name": "Wires",
                "covers": "Reviewed central-bank releases, Bloomberg on SWI, Reuters syndication, AP and specialist commodity research, alongside direct publisher feeds. Each link states its source, relevance and check date. Related headlines are not automatically causal evidence",
                "lag": "Prices and feeds refresh automatically. News covers the latest seven HKT calendar days. Reviewed analysis is dated and requires an editorial update when new evidence arrives. HTTP checks are cached 30 minutes; browser readability checks expire after seven days. Earlier policy decisions are labelled separately",
            },
            {
                "id": "charting",
                "name": "Charting",
                "covers": "Lightweight Charts (Apache 2.0) renders the dashboard panels; every point plotted comes from the feeds above",
                "lag": "Rendering only — no third-party data",
            },
        ]
    rows = snap.get("assets") or []
    regime = regime_from(rows)
    evidence.enrich(rows)
    mark_movers(rows)
    snap["regime"] = regime
    snap["pulse"] = market_pulse(rows, regime)
    snap["briefs"] = class_briefs(rows)
    by_cls: dict[str, list[dict]] = {}
    for row in rows:
        by_cls.setdefault(row["cls"], []).append(row)
    for cls in snap.get("classes") or []:
        cls["session"] = class_session(by_cls.get(cls.get("id"), []))


def refresh_loop() -> None:
    global SNAPSHOT
    while True:
        try:
            snap = assemble()
            with LOCK:
                prev = SNAPSHOT
            carry_last_good(snap, prev)
            rebuild_snapshot_views(snap)
            n_ok = live_prints(snap)
            if n_ok == 0 and live_prints(prev) > 0:
                print("[warn] empty prints — keep last good board", flush=True)
                with LOCK:
                    SNAPSHOT = {**prev, "status": "stale", "error": "upstream empty"}
            else:
                DATA.mkdir(exist_ok=True)
                CACHE.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
                with LOCK:
                    SNAPSHOT = snap
                print(f"[ok] snapshot {snap['updatedAtHkt']} regime={snap['regime']} prints={n_ok}", flush=True)
        except Exception as exc:
            print(f"[err] refresh {exc}", flush=True)
            with LOCK:
                SNAPSHOT = {**SNAPSHOT, "status": "error", "error": str(exc)}
        time.sleep(60)


def json_response(handler: SimpleHTTPRequestHandler, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[http]", self.address_string(), fmt % args, flush=True)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        self.path = parsed.path or "/"
        if self.path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()
        if self.path.startswith("/api/chart"):
            qs = urllib.parse.parse_qs(parsed.query)
            asset_id = (qs.get("id") or [""])[0]
            range_key = (qs.get("range") or ["1M"])[0].upper()
            if range_key not in CHART_RANGES:
                range_key = "1M"
            return json_response(self, series_for(asset_id, range_key))
        if self.path.startswith("/api/refchart"):
            qs = urllib.parse.parse_qs(parsed.query)
            body = fred_png((qs.get("id") or [""])[0])
            if not body:
                self.send_error(404, "no reference chart")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/news"):
            with LOCK:
                payload = {
                    "updatedAtHkt": SNAPSHOT.get("updatedAtHkt"),
                    "news": SNAPSHOT.get("news") or [],
                    "newsBySource": SNAPSHOT.get("newsBySource") or {},
                }
            return json_response(self, payload)
        if self.path.startswith("/api/snapshot"):
            with LOCK:
                payload = SNAPSHOT
            return json_response(self, payload)
        return super().do_GET()


def lan_host() -> str:
    """Wi-Fi address other machines on this network can open. Not a public internet address."""
    import subprocess

    found: list[tuple[int, str]] = []
    try:
        names = subprocess.check_output(["ifconfig", "-l"], text=True).split()
    except (OSError, subprocess.CalledProcessError):
        names = []
    skip = {"lo0", "gif0", "stf0", "awdl0", "llw0", "bridge0", "ap1"}
    for name in names:
        if name in skip or name.startswith("utun") or name.startswith("anpi"):
            continue
        try:
            ip = subprocess.check_output(["ipconfig", "getifaddr", name], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if ip.startswith(("192.168.", "10.")) or ip.startswith("172."):
            found.append((0 if name == "en0" else 1, ip))
    if found:
        found.sort()
        return found[0][1]
    return ""


def main() -> None:
    DATA.mkdir(exist_ok=True)
    if CACHE.exists():
        try:
            global SNAPSHOT
            SNAPSHOT = json.loads(CACHE.read_text(encoding="utf-8"))
            SNAPSHOT["status"] = SNAPSHOT.get("status") or "cache"
            # carry crypto stats across restarts so a throttled first fetch
            # still has 1w / 1m / market cap to show
            for row in SNAPSHOT.get("assets") or []:
                if row.get("cls") == "crypto" and row.get("last") is not None:
                    CRYPTO_LAST_GOOD[row["id"]] = {**row, "headlines": []}
            print("[boot] loaded cache", flush=True)
        except json.JSONDecodeError:
            pass
    if SNAPSHOT.get("assets"):
        rebuild_snapshot_views(SNAPSHOT)
    if os.environ.get("SNAPSHOT_ONLY") != "1":
        threading.Thread(target=refresh_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), PORT), Handler)
    print(f"Morning Meeting Dashboard → http://127.0.0.1:{PORT}", flush=True)
    share = lan_host()
    if share:
        print(f"Same network → http://{share}:{PORT}", flush=True)
    print(f"Market background → http://127.0.0.1:{PORT}/background.html", flush=True)
    print("Press F in the browser for projector / fullscreen.", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
