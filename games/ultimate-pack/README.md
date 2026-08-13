# Ultimate Pack 本地资源说明

此目录在公开仓库中只保存资源包统计和不可下载的模型/动作清单。购买的
GLB、FBX、贴图、Unity 原始包以及完整本地目录不会提交到 Git。

## 本地生成

1. 将已购买的 Unity 资源包放在 `C:\baidunetdiskdownload` 下，并按需要先用
   `scripts/extract_unitypackage.py` 解包。
2. 使用本机 FBX 转换器运行 `scripts/build_ultimate_pack.py`。完整目录会写入
   `games/ultimate-pack/catalog.local.json`，模型和贴图分别写入
   `models/ultimate-pack` 与 `textures/ultimate-pack`。
3. 启动 Wiki。页面会优先读取本地完整目录；该文件不存在时，会自动回退到
   `inventory.json`，此时仍可浏览分类和动作名称，但不能下载或播放模型。

构建器会合并资源包中可用的 FBX 动作，并记录动作适用的原模/低模版本。请勿
移除 `.gitignore` 中的 Ultimate Pack 规则，也不要强制添加购买素材到公开仓库。

## 许可边界

这些资源包允许在许可范围内用于项目，但原素材禁止转售或再分发。发布游戏或
其他成品前仍需逐个核对对应资源包的许可条款，并保证素材无法作为独立资源被提取。
