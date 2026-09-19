"""Stacks into a flame graph: merge, prune, and the functions table.

Pure, so it is tested without a database. The graph answers one question — where
did the total go — so every number in it is a share of one whole, and pruning
never makes the shares stop adding up: what is cut is folded into a node that
says so (spec FR8, NFR4, AC11).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

# The most nodes one graph returns. Enough to read any real service; few enough
# that the browser draws it without stalling (NFR4).
MAX_NODES: Final = 2_000

ROOT_NAME: Final = "all"
VARIOUS_LINES: Final = -1
MERGED_NAME: Final = "frames merged"
REMAINDER_NAME: Final = "smaller stacks"


@dataclass(frozen=True, slots=True)
class Stack:
    """One distinct stack and the value it accounts for, frames leaf first."""

    functions: Sequence[str]
    files: Sequence[str]
    binaries: Sequence[str]
    value: int
    # Parallel to `functions`; empty where the source gave none.
    lines: Sequence[int] = ()


# The whole a difference graph's widths are parts of: each side's share, averaged,
# in integer parts so pruning can rank and sum them as it does plain values.
SHARE_SCALE: Final = 1_000_000_000


@dataclass(slots=True)
class Node:
    """One box of the flame graph. `merged` counts the frames folded into it.

    In a difference graph `total` is the box's width — its two shares averaged,
    in `SHARE_SCALE` parts — and `baseline` and `comparison` are its own value on
    each side, in the profile's unit.
    """

    name: str
    file: str = ""
    unsymbolized: bool = False
    total: int = 0
    self_value: int = 0
    merged: int = 0
    baseline: int = 0
    comparison: int = 0
    # The source line every frame merged here was on: 0 when unknown, and
    # VARIOUS_LINES when they disagree — one line would then be a guess.
    line: int = 0
    children: dict[tuple[str, str, bool], Node] = field(default_factory=dict)

    def child(self, name: str, file: str, unsymbolized: bool) -> Node:
        key = (name, file, unsymbolized)
        found = self.children.get(key)
        if found is None:
            found = Node(name=name, file=file, unsymbolized=unsymbolized)
            self.children[key] = found
        return found


@dataclass(frozen=True, slots=True)
class Side:
    """One side of a comparison: its heaviest stacks and the whole they are part of."""

    stacks: Sequence[Stack]
    grand_total: int


@dataclass(frozen=True, slots=True)
class FunctionChange:
    """One function on both sides, as shares of each side's own whole."""

    name: str
    file: str
    unsymbolized: bool
    baseline_self: float
    comparison_self: float
    baseline_total: float
    comparison_total: float

    @property
    def change(self) -> float:
        return self.comparison_self - self.baseline_self


@dataclass(frozen=True, slots=True)
class FunctionRow:
    """One function's share: self is where it was the running frame, total where it
    was anywhere on the stack (counted once per stack, however deep it recursed)."""

    name: str
    file: str
    unsymbolized: bool
    self_value: int
    total: int


def frame_identity(function: str, file: str, binary: str) -> tuple[str, str, bool]:
    """How a frame is shown. A named frame is its function and file; an unnamed one
    is its binary, marked unsymbolized — never blank, never another function."""
    if function:
        return function, file, False
    return (binary or "unknown"), "", True


def build(stacks: Iterable[Stack], grand_total: int) -> Node:
    """Merge stacks into a tree rooted at `all`, whose total is the grand total.

    The stacks may be only the heaviest of the selection; what they do not account
    for becomes one `smaller stacks` node under the root, so every share is of the
    same whole.
    """
    root = Node(name=ROOT_NAME)
    accounted = 0
    for stack in stacks:
        accounted += stack.value
        node = root
        # Leaf first in storage; the graph reads root first.
        for index in range(len(stack.functions) - 1, -1, -1):
            name, file, unsymbolized = frame_identity(
                stack.functions[index], _at(stack.files, index), _at(stack.binaries, index)
            )
            node = node.child(name, file, unsymbolized)
            node.total += stack.value
            _see_line(node, _line_at(stack.lines, index))
        node.self_value += stack.value

    remainder = grand_total - accounted
    if remainder > 0:
        smaller = root.child(REMAINDER_NAME, "", False)
        smaller.total += remainder
        smaller.self_value += remainder
    root.total = max(grand_total, accounted)
    return root


def difference(baseline: Side, comparison: Side) -> Node:
    """Two profiles merged by frame path, each box carrying its value on both sides.

    Shares are compared, not values (spec FR9): a longer or busier range is not a
    regression. A box is as wide as its two shares averaged, so a frame present on
    one side only is still drawn, and the widths still sum to the whole. A side with
    nothing in it contributes nothing, rather than dividing by zero.
    """
    root = Node(name=ROOT_NAME)
    for side, record in ((baseline, _add_baseline), (comparison, _add_comparison)):
        accounted = 0
        for stack in side.stacks:
            accounted += stack.value
            node = root
            record(node, stack.value)
            for index in range(len(stack.functions) - 1, -1, -1):
                name, file, unsymbolized = frame_identity(
                    stack.functions[index], _at(stack.files, index), _at(stack.binaries, index)
                )
                node = node.child(name, file, unsymbolized)
                record(node, stack.value)
        remainder = side.grand_total - accounted
        if remainder > 0:
            record(root, remainder)
            record(root.child(REMAINDER_NAME, "", False), remainder)

    sides = [
        (total, value_of)
        for total, value_of in (
            (baseline.grand_total, _baseline_of),
            (comparison.grand_total, _comparison_of),
        )
        if total > 0
    ]
    for node in _walk(root):
        if sides:
            average = sum(value_of(node) / total for total, value_of in sides) / len(sides)
            node.total = round(average * SHARE_SCALE)
    return root


def compare_functions(baseline: Side, comparison: Side) -> list[FunctionChange]:
    """Every function on either side, the one whose self share grew most first.

    Ranked by self share because a regression is the function that spends more of
    the whole itself; its callers' totals rise with it and would otherwise tie.
    Growth first, since "what got slower" is the question a comparison is opened for.
    """
    before = {_key(row): row for row in functions(baseline.stacks)}
    after = {_key(row): row for row in functions(comparison.stacks)}
    changes = []
    for key in before.keys() | after.keys():
        was, now = before.get(key), after.get(key)
        changes.append(
            FunctionChange(
                name=key[0],
                file=key[1],
                unsymbolized=key[2],
                baseline_self=_share(was.self_value if was else 0, baseline.grand_total),
                comparison_self=_share(now.self_value if now else 0, comparison.grand_total),
                baseline_total=_share(was.total if was else 0, baseline.grand_total),
                comparison_total=_share(now.total if now else 0, comparison.grand_total),
            )
        )
    return sorted(
        changes,
        key=lambda row: (-row.change, -(row.comparison_total - row.baseline_total), row.name),
    )


def both_ends(changes: Sequence[FunctionChange], limit: int) -> list[FunctionChange]:
    """At most `limit` of a ranked comparison, from both ends: what grew most and
    what shrank most. A cut from the top alone would keep only growth, and "what now
    costs less" is half of what a comparison is read for (spec FR9)."""
    if len(changes) <= limit:
        return list(changes)
    half = limit // 2
    return [*changes[:half], *changes[len(changes) - (limit - half) :]]


def prune(root: Node, max_nodes: int = MAX_NODES) -> int:
    """Keep the heaviest `max_nodes` nodes; fold the rest, per parent, into one
    `frames merged` node. Returns how many frames were folded.

    Each parent that loses children gains one merged node, so the result holds at
    most `max_nodes` plus one per such parent — bounded, not exactly `max_nodes`.
    A child's total never exceeds its parent's, so keeping the heaviest nodes keeps
    every kept node's ancestors: the tree stays connected.
    """
    ranked = sorted(_walk(root), key=lambda node: node.total, reverse=True)
    if len(ranked) <= max_nodes:
        return 0
    kept = {id(node) for node in ranked[:max_nodes]}
    kept.add(id(root))
    return _fold(root, kept)


def functions(stacks: Iterable[Stack]) -> list[FunctionRow]:
    """Every function's self and total value, heaviest self first."""
    self_values: dict[tuple[str, str, bool], int] = {}
    totals: dict[tuple[str, str, bool], int] = {}
    for stack in stacks:
        seen: set[tuple[str, str, bool]] = set()
        for index, function in enumerate(stack.functions):
            key = frame_identity(function, _at(stack.files, index), _at(stack.binaries, index))
            if index == 0:
                self_values[key] = self_values.get(key, 0) + stack.value
            if key not in seen:
                seen.add(key)
                totals[key] = totals.get(key, 0) + stack.value
    rows = [
        FunctionRow(
            name=key[0],
            file=key[1],
            unsymbolized=key[2],
            self_value=self_values.get(key, 0),
            total=total,
        )
        for key, total in totals.items()
    ]
    return sorted(rows, key=lambda row: (row.self_value, row.total), reverse=True)


def _fold(node: Node, kept: set[int]) -> int:
    folded = 0
    merged = Node(name=MERGED_NAME)
    for key, child in list(node.children.items()):
        if id(child) in kept:
            folded += _fold(child, kept)
            continue
        merged.total += child.total
        merged.baseline += child.baseline
        merged.comparison += child.comparison
        merged.merged += _count(child)
        del node.children[key]
    if merged.merged:
        merged.self_value = merged.total
        node.children[(MERGED_NAME, "", False)] = merged
        folded += merged.merged
    return folded


def _see_line(node: Node, line: int) -> None:
    if line <= 0 or node.line == VARIOUS_LINES:
        return
    if node.line == 0:
        node.line = line
    elif node.line != line:
        node.line = VARIOUS_LINES


def _line_at(lines: Sequence[int], index: int) -> int:
    return lines[index] if index < len(lines) else 0


def _add_baseline(node: Node, value: int) -> None:
    node.baseline += value


def _add_comparison(node: Node, value: int) -> None:
    node.comparison += value


def _baseline_of(node: Node) -> int:
    return node.baseline


def _comparison_of(node: Node) -> int:
    return node.comparison


def _key(row: FunctionRow) -> tuple[str, str, bool]:
    return row.name, row.file, row.unsymbolized


def _share(value: int, whole: int) -> float:
    return value / whole if whole > 0 else 0.0


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children.values():
        yield from _walk(child)


def _count(node: Node) -> int:
    return 1 + sum(_count(child) for child in node.children.values())


def _at(values: Sequence[str], index: int) -> str:
    return values[index] if index < len(values) else ""
