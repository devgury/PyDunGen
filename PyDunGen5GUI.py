"""PyDunGen5 GUI: standalone visual procedural dungeon generator.

This file intentionally does not import PyDunGen1~4.  The generation logic is
copied/reworked here so existing files keep their current shape.
"""

from __future__ import annotations

import math
import random
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog
from tkinter import ttk

try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


CELL_PIXELS = 2
DEFAULT_WIDTH = 240
DEFAULT_HEIGHT = 150

TILE_RGB = {
    "#": (28, 28, 42),
    ".": (205, 214, 244),
    "c": (139, 92, 46),    # natural cave
    "r": (80, 190, 120),   # dungeon room
    "y": (230, 210, 80),   # dungeon corridor / connector
    ":": (69, 71, 90),
    "%": (88, 91, 112),
    "+": (108, 112, 134),
}
WALKABLE_TILES = {".", "c", "r", "y"}


@dataclass
class Room:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    @property
    def width(self) -> int:
        return self.x2 - self.x1 + 1

    @property
    def height(self) -> int:
        return self.y2 - self.y1 + 1

    def moved_to(self, x1: int, y1: int) -> "Room":
        return Room(x1, y1, x1 + self.width - 1, y1 + self.height - 1)

    def intersects(self, other: "Room", pad: int = 1) -> bool:
        return not (
            self.x2 + pad < other.x1
            or self.x1 - pad > other.x2
            or self.y2 + pad < other.y1
            or self.y1 - pad > other.y2
        )


class DungeonModel:
    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT):
        self.width = width
        self.height = height
        self.grid = self.blank(width, height)
        self.cave_base = [row[:] for row in self.grid]
        self.room_base = [row[:] for row in self.grid]
        self.connect_base = [row[:] for row in self.grid]
        self.rooms: list[Room] = []
        self.seed = 0

    @staticmethod
    def blank(width: int, height: int) -> list[list[str]]:
        return [["#" for _ in range(width)] for _ in range(height)]

    def resize(self, width: int, height: int) -> None:
        if width == self.width and height == self.height:
            return
        self.width = width
        self.height = height
        self.grid = self.blank(width, height)
        self.cave_base = [row[:] for row in self.grid]
        self.room_base = [row[:] for row in self.grid]
        self.connect_base = [row[:] for row in self.grid]
        self.rooms = []

    def reset(self) -> None:
        self.grid = self.blank(self.width, self.height)
        self.cave_base = [row[:] for row in self.grid]
        self.room_base = [row[:] for row in self.grid]
        self.connect_base = [row[:] for row in self.grid]
        self.rooms = []
        self.seed = random.randint(0, 999_999)

    def remember_cave_base(self) -> None:
        self.cave_base = [row[:] for row in self.grid]
        self.room_base = [row[:] for row in self.grid]
        self.connect_base = [row[:] for row in self.grid]

    def restore_cave_base(self) -> None:
        if len(self.cave_base) != self.height or len(self.cave_base[0]) != self.width:
            self.reset()
        self.grid = [row[:] for row in self.cave_base]
        self.rooms = []

    def remember_room_base(self) -> None:
        self.room_base = [row[:] for row in self.grid]
        self.connect_base = [row[:] for row in self.grid]

    def restore_room_base(self) -> None:
        if len(self.room_base) != self.height or len(self.room_base[0]) != self.width:
            self.restore_cave_base()
            return
        self.grid = [row[:] for row in self.room_base]

    def remember_connect_base(self) -> None:
        self.connect_base = [row[:] for row in self.grid]

    def restore_connect_base(self) -> None:
        if len(self.connect_base) != self.height or len(self.connect_base[0]) != self.width:
            self.restore_room_base()
            return
        self.grid = [row[:] for row in self.connect_base]

    def rebuild_rooms_from_cave(self, skip_index: int | None = None) -> None:
        self.grid = [row[:] for row in self.cave_base]
        for index, room in enumerate(self.rooms):
            if index == skip_index:
                continue
            carve_room(self.grid, room)


def smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def hash_noise(ix: int, iy: int, seed: int) -> float:
    n = ix * 374761393 + iy * 668265263 + seed * 1442695041
    n = (n ^ (n >> 13)) * 1274126177
    n ^= n >> 16
    return (n & 0xFFFFFFFF) / 0xFFFFFFFF


def value_noise(x: float, y: float, seed: int) -> float:
    x0 = math.floor(x)
    y0 = math.floor(y)
    tx = smoothstep(x - x0)
    ty = smoothstep(y - y0)

    a = hash_noise(x0, y0, seed)
    b = hash_noise(x0 + 1, y0, seed)
    c = hash_noise(x0, y0 + 1, seed)
    d = hash_noise(x0 + 1, y0 + 1, seed)
    top = a + (b - a) * tx
    bottom = c + (d - c) * tx
    return top + (bottom - top) * ty


def fractal_noise(x: float, y: float, seed: int, octaves: int) -> float:
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    norm = 0.0
    for octave in range(max(1, octaves)):
        total += value_noise(x * frequency, y * frequency, seed + octave * 1013) * amplitude
        norm += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return total / max(0.0001, norm)


def carve_disk(
    grid: list[list[str]],
    cx: int,
    cy: int,
    radius: int,
    tile: str = ".",
    preserve: set[str] | None = None,
) -> None:
    height = len(grid)
    width = len(grid[0])
    r2 = radius * radius
    preserve = preserve or set()
    for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(width, cx + radius + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2 and grid[y][x] not in preserve:
                grid[y][x] = tile


def carve_corridor(
    grid: list[list[str]],
    start: tuple[int, int],
    end: tuple[int, int],
    width: int,
    rng: random.Random,
    tile: str = "y",
) -> None:
    x, y = start
    tx, ty = end
    radius = max(0, width // 2)

    horizontal_first = rng.random() < 0.5
    points = [(tx, y), (tx, ty)] if horizontal_first else [(x, ty), (tx, ty)]
    for px, py in points:
        while x != px:
            x += 1 if px > x else -1
            carve_disk(grid, x, y, radius, tile=tile, preserve={"r"})
        while y != py:
            y += 1 if py > y else -1
            carve_disk(grid, x, y, radius, tile=tile, preserve={"r"})


def carve_line(
    grid: list[list[str]],
    start: tuple[int, int],
    end: tuple[int, int],
    width: int,
    tile: str = "y",
    corner_mode: str = "HV",
) -> None:
    for x, y in corridor_tiles(grid, start, end, width, preserve={"r"}, corner_mode=corner_mode):
        grid[y][x] = tile


def orthogonal_path(
    start: tuple[int, int],
    end: tuple[int, int],
    corner_mode: str = "HV",
) -> list[tuple[int, int]]:
    """Diagonal-looking stair-step path with no 4-neighbor gaps."""
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x1 >= x0 else -1
    sy = 1 if y1 >= y0 else -1

    x, y = x0, y0
    points = [(x, y)]

    if dx >= dy:
        error = dx // 2
        while x != x1:
            prev_x, prev_y = x, y
            x += sx
            error -= dy
            if error < 0:
                # Bridge the diagonal step with one orthogonal tile first.
                points.append((x, prev_y))
                y += sy
                error += dx
            points.append((x, y))
    else:
        error = dy // 2
        while y != y1:
            prev_x, prev_y = x, y
            y += sy
            error -= dx
            if error < 0:
                # Bridge the diagonal step with one orthogonal tile first.
                points.append((prev_x, y))
                x += sx
                error += dy
            points.append((x, y))
    return points


def corridor_tiles(
    grid: list[list[str]],
    start: tuple[int, int],
    end: tuple[int, int],
    width: int,
    preserve: set[str] | None = None,
    corner_mode: str = "HV",
) -> set[tuple[int, int]]:
    height = len(grid)
    grid_width = len(grid[0])
    radius = max(0, width // 2)
    preserve = preserve or set()
    tiles: set[tuple[int, int]] = set()

    for cx, cy in orthogonal_path(start, end, corner_mode=corner_mode):
        for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
            for x in range(max(0, cx - radius), min(grid_width, cx + radius + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                    if grid[y][x] not in preserve:
                        tiles.add((x, y))
    return tiles


def generate_cave(
    model: DungeonModel,
    *,
    scale: int,
    threshold: int,
    octaves: int,
    smooth: int,
) -> None:
    rng = random.Random()
    model.seed = rng.randint(0, 999_999)
    grid = model.blank(model.width, model.height)
    scale = max(4, scale)
    threshold_f = threshold / 100.0

    for y in range(model.height):
        for x in range(model.width):
            nx = x / scale
            ny = y / scale
            n = fractal_noise(nx, ny, model.seed, octaves)
            grid[y][x] = "c" if n > threshold_f else "#"

    for _ in range(max(0, smooth)):
        grid = smooth_cave(grid)

    model.grid = grid
    model.rooms = []


def smooth_cave(grid: list[list[str]]) -> list[list[str]]:
    height = len(grid)
    width = len(grid[0])
    out = [row[:] for row in grid]
    for y in range(height):
        for x in range(width):
            walls = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or grid[ny][nx] == "#":
                        walls += 1
            out[y][x] = "#" if walls >= 5 else "c"
    return out


def generate_rooms(
    model: DungeonModel,
    *,
    room_count: int,
    min_size: int,
    max_size: int,
    overlay: bool,
) -> None:
    rng = random.Random(random.randint(0, 999_999))
    if not overlay:
        model.grid = model.blank(model.width, model.height)
        model.rooms = []

    rooms: list[Room] = []
    attempts = room_count * 12
    for _ in range(attempts):
        if len(rooms) >= room_count:
            break
        w = rng.randint(min_size, max(min_size, max_size))
        h = rng.randint(min_size, max(min_size, max_size))
        if model.width - w - 2 <= 2 or model.height - h - 2 <= 2:
            continue
        x = rng.randint(2, model.width - w - 2)
        y = rng.randint(2, model.height - h - 2)
        room = Room(x, y, x + w - 1, y + h - 1)
        if any(room.intersects(existing, pad=2) for existing in rooms):
            continue
        carve_room(model.grid, room)
        rooms.append(room)

    model.rooms = rooms


def carve_room(grid: list[list[str]], room: Room) -> None:
    for y in range(room.y1, room.y2 + 1):
        for x in range(room.x1, room.x2 + 1):
            grid[y][x] = "r"


def floor_components(
    grid: list[list[str]],
    min_size: int,
) -> list[list[tuple[int, int]]]:
    height = len(grid)
    width = len(grid[0])
    seen = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []

    for sy in range(height):
        for sx in range(width):
            if seen[sy][sx] or grid[sy][sx] not in WALKABLE_TILES:
                continue
            queue = deque([(sx, sy)])
            seen[sy][sx] = True
            tiles: list[tuple[int, int]] = []
            while queue:
                x, y = queue.popleft()
                tiles.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx = x + dx
                    ny = y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        if not seen[ny][nx] and grid[ny][nx] in WALKABLE_TILES:
                            seen[ny][nx] = True
                            queue.append((nx, ny))
            if len(tiles) >= min_size:
                components.append(tiles)
    return components


def centroid(component: list[tuple[int, int]]) -> tuple[int, int]:
    sx = sum(x for x, _ in component)
    sy = sum(y for _, y in component)
    n = max(1, len(component))
    return sx // n, sy // n


def connect_regions(
    model: DungeonModel,
    *,
    min_region_size: int,
    corridor_width: int,
    max_links: int,
) -> int:
    components = floor_components(model.grid, min_region_size)
    if len(components) <= 1:
        return 0

    rng = random.Random(model.seed + 55)
    centers = [centroid(component) for component in components]
    connected = {0}
    links = 0

    while len(connected) < len(centers) and links < max_links:
        best: tuple[int, int, int] | None = None
        for i in connected:
            ax, ay = centers[i]
            for j, (bx, by) in enumerate(centers):
                if j in connected:
                    continue
                dist = abs(ax - bx) + abs(ay - by)
                if best is None or dist < best[0]:
                    best = (dist, i, j)
        if best is None:
            break
        _, i, j = best
        carve_corridor(model.grid, centers[i], centers[j], corridor_width, rng)
        connected.add(j)
        links += 1
    return links


def apply_vignette(
    model: DungeonModel,
    *,
    margin: int,
    strength: int,
    border: int,
    shape: str,
    angle: int,
) -> None:
    margin = max(border + 1, margin)
    strength_f = max(0.0, min(1.0, strength / 100.0))
    seed = model.seed or random.randint(0, 999_999)

    for y in range(model.height):
        for x in range(model.width):
            edge = shape_edge_distance(x, y, model.width, model.height, shape, angle)
            if edge < border:
                model.grid[y][x] = "#"
                continue
            if edge >= margin:
                continue
            zone = 1.0 - ((edge - border) / max(1, margin - border))
            n = fractal_noise(x / 14.0, y / 14.0, seed + 9000, 3)
            wall_strength = zone * strength_f + n * 0.35
            if wall_strength > 0.62:
                model.grid[y][x] = "#"
            elif wall_strength > 0.48 and model.grid[y][x] in WALKABLE_TILES:
                model.grid[y][x] = ":" if n < 0.5 else "%"


def shape_edge_distance(
    x: int,
    y: int,
    width: int,
    height: int,
    shape: str,
    angle: int,
) -> float:
    """Approximate distance from a rotated shape boundary in tile units."""
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    radius = max(1.0, min(width, height) / 2.0)
    dx = x - cx
    dy = y - cy
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    rx = dx * cos_a + dy * sin_a
    ry = -dx * sin_a + dy * cos_a

    if shape == "사각형":
        nx = rx / max(1.0, width / 2.0)
        ny = ry / max(1.0, height / 2.0)
        metric = max(abs(nx), abs(ny))
        return (1.0 - metric) * radius
    if shape == "원형":
        metric = math.sqrt(rx * rx + ry * ry) / radius
        return (1.0 - metric) * radius
    if shape == "다이아몬드":
        metric = (abs(rx) + abs(ry)) / radius
        return (1.0 - metric) * radius

    # Up-pointing equilateral-like triangle, then rotated by angle.
    nx = rx / radius
    ny = ry / radius
    metric = max(ny, 2.0 * nx - ny, -2.0 * nx - ny)
    return (1.0 - metric) * radius * 0.7


class PyDunGen5GUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PyDunGen5 — Visual Dungeon Generator")
        self.geometry("1160x760")
        self.minsize(940, 620)

        self.model = DungeonModel()
        self.slider_vars: dict[str, tk.Variable] = {}
        self.slider_ranges: dict[str, tuple[int, int]] = {}
        self.value_labels: dict[str, ttk.Label] = {}
        self.preview_image = None
        self.manual_draw = False
        self.delete_corridor = False
        self.last_draw_tile: tuple[int, int] | None = None
        self.manual_preview_tiles: set[tuple[int, int]] = set()
        self.corridor_corner_mode = "HV"
        self.room_move = False
        self.moving_room_index: int | None = None
        self.moving_room_offset: tuple[int, int] = (0, 0)
        self.moving_room_preview: Room | None = None

        self._build_ui()
        self.render_preview()

    def _build_ui(self) -> None:
        controls = ttk.Frame(self, padding=6)
        controls.pack(fill=tk.X)
        for col in range(4):
            controls.columnconfigure(col, weight=1)

        self._build_cave_panel(controls, 0)
        self._build_rooms_panel(controls, 1)
        self._build_connect_panel(controls, 2)
        self._build_border_panel(controls, 3)

        preview_bar = ttk.Frame(self, padding=(6, 0))
        preview_bar.pack(fill=tk.X)
        ttk.Button(preview_bar, text="익스포트", command=self.export_map, width=12).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(preview_bar, text="전체 클리어", command=self.clear_all, width=12).pack(
            side=tk.RIGHT, padx=4
        )
        self.status = ttk.Label(preview_bar, text="2×2 픽셀 프리뷰 — 단계별 생성 버튼을 눌러보세요")
        self.status.pack(side=tk.LEFT)
        ttk.Label(
            preview_bar,
            text="색상: 자연동굴=갈색 | 방=초록 | 복도/연결=노랑",
        ).pack(side=tk.LEFT, padx=16)

        frame = ttk.Frame(self, padding=6)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg="#11111b", highlightthickness=0)
        h_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        v_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self.on_canvas_press)
        self.canvas.bind("<Button-3>", self.on_canvas_right_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.bind("<Escape>", self.cancel_manual_preview)
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _build_cave_panel(self, parent, col: int) -> None:
        panel = self._panel(parent, "1. 자연동굴", col)
        self._slider(panel, "동굴 스케일", "cave_scale", 5, 36, 14)
        self._slider(panel, "바닥 임계값", "cave_threshold", 20, 75, 47)
        self._slider(panel, "노이즈 옥타브", "cave_octaves", 1, 6, 4)
        self._slider(panel, "부드럽게", "cave_smooth", 0, 6, 2)
        ttk.Button(
            panel,
            text="슬라이더 랜덤",
            command=lambda: self.randomize_sliders(
                ["cave_scale", "cave_threshold", "cave_octaves", "cave_smooth"],
                "자연동굴",
            ),
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(panel, text="동굴 생성", command=self.generate_cave_step).pack(fill=tk.X, pady=(6, 0))

    def _build_rooms_panel(self, parent, col: int) -> None:
        panel = self._panel(parent, "2. 던전 방", col)
        self._slider(panel, "방 개수", "room_count", 4, 60, 24)
        self._slider(panel, "방 최소", "room_min", 3, 14, 5)
        self._slider(panel, "방 최대", "room_max", 5, 24, 12)
        self.var_room_overlay = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text="현재 맵 위에 추가", variable=self.var_room_overlay).pack(anchor=tk.W)
        ttk.Button(
            panel,
            text="슬라이더 랜덤",
            command=lambda: self.randomize_sliders(
                ["room_count", "room_min", "room_max"],
                "던전 방",
            ),
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(panel, text="방 생성", command=self.generate_rooms_step).pack(fill=tk.X, pady=(6, 0))
        self.btn_room_move = ttk.Button(panel, text="방 이동 OFF", command=self.toggle_room_move)
        self.btn_room_move.pack(fill=tk.X, pady=(4, 0))

    def _build_connect_panel(self, parent, col: int) -> None:
        panel = self._panel(parent, "3. 고립 방 연결", col)
        self._slider(panel, "최소 영역", "connect_min_region", 10, 180, 30)
        self._slider(panel, "연결 폭", "connect_width", 1, 8, 1)
        self._slider(panel, "최대 연결", "connect_max_links", 1, 80, 24)
        ttk.Label(panel, text="인접한 바닥 영역을 가까운 순서로 연결").pack(anchor=tk.W, pady=(4, 0))
        ttk.Button(
            panel,
            text="슬라이더 랜덤",
            command=lambda: self.randomize_sliders(
                ["connect_min_region", "connect_width", "connect_max_links"],
                "고립 방 연결",
            ),
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(panel, text="고립 방 연결", command=self.connect_step).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(panel, text="모든 방 연결", command=self.connect_all_step).pack(fill=tk.X, pady=(4, 0))
        self.btn_manual_draw = ttk.Button(
            panel, text="수동 그리기 OFF", command=self.toggle_manual_draw
        )
        self.btn_manual_draw.pack(fill=tk.X, pady=(4, 0))
        self.btn_delete_corridor = ttk.Button(
            panel, text="복도 1개씩 지우기 OFF", command=self.toggle_delete_corridor
        )
        self.btn_delete_corridor.pack(fill=tk.X, pady=(4, 0))

    def _build_border_panel(self, parent, col: int) -> None:
        panel = self._panel(parent, "4. 외곽 / 크기", col)
        ttk.Label(panel, text="크기 프리셋").pack(anchor=tk.W)
        self.var_size_preset = tk.StringVar(value="240x240")
        preset_row = ttk.Frame(panel)
        preset_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Combobox(
            preset_row,
            textvariable=self.var_size_preset,
            values=("80x25", "80x80", "240x240"),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(preset_row, text="적용", command=self.apply_size_preset, width=6).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        self._slider(panel, "너비", "map_width", 80, 768, DEFAULT_WIDTH)
        self._slider(panel, "높이", "map_height", 25, 768, DEFAULT_HEIGHT)
        ttk.Label(panel, text="비네팅 형태").pack(anchor=tk.W, pady=(4, 0))
        self.var_vignette_shape = tk.StringVar(value="사각형")
        ttk.Combobox(
            panel,
            textvariable=self.var_vignette_shape,
            values=("사각형", "원형", "다이아몬드", "삼각형"),
            state="readonly",
            height=4,
        ).pack(fill=tk.X)
        self._slider(panel, "비네팅 폭", "vignette_margin", 4, 50, 24)
        self._slider(panel, "비네팅 강도", "vignette_strength", 10, 100, 75)
        self._slider(panel, "고정 테두리", "solid_border", 1, 8, 2)
        self._slider(panel, "각도", "vignette_angle", 0, 359, 0)
        ttk.Button(
            panel,
            text="슬라이더 랜덤",
            command=lambda: self.randomize_sliders(
                [
                    "map_width",
                    "map_height",
                    "vignette_margin",
                    "vignette_strength",
                    "solid_border",
                    "vignette_angle",
                ],
                "외곽 / 크기",
            ),
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(panel, text="외곽/크기 적용", command=self.border_step).pack(fill=tk.X, pady=(6, 0))

    def _panel(self, parent, title: str, col: int) -> ttk.LabelFrame:
        panel = ttk.LabelFrame(parent, text=title, padding=6)
        panel.grid(row=0, column=col, sticky="nsew", padx=4)
        return panel

    def _slider(self, parent, label: str, key: str, lo: int, hi: int, default: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text=label, width=12).pack(side=tk.LEFT)
        var = tk.IntVar(value=default)
        self.slider_vars[key] = var
        self.slider_ranges[key] = (lo, hi)
        value_label = ttk.Label(row, text=str(default), width=4)
        self.value_labels[key] = value_label

        def update(value, k=key):
            self.value_labels[k].config(text=str(int(float(value))))

        ttk.Scale(row, from_=lo, to=hi, variable=var, orient=tk.HORIZONTAL, command=update).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        value_label.pack(side=tk.RIGHT)

    def v(self, key: str) -> int:
        return int(self.slider_vars[key].get())

    def set_slider_value(self, key: str, value: int) -> None:
        lo, hi = self.slider_ranges[key]
        value = max(lo, min(hi, int(value)))
        self.slider_vars[key].set(value)
        self.value_labels[key].config(text=str(value))

    def apply_size_preset(self) -> None:
        sizes = {
            "80x25": (80, 25),
            "80x80": (80, 80),
            "240x240": (240, 240),
        }
        width, height = sizes.get(self.var_size_preset.get(), (DEFAULT_WIDTH, DEFAULT_HEIGHT))
        self.set_slider_value("map_width", width)
        self.set_slider_value("map_height", height)
        self.status.config(text=f"크기 프리셋 적용 — {width}×{height}")

    def randomize_sliders(self, keys: list[str], label: str) -> None:
        rng = random.Random()
        for key in keys:
            lo, hi = self.slider_ranges[key]
            value = rng.randint(lo, hi)
            self.set_slider_value(key, value)
        self.status.config(text=f"{label} 슬라이더 랜덤 이동 완료")

    def apply_size(self) -> None:
        width = self.v("map_width")
        height = self.v("map_height")
        self.model.resize(width, height)

    def generate_cave_step(self) -> None:
        self.disable_edit_modes()
        self.apply_size()
        generate_cave(
            self.model,
            scale=self.v("cave_scale"),
            threshold=self.v("cave_threshold"),
            octaves=self.v("cave_octaves"),
            smooth=self.v("cave_smooth"),
        )
        self.model.remember_cave_base()
        self.status.config(text=f"자연동굴 생성 완료 — seed={self.model.seed}")
        self.render_preview()

    def generate_rooms_step(self) -> None:
        self.disable_edit_modes()
        self.apply_size()
        self.model.restore_cave_base()
        generate_rooms(
            self.model,
            room_count=self.v("room_count"),
            min_size=min(self.v("room_min"), self.v("room_max")),
            max_size=max(self.v("room_min"), self.v("room_max")),
            overlay=self.var_room_overlay.get(),
        )
        self.model.remember_room_base()
        self.status.config(text=f"방 생성 완료 — 방 {len(self.model.rooms)}개")
        self.render_preview()

    def connect_step(self) -> None:
        self.disable_edit_modes()
        self.model.restore_room_base()
        links = connect_regions(
            self.model,
            min_region_size=self.v("connect_min_region"),
            corridor_width=self.v("connect_width"),
            max_links=self.v("connect_max_links"),
        )
        self.model.remember_connect_base()
        self.status.config(text=f"고립 방 연결 완료 — 연결 {links}개")
        self.render_preview()

    def connect_all_step(self) -> None:
        self.disable_edit_modes()
        self.model.restore_room_base()
        links = connect_regions(
            self.model,
            min_region_size=self.v("connect_min_region"),
            corridor_width=self.v("connect_width"),
            max_links=self.model.width * self.model.height,
        )
        self.model.remember_connect_base()
        self.status.config(text=f"모든 방 연결 완료 — 연결 {links}개")
        self.render_preview()

    def toggle_manual_draw(self) -> None:
        if self.manual_draw:
            self.disable_manual_draw()
            self.status.config(text="수동 그리기 OFF")
            return

        self.disable_delete_corridor()
        self.model.restore_connect_base()
        self.model.remember_connect_base()
        self.manual_draw = True
        self.last_draw_tile = None
        self.btn_manual_draw.config(text="수동 그리기 ON")
        self.status.config(text="수동 그리기 ON — 시작점 클릭 후 이동해 대각선 미리보기, 끝점 클릭으로 고정")
        self.render_preview()

    def disable_manual_draw(self) -> None:
        self.manual_draw = False
        self.last_draw_tile = None
        self.manual_preview_tiles.clear()
        if hasattr(self, "btn_manual_draw"):
            self.btn_manual_draw.config(text="수동 그리기 OFF")
        self.render_preview()

    def toggle_delete_corridor(self) -> None:
        if self.delete_corridor:
            self.disable_delete_corridor()
            self.status.config(text="복도 지우기 OFF")
            return

        self.disable_manual_draw()
        self.model.restore_connect_base()
        self.delete_corridor = True
        self.btn_delete_corridor.config(text="복도 1개씩 지우기 ON")
        self.status.config(text="복도 지우기 ON — 지울 노란 복도를 클릭하세요")
        self.render_preview()

    def disable_delete_corridor(self) -> None:
        self.delete_corridor = False
        if hasattr(self, "btn_delete_corridor"):
            self.btn_delete_corridor.config(text="복도 1개씩 지우기 OFF")

    def disable_edit_modes(self) -> None:
        self.disable_manual_draw()
        self.disable_delete_corridor()
        self.disable_room_move()

    def toggle_room_move(self) -> None:
        if self.room_move:
            self.disable_room_move()
            self.status.config(text="방 이동 OFF")
            return

        self.disable_manual_draw()
        self.disable_delete_corridor()
        self.model.restore_room_base()
        self.room_move = True
        self.moving_room_index = None
        self.moving_room_preview = None
        self.btn_room_move.config(text="방 이동 ON")
        self.status.config(text="방 이동 ON — 이동할 방을 클릭하세요")
        self.render_preview()

    def disable_room_move(self) -> None:
        if self.room_move and self.moving_room_index is not None:
            self.model.restore_room_base()
        self.room_move = False
        self.moving_room_index = None
        self.moving_room_preview = None
        if hasattr(self, "btn_room_move"):
            self.btn_room_move.config(text="방 이동 OFF")
        self.render_preview()

    def event_to_tile(self, event) -> tuple[int, int] | None:
        x = int(self.canvas.canvasx(event.x) // CELL_PIXELS)
        y = int(self.canvas.canvasy(event.y) // CELL_PIXELS)
        if 0 <= x < self.model.width and 0 <= y < self.model.height:
            return x, y
        return None

    def draw_manual_line(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.manual_preview_tiles.clear()
        carve_line(
            self.model.grid,
            start,
            end,
            self.v("connect_width"),
            tile="y",
            corner_mode=self.corridor_corner_mode,
        )
        self.model.remember_connect_base()
        self.render_preview()

    def on_canvas_press(self, event) -> None:
        if self.room_move:
            tile = self.event_to_tile(event)
            if tile is not None:
                self.handle_room_move_click(tile)
            return

        if self.delete_corridor:
            tile = self.event_to_tile(event)
            if tile is not None:
                removed = self.delete_corridor_at(tile)
                if removed:
                    self.status.config(text=f"복도 삭제 완료 — {removed}타일")
                else:
                    self.status.config(text="노란 복도를 클릭하세요")
            return

        if not self.manual_draw:
            return
        tile = self.event_to_tile(event)
        if tile is None:
            return
        if self.last_draw_tile is None:
            self.last_draw_tile = tile
            self.status.config(
                text=f"수동 그리기 — 시작점 {tile} 선택됨, 끝점을 클릭하세요"
            )
            self.update_manual_preview(tile)
            return
        start = self.last_draw_tile
        self.draw_manual_line(start, tile)
        self.last_draw_tile = None
        self.status.config(text=f"수동 그리기 — {start} → {tile} 직선 연결 완료")

    def on_canvas_drag(self, _event) -> None:
        return

    def on_canvas_motion(self, event) -> None:
        if self.room_move and self.moving_room_index is not None:
            tile = self.event_to_tile(event)
            if tile is not None:
                self.update_room_move_preview(tile)
            return

        if not self.manual_draw or self.last_draw_tile is None:
            return
        tile = self.event_to_tile(event)
        if tile is None:
            return
        self.update_manual_preview(tile)

    def update_manual_preview(self, end: tuple[int, int]) -> None:
        if self.last_draw_tile is None:
            return
        self.manual_preview_tiles = corridor_tiles(
            self.model.grid,
            self.last_draw_tile,
            end,
            self.v("connect_width"),
            preserve={"r"},
            corner_mode=self.corridor_corner_mode,
        )
        self.render_preview()

    def on_canvas_right_click(self, event) -> None:
        if not self.manual_draw:
            return
        tile = self.event_to_tile(event)
        if self.last_draw_tile is not None and tile is not None:
            self.update_manual_preview(tile)
        self.status.config(text="수동 그리기 — 대각선 복도는 픽셀이 끊기지 않게 자동 보정됩니다")

    def on_canvas_release(self, _event) -> None:
        return

    def cancel_manual_preview(self, _event=None) -> None:
        if self.room_move and self.moving_room_index is not None:
            self.model.restore_room_base()
            self.moving_room_index = None
            self.moving_room_preview = None
            self.status.config(text="방 이동 — 이동 취소됨")
            self.render_preview()
            return

        if not self.manual_draw or self.last_draw_tile is None:
            return
        self.last_draw_tile = None
        self.manual_preview_tiles.clear()
        self.status.config(text="수동 그리기 — 진행 중인 선 취소됨")
        self.render_preview()

    def delete_corridor_at(self, tile: tuple[int, int]) -> int:
        x, y = tile
        if self.model.grid[y][x] != "y":
            return 0
        queue = deque([(x, y)])
        seen = {(x, y)}
        removed = 0

        while queue:
            cx, cy = queue.popleft()
            self.model.grid[cy][cx] = self.base_tile_under_corridor(cx, cy)
            removed += 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = cx + dx
                ny = cy + dy
                if not (0 <= nx < self.model.width and 0 <= ny < self.model.height):
                    continue
                if (nx, ny) in seen or self.model.grid[ny][nx] != "y":
                    continue
                seen.add((nx, ny))
                queue.append((nx, ny))

        self.model.remember_connect_base()
        self.render_preview()
        return removed

    def base_tile_under_corridor(self, x: int, y: int) -> str:
        """Restore the layer below corridors instead of erasing terrain."""
        if (
            len(self.model.room_base) == self.model.height
            and self.model.room_base
            and len(self.model.room_base[0]) == self.model.width
        ):
            return self.model.room_base[y][x]
        return "#"

    def room_index_at(self, tile: tuple[int, int]) -> int | None:
        x, y = tile
        for index, room in enumerate(self.model.rooms):
            if room.x1 <= x <= room.x2 and room.y1 <= y <= room.y2:
                return index
        return None

    def clamp_room_position(self, room: Room, x1: int, y1: int) -> tuple[int, int]:
        max_x = max(0, self.model.width - room.width)
        max_y = max(0, self.model.height - room.height)
        return max(0, min(max_x, x1)), max(0, min(max_y, y1))

    def handle_room_move_click(self, tile: tuple[int, int]) -> None:
        if self.moving_room_index is None:
            index = self.room_index_at(tile)
            if index is None:
                self.status.config(text="방 이동 — 초록 방을 클릭하세요")
                return
            room = self.model.rooms[index]
            self.moving_room_index = index
            self.moving_room_offset = (tile[0] - room.x1, tile[1] - room.y1)
            self.model.rebuild_rooms_from_cave(skip_index=index)
            self.update_room_move_preview(tile)
            self.status.config(text="방 이동 — 마우스를 움직여 위치 확인, 다시 클릭해 고정")
            return

        self.commit_room_move()

    def update_room_move_preview(self, tile: tuple[int, int]) -> None:
        if self.moving_room_index is None:
            return
        room = self.model.rooms[self.moving_room_index]
        ox, oy = self.moving_room_offset
        x1, y1 = self.clamp_room_position(room, tile[0] - ox, tile[1] - oy)
        self.moving_room_preview = room.moved_to(x1, y1)
        self.render_preview()

    def commit_room_move(self) -> None:
        if self.moving_room_index is None or self.moving_room_preview is None:
            return
        self.model.rooms[self.moving_room_index] = self.moving_room_preview
        self.model.rebuild_rooms_from_cave()
        self.model.remember_room_base()
        self.moving_room_index = None
        self.moving_room_preview = None
        self.status.config(text="방 이동 완료 — 연결/비네팅은 다시 적용하세요")
        self.render_preview()

    def border_step(self) -> None:
        self.disable_edit_modes()
        self.apply_size()
        self.model.restore_connect_base()
        apply_vignette(
            self.model,
            margin=self.v("vignette_margin"),
            strength=self.v("vignette_strength"),
            border=self.v("solid_border"),
            shape=self.var_vignette_shape.get(),
            angle=self.v("vignette_angle"),
        )
        self.status.config(
            text=f"외곽 비네팅 적용 완료 — {self.var_vignette_shape.get()}, {self.v('vignette_angle')}도"
        )
        self.render_preview()

    def generate_all(self) -> None:
        self.generate_cave_step()
        self.generate_rooms_step()
        self.connect_step()
        self.border_step()
        self.status.config(text=f"전체 절차 완료 — {self.model.width}×{self.model.height}, 2×2 픽셀 프리뷰")

    def clear_all(self) -> None:
        self.apply_size()
        self.model.reset()
        self.status.config(text="전체 클리어 완료 — 빈 맵")
        self.render_preview()

    def export_map(self) -> None:
        path = filedialog.asksaveasfilename(
            title="맵 익스포트",
            defaultextension=".txt",
            filetypes=[("Text map", "*.txt"), ("All files", "*.*")],
            initialfile=f"pydungen5_{self.model.seed or 'map'}.txt",
        )
        if not path:
            return
        output = Path(path)
        output.write_text(
            "\n".join("".join(row) for row in self.model.grid) + "\n",
            encoding="utf-8",
        )
        self.status.config(text=f"익스포트 완료 — {output.name}")

    def render_preview(self) -> None:
        width = self.model.width
        height = self.model.height
        img_w = width * CELL_PIXELS
        img_h = height * CELL_PIXELS

        if HAS_PIL:
            image = Image.new("RGB", (width, height))
            pixels = image.load()
            for y, row in enumerate(self.model.grid):
                for x, ch in enumerate(row):
                    ch = self.preview_tile_at(x, y, ch)
                    pixels[x, y] = TILE_RGB.get(ch, TILE_RGB["#"])
            image = image.resize((img_w, img_h), Image.NEAREST)
            self.preview_image = ImageTk.PhotoImage(image)
        else:
            self.preview_image = self.render_preview_tk(img_w, img_h)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.preview_image, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, img_w, img_h))

    def render_preview_tk(self, img_w: int, img_h: int) -> tk.PhotoImage:
        image = tk.PhotoImage(width=img_w, height=img_h)
        for y, row in enumerate(self.model.grid):
            line = []
            for x, ch in enumerate(row):
                ch = self.preview_tile_at(x, y, ch)
                r, g, b = TILE_RGB.get(ch, TILE_RGB["#"])
                color = f"#{r:02x}{g:02x}{b:02x}"
                line.extend([color] * CELL_PIXELS)
            row_data = " ".join(line)
            for dy in range(CELL_PIXELS):
                image.put("{" + row_data + "}", to=(0, y * CELL_PIXELS + dy))
        return image

    def preview_tile_at(self, x: int, y: int, base: str) -> str:
        if (x, y) in self.manual_preview_tiles:
            return "y"
        room = self.moving_room_preview
        if room is not None and room.x1 <= x <= room.x2 and room.y1 <= y <= room.y2:
            return "r"
        return base


def main() -> None:
    app = PyDunGen5GUI()
    app.mainloop()


if __name__ == "__main__":
    main()
