import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  type Modifier,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { GripVertical } from "lucide-react";
import { type ReactNode, useMemo, useSyncExternalStore } from "react";

/**
 * Drag to reorder in the builder (F20, T10.1; FR22).
 *
 * **A second way, never the only one.** The toolbar's Earlier and Later stay and remain the
 * keyboard's way (WCAG 2.2, 2.5.7; AC37, AC144). So there is no keyboard sensor: the
 * library's moves by screen position, not by one place, and the trial (T3.7) saw the first
 * panel land fifth. A drop writes the same stored order a button writes (`move-to`), and
 * the page's one live region says it (AC143). **It must not grow into a grid:** the order is
 * a list, and a panel's width is still its own setting.
 *
 * **The trial's three conditions** (`status.md`, *Drag trial*, open decision 2): the other
 * panels hold still until the drop, and the panel under the pointer is marked, since the
 * library's own sliding preview overlapped panels in 21 of 40 mixed-width drags; no keyboard
 * sensor; and the dragged panel cannot leave the grid sideways, which scrolled the page in
 * 8 of 40 drags.
 *
 * **Only a handle starts a drag,** because a panel's chart draws its own selection with the
 * same pointer. And not on touch: a finger drags the page, and the buttons need no precision
 * (design, *The builder*).
 */

const FINE_POINTER_QUERY = "(pointer: fine)";
/** Pixels the pointer moves before a press becomes a drag, so a click is never one. */
const DRAG_DISTANCE = 5;

/** Whether the main pointer is precise, following it as it changes. */
export function useFinePointer(): boolean {
  return useSyncExternalStore(subscribeFine, readFine, () => true);
}

function subscribeFine(changed: () => void): () => void {
  try {
    const query = window.matchMedia(FINE_POINTER_QUERY);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  } catch {
    return () => {};
  }
}

function readFine(): boolean {
  try {
    return window.matchMedia(FINE_POINTER_QUERY).matches;
  } catch {
    // No way to ask: a mouse is the usual case, and the buttons are there either way.
    return true;
  }
}

interface Span {
  left: number;
  right: number;
}

/**
 * How far a panel may move sideways without leaving the grid: `x` is held so the panel's
 * edges stay inside it. A pure function of three numbers' worth of rectangles, so it is
 * tested without a browser.
 */
export function clampSideways(x: number, panel: Span, grid: Span): number {
  const furthestLeft = grid.left - panel.left;
  const furthestRight = grid.right - panel.right;
  // A panel wider than the grid (never, but never a throw) stays where it was.
  if (furthestLeft > furthestRight) return 0;
  return Math.min(Math.max(x, furthestLeft), furthestRight);
}

function keepInside(grid: () => HTMLElement | null): Modifier {
  return ({ transform, draggingNodeRect }) => {
    const bounds = grid()?.getBoundingClientRect();
    if (!bounds || !draggingNodeRect) return transform;
    return { ...transform, x: clampSideways(transform.x, draggingNodeRect, bounds) };
  };
}

export function PanelDragArea({
  grid,
  onDrop,
  onCancel,
  children,
}: {
  grid: () => HTMLElement | null;
  /** The panel dragged and the one it was dropped on. */
  onDrop: (id: string, over: string) => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  // A pointer sensor alone: no keyboard sensor, and nothing else offers a drag.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: DRAG_DISTANCE } }),
  );
  const modifiers = useMemo(() => [keepInside(grid)], [grid]);
  // The library renders its own `role="status"` live region and a hidden instruction. The
  // page has one live region (AC143), and a second would say each drop twice, so both go
  // into a node that is never on the page: nothing there reaches a screen reader.
  const unheard = useMemo(() => document.createElement("div"), []);

  const finish = ({ active, over }: DragEndEvent) => {
    if (over) onDrop(String(active.id), String(over.id));
    else onCancel();
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={modifiers}
      onDragEnd={finish}
      onDragCancel={onCancel}
      accessibility={{ container: unheard }}
    >
      {children}
    </DndContext>
  );
}

/**
 * One panel's frame in the builder's grid. Its `handle`, drawn only for a precise pointer,
 * starts the drag; the panel itself never moves until the drop, apart from following the
 * pointer, and the panel it would land on is marked.
 */
export function DraggablePanel({
  id,
  labelledBy,
  className,
  children,
}: {
  id: string;
  labelledBy: string;
  className: string;
  children: (handle: ReactNode) => ReactNode;
}) {
  const offered = useFinePointer();
  const drag = useDraggable({ id });
  const drop = useDroppable({ id });
  const target = drop.isOver && !drag.isDragging;
  const style = drag.transform
    ? { transform: `translate3d(${drag.transform.x}px, ${drag.transform.y}px, 0)` }
    : undefined;
  const handle = offered ? (
    // Not a button and not a tab stop: the toolbar is the keyboard's way, and a second
    // control per panel would add thirty tab stops (AC37).
    <span
      ref={drag.setActivatorNodeRef}
      {...drag.listeners}
      data-drag-handle
      aria-hidden="true"
      title="Drag to reorder"
      className="flex min-h-6 min-w-6 cursor-grab touch-none items-center justify-center rounded-xs text-ink-dim hover:bg-hover hover:text-ink"
    >
      <GripVertical size={16} />
    </span>
  ) : null;

  return (
    <section
      ref={(node) => {
        drag.setNodeRef(node);
        drop.setNodeRef(node);
      }}
      data-panel-id={id}
      data-drop-target={target ? "" : undefined}
      aria-labelledby={labelledBy}
      style={style}
      className={`${className} ${drag.isDragging ? "relative z-10 opacity-80" : ""} ${target ? "outline-2 outline-brand outline-offset-2" : ""}`}
    >
      {children(handle)}
    </section>
  );
}
