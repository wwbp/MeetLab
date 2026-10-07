"""HTTP for the integration tests, on the standard library (no undeclared `requests`)."""
import json as _json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Response:
    status_code: int
    text: str

    def json(self):
        return _json.loads(self.text)


def request(method: str, url: str, *, headers=None, json=None, data=None, timeout=30) -> Response:
    body = _json.dumps(json).encode() if json is not None else (data.encode() if isinstance(data, str) else data)
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
    if json is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read().decode())


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
