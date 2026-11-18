import re
import time
import httpx
from coverage_qa.tracking import current_headers
from coverage_qa.reporting.progress import emit

class EvidenceMismatch(AssertionError):
    pass


class API:
    def __init__(self, url, entry, key=None):
        self.url = url
        self.entry = entry
        self.client = httpx.Client(timeout=45, trust_env=False)
        self.key = key

    def raw(self, title, *, payload=None, content=None, headers=None, tracking=True):
        return self.http(title, "POST", self.url, payload=payload, content=content, headers=headers, tracking=tracking)

    def http(self, title, method, url, *, payload=None, content=None, headers=None, auth=True, tracking=True):
        outgoing = {**({"X-Coverage-Api-Key": self.key} if self.key and auth else {}), **(headers or {})}
        if tracking and method == "POST" and url.rstrip("/").endswith("/graphql"):
            run_id = outgoing.get("X-Coverage-Run-Id")
            if run_id:
                for name, value in current_headers(url.rsplit("/graphql", 1)[0], run_id).items():
                    outgoing.setdefault(name, value)
        event = {"kind": "request", "title": title,
                 "request": redact(payload) if content is None else content,
                 "method": method, "url": url,
                 "headers": {k: "<redacted>" if any(x in k.lower() for x in ("key", "authorization", "cookie", "csrf")) else v for k, v in outgoing.items()},
                 "httpStatus": None, "response": None, "error": None}
        self.entry["events"].append(event)
        emit()
        start = time.perf_counter()
        try:
            kwargs = ({"json": payload} if payload is not None else {}) if content is None else {
                "content": content, "headers": {"Content-Type": "application/json"},
            }
            if content is not None:
                outgoing = {"Content-Type": "application/json", **outgoing}
                kwargs.pop("headers", None)
            response = self.client.request(method, url, headers=outgoing, **kwargs)
            event["httpStatus"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = response.text
            event["response"] = redact(body)
            event["responseHeaders"] = {k: v for k, v in response.headers.items() if k.lower() in {"content-type", "content-encoding", "cache-control", "x-total-count"}}
            return response.status_code, body
        except httpx.HTTPError as exc:
            event["error"] = str(exc)
            raise
        finally:
            event["durationMs"] = round((time.perf_counter() - start) * 1000, 2)
            emit()

    def gql(self, title, query, variables=None, operation=None, *, errors=False):
        status, body = self.raw(title, payload={"query": query, "variables": variables,
                                               "operationName": operation})
        self.expect("HTTP-статус GraphQL-запроса", status, 200)
        self.expect("Ответ — JSON-объект", isinstance(body, dict), True)
        if not errors:
            self.expect("GraphQL errors отсутствуют", body.get("errors", []), [])
        return body

    def expect(self, title, actual, expected):
        passed = actual == expected
        self.entry["events"].append({"kind": "check", "title": title,
                                      "actual": redact(actual), "expected": redact(expected), "passed": passed})
        emit(force=not passed)
        if not passed:
            raise EvidenceMismatch(title)


BEARER_TOKEN_RE = re.compile(
    r'(?i)\bBearer\s+[A-Za-z0-9_\-\.=]+'
)
INLINE_SECRET_RE = re.compile(
    r'(?i)(["\']?(?:password|token|accesstoken|refreshtoken|access_token|refresh_token|secret|apikey|api_key)["\']?\s*:\s*)(["\'](?:\\.|[^"\\])*["\']|\S+)'
)

SENSITIVE_KEYS = {
    "password", "token", "accesstoken", "refreshtoken", "access_token",
    "refresh_token", "secret", "apikey", "api_key", "authorization",
    "cookie", "set-cookie", "x-coverage-api-key", "x-csrf-token",
    "csrftoken", "csrf_token", "currentpassword", "current_password",
    "newpassword", "new_password", "sessiontoken", "session_token"
}


def redact(value):
    if isinstance(value, (set, frozenset)):
        return [redact(item) for item in sorted(value, key=repr)]
    if isinstance(value, str):
        v = BEARER_TOKEN_RE.sub("Bearer <redacted>", value)
        return INLINE_SECRET_RE.sub(r'\g<1>"<redacted>"', v)
    if isinstance(value, dict):
        return {
            k: "<redacted>" if k.lower() in SENSITIVE_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def ensure_entry(request):
    if not hasattr(request.node, "api_entry"):
        marker = request.node.get_closest_marker("case")
        request.node.api_entry = {"id": request.node.nodeid, "title": marker.args[0] if marker else request.node.name,
                                  "status": "passed", "duration": 0, "events": [], "failure": None}
        request.config.api_report["tests"].append(request.node.api_entry)
    return request.node.api_entry
