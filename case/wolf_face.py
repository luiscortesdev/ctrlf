"""
wherewolf face plate: the logo's wolf head as a printable front panel.

Left eye (as you look at it): the CAMERA. A fixed eye: a flared bezel sticks out the front
(flared so it never blocks the wide lens), and a round cup on the back that the Tapo's
round face presses into, so the lens is centered automatically.
Right eye: the LASER. A raised ring that holds the LED ring (LEDs facing out, glowing red),
with an opening in the middle the laser pokes through and swings around in.
Nose: a shallow recess to paint or fill black. The back is flat (prints face-up, no supports); glue it to the box. The Tapo's cup prints
separately (tapo_cup.stl) and glues on behind the camera eye.

Every size is a setting below, in millimeters. MEASURE marks the ones to check with a
ruler (now filled in from the team's measurements); change them and run again:  python wolf_face.py   (writes wolf_face.stl, tapo_cup.stl)
Needs: pip install trimesh manifold3d shapely mapbox-earcut
"""

import math
import numpy as np
import trimesh
from shapely.geometry import Polygon, Point
from shapely import affinity

# ------------------------------------------------------------------ settings (mm)
FACE_W = 210            # face width; fits a 220 mm printer bed
PLATE_T = 3.0           # face thickness

# Camera eye (Tapo)
LENS_HOLE = 19.5        # lens + black ring measured 0.6 in (15.2 mm), plus room
CAM_BEZEL_OUT = 7       # how far the camera eye sticks out the front
CAM_BEZEL_FLARE = 40    # degrees from straight out; prints without supports, and stays out of the lens's view
TAPO_FACE_D = 58.4      # Tapo TCW61 round front face, measured 2.3 in
TAPO_CUP_DEPTH = 10     # how deep the Tapo sits in its cup

# Laser eye (LED ring + laser)
RING_OD = 45.7          # LED ring outside, measured 1.8 in
RING_ID = 31.75         # LED ring inside, measured 1.25 in
RING_POCKET = 3.4       # pocket depth for the ring board + LEDs (ring sits flush)
LASER_HOLE = 30         # front opening (just under RING_ID); widens toward the back
LASER_HOLE_BACK = 38    # opening at the back, so the beam clears at full swing
LASER_BOSS_OUT = 3.5    # how far the laser eye sticks out (= ring pocket: ring sits flush)

NOSE_DEPTH = 1.2

# ------------------------------------------------------------------ the logo's geometry
HEAD = [(155, 45), (283, 222), (390, 222), (517, 45), (572, 250), (624, 378), (442, 465),
        (336, 592), (230, 465), (48, 378), (100, 250)]
NOSE = [(296, 503), (376, 503), (336, 552)]
EYE_CAM, EYE_LASER = (246, 332), (426, 332)
S = FACE_W / 576.0
def mm(p):  # logo units -> mm, centered, y up
    return ((p[0] - 336) * S, -(p[1] - 318) * S)

def extrude(shape, h, z0=0.0):
    m = trimesh.creation.extrude_polygon(shape, h)
    m.apply_translation([0, 0, z0])
    return m

def ring(cx, cy, r_out, r_in, h, z0):
    shape = Point(cx, cy).buffer(r_out, 128).difference(Point(cx, cy).buffer(r_in, 128))
    return extrude(shape, h, z0)

def frustum(cx, cy, r0, r1, h, z0):
    """A solid cone section: radius r0 at z0, r1 at z0+h."""
    m = trimesh.creation.cylinder(radius=1, height=h, sections=128)
    v = m.vertices.copy()
    t = (v[:, 2] + h / 2) / h
    r = r0 + (r1 - r0) * t
    v[:, 0] = v[:, 0] * r + cx; v[:, 1] = v[:, 1] * r + cy
    v[:, 2] += h / 2 + z0
    m.vertices = v
    return m

def cone_shell(cx, cy, r0, r1, h, wall, z0):
    """A flared tube: inner radius r0 at z0 opening to r1 at z0+h."""
    def frustum(a, b):
        m = trimesh.creation.cylinder(radius=1, height=h, sections=128)
        v = m.vertices.copy()
        t = (v[:, 2] + h / 2) / h
        r = a + (b - a) * t
        v[:, 0] *= r; v[:, 1] *= r
        v[:, 2] += h / 2 + z0
        v[:, 0] += cx; v[:, 1] += cy
        m.vertices = v
        return m
    return trimesh.boolean.difference([frustum(r0 + wall, r1 + wall), frustum(r0, r1)], engine="manifold")

head = Polygon([mm(p) for p in HEAD])
cx, cy = mm(EYE_CAM)
lx, ly = mm(EYE_LASER)

# Front of the plate is +z. Plate from z=0 (back) to z=PLATE_T (front).
parts = [extrude(head, PLATE_T)]

# Camera eye: flared bezel in front. (The Tapo's cup is a separate part, see below.)
flare = math.radians(CAM_BEZEL_FLARE)
r_front = LENS_HOLE / 2 + CAM_BEZEL_OUT * math.tan(flare)
parts.append(cone_shell(cx, cy, LENS_HOLE / 2, r_front, CAM_BEZEL_OUT, 2.0, PLATE_T))

# Laser eye: raised boss around the LED ring.
parts.append(ring(lx, ly, RING_OD / 2 + 2.5, LASER_HOLE / 2, LASER_BOSS_OUT, PLATE_T))

# Laser placement (behind the face, it does NOT stick through): put the laser tip right
# behind the plate, centered on the eye, with the turret's pan pivot about 22 mm further
# back. That clears the turret base (36.8 mm wide) and lets the beam swing about +-26
# degrees through the eye. Further back = less swing.

# The back stays flat so the face prints face-up with no supports. Fix it to the box with
# hot glue or foam tape; the Tapo's cup is printed on its own and glued on the back.

face = trimesh.boolean.union(parts, engine="manifold")

# Cut-outs: lens hole, laser opening, LED ring pocket (+ its wire slot), nose recess.
cuts = [
    extrude(Point(cx, cy).buffer(LENS_HOLE / 2, 128), PLATE_T + 2, -1),
    frustum(lx, ly, LASER_HOLE_BACK / 2, LASER_HOLE / 2, PLATE_T + LASER_BOSS_OUT + 2, -1),
    extrude(Point(lx, ly).buffer(RING_OD / 2 + 0.4, 128), RING_POCKET + 0.01, PLATE_T + LASER_BOSS_OUT - RING_POCKET),
    # wire slot from the ring pocket down through the plate (ring's wires go out the back)
    extrude(Polygon([(lx - 3, ly - RING_OD / 2 - 0.2), (lx + 3, ly - RING_OD / 2 - 0.2),
                     (lx + 3, ly - RING_OD / 2 + 4), (lx - 3, ly - RING_OD / 2 + 4)]), PLATE_T + LASER_BOSS_OUT + 2, -1),
    extrude(Polygon([mm(p) for p in NOSE]), NOSE_DEPTH + 0.01, PLATE_T - NOSE_DEPTH),
]
face = trimesh.boolean.difference([face] + cuts, engine="manifold")

# Tapo cup: a ring glued to the back, centered on the lens hole (a lip helps you center it).
cup = trimesh.boolean.union([
    ring(0, 0, TAPO_FACE_D / 2 + 2.4, TAPO_FACE_D / 2 + 0.4, TAPO_CUP_DEPTH, 0),
    ring(0, 0, TAPO_FACE_D / 2 + 2.4, LENS_HOLE / 2 + 2, 1.6, 0),   # floor with a lens hole
], engine="manifold")

if __name__ == "__main__":
    face.export("wolf_face.stl")
    cup.export("tapo_cup.stl")
    b = face.bounds
    vol_cm3 = face.volume / 1000
    print(f"size: {b[1][0]-b[0][0]:.0f} x {b[1][1]-b[0][1]:.0f} x {b[1][2]-b[0][2]:.0f} mm, "
          f"watertight: {face.is_watertight}, plastic: {vol_cm3:.0f} cm3 (~{vol_cm3*1.24:.0f} g PLA solid)")
    print(f"camera eye at ({cx:.1f}, {cy:.1f}) mm, laser eye at ({lx:.1f}, {ly:.1f}) mm, "
          f"{lx-cx:.1f} mm apart; bezel front opening {2*r_front:.0f} mm")
