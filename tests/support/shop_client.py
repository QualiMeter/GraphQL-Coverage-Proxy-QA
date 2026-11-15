from pathlib import Path
from coverage_qa.environment import ROOT
from uuid import uuid4

from graphql import GraphQLObjectType, build_schema

from tests.support.coverage_client import CoverageRun


CATALOG = "query Catalog { products(limit:100) { id name priceCents stock } }"
ORDER_FIELDS = "id status totalCents createdAt customer { id } items { product { id stock } name quantity unitPriceCents totalCents }"
CREATE = "mutation Checkout($input: CreateOrderInput!) { createOrder(input:$input) { " + ORDER_FIELDS + " } }"
ORDER = "query OrderById($id:ID!) { order(id:$id) { " + ORDER_FIELDS + " } }"
CANCEL = "mutation Cancel($id:ID!) { cancelOrder(id:$id) { id status } }"


class BusinessClient:
    def __init__(self, api, stack, report, route):
        self.api, self.stack, self.report, self.route = api, stack, report, route
        self.run = None
        self.token = None
        self.created = []

    def prepare(self, test_id):
        self.api.entry["transport"] = self.route
        self.api.entry["target"] = (self.stack.proxy.base if self.route == "proxy" else self.stack.upstream.base) + "/graphql"
        if self.route == "proxy":
            schema = build_schema((ROOT / "examples" / "shop" / "api" / "schema.graphql").read_text(encoding="utf-8"))
            total = sum(len(t.fields) for name, t in schema.type_map.items() if isinstance(t, GraphQLObjectType) and not name.startswith("__"))
            self.run = CoverageRun(self.api, self.stack, self.report, total_fields=total)
            self.run.prepare(test_id)
        self.login("alice")

    def login(self, account, password="test-password-2026"):
        status, body = self.api.http("Войти как " + account, "POST", self.stack.upstream.base + "/graphql", auth=False,
                                     payload={"query": "mutation Login($email:String!,$password:String!){login(email:$email,password:$password){token user{id role}}}",
                                              "variables": {"email": account + "@example.test", "password": password}})
        self.api.expect("Login: HTTP", status, 200)
        self.api.expect("Login: нет GraphQL errors", body.get("errors", []), [])
        self.token = body["data"]["login"]["token"]
        return self.token

    def gql(self, title, query, variables=None, *, token="current", errors=False, fields=(), operation=None):
        actual_token = self.token if token == "current" else token
        headers = {"Authorization": "Bearer " + actual_token} if actual_token else {}
        if self.run:
            status, body = self.run.graphql(title, query, variables=variables, operation=operation, fields=fields, headers=headers)
        else:
            status, body = self.api.http(title, "POST", self.stack.upstream.base + "/graphql", auth=False, headers=headers,
                                         payload={"query": query, "variables": variables, "operationName": operation})
        self.api.expect(title + ": HTTP", status, 200)
        if not errors:
            self.api.expect(title + ": GraphQL errors отсутствуют", body.get("errors", []), [])
        return body

    def catalog(self):
        return self.gql("Прочитать фактические остатки", CATALOG)["data"]["products"]

    def create(self, items, key=None, *, errors=False, token="current"):
        body = self.gql("Оформить заказ", CREATE, {"input": {"items": items, "idempotencyKey": key or uuid4().hex}}, errors=errors, token=token)
        if body.get("data"):
            order = body["data"]["createOrder"]
            owner_token = self.token if token == "current" else token
            if not any(x[0] == order["id"] for x in self.created):
                self.created.append((order["id"], owner_token))
            return order
        return body

    def read_order(self, id, *, token="current", errors=False):
        return self.gql("Прочитать сохранённый заказ", ORDER, {"id": id}, token=token, errors=errors)

    def cancel(self, id, *, token="current", errors=False):
        return self.gql("Отменить заказ", CANCEL, {"id": id}, token=token, errors=errors)

    def cleanup(self):
        try:
            for order_id, token in self.created:
                self.cancel(order_id, token=token)
        finally:
            if self.run and self.run.run_id:
                try:
                    self.run.finish()
                finally:
                    self.run.snapshot("Итог бизнес-сценария")
