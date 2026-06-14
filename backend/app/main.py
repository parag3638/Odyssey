from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, accounts, orders, positions, bots, signals, activity, stocks, watchlist


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    if os.environ.get("FEY_DISABLE_SCHEDULER") != "1":
        from app.services.scheduler import start_scheduler
        start_scheduler()
    yield


app = FastAPI(title="Fey", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Any localhost/127.0.0.1 port — Next dev may land on 3000, 3001, … so we
    # can't hardcode a single port or the whole app shows "Failed to fetch".
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(bots.router)
app.include_router(signals.router)
app.include_router(activity.router)
app.include_router(stocks.router)
app.include_router(watchlist.router)
