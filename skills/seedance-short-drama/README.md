# seedance-short-drama

面向 Codex 的中文竖屏短剧生产技能，用于把一个故事想法、剧本片段、产品植入需求或分镜草稿，整理成可继续生成图片和视频的完整短剧制作包。

这个技能适合制作 15-60 秒、9:16 竖屏剧情短片，重点支持：

- 生成 `剧本-分镜头.md`
- 生成 `提示词.md`，包含统一风格、人物三视图、场景、道具、分段九宫格 PREVIS 故事板提示词
- 生成 `视频提示词.md`，用于 Seedance 2.0 / RunningHub SparkVideo 2.0 分段视频生成
- 通过 RunningHub API 生成图片资产和视频片段，并保存 manifest

## 安装

把本仓库克隆或解压到 Codex 的 skills 目录中。

Windows 示例：

```powershell
cd "$env:USERPROFILE\.codex\skills"
git clone https://github.com/Nianan29/seedance-short-drama.git
```

最终目录应类似：

```text
C:\Users\你的用户名\.codex\skills\seedance-short-drama
```

重启或刷新 Codex 后，即可使用：

```text
$seedance-short-drama
```

## RunningHub 配置

如果只生成剧本和提示词，不需要配置 API。

如果要实际生成图片资产或视频，需要在你的项目目录创建 `.env`，写入自己的 RunningHub API Key：

```text
RUNNINGHUB_API_KEY=your-own-key
```

不要把 `.env` 或真实 API Key 提交到 GitHub。

平台默认端点已经写在技能中，通常不需要修改：

- 图片文生图：`rhart-image-g-2/text-to-image`
- 图片图生图：`rhart-image-n-g31-flash/image-to-image`
- 视频生成：`rhart-video/sparkvideo-2.0/multimodal-video`

## 基本用法

### 1. 从想法生成短剧制作文件

在 Codex 中输入类似：

```text
使用 $seedance-short-drama
我有一个想法：儿媳带婆婆去服装店买衣服，表面上要买最便宜的，反转是婆婆舍不得花钱，儿媳其实让店员拿最好的礼服。30 秒，有对白，情绪基调反转，礼服是我的产品。
请生成剧本、分镜头、图片提示词和视频提示词。
```

技能会优先生成 Markdown 文件，而不是只在聊天里输出：

```text
剧本-分镜头.md
提示词.md
视频提示词.md
```

默认规则是：提示词文件生成后先停下来让你审核，不会立刻调用 RunningHub 生成图片或视频。

### 2. 生成图片资产

审核并修改 `提示词.md` 后，再明确要求生成资产：

```text
按照 [提示词.md](提示词.md) 生成资产，创建资产文件夹用于保存
```

技能会检查 `RUNNINGHUB_API_KEY`。如果没有配置，会直接回复：

```text
没有配置 RunningHub API，无法生成资产。
```

如果已经配置，会按两阶段生成：

1. 并发生成独立文生图资产，最多并发 100 个
2. 等参考图完成后，再生成需要参考图的图生图资产，例如人物换装图和分段九宫格故事板

PREVIS 九宫格故事板默认使用 4K 分辨率。

### 3. 生成视频片段

图片资产审核通过后，可继续要求：

```text
按照 [视频提示词.md](视频提示词.md) 生成视频，创建视频文件夹用于保存
```

技能会使用 RunningHub SparkVideo 2.0 multimodal-video 接口，每个分段提交一个任务。视频参考图最多支持 9 张，通常优先使用：

1. 产品 P0 图
2. 当前段主要人物图
3. 当前段场景图
4. 当前段九宫格故事板
5. 必要道具图

生成结果会保存到视频输出目录，并写入 manifest，记录任务 ID、状态、下载路径和失败项。

## 产物说明

常见输出包括：

```text
剧本-分镜头.md
提示词.md
视频提示词.md
output/imagegen/<project-name>/
output/video/<project-name>/
```

## 注意事项

- 技能默认使用中文输出，适合中文短剧工作流。
- 不要把真实 API Key 写进 `SKILL.md`、`README.md` 或 `references/`。
- 产品图是最高优先级参考，应优先锁定颜色、材质、版型、形状和关键细节。
- 人物三视图用于锁定身份、年龄、五官、发型、体型和服装。
- 分镜故事板用于锁定构图、动作、调度和情绪节奏，不应替代产品或人物参考图。
- 如果没有明确的“生成资产/生成视频”请求，技能只创建提示词文件并等待审核。
