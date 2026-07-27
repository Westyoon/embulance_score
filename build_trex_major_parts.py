import bpy
from pathlib import Path

SOURCE = Path(r"C:\Users\yoons\Downloads\tyrannosaurus_rex_stan_skeleton\scene.gltf")
OUTPUT_BLEND = SOURCE.with_name("trex_major_parts.blend")
OUTPUT_GLB = SOURCE.with_name("trex_major_parts.glb")


def category_for(obj):
    name = obj.name.lower()
    x = obj.matrix_world.translation.x

    if any(k in name for k in ("skull", "palette", "upper_teeth", "mandible", "lower_teeth")):
        return "01_HEAD"
    if any(k in name for k in ("cervical", "c_ribs", "atlas_axis")):
        return "02_NECK"
    if any(k in name for k in ("caudal", "haemal")):
        return "09_TAIL"
    if "sacral" in name:
        return "04_PELVIS"
    if any(k in name for k in ("gastralia", "rib_pair", "dorsal", "scap", "furcula")):
        return "03_TORSO_RIBCAGE"

    # The source uses duplicated L_* names for both sides, so world X is used.
    side = "LEFT" if x < 0 else "RIGHT"
    if any(k in name for k in ("humerus", "ulna", "radius")):
        return f"05_ARM_{side}"
    if any(k in name for k in ("femur", "tibia", "fibula", "tmt")):
        return f"07_LEG_{side}"

    # Hand and foot bones share generic digit/phalange/ungual names.
    if any(k in name for k in ("digit", "phalange", "ungual", "claw", "l_1_", "l_2_", "r_3_")):
        return f"05_ARM_{side}" if obj.matrix_world.translation.z > 85 else f"07_LEG_{side}"

    return "10_OTHER"


def join_objects(objects, result_name, target_collection):
    bpy.ops.object.select_all(action="DESELECT")
    copies = []

    for source in objects:
        copy = source.copy()
        copy.data = source.data.copy()
        copy.matrix_world = source.matrix_world.copy()
        copy.parent = None
        target_collection.objects.link(copy)
        copy.select_set(True)
        copies.append(copy)

    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = result_name
    joined.data.name = f"{result_name}_MESH"

    # A useful origin for rotating or moving each large section.
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    return joined


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(SOURCE))

source_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
groups = {}
for obj in source_meshes:
    groups.setdefault(category_for(obj), []).append(obj)

major_collection = bpy.data.collections.new("MAJOR_PARTS")
bpy.context.scene.collection.children.link(major_collection)

major_objects = []
for category in sorted(groups):
    major_objects.append(join_objects(groups[category], category, major_collection))

# Keep imported source as a hidden backup in the Blend file.
for obj in bpy.context.scene.objects:
    if obj not in major_objects:
        obj.hide_viewport = True
        obj.hide_render = True

# Make the major parts easy to select and visually inspect.
for obj in major_objects:
    obj.hide_viewport = False
    obj.hide_render = False
    obj.show_name = True

bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT_BLEND))

# Export only the visible, joined major pieces to a portable GLB.
bpy.ops.object.select_all(action="DESELECT")
for obj in major_objects:
    obj.select_set(True)
bpy.context.view_layer.objects.active = major_objects[0]
bpy.ops.export_scene.gltf(
    filepath=str(OUTPUT_GLB),
    export_format="GLB",
    use_selection=True,
    export_apply=True,
)

print("TREX_DONE")
print(f"BLEND={OUTPUT_BLEND}")
print(f"GLB={OUTPUT_GLB}")
for category in sorted(groups):
    print(f"GROUP={category}|SOURCE_OBJECTS={len(groups[category])}")
