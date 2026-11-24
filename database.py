import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "instance", "portfolio.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///" + DB_PATH)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("mysql://"):
    DATABASE_URL = "mysql+pymysql://" + DATABASE_URL[len("mysql://"):]

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()
IS_SQLITE = DATABASE_URL.startswith("sqlite")


def install_triggers():
    if not IS_SQLITE:
        return
    buy = """
    CREATE TRIGGER trg_holding_buy AFTER INSERT ON transactions
    WHEN NEW.quantity > 0
    BEGIN
      UPDATE holdings
      SET quantity = quantity + NEW.quantity,
          avg_cost = (quantity * avg_cost + NEW.quantity * NEW.price) / (quantity + NEW.quantity)
      WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id;
      INSERT INTO holdings (portfolio_id, asset_id, quantity, avg_cost)
      SELECT NEW.portfolio_id, NEW.asset_id, NEW.quantity, NEW.price
      WHERE NOT EXISTS (SELECT 1 FROM holdings
                        WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id);
    END
    """
    sell = """
    CREATE TRIGGER trg_holding_sell AFTER INSERT ON transactions
    WHEN NEW.quantity < 0
    BEGIN
      UPDATE holdings
      SET quantity = quantity + NEW.quantity
      WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id;
    END
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER IF EXISTS trg_holding_buy"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_holding_sell"))
        conn.execute(text(buy))
        conn.execute(text(sell))
