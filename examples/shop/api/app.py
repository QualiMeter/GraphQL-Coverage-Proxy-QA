import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from graphql import GraphQLError, build_schema, graphql


ROOT = Path(__file__).resolve().parent
SDL = (ROOT / "schema.graphql").read_text(encoding="utf-8")


def fail(code, message):
    raise GraphQLError(message, extensions={"code": code})


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return salt + ":" + digest


def user_dict(row):
    return {k: row[k] for k in ("id", "name", "email", "role")}


def product_dict(row):
    return {"id": row["id"], "name": row["name"], "priceCents": row["price_cents"], "stock": row["stock"]}


def principal(info, *, admin=False):
    user = info.context["user"]
    if user is None:
        fail("UNAUTHENTICATED", "Login required")
    if admin and user["role"] != "ADMIN":
        fail("FORBIDDEN", "Administrator role required")
    return user


async def read_order(pool, order_id, user):
    row = await pool.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
    if row is None or (row["customer_id"] != user["id"] and user["role"] != "ADMIN"):
        fail("NOT_FOUND", "Order not found")
    return {"id": row["id"], "customerId": row["customer_id"], "status": row["status"],
            "totalCents": row["total_cents"], "createdAt": row["created_at"].isoformat()}


async def login(_, info, email, password):
    pool = info.context["pool"]
    row = await pool.fetchrow("SELECT * FROM customers WHERE email=$1", email.strip().lower())
    stored = row["password_hash"] if row else password_hash("unused-password", "unknown-user")
    valid = hmac.compare_digest(password_hash(password, stored.split(":", 1)[0]), stored)
    if not row or not valid:
        fail("INVALID_CREDENTIALS", "Invalid email or password")
    token = secrets.token_urlsafe(32)
    await pool.execute("INSERT INTO sessions(token_hash,customer_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",
                       hashlib.sha256(token.encode()).hexdigest(), row["id"])
    return {"token": token, "user": user_dict(row)}


async def me(_, info):
    return principal(info)


async def products(_, info, limit=10, offset=0):
    if not 1 <= limit <= 100 or offset < 0:
        fail("BAD_USER_INPUT", "limit must be 1..100 and offset must be nonnegative")
    rows = await info.context["pool"].fetch("SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2", limit, offset)
    return [product_dict(row) for row in rows]


async def product(_, info, id):
    row = await info.context["pool"].fetchrow("SELECT * FROM products WHERE id=$1", id)
    return product_dict(row) if row else None


async def orders(_, info, status=None):
    user = principal(info)
    rows = await info.context["pool"].fetch(
        "SELECT id FROM orders WHERE customer_id=$1 AND ($2::text IS NULL OR status=$2) ORDER BY created_at,id", user["id"], status)
    return [await read_order(info.context["pool"], row["id"], user) for row in rows]


async def order(_, info, id):
    return await read_order(info.context["pool"], id, principal(info))


async def update_profile(_, info, name):
    user = principal(info)
    if not 1 <= len(name.strip()) <= 100:
        fail("BAD_USER_INPUT", "Name must contain 1..100 characters")
    row = await info.context["pool"].fetchrow("UPDATE customers SET name=$1 WHERE id=$2 RETURNING *", name.strip(), user["id"])
    return user_dict(row)


async def create_order(_, info, input):
    user = principal(info)
    lines, key = input["items"], input["idempotencyKey"]
    if not lines or len(lines) > 20 or not 6 <= len(key) <= 100:
        fail("BAD_USER_INPUT", "Order needs 1..20 items and a 6..100 character idempotency key")
    ids = [line["productId"] for line in lines]
    if len(set(ids)) != len(ids) or any(not 1 <= line["quantity"] <= 1000 for line in lines):
        fail("BAD_USER_INPUT", "Unique product IDs and quantity 1..1000 required")
    digest = hashlib.sha256(json.dumps(sorted(lines, key=lambda x: x["productId"]), sort_keys=True).encode()).hexdigest()
    pool = info.context["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchrow("SELECT id FROM customers WHERE id=$1 FOR UPDATE", user["id"])
            existing = await conn.fetchrow("SELECT * FROM orders WHERE customer_id=$1 AND idempotency_key=$2", user["id"], key)
            if existing:
                if existing["request_hash"] != digest:
                    fail("IDEMPOTENCY_CONFLICT", "Key already used for different order input")
                order_id = existing["id"]
            else:
                rows = await conn.fetch("SELECT * FROM products WHERE id=ANY($1::text[]) ORDER BY id FOR UPDATE", ids)
                by_id = {row["id"]: row for row in rows}
                if len(rows) != len(ids):
                    fail("NOT_FOUND", "Product not found")
                if any(by_id[line["productId"]]["stock"] < line["quantity"] for line in lines):
                    fail("OUT_OF_STOCK", "Insufficient stock")
                total = sum(by_id[line["productId"]]["price_cents"] * line["quantity"] for line in lines)
                order_id = str(uuid4())
                await conn.execute("INSERT INTO orders(id,customer_id,status,total_cents,idempotency_key,request_hash) VALUES($1,$2,'CREATED',$3,$4,$5)",
                                   order_id, user["id"], total, key, digest)
                for line in sorted(lines, key=lambda x: x["productId"]):
                    item = by_id[line["productId"]]
                    await conn.execute("UPDATE products SET stock=stock-$1 WHERE id=$2", line["quantity"], item["id"])
                    await conn.execute("INSERT INTO order_items(order_id,product_id,name,quantity,unit_price_cents) VALUES($1,$2,$3,$4,$5)",
                                       order_id, item["id"], item["name"], line["quantity"], item["price_cents"])
    return await read_order(pool, order_id, user)


async def cancel_order(_, info, id):
    user, pool = principal(info), info.context["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM orders WHERE id=$1 FOR UPDATE", id)
            if not row or (row["customer_id"] != user["id"] and user["role"] != "ADMIN"):
                fail("NOT_FOUND", "Order not found")
            if row["status"] != "CANCELLED":
                lines = await conn.fetch("SELECT product_id,quantity FROM order_items WHERE order_id=$1 ORDER BY product_id", id)
                for line in lines:
                    await conn.execute("UPDATE products SET stock=stock+$1 WHERE id=$2", line["quantity"], line["product_id"])
                await conn.execute("UPDATE orders SET status='CANCELLED' WHERE id=$1", id)
    return await read_order(pool, id, user)


async def admin_stats(_, info):
    principal(info, admin=True)
    pool = info.context["pool"]
    row = await pool.fetchrow("SELECT count(*) AS n,coalesce(sum(total_cents),0) AS total FROM orders WHERE status='CREATED'")
    return {"customers": await pool.fetchval("SELECT count(*) FROM customers WHERE role='CUSTOMER'"),
            "activeOrders": row["n"], "revenueCents": row["total"]}


async def order_items(parent, info):
    rows = await info.context["pool"].fetch("SELECT * FROM order_items WHERE order_id=$1 ORDER BY product_id", parent["id"])
    return [{"productId": row["product_id"], "name": row["name"], "quantity": row["quantity"],
             "unitPriceCents": row["unit_price_cents"], "totalCents": row["quantity"] * row["unit_price_cents"]} for row in rows]


async def order_customer(parent, info):
    return user_dict(await info.context["pool"].fetchrow("SELECT * FROM customers WHERE id=$1", parent["customerId"]))


async def item_product(parent, info):
    return await product(None, info, parent["productId"])


async def user_orders(parent, info):
    user = principal(info)
    if parent["id"] != user["id"] and user["role"] != "ADMIN":
        fail("FORBIDDEN", "Cannot read another customer")
    rows = await info.context["pool"].fetch("SELECT id FROM orders WHERE customer_id=$1 ORDER BY created_at,id", parent["id"])
    return [await read_order(info.context["pool"], row["id"], user) for row in rows]


@asynccontextmanager
async def lifespan(app):
    pool = await asyncpg.create_pool(os.environ["COMMERCE_DATABASE_URL"], min_size=1, max_size=10)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
                for id, name, email, role in [("alice", "Alice", "alice@example.test", "CUSTOMER"),
                                              ("bob", "Bob", "bob@example.test", "CUSTOMER"),
                                              ("admin", "Administrator", "admin@example.test", "ADMIN")]:
                    await conn.execute("INSERT INTO customers VALUES($1,$2,$3,$4,$5) ON CONFLICT(id) DO NOTHING",
                                       id, name, email, password_hash("test-password-2026"), role)
                for product in [("keyboard", "Mechanical Keyboard", 750000, 12), ("monitor", "27 inch Monitor", 2499000, 5),
                                ("mouse", "Wireless Mouse", 199000, 0), ("cable", "USB-C Cable", 99000, 100)]:
                    await conn.execute("INSERT INTO products VALUES($1,$2,$3,$4) ON CONFLICT(id) DO NOTHING", *product)
        app.state.pool = pool
        yield
    finally:
        await pool.close()


app = FastAPI(title="Independent Commerce GraphQL API", lifespan=lifespan)
schema = build_schema(SDL)
for type_name, resolvers in {
    "Query": {"me": me, "products": products, "product": product, "orders": orders, "order": order, "adminStats": admin_stats},
    "Mutation": {"login": login, "updateProfile": update_profile, "createOrder": create_order, "cancelOrder": cancel_order},
    "Order": {"items": order_items, "customer": order_customer}, "OrderItem": {"product": item_product}, "User": {"orders": user_orders},
}.items():
    for name, resolver in resolvers.items():
        schema.get_type(type_name).fields[name].resolve = resolver


@app.get("/health")
async def health(request: Request):
    await request.app.state.pool.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/schema.graphql", response_class=PlainTextResponse)
async def schema_sdl():
    return SDL


@app.post("/graphql")
async def execute(request: Request):
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"errors": [{"message": "Invalid JSON"}]}, status_code=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("query"), str) or (
        payload.get("variables") is not None and not isinstance(payload["variables"], dict)
    ) or (payload.get("operationName") is not None and not isinstance(payload["operationName"], str)):
        return JSONResponse({"errors": [{"message": "Invalid GraphQL envelope"}]}, status_code=400)
    pool = request.app.state.pool
    user = None
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        row = await pool.fetchrow("SELECT c.* FROM sessions s JOIN customers c ON c.id=s.customer_id WHERE s.token_hash=$1 AND s.expires_at>now()",
                                  hashlib.sha256(header[7:].encode()).hexdigest())
        if row:
            user = user_dict(row)
    result = await graphql(schema, payload["query"], variable_values=payload.get("variables"),
                           operation_name=payload.get("operationName"), context_value={"pool": pool, "user": user})
    return JSONResponse(result.formatted)
