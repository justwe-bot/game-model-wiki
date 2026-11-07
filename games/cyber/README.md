# Cyber

本分类收录从本机安装的游戏中提取、导出并在 Blender 中整理的 REDengine 4 战斗角色资产，共 **29 套角色外观**。

> **验收状态：0/29 套角色通过完整验收，0/116 类动作通过验收。** 本目录是资源归档与索引，不是已完成的质量成果。详见下方「验收状态」。

## 目录结构

每套角色**保留源工程的九分区结构**，不做拍平或重命名：

```text
games/cyber/
├── catalog.json                 # 29 套角色的结构化索引
├── README.md                    # 本文件
├── characters/
│   ├── MaxTac/      (6 套)
│   ├── KangTao/     (19 套)
│   └── TraumaTeam/  (4 套)
│       └── <catalog_id>/
│           ├── 01_models/                模型与静态 Blender 工程
│           ├── 02_textures_materials/    材质配置与贴图引用
│           ├── 03_rigs/                  骨架
│           ├── 04_animations/            动画 GLB
│           ├── 05_weapons/               武器
│           ├── 06_renders/               渲染截图
│           └── 07_recordings/            动作录像（MP4）
├── weapon_audio/                  42 把武器的原始 Wwise 音频叶
│                                  （1,991.7 MB / 21,052 文件，未验收）
└── shared/
    ├── soldier_material_depot/   共享材质库（1,849.8 MB / 30,704 文件）
    └── hq_material_depot/        高精度共享材质库（166.5 MB / 872 文件）
```

角色标识（`catalog_id`）沿用源工程的命名，例如 `max_tac--max_tac_soldier_01`、`corpo__kangtao_ma--ranger__lvl1_01`。每套角色的游戏内身份（APP 路径、entity 路径、外观名）记录在 `catalog.json` 的 `identity` 字段中。

## 收录范围

| 分区 | 内容 | 是否收录 |
|---|---|---|
| `01_models` | 分件模型 GLB、静态 Blender 工程 | ✅ |
| `02_textures_materials` | 材质 sidecar、贴图引用 | ✅ |
| `03_rigs` | 骨架 | ✅ |
| `04_animations` | 动画 GLB | ✅ |
| `05_weapons` | 武器 | ✅ |
| `06_renders` | 渲染截图 | ✅ |
| `07_recordings` | 动作录像 MP4 | ✅ |
| `08_reports` | 资源索引、诊断报告、来源 manifest | ❌ 未收录 |
| `09_sources` | 来源清单 | ❌ 未收录 |

`08_reports` / `09_sources` 留在源工程 `C:\Code\UeCover\Blender\characters\` 中，未被移动或删除。`catalog.json` 的 `identity` 字段已从源 manifest 中提取关键身份信息，因此本目录不依赖源工程即可识别每套角色。

## 共享材质库的处理（重要）

源工程中每套角色的 `02_textures_materials` 下**并非各自持有一份贴图**，而是通过 **299 个目录联接（Junction）** 指向两个共享材质库：

| 源联接 | 指向 |
|---|---|
| `02_textures_materials/shared_material_depot` | `Blender\hq_sample\material_depot` |
| `02_textures_materials/resources_*/catalog_material_depot` | `Blender\soldier_catalog\material_depot` |

如果直接复制，共享库会被展开 29 倍。因此本次迁移：

1. 复制时使用 `robocopy /XJ` **跳过全部目录联接**（已核验目标目录残留联接数为 0）；
2. 把两个共享库**各复制一份**到 `shared/`；
3. 因此各角色 `02_textures_materials` 中**不包含贴图本体**，需要贴图时请到 `shared/` 下按 depot 相对路径查找。

`soldier_material_depot` 后来并入了一批**材质依赖恢复 overlay**（来自源工程 `recovered_20261107_v2_all8/material_depot`），新增 24,154 个文件、把该库从 6,550 个文件扩到 30,704 个。这次合并是**纯增量**：仓库里原有的 4,198 个文件一个没有被覆盖；与源库同名的 2,352 个文件中，569 个仅差 WolvenKit 的 `ExportedDateTime` 导出时间戳、其余 1,783 个字节完全相同，因此保留仓库内版本。该 overlay 只补齐缺失的材质依赖，**不构成角色/材质/视觉/动作验收**。

## 武器音频（未验收）

`weapon_audio/` 收录 **42 把武器**的原始 Wwise 音频叶，整批迁自源工程 `Blender/soldier_catalog/weapon_audio`，保留每个武器的原始目录结构：

```text
weapon_audio/
├── weapon_sounds_batch_20261107_171449.json     批次汇总（42 条结果）
├── _migration_manifest.json                     本次迁移清单（来源、逐武器文件数）
└── weapon_sounds_<code>_20261107_171449/
    ├── attribution.json      归属与来源证据（归档 sha256、事件数、解析叶片数）
    ├── event_routes.json     事件 → 叶片路由
    ├── packs/                该武器需要的 .opuspak 容器
    └── wav/                  .wav（PCM 原始）+ .opus（压缩副本）
```

**边界（必须随资产一起读）：**

- 这些是**原始 Wwise 音频叶**。选择器、随机化、增益、RTPC、空间化与分层**均未求值**，因此单个叶子**不是**最终运行时事件混音，也不主张任何单发时序或射速。
- **从未人工试听**，`accepted` 全为 `false`。
- 仓库其他游戏的音效统一存 `sounds/<slug>/*.m4a`；本机没有 ffmpeg / afconvert，无法转换，因此这里按原始 `wav` + `opus` 形态入库，**不符合**既有 m4a 约定。
- 42 个武器目录共 21,050 个音频文件，其中 242 个 `.opuspak` 只有 79 种不同内容（git 按内容去重，实际增量约 77 MB）。

## 浏览方式

本分类**尚未接入**根目录的 Three.js 查看器（`index.html` + `games.json`）。原因是查看器要求每个条目提供**单个整体装配 GLB**（`models.original` / `models.low`），而源工程目前只有分件 GLB，完整静态装配尚未验收通过。在 `games.json` 中登记本分类会使查看器加载不到 `models` 字段。

当前请直接浏览目录，或用 `catalog.json` 中的 `renders` / `recordings` 路径定位截图与录像。

`catalog.json` 主要字段：

- `characters[].identity` — 游戏内身份（APP、entity、外观名）
- `characters[].partitions` — 各分区文件数与字节数
- `characters[].summary` — 模型/动画/骨架/武器/截图/录像数量
- `characters[].renders` / `.recordings` — 可直接打开的媒体相对路径
- `shared` — 两个共享材质库的文件数与字节数
- `weaponAudio` — 武器音频批次的来源、规模、格式分布、`accepted=false` 与边界说明
- `acceptance` — 验收计数（角色与动作均为 0；武器音频 0/42；材质恢复未通过）

## 验收状态

沿用源工程 `Blender/soldier_catalog/STATUS.md` 与 `Blender/characters/README.md` 的结论：

- **0/29 套角色**通过完整静态装配与材质验收。
- **0/116 类动作**通过验收。
- 已有录像为**诊断产物**，不是游戏级还原：射击声音为编辑配音，MaxTac 枪口闪光为艺术重建（非原版粒子）。
- 18 套角色缺少完整静态预览。
- 受击候选持枪手型不兼容，没有合格受击录像。
- 小腿装备源默认 DEVELOP 材质问题未解决。

本次新增的两批资源同样**未验收**：

- **武器音频 0/42**：原始 Wwise 音频叶，运行时混音未求值，从未人工试听。
- **材质依赖恢复未通过**：仅补齐缺失依赖，`recovery_report_v2.json` 自报 `accepted=false`，且它服务的 `animal_boss` 等身份仍被 `Unimplemented bound-space` 阻塞，恢复后没有任何新身份通过装配。

请勿把本目录的完整性理解为质量通过。

## 来源与许可

- 提取自本机安装的游戏副本（2.31），工具链为 WolvenKit（REDengine 归档解析）+ Blender 导入插件。
- 源工程：`C:\Code\UeCover\`（本次迁移为**复制**，源工程未被移动、删除或覆盖）。
- **资产仅用于个人学习与研究，请勿分发模型、贴图、动画或衍生文件。**
