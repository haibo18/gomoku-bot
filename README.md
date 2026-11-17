# 五子棋自动对弈机器人（Gomoku Bot）

通过 `adb` 截图识别手机端五子棋 App 盘面，调用 **Rapfi** 五子棋引擎计算最优落点，再回写点击落子的自动化闭环。

- 目标 App：`com.wedobest.fivechess.huawei`（人机模式）
- 设备：HUAWEI FOA-AL00，1084×2412，density 480
- 引擎：Rapfi `250615`（Piskvork / Gomocup 协议）

## 目录结构

```
├── gomoku_bot.py          # 主机器人：截图→识别盘面→引擎算点→adb 落子→校验
├── 自检.py                # 算法/判定/异常兜底自检（不操作手机）
├── 生成棋盘坐标.py         # 由实测像素生成 15×15 交叉点坐标 JSON
├── 紧凑化.py              # 坐标 JSON 紧凑格式化工具
├── 五子棋棋盘坐标.json      # 原始坐标（逐条线实测值）
├── 五子棋棋盘坐标.紧凑版.json
├── 五子棋棋盘坐标.txt
├── 自动对弈使用说明.md      # 闭环流程、落子手势、弹窗规则、实测坐标
├── 对局日志.txt
├── 对局截图/               # 历次对局 / 胜负 / 失败现场截图
├── engine/                 # Rapfi 引擎本体、权重、客户端与官方说明
│   ├── rapfi_client.py     # 引擎命令行封装：给盘面→回落点+手机像素坐标
│   ├── config.toml
│   ├── pbrain-rapfi-*      # 各指令集 / 各平台可执行文件
│   ├── mix9svq*.bin.lz4    # 神经网络权重
│   └── README.zh-CN.md
└── requirements.txt
```

## 环境准备

1. Python 3，安装依赖：

   ```powershell
   pip install -r requirements.txt
   ```

2. 安卓调试：`adb`（本机位于 `scrcpy` 目录，路径见 `gomoku_bot.py` 顶部常量），设备开启 USB 调试并已授权。
3. 引擎：`engine/` 目录下已含 Windows SSE 版（本机 Xeon E5-2680 v2 仅支持 SSE）及各平台版本、权重。

## 运行

```powershell
# 执黑下第一步
python gomoku_bot.py --color B --time 2000

# 执白
python gomoku_bot.py --color W --time 3000

# 只识别盘面，不落子
python gomoku_bot.py --dry-run

# 只测线 + 识别一次即退出
python gomoku_bot.py --probe-only

# 跑自检（不碰手机）
python 自检.py
```

引擎单独调用示例：

```powershell
cd engine
python rapfi_client.py --moves "H8:B,I8:W,G8:B,F8:W" --engine-color W --time 3000 --threads 8
```

## 版本

- 当前版本：`v0.1.0`（见 [CHANGELOG.md](CHANGELOG.md)）
- 安卓安装包：随 Release 附件分发（`五子棋.apk` 体积超过 git 单文件建议上限，不纳入仓库历史）。

## 致谢与许可证

- 引擎来源：[dotb1ue/rapfi](https://github.com/dotb1ue/rapfi)（Rapfi），引擎二进制与权重版权归原作者所有，详见 `engine/README.zh-CN.md` 与 `engine/AUTHORS`。
- 本仓库自有脚本（`gomoku_bot.py`、`自检.py`、`rapfi_client.py` 及坐标工具）为自动化适配代码。
