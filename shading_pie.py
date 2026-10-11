# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name": "ShadingPieZ",
    "author": "Zafio",
    "description": "Custom Shading Pie",
    "blender": (4, 5, 0),
    "version": (2, 1, 0),
    "location": "Z key",
    "warning": "",
    "doc_url": "",
    "tracker_url": "",
    "category": "3D View",
}


import bpy


# (keymap, keymap_item) pairs created by this add-on.
addon_keymaps = []

PIE_MENU_ID = "SNA_MT_E8F7C"


class _ShadingOperator(bpy.types.Operator):
    """Base class: only runs inside a 3D Viewport."""
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def apply(self, space):
        raise NotImplementedError

    def execute(self, context):
        self.apply(context.space_data)
        return {"FINISHED"}


class SNA_OT_Toggle_Overlays_519F3(_ShadingOperator):
    bl_idname = "sna.toggle_overlays_519f3"
    bl_label = "Toggle Overlays"
    bl_description = "Toggle Overlays"

    def apply(self, space):
        space.overlay.show_overlays = not space.overlay.show_overlays


class SNA_OT_Set_Mat_Shad_E3C82(_ShadingOperator):
    bl_idname = "sna.set_mat_shad_e3c82"
    bl_label = "Material Preview"
    bl_description = "Set Material Preview shading"

    def apply(self, space):
        space.shading.type = 'MATERIAL'


class SNA_OT_Set_Render_Shad_02048(_ShadingOperator):
    bl_idname = "sna.set_render_shad_02048"
    bl_label = "Rendered"
    bl_description = "Set Rendered shading"

    def apply(self, space):
        space.shading.type = 'RENDERED'


class SNA_OT_Toggle_Backfacecull_8E93F(_ShadingOperator):
    bl_idname = "sna.toggle_backfacecull_8e93f"
    bl_label = "Toggle Backface Culling"
    bl_description = "Toggle Backface Culling"

    def apply(self, space):
        space.shading.show_backface_culling = not space.shading.show_backface_culling


class SNA_OT_Toggle_Faceorient_Def8B(_ShadingOperator):
    bl_idname = "sna.toggle_faceorient_def8b"
    bl_label = "Toggle Face Orientation"
    bl_description = "Toggle Face Orientation overlay"

    def apply(self, space):
        space.overlay.show_face_orientation = not space.overlay.show_face_orientation


class SNA_OT_Set_Vertex_Color_50C8A(_ShadingOperator):
    bl_idname = "sna.set_vertex_color_50c8a"
    bl_label = "Flat Vertex Color"
    bl_description = "Flat lighting with vertex color"

    def apply(self, space):
        space.shading.type = 'SOLID'
        space.shading.light = 'FLAT'
        space.shading.color_type = 'VERTEX'


class SNA_OT_Set_Flat_Shad_0E168(_ShadingOperator):
    bl_idname = "sna.set_flat_shad_0e168"
    bl_label = "Flat Texture"
    bl_description = "Flat lighting with texture color"

    def apply(self, space):
        space.shading.type = 'SOLID'
        space.shading.light = 'FLAT'
        space.shading.color_type = 'TEXTURE'


class SNA_OT_Set_Matcap_94Fb0(_ShadingOperator):
    bl_idname = "sna.set_matcap_94fb0"
    bl_label = "Matcap"
    bl_description = "Set Matcap lighting"

    def apply(self, space):
        space.shading.type = 'SOLID'
        space.shading.light = 'MATCAP'
        space.shading.color_type = 'MATERIAL'
        try:
            space.shading.studio_light = 'clay_brown.exr'
        except TypeError:
            # Matcap not available in this install; keep the current one.
            pass


class SNA_OT_Set_Wireframe_Shad_5Cd18(_ShadingOperator):
    bl_idname = "sna.set_wireframe_shad_5cd18"
    bl_label = "Wireframe"
    bl_description = "Set Wireframe shading"

    def apply(self, space):
        space.shading.type = 'WIREFRAME'


class SNA_OT_Set_Solid_Shad_A809D(_ShadingOperator):
    bl_idname = "sna.set_solid_shad_a809d"
    bl_label = "Solid"
    bl_description = "Set Solid shading"

    def apply(self, space):
        space.shading.type = 'SOLID'
        space.shading.light = 'STUDIO'
        space.shading.color_type = 'MATERIAL'


class SNA_OT_Set_Solid_Vcol_75D4A(_ShadingOperator):
    bl_idname = "sna.set_solid_vcol_75d4a"
    bl_label = "Solid Vertex Color"
    bl_description = "Studio lighting with vertex color"

    def apply(self, space):
        space.shading.type = 'SOLID'
        space.shading.light = 'STUDIO'
        space.shading.color_type = 'VERTEX'


class SNA_MT_E8F7C(bpy.types.Menu):
    bl_idname = PIE_MENU_ID
    bl_label = "Shading"

    def draw(self, context):
        space = context.space_data
        pie = self.layout.menu_pie()

        # West
        pie.operator('sna.set_wireframe_shad_5cd18', text='Wireframe', icon='SHADING_WIRE')

        # East
        row = pie.row(align=True)
        row.scale_x = 0.95
        row.scale_y = 1.5
        row.operator('sna.set_solid_shad_a809d', text='Solid')
        row.operator('sna.set_solid_vcol_75d4a', text='SVcol')
        row.operator('sna.set_matcap_94fb0', text='Matcap')

        # South
        pie.operator('view3d.toggle_xray', text='Xray', icon='XRAY')

        # North
        pie.operator('sna.toggle_overlays_519f3', text='Overlays', icon='OVERLAY')

        # North-west
        pie.operator('sna.set_mat_shad_e3c82', text='Material', icon='MATERIAL')

        # North-east
        pie.operator('sna.set_render_shad_02048', text='Render', icon='SHADING_RENDERED')

        # South-west
        col = pie.column(align=True)
        col.scale_y = 1.25
        col.operator('sna.toggle_backfacecull_8e93f', text='BackFaceCull',
                     depress=space.shading.show_backface_culling)
        col.operator('sna.toggle_faceorient_def8b', text='FaceOrient',
                     depress=space.overlay.show_face_orientation)

        # South-east
        row = pie.row(align=True)
        row.scale_y = 1.5
        row.operator('sna.set_flat_shad_0e168', text='Flat')
        row.operator('sna.set_vertex_color_50c8a', text='Vcol')


classes = (
    SNA_OT_Toggle_Overlays_519F3,
    SNA_OT_Set_Mat_Shad_E3C82,
    SNA_OT_Set_Render_Shad_02048,
    SNA_OT_Toggle_Backfacecull_8E93F,
    SNA_OT_Toggle_Faceorient_Def8B,
    SNA_MT_E8F7C,
    SNA_OT_Set_Vertex_Color_50C8A,
    SNA_OT_Set_Flat_Shad_0E168,
    SNA_OT_Set_Matcap_94Fb0,
    SNA_OT_Set_Wireframe_Shad_5Cd18,
    SNA_OT_Set_Solid_Shad_A809D,
    SNA_OT_Set_Solid_Vcol_75D4A,
)


def _remove_keymaps():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    addon_keymaps.clear()


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    _remove_keymaps()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new('wm.call_menu_pie', 'Z', 'PRESS',
                                  ctrl=False, alt=False, shift=False, repeat=False)
        kmi.properties.name = PIE_MENU_ID
        addon_keymaps.append((km, kmi))


def unregister():
    _remove_keymaps()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
