import bpy

bl_info = {
    "name": "Wire Toggle (OJB display TYPE)",
    "author": "Various",
    "version": (1, 1),
    "blender": (2, 80, 0),
    "location": "W & ALT + W",
    "description": "Toggles wire object display type and over solid",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "View3D"
    }

class OBJECT_OT_displaytype_toggle(bpy.types.Operator):
    '''Toggle wire display type'''
    bl_idname = "object.displaytype_toggle"
    bl_label = "Toggle Wire Display Type"

    @classmethod
    def poll(cls, context):
        return context.active_object

    def execute(self, context):

        ob = context.active_object

        if ob.display_type == 'WIRE':
            ob.display_type = 'TEXTURED'
        else:
            ob.display_type = 'WIRE'

        return {'FINISHED'}


class OBJECT_OT_wire_toggle(bpy.types.Operator):
    '''Toggle wire over solid'''
    bl_idname = "object.wire_toggle"
    bl_label = "Toggle Wire over Solid"

    @classmethod
    def poll(cls, context):
        return context.active_object

    def execute(self, context):

        ob = context.active_object

        if ob.show_wire == False:
            ob.show_wire = True
        else:
            ob.show_wire = False

        return {'FINISHED'}

def register():
    bpy.utils.register_class(OBJECT_OT_displaytype_toggle)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new('object.displaytype_toggle', 'W', 'PRESS', alt=False)

    bpy.utils.register_class(OBJECT_OT_wire_toggle)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new('object.wire_toggle', 'W', 'PRESS', alt=True)


def unregister():
    bpy.utils.unregister_class(OBJECT_OT_displaytype_toggle)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["3D View"]
        for kmi in km.keymap_items:
            if kmi.idname == 'object.displaytype_toggle':
                km.keymap_items.remove(kmi)


    bpy.utils.unregister_class(OBJECT_OT_wire_toggle)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["3D View"]
        for kmi in km.keymap_items:
            if kmi.idname == 'object.wire_toggle':
                km.keymap_items.remove(kmi)
                break

if __name__ == "__main__":
    register()
