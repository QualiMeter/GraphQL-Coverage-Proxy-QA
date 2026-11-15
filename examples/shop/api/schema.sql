CREATE TABLE IF NOT EXISTS customers (
    id text PRIMARY KEY, name text NOT NULL, email text NOT NULL UNIQUE,
    password_hash text NOT NULL, role text NOT NULL CHECK (role IN ('CUSTOMER','ADMIN'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash text PRIMARY KEY, customer_id text NOT NULL REFERENCES customers(id),
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id text PRIMARY KEY, name text NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    stock integer NOT NULL CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    id text PRIMARY KEY, customer_id text NOT NULL REFERENCES customers(id),
    status text NOT NULL CHECK (status IN ('CREATED','CANCELLED')),
    total_cents integer NOT NULL CHECK (total_cents >= 0),
    idempotency_key text NOT NULL, request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(customer_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id text NOT NULL REFERENCES orders(id), product_id text NOT NULL REFERENCES products(id),
    name text NOT NULL, quantity integer NOT NULL CHECK (quantity > 0),
    unit_price_cents integer NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY(order_id, product_id)
);
