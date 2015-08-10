bl_info = {
    'name': 'Frame hotkeys',
    'author': 'Julio Iglesias, Adrian Novoa',
    'version': (0, 21),
    'blender': (2, 75, 2),
    'location': '1/2 (framerate), W/E (previous/next frame), Q (toggle preview range) at Timeline, Graph & Dopesheet editors',
    'warning': '',
    'description': 'Adds hotkeys to Increase/Decrease Framerate, Previous/Next Frame and toggle Preview Range',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Render'}

import bpy

# Increases framerate by 5 or the closest value to make it multiple of 5
class RENDER_OT_set_fps_increase(bpy.types.Operator):
    '''FPS Increase'''
    bl_idname = "render.set_fps_increase"
    bl_label = "FPS increase"

    def execute(self, context):

        scene = bpy.context.scene

        fps = int(scene.render.fps)

        if context.area.type != 'VIEW_3D' and fps % 5 == 0:
            scene.render.fps += 5

        elif context.area.type != 'VIEW_3D' and fps % 5 != 0:
            mod = fps % 5
            toAdd = 5 - mod
            scene.render.fps += toAdd

        return {'FINISHED'}

# Decreases framerate by 5 or the closest value to make it multiple of 5
class RENDER_OT_set_fps_decrease(bpy.types.Operator):
    '''FPS decrease'''
    bl_idname = "render.set_fps_decrease"
    bl_label = "FPS decrease"

    def execute(self, context):

        scene = bpy.context.scene

        fps = int(scene.render.fps)

        if context.area.type != 'VIEW_3D' and fps % 5 == 0:
            scene.render.fps -= 5

        elif context.area.type != 'VIEW_3D' and fps % 5 != 0:
            mod = fps % 5
            scene.render.fps -= mod

        return {'FINISHED'}

# Toggles preview range at timeline ON or OFF
class TIMELINE_OT_toggle_preview_range(bpy.types.Operator):
    '''Toggle Preview Range'''
    bl_idname = "timeline.toggle_preview_range"
    bl_label = "Toggle Preview Range"

    def execute(self, context):

        scene = bpy.context.scene

        if context.area.type != 'VIEW_3D' and scene.use_preview_range == True:
            scene.use_preview_range = False

        elif context.area.type != 'VIEW_3D' and scene.use_preview_range == False:
            scene.use_preview_range = True

        return {'FINISHED'}

#Jumps to next frame, or first frame when current frame equals to end frame
class TIMELINE_OT_next_frame(bpy.types.Operator):
    '''Next Frame'''
    bl_idname = "timeline.next_frame"
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

#Jumps to previous frame
class TIMELINE_OT_previous_frame(bpy.types.Operator):
    '''Next Frame'''
    bl_idname = "timeline.previous_frame"
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

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Graph Editor", space_type="GRAPH_EDITOR")
        kmi = km.keymap_items.new('render.set_fps_increase', 'THREE', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Dopesheet", space_type="DOPESHEET_EDITOR")
        kmi = km.keymap_items.new('render.set_fps_increase', 'THREE', 'PRESS')

    bpy.utils.register_class(RENDER_OT_set_fps_decrease)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('render.set_fps_decrease', 'TWO', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Graph Editor", space_type="GRAPH_EDITOR")
        kmi = km.keymap_items.new('render.set_fps_decrease', 'TWO', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Dopesheet", space_type="DOPESHEET_EDITOR")
        kmi = km.keymap_items.new('render.set_fps_decrease', 'TWO', 'PRESS')

    bpy.utils.register_class(TIMELINE_OT_toggle_preview_range)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('timeline.toggle_preview_range', 'Q', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Graph Editor", space_type="GRAPH_EDITOR")
        kmi = km.keymap_items.new('timeline.toggle_preview_range', 'Q', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Dopesheet", space_type="DOPESHEET_EDITOR")
        kmi = km.keymap_items.new('timeline.toggle_preview_range', 'Q', 'PRESS')

    bpy.utils.register_class(TIMELINE_OT_next_frame)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('timeline.next_frame', 'E', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Graph Editor", space_type="GRAPH_EDITOR")
        kmi = km.keymap_items.new('timeline.next_frame', 'E', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Dopesheet", space_type="DOPESHEET_EDITOR")
        kmi = km.keymap_items.new('timeline.next_frame', 'E', 'PRESS')

    bpy.utils.register_class(TIMELINE_OT_previous_frame)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Timeline", space_type="TIMELINE")
        kmi = km.keymap_items.new('timeline.previous_frame', 'W', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Graph Editor", space_type="GRAPH_EDITOR")
        kmi = km.keymap_items.new('timeline.previous_frame', 'W', 'PRESS')

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Dopesheet", space_type="DOPESHEET_EDITOR")
        kmi = km.keymap_items.new('timeline.previous_frame', 'W', 'PRESS')

def unregister():
    bpy.utils.unregister_class(RENDER_OT_set_fps_increase)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_increase':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(RENDER_OT_set_fps_increase)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Graph Editor"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_increase':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(RENDER_OT_set_fps_increase)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Dopesheet"]
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

    bpy.utils.unregister_class(RENDER_OT_set_fps_decrease)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Graph Editor"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_decrease':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(RENDER_OT_set_fps_decrease)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Dopesheet"]
        for kmi in km.keymap_items:
            if kmi.idname == 'render.set_fps_decrease':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_toggle_preview_range)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.toggle_preview_range':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_toggle_preview_range)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Graph Editor"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.toggle_preview_range':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_toggle_preview_range)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Dopesheet"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.toggle_preview_range':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_next_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_next_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Graph Editor"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_next_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Dopesheet"]
        for kmi in km.keymap_items:
            if kmi.idname == 'timeline.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_previous_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Timeline"]
        for kmi in km.keymap_items:
            if kmi.idname == 'previous.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_previous_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Graph Editor"]
        for kmi in km.keymap_items:
            if kmi.idname == 'previous.next_frame':
                km.keymap_items.remove(kmi)
                break

    bpy.utils.unregister_class(TIMELINE_OT_previous_frame)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps["Dopesheet"]
        for kmi in km.keymap_items:
            if kmi.idname == 'previous.next_frame':
                km.keymap_items.remove(kmi)
                break

if __name__ == "__main__":
    register()
