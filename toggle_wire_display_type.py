import bpy

bl_info = {
    "name": "Wire Toggle (OBJ display TYPE)",
    "author": "Zafio",
    "version": (1, 2),
    "blender": (4, 5, 0),
    "location": "3D Viewport: W / Alt + W",
    "description": "Toggles object display type (Wire/Textured) and wire over solid",
    "warning": "",
    "doc_url": "",
    "tracker_url": "",
    "category": "3D View",
}

# (keymap, keymap_item) pairs created by this add-on, so we only ever remove our own.
addon_keymaps = []


class OBJECT_OT_displaytype_toggle(bpy.types.Operator):
    """Toggle wire display type"""
    bl_idname = "object.displaytype_toggle"
    bl_label = "Toggle Wire Display Type"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        ob = context.active_object
        ob.display_type = 'TEXTURED' if ob.display_type == 'WIRE' else 'WIRE'
        return {'FINISHED'}


class OBJECT_OT_wire_toggle(bpy.types.Operator):
    """Toggle wire over solid"""
    bl_idname = "object.wire_toggle"
    bl_label = "Toggle Wire over Solid"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        ob = context.active_object
        ob.show_wire = not ob.show_wire
        return {'FINISHED'}


classes = (
    OBJECT_OT_displaytype_toggle,
    OBJECT_OT_wire_toggle,
)


def _remove_keymaps():
    """Remove only the keymap items this add-on created."""
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            # Item already gone (e.g. keymap was reset by the user).
            pass
    addon_keymaps.clear()


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Make sure a previous registration (script re-run, failed unregister)
    # can never leave duplicate entries behind.
    _remove_keymaps()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        for idname, alt in (
            ("object.displaytype_toggle", False),
            ("object.wire_toggle", True),
        ):
            kmi = km.keymap_items.new(idname, 'W', 'PRESS', alt=alt)
            addon_keymaps.append((km, kmi))


def unregister():
    _remove_keymaps()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
