# Morning Meeting Dashboard

在另一台电脑上打开：

```bash
git clone https://github.com/carolinezy99-wq/fina6010-dashboard.git
cd fina6010-dashboard
python3 server.py
```

浏览器打开 http://127.0.0.1:6010

只需要 Python 3.9 或更新版本，不用再安装别的包。按 **F** 全屏。页面会自己刷新。

GitHub 网页不能直接打开这个看板。它要实时读取 Yahoo、央行和交易所的数据，所以必须在那台电脑上先运行 `python3 server.py`。

## What it shows

Six assets in each class: stocks, foreign exchange, commodities, bonds, and cryptocurrencies. Prices come from Yahoo Finance, official yield fixings (ECB, Japan Ministry of Finance, Bank of England, Bank of Canada), Coinbase, OKX, and CoinGecko. No ETF proxies.

The daily and weekly summary follows the class format: report, percentage move, term, explanation, and what comes next. Each explanation links to the article it uses.
