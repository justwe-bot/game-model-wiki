# Doujie Model Handoff

## Current status

`blocked-head-replacement`

The original Tencent Hunyuan Web V3.1 body is reusable. The independent Web
V3.1 head has a softer, more mature identity and is also reusable. The current
combined `v2` artifact is rejected because the replacement implementation ran
voxel remesh over the entire character, damaging the body and clothing.

## Required inputs

- Body: `generated/doujie-hunyuan-web-v31-raw.glb`
- Head: `generated/doujie-head-hunyuan-web-v31.glb`
- Body provenance: `generated/provenance/doujie.json`
- Head references: `references/doujie/head/front.png`, `left.png`, `back.png`

## Accepted observations

- The body structure, clothing, limbs, shoes, and T-pose passed review.
- The original integrated body face failed identity review because it was too
  narrow, pointed, and severe.
- The independent head is complete and usable: full skull, hair, ears, jaw,
  short neck, and a softer neutral expression.
- The `v2` fusion is watertight in the automated report, but visual review
  overrides that result because the body surface is visibly degraded.

## Root cause

`cloud/modal_character_head_replacement.py` currently joins the trimmed body
and replacement head and calls `voxel_remesh()` on that complete object. The
4.5 mm voxel grid rebuilds the entire body, not only the neck seam. It also
requires a whole-character UV atlas and texture rebake, which loses the
original body material fidelity.

## Next implementation

1. Keep the original body mesh, UV layers, material slots, and textures intact.
2. Remove the old head above the selected body cut height.
3. Define a narrow neck fusion band around the cut, approximately 4-8 cm tall.
4. Extract only body vertices inside that band and the lower part of the new
   head/neck.
5. Fuse or boolean-union only those local pieces.
6. Stitch the fused neck band to the untouched body boundary with matching
   loops, then validate boundary and non-manifold edges.
7. Preserve the original body materials. Bake or blend only the replacement
   head and seam region; do not rebake the whole body.
8. Inspect close front/side/back views of face, neck, torso, hips, hands, and
   legs before running `prepare`.

Do not continue from `generated/doujie-head-replacement-v2/head-replaced.glb`.
The interrupted `generated/doujie-head-replaced-animation-mesh` task produced
no accepted output.

## External gates

- Generate replacement heads with Tencent Hunyuan 3D Web `V3.1`.
- Use the user's logged-in Edge browser state for Mixamo after a corrected
  fused animation mesh passes visual review.
