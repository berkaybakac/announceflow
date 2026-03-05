from PIL import Image, ImageDraw
import os

# Define size and high-quality drawing constants
SIZE = 120
CENTER = SIZE // 2

def create_icon(name, draw_func, bg_color=None):
    # Use 4x supersampling for high quality anti-aliasing
    scale = 4
    img_size = SIZE * scale
    
    img = Image.new('RGBA', (img_size, img_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    
    if bg_color:
        # Draw rounded rectangle background
        r = 20 * scale
        d.rounded_rectangle([0, 0, img_size, img_size], radius=r, fill=bg_color)
    
    # Draw icon specific shapes
    draw_func(d, scale)
    
    # Downsample to get smooth edges
    final_img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    
    os.makedirs('assets/icons', exist_ok=True)
    final_img.save(f'assets/icons/{name}.png')

def draw_play(d, scale):
    # Modern play triangle (slightly rounded corners natively are hard in PIL, so we use polygon)
    cx, cy = CENTER * scale, CENTER * scale
    size = 25 * scale
    offset_x = 5 * scale
    points = [
        (cx - size + offset_x, cy - size * 1.2),
        (cx - size + offset_x, cy + size * 1.2),
        (cx + size + offset_x, cy)
    ]
    d.polygon(points, fill="white")

def draw_stop(d, scale):
    cx, cy = CENTER * scale, CENTER * scale
    size = 22 * scale
    r = 6 * scale
    d.rounded_rectangle([cx - size, cy - size, cx + size, cy + size], radius=r, fill="white")

def draw_stream(d, scale):
    cx, cy = CENTER * scale, CENTER * scale
    # Signal waves
    for r in [18*scale, 32*scale, 46*scale]:
        d.arc([cx - r, cy - r, cx + r, cy + r], start=225, end=315, fill="white", width=4*scale)
    # Center dot
    dot_r = 6*scale
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill="white")
    # Base antenna
    d.line([cx, cy + dot_r, cx, cy + 30*scale], fill="white", width=4*scale)

def draw_upload(d, scale):
    cx, cy = CENTER * scale, CENTER * scale
    # Arrow up
    w = 6 * scale
    d.line([cx, cy + 20*scale, cx, cy - 15*scale], fill="white", width=w)
    d.line([cx - 15*scale, cy, cx, cy - 15*scale], fill="white", width=w)
    d.line([cx + 15*scale, cy, cx, cy - 15*scale], fill="white", width=w)
    # Base line
    d.line([cx - 20*scale, cy + 28*scale, cx + 20*scale, cy + 28*scale], fill="white", width=w)

def draw_web(d, scale):
    cx, cy = CENTER * scale, CENTER * scale
    r = 30 * scale
    w = 4 * scale
    # Outer circle
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="white", width=w)
    # Horizontal/Vertical lines
    d.line([cx - r, cy, cx + r, cy], fill="white", width=w)
    d.line([cx, cy - r, cx, cy + r], fill="white", width=w)
    # Arcs for globe effect
    d.ellipse([cx - r//2, cy - r, cx + r//2, cy + r], outline="white", width=w)

def draw_vol_icon(d, scale, is_high=False):
    cx, cy = CENTER * scale, CENTER * scale
    w = 4 * scale
    # Speaker body
    bx, by = cx - 15*scale, cy
    d.rectangle([bx - 10*scale, by - 12*scale, bx + 2*scale, by + 12*scale], fill="white")
    d.polygon([
        (bx + 2*scale, by - 12*scale),
        (bx + 18*scale, by - 24*scale),
        (bx + 18*scale, by + 24*scale),
        (bx + 2*scale, by + 12*scale)
    ], fill="white")
    
    if is_high:
        # Sound waves
        d.arc([cx + 10*scale, cy - 15*scale, cx + 30*scale, cy + 15*scale], start=315, end=45, fill="white", width=w)
        d.arc([cx + 15*scale, cy - 25*scale, cx + 45*scale, cy + 25*scale], start=315, end=45, fill="white", width=w)
    else:
        # Cross (mute) or single wave
        d.line([cx + 25*scale, cy - 10*scale, cx + 45*scale, cy + 10*scale], fill="white", width=w)
        d.line([cx + 25*scale, cy + 10*scale, cx + 45*scale, cy - 10*scale], fill="white", width=w)

create_icon('play', draw_play)
create_icon('stop', draw_stop)
create_icon('stream', draw_stream)
create_icon('upload', draw_upload)
create_icon('web', draw_web)
create_icon('vol_mute', lambda d, s: draw_vol_icon(d, s, False))
create_icon('vol_high', lambda d, s: draw_vol_icon(d, s, True))

print("Icons generated successfully!")
