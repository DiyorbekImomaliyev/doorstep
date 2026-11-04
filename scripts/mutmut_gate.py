"""Mutation gate for the money path (ADR-023).

Reads the results of the last `mutmut run` and fails if any surviving mutant changes logic.

A survivor is *text-only* (allowed) when the original and mutated functions are identical after
removing the places that only produce human-readable text:

- logger calls (`log.info(...)`, `logger.error(...)`, ...);
- the arguments of a raised exception (its message);
- the placeholder values given to `message(...)`, `note(...)` and `_Verdict.block(...)`; the reason
  code and the `target` keyword stay, because they are logic;
- the label argument of `_per_currency(...)` and `_exactly_one(...)` (used only in error text);
- the type argument of `typing.cast(...)`, which does nothing at runtime.

Every other surviving change is *logic* and fails the gate. That includes a changed string in a
comparison (`status == "COMPLETED"`), a dict key in an audit payload, or a reason code. True
equivalent mutants are excluded at the source with `# pragma: no mutate` and listed, with one
line each, in docs/mutation-report.md.

Exit code 0: no logic survivors. 1: logic survivors (printed with their diff). 2: no results.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from dataclasses import dataclass

import libcst as cst
from mutmut.mutation.data import SourceFileMutationData
from mutmut.mutation.diff_apply import (
    get_diff_for_mutant,
    read_mutant_function,
    read_mutants_module,
    read_original_function,
)
from mutmut.stats import status_by_exit_code
from mutmut.utils.file_utils import walk_mutatable_files

LOGGER_NAMES = {"log", "logger", "_log", "LOG"}
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
# Calls whose keyword arguments only fill message placeholders; listed keywords are logic.
TEXT_KWARG_CALLS: dict[str, set[str]] = {"message": set(), "note": set(), "block": {"target"}}
# Calls whose positional argument at this index is only a label for an error message.
LABEL_ARG_CALLS: dict[str, int] = {"_per_currency": 1, "_exactly_one": 1}
# Mutants that never ran or crashed the runner are not a pass.
BROKEN = {"not checked", "suspicious", "segfault", "check was interrupted by user"}


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_log_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOG_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in LOGGER_NAMES
    )


class _StripText(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = "_"  # mutmut renames the original and the mutant differently
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        if _is_log_call(node.value):
            return ast.Pass()
        return self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        if isinstance(node.exc, ast.Call):
            node.exc.args, node.exc.keywords = [], []
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        name = _call_name(node.func) or ""
        if name == "cast" and len(node.args) == 2:
            return self.visit(node.args[1])  # typing.cast is a no-op at runtime
        keep = TEXT_KWARG_CALLS.get(name)
        if keep is not None:
            node.keywords = [k for k in node.keywords if k.arg in keep]
        label = LABEL_ARG_CALLS.get(name)
        if label is not None and len(node.args) > label:
            node.args[label] = ast.Constant("")
        return self.generic_visit(node)


def _normalised(function: cst.FunctionDef) -> str:
    source = cst.Module([function.with_changes(leading_lines=[])]).code
    lines = source.splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    tree = ast.parse("\n".join(line[indent:] for line in lines))
    return ast.dump(_StripText().visit(tree))


@dataclass(frozen=True)
class Survivor:
    name: str
    status: str
    text_only: bool
    diff: str


def collect() -> tuple[Counter[str], list[Survivor]]:
    statuses: Counter[str] = Counter()
    survivors: list[Survivor] = []
    for path in walk_mutatable_files():
        data = SourceFileMutationData(path=path)
        data.load()
        if not data.exit_code_by_key:
            continue
        module = read_mutants_module(path)
        for name, exit_code in data.exit_code_by_key.items():
            status = status_by_exit_code[exit_code]
            statuses[status] += 1
            if status not in {"survived", "no tests"} | BROKEN:
                continue
            original = read_original_function(module, name)
            mutant = read_mutant_function(module, name)
            text_only = status not in BROKEN and _normalised(original) == _normalised(mutant)
            diff = get_diff_for_mutant(name, path=path)
            survivors.append(Survivor(name, status, text_only, diff))
    return statuses, survivors


def main() -> int:
    statuses, survivors = collect()
    if not statuses:
        print("mutmut-gate: no mutation results; run `mutmut run` first", file=sys.stderr)
        return 2
    text = [s for s in survivors if s.text_only]
    logic = [s for s in survivors if not s.text_only]
    total = sum(statuses.values())
    print(
        f"mutmut-gate: {total} mutants: "
        + ", ".join(f"{v} {k}" for k, v in sorted(statuses.items()))
    )
    print(f"mutmut-gate: survivors: {len(text)} text-only (allowed), {len(logic)} logic")
    if "-v" in sys.argv:
        for s in text:
            print(f"\n[text] {s.name}\n{s.diff}")
    for s in logic:
        print(f"\n[LOGIC {s.status}] {s.name}\n{s.diff}")
    if logic:
        print(
            f"\nmutmut-gate: FAIL: {len(logic)} logic survivors in the money path", file=sys.stderr
        )
        return 1
    print("mutmut-gate: OK: zero logic survivors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
