"""Windows client for an explicitly started, authenticated local test bridge."""

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


def call(config_path, op, *, timeout=200, **payload):
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    request = Request(
        "http://127.0.0.1:25510/v1/jobs",
        data=json.dumps({"op": op, **payload}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config["token"]},
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        message = json.loads(error.read()).get("error", "Local bridge rejected request")
        raise RuntimeError(message) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Local test bridge is unavailable; no operation was retried") from None
