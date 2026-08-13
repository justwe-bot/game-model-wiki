# Game Model Wiki

面向多个游戏的轻量 3D 模型 Wiki。模型来自本机 Steam 安装，并按游戏拆分目录和目录数据。

根页面通过游戏选择器切换资源池，共用 Three.js 查看器、移动端加载策略和模型信息结构。游戏级配置位于 `games.json`，每个新增游戏使用独立的 `games/<game-slug>/catalog.json`。

## 当前游戏

- `risk-of-rain-2`：20 个怪物和 23 张地图，支持模型对比、骨骼动作与独立地图浏览
- `hades-2`：已接入 14 个敌人，新增蹉跎者、拉米亚和蹒跚者，合计 173 组动作
- `custom-models`：自定义模型资源池，内部按英雄和怪物分类；当前收录 1 位腾讯混元 3D Web V3.1 + Mixamo 动画英雄
- `ultimate-pack`：5 个已购买 Unity 资源包的本地 Wiki，收录 3,831 个模型条目和 45,304 组动作元数据；商业模型与贴图不进入公开仓库

Ultimate Pack 的本地构建、动作导入和许可边界见
[`games/ultimate-pack/README.md`](games/ultimate-pack/README.md)。

《哈迪斯 II》的详细格式结论和移动低模准入标准见 [`games/hades-2/ANALYSIS.md`](games/hades-2/ANALYSIS.md)。

## 当前内容

- 34 个可浏览怪物，均包含游戏原始模型和移动端保形低模 GLB
- 保留 UV、骨骼和可用动画；Hades II 已接入 13 个敌人的游戏颜色贴图，发布包中未提供基础贴图的潜伏者继续使用材质配置恢复基础色
- 技能卡片可播放对应攻击动作，并提供中文攻击形式、距离和说明
- 支持搜索、体型筛选、原模/低模/并排对比、触控旋转和缩放
- 适配桌面与手机浏览器
- 手机端默认只加载当前条目的低模；普通模式同时保留 1 个模型，对比模式最多 2 个
- 手机端使用 1 倍渲染像素比，画布离开屏幕或标签页隐藏时暂停渲染
- 石傀儡使用独立发光贴图显示红眼；黏土圣堂武士接入法线贴图以恢复炮体和护甲细节
- 独立地图页支持搜索、扩展包筛选、自动旋转、俯视和重置视角
- 23 张地图均使用静态场景 GLB；其中 9 张已接入岩石、草地和泥土地形纹理的三平面混合材质

低模使用误差受限简化。10% 只是期望目标，不会为了达到固定比例强行删除脚、细腿、角、炮口或其他关键轮廓。不同模型会在接近明显变形前停止，因此实际保留比例约为 23%-79%。

低模贴图使用 256x256、96 色 RGB 像素化处理，并保留 UV；透明模型会单独保留 Alpha。页面采用最近邻采样和平面着色强化低多边形观感。

## 本机预览

在此目录启动静态文件服务器，然后访问 `index.html`：

```powershell
python -m http.server 4173
```

当前开发服务地址为 `http://127.0.0.1:4173/`。

雨中冒险 2 地图页地址为 `http://127.0.0.1:4173/maps.html`。

可用 `?game=hades-2` 直接打开《哈迪斯 II》资源池，例如
`http://127.0.0.1:4173/?game=hades-2`。
追加 `monster=<slug>` 可直接打开具体条目，例如
`http://127.0.0.1:4173/?game=hades-2&monster=jellyfish`。

自定义模型资源池可直接打开：
`http://127.0.0.1:4173/?game=custom-models&monster=nova-jet-sentinel`。

## Hades II 构建链

- `tools/hades2-granny-rebuild`：结合共享 SDB 重建 GPK 中的标准 GR2。
- `tools/hades2-granny-export`：通过游戏自带 `granny2_x64.dll` 提取网格、骨骼、蒙皮和动作到 H2GX。
- `scripts/build_hades2_glb.py`：在 Blender 中生成原模与移动低模 GLB，排除 Outline/ShadowMesh 辅助网格，并把低模动作降采样到 15 FPS。
- `scripts/build_hades2_asset.ps1`：对一个 SDB/GPK 角色执行重建、动作采样和两档 GLB 生成的完整单命令流程。
- `scripts/validate_hades2_glbs.py`：把目录中的两档 GLB 重新导入 Blender，核对三角面和动作是否与目录一致。

当前 Hades II 池包含 14 个怪物、28 个 GLB 和 173 组动作。查看器支持按条目配置初始朝向，并会跟随动作根位移，保证位移攻击和悬浮动作留在镜头内，但不会删除 GLB 内的原始动作数据。13 个条目使用从游戏 1080p 资源包提取的颜色贴图，并为移动端生成 256x256 版本。

## Risk of Rain 2 英雄构建链

- `scripts/extract_ror2_survivors.py`：读取本机 Addressables bundle，定位 17 位英雄的默认模型 prefab，恢复 Transform 层级，并将 SkinnedMeshRenderer 按骨骼和 bind pose 烘焙为完整模型。
- `scripts/build_ror2_survivor_glb.py`：为烘焙网格生成原模与 50% 移动低模 GLB，并写入三角面和文件大小统计。
- `scripts/build_ror2_bandit_game_rig.py`：从本机 Addressables bundle 直接写出盗贼的 98 根游戏原始骨骼、bind pose、顶点权重与 8 个游戏动画。
- `scripts/build_ror2_commando_game_rig.py`：保留突击兵的 78 根游戏原始骨骼、双枪挂点、蒙皮权重，并从 Unity `.anim` 曲线生成 18 个游戏动作。
- `scripts/build_ror2_bandit_low_glb.py`：在 Armature 之前简化盗贼网格，保留原始骨架、权重和动作生成移动低模。
- `scripts/validate_ror2_bandit_game_rig.py`：校验盗贼原模与低模的原始骨架来源、蒙皮权重、inverse bind matrices 和动画通道。
- `scripts/validate_ror2_commando_game_rig.py`：校验突击兵原模与低模的 78 骨骼、双枪、蒙皮权重和 18 个动作。
- `scripts/build_space_commando.py`：以突击兵原始蒙皮身体、双枪、骨骼和动作作为基准，重新材质化内层压力服并生成宇航机甲护甲、封闭头盔和喷气背包。
- `scripts/validate_space_commando.py`：校验宇宙突击兵的 78 骨骼、18 个动作、蒙皮权重、双枪挂点和原创材质边界。
- `references/space-commando-concept.png`：宇宙突击兵重构所依据的正反面设计稿。
- `scripts/build_popbot_pro.py`：依据 POPBOT_PRO_01 角色设计稿，在突击兵骨架上程序化生成兔耳头部、宽松夹克、撞色鞋袜、背部装置和大型枪械，并把原模与低模硬限制在 10,000 三角面以内。
- `scripts/validate_popbot_pro.py`：校验兔耳重炮手的三角面上限、78 骨骼、18 个动作、蒙皮权重、武器挂点和全部程序化分件。
- `references/popbot-pro-concept.png`：兔耳重炮手重构所依据的正侧背角色设计稿。
- `cloud/modal_triposr.py`：在 Modal L4 上按需运行 TripoSR，复用缓存容器镜像和持久模型卷，从角色图片生成带顶点色的原始 GLB。
- `scripts/build_rig_proxy_character.py`：按清单执行“白模三视图 -> Modal 多视图重建 -> UniRig 关节检查 -> 78 骨骼动作迁移 -> 变形验证 -> Wiki 发布”的可缓存分阶段流程，默认拒绝覆盖并要求视觉验收后才能发布。
- `references/character-production-pipeline.md`：记录设计图到可动画 GLB 的白模校准、分件权重、动作质量门槛和 Modal 额度使用策略。
- `references/image-hunyuan3d-mixamo-workflow.md`：记录新默认“生图 -> 混元高模 -> Blender/QRemeshify 网格规整与烘焙 -> Mixamo 最终蒙皮与动作 -> 动画 GLB/FBX”的实操流程，并保留历史重定向兼容说明。
- `cloud/modal_character_mesh.py`：在现有 Modal 套餐中运行 Blender 4.2 与 QRemeshify，对混元高模执行保守修复、T Pose/非流形门禁、四边重拓扑、UV 和高低模贴图烘焙，并输出 Mixamo 上传 FBX。
- `cloud/modal_mixamo_character.py`：把 Mixamo 下载的 With Skin 基础 FBX 与 Without Skin 动作包合并为同时带网格、蒙皮和多动作的 GLB/FBX，并统一角色高度、脚底原点和动作骨骼路径。
- `scripts/mixamo_character_pipeline.py`：新角色默认入口，提供 `prepare`、`finalize` 和 `validate` 三个阶段。
- `scripts/validate_mixamo_character.py`：校验最终 Mixamo 角色的核心人形骨骼、蒙皮网格、关节索引、归一化权重和动画关键帧。
- `scripts/rig_generated_character.py`：把 TripoSR 网格转为 Y-up，并迁移宇宙突击兵的 78 根骨骼、蒙皮权重和 20 个动作；支持把融合道具区域显式刚性绑定到左手或右手。
- `scripts/build_image_to_wiki_model.py`：单命令完成图片生成、骨骼动作迁移、结构校验、实验条目登记和主目录合并。
- `scripts/validate_generated_character.py`：校验图片生成模型的可见蒙皮网格、顶点色、法线、78 骨骼、20 个动作和归一化权重。
- `scripts/merge_ror2_survivors.py`：把英雄生成清单合并到主目录，保留原有怪物与其他游戏条目。

当前英雄池包含 18 位英雄、36 个 GLB，其中 17 位来自游戏资源提取，1 位是通过生图、混元 3D、UniRig 与 Mixamo 工作流生成的自定义角色。每个英雄均使用明确指定的默认 prefab 根节点，普通挂件与蒙皮分件会在同一世界姿态中合并，避免只显示腿部、散落武器或堆叠网格。

英雄材质默认按不透明方式导出，避免把游戏贴图中用于材质遮罩的 Alpha 误当成身体透明度；明确使用透明裁切的分件会单独保留 Alpha。提取器也支持 Unity 省略恒定权重的单骨骼顶点格式，并允许像 MUL-T 这样使用独立 MasterAnims 骨架的模型从展示姿态烘焙，而不是输出拆散的 bind pose。对于 MasterAnims 参考姿态仍不自然的角色，可以从 AssetRipper 还原的 `.anim` Transform 曲线采样展示帧；盗贼当前使用 `Bandit_SelectPoseIdle` 的首帧烘焙，并在烘焙结果上附加明确标记的重建建模骨架。

## 模型规模

| 怪物 | 原模三角面 | 低模三角面 | 保留比例 |
| --- | ---: | ---: | ---: |
| 甲壳虫 | 4,560 | 2,566 | 56% |
| 利莫里亚 | 3,586 | 2,836 | 79% |
| 石傀儡 | 4,048 | 2,674 | 66% |
| 小恶魔 | 2,436 | 1,362 | 56% |
| 巨角野牛 | 6,864 | 5,358 | 78% |
| 巨型幽魂 | 2,508 | 1,930 | 77% |
| 甲壳虫女王 | 17,944 | 5,317 | 30% |
| 黏土沙丘行者 | 12,104 | 9,492 | 78% |
| 甲壳虫守卫 | 13,080 | 4,298 | 33% |
| 远古利莫里亚 | 8,808 | 5,147 | 58% |
| 黏土圣堂武士 | 9,928 | 6,978 | 70% |
| 寄居蟹 | 9,034 | 5,718 | 63% |
| 水母 | 1,518 | 1,166 | 77% |
| 游荡者 | 7,270 | 3,620 | 50% |
| 小恶魔霸主 | 8,904 | 4,461 | 50% |
| 石巨人 | 15,526 | 9,560 | 62% |
| 合金秃鹫 | 8,146 | 1,876 | 23% |
| 黄铜装置 | 2,742 | 2,162 | 79% |
| 月球傀儡 | 9,118 | 5,270 | 58% |
| 月球幽魂 | 2,852 | 2,100 | 74% |
| 鼠兽（Hades II） | 3,686 | 2,136 | 58% |
| 鱼群怪（Hades II） | 906 | 652 | 72% |
| 水母（Hades II） | 1,134 | 850 | 75% |
| 自动机射手（Hades II） | 2,245 | 1,459 | 65% |
| 割喉者（Hades II） | 3,685 | 2,284 | 62% |
| 潜伏者（Hades II） | 8,316 | 5,320 | 64% |
| 海河马（Hades II） | 4,050 | 2,589 | 64% |
| 利爪鹰身女妖（Hades II） | 4,822 | 2,989 | 62% |
| 哀嚎者（Hades II） | 7,988 | 5,111 | 64% |
| 进水骷髅头（Hades II） | 1,555 | 1,057 | 68% |
| 海蛇（Hades II） | 3,808 | 2,513 | 66% |
| 蹉跎者（Hades II） | 4,001 | 2,719 | 68% |
| 拉米亚（Hades II） | 8,280 | 5,464 | 66% |
| 蹒跚者（Hades II） | 1,694 | 1,151 | 68% |

## 验证状态

- 51 个角色原模和 51 个角色低模均包含有效网格和三角面
- 23 个地图 GLB 均包含有效网格和三角面；9 张主题地图已恢复游戏地形纹理
- 所有低模均保留 UV；怪物模型保留动作所需的蒙皮属性，英雄模型保留完整姿态，盗贼和突击兵额外保留游戏原始骨骼、蒙皮与动作
- 动态怪物条目的默认动作和技能动作都存在于原模与低模 GLB
- 月球傀儡与月球幽魂的三项技能动作已在浏览器中逐项播放验证
- 甲壳虫女王、巨角野牛等细肢模型已用并排对比检查，低模未再出现缺脚问题
- Hades II 的 14 个条目均有原模、低模和目录中列出的骨骼动作；冲撞、砸地、飞行、长躯干卷曲、施法和巨斧攻击的极端姿态均保留完整可见本体
- 鱼群怪的鳍和上下颚、水母的全部触手、自动机的分离面板、鹰身女妖的羽翼和鱼人的武器均按低模完整性标准保留
- Risk of Rain 2 的 17 位游戏提取英雄已逐项加载验证；3 位自定义角色额外验证了原始骨骼继承、动作播放或程序化分件，图片生成实验体已检查绑定姿态、移动动作、重炮动作和原模/低模加载
- 英雄页面已在 390x844 移动视口验证，无横向溢出、模型空白或工具栏遮挡

## 已知边界

- 技能按钮播放的是模型骨骼动作。火球、激光、火焰、光晕、冲击波等独立粒子/VFX 不包含在角色 GLB 中。
- 少数游戏资源把静态网格和动作骨架拆在不同包中，需要额外的骨架与蒙皮合并管线，当前不会用空骨架冒充完整模型。
- 巨型幽魂与黏土沙丘行者的 FBX 导出缺少部分蒙皮权重，构建管线会为缺失顶点补充刚性权重，适合 Wiki 预览，但不等同于游戏项目中的精确蒙皮数据。
- 除盗贼和突击兵外，英雄当前使用默认姿态的静态烘焙模型，不包含 Animator 动作、技能特效或可切换皮肤；盗贼保留 98 骨骼和 8 个动作，突击兵保留 78 骨骼、双枪挂点和 18 个动作，但仍不包含运行时 Animator 状态机、IK 或技能特效。
- 《哈迪斯 II》优化资源由共享字符串库 `.sdb` 与角色 Packfile `.gpk` 组成，必须先重建为标准 GR2；仓库不包含原始 GPK/SDB、游戏 DLL、临时 GR2 或 H2GX 桥接文件。
- 地图结构预览不包含天空盒、粒子、动态机关、植被实例和烘焙光照；部分运行时拼装场景仍使用结构配色。

## 素材说明

游戏模型和贴图的权利归原权利方所有。这个目录适合作为本机研究、个人原型和技术验证；公开发布或提供下载前，请核对游戏 EULA、商标和素材使用许可，不要分发原始 bundle、FBX 或原尺寸贴图。
