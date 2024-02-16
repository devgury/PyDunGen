import random

class Room:
    def __init__(self, x, y, w, h):
        self.x1 = x
        self.y1 = y
        self.x2 = x + w
        self.y2 = y + h
    
    def center(self):
        centerX = (self.x1 + self.x2) // 2
        centerY = (self.y1 + self.y2) // 2
        return (centerX, centerY)
    
    def intersects(self, other):
        # Returns True if this room intersects with another one
        return (self.x1 <= other.x2 and self.x2 >= other.x1 and
                self.y1 <= other.y2 and self.y2 >= other.y1)
    
def create_h_tunnel(x1, x2, y, dungeon):
    """Create a horizontal tunnel in the dungeon."""
    for x in range(min(x1, x2), max(x1, x2) + 1):
        dungeon[y][x] = "."

def create_v_tunnel(y1, y2, x, dungeon):
    """Create a vertical tunnel in the dungeon."""
    for y in range(min(y1, y2), max(y1, y2) + 1):
        dungeon[y][x] = "."

def create_dungeon_with_corridors(width, height, max_rooms, room_min_size, room_max_size):
    dungeon = [["#" for _ in range(width)] for _ in range(height)]
    rooms = []

    for _ in range(max_rooms):
        w = random.randint(room_min_size, room_max_size)
        h = random.randint(room_min_size, room_max_size)
        x = random.randint(0, width - w - 1)
        y = random.randint(0, height - h - 1)

        new_room = Room(x, y, w, h)

        # Check if the new room intersects with any existing room
        if any(new_room.intersects(other_room) for other_room in rooms):
            continue

        # Create horizontal and vertical tunnels between this room and the previous one
        if rooms:
            prev_x, prev_y = rooms[-1].center()
            new_x, new_y = new_room.center()

            if random.randint(0, 1) == 1:
                # First horizontal, then vertical
                create_h_tunnel(prev_x, new_x, prev_y, dungeon)
                create_v_tunnel(prev_y, new_y, new_x, dungeon)
            else:
                # First vertical, then horizontal
                create_v_tunnel(prev_y, new_y, prev_x, dungeon)
                create_h_tunnel(prev_x, new_x, new_y, dungeon)

        rooms.append(new_room)

        # Mark the room on the dungeon map
        for x in range(new_room.x1, new_room.x2):
            for y in range(new_room.y1, new_room.y2):
                dungeon[y][x] = "."

    return dungeon

def print_dungeon(dungeon):
    for row in dungeon:
        print("".join(row))

# Create and print the dungeon with corridors
dungeon_with_corridors = create_dungeon_with_corridors(80, 50, 18, 4, 8)
print_dungeon(dungeon_with_corridors)