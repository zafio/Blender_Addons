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
Selects overlapped (stacked / mirrored) UV elements together (UV Editor). Press the key of the select mode you are already in to toggle overlap selection; the UV selection turns red while it is active.

- 1 / 2 / 3 / 4 again (on the active Vertex / Edge / Face / Island mode) = Toggles overlap selection
- Selecting an element also selects everything sitting at the same UV position; deselecting it deselects the overlapped partners too
- Works with UV Sync Selection on or off (Island mode only without sync)
- While active, the wire edges of stacked faces turn pale green and those of overlapping faces pale blue (toggle, colors and line width in the panel)
- N panel > Overlap tab: on/off button, tolerance, and "Keep On When Changing Mode"
