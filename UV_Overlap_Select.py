"""
UV Overlap Select  (Blender 4.5)

In the UV Editor, press the key of the select mode you are ALREADY in
(1 = vertex, 2 = edge, 3 = face, 4 = island) to toggle "overlap selection":

    * Whatever you select gets every element that sits exactly on top of it
      (same UV position) selected as well.
    * Deselecting an element deselects its overlapped partners too.
    * While active, the UV selection color turns RED whenever the selection
      actually contains overlapped elements.

Press the same key again to go back to normal selection.
Pressing a different mode key switches mode and turns overlap off
(unless "Keep On When Changing Mode" is enabled in the sidebar panel).

Shift + 1 / 2 / 3 / 4 (or Shift + W) toggles a wire display (independent of overlap selection):
    * stacked faces (identical UV footprint) get a pale green wire,
    * overlapping faces (partly covering another face) a mauve wire.
    Colors and line width in the N panel.

Works with UV Sync Selection on or off (island mode only exists with sync off).
Sidebar: UV Editor > N panel > "Overlap" tab.
"""

bl_info = {
    "name": "UV Overlap Select",
    "author": "Zafio",
    "version": (1, 3, 3),
    "blender": (4, 5, 0),
    "location": "UV Editor: 1/2/3/4 again on the active mode, Shift+1/2/3/4 or Shift+W | N panel > Overlap",
    "description": "Press the active UV select mode key again to also select overlapped "
                   "elements (vertex, edge, face, island). Selection turns red when it holds overlaps. "
                   "Shift+1/2/3/4 or Shift+W shows stacked (green) / overlapping (mauve) faces.",
    "category": "UV",
}

import time
import traceback

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except ImportError:      # background mode / tests
    gpu = None
    batch_for_shader = None


# --------------------------------------------------------------------------
# Constants / global state
# --------------------------------------------------------------------------

_RED = (1.0, 0.10, 0.10)
_THEME_PROPS = ("vertex_select", "edge_select", "face_select", "face_dot")
_MESH_MODE = {'VERTEX': 'VERT', 'EDGE': 'EDGE', 'FACE': 'FACE'}
_ACTIVE_VALUES = {'PRESS', 'RELEASE', 'CLICK', 'DOUBLE_CLICK', 'CLICK_DRAG'}
_MOUSE_BUTTONS = {'LEFTMOUSE', 'RIGHTMOUSE', 'PEN'}
_SETTLE = 0.06   # seconds to wait after the last click event before expanding

_saved_theme = {}
_addon_keymaps = []

_HAS_LOOPUV = hasattr(bmesh.types, "BMLoopUV") and hasattr(bmesh.types.BMLoopUV, "select")


class _State:
    def __init__(self):
        self.token = 0          # id of the current watcher
        self.heartbeat = 0.0    # last time the watcher ticked
        self.ov_green = []      # wire lines (UV coords): stacked faces
        self.ov_blue = []       # wire lines (UV coords): overlapping faces (mauve)
        self.ov_batches = None
        self.ov_sig = None
        self.ov_counts = (0, 0, True)
        self.reset()

    def reset(self):
        self.prev = None        # set of selected unit ids after our last expansion
        self.topo = None
        self.armed = None       # (mode, sync) the overlap mode was armed for
        self.dirty = False
        self.dirty_time = 0.0
        self.ops_sig = None
        self.last_check = 0.0
        self.interval = 0.4
        self.geo = {}           # unit -> UV position signature at our last snapshot
        self.status = "Off"     # shown in the N panel
        self.mouse_down = False # a select button is held (click not finished yet)
        self.mouse_time = 0.0
        self.red = False        # theme currently switched to red


S = _State()


# --------------------------------------------------------------------------
# Theme (selection color -> red)
# --------------------------------------------------------------------------

def _prefs(context):
    try:
        return context.preferences.addons[__name__].preferences
    except Exception:
        return None


def _theme_apply_red(context):
    if _saved_theme:
        return
    th = context.preferences.themes[0].image_editor
    flat = []
    for name in _THEME_PROPS:
        if hasattr(th, name):
            old = tuple(getattr(th, name))
            _saved_theme[name] = old
            flat += (list(old) + [1.0] * 4)[:4]
            setattr(th, name, _RED + old[3:])
        else:
            flat += [0.0] * 4
    p = _prefs(context)
    if p is not None:
        p.theme_backup = flat
        p.theme_backup_on = True


def _theme_restore(context):
    try:
        th = context.preferences.themes[0].image_editor
    except Exception:
        return
    p = _prefs(context)
    # Recover colors saved by a previous session that ended while overlap was on
    if not _saved_theme and p is not None and p.theme_backup_on:
        for i, name in enumerate(_THEME_PROPS):
            if hasattr(th, name):
                n = len(getattr(th, name))
                _saved_theme[name] = tuple(p.theme_backup[i * 4:i * 4 + n])
    for name, old in _saved_theme.items():
        try:
            setattr(th, name, old)
        except Exception:
            pass
    _saved_theme.clear()
    if p is not None:
        p.theme_backup_on = False


# --------------------------------------------------------------------------
# UV layer access (BMLoopUV in 4.x, bool-layer fallback)
# --------------------------------------------------------------------------

class _AccLoopUV:
    def __init__(self, uv):
        self.uv = uv

    def pos(self, l, inv):
        p = l[self.uv].uv
        return (round(p[0] * inv), round(p[1] * inv))

    def raw(self, l):
        p = l[self.uv].uv
        return (p[0], p[1])

    def vsel(self, l):
        return l[self.uv].select

    def esel(self, l):
        return l[self.uv].select_edge

    def set_vsel(self, l, s):
        l[self.uv].select = s

    def set_esel(self, l, s):
        l[self.uv].select_edge = s


class _AccBool:
    def __init__(self, uv, vs, es):
        self.uv, self.vs, self.es = uv, vs, es

    def pos(self, l, inv):
        p = l[self.uv]
        p = getattr(p, "uv", p)
        return (round(p[0] * inv), round(p[1] * inv))

    def raw(self, l):
        p = l[self.uv]
        p = getattr(p, "uv", p)
        return (p[0], p[1])

    def vsel(self, l):
        return bool(l[self.vs])

    def esel(self, l):
        return bool(l[self.es])

    def set_vsel(self, l, s):
        l[self.vs] = s

    def set_esel(self, l, s):
        l[self.es] = s


def _make_access(bm):
    uv = bm.loops.layers.uv.active
    if uv is None:
        return None
    if _HAS_LOOPUV:
        return _AccLoopUV(uv)
    vs = bm.loops.layers.bool.get(".vs." + uv.name)
    es = bm.loops.layers.bool.get(".es." + uv.name)
    if vs is None or es is None:
        return None
    return _AccBool(uv, vs, es)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _DSU:
    """Tiny union-find."""

    def __init__(self):
        self.p = {}

    def add(self, x):
        self.p.setdefault(x, x)

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _seen_union(dsu, seen, key, u):
    first = seen.get(key)
    if first is None:
        seen[key] = u
    else:
        dsu.union(first, u)


def _groups_from_dsu(dsu):
    key_of, groups = {}, {}
    for x in dsu.p:
        r = dsu.find(x)
        key_of[x] = r
        groups.setdefault(r, []).append(x)
    return key_of, groups


class _Model:
    __slots__ = ("key_of", "groups", "selected", "apply", "geo", "sig_count", "elem_of")

    def __init__(self, key_of, groups, selected, apply, geo):
        self.sig_count = None   # island mode: footprint -> number of faces with it
        self.elem_of = None     # vertex/edge mode: unit -> mesh vertex / edge it belongs to
        self.key_of, self.groups, self.selected, self.apply = key_of, groups, selected, apply
        self.geo = geo          # unit -> UV position signature (to detect moves)


_WATCH_IDS = {"UV_OT_overlap_watch", "uv.overlap_watch"}


_BUSY_WORDS = ("transform", "translate", "rotate", "resize", "scale", "shear",
               "move", "drag", "slide", "grab", "pivot", "collision", "mirror")


def _transform_running(context):
    """Name of a running modal transform-like operator, or None.

    Only tools that MOVE UVs count: other add-ons that keep a modal running in the
    background (and Blender's own box/circle select) must not pause overlap select."""
    win = context.window
    mods = getattr(win, "modal_operators", None) if win else None
    if not mods:
        return None
    for op in mods:
        name = getattr(op, "bl_idname", "") or ""
        if name in _WATCH_IDS:
            continue
        low = name.lower()
        if low.startswith("transform_ot_") or any(w in low for w in _BUSY_WORDS):
            return name
    return None


def _set_status(context, text):
    if S.status != text:
        S.status = text
        _tag_redraw(context)


def _is_uv_editor(context):
    sd = context.space_data
    if context.mode != 'EDIT_MESH' or sd is None or sd.type != 'IMAGE_EDITOR':
        return False
    # NOTE: space.ui_mode never reports 'UV' (its enum only has View/Paint/Mask),
    # so check space.mode / show_uvedit instead.
    return getattr(sd, "mode", None) == 'UV' or bool(getattr(sd, "show_uvedit", False))


def _current_mode(ts):
    if ts.use_uv_select_sync:
        v, e, f = ts.mesh_select_mode
        return 'VERTEX' if v else ('EDGE' if e else 'FACE')
    return ts.uv_select_mode


def _set_mode(context, mode):
    ts = context.tool_settings
    if ts.use_uv_select_sync:
        try:
            bpy.ops.mesh.select_mode(type=_MESH_MODE[mode])
            return
        except Exception:
            pass
        ts.mesh_select_mode = (mode == 'VERTEX', mode == 'EDGE', mode == 'FACE')
    else:
        ts.uv_select_mode = mode


def _tag_redraw(context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in {'IMAGE_EDITOR', 'VIEW_3D'}:
                area.tag_redraw()


def _collect(context):
    out = []
    for ob in context.objects_in_mode_unique_data:
        if ob.type != 'MESH':
            continue
        me = ob.data
        bm = bmesh.from_edit_mesh(me)
        acc = _make_access(bm)
        if acc is None:
            continue
        # NOTE: never call index_update() here - it writes to the mesh and breaks
        # running transforms. Units are identified by enumeration order instead.
        out.append((me, bm, acc))
    return out


# --------------------------------------------------------------------------
# Models: how "units" are identified, grouped by overlap, and (de)selected
#   key_of : unit -> group key       groups : group key -> [units]
#   selected : set of selected units  apply(to_sel, to_desel) -> touched object indices
# --------------------------------------------------------------------------

def _model_vertex(entries, inv):
    """UV sync OFF, vertex mode. Unit = face corner, group = same UV position."""
    key_of, groups, loop_of, sel, elem_of = {}, {}, {}, set(), {}
    for oi, (me, bm, acc) in enumerate(entries):
        pos, vsel = acc.pos, acc.vsel
        vidx = {v: i for i, v in enumerate(bm.verts)}
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            for k, l in enumerate(f.loops):
                u = (oi, fi, k)
                key = pos(l, inv)
                key_of[u] = key
                groups.setdefault(key, []).append(u)
                loop_of[u] = l
                elem_of[u] = (oi, vidx[l.vert])
                if vsel(l):
                    sel.add(u)

    def apply(to_sel, to_desel):
        faces = {}
        for units, state in ((to_desel, False), (to_sel, True)):
            for u in units:
                l = loop_of[u]
                entries[u[0]][2].set_vsel(l, state)
                faces[(u[0], u[1])] = l.face
        for (oi, _fi), f in faces.items():
            acc = entries[oi][2]
            for l in f.loops:
                acc.set_esel(l, acc.vsel(l) and acc.vsel(l.link_loop_next))
        return {oi for oi, _fi in faces}

    model = _Model(key_of, groups, sel, apply, key_of)
    model.elem_of = elem_of
    return model


class _Sticky:
    """Mimics Blender's UV sticky selection for edge / face mode.

    Selecting an edge or face in Blender also selects the touching corners of the
    neighbouring faces (same mesh vertex, same UV spot), so a move keeps the island
    connected. Without this, overlapped edges / faces we add get torn off."""

    def __init__(self, sticky):
        if sticky == 'DISABLED':
            self.fn = None
        elif sticky == 'SHARED_VERTEX':
            self.fn = lambda oi, vi, p: (oi, vi)
        else:                                     # 'SHARED_LOCATION' (default)
            self.fn = lambda oi, vi, p: (oi, vi, p)
        self.clusters = {}                        # sticky key -> [(oi, loop)]
        self.face_cks = {}                        # (oi, fi) -> [sticky key per corner]

    def add_face(self, oi, fi, f, ps, vidx):
        if self.fn is None:
            return
        cks = []
        for l, p in zip(f.loops, ps):
            ck = self.fn(oi, vidx[l.vert], p)
            self.clusters.setdefault(ck, []).append((oi, l))
            cks.append(ck)
        self.face_cks[(oi, fi)] = cks

    def flush(self, entries, face_keys):
        """Corner (vertex) selection of every cluster touched by these faces =
        'some selected edge in the cluster ends here'."""
        cks = set()
        for key in face_keys:
            cks.update(self.face_cks.get(key, ()))
        for ck in cks:
            members = self.clusters[ck]
            on = False
            for oi, l in members:
                acc = entries[oi][2]
                if acc.esel(l) or acc.esel(l.link_loop_prev):
                    on = True
                    break
            for oi, l in members:
                entries[oi][2].set_vsel(l, on)


def _model_edge(entries, inv, sticky):
    """UV sync OFF, edge mode. Unit = face corner edge, group = same pair of UV positions."""
    key_of, groups, loop_of, sel, elem_of = {}, {}, {}, set(), {}
    st = _Sticky(sticky)
    for oi, (me, bm, acc) in enumerate(entries):
        pos, esel = acc.pos, acc.esel
        vidx = {v: i for i, v in enumerate(bm.verts)} if st.fn else None
        eidx = {e: i for i, e in enumerate(bm.edges)}
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            ls = list(f.loops)
            ps = [pos(l, inv) for l in ls]
            st.add_face(oi, fi, f, ps, vidx)
            n = len(ls)
            for k, l in enumerate(ls):
                u = (oi, fi, k)
                a, b = ps[k], ps[(k + 1) % n]
                key = (a, b) if a <= b else (b, a)
                key_of[u] = key
                groups.setdefault(key, []).append(u)
                loop_of[u] = l
                elem_of[u] = (oi, eidx[l.edge])
                if esel(l):
                    sel.add(u)

    def apply(to_sel, to_desel):
        faces = {}
        for units, state in ((to_desel, False), (to_sel, True)):
            for u in units:
                l = loop_of[u]
                entries[u[0]][2].set_esel(l, state)
                faces[(u[0], u[1])] = l.face
        if st.fn is not None:
            st.flush(entries, faces.keys())
        else:
            for (oi, _fi), f in faces.items():
                acc = entries[oi][2]
                for l in f.loops:
                    acc.set_vsel(l, acc.esel(l) or acc.esel(l.link_loop_prev))
        return {oi for oi, _fi in faces}

    model = _Model(key_of, groups, sel, apply, key_of)
    model.elem_of = elem_of
    return model


def _apply_faces(entries, face_of, sticky=None):
    def apply(to_sel, to_desel):
        touched = set()
        for units, state in ((to_desel, False), (to_sel, True)):
            for u in units:
                acc = entries[u[0]][2]
                for l in face_of[u].loops:
                    acc.set_vsel(l, state)
                    acc.set_esel(l, state)
                touched.add(u[0])
        if sticky is not None and sticky.fn is not None:
            sticky.flush(entries, list(to_sel) + list(to_desel))
        return touched
    return apply


def _model_face(entries, inv, sticky):
    """UV sync OFF, face mode. Unit = face, group = identical UV footprint.
    (In edge/face mode Blender treats a face as selected when all its EDGES are.)"""
    key_of, groups, face_of, sel = {}, {}, {}, set()
    st = _Sticky(sticky)
    for oi, (me, bm, acc) in enumerate(entries):
        pos, esel = acc.pos, acc.esel
        vidx = {v: i for i, v in enumerate(bm.verts)} if st.fn else None
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            u = (oi, fi)
            ls = list(f.loops)
            ps = [pos(l, inv) for l in ls]
            st.add_face(oi, fi, f, ps, vidx)
            key = tuple(sorted(ps))
            key_of[u] = key
            groups.setdefault(key, []).append(u)
            face_of[u] = f
            if all(esel(l) for l in ls):
                sel.add(u)
    return _Model(key_of, groups, sel, _apply_faces(entries, face_of, st), key_of)


def _model_island(entries, inv):
    """UV sync OFF, island mode. Unit = face; group = UV island plus every island
    that shares an identical face footprint with it (stacked / mirrored islands)."""
    dsu = _DSU()
    seen_v, seen_sig, face_of, sel, geo = {}, {}, {}, set(), {}
    for oi, (me, bm, acc) in enumerate(entries):
        pos, vsel = acc.pos, acc.vsel
        vidx = {v: i for i, v in enumerate(bm.verts)}
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            fu = (oi, fi)
            dsu.add(fu)
            face_of[fu] = f
            ps = []
            all_sel = True
            for l in f.loops:
                p = pos(l, inv)
                ps.append(p)
                _seen_union(dsu, seen_v, (oi, vidx[l.vert], p), fu)
                if all_sel and not vsel(l):
                    all_sel = False
            sig = tuple(sorted(ps))
            geo[fu] = sig
            _seen_union(dsu, seen_sig, sig, fu)
            if all_sel:
                sel.add(fu)
    key_of, groups = _groups_from_dsu(dsu)
    model = _Model(key_of, groups, sel, _apply_faces(entries, face_of), geo)
    cnt = {}
    for g in geo.values():
        cnt[g] = cnt.get(g, 0) + 1
    model.sig_count = cnt       # an island group always has several faces; a real
    return model                # overlap means some face footprint appears twice


def _model_sync(mode, entries, inv):
    """UV sync ON. Unit = mesh vertex / edge / face. Units that share a UV position
    (or footprint) are chained into one overlap cluster."""
    dsu = _DSU()
    seen, obj_of, geo = {}, {}, {}
    for oi, (me, bm, acc) in enumerate(entries):
        pos = acc.pos
        if mode == 'VERTEX':
            idx = {v: i for i, v in enumerate(bm.verts)}
        elif mode == 'EDGE':
            idx = {e: i for i, e in enumerate(bm.edges)}
        for fi, f in enumerate(bm.faces):
            if f.hide:
                continue
            if mode == 'FACE':
                u = (oi, fi)
                key = tuple(sorted(pos(l, inv) for l in f.loops))
                dsu.add(u)
                obj_of[u] = f
                geo[u] = key
                _seen_union(dsu, seen, key, u)
                continue
            for l in f.loops:
                if mode == 'VERTEX':
                    o = l.vert
                    key = pos(l, inv)
                else:
                    o = l.edge
                    a, b = pos(l, inv), pos(l.link_loop_next, inv)
                    key = (a, b) if a <= b else (b, a)
                u = (oi, idx[o])
                if u not in obj_of:
                    dsu.add(u)
                    obj_of[u] = o
                    geo[u] = key
                _seen_union(dsu, seen, key, u)
    key_of, groups = _groups_from_dsu(dsu)
    sel = {u for u, o in obj_of.items() if o.select}

    def apply(to_sel, to_desel):
        touched = set()
        for u in to_desel:
            obj_of[u].select_set(False)
            touched.add(u[0])
        for u in to_sel:
            obj_of[u].select_set(True)
            touched.add(u[0])
        for oi in touched:
            entries[oi][1].select_flush_mode()
        return touched

    return _Model(key_of, groups, sel, apply, geo)


def _build_model(mode, sync, entries, inv, sticky='SHARED_LOCATION'):
    if sync:
        return _model_sync(mode, entries, inv)
    if mode == 'VERTEX':
        return _model_vertex(entries, inv)
    if mode == 'EDGE':
        return _model_edge(entries, inv, sticky)
    if mode == 'FACE':
        return _model_face(entries, inv, sticky)
    return _model_island(entries, inv)


# --------------------------------------------------------------------------
# Core: expand / shrink the selection by overlap groups
# --------------------------------------------------------------------------

def _has_overlap(model, sel):
    """Does anything in the selection actually have an overlapped partner?"""
    if model.sig_count is not None:                       # island mode
        cnt, geo = model.sig_count, model.geo
        return any(cnt.get(geo.get(u), 0) > 1 for u in sel)
    key_of, groups, elem_of = model.key_of, model.groups, model.elem_of
    done = set()
    for u in sel:
        k = key_of.get(u)
        if k is None or k in done:
            continue
        done.add(k)
        members = groups[k]
        if len(members) < 2:
            continue
        if elem_of is None:
            return True
        # The corners of ONE vertex (or the two sides of ONE edge) share a UV spot
        # inside every island - that is not an overlap. It is one only when the
        # spot holds different mesh vertices / edges.
        first = elem_of[members[0]]
        for x in members:
            if elem_of[x] != first:
                return True
    return False


def _set_red(on):
    """Selection turns red only while the selection really contains overlaps."""
    if on == S.red:
        return
    try:
        ctx = bpy.context
        if on:
            _theme_apply_red(ctx)
        else:
            _theme_restore(ctx)
        S.red = on
        _tag_redraw(ctx)
    except Exception:
        pass


def _snapshot(model, sel, topo):
    S.prev = set(sel)
    S.geo = model.geo           # positions of ALL units, to tell moves from selections
    S.topo = topo
    _set_red(_has_overlap(model, sel))


def _process(context):
    # Never touch the mesh while a move / rotate / scale is running - that is what
    # tore the UVs apart. (The "moved" check below is a second safety net.)
    busy = _transform_running(context)
    if busy:
        _set_status(context, "Paused while %s runs" % busy)
        return

    wm = context.window_manager
    ts = context.tool_settings
    sync = bool(ts.use_uv_select_sync)
    mode = _current_mode(ts)
    entries = _collect(context)
    if not entries:
        return
    inv = 1.0 / max(wm.uv_overlap_tolerance, 1e-9)
    topo = (mode, sync, round(inv)) + tuple(
        (len(bm.verts), len(bm.edges), len(bm.faces)) for _me, bm, _acc in entries)

    sticky = getattr(ts, "uv_sticky_select_mode", 'SHARED_LOCATION')
    model = _build_model(mode, sync, entries, inv, sticky)
    now = model.selected
    _update_overlay(context, entries, sync, inv, topo, model)
    if not wm.uv_overlap_active:          # only the Shift wire display is on
        S.prev = None
        return

    if S.prev is None or S.topo != topo:
        prev = set()
    else:
        prev = S.prev
        # If any selected element moved since our last look, the change came from a
        # transform (incl. click-drag), not a plain selection: just take a new snapshot
        # so nothing at the drop position gets grabbed.
        geo_now, geo_old = model.geo, S.geo
        for u in now:
            if geo_now.get(u) != geo_old.get(u):
                _snapshot(model, now, topo)
                _set_status(context, "Selection moved - ready")
                return

    if now == prev:
        _snapshot(model, now, topo)
        if S.status.startswith("Paused"):
            _set_status(context, "Ready")
        return

    key_of, groups = model.key_of, model.groups
    added = now - prev
    removed = prev - now
    keys_add = {key_of[u] for u in added if u in key_of}
    keys_rem = {key_of[u] for u in removed if u in key_of} - keys_add

    # A stack that lost only SOME of its members: Blender's own click select
    # ("deselect all, then select the clicked one") does that to the partners we
    # added, so put them back - unless the user really deselected (Shift / toggle
    # or deselect click), in which case the whole stack goes.
    removing = _last_op_deselects(wm)
    final = set(now)
    for k in keys_rem:
        members = groups[k]
        if not removing and any(u in now for u in members):
            final.update(members)
        else:
            final.difference_update(members)
    for k in keys_add:
        final.update(groups[k])

    to_sel, to_desel = final - now, now - final
    if to_sel or to_desel:
        touched = model.apply(to_sel, to_desel)
        for oi in touched:
            bmesh.update_edit_mesh(entries[oi][0], loop_triangles=False, destructive=False)
        _tag_redraw(context)
        _set_status(context, "Added %d / removed %d overlapped" % (len(to_sel), len(to_desel)))
    else:
        _set_status(context, "Nothing overlapped under the selection")

    _snapshot(model, final, topo)


# --------------------------------------------------------------------------
# Overlay: tint stacked faces (pale green) and overlapping faces (pale blue)
# --------------------------------------------------------------------------

_MAX_OVERLAP_TRIS = 150000      # above this, only stacked faces are detected


def _tri_overlap(t1, t2, eps):
    """2D triangles overlap by more than eps (touching edges / corners don't count)."""
    for tri in (t1, t2):
        for i in range(3):
            x1, y1 = tri[i]
            x2, y2 = tri[(i + 1) % 3]
            nx, ny = y1 - y2, x2 - x1
            ln = (nx * nx + ny * ny) ** 0.5
            if ln < 1e-12:
                continue
            nx /= ln
            ny /= ln
            a0 = nx * t1[0][0] + ny * t1[0][1]
            a1 = nx * t1[1][0] + ny * t1[1][1]
            a2 = nx * t1[2][0] + ny * t1[2][1]
            b0 = nx * t2[0][0] + ny * t2[0][1]
            b1 = nx * t2[1][0] + ny * t2[1][1]
            b2 = nx * t2[2][0] + ny * t2[2][1]
            amin, amax = min(a0, a1, a2), max(a0, a1, a2)
            bmin, bmax = min(b0, b1, b2), max(b0, b1, b2)
            if amax <= bmin + eps or bmax <= amin + eps:
                return False
    return True


def _tri_area2(t):
    return abs((t[1][0] - t[0][0]) * (t[2][1] - t[0][1])
               - (t[2][0] - t[0][0]) * (t[1][1] - t[0][1]))


def _compute_overlay(entries, sync, inv, eps):
    """Returns (stacked_lines, overlap_lines, n_stacked, n_overlap, overlap_checked).
    Lines are pairs of (u, v, 0) points along each face's outline (wire edges)."""
    keys, face_tris, face_rims = [], [], []
    for oi, (me, bm, acc) in enumerate(entries):
        by_face = {}
        for tri in bm.calc_loop_triangles():
            by_face.setdefault(tri[0].face, []).append(tri)
        for f in bm.faces:
            if f.hide or (not sync and not f.select):
                continue
            keys.append(tuple(sorted(acc.pos(l, inv) for l in f.loops)))
            face_tris.append([tuple(acc.raw(l) for l in tri) for tri in by_face.get(f, ())])
            face_rims.append([acc.raw(l) for l in f.loops])

    count = {}
    for k in keys:
        count[k] = count.get(k, 0) + 1
    stacked = [count[k] > 1 for k in keys]
    overlap = [False] * len(keys)

    # Broad phase on whole faces (bounding boxes in a grid), narrow phase on triangles.
    nf = len(keys)
    ntris = sum(len(ts) for ts in face_tris)
    checked = ntris <= _MAX_OVERLAP_TRIS
    if checked and nf > 1:
        bbs = []
        area = 0.0
        for ts in face_tris:
            xs = [p[0] for t in ts for p in t] or [0.0]
            ys = [p[1] for t in ts for p in t] or [0.0]
            bb = (min(xs), min(ys), max(xs), max(ys))
            bbs.append(bb)
            area += (bb[2] - bb[0]) * (bb[3] - bb[1])
        cell = max((area / nf) ** 0.5, 1e-4)
        grid, big = {}, []          # big: list of huge faces
        for i, bb in enumerate(bbs):
            if not face_tris[i]:
                continue
            ix0, iy0 = int(bb[0] // cell), int(bb[1] // cell)
            ix1, iy1 = int(bb[2] // cell), int(bb[3] // cell)
            if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > 256:
                big.append(i)                    # huge face: tested against everyone
                continue
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    grid.setdefault((ix, iy), []).append(i)

        def test(i, j):
            if keys[i] == keys[j] or (overlap[i] and overlap[j]):
                return                           # exact stack twins / already known
            bi, bj = bbs[i], bbs[j]
            if (bi[2] <= bj[0] + eps or bj[2] <= bi[0] + eps or
                    bi[3] <= bj[1] + eps or bj[3] <= bi[1] + eps):
                return
            for ti in face_tris[i]:
                for tj in face_tris[j]:
                    if _tri_area2(ti) > 1e-14 and _tri_area2(tj) > 1e-14 \
                            and _tri_overlap(ti, tj, eps):
                        overlap[i] = overlap[j] = True
                        return

        for (cx, cy), members in grid.items():
            n = len(members)
            for a in range(n):
                i = members[a]
                bi = bbs[i]
                for b in range(a + 1, n):
                    j = members[b]
                    bj = bbs[j]
                    # handle each pair only in the cell holding the lower-left corner
                    # of the two boxes' intersection (no "seen" set needed)
                    if (int(max(bi[0], bj[0]) // cell) != cx or
                            int(max(bi[1], bj[1]) // cell) != cy):
                        continue
                    test(i, j)
        big_set = set(big)
        for i in big:
            for j in range(nf):
                if j != i and face_tris[j] and (j not in big_set or j > i):
                    test(i, j)

    def rim_lines(out, rim):
        n = len(rim)
        for k in range(n):
            a, b = rim[k], rim[(k + 1) % n]
            out.append((a[0], a[1], 0.0))
            out.append((b[0], b[1], 0.0))

    green, blue = [], []
    n_st = n_ov = 0
    for fi, rim in enumerate(face_rims):
        if stacked[fi]:
            n_st += 1
            rim_lines(green, rim)
        elif overlap[fi]:
            n_ov += 1
            rim_lines(blue, rim)
    return green, blue, n_st, n_ov, checked


def _update_overlay(context, entries, sync, inv, topo, model):
    wm = context.window_manager
    if not getattr(wm, "uv_overlap_show", False):
        if S.ov_green or S.ov_blue:
            S.ov_green, S.ov_blue, S.ov_batches, S.ov_sig = [], [], None, None
            _tag_redraw(context)
        return
    sig = (topo, len(model.geo), hash(tuple(model.geo.items())))
    if sig == S.ov_sig:
        return
    eps = max(wm.uv_overlap_tolerance, 1e-9)
    green, blue, n_st, n_ov, checked = _compute_overlay(entries, sync, inv, eps)
    S.ov_green, S.ov_blue = green, blue
    S.ov_counts = (n_st, n_ov, checked)
    S.ov_batches = None          # rebuilt in the draw callback
    S.ov_sig = sig
    _tag_redraw(context)


def _space_is_uv(sd):
    return sd is not None and sd.type == 'IMAGE_EDITOR' and (
        getattr(sd, "mode", None) == 'UV' or bool(getattr(sd, "show_uvedit", False)))


_draw_handle = None


def _draw_overlay():
    if gpu is None:
        return
    try:
        ctx = bpy.context
        wm = ctx.window_manager
        if not wm.uv_overlap_show:
            return
        if not S.ov_green and not S.ov_blue:
            return
        if ctx.mode != 'EDIT_MESH' or not _space_is_uv(ctx.space_data):
            return
        if _transform_running(ctx):
            return                              # stale while moving: hide until the drop
        region = ctx.region
        v2d = region.view2d
        x0, y0 = v2d.view_to_region(0.0, 0.0, clip=False)
        x1, y1 = v2d.view_to_region(10.0, 10.0, clip=False)
        sx, sy = (x1 - x0) / 10.0, (y1 - y0) / 10.0

        width = float(getattr(wm, "uv_overlap_line_width", 1.0))
        # Thick lines: the polyline shader (works on every GPU backend); plain
        # UNIFORM_COLOR + line_width_set as a fallback.
        try:
            shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            poly = True
        except Exception:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            poly = False
        if S.ov_batches is None:
            S.ov_batches = (
                batch_for_shader(shader, 'LINES', {"pos": S.ov_green}) if S.ov_green else None,
                batch_for_shader(shader, 'LINES', {"pos": S.ov_blue}) if S.ov_blue else None,
            )
        b_green, b_blue = S.ov_batches

        gpu.state.blend_set('ALPHA')
        gpu.matrix.push()
        gpu.matrix.translate((x0, y0))
        gpu.matrix.scale((sx, sy))
        shader.bind()
        if poly:
            shader.uniform_float("viewportSize", (region.width, region.height))
            shader.uniform_float("lineWidth", width)
        else:
            gpu.state.line_width_set(width)
        if b_blue is not None:
            shader.uniform_float("color", tuple(wm.uv_overlap_color_overlap))
            b_blue.draw(shader)
        if b_green is not None:
            shader.uniform_float("color", tuple(wm.uv_overlap_color_stacked))
            b_green.draw(shader)
        gpu.matrix.pop()
        gpu.state.blend_set('NONE')
        if not poly:
            gpu.state.line_width_set(1.0)
    except Exception:
        pass


def _overlay_prop_update(self, context):
    S.ov_sig = None
    S.ov_batches = None
    if not getattr(self, "uv_overlap_show", True):
        S.ov_green, S.ov_blue = [], []
    _tag_redraw(context)


def set_overlap_active(context, state):
    wm = context.window_manager
    wm.uv_overlap_active = state
    S.reset()
    if state:
        ts = context.tool_settings
        S.armed = (_current_mode(ts), bool(ts.use_uv_select_sync))
        _theme_restore(context)          # red comes on only when an overlap is selected
        try:
            # Always start a fresh watcher (a new token makes any old one exit)
            bpy.ops.uv.overlap_watch('INVOKE_DEFAULT')
            _process(context)
        except Exception:
            wm.uv_overlap_active = False
            _theme_restore(context)
            raise
    else:
        _theme_restore(context)
    _tag_redraw(context)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class UV_OT_overlap_mode_key(bpy.types.Operator):
    """Select mode key: press the active mode again to toggle overlap selection"""
    bl_idname = "uv.overlap_mode_key"
    bl_label = "UV Select Mode (press again for Overlap)"
    bl_options = {'INTERNAL'}

    mode: EnumProperty(
        name="Mode",
        items=[('VERTEX', "Vertex", ""), ('EDGE', "Edge", ""),
               ('FACE', "Face", ""), ('ISLAND', "Island", "")],
        default='VERTEX',
    )

    @classmethod
    def poll(cls, context):
        return _is_uv_editor(context)

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        ts = context.tool_settings
        wm = context.window_manager
        if ts.use_uv_select_sync and self.mode == 'ISLAND':
            return {'PASS_THROUGH'}          # island mode doesn't exist with sync on
        try:
            if _current_mode(ts) != self.mode:
                # Different mode: let Blender's own 1/2/3/4 binding switch the mode
                # (so it converts the selection the normal way).
                if wm.uv_overlap_active and not wm.uv_overlap_keep:
                    set_overlap_active(context, False)
                return {'PASS_THROUGH'}
            on = not wm.uv_overlap_active
            set_overlap_active(context, on)
            self.report({'INFO'}, "UV Overlap Select %s (%s)"
                        % ("ON" if on else "OFF", self.mode.title()))
        except Exception as ex:
            traceback.print_exc()
            self.report({'ERROR'}, "UV Overlap Select: %s" % ex)
            return {'CANCELLED'}
        return {'FINISHED'}


class UV_OT_overlap_toggle(bpy.types.Operator):
    """Toggle overlap selection for the current UV select mode"""
    bl_idname = "uv.overlap_toggle"
    bl_label = "Toggle Overlap Select"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        try:
            set_overlap_active(context, not context.window_manager.uv_overlap_active)
        except Exception as ex:
            traceback.print_exc()
            self.report({'ERROR'}, "UV Overlap Select: %s" % ex)
            return {'CANCELLED'}
        return {'FINISHED'}


class UV_OT_overlap_wire_toggle(bpy.types.Operator):
    """Show / hide the wire of stacked (green) and overlapping (mauve) faces"""
    bl_idname = "uv.overlap_wire_toggle"
    bl_label = "Toggle Stacked / Overlapping Wire"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        wm = context.window_manager
        on = not wm.uv_overlap_show
        wm.uv_overlap_show = on
        try:
            if on:
                S.ov_sig = None
                if not wm.uv_overlap_active:     # overlap select keeps its own watcher
                    bpy.ops.uv.overlap_watch('INVOKE_DEFAULT')
                _process(context)
        except Exception as ex:
            traceback.print_exc()
            self.report({'ERROR'}, "UV Overlap Select: %s" % ex)
            return {'CANCELLED'}
        self.report({'INFO'}, "Stacked / overlapping wire %s" % ("ON" if on else "OFF"))
        return {'FINISHED'}


def _last_op_deselects(wm):
    """True if the last operator was a deselecting click (toggle / deselect)."""
    try:
        ops = wm.operators
        if not len(ops):
            return False
        kw = ops[-1].as_keywords()
        return bool(kw.get("toggle") or kw.get("deselect"))
    except Exception:
        return False


def _ops_signature(wm):
    """Cheap fingerprint of 'the last operator that ran' to notice selection operators."""
    try:
        ops = wm.operators
        if not len(ops):
            return (0, None, ())
        last = ops[-1]
        try:
            kw = tuple(sorted((k, str(v)) for k, v in last.as_keywords().items()))
        except Exception:
            kw = ()
        return (len(ops), last.bl_idname, kw)
    except Exception:
        return None


class UV_OT_overlap_watch(bpy.types.Operator):
    """Background watcher: re-expands the selection whenever it changes"""
    bl_idname = "uv.overlap_watch"
    bl_label = "UV Overlap Select Watcher"
    bl_options = {'INTERNAL'}

    _timer = None
    _token = 0

    def invoke(self, context, event):
        wm = context.window_manager
        S.token += 1
        self._token = S.token
        S.heartbeat = time.monotonic()
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _stop(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        return {'CANCELLED'}

    def modal(self, context, event):
        wm = context.window_manager
        if self._token != S.token or not (wm.uv_overlap_active or wm.uv_overlap_show):
            return self._stop(context)

        if event.type == 'TIMER':
            now = time.monotonic()
            S.heartbeat = now
            ts = context.tool_settings

            if context.mode != 'EDIT_MESH':
                if wm.uv_overlap_active:
                    set_overlap_active(context, False)
                if not wm.uv_overlap_show:
                    return self._stop(context)
                return {'PASS_THROUGH'}

            armed = (_current_mode(ts), bool(ts.use_uv_select_sync))
            if wm.uv_overlap_active and S.armed is not None and S.armed != armed:
                if wm.uv_overlap_keep:
                    S.armed, S.prev = armed, None
                else:
                    set_overlap_active(context, False)
                    if not wm.uv_overlap_show:
                        return self._stop(context)

            # Blender selects on press AND again on click (release); expanding in
            # between gets undone by the second pass, so wait for the click to end.
            if S.mouse_down:
                if now - S.mouse_time < 10.0:
                    return {'PASS_THROUGH'}
                S.mouse_down = False     # missed release (outside the window)

            sig = _ops_signature(wm)
            if S.dirty:
                due = now - S.dirty_time >= _SETTLE
                if not due:
                    return {'PASS_THROUGH'}
            elif sig != S.ops_sig:
                due = True
            elif sig != S.ops_sig:
                due = True
            else:
                due = now - S.last_check >= S.interval

            if due:
                S.dirty = False
                S.ops_sig = sig
                S.last_check = now
                t0 = time.perf_counter()
                try:
                    _process(context)
                except Exception as ex:
                    traceback.print_exc()
                    set_overlap_active(context, False)
                    self.report({'ERROR'}, "UV Overlap Select stopped: %s" % ex)
                    return self._stop(context)
                S.interval = max(0.4, (time.perf_counter() - t0) * 10.0)

        else:
            if event.type in _MOUSE_BUTTONS:
                if event.value == 'PRESS':
                    S.mouse_down = True
                    S.mouse_time = time.monotonic()
                elif event.value == 'RELEASE':
                    S.mouse_down = False
            if event.value in _ACTIVE_VALUES:
                S.dirty = True
                S.dirty_time = time.monotonic()

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._stop(context)


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

class UV_PT_overlap_select(bpy.types.Panel):
    bl_label = "Overlap Select"
    bl_idname = "UV_PT_overlap_select"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Overlap"

    @classmethod
    def poll(cls, context):
        return _is_uv_editor(context)

    def draw(self, context):
        wm = context.window_manager
        layout = self.layout
        active = wm.uv_overlap_active
        row = layout.row()
        row.scale_y = 1.4
        row.operator("uv.overlap_toggle",
                     text="Overlap Select: ON" if active else "Overlap Select: OFF",
                     icon='SELECT_INTERSECT', depress=active)
        if active:
            layout.label(text=S.status, icon='INFO')
        layout.prop(wm, "uv_overlap_tolerance")
        layout.prop(wm, "uv_overlap_keep")

        box = layout.box()
        show = wm.uv_overlap_show
        box.operator("uv.overlap_wire_toggle",
                     text="Stacked / Overlapping Wire: %s" % ("ON" if show else "OFF"),
                     icon='MOD_WIREFRAME', depress=show)
        sub = box.column()
        sub.enabled = show
        row = sub.row()
        row.prop(wm, "uv_overlap_color_stacked", text="Stacked")
        row = sub.row()
        row.prop(wm, "uv_overlap_color_overlap", text="Overlapping")
        sub.prop(wm, "uv_overlap_line_width")
        if show:
            n_st, n_ov, checked = S.ov_counts
            sub.label(text="Stacked faces: %d" % n_st)
            sub.label(text=("Overlapping faces: %d" % n_ov) if checked
                      else "Overlapping: mesh too dense, skipped")
        col = layout.column(align=True)
        col.scale_y = 0.85
        col.label(text="1/2/3/4 again on the active")
        col.label(text="mode: overlap select.")
        col.label(text="Shift+1/2/3/4 or Shift+W:")
        col.label(text="wire display.")


class UVOVERLAP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    # Backup of the theme colors, so a session that ends while overlap is on can be repaired
    theme_backup_on: BoolProperty(default=False, options={'HIDDEN'})
    theme_backup: FloatVectorProperty(size=16, default=(0.0,) * 16, options={'HIDDEN'})

    def draw(self, context):
        self.layout.label(text="UV Editor: press 1/2/3/4 again on the active select mode "
                               "to toggle overlap selection.")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

@persistent
def _on_load_post(_dummy):
    S.reset()
    S.heartbeat = 0.0
    S.token += 1
    try:
        bpy.context.window_manager.uv_overlap_active = False
        bpy.context.window_manager.uv_overlap_show = False
        _theme_restore(bpy.context)
    except Exception:
        pass


_classes = (
    UVOVERLAP_Prefs,
    UV_OT_overlap_mode_key,
    UV_OT_overlap_toggle,
    UV_OT_overlap_wire_toggle,
    UV_OT_overlap_watch,
    UV_PT_overlap_select,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)

    wm_t = bpy.types.WindowManager
    wm_t.uv_overlap_active = BoolProperty(default=False, options={'SKIP_SAVE'})
    wm_t.uv_overlap_keep = BoolProperty(
        name="Keep On When Changing Mode",
        description="Stay in overlap mode when switching between vertex/edge/face/island",
        default=False)
    wm_t.uv_overlap_tolerance = FloatProperty(
        name="Tolerance",
        description="Max UV distance for two elements to count as overlapped",
        default=0.0001, min=1e-7, max=0.01, precision=6)
    wm_t.uv_overlap_show = BoolProperty(
        name="Show Stacked / Overlapping Wire",
        description="Draw the wire of stacked faces (identical UV footprint, green) and "
                    "overlapping faces (partly covering another face, mauve). Shift+1/2/3/4 or Shift+W",
        default=False, options={'SKIP_SAVE'}, update=_overlay_prop_update)
    wm_t.uv_overlap_color_stacked = FloatVectorProperty(
        name="Stacked Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.296357, 0.676667, 0.302632, 1.0), update=_overlay_prop_update)
    wm_t.uv_overlap_color_overlap = FloatVectorProperty(
        name="Overlapping Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.062876, 0.705365, 1.0), update=_overlay_prop_update)
    wm_t.uv_overlap_line_width = FloatProperty(
        name="Line Width", description="Thickness of the stacked / overlapping wire",
        default=1.0, min=0.5, max=8.0, step=10, precision=1, update=_overlay_prop_update)

    global _draw_handle
    if gpu is not None and _draw_handle is None:
        _draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            _draw_overlay, (), 'WINDOW', 'POST_PIXEL')

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc:
        km = kc.keymaps.new(name="UV Editor", space_type='EMPTY', region_type='WINDOW')
        for key, mode in (('ONE', 'VERTEX'), ('TWO', 'EDGE'), ('THREE', 'FACE'), ('FOUR', 'ISLAND')):
            kmi = km.keymap_items.new("uv.overlap_mode_key", key, 'PRESS')
            kmi.properties.mode = mode
            _addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new("uv.overlap_wire_toggle", key, 'PRESS', shift=True)
            _addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new("uv.overlap_wire_toggle", 'W', 'PRESS', shift=True)
        _addon_keymaps.append((km, kmi))

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    try:
        _theme_restore(bpy.context)      # repair a theme left red by an earlier session
    except Exception:
        pass


def unregister():
    S.token += 1                          # makes any running watcher stop
    try:
        bpy.context.window_manager.uv_overlap_active = False
        _theme_restore(bpy.context)
    except Exception:
        pass

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None

    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()

    wm_t = bpy.types.WindowManager
    for name in ("uv_overlap_active", "uv_overlap_keep", "uv_overlap_tolerance",
                 "uv_overlap_show", "uv_overlap_color_stacked", "uv_overlap_color_overlap",
                 "uv_overlap_line_width"):
        if hasattr(wm_t, name):
            delattr(wm_t, name)

    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
