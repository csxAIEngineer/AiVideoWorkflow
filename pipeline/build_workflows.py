#!/usr/bin/env python3
"""Build reviewable ComfyUI workflows. Does not call Comfy Cloud."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = (
    "现代都市写实风格，电影级细腻质感，甜宠治愈色调，冷暖对撞光影（冷青蓝为骨、暖黄为点），"
    "浅景深虚化，胶片微颗粒，高光柔化暗部保留细节，真实皮肤质感，竖屏 9:16 构图，主体居中"
)
NEGATIVE = "模糊，低画质，变形，五官崩坏，多余肢体，水印，文字"

GLOBAL = """【全局设定】画面风格和类型: 现代都市写实，电影级细腻质感，甜宠治愈底色，全程人物边界独立，自然表演，人物无穿插、无穿透；全程禁止出现字幕、禁止角色变脸、变装、形象突变；杜绝肢体畸形、扭曲、残缺等人体结构错误；禁止画面闪烁、跳变、卡顿、帧异常；全程无背景音乐、无 BGM、只保留对白与现场环境声
【色调与光线】整体冷调蓝青为骨、暖调为点。夜戏以路灯与灯笼的暖白、暖黄作为画面里唯一暖源，与蓝调灯光、雨雾冷青形成冷暖对撞；人物面部保留一层极淡暖光，皮肤通透不惨白。
【都江堰地域质感】南桥廊桥的木质廊柱、飞檐斗拱与宝瓶口方向的岷江水声为地域锚；西街为临水窄巷，青石湿滑、木格窗、卷帘门、屋檐滴水，巷内灯光稀疏。
【湿度与天气】连续夜雨，空气湿度极高，画面始终有半透明水雾；物体表面挂水，玻璃与镜片起雾，地面成镜面反光；雨丝为细密斜织，非暴雨。
【镜头语言倾向】以固定轻呼吸与手持轻微推进为主，多用前景遮挡（廊柱、栏杆、雨帘、窗框）营造窥视感与亲密度；人像偏中近景与特写，情绪落在眼神与手部小动作上，节奏舒缓克制，留白多于对白。
【画幅与画质】竖屏 9:16 电影画幅，2.39:1 构图取景框内留边，浅景深，35mm～50mm 焦段为主，胶片微颗粒，高光柔化，暗部保留细节。"""

CHENG_BASE = "32 岁男性，身高 182cm，清瘦偏瘦体型，肩背微含（长期伏案）；黑色短发、发量偏厚、额前碎发自然、鬓角修剪干净；长偏方脸，眉骨略高，细长单眼皮，眼神沉静略带疲惫，不说话时嘴角平直；戴黑色细方框近视眼镜（镜框无花纹、细金属镜腿）；鼻梁挺直，肤色偏白微冷。穿深灰色纯棉连帽衫（宽松版型、袖口略松垮），下搭黑色直筒休闲裤，脚穿深灰运动鞋，背一只黑色双肩电脑包（侧面有磨损痕迹）。"
CHENG_RAIN = "在 `程屿-基础形象` 基础上：帽兜未戴，单手撑一把黑色长柄直骨伞，肩头与镜片沾细密雨珠，连帽衫肩线被雨洇出一片深色。"
SU_BASE = "28 岁女性，身高 165cm，纤细偏瘦体型，肩线单薄、站姿微微含肩；长直发染自然深棕近黑，扎低马尾（发绳为素色冷灰或黑），额前薄刘海，发丝在雨天微潮贴额；鹅蛋脸，皮肤白净偏冷调，眉毛细软平直，双眼皮较浅，眼下有淡淡青影（长期疲惫），唇色偏淡、唇形小巧；神情平静、略带疏离。穿浅燕麦色针织开衫（细坑条织法、落肩宽松、长度过臀），内搭白色圆领棉质 T 恤，下穿深灰直筒长裤，脚穿浅色帆布鞋。"
SU_RAIN = "在 `苏晚-基础形象` 基础上：外面又罩一件深灰 oversize 薄风衣（未系扣、敞着、下摆垂落），右手握一部黑色手机（素色手机壳）。"
BRIDGE = "南桥廊桥雨夜空镜，画面中不要出现人物。都江堰南桥廊桥，木质廊柱与飞檐斗拱，桥上一排暗红灯笼，桥面湿润成镜面反光，廊外岷江水面，蓝调灯光在水面拉出竖向波纹倒影，檐角滴落断续水帘，细密斜织雨丝与半透明水雾。主色调：冷青蓝 + 暗红灯笼点光。"
UMBRELLA = "黑色长柄直骨伞：哑光纯黑伞面、无图案、弯钩握把，伞长约为人物的 1.1 倍、伞面直径约为人物肩宽的 1.6 倍。伞面和内衬都不要出现文字。"
UMBRELLA_TEXT = "黑色长柄直骨伞：哑光纯黑伞面、无图案、弯钩握把，伞长约为人物的 1.1 倍、伞面直径约为人物肩宽的 1.6 倍。伞内衬印一行极小的浅灰色代码体字 `// 我等一场雨，等一个人把伞还给我`。"

REFS = {
    "P2": ("`程屿-雨夜送伞`", f"`程屿-基础形象`：{CHENG_BASE} `程屿-雨夜送伞`：{CHENG_RAIN}", "p2_chengyu_rain.jpg"),
    "P9": ("`苏晚-雨夜倚栏`", f"`苏晚-基础形象`：{SU_BASE} `苏晚-雨夜倚栏`：{SU_RAIN}", "p9_suwan_rain.jpg"),
    "P6": ("南桥廊桥空镜", BRIDGE, "p6_bridge.jpg"),
    "umbrella": ("`黑色长柄直骨伞`", UMBRELLA, "prop_umbrella.jpg"),
    "umbrella_text": ("`黑色长柄直骨伞`", UMBRELLA_TEXT, "prop_umbrella.jpg"),
}


def styled(text):
    return text.rstrip("，") + "，" + STYLE


ASSETS = [
    {
        "id": "P1",
        "title": "程屿-基础形象",
        "file": "input/p1_chengyu_base.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": False,
        "prompt": styled(
            "角色定妆照，单人男性，32 岁，身高 182cm，清瘦偏瘦体型，肩背微含；黑色短发，发量偏厚，额前碎发自然，鬓角修剪干净；长偏方脸，眉骨略高，细长单眼皮，眼神沉静略带疲惫，嘴角平直；黑色细方框近视眼镜，细金属镜腿；鼻梁挺直，肤色偏白微冷；穿深灰色纯棉连帽衫（宽松、袖口略松垮）。正面平视镜头，上半身居中，纯色浅灰无缝背景，均匀柔光棚拍，真实皮肤质感与毛孔细节"
        ),
    },
    {
        "id": "P2",
        "title": "程屿-雨夜送伞",
        "file": "input/p2_chengyu_rain.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": ["P1"],
        "used_by_s01_s03": True,
        "prompt": styled(
            "单人男性全身像，32 岁，身高 182cm，清瘦偏瘦，肩背微含；黑色短发发量偏厚，额前碎发自然；长偏方脸，细长单眼皮，眼神沉静，黑色细方框眼镜；深灰色宽松连帽衫，黑色直筒休闲裤，深灰运动鞋。帽兜未戴，右手撑一把哑光纯黑长柄直骨伞，弯钩握把，镜片与肩头沾细密雨珠，肩线被雨洇出一片深色。站在都江堰南桥廊桥雨夜：木质廊柱与飞檐，桥面湿润镜面反光，身后一排暗红灯笼，蓝调灯光与岷江水面倒影，细密斜织雨丝与半透明水雾。全身居中，微仰机位"
        ),
    },
    {
        "id": "P3",
        "title": "程屿-工作室",
        "file": "input/p3_chengyu_studio.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": ["P1"],
        "used_by_s01_s03": False,
        "prompt": styled(
            "单人男性，32 岁，清瘦，黑色短发，黑色细方框眼镜，深灰宽松连帽衫前襟随意敞开，帽兜垂在颈后，右手握黑色直板手机。坐在老居民楼小单间书桌前，双显示器屏幕爬满代码，机械键盘，桌边外卖盒堆成小山，泡面桶与可乐罐，屏幕冷白光映在镜片上，一盏暖黄台灯补侧光，窗帘半开。侧面中景，坐姿，低头看手机"
        ),
    },
    {
        "id": "P4",
        "title": "苏晚-基础形象",
        "file": "input/p4_suwan_base.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": False,
        "prompt": styled(
            "角色定妆照，单人女性，28 岁，身高 165cm，纤细偏瘦，肩线单薄微微含肩；长直发自然深棕近黑，低马尾，素色冷灰发绳，额前薄刘海；鹅蛋脸，皮肤白净偏冷调，眉毛细软平直，双眼皮较浅，眼下淡淡青影，唇色偏淡唇形小巧，神情平静略带疏离；浅燕麦色细坑条针织开衫，内搭白色圆领棉 T。正面平视镜头，上半身居中，纯色浅灰无缝背景，均匀柔光棚拍，真实皮肤质感"
        ),
    },
    {
        "id": "P5",
        "title": "苏晚-美甲店",
        "file": "input/p5_suwan_nail.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": ["P4"],
        "used_by_s01_s03": False,
        "prompt": styled(
            "单人女性，28 岁，纤细，深棕近黑长发扎低马尾，额前薄刘海，鹅蛋脸，眼下淡淡青影，神情平静专注，浅燕麦色针织开衫外系亚麻色围裙（前胸两个口袋、插着细毛刷），双手指甲涂素色裸粉雾面甲油。坐在西街美甲小铺原木工作台前低头给手模涂甲油，面前一整面甲油架（成排彩色小玻璃瓶），暖黄灯光与串灯，窗玻璃挂水珠，桌上散着甲油瓶。中景，俯角微侧"
        ),
    },
    {
        "id": "P6",
        "title": "南桥廊桥雨夜空镜",
        "file": "input/p6_bridge.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": True,
        "prompt": styled(
            "场景概念图，空镜无人物，都江堰南桥廊桥雨夜，木质廊柱与飞檐斗拱沿纵深排列，透视线汇聚，桥面湿润成镜面反射蓝调灯光，桥上一排暗红灯笼，廊外岷江水面倒影拉出竖向波纹，檐角滴落断续水帘，细密斜织雨丝与半透明水雾。纵深对称构图，冷青蓝主调 + 暗红灯笼点光"
        ),
    },
    {
        "id": "P7",
        "title": "西街美甲小铺空镜",
        "file": "input/p7_nailshop.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": False,
        "prompt": styled(
            "场景概念图，空镜无人物，临水窄巷里的小美甲店室内夜景，木质门框与木格窗，玻璃门内透出暖黄灯光，原木工作台，一整面甲油架摆满成排彩色小玻璃瓶，软垫座椅与手枕，暖光串灯，窗玻璃挂水珠，桌面散着甲油瓶。室内平视广角，暖黄主光 + 窗外冷蓝夜色"
        ),
    },
    {
        "id": "P8",
        "title": "程屿的工作室空镜",
        "file": "input/p8_studio.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": False,
        "prompt": styled(
            "场景概念图，空镜无人物，老居民楼小单间深夜内景，书桌上双显示器爬满代码，机械键盘，桌边外卖盒堆成小山，泡面桶与可乐罐，墙上贴满便利贴，窗帘半开透进对面楼窗光，屏幕冷白光为主光源，一盏暖黄台灯。平视中广角，冷白 + 暖黄双光源"
        ),
    },
    {
        "id": "P9",
        "title": "苏晚-雨夜倚栏",
        "file": "input/p9_suwan_rain.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": ["P4"],
        "used_by_s01_s03": True,
        "prompt": styled(
            "单人女性，28 岁，身高 165cm，纤细偏瘦体型，肩线单薄、站姿微微含肩；长直发自然深棕近黑，扎低马尾（素色冷灰发绳），额前薄刘海；鹅蛋脸，皮肤白净偏冷调，眉毛细软平直，双眼皮较浅，眼下有淡淡青影，唇色偏淡、唇形小巧，神情平静略带疏离。浅燕麦色针织开衫（细坑条、落肩宽松、过臀），内搭白色圆领棉 T，深灰直筒长裤，浅色帆布鞋。外罩深灰 oversize 薄风衣（未系扣敞着），右手握黑色手机。侧身倚在都江堰南桥廊桥雨夜的木质栏杆上：木质廊柱与飞檐斗拱，桥上一排暗红灯笼，桥面湿润成镜面反光，廊外岷江水面，蓝调灯光在水面拉出竖向波纹倒影，檐角滴落断续水帘，细密斜织雨丝与半透明水雾。中近景"
        ),
    },
    {
        "id": "umbrella",
        "title": "黑色长柄直骨伞",
        "file": "input/prop_umbrella.jpg",
        "generate": True,
        "skip_reason": None,
        "depends_on": [],
        "used_by_s01_s03": True,
        "prompt": styled(
            "道具静物，无人物，一把哑光纯黑长柄直骨伞，伞面无图案，弯钩握把，伞撑开，纯色浅灰背景，产品摄影，主体居中"
        ),
    },
]


S01_SPACE = "南桥廊桥 中远景。夜，细雨斜织，廊桥木质廊柱与飞檐在蓝调灯光下呈深青蓝色调，桥面湿润成镜面反光，廊外是岷江水面，蓝调灯光在水面拉出竖向波纹倒影；雨水沿廊檐滴落成断续水帘。持续可见元素：湿润的木质栏杆、桥上一排暗红灯笼、岷江水面倒影、斜织雨丝与半透明水雾。关键道具：`苏晚的黑色手机`，与手掌比例约 1.4:1。"
S01_BLOCK = "`苏晚-雨夜倚栏` 位于画面中央偏右，侧身靠在南桥廊桥木质栏杆上，面向画外的岷江水面，背对廊桥内侧；左肩倚柱、风衣下摆垂落，右手把手机按扣在掌心；背景廊柱与雨幕在身后半米处层层退远。"
S01_SOUND = "< 环境音・雨声 >：细雨持续落在廊桥瓦檐与木栏上，密集细碎的沙沙声，雨滴沿檐角成串滴落，落进江面时发出短促清响。< 环境音・水声 >：岷江水在桥下缓慢流动，低沉连续的哗哗声，偶有水浪拍打桥墩的闷响由下传来。< 环境音・桥面 >：远处零星行人的脚步在湿桥面上发出黏滞的啪嗒声，由远及近又渐渐远去。< 环境音・电子 >：手机按键的轻微「嗒」声，屏幕亮起时极细的电流声。全程无 BGM，仅有现场环境声与对白。"

S02_SPACE = "南桥廊桥 中景。夜雨，蓝调灯光沿廊桥木柱与飞檐铺开，桥面湿亮反光，雨丝斜织，廊外岷江水面倒映蓝调灯影；持续可见：湿润木栏杆、桥上一排暗红灯笼、随风微晃的雨帘与半透明水雾。关键道具：`黑色长柄直骨伞`，伞长约为人物的 1.1 倍、伞面直径约为人物肩宽的 1.6 倍，哑光纯黑无图案、弯钩握把。"
S02_BLOCK = "`苏晚-雨夜倚栏` 位于画面右侧、侧身倚栏面向画外江面；`程屿-雨夜送伞` 位于画面左侧三步外，正面朝向 `苏晚-雨夜倚栏`，两人同层横向对位，中间隔着湿桥面与一层薄雨帘。"
S02_SOUND = "< 环境音・雨声 >：雨丝打在伞面与木栏上，伞面上的雨点声更密更实，形成一层低频沙沙底噪，雨声盖过大部分环境杂音。< 环境音・水声 >：岷江水声在桥下远景处持续低响，垫在雨声之下。< 环境音・呼吸与脚步声 >：`程屿-雨夜送伞` 鞋底踩过湿桥面的黏滞脚步声由远及近、一步一顿放得很轻；两人呼吸在雨幕里清晰可辨，程屿呼吸平稳，苏晚呼吸略顿了一下。< 环境音・衣物 >：连帽衫被雨洇湿时布料摩擦的轻微窸窣声。台词语气：程屿压低声线、简短克制、无颤音。全程无 BGM，仅有现场环境声与对白。"

S03_SPACE = "南桥廊桥 中景转纵深双景。夜雨，蓝调灯光与岷江倒影贯穿全段，廊桥木柱与飞檐在雨雾里晕开；持续可见：湿润木栏杆、斜织雨帘、桥面镜面反光。关键道具：`黑色长柄直骨伞`，伞内衬印一行极小的浅灰色代码体字 `// 我等一场雨，等一个人把伞还给我`；伞长约为人物的 1.1 倍。"
S03_BLOCK = "`苏晚-雨夜倚栏` 位于画面右前，双手在身前接伞；`程屿-雨夜送伞` 位于画面左后，转身背对镜头向雨雾深处走去，两人由同层横向转为纵深错位。"
S03_SOUND = "< 环境音・雨声 >：伞被撑开时布料「噗」地一声张开，随即雨点密密打在伞面上，声音由散乱变为成片、成片后转闷。< 环境音・水声 >：岷江水声在雨中远景处持续低响，雨势没有变化。< 环境音・呼吸与脚步声 >：`程屿-雨夜送伞` 的脚步声由近及远、逐渐被雨声吸收直至消失；`苏晚-雨夜倚栏` 有一声轻轻的吸气。< 环境音・衣物 >：伞面布料展开与伞骨滑动的机械轻响。台词语气：苏晚困惑、轻声、尾音上扬。全程无 BGM，仅有现场环境声与对白。"


def shot_prompt(space, block, sound, refs, pose, visual, dialogue):
    lines = [GLOBAL, "", "【参考图】"]
    for index, key in enumerate(refs, start=1):
        label, text, filename = REFS[key]
        lines.append(f"Image {index} 是{label}，文件 {filename}。{text}")
    lines.append("")
    lines.append(f"【镜头】{pose}")
    lines.append(visual)
    lines.append("")
    lines.append("【空间】" + space)
    lines.append("【站位】" + block)
    lines.append("【声音】" + sound)
    lines.append("【对白】" + (dialogue if dialogue else "本镜头无对白。"))
    return "\n".join(lines)


SHOTS = [
    {
        "id": "S01-c01",
        "segment": "S01",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P9", "P6"],
        "pose": "姿态:苏晚-雨夜倚栏-侧身倚栏低头看手机",
        "visual": "中景，左侧廊柱与滴落雨帘作前景遮挡，固定轻呼吸，斜侧看苏晚侧脸与栏杆。雨水顺廊檐滴落成细帘，`苏晚-雨夜倚栏` 低头看掌心手机，屏幕亮光映在冷调脸庞上，眼神疲惫放空，刘海被潮气贴住额角。",
        "dialogue": None,
        "space": S01_SPACE,
        "block": S01_BLOCK,
        "sound": S01_SOUND,
    },
    {
        "id": "S01-c02",
        "segment": "S01",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P9", "P6"],
        "pose": "姿态:苏晚-雨夜倚栏-拇指滑动查看未接来电",
        "visual": "特写，手机屏幕作前景，手持轻微推进由手背推向屏幕。屏幕上顶端滚动显示三通未接来电，屏光在指缝与细密雨珠间泛白，`苏晚-雨夜倚栏` 的拇指在屏幕上停顿一秒，随后按下侧键熄灭屏幕。",
        "dialogue": None,
        "space": S01_SPACE,
        "block": S01_BLOCK,
        "sound": S01_SOUND,
    },
    {
        "id": "S01-c03",
        "segment": "S01",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P9", "P6"],
        "pose": "姿态:苏晚-雨夜倚栏-把手机按扣掌心并抬头望向江面",
        "visual": "中近景，带湿润栏杆与雨帘作前景，固定轻呼吸，平行跟拍由手机推向侧脸。`苏晚-雨夜倚栏` 把手机整个按扣在掌心，抬起下巴望向廊外的岷江水面，蓝调灯影在她眼底轻轻晃动，唇角平直。",
        "dialogue": "苏晚：（自语，声量极轻）「再站一会儿就好。」",
        "space": S01_SPACE,
        "block": S01_BLOCK,
        "sound": S01_SOUND,
    },
    {
        "id": "S02-c01",
        "segment": "S02",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P2", "P9", "P6", "umbrella"],
        "pose": "姿态:程屿-雨夜送伞-撑伞停步注视",
        "visual": "中景，左侧廊柱与斜织雨帘作前景遮挡，固定轻呼吸，由湿桥面缓慢上移停在他上身。`程屿-雨夜送伞` 撑着 `黑色长柄直骨伞` 在三步外站住，帽兜未戴，镜片上挂着细密雨珠，目光稳稳落在 `苏晚-雨夜倚栏` 身上，嘴角平直，没有多余动作。",
        "dialogue": None,
        "space": S02_SPACE,
        "block": S02_BLOCK,
        "sound": S02_SOUND,
    },
    {
        "id": "S02-c02",
        "segment": "S02",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P2", "P9", "P6", "umbrella"],
        "pose": "姿态:程屿-雨夜送伞-收步走近并递伞",
        "visual": "中近景，带湿润木栏杆作前景，手持轻微推进随他的脚步。`程屿-雨夜送伞` 缓步走近，把伞柄朝向 `苏晚-雨夜倚栏` 递出去，另一只手不自觉插回兜里，肩膀略微前倾，深灰连帽衫肩头被雨洇出一片深色。",
        "dialogue": None,
        "space": S02_SPACE,
        "block": S02_BLOCK,
        "sound": S02_SOUND,
    },
    {
        "id": "S02-c03",
        "segment": "S02",
        "duration_script": 5,
        "duration": 5,
        "duration_note": None,
        "refs": ["P2", "P9", "P6", "umbrella"],
        "pose": "姿态:程屿-雨夜送伞-递伞低声说话；苏晚-雨夜倚栏-抬眼怔住",
        "visual": "双人景，湿润栏杆横过画面下缘作前景，固定轻呼吸，两人同层对位。`程屿-雨夜送伞` 不看她眼睛、声音压得很低，伞柄悬在两人之间；`苏晚-雨夜倚栏` 侧脸微微转过来，眼神从疲惫转为怔然，手指尚未抬起。",
        "dialogue": "程屿：（低声，克制）「拿着。伞里有行字，你读得懂，再还我。」",
        "space": S02_SPACE,
        "block": S02_BLOCK,
        "sound": S02_SOUND,
    },
    {
        "id": "S03-c01",
        "segment": "S03",
        "duration_script": 3,
        "duration": 4,
        "duration_note": "分镜原时长 3 秒，MiniMax H3 节点最短 4 秒",
        "refs": ["P9", "P2", "P6", "umbrella"],
        "pose": "姿态:苏晚-雨夜倚栏-双手接过伞柄",
        "visual": "特写，弯钩伞柄作前景，手持轻微推进从程屿的手转向苏晚的手。两只手在湿冷的伞柄上交叠一瞬，`程屿-雨夜送伞` 的手指松开，`苏晚-雨夜倚栏` 的手指收紧、握住弯钩握把，指节因用力而微微泛白。",
        "dialogue": None,
        "space": S03_SPACE,
        "block": S03_BLOCK,
        "sound": S03_SOUND,
    },
    {
        "id": "S03-c02",
        "segment": "S03",
        "duration_script": 4,
        "duration": 4,
        "duration_note": None,
        "refs": ["P9", "P2", "P6", "umbrella"],
        "pose": "姿态:程屿-雨夜送伞-转身背向镜头走入雨雾",
        "visual": "中景，右侧 `苏晚-雨夜倚栏` 的肩线作前景虚化，平行跟拍随他的背影后退。`程屿-雨夜送伞` 不再回头，双手插兜走进斜织雨丝里，深灰连帽衫背影很快被水雾与蓝调灯影吞成一道剪影，逐渐失去轮廓。",
        "dialogue": None,
        "space": S03_SPACE,
        "block": S03_BLOCK,
        "sound": S03_SOUND,
    },
    {
        "id": "S03-c03",
        "segment": "S03",
        "duration_script": 5,
        "duration": 5,
        "duration_note": None,
        "refs": ["P9", "P2", "P6", "umbrella_text"],
        "pose": "姿态:苏晚-雨夜倚栏-撑开伞并仰头看内衬字",
        "visual": "中近景，张开的黑色伞面作上方前景遮挡，纵向极缓下降由伞面移至内衬。`苏晚-雨夜倚栏` 把伞撑开，伞内衬那行浅灰色小字在微弱灯光下浮现，她仰起脸，眉尖轻蹙，嘴唇微张，眼神里是没读懂的困惑。",
        "dialogue": "苏晚：（困惑，轻声）「代码……？」",
        "space": S03_SPACE,
        "block": S03_BLOCK,
        "sound": S03_SOUND,
    },
]


def compile_shot(raw):
    prompt = shot_prompt(
        raw["space"], raw["block"], raw["sound"], raw["refs"], raw["pose"], raw["visual"], raw["dialogue"]
    )
    images = []
    for index, key in enumerate(raw["refs"], start=1):
        label, _text, filename = REFS[key]
        images.append({"slot": f"Image {index}", "asset": key if not key.startswith("umbrella") else "umbrella", "label": label, "file": f"input/{filename}"})
    return {
        "id": raw["id"],
        "segment": raw["segment"],
        "duration_script": raw["duration_script"],
        "duration": raw["duration"],
        "duration_note": raw["duration_note"],
        "dialogue": raw["dialogue"],
        "images": images,
        "prompt": prompt,
    }


def note(nid, title, text, x, y, w, h):
    return {
        "id": nid,
        "type": "Note",
        "pos": [x, y],
        "size": [w, h],
        "flags": {},
        "order": nid,
        "mode": 0,
        "inputs": [],
        "outputs": [],
        "title": title,
        "properties": {"text": text},
        "widgets_values": [text],
        "color": "#432",
        "bgcolor": "#653",
    }


NANO_SYSTEM = (
    "You are an expert image-generation engine. You must ALWAYS produce an image.\n"
    "Interpret all user input—regardless of format, intent, or abstraction—as literal visual directives for image composition.\n"
    "If a prompt is conversational or lacks specific visual details, you must creatively invent a concrete visual scenario that depicts the concept.\n"
    "Prioritize generating the visual representation above any text, formatting, or conversational requests."
)


def gemini_node(nid, title, prompt, x, y, image_link):
    inputs = []
    if image_link is not None:
        inputs.append(
            {
                "label": "image_1",
                "name": "model.images.image_1",
                "shape": 7,
                "type": "IMAGE",
                "link": image_link,
            }
        )
    return {
        "id": nid,
        "type": "GeminiNanoBanana2V2",
        "pos": [x, y],
        "size": [420, 480],
        "flags": {},
        "order": nid,
        "mode": 0,
        "inputs": inputs,
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": []},
            {"name": "STRING", "type": "STRING", "links": None},
            {"name": "thought_image", "type": "IMAGE", "links": None},
        ],
        "title": title,
        "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "GeminiNanoBanana2V2"},
        "widgets_values": [
            prompt,
            "Nano Banana 2 (Gemini 3.1 Flash Image)",
            "9:16",
            "1K",
            "MINIMAL",
            42,
            "fixed",
            "IMAGE",
            NANO_SYSTEM,
        ],
        "color": "#432",
        "bgcolor": "#653",
    }


def save_image_node(nid, title, prefix, x, y, link_id):
    return {
        "id": nid,
        "type": "SaveImage",
        "pos": [x, y],
        "size": [320, 360],
        "flags": {},
        "order": nid,
        "mode": 0,
        "inputs": [{"name": "images", "type": "IMAGE", "link": link_id}],
        "outputs": [],
        "title": title,
        "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "SaveImage"},
        "widgets_values": [prefix],
    }


def asset_workflow():
    """P1/P4/空镜 are text-to-image. P2 and P3 take P1's image. P5 and P9 take P4's image."""
    by_id = {item["id"]: item for item in ASSETS}
    # node ids: generator, save. link ids assigned below.
    layout = {
        "P1": {"gen": 11, "save": 12, "x": 40, "y": 380, "kind": "文生图"},
        "P4": {"gen": 41, "save": 42, "x": 40, "y": 980, "kind": "文生图"},
        "P6": {"gen": 61, "save": 62, "x": 40, "y": 1580, "kind": "文生图"},
        "P7": {"gen": 71, "save": 72, "x": 40, "y": 2180, "kind": "文生图"},
        "P8": {"gen": 81, "save": 82, "x": 40, "y": 2780, "kind": "文生图"},
        "umbrella": {"gen": 101, "save": 102, "x": 40, "y": 3380, "kind": "文生图"},
        "P2": {"gen": 21, "save": 22, "x": 1280, "y": 80, "kind": "参考 P1"},
        "P3": {"gen": 31, "save": 32, "x": 1280, "y": 680, "kind": "参考 P1"},
        "P5": {"gen": 51, "save": 52, "x": 1280, "y": 980, "kind": "参考 P4"},
        "P9": {"gen": 91, "save": 92, "x": 1280, "y": 1580, "kind": "参考 P4"},
    }
    parent_of = {"P2": "P1", "P3": "P1", "P5": "P4", "P9": "P4"}
    nodes = [
        note(
            1,
            "资产图从这里生成",
            "左侧 Nano Banana 出资产图。P1 接到 P2、P3。P4 接到 P5、P9。\n"
            "前三段用到的 P2、P9、P6、黑伞，输出再直接接到右侧 MiniMax H3。\n"
            "S01 只接苏晚和南桥。S02、S03 再接程屿和黑伞。九段成片再按镜头顺序接进 Concatenate Video，存成一条。\n"
            "这张图还没有提交运行。\n"
            "负面词已写进每条提示词：" + NEGATIVE,
            40,
            40,
            1100,
            280,
        )
    ]
    links = []
    link_id = 1
    image_links = {key: [] for key in layout}

    def add_link(origin, origin_slot, target, target_slot, link_type):
        nonlocal link_id
        current = link_id
        links.append([current, origin, origin_slot, target, target_slot, link_type])
        link_id += 1
        return current

    for key, place in layout.items():
        asset = by_id[key]
        prompt = asset["prompt"]
        if key in parent_of:
            prompt = "参考图是同一角色的定妆，必须锁定同一张脸，不要换脸。\n" + prompt
        ref_link = None
        nodes.append(gemini_node(place["gen"], f"{key} {asset['title']} · {place['kind']}", prompt, place["x"], place["y"], ref_link))

    # Wires are added after nodes exist so reference inputs can be attached.
    for key, parent in parent_of.items():
        place = layout[key]
        ref = add_link(layout[parent]["gen"], 0, place["gen"], 0, "IMAGE")
        image_links[parent].append(ref)
        gen = next(node for node in nodes if node["id"] == place["gen"])
        gen["inputs"] = [
            {
                "label": "image_1",
                "name": "model.images.image_1",
                "shape": 7,
                "type": "IMAGE",
                "link": ref,
            }
        ]

    for key, place in layout.items():
        asset = by_id[key]
        prefix = Path(asset["file"]).stem
        save_link = add_link(place["gen"], 0, place["save"], 0, "IMAGE")
        image_links[key].append(save_link)
        nodes.append(save_image_node(place["save"], f"保存 {key}", prefix, place["x"] + 500, place["y"], save_link))
        gen = next(node for node in nodes if node["id"] == place["gen"])
        gen["outputs"][0]["links"] = image_links[key]

    source_of = {
        "P2": layout["P2"]["gen"],
        "P9": layout["P9"]["gen"],
        "P6": layout["P6"]["gen"],
        "umbrella": layout["umbrella"]["gen"],
        "umbrella_text": layout["umbrella"]["gen"],
    }
    concat_inputs = []
    for index, raw in enumerate(SHOTS):
        compiled = compile_shot(raw)
        nid = 200 + index * 2
        save_id = nid + 1
        inputs = []
        for slot, key in enumerate(raw["refs"]):
            origin = source_of[key]
            lid = add_link(origin, 0, nid, slot, "IMAGE")
            origin_node = next(node for node in nodes if node["id"] == origin)
            origin_node["outputs"][0]["links"].append(lid)
            inputs.append(
                {
                    "label": f"image_{slot + 1}",
                    "name": f"model.reference_images.image_{slot + 1}",
                    "shape": 7,
                    "type": "IMAGE",
                    "link": lid,
                }
            )
        inputs.append({"label": "video_1", "name": "model.reference_videos.video_1", "shape": 7, "type": "VIDEO", "link": None})
        inputs.append({"label": "audio_1", "name": "model.reference_audios.audio_1", "shape": 7, "type": "AUDIO", "link": None})
        video_link = add_link(nid, 0, save_id, 0, "VIDEO")
        concat_link = add_link(nid, 0, 300, index, "VIDEO")
        y = 80 + index * 640
        nodes.append(
            {
                "id": nid,
                "type": "MinimaxHailuo03ReferenceNode",
                "pos": [2500, y],
                "size": [460, 520],
                "flags": {},
                "order": nid,
                "mode": 0,
                "inputs": inputs,
                "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [video_link, concat_link]}],
                "title": f"{compiled['id']} · MiniMax H3 · {compiled['duration']}秒",
                "properties": {"Node name for S&R": "MinimaxHailuo03ReferenceNode"},
                "widgets_values": ["MiniMax H3", compiled["prompt"], "768P", "9:16", compiled["duration"], 42, "fixed", False],
                "color": "#432",
                "bgcolor": "#653",
            }
        )
        nodes.append(
            {
                "id": save_id,
                "type": "SaveVideo",
                "pos": [3040, y],
                "size": [420, 140],
                "flags": {},
                "order": save_id,
                "mode": 0,
                "inputs": [{"name": "video", "type": "VIDEO", "link": video_link}],
                "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
                "title": f"保存 {compiled['id']}",
                "properties": {},
                "widgets_values": [f"video/MinJiang_{compiled['id']}", "auto", "auto"],
            }
        )
        concat_inputs.append(
            {
                "label": f"video_{index + 1}",
                "name": f"videos.video_{index + 1}",
                "shape": 7,
                "type": "VIDEO",
                "link": concat_link,
            }
        )

    joined_link = add_link(300, 0, 301, 0, "VIDEO")
    nodes.append(
        {
            "id": 300,
            "type": "ConcatenateVideo",
            "pos": [3600, 80],
            "size": [420, 520],
            "flags": {},
            "order": 300,
            "mode": 0,
            "inputs": concat_inputs,
            "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [joined_link]}],
            "title": "按 S01 到 S03 顺序拼接",
            "properties": {"cnr_id": "comfy-core", "ver": "0.20.1", "Node name for S&R": "ConcatenateVideo"},
            "widgets_values": ["auto"],
            "color": "#432",
            "bgcolor": "#653",
        }
    )
    nodes.append(
        {
            "id": 301,
            "type": "SaveVideo",
            "pos": [4100, 80],
            "size": [420, 160],
            "flags": {},
            "order": 301,
            "mode": 0,
            "inputs": [{"name": "video", "type": "VIDEO", "link": joined_link}],
            "outputs": [{"name": "video", "type": "VIDEO", "links": None}],
            "title": "保存整段",
            "properties": {},
            "widgets_values": ["video/MinJiang_S01-S03", "auto", "auto"],
        }
    )

    return {
        "id": "minjiang-weiwang-assets",
        "revision": 0,
        "last_node_id": max(node["id"] for node in nodes),
        "last_link_id": link_id - 1,
        "nodes": nodes,
        "links": links,
        "groups": [
            {"title": "文生图：定妆与空镜", "bounding": [20, 340, 900, 3600], "color": "#3f789e", "font_size": 24, "flags": {}},
            {"title": "变体：image_1 接定妆输出", "bounding": [1260, 40, 900, 2100], "color": "#8A8", "font_size": 24, "flags": {}},
            {"title": "MiniMax H3：参考图来自左侧出图", "bounding": [2460, 40, 1100, 5800], "color": "#A84", "font_size": 24, "flags": {}},
            {"title": "拼接成一条", "bounding": [3560, 40, 1000, 700], "color": "#448", "font_size": 24, "flags": {}},
        ],
        "config": {},
        "extra": {"frontendVersion": "1.47.11", "ds": {"scale": 0.55, "offset": [0, 0]}},
        "version": 0.4,
    }


def main():
    compiled = [compile_shot(item) for item in SHOTS]
    example = next(item for item in compiled if item["id"] == "S02-c03")
    assets_doc = {
        "title": "岷江未晚 资产图",
        "aspect": "9:16",
        "negative": NEGATIVE,
        "style_suffix": STYLE,
        "cloud": False,
        "order": "Nano Banana 先出图。P2、P3 接 P1。P5、P9 接 P4。S01–S03 的 MiniMax 直接接 P2、P9、P6、黑伞的输出。",
        "assets": [{**asset, "negative": NEGATIVE} for asset in ASSETS],
    }
    shots_doc = {
        "title": "岷江未晚 第一集 S01–S03",
        "workflow": "workflows/video_r2v.json",
        "model": "MiniMax H3",
        "resolution": "768P",
        "ratio": "9:16",
        "watermark": False,
        "cloud": False,
        "filled_example": "S02-c03",
        "global_prompt": GLOBAL,
        "shots": compiled,
    }
    (ROOT / "pipeline").mkdir(exist_ok=True)
    (ROOT / "workflows").mkdir(exist_ok=True)
    (ROOT / "pipeline" / "assets.json").write_text(json.dumps(assets_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "pipeline" / "shots_s01_s03.json").write_text(json.dumps(shots_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    graph = asset_workflow()
    (ROOT / "workflows" / "asset_still.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    video = dict(graph)
    video["id"] = "minjiang-weiwang-s01-s03-r2v"
    (ROOT / "workflows" / "video_r2v.json").write_text(json.dumps(video, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"assets {len(ASSETS)} shots {len(compiled)} nodes {len(graph['nodes'])} links {len(graph['links'])}")


if __name__ == "__main__":
    main()
