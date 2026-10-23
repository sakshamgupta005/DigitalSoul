from PIL import Image, ImageFilter
import numpy as np
from collections import deque

# Load original image
img = Image.open('contracts/assets/landingpage.png').convert('RGBA')
arr = np.array(img, dtype=np.float32)
h, w, _ = arr.shape

# Distance from pure white
rgb = arr[:, :, :3]
diff_from_white = np.sqrt(np.sum((255.0 - rgb) ** 2, axis=2))

# Background seed: very close to white (< 20 distance)
is_seed = diff_from_white < 25.0

visited = np.zeros((h, w), dtype=bool)
q = deque()

# Seed from borders
for x in range(w):
    if is_seed[0, x]:
        q.append((0, x))
        visited[0, x] = True
for y in range(h):
    if is_seed[y, 0] and not visited[y, 0]:
        q.append((y, 0))
        visited[y, 0] = True
    if is_seed[y, w-1] and not visited[y, w-1]:
        q.append((y, w-1))
        visited[y, w-1] = True

# Also find enclosed white areas like between monitor stand or arms if diff_from_white < 12
for y in range(int(h * 0.8)):
    for x in range(w):
        if not visited[y, x] and diff_from_white[y, x] < 12.0:
            q.append((y, x))
            visited[y, x] = True

while q:
    y, x = q.popleft()
    for dy, dx in [(-1,0), (1,0), (0,-1), (0,1)]:
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
            # Expand to pixels that are clearly background
            if diff_from_white[ny, nx] < 45.0:
                visited[ny, nx] = True
                q.append((ny, nx))

# Alpha mask: 0 where background, 255 where foreground
alpha_mask = np.ones((h, w), dtype=np.uint8) * 255
alpha_mask[visited] = 0

# Feather edges to remove white halo
mask_img = Image.fromarray(alpha_mask, mode='L')
mask_blur = mask_img.filter(ImageFilter.GaussianBlur(radius=0.7))

# Defringe / decontaminate edges: multiply near-white edges to remove white fringe against dark background
clean_img = Image.open('contracts/assets/landingpage.png').convert('RGB')
final_img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
final_img.paste(clean_img, (0, 0), mask=mask_blur)

final_img.save('contracts/assets/characters_transparent.png')
print('Enhanced transparent image saved successfully.')
