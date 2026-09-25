"""Hosted-CI parity check between the Python and the C# bridge executors.

``nx_mcp.nx_bridge`` and ``nx_gui_bridge/NxMcpGuiBridge.cs`` are two
hand-written implementations of one command contract. Until now only review
kept them in step. This check reads the C# source as text, describes both
bridges as a :class:`BridgeSurface` and reports every difference, so adding a
command to one bridge alone fails hosted CI. It needs neither NX nor .NET.

What the parser assumes about the C# source, which is the contract a refactor
has to keep:

1. ``internal static class Protocol`` declares ``public const int Version``,
   ``public const int MaxMessageBytes`` (an integer, or integers multiplied)
   and ``public static readonly TimeSpan ReadTimeout = TimeSpan.FromSeconds(n)``.
2. The mutation set is a single ``HashSet<string> Mutations`` collection
   initializer holding string literals.
3. Every command is registered in the ``Executor`` constructor by a literal
   ``Add("<command>", <handler>, ...)`` call. A bare string argument is a
   required parameter, ``Opt("<name>", <default>)`` an optional one, and a
   default is a string, ``true``, ``false``, ``null`` or a number.
4. String literals in those three places are plain: no escape sequences and no
   ``@`` verbatim form.

When a construct is missing, duplicated or unreadable the parser raises
:class:`ParseError` naming it, so a refactor cannot quietly turn this check
into a no-op.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from nx_mcp.bridge import _MAX_MESSAGE_BYTES, BRIDGE_PROTOCOL_VERSION, BridgeServer
from nx_mcp.certified import CERTIFIED_TOOL_NAMES
from nx_mcp.nx_bridge import NXOpenExecutor
from nx_mcp.workspace import Workspace

CSHARP_SOURCE = Path(__file__).resolve().parents[1] / "nx_gui_bridge" / "NxMcpGuiBridge.cs"


class ParseError(Exception):
    """A construct this check has to read is missing, duplicated or unreadable."""


# --- Reading C# text -------------------------------------------------------
#
# Every scan below runs over a masked copy of the source in which the contents
# of comments and literals are spaces. The mask has the same length as the
# source, so an index found in it also points into the source, and no brace,
# parenthesis, comma or quote can hide inside a comment or a literal. The C#
# file does contain such characters, for example the literals '{' and '"'.


def _closing_quote(source: str, start: int, quote: str) -> int:
    """Return the index just past the literal that `quote` closes."""
    index = start
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    raise ParseError(f"the C# source has an unterminated {quote} literal")


def _closing_verbatim_quote(source: str, start: int) -> int:
    """Return the index just past a @"..." literal, in which "" is one quote."""
    index = start
    while index < len(source):
        if source[index] == '"':
            if source.startswith('""', index):
                index += 2
                continue
            return index + 1
        index += 1
    raise ParseError('the C# source has an unterminated @" literal')


def _mask_literals(source: str) -> str:
    """Blank out the contents of comments and literals, keeping their length."""
    masked = list(source)
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            newline = source.find("\n", index)
            body = (index, len(source) if newline < 0 else newline)
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ParseError("the C# source has an unterminated block comment")
            body = (index, end + 2)
        elif source.startswith('@"', index):
            body = (index + 2, _closing_verbatim_quote(source, index + 2) - 1)
        elif source[index] in "\"'":
            body = (index + 1, _closing_quote(source, index + 1, source[index]) - 1)
        else:
            index += 1
            continue
        for position in range(*body):
            if masked[position] != "\n":
                masked[position] = " "
        # Step past the closing delimiter, which the mask keeps in place.
        index = body[1] + 1
    return "".join(masked)


def _only_match(pattern: str, masked: str, what: str) -> re.Match[str]:
    """Return the one match of `pattern`, or fail naming what could not be read."""
    found = re.findall(f"(?={pattern})", masked)
    if len(found) != 1:
        raise ParseError(f"expected exactly one {what} in the C# source, found {len(found)}")
    match = re.search(pattern, masked)
    assert match is not None
    return match


_BRACKETS = {"{": "}", "(": ")"}


def _group(masked: str, start: int, opener: str) -> tuple[int, int]:
    """Return the span inside the `opener` group that starts at or after `start`."""
    first = masked.find(opener, start)
    if first < 0:
        raise ParseError(f"the C# source has no '{opener}' after offset {start}")
    closer = _BRACKETS[opener]
    depth = 0
    for index in range(first, len(masked)):
        if masked[index] == opener:
            depth += 1
        elif masked[index] == closer:
            depth -= 1
            if depth == 0:
                return first + 1, index
    raise ParseError(f"the C# source has an unterminated '{opener}' at offset {first}")


def _arguments(masked: str, span: tuple[int, int]) -> list[tuple[int, int]]:
    """Split a span on the commas that sit at its own nesting level."""
    start, end = span
    spans = []
    depth = 0
    piece = start
    for index in range(start, end):
        if masked[index] in "({[":
            depth += 1
        elif masked[index] in ")}]":
            depth -= 1
        elif masked[index] == "," and depth == 0:
            spans.append((piece, index))
            piece = index + 1
    spans.append((piece, end))
    return [(a, b) for a, b in spans if masked[a:b].strip()]


_PLAIN_STRING = re.compile(r'^"([^"\\]*)"$')
_CONSTANTS: dict[str, Any] = {"null": None, "true": True, "false": False}


def _value(text: str, what: str) -> Any:
    """Read one C# literal: a plain string, null, true, false or a number."""
    text = text.strip()
    if text in _CONSTANTS:
        return _CONSTANTS[text]
    string = _PLAIN_STRING.match(text)
    if string is not None:
        return string.group(1)
    for number in (int, float):
        try:
            return number(text)
        except ValueError:
            continue
    raise ParseError(f"cannot read {what} as a literal: {text!r}")


def _string(text: str, what: str) -> str:
    value = _value(text, what)
    if not isinstance(value, str):
        raise ParseError(f"expected {what} to be a string, found {value!r}")
    return value


def _integer(text: str, what: str) -> int:
    """Read an integer, or integers multiplied together such as `1024 * 1024`."""
    product = 1
    for factor in text.split("*"):
        try:
            product *= int(factor.strip())
        except ValueError as error:
            raise ParseError(f"cannot read {what} as an integer: {text.strip()!r}") from error
    return product


# --- The two bridge surfaces -----------------------------------------------


@dataclass(frozen=True)
class Command:
    """One bridge command: the parameters it demands and the ones it defaults.

    Both are ordered by name, because the bridge passes parameters by name and
    the order they are declared in carries no meaning.
    """

    required: tuple[str, ...]
    optional: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class BridgeSurface:
    """What one bridge promises, as far as this check can read it."""

    commands: dict[str, Command]
    mutations: frozenset[str]
    protocol_version: int
    max_message_bytes: int
    receive_deadline: float


def _command(required: list[str], optional: list[tuple[str, Any]]) -> Command:
    return Command(
        required=tuple(sorted(required)),
        optional=tuple(sorted(optional, key=lambda option: option[0])),
    )


def python_surface() -> BridgeSurface:
    """Describe the Python bridge by introspecting its command handlers."""
    # The constructor only records its arguments and builds the handler table,
    # so it runs without NX: nothing here reaches NXOpen.
    executor = NXOpenExecutor(None, None, "", Workspace("."))
    commands = {}
    for name, handler in executor._handlers.items():
        required: list[str] = []
        optional: list[tuple[str, Any]] = []
        for parameter in inspect.signature(handler).parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                raise ParseError(f"{name} takes *args or **kwargs, so its parameters are unknown")
            if parameter.default is parameter.empty:
                required.append(parameter.name)
            else:
                optional.append((parameter.name, parameter.default))
        commands[name] = _command(required, optional)
    deadline = inspect.signature(BridgeServer).parameters["read_timeout"].default
    if not isinstance(deadline, (int, float)):
        raise ParseError("BridgeServer.read_timeout has no numeric default in nx_mcp.bridge")
    return BridgeSurface(
        commands=commands,
        mutations=frozenset(NXOpenExecutor._MODEL_MUTATIONS),
        protocol_version=BRIDGE_PROTOCOL_VERSION,
        max_message_bytes=_MAX_MESSAGE_BYTES,
        receive_deadline=float(deadline),
    )


def _csharp_protocol(masked: str) -> tuple[int, int, float]:
    header = _only_match(r"internal static class Protocol\b", masked, "Protocol class")
    body = masked[slice(*_group(masked, header.end(), "{"))]
    version = _only_match(r"const int Version\s*=\s*([^;]+);", body, "Protocol.Version")
    limit = _only_match(
        r"const int MaxMessageBytes\s*=\s*([^;]+);", body, "Protocol.MaxMessageBytes"
    )
    deadline = _only_match(
        r"TimeSpan ReadTimeout\s*=\s*TimeSpan\.FromSeconds\(([^)]*)\)\s*;",
        body,
        "Protocol.ReadTimeout",
    )
    return (
        _integer(version.group(1), "Protocol.Version"),
        _integer(limit.group(1), "Protocol.MaxMessageBytes"),
        float(_value(deadline.group(1), "Protocol.ReadTimeout")),
    )


def _csharp_mutations(source: str, masked: str) -> frozenset[str]:
    header = _only_match(
        r"HashSet<string>\s+Mutations\s*=\s*new\s+HashSet<string>",
        masked,
        "Mutations collection initializer",
    )
    span = _group(masked, header.end(), "{")
    return frozenset(_string(source[a:b], "a Mutations entry") for a, b in _arguments(masked, span))


def _csharp_commands(source: str, masked: str) -> dict[str, Command]:
    header = _only_match(r"public Executor\s*\(", masked, "Executor constructor")
    body_start, body_end = _group(masked, header.end(), "{")
    body, body_mask = source[body_start:body_end], masked[body_start:body_end]
    commands: dict[str, Command] = {}
    # `Add` registers a command; `something.Add(...)` on a collection does not.
    for call in re.finditer(r"(?<![\w.])Add\s*\(", body_mask):
        arguments = _arguments(body_mask, _group(body_mask, call.start(), "("))
        if len(arguments) < 2:
            raise ParseError(f"an Add(...) call takes no handler: {body[call.start() :][:60]!r}")
        name = _string(body[slice(*arguments[0])], "a command name")
        if name in commands:
            raise ParseError(f"command {name} is registered twice in the C# source")
        required: list[str] = []
        optional: list[tuple[str, Any]] = []
        # Argument 1 is the handler, which this check does not read.
        for span in arguments[2:]:
            text = body[slice(*span)].strip()
            if not text.startswith("Opt("):
                required.append(_string(text, f"a required parameter of {name}"))
                continue
            pair = _arguments(body_mask, _group(body_mask, span[0], "("))
            if len(pair) != 2:
                raise ParseError(f"{name} has an Opt(...) that is not a name and a default")
            option = _string(body[slice(*pair[0])], f"an optional parameter name of {name}")
            default = _value(body[slice(*pair[1])], f"the default of {name}.{option}")
            optional.append((option, default))
        commands[name] = _command(required, optional)
    if not commands:
        raise ParseError("the Executor constructor registers no commands in the C# source")
    return commands


def csharp_surface(source: str) -> BridgeSurface:
    """Describe the C# bridge by reading its source text."""
    masked = _mask_literals(source)
    version, limit, deadline = _csharp_protocol(masked)
    return BridgeSurface(
        commands=_csharp_commands(source, masked),
        mutations=_csharp_mutations(source, masked),
        protocol_version=version,
        max_message_bytes=limit,
        receive_deadline=deadline,
    )


# --- Comparing them --------------------------------------------------------


def drift(python: BridgeSurface, csharp: BridgeSurface) -> list[str]:
    """Describe every way the two bridges disagree."""
    report = []
    for name in sorted(set(python.commands) - set(csharp.commands)):
        report.append(f"command {name} exists only in the Python bridge")
    for name in sorted(set(csharp.commands) - set(python.commands)):
        report.append(f"command {name} exists only in the C# bridge")
    for name in sorted(set(python.commands) & set(csharp.commands)):
        mine, theirs = python.commands[name], csharp.commands[name]
        if mine.required != theirs.required:
            report.append(
                f"{name} requires {list(mine.required)} in Python and {list(theirs.required)} in C#"
            )
        if mine.optional != theirs.optional:
            report.append(
                f"{name} defaults to {dict(mine.optional)} in Python "
                f"and {dict(theirs.optional)} in C#"
            )
    for name in sorted(python.mutations ^ csharp.mutations):
        bridge = "Python" if name in python.mutations else "C#"
        report.append(f"{name} is a model mutation only in the {bridge} bridge")
    for label, mine, theirs in (
        ("protocol version", python.protocol_version, csharp.protocol_version),
        ("maximum message bytes", python.max_message_bytes, csharp.max_message_bytes),
        ("receive deadline in seconds", python.receive_deadline, csharp.receive_deadline),
    ):
        if mine != theirs:
            report.append(f"{label} is {mine} in Python and {theirs} in C#")
    return report


# --- The check itself ------------------------------------------------------


@pytest.fixture(scope="module")
def source() -> str:
    return CSHARP_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def python_bridge() -> BridgeSurface:
    return python_surface()


@pytest.fixture(scope="module")
def csharp_bridge(source: str) -> BridgeSurface:
    return csharp_surface(source)


def test_the_bridges_agree_on_every_command(python_bridge, csharp_bridge):
    assert drift(python_bridge, csharp_bridge) == []


def test_both_bridges_implement_the_certified_tools(python_bridge, csharp_bridge):
    assert set(python_bridge.commands) == CERTIFIED_TOOL_NAMES
    assert set(csharp_bridge.commands) == CERTIFIED_TOOL_NAMES


def test_every_mutation_is_a_registered_command(python_bridge, csharp_bridge):
    assert python_bridge.mutations <= set(python_bridge.commands)
    assert csharp_bridge.mutations <= set(csharp_bridge.commands)


def _edit(source: str, before: str, after: str) -> str:
    """Rewrite the C# source once, failing if the anchor is no longer there."""
    if source.count(before) != 1:
        raise AssertionError(f"this test's anchor is not in the C# source exactly once: {before!r}")
    return source.replace(before, after)


DRIFT_CASES = [
    pytest.param(
        'Add("nx_fit_view", FitView);',
        "",
        ["command nx_fit_view exists only in the Python bridge"],
        id="command-removed-from-csharp",
    ),
    pytest.param(
        'Add("nx_undo", Undo);',
        'Add("nx_undo", Undo);\n            Add("nx_probe", Undo);',
        ["command nx_probe exists only in the C# bridge"],
        id="command-added-to-csharp-only",
    ),
    pytest.param(
        'Add("nx_open_part", OpenPart, "path");',
        'Add("nx_open_part", OpenPart);',
        ["nx_open_part requires ['path'] in Python and [] in C#"],
        id="required-parameter-dropped",
    ),
    pytest.param(
        'Opt("units", "mm")',
        'Opt("units", "inch")',
        ["nx_create_part defaults to {'units': 'mm'} in Python and {'units': 'inch'} in C#"],
        id="optional-default-changed",
    ),
    pytest.param(
        '"nx_finish_sketch", "nx_extrude",',
        '"nx_finish_sketch",',
        ["nx_extrude is a model mutation only in the Python bridge"],
        id="mutation-removed",
    ),
    pytest.param(
        "public const int Version = 1;",
        "public const int Version = 2;",
        ["protocol version is 1 in Python and 2 in C#"],
        id="protocol-version-changed",
    ),
    pytest.param(
        "public const int MaxMessageBytes = 1024 * 1024;",
        "public const int MaxMessageBytes = 4 * 1024 * 1024;",
        ["maximum message bytes is 1048576 in Python and 4194304 in C#"],
        id="message-limit-changed",
    ),
    pytest.param(
        "TimeSpan ReadTimeout = TimeSpan.FromSeconds(5);",
        "TimeSpan ReadTimeout = TimeSpan.FromSeconds(30);",
        ["receive deadline in seconds is 5.0 in Python and 30.0 in C#"],
        id="receive-deadline-changed",
    ),
]


@pytest.mark.parametrize(("before", "after", "expected"), DRIFT_CASES)
def test_drift_in_the_csharp_bridge_is_reported(python_bridge, source, before, after, expected):
    assert drift(python_bridge, csharp_surface(_edit(source, before, after))) == expected


def test_a_commented_out_registration_is_not_a_command(python_bridge, source):
    """Masking earns its keep: a parked `Add(...)` must not register a command."""
    parked = 'Add("nx_undo", Undo);\n            // Add("nx_probe", Undo);'
    doctored = _edit(source, 'Add("nx_undo", Undo);', parked)
    assert drift(python_bridge, csharp_surface(doctored)) == []


def test_drift_in_the_python_bridge_is_reported(python_bridge, csharp_bridge):
    without_undo = {
        name: command for name, command in python_bridge.commands.items() if name != "nx_undo"
    }
    assert drift(replace(python_bridge, commands=without_undo), csharp_bridge) == [
        "command nx_undo exists only in the C# bridge"
    ]
    assert drift(replace(python_bridge, receive_deadline=9.0), csharp_bridge) == [
        "receive deadline in seconds is 9.0 in Python and 5.0 in C#"
    ]
    assert drift(
        replace(python_bridge, mutations=python_bridge.mutations | {"nx_fit_view"}), csharp_bridge
    ) == ["nx_fit_view is a model mutation only in the Python bridge"]


PARSE_FAILURES = [
    pytest.param("public const int Version = 1;", "", "Protocol.Version", id="version-missing"),
    pytest.param(
        "TimeSpan ReadTimeout = TimeSpan.FromSeconds(5);",
        "",
        "Protocol.ReadTimeout",
        id="deadline-missing",
    ),
    pytest.param(
        "public const int MaxMessageBytes = 1024 * 1024;",
        "public const int MaxMessageBytes = 1024 * Kilobyte;",
        "cannot read Protocol.MaxMessageBytes as an integer",
        id="message-limit-not-a-number",
    ),
    pytest.param(
        "internal static class Protocol",
        "internal static class Wire",
        "Protocol class",
        id="protocol-class-renamed",
    ),
    pytest.param(
        "HashSet<string> Mutations = new HashSet<string>",
        "HashSet<string> Mutated = new HashSet<string>",
        "Mutations collection initializer",
        id="mutation-set-renamed",
    ),
    pytest.param(
        "public Executor(Session session, string nxVersion, Workspace workspace)",
        "public Executor(Session session, string nxVersion, Workspace workspace)"
        "\n        {\n        }\n\n        private void Register()",
        "the Executor constructor registers no commands",
        id="commands-moved-out-of-the-constructor",
    ),
    pytest.param(
        'Add("nx_status", Status);',
        "Add(StatusCommandName, Status);",
        "cannot read a command name as a literal",
        id="command-name-not-a-literal",
    ),
    pytest.param(
        'Opt("units", "mm")',
        'Opt("units", Defaults.Units)',
        "cannot read the default of nx_create_part.units",
        id="default-not-a-literal",
    ),
    pytest.param(
        'Add("nx_undo", Undo);',
        'Add("nx_undo", Undo);\n            Add("nx_undo", Undo);',
        "command nx_undo is registered twice",
        id="command-registered-twice",
    ),
    pytest.param(
        'Add("nx_save_part", SavePart);',
        'Add("nx_save_part");',
        "takes no handler",
        id="registration-without-a-handler",
    ),
]


@pytest.mark.parametrize(("before", "after", "message"), PARSE_FAILURES)
def test_unreadable_csharp_source_fails_loudly(source, before, after, message):
    with pytest.raises(ParseError, match=re.escape(message)):
        csharp_surface(_edit(source, before, after))
