"""
UV Overlap Select  (Blender 4.5)

In the UV Editor, press the key of the select mode you are ALREADY in
(1 = vertex, 2 = edge, 3 = face, 4 = island) to toggle "overlap selection":

    * Whatever you select gets every element that sits exactly on top of it
      (same UV position) selected as well.
    * Deselecting an element deselects its overlapped partners too.
    * While active, the UV selection color turns RED.

Press the same key again to go back to normal selection.
Pressing a different mode key switches mode and turns overlap off
(unless "Keep On When Changing Mode" is enabled in the sidebar panel).

Works with UV Sync Selection on or off (island mode only exists with sync off).
Sidebar: UV Editor > N panel > "Overlap" tab.
"""

bl_info = {
    "name": "UV Overlap Select",
    "author": "Zafio",
    "version": (1, 0, 2),
    "blender": (4, 5, 0),
    "location": "UV Editor: press 1/2/3/4 again on the active mode | N panel > Overlap",
    "description": "Press the active UV select mode key again to also select overlapped "
                   "elements (vertex, edge, face, island). Selection turns red while active.",
    "category": "UV",
}

import time
import traceback

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty


# --------------------------------------------------------------------------
# Constants / global state
# --------------------------------------------------------------------------

_RED = (1.0, 0.10, 0.10)
_THEME_PROPS = ("vertex_select", "edge_select", "face_select", "face_dot")
_MESH_MODE = {'VERTEX': 'VERT', 'EDGE': 'EDGE', 'FACE': 'FACE'}
_ACTIVE_VALUES = {'PRESS', 'RELEASE', 'CLICK', 'DOUBLE_CLICK', 'CLICK_DRAG'}
_MOUSE_BUTTONS = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'PEN', 'ERASER'}

_saved_theme = {}
_addon_keymaps = []

_HAS_LOOPUV = hasattr(bmesh.types, "BMLoopUV") and hasattr(bmesh.types.BMLoopUV, "select")


class _State:
    def __init__(self):
        self.token = 0          # id of the current watcher
        self.heartbeat = 0.0    # last time the watcher ticked
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
        self.mouse_down = False
        self.mouse_time = 0.0


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
    __slots__ = ("key_of", "groups", "selected", "apply", "geo")

    def __init__(self, key_of, groups, selected, apply, geo):
        self.key_of, self.groups, self.selected, self.apply = key_of, groups, selected, apply
        self.geo = geo          # unit -> UV position signature (to detect moves)


_WATCH_IDS = {"UV_OT_overlap_watch", "uv.overlap_watch"}


def _foreign_modal_running(context):
    """True while another modal operator (move, rotate, box select...) is running."""
    win = context.window
    mods = getattr(win, "modal_operators", None) if win else None
    if mods is None:
        return False
    for op in mods:
        if getattr(op, "bl_idname", "") not in _WATCH_IDS:
            return True
    return False


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
    key_of, groups, loop_of, sel = {}, {}, {}, set()
    for oi, (me, bm, acc) in enumerate(entries):
        pos, vsel = acc.pos, acc.vsel
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            for k, l in enumerate(f.loops):
                u = (oi, fi, k)
                key = pos(l, inv)
                key_of[u] = key
                groups.setdefault(key, []).append(u)
                loop_of[u] = l
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

    return _Model(key_of, groups, sel, apply, key_of)


def _model_edge(entries, inv):
    """UV sync OFF, edge mode. Unit = face corner edge, group = same pair of UV positions."""
    key_of, groups, loop_of, sel = {}, {}, {}, set()
    for oi, (me, bm, acc) in enumerate(entries):
        pos, esel = acc.pos, acc.esel
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            for k, l in enumerate(f.loops):
                u = (oi, fi, k)
                a, b = pos(l, inv), pos(l.link_loop_next, inv)
                key = (a, b) if a <= b else (b, a)
                key_of[u] = key
                groups.setdefault(key, []).append(u)
                loop_of[u] = l
                if esel(l):
                    sel.add(u)

    def apply(to_sel, to_desel):
        faces = {}
        for units, state in ((to_desel, False), (to_sel, True)):
            for u in units:
                l = loop_of[u]
                entries[u[0]][2].set_esel(l, state)
                faces[(u[0], u[1])] = l.face
        for (oi, _fi), f in faces.items():
            acc = entries[oi][2]
            for l in f.loops:
                acc.set_vsel(l, acc.esel(l) or acc.esel(l.link_loop_prev))
        return {oi for oi, _fi in faces}

    return _Model(key_of, groups, sel, apply, key_of)


def _apply_faces(entries, face_of):
    def apply(to_sel, to_desel):
        touched = set()
        for units, state in ((to_desel, False), (to_sel, True)):
            for u in units:
                acc = entries[u[0]][2]
                for l in face_of[u].loops:
                    acc.set_vsel(l, state)
                    acc.set_esel(l, state)
                touched.add(u[0])
        return touched
    return apply


def _model_face(entries, inv):
    """UV sync OFF, face mode. Unit = face, group = identical UV footprint."""
    key_of, groups, face_of, sel = {}, {}, {}, set()
    for oi, (me, bm, acc) in enumerate(entries):
        pos, vsel = acc.pos, acc.vsel
        for fi, f in enumerate(bm.faces):
            if f.hide or not f.select:
                continue
            u = (oi, fi)
            ls = list(f.loops)
            key = tuple(sorted(pos(l, inv) for l in ls))
            key_of[u] = key
            groups.setdefault(key, []).append(u)
            face_of[u] = f
            if all(vsel(l) for l in ls):
                sel.add(u)
    return _Model(key_of, groups, sel, _apply_faces(entries, face_of), key_of)


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
    return _Model(key_of, groups, sel, _apply_faces(entries, face_of), geo)


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


def _build_model(mode, sync, entries, inv):
    if sync:
        return _model_sync(mode, entries, inv)
    if mode == 'VERTEX':
        return _model_vertex(entries, inv)
    if mode == 'EDGE':
        return _model_edge(entries, inv)
    if mode == 'FACE':
        return _model_face(entries, inv)
    return _model_island(entries, inv)


# --------------------------------------------------------------------------
# Core: expand / shrink the selection by overlap groups
# --------------------------------------------------------------------------

def _snapshot(model, sel, topo):
    S.prev = set(sel)
    S.geo = model.geo           # positions of ALL units, to tell moves from selections
    S.topo = topo


def _process(context):
    # Never read or write the mesh while something else is working on it
    # (move / rotate / scale / box select / a held mouse button) - touching the
    # mesh mid-transform is what tore the UVs apart.
    if S.mouse_down or _foreign_modal_running(context):
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

    model = _build_model(mode, sync, entries, inv)
    now = model.selected

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
                return

    if now == prev:
        _snapshot(model, now, topo)
        return

    key_of, groups = model.key_of, model.groups
    added = now - prev
    removed = prev - now
    keys_add = {key_of[u] for u in added if u in key_of}
    keys_rem = {key_of[u] for u in removed if u in key_of} - keys_add

    final = set(now)
    for k in keys_rem:
        final.difference_update(groups[k])
    for k in keys_add:
        final.update(groups[k])

    to_sel, to_desel = final - now, now - final
    if to_sel or to_desel:
        touched = model.apply(to_sel, to_desel)
        for oi in touched:
            bmesh.update_edit_mesh(entries[oi][0], loop_triangles=False, destructive=False)
        _tag_redraw(context)

    _snapshot(model, final, topo)


def set_overlap_active(context, state):
    wm = context.window_manager
    wm.uv_overlap_active = state
    S.reset()
    if state:
        ts = context.tool_settings
        S.armed = (_current_mode(ts), bool(ts.use_uv_select_sync))
        _theme_apply_red(context)
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
        if self._token != S.token or not wm.uv_overlap_active:
            return self._stop(context)

        if event.type == 'TIMER':
            now = time.monotonic()
            S.heartbeat = now
            ts = context.tool_settings
            # A release that happened outside the window never reaches us
            if S.mouse_down and now - S.mouse_time > 15.0:
                S.mouse_down = False

            if context.mode != 'EDIT_MESH':
                set_overlap_active(context, False)
                return self._stop(context)

            armed = (_current_mode(ts), bool(ts.use_uv_select_sync))
            if S.armed is not None and S.armed != armed:
                if wm.uv_overlap_keep:
                    S.armed, S.prev = armed, None
                else:
                    set_overlap_active(context, False)
                    return self._stop(context)

            sig = _ops_signature(wm)
            if S.dirty and now - S.dirty_time >= 0.04:
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
        layout.prop(wm, "uv_overlap_tolerance")
        layout.prop(wm, "uv_overlap_keep")
        col = layout.column(align=True)
        col.scale_y = 0.85
        col.label(text="Press 1/2/3/4 again on the")
        col.label(text="active mode to toggle.")


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
        _theme_restore(bpy.context)
    except Exception:
        pass


_classes = (
    UVOVERLAP_Prefs,
    UV_OT_overlap_mode_key,
    UV_OT_overlap_toggle,
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

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc:
        km = kc.keymaps.new(name="UV Editor", space_type='EMPTY', region_type='WINDOW')
        for key, mode in (('ONE', 'VERTEX'), ('TWO', 'EDGE'), ('THREE', 'FACE'), ('FOUR', 'ISLAND')):
            kmi = km.keymap_items.new("uv.overlap_mode_key", key, 'PRESS')
            kmi.properties.mode = mode
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

    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()

    wm_t = bpy.types.WindowManager
    for name in ("uv_overlap_active", "uv_overlap_keep", "uv_overlap_tolerance"):
        if hasattr(wm_t, name):
            delattr(wm_t, name)

    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
