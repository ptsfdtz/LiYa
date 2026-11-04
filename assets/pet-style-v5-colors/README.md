# 原版配色统一

本轮解决各状态色调不统一的问题，唯一配色基准为用户选择的原版抱尾图及其已验收动作条：`assets/pet-style-v4/reference-sleepy.png`、`qa/rows/sleepy/row-clean.png`。

保留该基准本身，使用内置图像工具对其余14条原动作条做仅颜色修正：银白淡紫头发、低饱和青绿角/尾/衣料、象牙白布料、柔和金边和一致的肤色与光照。校色不以不同情绪改变整张图的色温、明暗或饱和度，害羞的局部脸红仍保留。原有八帧动作顺序、表情、圆润比例和单尾造型通过逐行目视检查。

源图位于 `decoded/`，精确校色提示词位于 `prompts/`，图像工具输出的颜色/轮廓复核位于 `qa/<state>/`，随后实际提帧与检查报告位于 `qa/rows/<state>/`。原v4动作条与文件仍保留，不覆盖其历史记录。

`shared-palette.json` 从已验收抱尾像素提取253个角色颜色，另外保留透明索引、预览背景和文字颜色。最终图集、静态图和全部GIF从同一份色表编码，无逐帧自适应色表或抖色。图像工具负责源颜色修正；编码器只进行固定色表量化，不制造动作或几何变化。

所有15种、120个GIF帧的色表哈希必须完全相同；另检查每个不透明像素位置均保留，避免透明索引吞掉深色轮廓。静态PNG的所有不透明RGB颜色来自同一张色表，且插件/设计文件逐字节一致。

重建沿用 `scripts/qq_pet_frames.py <state>` 与 `scripts/assemble_pet_style_set.py`，当前默认处理本目录。最终素材仍安装到 `assets/expressions/` 和 `src/liya_companion/assets/`。这次没有同步服务器。

见 `validation/pet-style-v5-color-validation.json` 与 `validation/pet-style-v5-color-palette-audit.json`。
