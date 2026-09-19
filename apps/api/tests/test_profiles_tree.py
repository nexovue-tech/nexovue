"""Stacks into a flame graph (F17: FR8, NFR4, AC11) — the pure half, no database."""

from __future__ import annotations

from nexovue_api.profiles import tree


def _stack(value: int, *frames: str, binaries: tuple[str, ...] = ()) -> tree.Stack:
    """Frames leaf first, as storage keeps them; an empty name is an unnamed frame."""
    return tree.Stack(
        functions=list(frames),
        files=[f"{frame}.go" if frame else "" for frame in frames],
        binaries=list(binaries) or [""] * len(frames),
        value=value,
    )


def _child(node: tree.Node, name: str) -> tree.Node:
    return next(child for child in node.children.values() if child.name == name)


def test_stacks_merge_root_first_and_every_share_is_of_one_whole() -> None:
    root = tree.build(
        [
            _stack(60, "sha256.block", "Cart.Checkout", "http.serve"),
            _stack(30, "json.Marshal", "Cart.Checkout", "http.serve"),
            _stack(10, "gc.mark", "runtime.gcBgMarkWorker"),
        ],
        grand_total=100,
    )

    assert root.name == tree.ROOT_NAME
    assert root.total == 100
    serve = _child(root, "http.serve")
    checkout = _child(serve, "Cart.Checkout")
    assert (serve.total, checkout.total) == (90, 90)
    assert _child(checkout, "sha256.block").self_value == 60
    assert sum(child.total for child in root.children.values()) == root.total


def test_what_the_heaviest_stacks_leave_out_is_one_node_so_shares_still_add_up() -> None:
    root = tree.build([_stack(70, "a", "main")], grand_total=100)

    remainder = _child(root, tree.REMAINDER_NAME)
    assert remainder.total == 30
    assert sum(child.total for child in root.children.values()) == 100


def test_an_unnamed_frame_is_its_binary_marked_unsymbolized_never_blank() -> None:
    root = tree.build([_stack(5, "", "main", binaries=("libc.so.6", ""))], grand_total=5)

    leaf = _child(_child(root, "main"), "libc.so.6")
    assert leaf.unsymbolized is True
    assert leaf.name == "libc.so.6"


def test_pruning_folds_the_lightest_frames_into_one_node_that_says_how_many() -> None:
    stacks = [_stack(1000, "main")] + [_stack(1, f"tiny{i}", "main") for i in range(50)]
    root = tree.build(stacks, grand_total=1050)

    folded = tree.prune(root, max_nodes=10)

    main = _child(root, "main")
    merged = _child(main, tree.MERGED_NAME)
    assert folded == merged.merged
    assert merged.merged > 0
    # Folding moves value; it never loses any.
    assert sum(child.total for child in main.children.values()) == 50


def test_a_small_graph_is_left_whole() -> None:
    root = tree.build([_stack(5, "a", "main")], grand_total=5)
    assert tree.prune(root, max_nodes=10) == 0


def test_the_functions_table_counts_a_recursive_function_once_per_stack() -> None:
    rows = tree.functions(
        [_stack(10, "walk", "walk", "walk", "main"), _stack(5, "main")],
    )
    by_name = {row.name: row for row in rows}
    assert by_name["walk"].total == 10
    assert by_name["walk"].self_value == 10
    assert by_name["main"].total == 15
    assert by_name["main"].self_value == 5
    assert rows[0].name == "walk"


def _side(*stacks: tree.Stack) -> tree.Side:
    return tree.Side(stacks=list(stacks), grand_total=sum(stack.value for stack in stacks))


def test_a_function_whose_share_doubled_ranks_first_as_increased() -> None:
    """AC9. The comparison side is twice as busy, so a raw value doubles everywhere;
    only `sha256.block`'s *share* grew, and that is what must rank first."""
    before = _side(_stack(20, "sha256.block", "main"), _stack(80, "json.Marshal", "main"))
    after = _side(_stack(80, "sha256.block", "main"), _stack(120, "json.Marshal", "main"))

    rows = tree.compare_functions(before, after)

    assert rows[0].name == "sha256.block"
    assert (rows[0].baseline_self, rows[0].comparison_self) == (0.2, 0.4)
    assert rows[0].change > 0
    marshal = next(row for row in rows if row.name == "json.Marshal")
    assert marshal.change < 0


def test_a_difference_graph_merges_by_path_and_keeps_one_sided_frames() -> None:
    before = _side(_stack(50, "old.path", "main"), _stack(50, "shared", "main"))
    after = _side(_stack(40, "new.path", "main"), _stack(60, "shared", "main"))

    root = tree.difference(before, after)

    main = _child(root, "main")
    assert (main.baseline, main.comparison) == (100, 100)
    assert (_child(main, "old.path").baseline, _child(main, "old.path").comparison) == (50, 0)
    assert (_child(main, "new.path").baseline, _child(main, "new.path").comparison) == (0, 40)
    # Widths are the two shares averaged, so they still make up the whole.
    assert root.total == tree.SHARE_SCALE
    assert sum(child.total for child in main.children.values()) == main.total


def test_an_empty_side_contributes_nothing_rather_than_dividing_by_zero() -> None:
    root = tree.difference(_side(), _side(_stack(10, "a", "main")))

    assert root.total == tree.SHARE_SCALE
    assert _child(root, "main").baseline == 0


def test_folding_a_difference_graph_keeps_both_sides_values() -> None:
    before = _side(*[_stack(1, f"tiny{i}", "main") for i in range(30)], _stack(100, "main"))
    after = _side(*[_stack(2, f"tiny{i}", "main") for i in range(30)], _stack(100, "main"))
    root = tree.difference(before, after)

    tree.prune(root, max_nodes=2)

    merged = _child(_child(root, "main"), tree.MERGED_NAME)
    assert (merged.baseline, merged.comparison) == (30, 60)


def test_a_long_comparison_keeps_what_shrank_as_well_as_what_grew() -> None:
    before = _side(*[_stack(10 + i, f"f{i}", "main") for i in range(40)])
    after = _side(*[_stack(50 - i, f"f{i}", "main") for i in range(40)])

    kept = tree.both_ends(tree.compare_functions(before, after), 10)

    assert len(kept) == 10
    assert kept[0].change > 0
    assert kept[-1].change < 0
    assert min(row.change for row in kept) == min(
        row.change for row in tree.compare_functions(before, after)
    )


def test_a_box_names_its_line_only_when_every_frame_in_it_was_on_that_line() -> None:
    root = tree.build(
        [
            tree.Stack(["a", "main"], ["a.go", "main.go"], ["", ""], 5, lines=[10, 3]),
            tree.Stack(["a", "main"], ["a.go", "main.go"], ["", ""], 5, lines=[12, 3]),
        ],
        grand_total=10,
    )

    main = _child(root, "main")
    assert main.line == 3
    assert _child(main, "a").line == tree.VARIOUS_LINES
