import hashlib
import json
import re
import httpx

TRADES_URL = "https://www.capitoltrades.com/trades"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html"}

_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)')
_TXTYPE_RE = re.compile(r'"txType":"(?:buy|sell)"')


def _clean_ticker(raw_ticker: str | None) -> str | None:
    """Capitol Trades tickers look like 'NVDA:US' or 'BRK/B:US' -> 'NVDA' / 'BRK.B'."""
    if not raw_ticker:
        return None
    t = str(raw_ticker).split(":")[0].strip().replace("/", ".")
    return t or None


def _enclosing_object(text: str, idx: int) -> str | None:
    """Return the JSON object literal directly enclosing position idx (brace-balanced)."""
    depth = 0
    start = None
    for j in range(idx, -1, -1):
        ch = text[j]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                start = j
                break
            depth -= 1
    if start is None:
        return None
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def extract_trades_from_html(html: str) -> list[dict]:
    """Pure: pull trade objects out of Capitol Trades' Next.js RSC flight payload and map
    them into the raw shape parse_capitol_trades expects. No network."""
    decoded = "".join(json.loads(c) for c in _CHUNK_RE.findall(html))
    raw: list[dict] = []
    seen_tx: set = set()
    for m in _TXTYPE_RE.finditer(decoded):
        obj_str = _enclosing_object(decoded, m.start())
        if not obj_str or '"issuerTicker"' not in obj_str:
            continue
        try:
            t = json.loads(obj_str)
        except (ValueError, json.JSONDecodeError):
            continue
        tx_id = t.get("_txId")
        if tx_id in seen_tx:
            continue
        seen_tx.add(tx_id)
        pol = t.get("politician") or {}
        issuer = t.get("issuer") or {}
        raw.append({
            "txType": t.get("txType"),
            "txDate": t.get("txDate"),
            "pubDate": t.get("pubDate"),
            "politician": {"firstName": pol.get("firstName", ""), "lastName": pol.get("lastName", "")},
            "asset": {"assetTicker": _clean_ticker(issuer.get("issuerTicker"))},
            "value": t.get("value"),
            "_txId": tx_id,
        })
    return raw


def signal_hash(politician: str, symbol: str, tx_type: str, tx_date: str, amount_range: str) -> str:
    key = f"{politician}|{symbol}|{tx_type}|{tx_date}|{amount_range}".lower()
    return hashlib.sha1(key.encode()).hexdigest()


def parse_capitol_trades(raw: list[dict]) -> list[dict]:
    """Pure: normalize raw Capitol Trades trade dicts into Signal dicts; drop rows w/o ticker."""
    out: list[dict] = []
    for r in raw:
        asset = (r.get("asset") or {})
        ticker = asset.get("assetTicker")
        if not ticker:
            continue
        pol = (r.get("politician") or {})
        name = f"{pol.get('firstName', '').strip()} {pol.get('lastName', '').strip()}".strip()
        tx_type = "sell" if str(r.get("txType", "")).lower().startswith("s") else "buy"
        tx_date = str(r.get("txDate", ""))[:10]
        disclosed = str(r.get("pubDate", ""))[:10]
        amount = str(r.get("value", "") or "")
        tx_id = r.get("_txId", "")
        out.append({
            "politician": name,
            "symbol": str(ticker).upper(),
            "tx_type": tx_type,
            "tx_date": tx_date,
            "disclosed_date": disclosed,
            "amount_range": amount,
            "source_url": f"https://www.capitoltrades.com/trades/{tx_id}" if tx_id else "https://www.capitoltrades.com/trades",
            "hash": signal_hash(name, str(ticker).upper(), tx_type, tx_date, amount),
        })
    return out


def fetch_capitol_trades(limit: int = 50, politician: str | None = None) -> list[dict]:
    """Live fetch: GET the Capitol Trades /trades page and parse the trade objects embedded
    in its server-rendered Next.js flight payload. (Their public BFF JSON API is unreliable,
    so we read the SSR'd HTML instead.) Network/parse failures raise to the caller."""
    resp = httpx.get(TRADES_URL, headers=_HEADERS, timeout=25.0, follow_redirects=True)
    resp.raise_for_status()
    raw = extract_trades_from_html(resp.text)
    signals = parse_capitol_trades(raw)
    if politician:
        pl = politician.lower()
        signals = [s for s in signals if pl in s["politician"].lower()]
    return signals[:limit]
