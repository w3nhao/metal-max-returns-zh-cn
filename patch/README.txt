重装机兵 Returns 简体中文修订 0.1.0（公开测试版）

输入：未修改的日版，4,194,304字节，不带512字节复制头。
补丁：METAL_MAX_RETURNS_JP_TO_ZH.bps

用支持BPS的工具应用，或使用Python 3：
python apply_patch.py "Metal Max Returns (Japan).sfc" "Metal Max Returns (Chinese).sfc"

输入也支持包含唯一.sfc/.smc的ZIP，脚本不覆盖已有输出。
本补丁不适用于旧中文ROM。游戏文件需自行准备。

这是公开测试版，部分特殊事件和库存边界尚待验证。
具体范围：https://github.com/w3nhao/metal-max-returns-zh-cn
