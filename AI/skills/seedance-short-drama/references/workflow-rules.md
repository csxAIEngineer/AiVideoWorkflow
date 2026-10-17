# Seedance Short Drama Workflow Rules

## File Set

Always create Markdown files for the text deliverables. Do not leave the script, image prompts, or video prompts only in the chat response.

- `剧本-分镜头.md`: 30-second drama script, dialogue, shot breakdown, product notes.
- `提示词.md`: GPT-image/RunningHub prompts for reference assets and segmented PREVIS storyboards.
- `视频提示词.md`: Seedance 2.0 prompts, one segment per storyboard.
- `output/imagegen/<project-name>/storyboard_manifest.md`: image asset status and deprecated assets, if images were generated.

## Script Rules

For a 30-second dialogue short drama, use a compact arc:

1. 0-10s: setup and misunderstanding.
2. 10-20s: tension, private clarification, reversal.
3. 20-30s: product reveal, emotional payoff, closing line.

Include:

- Basic info: type, duration, tone, product, characters, core reversal.
- Dialogue table with time ranges.
- Shot table with duration, size, visual content, camera movement, emotional purpose.
- Product presentation notes.
- Shooting notes.

Avoid solving a reversal through hidden payment if the user explicitly wants the reversal expressed in dialogue.

## Reference Image Inventory

Use stable numeric names so upload order is unambiguous:

- `00_style_look_reference.png`: unified visual style, no people unless needed.
- `01_character_<role>_turnaround.png`: front/side/back full-body view.
- `02_character_<role>_turnaround_before.png`: pre-change state.
- `03_character_<role>_turnaround_after_<state>.png`: post-change state, generated image-to-image from the before identity plus the product reference.
- `04_character_<role>_turnaround.png`: additional character.
- `05_scene_<place>.png`: first scene.
- `06_scene_<place>.png`: main scene.
- `07_scene_<place>.png`: later scene or fitting/mirror area.
- `08_product_<product>_reference_sheet.png`: P0 product lock, unless the user supplies a real product image.
- `09_prop_<prop>.png`, `10_prop_<prop>.png`: supporting props.
- `11_storyboard_segment_01_0-10s.png`, `12_storyboard_segment_02_10-20s.png`, `13_storyboard_segment_03_20-30s.png`: segmented nine-panel storyboards.

When a character changes clothing, generate the after-state character sheet by image-to-image using both the original character sheet and the product reference. State explicitly: same person, same face, same age, same body, no beautification, no new identity.

Scene reference images used only for environment should be empty spaces. Prohibit people, mannequins, faces, hands, and body parts unless those are intentional story elements.

## Image Prompt Constraints

All generated asset images must share a unified visual style. Define the global style once at the top of `提示词.md`, then reuse it in every asset prompt unless a special storyboard style is explicitly required.

All image prompts should include:

- 9:16 vertical composition unless the asset needs a reference-sheet layout.
- Realistic cinematic short-drama style, modern Chinese setting when applicable.
- No subtitles, no screen text, no readable signage, no price tags, no brand logos, no watermark, no corner mark.
- No celebrity faces, real IP characters, distorted fingers, extra limbs, impossible clothing motion, or deformed mirror reflections.

## Fixed Character Multi-View Template

Use this exact base template for every character turnaround / character multi-view asset. Add the role-specific age, face, body, hair, clothing, expression, and temperament details after the template; do not replace the template style wording.

```text
人物多视图的角色设定图：按照人物的正面、侧面45度、背面的视图顺序排列，画面上部分为人物的面部特写，下部分为人物对应视图的全身照。
超写实人像，细腻皮肤纹理，可见毛孔，自然皮肤瑕疵，次表面散射，柔和自然光，8K RAW照片，佳能R5拍摄，85mm定焦，眼部锐利对焦。
整体为纯白色摄影棚背景，写实风格，人物一致性高，无文字，无字幕，无水印，无复杂背景。
```

Negative requirements for character multi-view assets:

- Do not use colored backgrounds, store interiors, street scenes, decorative props, text labels, panel labels, watermarks, or subtitles.
- Do not create different faces between the front, 45-degree side, and back views.
- Do not stylize as anime, illustration, fashion sketch, or cartoon.

Priority hierarchy:

1. Product image is P0 product lock.
2. Character turnarounds lock character identity.
3. Scene images lock space and light.
4. Storyboards lock structure, not product details.

## Segmented Storyboard Prompt Skeleton

Generate every PREVIS segmented storyboard image at `4k` resolution. Keep other reference assets at the project default unless the user requests higher detail.

Use this skeleton for each 10-second segment:

```text
创建一个 PREVIS 导演分镜故事板。使用参考图像作为角色、场景和产品锁定参考。9:16 故事板纸，严格 3 列 x 3 行，共 9 个电影式面板。专注于构图和动作，不要生成彩色成片截图。

实际故事板绘画必须仅使用黑色和白色：粗铅笔线条，细节最少，快速手势绘画能量，简单解剖结构构建和强烈的轮廓可读性。保持艺术作品轻量、动态和未完成，像早期分镜绘画。

注释颜色系统：
红色箭头 = 身体运动
蓝色箭头 = 摄像机运动
绿色标记 = 构图/构图笔记
橙色标记 = 光线方向
紫色标记 = 歌声/情感强调
黑色文字 = 短焦镜头笔记和面板标签

版式要求：必须是一张 9:16 竖版故事板纸，9 个面板必须全部有内容，面板之间有黑色边框；禁止 2 列长图排版，禁止空白占位面板，禁止写实短剧截图排版，禁止把人物三视图直接拼进格子里。参考图只用于保持角色、服装、场景和产品一致性。产品外观以产品参考图为准。

九格内容：
第 1 格：...
第 2 格：...
...
第 9 格：...
```

Do not generate an overall nine-grid storyboard if segmented storyboards exist. For 30 seconds, use three boards: 0-10s, 10-20s, 20-30s.

## Seedance Prompt Rules

Each video segment should include:

- Reference upload order.
- Reference mapping, such as `@图片1` as product P0, `@图片2` as character, and so on.
- Creation statement: 9:16 vertical short-drama segment and exact duration.
- Visual/audio restrictions.
- Visual style.
- Storyboard alignment statement.
- Shot list mapped to the storyboard's nine panels.
- Hard constraints and QC checks.

Reference upload order:

1. Product P0 image first.
2. Character references for the current segment.
3. Scene references for the current segment.
4. Props if needed.
5. Current segment storyboard last.

Do not upload or reference generated video clips from earlier segments. Do not use a previous generated clip as first-frame, last-frame, or continuity reference unless the user explicitly changes the workflow.

## Seedance Restrictions

Always prohibit:

- Subtitles.
- Screen text.
- Readable price tags.
- Brand logos unless the user explicitly owns and wants them.
- Watermarks and corner marks.
- Background music, unless requested.
- Storyboard arrows, paths, panel borders, labels, and handwritten notes appearing in the final video.
- Sudden face changes, clothing drift, impossible dressing actions, hands passing through clothing, broken reflections, and contradictory scene geography.

For high-risk dressing or object manipulation, prefer a cut to a stable state instead of asking the model to simulate the full continuous action.

## QC Checklist

Before delivering:

- Timecodes in all files match.
- Each 10-second segment has exactly one storyboard prompt and one Seedance prompt.
- Product image is listed first in every relevant reference upload order.
- The final segment uses the character after-state reference if the product is worn.
- Storyboard panels are consistent with the script beats.
- No deprecated overall storyboard is still treated as active.
- Any regenerated inconsistent assets are noted in the manifest.
