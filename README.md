# gret

A collection of Blender tools I've written for myself over the years. I use these daily so they should be bug-free, mostly. Feel free to take and use any parts of this project. `gret` can be typed with one hand in the search bar.



# Installation

Blender 2.92 or later required.

1. Download the [latest release](https://github.com/greisane/gret/releases/latest).

2. In Blender, go to Edit → Preferences → Add-ons → Install.

   ![Step 2](../readme/installation-02.png?raw=true)

3. Find and select the downloaded zip file, then click *Install Add-on*.

4. Enable the add-on by clicking the checkbox. It should be listed as *gret*. 

   ![Step 4](../readme/installation-04.png?raw=true)

5. Typing `gret` in the search bar should ensure it's working. Other tools and operators will show up depending on context.

   ![Step 5](../readme/installation-05.png?raw=true)



# Configuration

TODO



# Tools

## Mesh: Graft

Connects boundaries of selected objects to the active object. I wrote it to deal with stylised fur in a non-destructive way that allows normals to be lifted from the body.

![Demo](../readme/graft-demo.gif?raw=true)

## Mesh: Retarget Mesh

Refit clothing meshes to a modified version of the character mesh.

![Demo](../readme/retargetmesh-demo.gif?raw=true)

If retargeting to a different mesh, make sure they share topology and vertex order. If the retargeted mesh becomes polygon soup then it's probably the vertex order. Try using an addon like [Transfer Vert Order](https://gumroad.com/l/copy_verts_ids) to fix it.

## Mesh: Make Collision

Intended for use with UE4, generates collision shapes for selected geometry. For example, to make compound collision for a chair:

1. Select a part of the chair in edit mode (can use *Select Linked Pick* if the pieces are separate).
2. Click *Make Collision* and select an appropriate shape, e.g. capsules for the posts, a box for the backrest and cylinder for the seat.
3. Repeat for every piece.

![Demo](../readme/makecollision-demo.gif?raw=true)

## Mesh: Vertex Color Mapping

Procedurally generates vertex colors from various sources. Sources can be vertex groups, object or vertex position, or a random value. Useful for exporting masks to game engines.

![Panel](../readme/vcolmapping.png?raw=true)

## Mesh: Vertex Group Smooth Loops

Skinning tool to separately smooth weights on parallel loops. Belts and such should deform lengthwise without compressing and regular Smooth isn't too good at this. Found in Weights → Smooth Loops while in Weight Paint mode, vertex selection must be enabled to use.

![Demo](../readme/smoothloops-demo.gif?raw=true)

## Mesh: Apply Modifiers with Shape Keys

The much needed ability to apply modifiers on a mesh with shape keys. Mirrors are specially handled to fix shape keys that move vertices off the center axis. Found in Shape Keys → Specials Menu → Apply Modifiers with Shape Keys.

## Mesh: Sync UV Maps

Adds a few buttons that allow reordering UV maps. *Sync UV Maps* works on all selected objects to ensure UV layer names and order are consistent with the active object. It can also be used to simply switch the active UV layer for multiple objects.

![Buttons](../readme/syncuvmaps.png?raw=true)

## Mesh: Add Strap

Similar in function to an extruded curve. Since it's mesh and not curve based, typical mesh operators can be used to edit it. Use case is adding belts to characters.

## Mesh: Add Rope

Generates helicoid meshes, mostly useful as ropes. Can edit the base shape once created.

## Animation: Pose Blender

Allows blending poses together, similar to the UE4 [AnimGraph node](https://docs.unrealengine.com/en-US/AnimatingObjects/SkeletalMeshAnimation/AnimPose/PoseBlenderNode/index.html). Works on bones, not shape keys.

![Demo](../readme/poseblender-demo.gif?raw=true)

Has a performance cost, I'll try to optimize it further at some point.

## Animation: Actions Panel

A panel for quick access to actions and working with pose libraries. Pose libraries are simply actions where each frame has a named marker, and normally they're very annoying to work with. A pose library is necessary to use the Pose Blender tool.

![Panel](../readme/actions-panel.png?raw=True)

## Animation: Rig Panel

Add any frequently used rig or bone properties here. To find the data path of a property, right click it then select *Copy Data Path*.

The addon [Bone Selection Sets](https://docs.blender.org/manual/en/latest/addons/animation/bone_selection_sets.html) must be enabled for the second panel to show. I don't find bone pickers to be comfortable to use, and this is a workable alternative. Add and delete buttons make it easy to create temporary sets while animating.

![Panel](../readme/rig-panel.png?raw=True)

## Material: Texture Bake

One-click bake and export. Intended for quickly baking out curvature and AO masks.

![Panel](../readme/texturebake.png?raw=true)

## Material: Tile Paint

Rudimentary tool to create tile-based UV maps. For anything more complicated use [Sprytile](https://github.com/Sprytile/Sprytile) instead.

![Demo](../readme/tilepaint-demo.gif?raw=true)

## UV: Relax Loops

Relaxes selected UV edge loops to their respective length on the mesh. Together with pins it can be used to rectify non-grid meshes that TexTools Rectify won't work on. Found in UV Editor → UV → Relax Loops.

![Demo](../readme/uvrelax-demo.gif?raw=true)

## Other

**Sculpt Selection**: Sets the sculpt mask from the current edit-mode vertex selection. Found in the Select menu in edit mode.

**Normalize Shape Key**: Resets min/max of shape keys while keeping the range of motion. A shape key with range [-1..3] becomes [0..1], neutral at 0.25. Some game engines don't allow extrapolation of shape keys. Found in Shape Keys → Specials Menu → Normalize Shape Key.

~~**Merge Shape Keys to Basis**: Mixes active shape keys into the basis shape. It's possible to filter shape keys by name~~.

**Remove Unused Vertex Groups**: Originally an addon by CoDEmanX, this operator respects L/R pairs of vertex groups. Found in Vertex Groups → Specials Menu → Remove Unused Vertex Groups.

**Deduplicate Materials**: Squashes duplicate materials, like "Skin.002", "Skin.003", etc. Found in File → Clean Up.

**Replace References**: Replaces object references in modifiers. I use it to swap meshes that are shrinkwrap targets and such. Found in File → Clean Up.

# Export Jobs

TODO