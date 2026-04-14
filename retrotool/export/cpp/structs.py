"""Python dataclasses → C++ header/source."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass

_PY_TO_CPP = {
    int: "int32_t",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u24": "uint32_t",
    "u32": "uint32_t",
    float: "float",
    str: "std::string",
    bool: "bool",
}


def cpp_type(py_type) -> str:
    if isinstance(py_type, str):
        return _PY_TO_CPP.get(py_type, py_type)
    return _PY_TO_CPP.get(py_type, "auto")


@dataclass
class CppStructField:
    name: str
    type: str
    comment: str = ""


@dataclass
class CppStruct:
    name: str
    fields: list[CppStructField] = field(default_factory=list)
    comment: str = ""


def render_header(namespace: str, structs: list[CppStruct], include_guard: str = "ROM_DATA_H") -> str:
    out = [f"#ifndef {include_guard}", f"#define {include_guard}", "",
           "#include <cstdint>", "#include <string>", "#include <vector>", ""]
    out.append(f"namespace {namespace} {{")
    out.append("")
    for s in structs:
        if s.comment:
            out.append(f"// {s.comment}")
        out.append(f"struct {s.name} {{")
        for f in s.fields:
            line = f"    {cpp_type(f.type)} {f.name};"
            if f.comment:
                line += f"   // {f.comment}"
            out.append(line)
        out.append("};")
        out.append("")
    out.append(f"}} // namespace {namespace}")
    out.append("")
    out.append(f"#endif // {include_guard}")
    return '\n'.join(out) + '\n'
