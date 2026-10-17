这些规则只填进 JSON 字段。script 放整段可复制的产品特写提示词。storyboard 用编号分镜。image_prompts 和各张图的 prompt 用生图规则。整段回复不要在 JSON 外面再写正文。

不要读速度片案例，不要点名案例链接。结构按 apple-product-closeup 的六段来写，不要把几段合成一段。

script 必须有这六段：

1. 开场：时长、画幅 9:16、帧率、镜头数。
2. 产品锁定：哪一件产品、颜色、必须被认出来的轮廓。不写车辆，不写司机。
3. 光线和背景：光从哪来，背景留白。背景是一张地点，不是一条路。
4. 一句配比：多少是产品本身，多少是光线在表面上走。
5. 编号分镜：一镜一个机位。写这件产品怎么被看见。
6. 风格，加上产品会怎么坏的负面清单：多余产品、几个角度拼在一张图里、塑料感、字幕、乱码。

出片约束：画幅固定 9:16。每镜 duration 是 4 到 15 的整数秒。一个镜头只有一个机位。

资产：这件产品写三张，subject 相同，subject_kind 用 prop。view 分别是 hero、three_quarter、macro。背景是单张，subject_kind 用 location，view 为 single。微距的 depends_on 填英雄图 id。英雄图和四分之三的 depends_on 留空。上传图只是参考：英雄图、四分之三、微距的 references 都填同一批上传图 id，不要写进 depends_on，也不要写进镜头 images。不要给每个道具补正面、侧面、背面。不要写 front、side、back。姿势和机位不是资产。产品锚点必须能画出来：轮廓、颜色、材质。背景要写从哪看、留白和一到三个固定锚点。

生图：所有图片只放进 Image-Node，分镜不新造图片。prompt 用中文，能直接拿去文生图。顺序是产品锚点、角度、光线和材质、背景边界、禁止项。写成实拍。英雄图和背景的 depends_on 留空。微距 depends_on 填英雄图 id。禁止字幕、水印、logo 和乱码文字。不要占位符。

分镜：每镜只有一个机位、一个主要运动。镜头数量按产品被看见的节拍来，不要为了凑数加空镜。duration 是 4 到 15 的整数秒。镜头 prompt 用中文，写这一镜光线怎么走完，不要只写一张定格，也不要在同一镜里写第二个机位。没有对白时 dialogue 为空字符串。images 只列这一镜看得见、且已经写进 Image-Node 的生成资产 id，不列上传图。

字段：用 ## 分节。产品、材质、光线和要看清的细节，用 ==重点== 包起来。view 只能是 hero、three_quarter、macro、single。产品三张的 view 是 hero、three_quarter、macro，subject 相同，subject_kind 用 prop。背景 view 为 single，subject_kind 用 location。微距 depends_on 填 hero 的 id。上传图只进 references，而且英雄图、四分之三、微距三张都要带上同一批上传图 id。提示词里禁止字幕、水印、乱码文字和车辆。
