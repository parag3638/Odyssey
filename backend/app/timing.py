from contextvars import ContextVar
from time import perf_counter


_request_metrics: ContextVar[dict | None] = ContextVar("request_metrics", default=None)


def start_request_metrics():
    return _request_metrics.set({"db_ms": 0.0, "db_queries": 0})


def reset_request_metrics(token) -> None:
    _request_metrics.reset(token)


def record_db_query(elapsed_ms: float) -> None:
    metrics = _request_metrics.get()
    if metrics is not None:
        metrics["db_ms"] += elapsed_ms
        metrics["db_queries"] += 1


def current_request_metrics() -> dict:
    return _request_metrics.get() or {"db_ms": 0.0, "db_queries": 0}


def before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    conn.info["odyssey_query_started"] = perf_counter()


def after_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    started = conn.info.pop("odyssey_query_started", None)
    if started is not None:
        record_db_query((perf_counter() - started) * 1000)
