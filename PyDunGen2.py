import random
import noise  # Perlin noise library
import numpy as np

def generate_perlin_noise(width, height, scale=10, octaves=6, persistence=0.5, lacunarity=2.0):
    """Generate a 2D numpy array of perlin noise."""
    world = np.zeros((height, width))
    seed = random.randint(0, 100)
    for i in range(height):
        for j in range(width):
            world[i][j] = noise.pnoise2(i / scale, 
                                        j / scale, 
                                        octaves=octaves, 
                                        persistence=persistence, 
                                        lacunarity=lacunarity, 
                                        repeatx=width, 
                                        repeaty=height, 
                                        base=seed)
    return world

def create_cave_dungeon(width, height, threshold=-0.1):
    """Create a dungeon map using perlin noise."""
    dungeon = generate_perlin_noise(width, height)
    cave_map = [[" " for _ in range(width)] for _ in range(height)]
    
    for i in range(height):
        for j in range(width):
            if dungeon[i][j] < threshold:
                cave_map[i][j] = "."  # Floor
            else:
                cave_map[i][j] = "#"  # Wall
    
    return cave_map

def print_dungeon(dungeon):
    for row in dungeon:
        print("".join(row))

# Dungeon settings
width = 80
height = 50
threshold = -0.1  # Adjust this to control the density of the cave walls

# Generate and print the cave dungeon
cave_dungeon = create_cave_dungeon(width, height, threshold)
print_dungeon(cave_dungeon)
