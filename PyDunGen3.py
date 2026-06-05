"""9-chunk hybrid dungeon: cave + corridors stitched in a 3x3 grid."""

import argparse
import random
import sys
import types
from collections import deque
from pathlib import Path

try:
    import noise
    import numpy as np
except ImportError as exc:
    print(
        "필요한 패키지가 없습니다. 아래 명령으로 설치 후 다시 실행하세요:\n"
        "  pip install -r requirements.txt",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1) from exc

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load PyDunGen1 functions without running its main block.
_pydungen1_path = Path(__file__).with_name("PyDunGen1.py")
_pydungen1_src = _pydungen1_path.read_text(encoding="utf-8").split("# Create and print")[0]
_pydungen1 = types.ModuleType("PyDunGen1")
exec(compile(_pydungen1_src, str(_pydungen1_path), "exec"), _pydungen1.__dict__)
create_dungeon_with_corridors = _pydungen1.create_dungeon_with_corridors

CHUNK_WIDTH = 80
CHUNK_HEIGHT = 50
GRID_SIZE = 3
BLEND_WIDTH = 10
CA_ITERATIONS = 3

CAVE_THRESHOLD = -0.012
NOISE_SCALE = 10
NOISE_OCTAVES = 6
NOISE_PERSISTENCE = 0.5
NOISE_LACUNARITY = 2.0

MASK_CAVE = 0.35
MASK_STRUCT = 0.65

DIRECTIONS = {
    "N": (0, -1),
    "NE": (1, -1),
    "E": (1, 0),
    "SE": (1, 1),
    "S": (0, 1),
    "SW": (-1, 1),
    "W": (-1, 0),
    "NW": (-1, -1),
}

OPPOSITE = {
    "N": "S",
    "NE": "SW",
    "E": "W",
    "SE": "NW",
    "S": "N",
    "SW": "NE",
    "W": "E",
    "NW": "SE",
}


def generate_global_perlin(
    width,
    height,
    offset_x,
    offset_y,
    scale=NOISE_SCALE,
    octaves=NOISE_OCTAVES,
    persistence=NOISE_PERSISTENCE,
    lacunarity=NOISE_LACUNARITY,
    base=0,
):
    """Generate Perlin noise using world coordinates for seamless chunk borders."""
    world = np.zeros((height, width))
    for y in range(height):
        for x in range(width):
            world[y][x] = noise.pnoise2(
                (y + offset_y) / scale,
                (x + offset_x) / scale,
                octaves=octaves,
                persistence=persistence,
                lacunarity=lacunarity,
                base=base,
            )
    return world


def normalize_noise(noise_map):
    n_min = float(noise_map.min())
    n_max = float(noise_map.max())
    span = n_max - n_min
    if span <= 0:
        return np.zeros_like(noise_map)
    return (noise_map - n_min) / span


def noise_to_cave(noise_map, threshold=CAVE_THRESHOLD):
    height, width = noise_map.shape
    cave = [["#" for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if noise_map[y][x] < threshold:
                cave[y][x] = "."
    return cave


def get_required_exits(cx, cy):
    exits = []
    for name, (dx, dy) in DIRECTIONS.items():
        nx, ny = cx + dx, cy + dy
        if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE:
            exits.append(name)
    return exits


def carve_edge_tunnel(dungeon, direction, width=6):
    """Carve a floor tunnel on the chunk edge toward a neighbor."""
    h = len(dungeon)
    w = len(dungeon[0])
    cx, cy = w // 2, h // 2
    half = width // 2

    if direction == "N":
        y = 0
        for x in range(max(0, cx - half), min(w, cx + half + 1)):
            dungeon[y][x] = "."
            if y + 1 < h:
                dungeon[y + 1][x] = "."
    elif direction == "S":
        y = h - 1
        for x in range(max(0, cx - half), min(w, cx + half + 1)):
            dungeon[y][x] = "."
            if y - 1 >= 0:
                dungeon[y - 1][x] = "."
    elif direction == "W":
        x = 0
        for y in range(max(0, cy - half), min(h, cy + half + 1)):
            dungeon[y][x] = "."
            if x + 1 < w:
                dungeon[y][x + 1] = "."
    elif direction == "E":
        x = w - 1
        for y in range(max(0, cy - half), min(h, cy + half + 1)):
            dungeon[y][x] = "."
            if x - 1 >= 0:
                dungeon[y][x - 1] = "."
    elif direction == "NE":
        for i in range(min(width + 4, w, h)):
            x = w - 1 - i
            y = i
            if 0 <= x < w and 0 <= y < h:
                dungeon[y][x] = "."
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        dungeon[ny][nx] = "."
    elif direction == "NW":
        for i in range(min(width + 4, w, h)):
            x = i
            y = i
            if 0 <= x < w and 0 <= y < h:
                dungeon[y][x] = "."
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        dungeon[ny][nx] = "."
    elif direction == "SE":
        for i in range(min(width + 4, w, h)):
            x = w - 1 - i
            y = h - 1 - i
            if 0 <= x < w and 0 <= y < h:
                dungeon[y][x] = "."
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        dungeon[ny][nx] = "."
    elif direction == "SW":
        for i in range(min(width + 4, w, h)):
            x = i
            y = h - 1 - i
            if 0 <= x < w and 0 <= y < h:
                dungeon[y][x] = "."
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        dungeon[ny][nx] = "."
    return dungeon


def carve_emergency_tunnel(dungeon, direction, width=8):
    """Widen an edge tunnel when connectivity check fails."""
    return carve_edge_tunnel(dungeon, direction, width=width)


def apply_noise_mask(cave, structured, norm_noise):
    height = len(cave)
    width = len(cave[0])
    result = [["#" for _ in range(width)] for _ in range(height)]

    for y in range(height):
        for x in range(width):
            n = norm_noise[y][x]
            c_floor = cave[y][x] == "."
            s_floor = structured[y][x] == "."

            if n < MASK_CAVE:
                if c_floor:
                    result[y][x] = "."
                elif s_floor:
                    result[y][x] = "."
            elif n < MASK_STRUCT:
                if c_floor or s_floor:
                    result[y][x] = "."
            else:
                if s_floor:
                    result[y][x] = "."
                elif c_floor:
                    result[y][x] = "."
    return result


def overlay_structure(cave, structured):
    height = len(cave)
    width = len(cave[0])
    overlay = [row[:] for row in cave]
    for y in range(height):
        for x in range(width):
            if structured[y][x] == ".":
                overlay[y][x] = "."
    return overlay


def create_hybrid_chunk(cx, cy, seed, required_exits, noise_base=0):
    """Build one 80x50 chunk: cave layer, structure overlay, noise mask, edge tunnels."""
    random.seed(seed)
    offset_x = cx * CHUNK_WIDTH
    offset_y = cy * CHUNK_HEIGHT

    noise_map = generate_global_perlin(
        CHUNK_WIDTH,
        CHUNK_HEIGHT,
        offset_x,
        offset_y,
        base=noise_base,
    )
    norm_noise = normalize_noise(noise_map)
    cave = noise_to_cave(noise_map, CAVE_THRESHOLD)

    random.seed(seed + 1000)
    structured = create_dungeon_with_corridors(CHUNK_WIDTH, CHUNK_HEIGHT, 18, 4, 8)
    layered = overlay_structure(cave, structured)
    hybrid = apply_noise_mask(cave, structured, norm_noise)

    for y in range(CHUNK_HEIGHT):
        for x in range(CHUNK_WIDTH):
            if layered[y][x] == ".":
                hybrid[y][x] = "."

    for direction in required_exits:
        carve_edge_tunnel(hybrid, direction)

    return hybrid, norm_noise


def tile_value(dungeon, x, y):
    h = len(dungeon)
    w = len(dungeon[0])
    if 0 <= x < w and 0 <= y < h:
        return 1.0 if dungeon[y][x] == "." else 0.0
    return 0.0


def sample_global_noise(world_x, world_y, noise_base):
    return noise.pnoise2(
        world_y / NOISE_SCALE,
        world_x / NOISE_SCALE,
        octaves=NOISE_OCTAVES,
        persistence=NOISE_PERSISTENCE,
        lacunarity=NOISE_LACUNARITY,
        base=noise_base,
    )


def blend_vertical_boundary(world, left_cx, noise_base):
    """Blend columns at the boundary between left_cx and left_cx+1 chunks."""
    boundary_x = (left_cx + 1) * CHUNK_WIDTH
    world_h = len(world)
    start_x = boundary_x - BLEND_WIDTH
    end_x = boundary_x + BLEND_WIDTH

    for y in range(world_h):
        for x in range(start_x, end_x):
            if x < 0 or x >= len(world[0]):
                continue
            dist = abs(x - boundary_x)
            t = dist / BLEND_WIDTH
            left_val = tile_value(world, x if x < boundary_x else boundary_x - 1, y)
            right_val = tile_value(world, x if x >= boundary_x else boundary_x, y)
            blend = (1 - t) * left_val + t * right_val
            perturb = sample_global_noise(x, y, noise_base) * 0.1
            world[y][x] = "." if blend + perturb > 0.5 else "#"


def blend_horizontal_boundary(world, top_cy, noise_base):
    """Blend rows at the boundary between top_cy and top_cy+1 chunks."""
    boundary_y = (top_cy + 1) * CHUNK_HEIGHT
    world_w = len(world[0])
    start_y = boundary_y - BLEND_WIDTH
    end_y = boundary_y + BLEND_WIDTH

    for y in range(start_y, end_y):
        if y < 0 or y >= len(world):
            continue
        for x in range(world_w):
            dist = abs(y - boundary_y)
            t = dist / BLEND_WIDTH
            top_val = tile_value(world, x, y if y < boundary_y else boundary_y - 1)
            bottom_val = tile_value(world, x, y if y >= boundary_y else boundary_y)
            blend = (1 - t) * top_val + t * bottom_val
            perturb = sample_global_noise(x, y, noise_base) * 0.1
            world[y][x] = "." if blend + perturb > 0.5 else "#"


def blend_diagonal_corner(world, cx, cy, noise_base):
    """Smooth the corner where four chunks meet (or two diagonally adjacent)."""
    corner_x = (cx + 1) * CHUNK_WIDTH
    corner_y = (cy + 1) * CHUNK_HEIGHT
    patch = BLEND_WIDTH

    for dy in range(-patch, patch):
        for dx in range(-patch, patch):
            x = corner_x + dx
            y = corner_y + dy
            if not (0 <= x < len(world[0]) and 0 <= y < len(world)):
                continue
            dist = (dx * dx + dy * dy) ** 0.5 / patch
            if dist > 1.0:
                continue
            floor_count = sum(
                tile_value(world, x + ox, y + oy)
                for ox in (-1, 0, 1)
                for oy in (-1, 0, 1)
            )
            perturb = sample_global_noise(x, y, noise_base) * 0.08
            threshold = 0.45 - dist * 0.15 + perturb
            world[y][x] = "." if floor_count / 9.0 > threshold else "#"


def carve_diagonal_path(world, x0, y0, x1, y1):
    """Ensure a walkable diagonal corridor between two world points."""
    x, y = x0, y0
    while x != x1 or y != y1:
        if 0 <= x < len(world[0]) and 0 <= y < len(world):
            world[y][x] = "."
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < len(world[0]) and 0 <= ny < len(world):
                    world[ny][nx] = "."
        if x < x1:
            x += 1
        elif x > x1:
            x -= 1
        if y < y1:
            y += 1
        elif y > y1:
            y -= 1


def apply_corner_diagonal_links(world, noise_base):
    """Connect center chunk corners to diagonal neighbors."""
    center_cx, center_cy = 1, 1
    links = {
        "NE": ((2 * CHUNK_WIDTH - 1, CHUNK_HEIGHT), (CHUNK_WIDTH, CHUNK_HEIGHT - 1)),
        "NW": ((CHUNK_WIDTH, CHUNK_HEIGHT), (0, CHUNK_HEIGHT - 1)),
        "SE": ((2 * CHUNK_WIDTH - 1, 2 * CHUNK_HEIGHT - 1), (CHUNK_WIDTH, 2 * CHUNK_HEIGHT - 1)),
        "SW": ((CHUNK_WIDTH, 2 * CHUNK_HEIGHT - 1), (0, 2 * CHUNK_HEIGHT - 1)),
    }
    for (x0, y0), (x1, y1) in links.values():
        carve_diagonal_path(world, x0, y0, x1, y1)

    for cx in range(GRID_SIZE - 1):
        for cy in range(GRID_SIZE - 1):
            blend_diagonal_corner(world, cx, cy, noise_base)


def cellular_automata_smooth(world, iterations=CA_ITERATIONS):
    height = len(world)
    width = len(world[0])

    for _ in range(iterations):
        new_world = [row[:] for row in world]
        for y in range(height):
            for x in range(width):
                floor_neighbors = 0
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if not (dx or dy):
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < width and 0 <= ny < height and world[ny][nx] == ".":
                            floor_neighbors += 1
                if world[y][x] == "#" and floor_neighbors >= 5:
                    new_world[y][x] = "."
                elif world[y][x] == "." and floor_neighbors <= 2:
                    new_world[y][x] = "#"
        world = new_world
    return world


def paste_chunk(world, chunk, cx, cy):
    for y in range(CHUNK_HEIGHT):
        for x in range(CHUNK_WIDTH):
            world[cy * CHUNK_HEIGHT + y][cx * CHUNK_WIDTH + x] = chunk[y][x]


def stitch_3x3(chunks, noise_base):
    world_w = GRID_SIZE * CHUNK_WIDTH
    world_h = GRID_SIZE * CHUNK_HEIGHT
    world = [["#" for _ in range(world_w)] for _ in range(world_h)]

    for cy in range(GRID_SIZE):
        for cx in range(GRID_SIZE):
            paste_chunk(world, chunks[(cx, cy)], cx, cy)

    for cx in range(GRID_SIZE - 1):
        blend_vertical_boundary(world, cx, noise_base)

    for cy in range(GRID_SIZE - 1):
        blend_horizontal_boundary(world, cy, noise_base)

    apply_corner_diagonal_links(world, noise_base)
    world = cellular_automata_smooth(world)
    return world


def chunk_center_world(cx, cy):
    return cx * CHUNK_WIDTH + CHUNK_WIDTH // 2, cy * CHUNK_HEIGHT + CHUNK_HEIGHT // 2


def chunk_bounds(cx, cy):
    x0 = cx * CHUNK_WIDTH
    y0 = cy * CHUNK_HEIGHT
    return x0, y0, x0 + CHUNK_WIDTH - 1, y0 + CHUNK_HEIGHT - 1


def find_nearest_floor(world, x, y, max_radius=20):
    world_w = len(world[0])
    world_h = len(world)
    if 0 <= x < world_w and 0 <= y < world_h and world[y][x] == ".":
        return x, y
    for radius in range(1, max_radius + 1):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < world_w and 0 <= ny < world_h and world[ny][nx] == ".":
                    return nx, ny
    return x, y


def bfs_reaches_chunk(world, start, ncx, ncy):
    """Return True if any floor tile in neighbor chunk is reachable from start."""
    world_w = len(world[0])
    world_h = len(world)
    sx, sy = start
    if not (0 <= sx < world_w and 0 <= sy < world_h):
        return False
    if world[sy][sx] != ".":
        sx, sy = find_nearest_floor(world, sx, sy)
        if world[sy][sx] != ".":
            return False

    x0, y0, x1, y1 = chunk_bounds(ncx, ncy)
    queue = deque([(sx, sy)])
    visited = {(sx, sy)}

    while queue:
        x, y = queue.popleft()
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < world_w and 0 <= ny < world_h and (nx, ny) not in visited:
                if world[ny][nx] == ".":
                    visited.add((nx, ny))
                    queue.append((nx, ny))
    return False


def carve_world_corridor(world, x0, y0, x1, y1, width=3):
    """Carve an L-shaped corridor between two world coordinates."""
    half = width // 2
    x, y = x0, y0
    while x != x1:
        for w in range(-half, half + 1):
            cy = y + w
            if 0 <= x < len(world[0]) and 0 <= cy < len(world):
                world[cy][x] = "."
        x += 1 if x1 > x0 else -1
    while y != y1:
        for w in range(-half, half + 1):
            cx = x + w
            if 0 <= cx < len(world[0]) and 0 <= y < len(world):
                world[y][cx] = "."
        y += 1 if y1 > y0 else -1
    for w in range(-half, half + 1):
        for dw in range(-half, half + 1):
            cx, cy = x1 + w, y1 + dw
            if 0 <= cx < len(world[0]) and 0 <= cy < len(world):
                world[cy][cx] = "."


def direction_to_neighbor(cx, cy, direction):
    dx, dy = DIRECTIONS[direction]
    return cx + dx, cy + dy


def verify_and_repair_connectivity(chunks, noise_base=0, max_retries=3):
    """Ensure center chunk connects to all 8 neighbors; widen tunnels if needed."""
    center = (1, 1)
    center_start = chunk_center_world(*center)
    neighbor_dirs = get_required_exits(*center)

    for attempt in range(max_retries):
        world = stitch_3x3(chunks, noise_base=noise_base)
        start = find_nearest_floor(world, *center_start)
        missing_directions = [
            direction
            for direction in neighbor_dirs
            if not bfs_reaches_chunk(world, start, *direction_to_neighbor(*center, direction))
        ]
        if not missing_directions:
            return world, chunks

        for direction in missing_directions:
            carve_emergency_tunnel(chunks[center], direction, width=10 + attempt * 2)
            opposite = OPPOSITE[direction]
            ncx, ncy = direction_to_neighbor(*center, direction)
            carve_emergency_tunnel(chunks[(ncx, ncy)], opposite, width=10 + attempt * 2)

    world = stitch_3x3(chunks, noise_base=noise_base)
    start = find_nearest_floor(world, *center_start)
    for direction in neighbor_dirs:
        ncx, ncy = direction_to_neighbor(*center, direction)
        if not bfs_reaches_chunk(world, start, ncx, ncy):
            goal = chunk_center_world(ncx, ncy)
            goal = find_nearest_floor(world, *goal)
            carve_world_corridor(world, start[0], start[1], goal[0], goal[1], width=4)

    return world, chunks


def generate_world(seed=None):
    if seed is None:
        seed = random.randint(0, 999999)
    random.seed(seed)

    chunks = {}
    for cy in range(GRID_SIZE):
        for cx in range(GRID_SIZE):
            exits = get_required_exits(cx, cy)
            chunk_seed = seed + cx * 100 + cy * 1000
            chunk, _ = create_hybrid_chunk(cx, cy, chunk_seed, exits, noise_base=seed)
            chunks[(cx, cy)] = chunk

    world, _ = verify_and_repair_connectivity(chunks, noise_base=seed)
    return world, seed


def print_dungeon(dungeon, file=None, flush=True):
    out = file or sys.stdout
    for row in dungeon:
        print("".join(row), file=out, flush=flush)


def save_dungeon(dungeon, path):
    path = Path(path)
    path.write_text("\n".join("".join(row) for row in dungeon) + "\n", encoding="utf-8")
    return path.resolve()


def print_preview(dungeon, lines=12, width=80):
    """Print a small preview that fits typical terminal widths."""
    print(f"# 미리보기 (처음 {lines}행, {width}열)", flush=True)
    for row in dungeon[:lines]:
        print("".join(row[:width]), flush=True)
    if len(dungeon) > lines:
        print(f"# ... ({len(dungeon) - lines}행 더 있음)", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Generate 9-chunk hybrid dungeon (240x150)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible output")
    parser.add_argument(
        "--output",
        "-o",
        default="dungeon_output.txt",
        help="Save full map to this file (default: dungeon_output.txt)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the entire 240x150 map to the console (very long)",
    )
    parser.add_argument(
        "--preview-lines",
        type=int,
        default=12,
        help="Preview line count for console (default: 12)",
    )
    args = parser.parse_args()

    print("던전 생성 중...", flush=True)
    world, used_seed = generate_world(args.seed)
    height = len(world)
    width = len(world[0]) if world else 0

    output_path = Path(args.output)
    if args.output == "dungeon_output.txt":
        output_path = Path(f"dungeon_{used_seed}.txt")
    output_path = save_dungeon(world, output_path)

    print(f"# seed={used_seed}", flush=True)
    print(f"# 크기={width}x{height}", flush=True)
    print(f"# 전체 맵 저장: {output_path}", flush=True)
    print_preview(world, lines=args.preview_lines)

    if args.full:
        print("# 전체 맵 출력:", flush=True)
        print_dungeon(world)

    print(
        "\n완료. 터미널에는 미리보기만 표시됩니다. "
        f"전체 맵은 파일을 여세요: {output_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
