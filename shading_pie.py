# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name" : "ShadingPieZ",
    "author" : "Zafio", 
    "description" : "Custom Shading Pie",
    "blender" : (3, 0, 0),
    "version" : (2, 0, 1),
    "location" : "Z key",
    "warning" : "",
    "doc_url": "", 
    "tracker_url": "", 
    "category" : "3D View" 
}


import bpy
import bpy.utils.previews


addon_keymaps = {}
_icons = None
shadingpiez = {'sna_backfacebool': False, 'sna_faceorientbool': False, }
class SNA_OT_Toggle_Overlays_519F3(bpy.types.Operator):
    bl_idname = "sna.toggle_overlays_519f3"
    bl_label = "toggle_overlays"
    bl_description = "Toggle Overlays"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        if bpy.context.area.spaces[0].overlay.show_overlays:
            bpy.context.area.spaces[0].overlay.show_overlays = False
        else:
            bpy.context.area.spaces[0].overlay.show_overlays = True
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Mat_Shad_E3C82(bpy.types.Operator):
    bl_idname = "sna.set_mat_shad_e3c82"
    bl_label = "Set_Mat_Shad"
    bl_description = "Set Material Shad"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'MATERIAL'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Render_Shad_02048(bpy.types.Operator):
    bl_idname = "sna.set_render_shad_02048"
    bl_label = "Set_Render_Shad"
    bl_description = "Set Render Shad"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'RENDERED'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Backfacecull_8E93F(bpy.types.Operator):
    bl_idname = "sna.toggle_backfacecull_8e93f"
    bl_label = "Toggle_BackfaceCull"
    bl_description = "Toggle Backface Cull"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        if bpy.context.area.spaces[0].shading.show_backface_culling:
            bpy.context.area.spaces[0].shading.show_backface_culling = False
            shadingpiez['sna_backfacebool'] = False
        else:
            bpy.context.area.spaces[0].shading.show_backface_culling = True
            shadingpiez['sna_backfacebool'] = True
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Faceorient_Def8B(bpy.types.Operator):
    bl_idname = "sna.toggle_faceorient_def8b"
    bl_label = "Toggle_FaceOrient"
    bl_description = "ToggleFaceOrient"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        if bpy.context.area.spaces[0].overlay.show_face_orientation:
            bpy.context.area.spaces[0].overlay.show_face_orientation = False
            shadingpiez['sna_faceorientbool'] = False
        else:
            bpy.context.area.spaces[0].overlay.show_face_orientation = True
            shadingpiez['sna_faceorientbool'] = True
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_MT_E8F7C(bpy.types.Menu):
    bl_idname = "SNA_MT_E8F7C"
    bl_label = "Shading"

    @classmethod
    def poll(cls, context):
        return not (False)

    def draw(self, context):
        layout = self.layout.menu_pie()
        op = layout.operator('sna.set_wireframe_shad_5cd18', text='Wireframe', icon_value=625, emboss=True, depress=False)
        row_519A8 = layout.row(heading='', align=True)
        row_519A8.alert = False
        row_519A8.enabled = True
        row_519A8.active = True
        row_519A8.use_property_split = False
        row_519A8.use_property_decorate = False
        row_519A8.scale_x = 0.949999988079071
        row_519A8.scale_y = 1.5
        row_519A8.alignment = 'Expand'.upper()
        if not True: row_519A8.operator_context = "EXEC_DEFAULT"
        op = row_519A8.operator('sna.set_solid_shad_a809d', text='Solid', icon_value=0, emboss=True, depress=False)
        op = row_519A8.operator('sna.set_solid_vcol_75d4a', text='SVcol', icon_value=0, emboss=True, depress=False)
        op = row_519A8.operator('sna.set_matcap_94fb0', text='Matcap', icon_value=0, emboss=True, depress=False)
        op = layout.operator('view3d.toggle_xray', text='Xray', icon_value=630, emboss=True, depress=False)
        op = layout.operator('sna.toggle_overlays_519f3', text='Overlays', icon_value=630, emboss=True, depress=False)
        op = layout.operator('sna.set_mat_shad_e3c82', text='Material', icon_value=79, emboss=True, depress=False)
        op = layout.operator('sna.set_render_shad_02048', text='Render', icon_value=627, emboss=True, depress=False)
        col_6944D = layout.column(heading='asdasd', align=True)
        col_6944D.alert = False
        col_6944D.enabled = True
        col_6944D.active = True
        col_6944D.use_property_split = False
        col_6944D.use_property_decorate = True
        col_6944D.scale_x = 1.0
        col_6944D.scale_y = 1.25
        col_6944D.alignment = 'Expand'.upper()
        if not True: col_6944D.operator_context = "EXEC_DEFAULT"
        op = col_6944D.operator('sna.toggle_backfacecull_8e93f', text='BackFaceCull', icon_value=0, emboss=True, depress=shadingpiez['sna_backfacebool'])
        op = col_6944D.operator('sna.toggle_faceorient_def8b', text='FaceOrient', icon_value=0, emboss=True, depress=shadingpiez['sna_faceorientbool'])
        row_C18EE = layout.row(heading='', align=True)
        row_C18EE.alert = False
        row_C18EE.enabled = True
        row_C18EE.active = True
        row_C18EE.use_property_split = False
        row_C18EE.use_property_decorate = False
        row_C18EE.scale_x = 1.0
        row_C18EE.scale_y = 1.5
        row_C18EE.alignment = 'Expand'.upper()
        if not True: row_C18EE.operator_context = "EXEC_DEFAULT"
        op = row_C18EE.operator('sna.set_flat_shad_0e168', text='Flat', icon_value=0, emboss=True, depress=False)
        op = row_C18EE.operator('sna.set_vertex_color_50c8a', text='Vcol', icon_value=0, emboss=True, depress=False)


class SNA_OT_Set_Vertex_Color_50C8A(bpy.types.Operator):
    bl_idname = "sna.set_vertex_color_50c8a"
    bl_label = "Set_Vertex_Color"
    bl_description = "Set vertex color"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'SOLID'
        bpy.context.area.spaces[0].shading.light = 'FLAT'
        bpy.context.area.spaces[0].shading.color_type = 'VERTEX'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Flat_Shad_0E168(bpy.types.Operator):
    bl_idname = "sna.set_flat_shad_0e168"
    bl_label = "Set_Flat_Shad"
    bl_description = "SetFlatShad"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'SOLID'
        bpy.context.area.spaces[0].shading.light = 'FLAT'
        bpy.context.area.spaces[0].shading.color_type = 'TEXTURE'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Matcap_94Fb0(bpy.types.Operator):
    bl_idname = "sna.set_matcap_94fb0"
    bl_label = "Set_Matcap"
    bl_description = "Set Matcap"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'SOLID'
        bpy.context.area.spaces[0].shading.light = 'MATCAP'
        bpy.context.area.spaces[0].shading.color_type = 'MATERIAL'
        exec("bpy.context.space_data.shading.studio_light = 'clay_brown.exr'")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Wireframe_Shad_5Cd18(bpy.types.Operator):
    bl_idname = "sna.set_wireframe_shad_5cd18"
    bl_label = "Set_Wireframe_Shad"
    bl_description = ""
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'WIREFRAME'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Solid_Shad_A809D(bpy.types.Operator):
    bl_idname = "sna.set_solid_shad_a809d"
    bl_label = "Set_Solid_Shad"
    bl_description = "Set Solid"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'SOLID'
        bpy.context.area.spaces[0].shading.light = 'STUDIO'
        bpy.context.area.spaces[0].shading.color_type = 'MATERIAL'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Set_Solid_Vcol_75D4A(bpy.types.Operator):
    bl_idname = "sna.set_solid_vcol_75d4a"
    bl_label = "Set_Solid_Vcol"
    bl_description = "Set Solid & Vcol"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.area.spaces[0].shading.type = 'SOLID'
        bpy.context.area.spaces[0].shading.light = 'STUDIO'
        bpy.context.area.spaces[0].shading.color_type = 'VERTEX'
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


def register():
    global _icons
    _icons = bpy.utils.previews.new()
    bpy.utils.register_class(SNA_OT_Toggle_Overlays_519F3)
    bpy.utils.register_class(SNA_OT_Set_Mat_Shad_E3C82)
    bpy.utils.register_class(SNA_OT_Set_Render_Shad_02048)
    bpy.utils.register_class(SNA_OT_Toggle_Backfacecull_8E93F)
    bpy.utils.register_class(SNA_OT_Toggle_Faceorient_Def8B)
    bpy.utils.register_class(SNA_MT_E8F7C)
    bpy.utils.register_class(SNA_OT_Set_Vertex_Color_50C8A)
    bpy.utils.register_class(SNA_OT_Set_Flat_Shad_0E168)
    bpy.utils.register_class(SNA_OT_Set_Matcap_94Fb0)
    bpy.utils.register_class(SNA_OT_Set_Wireframe_Shad_5Cd18)
    bpy.utils.register_class(SNA_OT_Set_Solid_Shad_A809D)
    bpy.utils.register_class(SNA_OT_Set_Solid_Vcol_75D4A)
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
    kmi = km.keymap_items.new('wm.call_menu_pie', 'Z', 'PRESS',
        ctrl=False, alt=False, shift=False, repeat=False)
    kmi.properties.name = 'SNA_MT_E8F7C'
    addon_keymaps['AE749'] = (km, kmi)


def unregister():
    global _icons
    bpy.utils.previews.remove(_icons)
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    for km, kmi in addon_keymaps.values():
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    bpy.utils.unregister_class(SNA_OT_Toggle_Overlays_519F3)
    bpy.utils.unregister_class(SNA_OT_Set_Mat_Shad_E3C82)
    bpy.utils.unregister_class(SNA_OT_Set_Render_Shad_02048)
    bpy.utils.unregister_class(SNA_OT_Toggle_Backfacecull_8E93F)
    bpy.utils.unregister_class(SNA_OT_Toggle_Faceorient_Def8B)
    bpy.utils.unregister_class(SNA_MT_E8F7C)
    bpy.utils.unregister_class(SNA_OT_Set_Vertex_Color_50C8A)
    bpy.utils.unregister_class(SNA_OT_Set_Flat_Shad_0E168)
    bpy.utils.unregister_class(SNA_OT_Set_Matcap_94Fb0)
    bpy.utils.unregister_class(SNA_OT_Set_Wireframe_Shad_5Cd18)
    bpy.utils.unregister_class(SNA_OT_Set_Solid_Shad_A809D)
    bpy.utils.unregister_class(SNA_OT_Set_Solid_Vcol_75D4A)
