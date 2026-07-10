"""Pure graph construction and force-layout helpers for note relationships."""

from __future__ import annotations

import math
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .links import LinkIndex


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    path: str | None
    ghost: bool = False


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str


@dataclass(frozen=True)
class GraphData:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def _ghost_parts(target: str) -> tuple[str, str]:
    raw = target.strip().split("#", 1)[0].strip().replace("\\", "/")
    if raw.casefold().endswith(".md"):
        raw = raw[:-3]
    label = raw.rsplit("/", 1)[-1].strip() or raw
    return f"ghost:{raw.casefold()}", label


def build_graph(index: LinkIndex) -> GraphData:
    """Build note nodes and wiki-link edges from a populated ``LinkIndex``.

    Every indexed note becomes a node, including notes with no links. Unknown
    wiki-link targets become deduplicated ghost nodes. Multiple references from
    one source to the same target collapse to one directed edge.
    """
    paths = set(index.forward)
    paths.update(index.raw_targets)
    paths.update(index.backward)
    for targets in index.forward.values():
        paths.update(targets)

    nodes: dict[str, GraphNode] = {
        path: GraphNode(path, Path(path).stem, path)
        for path in sorted(paths, key=lambda value: value.casefold())
    }
    edges: set[tuple[str, str]] = set()

    for source in sorted(index.raw_targets, key=lambda value: value.casefold()):
        for raw_target in index.raw_targets[source]:
            resolved = index.resolve(raw_target, source)
            if resolved is not None:
                target = str(resolved)
                if target != source:
                    nodes.setdefault(target, GraphNode(target, resolved.stem, target))
                    edges.add((source, target))
                continue
            ghost_id, label = _ghost_parts(raw_target)
            if ghost_id == "ghost:":
                continue
            nodes.setdefault(ghost_id, GraphNode(ghost_id, label, None, ghost=True))
            edges.add((source, ghost_id))

    ordered_nodes = tuple(
        sorted(nodes.values(), key=lambda node: (node.ghost, node.label.casefold(), node.id))
    )
    ordered_edges = tuple(GraphEdge(*edge) for edge in sorted(edges))
    return GraphData(ordered_nodes, ordered_edges)


def initial_positions(nodes: Iterable[GraphNode], radius: float | None = None) -> dict[str, tuple[float, float]]:
    """Place nodes deterministically on a circle before force iteration."""
    ordered = list(nodes)
    count = len(ordered)
    if not count:
        return {}
    ring = radius if radius is not None else max(120.0, 38.0 * math.sqrt(count))
    return {
        node.id: (
            ring * math.cos((2.0 * math.pi * index) / count),
            ring * math.sin((2.0 * math.pi * index) / count),
        )
        for index, node in enumerate(ordered)
    }


def assign_node_groups(
    nodes: Iterable[GraphNode],
    libraries: Iterable[object],
    *,
    unmatched_group: str = "其他",
) -> dict[str, str | None]:
    """Assign real nodes to their containing document library.

    Library-like values only need ``name`` and ``path`` attributes, keeping the
    grouping policy independent of Qt and the document-library store. Ghost
    nodes deliberately have no group so they remain visible and grey.
    """

    roots: list[tuple[str, str]] = []
    for library in libraries:
        name = str(getattr(library, "name", "") or "").strip()
        path = str(getattr(library, "path", "") or "").strip()
        if name and path:
            roots.append((name, _normalized_group_path(path)))
    roots.sort(key=lambda entry: len(entry[1]), reverse=True)

    assignments: dict[str, str | None] = {}
    for node in nodes:
        if node.ghost or not node.path:
            assignments[node.id] = None
            continue
        node_path = _normalized_group_path(node.path)
        group = next(
            (
                name
                for name, root in roots
                if node_path == root or node_path.startswith(f"{root}/")
            ),
            unmatched_group,
        )
        assignments[node.id] = group
    return assignments


def group_visibility(
    node_groups: Mapping[str, str | None], hidden_groups: Iterable[str]
) -> dict[str, bool]:
    """Return node visibility for a set of hidden legend groups."""

    hidden = set(hidden_groups)
    return {
        node_id: group is None or group not in hidden
        for node_id, group in node_groups.items()
    }


def separate_overlapping_nodes(
    positions: Mapping[str, tuple[float, float]],
    sizes: Mapping[str, tuple[float, float]],
    *,
    min_gap: float = 12.0,
    pinned: Iterable[str] = (),
    iterations: int = 64,
    ensure_separated: bool = True,
) -> dict[str, tuple[float, float]]:
    """Separate axis-aligned node rectangles without mutating inputs.

    Each collision is resolved along its least-overlapping axis. Repeated
    passes handle dense piles while pinned nodes stay fixed.
    """

    result = {node_id: (float(x), float(y)) for node_id, (x, y) in positions.items()}
    ids = list(result)
    fixed = set(pinned)
    gap = max(0.0, float(min_gap))

    for _ in range(max(0, int(iterations))):
        changed = False
        for left_index, left in enumerate(ids):
            left_size = sizes.get(left)
            if left_size is None:
                continue
            for right in ids[left_index + 1 :]:
                right_size = sizes.get(right)
                if right_size is None or (left in fixed and right in fixed):
                    continue
                lx, ly = result[left]
                rx, ry = result[right]
                dx, dy = lx - rx, ly - ry
                overlap_x = (left_size[0] + right_size[0]) / 2.0 + gap - abs(dx)
                overlap_y = (left_size[1] + right_size[1]) / 2.0 + gap - abs(dy)
                if overlap_x <= 0.0 or overlap_y <= 0.0:
                    continue

                changed = True
                move_left = 0.0 if left in fixed else (1.0 if right in fixed else 0.5)
                move_right = 0.0 if right in fixed else (1.0 if left in fixed else 0.5)
                if overlap_x <= overlap_y:
                    direction = -1.0 if dx <= 0.0 else 1.0
                    shift = overlap_x + 1e-6
                    result[left] = (lx + direction * shift * move_left, ly)
                    result[right] = (rx - direction * shift * move_right, ry)
                else:
                    direction = -1.0 if dy <= 0.0 else 1.0
                    shift = overlap_y + 1e-6
                    result[left] = (lx, ly + direction * shift * move_left)
                    result[right] = (rx, ry - direction * shift * move_right)
        if not changed:
            break
    if ensure_separated and _has_overlaps(result, sizes, gap):
        result = _pack_without_overlaps(result, sizes, gap, fixed)
    return result


def _normalized_group_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return normalized.rstrip("/").casefold()


def _has_overlaps(
    positions: Mapping[str, tuple[float, float]],
    sizes: Mapping[str, tuple[float, float]],
    gap: float,
) -> bool:
    ids = [node_id for node_id in positions if node_id in sizes]
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            if _rectangles_overlap(left, positions[left], right, positions[right], sizes, gap):
                return True
    return False


def _pack_without_overlaps(
    positions: Mapping[str, tuple[float, float]],
    sizes: Mapping[str, tuple[float, float]],
    gap: float,
    fixed: set[str],
) -> dict[str, tuple[float, float]]:
    """Deterministic fallback that guarantees separation for non-pinned nodes."""

    result = dict(positions)
    sized = [node_id for node_id in result if node_id in sizes]
    order = sorted(
        sized,
        key=lambda node_id: (
            node_id not in fixed,
            result[node_id][1],
            result[node_id][0],
            node_id,
        ),
    )
    placed: list[str] = []
    for node_id in order:
        if node_id in fixed:
            placed.append(node_id)
            continue
        origin = result[node_id]
        if not any(
            _rectangles_overlap(node_id, origin, other, result[other], sizes, gap)
            for other in placed
        ):
            placed.append(node_id)
            continue

        step = max(8.0, min(sizes[node_id]) + gap)
        found: tuple[float, float] | None = None
        for ring in range(1, max(8, len(order) * 2) + 1):
            offsets = []
            for offset in range(-ring, ring + 1):
                offsets.extend(
                    ((offset, -ring), (offset, ring), (-ring, offset), (ring, offset))
                )
            for offset_x, offset_y in offsets:
                candidate = (
                    origin[0] + offset_x * step,
                    origin[1] + offset_y * step,
                )
                if not any(
                    _rectangles_overlap(
                        node_id, candidate, other, result[other], sizes, gap
                    )
                    for other in placed
                ):
                    found = candidate
                    break
            if found is not None:
                break
        if found is not None:
            result[node_id] = found
        placed.append(node_id)
    return result


def _rectangles_overlap(
    left: str,
    left_position: tuple[float, float],
    right: str,
    right_position: tuple[float, float],
    sizes: Mapping[str, tuple[float, float]],
    gap: float,
) -> bool:
    dx = abs(left_position[0] - right_position[0])
    dy = abs(left_position[1] - right_position[1])
    required_x = (sizes[left][0] + sizes[right][0]) / 2.0 + gap
    required_y = (sizes[left][1] + sizes[right][1]) / 2.0 + gap
    return dx < required_x - 1e-7 and dy < required_y - 1e-7


def layout_step(
    positions: Mapping[str, tuple[float, float]],
    edges: Iterable[GraphEdge],
    *,
    temperature: float = 12.0,
    ideal_length: float = 90.0,
    pinned: Iterable[str] = (),
) -> tuple[dict[str, tuple[float, float]], float]:
    """Run one bounded Fruchterman-Reingold-style iteration.

    Returns ``(new_positions, total_movement)`` and never mutates the input.
    """
    ids = list(positions)
    if not ids:
        return {}, 0.0
    fixed = set(pinned)
    displacement = {node_id: [0.0, 0.0] for node_id in ids}
    epsilon = 0.01

    for left_index, left in enumerate(ids):
        lx, ly = positions[left]
        for right in ids[left_index + 1 :]:
            rx, ry = positions[right]
            dx, dy = lx - rx, ly - ry
            distance = max(math.hypot(dx, dy), epsilon)
            if distance == epsilon:
                dx = 0.01 if left < right else -0.01
                dy = 0.006
                distance = math.hypot(dx, dy)
            force = (ideal_length * ideal_length) / distance
            fx, fy = dx / distance * force, dy / distance * force
            displacement[left][0] += fx
            displacement[left][1] += fy
            displacement[right][0] -= fx
            displacement[right][1] -= fy

    for edge in edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        dx, dy = sx - tx, sy - ty
        distance = max(math.hypot(dx, dy), epsilon)
        force = (distance * distance) / ideal_length
        fx, fy = dx / distance * force, dy / distance * force
        displacement[edge.source][0] -= fx
        displacement[edge.source][1] -= fy
        displacement[edge.target][0] += fx
        displacement[edge.target][1] += fy

    updated: dict[str, tuple[float, float]] = {}
    total_movement = 0.0
    max_step = max(0.0, float(temperature))
    for node_id in ids:
        x, y = positions[node_id]
        if node_id in fixed:
            updated[node_id] = (x, y)
            continue
        dx, dy = displacement[node_id]
        magnitude = math.hypot(dx, dy)
        if magnitude:
            step = min(max_step, magnitude)
            dx, dy = dx / magnitude * step, dy / magnitude * step
        # A light pull toward the origin keeps disconnected components nearby.
        dx -= x * 0.015
        dy -= y * 0.015
        updated[node_id] = (x + dx, y + dy)
        total_movement += math.hypot(dx, dy)
    return updated, total_movement
