bl_info = {
    "name": "Active Node Cycle",
    "author": "Zafio",
    "version": (1, 1, 0),
    "blender": (4, 5, 0),
    "location": "Shader Editor > Tab",
    "description": "Press Tab to cycle the active node through the currently selected nodes. "
                   "If the new active node has no connections, it takes over the previous "
                   "active node's connections.",
    "category": "Node",
}

import bpy


# ------------------------------------------------------------------------
# Moving connections
# ------------------------------------------------------------------------

def node_links(tree, node):
    return [link for link in tree.links if link.from_node == node or link.to_node == node]


def matching_socket(src_node, socket, dst_node, is_output):
    """Socket of dst_node that corresponds to `socket` of src_node: same identifier, else same
    name, else same position with the same type. None if nothing fits."""
    src = src_node.outputs if is_output else src_node.inputs
    dst = list(dst_node.outputs if is_output else dst_node.inputs)
    usable = [s for s in dst if getattr(s, "enabled", True)]
    for key in ("identifier", "name"):
        for s in usable:
            if getattr(s, key) == getattr(socket, key):
                return s
    index = list(src).index(socket)
    if index < len(dst) and dst[index].type == socket.type and getattr(dst[index], "enabled", True):
        return dst[index]
    return None


def move_links(tree, old_node, new_node):
    """Re-create old_node's links on new_node (matching sockets), removing them from old_node.
    Links with no matching socket stay where they are. Returns (moved, kept)."""
    plan = []
    for link in node_links(tree, old_node):
        if link.from_node == old_node:
            sock = matching_socket(old_node, link.from_socket, new_node, True)
            plan.append((link, (sock, link.to_socket) if sock else None))
        else:
            sock = matching_socket(old_node, link.to_socket, new_node, False)
            plan.append((link, (link.from_socket, sock) if sock else None))
    moved = kept = 0
    for link, target in plan:
        if target is None:
            kept += 1
            continue
        from_socket, to_socket = target
        tree.links.remove(link)
        for s in (from_socket, to_socket):
            if s.hide:
                s.hide = False
        tree.links.new(from_socket, to_socket)
        moved += 1
    return moved, kept


class NODE_OT_cycle_active_node(bpy.types.Operator):
    """Cycle the active node through the currently selected nodes"""
    bl_idname = "node.cycle_active_node"
    bl_label = "Cycle Active Node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        space = context.space_data

        # Only operate in a Shader Editor
        if space is None or space.type != 'NODE_EDITOR':
            return False

        # Only operate on shader node trees
        if space.tree_type != 'ShaderNodeTree':
            return False

        node_tree = space.edit_tree
        if node_tree is None:
            return False

        # Need at least two selected nodes to cycle
        return len([n for n in node_tree.nodes if n.select]) >= 2

    def execute(self, context):
        space = context.space_data
        node_tree = space.edit_tree

        # Get selected nodes in a deterministic order.
        # Sorting by name means cycling remains predictable.
        selected = sorted(
            [node for node in node_tree.nodes if node.select],
            key=lambda node: node.name
        )

        if len(selected) < 2:
            return {'CANCELLED'}

        active = node_tree.nodes.active

        if active not in selected:
            # If the active node isn't selected, start with the first one.
            next_node = selected[0]
        else:
            current_index = selected.index(active)
            next_node = selected[(current_index + 1) % len(selected)]

        # The newly active node has no connections and the previous one has:
        # it takes over the connections (e.g. Tab between textures swaps which one is wired)
        if (active is not None and active in selected and active != next_node
                and node_links(node_tree, active) and not node_links(node_tree, next_node)):
            moved, kept = move_links(node_tree, active, next_node)
            if kept:
                self.report({'WARNING'}, f"{kept} connection(s) had no matching socket on "
                                         f"'{next_node.name}' and stayed on '{active.name}'")

        node_tree.nodes.active = next_node

        return {'FINISHED'}


# ------------------------------------------------------------------------
# Keymap
# ------------------------------------------------------------------------

addon_keymaps = []


def register_keymap():
    wm = bpy.context.window_manager

    # Shader Editor keymap
    kc = wm.keyconfigs.addon

    if kc is None:
        return

    km = kc.keymaps.new(
        name='Node Editor',
        space_type='NODE_EDITOR',
        region_type='WINDOW',
    )

    kmi = km.keymap_items.new(
        NODE_OT_cycle_active_node.bl_idname,
        type='TAB',
        value='PRESS',
    )

    addon_keymaps.append((km, kmi))


def unregister_keymap():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass

    addon_keymaps.clear()


classes = (
    NODE_OT_cycle_active_node,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    register_keymap()


def unregister():
    unregister_keymap()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()