bl_info = {
    "name": "Pixel Perfect UV",
    "author": "Zafio",
    "version": (0, 3, 1),
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


# ============================================================================
# Core (pure Python, no bpy)
# ============================================================================

class FaceRec:
    """One mesh face: vertex keys, current UVs in pixels, world positions, density settings."""
    __slots__ = ("vkeys", "uv", "p3", "density", "scale", "ref", "keep_rot")

    def __init__(self, vkeys, uv, p3, density=0.0, scale=1.0, ref=None, keep_rot=False):
        self.keep_rot = keep_rot
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
        scales = []
        for fi in fl:
            r = recs[fi]
            face = []
            for key, uv, p in zip(corner_keys[fi], r.uv, r.p3):
                idx = local.get(key)
                if idx is None:
                    idx = local[key] = len(isl.uv)
                    isl.uv.append((uv[0], uv[1]))
                    isl.p3.append(p)
                face.append(idx)
            isl.faces.append(face)
            isl.face_ids.append(fi)
            if r.density > 0:
                dens.append(r.density)
            if r.scale > 0 and abs(r.scale - 1.0) > 1e-6:
                scales.append(r.scale)
        isl.keep_rot = any(recs[fi].keep_rot for fi in fl)
        isl.density = max(dens) if dens else 0.0
        isl.scale = max(scales) if scales else 1.0
        islands.append(isl)
    return islands


def island_areas(isl, coords):
    a3 = sum(area3d([isl.p3[i] for i in f]) for f in isl.faces)
    a2 = sum(signed_area2d([coords[i] for i in f]) for f in isl.faces)
    return a3, a2


def distortion(isl, coords):
    """Area-weighted mean |log(stretch)| of the edges, after removing the island's uniform scale.
    0 = no distortion, 0.1 is about 10 % stretch."""
    a3, a2 = island_areas(isl, coords)
    if a3 < EPS or abs(a2) < EPS:
        return float("inf")
    k = math.sqrt(a3 / abs(a2))  # uv -> world scale
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
    # flipped faces count as heavy distortion
    sign = 1.0 if a2 > 0 else -1.0
    for f in isl.faces:
        if signed_area2d([coords[i] for i in f]) * sign <= 0:
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
    _, a_orig = island_areas(sub, sub.uv)
    _, a_new = island_areas(sub, lay)
    k = math.sqrt(abs(a_orig) / max(abs(a_new), EPS))
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
    a3, a2 = island_areas(isl, isl.uv)
    if a3 < EPS or abs(a2) < EPS:
        return None
    k = math.sqrt(a3 / abs(a2))
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
    a = sum(signed_area2d([orig[i] for i in f]) for f in isl.faces)
    sign = 1.0 if a >= 0 else -1.0
    vfaces = defaultdict(list)
    for fi, f in enumerate(isl.faces):
        for v in f:
            vfaces[v].append(fi)

    def ok(fi, pts):
        return signed_area2d([pts[i] for i in isl.faces[fi]]) * sign > 0

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
    _, a = island_areas(isl, isl.uv)
    sign = 1.0 if a >= 0 else -1.0
    return sum(1 for f in isl.faces if signed_area2d([coords[i] for i in f]) * sign <= 0)


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
        self.max_distortion = 0.08
        self.align = True
        self.density_mode = "FIT"     # FIT, FIXED, CURRENT
        self.density = 32.0           # px / unit for FIXED
        self.snap = True
        self.pack = True
        self.padding = 1
        self.allow_rotate = False
        self.__dict__.update(kw)


def current_density(islands):
    a3 = a2 = 0.0
    for isl in islands:
        x, y = island_areas(isl, isl.uv)
        a3 += x
        a2 += abs(y)
    return math.sqrt(a2 / a3) if a3 > EPS else 0.0


def build_layouts(islands, opt):
    for isl in islands:
        isl.units = None
        isl.kind = "skip"
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


def _place(isl, base, do_snap):
    d = _island_density(isl, base)
    pts = [(x * d, y * d) for x, y in isl.units]
    return snap(pts, isl.mask, isl) if do_snap else pts


def _try_pack(islands, base, W, H, opt):
    locs = []
    sizes = []
    for isl in islands:
        loc, w, h = _local(_place(isl, base, opt.snap))
        locs.append(loc)
        sizes.append((math.ceil(w - 1e-9), math.ceil(h - 1e-9)))
    res = pack_rects(sizes, W, H, opt.padding, opt.allow_rotate)
    return res, locs, sizes


def run(islands, W, H, opt):
    """Full pipeline. Fills isl.new (px). Returns (stats dict, error string or None)."""
    build_layouts(islands, opt)
    active = [i for i in islands if i.units is not None]
    if not active:
        return None, "No usable islands (zero-area faces?)"

    free_isl = [i for i in active if i.density <= 0]
    if opt.density_mode == "FIXED":
        base = opt.density
    elif opt.density_mode == "CURRENT" or not opt.pack:
        base = current_density(active)
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
            pts = _place(isl, base, False)
            cn = _centroid(pts)
            co = _centroid(isl.uv)
            off = (co[0] - cn[0], co[1] - cn[1])
            if opt.snap:
                off = (round(off[0]), round(off[1]))
                pts = snap(pts, isl.mask, isl)
            isl.new = [(x + off[0], y + off[1]) for x, y in pts]

    stats = {
        "density": base,
        "straight": sum(1 for i in active if i.kind == "straight"),
        "aligned": sum(1 for i in active if i.kind in ("aligned", "keep")),
        "skipped": len(islands) - len(active),
        "broken": sum(broken_faces(i, i.new) for i in active),
        "max_distortion": max((i.distortion for i in active if i.kind == "straight"), default=0.0),
    }
    return stats, None


def _bbox_area(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(max(xs) - min(xs), 1e-4) * max(max(ys) - min(ys), 1e-4)


def run_snap_only(islands):
    for isl in islands:
        isl.mask = grid_mask(isl, isl.uv)
        isl.new = snap(isl.uv, isl.mask, isl)
    return {"broken": sum(broken_faces(i, i.new) for i in islands)}


def run_straighten_only(islands, opt):
    """Straightens / aligns but keeps each island's size and position."""
    build_layouts(islands, opt)
    for isl in islands:
        isl.new = None
        if isl.units is None or (isl.kind != "straight" and not opt.align):
            continue
        _, a_orig = island_areas(isl, isl.uv)
        _, a_new = island_areas(isl, isl.units)
        k = math.sqrt(abs(a_orig) / max(abs(a_new), EPS))
        co = _centroid(isl.uv)
        isl.new = [(x * k + co[0], y * k + co[1]) for x, y in isl.units]
    return {
        "straight": sum(1 for i in islands if i.kind == "straight"),
        "aligned": sum(1 for i in islands if i.kind == "aligned"),
    }


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


def move_pixels(src, W, H, moves, masks, clear=(0.0, 0.0, 0.0, 0.0)):
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
    for mv in moves:
        members = set(mv.members)
        cells = set()
        for k in mv.members:
            cells |= masks[k]
        ring = set()
        for i, j in cells:
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    c = (i + di, j + dj)
                    if c in cells or not (mv.mx <= c[0] < mv.mx + mv.w and mv.my <= c[1] < mv.my + mv.h):
                        continue
                    if claimed.get(c, members) <= members:
                        ring.add(c)
        for i, j in cells | ring:
            if not (0 <= i < W and 0 <= j < H):
                continue
            ni, nj = mv.cell(i, j)
            if 0 <= ni < W and 0 <= nj < H:
                dst[nj, ni] = src[j, i]
    return dst


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
# Blender side
# ============================================================================

if bpy is not None:

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

    def collect(context, W, H, keep_rot=frozenset(), all_faces=False):
        """FaceRecs of the selected faces (all visible faces when nothing is selected, every
        face including hidden ones with all_faces)."""
        objs = edit_objects(context)
        bms = []
        any_sel = False
        for o in objs:
            bm = bmesh.from_edit_mesh(o.data)
            bms.append((o, bm))
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
                    (o.name, f.index) in keep_rot))
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
        bms = [(o, bmesh.from_edit_mesh(o.data)) for o in objs]
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
            padding=s.padding, allow_rotate=s.allow_rotate)

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
            name="Max Distortion %", default=8.0, min=0.0, max=100.0, subtype='PERCENTAGE',
            description="Only straighten an island if the average edge stretch stays below this. "
                        "Otherwise the original unwrap is kept (no added distortion)")
        align: BoolProperty(name="Align Other Islands", default=True,
                            description="Rotate islands that aren't straightened so their bounds are axis aligned")
        pack: BoolProperty(name="Pack", default=True)
        preserve_texture: BoolProperty(
            name="Preserve Texture", default=True,
            description="Pack only: move the painted pixels with their islands so the model looks the "
                        "same. Packs every face of the edited objects. Ctrl+Z only undoes the UVs: use "
                        "Restore Texture after it to get the old pixels back")
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
            ('PACK', "Pack", "Pack the islands in whole pixels at their current size")],
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
            if self.step in ('ALL', 'STRAIGHTEN') and s.straighten:
                mixed = straighten_mixed(context, W, H, opt)
            objs, recs = collect(context, W, H, mixed, all_faces=img is not None)
            if not recs:
                self.report({'WARNING'}, "No faces")
                return {'CANCELLED'}
            islands = find_islands(recs)

            if self.step == 'SNAP':
                st = run_snap_only(islands)
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
                    write_pixels(img, move_pixels(src, W, H, moves, masks))
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
                if s.density_mode == 'FIT' and s.pack:
                    s.density = round(st["density"], 3)

            write_back(objs, recs, islands, W, H)
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
                bm = bmesh.from_edit_mesh(o.data)
                sel = [f for f in bm.faces if f.select and not f.hide]
                if not sel:
                    continue
                dl = bm.faces.layers.float.get(DENSITY_ATTR) or bm.faces.layers.float.new(DENSITY_ATTR)
                sl = bm.faces.layers.float.get(SCALE_ATTR) or bm.faces.layers.float.new(SCALE_ATTR)
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
                bm = bmesh.from_edit_mesh(o.data)
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
            bm = bmesh.from_edit_mesh(o.data)
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
            col.label(text="Ctrl+Z won't undo the image: Restore Texture will", icon='INFO')

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
            self.report({'INFO'}, msg + ". Save the image!")
            return {'FINISHED'}

    class UV_OT_pixel_restore_texture(bpy.types.Operator):
        bl_idname = "uv.pixel_restore_texture"
        bl_label = "Restore Texture"
        bl_description = ("Put back the pixels from before the last Preserve Texture pack or resize. "
                          "Use it together with Ctrl+Z (which only undoes the UVs)")
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            img = find_image(context)
            return img is not None and bpy.data.images.get(img.name + BACKUP_SUFFIX) is not None

        def execute(self, context):
            img = find_image(context)
            b = bpy.data.images.get(img.name + BACKUP_SUFFIX)
            write_pixels(img, read_pixels(b))
            self.report({'INFO'}, "Texture restored (%d x %d)" % tuple(b.size))
            return {'FINISHED'}

    def active_face_density(context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            return None
        bm = bmesh.from_edit_mesh(obj.data)
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

        box = layout.box()
        box.label(text="Packing", icon='PACKAGE')
        box.prop(s, "pack")
        sub = box.column()
        sub.enabled = s.pack
        sub.prop(s, "padding")
        sub.prop(s, "allow_rotate")
        box.prop(s, "preserve_texture")
        if s.preserve_texture:
            box.label(text="Preserve works with the Pack button", icon='INFO')

        col = layout.column()
        col.scale_y = 1.4
        col.operator("uv.pixel_perfect", icon='UV').step = 'ALL'
        row = layout.row(align=True)
        row.operator("uv.pixel_perfect", text="Straighten").step = 'STRAIGHTEN'
        row.operator("uv.pixel_perfect", text="Snap").step = 'SNAP'
        row.operator("uv.pixel_perfect", text="Pack").step = 'PACK'

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
        IMAGE_PT_pixel_perfect_uv,
        VIEW3D_PT_pixel_perfect_uv,
    )

    def register():
        for c in classes:
            bpy.utils.register_class(c)
        bpy.types.Scene.pixel_uv = PointerProperty(type=PixelUVSettings)
        bpy.types.IMAGE_MT_uvs.append(menu_func)
        bpy.types.VIEW3D_MT_uv_map.append(menu_func)

    def unregister():
        bpy.types.VIEW3D_MT_uv_map.remove(menu_func)
        bpy.types.IMAGE_MT_uvs.remove(menu_func)
        del bpy.types.Scene.pixel_uv
        for c in reversed(classes):
            bpy.utils.unregister_class(c)

    if __name__ == "__main__":
        register()
