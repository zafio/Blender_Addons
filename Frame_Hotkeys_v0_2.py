bl_info = {
    'name': 'Frame hotkeys',
    'author': 'Julio Iglesias',
    'version': (0, 2),
    'blender': (2, 75, 2),
    'location': '1/2 (framerate), W/E (previous/next frame) at Timeline',
    'warning': '',
    'description': 'Adds hotkeys to Increase/Decrease Framerate & Previous/Next Frame',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Render'}

import bpy

class RENDER_OT_set_fps_increase(bpy.types.Operator):
    '''FPS Increase'''
    bl_idname = "render.set_fps_increase"
    bl_label = "FPS increase"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type != 'VIEW_3D' and scene.render.fps == 24:
            scene.render.fps += 1

        elif context.area.type != 'VIEW_3D' and scene.render.fps == 20:
            scene.render.fps += 5

        elif context.area.type != 'VIEW_3D' and scene.render.fps >= 25:
            scene.render.fps += 5

        elif context.area.type != 'VIEW_3D' and scene.render.fps <= 1:
            scene.render.fps += 4

        elif context.area.type != 'VIEW_3D' and scene.render.fps <= 20:
            scene.render.fps += 5

        return {'FINISHED'}

class RENDER_OT_set_fps_decrease(bpy.types.Operator):
    '''FPS decrease'''
    bl_idname = "render.set_fps_decrease"
    bl_label = "FPS decrease"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type != 'VIEW_3D' and scene.render.fps == 24:
            scene.render.fps -= 4

        elif context.area.type != 'VIEW_3D' and scene.render.fps == 25:
            scene.render.fps -= 5

        elif context.area.type != 'VIEW_3D' and scene.render.fps <= 20:
            scene.render.fps -= 5

        elif context.area.type != 'VIEW_3D' and scene.render.fps >= 30:
            scene.render.fps -= 5

        return {'FINISHED'}

class SCREEN_OT_next_frame(bpy.types.Operator):
    '''Next Frame'''
    bl_idname = "screen.next_frame"
    bl_label = "Next Frame"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type != 'VIEW_3D' and scene.frame_current == scene.frame_end and scene.use_preview_range == False:
            scene.frame_current = scene.frame_start

        elif context.area.type != 'VIEW_3D' and scene.frame_current == scene.frame_preview_end and scene.use_preview_range == True:
            scene.frame_current = scene.frame_preview_start

        elif context.area.type != 'VIEW_3D' and scene.frame_current <= scene.frame_end and scene.use_preview_range == False:
            scene.frame_current += 1

        elif context.area.type != 'VIEW_3D' and scene.frame_current <= scene.frame_preview_end and scene.use_preview_range == True:
            scene.frame_current += 1

        return {'FINISHED'}

class SCREEN_OT_previous_frame(bpy.types.Operator):
    '''Next Frame'''
    bl_idname = "screen.previous_frame"
    bl_label = "Previous Frame"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type != 'VIEW_3D' and scene.frame_current == scene.frame_start and scene.use_preview_range == False:
            scene.frame_current = scene.frame_end

        elif context.area.type != 'VIEW_3D' and scene.frame_current == scene.frame_preview_start and scene.use_preview_range == True:
            scene.frame_current = scene.frame_preview_end

        elif context.area.type != 'VIEW_3D' and scene.frame_current >= scene.frame_start and scene.use_preview_range == False:
            scene.frame_current -= 1

        elif context.area.type != 'VIEW_3D' and scene.frame_current >= scene.frame_preview_start and scene.use_preview_range == True:
            scene.frame_current -= 1

        return {'FINISHED'}

def register():
    bpy.utils.register_class(RENDER_OT_set_fps_increase)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('render.set_fps_increase', 'THREE', 'PRESS')

    bpy.utils.register_class(RENDER_OT_set_fps_decrease)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('render.set_fps_decrease', 'TWO', 'PRESS')

    bpy.utils.register_class(SCREEN_OT_next_frame)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('screen.next_frame', 'E', 'PRESS')


    bpy.utils.register_class(SCREEN_OT_previous_frame)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('screen.previous_frame', 'W', 'PRESS')

def unregister():
    bpy.utils.unregister_class(RENDER_OT_set_fps_increase)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_increase':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(RENDER_OT_set_fps_decrease)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_decrease':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(SCREEN_OT_next_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'screen.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(SCREEN_OT_previous_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'previous.next_frame':
                km.keymap_items.remove(kmi)
                break

if __name__ == "__main__":
    register()
