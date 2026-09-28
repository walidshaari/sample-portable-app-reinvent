#!/usr/bin/env python3
"""Generate a runnable clean-architecture Python service (stdlib only).

Usage:
  new_service.py <service_name> <dest_dir> [--entity Note] [--archguard PATH]

Creates <dest_dir>/<service_name>/ with domain, ports, use cases, an in-memory
adapter, a composition root, a stdlib HTTP server, tests, a vendored archguard,
Kiro steering and hooks, and a Dockerfile.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()

CORE_ALLOW = ["typing", "dataclasses", "re", "uuid", "datetime", "decimal", "enum", "abc",
              "__future__", "collections", "functools", "itertools", "math", "numbers", "string"]


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def plural(word: str) -> str:
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def find_upward(candidates: list[str]) -> Path | None:
    for base in SCRIPT.parents:
        for rel in candidates:
            p = base / rel
            if p.is_file():
                return p
    return None


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


TEMPLATES: dict[str, str] = {}

TEMPLATES["archguard.toml"] = """\
# archguard rules for this service. Paths are relative to this file.
[project]
source_roots = ["src"]

[layers.domain]
paths = ["domain"]
allow = __DOMAIN_ALLOW__

[layers.application]
paths = ["application"]
allow = __APP_ALLOW__

[layers.infrastructure]
paths = ["infrastructure"]
allow = ["*"]

[rules]
composition_roots = ["infrastructure/composition.py"]
adapter_packages = ["infrastructure.repositories"]
config_read_modules = ["dotenv", "decouple", "environs"]
"""

TEMPLATES["pytest.ini"] = """\
[pytest]
pythonpath = src
testpaths = tests
addopts = -q -p no:cacheprovider
"""

TEMPLATES["src/domain/__init__.py"] = ""
TEMPLATES["src/application/__init__.py"] = ""
TEMPLATES["src/application/ports/__init__.py"] = ""
TEMPLATES["src/application/use_cases/__init__.py"] = ""
TEMPLATES["src/infrastructure/__init__.py"] = ""
TEMPLATES["src/infrastructure/http/__init__.py"] = ""
TEMPLATES["src/infrastructure/repositories/__init__.py"] = ""

TEMPLATES["src/domain/__entity__.py"] = '''\
"""__Entity__ entity. Pure Python: no I/O, no configuration, no framework."""
from dataclasses import dataclass

MAX_TITLE_LENGTH = 200


@dataclass(frozen=True)
class __Entity__:
    id: str
    title: str
    body: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("__Entity__ id is required")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("__Entity__ title is required")
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValueError(f"__Entity__ title must be at most {MAX_TITLE_LENGTH} characters")
        if not isinstance(self.body, str):
            raise ValueError("__Entity__ body must be a string")

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "body": self.body}
'''

TEMPLATES["src/application/ports/__entity___repository.py"] = '''\
"""Port: what the use cases need from storage. No implementation here."""
from abc import ABC, abstractmethod
from typing import List, Optional

from domain.__entity__ import __Entity__


class __Entity__Repository(ABC):
    @abstractmethod
    async def create(self, __entity__: __Entity__) -> None:
        """Persist a new __entity__."""

    @abstractmethod
    async def find_by_id(self, id: str) -> Optional[__Entity__]:
        """Return the __entity__ or None."""

    @abstractmethod
    async def find_all(self) -> List[__Entity__]:
        """Return every __entity__."""

    @abstractmethod
    async def delete(self, id: str) -> bool:
        """Delete the __entity__; return True if it existed."""
'''

TEMPLATES["src/application/use_cases/create___entity__.py"] = '''\
"""Use case: create a __entity__. Orchestration and id generation live here."""
import uuid
from dataclasses import dataclass
from typing import Callable

from application.ports.__entity___repository import __Entity__Repository
from domain.__entity__ import __Entity__


@dataclass(frozen=True)
class Create__Entity__Input:
    title: str
    body: str = ""


def _new_id() -> str:
    return str(uuid.uuid4())


class Create__Entity__UseCase:
    def __init__(self, repository: __Entity__Repository, new_id: Callable[[], str] = _new_id) -> None:
        self._repository = repository
        self._new_id = new_id

    async def execute(self, data: Create__Entity__Input) -> __Entity__:
        __entity__ = __Entity__(id=self._new_id(), title=data.title.strip(), body=data.body)
        await self._repository.create(__entity__)
        return __entity__
'''

TEMPLATES["src/application/use_cases/get___entity__.py"] = '''\
"""Use cases: read __entities__."""
from typing import List, Optional

from application.ports.__entity___repository import __Entity__Repository
from domain.__entity__ import __Entity__


class Get__Entity__UseCase:
    def __init__(self, repository: __Entity__Repository) -> None:
        self._repository = repository

    async def execute(self, id: str) -> Optional[__Entity__]:
        return await self._repository.find_by_id(id)


class List__Entities__UseCase:
    def __init__(self, repository: __Entity__Repository) -> None:
        self._repository = repository

    async def execute(self) -> List[__Entity__]:
        return await self._repository.find_all()
'''

TEMPLATES["src/infrastructure/repositories/in_memory___entity___repository.py"] = '''\
"""In-memory adapter for __Entity__Repository. Used by tests and local runs."""
import threading
from typing import Dict, List, Optional

from application.ports.__entity___repository import __Entity__Repository
from domain.__entity__ import __Entity__


class InMemory__Entity__Repository(__Entity__Repository):
    def __init__(self) -> None:
        self._items: Dict[str, __Entity__] = {}
        self._lock = threading.Lock()

    async def create(self, __entity__: __Entity__) -> None:
        with self._lock:
            if __entity__.id in self._items:
                raise ValueError(f"__Entity__ {__entity__.id} already exists")
            self._items[__entity__.id] = __entity__

    async def find_by_id(self, id: str) -> Optional[__Entity__]:
        with self._lock:
            return self._items.get(id)

    async def find_all(self) -> List[__Entity__]:
        with self._lock:
            return list(self._items.values())

    async def delete(self, id: str) -> bool:
        with self._lock:
            return self._items.pop(id, None) is not None
'''

TEMPLATES["src/infrastructure/composition.py"] = '''\
"""Composition root.

The only module that reads configuration and selects adapters. Everything else
receives its dependencies as parameters.

Environment variables (all optional):
  REPOSITORY_BACKEND  adapter name from REPOSITORY_REGISTRY (default "memory")
  APP_RUNTIME         label reported by GET /api/health (default "local")
  HOST                bind address (default 127.0.0.1)
  PORT                listen port (default 8080; 0 picks a free port)
"""
import os
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional

from application.ports.__entity___repository import __Entity__Repository
from application.use_cases.create___entity__ import Create__Entity__UseCase
from application.use_cases.get___entity__ import Get__Entity__UseCase, List__Entities__UseCase
from infrastructure.http.server import __Entity__Api
from infrastructure.repositories.in_memory___entity___repository import InMemory__Entity__Repository

REPOSITORY_REGISTRY: Dict[str, Callable[[], __Entity__Repository]] = {
    "memory": InMemory__Entity__Repository,
}


class ConfigurationError(RuntimeError):
    """Raised when configuration asks for something that is not wired."""


@dataclass(frozen=True)
class Settings:
    backend: str
    runtime: str
    host: str
    port: int


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    backend = env.get("REPOSITORY_BACKEND", "memory").strip().lower()
    if backend not in REPOSITORY_REGISTRY:
        raise ConfigurationError(
            f"REPOSITORY_BACKEND={backend!r} is not supported; use one of {sorted(REPOSITORY_REGISTRY)}")
    raw_port = env.get("PORT", "8080").strip()
    if not raw_port.isdigit() or not 0 <= int(raw_port) <= 65535:
        raise ConfigurationError(f"PORT={raw_port!r} is not a valid port")
    return Settings(
        backend=backend,
        runtime=env.get("APP_RUNTIME", "local").strip() or "local",
        host=env.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(raw_port),
    )


def build_app(settings: Optional[Settings] = None) -> __Entity__Api:
    settings = settings or load_settings()
    repository = REPOSITORY_REGISTRY[settings.backend]()
    return __Entity__Api(
        create=Create__Entity__UseCase(repository),
        get=Get__Entity__UseCase(repository),
        list_all=List__Entities__UseCase(repository),
        runtime=settings.runtime,
    )
'''

TEMPLATES["src/infrastructure/http/server.py"] = '''\
"""Stdlib HTTP adapter. Translates JSON requests to use-case calls.

No authentication is implemented. Bind to 127.0.0.1 (the default) or put the
service behind an authenticating proxy before exposing it.
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple

from application.use_cases.create___entity__ import Create__Entity__Input, Create__Entity__UseCase
from application.use_cases.get___entity__ import Get__Entity__UseCase, List__Entities__UseCase

MAX_BODY_BYTES = 64 * 1024
COLLECTION = "/api/__entities__"


class __Entity__Api:
    """Framework-free routing: (method, path, body) -> (status, JSON payload)."""

    def __init__(self, create: Create__Entity__UseCase, get: Get__Entity__UseCase,
                 list_all: List__Entities__UseCase, runtime: str) -> None:
        self._create = create
        self._get = get
        self._list = list_all
        self.runtime = runtime

    def handle(self, method: str, path: str, body: bytes) -> Tuple[int, dict]:
        path = path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/health":
            if method != "GET":
                return 405, {"error": "method not allowed"}
            return 200, {"status": "ok", "runtime": self.runtime}
        if path == COLLECTION:
            if method == "GET":
                items = asyncio.run(self._list.execute())
                return 200, {"__entities__": [i.to_dict() for i in items]}
            if method == "POST":
                return self._create_item(body)
            return 405, {"error": "method not allowed"}
        if path.startswith(COLLECTION + "/"):
            if method != "GET":
                return 405, {"error": "method not allowed"}
            item = asyncio.run(self._get.execute(path[len(COLLECTION) + 1:]))
            if item is None:
                return 404, {"error": "__entity__ not found"}
            return 200, item.to_dict()
        return 404, {"error": "not found"}

    def _create_item(self, body: bytes) -> Tuple[int, dict]:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return 400, {"error": "request body must be JSON"}
        if not isinstance(data, dict):
            return 400, {"error": "request body must be a JSON object"}
        title, text = data.get("title"), data.get("body", "")
        if not isinstance(title, str) or not isinstance(text, str):
            return 400, {"error": "title and body must be strings"}
        try:
            item = asyncio.run(self._create.execute(Create__Entity__Input(title=title, body=text)))
        except ValueError as exc:
            return 422, {"error": str(exc)}
        return 201, item.to_dict()


def make_server(api: __Entity__Api, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "__service__"

        def _dispatch(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"error": "invalid Content-Length"})
            if length < 0 or length > MAX_BODY_BYTES:
                return self._send(413, {"error": "request body too large"})
            body = self.rfile.read(length) if length else b""
            status, payload = api.handle(method, self.path, body)
            self._send(status, payload)

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return ThreadingHTTPServer((host, port), Handler)
'''

TEMPLATES["src/main.py"] = '''\
"""Process entrypoint. Configuration and wiring come from the composition root."""
from infrastructure.composition import build_app, load_settings
from infrastructure.http.server import make_server


def main() -> None:
    settings = load_settings()
    server = make_server(build_app(settings), settings.host, settings.port)
    host, port = server.server_address[:2]
    print(f"__service__ listening on http://{host}:{port} (runtime={settings.runtime})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
'''

TEMPLATES["tests/__init__.py"] = ""

TEMPLATES["tests/conftest.py"] = '''\
"""Core tests run with network sockets blocked. Local AF_UNIX pairs (used by
asyncio internally) are still allowed."""
import socket

import pytest

_REAL_SOCKET = socket.socket
_NETWORK = {-1, socket.AF_INET, socket.AF_INET6}


class NetworkBlockedError(RuntimeError):
    pass


class _GuardedSocket(_REAL_SOCKET):
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        if family in _NETWORK:
            raise NetworkBlockedError("network access is blocked in unit tests")
        super().__init__(family, type, proto, fileno)


def _blocked(*args, **kwargs):
    raise NetworkBlockedError("network access is blocked in unit tests")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _GuardedSocket)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
'''

TEMPLATES["tests/test_domain.py"] = '''\
import pytest

from domain.__entity__ import MAX_TITLE_LENGTH, __Entity__


def test_valid___entity__():
    item = __Entity__(id="1", title="First", body="text")
    assert item.to_dict() == {"id": "1", "title": "First", "body": "text"}


@pytest.mark.parametrize("kwargs,message", [
    ({"id": "", "title": "t"}, "id is required"),
    ({"id": "1", "title": "  "}, "title is required"),
    ({"id": "1", "title": "x" * (MAX_TITLE_LENGTH + 1)}, "at most"),
    ({"id": "1", "title": "t", "body": 3}, "body must be a string"),
])
def test_invalid___entity__(kwargs, message):
    with pytest.raises(ValueError, match=message):
        __Entity__(**kwargs)
'''

TEMPLATES["tests/test_use_cases.py"] = '''\
import asyncio
import socket

import pytest

from application.use_cases.create___entity__ import Create__Entity__Input, Create__Entity__UseCase
from application.use_cases.get___entity__ import Get__Entity__UseCase, List__Entities__UseCase
from infrastructure.repositories.in_memory___entity___repository import InMemory__Entity__Repository


def test_create_then_read():
    repo = InMemory__Entity__Repository()
    created = asyncio.run(Create__Entity__UseCase(repo, new_id=lambda: "id-1").execute(
        Create__Entity__Input(title="  Hello ", body="b")))
    assert created.id == "id-1" and created.title == "Hello"
    assert asyncio.run(Get__Entity__UseCase(repo).execute("id-1")) == created
    assert asyncio.run(List__Entities__UseCase(repo).execute()) == [created]
    assert asyncio.run(Get__Entity__UseCase(repo).execute("missing")) is None


def test_invalid_input_stores_nothing():
    repo = InMemory__Entity__Repository()
    with pytest.raises(ValueError):
        asyncio.run(Create__Entity__UseCase(repo).execute(Create__Entity__Input(title="")))
    assert asyncio.run(repo.find_all()) == []


def test_repository_delete():
    repo = InMemory__Entity__Repository()
    asyncio.run(Create__Entity__UseCase(repo, new_id=lambda: "x").execute(Create__Entity__Input(title="t")))
    assert asyncio.run(repo.delete("x")) is True
    assert asyncio.run(repo.delete("x")) is False


def test_network_is_blocked():
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
'''

TEMPLATES["tests/test_api.py"] = '''\
"""HTTP routing tested without sockets: the API object is plain Python."""
import json

from infrastructure.composition import Settings, build_app


def api():
    return build_app(Settings(backend="memory", runtime="test", host="127.0.0.1", port=0))


def test_health_reports_runtime_from_composition():
    assert api().handle("GET", "/api/health", b"") == (200, {"status": "ok", "runtime": "test"})


def test_create_and_read():
    a = api()
    status, body = a.handle("POST", "/api/__entities__", json.dumps({"title": "t"}).encode())
    assert status == 201
    assert a.handle("GET", f"/api/__entities__/{body['id']}", b"") == (200, body)
    assert a.handle("GET", "/api/__entities__", b"")[1] == {"__entities__": [body]}


def test_errors():
    a = api()
    assert a.handle("POST", "/api/__entities__", b"not json")[0] == 400
    assert a.handle("POST", "/api/__entities__", json.dumps({"title": ""}).encode())[0] == 422
    assert a.handle("GET", "/api/__entities__/missing", b"")[0] == 404
    assert a.handle("DELETE", "/api/__entities__", b"")[0] == 405
'''

TEMPLATES["tests/test_architecture.py"] = '''\
"""The service must pass its own boundary check."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_archguard_check_passes():
    p = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "archguard" / "archguard.py"), "check",
         "--config", str(ROOT / "archguard.toml")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
'''

TEMPLATES["Dockerfile"] = """\
# Pass a digest-pinned Python base image from a private registry, for example
#   docker build --build-arg PYTHON_BASE_IMAGE=<registry>/python@sha256:<digest> .
# There is intentionally no default.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app
COPY src/ /app/src/

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    HOST=0.0.0.0 \\
    PORT=8080 \\
    APP_RUNTIME=container

USER 65532:65532
EXPOSE 8080
CMD ["python", "src/main.py"]
"""

TEMPLATES[".kiro/hooks/clean-guard.json"] = json.dumps({
    "version": "v1",
    "hooks": [
        {"name": "Block boundary violations before write", "trigger": "PreToolUse",
         "matcher": "fs_write|str_replace|fs_append|write",
         "timeout": 20,
         "action": {"type": "command", "command": "python3 tools/archguard/archguard.py hook --agent kiro"}},
        {"name": "Block commits that break boundaries", "trigger": "PreToolUse",
         "matcher": "execute_bash|shell",
         "timeout": 20,
         "action": {"type": "command", "command": "python3 tools/archguard/archguard.py hook --agent kiro"}},
        {"name": "Architecture check when the agent stops", "trigger": "Stop",
         "timeout": 30,
         "action": {"type": "command", "command": "python3 tools/archguard/archguard.py check 1>&2"}},
    ],
}, indent=2) + "\n"

TEMPLATES["README.md"] = """\
# __service__

Clean-architecture Python service generated by `new_service.py`. Standard library only.

## Layout

- `src/domain/__entity__.py`: the `__Entity__` entity and its validation.
- `src/application/ports/__entity___repository.py`: the storage port (ABC).
- `src/application/use_cases/`: create, get and list use cases.
- `src/infrastructure/repositories/`: adapters (in-memory for now).
- `src/infrastructure/composition.py`: the only module that reads configuration.
- `src/infrastructure/http/server.py`: stdlib JSON API.
- `tools/archguard/archguard.py`: vendored boundary checker; rules in `archguard.toml`.

## Run

```sh
python -m pytest            # domain, use-case, API and architecture tests; network blocked
python3 tools/archguard/archguard.py check
PORT=8080 python src/main.py
curl -s localhost:8080/api/health
curl -s -X POST localhost:8080/api/__entities__ -H 'Content-Type: application/json' -d '{"title":"first"}'
```

The HTTP API has no authentication. It binds to 127.0.0.1 unless `HOST` is set.
Put it behind an authenticating proxy before exposing it.

## Add a real adapter

1. Implement `__Entity__Repository` in `src/infrastructure/repositories/<backend>___entity___repository.py`.
2. Register it in `REPOSITORY_REGISTRY` in the composition root and read its settings there.
3. Run the same repository tests against it.

## Container

```sh
docker build --build-arg PYTHON_BASE_IMAGE=<registry>/python@sha256:<digest> -t __service__ .
```
"""


def render(text: str, entity: str) -> str:
    s = snake(entity)
    return (text.replace("__DOMAIN_ALLOW__", toml_list(["domain"] + CORE_ALLOW))
            .replace("__APP_ALLOW__", toml_list(["domain", "application"] + CORE_ALLOW))
            .replace("__Entities__", plural(entity))
            .replace("__entities__", plural(s))
            .replace("__Entity__", entity)
            .replace("__entity__", s))


def generate(service: str, dest: Path, entity: str, archguard: Path, steering: list[Path]) -> Path:
    root = dest / service
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty directory: {root}")
    for rel, text in TEMPLATES.items():
        path = root / render(rel, entity)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(text, entity).replace("__service__", service), encoding="utf-8")
    vendored = root / "tools" / "archguard" / "archguard.py"
    vendored.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archguard, vendored)
    for s in steering:
        target = root / ".kiro" / "steering" / s.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(s, target)
    return root


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("service_name")
    p.add_argument("dest_dir")
    p.add_argument("--entity", default="Note", help="CamelCase entity name (default Note)")
    p.add_argument("--archguard", help="path to archguard.py to vendor (default: search upward)")
    args = p.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", args.service_name):
        p.error("service_name must be lowercase letters, digits, '-' or '_'")
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]{0,62}", args.entity):
        p.error("--entity must be CamelCase, for example Note or OrderLine")
    archguard = Path(args.archguard) if args.archguard else find_upward(
        ["kiro/archguard/archguard.py", "archguard/archguard.py",
         "tools/archguard/archguard.py"])
    if archguard is None or not archguard.is_file():
        p.error("archguard.py not found; pass --archguard PATH")
    steering_dir = None
    for rel in ["steering/clean-architecture.md", ".kiro/steering/clean-architecture.md",
                "dev.kiro/steering/clean-architecture.md"]:
        hit = find_upward([rel])
        if hit:
            steering_dir = hit.parent
            break
    steering = sorted(steering_dir.glob("*.md")) if steering_dir else []
    steering = [s for s in steering if s.name in ("clean-architecture.md", "domain-purity.md")]
    if not steering:
        print("warning: steering files not found; .kiro/steering was not written", file=sys.stderr)
    root = generate(args.service_name, Path(args.dest_dir).expanduser().resolve(), args.entity,
                    archguard, steering)
    print(f"created {root}")
    print("next: cd into it, run 'python -m pytest' and 'python3 tools/archguard/archguard.py check'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
