"""Agent tools — pi-style: plain typed functions, decorated, schemas
auto-derived from type hints (P1 §3.1). No pydantic, no dependency.

A Tool wraps a boring function:

    @tool("Get the live spot price for a symbol")
    def get_spot(symbol: str) -> dict:
        ...

and exposes:
    .name          function name
    .description   the decorator arg
    .parameters    JSON-schema (OpenAI tools format)
    .fn            the wrapped function
    .returns       hint of the return type (documentation only)

The registry enforces the read-only law (L13/L14): only functions whose
module lives in the ALLOWED_MODULES allowlist may be registered by the
desk-tool factory. Research-only tools (web fetch) live in browse.py.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

_PRIMITIVES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    module: str = ""
    mutating: bool = False           # must stay False — enforced by tests
    returns: str = "object"

    def schema(self) -> dict:
        """OpenAI tools-format function descriptor."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, **kwargs) -> Any:
        """Execute with JSON-safe result coercion (fail-closed per call)."""
        try:
            out = self.fn(**kwargs)
        except Exception as e:  # tool failure = data, not a crash (L5)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return out if _is_json_safe(out) else _jsonify(out)


def _is_json_safe(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _jsonify(v: Any) -> Any:
    """Last-ditch coercion: str() anything the JSON encoder rejects."""
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return json.dumps(v, default=str, ensure_ascii=False)


def _param_schema(ann: Any, default: Any) -> dict:
    t = _PRIMITIVES.get(ann, "object")
    schema: dict = {"type": t}
    if default is not inspect.Parameter.empty and default is not None:
        schema["default"] = default
    return schema


def tool(description: str, *, returns: str = "object"):
    """Decorator turning a typed function into an agent Tool."""
    def wrap(fn: Callable[..., Any]) -> Tool:
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            ann = hints.get(pname, str)
            props[pname] = _param_schema(ann, param.default)
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        parameters: dict = {
            "type": "object",
            "properties": props,
        }
        if required:
            parameters["required"] = required
        return Tool(
            name=fn.__name__,
            description=description,
            parameters=parameters,
            fn=fn,
            module=getattr(fn, "__module__", "") or "",
            returns=returns,
        )
    return wrap


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, t: Tool) -> None:
        if t.mutating:
            raise ValueError(f"mutating tool rejected: {t.name}")
        if t.name in self.tools:
            raise ValueError(f"duplicate tool: {t.name}")
        self.tools[t.name] = t

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def call(self, name: str, arguments: dict | str) -> Any:
        t = self.get(name)
        if t is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"bad tool arguments: {e}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "tool arguments must be an object"}
        return t.call(**arguments)

    def names(self) -> list[str]:
        return sorted(self.tools.keys())
