import bpy

SOURCE = r"C:\Users\yoons\Downloads\tyrannosaurus_rex_stan_skeleton\scene.gltf"

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SOURCE)

for obj in bpy.context.scene.objects:
    if obj.type != "MESH":
        continue
    center = obj.matrix_world.translation
    dims = obj.dimensions
    print(
        "TREX_OBJECT|"
        f"{obj.name}|"
        f"{center.x:.4f},{center.y:.4f},{center.z:.4f}|"
        f"{dims.x:.4f},{dims.y:.4f},{dims.z:.4f}|"
        f"{len(obj.data.vertices)}"
    )
