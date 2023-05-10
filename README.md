# gret

A collection of Blender tools I've written for myself over the years. I use these daily so they should be mostly bug-free. Feel free to take and use any parts of this project. `gret` can be typed with one hand in the search bar.



# Installation

Blender 3.5 or later required.

1. Download the [latest release](https://github.com/greisane/gret/releases/latest).

2. In Blender, go to Edit → Preferences → Add-ons → Install.

   ![Step 2](../readme/installation-02.png?raw=true)

3. Find and select the downloaded zip file, then click *Install Add-on*.

4. Enable the add-on by clicking the checkbox. It should be listed as *gret*. 

   ![Step 4](../readme/installation-04.png?raw=true)

5. Typing `gret` in the search bar should ensure it's working. Other tools and operators will show up depending on context.

   ![Step 5](../readme/installation-05.png?raw=true)



# Configuration

Everything is enabled by default except [Export Jobs](#export-jobs). Most tools can be individually toggled off to avoid clutter.

![Preferences](../readme/preferences.png?raw=true)



# Tools

## Mesh: Graft

Connects boundaries of selected objects to the active object. I wrote it to deal with stylised fur in a non-destructive way that allows normals to be lifted from the body.

![Demo](../readme/graft-demo.gif?raw=true)

![Examples](../readme/graft-examples.png?raw=true)

## Mesh: Merge

Boolean merges one or more objects, with options to tweak the resulting normals. Though it's no substitute for proper retopo it can be a decent starting point.

![Demo](../readme/merge-demo.gif?raw=true)

![Examples](../readme/merge-examples.png?raw=true)

> Merging vertices exclusively along the UV direction to keep seams intact.

## Mesh: Retarget

Uses [radial basis functions](https://www.marcuskrautwurst.com/2017/12/rbf-node.html) to retarget meshes or armatures. It can be used to transfer body shape keys to clothing, to recycle outfits between characters or to apply changes in body proportions to the skeleton's rest pose.

Source and destination meshes can look completely different but *must* share topology and vertex order. Shape keys as destination will always work correctly. Best results with T-posed or starfish-posed characters as there is less potential for ambiguity.

![Examples](../readme/retargetmesh-examples.png?raw=true)

## Mesh: Make Collision

Intended for use with UE4, generates collision shapes for selected geometry. For example, to make compound collision for a chair:

1. Select a part of the chair in edit mode (can use *Select Linked Pick* if the pieces are separate).
2. Click *Make Collision* and select an appropriate shape, e.g. capsules for the posts, a box for the backrest and cylinder for the seat.
3. Repeat for every piece.

![Demo](../readme/makecollision-demo.gif?raw=true)

## Mesh: Vertex Color Mapping

Procedurally generates vertex colors from various sources. Sources can be vertex groups, vertex position, mesh distance, cavity and more. Useful for exporting masks to game engines.

![Panel](../readme/vertexcolormapping.png?raw=true)

## Mesh: Vertex Group Bleed

The Smooth operator with "Expand" tends to either clip values or smear them way too much. Bleed provides finer control and guarantees that new weights will never be lower than the input weights.

Use to create precise weight gradients or to soften skinning without weakening the overall deformation.

## Mesh: Vertex Group Smooth Loops

Skinning tool to separately smooth weights on parallel loops, like belts and such that should deform lengthwise without compressing. Uses the Bleed operator above. Found in Weights → Smooth Loops while in Weight Paint mode, vertex selection must be enabled.

![Demo](../readme/smoothloops-demo.gif?raw=true)

## Mesh: Apply Modifiers with Shape Keys

The much needed ability to apply modifiers on a mesh with shape keys. Mirrors are specially handled to fix shape keys that move vertices off the center axis. Found in Shape Keys → Specials Menu → Apply Modifiers with Shape Keys.

## Mesh: Shape Key Presets

Ctrl-Click a letter button to save current shape key values to that slot, click to restore and apply those values. Note that this doesn't store the shape keys themselves, only their influence. Comes in handy sometimes if you use a lot of shape keys.

Number of buttons and behavior can be configured in preferences.

![Panel](../readme/shapekeystore.png?raw=true)

## Mesh: Add Rope

Generates helicoid meshes like ropes or drill bits. Can also be edited manually once created.

![Demo](../readme/rope-demo.gif?raw=true)

## Animation: Pose Blender

Allows blending poses together, similar to the UE4 [AnimGraph node](https://docs.unrealengine.com/en-US/AnimatingObjects/SkeletalMeshAnimation/AnimPose/PoseBlenderNode/index.html). Works on bones, not shape keys.

![Demo](../readme/poseblender-demo.gif?raw=true)

## Animation: Actions Panel

A panel for quick access to actions and working with pose libraries. Pose libraries are simply actions where each frame has a named marker, and normally they're very annoying to work with. A pose library is necessary to use the Pose Blender tool.

![Panel](../readme/actions-panel.png?raw=True)

## Animation: Rig Properties

Customizable panel for frequently used rig or bone properties. To add a property, first find its data path (right click and select *Copy Data Path*) then click the plus sign.

![Panel](../readme/rigproperties-demo.gif?raw=True)

## Animation: Selection Sets

Panel for quick bone selection if you don't find graphical bone pickers comfortable. Built-in addon [Bone Selection Sets](https://docs.blender.org/manual/en/latest/addons/animation/bone_selection_sets.html) must be enabled.

![Panel](../readme/selectionsets-panel.png?raw=True)

## Animation: Miscellaneous Tools

**Auto-Group Channels**: Groups animation channels by their bone name. Found in the Channel menu in the Dope Sheet or Graph Editor.

**Delete Unavailable Channels**: Deletes location/rotation/scale animation channels locked in the transform panel. Found in the Channel menu in the Dope Sheet or Graph Editor.

**Reset Stretch To Constraints**: Reset rest length of "Stretch To" constraints in selected bones, or all bones if none are selected. Found in Pose → Constraints.

**Toggle Bone Lock**: Simple but useful toggle that causes a pose bone to become anchored in world space. Found in Pose → Constraints.

## Material: Texture Bake

*This tool is disabled by default, enable it in the addon configuration.*

One-click bake and export. Intended for quickly baking out curvature and AO masks.

![Panel](../readme/texturebake.png?raw=true)

## UV: UV Paint

Work-in-progress tool to assign UVs from a previously configured tileset or trim sheet.

![Demo](../readme/tilepaint-demo.gif?raw=true)

> Above is an older video of the tool, but the functionality is the same.

## UV: Relax Loops

Relaxes selected UV edge loops to their respective length on the mesh. Together with pins it can be used to rectify non-grid meshes that TexTools Rectify won't work on. Found in UV Editor → UV → Relax Loops.

![Demo](../readme/uvrelax-demo.gif?raw=true)

## UV: Reorder UV Maps

Adds a few buttons that allow reordering UV maps. *Sync UV Maps* works on all selected objects to ensure UV layer names and order are consistent with the active object. Can be used to just switch the active UV layer for multiple objects.

![Buttons](../readme/syncuvmaps.png?raw=true)

## Other

**Sculpt Selection**: Sets the sculpt mask from the current edit-mode vertex selection. Found in the Select menu in mesh edit mode.

**Normalize Shape Key**: Resets min/max of shape keys while keeping the range of motion. A shape key with range [-1..3] becomes [0..1], neutral at 0.25. Some game engines don't allow extrapolation of shape keys. Found in Shape Keys → Specials Menu.

**Select Shape Key**: Select vertices affected by the current shape key. Found in Shape Keys → Specials Menu.

**Encode Shape Key**: Implements shape key to UV channel encoding required for [Static Mesh Morph Targets](https://docs.unrealengine.com/4.27/en-US/WorkingWithContent/Types/StaticMeshes/MorphTargets/). No menu button, use operator search.

**Remove Unused Vertex Groups**: Originally an addon by CoDEmanX, this operator respects L/R pairs of vertex groups. Found in Vertex Groups → Specials Menu.

**Create Mirrored Vertex Groups**: Create any missing mirror vertex groups. New vertex groups will be empty. Found in Vertex Groups → Specials Menu.

**Auto-Name Bone Chain**: Automatically renames a chain of bones starting at the selected bone. Found in Armature → Names.

**Copy Alone**: Removes references and optional data so that objects can easily be duplicated or moved between files. Still a bit incomplete. Found in the Object menu in the 3D view.

**Deduplicate Materials**: Squashes duplicate materials, like "Skin.002", "Skin.003", etc. Found in File → Clean Up.

# Export Jobs

*This panel is not shown by default, enable it in the addon configuration.*

Jobs automate the export process for multiple objects or complex setups. Since this is tailored to my workflow, mileage may vary. Some extra options are available for Auto-Rig Pro rigs. 

Example use cases:

- Exporting many objects to one file each along with collision and socket metadata (UE4).
- Different versions of a single character with swapped materials or clothes.
- Joining a character with many parts and modifiers into a single optimized mesh, for performance while animating.
- Avoiding mistakes like not correctly resetting an armature prior to animation export.

![Panel](../readme/jobs-panel.png?raw=True)