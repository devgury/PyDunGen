"""PyDunGen4: PyDunGen3 + shortest-path linking for disconnected rooms."""

import argparse
import heapq
import importlib.util
import random
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

try:
    import noise
except ImportError:
    noise = None

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_d3_path = Path(__file__).with_name("PyDunGen3.py")
_spec = importlib.util.spec_from_file_location("PyDunGen3", _d3_path)
_d3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_d3)

MIN_ROOM_SIZE = 20
ROOM_DEPTH = 3
CORRIDOR_WIDTH = 3


def _progress(msg: str) -> None:
    print(msg, flush=True)


PROGRESS_TOTAL = 10
_progress_count = 0


def _progress_reset(start=0) -> None:
    global _progress_count
    _progress_count = max(0, min(PROGRESS_TOTAL, start))
    print("진행도 : " + ("|" * _progress_count), end="", flush=True)


def _progress_step() -> None:
    global _progress_count
    if _progress_count < PROGRESS_TOTAL:
        _progress_count += 1
        print("|", end="", flush=True)


def _progress_complete() -> None:
    global _progress_count
    if _progress_count < PROGRESS_TOTAL:
        print("|" * (PROGRESS_TOTAL - _progress_count), end="", flush=True)
    _progress_count = PROGRESS_TOTAL
    print(" (완료)", flush=True)

# Closed-border vignette (player cannot leave the map)
SOLID_BORDER = 2
VIGNETTE_MARGIN = 30
VIGNETTE_NOISE_SCALE = 14.0
NOISE_TILES = (":", "%", "+", "#")


@dataclass
class Room:
    room_id: int
    tiles: set[tuple[int, int]] = field(default_factory=set)
    centroid: tuple[int, int] = (0, 0)

    def update_centroid(self):
        if not self.tiles:
            self.centroid = (0, 0)
            return
        sx = sum(x for x, _ in self.tiles)
        sy = sum(y for _, y in self.tiles)
        n = len(self.tiles)
        self.centroid = (sx // n, sy // n)


def floor_distance_to_wall(world):
    """Manhattan BFS distance from each floor tile to the nearest wall."""
    height = len(world)
    width = len(world[0])
    dist = [[-1] * width for _ in range(height)]
    queue = deque()

    for y in range(height):
        for x in range(width):
            if world[y][x] == "#":
                dist[y][x] = 0
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        base = dist[y][x]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and dist[ny][nx] == -1:
                if world[ny][nx] == ".":
                    dist[ny][nx] = base + 1
                    queue.append((nx, ny))
    return dist


def find_rooms(world, min_size=MIN_ROOM_SIZE, room_depth=ROOM_DEPTH):
    """
    Detect rooms as interior floor blobs (far from walls).
    Corridors/caves with depth < room_depth are excluded.
    """
    height = len(world)
    width = len(world[0])
    dist = floor_distance_to_wall(world)
    visited = [[False] * width for _ in range(height)]
    rooms = []
    room_id = 0

    for y in range(height):
        for x in range(width):
            if visited[y][x] or world[y][x] != ".":
                continue
            if dist[y][x] < room_depth:
                continue

            queue = deque([(x, y)])
            visited[y][x] = True
            tiles = set()

            while queue:
                cx, cy = queue.popleft()
                tiles.add((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    if visited[ny][nx] or world[ny][nx] != ".":
                        continue
                    if dist[ny][nx] < room_depth:
                        continue
                    visited[ny][nx] = True
                    queue.append((nx, ny))

            if len(tiles) >= min_size:
                room = Room(room_id, tiles)
                room.update_centroid()
                rooms.append(room)
                room_id += 1

    return rooms


def rooms_already_connected(world, room_a: Room, room_b: Room) -> bool:
    """True if two rooms are reachable via existing floor (no carving)."""
    height = len(world)
    width = len(world[0])
    start = next(iter(room_a.tiles))
    target = room_b.tiles
    queue = deque([start])
    visited = {start}

    while queue:
        x, y = queue.popleft()
        if (x, y) in target:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in visited:
                if world[ny][nx] == ".":
                    visited.add((nx, ny))
                    queue.append((nx, ny))
    return False


def build_room_groups(world, rooms):
    """Union rooms that are already connected by existing floor paths."""
    n = len(rooms)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if rooms_already_connected(world, rooms[i], rooms[j]):
                union(i, j)

    groups = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(rooms[i])
    return list(groups.values())


def pick_connection_points(room_a: Room, room_b: Room):
    """Pick the closest tile pair between two rooms (for path endpoints)."""
    best = None
    best_dist = float("inf")
    for ax, ay in room_a.tiles:
        for bx, by in room_b.tiles:
            d = abs(ax - bx) + abs(ay - by)
            if d < best_dist:
                best_dist = d
                best = ((ax, ay), (bx, by))
    return best


def shortest_path_carve(world, start, goal, corridor_width=CORRIDOR_WIDTH):
    """
    Dijkstra on grid: cost 0 on floor, 1 on wall (carve).
    Returns carved tile count, or 0 if no path.
    """
    height = len(world)
    width = len(world[0])
    sx, sy = start
    gx, gy = goal

    if not (0 <= sx < width and 0 <= sy < height and 0 <= gx < width and 0 <= gy < height):
        return 0

    heap = [(0, sx, sy)]
    costs = {(sx, sy): 0}
    parents = {(sx, sy): None}

    while heap:
        cost, x, y = heapq.heappop(heap)
        if (x, y) == (gx, gy):
            carved = 0
            cur = (gx, gy)
            while cur is not None:
                cx, cy = cur
                if world[cy][cx] == "#":
                    carved += 1
                world[cy][cx] = "."
                for dw in range(-(corridor_width // 2), corridor_width // 2 + 1):
                    for dh in range(-(corridor_width // 2), corridor_width // 2 + 1):
                        wx, wy = cx + dw, cy + dh
                        if 0 <= wx < width and 0 <= wy < height:
                            if world[wy][wx] == "#":
                                carved += 1
                            world[wy][wx] = "."
                cur = parents[cur]
            return carved

        if cost > costs.get((x, y), float("inf")):
            continue

        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            step = 0 if world[ny][nx] == "." else 1
            new_cost = cost + step
            if new_cost < costs.get((nx, ny), float("inf")):
                costs[(nx, ny)] = new_cost
                parents[(nx, ny)] = (x, y)
                heapq.heappush(heap, (new_cost, nx, ny))

    return 0


def group_representative_tiles(group):
    """Merge tiles from a room group for pathfinding endpoints."""
    merged = set()
    for room in group:
        merged |= room.tiles
    return merged


def shortest_path_between_groups(world, group_a, group_b, corridor_width=CORRIDOR_WIDTH):
    """Find shortest carve path between two room groups."""
    tiles_a = group_representative_tiles(group_a)
    tiles_b = group_representative_tiles(group_b)

    best_pair = None
    best_dist = float("inf")
    for ax, ay in tiles_a:
        for bx, by in tiles_b:
            d = abs(ax - bx) + abs(ay - by)
            if d < best_dist:
                best_dist = d
                best_pair = ((ax, ay), (bx, by))

    if best_pair is None:
        return 0

    return shortest_path_carve(world, best_pair[0], best_pair[1], corridor_width)


def path_cost_between_groups(world, group_a, group_b):
    """Estimate connection cost without mutating world (for MST)."""
    tiles_a = group_representative_tiles(group_a)
    tiles_b = group_representative_tiles(group_b)
    height = len(world)
    width = len(world[0])

    heap = []
    costs = {}
    for ax, ay in tiles_a:
        c = 0 if world[ay][ax] == "." else 1
        costs[(ax, ay)] = c
        heapq.heappush(heap, (c, ax, ay))

    targets = tiles_b

    while heap:
        cost, x, y = heapq.heappop(heap)
        if (x, y) in targets:
            return cost
        if cost > costs.get((x, y), float("inf")):
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            step = 0 if world[ny][nx] == "." else 1
            new_cost = cost + step
            if new_cost < costs.get((nx, ny), float("inf")):
                costs[(nx, ny)] = new_cost
                heapq.heappush(heap, (new_cost, nx, ny))

    return float("inf")


def connect_groups_mst(world, groups, corridor_width=CORRIDOR_WIDTH):
    """Connect all room groups with minimum total carve cost (MST)."""
    if len(groups) <= 1:
        return 0

    n = len(groups)
    in_tree = {0}
    edges_carved = 0

    while len(in_tree) < n:
        best_edge = None
        best_cost = float("inf")

        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                cost = path_cost_between_groups(world, groups[i], groups[j])
                if cost < best_cost:
                    best_cost = cost
                    best_edge = (i, j)

        if best_edge is None or best_cost == float("inf"):
            break

        i, j = best_edge
        shortest_path_between_groups(world, groups[i], groups[j], corridor_width)
        in_tree.add(j)
        edges_carved += 1

    return edges_carved


def edge_distance(x, y, width, height):
    """Chebyshev-ish margin: distance to nearest map edge."""
    return min(x, y, width - 1 - x, height - 1 - y)


def sample_border_noise(x, y, seed):
    if noise is None:
        random.seed(seed + x * 131 + y * 313)
        return random.random()
    return (noise.pnoise2(x / VIGNETTE_NOISE_SCALE, y / VIGNETTE_NOISE_SCALE, base=seed) + 1) * 0.5


def pick_noise_tile(value, force_wall=False):
    """Organic impassable tile for the outer noise ring."""
    if force_wall:
        return "#"
    idx = int(value * len(NOISE_TILES)) % len(NOISE_TILES)
    return NOISE_TILES[idx]


def apply_closed_border_vignette(
    world,
    seed=0,
    solid_border=SOLID_BORDER,
    vignette_margin=VIGNETTE_MARGIN,
):
    """
    Seal the dungeon:
      - Solid wall ring on the outermost tiles (no exit).
      - Vignette zone: wall density rises toward the edge.
      - Perlin noise overlay on the margin for a natural closed cave feel.
    """
    height = len(world)
    width = len(world[0])
    margin = max(solid_border + 1, vignette_margin)

    for y in range(height):
        for x in range(width):
            dist = edge_distance(x, y, width, height)

            if dist < solid_border:
                world[y][x] = "#"
                continue

            if dist >= margin:
                continue

            zone = (dist - solid_border) / max(1, margin - solid_border)
            vignette = 1.0 - zone
            n = sample_border_noise(x, y, seed)

            wall_strength = vignette * 0.75 + n * 0.35
            if wall_strength >= 0.52:
                world[y][x] = pick_noise_tile(n, force_wall=vignette > 0.82)
            elif wall_strength >= 0.38 and world[y][x] == ".":
                world[y][x] = pick_noise_tile(n * 0.7)

    return world


def connect_disconnected_rooms(world, min_size=MIN_ROOM_SIZE, room_depth=ROOM_DEPTH, corridor_width=CORRIDOR_WIDTH):
    """Find rooms, group by existing connectivity, MST-link remaining groups."""
    rooms = find_rooms(world, min_size=min_size, room_depth=room_depth)
    if len(rooms) <= 1:
        return world, 0, len(rooms)

    groups = build_room_groups(world, rooms)
    if len(groups) <= 1:
        return world, 0, len(rooms)

    links = connect_groups_mst(world, groups, corridor_width=corridor_width)
    return world, links, len(rooms)


def generate_world(
    seed=None,
    min_room_size=MIN_ROOM_SIZE,
    room_depth=ROOM_DEPTH,
    corridor_width=CORRIDOR_WIDTH,
    vignette_margin=VIGNETTE_MARGIN,
):
    _progress_step()
    world, used_seed = _d3.generate_world(seed)
    _progress_step()

    _progress_step()
    world, links, room_count = connect_disconnected_rooms(
        world,
        min_size=min_room_size,
        room_depth=room_depth,
        corridor_width=corridor_width,
    )
    _progress_step()

    _progress_step()
    apply_closed_border_vignette(world, seed=used_seed, vignette_margin=vignette_margin)
    _progress_step()
    return world, used_seed, links, room_count


def is_walkable(tile):
    return tile == "."


def save_dungeon(dungeon, path):
    path = Path(path)
    path.write_text("\n".join("".join(row) for row in dungeon) + "\n", encoding="utf-8")
    return path.resolve()


def print_preview(dungeon, lines=12, width=80):
    print(f"# 미리보기 (처음 {lines}행, {width}열)", flush=True)
    for row in dungeon[:lines]:
        print("".join(row[:width]), flush=True)
    if len(dungeon) > lines:
        print(f"# ... ({len(dungeon) - lines}행 더 있음)", flush=True)


def print_exit_summary(
    output_path: Path,
    world,
    *,
    seed: int,
    width: int,
    height: int,
    room_count: int,
    links: int,
    group_count_after: int,
    vignette_margin: int,
    preview_lines: int = 12,
    preview_width: int = 80,
):
    """종료 시 생성 파일명·요약·일부 미리보기."""
    resolved = output_path.resolve()
    print("", flush=True)
    _progress("=== 생성 완료 ===")
    print(f"파일명: {resolved.name}", flush=True)
    print(f"경로:   {resolved}", flush=True)
    print(
        f"seed={seed} | {width}×{height} | 방 {room_count} | "
        f"MST 연결 {links} | 그룹 {group_count_after} | "
        f"외곽 {vignette_margin}칸",
        flush=True,
    )
    print(f"--- 미리보기 (처음 {preview_lines}행, {preview_width}열) ---", flush=True)
    for row in world[:preview_lines]:
        print("".join(row[:preview_width]), flush=True)
    if len(world) > preview_lines:
        print(f"... ({len(world) - preview_lines}행 더 있음 — 파일에서 확인)", flush=True)
    print("---", flush=True)


def count_room_groups(world, min_size=MIN_ROOM_SIZE, room_depth=ROOM_DEPTH):
    rooms = find_rooms(world, min_size=min_size, room_depth=room_depth)
    if len(rooms) <= 1:
        return len(rooms), len(rooms) if rooms else 0
    groups = build_room_groups(world, rooms)
    return len(rooms), len(groups)


def main():
    parser = argparse.ArgumentParser(
        description="PyDunGen4: hybrid dungeon + shortest-path room linking"
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("-o", "--output", default="dungeon_output.txt")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--preview-lines", type=int, default=12)
    parser.add_argument(
        "--attempts",
        type=int,
        default=8,
        help="Random-seed retry count when no seed is specified (default: 8)",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--progress-start", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--min-room-size", type=int, default=MIN_ROOM_SIZE)
    parser.add_argument("--room-depth", type=int, default=ROOM_DEPTH)
    parser.add_argument(
        "--vignette-margin",
        type=int,
        default=VIGNETTE_MARGIN,
        help="Outward vignette+noise band thickness in tiles (default: 30)",
    )
    args = parser.parse_args()

    if not args.worker:
        run_with_retry(args)
        return

    _progress_reset(args.progress_start)
    world, used_seed, links, room_count = generate_world(
        args.seed,
        min_room_size=args.min_room_size,
        room_depth=args.room_depth,
        vignette_margin=args.vignette_margin,
    )

    _progress_step()
    _, group_count_after = count_room_groups(
        world, min_size=args.min_room_size, room_depth=args.room_depth
    )
    _progress_step()

    height = len(world)
    width = len(world[0]) if world else 0

    output_path = Path(args.output)
    if args.output == "dungeon_output.txt":
        output_path = Path(f"dungeon4_{used_seed}.txt")
    _progress_step()
    output_path = save_dungeon(world, output_path)
    _progress_complete()

    if args.full:
        print("--- 전체 맵 ---", flush=True)
        for row in world:
            print("".join(row), flush=True)
        print("---", flush=True)

    print_exit_summary(
        output_path,
        world,
        seed=used_seed,
        width=width,
        height=height,
        room_count=room_count,
        links=links,
        group_count_after=group_count_after,
        vignette_margin=args.vignette_margin,
        preview_lines=args.preview_lines,
    )


def run_with_retry(args):
    """Run generation in a child process so native crashes can be retried."""
    script = Path(__file__).resolve()
    attempts = 1 if args.seed is not None else max(1, args.attempts)
    fallback_seed = 42

    _progress_reset(0)
    for attempt in range(1, attempts + 1):
        if args.seed is not None:
            seed = args.seed
        elif attempt == attempts:
            seed = fallback_seed
        else:
            seed = random.randint(0, 999_999)

        cmd = [
            sys.executable,
            str(script),
            "--worker",
            "--seed",
            str(seed),
            "--vignette-margin",
            str(args.vignette_margin),
            "--min-room-size",
            str(args.min_room_size),
            "--room-depth",
            str(args.room_depth),
            "--preview-lines",
            str(args.preview_lines),
            "--progress-start",
            "0",
        ]
        if args.output != "dungeon_output.txt":
            cmd.extend(["--output", args.output])
        if args.full:
            cmd.append("--full")

        _progress_step()
        result = subprocess.run(
            cmd,
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            _progress_complete()
            for line in result.stdout.splitlines():
                if line.startswith("진행도 :"):
                    continue
                print(line, flush=True)
            return

        if args.seed is not None:
            print("", flush=True)
            _progress(f"seed={seed} 실패 (종료 코드 {result.returncode})")
            break

    print("", flush=True)
    _progress("모든 생성 시도가 실패했습니다. --seed 42처럼 seed를 고정해 다시 시도해 보세요.")


if __name__ == "__main__":
    main()
