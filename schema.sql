CREATE DATABASE IF NOT EXISTS ipm_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ipm_db;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(160) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_profiles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL UNIQUE,
  score INT NOT NULL CHECK (score BETWEEN 0 AND 100),
  category VARCHAR(20) NOT NULL,
  horizon VARCHAR(40) NOT NULL,
  answers TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS assets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  symbol VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  sector VARCHAR(60) NOT NULL,
  asset_class VARCHAR(30) NOT NULL,
  expected_drift DOUBLE DEFAULT 0.10,
  volatility DOUBLE DEFAULT 0.20,
  base_price DOUBLE DEFAULT 100.0
);

CREATE TABLE IF NOT EXISTS price_history (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_id INT NOT NULL,
  date DATE NOT NULL,
  open DOUBLE NOT NULL,
  high DOUBLE NOT NULL,
  low DOUBLE NOT NULL,
  close DOUBLE NOT NULL,
  volume INT DEFAULT 0,
  UNIQUE KEY uq_asset_date (asset_id, date),
  KEY idx_price_date (date),
  FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS portfolios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  name VARCHAR(120) NOT NULL DEFAULT 'Main Portfolio',
  cash DOUBLE NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS holdings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  portfolio_id INT NOT NULL,
  asset_id INT NOT NULL,
  quantity DOUBLE NOT NULL DEFAULT 0,
  avg_cost DOUBLE NOT NULL DEFAULT 0,
  UNIQUE KEY uq_portfolio_asset (portfolio_id, asset_id),
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
  FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS transactions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  portfolio_id INT NOT NULL,
  asset_id INT NOT NULL,
  side VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity DOUBLE NOT NULL,
  price DOUBLE NOT NULL,
  executed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_txn_portfolio (portfolio_id, executed_at),
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
  FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS allocation_plans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  portfolio_id INT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  method VARCHAR(60) NOT NULL,
  risk_category VARCHAR(20) NOT NULL,
  expected_return DOUBLE NOT NULL,
  volatility DOUBLE NOT NULL,
  sharpe DOUBLE NOT NULL,
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);

CREATE TABLE IF NOT EXISTS plan_weights (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  asset_id INT NOT NULL,
  weight DOUBLE NOT NULL,
  target_amount DOUBLE NOT NULL,
  UNIQUE KEY uq_plan_asset (plan_id, asset_id),
  FOREIGN KEY (plan_id) REFERENCES allocation_plans(id),
  FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS rebalance_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  portfolio_id INT NOT NULL,
  asset_id INT NOT NULL,
  current_weight DOUBLE NOT NULL,
  target_weight DOUBLE NOT NULL,
  drift DOUBLE NOT NULL,
  side VARCHAR(4) NOT NULL,
  quantity DOUBLE NOT NULL,
  executed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
  FOREIGN KEY (asset_id) REFERENCES assets(id)
);

DELIMITER $$

CREATE TRIGGER trg_holding_buy AFTER INSERT ON transactions FOR EACH ROW
BEGIN
  UPDATE holdings
  SET quantity = quantity + NEW.quantity,
      avg_cost = (quantity * avg_cost + NEW.quantity * NEW.price) / (quantity + NEW.quantity)
  WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id;
  INSERT INTO holdings (portfolio_id, asset_id, quantity, avg_cost)
  SELECT NEW.portfolio_id, NEW.asset_id, NEW.quantity, NEW.price
  WHERE NOT EXISTS (SELECT 1 FROM holdings
                    WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id);
END$$

CREATE TRIGGER trg_holding_sell AFTER INSERT ON transactions FOR EACH ROW
BEGIN
  UPDATE holdings
  SET quantity = quantity + NEW.quantity
  WHERE portfolio_id = NEW.portfolio_id AND asset_id = NEW.asset_id;
END$$

DELIMITER ;

CREATE OR REPLACE VIEW v_portfolio_value AS
SELECT p.id AS portfolio_id, a.symbol, h.quantity, h.avg_cost,
       h.quantity * latest.close AS market_value
FROM portfolios p
JOIN holdings h ON h.portfolio_id = p.id
JOIN assets a ON a.id = h.asset_id
JOIN price_history latest ON latest.asset_id = a.id
JOIN (SELECT asset_id, MAX(date) AS max_date FROM price_history GROUP BY asset_id) m
  ON m.asset_id = latest.asset_id AND m.max_date = latest.date;
