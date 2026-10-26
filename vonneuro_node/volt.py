"""A small VOLT 0.1 parser and VNG compiler for device-evolution programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LANGUAGE_VERSION = "0.1"
_TOKEN_PATTERN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<comment>//[^\n]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\")"
    r"|(?P<number>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<symbol>[{}():;,.=\[\]])"
)


class VoltError(ValueError):
    """A VOLT source or semantic validation error."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    offset: int


@dataclass(frozen=True)
class Parameter:
    name: str
    value_type: str


@dataclass(frozen=True)
class State:
    name: str
    state_type: str


@dataclass(frozen=True)
class Evolve:
    state: str
    duration: dict[str, int | float | str]
    action: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    name: str
    value_type: str
    selector: str


@dataclass(frozen=True)
class VoltProgram:
    name: str
    parameters: tuple[Parameter, ...]
    states: tuple[State, ...]
    evolve: Evolve
    observations: tuple[Observation, ...]
    return_value: str
    contracts: dict[str, dict[str, Any]]


def _tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    offset = 0
    while offset < len(source):
        match = _TOKEN_PATTERN.match(source, offset)
        if match is None:
            line = source.count("\n", 0, offset) + 1
            last_newline = source.rfind("\n", 0, offset)
            column = offset - last_newline
            raise VoltError(f"line {line}, column {column}: unexpected character")
        kind = match.lastgroup
        value = match.group()
        if kind not in {"space", "comment"}:
            tokens.append(Token(kind or "", value, offset))
        offset = match.end()
    tokens.append(Token("eof", "", len(source)))
    return tokens


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.tokens = _tokenize(source)
        self.index = 0

    def _peek(self, value: str | None = None) -> bool:
        token = self.tokens[self.index]
        return token.value == value if value is not None else token.kind != "eof"

    def _take(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, value: str) -> Token:
        token = self.tokens[self.index]
        if token.value != value:
            self._error(f"expected {value!r}, found {token.value or 'end of file'!r}")
        return self._take()

    def _identifier(self) -> str:
        token = self.tokens[self.index]
        if token.kind != "identifier":
            self._error(f"expected an identifier, found {token.value or 'end of file'!r}")
        return self._take().value

    def _qualified_name(self) -> str:
        parts = [self._identifier()]
        while self._peek("."):
            self._take()
            parts.append(self._identifier())
        return ".".join(parts)

    def _error(self, message: str) -> None:
        token = self.tokens[self.index]
        line = self.source.count("\n", 0, token.offset) + 1
        last_newline = self.source.rfind("\n", 0, token.offset)
        column = token.offset - last_newline
        raise VoltError(f"line {line}, column {column}: {message}")

    def parse(self) -> VoltProgram:
        if self._take().value != "system":
            self.index -= 1
            self._error("VOLT 0.1 requires a top-level 'system' declaration")
        name = self._identifier()
        self._expect("(")
        parameters = self._parameters()
        self._expect(")")
        self._expect("{")

        states: list[State] = []
        evolve: Evolve | None = None
        observations: list[Observation] = []
        return_value: str | None = None
        contracts: dict[str, dict[str, Any]] = {}

        while not self._peek("}"):
            if not self._peek():
                self._error("expected '}' before end of file")
            if self._peek("state"):
                state = self._state()
                if any(existing.name == state.name for existing in states):
                    self._error(f"duplicate state {state.name!r}")
                states.append(state)
            elif self._peek("evolve"):
                if evolve is not None:
                    self._error("VOLT 0.1 supports one evolve statement per system")
                evolve = self._evolve()
            elif self._peek("observe"):
                observations.append(self._observe())
            elif self._peek("return"):
                if return_value is not None:
                    self._error("duplicate return statement")
                self._take()
                return_value = self._identifier()
                self._expect(";")
            elif self.tokens[self.index].value in {
                "execution",
                "device",
                "thermodynamics",
            }:
                block_name = self._take().value
                if block_name in contracts:
                    self._error(f"duplicate {block_name!r} contract block")
                contracts[block_name] = self._contract_block()
            else:
                self._error("expected state, evolve, observe, return, or a contract block")

        self._expect("}")
        if self._peek():
            self._error("unexpected content after system declaration")
        if evolve is None:
            self._error("system must contain one evolve statement")
        if return_value is None:
            self._error("system must return an observation")
        if not states:
            self._error("system must declare at least one state")
        if set(contracts) != {"execution", "device", "thermodynamics"}:
            missing = {"execution", "device", "thermodynamics"} - contracts.keys()
            self._error(f"missing contract blocks: {', '.join(sorted(missing))}")

        symbols = {parameter.name for parameter in parameters}
        state_names = {state.name for state in states}
        duplicate_state_names = symbols & state_names
        if duplicate_state_names:
            self._error(f"duplicate symbol {sorted(duplicate_state_names)[0]!r}")
        symbols.update(state_names)
        for observation in observations:
            if observation.name in symbols:
                self._error(f"duplicate symbol {observation.name!r}")
            symbols.add(observation.name)
        if evolve.state not in {state.name for state in states}:
            self._error(f"evolve references unknown state {evolve.state!r}")
        for input_name in evolve.inputs:
            if input_name not in {parameter.name for parameter in parameters}:
                self._error(f"evolve references unknown input {input_name!r}")
        if return_value not in {observation.name for observation in observations}:
            self._error("return must name a declared observation")
        for observation in observations:
            source_state = observation.selector.split(".", maxsplit=1)[0]
            if source_state not in {state.name for state in states}:
                self._error(
                    f"observation selector references unknown state {source_state!r}"
                )

        if contracts["execution"].get("intent") != "evolve":
            self._error("execution.intent must be 'evolve' in VOLT 0.1")
        return VoltProgram(
            name=name,
            parameters=tuple(parameters),
            states=tuple(states),
            evolve=evolve,
            observations=tuple(observations),
            return_value=return_value,
            contracts=contracts,
        )

    def _parameters(self) -> list[Parameter]:
        parameters: list[Parameter] = []
        if self._peek(")"):
            return parameters
        while True:
            name = self._identifier()
            self._expect(":")
            value_type = self._qualified_name()
            if any(parameter.name == name for parameter in parameters):
                self._error(f"duplicate parameter {name!r}")
            parameters.append(Parameter(name, value_type))
            if not self._peek(","):
                return parameters
            self._take()

    def _state(self) -> State:
        self._take()
        name = self._identifier()
        self._expect(":")
        state_type = self._qualified_name()
        self._expect(";")
        return State(name, state_type)

    def _evolve(self) -> Evolve:
        self._take()
        state = self._identifier()
        self._expect("for")
        duration_token = self.tokens[self.index]
        if duration_token.kind != "number":
            self._error("evolve duration must be a positive number and unit")
        duration_number = float(self._take().value)
        if duration_number <= 0:
            self._error("evolve duration must be positive")
        unit = self._identifier()
        self._expect("using")
        action = self._qualified_name()
        self._expect("(")
        inputs: list[str] = []
        if not self._peek(")"):
            while True:
                inputs.append(self._identifier())
                if not self._peek(","):
                    break
                self._take()
        self._expect(")")
        self._expect(";")
        number: int | float = (
            int(duration_number) if duration_number.is_integer() else duration_number
        )
        return Evolve(state, {"value": number, "unit": unit}, action, tuple(inputs))

    def _observe(self) -> Observation:
        self._take()
        name = self._identifier()
        self._expect(":")
        value_type = self._qualified_name()
        self._expect("from")
        selector = self._qualified_name()
        self._expect(";")
        return Observation(name, value_type, selector)

    def _contract_block(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self._expect("{")
        while not self._peek("}"):
            if not self._peek():
                self._error("expected '}' before end of contract block")
            key = self._identifier()
            if key in result:
                self._error(f"duplicate contract field {key!r}")
            self._expect("=")
            result[key] = self._value()
            self._expect(";")
        self._expect("}")
        return result

    def _value(self) -> Any:
        token = self.tokens[self.index]
        if token.kind == "string":
            return json.loads(self._take().value)
        if token.kind == "number":
            number = float(self._take().value)
            value: int | float = int(number) if number.is_integer() else number
            if self.tokens[self.index].kind == "identifier":
                return {"value": value, "unit": self._take().value}
            return value
        if token.kind == "identifier":
            value = self._qualified_name()
            if value == "true":
                return True
            if value == "false":
                return False
            return value
        if self._peek("["):
            self._take()
            values: list[Any] = []
            if not self._peek("]"):
                while True:
                    values.append(self._value())
                    if not self._peek(","):
                        break
                    self._take()
            self._expect("]")
            return values
        self._error(
            "contract values must be strings, numbers, booleans, identifiers, or lists"
        )


def parse(source: str):
    """Parse a VOLT system program or stateless processing program."""
    if re.match(r"\s*process\b", re.sub(r"//[^\n]*", "", source)):
        from .processing import parse_process
        return parse_process(source)
    return _Parser(source).parse()


def compile_source(source: str) -> dict[str, Any]:
    """Compile VOLT source into a deterministic, device-independent VNG object."""
    program = parse(source)
    if hasattr(program, "graph"):
        return program.graph()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    for parameter in program.parameters:
        nodes.append(
            {
                "id": f"input:{parameter.name}",
                "kind": "input",
                "name": parameter.name,
                "type": parameter.value_type,
            }
        )
    for state in program.states:
        nodes.append(
            {
                "id": f"state:{state.name}",
                "kind": "state",
                "name": state.name,
                "type": state.state_type,
                "residency": "domain",
            }
        )

    evolve_id = "operation:evolve:0"
    nodes.append(
        {
            "id": evolve_id,
            "kind": "operation",
            "intent": "evolve",
            "action": program.evolve.action,
            "state": program.evolve.state,
            "duration": program.evolve.duration,
            "inputs": list(program.evolve.inputs),
        }
    )
    edges.append(
        {"from": f"state:{program.evolve.state}", "to": evolve_id, "kind": "state"}
    )
    for input_name in program.evolve.inputs:
        edges.append(
            {"from": f"input:{input_name}", "to": evolve_id, "kind": "control"}
        )

    for observation in program.observations:
        observation_id = f"observation:{observation.name}"
        nodes.append(
            {
                "id": observation_id,
                "kind": "observation",
                "intent": "observe",
                "name": observation.name,
                "type": observation.value_type,
                "selector": observation.selector,
            }
        )
        edges.append(
            {"from": evolve_id, "to": observation_id, "kind": "sequence"}
        )
        edges.append(
            {
                "from": f"state:{observation.selector.split('.', maxsplit=1)[0]}",
                "to": observation_id,
                "kind": "observation",
            }
        )

    return_id = "result:0"
    nodes.append({"id": return_id, "kind": "result", "value": program.return_value})
    edges.append(
        {
            "from": f"observation:{program.return_value}",
            "to": return_id,
            "kind": "result",
        }
    )

    return {
        "schema": "vonNeuro.VNG",
        "schema_version": 1,
        "language": {"name": "VOLT", "version": LANGUAGE_VERSION},
        "program": program.name,
        "nodes": nodes,
        "edges": edges,
        "contracts": program.contracts,
        "provenance": {"source_sha256": hashlib.sha256(source.encode()).hexdigest()},
    }


def _main(argv: list[str] | None = None) -> int:
    argument_parser = argparse.ArgumentParser(
        description="Compile VOLT 0.1 source to VNG JSON"
    )
    argument_parser.add_argument("source", type=Path, help="VOLT source file")
    argument_parser.add_argument("-o", "--output", type=Path, help="write VNG JSON here")
    args = argument_parser.parse_args(argv)
    try:
        source = args.source.read_text(encoding="utf-8")
        graph = compile_source(source)
    except (OSError, ValueError) as error:
        print(f"volt: error: {error}", file=sys.stderr)
        return 1

    output = json.dumps(graph, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())