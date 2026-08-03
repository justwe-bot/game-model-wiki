# Hades II model analysis

Local install inspected: Steam app `1145350`, `Content/GR2/_Optimized`.

## Result

Hades II ships 3D monster assets rather than only prerendered sprites.
Character assets are grouped as matching `.sdb` and `.gpk` files. Animation
SJSON files identify the Granny model, clips, loops, attachments, masks, and
gameplay events.

The optimized format is not a directory of ordinary `.gr2` files:

- `.sdb` is a valid Granny shared string database.
- `.gpk` is a Supergiant packfile containing named model and animation blocks.
- Extracted blocks depend on the shared string database and cannot be loaded as
  standalone GR2 files.
- The game runtime exposes Granny string-database rebasing APIs, but current
  open-source importers do not perform this Supergiant-specific packfile step.

The local reconstruction pipeline now completes that step. Granny SDK
structures must use 4-byte packing (`Pack = 4`) to match the game DLL. The
rebuilt standalone files validate as 3 meshes, 1 model, 1 skeleton, and 5
materials before animation export.

## Integrated candidates

`Crawler` / `鼠兽` uses `RatThugGiant_Mesh` and the 578 KB
`RatThugGiant.gpk` pack. It contains one model block and 13 animation blocks:
idle, movement, burrow, rush, ground pound, and roar sequences.

The source contains three meshes with different runtime roles:

- `RatThugGiant_MeshShape`: the 3,686-triangle visible body used by the Wiki.
- `RatThugGiantOutline_MeshShape`: a 3,686-triangle outline shell.
- `RatThugGiantShadowMesh_MeshShape`: a 736-triangle shadow proxy.

The outline shell and shadow proxy are excluded from the Wiki GLBs because they
are rendering helpers rather than missing body geometry. The exported body
retains 87 bones and all 13 named animation clips.

The integrated pool now spans fourteen different body types:

| Entry | Source tris | Mobile tris | Bones | Clips | Mobile retention |
| --- | ---: | ---: | ---: | ---: | ---: |
| Crawler | 3,686 | 2,136 | 87 | 13 | 58% |
| Fish Swarmer | 906 | 652 | 12 | 9 | 72% |
| Jellyfish | 1,134 | 850 | 18 | 10 | 75% |
| Automaton Beamer | 2,245 | 1,459 | 13 | 12 | 65% |
| Assassin | 3,685 | 2,284 | 56 | 11 | 62% |
| Fishman Melee | 8,316 | 5,320 | 79 | 10 | 64% |
| Fishman Ranged | 4,050 | 2,589 | 64 | 14 | 64% |
| Harpy Talon Cutter | 4,822 | 2,989 | 37 | 18 | 62% |
| Screamer | 7,988 | 5,111 | 49 | 11 | 64% |
| Water Elemental | 1,555 | 1,057 | 7 | 12 | 68% |
| Water Unit | 3,808 | 2,513 | 31 | 17 | 66% |
| Brawler | 4,001 | 2,719 | 75 | 7 | 68% |
| Lamia | 8,280 | 5,464 | 64 | 13 | 66% |
| Zombie | 1,694 | 1,151 | 23 | 16 | 68% |

Fish Swarmer uses an entry-level 90-degree X rotation in the viewer because its
exported body axis is vertical in glTF coordinates. Root-motion stabilization
applies the same rotation when calculating its offset, so animation data remains
unchanged and the fish stays correctly framed.

## Mobile low-poly gate

- Preserve every connected body part, thin limb, tooth, ear, tail, weapon, and
  attachment silhouette.
- Protect UV seams, hard normals, material borders, bone-weight boundaries, and
  outline-critical edges during simplification.
- Use an error-limited target instead of forcing a fixed reduction percentage.
- Keep no more than four skin influences per vertex and compare idle plus attack
  extremes against the original.
- Default mobile delivery loads one low model, caps render pixel ratio at 1,
  and avoids loading the original until explicitly requested.

## Current Wiki export

- Fourteen enemy entries are registered in `games/hades-2/catalog.json`, each
  with original and mobile GLBs, 15 FPS low-model actions, and a per-material
  viewer profile applied identically to both variants.
- Simplification protects UV seams, material boundaries, and mesh seams.
- Skin weights are normalized and limited to four influences per vertex.
- The viewer follows `root_00_M_JNT` translation where available so rush,
  flight, dive, and long-body attack clips remain in frame without deleting
  root motion from the GLB.
- The export bridge carries material binding names but not texture pixels from
  the GPK. Color textures are extracted separately from the game's 1080p PKG
  resources for thirteen entries, with 256px mobile variants. Fishman Melee
  has no matching base texture in the shipped PKG index and retains its curated
  material color profile.

## Toolchain

1. `tools/hades2-granny-rebuild` rebases selected GPK blocks against the shared
   string database and writes standalone GR2 files.
2. `tools/hades2-granny-export` uses the game-local `granny2_x64.dll` to export
   mesh, skeleton, skin bindings, materials, and sampled actions to H2GX.
3. `scripts/build_hades2_glb.py` builds original/mobile GLBs in Blender,
   excludes helper meshes, constrains skin influences, and reduces low-model
   animation sampling.
4. `scripts/build_hades2_asset.ps1` runs the complete reconstruction/export/GLB
   sequence for one matching SDB/GPK pair.

Example:

```powershell
.\scripts\build_hades2_asset.ps1 `
  -Asset JellyFish `
  -ModelEntry JellyFish_Mesh `
  -OutputSlug hades2-jellyfish `
  -LowRatio 0.75
```

Do not commit or distribute the source GPK/SDB, game DLL, temporary GR2 files,
or H2GX bridge bundles.
