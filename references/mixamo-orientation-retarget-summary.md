# Mixamo 朝向反转与重定向问题总结

## 现象

- Mixamo 自动标记页显示的是角色背面，而参考图要求正面。
- Mixamo 网站里的动作本身看起来正常，但重定向到 POPBOT 后出现脸朝一边、腿向另一边，像是在倒着跑。
- 早期版本还出现左右手脚交换、手臂靠近头部和腿部方向异常。

## 已确认根因

1. 上传白模的前向轴与目标角色相差 180 度。Mixamo 下载骨架的动作空间和目标 UniRig/Commando 骨架不是同一个朝向。
2. 旧重定向把源骨骼的世界旋转变化直接当作目标骨骼局部旋转使用，隐含假设两套骨架的局部轴完全相同；该假设不成立。
3. 旧版本曾按画面空间交换左右骨骼，用来补偿背向上传。加入 180 度朝向修正后继续交换左右，会造成二次错误。
4. 最初对比截图不是同一相位：Mixamo 为第 10/16 帧，本地接近第 0 帧，使问题看起来比实际更混乱。

## 固定修复

- 源世界旋转差使用 `animated_world * inverse(rest_world)` 计算。
- 使用 `A * source_delta * inverse(A)` 把源旋转差转换到目标朝向；当前反向 Mixamo 源的 `A` 是 Y 轴 180 度。
- 源根位移使用同一个 `A` 旋转，不能只修骨骼旋转而保留反向位移。
- 左右骨骼始终按解剖学映射：Mixamo Left 对应目标 `.l`，Mixamo Right 对应目标 `.r`。
- 当前已下载的反向源动作统一使用 `--source-yaw-degrees 180`。
- 新上传包保持白模真实 `+Z` 正面，`scripts/export_mixamo_upload.py` 默认 `--yaw-degrees 0`。

## 上传与验收规则

1. Mixamo 标记页必须能看到脸、胸口和鞋尖；看到后脑、背部图案或脚跟时必须先旋转模型。
2. 游戏移动动作优先下载 `In Place` 或动作包的 `No Character`/`Without Skin` 版本。
3. 重定向后用正面和侧面检查同一个归一化时间点，至少检查 0.125、0.375、0.625、0.875 四个相位。
4. 正向跑应满足：脸朝前、胸口前倾、前腿向前、后腿向后、左右手臂交替。
5. 左右平移动作必须成镜像关系，不能通过交换骨名修正朝向。

## 当前 Mixamo 移动动作集

- `Mixamo_Idle`
- `Mixamo_Jump`
- `Mixamo_WalkForward`
- `Mixamo_WalkBackward`
- `Mixamo_WalkLeft`
- `Mixamo_WalkRight`
- `Mixamo_RunForward`
- `Mixamo_RunBackward`
- `Mixamo_RunLeft`
- `Mixamo_RunRight`
- `Mixamo_TurnLeft`
- `Mixamo_TurnRight`
- `Mixamo_TurnLeft90`
- `Mixamo_TurnRight90`
- `Mixamo_SprintForward`

候选模型：`generated/popbot-hunyuan-v31-mixamo-locomotion-120k.glb`。该文件保留原有 20 个动作并新增 15 个 Mixamo 动作，共 35 个动画。
