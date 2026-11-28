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

Scale (<): press T while scaling and type a texel density (px/unit) to scale the selection to exactly that density (with X / Y: along that axis). The header shows the live texel density while scaling. The texture size comes from the UV Editor's image (or Pixel Perfect UV's custom size).

- CTRL+R = Rotate around the nearest vertex
- CTRL+X = Mirror around the nearest vertex (X again switches to Y axis, again turns mirror off)
- < = Scale from the nearest vertex (X / Y to constrain the axis, Ctrl snaps in whole texture pixels, or 0.1 steps without a texture)
- With Pixel Perfect UV's Preserve Texture on, 90º rotations and mirrors carry the painted pixels along (and Ctrl+Z undoes them)
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

- Auto Unwrap Character = Auto Seams + unwrap + Character Density Preset + Pixel Perfect UV, in one click
- Seams: Mine Only (default) = Auto Unwrap uses only the seams you marked: no Auto Seams, straight to unwrap, density and Pixel Perfect
- Auto Seams: marks seams like a hand unwrap. Learned from hand-unwrapped characters (a small model predicts which edges an artist would cut), then every island is made a flat piece (tubes opened, closed parts cut on the most hidden side) and everything is mirrored left / right. The face is kept in one piece (forehead, mouth, chin; the nose too unless it comes apart cleanly) and never cut down the middle, so it is never split and stacked. Per object Seam Type: Auto, Organic (body), Hard Surface (props: cut the creases) or Tube (rope, cable). "Keep My Seams" builds around seams you marked. Rules mode uses crease angles instead of the model
- Pixel Perfect UV = Straighten + Density + Snap + Pack in one click (or run Straighten / Snap / Pack on their own)
- Orient Up: islands are turned so the model's up points up in the UV (straight grids in 90º steps, free-form islands by the exact angle, islands crossing the centre line with it exactly vertical); islands lying flat (top of the head, soles) get the front pointing up
- Snap: Bounds (default: only the island's outer edges go on pixel lines, the inside keeps its shape) or All Vertices
- Straighten: quad-grid islands (limbs, torso strips...) become perfect rectangles, sized from the real 3D edge lengths. Only applied when the stretch stays under "Max Distortion %" (default 25%); other islands keep their unwrap and are just rotated to sit straight. Islands mixing quads and triangles / n-gons (hands, caps...) and quad islands with a pole get their clean grid part straightened and pinned, and Blender's unwrap fits the rest around it (idea from noio's [Pixel-Unwrapper](https://github.com/noio/Pixel-Unwrapper))
- Density: Fit Texture (biggest density that fits), Fixed px/unit (eyedropper reads it from the selection) or Current Average
- Stack Mirrored L/R: islands that mirror each other across the object's X axis share the same pixels (the +X side is laid out, the -X one is stacked on it). Also as its own button. Islands folded onto themselves (left half over the right) are kept as they are
- Character Density Preset: head x2.4 (ends up about x2 the body, like the sample unwraps), hands x1.3, legs x0.8 by height / distance from the centre, hard-surface props x1.5; values in the Auto Unwrap box. Each island takes the multiplier of the part covering most of it, and islands that are the front and back of the same neck, arm or leg share one (Same Front and Back, on by default), so the density matches all round
- Show Texel Density (Density box, on by default): live readout at the top left of the UV Editor of the selected UVs' px/unit, overall and along X / Y; it follows a scale while you drag
- Panel sections (Auto Unwrap, Texture, Density, Island Density, Shape, Packing) fold with the arrow on their header; Auto Unwrap Character and the Pixel Perfect buttons stay visible
- The multipliers show their absolute px/unit (Fixed: the typed density; otherwise the last Pixel Perfect run's)
- Get from Mesh: reads the Head / Hands / Legs / Props multipliers from a character you unwrapped by hand (each part's px/unit divided by the body's)
- Select Part / Assign Part: see which faces count as head, hands, body, legs or props, and set them by hand (Automatic undoes it); an island still takes the multiplier covering most of its area
- Packing fills the texture from the top left corner; islands stay upright (straightened islands whose grid runs across the body are turned upright too)
- Single Pixel Fill: maps the selected faces onto one free pixel; with Preserve Texture it keeps their average colour
- Island Density: give the selected faces' islands their own fixed px/unit or a multiplier (e.g. x2 for the face). Stored on the mesh, so it's kept every time you re-run
- Straight rows and columns always snap to whole pixels and never collapse below 1 px
- Preserve Texel Density (+ tolerance, default 5%): snapping may not change an island's size by more than the tolerance; tiny pieces and thin strips that would are only moved onto the pixel grid, keeping their exact density
- Pack: whole-pixel packing with pixel padding, optional 90º rotation. Pack Shapes (default) packs the islands' real pixel shapes, so small islands fill the gaps of big ones (about 70-75% of the texture used instead of ~58% with boxes). Stacked / mirrored islands that share pixels stay together
- Preserve Texture (Pack button): the painted pixels move with their islands, so you can repack after painting. Packs every face of the edited objects
- Resize Texture: Canvas (more / less room, the painting stays where it is; choose the anchor, e.g. Top keeps the painting at the top and adds the space below) or Upscale (every pixel becomes N x N for finer painting)
- ALT+G (UV Editor) = Move Islands: moves the selected islands in whole pixels and the painted pixels follow live (needs Preserve Texture on), together with a margin as wide as Padding. Mouse / Arrows (Shift = 8 px) move, R / Shift+R turn 90º, X / Y lock an axis, Enter & LMB accept, Esc & RMB cancel. The header warns when it overlaps another island or leaves the texture
- Ctrl+Z / Ctrl+Shift+Z undo and redo the texture changes too (Pack, Move, Fill, Resize, Pivot Rotate / Mirror). Restore Texture is still there as a manual fallback. Remember to save the image
- With UV Vertex Pivot Transform installed: its Ctrl+R (at 90º steps, e.g. holding Alt) and Ctrl+X carry the painted pixels too while Preserve Texture is on; the pivot snaps to the nearest pixel corner / centre so the pixels map exactly
- Texture size comes from the image in the UV Editor (or the material's image texture), or a custom size
