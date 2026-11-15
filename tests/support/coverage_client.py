import hashlib
import json
import urllib.parse
from uuid import uuid4


def field_hits(graph):
    return {f"{t['name']}.{f['name']}": f["hits"] for t in graph["types"] for f in t["fields"]}


class CoverageRun:
    def __init__(self, api, stack, report, *, total_fields=10):
        self.api, self.stack, self.report = api, stack, report
        self.base = stack.proxy.base
        self.run_id = self.snapshot_id = self.service_id = None
        self.finished = False
        self.record = None
        self.seen_requests = set()
        self.total_fields = total_fields

    def rest(self, title, method, path, payload=None, *, expected=200, auth=True):
        status, body = self.api.http(title, method, self.base + path, payload=payload, auth=auth)
        self.api.expect(title + ": HTTP", status, expected)
        return body

    def prepare(self, test_id, *, endpoint=None):
        service = self.rest("Зарегистрировать отдельный тестовый сервис", "POST", "/api/services",
                            {"key": "test-" + uuid4().hex, "name": test_id[-200:],
                             "endpointUrl": endpoint or self.stack.upstream.base + "/graphql"}, expected=201)
        self.service_id = service["id"]
        snapshot = self.rest("Получить настоящую схему через introspection", "POST", "/api/schemas/import/introspection",
                             {"serviceId": self.service_id}, expected=201)
        self.snapshot_id = snapshot["id"]
        self.schema_hash = snapshot["schemaHash"]
        self.api.expect("Снимок получен через introspection", snapshot["source"], "introspection")
        self.new_phase(test_id, "Основной")

    def new_phase(self, test_id, name):
        node_id = self.api.entry["id"]
        run = self.rest("Создать прогон: " + name, "POST", "/api/runs",
                        {"snapshotId": self.snapshot_id, "name": (name + ": " + test_id)[-200:],
                         "metadata": {"testRunId": self.report["id"], "pytestNodeId": node_id}}, expected=201)
        self.run_id = run["id"]
        self.finished = False
        self.seen_requests = set()
        self.record = {"testId": node_id, "serviceId": self.service_id, "snapshotId": self.snapshot_id,
                       "runId": self.run_id, "schemaHash": self.schema_hash,
                       "name": name, "exchanges": [], "checkpoints": [], "requestDetails": {}}
        self.report["coverageRuns"].append(self.record)
        self.api.entry["coverageContext"] = {k: self.record[k] for k in ("runId", "snapshotId", "serviceId")}

    def graphql(self, title, query, *, variables=None, operation=None, fields=(), mode=None, run_id=None, headers=None):
        headers = {**(headers or {}), "X-Coverage-Run-Id": run_id or self.run_id}
        if self.record:
            if "testId" in self.record and self.record["testId"]:
                headers["X-Test-Id"] = urllib.parse.quote(str(self.record["testId"]))
            if "name" in self.record and self.record["name"]:
                headers["X-Scenario-Name"] = urllib.parse.quote(str(self.record["name"]))
        if mode:
            headers["X-Test-Mode"] = mode
        status, body = self.api.raw(title, payload={"query": query, "variables": variables, "operationName": operation}, headers=headers)
        selected_name = operation
        if selected_name is None:
            from graphql import parse, get_operation_ast, GraphQLError
            try:
                selected = get_operation_ast(parse(query))
                selected_name = selected.name.value if selected and selected.name else None
            except GraphQLError:
                pass
        exchange = {"title": title, "operationName": selected_name, "queryHash": hashlib.sha256(query.encode()).hexdigest(),
                    "declaredSelectedFields": sorted(fields), "httpStatus": status,
                    "eventIndex": len(self.api.entry["events"]) - 1, "runId": headers["X-Coverage-Run-Id"]}
        self.record["exchanges"].append(exchange)
        if headers["X-Coverage-Run-Id"] == self.run_id:
            observations = self.rest("Сопоставить HTTP-запрос с наблюдением", "GET", f"/api/runs/{self.run_id}/requests?limit=500")
            new_ids = {o["id"] for o in observations} - self.seen_requests
            exchange["observedRequestIds"] = sorted(new_ids)
            exchange["attribution"] = "single-request-delta" if len(new_ids) == 1 else "none-or-ambiguous"
            self.seen_requests.update(new_ids)
            exchange["requestDetails"] = [self.request_detail(id) for id in sorted(new_ids)]
            if len(new_ids) == 1:
                self.attach_detail(exchange, exchange["requestDetails"][0])
        return status, body

    def request_detail(self, id):
        details = self.record["requestDetails"]
        if id not in details:
            details[id] = self.rest("Фактические поля отдельного запроса", "GET",
                                    f"/api/runs/{self.run_id}/requests/{id}")
        return details[id]

    def attach_detail(self, exchange, detail):
        exchange["actualSelectedFields"] = sorted(f"{f['typeName']}.{f['fieldName']}" for f in detail["selectedFields"])
        exchange["countedInCoverage"] = detail["countedInCoverage"]
        event = self.api.entry["events"][exchange["eventIndex"]]
        event["observedRequestId"] = detail["id"]
        event["actualSelectedFields"] = exchange["actualSelectedFields"]
        event["countedInCoverage"] = detail["countedInCoverage"]

    def snapshot(self, title):
        graph = self.rest(title + ": покрытие", "GET", f"/api/runs/{self.run_id}/coverage")
        operations = self.rest(title + ": поля операций", "GET", f"/api/runs/{self.run_id}/operations")
        requests = self.rest(title + ": обращения", "GET", f"/api/runs/{self.run_id}/requests?limit=500")
        details = [self.request_detail(row["id"]) for row in requests]
        checkpoint = {"title": title, "graph": graph, "operations": operations, "requests": requests,
                      "requestDetails": details,
                      "fieldHits": field_hits(graph)}
        self.record["checkpoints"].append(checkpoint)
        for exchange in self.record["exchanges"]:
            if exchange["runId"] != self.run_id:
                continue
            for operation in operations:
                identity = [exchange["queryHash"], exchange["operationName"], operation["operationType"]]
                key = hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
                if key == operation["operationKey"]:
                    exchange["observedOperationKey"] = key
                    matching = [d for d in details if d["operationKey"] == key]
                    if exchange.get("attribution") == "browser-operation-aggregate" and len(matching) == 1:
                        exchange["observedRequestIds"] = [matching[0]["id"]]
                        exchange["attribution"] = "unique-operation-request"
                        self.attach_detail(exchange, matching[0])
                    exchange["observedOperationAggregate"] = {
                        "scope": "operation-across-run-not-single-request",
                        "requestCount": operation["requestCount"],
                        "countedRequestCount": operation["countedRequestCount"],
                        "fieldHits": {f"{f['typeName']}.{f['fieldName']}": f["hits"] for f in operation["fields"]},
                    }
        self.record["finalFieldHits"] = checkpoint["fieldHits"]
        self.record["status"] = "completed" if self.finished else "running"
        self.api.entry["events"].append({"kind": "coverage", "title": title, "runId": self.run_id,
                                         "summary": graph["summary"], "fieldHits": field_hits(graph)})
        return checkpoint

    def expect_fields(self, checkpoint, expected):
        self.api.expect("Точный набор покрытых полей", sorted(k for k, v in checkpoint["fieldHits"].items() if v), sorted(expected))
        self.api.expect("Знаменатель соответствует тестовой схеме", checkpoint["graph"]["summary"]["totalFields"], self.total_fields)
        self.api.expect("Число покрытых полей", checkpoint["graph"]["summary"]["coveredFields"], len(expected))
        self.api.expect("Процент вычислен из уникальных полей", checkpoint["graph"]["summary"]["coveragePercent"], round(len(expected) * 100 / self.total_fields, 2))

    def finish(self):
        if not self.finished:
            self.rest("Завершить сбор (cleanup)", "POST", f"/api/runs/{self.run_id}/finish")
            self.finished = True
