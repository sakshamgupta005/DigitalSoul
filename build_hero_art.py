import math
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
import numpy as np

def create_enhanced_hero():
    # 1. Load original 1024x432 image
    im = Image.open('assets/hero_desk_extended.png').convert('RGBA')
    w_orig, h_orig = im.size # 1024, 432

    # Scale 2x for crisp Retina/4K sharpness
    scale = 2
    w_up = w_orig * scale # 2048
    h_up = h_orig * scale # 864

    # High quality upscale of characters
    im_up = im.resize((w_up, h_up), Image.Resampling.LANCZOS)

    # Enhance sharpness of lineart: slight unsharp mask to make strokes punchy and vector-crisp
    alpha = im_up.split()[3]
    rgb = im_up.convert('RGB')
    enhancer = ImageEnhance.Sharpness(rgb)
    rgb_sharp = enhancer.enhance(1.4)
    # Also crisp the alpha edges
    im_up = Image.merge('RGBA', (*rgb_sharp.split(), alpha))

    # 2. Canvas dimensions: 4800 x 864
    canvas_w = 4800
    canvas_h = h_up
    canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))

    # Offset to center characters
    offset_x = (canvas_w - w_up) // 2 # 1376
    canvas.paste(im_up, (offset_x, 0), im_up)

    # 3. Desk surface coordinates in 2x:
    desk_y_top = 736
    desk_y_edge = 770
    desk_y_bot = 804

    # Sample clean desk slice from x = offset_x + 50
    arr = np.array(canvas)
    slice_desk = arr[desk_y_top:desk_y_bot+1, offset_x + 50, :].copy()

    # Fill continuous desk from x=0 to x=canvas_w
    for x in range(0, offset_x):
        arr[desk_y_top:desk_y_bot+1, x, :] = slice_desk
    for x in range(offset_x + w_up, canvas_w):
        arr[desk_y_top:desk_y_bot+1, x, :] = slice_desk

    # Legs at balanced intervals
    leg_slice = arr[desk_y_bot:canvas_h, offset_x + 88:offset_x + 148, :].copy()
    leg_w = leg_slice.shape[1]
    for lx in [380, 920, canvas_w - 920 - leg_w, canvas_w - 380 - leg_w]:
        arr[desk_y_bot:canvas_h, lx:lx+leg_w, :] = leg_slice

    canvas = Image.fromarray(arr)

    # 4. Now draw rich desk props in the exact black lineart + clean minimal style
    # We draw on a transparent overlay at 4x resolution for super-smooth anti-aliased curves
    draw_canvas = canvas.copy()
    overlay = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    INK = (16, 18, 23, 255)
    CREAM = (253, 240, 224, 255)
    WHITE = (255, 255, 255, 255)
    DARK_FILL = (24, 26, 32, 255)
    MUTED_GRAY = (220, 215, 204, 255)
    STEAM_GRAY = (140, 134, 122, 180)

    # --- LEFT SIDE PROPS ---

    # PROP A: Stack of Hardcover Books (around x=860..1020, on desk at y=desk_y_top)
    # Book 1 (bottom):
    b1_x, b1_y = 880, desk_y_top
    b1_w, b1_h = 130, 28
    d.rounded_rectangle([b1_x, b1_y - b1_h, b1_x + b1_w, b1_y], radius=3, fill=DARK_FILL, outline=INK, width=3)
    # Pages line
    d.rectangle([b1_x + 10, b1_y - b1_h + 5, b1_x + b1_w - 8, b1_y - 5], fill=WHITE, outline=INK, width=2)
    # Spine band
    d.line([b1_x + 24, b1_y - b1_h, b1_x + 24, b1_y], fill=INK, width=3)

    # Book 2 (middle):
    b2_x, b2_y = 895, b1_y - b1_h
    b2_w, b2_h = 115, 24
    d.rounded_rectangle([b2_x, b2_y - b2_h, b2_x + b2_w, b2_y], radius=3, fill=WHITE, outline=INK, width=3)
    d.line([b2_x + 20, b2_y - b2_h, b2_x + 20, b2_y], fill=INK, width=2)
    d.line([b2_x + 35, b2_y - b2_h + 7, b2_x + 75, b2_y - b2_h + 7], fill=INK, width=2)

    # Book 3 (top, slightly tilted):
    b3_x, b3_y = 905, b2_y - b2_h
    b3_w, b3_h = 95, 20
    d.rounded_rectangle([b3_x, b3_y - b3_h, b3_x + b3_w, b3_y], radius=2, fill=DARK_FILL, outline=INK, width=3)
    d.line([b3_x + 16, b3_y - b3_h, b3_x + 16, b3_y], fill=WHITE, width=2)

    # Trailing bookmark ribbon onto desk:
    ribbon = [(b2_x + b2_w - 15, b2_y - 10), (b2_x + b2_w + 10, b1_y - 8), (b2_x + b2_w + 22, desk_y_top + 4)]
    d.line(ribbon, fill=INK, width=3)

    # PROP B: Architect / Anglepoise Task Lamp (around x=680..820)
    # Lamp base sitting on desk:
    lx, ly = 720, desk_y_top
    d.ellipse([lx - 32, ly - 10, lx + 32, ly + 4], fill=DARK_FILL, outline=INK, width=3)
    d.rounded_rectangle([lx - 8, ly - 22, lx + 8, ly - 8], radius=2, fill=DARK_FILL, outline=INK, width=2)

    # Armature segment 1 (angled up and right):
    joint1 = (lx, ly - 16)
    joint2 = (lx + 35, ly - 130)
    d.line([joint1, joint2], fill=INK, width=5)
    d.circle(joint2, radius=6, fill=WHITE, outline=INK, width=3)

    # Armature segment 2 (angled up and forward to center):
    joint3 = (lx + 95, ly - 210)
    d.line([joint2, joint3], fill=INK, width=4)
    d.circle(joint3, radius=6, fill=WHITE, outline=INK, width=3)

    # Lamp head / bell shade (pointing down-right toward the desk):
    shade_poly = [
        (joint3[0] + 5, joint3[1] - 12),
        (joint3[0] + 55, joint3[1] + 18),
        (joint3[0] + 35, joint3[1] + 50),
        (joint3[0] - 10, joint3[1] + 16)
    ]
    d.polygon(shade_poly, fill=DARK_FILL, outline=INK)
    d.line(shade_poly + [shade_poly[0]], fill=INK, width=3)
    # Light rim ellipse
    d.ellipse([joint3[0] + 20, joint3[1] + 18, joint3[0] + 62, joint3[1] + 54], fill=CREAM, outline=INK, width=3)

    # PROP C: Steaming Ceramic Coffee Mug & Saucer (around x=1180)
    mx, my = 1190, desk_y_top
    # Saucer
    d.ellipse([mx - 28, my - 6, mx + 28, my + 4], fill=WHITE, outline=INK, width=3)
    # Mug body
    d.rounded_rectangle([mx - 20, my - 38, mx + 20, my - 4], radius=4, fill=WHITE, outline=INK, width=3)
    # Coffee rim
    d.ellipse([mx - 18, my - 42, mx + 18, my - 34], fill=DARK_FILL, outline=INK, width=3)
    # Mug handle
    d.arc([mx + 12, my - 32, mx + 32, my - 12], start=-90, end=90, fill=INK, width=3)
    # Minimal steam lines
    d.arc([mx - 8, my - 65, mx, my - 48], start=220, end=340, fill=STEAM_GRAY, width=2)
    d.arc([mx + 2, my - 72, mx + 10, my - 52], start=40, end=160, fill=STEAM_GRAY, width=2)

    # PROP D: Sticky Notes & Architecture Sketches (around x=1260)
    sx, sy = 1270, desk_y_top
    # Note 1 (square, slightly rotated):
    d.polygon([(sx, sy - 28), (sx + 34, sy - 34), (sx + 40, sy - 4), (sx + 6, sy + 2)], fill=CREAM, outline=INK)
    d.line([(sx, sy - 28), (sx + 34, sy - 34), (sx + 40, sy - 4), (sx + 6, sy + 2), (sx, sy - 28)], fill=INK, width=2)
    d.line([(sx + 8, sy - 22), (sx + 28, sy - 25)], fill=INK, width=2)
    d.line([(sx + 10, sy - 15), (sx + 26, sy - 18)], fill=INK, width=2)

    # --- RIGHT SIDE PROPS ---

    # PROP E: Potted Monstera / Succulent Desk Plant (around x=3480..3580)
    # Ceramic pot
    px, py = 3520, desk_y_top
    pot_poly = [(px - 26, py - 46), (px + 26, py - 46), (px + 20, py), (px - 20, py)]
    d.polygon(pot_poly, fill=WHITE, outline=INK)
    d.line(pot_poly + [pot_poly[0]], fill=INK, width=3)
    d.ellipse([px - 26, py - 52, px + 26, py - 40], fill=DARK_FILL, outline=INK, width=3)

    # Leaf 1 (curving left):
    leaf1 = [(px - 5, py - 46), (px - 25, py - 70), (px - 55, py - 85), (px - 60, py - 110), (px - 40, py - 112), (px - 20, py - 85), (px - 5, py - 46)]
    d.polygon(leaf1, fill=DARK_FILL, outline=INK)
    d.line(leaf1, fill=INK, width=3)

    # Leaf 2 (upright center):
    leaf2 = [(px, py - 46), (px - 15, py - 95), (px, py - 135), (px + 18, py - 115), (px + 8, py - 80), (px, py - 46)]
    d.polygon(leaf2, fill=DARK_FILL, outline=INK)
    d.line(leaf2, fill=INK, width=3)

    # Leaf 3 (curving right):
    leaf3 = [(px + 6, py - 46), (px + 30, py - 75), (px + 65, py - 90), (px + 70, py - 112), (px + 50, py - 114), (px + 25, py - 80), (px + 6, py - 46)]
    d.polygon(leaf3, fill=DARK_FILL, outline=INK)
    d.line(leaf3, fill=INK, width=3)

    # Small baby leaf:
    leaf4 = [(px + 4, py - 46), (px + 22, py - 60), (px + 35, py - 65), (px + 20, py - 52), (px + 4, py - 46)]
    d.polygon(leaf4, fill=WHITE, outline=INK)
    d.line(leaf4, fill=INK, width=2)

    # PROP F: Studio Over-Ear Headphones (around x=3660..3780)
    hx, hy = 3710, desk_y_top
    # Left earcup (angled on desk):
    d.rounded_rectangle([hx - 48, hy - 40, hx - 22, hy], radius=10, fill=DARK_FILL, outline=INK, width=3)
    d.rounded_rectangle([hx - 42, hy - 34, hx - 28, hy - 6], radius=5, fill=WHITE, outline=INK, width=2)
    # Right earcup:
    d.rounded_rectangle([hx + 22, hy - 40, hx + 48, hy], radius=10, fill=DARK_FILL, outline=INK, width=3)
    d.rounded_rectangle([hx + 28, hy - 34, hx + 42, hy - 6], radius=5, fill=WHITE, outline=INK, width=2)
    # Padded headband arch connecting both earcups:
    d.arc([hx - 40, hy - 85, hx + 40, hy - 15], start=185, end=355, fill=INK, width=6)
    # Subtle coiled audio cable on desk:
    d.arc([hx + 42, hy - 12, hx + 72, hy + 2], start=0, end=270, fill=INK, width=3)

    # PROP G: Open Wireframe Sketchbook / Pad with Stylus (around x=3860..4000)
    kx, ky = 3910, desk_y_top
    # Pad lying in perspective:
    pad_poly = [(kx - 55, ky - 32), (kx + 55, ky - 32), (kx + 65, ky), (kx - 65, ky)]
    d.polygon(pad_poly, fill=WHITE, outline=INK)
    d.line(pad_poly + [pad_poly[0]], fill=INK, width=3)
    # Center fold
    d.line([(kx, ky - 32), (kx, ky)], fill=INK, width=2)
    # Wireframe wire lines on page
    d.line([(kx - 42, ky - 24), (kx - 10, ky - 24)], fill=INK, width=2)
    d.line([(kx - 42, ky - 16), (kx - 10, ky - 16)], fill=INK, width=2)
    d.line([(kx - 42, ky - 8), (kx - 22, ky - 8)], fill=INK, width=2)
    # Stylus beside pad:
    d.line([(kx + 72, ky - 28), (kx + 88, ky - 2)], fill=DARK_FILL, width=4)
    d.line([(kx + 72, ky - 28), (kx + 88, ky - 2)], fill=INK, width=2)

    # PROP H: Smartphone / Tech Gadget on Angled Desktop Dock (around x=4070)
    dx, dy = 4080, desk_y_top
    # Small dock easel base:
    d.polygon([(dx - 16, dy), (dx + 16, dy), (dx + 12, dy - 18), (dx - 12, dy - 18)], fill=DARK_FILL, outline=INK)
    # Phone body (leaning slightly backward):
    phone = [(dx - 18, dy - 65), (dx + 18, dy - 65), (dx + 15, dy - 8), (dx - 15, dy - 8)]
    d.polygon(phone, fill=WHITE, outline=INK)
    d.line(phone + [phone[0]], fill=INK, width=3)
    # Screen bezel
    d.polygon([(dx - 14, dy - 60), (dx + 14, dy - 60), (dx + 12, dy - 14), (dx - 12, dy - 14)], fill=DARK_FILL, outline=INK)
    # Speaker notch
    d.line([(dx - 4, dy - 62), (dx + 4, dy - 62)], fill=INK, width=2)

    # 5. Composite overlay onto canvas
    final_canvas = Image.alpha_composite(canvas, overlay)

    # Save enhanced wide hero image
    final_canvas.save('assets/hero_desk_wide.png')
    final_canvas.save('contracts/assets/hero_desk_wide.png')
    print('Enhanced high-resolution hero art saved successfully with rich desk props!')

if __name__ == '__main__':
    create_enhanced_hero()
