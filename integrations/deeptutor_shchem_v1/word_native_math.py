"""Bounded, loss-intolerant reading of an explicitly supported OMML subset.

``omml_text`` accepts one complete ``m:oMath`` or ``m:oMathPara`` element,
using either ElementTree or lxml. It reads existing Unicode text, never guesses
chemistry, evaluates expressions, resolves styles, or opens external resources.
Scripts use ``base_{sub}^{sup}`` with braces around compound bases; fractions
use ``\\frac{num}{den}``; radicals use ``\\sqrt{base}`` or
``\\sqrt[degree]{base}``. Limits retain their above/below placement through
``\\overset{limit}{base}`` and ``\\underset{limit}{base}``. Delimiters remain
literal Unicode characters. Separate paragraph equations are newline separated.

This is a readable structural transcription, not a general TeX serializer or
an OOXML validator. Unknown nodes/attributes, unresolved styles, hidden/deleted
content, malformed operands, and exceeded bounds reject the WHOLE object.
Only the explicitly enumerated presentation properties below are discarded.
The caller must still check ancestors and document styles for hidden content:
an ElementTree subtree cannot reveal properties outside that subtree.
"""

from __future__ import annotations

import re
from typing import Any

_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML = "{http://www.w3.org/XML/1998/namespace}"

MAX_MATH_ELEMENTS = 4096
MAX_MATH_DEPTH = 64
MAX_MATH_TEXT = 32768
MAX_MATH_OUTPUT = 65536
_MAX_ATTRIBUTES = 32

_TRUE = {"1", "true", "on"}
_FALSE = {"0", "false", "off"}
_BOOL = _TRUE | _FALSE
_OPEN_DELIMITERS = {"", "(", "[", "{", "|", "‖", "⟨", "⌊", "⌈"}
_CLOSE_DELIMITERS = {"", ")", "]", "}", "|", "‖", "⟩", "⌋", "⌉"}
_SEPARATORS = {"", "|", "‖", ",", ";"}
_FONTS = {
    "arial",
    "calibri",
    "cambria",
    "cambria math",
    "times new roman",
    "aptos",
    "aptos display",
    "simsun",
    "simhei",
    "microsoft yahei",
    "宋体",
    "黑体",
    "微软雅黑",
    "等线",
    "dengxian",
}
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_SCRIPT_ATOM = re.compile(r"[^\W_]+\Z", re.UNICODE)
_EXPRESSIONS = {
    _M + name
    for name in (
        "r",
        "sSub",
        "sSup",
        "sSubSup",
        "f",
        "rad",
        "limLow",
        "limUpp",
        "d",
    )
}


class _UnsupportedMath(ValueError):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise _UnsupportedMath


def _attributes(node: Any, allowed: set[str] | frozenset[str] = frozenset()) -> None:
    _require(set(node.attrib).issubset(allowed))


def _preflight(root: Any) -> None:
    """Bound the entire tree before recursive rendering, including metadata."""
    pending = [(root, 0)]
    # Keep the proxies alive: lxml can otherwise reuse Python object ids for
    # different XML nodes after a previous proxy has been garbage collected.
    seen: dict[int, Any] = {}
    characters = 0
    while pending:
        node, depth = pending.pop()
        _require(depth <= MAX_MATH_DEPTH and id(node) not in seen)
        seen[id(node)] = node
        _require(len(seen) <= MAX_MATH_ELEMENTS)
        _require(isinstance(node.tag, str))
        _require(node.tag.startswith((_M, _W)))
        _require(len(node.attrib) <= _MAX_ATTRIBUTES)
        for key, value in node.attrib.items():
            _require(isinstance(key, str) and isinstance(value, str))
            characters += len(key) + len(value)
        for value in (node.text, node.tail if node is not root else None):
            _require(value is None or isinstance(value, str))
            characters += len(value or "")
        _require(characters <= MAX_MATH_TEXT)
        if node is not root:
            _require(not (node.tail or "").strip())
        if node.tag != _M + "t":
            _require(not (node.text or "").strip())
        # Never materialize an unbounded list of children on a wide input.
        _require(len(node) + len(pending) <= MAX_MATH_ELEMENTS - len(seen))
        pending.extend((child, depth + 1) for child in reversed(node))


def _leaf_value(
    node: Any,
    values: set[str] | frozenset[str] | None = None,
    *,
    default: str | None = None,
    namespace: str = _M,
) -> str:
    _attributes(node, {namespace + "val"})
    _require(len(node) == 0)
    value = node.get(namespace + "val", default)
    _require(isinstance(value, str))
    if values is not None:
        _require(value in values)
    return value


def _word_properties(node: Any) -> None:
    """Accept ordinary explicit run appearance, never inherited/hidden styles."""
    _attributes(node)
    seen: set[str] = set()
    for child in node:
        tag = child.tag
        _require(tag not in seen and tag.startswith(_W))
        seen.add(tag)
        name = tag[len(_W) :]
        if name in {"b", "bCs", "i", "iCs", "noProof"}:
            _leaf_value(child, _BOOL, default="true", namespace=_W)
        elif name in {"sz", "szCs", "kern", "spacing"}:
            value = _leaf_value(child, namespace=_W)
            _require(re.fullmatch(r"-?\d{1,6}", value) is not None)
            _require(name == "spacing" or int(value) >= 0)
        elif name == "color":
            _attributes(
                child,
                {
                    _W + value
                    for value in ("val", "themeColor", "themeTint", "themeShade")
                },
            )
            _require(len(child) == 0 and child.get(_W + "val") is not None)
            _require(
                re.fullmatch(r"auto|[0-9a-fA-F]{6}", child.get(_W + "val")) is not None
            )
            for attr in ("themeTint", "themeShade"):
                if child.get(_W + attr) is not None:
                    _require(
                        re.fullmatch(r"[0-9a-fA-F]{2}", child.get(_W + attr))
                        is not None
                    )
            if child.get(_W + "themeColor") is not None:
                _require(
                    child.get(_W + "themeColor")
                    in {
                        "dark1",
                        "light1",
                        "dark2",
                        "light2",
                        "accent1",
                        "accent2",
                        "accent3",
                        "accent4",
                        "accent5",
                        "accent6",
                        "hyperlink",
                        "followedHyperlink",
                        "none",
                        "background1",
                        "text1",
                        "background2",
                        "text2",
                    }
                )
        elif name == "lang":
            _attributes(child, {_W + "val", _W + "eastAsia", _W + "bidi"})
            _require(len(child) == 0 and bool(child.attrib))
            _require(
                all(
                    re.fullmatch(r"[A-Za-z0-9-]{1,35}", value)
                    for value in child.attrib.values()
                )
            )
        elif name == "rFonts":
            # Theme references and symbol fonts need outside context or glyph
            # mapping. Explicit familiar Unicode fonts are safe presentation.
            _attributes(
                child,
                {_W + key for key in ("ascii", "hAnsi", "eastAsia", "cs", "hint")},
            )
            _require(len(child) == 0)
            for key, value in child.attrib.items():
                if key == _W + "hint":
                    _require(value in {"default", "eastAsia", "cs"})
                else:
                    _require(value.casefold() in _FONTS)
        else:
            # Includes vanish, specVanish, webHidden, revisions, strike,
            # rStyle, position, vertAlign and unknown extensions.
            raise _UnsupportedMath


def _properties(node: Any) -> dict[str, str]:
    _attributes(node)
    name = node.tag[len(_M) :]
    allowed = {
        "rPr": {"lit", "nor", "scr", "sty", "aln", "brk"},
        "argPr": {"argSz"},
        "oMathParaPr": {"jc"},
        "sSubPr": {"ctrlPr"},
        "sSupPr": {"ctrlPr"},
        "sSubSupPr": {"alnScr", "ctrlPr"},
        "fPr": {"type", "ctrlPr"},
        "radPr": {"degHide", "ctrlPr"},
        "dPr": {"begChr", "sepChr", "endChr", "grow", "shp", "ctrlPr"},
        "limLowPr": {"ctrlPr"},
        "limUppPr": {"ctrlPr"},
    }.get(name)
    _require(allowed is not None)
    result: dict[str, str] = {}
    for child in node:
        _require(child.tag.startswith(_M))
        child_name = child.tag[len(_M) :]
        _require(child_name in allowed and child_name not in result)
        if child_name == "ctrlPr":
            _attributes(child)
            _require(len(child) <= 1)
            for appearance in child:
                _require(appearance.tag == _W + "rPr")
                _word_properties(appearance)
            value = ""
        elif child_name in {"lit", "nor", "aln", "alnScr", "degHide", "grow"}:
            value = _leaf_value(child, _BOOL, default="true")
        elif child_name == "scr":
            # Alternate mathematical alphabets can distinguish variables.
            value = _leaf_value(child, {"roman"})
        elif child_name == "sty":
            value = _leaf_value(child, {"p", "b", "i", "bi"})
        elif child_name == "argSz":
            value = _leaf_value(child, {"-2", "-1", "0", "1", "2"})
        elif child_name == "jc":
            value = _leaf_value(child, {"center", "centerGroup", "left", "right"})
        elif child_name == "type":
            # noBar means a stack, not a fraction. Do not invent division.
            value = _leaf_value(child, {"bar", "skw", "lin"})
        elif child_name == "shp":
            value = _leaf_value(child, {"centered", "match"})
        elif child_name in {"begChr", "endChr", "sepChr"}:
            values = {
                "begChr": _OPEN_DELIMITERS,
                "endChr": _CLOSE_DELIMITERS,
                "sepChr": _SEPARATORS,
            }[child_name]
            value = _leaf_value(child, values)
        elif child_name == "brk":
            _attributes(child, {_M + "alnAt"})
            _require(len(child) == 0)
            value = child.get(_M + "alnAt", "1")
            _require(re.fullmatch(r"[1-9]\d{0,5}", value) is not None)
        else:
            raise _UnsupportedMath
        result[child_name] = value
    return result


def _parts(
    node: Any, property_name: str | None = None
) -> tuple[dict[str, str], list[Any]]:
    _attributes(node)
    children = list(node)
    properties: dict[str, str] = {}
    if children and property_name and children[0].tag == _M + property_name:
        properties = _properties(children.pop(0))
    return properties, children


def _argument(node: Any, *, empty: bool = False) -> str:
    _, children = _parts(node, "argPr")
    _require(all(child.tag in _EXPRESSIONS | {_M + "oMath"} for child in children))
    result = "".join(_render(child) for child in children)
    _require(empty or bool(result.strip()))
    return result


def _operands(
    node: Any, name: str, required: tuple[str, ...]
) -> tuple[dict[str, str], list[Any]]:
    properties, children = _parts(node, name + "Pr")
    _require([child.tag for child in children] == [_M + name for name in required])
    return properties, children


def _base_group(value: str) -> str:
    if len(value) == 1 or _SCRIPT_ATOM.fullmatch(value):
        return value
    return "{" + value + "}"


def _render(node: Any) -> str:
    _require(node.tag.startswith(_M))
    name = node.tag[len(_M) :]
    if name == "r":
        _, children = _parts(node, "rPr")
        if children and children[0].tag == _W + "rPr":
            _word_properties(children.pop(0))
        _require(bool(children) and all(child.tag == _M + "t" for child in children))
        fragments: list[str] = []
        for child in children:
            _attributes(child, {_XML + "space"})
            _require(len(child) == 0)
            _require(child.get(_XML + "space", "default") in {"default", "preserve"})
            value = child.text or ""
            _require(
                not any(
                    char in _BIDI_CONTROLS or (ord(char) < 32 and char not in "\t\r\n")
                    for char in value
                )
            )
            fragments.append(value)
        result = "".join(fragments)
        _require(bool(result))
    elif name == "oMath":
        _attributes(node)
        _require(len(node) > 0)
        _require(all(child.tag in _EXPRESSIONS for child in node))
        result = "".join(_render(child) for child in node)
    elif name == "oMathPara":
        _, children = _parts(node, "oMathParaPr")
        _require(
            bool(children) and all(child.tag == _M + "oMath" for child in children)
        )
        result = "\n".join(_render(child) for child in children)
    elif name in {"sSub", "sSup", "sSubSup"}:
        required = {
            "sSub": ("e", "sub"),
            "sSup": ("e", "sup"),
            "sSubSup": ("e", "sub", "sup"),
        }[name]
        _, children = _operands(node, name, required)
        values = [_argument(child) for child in children]
        result = _base_group(values[0])
        if name in {"sSub", "sSubSup"}:
            result += "_{" + values[1] + "}"
        if name in {"sSup", "sSubSup"}:
            result += "^{" + values[-1] + "}"
    elif name == "f":
        _, children = _operands(node, name, ("num", "den"))
        result = (
            "\\frac{" + _argument(children[0]) + "}{" + _argument(children[1]) + "}"
        )
    elif name == "rad":
        properties, children = _operands(node, name, ("deg", "e"))
        hidden = properties.get("degHide", "false") in _TRUE
        degree = _argument(children[0], empty=hidden)
        base = _argument(children[1])
        if hidden:
            # Hidden nonempty degree would lose authored content on export.
            _require(not degree.strip())
            result = "\\sqrt{" + base + "}"
        else:
            result = "\\sqrt[" + degree + "]{" + base + "}"
    elif name in {"limLow", "limUpp"}:
        _, children = _operands(node, name, ("e", "lim"))
        command = "\\underset" if name == "limLow" else "\\overset"
        result = (
            command + "{" + _argument(children[1]) + "}{" + _argument(children[0]) + "}"
        )
    elif name == "d":
        properties, children = _parts(node, "dPr")
        _require(bool(children) and all(child.tag == _M + "e" for child in children))
        result = (
            properties.get("begChr", "(")
            + properties.get("sepChr", "|").join(_argument(child) for child in children)
            + properties.get("endChr", ")")
        )
    else:
        raise _UnsupportedMath
    _require(len(result) <= MAX_MATH_OUTPUT)
    return result


def omml_text(element: Any) -> str | None:
    """Return a complete supported OMML transcription, or ``None`` on doubt.

    Input must be a math object, not a paragraph/document, XML string or partial
    operand. No tree is modified. Bounds cover metadata, text and output as
    well as operators; a malformed or unsupported descendant is never skipped.
    """
    try:
        _require(element.tag in {_M + "oMath", _M + "oMathPara"})
        _preflight(element)
        result = _render(element)
        return result if result.strip() else None
    except (_UnsupportedMath, AttributeError, TypeError, ValueError, RecursionError):
        return None
