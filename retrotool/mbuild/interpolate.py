"""Variable interpolation + condition evaluation for mbxml front-end.

Vars come from `<build>` attrs (name/version/revision) plus user-supplied
defines (CLI `-D version=en` or `defines=` kwarg). Syntax: `${var}`.

Conditions are simple equality/inequality only — full expressions aren't
needed for SNES romhacking patch matrices and complicate diff/audit.
"""
from __future__ import annotations

import re
from typing import Optional

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*)\}")


class InterpolationError(ValueError):
    """Raised when a `${var}` reference can't be resolved."""


def interpolate(value: str, vars: dict[str, str], *, source: str = "") -> str:
    """Substitute every `${var}` in `value` from `vars`. Unknown name raises."""
    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in vars:
            raise InterpolationError(
                f"{source}: unknown variable ${{{name}}} "
                f"(known: {sorted(vars)})"
            )
        return vars[name]
    return _VAR_RE.sub(_sub, value)


def interpolate_attrs(
    attrs: dict[str, str], vars: dict[str, str], *, source: str = ""
) -> dict[str, str]:
    """Apply `interpolate` to every value in `attrs`. Returns a new dict."""
    return {k: interpolate(v, vars, source=source) for k, v in attrs.items()}


# ---- conditions -----------------------------------------------------------

# Outermost operator wins — single regex avoids precedence errors when the
# comparison value itself contains an operator substring (e.g. `en!=ja`).
_COND_RE = re.compile(r"^(.*?)\s*(==|!=)\s*(.*)$", re.DOTALL)


def evaluate_condition(expr: str, vars: dict[str, str], *, source: str = "") -> bool:
    """Evaluate `if=` expression. Supports `${var}==literal` and `${var}!=literal`.

    Whitespace around the operator is allowed. Literal is taken verbatim
    (no quoting required) — trailing whitespace is stripped.
    """
    rendered = interpolate(expr, vars, source=source)
    m = _COND_RE.match(rendered)
    if not m:
        raise InterpolationError(
            f"{source}: condition {expr!r} must contain one of ('==', '!=')"
        )
    lhs, op, rhs = m.group(1).strip(), m.group(2), m.group(3).strip()
    return lhs == rhs if op == "==" else lhs != rhs


# ---- vars assembly --------------------------------------------------------

def build_vars(
    build_attrs: dict[str, str], defines: Optional[dict[str, str]] = None
) -> dict[str, str]:
    """Assemble the vars dict: built-ins from <build> attrs + user defines.

    User defines override built-ins on conflict — that's what enables
    `-D version=en` to swap a default `version="ja"` from the file.
    """
    vars: dict[str, str] = {}
    for k in ("name", "version", "revision"):
        if k in build_attrs:
            vars[k] = build_attrs[k]
    if defines:
        vars.update(defines)
    return vars
