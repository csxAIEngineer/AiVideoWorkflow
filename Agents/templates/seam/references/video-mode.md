## video_mode

官方 MiniMax H3 只有 first_frame 和 reference。一次调用只能是一种，不能混用首帧和参考图。

| video_mode | 什么时候 | 画面 |
|---|---|---|
| first_frame | 这一单元和上一单元动作连续或剧情连续 | first_frame_source 写 previous_tail。这一发不要带参考图，也不要带 end_image |
| reference | 其余所有单元，包括第一个封装单元 | 绑定这一单元已经做好的人物静帧。end_image 空。不送上一段尾巴 |

第一个封装单元没有上一段，video_mode 用 reference。动作连续（action_continuous 为真）或剧情连续（plot_continuous 为真，或 scene_relation 是 dialogue_split、oner）才用 first_frame。same_scene 单独不算剧情连续。单元 images 是空的也仍然是 reference。
硬护栏：不要写 text，不要写 first_last_frame，不要写 end_image 来代替官方这两种。
