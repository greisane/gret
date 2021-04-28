from bpy.app.handlers import persistent
import bpy

from gret import prefs

saved_unhidden_collections = set()
@persistent
def save_pre(dummy):
    # Automatically hide the ARP armature collection on saving since I'm always forgetting
    # This is so that the linked armature doesn't interfere with the proxy when linking
    for coll in bpy.data.collections:
        if coll.name.endswith('_grp_rig') and not coll.library and not coll.hide_viewport:
            saved_unhidden_collections.add(coll.name)
            coll.hide_viewport = True

@persistent
def save_post(dummy):
    # Undo save_pre hiding of collections
    for coll_name in saved_unhidden_collections:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            coll.hide_viewport = False
    saved_unhidden_collections.clear()

@persistent
def load_post(dummy):
    # Unhide on load, otherwise it's annoying
    for coll in bpy.data.collections:
        if coll.name.endswith('_grp_rig') and not coll.library and coll.hide_viewport:
            coll.hide_viewport = False

def register(settings):
    if prefs.auto_hide_arp_enable:
        bpy.app.handlers.save_pre.append(save_pre)
        bpy.app.handlers.save_post.append(save_post)
        bpy.app.handlers.load_post.append(load_post)

def unregister():
    try:
        bpy.app.handlers.save_pre.remove(save_pre)
        bpy.app.handlers.save_post.remove(save_post)
        bpy.app.handlers.load_post.remove(load_post)
    except ValueError:
        pass
