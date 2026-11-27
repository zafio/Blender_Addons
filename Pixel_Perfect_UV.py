bl_info = {
    "name": "Pixel Perfect UV",
    "author": "Zafio",
    "version": (0, 7, 4),
    "blender": (4, 5, 0),
    "location": "UV Editor / 3D View (Edit Mode) > Sidebar > Pixel UV, and the UV menus",
    "description": "Straightens, scales, snaps and packs UV islands onto the texture's pixel grid "
                   "with low distortion and per-island texel density",
    "category": "UV",
}

"""
Pipeline (each step can also be run on its own):

1. Straighten  - quad-grid islands are laid out as perfect rectangles (column widths / row
                 heights from the averaged 3D edge lengths). An island is only straightened if
                 the result stays under the distortion limit; otherwise it keeps its unwrap and
                 is just rotated so its bounding box is axis aligned.
2. Density     - every island is scaled to the same texel density (px per unit), or to its own
                 density / multiplier stored on its faces (see "Island Density").
   Islands mixing quads with triangles / n-gons get their main quad group straightened and
                 pinned, and Blender's unwrap fits the rest around it (idea from noio's
                 Pixel-Unwrapper, MIT: https://github.com/noio/Pixel-Unwrapper).
3. Snap        - vertices land on pixel corners. Straight islands keep every row / column at
                 least 1 px wide so no face collapses.
4. Pack        - islands are packed in whole pixels with pixel padding (MaxRects), optionally
                 rotated by 90 degrees. "Fit" mode finds the highest density that fits.
"""

import heapq
import math
from collections import defaultdict, deque

try:
    import bpy
    import bmesh
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
except ImportError:  # allows the core to be tested outside Blender
    bpy = None

DENSITY_ATTR = "pixuv_density"   # face float: fixed px/unit for the island (0 = use global)
SCALE_ATTR = "pixuv_scale"       # face float: multiplier on the global density (0/1 = none)

EPS = 1e-6
PIXEL_UV_TEXTURE_API = None  # set while the add-on is enabled (see TextureSession)


# ============================================================================
# Core (pure Python, no bpy)
# ============================================================================

class FaceRec:
    """One mesh face: vertex keys, current UVs in pixels, world positions, density settings."""
    __slots__ = ("vkeys", "uv", "p3", "density", "scale", "ref", "keep_rot", "pl", "group")

    def __init__(self, vkeys, uv, p3, density=0.0, scale=1.0, ref=None, keep_rot=False,
                 pl=None, group=None):
        self.keep_rot = keep_rot
        self.pl = pl        # object-space positions (mirror detection); world p3 if None
        self.group = group  # islands only mirror-pair inside the same group (object)
        self.vkeys = vkeys
        self.uv = uv
        self.p3 = p3
        self.density = density
        self.scale = scale
        self.ref = ref


class Island:
    def __init__(self):
        self.faces = []       # per face: list of local uv-vertex indices
        self.face_ids = []    # index into the FaceRec list
        self.uv = []          # original px position per uv-vertex
        self.p3 = []          # world position per uv-vertex
        self.pl = []          # object-space position per uv-vertex
        self.group = None
        self.density = 0.0    # fixed px/unit (0 = global)
        self.scale = 1.0      # multiplier on global density
        # filled by the pipeline
        self.units = None     # layout in world units (isotropic)
        self.kind = "keep"    # "straight", "aligned", "keep", "skip"
        self.mask = None       # per uv-vertex: on a straight row / column
        self.keep_rot = False  # already straightened (mixed island): don't re-rotate
        self.distortion = 0.0
        self.new = None       # final px coords


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _len3(a, b):
    d = _sub(a, b)
    return math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])


def _len2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def area3d(pts):
    if len(pts) < 3:
        return 0.0
    o = pts[0]
    sx = sy = sz = 0.0
    for i in range(1, len(pts) - 1):
        a = _sub(pts[i], o)
        b = _sub(pts[i + 1], o)
        sx += a[1] * b[2] - a[2] * b[1]
        sy += a[2] * b[0] - a[0] * b[2]
        sz += a[0] * b[1] - a[1] * b[0]
    return 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)


def signed_area2d(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def find_islands(recs):
    """Faces are in the same island when they share an edge with identical UVs on both ends."""
    def q(p):
        return (round(p[0] * 1e4), round(p[1] * 1e4))

    corner_keys = [[(k, q(u)) for k, u in zip(r.vkeys, r.uv)] for r in recs]
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges = defaultdict(list)
    for fi, ck in enumerate(corner_keys):
        n = len(ck)
        for i in range(n):
            a, b = ck[i], ck[(i + 1) % n]
            edges[frozenset((a, b))].append(fi)
    for fl in edges.values():
        for other in fl[1:]:
            ra, rb = find(fl[0]), find(other)
            if ra != rb:
                parent[ra] = rb

    groups = defaultdict(list)
    for fi in range(len(recs)):
        groups[find(fi)].append(fi)

    islands = []
    for fl in groups.values():
        isl = Island()
        local = {}
        dens = []
        sc_sum = sc_w = 0.0
        for fi in fl:
            r = recs[fi]
            face = []
            pls = r.pl or r.p3
            for key, uv, p, q in zip(corner_keys[fi], r.uv, r.p3, pls):
                idx = local.get(key)
                if idx is None:
                    idx = local[key] = len(isl.uv)
                    isl.uv.append((uv[0], uv[1]))
                    isl.p3.append(p)
                    isl.pl.append(q)
                face.append(idx)
            isl.faces.append(face)
            isl.face_ids.append(fi)
            if r.density > 0:
                dens.append(r.density)
            a = area3d(r.p3) + 1e-12
            sc_sum += (r.scale if r.scale > 0 else 1.0) * a
            sc_w += a
        isl.group = recs[fl[0]].group
        isl.keep_rot = any(recs[fi].keep_rot for fi in fl)
        isl.density = max(dens) if dens else 0.0
        isl.scale = sc_sum / sc_w if sc_w else 1.0  # area-weighted multiplier
        islands.append(isl)
    return islands


def island_areas(isl, coords):
    a3 = sum(area3d([isl.p3[i] for i in f]) for f in isl.faces)
    a2 = sum(signed_area2d([coords[i] for i in f]) for f in isl.faces)
    return a3, a2


def abs_area(isl, coords):
    """Sum of unsigned face areas (folded / self-mirrored islands don't cancel out)."""
    return sum(abs(signed_area2d([coords[i] for i in f])) for f in isl.faces)


def face_signs(isl, coords):
    """+1 / -1 per face (0 for degenerate faces) in coords."""
    out = []
    for f in isl.faces:
        a = signed_area2d([coords[i] for i in f])
        out.append(0 if abs(a) < 1e-9 else (1 if a > 0 else -1))
    return out


def distortion(isl, coords):
    """Area-weighted mean |log(stretch)| of the edges, after removing the island's uniform scale.
    0 = no distortion, 0.1 is about 10 % stretch."""
    a3, _ = island_areas(isl, coords)
    a2 = abs_area(isl, coords)
    if a3 < EPS or a2 < EPS:
        return float("inf")
    k = math.sqrt(a3 / a2)  # uv -> world scale
    tot = 0.0
    wsum = 0.0
    for f in isl.faces:
        n = len(f)
        for i in range(n):
            a, b = f[i], f[(i + 1) % n]
            l3 = _len3(isl.p3[a], isl.p3[b])
            l2 = _len2(coords[a], coords[b]) * k
            if l3 < EPS:
                continue
            if l2 < EPS:
                return float("inf")
            tot += abs(math.log(l2 / l3)) * l3
            wsum += l3
    # faces flipped compared to the island's own unwrap count as heavy distortion
    for s0, s1 in zip(face_signs(isl, isl.uv), face_signs(isl, coords)):
        if s0 and s1 != s0:
            return float("inf")
    return tot / wsum if wsum > 0 else 0.0


def _centroid(pts):
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def lattice_layout(isl):
    """Rectangular layout for an all-quad grid island, in world units. None if not a clean grid."""
    faces = isl.faces
    if not faces or any(len(f) != 4 or len(set(f)) != 4 for f in faces):
        return None
    edge_faces = defaultdict(list)
    for fi, f in enumerate(faces):
        for k in range(4):
            edge_faces[frozenset((f[k], f[(k + 1) % 4]))].append(fi)
    if any(len(v) > 2 for v in edge_faces.values()):
        return None

    def nbrs(fi):
        f = faces[fi]
        return sum(len(edge_faces[frozenset((f[k], f[(k + 1) % 4]))]) - 1 for k in range(4))

    start = max(range(len(faces)), key=nbrs)
    lat = {}
    for c, p in zip(faces[start], ((0, 0), (1, 0), (1, 1), (0, 1))):
        lat[c] = p
    visited = {start}
    queue = deque([start])
    while queue:
        fi = queue.popleft()
        f = faces[fi]
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            a_prev = f[(k - 1) % 4]
            step = (lat[a][0] - lat[a_prev][0], lat[a][1] - lat[a_prev][1])
            for nf in edge_faces[frozenset((a, b))]:
                if nf == fi:
                    continue
                g = faces[nf]
                ia, ib = g.index(a), g.index(b)
                if g[(ia + 1) % 4] == b:
                    oa, ob = g[(ia - 1) % 4], g[(ib + 1) % 4]
                elif g[(ia - 1) % 4] == b:
                    oa, ob = g[(ia + 1) % 4], g[(ib - 1) % 4]
                else:
                    return None
                for v, base in ((oa, a), (ob, b)):
                    p = (lat[base][0] + step[0], lat[base][1] + step[1])
                    if v in lat:
                        if lat[v] != p:
                            return None
                    else:
                        lat[v] = p
                if nf not in visited:
                    visited.add(nf)
                    queue.append(nf)
    if len(visited) != len(faces) or len(lat) != len(isl.uv):
        return None
    if len(set(lat.values())) != len(lat):
        return None  # grid folds onto itself (e.g. an uncut cylinder)
    for f in faces:
        pts = [lat[c] for c in f]
        xs = sorted({p[0] for p in pts})
        ys = sorted({p[1] for p in pts})
        if len(xs) != 2 or len(ys) != 2 or xs[1] - xs[0] != 1 or ys[1] - ys[0] != 1:
            return None

    cols = defaultdict(list)
    rows = defaultdict(list)
    for f in faces:
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            pa, pb = lat[a], lat[b]
            l3 = _len3(isl.p3[a], isl.p3[b])
            if pa[1] == pb[1]:
                cols[min(pa[0], pb[0])].append(l3)
            else:
                rows[min(pa[1], pb[1])].append(l3)
    X = {}
    Y = {}
    xs = sorted({p[0] for p in lat.values()})
    ys = sorted({p[1] for p in lat.values()})
    X[xs[0]] = 0.0
    for x in xs[1:]:
        X[x] = X[x - 1] + sum(cols[x - 1]) / len(cols[x - 1])
    Y[ys[0]] = 0.0
    for y in ys[1:]:
        Y[y] = Y[y - 1] + sum(rows[y - 1]) / len(rows[y - 1])
    coords = [(X[lat[i][0]], Y[lat[i][1]]) for i in range(len(isl.uv))]

    # keep the original winding (never mirror the texture) ...
    _, a_orig = island_areas(isl, isl.uv)
    _, a_new = island_areas(isl, coords)
    if a_orig * a_new < 0:
        coords = [(x, -y) for x, y in coords]
    # ... and the 90-degree rotation closest to the original orientation
    co = _centroid(isl.uv)
    orig = [(u - co[0], v - co[1]) for u, v in isl.uv]
    cn = _centroid(coords)
    base = [(x - cn[0], y - cn[1]) for x, y in coords]
    best, best_score = base, -float("inf")
    for rot in range(4):
        cand = base
        for _ in range(rot):
            cand = [(-y, x) for x, y in cand]
        score = sum(a[0] * b[0] + a[1] * b[1] for a, b in zip(cand, orig))
        if score > best_score:
            best, best_score = cand, score
    return best


def quad_part(isl):
    """Largest edge-connected group of quads in a mixed island, as its own Island.
    Returns (sub_island, sub_index -> island_index) or (None, None)."""
    quads = [fi for fi, f in enumerate(isl.faces) if len(f) == 4]
    if not quads or len(quads) == len(isl.faces):
        return None, None
    edge_faces = defaultdict(list)
    for fi in quads:
        f = isl.faces[fi]
        for k in range(4):
            edge_faces[frozenset((f[k], f[(k + 1) % 4]))].append(fi)
    seen = set()
    best = []
    for start in quads:
        if start in seen:
            continue
        comp = []
        stack = [start]
        seen.add(start)
        while stack:
            fi = stack.pop()
            comp.append(fi)
            f = isl.faces[fi]
            for k in range(4):
                for g in edge_faces[frozenset((f[k], f[(k + 1) % 4]))]:
                    if g not in seen:
                        seen.add(g)
                        stack.append(g)
        if len(comp) > len(best):
            best = comp
    sub = Island()
    remap = {}
    back = []
    for fi in best:
        face = []
        for v in isl.faces[fi]:
            if v not in remap:
                remap[v] = len(back)
                back.append(v)
                sub.uv.append(isl.uv[v])
                sub.p3.append(isl.p3[v])
            face.append(remap[v])
        sub.faces.append(face)
        sub.face_ids.append(isl.face_ids[fi])
    return sub, back


def mixed_grid(isl, max_distortion):
    """For a quad + non-quad island: straight layout of its main quad group, scaled to the
    group's current size and placed where it is now (px). Returns {island_vertex: (x, y)} or None."""
    sub, back = quad_part(isl)
    if sub is None or len(sub.faces) < 2:
        return None
    lay = lattice_layout(sub)
    if lay is None or distortion(sub, lay) > max_distortion:
        return None
    k = math.sqrt(abs_area(sub, sub.uv) / max(abs_area(sub, lay), EPS))
    co = _centroid(sub.uv)
    return {back[i]: (x * k + co[0], y * k + co[1]) for i, (x, y) in enumerate(lay)}


def convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def align_layout(isl, rotate=True):
    """Original unwrap in world units (uniform scale only, no added distortion), optionally rotated
    so its minimum-area bounding box is axis aligned."""
    a3, _ = island_areas(isl, isl.uv)
    a2 = abs_area(isl, isl.uv)
    if a3 < EPS or a2 < EPS:
        return None
    k = math.sqrt(a3 / a2)
    c = _centroid(isl.uv)
    pts = [((u - c[0]) * k, (v - c[1]) * k) for u, v in isl.uv]
    if not rotate:
        return pts
    hull = convex_hull(pts)
    best_ang, best_area = 0.0, float("inf")
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        # fold to the smallest equivalent rotation (-45..45 deg)
        ang = (ang + math.pi / 4) % (math.pi / 2) - math.pi / 4
        cs, sn = math.cos(-ang), math.sin(-ang)
        xs = [x * cs - y * sn for x, y in hull]
        ys = [x * sn + y * cs for x, y in hull]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area < best_area - 1e-9 or (abs(area - best_area) <= 1e-9 and abs(ang) < abs(best_ang)):
            best_area, best_ang = area, ang
    cs, sn = math.cos(-best_ang), math.sin(-best_ang)
    return [(x * cs - y * sn, x * sn + y * cs) for x, y in pts]


def grid_mask(isl, coords, tol=1e-4):
    """True for uv-vertices that belong to at least one axis-aligned quad (rows / columns)."""
    mask = [False] * len(coords)
    for f in isl.faces:
        if len(f) != 4:
            continue
        if all(abs(coords[f[k]][0] - coords[f[(k + 1) % 4]][0]) <= tol or
               abs(coords[f[k]][1] - coords[f[(k + 1) % 4]][1]) <= tol for k in range(4)):
            for v in f:
                mask[v] = True
    return mask


def is_grid(isl, coords, tol=1e-4):
    return all(len(f) == 4 for f in isl.faces) and all(grid_mask(isl, coords, tol))


def _snap_axis(vals, min_gap):
    """Rounds to integers keeping the order; values more than min_gap apart never merge."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0] * len(vals)
    prev_c = prev_s = None
    for i in order:
        v = vals[i]
        if prev_c is not None and v - prev_c < 1e-4:
            out[i] = prev_s
            continue
        s = round(v)
        if prev_s is not None and s <= prev_s:
            s = prev_s + 1 if (v - prev_c) >= min_gap else prev_s
        out[i] = s
        prev_c, prev_s = v, s
    return out


def snap(coords, mask, isl=None):
    """Grid vertices (mask True): ordered per-axis rounding, so rows / columns stay straight and
    never collapse below 1 px. Other vertices: plain rounding, then vertices of flipped or
    collapsed faces are nudged by 1 px."""
    if mask is True or mask is False:
        mask = [mask] * len(coords)
    out = [(round(x), round(y)) for x, y in coords]
    gi = [i for i, m in enumerate(mask) if m]
    if gi:
        xs = _snap_axis([coords[i][0] for i in gi], 0.0)
        ys = _snap_axis([coords[i][1] for i in gi], 0.0)
        for i, x, y in zip(gi, xs, ys):
            out[i] = (x, y)
    if isl is not None and not all(mask):
        out = _repair(isl, coords, out, movable=[not m for m in mask])
    return out


def _repair(isl, orig, out, passes=4, movable=None):
    signs = face_signs(isl, orig)
    vfaces = defaultdict(list)
    for fi, f in enumerate(isl.faces):
        for v in f:
            vfaces[v].append(fi)

    def ok(fi, pts):
        if not signs[fi]:
            return True
        return signed_area2d([pts[i] for i in isl.faces[fi]]) * signs[fi] > 0

    offsets = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    out = list(out)
    for _ in range(passes):
        bad = [fi for fi in range(len(isl.faces)) if not ok(fi, out)]
        if not bad:
            break
        for fi in bad:
            if ok(fi, out):
                continue
            best = None
            for v in isl.faces[fi]:
                if movable is not None and not movable[v]:
                    continue
                base = out[v]
                for dx, dy in offsets:
                    out[v] = (base[0] + dx, base[1] + dy)
                    nbad = sum(1 for g in vfaces[v] if not ok(g, out))
                    err = (out[v][0] - orig[v][0]) ** 2 + (out[v][1] - orig[v][1]) ** 2
                    key = (nbad, err)
                    if best is None or key < best[0]:
                        best = (key, v, out[v])
                out[v] = base
            if best is None:
                continue
            cur_bad = sum(1 for g in vfaces[best[1]] if not ok(g, out))
            if best[0][0] < cur_bad:
                out[best[1]] = best[2]
    return out


def broken_faces(isl, coords):
    """Faces that collapsed or flipped compared to the island's original unwrap."""
    return sum(1 for s0, s1 in zip(face_signs(isl, isl.uv), face_signs(isl, coords)) if s0 and s1 != s0)


# ---- packing (MaxRects, best short side fit, integer pixels) ----------------

def pack_rects(sizes, W, H, pad, allow_rotate):
    """sizes: list of (w, h) ints. Returns list of (x, y, rotated) or None if they don't fit.
    Gap between islands and to the border = pad."""
    free = [(pad, pad, W, H)]  # x1, y1, x2, y2 (each item takes w + pad, h + pad)
    if free[0][2] <= free[0][0] or free[0][3] <= free[0][1]:
        return None
    order = sorted(range(len(sizes)), key=lambda i: (max(sizes[i]), sizes[i][0] * sizes[i][1]),
                   reverse=True)
    out = [None] * len(sizes)
    for i in order:
        w0, h0 = sizes[i]
        best = None
        options = [(w0, h0, False)]
        if allow_rotate and w0 != h0:
            options.append((h0, w0, True))
        for w, h, rot in options:
            ww, hh = w + pad, h + pad
            for fx1, fy1, fx2, fy2 in free:
                fw, fh = fx2 - fx1, fy2 - fy1
                if ww <= fw and hh <= fh:
                    short = min(fw - ww, fh - hh)
                    long_ = max(fw - ww, fh - hh)
                    key = (short, long_, fy1, fx1)
                    if best is None or key < best[0]:
                        best = (key, fx1, fy1, ww, hh, rot)
        if best is None:
            return None
        _, x, y, ww, hh, rot = best
        out[i] = (x, y, rot)
        px1, py1, px2, py2 = x, y, x + ww, y + hh
        new_free = []
        for f in free:
            fx1, fy1, fx2, fy2 = f
            if px1 >= fx2 or px2 <= fx1 or py1 >= fy2 or py2 <= fy1:
                new_free.append(f)
                continue
            if px1 > fx1:
                new_free.append((fx1, fy1, px1, fy2))
            if px2 < fx2:
                new_free.append((px2, fy1, fx2, fy2))
            if py1 > fy1:
                new_free.append((fx1, fy1, fx2, py1))
            if py2 < fy2:
                new_free.append((fx1, py2, fx2, fy2))
        pruned = []
        for a in new_free:
            if any(a is not b and b[0] <= a[0] and b[1] <= a[1] and b[2] >= a[2] and b[3] >= a[3]
                   and (b != a or id(b) < id(a)) for b in new_free):
                continue
            pruned.append(a)
        free = pruned
    return out


def _local(snapped):
    mx = min(p[0] for p in snapped)
    my = min(p[1] for p in snapped)
    loc = [(x - mx, y - my) for x, y in snapped]
    w = max(p[0] for p in loc)
    h = max(p[1] for p in loc)
    return loc, w, h


def _rot90(loc, h):
    return [(h - y, x) for x, y in loc]


class Options:
    def __init__(self, **kw):
        self.straighten = True
        self.max_distortion = 0.12
        self.align = True
        self.density_mode = "FIT"     # FIT, FIXED, CURRENT
        self.density = 32.0           # px / unit for FIXED
        self.snap = True
        self.snap_mode = "BOUNDS"     # BOUNDS (outer edges on pixel lines) or ALL (every vertex)
        self.pack = True
        self.padding = 1
        self.allow_rotate = False
        self.stack_mirrored = True
        self.keep_density = False      # Preserve Texel Density
        self.density_tolerance = 0.05  # largest size change snapping may cause when preserving
        self.__dict__.update(kw)


def _keep(opt):
    return opt.density_tolerance if getattr(opt, "keep_density", False) else None


PIXEL_AREA = 0.25  # px^2: islands smaller than this are single-colour (one pixel) islands


def is_pixel_island(isl):
    xs = [p[0] for p in isl.uv]
    ys = [p[1] for p in isl.uv]
    a2 = abs_area(isl, isl.uv)
    return a2 < PIXEL_AREA and max(xs) - min(xs) < 1.5 and max(ys) - min(ys) < 1.5


def current_density(islands):
    a3 = a2 = 0.0
    for isl in islands:
        x, _ = island_areas(isl, isl.uv)
        y = abs_area(isl, isl.uv)
        if y < PIXEL_AREA:
            continue
        a3 += x
        a2 += y
    return math.sqrt(a2 / a3) if a3 > EPS else 0.0


# ---- mirrored islands --------------------------------------------------------

def _match_mirror(A, B, axis, tol):
    """slave (B) uv-vertex -> master (A) uv-vertex, matching face by face. None if not a mirror."""
    def mir(p):
        q = list(p)
        q[axis] = -q[axis]
        return q

    def fc(isl, f):
        n = len(f)
        return [sum(isl.pl[v][k] for v in f) / n for k in range(3)]

    a_cent = [fc(A, f) for f in A.faces]
    used_faces = set()
    mp = {}
    t2 = tol * tol
    for f in B.faces:
        c = mir(fc(B, f))
        best, bd = None, t2
        for fi, ac in enumerate(a_cent):
            if fi in used_faces or len(A.faces[fi]) != len(f):
                continue
            d = (ac[0] - c[0]) ** 2 + (ac[1] - c[1]) ** 2 + (ac[2] - c[2]) ** 2
            if d <= bd:
                best, bd = fi, d
        if best is None:
            return None
        used_faces.add(best)
        af = A.faces[best]
        for v in f:
            q = mir(B.pl[v])
            w = min(af, key=lambda u: sum((A.pl[u][k] - q[k]) ** 2 for k in range(3)))
            if sum((A.pl[w][k] - q[k]) ** 2 for k in range(3)) > t2:
                return None
            if mp.setdefault(v, w) != w:
                return None
    if len(mp) != len(B.pl) or len(set(mp.values())) != len(mp):
        return None
    return mp


def find_mirror_pairs(islands, axis=0, tol_rel=6e-3):
    """Islands that are mirror images of each other across the object's X = 0 plane (small
    asymmetries of about half a percent of the model size are tolerated).
    Returns [(master, slave, slave_vertex -> master_vertex)], master on the +X side."""
    pts = [p for isl in islands for p in isl.pl]
    if not pts:
        return []
    size = max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3)) or 1.0

    def cent(isl):
        n = len(isl.pl)
        return [sum(p[k] for p in isl.pl) / n for k in range(3)]

    def min_edge(isl):
        m = float("inf")
        for f in isl.faces:
            for i in range(len(f)):
                d = _len3(isl.pl[f[i]], isl.pl[f[(i + 1) % len(f)]])
                if d > 1e-9:
                    m = min(m, d)
        return m

    cents = [cent(i) if i.pl else [0, 0, 0] for i in islands]
    used = set()
    pairs = []
    order = sorted(range(len(islands)), key=lambda k: -cents[k][axis])
    for a in order:
        A = islands[a]
        if a in used or not A.pl:
            continue
        tol = min(size * tol_rel, 0.3 * min_edge(A))
        if cents[a][axis] <= tol:
            continue
        ma = list(cents[a])
        ma[axis] = -ma[axis]
        for b in order:
            B = islands[b]
            if b == a or b in used or B.group != A.group:
                continue
            if len(B.faces) != len(A.faces) or len(B.pl) != len(A.pl):
                continue
            if sum((cents[b][k] - ma[k]) ** 2 for k in range(3)) > (size * 0.02) ** 2:
                continue
            mp = _match_mirror(A, B, axis, tol)
            if mp is None:
                continue
            pairs.append((a, b, mp))
            used.update((a, b))
            break
    return pairs


def stack_mirrored(islands, pairs):
    """Slave islands take their master's UVs (current positions)."""
    for a, b, mp in pairs:
        src = islands[a].new if islands[a].new is not None else islands[a].uv
        islands[b].new = [src[mp[j]] for j in range(len(islands[b].uv))]


# ---- pipeline ----------------------------------------------------------------

def build_layouts(islands, opt):
    for isl in islands:
        isl.units = None
        isl.kind = "skip"
        if is_pixel_island(isl):
            isl.kind = "pixel"
            continue
        lay = lattice_layout(isl) if opt.straighten else None
        if lay is not None:
            d = distortion(isl, lay)
            if d <= opt.max_distortion:
                isl.units, isl.kind, isl.mask, isl.distortion = lay, "straight", [True] * len(lay), d
                continue
        lay = align_layout(isl, rotate=opt.align and not isl.keep_rot)
        if lay is not None:
            isl.units = lay
            isl.kind = "straight" if isl.keep_rot else ("aligned" if opt.align else "keep")
            isl.mask = grid_mask(isl, lay)
            isl.distortion = distortion(isl, lay)


def _island_density(isl, base):
    return isl.density if isl.density > 0 else base * isl.scale


def snap_bounds(pts):
    """Only the island's bounding box goes onto pixel lines (min corner on a pixel corner, width
    and height whole pixels); the inside keeps its shape (scaled by under half a pixel)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    sx = max(1, round(w)) / w if w > 1e-6 else 1.0
    sy = max(1, round(h)) / h if h > 1e-6 else 1.0
    nx, ny = round(x0), round(y0)
    return [(nx + (x - x0) * sx, ny + (y - y0) * sy) for x, y in pts]


def snap_translate(pts):
    """Moves the island so its bounding box corner sits on a pixel corner; size untouched."""
    x0 = min(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    dx, dy = round(x0) - x0, round(y0) - y0
    return [(x + dx, y + dy) for x, y in pts]


def snap_change(pts, snapped):
    """How much snapping changed the island: worst of width / height / area change (fraction)."""
    def ext(ps, k):
        return max(p[k] for p in ps) - min(p[k] for p in ps)
    worst = 0.0
    for k in (0, 1):
        a, b = ext(pts, k), ext(snapped, k)
        if a > 1e-6:
            worst = max(worst, abs(b / a - 1.0))
    return worst


def snap_island(isl, pts, mode, keep_density=None):
    """keep_density: None, or the largest size change (fraction) snapping may cause; islands that
    would change more are only moved onto the grid, keeping their exact texel density."""
    out = _snap_island(isl, pts, mode)
    if keep_density is not None and snap_change(pts, out) > keep_density:
        return snap_translate(pts)
    return out


def _snap_island(isl, pts, mode):
    """Straightened islands: strict grid snap (rows / columns never collapse). Anything else:
    Bounds (outer edges only) or, in ALL mode, plain rounding + repair. A free-form island
    that just happens to contain a few axis-aligned quads must not get the strict grid snap:
    its many distinct coordinates would be pushed apart pixel by pixel and faces would flip."""
    if isl.kind == "straight" and isl.mask is not None and any(isl.mask):
        return snap(pts, isl.mask, isl)
    if mode == "ALL":
        return snap(pts, False, isl)
    return snap_bounds(pts)


def _place(isl, base, do_snap, mode="ALL", keep_density=None):
    d = _island_density(isl, base)
    pts = [(x * d, y * d) for x, y in isl.units]
    return snap_island(isl, pts, mode, keep_density) if do_snap else pts


def _try_pack(islands, base, W, H, opt):
    locs = []
    sizes = []
    for isl in islands:
        if isl.kind == "pixel":
            locs.append([(0.5, 0.5)] * len(isl.uv))
            sizes.append((1, 1))
            continue
        loc, w, h = _local(_place(isl, base, opt.snap, opt.snap_mode, _keep(opt)))
        locs.append(loc)
        sizes.append((max(1, math.ceil(w - 1e-9)), max(1, math.ceil(h - 1e-9))))
    res = pack_rects(sizes, W, H, opt.padding, opt.allow_rotate)
    return res, locs, sizes


def run(islands, W, H, opt):
    """Full pipeline. Fills isl.new (px). Returns (stats dict, error string or None)."""
    pairs = find_mirror_pairs(islands) if opt.stack_mirrored else []
    slaves = {b for _, b, _ in pairs}
    work = [isl for k, isl in enumerate(islands) if k not in slaves]
    build_layouts(work, opt)
    active = [i for i in work if i.units is not None or i.kind == "pixel"]
    sized = [i for i in active if i.kind != "pixel"]
    if not sized and not active:
        return None, "No usable islands (zero-area faces?)"

    free_isl = [i for i in sized if i.density <= 0]
    if opt.density_mode == "FIXED":
        base = opt.density
    elif opt.density_mode == "CURRENT" or not opt.pack:
        base = current_density(sized) or 1.0
    else:
        base = None  # FIT

    if opt.pack:
        if base is None:
            if not free_isl:
                base = 1.0  # every island has a fixed density: nothing to fit
            else:
                tot = sum(_bbox_area(i.units) * (i.scale ** 2) for i in free_isl)
                hi = math.sqrt(W * H / max(tot, EPS)) * 1.5
                lo = hi / 256.0
                if _try_pack(active, lo, W, H, opt)[0] is None:
                    return None, "Islands don't fit even at a tiny density (fixed-density islands too big?)"
                for _ in range(40):
                    mid = (lo + hi) / 2
                    if _try_pack(active, mid, W, H, opt)[0] is None:
                        hi = mid
                    else:
                        lo = mid
                base = lo
        res, locs, sizes = _try_pack(active, base, W, H, opt)
        if res is None:
            return None, ("Islands don't fit in %d x %d at %.2f px/unit. Lower the density, use Fit, "
                          "or a bigger texture" % (W, H, base))
        for isl, loc, (w, h), (x, y, rot) in zip(active, locs, sizes, res):
            if rot:
                loc = _rot90(loc, h)
            isl.new = [(px + x, py + y) for px, py in loc]
    else:
        for isl in active:
            if isl.kind == "pixel":
                isl.new = list(isl.uv)
                continue
            pts = _place(isl, base, False)
            cn = _centroid(pts)
            co = _centroid(isl.uv)
            off = (co[0] - cn[0], co[1] - cn[1])
            if opt.snap:
                off = (round(off[0]), round(off[1]))
                pts = snap_island(isl, pts, opt.snap_mode, _keep(opt))
            isl.new = [(x + off[0], y + off[1]) for x, y in pts]

    stack_mirrored(islands, pairs)
    stats = {
        "density": base,
        "straight": sum(1 for i in sized if i.kind == "straight"),
        "aligned": sum(1 for i in sized if i.kind in ("aligned", "keep")),
        "pixel": sum(1 for i in active if i.kind == "pixel"),
        "mirrored": len(pairs),
        "skipped": len(work) - len(active),
        "broken": sum(broken_faces(i, i.new) for i in sized),
        "max_distortion": max((i.distortion for i in sized if i.kind == "straight"), default=0.0),
    }
    return stats, None


def _bbox_area(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(max(xs) - min(xs), 1e-4) * max(max(ys) - min(ys), 1e-4)


def run_snap_only(islands, mode="ALL", keep_density=None):
    for isl in islands:
        if is_pixel_island(isl):
            isl.new = None
            continue
        isl.mask = grid_mask(isl, isl.uv)
        isl.kind = "straight" if is_grid(isl, isl.uv) else "keep"
        isl.new = snap_island(isl, isl.uv, mode, keep_density)
    return {"broken": sum(broken_faces(i, i.new) for i in islands if i.new is not None)}


def run_straighten_only(islands, opt):
    """Straightens / aligns but keeps each island's size and position."""
    build_layouts(islands, opt)
    for isl in islands:
        isl.new = None
        if isl.units is None or (isl.kind != "straight" and not opt.align):
            continue
        k = math.sqrt(abs_area(isl, isl.uv) / max(abs_area(isl, isl.units), EPS))
        co = _centroid(isl.uv)
        isl.new = [(x * k + co[0], y * k + co[1]) for x, y in isl.units]
    return {
        "straight": sum(1 for i in islands if i.kind == "straight"),
        "aligned": sum(1 for i in islands if i.kind == "aligned"),
    }


def run_stack_only(islands):
    pairs = find_mirror_pairs(islands)
    stack_mirrored(islands, pairs)
    return len(pairs)


def free_pixel(occupied, W, H, margin=1):
    """First pixel (from the top-left) with no occupied pixel within margin."""
    for j in range(H - 1 - margin, margin - 1, -1):
        for i in range(margin, W - margin):
            if all((i + di, j + dj) not in occupied
                   for di in range(-margin, margin + 1) for dj in range(-margin, margin + 1)):
                return i, j
    return None


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def pixel_mask(isl, coords, W, H):
    """Pixels (i, j) whose centre lies inside one of the island's faces (coords in px)."""
    cells = set()
    for f in isl.faces:
        poly = [coords[v] for v in f]
        x0 = max(0, math.floor(min(p[0] for p in poly)))
        x1 = min(W - 1, math.ceil(max(p[0] for p in poly)))
        y0 = max(0, math.floor(min(p[1] for p in poly)))
        y1 = min(H - 1, math.ceil(max(p[1] for p in poly)))
        for j in range(y0, y1 + 1):
            for i in range(x0, x1 + 1):
                if _point_in_poly(i + 0.5, j + 0.5, poly):
                    cells.add((i, j))
    if not cells:  # sliver: take the pixels its vertices touch
        for x, y in coords:
            i, j = min(max(int(math.floor(x)), 0), W - 1), min(max(int(math.floor(y)), 0), H - 1)
            cells.add((i, j))
    return cells


def group_overlapping(islands, masks):
    """Islands sharing pixels (stacked / mirrored halves) stay together as one packing unit."""
    owner = {}
    parent = list(range(len(islands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for k, m in enumerate(masks):
        for c in m:
            o = owner.get(c)
            if o is None:
                owner[c] = k
            else:
                ra, rb = find(o), find(k)
                if ra != rb:
                    parent[ra] = rb
    groups = defaultdict(list)
    for k in range(len(islands)):
        groups[find(k)].append(k)
    return list(groups.values())


class PackMove:
    """Integer move of a group of islands: pixel block [mx, mx+w) x [my, my+h) goes to (x, y),
    rotated 90 degrees counter-clockwise first when rot is True."""
    __slots__ = ("members", "mx", "my", "w", "h", "x", "y", "rot")

    def __init__(self, members, mx, my, w, h, x, y, rot):
        self.members, self.mx, self.my, self.w, self.h = members, mx, my, w, h
        self.x, self.y, self.rot = x, y, rot

    def cell(self, i, j):
        a, b = i - self.mx, j - self.my
        if self.rot:
            a, b = self.h - 1 - b, a
        return a + self.x, b + self.y


def run_pack_only(islands, W, H, opt, keep_overlaps=True):
    """Packs the islands at their current size (whole-pixel moves / 90 degree turns only).
    Returns (error, moves, masks)."""
    masks = [pixel_mask(isl, isl.uv, W, H) for isl in islands]
    groups = group_overlapping(islands, masks) if keep_overlaps else [[k] for k in range(len(islands))]
    boxes, sizes = [], []
    for g in groups:
        pts = [p for k in g for p in islands[k].uv]
        mx = math.floor(min(p[0] for p in pts) + 1e-6)
        my = math.floor(min(p[1] for p in pts) + 1e-6)
        w = max(1, math.ceil(max(p[0] for p in pts) - mx - 1e-6))
        h = max(1, math.ceil(max(p[1] for p in pts) - my - 1e-6))
        boxes.append((mx, my))
        sizes.append((w, h))
    res = pack_rects(sizes, W, H, opt.padding, opt.allow_rotate)
    if res is None:
        return "Islands don't fit in %d x %d at their current size" % (W, H), None, masks
    moves = []
    for g, (mx, my), (w, h), (x, y, rot) in zip(groups, boxes, sizes, res):
        for k in g:
            loc = [(px - mx, py - my) for px, py in islands[k].uv]
            if rot:
                loc = _rot90(loc, h)
            islands[k].new = [(px + x, py + y) for px, py in loc]
        moves.append(PackMove(g, mx, my, w, h, x, y, rot))
    return None, moves, masks


def move_pixels(src, W, H, moves, masks, clear=(0.0, 0.0, 0.0, 0.0), radius=1):
    """src: H x W x 4 array (numpy, row 0 = bottom). Returns a new array where each island's
    pixels (plus a 1 px bleed ring that no other island claims) follow its PackMove.
    Pixels not under any island are cleared."""
    import numpy as np
    dst = np.empty_like(src)
    dst[:, :] = clear
    claimed = {}
    for k, m in enumerate(masks):
        for c in m:
            claimed.setdefault(c, set()).add(k)
    plan = []
    for mv in moves:
        members = set(mv.members)
        cells = set()
        for k in mv.members:
            cells |= masks[k]
        ring = set()
        r = max(0, int(radius))
        for i, j in cells:
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    c = (i + di, j + dj)
                    if c in cells or not (mv.mx - r <= c[0] < mv.mx + mv.w + r and
                                          mv.my - r <= c[1] < mv.my + mv.h + r):
                        continue
                    if claimed.get(c, members) <= members:
                        ring.add(c)
        plan.append((mv, cells, ring))
    # bleed rings first, then every island's own pixels on top (rings never cover islands)
    for mv, cells, ring in plan:
        for i, j in ring:
            if 0 <= i < W and 0 <= j < H:
                ni, nj = mv.cell(i, j)
                if 0 <= ni < W and 0 <= nj < H:
                    dst[nj, ni] = src[j, i]
    for mv, cells, ring in plan:
        for i, j in cells:
            if 0 <= i < W and 0 <= j < H:
                ni, nj = mv.cell(i, j)
                if 0 <= ni < W and 0 <= nj < H:
                    dst[nj, ni] = src[j, i]
    return dst


class BlockMove:
    """Pixel block [mx, mx+w) x [my, my+h) turned k times by 90 degrees counter-clockwise around
    its centre, then shifted by (dx, dy) whole pixels."""

    def __init__(self, mx, my, w, h, k=0, dx=0, dy=0):
        self.mx, self.my, self.w, self.h = mx, my, w, h
        self.k, self.dx, self.dy = k % 4, dx, dy
        w2, h2 = (h, w) if self.k % 2 else (w, h)
        self.ox = mx + (w - w2) // 2 + dx  # new block origin
        self.oy = my + (h - h2) // 2 + dy

    def uv(self, x, y):
        a, b, w, h = x - self.mx, y - self.my, self.w, self.h
        for _ in range(self.k):
            a, b, w, h = h - b, a, h, w
        return a + self.ox, b + self.oy

    def cell(self, i, j):
        a, b, w, h = i - self.mx, j - self.my, self.w, self.h
        for _ in range(self.k):
            a, b, w, h = h - 1 - b, a, h, w
        return a + self.ox, b + self.oy


def lifted_cells(moving, masks, box, radius=1):
    """Pixels that travel with the moving islands: their own pixels plus a ring of `radius` px
    (the padding / bleed area) that no other island claims. Returns (cells, others): `others`
    are pixels used by islands that stay, which are copied but never cleared."""
    mx, my, w, h = box
    cells = set()
    for k in moving:
        cells |= masks[k]
    others = set()
    for k, m in enumerate(masks):
        if k not in moving:
            others |= m
    r = max(0, int(radius))
    ring = set()
    for i, j in cells:
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                c = (i + di, j + dj)
                if (c not in cells and c not in others and mx - r <= c[0] < mx + w + r
                        and my - r <= c[1] < my + h + r):
                    ring.add(c)
    return cells | ring, others


def block_move_pixels(src, W, H, mv, cells, clear=(0.0, 0.0, 0.0, 0.0), keep=frozenset()):
    """Copy of src with `cells` lifted (cleared, except those in `keep`) and pasted through mv
    (anything with a .cell(i, j) method)."""
    dst = src.copy()
    for i, j in cells:
        if (i, j) not in keep and 0 <= i < W and 0 <= j < H:
            dst[j, i] = clear
    for i, j in cells:
        if 0 <= i < W and 0 <= j < H:
            ni, nj = mv.cell(i, j)
            if 0 <= ni < W and 0 <= nj < H:
                dst[nj, ni] = src[j, i]
    return dst


# ---- 90 degree rotations / mirrors around a pivot ---------------------------

ROTATIONS = {0: (1, 0, 0, 1), 1: (0, -1, 1, 0), 2: (-1, 0, 0, -1), 3: (0, 1, -1, 0)}
MIRROR_X = (-1, 0, 0, 1)   # flips U (mirror line is vertical)
MIRROR_Y = (1, 0, 0, -1)   # flips V


def snap_pivot(p, kind):
    """Nearest pivot (px) about which the transform maps whole pixels onto whole pixels:
    rotations need a pixel corner or a pixel centre, mirrors a corner / centre line."""
    x, y = p
    if kind == "MIRROR":
        return round(x * 2) / 2, round(y * 2) / 2
    if kind == "MIRROR_X":
        return round(x * 2) / 2, y
    if kind == "MIRROR_Y":
        return x, round(y * 2) / 2
    a = (round(x), round(y))
    b = (math.floor(x) + 0.5, math.floor(y) + 0.5)
    return a if (a[0] - x) ** 2 + (a[1] - y) ** 2 <= (b[0] - x) ** 2 + (b[1] - y) ** 2 else b


class PivotMove:
    """x' = A (x - P) + P for a signed-permutation matrix A (90 degree turns, mirrors)."""

    def __init__(self, A, P):
        self.A, self.P = A, P

    def uv(self, x, y):
        a, b, c, d = self.A
        dx, dy = x - self.P[0], y - self.P[1]
        return a * dx + b * dy + self.P[0], c * dx + d * dy + self.P[1]

    def cell(self, i, j):
        x, y = self.uv(i + 0.5, j + 0.5)
        return int(math.floor(x)), int(math.floor(y))

    def exact(self):
        """True when pixel centres land on pixel centres (otherwise the texture can't follow)."""
        x, y = self.uv(0.5, 0.5)
        return abs((x % 1) - 0.5) < 1e-6 and abs((y % 1) - 0.5) < 1e-6


def canvas_offset(W, H, nw, nh, anchor_x, anchor_y):
    """Pixel offset of the old image inside the new canvas (row 0 = bottom)."""
    ox = {"LEFT": 0, "CENTER": (nw - W) // 2, "RIGHT": nw - W}[anchor_x]
    oy = {"BOTTOM": 0, "CENTER": (nh - H) // 2, "TOP": nh - H}[anchor_y]
    return ox, oy


def place_canvas(src, nw, nh, ox, oy):
    """New nh x nw x 4 array with src placed at (ox, oy); negative offsets / smaller sizes crop."""
    import numpy as np
    H, W = src.shape[:2]
    dst = np.zeros((nh, nw, src.shape[2]), dtype=src.dtype)
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(nw, ox + W), min(nh, oy + H)
    if x1 > x0 and y1 > y0:
        dst[y0:y1, x0:x1] = src[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    return dst


# ============================================================================
# Auto seams
# ============================================================================
#
# Seams come from a small boosted-tree model trained on hand-unwrapped characters (it predicts
# per edge how likely the artist was to cut it), followed by geometry rules that make every
# island a flat-able disk: tubes are opened and closed pieces cut along the cheapest, most
# seam-like path, and every cut is mirrored across X so left / right islands can be stacked.
# Without the model (or with "Rules" chosen) crease angles per object type are used instead.

TWO_PI = 2.0 * math.pi



def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _normalize(a):
    l = _length(a)
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-12 else (0.0, 0.0, 0.0)


SEAM_TYPES = {
    # angle: feature edge angle; chain: min edges in a sharp chain to count as a border
    # curv: max total |Gaussian curvature| (radians) a chart may keep before being split
    "HARD": dict(angle=55.0, chain=1, curv=10.4, min_split_faces=6),
    "ORGANIC": dict(angle=60.0, chain=4, curv=14.4, min_split_faces=12),
    "TUBE": dict(angle=181.0, chain=1, curv=18.0, min_split_faces=12),   # no crease cuts
}


class SeamMesh:
    def __init__(self, verts, faces, groups=None, up=(0.0, 0.0, 1.0), front=(0.0, -1.0, 0.0),
                 mirror_axis=0, existing_seams=()):
        self.V = [tuple(v) for v in verts]
        self.F = [list(f) for f in faces]
        self.G = list(groups) if groups is not None else [None] * len(self.F)
        self.up = _normalize(up)
        self.front = _normalize(front)
        self.axis = mirror_axis
        self.edge_index = {}
        self.E = []            # (a, b)
        self.EF = []           # faces per edge
        self.FE = []           # edges per face
        for fi, f in enumerate(self.F):
            fe = []
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                key = (a, b) if a < b else (b, a)
                ei = self.edge_index.get(key)
                if ei is None:
                    ei = self.edge_index[key] = len(self.E)
                    self.E.append(key)
                    self.EF.append([])
                self.EF[ei].append(fi)
                fe.append(ei)
            self.FE.append(fe)
        self.VE = defaultdict(list)
        for ei, (a, b) in enumerate(self.E):
            self.VE[a].append(ei)
            self.VE[b].append(ei)
        self.fn, self.fa, self.fc = [], [], []
        for f in self.F:
            p = [self.V[v] for v in f]
            n = (0.0, 0.0, 0.0)
            for i in range(1, len(p) - 1):
                n = _add(n, _cross(_sub(p[i], p[0]), _sub(p[i + 1], p[0])))
            self.fa.append(0.5 * _length(n))
            self.fn.append(_normalize(n))
            self.fc.append(_mul(tuple(map(sum, zip(*p))), 1.0 / len(p)))
        self.length = [_length(_sub(self.V[a], self.V[b])) for a, b in self.E]
        self.angle = []
        for ei, fl in enumerate(self.EF):
            if len(fl) == 2:
                d = max(-1.0, min(1.0, _dot(self.fn[fl[0]], self.fn[fl[1]])))
                self.angle.append(math.degrees(math.acos(d)))
            else:
                self.angle.append(0.0)
        self.border = {ei for ei, fl in enumerate(self.EF) if len(fl) != 2}
        self.existing = {self.edge_index[(min(a, b), max(a, b))] for a, b in existing_seams
                         if (min(a, b), max(a, b)) in self.edge_index}
        self._mirror_maps()
        self._costs()

    # -- symmetry -------------------------------------------------------------
    def _mirror_maps(self):
        xs = [v[self.axis] for v in self.V]
        size = max(max(c) - min(c) for c in zip(*self.V)) if self.V else 1.0
        self.tol = size * 2e-3
        cell = self.tol * 2
        grid = defaultdict(list)
        for i, v in enumerate(self.V):
            grid[tuple(int(math.floor(c / cell)) for c in v)].append(i)
        self.vmir = {}
        for i, v in enumerate(self.V):
            m = list(v)
            m[self.axis] = -m[self.axis]
            key = tuple(int(math.floor(c / cell)) for c in m)
            best, bd = None, self.tol ** 2
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for j in grid.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                            d = sum((self.V[j][k] - m[k]) ** 2 for k in range(3))
                            if d <= bd:
                                best, bd = j, d
            if best is not None:
                self.vmir[i] = best
        self.emir = {}
        for ei, (a, b) in enumerate(self.E):
            ma, mb = self.vmir.get(a), self.vmir.get(b)
            if ma is None or mb is None:
                continue
            mk = (ma, mb) if ma < mb else (mb, ma)
            mj = self.edge_index.get(mk)
            if mj is not None:
                self.emir[ei] = mj
        self.on_center = {i for i, v in enumerate(self.V) if abs(v[self.axis]) <= self.tol}

    # -- cut path costs ---------------------------------------------------------
    def hidden(self, ei):
        """0 (fully visible: front / top) .. 1 (hidden: underneath, inner side)."""
        n = (0.0, 0.0, 0.0)
        for f in self.EF[ei]:
            n = _add(n, self.fn[f])
        n = _normalize(n)
        a, b = self.E[ei]
        cx = (self.V[a][self.axis] + self.V[b][self.axis]) / 2
        inward = 0.0
        if abs(cx) > self.tol * 5:
            inward = max(0.0, -n[self.axis] * (1 if cx > 0 else -1))
        down = max(0.0, -_dot(n, self.up))
        back = max(0.0, -_dot(n, self.front))
        frontness = max(0.0, _dot(n, self.front))
        return max(0.0, min(1.0, 0.55 * down + 0.3 * back + 0.35 * inward + 0.15 - 0.3 * frontness))

    def _costs(self):
        self.cost = []
        for ei in range(len(self.E)):
            c = self.length[ei] * (0.25 + 2.5 * (1.0 - self.hidden(ei)))
            if self.angle[ei] >= 30:
                c *= 0.45
            a, b = self.E[ei]
            if a in self.on_center and b in self.on_center:
                c *= 0.5
            if ei in self.existing:
                c *= 0.2
            self.cost.append(c)

    # -- charts -----------------------------------------------------------------
    def charts(self, cuts):
        parent = list(range(len(self.F)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for ei, fl in enumerate(self.EF):
            if len(fl) == 2 and ei not in cuts:
                ra, rb = find(fl[0]), find(fl[1])
                if ra != rb:
                    parent[ra] = rb
        groups = defaultdict(list)
        for fi in range(len(self.F)):
            groups[find(fi)].append(fi)
        return list(groups.values())

    def topology(self, chart, cuts):
        """(boundary loops as lists of edge ids, genus, interior edge set, boundary vertex set)."""
        fs = set(chart)
        edges = set()
        for f in chart:
            edges.update(self.FE[f])
        bnd = {e for e in edges if e in cuts or e in self.border or
               any(g not in fs for g in self.EF[e]) or len(self.EF[e]) != 2}
        interior = edges - bnd
        verts = set()
        for f in chart:
            verts.update(self.F[f])
        chi = len(verts) - len(edges) + len(chart)
        # boundary components: edges connected through shared vertices
        vb = defaultdict(list)
        for e in bnd:
            a, b = self.E[e]
            vb[a].append(e)
            vb[b].append(e)
        seen = set()
        loops = []
        for e0 in bnd:
            if e0 in seen:
                continue
            comp, stack = [], [e0]
            seen.add(e0)
            while stack:
                e = stack.pop()
                comp.append(e)
                for v in self.E[e]:
                    for e2 in vb[v]:
                        if e2 not in seen:
                            seen.add(e2)
                            stack.append(e2)
            loops.append(comp)
        b = len(loops)
        genus = (2 - b - chi) // 2
        bverts = set(vb)
        return loops, genus, interior, bverts

    def curvature(self, chart, cuts):
        """Total |angle deficit| at the chart's interior vertices (radians)."""
        _, _, interior, bverts = self.topology(chart, cuts)
        ang = defaultdict(float)
        for f in chart:
            vs = self.F[f]
            n = len(vs)
            for k in range(n):
                p = self.V[vs[k]]
                a = _sub(self.V[vs[k - 1]], p)
                b = _sub(self.V[vs[(k + 1) % n]], p)
                la, lb = _length(a), _length(b)
                if la < 1e-12 or lb < 1e-12:
                    continue
                ang[vs[k]] += math.acos(max(-1.0, min(1.0, _dot(a, b) / (la * lb))))
        return sum(abs(TWO_PI - s) for v, s in ang.items() if v not in bverts)

    # -- paths ----------------------------------------------------------------
    def path(self, sources, targets, allowed_edges):
        """Cheapest edge path from any source vertex to any target vertex using allowed edges."""
        dist = {}
        prev = {}
        heap = []
        for s in sources:
            dist[s] = 0.0
            heap.append((0.0, s))
        heapq.heapify(heap)
        targets = set(targets)
        while heap:
            d, v = heapq.heappop(heap)
            if d > dist.get(v, float("inf")):
                continue
            if v in targets and v not in sources:
                out = []
                while v in prev:
                    e, v = prev[v]
                    out.append(e)
                return out
            for e in self.VE[v]:
                if e not in allowed_edges:
                    continue
                a, b = self.E[e]
                w = b if a == v else a
                nd = d + self.cost[e]
                if nd < dist.get(w, float("inf")):
                    dist[w] = nd
                    prev[w] = (e, v)
                    heapq.heappush(heap, (nd, w))
        return None

    def add_mirrored(self, cuts, edges):
        for e in edges:
            cuts.add(e)
            m = self.emir.get(e)
            if m is not None:
                cuts.add(m)

    # -- steps ----------------------------------------------------------------
    def feature_cuts(self, faces_by_type):
        cuts = set(self.existing)
        for typ, faces in faces_by_type.items():
            prm = SEAM_TYPES[typ]
            fs = set(faces)
            sharp = {e for f in faces for e in self.FE[f]
                     if self.angle[e] >= prm["angle"] and all(g in fs for g in self.EF[e])}
            # keep chains of at least `chain` edges
            vb = defaultdict(list)
            for e in sharp:
                for v in self.E[e]:
                    vb[v].append(e)
            seen = set()
            for e0 in sharp:
                if e0 in seen:
                    continue
                comp, stack = [], [e0]
                seen.add(e0)
                while stack:
                    e = stack.pop()
                    comp.append(e)
                    for v in self.E[e]:
                        for e2 in vb[v]:
                            if e2 not in seen:
                                seen.add(e2)
                                stack.append(e2)
                if len(comp) >= prm["chain"]:
                    self.add_mirrored(cuts, comp)
        return cuts

    def split_plane(self, chart, cuts):
        """Cut the chart across its main axis at the narrowest, cheapest cross-section."""
        pts = [self.fc[f] for f in chart]
        c = _mul(tuple(map(sum, zip(*pts))), 1.0 / len(pts))
        # main axis by power iteration on the covariance
        cov = [[0.0] * 3 for _ in range(3)]
        for p in pts:
            d = _sub(p, c)
            for i in range(3):
                for j in range(3):
                    cov[i][j] += d[i] * d[j]
        ax = (1.0, 0.7, 0.3)
        for _ in range(30):
            ax = _normalize(tuple(sum(cov[i][j] * ax[j] for j in range(3)) for i in range(3)))
        proj = {f: _dot(_sub(self.fc[f], c), ax) for f in chart}
        lo, hi = min(proj.values()), max(proj.values())
        if hi - lo < 1e-9:
            return None
        fs = set(chart)
        best = None
        for step in range(1, 20):
            t = lo + (hi - lo) * step / 20.0
            side = {f: proj[f] > t for f in chart}
            n_pos = sum(side.values())
            if n_pos < 2 or len(chart) - n_pos < 2:
                continue
            cut = [e for f in chart for e in self.FE[f]
                   if len(self.EF[e]) == 2 and e not in cuts and all(g in fs for g in self.EF[e])
                   and side[self.EF[e][0]] != side[self.EF[e][1]]]
            cut = list(set(cut))
            if not cut:
                continue
            balance = min(n_pos, len(chart) - n_pos) / len(chart)
            score = sum(self.cost[e] for e in cut) / (0.2 + balance)
            if best is None or score < best[0]:
                best = (score, cut)
        return best[1] if best else None

    def diskify(self, chart, cuts):
        """Adds cuts until the chart is a disk. Returns True if something was cut."""
        loops, genus, interior, bverts = self.topology(chart, cuts)
        if len(loops) == 1 and genus == 0:
            return False
        if genus > 0 or len(loops) == 0:
            if len(loops) == 0 and genus == 0:
                # closed sphere: cut from the most hidden vertex to a far hidden vertex
                verts = {v for f in chart for v in self.F[f]}
                vh = {}
                for v in verts:
                    es = [e for e in self.VE[v] if e in interior]
                    vh[v] = sum(self.hidden(e) for e in es) / len(es) if es else 0
                a = max(verts, key=lambda v: vh[v])
                far = sorted(verts, key=lambda v: -_length(_sub(self.V[v], self.V[a])))
                cand = far[:max(3, len(far) // 4)]
                b = max(cand, key=lambda v: vh[v])
                p = self.path({a}, {b}, interior)
                if p:
                    self.add_mirrored(cuts, p)
                    return True
            p = self.split_plane(chart, cuts)
            if p:
                self.add_mirrored(cuts, p)
                return True
            return False
        # several boundary loops: connect loop 0 to the nearest other loop
        lv = []
        for loop in loops:
            vs = set()
            for e in loop:
                vs.update(self.E[e])
            lv.append(vs)
        best = None
        for i in range(1, len(lv)):
            p = self.path(lv[0], lv[i] - lv[0], interior)
            if p is not None:
                c = sum(self.cost[e] for e in p)
                if best is None or c < best[0]:
                    best = (c, p)
        if best is None:
            p = self.split_plane(chart, cuts)
            if p:
                self.add_mirrored(cuts, p)
                return True
            return False
        self.add_mirrored(cuts, best[1])
        return True

    def use_probabilities(self, prob, threshold=0.4, min_chain=2):
        """Learned per-edge seam probabilities drive the cuts and the path costs."""
        self.prob = prob
        self.threshold = threshold
        self.min_chain = min_chain
        for ei in range(len(self.E)):
            c = self.length[ei] * (0.04 + 3.0 * (1.0 - prob[ei]) ** 2)
            if ei in self.existing:
                c *= 0.2
            self.cost[ei] = c

    def prob_cuts(self):
        cand = {e for e in range(len(self.E)) if self.prob[e] >= self.threshold and len(self.EF[e]) == 2}
        cand |= self.existing
        vb = defaultdict(list)
        for e in cand:
            for v in self.E[e]:
                vb[v].append(e)
        cuts, seen = set(self.existing), set()
        for e0 in cand:
            if e0 in seen:
                continue
            comp, stack = [], [e0]
            seen.add(e0)
            while stack:
                e = stack.pop()
                comp.append(e)
                for v in self.E[e]:
                    for e2 in vb[v]:
                        if e2 not in seen:
                            seen.add(e2)
                            stack.append(e2)
            if len(comp) >= self.min_chain:
                self.add_mirrored(cuts, comp)
        return cuts

    def run(self, types, max_iter=400):
        """types: group -> 'HARD' / 'ORGANIC' / 'TUBE'. Returns the set of cut edge ids."""
        faces_by_type = defaultdict(list)
        for f, g in enumerate(self.G):
            faces_by_type[types.get(g, "ORGANIC")].append(f)
        ftype = {f: types.get(g, "ORGANIC") for f, g in enumerate(self.G)}
        cuts = self.prob_cuts() if getattr(self, "prob", None) is not None else self.feature_cuts(faces_by_type)
        for _ in range(max_iter):
            changed = False
            for chart in self.charts(cuts):
                prm = SEAM_TYPES[ftype[chart[0]]]
                if len(chart) >= prm["min_split_faces"] and self.curvature(chart, cuts) > prm["curv"]:
                    p = self.split_plane(chart, cuts)
                    if p:
                        self.add_mirrored(cuts, p)
                        changed = True
                        break
            if not changed:
                break
        for _ in range(max_iter):
            changed = False
            for chart in self.charts(cuts):
                if self.diskify(chart, cuts):
                    changed = True
                    break
            if not changed:
                break
        self.merge_small(cuts, tex_px=getattr(self, "tex_px", 256 * 256))
        return cuts

    def merge_small(self, cuts, min_px=4.0, tex_px=256 * 256, fill=0.65, max_iter=300):
        """Islands that would end up smaller than min_px pixels (estimated at the density that
        fills `fill` of a texture of tex_px pixels) are merged into the neighbour they share the
        most seam with, if the result is still a disk (mirrored twins follow)."""
        total = sum(self.fa) or 1.0
        px_per_area = fill * tex_px / total
        min_area_abs = min_px / px_per_area
        garea = defaultdict(lambda: 1.0)
        for _ in range(max_iter):
            charts = self.charts(cuts)
            owner = {}
            for ci, ch in enumerate(charts):
                for f in ch:
                    owner[f] = ci
            merged = False
            order = sorted(range(len(charts)), key=lambda ci: sum(self.fa[f] for f in charts[ci]))
            for ci in order:
                ch = charts[ci]
                area = sum(self.fa[f] for f in ch)
                if area >= min_area_abs:
                    continue
                shared = defaultdict(list)
                for f in ch:
                    for e in self.FE[f]:
                        if e in cuts and len(self.EF[e]) == 2:
                            o = owner[self.EF[e][0]] if owner[self.EF[e][0]] != ci else owner[self.EF[e][1]]
                            if o != ci:
                                shared[o].append(e)
                for o, es in sorted(shared.items(), key=lambda kv: -len(kv[1])):
                    trial = set(cuts)
                    for e in es:
                        trial.discard(e)
                        trial.discard(self.emir.get(e, -1))
                    merged_chart = ch + charts[o]
                    loops, genus, _, _ = self.topology(merged_chart, trial)
                    if len(loops) != 1 or genus != 0:
                        continue
                    ok = True
                    for f in (ch[0],):   # the mirrored twin must stay a disk too
                        mf = None
                    for c2 in self.charts(trial):
                        lp, gn, _, _ = self.topology(c2, trial)
                        if len(lp) != 1 or gn != 0:
                            ok = False
                            break
                    if ok:
                        cuts.clear()
                        cuts.update(trial)
                        merged = True
                        break
                if merged:
                    break
            if not merged:
                return


def classify_group(mesh, faces):
    """Guess the seam type of an object from its shape: many sharp edges -> HARD, long and thin
    -> TUBE, otherwise ORGANIC."""
    es = {e for f in faces for e in mesh.FE[f] if len(mesh.EF[e]) == 2}
    if not es:
        return "ORGANIC"
    sharp = sum(1 for e in es if mesh.angle[e] >= 45) / len(es)
    quads = sum(1 for f in faces if len(mesh.F[f]) == 4) / len(faces)
    if quads >= 0.95 and len(faces) >= 24:
        return "TUBE"      # a clean quad tube (rope, cable, strap loop)
    if sharp >= 0.35:
        return "HARD"
    return "ORGANIC"


# ---- learned edge model ----------------------------------------------------------

FEATURE_NAMES = [
    "angle", "sharp30", "sharp45", "sharp60", "sharp90", "concave",
    "down", "up", "back", "front", "inward", "outward",
    "height", "side", "center", "len_rel", "quads_adj", "tris_adj", "valence",
    "chain45", "chain60", "nb_sharp", "loop_straight",
    "t_hard", "t_organic", "t_tube",
]


def edge_features(mesh, types):
    """One feature row per edge (pure Python). types: group -> seam type."""
    V, E = mesh.V, mesh.E
    allv = V
    lo = [min(p[k] for p in allv) for k in range(3)]
    hi = [max(p[k] for p in allv) for k in range(3)]
    up = mesh.up
    hmin = min(_dot(p, up) for p in allv)
    hmax = max(_dot(p, up) for p in allv)
    xmax = max(abs(p[mesh.axis]) for p in allv) or 1.0
    lens = sorted(mesh.length)
    lmed = lens[len(lens) // 2] or 1.0
    # sharp chain lengths
    chains = {}
    for th in (45, 60):
        sharp = {e for e in range(len(E)) if mesh.angle[e] >= th and len(mesh.EF[e]) == 2}
        vb = defaultdict(list)
        for e in sharp:
            for v in E[e]:
                vb[v].append(e)
        size = {}
        seen = set()
        for e0 in sharp:
            if e0 in seen:
                continue
            comp, stack = [], [e0]
            seen.add(e0)
            while stack:
                e = stack.pop()
                comp.append(e)
                for v in E[e]:
                    for e2 in vb[v]:
                        if e2 not in seen:
                            seen.add(e2)
                            stack.append(e2)
            for e in comp:
                size[e] = len(comp)
        chains[th] = size
    rows = []
    for ei, (a, b) in enumerate(E):
        fl = mesh.EF[ei]
        ang = mesh.angle[ei]
        n = (0.0, 0.0, 0.0)
        for f in fl:
            n = _add(n, mesh.fn[f])
        n = _normalize(n)
        concave = 0.0
        if len(fl) == 2:
            d = _sub(mesh.fc[fl[1]], mesh.fc[fl[0]])
            concave = 1.0 if _dot(mesh.fn[fl[0]], d) > 1e-9 else 0.0
        mid = _mul(_add(V[a], V[b]), 0.5)
        cx = mid[mesh.axis]
        side_sign = 1 if cx > 0 else -1
        inward = max(0.0, -n[mesh.axis] * side_sign) if abs(cx) > mesh.tol * 5 else 0.0
        outward = max(0.0, n[mesh.axis] * side_sign) if abs(cx) > mesh.tol * 5 else 0.0
        g = mesh.G[fl[0]] if fl else None
        t = types.get(g, "ORGANIC")
        # does the edge continue straight into a neighbour edge (part of an edge loop)?
        dvec = _normalize(_sub(V[b], V[a]))
        straight = 0.0
        for v, sgn in ((a, -1), (b, 1)):
            for e2 in mesh.VE[v]:
                if e2 == ei:
                    continue
                p, q = E[e2]
                w = q if p == v else p
                d2 = _normalize(_sub(V[w], V[v]))
                straight = max(straight, _dot(d2, dvec) * sgn)
        nb = [e2 for v in (a, b) for e2 in mesh.VE[v] if e2 != ei]
        rows.append([
            ang / 180.0, float(ang >= 30), float(ang >= 45), float(ang >= 60), float(ang >= 90), concave,
            max(0.0, -_dot(n, up)), max(0.0, _dot(n, up)),
            max(0.0, -_dot(n, mesh.front)), max(0.0, _dot(n, mesh.front)), inward, outward,
            (_dot(mid, up) - hmin) / ((hmax - hmin) or 1.0), abs(cx) / xmax,
            float(a in mesh.on_center and b in mesh.on_center),
            mesh.length[ei] / lmed,
            float(sum(1 for f in fl if len(mesh.F[f]) == 4)), float(sum(1 for f in fl if len(mesh.F[f]) == 3)),
            (len(mesh.VE[a]) + len(mesh.VE[b])) / 2.0,
            min(chains[45].get(ei, 0), 40) / 40.0, min(chains[60].get(ei, 0), 40) / 40.0,
            sum(1 for e2 in nb if mesh.angle[e2] >= 45) / max(1, len(nb)),
            straight,
            float(t == "HARD"), float(t == "ORGANIC"), float(t == "TUBE"),
        ])
    return rows


# Trained on 3 characters (Commoner, Monk, Guard with helmet / sword / sheath / strap / rope);
# leave-one-character-out edge F1 about 0.64 against the artist's own seams.
SEAM_MODEL_BLOB = (
    "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"
    "nmrSpDCD2TFbIpsOV2CDTFW0d1xECmWroFihvMuyt4LRLt0g7lApZlVk5dXuKkIZy2qiEudd+gkVsXZs4cPYFQqOlHnpBoeT"
    "YwSqu0CWKF2C2q9M5uS230YKuVXcT+XsPqS78mm12sPm4wu7rCyHvTjbBisb+LvKlWjvBVBqo0YT+OgdznvzaoBmy0kfRoei"
    "u9/TiFGbos+MK04BlJxkSWwgYEslG4KZjOLACJ+y1hUNwcxXKmteNPCjl1LjjwO/TCfVMrFj4GJcOkH5ZaK9cRQbKJvSVNZU"
    "YkNn2rWT1VCZDw14ldPo8Pimhx/wlFZBl1kqgcYzWFBXIm8AmYJ8g0crWope6IDqD/Jh8TrNIrzzySPGL/9BjO+OMX44FNfx"
    "ZDRERC+1c/kQ4x+0xVVph9/ErDFt3TUiVYLdIb674i+UQpIJZC6hTMKpjLxbqiuztQVeAfUUW8PLlCKidVg6MBkBYLs0Dm+9"
    "pmWzH41Kh9xIB0+Jjpx4NynaHXhshReJslX1tLt9C1Y1WjvT8P2Uy6URQHX7VZTyldb5BDArHGbuCkSJMx8RBBsxzitoHMtT"
    "bs3pULcpG2ZB8uEOKKufU0xYas1lpgC1DJbDlEzFt61VUplOLrehi8GaX3BLthgn0oLUdrX5muM2qdmh2VFJymVgsQXyusxl"
    "rVUMSeqafaiqBvfhAhy5wT/iTqAozvT/xUqSMag3WFYScIxD1TZlT3OirMNFeOaD1CcdtEDkIPUadUzTcU6SG2wZP7EI09pY"
    "K80FNFsNjHmjitK7TwwBXrXjAzuaynuUyqGspYJXswrUy+55CBmOW9Es+KjwRWzGdofRLdRDj2wwb6zFAW8CcCs8n5MBUc0f"
    "KjfXJpw33RITERqQ5VDkHKmmKgcYTsyooc2tyGIGzkZF61mPuA6UOFBH3BDcyIOH2ZEU04LNO62BhR1IizB6OR/4q+HEX00p"
    "HfirKPrzvmOlu9NJBojCV0xw56+iXCFC2fi1HPiAwKAWGU/fpYY9plAi1dSBPx1CJYbzuqcUSjiIlXPFAoh5a+fmoV7h383q"
    "wzGp96ekngpx4KV95Dm9Tg5kV1yHZzaud5LyQV7caf9o5rJ1yGsOM9xFpaSoSgDipRKOHFTsLB1t2NR38H7UqqkpVxAr984M"
    "1dhyRreqXqMZ8IVNh+ya0pHnMa8eSerDVLAp7CEQdReOFMeh74QFConxd7KCHkf9ZTiXdJYRU5/Es2BEV5S+BkMTV6sASgrZ"
    "WBpE3p7H5IgbWqNS9cDVdKjEP719+OfPn77/+e3zHx9++suHT1/+9vfPH94+/PHbp2//CO76U0zXn/L6u6Z/+uXrl18+/Zv+"
    "i1+//vsX/Mef/8D/99dPv/wL/uOfv3398h3/+fuXf//07Vf84euf3+effvv8+99+08/++P1X/de/fP7y/fM3/OHvn7/8/O3z"
    "3/Gn//3np1//+PnTr/8Lf/7+7ffrj//2Cf+TX/q/+e3T71/6k/U/9Sf78tef+8PpN339+o+f//j+7dP8pe8//zZ++/vPX7/9"
    "7dOX33/pf/7+518/f8AqfP8NS/Db17//+uEn3XP9TXzp519//vrlw08fwg/4jW+ffsFD/vHDf/nvX//1X79++fzt7Yf/+fXL"
    "v7z98D/+xDf/8N9++Me3r//4479++L//D9HI/G8="
)
_SEAM_MODEL = None


def seam_model():
    global _SEAM_MODEL
    if _SEAM_MODEL is None:
        import base64
        import json
        import zlib
        _SEAM_MODEL = json.loads(zlib.decompress(base64.b64decode(SEAM_MODEL_BLOB)))
    return _SEAM_MODEL


def predict_seams(rows, model=None):
    """Seam probability per feature row (pure Python boosted trees)."""
    model = model or seam_model()
    base, lr, trees = model["base"], model["lr"], model["trees"]
    out = []
    for r in rows:
        s = base
        for nodes in trees:
            i = 0
            node = nodes[0]
            while node[2] != -1:
                i = node[2] if r[node[0]] <= node[1] else node[3]
                node = nodes[i]
            s += lr * node[4]
        out.append(1.0 / (1.0 + math.exp(-s)))
    return out


def auto_seams(verts, faces, groups, types=None, up=(0.0, 0.0, 1.0), front=(0.0, -1.0, 0.0),
               method="LEARNED", existing=(), threshold=None, tex_px=256 * 256):
    """Returns (cut edges as vertex pairs, chart count, types used). types: group -> type or 'AUTO'.
    tex_px: texture pixel count, used to merge islands that would end up under ~4 px."""
    mesh = SeamMesh(verts, faces, groups, up=up, front=front, existing_seams=existing)
    mesh.tex_px = tex_px
    by_group = defaultdict(list)
    for fi, g in enumerate(mesh.G):
        by_group[g].append(fi)
    used = {}
    for g, fs in by_group.items():
        t = (types or {}).get(g, "AUTO")
        used[g] = classify_group(mesh, fs) if t in (None, "AUTO") else t
    if method == "LEARNED":
        model = seam_model()
        prob = predict_seams(edge_features(mesh, used), model)
        mesh.use_probabilities(prob, threshold if threshold is not None else model["threshold"], 1)
    cuts = mesh.run(used)
    pairs = [mesh.E[e] for e in cuts if e not in mesh.border]
    return pairs, len(mesh.charts(cuts)), used


# ============================================================================
# Blender side
# ============================================================================

if bpy is not None:

    # One BMesh wrapper per edit mesh, kept alive. When any wrapper of an edit mesh is freed,
    # Blender invalidates every BMFace / BMLoop reference of that mesh, including ones taken
    # through another wrapper ("BMesh data of type BMFace has been removed"). Fetching meshes
    # only through here means no wrapper ever dies mid-operator. A wrapper is replaced once
    # Blender rebuilds the edit mesh (undo, leaving Edit Mode), which makes it invalid.
    _BM_CACHE = {}

    def edit_bm(me):
        key = me.as_pointer()
        bm = _BM_CACHE.get(key)
        if bm is None or not bm.is_valid:
            bm = bmesh.from_edit_mesh(me)
            _BM_CACHE[key] = bm
            for k in [k for k, v in _BM_CACHE.items() if not v.is_valid]:
                del _BM_CACHE[k]
        return bm

    def face_layers(bm):
        """The per-face density / multiplier attributes, created if missing. Call it BEFORE taking
        any BMFace references: adding a face attribute rebuilds the face data and invalidates
        every BMFace reference taken earlier ("BMesh data of type BMFace has been removed")."""
        fl = bm.faces.layers.float
        dl = fl.get(DENSITY_ATTR)
        if dl is None:
            fl.new(DENSITY_ATTR)
        sl = fl.get(SCALE_ATTR)
        if sl is None:
            fl.new(SCALE_ATTR)
        return fl.get(DENSITY_ATTR), fl.get(SCALE_ATTR)

    def find_image(context):
        sp = context.space_data
        if sp and sp.type == 'IMAGE_EDITOR' and sp.image and sp.image.size[0] > 0:
            return sp.image
        if context.screen:
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    img = area.spaces.active.image
                    if img and img.size[0] > 0:
                        return img
        obj = context.active_object
        mat = obj.active_material if obj else None
        if mat and mat.use_nodes and mat.node_tree:
            nodes = mat.node_tree.nodes
            n = nodes.active
            if n and n.type == 'TEX_IMAGE' and n.image and n.image.size[0] > 0:
                return n.image
            for n in nodes:
                if n.type == 'TEX_IMAGE' and n.image and n.image.size[0] > 0:
                    return n.image
        return None

    def texture_size(context, s):
        if s.tex_source == 'AUTO':
            img = find_image(context)
            if img:
                return int(img.size[0]), int(img.size[1]), img.name
        return s.tex_width, s.tex_height, None

    def edit_objects(context):
        return [o for o in context.objects_in_mode_unique_data if o.type == 'MESH']

    # BMesh wrappers must stay alive while we hold BMLoop / BMFace references: when the Python
    # BMesh object is freed, Blender invalidates every element taken from it.
    _KEEP_BMESH = []

    def collect(context, W, H, keep_rot=frozenset(), all_faces=False):
        """FaceRecs of the selected faces (all visible faces when nothing is selected, every
        face including hidden ones with all_faces)."""
        objs = edit_objects(context)
        bms = []
        _KEEP_BMESH.clear()
        any_sel = False
        for o in objs:
            bm = edit_bm(o.data)
            bms.append((o, bm))
            _KEEP_BMESH.append(bm)
            if not all_faces and not any_sel and any(f.select and not f.hide for f in bm.faces):
                any_sel = True
        recs = []
        for o, bm in bms:
            uvl = bm.loops.layers.uv.verify()
            dl = bm.faces.layers.float.get(DENSITY_ATTR)
            sl = bm.faces.layers.float.get(SCALE_ATTR)
            mw = o.matrix_world
            for f in bm.faces:
                if not all_faces and (f.hide or (any_sel and not f.select)):
                    continue
                loops = list(f.loops)
                p3 = [tuple(mw @ l.vert.co) for l in loops]
                uv = [(l[uvl].uv.x * W, l[uvl].uv.y * H) for l in loops]
                recs.append(FaceRec(
                    [l.vert for l in loops], uv, p3,
                    f[dl] if dl else 0.0,
                    (f[sl] or 1.0) if sl else 1.0,
                    (uvl, loops, o),
                    (o.name, f.index) in keep_rot,
                    [tuple(l.vert.co) for l in loops],
                    o.name))
        return objs, recs

    def write_back(objs, recs, islands, W, H):
        for isl in islands:
            if isl.new is None:
                continue
            for face, fid in zip(isl.faces, isl.face_ids):
                uvl, loops, _ = recs[fid].ref
                for l, idx in zip(loops, face):
                    x, y = isl.new[idx]
                    l[uvl].uv = (x / W, y / H)
        for o in objs:
            bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)

    def straighten_mixed(context, W, H, opt):
        """Islands mixing quads and triangles / n-gons: the main quad group is laid out as a
        rectangle grid and pinned, then Blender's unwrap fits the other faces around it.
        Islands whose result is too distorted are put back as they were.
        Returns {(object name, face index)} of the faces handled this way."""
        objs, recs = collect(context, W, H)
        todo = []
        for isl in find_islands(recs):
            g = mixed_grid(isl, opt.max_distortion)
            if g:
                todo.append((isl, g))
        done = set()
        if not todo:
            return done
        bms = [(o, edit_bm(o.data)) for o in objs]
        saved_sel = [([v.select for v in bm.verts], [e.select for e in bm.edges],
                      [f.select for f in bm.faces]) for _, bm in bms]
        for isl, g in todo:
            corners = []
            for face, fid in zip(isl.faces, isl.face_ids):
                uvl, loops, o = recs[fid].ref
                for l, idx in zip(loops, face):
                    corners.append((uvl, l, idx, o))
            saved = [(l[uvl].uv.copy(), l[uvl].pin_uv) for uvl, l, _, _ in corners]
            for uvl, l, idx, _ in corners:
                if idx in g:
                    x, y = g[idx]
                    l[uvl].uv = (x / W, y / H)
                    l[uvl].pin_uv = True
                else:
                    l[uvl].pin_uv = False
            for _, bm in bms:
                for elems in (bm.verts, bm.edges, bm.faces):
                    for e in elems:
                        e.select = False
            for _, l, _, _ in corners:
                l.face.select_set(True)
            for o, _ in bms:
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            try:
                bpy.ops.uv.unwrap(method='ANGLE_BASED', fill_holes=True, correct_aspect=True, margin=0.0)
                ok = True
            except RuntimeError:
                ok = False
            if ok:
                coords = [None] * len(isl.uv)
                for uvl, l, idx, _ in corners:
                    coords[idx] = (l[uvl].uv.x * W, l[uvl].uv.y * H)
                ok = distortion(isl, coords) <= max(opt.max_distortion * 1.5, 0.05)
            for (uv, pin), (uvl, l, _, o) in zip(saved, corners):
                if not ok:
                    l[uvl].uv = uv
                l[uvl].pin_uv = pin
            if ok:
                done.update((o.name, l.face.index) for _, l, _, o in corners)
        for (o, bm), (vs, es, fs) in zip(bms, saved_sel):
            for elems, st in ((bm.verts, vs), (bm.edges, es), (bm.faces, fs)):
                for e, v in zip(elems, st):
                    e.select = v
            bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
        return done

    BACKUP_SUFFIX = ".pixuv_backup"

    def read_pixels(img):
        import numpy as np
        w, h = img.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(h, w, 4)

    def write_pixels(img, arr):
        h, w = arr.shape[:2]
        if tuple(img.size) != (w, h):
            img.scale(w, h)
        img.pixels.foreach_set(arr.astype("float32").ravel())
        img.update()

    def backup_image(img, arr):
        """Keeps a copy of the pixels before they are moved (Ctrl+Z only undoes the UVs)."""
        name = img.name + BACKUP_SUFFIX
        old = bpy.data.images.get(name)
        if old:
            bpy.data.images.remove(old)
        h, w = arr.shape[:2]
        b = bpy.data.images.new(name, w, h, alpha=True, float_buffer=img.is_float)
        b.pixels.foreach_set(arr.astype("float32").ravel())
        b.use_fake_user = True
        try:
            b.pack()
        except RuntimeError:
            pass
        return b

    # ---- texture undo -------------------------------------------------------
    # Ctrl+Z only restores mesh data in Edit Mode, never image pixels. Every texture change
    # stamps a random revision into a hidden face attribute (part of the mesh undo) and keeps
    # the pixels before / after; the undo / redo handlers compare the stamps and swap the image.
    REV_ATTR = "pixuv_rev"
    HISTORY = []
    HISTORY_MAX = 24

    class TexUndo:
        __slots__ = ("img", "objs", "rev_before", "rev_after", "before", "after", "applied")

    def read_revs(names):
        out = []
        for name in names:
            o = bpy.data.objects.get(name)
            v = 0
            if o is not None and o.type == 'MESH':
                me = o.data
                if me.is_editmode:
                    bm = edit_bm(me)
                    lay = bm.faces.layers.int.get(REV_ATTR)
                    if lay is not None and len(bm.faces):
                        bm.faces.ensure_lookup_table()
                        v = bm.faces[0][lay]
                else:
                    at = me.attributes.get(REV_ATTR)
                    if at is not None and len(at.data):
                        v = at.data[0].value
            out.append(v)
        return tuple(out)

    def bump_revs(objs):
        import random
        for o in objs:
            bm = edit_bm(o.data)
            if not len(bm.faces):
                continue
            lay = bm.faces.layers.int.get(REV_ATTR)
            if lay is None:  # note: invalidates BMFace references taken before (none are used after)
                bm.faces.layers.int.new(REV_ATTR)
                lay = bm.faces.layers.int.get(REV_ATTR)
            bm.faces.ensure_lookup_table()
            bm.faces[0][lay] = random.randint(1, 2 ** 31 - 1)
            bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)

    def push_texture_undo(img, objs, rev_before, before, after):
        """Call at the end of an operator, after the UVs / pixels were changed."""
        bump_revs(objs)
        e = TexUndo()
        e.img = img.name
        e.objs = [o.name for o in objs]
        e.rev_before = rev_before
        e.rev_after = read_revs(e.objs)
        e.before = before
        e.after = after.copy()
        e.applied = True
        while HISTORY and not HISTORY[-1].applied:  # a new change drops the redo branch
            HISTORY.pop()
        HISTORY.append(e)
        del HISTORY[:-HISTORY_MAX]

    def _set_pixels(e, arr):
        img = bpy.data.images.get(e.img)
        if img is not None:
            write_pixels(img, arr)

    # persistent: Blender removes non-persistent handlers whenever a .blend is loaded (which
    # happens right after add-ons are enabled at startup), and texture undo would silently stop.
    @bpy.app.handlers.persistent
    def _on_undo(*_args):
        try:
            _undo_texture()
        except Exception as exc:  # never break Blender's own undo
            print("Pixel Perfect UV: texture undo failed:", exc)

    @bpy.app.handlers.persistent
    def _on_redo(*_args):
        try:
            _redo_texture()
        except Exception as exc:
            print("Pixel Perfect UV: texture redo failed:", exc)

    def _undo_texture():
        applied = [k for k, e in enumerate(HISTORY) if e.applied]
        for k in reversed(applied):
            if read_revs(HISTORY[k].objs) == HISTORY[k].rev_before:
                for j in reversed(applied):   # undo everything newer, down to k
                    if j < k:
                        break
                    _set_pixels(HISTORY[j], HISTORY[j].before)
                    HISTORY[j].applied = False
                return

    def _redo_texture():
        pending = [k for k, e in enumerate(HISTORY) if not e.applied]
        for k in reversed(pending):
            if read_revs(HISTORY[k].objs) == HISTORY[k].rev_after:
                for j in pending:            # redo everything older, up to k
                    if j > k:
                        break
                    _set_pixels(HISTORY[j], HISTORY[j].after)
                    HISTORY[j].applied = True
                return

    @bpy.app.handlers.persistent
    def _on_load(*_args):
        HISTORY.clear()
        _BM_CACHE.clear()

    def _handlers():
        h = bpy.app.handlers
        return ((h.undo_post, _on_undo), (h.redo_post, _on_redo), (h.load_post, _on_load))

    # ---- texture session (used by Move Islands and other add-ons) -----------

    def loop_selected(context, f, l, uvl):
        if f.hide:
            return False
        ts = context.tool_settings
        if ts.use_uv_select_sync:
            msm = ts.mesh_select_mode
            return f.select if (msm[2] and not msm[0]) else l.vert.select
        return f.select and l[uvl].select

    class TextureSession:
        """Carries the painted pixels of the selected UV islands through 90 degree rotations /
        mirrors around a pivot. Other add-ons (UV Vertex Pivot Transform) get one with
        PIXEL_UV_TEXTURE_API.start(context): call update(), then finish() or cancel()."""

        @classmethod
        def start(cls, context):
            s = context.scene.pixel_uv
            if not s.preserve_texture:
                return None
            img = find_image(context)
            if img is None:
                return None
            self = cls()
            self.img = img
            W, H = int(img.size[0]), int(img.size[1])
            self.W, self.H = W, H
            self.objs, recs = collect(context, W, H, all_faces=True)
            islands = find_islands(recs)
            moving = set()
            for k, isl in enumerate(islands):
                for fid in isl.face_ids:
                    uvl, loops, _ = recs[fid].ref
                    if any(loop_selected(context, loops[0].face, l, uvl) for l in loops):
                        moving.add(k)
                        break
            if not moving:
                return None
            masks = [pixel_mask(isl, isl.uv, W, H) for isl in islands]
            pts = [p for k in moving for p in islands[k].uv]
            mx, my = math.floor(min(p[0] for p in pts)), math.floor(min(p[1] for p in pts))
            box = (mx, my, math.ceil(max(p[0] for p in pts)) - mx, math.ceil(max(p[1] for p in pts)) - my)
            self.cells, self.others = lifted_cells(moving, masks, box, radius=max(1, s.padding))
            self.src = read_pixels(img)
            self.rev_before = read_revs([o.name for o in self.objs])
            self.state = None
            self.cur = None
            return self

        def snap_pivot(self, uv, kind):
            """kind: 'ROTATE', 'MIRROR', 'MIRROR_X' or 'MIRROR_Y'. Returns the pivot in UV space."""
            x, y = snap_pivot((uv[0] * self.W, uv[1] * self.H), kind)
            return x / self.W, y / self.H

        def update(self, A, pivot_uv):
            """A: 2x2 matrix as (a, b, c, d) in pixel space, or None for 'can't follow'."""
            key = (A, tuple(pivot_uv)) if A is not None else None
            if key == self.cur:
                return self.state is not None
            self.cur = key
            mv = PivotMove(A, (pivot_uv[0] * self.W, pivot_uv[1] * self.H)) if A is not None else None
            if mv is None or not mv.exact() or A == ROTATIONS[0]:
                write_pixels(self.img, self.src)
                self.state = None
                return mv is not None and A == ROTATIONS[0]
            self.dst = block_move_pixels(self.src, self.W, self.H, mv, self.cells, keep=self.others)
            write_pixels(self.img, self.dst)
            self.state = A
            return True

        def finish(self):
            if self.state is None:
                return False
            backup_image(self.img, self.src)
            push_texture_undo(self.img, self.objs, self.rev_before, self.src, self.dst)
            return True

        def cancel(self):
            write_pixels(self.img, self.src)
            self.state = None

    def image_users(img, exclude_meshes):
        """Other mesh objects (not being edited) whose materials use img."""
        names = []
        for o in bpy.data.objects:
            if o.type != 'MESH' or o.data in exclude_meshes:
                continue
            for slot in o.material_slots:
                m = slot.material
                if m and m.use_nodes and m.node_tree and any(
                        n.type == 'TEX_IMAGE' and n.image == img for n in m.node_tree.nodes):
                    names.append(o.name)
                    break
        return names

    def options_from(s):
        return Options(
            straighten=s.straighten, max_distortion=s.max_distortion / 100.0, align=s.align,
            density_mode=s.density_mode, density=s.density, snap=True, pack=s.pack,
            padding=s.padding, allow_rotate=s.allow_rotate, snap_mode=s.snap_mode,
            stack_mirrored=s.stack_mirrored, keep_density=s.keep_density,
            density_tolerance=s.density_tolerance / 100.0)

    class PixelUVSettings(bpy.types.PropertyGroup):
        tex_source: EnumProperty(
            name="Texture Size", items=[
                ('AUTO', "From Image", "Image shown in the UV Editor, or the material's image texture"),
                ('CUSTOM', "Custom", "Use the width / height below")],
            default='AUTO')
        tex_width: IntProperty(name="Width", default=64, min=1, max=16384)
        tex_height: IntProperty(name="Height", default=64, min=1, max=16384)
        density_mode: EnumProperty(
            name="Density", items=[
                ('FIT', "Fit Texture", "Highest density at which every island fits the texture"),
                ('FIXED', "Fixed", "Use the px/unit value below"),
                ('CURRENT', "Current Average", "Average density of the current UVs, made uniform")],
            default='FIT')
        density: FloatProperty(name="Pixels / Unit", default=32.0, min=0.001, soft_max=1024,
                               description="Texel density in pixels per Blender unit (metre)")
        padding: IntProperty(name="Padding (px)", default=1, min=0, max=64,
                             description="Pixels between islands and to the texture border")
        allow_rotate: BoolProperty(name="Rotate 90°", default=False,
                                   description="Allow islands to be rotated by 90° when packing")
        straighten: BoolProperty(name="Straighten Quad Islands", default=True,
                                 description="Lay quad-grid islands out as rectangles")
        max_distortion: FloatProperty(
            name="Max Distortion %", default=12.0, min=0.0, max=100.0, subtype='PERCENTAGE',
            description="Only straighten an island if the average edge stretch stays below this. "
                        "Otherwise the original unwrap is kept (no added distortion)")
        align: BoolProperty(name="Align Other Islands", default=True,
                            description="Rotate islands that aren't straightened so their bounds are axis aligned")
        pack: BoolProperty(name="Pack", default=True)
        snap_mode: EnumProperty(
            name="Snap", items=[
                ('BOUNDS', "Bounds", "Only the island's outer edges go on pixel lines; the inside keeps "
                                     "its shape (least distortion). Straightened rows / columns still snap"),
                ('ALL', "All Vertices", "Every vertex goes onto a pixel corner")],
            default='BOUNDS')
        keep_density: BoolProperty(
            name="Preserve Texel Density", default=False,
            description="Snapping may not change an island's size by more than the tolerance: islands "
                        "that would (tiny pieces, thin strips) are only moved onto the pixel grid, "
                        "keeping their exact density")
        density_tolerance: FloatProperty(
            name="Tolerance", default=5.0, min=0.0, max=100.0, subtype='PERCENTAGE',
            description="Largest change in width / height that snapping may cause with Preserve "
                        "Texel Density on")
        stack_mirrored: BoolProperty(
            name="Stack Mirrored L/R", default=True,
            description="Islands that mirror each other across the object's X axis share the same "
                        "pixels (the +X side is laid out, the -X side is stacked on it, flipped)")
        preserve_texture: BoolProperty(
            name="Preserve Texture", default=True,
            description="Move the painted pixels with their islands so the model looks the same "
                        "(Pack, Move Islands, Single Pixel Fill and UV Vertex Pivot Transform's 90° "
                        "rotations / mirrors). Pack then packs every face of the edited objects. "
                        "Ctrl+Z undoes the texture too")
        seam_method: EnumProperty(
            name="Seams", items=[
                ('LEARNED', "Learned", "Seams predicted by a model trained on hand-unwrapped characters"),
                ('RULES', "Rules", "Crease angles per object type (hard-surface / organic / tube)")],
            default='LEARNED')
        keep_seams: BoolProperty(
            name="Keep My Seams", default=False,
            description="Keep the seams already marked (and build around them) instead of replacing them")
        unwrap_preset: BoolProperty(
            name="Character Density Preset", default=True,
            description="Auto Unwrap applies the Character Density Preset (head x2, hands, legs, props)")
        island_mode: EnumProperty(
            name="Island Density", items=[
                ('FIXED', "Fixed px/unit", "The selected islands always use this density"),
                ('SCALE', "Multiplier", "The selected islands use the global density times this")],
            default='SCALE')
        island_value: FloatProperty(name="Value", default=2.0, min=0.001, soft_max=64)

    class UV_OT_pixel_perfect(bpy.types.Operator):
        bl_idname = "uv.pixel_perfect"
        bl_label = "Pixel Perfect UV"
        bl_description = "Straighten, scale, snap to pixels and pack the selected UV islands"
        bl_options = {'REGISTER', 'UNDO'}

        step: EnumProperty(items=[
            ('ALL', "All", "Straighten, density, snap and pack"),
            ('STRAIGHTEN', "Straighten", "Straighten / align islands, keeping their size and place"),
            ('SNAP', "Snap to Pixels", "Snap the current UVs to the pixel grid"),
            ('PACK', "Pack", "Pack the islands in whole pixels at their current size"),
            ('STACK', "Stack Mirrored", "Put each -X island on top of its +X mirror twin")],
            default='ALL', options={'SKIP_SAVE'})

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            W, H, _ = texture_size(context, s)
            opt = options_from(s)
            img = None
            if self.step == 'PACK' and s.preserve_texture:
                img = find_image(context)
                if img is None:
                    self.report({'ERROR'}, "Preserve Texture needs an image (show it in the UV Editor "
                                           "or use it in the material)")
                    return {'CANCELLED'}
                W, H = int(img.size[0]), int(img.size[1])
            mixed = set()
            if getattr(self, "step", 'ALL') in ('ALL', 'STRAIGHTEN') and s.straighten:
                mixed = straighten_mixed(context, W, H, opt)
            objs, recs = collect(context, W, H, mixed, all_faces=img is not None)
            rev_before = read_revs([o.name for o in objs]) if img is not None else None
            if not recs:
                self.report({'WARNING'}, "No faces")
                return {'CANCELLED'}
            islands = find_islands(recs)

            if self.step == 'STACK':
                n = run_stack_only(islands)
                st = {}
                msg = "Stacked %d mirrored pairs" % n
            elif self.step == 'SNAP':
                st = run_snap_only(islands, s.snap_mode,
                                   s.density_tolerance / 100.0 if s.keep_density else None)
                msg = "Snapped %d islands to %d x %d" % (len(islands), W, H)
            elif self.step == 'STRAIGHTEN':
                st = run_straighten_only(islands, opt)
                msg = "%d straightened, %d aligned" % (st["straight"], st["aligned"])
            elif self.step == 'PACK':
                err, moves, masks = run_pack_only(islands, W, H, opt)
                if err:
                    self.report({'ERROR'}, err)
                    return {'CANCELLED'}
                st = {}
                msg = "Packed %d islands" % len(islands)
                if img is not None:
                    src = read_pixels(img)
                    backup_image(img, src)
                    packed_px = move_pixels(src, W, H, moves, masks, radius=max(1, s.padding))
                    write_pixels(img, packed_px)
                    msg += ", texture preserved (save the image!)"
                    others = image_users(img, {o.data for o in objs})
                    if others:
                        msg += " - also used by %s, which wasn't in Edit Mode and now looks wrong" % (
                            ", ".join(others[:3]))
            else:
                st, err = run(islands, W, H, opt)
                if err:
                    self.report({'ERROR'}, err)
                    return {'CANCELLED'}
                msg = "%d straightened (max stretch %.1f%%), %d kept shape, %.2f px/unit" % (
                    st["straight"], st["max_distortion"] * 100, st["aligned"], st["density"])
                if st["mirrored"]:
                    msg += ", %d mirrored pairs stacked" % st["mirrored"]
                if st["pixel"]:
                    msg += ", %d single-pixel islands" % st["pixel"]
                if s.density_mode == 'FIT' and s.pack:
                    s.density = round(st["density"], 3)

            write_back(objs, recs, islands, W, H)
            if self.step == 'PACK' and img is not None:
                push_texture_undo(img, objs, rev_before, src, packed_px)
            n_mixed = sum(1 for i in islands if i.keep_rot)
            if n_mixed:
                msg += " (%d with triangles / n-gons)" % n_mixed
            if st.get("broken"):
                self.report({'WARNING'}, msg + " - %d faces collapsed or flipped by snapping "
                                               "(raise the density)" % st["broken"])
            else:
                self.report({'INFO'}, msg)
            return {'FINISHED'}

    class UV_OT_pixel_density_from_selection(bpy.types.Operator):
        bl_idname = "uv.pixel_density_from_selection"
        bl_label = "Get Density"
        bl_description = "Set Pixels / Unit to the average density of the selected UVs"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            W, H, _ = texture_size(context, s)
            _, recs = collect(context, W, H)
            d = current_density(find_islands(recs)) if recs else 0.0
            if d <= 0:
                self.report({'WARNING'}, "Couldn't measure (no UV area)")
                return {'CANCELLED'}
            s.density = round(d, 3)
            s.density_mode = 'FIXED'
            self.report({'INFO'}, "%.3f px/unit" % d)
            return {'FINISHED'}

    class UV_OT_pixel_island_density(bpy.types.Operator):
        bl_idname = "uv.pixel_island_density"
        bl_label = "Set Island Density"
        bl_description = "Store a fixed density or a multiplier on the selected faces' islands"
        bl_options = {'REGISTER', 'UNDO'}

        clear: BoolProperty(default=False, options={'SKIP_SAVE'})

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            count = 0
            for o in edit_objects(context):
                bm = edit_bm(o.data)
                if not any(f.select and not f.hide for f in bm.faces):
                    continue
                dl, sl = face_layers(bm)  # before taking face references
                sel = [f for f in bm.faces if f.select and not f.hide]
                for f in sel:
                    if self.clear:
                        f[dl], f[sl] = 0.0, 1.0
                    elif s.island_mode == 'FIXED':
                        f[dl], f[sl] = s.island_value, 1.0
                    else:
                        f[dl], f[sl] = 0.0, s.island_value
                count += len(sel)
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            if not count:
                self.report({'WARNING'}, "Select faces first")
                return {'CANCELLED'}
            self.report({'INFO'}, "%s on %d faces" % ("Cleared" if self.clear else "Set", count))
            return {'FINISHED'}

    class UV_OT_pixel_select_custom(bpy.types.Operator):
        bl_idname = "uv.pixel_select_custom_density"
        bl_label = "Select Custom"
        bl_description = "Select faces that have their own density or multiplier"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            for o in edit_objects(context):
                bm = edit_bm(o.data)
                dl = bm.faces.layers.float.get(DENSITY_ATTR)
                sl = bm.faces.layers.float.get(SCALE_ATTR)
                for f in bm.faces:
                    if f.hide:
                        continue
                    custom = (dl and f[dl] > 0) or (sl and f[sl] > 0 and abs(f[sl] - 1.0) > 1e-6)
                    if custom:
                        f.select_set(True)
                bm.select_flush_mode()
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            return {'FINISHED'}

    def transform_all_uvs(objs, img, fx, fy, ox=0.0, oy=0.0):
        """uv -> uv * f + o for every face of the edited objects and other meshes using img."""
        for o in objs:
            bm = edit_bm(o.data)
            uvl = bm.loops.layers.uv.verify()
            for f in bm.faces:
                for l in f.loops:
                    uv = l[uvl].uv
                    l[uvl].uv = (uv.x * fx + ox, uv.y * fy + oy)
            bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
        edited = {o.data for o in objs}
        done = set()
        for name in image_users(img, edited):
            me = bpy.data.objects[name].data
            if me in done or not me.uv_layers.active:
                continue
            done.add(me)
            for d in me.uv_layers.active.data:
                d.uv = (d.uv.x * fx + ox, d.uv.y * fy + oy)

    class UV_OT_pixel_resize_texture(bpy.types.Operator):
        bl_idname = "uv.pixel_resize_texture"
        bl_label = "Resize Texture"
        bl_description = ("Resize the image without breaking the painting: Canvas adds / removes room "
                          "(islands keep their pixels), Upscale makes every pixel N x N (finer painting)")
        bl_options = {'REGISTER', 'UNDO'}

        mode: EnumProperty(name="Mode", items=[
            ('CANVAS', "Canvas", "Change the image size, the painting and islands keep their pixels; "
                                 "the anchor sets which side the space is added to / removed from"),
            ('UPSCALE', "Upscale", "Every pixel becomes N x N pixels, UVs unchanged")], default='CANVAS')
        width: IntProperty(name="Width", default=128, min=1, max=16384)
        height: IntProperty(name="Height", default=128, min=1, max=16384)
        factor: IntProperty(name="Factor", default=2, min=2, max=16)
        anchor_y: EnumProperty(name="Vertical", items=[
            ('TOP', "Top", "Keep the painting at the top: space is added below"),
            ('CENTER', "Center", "Add space above and below equally"),
            ('BOTTOM', "Bottom", "Keep the painting at the bottom: space is added above")], default='TOP')
        anchor_x: EnumProperty(name="Horizontal", items=[
            ('LEFT', "Left", "Keep the painting on the left: space is added on the right"),
            ('CENTER', "Center", "Add space on both sides equally"),
            ('RIGHT', "Right", "Keep the painting on the right: space is added on the left")], default='LEFT')

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def invoke(self, context, event):
            img = find_image(context)
            if img is None:
                self.report({'ERROR'}, "No image found")
                return {'CANCELLED'}
            self.width, self.height = int(img.size[0]) * 2, int(img.size[1]) * 2
            return context.window_manager.invoke_props_dialog(self)

        def draw(self, context):
            col = self.layout.column()
            col.prop(self, "mode", expand=True)
            if self.mode == 'CANVAS':
                row = col.row(align=True)
                row.prop(self, "width")
                row.prop(self, "height")
                col.label(text="Keep the painting at:")
                col.row(align=True).prop(self, "anchor_y", expand=True)
                col.row(align=True).prop(self, "anchor_x", expand=True)
            else:
                col.prop(self, "factor")

        def execute(self, context):
            import numpy as np
            img = find_image(context)
            if img is None:
                self.report({'ERROR'}, "No image found")
                return {'CANCELLED'}
            W, H = int(img.size[0]), int(img.size[1])
            src = read_pixels(img)
            backup_image(img, src)
            objs = edit_objects(context)
            rev_before = read_revs([o.name for o in objs])
            s = context.scene.pixel_uv
            if self.mode == 'UPSCALE':
                f = self.factor
                write_pixels(img, np.repeat(np.repeat(src, f, axis=0), f, axis=1))
                s.density *= f
                msg = "%d x %d -> %d x %d (density x%d)" % (W, H, W * f, H * f, f)
            else:
                nw, nh = self.width, self.height
                ox, oy = canvas_offset(W, H, nw, nh, self.anchor_x, self.anchor_y)
                write_pixels(img, place_canvas(src, nw, nh, ox, oy))
                transform_all_uvs(objs, img, W / nw, H / nh, ox / nw, oy / nh)
                msg = "Canvas %d x %d -> %d x %d" % (W, H, nw, nh)
                if nw < W or nh < H:
                    msg += " (cropped: check for islands outside the texture)"
            if s.tex_source == 'CUSTOM':
                s.tex_width, s.tex_height = int(img.size[0]), int(img.size[1])
            push_texture_undo(img, objs, rev_before, src, read_pixels(img))
            self.report({'INFO'}, msg + ". Save the image!")
            return {'FINISHED'}

    class UV_OT_pixel_restore_texture(bpy.types.Operator):
        bl_idname = "uv.pixel_restore_texture"
        bl_label = "Restore Texture"
        bl_description = ("Put back the pixels from before the last texture change (normally Ctrl+Z "
                          "already does it)")
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            img = find_image(context)
            return img is not None and bpy.data.images.get(img.name + BACKUP_SUFFIX) is not None

        def execute(self, context):
            img = find_image(context)
            b = bpy.data.images.get(img.name + BACKUP_SUFFIX)
            objs = edit_objects(context)
            rev_before = read_revs([o.name for o in objs])
            cur = read_pixels(img)
            restored = read_pixels(b)
            write_pixels(img, restored)
            push_texture_undo(img, objs, rev_before, cur, restored)
            self.report({'INFO'}, "Texture restored (%d x %d)" % tuple(b.size))
            return {'FINISHED'}

    PASS_THROUGH = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
                    'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'}
    NUDGE = {'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0), 'UP_ARROW': (0, 1), 'DOWN_ARROW': (0, -1)}

    def uv_selected(context, f, uvl):
        if f.hide or not f.select:
            return False
        if context.tool_settings.use_uv_select_sync:
            return True
        return all(l[uvl].select for l in f.loops)

    class UV_OT_pixel_move(bpy.types.Operator):
        bl_idname = "uv.pixel_move"
        bl_label = "Move Islands (Preserve Texture)"
        bl_description = ("Move the selected UV islands in whole pixels, the painted pixels move with "
                          "them. Mouse / arrows move, R / Shift+R turn 90°, X / Y lock an axis, "
                          "Enter / LMB confirm, Esc / RMB cancel")
        bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

        @classmethod
        def poll(cls, context):
            sp = context.space_data
            return context.mode == 'EDIT_MESH' and sp is not None and sp.type == 'IMAGE_EDITOR'

        def mouse_uv(self, event):
            return self.region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)

        def invoke(self, context, event):
            s = context.scene.pixel_uv
            self.img = find_image(context) if s.preserve_texture else None
            if self.img is not None:
                W, H = int(self.img.size[0]), int(self.img.size[1])
            else:
                W, H, _ = texture_size(context, s)
            self.W, self.H = W, H
            self.objs, recs = collect(context, W, H, all_faces=True)
            self.bms = list(_KEEP_BMESH)  # keep the loops valid for the whole modal
            islands = find_islands(recs)
            selected = set()
            for k, isl in enumerate(islands):
                for fid in isl.face_ids:
                    uvl, loops, _ = recs[fid].ref
                    if uv_selected(context, loops[0].face, uvl):
                        selected.add(k)
                        break
            if not selected:
                self.report({'WARNING'}, "Select the islands to move")
                return {'CANCELLED'}
            masks = [pixel_mask(isl, isl.uv, W, H) for isl in islands]
            moving = set()
            for g in group_overlapping(islands, masks):  # stacked partners come along
                if selected & set(g):
                    moving |= set(g)
            pts = [p for k in moving for p in islands[k].uv]
            mx = math.floor(min(p[0] for p in pts) + 1e-6)
            my = math.floor(min(p[1] for p in pts) + 1e-6)
            w = max(1, math.ceil(max(p[0] for p in pts) - mx - 1e-6))
            h = max(1, math.ceil(max(p[1] for p in pts) - my - 1e-6))
            self.box = (mx, my, w, h)
            self.corners = []
            for k in moving:
                for fid in islands[k].face_ids:
                    uvl, loops, _ = recs[fid].ref
                    for l in loops:
                        uv = l[uvl].uv
                        self.corners.append((uvl, l, uv.x * W, uv.y * H))
            if self.img:
                self.cells, self.others = lifted_cells(moving, masks, self.box, radius=max(1, s.padding))
            else:
                self.cells, self.others = set(), set()
            self.src = read_pixels(self.img) if self.img else None
            self.rev_before = read_revs([o.name for o in self.objs])
            self.dst = None
            self.region = context.region
            self.area = context.area
            self.start = self.mouse_uv(event)
            self.mouse = self.start
            self.k = 0
            self.nudge = [0, 0]
            self.axis = None
            self.last = None
            self.apply(context)
            context.window_manager.modal_handler_add(self)
            context.workspace.status_text_set(
                "Mouse / Arrows: move   R / Shift+R: turn 90°   X / Y: lock axis   "
                "Enter / LMB: confirm   Esc / RMB: cancel")
            return {'RUNNING_MODAL'}

        def current_move(self):
            dx = round((self.mouse[0] - self.start[0]) * self.W) + self.nudge[0]
            dy = round((self.mouse[1] - self.start[1]) * self.H) + self.nudge[1]
            if self.axis == 'X':
                dy = 0
            elif self.axis == 'Y':
                dx = 0
            return BlockMove(*self.box, k=self.k, dx=dx, dy=dy)

        def apply(self, context, force=False):
            mv = self.current_move()
            key = (mv.k, mv.dx, mv.dy)
            if key == self.last and not force:
                return
            self.last = key
            W, H = self.W, self.H
            for uvl, l, x, y in self.corners:
                nx, ny = mv.uv(x, y)
                l[uvl].uv = (nx / W, ny / H)
            for o in self.objs:
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            text = "Move %+d, %+d px" % (mv.dx, mv.dy)
            if mv.k:
                text += "   turned %d°" % (mv.k * 90)
            if self.axis:
                text += "   [%s axis]" % self.axis
            if self.img is not None:
                self.dst = block_move_pixels(self.src, W, H, mv, self.cells, keep=self.others)
                write_pixels(self.img, self.dst)
                dest = {mv.cell(i, j) for i, j in self.cells}
                if dest & (self.others - self.cells):
                    text += "   OVERLAPS another island"
                if any(not (0 <= c[0] < W and 0 <= c[1] < H) for c in dest):
                    text += "   OUTSIDE the texture (pixels lost)"
            else:
                text += "   (UVs only: Preserve Texture off or no image)"
            self.area.header_text_set(text)

        def finish(self, context):
            self.bms = None
            self.area.header_text_set(None)
            context.workspace.status_text_set(None)

        def modal(self, context, event):
            et = event.type
            if et in PASS_THROUGH:
                return {'PASS_THROUGH'}
            if et == 'MOUSEMOVE':
                self.mouse = self.mouse_uv(event)
                self.apply(context)
                return {'RUNNING_MODAL'}
            if event.value != 'PRESS':
                return {'RUNNING_MODAL'}
            if et in NUDGE:
                step = 8 if event.shift else 1
                self.nudge[0] += NUDGE[et][0] * step
                self.nudge[1] += NUDGE[et][1] * step
            elif et == 'R':
                self.k += -1 if event.shift else 1
            elif et in ('X', 'Y'):
                self.axis = None if self.axis == et else et
            elif et in ('LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'):
                if self.img is not None and self.last != (0, 0, 0) and self.dst is not None:
                    backup_image(self.img, self.src)
                    push_texture_undo(self.img, self.objs, self.rev_before, self.src, self.dst)
                self.finish(context)
                return {'FINISHED'}
            elif et in ('RIGHTMOUSE', 'ESC'):
                W, H = self.W, self.H
                for uvl, l, x, y in self.corners:
                    l[uvl].uv = (x / W, y / H)
                for o in self.objs:
                    bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
                if self.img is not None:
                    write_pixels(self.img, self.src)
                self.finish(context)
                return {'CANCELLED'}
            else:
                return {'RUNNING_MODAL'}
            self.apply(context)
            return {'RUNNING_MODAL'}

    CHARACTER_PRESET = dict(head=2.0, hands=1.3, body=1.0, legs=0.8, props=1.5,
                            head_from=0.78, legs_to=0.45, hands_from=0.6)

    class _Stage:
        """Stands in for an operator when Auto Unwrap runs another operator's code directly."""

        def __init__(self, **props):
            self.__dict__.update(props)
            self.messages = []

        def report(self, level, msg):
            self.messages.append((level, msg))

    class _StageError(Exception):
        pass

    class UV_OT_pixel_character_density(bpy.types.Operator):
        bl_idname = "uv.pixel_character_density"
        bl_label = "Character Density Preset"
        bl_description = ("Give the head, hands and legs their own density multiplier, by height "
                          "(world Z) and distance from the centre. Faces with a fixed px/unit keep it")
        bl_options = {'REGISTER', 'UNDO'}

        head: FloatProperty(name="Head", default=2.0, min=0.05, soft_max=8)
        hands: FloatProperty(name="Hands", default=1.3, min=0.05, soft_max=8)
        body: FloatProperty(name="Body / Arms", default=1.0, min=0.05, soft_max=8)
        legs: FloatProperty(name="Legs / Feet", default=0.8, min=0.05, soft_max=8)
        props: FloatProperty(name="Props", default=1.5, min=0.05, soft_max=8,
                             description="Hard-surface objects (sword, sheath, strap...)")
        head_from: FloatProperty(name="Head Starts At", default=0.78, min=0, max=1, subtype='FACTOR',
                                 description="Height (fraction of the character) where the head starts")
        legs_to: FloatProperty(name="Legs End At", default=0.45, min=0, max=1, subtype='FACTOR',
                               description="Height (fraction of the character) where the legs end")
        hands_from: FloatProperty(name="Hands From", default=0.6, min=0, max=1, subtype='FACTOR',
                                  description="Sideways distance (fraction of half the width) where hands start")

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            objs = edit_objects(context)
            data = []
            zs, xs = [], []
            kinds = object_types(context)
            for o in objs:
                bm = edit_bm(o.data)
                face_layers(bm)  # before taking face references
                mw = o.matrix_world
                cents = []
                for f in bm.faces:
                    c = mw @ f.calc_center_median()
                    cents.append((f, c))
                    zs.append(c.z)
                    xs.append(abs(c.x))
                data.append((o, bm, cents))
            if not zs:
                return {'CANCELLED'}
            z0, z1 = min(zs), max(zs)
            hx = max(xs) or 1.0
            counts = {"head": 0, "hands": 0, "body": 0, "legs": 0, "props": 0}
            for o, bm, cents in data:
                dl, sl = face_layers(bm)
                is_prop = kinds.get(o.name) == 'HARD'
                for f, c in cents:
                    if f[dl] > 0:
                        continue
                    t = (c.z - z0) / (z1 - z0) if z1 > z0 else 0.5
                    if is_prop:
                        part = "props"
                    elif t >= self.head_from:
                        part = "head"
                    elif t < self.legs_to:
                        part = "legs"
                    elif abs(c.x) / hx >= self.hands_from:
                        part = "hands"
                    else:
                        part = "body"
                    f[sl] = getattr(self, part)
                    counts[part] += 1
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            self.report({'INFO'}, "Faces: head %(head)d, hands %(hands)d, body %(body)d, legs %(legs)d, "
                                  "props %(props)d" % counts)
            return {'FINISHED'}

    def is_face_selected(context, f, uvl):
        sp = context.space_data
        if sp and sp.type == 'IMAGE_EDITOR':
            return uv_selected(context, f, uvl)
        return f.select and not f.hide

    class UV_OT_pixel_fill(bpy.types.Operator):
        bl_idname = "uv.pixel_fill"
        bl_label = "Single Pixel Fill"
        bl_description = ("Map the selected faces onto one free pixel (a flat colour). With Preserve "
                          "Texture, that pixel gets the average colour they had")
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            img = find_image(context) if s.preserve_texture else None
            if img is not None:
                W, H = int(img.size[0]), int(img.size[1])
            else:
                W, H, _ = texture_size(context, s)
            objs, recs = collect(context, W, H, all_faces=True)
            bms = list(_KEEP_BMESH)
            sel = set()
            for k, r in enumerate(recs):
                uvl, loops, _ = r.ref
                if is_face_selected(context, loops[0].face, uvl):
                    sel.add(k)
            if not sel:
                self.report({'WARNING'}, "Select faces first")
                return {'CANCELLED'}
            occupied = set()
            for isl in find_islands(recs):
                if not any(fid in sel for fid in isl.face_ids):
                    occupied |= pixel_mask(isl, isl.uv, W, H)
            target = free_pixel(occupied, W, H, margin=1)
            if target is None:
                self.report({'ERROR'}, "No free pixel left (with a 1 px margin)")
                return {'CANCELLED'}
            i, j = target
            rev_before = read_revs([o.name for o in objs])
            dst = None
            if img is not None:
                src = read_pixels(img)
                cells = set()
                for k in sel:
                    tmp = Island()
                    tmp.uv = recs[k].uv
                    tmp.faces = [list(range(len(tmp.uv)))]
                    cells |= pixel_mask(tmp, tmp.uv, W, H)
                if cells:
                    col = sum(src[b, a] for a, b in cells) / len(cells)
                    backup_image(img, src)
                    dst = src.copy()
                    dst[max(0, j - 1):j + 2, max(0, i - 1):i + 2] = col  # 3x3: no bleeding
                    write_pixels(img, dst)
            u, v = (i + 0.5) / W, (j + 0.5) / H
            for k in sel:
                uvl, loops, _ = recs[k].ref
                for l in loops:
                    l[uvl].uv = (u, v)
            for o in objs:
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            if dst is not None:
                push_texture_undo(img, objs, rev_before, src, dst)
            del bms
            self.report({'INFO'}, "%d faces -> pixel %d, %d" % (len(sel), i, j))
            return {'FINISHED'}

    SEAM_TYPE_ITEMS = [
        ('AUTO', "Auto", "Guess from the shape: many creases = hard-surface, all-quad tube = tube, "
                         "otherwise organic"),
        ('ORGANIC', "Organic", "Body, head, cloth: cut at part borders, open tubes on the hidden side"),
        ('HARD', "Hard Surface", "Props (sword, strap, armour): cut along the creases"),
        ('TUBE', "Tube", "Rope, cable, belt: one cut along the hidden side, then straightened")]

    def gather_seam_mesh(context, keep):
        """World-space vertices / faces of every edited object, one group per object."""
        verts, faces, groups, existing, parts = [], [], [], [], []
        for o in edit_objects(context):
            bm = edit_bm(o.data)
            bm.verts.index_update()
            mw = o.matrix_world
            base = len(verts)
            verts.extend(tuple(mw @ v.co) for v in bm.verts)
            for f in bm.faces:
                if not f.hide:
                    faces.append([base + v.index for v in f.verts])
                    groups.append(o.name)
            if keep:
                existing.extend((base + e.verts[0].index, base + e.verts[1].index)
                                for e in bm.edges if e.seam)
            parts.append((o, bm, base))
        return verts, faces, groups, existing, parts

    def object_types(context):
        """Seam type per edited object (its own setting, or the automatic guess)."""
        verts, faces, groups, _, parts = gather_seam_mesh(context, False)
        out = {}
        if not faces:
            return out
        mesh = SeamMesh(verts, faces, groups)
        by = defaultdict(list)
        for fi, g in enumerate(groups):
            by[g].append(fi)
        for o, _, _ in parts:
            t = getattr(o, "pixuv_seam_type", 'AUTO')
            out[o.name] = classify_group(mesh, by[o.name]) if (t == 'AUTO' and by[o.name]) else t
        return out

    class UV_OT_pixel_auto_seams(bpy.types.Operator):
        bl_idname = "uv.pixel_auto_seams"
        bl_label = "Auto Seams"
        bl_description = ("Mark seams on the edited objects like a hand unwrap: learned from "
                          "hand-unwrapped characters, mirrored left / right, every island a flat piece")
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            verts, faces, groups, existing, parts = gather_seam_mesh(context, s.keep_seams)
            if not faces:
                self.report({'WARNING'}, "No visible faces")
                return {'CANCELLED'}
            types = {o.name: getattr(o, "pixuv_seam_type", 'AUTO') for o, _, _ in parts}
            W, H, _ = texture_size(context, s)
            pairs, n_islands, used = auto_seams(verts, faces, groups, types, method=s.seam_method,
                                                existing=existing, tex_px=W * H)
            cut = set(pairs)
            n = 0
            for o, bm, base in parts:
                for e in bm.edges:
                    if all(f.hide for f in e.link_faces):
                        continue
                    a, b = base + e.verts[0].index, base + e.verts[1].index
                    on = (min(a, b), max(a, b)) in cut
                    if on:
                        n += 1
                    if on or not s.keep_seams:
                        e.seam = on
                bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            kinds = ", ".join("%s: %s" % (k, v.lower()) for k, v in sorted(used.items()))
            self.report({'INFO'}, "%d seams, %d islands (%s)" % (n, n_islands, kinds))
            return {'FINISHED'}

    class UV_OT_pixel_auto_unwrap(bpy.types.Operator):
        bl_idname = "uv.pixel_auto_unwrap"
        bl_label = "Auto Unwrap Character"
        bl_description = ("Auto Seams, unwrap, Character Density Preset, then Pixel Perfect UV "
                          "(straighten, mirror stacking, snapping, packing) on every edited object")
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def execute(self, context):
            s = context.scene.pixel_uv
            done = []

            def stage(name, op_class, **props):
                """Runs another operator's code directly (no bpy.ops round trip), keeping its
                messages, and raises with the stage name if it doesn't finish."""
                proxy = _Stage(**props)
                try:
                    result = op_class.execute(proxy, context)
                except Exception as exc:
                    raise _StageError("%s failed: %s" % (name, exc))
                if result != {'FINISHED'}:
                    msg = "; ".join(m for _, m in proxy.messages) or "cancelled"
                    raise _StageError("%s stopped: %s" % (name, msg))
                done.append("%s: %s" % (name, "; ".join(m for _, m in proxy.messages)))

            saved = []
            try:
                stage("Seams", UV_OT_pixel_auto_seams)
                for o in edit_objects(context):
                    bm = edit_bm(o.data)
                    saved.append((o, bm, [v.select for v in bm.verts], [e.select for e in bm.edges],
                                   [f.select for f in bm.faces]))
                    for f in bm.faces:
                        if not f.hide:
                            f.select_set(True)
                    bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
                try:
                    res = bpy.ops.uv.unwrap(method='ANGLE_BASED', fill_holes=True, correct_aspect=True,
                                            margin=0.0)
                except Exception as exc:
                    raise _StageError("Unwrap failed: %s" % exc)
                if res != {'FINISHED'}:
                    raise _StageError("Blender's Unwrap was cancelled")
                if s.unwrap_preset:
                    stage("Density preset", UV_OT_pixel_character_density, **CHARACTER_PRESET)
                stage("Pixel Perfect", UV_OT_pixel_perfect, step='ALL')
            except _StageError as exc:
                self.report({'ERROR'}, "Auto Unwrap: %s" % exc)
                return {'CANCELLED'}
            finally:
                for o, bm, vs, es, fs in saved:
                    for elems, st in ((bm.verts, vs), (bm.edges, es), (bm.faces, fs)):
                        for e, v in zip(elems, st):
                            e.select = v
                    bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)
            self.report({'INFO'}, " | ".join(done))
            return {'FINISHED'}

    def active_face_density(context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            return None
        bm = edit_bm(obj.data)
        f = bm.faces.active
        if f is None or not f.select:
            return None
        dl = bm.faces.layers.float.get(DENSITY_ATTR)
        sl = bm.faces.layers.float.get(SCALE_ATTR)
        if dl and f[dl] > 0:
            return "%.2f px/unit" % f[dl]
        if sl and f[sl] > 0 and abs(f[sl] - 1.0) > 1e-6:
            return "x%.2f" % f[sl]
        return "global"

    def draw_panel(layout, context):
        s = context.scene.pixel_uv
        W, H, img = texture_size(context, s)

        box = layout.box()
        box.label(text="Auto Unwrap", icon='MOD_UVPROJECT')
        col = box.column()
        col.scale_y = 1.4
        col.operator("uv.pixel_auto_unwrap", icon='OUTLINER_OB_ARMATURE')
        row = box.row(align=True)
        row.prop(s, "seam_method", expand=True)
        box.operator("uv.pixel_auto_seams", icon='MOD_EDGESPLIT')
        box.prop(s, "keep_seams")
        box.prop(s, "unwrap_preset")
        obj = context.edit_object
        if obj is not None:
            box.prop(obj, "pixuv_seam_type", text=obj.name[:18])

        box = layout.box()
        box.label(text="Texture", icon='IMAGE_DATA')
        box.prop(s, "tex_source", text="")
        if s.tex_source == 'CUSTOM' or img is None:
            row = box.row(align=True)
            row.prop(s, "tex_width")
            row.prop(s, "tex_height")
        box.label(text="%s: %d x %d" % (img or "Custom", W, H))
        row = box.row(align=True)
        row.operator("uv.pixel_resize_texture", icon='FULLSCREEN_ENTER')
        row.operator("uv.pixel_restore_texture", text="", icon='LOOP_BACK')

        box = layout.box()
        box.label(text="Density", icon='MOD_UVPROJECT')
        box.prop(s, "density_mode", text="")
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = s.density_mode == 'FIXED'
        sub.prop(s, "density")
        row.operator("uv.pixel_density_from_selection", text="", icon='EYEDROPPER')

        box = layout.box()
        box.label(text="Island Density", icon='FACESEL')
        box.prop(s, "island_mode", text="")
        row = box.row(align=True)
        row.prop(s, "island_value")
        row = box.row(align=True)
        row.operator("uv.pixel_island_density", text="Set")
        row.operator("uv.pixel_island_density", text="Clear").clear = True
        row.operator("uv.pixel_select_custom_density", text="", icon='RESTRICT_SELECT_OFF')
        box.operator("uv.pixel_character_density", icon='ARMATURE_DATA')
        cur = active_face_density(context)
        if cur:
            box.label(text="Active face: " + cur)

        box = layout.box()
        box.label(text="Shape", icon='MESH_GRID')
        box.prop(s, "straighten")
        sub = box.column()
        sub.enabled = s.straighten
        sub.prop(s, "max_distortion")
        box.prop(s, "align")
        box.row().prop(s, "snap_mode", expand=True)
        row = box.row(align=True)
        row.prop(s, "keep_density")
        sub = row.row(align=True)
        sub.enabled = s.keep_density
        sub.prop(s, "density_tolerance", text="")

        box = layout.box()
        box.label(text="Packing", icon='PACKAGE')
        box.prop(s, "pack")
        sub = box.column()
        sub.enabled = s.pack
        sub.prop(s, "padding")
        sub.prop(s, "allow_rotate")
        box.prop(s, "stack_mirrored")
        box.prop(s, "preserve_texture")
        if s.preserve_texture:
            box.label(text="Pack, Move, Fill, Pivot Rotate / Mirror", icon='INFO')
        if context.space_data and context.space_data.type == 'IMAGE_EDITOR':
            box.operator("uv.pixel_move", text="Move Islands (Alt+G)", icon='VIEW_PAN')

        col = layout.column()
        col.scale_y = 1.4
        col.operator("uv.pixel_perfect", icon='UV').step = 'ALL'
        row = layout.row(align=True)
        row.operator("uv.pixel_perfect", text="Straighten").step = 'STRAIGHTEN'
        row.operator("uv.pixel_perfect", text="Snap").step = 'SNAP'
        row.operator("uv.pixel_perfect", text="Pack").step = 'PACK'
        row = layout.row(align=True)
        row.operator("uv.pixel_perfect", text="Stack Mirrored", icon='MOD_MIRROR').step = 'STACK'
        row.operator("uv.pixel_fill", icon='SNAP_FACE_CENTER')

    class IMAGE_PT_pixel_perfect_uv(bpy.types.Panel):
        bl_space_type = 'IMAGE_EDITOR'
        bl_region_type = 'UI'
        bl_category = "Pixel UV"
        bl_label = "Pixel Perfect UV"

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def draw(self, context):
            draw_panel(self.layout, context)

    class VIEW3D_PT_pixel_perfect_uv(bpy.types.Panel):
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = "Pixel UV"
        bl_label = "Pixel Perfect UV"

        @classmethod
        def poll(cls, context):
            return context.mode == 'EDIT_MESH'

        def draw(self, context):
            draw_panel(self.layout, context)

    def menu_func(self, context):
        self.layout.separator()
        self.layout.operator("uv.pixel_perfect", icon='UV').step = 'ALL'

    classes = (
        PixelUVSettings,
        UV_OT_pixel_perfect,
        UV_OT_pixel_density_from_selection,
        UV_OT_pixel_island_density,
        UV_OT_pixel_select_custom,
        UV_OT_pixel_resize_texture,
        UV_OT_pixel_restore_texture,
        UV_OT_pixel_move,
        UV_OT_pixel_character_density,
        UV_OT_pixel_fill,
        UV_OT_pixel_auto_seams,
        UV_OT_pixel_auto_unwrap,
        IMAGE_PT_pixel_perfect_uv,
        VIEW3D_PT_pixel_perfect_uv,
    )

    addon_keymaps = []

    def register():
        global PIXEL_UV_TEXTURE_API
        for c in classes:
            bpy.utils.register_class(c)
        for lst, fn in _handlers():
            if fn not in lst:
                lst.append(fn)
        import types as _types
        PIXEL_UV_TEXTURE_API = _types.SimpleNamespace(
            version=1, start=TextureSession.start, ROTATIONS=ROTATIONS,
            MIRROR_X=MIRROR_X, MIRROR_Y=MIRROR_Y)
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc:
            km = kc.keymaps.new(name="UV Editor", space_type='EMPTY')
            kmi = km.keymap_items.new(UV_OT_pixel_move.bl_idname, 'G', 'PRESS', alt=True)
            addon_keymaps.append((km, kmi))
        bpy.types.Scene.pixel_uv = PointerProperty(type=PixelUVSettings)
        bpy.types.Object.pixuv_seam_type = EnumProperty(
            name="Seam Type", items=SEAM_TYPE_ITEMS, default='AUTO',
            description="How Auto Seams cuts this object")
        bpy.types.IMAGE_MT_uvs.append(menu_func)
        bpy.types.VIEW3D_MT_uv_map.append(menu_func)

    def unregister():
        global PIXEL_UV_TEXTURE_API
        PIXEL_UV_TEXTURE_API = None
        for lst, fn in _handlers():
            if fn in lst:
                lst.remove(fn)
        for km, kmi in addon_keymaps:
            km.keymap_items.remove(kmi)
        addon_keymaps.clear()
        bpy.types.VIEW3D_MT_uv_map.remove(menu_func)
        bpy.types.IMAGE_MT_uvs.remove(menu_func)
        del bpy.types.Object.pixuv_seam_type
        del bpy.types.Scene.pixel_uv
        for c in reversed(classes):
            bpy.utils.unregister_class(c)

    if __name__ == "__main__":
        register()
