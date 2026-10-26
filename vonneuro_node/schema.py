"""Operation declarations and their parameters: a small, strict subset of JSON Schema.

Both sides use this module. The hub checks a job's parameters before queueing it (fast errors,
no obviously bad jobs on the wire); the agent checks them again before the driver sees them.
Whether a job is *safe* is the driver's own check(), never this module.

A parameter is {"type": ..., constraints..., "default": ...}:

    integer  minimum, maximum, enum
    number   minimum, maximum, exclusiveMinimum, exclusiveMaximum, enum   (finite only)
    string   minLength, maxLength, pattern, enum
    boolean
    object   additionalProperties: {"type": "number"|"integer"|"string"|"boolean"}, maxProperties
    array    items: {a parameter}, minItems, maxItems

A parameter without a default is required. "unit" and "description" are shown to users only.
"""
from __future__ import annotations

import ast
import json
import math
import re
from typing import Any

TYPES = ("integer", "number", "string", "boolean", "object", "array")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_OPERATION_BYTES = 65536
MAX_PARAMS = 32


class ParamError(ValueError):
    pass


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_value(name: str, spec: dict, value: Any):
    kind = spec.get("type")
    if kind == "integer":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ParamError(f"{name} must be an integer")
    elif kind == "number":
        if not _number(value):
            raise ParamError(f"{name} must be a finite number")
        value = float(value)
    elif kind == "string":
        if not isinstance(value, str):
            raise ParamError(f"{name} must be a string")
        if len(value) < spec.get("minLength", 0):
            raise ParamError(f"{name} must be at least {spec['minLength']} characters")
        if "maxLength" in spec and len(value.encode()) > spec["maxLength"]:
            raise ParamError(f"{name} must be at most {spec['maxLength']} bytes")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            raise ParamError(f"{name} does not match {spec['pattern']}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ParamError(f"{name} must be true or false")
    elif kind == "object":
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ParamError(f"{name} must be an object")
        if len(value) > spec.get("maxProperties", 64):
            raise ParamError(f"{name} has more than {spec.get('maxProperties', 64)} entries")
        inner = spec.get("additionalProperties", {"type": "number"})
        value = {key: _check_value(f"{name}.{key}", inner, item) for key, item in value.items()}
    elif kind == "array":
        if not isinstance(value, list):
            raise ParamError(f"{name} must be a list")
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 1024):
            raise ParamError(f"{name} must have {spec.get('minItems', 0)}..{spec.get('maxItems', 1024)} items")
        value = [_check_value(f"{name}[{i}]", spec.get("items", {"type": "number"}), item) for i, item in enumerate(value)]
    else:
        raise ParamError(f"{name} has unknown type {kind!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise ParamError(f"{name} must be one of {', '.join(map(str, spec['enum']))}")
    if kind in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"]:
            raise ParamError(f"{name} must be at least {spec['minimum']:g}{_unit(spec)}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ParamError(f"{name} must be at most {spec['maximum']:g}{_unit(spec)}")
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            raise ParamError(f"{name} must be above {spec['exclusiveMinimum']:g}{_unit(spec)}")
        if "exclusiveMaximum" in spec and value >= spec["exclusiveMaximum"]:
            raise ParamError(f"{name} must be below {spec['exclusiveMaximum']:g}{_unit(spec)}")
    return value


def _unit(spec):
    return f" {spec['unit']}" if spec.get("unit") else ""


def validate_params(declared: dict, params: dict | None) -> dict:
    """Return the parameters with defaults filled in, or raise ParamError naming the first problem."""
    params = dict(params or {})
    unknown = sorted(set(params) - set(declared))
    if unknown:
        raise ParamError(f"Unknown parameter{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
                         f" (this operation takes {', '.join(sorted(declared)) or 'none'})")
    clean = {}
    for name, spec in declared.items():
        if name in params and params[name] is not None:
            clean[name] = _check_value(name, spec, params[name])
        elif "default" in spec:
            clean[name] = spec["default"]
        else:
            raise ParamError(f"{name} is required")
    return clean


def validate_settings(declared: dict, values: dict) -> dict:
    """Check a partial update of device settings: known names, each value valid for its spec,
    or None (back to the default). Returns the checked values; raises ParamError."""
    if not isinstance(values, dict) or not values:
        raise ParamError("Give at least one setting")
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ParamError(f"Unknown setting{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
                         f" (this device has {', '.join(sorted(declared)) or 'none'})")
    spec_of = lambda name: {k: v for k, v in declared[name].items() if k != "value"}
    return {name: None if value is None else _check_value(name, spec_of(name), value) for name, value in values.items()}


def check_operation(operation: dict) -> dict:
    """Validate an operation declaration coming from a driver; return it unchanged or raise ParamError."""
    if not isinstance(operation, dict):
        raise ParamError("An operation must be an object")
    name = operation.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ParamError(f"Operation name {name!r} must be lowercase letters, digits and _")
    params = operation.get("params", {})
    if not isinstance(params, dict) or len(params) > MAX_PARAMS:
        raise ParamError(f"{name}: params must be an object of at most {MAX_PARAMS} parameters")
    for key, spec in params.items():
        if not NAME.fullmatch(key) or not isinstance(spec, dict) or spec.get("type") not in TYPES:
            raise ParamError(f"{name}.{key}: each parameter needs a lowercase name and a type in {TYPES}")
        if "default" in spec and spec["default"] is not None:
            _check_value(f"{name}.{key} default", spec, spec["default"])
    if operation.get("estimate") is not None:
        estimate_seconds(operation["estimate"], {key: 1.0 for key in params})   # parses, or raises
    for key in ("timeout_s",):
        if key in operation and not (_number(operation[key]) and 0 < operation[key] <= 86400):
            raise ParamError(f"{name}: {key} must be in (0, 86400] seconds")
    if len(json.dumps(operation, allow_nan=False)) > MAX_OPERATION_BYTES:
        raise ParamError(f"{name}: declaration exceeds {MAX_OPERATION_BYTES} bytes")
    return operation


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


def estimate_seconds(expression: str, params: dict) -> float:
    """Evaluate an operation's run-time estimate, e.g. "6 / frequency + 15".

    Numbers, parameter names, + - * / ** and min()/max() only; anything else raises ParamError."""
    if not isinstance(expression, str) or len(expression) > 256:
        raise ParamError("estimate must be a short expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ParamError(f"estimate {expression!r} is not an expression") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ParamError(f"estimate {expression!r} uses {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in ("min", "max")):
            raise ParamError("estimate may only call min() and max()")
        if isinstance(node, ast.Constant) and not _number(node.value):
            raise ParamError("estimate constants must be numbers")

    def value(node):
        if isinstance(node, ast.Expression):
            return value(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in params or not _number(params[node.id]):
                raise ParamError(f"estimate uses {node.id!r}, which is not a numeric parameter")
            return float(params[node.id])
        if isinstance(node, ast.UnaryOp):
            return -value(node.operand) if isinstance(node.op, ast.USub) else value(node.operand)
        if isinstance(node, ast.Call):
            return (min if node.func.id == "min" else max)(value(arg) for arg in node.args)
        left, right = value(node.left), value(node.right)
        if isinstance(node.op, ast.Add): return left + right
        if isinstance(node.op, ast.Sub): return left - right
        if isinstance(node.op, ast.Mult): return left * right
        if isinstance(node.op, ast.Div): return left / right if right else math.inf
        return left ** right if abs(right) <= 8 else math.inf

    result = value(tree)
    if not math.isfinite(result) or result < 0:
        raise ParamError(f"estimate {expression!r} is not a finite, nonnegative time for these parameters")
    return result
