"""
wherewolf small head: a hollow low-poly wolf head (the logo extruded back), printed
face-down with no supports.

Inside: the Tapo behind the left eye (in a cup that holds it still), the laser turret
behind the right eye (its base goes down through a slot in the bottom), and the mini
breadboard with the ESP32 (on a shelf at the back). Outside: the battery. The back is open for putting things in
and running cables. A 14 mm hole on top between the ears for the ESP32 antenna.
Eye rings print separately and glue on the front (so the head prints flat, face-down).

    python head.py   -> wolf_head.stl, camera_bezel.stl, led_ring_holder.stl
Needs: pip install trimesh manifold3d shapely mapbox-earcut
"""
import math
import numpy as np
import trimesh
from shapely.geometry import Polygon, Point
from trimesh.creation import triangulate_polygon

# ------------------------------------------------------------------ settings (mm)
FACE_W = 210          # width
DEPTH = 120           # front to back
BACK_SCALE = 0.80     # the back is 80% the size of the front (faceted taper)
WALL = 1.8            # wall thickness (4 perimeters at 0.45 mm)
ANTENNA_HOLE = 14     # >= 1/2 inch, as asked

TAPO_FACE_D = 58.4    # measured 2.3 in
TAPO_LEN = 55         # Tapo body length behind its face (estimate)
LENS_HOLE = 19.5      # lens 0.6 in + room
RING_OD, RING_ID = 45.7, 31.75   # LED ring, measured
LASER_HOLE, LASER_HOLE_BACK = 30, 38
TURRET_W, TURRET_H = 36.8, 75    # base width measured; height with laser (estimate)
PIVOT_BEHIND = 22     # laser tip ~2 cm in front of the pan pivot
BREADBOARD = (83, 55, 40)        # 400-point breadboard + ESP32 + wires (L, W, height)
NOSE_DEPTH = 1.2

HEAD = [(155, 45), (283, 222), (390, 222), (517, 45), (572, 250), (624, 378), (442, 465),
        (336, 592), (230, 465), (48, 378), (100, 250)]
NOSE = [(296, 503), (376, 503), (336, 552)]
S = FACE_W / 576.0
def mm(p): return ((p[0] - 336) * S, -(p[1] - 318) * S)
CAM, LAS = mm((246, 332)), mm((426, 332))

def offset_outline(pts, d):
    """Same outline moved inward by d (keeps one vertex per corner, so it lofts)."""
    poly = Polygon(pts).buffer(-d, join_style=2, mitre_limit=20)
    ring = list(poly.exterior.coords)[:-1]
    return [min(ring, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) for p in pts]

def loft(front, back, z_front, z_back):
    n = len(front)
    v = [(x, y, z_front) for x, y in front] + [(x, y, z_back) for x, y in back]
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j), (i, n + j, n + i)]
    vf, ff = triangulate_polygon(Polygon(front))
    idx = [min(range(n), key=lambda k: (front[k][0] - p[0]) ** 2 + (front[k][1] - p[1]) ** 2) for p in vf]
    for a, b, c in ff:
        f.append((idx[a], idx[b], idx[c])); f.append((n + idx[c], n + idx[b], n + idx[a]))
    m = trimesh.Trimesh(np.array(v, float), np.array(f), process=True)
    m.fix_normals()
    return m

def box(c, size):
    b = trimesh.creation.box(size); b.apply_translation(c); return b

def cyl(cx, cy, z0, z1, r, axis="z"):
    c = trimesh.creation.cylinder(radius=r, height=abs(z1 - z0), sections=96)
    c.apply_translation((0, 0, (z0 + z1) / 2))
    if axis == "y":
        c.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)))
    c.apply_translation((cx, cy, 0) if axis == "z" else (cx, 0, cy))
    return c

def frustum(cx, cy, r0, r1, z0, z1):
    m = trimesh.creation.cylinder(radius=1, height=1, sections=96)
    v = m.vertices.copy(); t = v[:, 2] + 0.5
    r = r0 + (r1 - r0) * t
    v[:, 0] = v[:, 0] * r + cx; v[:, 1] = v[:, 1] * r + cy; v[:, 2] = z0 + (z1 - z0) * t
    m.vertices = v; return m

def ring(cx, cy, ro, ri, z0, z1):
    return trimesh.boolean.difference([cyl(cx, cy, z0, z1, ro), cyl(cx, cy, z0 - 1, z1 + 1, ri)], engine="manifold")

# Front face at z=0 (outside) .. -WALL (inside); the head goes back to z=-DEPTH.
front = [mm(p) for p in HEAD]
back = [(x * BACK_SCALE, y * BACK_SCALE) for x, y in front]
outer = loft(front, back, 0, -DEPTH)
fi, bi = offset_outline(front, WALL), offset_outline(back, WALL)
inner = loft(fi, bi, -WALL, -DEPTH - 5)          # runs out the back: open back
head = trimesh.boolean.difference([outer, inner], engine="manifold")

# Parts (for fit checks and the slot).
cx, cy = CAM; lx, ly = LAS
tapo = cyl(cx, cy, -WALL - TAPO_LEN, -WALL, TAPO_FACE_D / 2)
tz = -WALL - 2 - PIVOT_BEHIND               # pan pivot depth
turret = box((lx, ly - TURRET_H / 2 + 12, tz), (TURRET_W, TURRET_H, TURRET_W))

# Tapo cup (inside, holds the camera still and centered), printed as part of the head.
cup = ring(cx, cy, TAPO_FACE_D / 2 + 2.4, TAPO_FACE_D / 2 + 0.4, -WALL - 10, -WALL + 0.5)
head = trimesh.boolean.union([head, cup], engine="manifold")

# Shelf for the breadboard + ESP32, at the back (prints as a wall when face-down: no supports).
SHELF_Y, SHELF_FROM_Z = -33, -62
shelf = box((0, SHELF_Y - 1.5, (SHELF_FROM_Z - DEPTH) / 2), (200, 3, DEPTH + SHELF_FROM_Z))
shelf = trimesh.boolean.intersection([shelf, loft(offset_outline(front, 0.5), offset_outline(back, 0.5), -WALL, -DEPTH)], engine="manifold")
head = trimesh.boolean.union([head, shelf], engine="manifold")

cuts = [
    cyl(cx, cy, -WALL - 1, 1, LENS_HOLE / 2),                               # lens
    frustum(lx, ly, LASER_HOLE_BACK / 2, LASER_HOLE / 2, -WALL - 1, 1),     # laser (wider at the back)
    # slot in the bottom for the turret base: the turret's footprint + 3 mm, straight down
    box((lx, ly - TURRET_H / 2 + 12 - 150, tz), (TURRET_W + 6, TURRET_H + 300, TURRET_W + 6)),
    # antenna: vertical hole through the top, between the ears, halfway back
    cyl(0, -DEPTH * 0.45, -200, 0, ANTENNA_HOLE / 2, axis="y"),  # spans y 0..200 after the turn
    trimesh.creation.extrude_polygon(Polygon([mm(p) for p in NOSE]), NOSE_DEPTH + 0.01),
]
cuts[-1].apply_translation((0, 0, -NOSE_DEPTH))
head = trimesh.boolean.difference([head] + cuts, engine="manifold")

# Eye rings, printed separately (flat) and glued on the front.
r_front = LENS_HOLE / 2 + 7 * math.tan(math.radians(40))
bezel = trimesh.boolean.difference([frustum(0, 0, LENS_HOLE / 2 + 2, r_front + 2, 0, 7),
                                    frustum(0, 0, LENS_HOLE / 2, r_front, -0.5, 7.5)], engine="manifold")
holder = trimesh.boolean.difference([cyl(0, 0, 0, 3.5, RING_OD / 2 + 2.5),
                                     cyl(0, 0, -1, 5, LASER_HOLE / 2),
                                     cyl(0, 0, 0.6, 5, RING_OD / 2 + 0.4),        # ring pocket (0.6 mm floor)
                                     box((0, -RING_OD / 2, 0), (6, 6, 12))], engine="manifold")  # wire slot

def fit(part):
    return trimesh.boolean.intersection([inner, part], engine="manifold").volume / part.volume

def find_breadboard():
    """Somewhere inside that misses the Tapo and the turret (with 3 mm clearance)."""
    L, Wd, H = BREADBOARD
    keep = trimesh.boolean.union([tapo, turret], engine="manifold")
    best = None
    for dims in ((L, H, Wd), (L, Wd, H), (H, L, Wd), (Wd, L, H), (H, Wd, L), (Wd, H, L)):
        for y in range(-10, -9, 5):
            for x in range(-10, -9, 5):
                for z in range(-90, -91, -5):
                    b = box((x, y, z), [d + 6 for d in dims])
                    if fit(b) > 0.999 and trimesh.boolean.intersection([b, keep], engine="manifold").volume < 1:
                        return (x, y, z), dims
    return best

if __name__ == "__main__":
    head.export("wolf_head.stl"); bezel.export("camera_bezel.stl"); holder.export("led_ring_holder.stl")
    vol = head.volume + bezel.volume + holder.volume
    print(f"head {np.round(head.extents)} mm, watertight {head.is_watertight}, "
          f"plastic {vol/1000:.0f} cm3 (~{vol/1000*1.24:.0f} g), est {vol/9/3600*0.8:.1f}-{vol/9/3600*1.3:.1f} h")
    print(f"Tapo inside: {fit(tapo)*100:.0f}%   turret inside: {fit(turret)*100:.0f}% (rest goes through the bottom slot)")
    print("breadboard spot:", find_breadboard())
