"""AI advisory layer — read-only endpoints that turn already-cached structured
data into grounded, cited, plain-English prose. Every endpoint degrades to
``available:false`` (HTTP 200) when the LLM is unconfigured, so the UI hides the
AI affordance the same way the Analyst/Earnings tabs do without a Finnhub key.
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_sessionmaker
from app.models import ActivityLog, BrokerageAccount, Signal, Ticker
from app.schemas import (
    AiResponse,
    BullBearOut,
    ChatRequest,
    Citation,
    ConcentrationOut,
    PortfolioHealthOut,
    RiskExplainIn,
    ScreenerFilter,
    ScreenerParseIn,
    ScreenerParseOut,
)
from app.services import ai as ai_service
from app.services.finnhub import cached, cached_value, set_cached_many
from app.services.finnhub import get_finnhub as _get_finnhub
from app.services.llm import get_llm as _get_llm
from app.services.market_data import market_data_for

router = APIRouter(prefix="/ai")
_context_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="congress-context")
_context_lock = Lock()
_context_pending: set[str] = set()


# Overridable seams for tests (mirror stocks_router.get_market_data / get_finnhub).
def get_llm():
    return _get_llm()


def get_market_data(db: Session):
    return market_data_for(db)


def get_finnhub():
    return _get_finnhub()


def get_broker(db: Session):
    """Read-only broker for the first account (mirrors market_data_for). Built here
    from safe modules — NOT imported from the order path — so the wall holds. Returns
    None when no account is connected. Overridable in tests."""
    acct = db.query(BrokerageAccount).first()
    if acct is None:
        return None
    from app.brokers.alpaca import AlpacaBroker
    from app.crypto import decrypt_secret

    secret = decrypt_secret(acct.alpaca_secret, get_settings().fernet_key)
    return AlpacaBroker(key_id=acct.alpaca_key_id, secret=secret, paper=True)


def _unavailable() -> AiResponse:
    return AiResponse(available=False, text=None)


def _generate_congress_context(symbol: str) -> None:
    db = get_sessionmaker()()
    try:
        if cached_value(db, f"ai_congress:{symbol}", 12 * 3600) is not None:
            return
        rows = (
            db.query(Signal)
            .filter(func.upper(Signal.symbol) == symbol)
            .order_by(Signal.id.desc())
            .limit(30)
            .all()
        )
        if not rows:
            return
        signals = [
            {
                "politician": row.politician,
                "tx_type": row.tx_type,
                "amount_range": row.amount_range,
                "tx_date": row.tx_date,
                "source_url": row.source_url,
            }
            for row in rows
        ]
        db.rollback()
        bundle = ai_service.build_congress_bundle(symbol, signals)
        llm = get_llm()
        if not llm.enabled:
            return
        result = llm.complete_json(
            system=ai_service.CONGRESS_SYSTEM,
            user=ai_service.render_congress_prompt(symbol, bundle),
            schema=ai_service.SUMMARY_SCHEMA,
        )
        if not result or not result.get("text"):
            return
        citations = ai_service.validate_citations(result.get("citation_ids") or [], bundle)
        value = AiResponse(
            available=True,
            text=result["text"],
            citations=[Citation(**citation) for citation in citations],
            model=llm.model,
        ).model_dump()
        set_cached_many(db, {f"ai_congress:{symbol}": value})
    finally:
        db.close()


def _schedule_congress_context(symbol: str) -> None:
    with _context_lock:
        if symbol in _context_pending:
            return
        _context_pending.add(symbol)

    def _run() -> None:
        try:
            _generate_congress_context(symbol)
        finally:
            with _context_lock:
                _context_pending.discard(symbol)

    _context_executor.submit(_run)


@router.get("/stocks/{symbol}/summary", response_model=AiResponse)
def stock_summary(symbol: str, db: Session = Depends(get_db)) -> AiResponse:
    """Plain-English summary of a stock's recent news + analyst views, grounded on
    the same cached data the research page already shows, with citation links."""
    llm = get_llm()
    if not llm.enabled:
        return _unavailable()

    sym = symbol.upper()
    md = get_market_data(db)
    fh = get_finnhub()
    news = cached(db, f"news:{sym}", 1800, lambda: md.news([sym], 15)) if md else []
    reco = cached(db, f"reco:{sym}", 12 * 3600, lambda: fh.recommendation(sym))
    metrics = cached(db, f"metrics:{sym}", 6 * 3600, lambda: fh.metrics(sym))

    bundle = ai_service.build_summary_bundle(
        sym,
        news=news if isinstance(news, list) else [],
        reco=reco if isinstance(reco, list) else [],
        metrics=metrics if isinstance(metrics, dict) else None,
    )
    if not bundle["facts"]:
        return _unavailable()

    def _generate():
        result = llm.complete_json(
            system=ai_service.SYSTEM_PROMPT,
            user=ai_service.render_user_prompt(sym, bundle),
            schema=ai_service.SUMMARY_SCHEMA,
        )
        if not result or not result.get("text"):
            return None  # don't cache transient failures
        citations = ai_service.validate_citations(result.get("citation_ids") or [], bundle)
        return AiResponse(
            available=True,
            text=result["text"],
            citations=[Citation(**c) for c in citations],
            model=llm.model,
        ).model_dump()

    data = cached(db, f"ai_sum:{sym}", 1800, _generate)
    return AiResponse(**data) if isinstance(data, dict) else _unavailable()


@router.get("/stocks/{symbol}/bull-bear", response_model=BullBearOut)
def bull_bear(symbol: str, db: Session = Depends(get_db)) -> BullBearOut:
    """Cross-source bull-vs-bear synthesis: reconciles news, ratings, and fundamentals
    and names the crux of the disagreement."""
    llm = get_llm()
    if not llm.enabled:
        return BullBearOut(available=False)
    sym = symbol.upper()
    md = get_market_data(db)
    fh = get_finnhub()
    news = cached(db, f"news:{sym}", 1800, lambda: md.news([sym], 15)) if md else []
    reco = cached(db, f"reco:{sym}", 12 * 3600, lambda: fh.recommendation(sym))
    metrics = cached(db, f"metrics:{sym}", 6 * 3600, lambda: fh.metrics(sym))
    bundle = ai_service.build_summary_bundle(
        sym,
        news=news if isinstance(news, list) else [],
        reco=reco if isinstance(reco, list) else [],
        metrics=metrics if isinstance(metrics, dict) else None,
    )
    if not bundle["facts"]:
        return BullBearOut(available=False)

    def _generate():
        result = llm.complete_json(
            system=ai_service.BULL_BEAR_SYSTEM,
            user=ai_service.render_bull_bear_prompt(sym, bundle),
            schema=ai_service.BULL_BEAR_SCHEMA,
        )
        if not result:
            return None
        cites = ai_service.validate_citations(result.get("citation_ids") or [], bundle)
        return BullBearOut(
            available=True,
            bull=result.get("bull") or [],
            bear=result.get("bear") or [],
            crux=result.get("crux"),
            citations=[Citation(**c) for c in cites],
            model=llm.model,
        ).model_dump()

    data = cached(db, f"ai_bullbear:{sym}", 6 * 3600, _generate)
    return BullBearOut(**data) if isinstance(data, dict) else BullBearOut(available=False)


@router.get("/signals/{symbol}/context", response_model=AiResponse)
def congress_context(symbol: str, db: Session = Depends(get_db)) -> AiResponse:
    """Descriptive context over a stock's congressional trades: deterministic cluster
    detection + plain-English prose, each claim citing a disclosure."""
    llm = get_llm()
    if not llm.enabled:
        return _unavailable()
    sym = symbol.upper()
    data = cached_value(db, f"ai_congress:{sym}", 12 * 3600)
    if isinstance(data, dict):
        return AiResponse(**data)
    has_signal = (
        db.query(Signal.id)
        .filter(func.upper(Signal.symbol) == sym)
        .first()
        is not None
    )
    if not has_signal:
        return _unavailable()
    _schedule_congress_context(sym)
    return _unavailable()


@router.get("/portfolio/health", response_model=PortfolioHealthOut)
def portfolio_health(db: Session = Depends(get_db)) -> PortfolioHealthOut:
    """Concentration/overlap observer: deterministic position + sector weights, with
    the LLM narrating what they mean. Advisory framing only."""
    llm = get_llm()
    if not llm.enabled:
        return PortfolioHealthOut(available=False)
    snap = _portfolio_snapshot(db)
    if not snap.get("connected"):
        return PortfolioHealthOut(available=False)
    quotes = snap.get("quotes") or {}
    holdings = [
        {"symbol": h["symbol"], "qty": h["qty"],
         "price": (quotes.get(h["symbol"]) or {}).get("price") or h.get("avg") or 0}
        for h in (snap.get("holdings") or [])
    ]
    if not holdings:
        return PortfolioHealthOut(available=False)
    syms = [h["symbol"] for h in holdings]
    sectors = {
        t.symbol: (t.sector or "Unknown")
        for t in db.query(Ticker).filter(Ticker.symbol.in_(syms)).all()
    }
    conc = ai_service.portfolio_concentrations(holdings, sectors)
    if conc["total"] <= 0:
        return PortfolioHealthOut(available=False)

    ordered = sorted(holdings, key=lambda x: x["symbol"])
    sig = ",".join(f"{h['symbol']}:{round(float(h['qty']), 2)}" for h in ordered)
    key = f"ai_health:{hashlib.sha1(sig.encode()).hexdigest()[:16]}"

    def _generate():
        result = llm.complete_json(
            system=ai_service.HEALTH_SYSTEM,
            user=ai_service.render_health_prompt(conc),
            schema=ai_service.PROSE_SCHEMA,
        )
        if not result or not result.get("text"):
            return None
        top = [ConcentrationOut(**c) for c in (conc["positions"][:3] + conc["sectors"][:2])]
        return PortfolioHealthOut(
            available=True, text=result["text"], concentrations=top, model=llm.model,
        ).model_dump()

    data = cached(db, key, 6 * 3600, _generate)
    if isinstance(data, dict):
        return PortfolioHealthOut(**data)
    return PortfolioHealthOut(available=False)


@router.post("/risk/explain", response_model=AiResponse)
def risk_explain(body: RiskExplainIn, db: Session = Depends(get_db)) -> AiResponse:
    """Explain an ALREADY-PRODUCED risk rejection in plain English.

    Read-only: this endpoint never participates in the decision — the deterministic
    engine (app/risk.py) has already rejected the order before this is ever called.
    """
    llm = get_llm()
    if not llm.enabled or not body.reason.strip():
        return _unavailable()
    reason = body.reason.strip()[:400]
    key = f"ai_risk:{hashlib.sha1(reason.encode()).hexdigest()[:16]}"

    def _generate():
        result = llm.complete_json(
            system=ai_service.RISK_SYSTEM,
            user=ai_service.render_risk_prompt(reason, body.symbol, body.qty, body.side),
            schema=ai_service.PROSE_SCHEMA,
        )
        if not result or not result.get("text"):
            return None
        return AiResponse(available=True, text=result["text"], model=llm.model).model_dump()

    data = cached(db, key, 30 * 24 * 3600, _generate)
    return AiResponse(**data) if isinstance(data, dict) else _unavailable()


@router.post("/screener/parse", response_model=ScreenerParseOut)
def screener_parse(body: ScreenerParseIn, db: Session = Depends(get_db)) -> ScreenerParseOut:
    """Translate a plain-English screen into structured filters. Parsing only — the
    deterministic client-side engine runs the actual query."""
    llm = get_llm()
    q = (body.query or "").strip()
    if not llm.enabled or not q:
        return ScreenerParseOut(available=False)
    key = f"ai_screen:{hashlib.sha1(q.lower().encode()).hexdigest()[:16]}"

    def _generate():
        result = llm.complete_json(
            system=ai_service.SCREENER_SYSTEM,
            user=f"Screen request: {q}",
            schema=ai_service.SCREENER_SCHEMA,
        )
        if not result:
            return None
        # Defence in depth: drop anything outside the allowed field list.
        filters = [
            ScreenerFilter(**f)
            for f in (result.get("filters") or [])
            if f.get("field") in ai_service.SCREENER_FIELDS
        ]
        sort_field = result.get("sort_field") or None
        if sort_field not in ai_service.SCREENER_FIELDS:
            sort_field = None
        return ScreenerParseOut(
            available=True,
            filters=filters,
            sort_field=sort_field,
            sort_dir=result.get("sort_dir") or "desc",
            note=result.get("note") or None,
            model=llm.model,
        ).model_dump()

    data = cached(db, key, 24 * 3600, _generate)
    return ScreenerParseOut(**data) if isinstance(data, dict) else ScreenerParseOut(available=False)


# ── Global assistant (chat) ──────────────────────────────────────────────────

def _portfolio_snapshot(db: Session) -> dict:
    """A JSON snapshot of the account's holdings/quotes/cash/activity, cached 60s so
    chat turns don't hammer the broker. {"connected": False} when no account."""
    def build():
        broker = get_broker(db)
        if broker is None:
            return {"connected": False}
        try:
            positions = broker.get_positions()
        except Exception:
            positions = []
        holdings = [{"symbol": p.symbol, "qty": p.qty, "avg": p.avg_entry_price} for p in positions]
        quotes: dict = {}
        for h in holdings:
            try:
                q = broker.get_quote(h["symbol"])
                quotes[h["symbol"]] = {"price": q.price, "prev": q.prev_close}
            except Exception:
                continue
        try:
            cash = broker.get_cash()
        except Exception:
            cash = None
        acts = db.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(8).all()
        activity = [{"event": a.event, "detail": a.detail or {}, "level": a.level} for a in acts]
        return {"connected": True, "holdings": holdings, "quotes": quotes,
                "cash": cash, "activity": activity}

    return cached(db, "ai_pf_snapshot", 60, build) or {"connected": False}


def _chat_bundle(db: Session, symbol: str | None) -> dict:
    """Assemble the grounding bundle for a chat turn: per-symbol facts (if a stock is
    in context) plus the portfolio snapshot. All DB/broker reads happen here (sync)."""
    symbol_data = None
    if symbol:
        md = get_market_data(db)
        fh = get_finnhub()
        news = cached(db, f"news:{symbol}", 1800, lambda: md.news([symbol], 15)) if md else []
        reco = cached(db, f"reco:{symbol}", 12 * 3600, lambda: fh.recommendation(symbol))
        metrics = cached(db, f"metrics:{symbol}", 6 * 3600, lambda: fh.metrics(symbol))
        sigs = (
            db.query(Signal)
            .filter(func.upper(Signal.symbol) == symbol)
            .order_by(Signal.id.desc())
            .limit(10)
            .all()
        )
        signals = [
            {"politician": s.politician, "tx_type": s.tx_type, "amount_range": s.amount_range,
             "tx_date": s.tx_date, "source_url": s.source_url}
            for s in sigs
        ]
        symbol_data = {
            "news": news if isinstance(news, list) else [],
            "reco": reco if isinstance(reco, list) else [],
            "metrics": metrics if isinstance(metrics, dict) else None,
            "signals": signals,
        }
    pf = _portfolio_snapshot(db)
    return ai_service.build_chat_bundle(symbol, symbol_data, pf)


def _sse(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _context_symbol(body: ChatRequest) -> str | None:
    if body.context and body.context.symbol:
        return body.context.symbol.upper()
    return None


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, db: Session = Depends(get_db)):
    """Streaming (SSE) grounded chat. Frames: {"type":"token","v":...} per token, then
    {"type":"citations","v":[...]}, then the literal `[DONE]`."""
    llm = get_llm()
    symbol = _context_symbol(body)
    bundle = _chat_bundle(db, symbol)  # sync DB/broker reads before we start streaming
    system = ai_service.render_chat_system(bundle)
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    def gen():
        if not llm.enabled:
            yield _sse({"type": "error", "v": "unavailable"})
            yield "data: [DONE]\n\n"
            return
        acc: list[str] = []
        for tok in llm.stream_messages(messages=messages, system=system):
            acc.append(tok)
            yield _sse({"type": "token", "v": tok})
        cites = ai_service.citations_from_text("".join(acc), bundle)
        yield _sse({"type": "citations", "v": cites})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/chat", response_model=AiResponse)
def chat(body: ChatRequest, db: Session = Depends(get_db)) -> AiResponse:
    """Non-streaming grounded chat — the demo-mode / fallback path for /chat/stream."""
    llm = get_llm()
    if not llm.enabled:
        return _unavailable()
    symbol = _context_symbol(body)
    bundle = _chat_bundle(db, symbol)
    system = ai_service.render_chat_system(bundle)
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    text = llm.chat(messages=messages, system=system)
    if not text:
        return _unavailable()
    cites = ai_service.citations_from_text(text, bundle)
    return AiResponse(
        available=True,
        text=text,
        citations=[Citation(**c) for c in cites],
        model=llm.model,
    )
