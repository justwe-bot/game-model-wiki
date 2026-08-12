# 设计图到可动画 GLB 的角色生产流程

这套流程用于把角色设计图转换成可复用、可验证、可发布到 Game Model Wiki 的动画 GLB。当前生产范围是双足人形英雄和敌人；四足、飞行体、蛇形或多足敌人需要新增骨架与动作模板，不能直接套用 Mixamo 或 Commando。

新角色的默认交付路径已经切换为“混元最终高模 -> Modal Blender/QRemeshify 网格规整与贴图烘焙 -> Mixamo 为最终低模直接生成 Skin 和动作”。本文件后续的 UniRig/Commando 模板迁移仍用于 POPBOT 等历史回归资产和无法重新上传 Mixamo 的兼容场景。新路径的具体命令见 `references/image-hunyuan3d-mixamo-workflow.md`。

## 核心结构

### 新角色默认路径

```text
设计图 / 三视图
  -> 混元最终高模 GLB
  -> Modal Blender 保守修复和质量门禁
  -> QRemeshify / QuadWild Bi-MDF 重拓扑
  -> UV + Base Color / Normal / AO 烘焙
  -> Mixamo 上传最终低模 FBX
  -> With Skin 基础 FBX + Without Skin 动作包
  -> Modal Blender 合并动画 GLB / FBX
  -> 结构校验 + 动作视觉验收
```

### 历史模板迁移路径

```text
设计图
  -> 最终角色正/左/背 T Pose 图
  -> 最终角色高模 GLB
  -> UniRig full（最终网格权重）
  -> 共享简化为 original/low
  -> 匿名骨骼语义映射 + 最终角色 rig profile
  -> 标准模板骨架与动作迁移
  -> 结构校验 + 动作验收
  -> Wiki 发布 + provenance

设计图 -> 无服装白模三视图 -> 白模 GLB -> UniRig 骨架代理
                                      |
                                      +-> 仅用于方向、骨长和比例异常检查
```

白模不是最终角色，也不直接替代服装模型。它负责验证左右方向、骨链语义、人体比例和动作曲线。最终穿衣网格仍需独立生成；最终关节位置和蒙皮权重优先来自最终角色自身的 UniRig full 输出，不能用白模关节坐标硬套不同体型或不同服装的网格。

不能把白模权重逐顶点复制到拓扑不同的穿衣模型，也不能直接采用白模关节位置。正确做法是让最终穿衣网格独立完成 UniRig full，再把它自己的权重按骨骼语义映射到已审核动作模板；白模只负责在映射前暴露左右反转、骨长异常和比例偏差。

## 生产阶段

### 1. 设计图规范化

- 最终角色需要正面、严格左侧、背面三张同高 T Pose 图。
- 三张图必须保持相同头身比、肩宽、髋宽、臂长、腿长和地面线。
- 去掉枪械和手持道具；手臂水平展开，腋下和袖子之间留出清晰间隙。
- 宽大袖子、外套和裙摆必须与身体分离，不能跨接到躯干或另一条腿。
- 手指并拢、掌心向下；双腿分开，脚尖平行向前。
- 若目标动作以跑动、持枪和近战为主，最终角色图优先使用自然半握拳或并拢手指。完全张开的五指在刚性手部策略下会一直保持张开；精细手指动画需要单独的手部修形和骨骼重定向阶段。

## 历史 UniRig / 模板兼容阶段

以下第 2-7 节只用于 POPBOT 等已有模板迁移资产。新角色默认跳过白模权重迁移，直接执行混元最终网格、Modal QRemeshify、Mixamo 最终 Skin 与动作合并。

### 2. 白模参考图

- 使用内置 `image_gen` 生成无服装轮廓、无头发、无兔耳、无耳机、无背包、无武器的中性建模服白模。
- 在当前 CPA/Sub2API 环境中，每次生成后都使用 `sub2api-image-generator` 恢复实际位图，再复制到仓库。
- 白模必须是成人比例、正/左/背严格正交、同尺度、同骨长、无透视。
- 组合图按清单中的显式像素裁框拆分，不能默认三等分。
- 裁图后检查手、脚和手臂没有被截断，侧视图脸朝向正确。

图像生成是 Codex/人工工作站阶段，Python CLI 不会自行调用聊天里的 `image_gen`。清单保存生成适配器、实际位图路径和提示词，以便复现与审计。

### 3. 白模 3D 重建

- 默认适配器：`hunyuan-multiview`。
- 当前 Modal 基线：L4 24GB、50 steps、380 octree resolution。
- 单图快速实验可切换 `triposr-single`；已有白模 GLB 可切换 `existing-glb`。
- 白模只需要可靠的身体比例和关节区域，不在此阶段花费高成本纹理额度。

### 4. 白模关节识别

- 默认适配器：`unirig`。
- UniRig 输出骨架 FBX 和可检查的关节 JSON。
- 自动检查骨架层级、左右镜像误差、上下臂和腿长差异、膝踝方向及关节是否位于网格包围盒内。
- 在 `tools/unirig-preview.html` 中叠加查看正面、侧面和背面。
- UniRig 骨架结构通过不等于最终蒙皮通过；左右映射和膝盖方向仍需人工批准。

### 5. 最终角色网格

`finalGeometry` 与白模重建独立，支持三种适配器：

- `hunyuan-multiview-variants`：从最终穿衣三视图分别生成 original/low GLB。
- `existing-variants`：使用已经生成并审核过的高模和低模。
- `reuse-reconstruction`：简单角色直接复用重建网格，适合单图实验，不适合复杂服装生产。

模板默认对 original 使用 380 octree resolution，对 low 使用 256。分辨率只影响几何采样上限，不保证低模预算；正式发布仍应增加确定性的 Blender 或 glTF Transform 简化步骤，并检查 UV、材质和蒙皮。

### 6. 最终角色自动蒙皮

通用人形默认使用 `finalSkin.adapter=unirig-shared`：

- 只对最高质量的最终角色网格运行一次 UniRig full，保留它生成的真实蒙皮权重、材质、UV 和纹理。
- 从同一份已蒙皮高模在 Modal CPU/Blender 中简化为 original/low，避免高低模分别支付 GPU 自动绑定成本。
- 从 UniRig 34/52 骨匿名拓扑自动识别 19 个核心人体关节，并生成角色自身的 rig profile。
- 白模 profile 只用于发现方向、骨长和比例异常；若与最终角色冲突，以最终角色自身关节为准。

### 7. 骨架与动作迁移

当前可用适配器：

- `hunyuan-component-aware`：POPBOT 已验证路径，使用专用关节 profile、连通分件识别和部位权重规则。
- `nearest-template`：快速实验路径，使用模板权重的空间近邻迁移。
- `none`：已有完整骨架与动作的 GLB 直接进入校验。
- `unirig-template-transfer`：把最终角色的 UniRig 权重按骨骼语义迁移到已审核动作模板。当前已支持 UniRig 34 骨和 52 骨拓扑，输出保留材质、UV、纹理和模板动作。

当前默认动作模板是内部资产 `generated/templates/space-commando-original.glb`，包含 78 根骨骼和 20 个动作，不作为 Wiki 角色发布。其他白模也可以作为模板，但必须先确认骨骼命名、父子层级、朝向、Bind Pose 和动作集合。

最终网格的权重规则：

- 手部刚性跟随手骨，避免手指插值尖刺。
- 鞋整体跟随足骨，只在窄脚踝区域混合，不给鞋头分配脚趾权重。
- 大腿和小腿采用分段权重，只在膝盖窄区域混合。
- 袖子只跟随上臂和前臂，禁止躯干权重跨到袖口。
- 外套主体分配给骨盆、腹部和胸部，下摆限制髋部过渡。
- 头发、耳朵、耳机、背包和武器使用独立骨链或刚性挂点。

`unirig-template-transfer` 默认使用 `fingerMode=rigid`，把手指稳定地绑定到手骨，优先避免拉丝和爆点。对 UniRig 52 骨输出，`handPose=relaxed` 会先使用源手指骨和源权重烘焙自然半握拳，再合并到目标手骨，因此动作中能保持稳定手型。34 骨输出没有可靠的五指链，会自动回退到源手型并在 GLB provenance 中记录 `source-fallback-34-bone`，仍需通过视觉门禁。`fingerMode=mapped` 只有在源手指骨朝向和动作模板 Bind Pose 已专门校准后才能启用；未经校准的逐骨映射会造成手指爆炸。需要逐指动画的角色仍应增加 Blender 手部修形/重定向阶段。`footMode=rigid-shoe` 会把鞋头权重合并到足骨，并对地面移动动作应用世界空间脚掌俯仰补偿，避免厚底鞋从中间折弯或在前摆相过度压脚尖；赤脚或需要脚趾弯曲的角色可改为 `articulated`。地面移动曲线还会对大腿、小腿和脚掌的孤立异常旋转关键帧执行阈值去尖峰，不对整段动作做统一降幅，以避免循环播放时周期性顿挫同时保留步幅。

### 8. 验证和发布

- 结构校验：可见网格、骨骼数、动作数、顶点属性、关节索引、权重归一化、三角面和文件大小。
- 浏览器验收：Bind Pose、跑、冲刺、翻滚、跳跃和射击。
- 直接拒绝：反膝、外八、脚踝塌陷、手指尖刺、大腿撕裂、袖子跨胸拉伸、地面穿透和方向反转。
- `tools/unirig-preview.html` 默认按当前动作模板的正面方向显示，并锁定水平/垂直 Root Motion，便于比较形变；传 `lockRoot=0` 可查看动作原始位移。
- `rig-final` 前必须显式传 `--approve-proxy`。
- `publish` 前必须显式传 `--approve-visual`。
- 已存在的 GLB 不会被覆盖，除非传 `--force`；已有 Wiki slug 还需要 `--replace-entry`。
- 发布会写入 SHA-256、文件大小、验证数据、生成适配器和权利边界的 provenance JSON。

## 统一 CLI

初始化新角色：

```bash
python3 scripts/character_asset_pipeline.py init \
  --slug new-character \
  --name '新角色' \
  --name-en 'New Character'
```

输出清单位于 `references/characters/new-character.json`。先填写设计图、白模裁框、最终角色三视图和 rig profile 路径。

初始化器会让 Risk of Rain 2 英雄使用 survivor 模板继承；`--kind enemy` 或其他 `--game` 会改为直接写目标游戏 catalog，避免把敌人错误注册为 Commando 衍生英雄。新游戏仍需先在 `games.json` 中登记 catalog。

校验清单结构：

```bash
python3 scripts/character_asset_pipeline.py validate \
  --manifest references/characters/new-character.json
```

同时检查当前输入文件是否已准备好：

```bash
python3 scripts/character_asset_pipeline.py validate \
  --manifest references/characters/new-character.json \
  --check-files
```

查看所有本地和 Modal 命令但不执行：

```bash
python3 scripts/character_asset_pipeline.py plan \
  --manifest references/characters/new-character.json
```

执行到最终动作结构校验，不自动发布：

```bash
python3 scripts/character_asset_pipeline.py run \
  --manifest references/characters/new-character.json \
  --stage all \
  --approve-proxy
```

已有缓存时跳过 GPU 重建：

```bash
python3 scripts/character_asset_pipeline.py run \
  --manifest references/characters/new-character.json \
  --stage all \
  --skip-generation \
  --approve-proxy
```

`--skip-generation` 会跳过重建、UniRig 骨架/完整蒙皮和最终网格生成等云端阶段，只执行本地裁图检查、缓存校验、profile 生成、动作模板迁移和最终验证。缺少缓存时会直接报错，不会静默启动 GPU。

浏览器验收后发布：

```bash
python3 scripts/character_asset_pipeline.py run \
  --manifest references/characters/new-character.json \
  --stage publish \
  --approve-visual
```

`run --stage all` 故意不包含 publish，防止结构校验通过但视觉形变失败的模型自动进入 Wiki。

## 适配器选择

| 目标 | 白模重建 | 最终网格 | 骨架/蒙皮 |
| --- | --- | --- | --- |
| 新人形角色生产 | 不需要白模 | 混元多视图 + Modal QRemeshify | Mixamo 直接绑定最终低模 |
| 最快单图实验 | `triposr-single` | `reuse-reconstruction` | `nearest-template` |
| 历史模板迁移 | `hunyuan-multiview` | `hunyuan-multiview-variants` | `unirig-shared` + `unirig-template-transfer` |
| 已有白模和角色 GLB | `existing-glb` | `existing-variants` | 任一迁移适配器 |
| 已有完整动画 GLB | `existing-glb` | `reuse-reconstruction` | `none` |

Hunyuan、TRELLIS、GoSkinning、UniRig 或后续模型都应作为适配器加入，统一清单、批准门禁、校验和发布契约保持不变。Mixamo、AccuRIG 可以作为动作来源或人工标记的对照工具，但当前自动语义映射的正式模板仍是 78 骨 Commando；接入新的 Mixamo/AccuRIG 骨架时，应先在 Blender 中重定向到标准动作模板，或新增明确的模板骨名适配器，不能只替换 FBX 就假定兼容。

### Mixamo 前向轴校准

- 完整问题总结与验收清单见 `references/mixamo-orientation-retarget-summary.md`。
- 上传标记页必须先看到角色正面。看到后脑、背部图案或脚跟时，不得继续放置关节点。
- POPBOT 白模自身以 `+Z` 为正面，`scripts/export_mixamo_upload.py` 默认保留该朝向；只有源模型确实背向 Mixamo 时才传 `--yaw-degrees 180`。
- 已经按反向白模生成的 Mixamo 动作可继续复用，但重定向时必须传 `--source-yaw-degrees 180`，将源世界旋转差和根位移统一转回目标角色坐标系。
- 180° 朝向修正后仍按解剖学映射左右骨骼：Mixamo Left 对应目标 `.l`，Mixamo Right 对应目标 `.r`，不能再用左右互换补偿朝向错误。
- 验收至少检查正面和侧面四个相位，确认脸朝跑动方向、前腿向前、后腿向后、膝盖与脚尖方向一致。

## Modal 成本与缓存

- 默认人形生产需要一次白模重建/骨架诊断（可选）、一至两次最终网格生成，以及一次最终角色 UniRig full。高低模共享这次 UniRig 结果。
- `skin-proxy` 不在 `all` 中，仅在需要单独判断 UniRig 蒙皮质量时运行。
- 已存在输出默认直接复用；只有 `--force` 才会重算。
- Modal 会复用已构建镜像和模型卷。缩容到零后下次主要支付冷启动、模型加载和实际 GPU 时间，不需要每次重装环境。
- 多个 Modal 账号可以复用同一份仓库、清单和脚本，但镜像缓存、Volume、Secret 和额度属于各自账号，不能跨账号直接共享。

## POPBOT 回归样例

- 已发布 v1 回归清单：`references/characters/popbot-hunyuan-v31-proxy-v1.json`
- 新白模组合图：`references/popbot-rig-proxy-turnaround-v2.png`
- 新白模裁图：`references/popbot-rig-proxy-v2/front.png`、`left.png`、`back.png`
- 新白模 v2 候选清单：`references/characters/popbot-hunyuan-v31-proxy-v2.json`
- 旧版阶段清单：`references/popbot-rig-proxy-pipeline-v1.json`
- 最终角色关节配置：`references/popbot-hunyuan-v31-tpose-v5-rig-profile.json`
- 分件权重与动作迁移：`scripts/rig_hunyuan_character.py`
- 动作约束验证：`scripts/validate_hunyuan_character.py`

旧的 `scripts/build_rig_proxy_character.py` 保留为 POPBOT 专用回归入口；新角色应使用 `scripts/character_asset_pipeline.py`。
