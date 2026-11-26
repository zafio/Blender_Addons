####RENAME TOOLS (coded by Luca Scheller)
Adds a modal operator to rename objects, and automatically renames object suffixes (based on Unity3D LOD naming) when duplicating an object: HI > LOD0 > LOD1 > LOD2... renaming also the mesh data name.

- CTRL+ALT+R = runs the rename tool (CTRL+ALT+R again deletes the whole name)
- Enter & LMB = Accept
- Esc & RMB = Cancel

####FRAME HOTKEYS
Adds hotkeys to improve control over animation playback. At Timeline, Graph or Dopesheet Editors:

- Q = Toggles preview range
- W = Previous frame (loops within start & end frames)
- E = Next frame (loops within start & end frames)
- D = Previous Action (loops within action list, auto-sets preview range for each one)
- F = Next Action (loops within action list, auto-sets preview range for each one)
- 2 = Decreases Framerate by 5
- 3 = Increases Framerate by 5

####PROJECT PAINT TOGGLE
Alt+S (Texture Paint mode) toggles "Occlude, Cull and Normal" at once; Alt+D toggles Bleed between 0 and its previous value.
Cursor: green = paint through, red = bleed on, blue = both, white = neither.
https://www.youtube.com/watch?v=rL1v3YSVyCg

####EASY PIXEL INTERPOL
Makes pixel-art texturing quicker: no more trips to the Shader Editor to set Image Texture nodes to "Closest".

- New Image Texture nodes default to Closest (toggle and default mode in the add-on preferences; existing nodes are left untouched)
- CTRL+ALT+L = Toggles Closest / Linear for the active image (Image Editor, Texture Paint mode, or the active Image Texture node in the Shader Editor)
- Interpolation dropdown in the Image Editor header and the Texture Slots panel

####UV VERTEX PIVOT TRANSFORM
Rotates, mirrors and scales the UV selection using the closest vertex below the cursor as pivot (UV Editor). Works like Blender's own transforms: type a value, hold Ctrl to snap the angle, Alt to snap to 90º, Shift for precision.

- CTRL+R = Rotate around the nearest vertex
- CTRL+X = Mirror around the nearest vertex (X again switches to Y axis, again turns mirror off)
- < = Scale from the nearest vertex (X / Y to constrain the axis, Ctrl snaps in whole texture pixels, or 0.1 steps without a texture)
- Enter & LMB = Accept
- Esc & RMB = Cancel


####UV OVERLAP SELECT
Selects overlapped (stacked / mirrored) UV elements together (UV Editor). Press the key of the select mode you are already in to toggle overlap selection; while it is active, the UV selection turns red whenever it actually contains overlapped elements.

- 1 / 2 / 3 / 4 again (on the active Vertex / Edge / Face / Island mode) = Toggles overlap selection
- Selecting an element also selects everything sitting at the same UV position; deselecting it deselects the overlapped partners too
- Works with UV Sync Selection on or off (Island mode only without sync)
- SHIFT+1 / 2 / 3 / 4 or SHIFT+W = Toggles a wire display: stacked faces pale green, overlapping faces mauve (colors and line width in the panel)
- N panel > Overlap tab: on/off button, tolerance, and "Keep On When Changing Mode"

####PIXEL PERFECT UV
Lays UV islands out on the texture's pixel grid for low poly / pixel art characters (UV Editor or 3D View in Edit Mode > Sidebar > Pixel UV, also in the UV menus). Works on the selected faces (all faces if none are selected) of every object in Edit Mode, packed together.

- Pixel Perfect UV = Straighten + Density + Snap + Pack in one click (or run Straighten / Snap / Pack on their own)
- Straighten: quad-grid islands (limbs, torso strips...) become perfect rectangles, sized from the real 3D edge lengths. Only applied when the stretch stays under "Max Distortion %"; other islands keep their unwrap and are just rotated to sit straight
- Density: Fit Texture (biggest density that fits), Fixed px/unit (eyedropper reads it from the selection) or Current Average
- Island Density: give the selected faces' islands their own fixed px/unit or a multiplier (e.g. x2 for the face). Stored on the mesh, so it's kept every time you re-run
- Snap: every vertex lands on a pixel corner; rows and columns of straight islands never collapse below 1 px
- Pack: whole-pixel packing with pixel padding, optional 90º rotation
- Texture size comes from the image in the UV Editor (or the material's image texture), or a custom size
