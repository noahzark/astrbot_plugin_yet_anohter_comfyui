<div align="center">

# Yet Another ComfyUI

_⭐ 在 AstrBot 里调用 ComfyUI 工作流进行绘图的插件 ⭐_  
_⭐ 文生图、图生图、超分、打标、视频、音频……ComfyUI 能做到的它都可以 ⭐_  
_⭐ 支持多后端，可以同时使用多个后端生图 ⭐_

<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue"></a>
<a href="https://github.com/AstrBotDevs/AstrBot"><img src="https://img.shields.io/badge/AstrBot-4.25+-purple"></a>
<a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue"></a>

</div>

---

> 本插件移植自 [nonebot-plugin-comfyui](https://github.com/DiaoDaiaChan/nonebot-plugin-comfyui)。
> 工作流文件格式（`<名称>.json` + `<名称>_reflex.json`）与 NoneBot 版本完全一致，原来的工作流可以直接拿来用。
> 队列管理、帮助卡片、LLM 工具等命令仍在迁移中；`nonebot_plugin_comfyui/` 目录在迁移完成前继续保留。

## 📑 目录

- [介绍](#-介绍)
- [安装](#-安装)
- [配置](#️-配置)
- [使用](#-使用)
- [图片审核](#️-图片审核)
- [提示词审核](#-提示词审核)
- [发送方式](#-发送方式)
- [按群设置](#-按群设置)
- [文档](#-文档)
- [免责声明](#-免责声明)
- [特别鸣谢](#-特别鸣谢)

---

## ⭐ 介绍

- 🎨 **任何工作流都能用**：在 ComfyUI 里导出 API 格式的工作流，配一个 reflex 文件，就能在聊天里调用
- 🔧 **Reflex 节点控制**：用 reflex 文件把命令参数映射到任意节点，还能把工作流注册成独立命令（如 `/超分`、`/打标`）
- 🚀 **多后端**：自动选择空闲后端，也可以手动指定；`-con` 让多个后端并发生图
- 🖼️ **混合输出**：一个工作流可以同时输出多张图片、文字、视频和音频
- 🛡️ **审核**：图片审核（远程 tagger、本地 WD14、本地 NudeNet）和提示词审核（AstrBot 的模型）
- ⏱️ **频率限制**：冷却时间、每人每日上限、单个工作流的每日次数、参数上限

![工作流命令示例](./docs/image/command2.png)
![并发生图示例](./docs/image/con.png)

---

## 💿 安装

1. 在 AstrBot 管理面板的插件市场里搜索安装，或者把本仓库克隆到 AstrBot 的 `data/plugins/` 目录：

   ```bash
   cd data/plugins
   git clone https://github.com/noahzark/astrbot_plugin_yet_anohter_comfyui
   ```

2. 重启 AstrBot（或在面板里重载插件），AstrBot 会按 `requirements.txt` 安装依赖。
3. 在插件配置里填好 ComfyUI 后端地址。

首次启动时，插件会把 `build_in_wf/` 里的内置工作流复制到
`data/plugin_data/astrbot_plugin_yet_anohter_comfyui/workflows/`。自己的工作流放进这个目录，
然后发送 `/comfy reload` 即可生效，不需要重启。

### 可选依赖

只有使用本地审核时才需要，由管理员在 AstrBot 的 Python 环境里自行安装：

| 审核方式 | 依赖 |
|:---|:---|
| 本地 WD14 | `onnxruntime`（GPU 推理用 `onnxruntime-gpu`）、`pandas`、`huggingface_hub` |
| 本地 NudeNet | `nudenet` |

---

## ⚙️ 配置

所有配置都在 AstrBot 管理面板的插件配置页里修改，每一项都有说明。

| 分组 | 主要配置 |
|:---|:---|
| ComfyUI 后端 | 后端地址列表（**必填**）、探测超时、任务超时、HTTP 代理、默认模型 |
| 工作流 | 工作流目录（留空用插件数据目录）、默认工作流、额外触发词、自动匹配 LoRA |
| 生成参数 | 基准分辨率、分辨率预设、默认值（宽高、步数、CFG、采样器、内置提示词等）、普通用户参数上限 |
| 频率限制 | 冷却时间、每人每日张数上限、超级用户 |
| 消息与保存 | 保存图片、静默模式、安静模式、图片发送方式、违规图片处理方式 |
| 图片审核 | 开关、审核等级、审核方式、远程地址、失败策略、缩图、GPU、WD14 模型、NudeNet 模型 |
| 提示词审核 | 开关、额外违禁词、审核模型、自定义接口 |
| 群设置 | 按群覆盖（见 [按群设置](#-按群设置)） |

- AstrBot 管理员和「超级用户」不受冷却、每日上限和参数上限约束。
- 每日用量保存在 AstrBot 的插件存储里，重启后不会清零；生成失败的请求会退还额度。
- reflex 里的 `daylimit` 限制单个工作流每人每天的调用次数，`admin` 和 `group` 限制谁能用这个工作流。

---

## ⭐ 使用

### 命令

| 命令 | 说明 | 权限 |
|:---|:---|:---|
| `/prompt <提示词> [参数]` | 用默认工作流（或 `-wf` 指定的工作流）生成 | 所有人 |
| `/绘画`、`/comfyui执行` | 同上，触发词可以在配置里添加 | 所有人 |
| reflex 里的 `command` | 每个注册了命令的工作流各有一个命令，如 `/超分`、`/图生图`、`/打标` | 所有人 |
| `/comfy reload` | 重新扫描工作流目录，注册新命令 | 管理员 |

需要输入图片的工作流，可以直接在消息里附图、引用一条带图片的消息，或者在 QQ 上 @ 某人使用其头像。

### 常用参数

| 参数 | 说明 | 示例 |
|:---|:---|:---|
| `-u` | 负面提示词 | `-u "bad hands"` |
| `-ar` | 画面比例 | `-ar 16:9` |
| `-r` | 分辨率预设或 宽x高 | `-r p`、`-r 960x1920` |
| `-宽` / `-高` | 宽、高 | `-宽 832 -高 1216` |
| `-s` | 种子 | `-s 42` |
| `-t` | 步数 | `-t 28` |
| `-cfg` | CFG | `-cfg 7` |
| `-n` | 降噪强度 | `-n 0.6` |
| `-sp` / `-sch` | 采样器 / 调度器 | `-sp euler -sch normal` |
| `-b` / `-bc` | 每批张数 / 批数 | `-b 2 -bc 2` |
| `-m` | 模型 | `-m xxx.safetensors` |
| `-wf` | 选择工作流（名称或序号） | `-wf flux` |
| `-be` | 指定后端索引（URL 仅管理员可用） | `-be 1` |
| `-con` | 多后端并发，配合 `-bc` | `-con -bc 3` |
| `-o` / `-on` | 不使用内置正面 / 负面提示词 | `-o` |
| `-gif` | 使用 GIF 输入 | `-gif` |
| `-f` | 以合并转发发送结果（QQ） | `-f` |
| `-sil` | 不发送「正在生成」等提示 | `-sil` |
| `-notice` | 完成后私聊通知，适合长工作流 | `-notice` |
| `-h` | 显示当前工作流可用的全部参数 | `/绘画 -h` |

工作流还可以通过 reflex 的 `reg_args` 注册自己的参数，用 `-h` 查看。

### 示例

```text
/绘画 1girl, cherry blossoms -ar 16:9 -s 42
/prompt "1girl, red eyes" -u "bad hands" -wf txt2img -b 2
/超分 [图片]
/图生图 make her hair blue [引用一张图片]
/绘画 a long video prompt -wf wan22_video -notice
```

---

## 🛡️ 图片审核

| 审核方式 | 说明 |
|:---|:---|
| 远程 tagger API | 调用 sd-webui tagger 接口（默认） |
| 本地 WD14 | 本地运行 WD14 tagger，首次使用会从 Hugging Face 下载模型 |
| 本地 NudeNet | 本地运行 NudeNet |
| WD14 + NudeNet | 两个都跑，任一判定违规即违规 |

审核等级：`0` 关闭；`1` 只拦截 explicit；`2` 再拦截 questionable；`3` 再拦截 sensitive；`100` 及以上全部拦截。

审核服务不可用或出错时，「审核失败时按违规处理」开启（默认）则按违规处理，关闭则按安全处理。
**如果默认的远程地址连不上，开启该项会让所有图片都被私聊发送或拦截**，请换成自己的 tagger 地址或改用本地审核。

---

## 📝 提示词审核

开启后，生成前会让模型判断提示词是否违规，违规则直接拦截。默认使用当前会话的 AstrBot 模型，
也可以在「审核使用的模型」里另选一个提供商，或者打开「改用自定义接口」使用任意 OpenAI 兼容接口。
审核请求失败时按违规处理。

按群设置里还可以选择「让模型改写提示词」：违规的部分会被去掉，再用改写后的提示词生成，并告诉用户改写结果。

---

## 📤 发送方式

**图片发送方式**

| 值 | 说明 |
|:---:|:---|
| 1 | 直接发送图片 |
| 2 | 发送图片链接的二维码 |
| 3 | 发送图片链接 |

**违规图片处理方式**

| 值 | 说明 |
|:---:|:---|
| 1 | 私聊发给用户（默认），私聊失败则拦截 |
| 2 | 在群里发图片链接的二维码 |
| 3 | 在群里发图片链接 |
| 其他 | 直接拦截 |

二维码和链接只在 QQ（aiocqhttp / OneBot v11）上可用：插件把图片发给机器人自己来拿到图床链接。
其他平台上图片发送方式按 1 处理，违规图片的 2、3 按拦截处理。

在 QQ 上，「正在生成」、冷却、每日上限和报错提示会在一段时间后自动撤回。

---

## 👥 按群设置

在「群设置 → 按群覆盖」里为某个群添加一项，填群号后可以单独设置：

| 项 | 说明 |
|:---|:---|
| 在本群启用生图 | 关闭后本群的生图命令只回复「本群已禁用」 |
| 图片审核等级 | `-1` 跟随全局，其余同全局 |
| 提示词审核 | `-1` 跟随全局；`0` 关闭；`1` 拦截；`2` 让模型改写 |
| 图片发送方式 | `-1` 跟随全局，其余同全局 |
| 纯净模式 | 只发结果，不发耗时、任务 ID 等信息 |

---

## 📚 文档

- 📖 [节点控制功能文档](./docs/md/node_control.md) - **必读！** 了解如何编写 reflex 文件、注册命令和参数
- [ComfyUI 官方仓库](https://github.com/comfyanonymous/ComfyUI)
- [AstrBot 官方文档](https://docs.astrbot.app/)

---

## 📜 免责声明

> [!note]
>
> 本插件仅供**学习**和**研究**使用，使用者需自行承担使用插件的风险。作者不对插件的使用造成的任何损失或问题负责。请合理使用插件，**遵守相关法律法规**。
>
> 使用**本插件即表示您已阅读并同意遵守以上免责声明**。如果您不同意或无法遵守以上声明，请不要使用本插件。

---

## 💝 特别鸣谢

- [nonebot-plugin-comfyui](https://github.com/DiaoDaiaChan/nonebot-plugin-comfyui) - 本插件的前身，工作流格式和大部分功能都来自它
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 插件运行的聊天机器人框架
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) - 强大的节点式 AI 工作流工具
