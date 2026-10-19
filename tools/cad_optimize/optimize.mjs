// node optimize.mjs <in.gltf|.glb> <out.glb> [--compress]  (via tools/cad_optimize.py)
//
// An Onshape export is one primitive per CAD face: 72,625 draw calls and
// 12.7M triangles for the 2026 robot, as 360 MB of JSON with the geometry in
// base64. Measured in the app's Chromium (2026-10-18): 147 ms a frame. This
// keeps every node and name (the subsystem focus finds nodes by name) and
// changes only how the geometry is stored: 979 draw calls, 14 ms a frame,
// 148 MB, the same picture pixel for pixel, readable by every build.
// --compress adds meshopt (25 MB, 12.6 ms), which needs the viewer's decoder
// (in builds from v0.2.1-beta.10).
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, join, weld, prune, meshopt } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';

const [input, output, flag] = process.argv.slice(2);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await io.read(input);

function stats() {
  const root = doc.getRoot();
  let draws = 0, tris = 0;
  for (const node of root.listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    for (const p of mesh.listPrimitives()) {
      draws++;
      const idx = p.getIndices();
      tris += (idx ? idx.getCount() : p.getAttribute('POSITION').getCount()) / 3;
    }
  }
  return { nodes: root.listNodes().map(n => n.getName()), draws, tris: Math.round(tris) };
}

const before = stats();
// No textures in a CAD export: the UVs are dead weight.
for (const mesh of doc.getRoot().listMeshes())
  for (const p of mesh.listPrimitives()) p.setAttribute('TEXCOORD_0', null);
await doc.transform(
  dedup(),
  // One primitive per material inside each mesh; meshes and nodes stay.
  join({ keepMeshes: true, keepNamed: false }),
  weld(),
  prune(),
);
if (flag === '--compress') {
  // Quantize over the whole scene: the default per-mesh volume rewrites node
  // transforms and, after the join, scattered small parts across the view.
  await doc.transform(
    meshopt({ encoder: MeshoptEncoder, level: 'medium', quantizationVolume: 'scene' }));
}
const after = stats();
await io.write(output, doc);
console.log(JSON.stringify({
  draws: [before.draws, after.draws], triangles: [before.tris, after.tris],
  nodes: [before.nodes.length, after.nodes.length],
  names_kept: JSON.stringify(before.nodes) === JSON.stringify(after.nodes),
}));
