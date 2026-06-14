import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet


@pytest.fixture(scope="session", autouse=True)
def _env():
    os.environ["DATABASE_URL"] = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://fey:fey@localhost:5432/fey_test")
    os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
    os.environ["FEY_DISABLE_SCHEDULER"] = "1"


@pytest.fixture
def client():
    from app.db import Base, get_engine, get_db
    from app.main import app
    from app.models import BrokerageAccount  # noqa
    from app.routers import orders as orders_router
    from app.brokers.fake import FakeBroker

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    fake = FakeBroker(cash=50_000, quotes={"AAPL": 100.0, "TSLA": 250.0})

    app.dependency_overrides[get_db] = _override_db
    orders_router.get_broker_for_account = lambda account: fake
    from app.routers import bots as bots_router
    bots_router.get_broker_for_account = lambda account: fake
    c = TestClient(app)
    c._fake = fake
    yield c
    app.dependency_overrides.clear()
