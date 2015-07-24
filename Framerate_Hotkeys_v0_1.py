bl_info = {
    'name': 'Framerate hotkeys',
    'author': 'Julio Iglesias',
    'version': (0.1),
    'blender': (2, 75, 2),
    'location': 'Z & X keys at Timeline',
    'warning': '',
    'description': 'Increases / Decreases Framerate',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Render'}

import bpy

class RENDER_OT_set_fps_increment(bpy.types.Operator):
    '''FPS Increase'''
    bl_idname = "render.set_fps_increase"
    bl_label = "FPS increase"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type == 'TIMELINE' and scene.render.fps == 24:
            scene.render.fps += 1

        elif context.area.type == 'TIMELINE' and scene.render.fps == 20:
            scene.render.fps += 5

        elif context.area.type == 'TIMELINE' and scene.render.fps >= 25:
            scene.render.fps += 5

        elif context.area.type == 'TIMELINE' and scene.render.fps <= 1:
            scene.render.fps += 4

        elif context.area.type == 'TIMELINE' and scene.render.fps <= 20:
            scene.render.fps += 5



        return {'FINISHED'}

class RENDER_OT_set_fps_decrement(bpy.types.Operator):
    '''FPS decrease'''
    bl_idname = "render.set_fps_decrease"
    bl_label = "FPS decrease"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type == 'TIMELINE' and scene.render.fps == 24:
            scene.render.fps -= 4

        elif context.area.type == 'TIMELINE' and scene.render.fps == 25:
            scene.render.fps -= 5

        elif context.area.type == 'TIMELINE' and scene.render.fps <= 20:
            scene.render.fps -= 5

        elif context.area.type == 'TIMELINE' and scene.render.fps >= 30:
            scene.render.fps -= 5

        return {'FINISHED'}

def register():
    bpy.utils.register_class(RENDER_OT_set_fps_increment)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('render.set_fps_increase', 'X', 'PRESS')

    bpy.utils.register_class(RENDER_OT_set_fps_decrement)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('render.set_fps_decrease', 'Z', 'PRESS')

def unregister():
    bpy.utils.unregister_class(RENDER_OT_set_fps_increment)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_increase':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(RENDER_OT_set_fps_decrement)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_decrease':
                km.keymap_items.remove(kmi)
                break



if __name__ == "__main__":
    register()
