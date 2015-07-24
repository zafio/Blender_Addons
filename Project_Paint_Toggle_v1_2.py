bl_info = {
    'name': 'Project Paint Toggle',
    'author': 'Todd McIntosh, Diego Quevedo, Julio Iglesias',
    'version': (1,2),
    'blender': (2, 75, 1),
    'location': 'Q key in Texture Paint mode',
    'warning': '',
    'description': 'Toggles Occlude, Cull, Normal and changes the cursor color',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Paint'}

import bpy

def main(context):

    area = bpy.context.area
    info =  bpy.data
    paint = bpy.context.tool_settings.image_paint

    if area.type == 'VIEW_3D' and context.mode == 'PAINT_TEXTURE':
        
        
        name = bpy.context.tool_settings.image_paint.brush.name
        
        
        if (paint.use_occlude and paint.use_backface_culling and paint.use_normal_falloff):
            paint.use_occlude = False
            paint.use_backface_culling = False
            paint.use_normal_falloff = False
        
            try:
                info.brushes[name].cursor_color_add= (0,1,0)
            except:
                print("error")
        else:
            paint.use_occlude = True
            paint.use_backface_culling = True
            paint.use_normal_falloff = True
        
            try:
                info.brushes[name].cursor_color_add= (1,1,1)
            except:
                print("error")
            

class opToggleCheckboxes(bpy.types.Operator):
    """Tooltip"""
    bl_idname = "object.toggle_checkboxes"
    bl_label = "Toggle Project Paint Checkboxes"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        main(context)
        return {'FINISHED'}

addon_keymaps = []


def register():
    bpy.utils.register_class(opToggleCheckboxes)

    # handle the keymap
    wm = bpy.context.window_manager
    km = wm.keyconfigs.default.keymaps['Image Paint']
    kmi = km.keymap_items.new(opToggleCheckboxes.bl_idname, 'Q', 'PRESS')
    addon_keymaps.append(km)


def unregister():
    bpy.utils.unregister_class(opToggleCheckboxes)

    # handle the keymap
    wm = bpy.context.window_manager
    for km in addon_keymaps:
        wm.keyconfigs.addon.keymaps.remove(km)
    # clear the list
    del addon_keymaps[:]



if __name__ == "__main__":
    register()