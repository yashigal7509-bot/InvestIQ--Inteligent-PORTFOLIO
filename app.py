import os
from datetime import date
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import desc, func
from werkzeug.security import check_password_hash, generate_password_hash

import ai_engine as ai
from data import extend_market_data, latest_prices, price_series, seed_database
from database import Base, SessionLocal, engine, install_triggers
from models import (
    AllocationPlan, Asset, Holding, PlanWeight, Portfolio, PriceHistory,
    RebalanceLog, RiskProfile, Transaction, User,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "classroom-project-change-this-key")

Base.metadata.create_all(engine)
install_triggers()
bootstrap = SessionLocal()
seed_database(bootstrap)
bootstrap.close()


@app.before_request
def open_session():
    g.db = SessionLocal()


@app.teardown_appcontext
def close_session(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or current_user() is None:
            session.clear()
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def profile_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user().profile:
            flash("Complete the risk questionnaire first.", "warning")
            return redirect(url_for("questionnaire"))
        return view(*args, **kwargs)
    return login_required(wrapped)


def current_user():
    uid = session.get("user_id")
    return g.db.get(User, uid) if uid is not None else None


def get_portfolio(user):
    return g.db.query(Portfolio).filter_by(user_id=user.id).first()


def latest_plan(portfolio):
    return g.db.query(AllocationPlan).filter_by(portfolio_id=portfolio.id)\
        .order_by(desc(AllocationPlan.created_at), desc(AllocationPlan.id)).first()


def plan_rows(plan):
    if not plan:
        return []
    rows = g.db.query(PlanWeight, Asset).join(Asset, Asset.id == PlanWeight.asset_id)\
        .filter(PlanWeight.plan_id == plan.id).all()
    return [{"symbol": a.symbol, "name": a.name, "sector": a.sector,
             "weight": w.weight * 100, "target_amount": w.target_amount,
             "color": color_for(i)} for i, (w, a) in enumerate(rows)]


def color_for(index):
    palette = ["#1b9aaa", "#e8b84b", "#0f2a43", "#d9603b", "#4f8fd8", "#7fb069",
               "#9b6b9e", "#c9a227", "#3d5a80", "#e07a5f", "#548687", "#8d6262"]
    return palette[index % len(palette)]


def snapshot(portfolio):
    prices = latest_prices(g.db)
    holdings = []
    invested = 0.0
    for h in portfolio.holdings:
        if h.quantity <= 0:
            continue
        close = prices.get(h.asset_id, h.avg_cost)
        value = h.quantity * close
        pnl = value - h.quantity * h.avg_cost
        invested += value
        holdings.append({
            "symbol": h.asset.symbol, "name": h.asset.name, "sector": h.asset.sector,
            "quantity": round(h.quantity, 4), "avg_cost": h.avg_cost, "close": close,
            "value": value, "pnl": pnl, "pnl_pct": (pnl / (h.quantity * h.avg_cost) * 100) if h.avg_cost else 0.0,
        })
    total = invested + portfolio.cash
    for item in holdings:
        item["weight"] = item["value"] / total * 100 if total else 0.0
    return holdings, invested, total


def portfolio_series(portfolio, holdings, days=90):
    assets = [a for a in portfolio.holdings if a.quantity > 0]
    if not assets:
        return [], []
    ids = [h.asset_id for h in assets]
    cutoff = date.today().fromordinal(date.today().toordinal() - days * 2)
    rows = g.db.query(PriceHistory.date, PriceHistory.asset_id, PriceHistory.close)\
        .filter(PriceHistory.asset_id.in_(ids), PriceHistory.date >= cutoff)\
        .order_by(PriceHistory.date).all()
    by_date = {}
    for d, aid, close in rows:
        by_date.setdefault(d, {})[aid] = close
    quantities = {h.asset_id: h.quantity for h in assets}
    labels, values = [], []
    for d in sorted(by_date)[-days:]:
        value = portfolio.cash + sum(quantities[aid] * close for aid, close in by_date[d].items())
        labels.append(d.strftime("%d %b"))
        values.append(round(value, 2))
    return labels, values


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or "@" not in email or len(password) < 6:
            flash("Enter a valid name, e-mail and a password of at least 6 characters.", "danger")
            return render_template("register.html")
        if g.db.query(User.id).filter_by(email=email).first():
            flash("An account with this e-mail already exists.", "danger")
            return render_template("register.html")
        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        g.db.add(user)
        g.db.flush()
        g.db.add(Portfolio(user_id=user.id, name="Main Portfolio", cash=1000000.0))
        g.db.commit()
        flash("Account created. Log in and complete your risk profile.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = g.db.query(User).filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.name.split()[0]}!", "success")
            return redirect(url_for("questionnaire") if not user.profile else url_for("dashboard"))
        flash("Invalid e-mail or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/questionnaire", methods=["GET", "POST"])
@login_required
def questionnaire():
    user = current_user()
    if request.method == "POST":
        score = ai.score_answers(request.form)
        category = ai.categorize(score)
        horizon = "1 to 3 years" if score < 40 else ("3 to 7 years" if score <= 70 else "7+ years")
        profile = user.profile or RiskProfile(user_id=user.id)
        profile.score = score
        profile.category = category
        profile.horizon = horizon
        profile.answers = ",".join(request.form.get(f"q_{i}", "0") for i in range(len(ai.QUESTIONS)))
        g.db.add(profile)
        g.db.commit()
        flash(f"Risk profile saved: {category} (score {score}/100).", "success")
        return redirect(url_for("dashboard"))
    return render_template("questionnaire.html", questions=ai.QUESTIONS, profile=user.profile)


@app.route("/dashboard")
@profile_required
def dashboard():
    user = current_user()
    portfolio = get_portfolio(user)
    holdings, invested, total = snapshot(portfolio)
    labels, values = portfolio_series(portfolio, holdings)
    plan = latest_plan(portfolio)
    weights = plan_rows(plan)
    recent = g.db.query(Transaction).filter_by(portfolio_id=portfolio.id)\
        .order_by(desc(Transaction.executed_at), desc(Transaction.id)).limit(6).all()
    tx_rows = [{"side": t.side, "symbol": t.asset.symbol, "quantity": t.quantity,
                "price": t.price, "date": t.executed_at.strftime("%d %b %Y")} for t in recent]
    allocation = [{"label": row["symbol"], "value": round(row["weight"], 1), "color": row["color"]}
                  for row in weights]
    cash_weight = portfolio.cash / total * 100 if total else 100
    allocation.append({"label": "CASH", "value": round(cash_weight, 1), "color": "#8fa3b5"})
    return render_template("dashboard.html", user=user, profile=user.profile, portfolio=portfolio,
                           holdings=holdings, invested=invested, total=total, labels=labels,
                           values=values, plan=plan, weights=weights, allocation=allocation,
                           recent=tx_rows, assets_count=g.db.query(func.count(Asset.id)).scalar() - 1)


@app.route("/allocate", methods=["GET", "POST"])
@profile_required
def allocate():
    user = current_user()
    portfolio = get_portfolio(user)
    if request.method == "POST":
        _, invested, total = snapshot(portfolio)
        ai.create_plan(g.db, portfolio, user.profile.category, total)
        flash("New AI allocation plan generated and saved.", "success")
        return redirect(url_for("allocate"))
    plan = latest_plan(portfolio)
    frontier = ai.compute_frontier(g.db, user.profile.category) if plan else []
    return render_template("allocation.html", user=user, plan=plan, weights=plan_rows(plan),
                           frontier=frontier, category=user.profile.category,
                           band=ai.BANDS[user.profile.category], method=ai.HAS_SKLEARN)


@app.route("/portfolio")
@profile_required
def portfolio_view():
    user = current_user()
    portfolio = get_portfolio(user)
    holdings, invested, total = snapshot(portfolio)
    return render_template("portfolio.html", user=user, holdings=holdings, invested=invested,
                           total=total, cash=portfolio.cash, portfolio=portfolio)


@app.route("/trade", methods=["GET", "POST"])
@profile_required
def trade():
    user = current_user()
    portfolio = get_portfolio(user)
    if request.method == "POST":
        try:
            asset = g.db.get(Asset, int(request.form.get("asset_id", 0)))
            side = request.form.get("side", "BUY").upper()
            quantity = float(request.form.get("quantity", 0))
        except (TypeError, ValueError):
            asset, quantity = None, 0
        prices = latest_prices(g.db)
        price = prices.get(asset.id) if asset else None
        if not asset or side not in ("BUY", "SELL") or quantity <= 0 or price is None:
            flash("Choose an asset and enter a positive quantity.", "danger")
        elif side == "BUY" and quantity * price > portfolio.cash:
            flash("Insufficient cash for this purchase.", "danger")
        elif side == "SELL":
            held = g.db.query(Holding).filter_by(portfolio_id=portfolio.id, asset_id=asset.id).first()
            if not held or held.quantity < quantity:
                flash("Not enough units to sell.", "danger")
                return redirect(url_for("trade"))
            portfolio.cash += quantity * price
            g.db.add(Transaction(portfolio_id=portfolio.id, asset_id=asset.id, side="SELL",
                                 quantity=-quantity, price=price))
            g.db.commit()
            flash(f"Sold {quantity:g} {asset.symbol} at Rs {price:,.2f}.", "success")
            return redirect(url_for("portfolio_view"))
        else:
            portfolio.cash -= quantity * price
            g.db.add(Transaction(portfolio_id=portfolio.id, asset_id=asset.id, side="BUY",
                                 quantity=quantity, price=price))
            g.db.commit()
            flash(f"Bought {quantity:g} {asset.symbol} at Rs {price:,.2f}.", "success")
            return redirect(url_for("portfolio_view"))
    assets = g.db.query(Asset).order_by(Asset.symbol).all()
    prices = latest_prices(g.db)
    held = {h.asset_id: h.quantity for h in portfolio.holdings}
    options = [{"id": a.id, "symbol": a.symbol, "name": a.name, "price": prices.get(a.id, 0),
                "held": held.get(a.id, 0)} for a in assets]
    return render_template("trade.html", user=user, assets=options, cash=portfolio.cash)


@app.route("/rebalance", methods=["GET", "POST"])
@profile_required
def rebalance():
    user = current_user()
    portfolio = get_portfolio(user)
    plan = latest_plan(portfolio)
    if not plan:
        flash("Generate an allocation plan first.", "warning")
        return redirect(url_for("allocate"))
    holdings, invested, total = snapshot(portfolio)
    current = {h["symbol"]: h for h in holdings}
    prices = latest_prices(g.db)
    rows = []
    for w in plan.weights:
        asset = w.asset
        current_value = current.get(asset.symbol, {}).get("value", 0.0)
        current_weight = current_value / total * 100 if total else 0.0
        target_weight = w.weight * 100
        drift = current_weight - target_weight
        price = prices.get(asset.id, 0)
        quantity = abs((w.target_amount - current_value) / price) if price else 0
        rows.append({"symbol": asset.symbol, "name": asset.name, "current_weight": current_weight,
                     "target_weight": target_weight, "drift": drift, "price": price,
                     "side": "SELL" if drift > 0 else "BUY", "quantity": quantity})
    if request.method == "POST":
        required_cash = sum(r["quantity"] * r["price"] for r in rows
                            if r["side"] == "BUY" and abs(r["drift"]) > 5)
        if required_cash > portfolio.cash:
            flash("Not enough cash to rebalance fully. Sell overweight assets first.", "danger")
        else:
            executed = 0
            for row in rows:
                if abs(row["drift"]) <= 5 or row["quantity"] <= 0:
                    continue
                asset = g.db.query(Asset).filter_by(symbol=row["symbol"]).first()
                signed = -row["quantity"] if row["side"] == "SELL" else row["quantity"]
                g.db.add(Transaction(portfolio_id=portfolio.id, asset_id=asset.id,
                                     side=row["side"], quantity=signed, price=row["price"]))
                g.db.add(RebalanceLog(portfolio_id=portfolio.id, asset_id=asset.id,
                                      current_weight=row["current_weight"] / 100,
                                      target_weight=row["target_weight"] / 100,
                                      drift=row["drift"] / 100, side=row["side"],
                                      quantity=row["quantity"]))
                portfolio.cash += -signed * row["price"]
                executed += 1
            g.db.commit()
            flash(f"Rebalance executed: {executed} trades in one ACID transaction.", "success")
            return redirect(url_for("portfolio_view"))
    return render_template("rebalance.html", user=user, rows=rows, plan=plan,
                           total=total, cash=portfolio.cash)


@app.route("/assets", methods=["GET", "POST"])
@profile_required
def assets_view():
    if request.method == "POST":
        day, added = extend_market_data(g.db)
        flash(f"Market data synced to {day}: {added} new price records.", "success")
        return redirect(url_for("assets_view"))
    assets = g.db.query(Asset).order_by(Asset.symbol).all()
    prices = latest_prices(g.db)
    previous = g.db.query(func.max(PriceHistory.date)).scalar()
    rows = []
    for asset in assets:
        history = price_series(g.db, asset.id, 2)
        close = prices.get(asset.id, 0)
        old = history[-2][1] if len(history) > 1 else close
        rows.append({"symbol": asset.symbol, "name": asset.name, "sector": asset.sector,
                     "asset_class": asset.asset_class, "close": close,
                     "change": (close / old - 1) * 100 if old else 0,
                     "points": [p[1] for p in price_series(g.db, asset.id, 30)]})
    return render_template("assets.html", user=current_user(), rows=rows, last_date=previous,
                           count=g.db.query(func.count(PriceHistory.id)).scalar())


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
